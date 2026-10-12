"""审计日志服务（TBL-28，F-1.5，接口 API-28 / API-29）。

三条贯穿全文件的规则
====================

🔴 规则一：写入**永远用独立事务**，不复用调用方的 Session
-----------------------------------------------------
这不是实现偷懒，而是被业务语义逼出来的：

- **失败路径必须留痕**：登录失败 / 账号锁定这两个事件本身就是**抛异常**的，
  调用方的业务事务随后会 ``rollback()``。若审计共用那个 Session，
  记录会跟着一起回滚 —— **恰恰把最该留的痕抹掉**。
- **只追加表无锁竞争**：插入的是新行、各月分区互相独立，独立事务不会与业务争锁。
- **避免「顺手提交」事故**：共用 Session 时，审计一旦 commit，
  会把调用方尚未完成的其它改动一并提交 —— 这类 bug 极难定位。

代价与处置：审计可能在业务回滚后仍留下一条记录。故**所有调用点都安排在
业务 ``commit()`` 之后**（本单元已把 CU-07 的 3 处调用点后移），
使「已落库的审计」在时间上晚于「已提交的业务」。

🔴 规则二：**fail-open** —— 审计写失败不得让业务失败
--------------------------------------------------
与 :mod:`app.core.ratelimit` 同源：MySQL 抖动时**放行 + 告警**，
绝不让「记日志」变成「业务不可用」。
⚠️ 但日志级别用 ``ERROR``（不是 ``WARNING``）—— 因为审计写失败意味着
**留痕能力已经受损**，运维必须看到它。此取舍已登记遗留：
生产应加「审计写失败」告警；若某类操作要求 fail-closed（如权限变更），
需单列白名单另行评估。

🔴 规则三：**权限数据只从 JWT 读**（接口 §1.3）
--------------------------------------------
查询主体一律取 ``principal``，前端只能**收窄**（传 ``user_id`` 等筛选条件）。
⚠️ 一处**刻意不套用** ``DataScopeFilter.require_detail_access``：
``AUDIT`` 角色的 ``data_scope=4``（仅统计值）在该判定下会被 40301，
但接口 §4 权限矩阵**明确**给 AUDIT 开放 API-28/29 —— 审计日志是**操作流水**，
不是「人员明细数据」，两者的可见性规则不同。以矩阵为准。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import get_client_ip, get_trace_id, get_user_agent, get_user_id
from app.core.logging import get_logger
from app.core.tenant import CurrentPrincipal
from app.dao import audit_dao, user_admin_dao
from app.dao.session import AuditSessionLocal
from app.exceptions import BizError, ErrorCode
from app.models.audit import AuditLog
from app.schemas.audit import (
    AuditAction,
    AuditExportOut,
    AuditExportRequest,
    AuditFilterRequest,
    AuditLogOut,
    AuditTarget,
)
from app.schemas.common import normalize_per_page

logger = get_logger(__name__)

#: 脱敏黑名单：**键名包含**这些子串的字段一律不落库（接口 API-01 备注：密码/Token 永不写审计）。
#: 用「包含」而不是「相等」匹配 —— 真实字段名有 ``new_password`` / ``password_hash`` /
#: ``oldPassword`` 等变体，列举法必然漏。
#:
#: ⚠️ 刻意**不含** ``key``（太宽泛）：``keyword`` 是正常的审计字段，
#: 用 ``key`` 会把它一起脱敏掉。故改成列举真正敏感的几种写法
#: （``api_key`` / ``apikey`` / ``aes`` / ``encrypt``）。
_FORBIDDEN_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "jwt",
    "authorization",
    "credential",
    "salt",
    "private",
    "apikey",
    "api_key",
    "aes",
    "encrypt",
    "sign_key",
)

#: 单个值的最大长度（超出截断）。防止某个超大文本（如导入的整份文件内容）
#: 被顺手塞进审计，把 MEDIUMTEXT 当数据仓库用。
_MAX_VALUE_LEN = 2000

#: CSV 注入防护前缀（见 :func:`_csv_safe`）
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: 导出 CSV 的列顺序（固定，避免不同版本导出的文件列序不一致导致下游解析失败）
_CSV_HEADER = (
    "id",
    "created_at",
    "user_id",
    "username",
    "action",
    "action_name",
    "target_type",
    "target_name",
    "target_id",
    "ip",
    "ua",
    "trace_id",
    "before",
    "after",
)


class AuditService:
    """审计日志的写入与查询。"""

    # ==================================================================
    # 写入
    # ==================================================================
    @staticmethod
    def record(
        action: str,
        *,
        tenant_id: int,
        user_id: int | None = None,
        target_type: str | None = None,
        target_id: str | int | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        ua: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """写一条审计日志（**独立事务 + fail-open**，见模块 docstring 规则一/二）。

        :param action: 动作码，取值见 :class:`~app.schemas.audit.AuditAction`
        :param tenant_id: 租户 ID（**必填**，调用方一律传 ``principal.tenant_id``）。
            ⚠️ 之所以**必填**而不是「缺省从上下文取」：上下文里根本没有 tenant_id
            （租户是权限数据，只从 JWT 读，不该进「随手可写」的上下文）。
            若设成可选再在运行时兜底，错误会从「调用时立刻 TypeError」
            退化成「运行期静默丢一条日志」—— 后者正是本项目要避免的假成功。
            要求显式传参，等价于**在调用点强制写明归属**。
        :param before: **改前快照**（仅变更类动作有值）
        :param after: **改后快照**；对无「改前/改后」之分的动作（登录、导出），
            这里放**动作结果上下文**（如 ``{"result": "success", "username": ...}``）。
            —— 表结构只有这两列 JSON，故把「结果上下文」归到 ``after`` 而不是新造列。

        🔴 本函数**从不抛异常**：任何失败只记 ``ERROR`` 日志。
        调用方不需要（也不应该）用 try 包住它 —— 那是「怕忘」而不是「有语义」。
        """
        try:
            log = AuditLog(
                tenant_id=int(tenant_id),
                user_id=int(user_id) if user_id is not None else _user_id_from_context(),
                action=str(action)[:64],
                target_type=_clip(target_type, 32),
                target_id=_clip(None if target_id is None else str(target_id), 64),
                before_json=_dump(before),
                after_json=_dump(after),
                ip=_clip(ip if ip is not None else get_client_ip(), 64),
                ua=_clip(ua if ua is not None else get_user_agent(), 255),
                trace_id=_clip(trace_id if trace_id is not None else get_trace_id(), 64),
            )

            # ⚠️ 独立 Session（规则一）：不复用调用方的 db
            # 🔴 且走**审计专用账号**（``AuditSessionLocal``，CU-24）：
            #    该账号在库级只有 SELECT + INSERT，是「只追加」的**第三层**保证 ——
            #    前两层（模型无改删列、DAO 无改删函数）挡的是应用代码，挡不住直接连库写 SQL。
            db = AuditSessionLocal()
            try:
                audit_dao.insert_log(db, log)
                db.commit()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001 — 规则二：fail-open，绝不外抛
            logger.error(
                "审计日志写入失败（已放行，请检查 DB）action=%s err=%s", action, exc, exc_info=True
            )

    # ==================================================================
    # 查询（API-28）
    # ==================================================================
    @staticmethod
    def query(
        db: Session,
        principal: CurrentPrincipal,
        filters: AuditFilterRequest,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[AuditLogOut], int, int, int]:
        """按条件分页查询（时间倒序）。返回 ``(items, total, page, per_page)``。

        ⚠️ 返回值形状与 :meth:`app.services.user_admin_service.UserAdminService.list_users`
        保持一致（都返回归一化后的 ``page`` / ``per_page``）—— 这样 Router 里
        调用 :func:`~app.schemas.common.paginated` 的写法也与 ``/users`` 完全相同，
        不会出现「某个列表接口的 per_page 回显值不对」这类只在特定接口复现的怪异问题。

        归一化在此处而非 Router：``per_page=999`` 要**收敛为 100**（接口 §1.1），
        服务层算 ``offset`` 时用的必须就是收敛后的值，否则第 2 页会跳过一片记录。

        ⚠️ 权限：**租户内全量可见**（矩阵给 ADM/AUDIT），不套行级数据域 ——
        理由见模块 docstring 规则三。
        """
        current = max(1, int(page))
        size = normalize_per_page(per_page)
        kwargs = _filter_kwargs(filters)

        total = audit_dao.count_logs(db, principal.tenant_id, **kwargs)
        rows = audit_dao.list_logs(
            db,
            principal.tenant_id,
            **kwargs,
            limit=size,
            offset=(current - 1) * size,
        )
        return _to_out_list(db, principal.tenant_id, rows), total, current, size

    # ==================================================================
    # 导出（API-29）
    # ==================================================================
    @staticmethod
    def export(db: Session, principal: CurrentPrincipal, req: AuditExportRequest) -> AuditExportOut:
        """导出为 CSV 文本，并**为本次导出行为本身写一条审计**（接口 §2.2 明确要求）。

        🔴 超出 ``max_rows`` 时**报错而不是截断**
        ----------------------------------------
        「导出一半」在审计场景里是危险的：使用者会拿这份文件去做核查，
        却无法从内容上看出它不完整（行数少并不显眼）。因此这里选择 **fail-loud**：
        ``40002`` 明确告知匹配条数与上限，要求收窄条件后重导。

        :raises BizError(40002): 匹配条数超过 ``max_rows``
        """
        kwargs = _filter_kwargs(req)
        total = audit_dao.count_logs(db, principal.tenant_id, **kwargs)
        if total > req.max_rows:
            raise BizError(
                ErrorCode.PARAM_OVER_LIMIT,
                f"匹配 {total} 条，超过单次导出上限 {req.max_rows} 条，请收窄时间范围或筛选条件",
                data={"matched": total, "max_rows": req.max_rows},
            )

        rows = audit_dao.list_logs(db, principal.tenant_id, **kwargs, limit=req.max_rows, offset=0)
        items = _to_out_list(db, principal.tenant_id, rows)
        content = _render_csv(items)
        generated_at = datetime.now()
        filename = f"audit_log_{generated_at.strftime('%Y%m%d_%H%M%S')}.csv"

        # 🔴 导出行为自身留痕（接口 §2.2）—— 必须记录**用了什么筛选条件**，
        #    否则「谁在什么时候导走了什么范围的数据」这个最关键的审计问题答不上来。
        filters_echo = req.model_dump(mode="json", exclude={"format", "max_rows"})
        AuditService.record(
            AuditAction.AUDIT_EXPORT,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            target_type=AuditTarget.AUDIT_LOG,
            target_id=None,
            after={
                "filename": filename,
                "row_count": len(items),
                "filters": filters_echo,
                "format": req.format,
            },
        )

        return AuditExportOut(
            filename=filename,
            format=req.format,
            row_count=len(items),
            content=content,
            filters=filters_echo,
            generated_at=generated_at,
        )


# ============================================================================
# 内部工具
# ============================================================================
def _filter_kwargs(filters: AuditFilterRequest) -> dict[str, Any]:
    """把请求模型摊成 DAO 关键字参数（**唯一**的字段映射点）。

    单独抽出来是为了让 ``count`` 与 ``list`` 不可能用上不同的条件集 ——
    两者的参数由**同一行代码**产出。
    """
    return {
        "user_id": filters.user_id,
        "action": filters.action,
        "target_type": filters.target_type,
        "target_id": filters.target_id,
        "start": filters.start_time,
        "end": filters.end_time,
        "keyword": filters.keyword,
    }


def _user_id_from_context() -> int | None:
    """从上下文取当前用户 ID（鉴权依赖已写入；未鉴权时为 ``None``）。

    ⚠️ 只在调用方**没显式传** ``user_id`` 时兜底（如未登录的失败登录。
    那种场景下 ``principal`` 根本不存在，只能从上下文取）。
    """
    raw = get_user_id()
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _clip(value: str | None, width: int) -> str | None:
    """按列宽截断（``None`` 原样返回）。"""
    if value is None:
        return None
    return value[:width]


def _dump(payload: dict[str, Any] | None) -> str | None:
    """把快照序列化成 JSON 字符串（**含脱敏与截断**）。

    ⚠️ ``ensure_ascii=False``：审计内容多为中文（部门名、岗位名），
    用 ASCII 转义后库里全是 ``\\uXXXX``，人工排查时根本读不了。
    """
    if payload is None:
        return None
    cleaned = _sanitize(payload)
    return json.dumps(cleaned, ensure_ascii=False, default=str)


def _sanitize(payload: Any, *, depth: int = 0) -> Any:
    """递归剔除敏感键、截断超长值（接口 API-01 备注的落库侧防线）。

    🔴 这里是**最后一道防线**：调用方可能把整个 ``req`` 对象丢进来，
    而 ``req`` 里就可能有 ``new_password``。靠「调用方记得别传」是靠不住的，
    因此在这一层按**键名黑名单**兜住。
    """
    if depth > 6:  # 异常深的嵌套：直接截断，避免递归把栈打穿
        return "<嵌套过深已截断>"
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            name = str(key)
            if any(part in name.lower() for part in _FORBIDDEN_KEY_PARTS):
                out[name] = "***（已脱敏，不入审计）***"
                continue
            out[name] = _sanitize(value, depth=depth + 1)
        return out
    if isinstance(payload, (list, tuple)):
        return [_sanitize(v, depth=depth + 1) for v in payload]
    if isinstance(payload, str):
        return payload[:_MAX_VALUE_LEN] + ("…（已截断）" if len(payload) > _MAX_VALUE_LEN else "")
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    # datetime / Decimal / Enum 等：转字符串（``default=str`` 只对顶层生效，这里显式处理）
    return str(payload)


def _to_out_list(db: Session, tenant_id: int, rows: list[AuditLog]) -> list[AuditLogOut]:
    """ORM 行 → 出参；``username`` 用**一次** ``IN`` 查询批量反查（避免 N+1）。"""
    usernames = _resolve_usernames(db, tenant_id, [r.user_id for r in rows if r.user_id])
    out: list[AuditLogOut] = []
    for row in rows:
        uid = row.user_id
        out.append(
            AuditLogOut(
                id=row.id,
                created_at=row.created_at,
                user_id=uid,
                username=usernames.get(int(uid)) if uid is not None else None,
                action=row.action,
                action_name=AuditAction.label_of(row.action),
                target_type=row.target_type,
                target_name=AuditTarget.label_of(row.target_type),
                target_id=row.target_id,
                before=_loads(row.before_json),
                after=_loads(row.after_json),
                ip=row.ip,
                ua=row.ua,
                trace_id=row.trace_id,
            )
        )
    return out


def _loads(text: str | None) -> Any:
    """读取侧解析 JSON（失败原样返回字符串，**绝不抛错**，理由见 schemas 模块同名函数）。"""
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _resolve_usernames(db: Session, tenant_id: int, user_ids: list[int]) -> dict[int, str]:
    """批量反查 ``user_id → username``。

    ⚠️ 查不到（用户已硬删 / 跨租户）时**返回空映射**，出参 ``username`` 为 ``None``，
    前端显示「已删除用户(uid)」即可 —— 审计记录本身必须自洽，
    不能因为关联的用户不在了就显示不出这一条操作。
    """
    ids = {int(i) for i in user_ids if i is not None}
    if not ids:
        return {}
    return user_admin_dao.list_usernames_by_ids(db, tenant_id, ids)


def _render_csv(items: list[AuditLogOut]) -> str:
    """渲染 CSV（UTF-8 + BOM，Excel 双击可直接正确显示中文）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")  # RFC 4180 用 CRLF
    writer.writerow(_CSV_HEADER)
    for item in items:
        writer.writerow(
            [
                _csv_safe(str(item.id)),
                item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else "",
                _csv_safe(str(item.user_id) if item.user_id is not None else ""),
                _csv_safe(item.username or ""),
                _csv_safe(item.action),
                _csv_safe(item.action_name or ""),
                _csv_safe(item.target_type or ""),
                _csv_safe(item.target_name or ""),
                _csv_safe(item.target_id or ""),
                _csv_safe(item.ip or ""),
                _csv_safe(item.ua or ""),
                _csv_safe(item.trace_id or ""),
                _csv_safe(json.dumps(item.before, ensure_ascii=False) if item.before is not None else ""),
                _csv_safe(json.dumps(item.after, ensure_ascii=False) if item.after is not None else ""),
            ]
        )
    return "\ufeff" + buffer.getvalue()


def _csv_safe(value: str) -> str:
    """CSV 公式注入防护（**这不是多余的谨慎**）。

    🔴 本表的 ``ua`` 列内容来自 ``User-Agent`` 请求头 —— **完全由客户端控制**。
    若某条记录的 UA 是 ``=cmd|'/c calc'!A0``（或 ``=HYPERLINK(...)``、``@SUM(...)``），
    导出后使用者**用 Excel 打开就会触发公式执行**（CSV 公式注入，CWE-1236）。
    审计日志的导出文件恰恰是高权限人员会打开的东西，因此必须在写出前转义。

    处置：以公式起始字符开头时**前置一个单引号**（Excel 会把它当纯文本）。
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
