"""开发环境种子数据（租户 / 7 预置角色 / 权限点 / 字典 / 首个管理员）。

**为什么需要这个脚本**（这不是可选的美化）
---------------------------------------
API-01 登录必须先去 ``sys_user`` 找一个账号，而库里**一行都没有**时会形成死锁：
「没有账号 → 无法登录 → 无法调 API-14 新建用户 → 永远没有账号」。
故首个租户 + 首个管理员**只能由脚本/迁移提供**，这是 CU-05 交付的一部分。

用法
----
.. code-block:: bash

    cd backend
    # 最小用法：自动生成管理员密码并打印（推荐，比固定默认密码安全）
    .venv/Scripts/python.exe scripts/seed_dev.py

    # 指定账号密码（联调/自动化测试用）
    .venv/Scripts/python.exe scripts/seed_dev.py --username admin --password 'Admin@12345'

    # 已存在时重设密码（其他数据不动）
    .venv/Scripts/python.exe scripts/seed_dev.py --reset-password --password 'NewPass@123'

🔴 **幂等**：可重复执行。已存在的租户/角色/权限点/用户一律**复用不重建**，
   避免重置掉手工改过的数据。
🔴 **本脚本不做业务判定**（不校验越权/内置角色不可删等），它直接写库 ——
   属「初始化」而非「业务操作」，与 ``services`` 层的职责边界不冲突。
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from decimal import Decimal
from pathlib import Path

# 允许 `python scripts/seed_dev.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import SecurityUtils  # noqa: E402
from app.dao.session import SessionLocal  # noqa: E402
from app.models.ai import AiModelConfig, PromptTemplate  # noqa: E402
from app.models.dict import DictItem, DictType  # noqa: E402
from app.models.enums import (  # noqa: E402
    AgentInterruptStatus,
    AgentTaskStatus,
    AiCallStatus,
    ChangeSource,
    DupPairStatus,
    Gender,
    ImportFileParseStatus,
    ImportTaskStatus,
    LabeledIntEnum,
    ParseReviewStatus,
    PermType,
    PromptStatus,
    RoleDataScope,
    SysUserStatus,
    TagDictStatus,
    TagSource,
    TalentDataStatus,
)
from app.models.tenant import OrgDept, Tenant  # noqa: E402
from app.models.user import (  # noqa: E402
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)

DEFAULT_TENANT_CODE = "demo"
DEFAULT_TENANT_NAME = "示范单位"

# ============================================================================
# 7 个预置角色（role_code → 名称 / 数据域）
# ============================================================================
#: 🔴 ``data_scope`` 必须与接口 §1.7 的权限矩阵一致：
#: ADM/HR=1 全部、MGR=2 本部门、EMP=3 本人、EXEC/AIOPS/AUDIT=4 仅统计值。
BUILTIN_ROLES: list[tuple[str, str, RoleDataScope]] = [
    ("ADM", "超级管理员", RoleDataScope.ALL),
    ("EXEC", "决策层", RoleDataScope.STATS_ONLY),
    ("HR", "人力资源", RoleDataScope.ALL),
    ("MGR", "部门经理", RoleDataScope.OWN_DEPT),
    ("EMP", "员工", RoleDataScope.SELF),
    ("AIOPS", "AI 运维", RoleDataScope.STATS_ONLY),
    ("AUDIT", "审计", RoleDataScope.STATS_ONLY),
]

# ============================================================================
# 权限点（两级：菜单 → 接口/按钮）
# ============================================================================
# 命名规范：``<模块>:<资源>:<动作>``；与 rbac_router 的 require_perm 码逐字一致。
_PERM_TREE: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("rbac", "角色与权限", [
        ("rbac:role:list", "角色列表"),
        ("rbac:role:create", "新建角色"),
        ("rbac:role:update", "修改角色"),
        ("rbac:perm:tree", "权限点树"),
        ("rbac:role:bind", "绑定权限点"),
    ]),
    ("org", "组织架构", [
        ("org:view", "组织树查看"),
        ("org:create", "新建部门"),
        ("org:update", "修改部门"),
        ("org:delete", "删除部门"),
    ]),
    ("position", "岗位管理", [
        ("position:view", "岗位列表"),
        ("position:create", "新建岗位"),
    ]),
    ("user", "用户管理", [
        ("user:view", "用户列表"),
        ("user:create", "新建用户"),
        ("user:update", "修改用户"),
        ("user:role", "分配角色"),
        ("user:import", "批量导入用户"),
    ]),
    ("dict", "数据字典", [
        # ⚠️ ``dict:type:list`` 是 CU-07 为 API-23 专设的权限点，只授 ADM。
        #    为什么不能复用 ``dict:view``：接口 §4 矩阵里 API-23（字典类型列表）是
        #    **仅 ADM**，而 ``dict:view`` 被广授给 HR/MGR/EMP/EXEC（供前端下拉与菜单）；
        #    用 ``dict:view`` 把关会让这 4 个角色也能列类型，与矩阵不符。
        #    「管理端列类型」与「读字典项」本就是两件事，分成两个权限点更准确。
        ("dict:type:list", "字典类型列表"),
        ("dict:view", "字典查看"),
        ("dict:create", "新增字典项"),
        ("dict:update", "修改字典项"),
    ]),
    ("audit", "审计日志", [
        ("audit:view", "审计日志查看"),
        ("audit:export", "审计日志导出"),
    ]),
    ("talent", "人才档案", [
        ("talent:list", "档案列表"),
        ("talent:detail", "档案详情"),
        ("talent:edit", "编辑档案"),
        ("talent:archive", "归档档案"),
        ("talent:export", "导出档案"),
    ]),
    ("import", "简历导入", [
        ("import:create", "创建导入任务"),
        ("import:view", "导入任务查看"),
        ("import:report", "数据质量报告"),
        ("import:retry", "失败重试"),
    ]),
    ("review", "分层复核", [
        ("review:queue", "复核队列"),
        ("review:detail", "复核详情"),
        ("review:confirm", "批量确认"),
        ("review:correct", "修正字段"),
        ("review:skip", "标记免复核"),
        ("review:dashboard", "复核看板"),
    ]),
    ("tag", "标签体系", [
        ("tag:view", "标签词表"),
        ("tag:create", "新增标签"),
        ("tag:update", "修改标签"),
        ("tag:suggest", "提交 AI 建议标签"),
        ("tag:audit", "审核标签建议"),
        ("tag:assign", "调整人才标签"),
    ]),
    ("dedup", "查重合并", [
        ("dedup:list", "重复候选列表"),
        ("dedup:compare", "并排对比"),
        ("dedup:merge", "一键合并"),
        ("dedup:ignore", "忽略候选对"),
        ("dedup:rollback", "合并回滚"),
    ]),
    ("search", "语义检索", [
        ("search:semantic", "语义人才检索"),
        ("search:ask", "档案 RAG 问答"),
    ]),
    ("change", "变更留痕", [
        ("change:list", "变更留痕列表"),
        ("change:chain", "档案变更链"),
        ("change:snapshot", "时间点快照回溯"),
        ("change:field", "单字段变更链"),
    ]),
    ("privacy", "个人权利", [
        ("privacy:self_view", "查阅本人档案"),
        ("privacy:correct", "更正申请"),
        ("privacy:delete", "删除申请"),
        ("privacy:export", "导出个人数据包"),
        ("privacy:list", "权利请求台账"),
        ("privacy:approve", "审批删除"),
    ]),
    ("ai", "AI 中台", [
        ("ai:model:list", "模型配置列表"),
        ("ai:model:update", "修改模型配置"),
        ("ai:model:health", "模型健康状态"),
        ("ai:prompt:list", "Prompt 列表"),
        ("ai:prompt:create", "新建 Prompt"),
        ("ai:prompt:update", "修改 Prompt"),
        ("ai:prompt:rollback", "回滚 Prompt"),
        ("ai:log:view", "AI 调用日志"),
        ("ai:cost:view", "成本看板"),
        ("ai:mask:check", "脱敏自检"),
    ]),
    ("agent", "Agent 平台", [
        ("agent:task:list", "Agent 任务列表"),
        ("agent:task:detail", "任务详情"),
        ("agent:task:replay", "步骤回放"),
        ("agent:interrupt:list", "人工待办列表"),
        ("agent:interrupt:resolve", "处理人工待办"),
        ("agent:task:retry", "重试 Agent 任务"),
    ]),
    ("assessment", "人才测评", [
        ("assessment:task:list", "测评任务查看"),
        ("assessment:task:create", "测评任务创建与激活"),
        ("assessment:task:monitor", "测评任务进度与提醒"),
    ]),
]

# ============================================================================
# 角色 → 权限（按前缀授予；``*`` 表示全部）
# ============================================================================
#: 与接口 §4 权限矩阵对齐。ADM 虽在 ``require_perm`` 内直接放行，
#: 仍显式授予全部，以便 API-05 快照与前端 ``v-perm`` 表现一致。
ROLE_GRANTS: dict[str, list[str]] = {
    "ADM": ["*"],
    "HR": [
        "talent:", "import:", "review:", "tag:", "dedup:", "change:", "search:",
        "org:view", "position:view", "dict:view",
        "privacy:list", "privacy:correct", "privacy:delete", "privacy:export",
        # 🔴 CU-13 补：接口 §4 的矩阵里 API-90~94（Agent 任务与人工待办）**有 HR**，
        #    但此前 HR 的授权列表里没有任何 ``agent:`` 前缀 → 矩阵写了 ✓、实际一律 40301。
        #    Agent 的解析复核 / 合并确认待办正是 HR 的日常工作面（F-A0.3），这个缺口
        #    会让「人工介入漏斗」对唯一该用它的人不可见。
        #    ⚠️ 刻意**不含** ``agent:task:retry`` —— 矩阵里 API-95 只有 ADM / AIOPS。
        "agent:task:list", "agent:task:detail", "agent:task:replay",
        "agent:interrupt:list", "agent:interrupt:resolve",
        "assessment:task:list", "assessment:task:create", "assessment:task:monitor",
    ],
    "MGR": [
        "talent:list", "talent:detail", "search:", "tag:view", "dict:view",
        "assessment:task:list", "assessment:task:monitor",
    ],
    "EMP": [
        "dict:view", "tag:view",
        "privacy:self_view", "privacy:correct", "privacy:delete",
        "privacy:export", "privacy:list",
    ],
    "EXEC": ["dict:view", "tag:view", "ai:cost:view"],
    "AIOPS": ["ai:", "agent:"],
    "AUDIT": ["audit:", "change:", "ai:log:view"],
}


# ============================================================================
# 工具
# ============================================================================
def _gen_password(length: int = 16) -> str:
    """生成满足强度要求的随机密码（含大小写/数字/符号，长度 ≥ 8）。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd


def _get_or_create_tenant(db: Session, code: str, name: str) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.tenant_code == code)).scalar_one_or_none()
    if tenant is not None:
        return tenant
    tenant = Tenant(tenant_code=code, tenant_name=name, status=1)
    db.add(tenant)
    db.flush()
    return tenant


def _seed_roles(db: Session, tenant_id: int) -> dict[str, SysRole]:
    roles: dict[str, SysRole] = {}
    for code, name, scope in BUILTIN_ROLES:
        role = db.execute(
            select(SysRole).where(
                SysRole.tenant_id == tenant_id,
                SysRole.role_code == code,
                SysRole.is_deleted == 0,
            )
        ).scalar_one_or_none()
        if role is None:
            role = SysRole(
                tenant_id=tenant_id,
                role_code=code,
                role_name=name,
                data_scope=int(scope),
                is_builtin=1,
                status=1,
                is_deleted=0,
            )
            db.add(role)
            db.flush()
        roles[code] = role
    return roles


def _seed_permissions(db: Session, tenant_id: int) -> dict[str, SysPermission]:
    """建权限点树（菜单 → 子项）。返回 ``perm_code → SysPermission``。"""
    perms: dict[str, SysPermission] = {}
    sort_menu = 0
    for menu_code, menu_name, children in _PERM_TREE:
        sort_menu += 10
        menu = db.execute(
            select(SysPermission).where(
                SysPermission.tenant_id == tenant_id,
                SysPermission.perm_code == menu_code,
                SysPermission.is_deleted == 0,
            )
        ).scalar_one_or_none()
        if menu is None:
            menu = SysPermission(
                tenant_id=tenant_id,
                perm_code=menu_code,
                perm_name=menu_name,
                perm_type=int(PermType.MENU),
                parent_id=0,
                sort_no=sort_menu,
                status=1,
                is_deleted=0,
            )
            db.add(menu)
            db.flush()
        perms[menu_code] = menu

        for idx, (child_code, child_name) in enumerate(children, start=1):
            child = db.execute(
                select(SysPermission).where(
                    SysPermission.tenant_id == tenant_id,
                    SysPermission.perm_code == child_code,
                    SysPermission.is_deleted == 0,
                )
            ).scalar_one_or_none()
            if child is None:
                child = SysPermission(
                    tenant_id=tenant_id,
                    perm_code=child_code,
                    perm_name=child_name,
                    perm_type=int(PermType.API),
                    parent_id=menu.id,
                    sort_no=idx * 10,
                    status=1,
                    is_deleted=0,
                )
                db.add(child)
                db.flush()
            perms[child_code] = child
    return perms


def _grant(db: Session, tenant_id: int, role: SysRole, perms: dict[str, SysPermission], prefixes: list[str]) -> int:
    """按前缀授予权限（``*`` 表示全部）。返回**新增**绑定数（已存在的跳过）。"""
    existing = {
        int(x)
        for x in db.execute(
            select(SysRolePermission.perm_id).where(
                SysRolePermission.tenant_id == tenant_id,
                SysRolePermission.role_id == role.id,
            )
        ).scalars().all()
    }
    all_codes = list(perms.keys())
    wanted: list[str] = []
    if prefixes == ["*"]:
        wanted = all_codes
    else:
        for code in all_codes:
            if any(code == p or code.startswith(p) for p in prefixes):
                wanted.append(code)

    added = 0
    for code in wanted:
        perm = perms[code]
        if int(perm.id) in existing:
            continue
        db.add(SysRolePermission(tenant_id=tenant_id, role_id=role.id, perm_id=perm.id))
        added += 1
    return added


def _get_or_create_user(
    db: Session, tenant_id: int, username: str, password: str, *, reset_password: bool
) -> tuple[SysUser, bool]:
    """返回 ``(用户, 是否新建/改密)``。"""
    user = db.execute(
        select(SysUser).where(
            SysUser.tenant_id == tenant_id,
            SysUser.username == username,
            SysUser.is_deleted == 0,
        )
    ).scalar_one_or_none()
    if user is not None:
        if reset_password:
            user.password_hash = SecurityUtils.hash_password(password)
            user.login_fail_count = 0
            user.locked_until = None
            return user, True
        return user, False

    user = SysUser(
        tenant_id=tenant_id,
        username=username,
        password_hash=SecurityUtils.hash_password(password),
        real_name="系统管理员",
        org_id=None,
        status=1,
        is_deleted=0,
    )
    db.add(user)
    db.flush()
    return user, True


def _seed_root_dept(db: Session, tenant_id: int) -> OrgDept:
    """建一个根部门（``dept_path`` 含自身 + 尾斜杠）。

    🔴 ``dept_path`` 必须是 ``"/<自身id>/"`` 形式 —— 这是 CU-05 部门子树前缀匹配的前提；
    不建根部门时，``MGR``（``data_scope=2``）的行级过滤会锁死为不可见。
    """
    dept = db.execute(
        select(OrgDept).where(
            OrgDept.tenant_id == tenant_id,
            OrgDept.parent_id == 0,
            OrgDept.is_deleted == 0,
        )
    ).scalars().first()
    if dept is not None:
        if not dept.dept_path:
            dept.dept_path = f"/{dept.id}/"
        return dept

    dept = OrgDept(
        tenant_id=tenant_id,
        parent_id=0,
        dept_code="ROOT",
        dept_name=f"{DEFAULT_TENANT_NAME}总部",
        dept_level=1,
        dept_path=None,
        sort_no=0,
        status=1,
        is_deleted=0,
    )
    db.add(dept)
    db.flush()
    dept.dept_path = f"/{dept.id}/"  # 自身 ID 生成后才能拼路径
    return dept


# ============================================================================
# 字典初始化数据（TBL-09 / TBL-10，CU-07）
# ============================================================================
#: §6.1 内建枚举 → ``dict_type``。
#:
#: 🔴 **字典项不手写，而是从代码枚举「派生」**（``enum.options()``，即 ``LABELS`` 表）。
#:    原因：手写就会有两份事实源，早晚会漂移（数据库设计 §6 开头那句警告：
#:    「枚举值必须与需求文档状态机、接口响应示例中的取值完全一致 ——
#:    最常见的跨文档不一致点」）。派生则**在结构上不可能漂移**，
#:    并由 ``tests/test_dict_crud.py`` 的 ``test_seed_dict_matches_builtin_enums`` 钉死。
#:
#: 这些类型的 ``remark`` 标注「内建」，提示不要手工改其项（改了会让业务表里
#: 已存的 int 值含义漂移）。
_BUILTIN_ENUM_DICTS: list[tuple[str, str, type[LabeledIntEnum]]] = [
    ("sys_user_status", "用户状态", SysUserStatus),
    ("role_data_scope", "角色数据范围", RoleDataScope),
    ("perm_type", "权限点类型", PermType),
    ("gender", "性别", Gender),
    ("talent_data_status", "人才档案状态", TalentDataStatus),
    ("parse_review_status", "解析字段复核状态", ParseReviewStatus),
    ("dup_pair_status", "查重对状态", DupPairStatus),
    ("tag_dict_status", "标签字典状态", TagDictStatus),
    ("tag_source", "标签来源", TagSource),
    ("import_task_status", "导入任务状态", ImportTaskStatus),
    ("import_file_parse_status", "导入文件解析状态", ImportFileParseStatus),
    ("change_source", "变更来源", ChangeSource),
    ("prompt_status", "Prompt 模板状态", PromptStatus),
    ("ai_call_status", "AI 调用状态", AiCallStatus),
    ("agent_task_status", "Agent 任务状态", AgentTaskStatus),
    ("agent_interrupt_status", "Agent 待办状态", AgentInterruptStatus),
]

#: §6.2 业务字典 —— **人工维护**的枚举（不由代码枚举派生）。
#: 一期只有「学历」一项：它是 ``talent_base.education_level`` 的取值来源，
#: 且学历档位会随政策调整，必须能由 ADM 在界面上改（这正是 F-1.7 的存在意义）。
#:
#: ⚠️ 编码用 ``01``~``05`` 而非 1~5，与数据库设计 §6.2 完全一致 ——
#: 保持前导零便于在界面上按 ``item_code`` 排序时得到 ``01,02,…,10``。
_BUSINESS_DICTS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    (
        "education_level",
        "学历",
        "业务字典：可由 ADM 维护",
        [("01", "高中及以下"), ("02", "大专"), ("03", "本科"), ("04", "硕士"), ("05", "博士")],
    ),
]

#: 内建枚举类型的备注（统一措辞，便于在界面上与业务字典区分）
_BUILTIN_REMARK = "内建枚举：取值由代码枚举派生，请勿手工增删"


def _get_or_create_dict_type(
    db: Session, tenant_id: int, type_code: str, type_name: str, remark: str
) -> tuple[DictType, bool]:
    """复用已有字典类型；不存在则新建。返回 ``(实体, 是否新建)``。"""
    row = db.execute(
        select(DictType).where(
            DictType.tenant_id == tenant_id,
            DictType.type_code == type_code,
            DictType.is_deleted == 0,
        )
    ).scalar_one_or_none()
    if row is not None:
        return row, False
    row = DictType(
        tenant_id=tenant_id,
        type_code=type_code,
        type_name=type_name,
        remark=remark,
        status=1,
        is_deleted=0,
    )
    db.add(row)
    db.flush()
    return row, True


def _get_or_create_dict_item(
    db: Session, tenant_id: int, type_code: str, item_code: str, item_name: str, sort_no: int
) -> bool:
    """复用已有字典项；不存在则新建。返回是否新建（供打印「新增」条数）。"""
    row = db.execute(
        select(DictItem).where(
            DictItem.tenant_id == tenant_id,
            DictItem.type_code == type_code,
            DictItem.item_code == item_code,
            DictItem.is_deleted == 0,
        )
    ).scalar_one_or_none()
    if row is not None:
        return False
    db.add(
        DictItem(
            tenant_id=tenant_id,
            type_code=type_code,
            item_code=item_code,
            item_name=item_name,
            sort_no=sort_no,
            parent_code=None,
            status=1,
            is_deleted=0,
        )
    )
    db.flush()
    return True


def _seed_dicts(db: Session, tenant_id: int) -> tuple[int, int]:
    """初始化字典（CU-07）。返回 ``(类型总数, 新增项数)``。

    🔴 **幂等**：类型/项按 ``(tenant_id, type_code[, item_code])`` 复用，不重建，
    因此重复执行不会覆盖 ADM 在界面上改过的名字或排序。
    """
    type_total = 0
    items_added = 0

    # ① 内建枚举：项由代码枚举派生
    for type_code, type_name, enum_cls in _BUILTIN_ENUM_DICTS:
        _row, created = _get_or_create_dict_type(
            db, tenant_id, type_code, type_name, _BUILTIN_REMARK
        )
        type_total += 1
        for idx, option in enumerate(enum_cls.options(), start=1):
            if _get_or_create_dict_item(
                db,
                tenant_id,
                type_code,
                str(option["code"]),
                str(option["name"]),
                idx * 10,
            ):
                items_added += 1

    # ② 业务字典：项来自 §6.2
    for type_code, type_name, remark, items in _BUSINESS_DICTS:
        _row, created = _get_or_create_dict_type(db, tenant_id, type_code, type_name, remark)
        type_total += 1
        for idx, (item_code, item_name) in enumerate(items, start=1):
            if _get_or_create_dict_item(
                db, tenant_id, type_code, item_code, item_name, idx * 10
            ):
                items_added += 1

    return type_total, items_added


# ============================================================================
# AI 中台种子（CU-10 / CU-11：模型网关配置 TBL-29 + Prompt 模板 TBL-23）
# ============================================================================
#: 每个场景的主备链（``priority`` 升序 = 尝试顺序）。
#:
#: 🔴 **为什么必须有种**：``ai_model_config`` 为空时，模型网关对任何场景都返回
#: **降级**（``code=50301`` + ``data.degraded=true``）—— 功能不会崩，
#: 但所有 AI 能力都走兜底文案，联调时极难判断「是我的代码错了还是没配模型」。
#: 故把本机已部署的 Ollama / bge-m3 线路直接种进去，让 N2 的 AI 能力**开箱可用**。
#:
#: ⚠️ ``ocr`` 场景**刻意不种**：本机没有可用的 OCR 服务（它由 N0/解析链路自带），
#: 凭空编一个 ``base_url`` 只会让健康检查长期飘红。等 N2 落地 OCR 服务时再补。
#: 字段顺序：scene, model_code, display_name, provider, deployment, base_url,
#:           model_name, priority, timeout_seconds, max_retries,
#:           price_in_per_1k, price_out_per_1k, status, remark
_MODEL_CONFIGS: list[tuple] = [
    # ---- 简历字段抽取（本地，不出网）----
    (
        "resume_parse", "PARSE_LOCAL_MAIN", "本地解析主模型", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL_PARSE, 1, 60.0, 1,
        "0", "0", 1, "字段抽取主路（本地 Ollama，不出网）",
    ),
    (
        "resume_parse", "PARSE_LOCAL_BACKUP", "本地解析备用模型", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL_REASON, 2, 120.0, 1,
        "0", "0", 1, "主路超时/熔断时接管（推理型模型，更慢但抽取质量更高）",
    ),
    # ---- 检索问题改写（本地）----
    (
        "query_rewrite", "REWRITE_LOCAL_MAIN", "本地改写模型", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL_PARSE, 1, 30.0, 1,
        "0", "0", 1, "问题改写（本地 Ollama，不出网；改写属轻任务故用小模型）",
    ),
    # ---- 标签建议（本地）----
    (
        "tag_suggest", "TAG_LOCAL_MAIN", "本地标签建议模型", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL_PARSE, 1, 30.0, 1,
        "0", "0", 1, "标签建议（本地 Ollama，不出网）",
    ),
    # ---- AI 判定（本地）----
    (
        "judge", "JUDGE_LOCAL_MAIN", "本地判定模型", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL_REASON, 1, 60.0, 1,
        "0", "0", 1,
        "AI 判定（本地；⚠️ 只产出文本与理由，数值结论一律由 ThresholdEngine 等确定性组件给出）",
    ),
    # ---- 向量化（本地）----
    (
        "embedding", "EMBED_LOCAL_MAIN", "本地向量化 bge-m3", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.EMBEDDING_MODEL, 1, 30.0, 1,
        "0", "0", 1, f"向量化（本地 {settings.EMBEDDING_MODEL}，{settings.EMBEDDING_DIM} 维，不出网）",
    ),
    # ---- 精排（本地）----
    (
        "rerank", "RERANK_LOCAL_MAIN", "本地精排模型", "openai", "local",
        settings.RERANKER_BASE_URL, settings.RERANKER_MODEL, 1, 30.0, 1,
        "0", "0", 1, "精排（本地 TEI/Infinity 兼容接口，不出网）",
    ),
    # ---- 档案 RAG 问答：云端主路 + 本地兜底 ----
    (
        "talent_ask", "ASK_CLOUD_MAIN", "云端问答主通道", "cloud", "cloud",
        settings.CLOUD_LLM_BASE_URL or "https://unset.invalid/v1",
        settings.CLOUD_LLM_MODEL or "cloud-llm-a", 1, 60.0, 1,
        "0.001", "0.002",
        # 🔴 未配置云端通道时种**停用**：种一个 ``base_url`` 是占位符的启用模型，
        #    会让 API-83 的健康检查每次探测都失败（并且是「一个配错了的模型」这种假象）。
        #    停用状态下网关自动落到下面的本地兜底，且 ``deployment`` 记为 local，
        #    脱敏率分母不会被污染。
        1 if settings.CLOUD_LLM_BASE_URL else 0,
        "出网通道（F-3.7）：**只接收脱敏占位符片段 + Prompt**；"
        "未配置 CLOUD_LLM_* 时保持停用，自动落到本地兜底",
    ),
    (
        "talent_ask", "ASK_LOCAL_FALLBACK", "本地问答兜底", "ollama", "local",
        settings.OLLAMA_BASE_URL, settings.OLLAMA_MODEL_REASON, 2, 120.0, 1,
        "0", "0", 1,
        "云端不可用时的本地兜底（不出网；此时答案质量下降但服务不中断 —— 降级不抛 500）",
    ),
]

#: Prompt 模板种子（每个 ``prompt_code`` 只种 v1）。
#:
#: ⚠️ ``prompt_template.scene`` 与 ``AiScene`` **不是同一套取值**（见 schemas/ai.py 的
#: ``PromptScene``）：Prompt 的场景更粗，一个 Prompt 可服务多个调用场景。
#: 这里的映射是：extract→resume_parse / rewrite→query_rewrite / ask→talent_ask /
#: judge→tag_suggest + judge。
#:
#: 🔴 正文**只种骨架，不写完整提示词**：提示词质量属「模型调优」，是 N2 的工作；
#: 现在写一段看起来很像样但没经过评测的提示词，比留一个明确的 TODO 更危险
#: （后来的人会以为它已经被调过）。种子只保证「有可加载的版本」这条链路通。
_PROMPT_TEMPLATES: list[tuple[str, str, str]] = [
    (
        "RESUME_EXTRACT",
        "extract",
        "【简历字段抽取 v1 · 骨架】\n"
        "角色：你是简历信息抽取器。\n"
        "输入：{resume_text}\n"
        "要求：只输出 JSON，字段为 {fields}；无法判断的字段输出 null，**不要猜测**。\n"
        "输出：{output_schema}\n"
        "<!-- TODO(N2)：本骨架待用评测集调优，勿直接上生产 -->",
    ),
    (
        "QUERY_REWRITE",
        "rewrite",
        "【检索问题改写 v1 · 骨架】\n"
        "角色：把用户口语化问题改写为检索用查询串。\n"
        "输入：{question}\n"
        "要求：保留实体与约束（岗位、年限、技能），去掉寒暄；输出一行纯文本。\n"
        "<!-- TODO(N2)：本骨架待用评测集调优，勿直接上生产 -->",
    ),
    (
        "ASK_TALENT",
        "ask",
        "【档案 RAG 问答 v1 · 骨架】\n"
        "角色：依据给定档案片段回答问题，**不得引入片段之外的信息**。\n"
        "档案片段：{fragments}\n"
        "问题：{question}\n"
        "要求：片段不足以回答时明确回复「档案中未找到相关信息」，不要编造。\n"
        "<!-- TODO(N2)：本骨架待用评测集调优，勿直接上生产 -->",
    ),
    (
        "JUDGE_SCORE",
        "judge",
        "【判定打分 v1 · 骨架】\n"
        "角色：对给定对象给出定性判断与理由。\n"
        "输入：{payload}\n"
        "要求：输出 JSON {{ \"reason\": \"...\", \"suggestion\": \"...\" }}；\n"
        "🔴 **不要输出任何分数或阈值结论** —— 数值结论由确定性组件（ThresholdEngine）给出。\n"
        "<!-- TODO(N2)：本骨架待用评测集调优，勿直接上生产 -->",
    ),
]


def _get_or_create_model_config(db: Session, tenant_id: int, row: tuple) -> bool:
    """复用已有模型配置；不存在则新建。返回是否新建。

    🔴 **幂等键取 ``(tenant_id, scene, model_code)``**（与表上的
    ``uk_tenant_scene_model`` 一致）：重复执行不会覆盖运维在界面上改过的
    ``priority`` / ``timeout_seconds`` / 单价 —— 那些是本脚本最容易被人工调参的字段。
    """
    (scene, model_code, display_name, provider, deployment, base_url, model_name,
     priority, timeout_seconds, max_retries, price_in, price_out, status, remark) = row
    existing = db.execute(
        select(AiModelConfig).where(
            AiModelConfig.tenant_id == tenant_id,
            AiModelConfig.scene == scene,
            AiModelConfig.model_code == model_code,
            AiModelConfig.is_deleted == 0,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    db.add(
        AiModelConfig(
            tenant_id=tenant_id,
            scene=scene,
            model_code=model_code,
            display_name=display_name,
            provider=provider,
            deployment=deployment,
            base_url=base_url,
            model_name=model_name,
            priority=priority,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            breaker_threshold=3,
            breaker_cooldown_seconds=60,
            price_in_per_1k=Decimal(price_in),
            price_out_per_1k=Decimal(price_out),
            status=status,
            remark=remark,
        )
    )
    db.flush()
    return True


def _get_or_create_prompt(
    db: Session, tenant_id: int, prompt_code: str, scene: str, content: str
) -> bool:
    """种 ``prompt_code`` 的 v1（已存在任何版本则跳过）。返回是否新建。

    🔴 **只判断「这个族有没有版本」，不判断「正文是不是我种的那份」**：
    若某天有人通过 API-86 把 v1 改成 v2 并**停用了 v1**，本脚本绝不能
    「发现 v1 停用了就把它改回启用」—— 那等于脚本可以**回滚线上 Prompt**。
    初始化脚本只负责「从零到有」，不参与版本治理。

    ⚠️ ``prompt_template`` **没有 ``remark`` 列**（变更说明写在审计 ``after`` 快照里，
    见 ``PromptService._snapshot``）——别往这里塞。
    """
    existing = db.execute(
        select(PromptTemplate).where(
            PromptTemplate.tenant_id == tenant_id,
            PromptTemplate.prompt_code == prompt_code,
            PromptTemplate.is_deleted == 0,
        )
    ).first()
    if existing is not None:
        return False
    db.add(
        PromptTemplate(
            tenant_id=tenant_id,
            prompt_code=prompt_code,
            scene=scene,
            version=1,
            content=content,
            # ``PromptStatus.ENABLED`` —— 骨架虽待调优，但**必须有启用版本**，
            # 否则调用方拿不到 Prompt（骨架本身是安全的：它只要求「不编造」）。
            status=int(PromptStatus.ENABLED),
            created_by=None,
        )
    )
    db.flush()
    return True


def _seed_ai(db: Session, tenant_id: int) -> tuple[int, int]:
    """初始化 AI 中台配置（CU-10 / CU-11）。返回 ``(新增模型配置数, 新增 Prompt 数)``。"""
    models_added = 0
    for row in _MODEL_CONFIGS:
        if _get_or_create_model_config(db, tenant_id, row):
            models_added += 1
    prompts_added = 0
    for prompt_code, scene, content in _PROMPT_TEMPLATES:
        if _get_or_create_prompt(db, tenant_id, prompt_code, scene, content):
            prompts_added += 1
    return models_added, prompts_added


# ============================================================================
# 受控标签词表种子（CU-17，F-3.4.1：一期 ≥100 个启用标签）
# ============================================================================
#: ``(一级分类, 二级分类, [(标签码, 标签名, 同义词), ...])``。
#: 编码规则：大写 + 下划线（``TagDictValidator.is_valid_code``）；
#: 同义词供 AI 选词归一与自动打标匹配（``parse_pipeline._auto_tag``）。
#: 🔴 幂等：按 ``tag_code`` 查重，已存在则跳过（不覆盖人工改过的名字）。
_TAG_SEED: list[tuple[str, str, list[tuple[str, str, list[str]]]]] = [
    ("技术技能", "开发语言", [
        ("SK_PYTHON", "Python", ["py"]),
        ("SK_JAVA", "Java", []),
        ("SK_GO", "Go", ["golang"]),
        ("SK_CPP", "C++", ["cpp"]),
        ("SK_CSHARP", "C#", ["csharp", "dotnet"]),
        ("SK_JS", "JavaScript", ["js"]),
        ("SK_TS", "TypeScript", ["ts"]),
        ("SK_RUST", "Rust", []),
        ("SK_KOTLIN", "Kotlin", []),
        ("SK_SWIFT", "Swift", []),
        ("SK_PHP", "PHP", []),
        ("SK_RUBY", "Ruby", []),
        ("SK_SCALA", "Scala", []),
        ("SK_R_LANG", "R 语言", ["rlang", "R"]),
        ("SK_SQL", "SQL", []),
        ("SK_SHELL", "Shell", ["bash"]),
    ]),
    ("技术技能", "前端", [
        ("SK_VUE", "Vue", ["vue3", "vuejs"]),
        ("SK_REACT", "React", ["reactjs"]),
        ("SK_ANGULAR", "Angular", []),
        ("SK_HTML_CSS", "HTML/CSS", ["html", "css"]),
        ("SK_MINIPROGRAM", "小程序", ["miniprogram", "wechat-mp"]),
        ("SK_ELEMENT_PLUS", "Element Plus", ["elementui"]),
        ("SK_ANTD", "Ant Design", ["antd"]),
        ("SK_ECHARTS", "ECharts", []),
        ("SK_WEBGL", "WebGL", ["threejs"]),
    ]),
    ("技术技能", "后端与架构", [
        ("SK_SPRING", "Spring", ["springboot", "springcloud"]),
        ("SK_DJANGO", "Django", ["drf"]),
        ("SK_FLASK", "Flask", []),
        ("SK_FASTAPI", "FastAPI", []),
        ("SK_NODEJS", "Node.js", ["nodejs", "node"]),
        ("SK_MICROSERVICE", "微服务", ["microservice"]),
        ("SK_MQ", "消息队列", ["kafka", "rabbitmq", "rocketmq"]),
        ("SK_CACHE", "缓存", ["redis", "memcached"]),
        ("SK_GATEWAY", "网关", ["nginx", "apisix"]),
    ]),
    ("技术技能", "数据与 AI", [
        ("SK_MYSQL", "MySQL", []),
        ("SK_PGSQL", "PostgreSQL", ["pgsql", "postgres"]),
        ("SK_MONGODB", "MongoDB", ["mongo"]),
        ("SK_MILVUS", "Milvus", ["向量数据库"]),
        ("SK_ES", "Elasticsearch", ["es", "elk"]),
        ("SK_HADOOP", "Hadoop", ["hdfs", "hive"]),
        ("SK_SPARK", "Spark", []),
        ("SK_FLINK", "Flink", []),
        ("SK_PYTORCH", "PyTorch", ["torch"]),
        ("SK_TENSORFLOW", "TensorFlow", ["tf"]),
        ("SK_LLM", "大模型", ["llm", "large language model"]),
        ("SK_RAG", "RAG", ["检索增强生成"]),
        ("SK_LANGCHAIN", "LangChain", ["langgraph"]),
        ("SK_PROMPT_ENG", "提示词工程", ["prompt engineering"]),
        ("SK_OLLAMA", "Ollama", ["本地推理"]),
        ("SK_FINETUNE", "模型微调", ["finetune", "sft", "lora"]),
        ("SK_CV", "计算机视觉", ["cv", "opencv"]),
        ("SK_NLP", "自然语言处理", ["nlp"]),
        ("SK_ASR_TTS", "语音识别合成", ["asr", "tts"]),
        ("SK_AGENT", "智能体", ["agent", "multi-agent"]),
    ]),
    ("技术技能", "运维与云", [
        ("SK_DOCKER", "Docker", ["container"]),
        ("SK_K8S", "Kubernetes", ["k8s", "kubernetes"]),
        ("SK_CI_CD", "CI/CD", ["jenkins", "gitlab-ci", "github-actions"]),
        ("SK_LINUX", "Linux", []),
        ("SK_TERRAFORM", "Terraform", ["iac"]),
        ("SK_AWS", "AWS", ["amazon web services"]),
        ("SK_ALIYUN", "阿里云", ["aliyun"]),
        ("SK_TENCENT_CLOUD", "腾讯云", ["tencentcloud"]),
        ("SK_MONITOR", "监控告警", ["prometheus", "grafana", "zabbix"]),
        ("SK_DEVOPS", "DevOps", []),
        ("SK_SECURITY_OPS", "安全运维", ["secops"]),
    ]),
    ("技术技能", "数据职能", [
        ("SK_DATA_ANALYSIS", "数据分析", ["data analysis"]),
        ("SK_BI", "BI 报表", ["powerbi", "tableau", "finereport"]),
        ("SK_ETL", "ETL", ["数据仓库", "datawarehouse"]),
        ("SK_DATA_MODEL", "数据建模", ["dimensional modeling"]),
        ("SK_DATA_GOV", "数据治理", ["data governance"]),
        ("SK_STATISTICS", "统计学", ["statistics"]),
        ("SK_A_B_TEST", "AB 实验", ["a/b test", "abtest"]),
    ]),
    ("行业经验", "互联网与软件", [
        ("IND_INTERNET", "互联网", ["internet"]),
        ("IND_SOFTWARE", "软件", ["software"]),
        ("IND_SAAS", "SaaS", ["saas"]),
        ("IND_GAME", "游戏", ["gaming"]),
        ("IND_ECOMMERCE", "电商", ["e-commerce"]),
        ("IND_SOCIAL", "社交", []),
        ("IND_LIVESTREAM", "直播", ["livestreaming"]),
        ("IND_ADS", "广告营销", ["advertising"]),
    ]),
    ("行业经验", "金融与制造", [
        ("IND_FINTECH", "金融科技", ["fintech"]),
        ("IND_BANK", "银行", ["banking"]),
        ("IND_SECURITIES", "证券", ["securities"]),
        ("IND_INSURANCE", "保险", ["insurance"]),
        ("IND_MANUFACTURE", "制造", ["manufacturing"]),
        ("IND_AUTOMOTIVE", "汽车", ["automotive", "新能源车"]),
        ("IND_SEMI", "半导体", ["semiconductor", "芯片"]),
        ("IND_ENERGY", "能源", ["energy", "光伏"]),
    ]),
    ("行业经验", "传统行业", [
        ("IND_EDU", "教育", ["education"]),
        ("IND_HEALTH", "医疗健康", ["healthcare"]),
        ("IND_LOGISTICS", "物流", ["logistics"]),
        ("IND_RETAIL", "零售", ["retail"]),
        ("IND_REALTY", "地产", ["real estate"]),
        ("IND_GOV", "政务", ["government"]),
        ("IND_TELCOM", "通信", ["telecom", "运营商"]),
        ("IND_LEGAL", "法律", ["legal"]),
    ]),
    ("职能角色", "研发职能", [
        ("FUN_FRONTEND_ENG", "前端工程师", ["frontend engineer"]),
        ("FUN_BACKEND_ENG", "后端工程师", ["backend engineer"]),
        ("FUN_FULLSTACK", "全栈工程师", ["fullstack"]),
        ("FUN_ALGO_ENG", "算法工程师", ["algorithm engineer"]),
        ("FUN_AI_ENG", "AI 工程师", ["ai engineer"]),
        ("FUN_TEST_ENG", "测试工程师", ["qa", "test engineer"]),
        ("FUN_OPS_ENG", "运维工程师", ["sre", "operations engineer"]),
        ("FUN_DATA_ENG", "数据工程师", ["data engineer"]),
        ("FUN_ARCHITECT", "架构师", ["architect"]),
        ("FUN_SECURITY_ENG", "安全工程师", ["security engineer"]),
        ("FUN_EMBEDDED_ENG", "嵌入式工程师", ["embedded engineer"]),
        ("FUN_GAME_DEV", "游戏开发", ["game developer"]),
    ]),
    ("职能角色", "产品与设计", [
        ("FUN_PM", "产品经理", ["product manager"]),
        ("FUN_APM", "助理产品经理", ["associate pm"]),
        ("FUN_PROJECT_MGR", "项目经理", ["project manager"]),
        ("FUN_UX", "交互设计", ["ux design"]),
        ("FUN_UI", "视觉设计", ["ui design"]),
        ("FUN_USER_RESEARCH", "用户研究", ["user research"]),
        ("FUN_TECH_WRITER", "技术写作", ["technical writer"]),
    ]),
    ("职能角色", "管理与业务", [
        ("FUN_TEAM_LEAD", "团队负责人", ["team lead"]),
        ("FUN_TECH_DIR", "技术总监", ["technical director"]),
        ("FUN_CTO", "CTO", ["chief technology officer"]),
        ("FUN_DATA_SCI", "数据科学家", ["data scientist"]),
        ("FUN_ANALYST", "业务分析", ["business analyst"]),
        ("FUN_SALES", "销售", ["sales"]),
        ("FUN_CSM", "客户成功", ["customer success"]),
        ("FUN_HR", "人力资源", ["human resources"]),
    ]),
    ("软素质", "能力素质", [
        ("QUA_LEADERSHIP", "领导力", ["leadership"]),
        ("QUA_COMMS", "沟通表达", ["communication"]),
        ("QUA_CROSS_TEAM", "跨团队协作", ["cross-team"]),
        ("QUA_SELF_DRIVE", "自驱力", ["self-driven"]),
        ("QUA_LEARNING", "快速学习", ["fast learning"]),
        ("QUA_OWNERSHIP", "结果导向", ["result-oriented"]),
        ("QUA_INNOVATION", "创新", ["innovation"]),
        ("QUA_MENTORING", "带教新人", ["mentoring"]),
        ("QUA_DOC", "文档能力", ["documentation"]),
        ("QUA_PRESENT", "公开演讲", ["presentation"]),
    ]),
    ("软素质", "工作偏好", [
        ("PREF_REMOTE", "可远程", ["remote"]),
        ("PREF_RELOCATE", "可出差", ["travel-ready"]),
        ("PREF_MANAGE", "愿带团队", ["wants management"]),
        ("PREF_IC", "深耕专业线", ["individual contributor"]),
        ("PREF_STARTUP", "创业倾向", ["startup mindset"]),
        ("PREF_CERT_CPA", "持证（CPA/PMP 等）", ["certified"]),
    ]),
]


def _seed_tags(db: Session, tenant_id: int) -> int:
    """种子化受控标签词表（≥100 个启用项，幂等）。返回新增个数。"""
    from app.models.enums import TagDictStatus, TagSource
    from app.models.talent import TalentTagDict

    added = 0
    for l1, l2, items in _TAG_SEED:
        for code, name, synonyms in items:
            exists = db.execute(
                select(TalentTagDict).where(
                    TalentTagDict.tenant_id == tenant_id,
                    TalentTagDict.tag_code == code,
                    TalentTagDict.is_deleted == 0,
                )
            ).scalar_one_or_none()
            if exists is not None:
                continue
            db.add(
                TalentTagDict(
                    tenant_id=tenant_id,
                    tag_code=code,
                    tag_name=name,
                    category_l1=l1,
                    category_l2=l2,
                    category_l3=None,
                    synonyms=synonyms,
                    status=int(TagDictStatus.ENABLED),
                    source=int(TagSource.MANUAL),
                    is_deleted=0,
                )
            )
            added += 1
    db.flush()
    return added


# ============================================================================
# 主流程
# ============================================================================
def seed(
    *,
    tenant_code: str,
    tenant_name: str,
    username: str,
    password: str,
    reset_password: bool,
) -> dict[str, object]:
    db = SessionLocal()
    try:
        tenant = _get_or_create_tenant(db, tenant_code, tenant_name)
        roles = _seed_roles(db, tenant.id)
        perms = _seed_permissions(db, tenant.id)

        granted = 0
        for code, prefixes in ROLE_GRANTS.items():
            if code in roles:
                granted += _grant(db, tenant.id, roles[code], perms, prefixes)

        dept = _seed_root_dept(db, tenant.id)

        dict_types, dict_items_added = _seed_dicts(db, tenant.id)

        ai_models_added, ai_prompts_added = _seed_ai(db, tenant.id)

        tags_added = _seed_tags(db, tenant.id)

        user, changed = _get_or_create_user(
            db, tenant.id, username, password, reset_password=reset_password
        )

        # 绑定 ADM 角色（已绑定则跳过）
        has_adm = db.execute(
            select(SysUserRole).where(
                SysUserRole.tenant_id == tenant.id,
                SysUserRole.user_id == user.id,
                SysUserRole.role_id == roles["ADM"].id,
            )
        ).scalar_one_or_none()
        if has_adm is None:
            db.add(SysUserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["ADM"].id))

        db.commit()

        return {
            "tenant_id": tenant.id,
            "tenant_code": tenant.tenant_code,
            "roles": len(roles),
            "permissions": len(perms),
            "granted_bindings": granted,
            "root_dept_id": dept.id,
            "dict_types": dict_types,
            "dict_items_added": dict_items_added,
            "ai_models_added": ai_models_added,
            "ai_prompts_added": ai_prompts_added,
            "tags_added": tags_added,
            "user_id": user.id,
            "username": user.username,
            "password_changed": changed,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="开发环境种子数据（租户 / 角色 / 权限点 / 首个管理员）"
    )
    parser.add_argument("--tenant-code", default=DEFAULT_TENANT_CODE, help="租户编码")
    parser.add_argument("--tenant-name", default=DEFAULT_TENANT_NAME, help="租户名称")
    parser.add_argument("--username", default="admin", help="管理员登录名")
    parser.add_argument(
        "--password",
        default=os.environ.get("SEED_ADMIN_PASSWORD"),
        help="管理员密码（≥8 位；缺省则随机生成并打印）",
    )
    parser.add_argument(
        "--reset-password", action="store_true", help="用户已存在时重设密码（其余数据不动）"
    )
    args = parser.parse_args()

    password = args.password or _gen_password()
    generated = args.password is None

    try:
        result = seed(
            tenant_code=args.tenant_code,
            tenant_name=args.tenant_name,
            username=args.username,
            password=password,
            reset_password=args.reset_password,
        )
    except Exception as exc:  # noqa: BLE001 — CLI 入口，需给出可读信息
        print(f"\n[FAIL] 种子数据写入失败：{exc}")
        print("       常见原因：① MySQL 未启动；② .env 的 MYSQL_PASSWORD 未填；")
        print("                 ③ 尚未执行 `alembic upgrade head`（表不存在）。")
        return 1

    print("\n[OK] 种子数据就绪")
    print(f"     租户      : {result['tenant_code']} (id={result['tenant_id']})")
    print(f"     角色      : {result['roles']} 个预置角色")
    print(f"     权限点    : {result['permissions']} 个（新增绑定 {result['granted_bindings']} 条）")
    print(f"     根部门    : id={result['root_dept_id']}")
    print(
        f"     字典      : {result['dict_types']} 个类型"
        f"（本次新增 {result['dict_items_added']} 个字典项）"
    )
    print(
        f"     AI 中台   : 本次新增 {result['ai_models_added']} 条模型配置、"
        f"{result['ai_prompts_added']} 条 Prompt（已存在的复用不重建）"
    )
    print(f"     管理员    : {result['username']} (id={result['user_id']})")
    if result["password_changed"] and generated:
        print("\n     🔴 本次随机生成的登录密码（仅显示这一次，请立即记录）：")
        print(f"        {password}")
    elif result["password_changed"]:
        print("     密码已按 --password 设置/重设。")
    else:
        # 🔴 用户已存在且未要求重设 ⇒ **本次生成的随机密码并未生效**，
        #    不能打印它 —— 打印一个「其实没设上去」的密码，会让人以为登录不上
        #    是密码记错了，而不是「压根没用这个密码」。
        print("     用户已存在，密码未改动（如需重设请加 --reset-password）。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
