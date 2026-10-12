"""审计日志契约（接口 API-28 / API-29，F-1.5）。

接口 §2.2 只给了这两个接口的一行摘要（方法 / 路径 / 权限 / 表），
与 CU-05/CU-07 的情况相同 —— **本文件即这两个接口的契约事实来源**，
沿用已确立的约定：雪花 ID 一律字符串、时间 `YYYY-MM-DD HH:mm:ss`、
未填字段下发 `null`、请求体 `extra="forbid"`。

## 为什么 `action` 要在本文件里集中定义

``action`` 是 ``VARCHAR(64)`` 的自由文本列 —— 数据库管不住它。
若写入方写 `"dict_item_update"`、筛选方传 `"dict_item_updated"`，
表现是**查询永远返回空列表**（不报错、不告警，最难发现的一类问题）。

因此把取值集中成 :class:`AuditAction` 常量，**写入方与服务端筛选校验共用同一份**：

- 写入方：``audit_service.record(AuditAction.LOGIN, ...)``
- 读入方：API-28 的 ``action`` 参数按 ``Literal`` 校验，拼错直接 **40001**
  （而不是静默查出空结果）。

## 与其它日志表的边界

| 表 | 记什么 | 谁在写 |
| --- | --- | --- |
| **TBL-28 `audit_log`（本文件）** | **操作行为**：登录、配置变更、权限变更、导出 | CU-04/05/06/07 + 本单元 |
| TBL-24 `ai_call_log` | AI 调用（模型 / token / 成本） | CU-12 |
| TBL-22 `talent_change_log` | **人才档案**字段级变更 | CU-22 |

⚠️ 接口 §2.2 曾把 API-26（字典项留痕）指向 TBL-22，但该表 ``talent_id NOT NULL``、
语义是「人才档案变更」，字典项落不进去 → 已改走本表（评审文档 §7.3 批次 4/5 登记）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import DateTimeStr, SnowflakeId


# ============================================================================
# 动作取值表（写入方与筛选方共用）
# ============================================================================
class AuditAction:
    """``audit_log.action`` 的**受控词表**（字符串常量，不是 Enum）。

    为什么用常量类而不是 ``enum.StrEnum``：``action`` 列是 VARCHAR 自由文本，
    词表属于**应用层约定**；用 Enum 会在「历史日志里存在已废弃取值」时
    读取侧反序列化失败（旧的 ``"login"`` 能被 Enum 接受，但一旦有人删掉枚举项，
    **读历史日志就会炸** —— 日志表必须能读十年前的数据）。
    故：常量 + 标签映射，未知取值在读取侧降级为「取值为原样、标签为 ``None``」。
    """

    #: 认证类（CU-04）
    LOGIN = "login"
    LOGOUT = "logout"
    CHANGE_PASSWORD = "change_password"
    #: 会话/权限快照（CU-04）
    PERM_CHANGE = "perm_change"
    #: 字典配置（CU-07）
    DICT_ITEM_CREATE = "dict_item_create"
    DICT_ITEM_UPDATE = "dict_item_update"
    DICT_ITEM_DISABLE = "dict_item_disable"
    #: 组织 / 岗位 / 用户（CU-06）
    DEPT_CREATE = "dept_create"
    DEPT_UPDATE = "dept_update"
    DEPT_DELETE = "dept_delete"
    POSITION_CREATE = "position_create"
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_ROLE_ASSIGN = "user_role_assign"
    USER_IMPORT = "user_import"
    #: 角色与权限绑定（CU-05）
    ROLE_CREATE = "role_create"
    ROLE_UPDATE = "role_update"
    ROLE_PERM_BIND = "role_perm_bind"
    #: 审计自身（API-29：导出行为必须留痕）
    AUDIT_EXPORT = "audit_export"
    #: AI 中台（CU-10 / CU-11）
    #: ⚠️ 模型与 Prompt 的变更是**影响全平台行为**的配置改动（改错会让所有 AI 调用
    #: 换模型 / 换 Prompt），故一律入审计，且 ``after`` 里必须带**改后生效值**。
    AI_MODEL_UPDATE = "ai_model_update"
    AI_PROMPT_CREATE = "ai_prompt_create"
    AI_PROMPT_UPDATE = "ai_prompt_update"
    AI_PROMPT_ROLLBACK = "ai_prompt_rollback"
    #: Agent 人工介入（CU-13，API-94）
    #: 🔴 这是**责任人留痕**：谁在什么时候批准/驳回了哪个 Agent 决策，
    #: 属合规问责链的一环（ADR-11「满足合规问责」）。
    AGENT_INTERRUPT_RESOLVE = "agent_interrupt_resolve"
    AGENT_TASK_RETRY = "agent_task_retry"
    #: 人才域（CU-14 ~ CU-17）：导入 / 复核 / 标签
    #: 🔴 复核确认是**责任人留痕**（F-3.10 与审计双轨：TBL-22 记「值变了」，
    #:    审计记「谁改的」）；标签词表变更影响所有按标签的统计口径，必须可追。
    IMPORT_CREATE = "import_create"
    IMPORT_RETRY = "import_retry"
    REVIEW_CONFIRM = "review_confirm"
    REVIEW_CORRECT = "review_correct"
    REVIEW_SKIP = "review_skip"
    TAG_CREATE = "tag_create"
    TAG_UPDATE = "tag_update"
    TAG_DISABLE = "tag_disable"
    TAG_SUGGEST = "tag_suggest"
    TAG_REVIEW = "tag_review"
    TAG_ASSIGN = "tag_assign"
    #: 查重合并（CU-19）：合并是**不可逆程度极高**的写操作（改了主档、软删了从档、
    #:   删了向量），回滚也改变了两份档案 —— 必须留下「谁在什么时候合并了谁」的问责链。
    DEDUP_MERGE = "dedup_merge"
    DEDUP_IGNORE = "dedup_ignore"
    DEDUP_ROLLBACK = "dedup_rollback"
    #: 检索与问答（CU-20 / CU-21）：接口 API-62 备注明确「检索行为写审计」——
    #:   F-1.5 要求审计记录「检索行为」；``query`` 原文入 ``after``，
    #:   用于后续构建金标准查询集（IDX-01，≥50 条）。问答同理（谁问了什么、走了哪路模型）。
    TALENT_SEARCH = "talent_search"
    TALENT_ASK = "talent_ask"
    #: 档案主线写与出库（CU-29）：**列表 / 详情这类读动作刻意不写审计** ——
    #:   档案列表是最高频入口，每翻一页写一条审计会在一天内把日志表刷爆，
    #:   真正的合规动作反而被噪声淹没（审计的价值在于「少而准」，不是「全」）。
    #:   故只登记三类**不可逆或数据出库**的动作：
    #:   ① 编辑（改主档，TBL-22 记「值变了」、审计记「谁改的」，双轨不重复）；
    #:   ② 归档（软删 + 向量清理，不可逆程度高）；
    #:   ③ 导出（数据出库 —— 与 API-29「导出行为自身写审计」同源）。
    TALENT_EDIT = "talent_edit"
    TALENT_ARCHIVE = "talent_archive"
    TALENT_EXPORT = "talent_export"
    #: 脱敏自检（CU-29 · API-66）：🔴 ``after`` **只记条数与漏检数，不记样例内容** ——
    #:   样例里装的就是待检的明文 PII。自检器把 PII 写进审计，
    #:   等于用一个「查泄漏」的动作制造了一次泄漏。
    MASK_VERIFY = "mask_verify"
    #: 问答会话回看与清空（CU-29 · API-64/65）：问题原文属**业务数据**
    #:   （接口 §2.2 明确「仅 ADM/HR 经 API-64 可见」），故回看行为必须留痕；
    #:   清空是破坏性动作（抹掉对话证据），同样必须可追。
    ASK_SESSION_VIEW = "ask_session_view"
    ASK_SESSION_CLEAR = "ask_session_clear"
    #: 变更留痕与个人权利（CU-22）：F-3.10 / F-3.11 是**合规动作**，
    #:   最需要回答的两个问题都要靠这里的动作码：
    #:   ① 「谁在什么时候看了某人的历史快照」（``CHANGE_SNAPSHOT_VIEW``）——
    #:      回溯是合规证据，查阅行为本身也要留痕（与 API-29 导出自身写审计同源）；
    #:   ② 「谁审批了删除 / 谁下载了数据包」（``PRIVACY_DELETE_APPROVE`` /
    #:      ``PRIVACY_EXPORT_DOWNLOAD``）—— 不可逆与数据出库两类动作的问责链。
    #:   ⚠️ 个人权利类动作**全部**记审计：请求单（TBL-30）只记「单子到哪一步」，
    #:      不记「谁点了按钮」—— 两者缺一不可，不重复。
    CHANGE_SNAPSHOT_VIEW = "change_snapshot_view"
    PRIVACY_SELF_VIEW = "privacy_self_view"
    PRIVACY_CORRECT_SUBMIT = "privacy_correct_submit"
    PRIVACY_CORRECT_RESOLVE = "privacy_correct_resolve"
    PRIVACY_DELETE_SUBMIT = "privacy_delete_submit"
    PRIVACY_DELETE_APPROVE = "privacy_delete_approve"
    PRIVACY_EXPORT_SUBMIT = "privacy_export_submit"
    PRIVACY_EXPORT_DOWNLOAD = "privacy_export_download"

    #: 取值 → 中文标签（供 API-28 直接渲染，前端不必再维护一份映射）
    LABELS: ClassVar[dict[str, str]] = {
        LOGIN: "登录",
        LOGOUT: "登出",
        CHANGE_PASSWORD: "修改密码",
        PERM_CHANGE: "权限变更",
        DICT_ITEM_CREATE: "新增字典项",
        DICT_ITEM_UPDATE: "修改字典项",
        DICT_ITEM_DISABLE: "停用字典项",
        DEPT_CREATE: "新建部门",
        DEPT_UPDATE: "修改部门",
        DEPT_DELETE: "删除部门",
        POSITION_CREATE: "新建岗位",
        USER_CREATE: "新建用户",
        USER_UPDATE: "修改用户",
        USER_ROLE_ASSIGN: "分配角色",
        USER_IMPORT: "批量导入用户",
        ROLE_CREATE: "新建角色",
        ROLE_UPDATE: "修改角色",
        ROLE_PERM_BIND: "角色权限绑定",
        AUDIT_EXPORT: "导出审计日志",
        AI_MODEL_UPDATE: "修改模型配置",
        AI_PROMPT_CREATE: "新建 Prompt",
        AI_PROMPT_UPDATE: "修改 Prompt（新版本）",
        AI_PROMPT_ROLLBACK: "回滚 Prompt",
        AGENT_INTERRUPT_RESOLVE: "处理人工待办",
        AGENT_TASK_RETRY: "重试 Agent 任务",
        IMPORT_CREATE: "创建导入任务",
        IMPORT_RETRY: "重试失败文件",
        REVIEW_CONFIRM: "批量确认复核",
        REVIEW_CORRECT: "修正解析字段",
        REVIEW_SKIP: "标记免复核",
        TAG_CREATE: "新增标签",
        TAG_UPDATE: "修改标签",
        TAG_DISABLE: "停用标签",
        TAG_SUGGEST: "提交 AI 建议标签",
        TAG_REVIEW: "审核标签建议",
        TAG_ASSIGN: "调整人才标签",
        DEDUP_MERGE: "合并重复档案",
        DEDUP_IGNORE: "忽略重复候选",
        DEDUP_ROLLBACK: "回滚合并",
        TALENT_SEARCH: "语义人才检索",
        TALENT_ASK: "档案 RAG 问答",
        TALENT_EDIT: "编辑人才档案",
        TALENT_ARCHIVE: "归档人才档案",
        TALENT_EXPORT: "导出人才档案",
        MASK_VERIFY: "脱敏自检",
        ASK_SESSION_VIEW: "回看问答会话",
        ASK_SESSION_CLEAR: "清空问答会话",
        CHANGE_SNAPSHOT_VIEW: "查看时间点快照",
        PRIVACY_SELF_VIEW: "查阅本人档案",
        PRIVACY_CORRECT_SUBMIT: "提交更正申请",
        PRIVACY_CORRECT_RESOLVE: "处理更正申请",
        PRIVACY_DELETE_SUBMIT: "提交删除申请",
        PRIVACY_DELETE_APPROVE: "审批删除申请",
        PRIVACY_EXPORT_SUBMIT: "提交导出申请",
        PRIVACY_EXPORT_DOWNLOAD: "下载个人数据包",
    }

    #: 允许出现在 API-28 ``action`` 参数中的取值（供 ``Literal`` 校验用）
    ALL: ClassVar[tuple[str, ...]] = tuple(LABELS)

    @classmethod
    def label_of(cls, action: str | None) -> str | None:
        """取中文标签；**未知取值返回 ``None`` 而不是抛错**（见类 docstring）。"""
        return cls.LABELS.get(action) if action else None


class AuditTarget:
    """``audit_log.target_type`` 的受控词表（对象类型）。

    与 :class:`AuditAction` 同样用常量而非 Enum；未知取值读取侧原样返回。
    """

    SESSION = "session"
    USER = "user"
    ROLE = "role"
    PERMISSION = "permission"
    DEPT = "dept"
    POSITION = "position"
    DICT_TYPE = "dict_type"
    DICT_ITEM = "dict_item"
    AUDIT_LOG = "audit_log"
    #: AI 中台与 Agent（CU-10 ~ CU-13）
    AI_MODEL = "ai_model"
    PROMPT = "prompt"
    AGENT_TASK = "agent_task"
    AGENT_INTERRUPT = "agent_interrupt"
    #: 人才域（CU-14 ~ CU-17）
    IMPORT_TASK = "import_task"
    TALENT = "talent"
    TALENT_FIELD = "talent_field"
    TAG = "tag"
    #: 变更留痕与个人权利（CU-22）：
    #:   ``PRIVACY_REQUEST`` = TBL-30 权利请求单（``target_id`` 为请求 ID）；
    #:   ``CHANGE_LOG`` = TBL-22 留痕（看快照时指向被查看的档案，不留痕行 ID ——
    #:   因为「查了一次快照」并没有产生新的留痕行，指过去会是个不存在的 ID）。
    PRIVACY_REQUEST = "privacy_request"
    CHANGE_LOG = "change_log"

    LABELS: ClassVar[dict[str, str]] = {
        SESSION: "会话",
        USER: "用户",
        ROLE: "角色",
        PERMISSION: "权限点",
        DEPT: "部门",
        POSITION: "岗位",
        DICT_TYPE: "字典类型",
        DICT_ITEM: "字典项",
        AUDIT_LOG: "审计日志",
        AI_MODEL: "模型配置",
        PROMPT: "Prompt 模板",
        AGENT_TASK: "Agent 任务",
        AGENT_INTERRUPT: "人工待办",
        IMPORT_TASK: "导入任务",
        TALENT: "人才档案",
        TALENT_FIELD: "解析字段",
        TAG: "标签",
        PRIVACY_REQUEST: "权利请求单",
        CHANGE_LOG: "变更留痕",
    }

    @classmethod
    def label_of(cls, target_type: str | None) -> str | None:
        return cls.LABELS.get(target_type) if target_type else None


# ============================================================================
# 出参（API-28）
# ============================================================================
def _loads_or_raw(text: str | None) -> Any:
    """把库里的 JSON 字符串还原成对象。

    ⚠️ 解析失败时**原样返回字符串**而不是抛错：审计日志的价值在于「能读出来」，
    若某条记录因历史原因存了非法 JSON（早期版本、人工插入），
    一次导入错误就会让整个列表接口 500 —— 那等于**日志把自己锁死**。
    读取侧永远降级、不阻塞。
    """
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


class AuditLogOut(BaseModel):
    """单条审计日志。

    🔴 **本模型没有「修改时间」字段** —— 表里也不存在。
    这是「只追加」在契约层的体现：一条审计记录生成即定稿。
    """

    model_config = ConfigDict(extra="forbid")

    id: SnowflakeId = Field(..., description="日志 ID（雪花 ID 字符串）")
    created_at: DateTimeStr = Field(..., description="操作时间（分区键）")
    user_id: SnowflakeId | None = Field(None, description="操作人 ID（系统动作时为 null）")
    username: str | None = Field(None, description="操作人账号（由 user_id 批量反查，便于直接展示）")
    action: str = Field(..., description="动作码，取值见 `AuditAction` 词表")
    action_name: str | None = Field(None, description="动作中文名（未知取值时为 null）")
    target_type: str | None = Field(None, description="对象类型码")
    target_name: str | None = Field(None, description="对象类型中文名")
    target_id: str | None = Field(None, description="对象 ID（字符串，兼容雪花 ID 与业务编码）")
    before: Any = Field(None, description="改前快照（已解析为对象；非 JSON 时原样为字符串）")
    after: Any = Field(None, description="改后快照（同上）")
    ip: str | None = Field(None, description="来源 IP")
    ua: str | None = Field(None, description="User-Agent")
    trace_id: str | None = Field(None, description="链路 ID（可与日志/响应头 X-Request-Id 对照）")


class AuditLogPageOut(BaseModel):
    """分页列表的形状说明（**仅供文档**，不作为 ``response_model``）。

    接口 §1.1 规定分页响应的 ``data`` 是**数组**、``page`` 与 ``data`` **同级**，
    若把 ``data`` 包成 ``{total, items}`` 就与 ``/users``、``/talents`` 不一致了。
    故 API-28 的真实 ``response_model`` 是 ``CommonResp[list[AuditLogOut]]``，
    本模型只在 OpenAPI 描述里引用，用来把「形状长什么样」写进文档。
    """

    model_config = ConfigDict(extra="forbid")

    items: list[AuditLogOut] = Field(default_factory=list, description="审计日志数组（即 `data`）")


# ============================================================================
# 入参（API-28 / API-29 共用的筛选条件）
# ============================================================================
#: 时间窗跨度上限（天）。审计表按月分区，**跨月查询无法被分区裁剪**，
#: 单次扫全表在 10⁷ 量级上会明显拖慢；90 天足够覆盖常规审计核查场景。
MAX_RANGE_DAYS = 90

#: 导出行数上限。⚠️ 为什么不做成「异步导出任务」：一期无对象存储（MinIO 未就绪，N0 实测
#: 连接被拒），同步返回受响应体大小约束，故设硬上限并明确报错；
#: 需要更大规模导出时再引入异步任务 + MinIO（已登记遗留）。
MAX_EXPORT_ROWS = 50_000


class AuditFilterRequest(BaseModel):
    """筛选条件（API-28 的 query 参数与 API-29 的请求体**共用同一套语义**）。

    两处共用一个模型是为了避免「页面能筛出来的、导出的却是全量」——
    这是审计系统里最容易发生、也最容易被忽略的不一致。
    """

    model_config = ConfigDict(extra="forbid")

    user_id: SnowflakeId | None = Field(None, description="按操作人筛选")
    action: str | None = Field(None, max_length=64, description="按动作码筛选（精确匹配）")
    target_type: str | None = Field(None, max_length=32, description="按对象类型筛选")
    target_id: str | None = Field(None, max_length=64, description="按对象 ID 筛选")
    start_time: datetime | None = Field(None, description="起始时间（含），`YYYY-MM-DD HH:mm:ss`")
    end_time: datetime | None = Field(None, description="截止时间（含）")
    keyword: str | None = Field(
        None, max_length=128, description="模糊搜索 `target_id` / `trace_id`"
    )

    @field_validator("action", "target_type", "target_id", "keyword", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """空字符串视为未传 —— 前端表单清空后常发出 ``?action=``，
        若当成「筛选 action 等于空串」会查出 0 条，用户会以为「日志丢了」。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_vocabulary(self) -> AuditFilterRequest:
        """``action`` / ``target_type`` 必须在受控词表内。

        🔴 为什么这里要**拒绝**而不是放行后查空：
        ``action`` 是 ``VARCHAR(64)`` 自由文本，前端手输或旧版本前端拼错一个字母
        （``dict_item_updated`` vs ``dict_item_update``）的结果是
        **「查询成功、返回 0 条」** —— 使用者会合理推断「这段时间没人改过字典」，
        从而得出错误结论。返回 **40001 并列出合法取值**，把猜测变成明确提示。

        ⚠️ 这只影响**输入过滤值**，不影响读取历史数据：
        库里若存在已废弃的 ``action``（词表里已删掉），
        该行**仍会被正常列出**（:meth:`AuditAction.label_of` 对未知值返回 ``None``）。
        两者是不同的东西 —— 前者是「你不能用它来筛」，后者是「它必须能被读出来」。
        """
        if self.action and self.action not in AuditAction.LABELS:
            raise ValueError(
                f"未知的 action={self.action!r}；合法取值：{', '.join(AuditAction.ALL)}"
            )
        if self.target_type and self.target_type not in AuditTarget.LABELS:
            raise ValueError(
                f"未知的 target_type={self.target_type!r}；"
                f"合法取值：{', '.join(sorted(AuditTarget.LABELS))}"
            )
        return self

    @model_validator(mode="after")
    def _check_range(self) -> AuditFilterRequest:
        """校验时间窗：起止颠倒 → 40001；跨度超上限 → 40001 并提示收窄。"""
        if self.start_time and self.end_time and self.start_time > self.end_time:
            raise ValueError("start_time 不能晚于 end_time")
        if self.start_time and self.end_time:
            span_days = (self.end_time - self.start_time).days
            if span_days > MAX_RANGE_DAYS:
                raise ValueError(
                    f"时间跨度不得超过 {MAX_RANGE_DAYS} 天（当前 {span_days} 天），请收窄后重试"
                )
        return self


class AuditExportRequest(AuditFilterRequest):
    """API-29 导出请求：筛选条件 + 格式 + 上限。

    ⚠️ 刻意**不提供**「导出全部」的开关：审计表是 10⁷ 量级，
    「给我全部」这个操作本身在生产上就不该存在（它既拖垮库、又让下载失去意义）。
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["csv"] = Field("csv", description="导出格式，当前仅支持 csv")
    max_rows: int = Field(
        10_000,
        ge=1,
        le=MAX_EXPORT_ROWS,
        description=f"最多导出条数，上限 {MAX_EXPORT_ROWS}（超出请收窄筛选条件）",
    )


class AuditExportOut(BaseModel):
    """API-29 出参。

    🔴 **为什么把 CSV 内容放在 ``data.content`` 里，而不是返回文件流**
    ---------------------------------------------------------------
    接口 §1.1 的红线是「所有接口统一响应五件套」，文件流天然无法包裹
    （返回 ``text/csv`` 就只能放弃 ``code``/``trace_id``）。
    在「统一响应」与「二进制流」之间，本项目选择前者，原因是：

    1. ``trace_id`` 必须能串起来 —— 审计导出恰恰是最需要归因的操作；
    2. 前端已有统一拦截器（§4 第 3 层）按 ``code != 0`` 抛业务异常，
       文件流会让它失效，导出失败时用户只能看到一个坏掉的文件；
    3. 一期已设 ``max_rows`` 上限，单次响应体可控。

    代价与替代：超大规模导出需改为「异步任务 + MinIO 下载链接」，
    待 MinIO 就绪后另开单元（已登记遗留）。
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., description="建议文件名（含时间戳，前端直接用它落盘）")
    format: Literal["csv"] = Field("csv", description="导出格式")
    row_count: int = Field(0, description="本次导出的记录条数")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="本次导出实际生效的筛选条件回显 —— 让「导走了什么范围」有据可查"
        "（同一份条件也会写入审计，两处一致）",
    )
    content: str = Field("", description="CSV 文本（UTF-8，含表头，已带 BOM 供 Excel 直接打开）")
    generated_at: DateTimeStr = Field(..., description="生成时间")

    # ⚠️ 刻意**没有** ``truncated`` 字段：超限时服务层直接报 40002 而不是截断
    #    （「导出一半却看不出不完整」是审计场景里最危险的一类静默错误）。
