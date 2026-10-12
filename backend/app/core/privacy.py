"""变更留痕与个人权利的**确定性组件**（CU-22，F-3.10 / F-3.11）—— 铁律 10 的落点。

为什么这些逻辑必须放在 ``core/`` 且是纯函数
==========================================
本单元有两类「结论」，都不能由大模型或散落的 ``if`` 产生：

| 结论 | 为什么必须确定性 | 本模块的落点 |
| --- | --- | --- |
| **时间点快照**（API-69） | 它是「档案在某天长什么样」的**合规证据**。同一次回溯换一次计算得出不同的值，等于自证留痕不可信 | :func:`build_snapshot`（反向叠加，无 IO） |
| **处理时限统计**（API-80） | 是《个保法》履行情况的**对外数字**。分子口径一旦随实现漂移，合规报告就不可复核 | :func:`summarize_requests`（纯函数） |

二者都做成**接收普通数据结构、返回 dataclass** 的纯函数：
Service 负责把 ORM 行翻译成 :class:`ChangeRecord` / :class:`RequestRecord`（IO 在 Service/DAO），
于是「回溯算法」与「时限口径」可以被单测逐条钉死（``tests/test_change_log.py``），
而**不需要**任何数据库 —— 这也是本单元 L1 用例能覆盖核心算法的原因。

🔴 一处刻意的取舍：**可更正字段白名单**
--------------------------------------
F-3.11 的「更正」不是「本人可以改档案里的任何东西」。白名单把两类字段排除在外：

1. **不可由本人单方面更正的字段**：``id_card``（证件号是**精确去重的唯一键**，
   改错会让查重、合并、去重全部失真，必须走线下核验）、以及尚无明细表的履历类字段；
2. **派生字段**：``profile_vec_id`` / ``version_no`` / ``data_status`` ——
   它们是系统计算的产物，不是「本人陈述的事实」，放进白名单等于让本人改系统状态。

白名单同时决定了 **API-74 批准时写哪一列**，所以它必须是**唯一一份**（不散落在 service 与 schema 两处）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from app.core.dup import MERGEABLE_FIELDS
from app.models.enums import ChangeSource, PrivacyRequestStatus, PrivacyRequestType
from app.schemas.common import DATETIME_FORMAT

# ============================================================================
# 一、可更正字段白名单（API-72 提交 / API-74 生效 共用）
# ============================================================================
FieldKind = Literal["str", "int", "date"]


@dataclass(frozen=True, slots=True)
class CorrectableField:
    """一个「本人可以申请更正」的字段的完整口径。"""

    code: str
    label: str
    kind: FieldKind
    max_len: int
    #: 对应 ``talent_base`` 的列（默认与 ``code`` 同名）
    column: str = ""
    #: 是否属于「加密存储」列（手机/邮箱）—— 更正时需重算加密值（+ 盐化哈希）
    encrypted: bool = False

    def __post_init__(self) -> None:  # pragma: no cover - 仅用于装配期自检
        if not self.column:
            # frozen dataclass 只能通过 object.__setattr__ 补默认值
            object.__setattr__(self, "column", self.code)


CORRECTABLE_FIELDS: dict[str, CorrectableField] = {
    "name": CorrectableField("name", "姓名", "str", 64),
    "gender": CorrectableField("gender", "性别", "int", 1),
    "birth_date": CorrectableField("birth_date", "出生年月", "date", 10),
    "education_level": CorrectableField("education_level", "学历", "str", 32),
    "school": CorrectableField("school", "院校", "str", 128),
    "major": CorrectableField("major", "专业", "str", 128),
    "work_years": CorrectableField("work_years", "工作年限", "int", 3),
    # 🔴 手机号是「精确去重键」的原料之一：更正要同时重算 ``phone_enc`` 与 ``phone_hash``
    #    （只改其一 → 档案里存着新号、去重却按旧号比，是最难发现的一类脏数据）。
    "phone": CorrectableField("phone", "手机号", "str", 20, column="phone_enc", encrypted=True),
    "email": CorrectableField("email", "邮箱", "str", 128, column="email_enc", encrypted=True),
}

#: 白名单的稳定序（用于报错信息与出参，保证同输入同输出）
CORRECTABLE_FIELD_CODES: tuple[str, ...] = tuple(CORRECTABLE_FIELDS)

#: 明确**不开放**更正的字段及理由（写进契约的说明字段，避免用户以为「漏了」）
CORRECTION_EXCLUDED: dict[str, str] = {
    "id_card": "证件号为精确去重唯一键，更正须走线下核验后由管理员在档案侧维护",
    "data_status": "档案状态由系统流程决定，不接受本人申请",
    "version_no": "版本号为系统计算值",
    "profile_vec_id": "向量主键为派生索引，由系统重建",
}


def get_correctable(code: str | None) -> CorrectableField | None:
    """取白名单条目；不在白名单返回 ``None``（由调用方决定报什么错）。"""
    if not code:
        return None
    return CORRECTABLE_FIELDS.get(code.strip())


def coerce_corrected_value(field_def: CorrectableField, raw: str | None) -> Any:
    """把本人填的字符串按字段类型转成可写库的值（确定性，失败抛 :class:`ValueError`）。

    为什么要在**批准生效前**再做一次类型转换，而不是在提交时直接信任：
    提交与批准之间可能隔几天，期间字段类型口径（或历史脏数据）都不能假定一致；
    转换失败必须在**写库之前**变成 42201，绝不能把 ``work_years="五年"`` 写进 INT 列
    （MySQL 非严格模式下会**静默截断成 0**，于是「更正」反而把数据改坏了）。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{field_def.label}不能为空")
    if len(text) > field_def.max_len:
        raise ValueError(f"{field_def.label}长度不得超过 {field_def.max_len} 个字符")
    if field_def.kind == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{field_def.label}必须是整数") from exc
    if field_def.kind == "date":
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"{field_def.label}格式应为 YYYY-MM-DD")
    return text


def render_value(value: Any) -> str | None:
    """把库里的值渲染成「可比较的字符串」（提交侧乐观锁比对、留痕 before/after 共用）。

    ⚠️ 必须与 :func:`coerce_corrected_value` 互为逆运算，否则会出现
    「本人填的值与库里同值却判为不一致」（前端把 ``5`` 显示成 ``5.0`` 之类）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(DATETIME_FORMAT)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


#: 「写入留痕 / 审计时必须脱敏」的字段（加密存储的联系方式）。
#: 🔴 为什么必须收在这里而不是各写各的：``talent_change_log`` 与 ``audit_log``
#:   都是 ADM/HR/AUDIT 可读的表 —— 谁把手机号明文写进去，就等于把 PII 从
#:   加密列「搬」到明文列，AES 就白做了。这是**口径**，只能有一份。
MASKED_FIELD_CODES: frozenset[str] = frozenset({"phone", "email"})


def log_field_value(code: str, plain_value: Any) -> str | None:
    """该字段在**留痕 / 审计**里的可记录形态（确定性，无 IO）。

    - 加密字段（:data:`MASKED_FIELD_CODES`）→ **脱敏串**（``138****5678`` / ``l***@x.com``）；
    - 其余字段 → :func:`render_value` 的常规渲染。

    传入的 ``plain_value`` 是**已解密的明文**（调用方负责解密；``core`` 不碰 ORM）。
    效果上留痕只证明「变了」、不暴露「变成什么」——
    F-3.11 要求「更正可追溯」，并不要求「更正内容对所有人可见」。
    """
    if code in MASKED_FIELD_CODES:
        # 局部导入：crypto 依赖 config，此处延迟到调用点可避免 core 内互相牵扯的
        # 导入顺序问题（两者本就无环，但保持 import 图扁平更容易审计）
        from app.core.crypto import mask_email, mask_phone

        text = render_value(plain_value)
        return mask_phone(text) if code == "phone" else mask_email(text)
    return render_value(plain_value)


# ============================================================================
# 二、变更留痕字段口径（API-67~70 与快照回溯共用）
# ============================================================================
#: 可回溯的档案字段 = 可合并字段（``core.dup.MERGEABLE_FIELDS``）+ 状态类字段。
#: 🔴 与 ``MERGEABLE_FIELDS`` 保持**推导关系**而不是复制一份字面量：
#: 「哪些字段会进合并」与「哪些字段会进留痕」在本项目**必须一致**——
#: 合并若写了留痕里没有的字段，回溯就会漏掉那一段历史。
TRACKABLE_FIELDS: tuple[str, ...] = (*MERGEABLE_FIELDS, "data_status")

#: 留痕来源 → 展示名（复用枚举，避免再维护一份映射）
SOURCE_LABELS: dict[int, str] = {
    int(m): ChangeSource.name_of(int(m)) or str(int(m)) for m in ChangeSource
}

#: 中文标签表（``field_name`` → 展示名）：留痕里也可能是 ``data_status`` 这类非档案字段
FIELD_LABELS: dict[str, str] = {
    **{code: d.label for code, d in CORRECTABLE_FIELDS.items()},
    "data_status": "档案状态",
    "version_no": "版本号",
    "tags": "标签集合",
}


def field_label(field_name: str | None) -> str | None:
    if not field_name:
        return None
    return FIELD_LABELS.get(field_name, field_name)


# ============================================================================
# 三、时间点快照回溯（API-69）—— 反向叠加
# ============================================================================
@dataclass(frozen=True, slots=True)
class ChangeRecord:
    """一条留痕的**纯数据形态**（core 不依赖 ORM；Service 负责翻译）。"""

    log_id: int
    created_at: datetime
    field_name: str | None
    before_value: str | None
    after_value: str | None
    change_source: int
    operator_id: int | None = None


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """时间点快照的确定性结果。"""

    values: dict[str, Any]
    effective_from: datetime
    effective_to: datetime | None
    changes_after: int
    untracked_fields: list[str] = field(default_factory=list)
    snapshot_from_change_log_id: int | None = None
    #: 被反向回退过的字段（解释用：「这份快照相对当前值改了哪几项」）
    restored_fields: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "changes_after": self.changes_after,
            "untracked_fields": list(self.untracked_fields),
            "snapshot_from_change_log_id": self.snapshot_from_change_log_id,
            "restored_fields": list(self.restored_fields),
        }


def _coerce_tracked(field_name: str, raw: str | None) -> Any:
    """留痕里的值是 TEXT，出参要还原成业务类型（失败则 ``None``，不抛错）。

    ⚠️ 失败**不抛错**：留痕是 10⁷ 量级的历史数据，允许存在早期版本写入的
    非规范值（如 ``data_status`` 曾写成中文）。一条脏值让整次回溯 500，
    等于「因为历史不完美而无法证明历史」。
    """
    if raw is None:
        return None
    text = str(raw)
    if field_name in ("gender", "work_years", "data_status", "version_no"):
        try:
            return int(text)
        except ValueError:
            return None
    if field_name == "birth_date":
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    if field_name == "name":
        return text
    return text


def build_snapshot(
    *,
    current_values: dict[str, Any],
    records: list[ChangeRecord],
    at: datetime,
    created_at: datetime,
    field_codes: list[str] | None = None,
) -> SnapshotResult:
    """重建档案在 ``at`` 时刻的字段值（**反向叠加**，纯函数）。

    算法（为什么是反向）
    -------------------
    留痕只记「每次变更的前值 / 后值」，不存整份快照（那样存储会爆炸）。
    要还原 **T 时刻** 的值，最直接的做法是从当前值出发，把 **T 之后**的变更
    **逐条倒着回退**（取那次变更的 ``before_value``）—— 倒序保证「更早的那条最后生效」。
    对单个字段而言等价于：**T 之后最早那条变更的 before_value**。

    :param current_values: 当前字段值（``{字段名: 值}``，Service 从 ``talent_base`` 摘）
    :param records: 该档案的**全部**留痕（顺序不限，函数内部自行排序）
    :param at: 回溯时间点
    :param created_at: 档案创建时间（``at`` 早于它 → 由调用方报 40001，本函数只用于兜 effective_from）
    :param field_codes: 只回溯指定字段（``None`` = 全部 :data:`TRACKABLE_FIELDS`）

    :return: :class:`SnapshotResult`
    """
    wanted = [c.strip() for c in (field_codes or []) if c and c.strip()] or list(TRACKABLE_FIELDS)
    # 未知字段名直接按「无留痕」处理 —— 调用方（契约层）已按白名单拦过，这里只是兜底
    unknown = [c for c in wanted if c not in TRACKABLE_FIELDS]

    by_field: dict[str, list[ChangeRecord]] = {}
    for rec in records:
        if not rec.field_name:
            continue
        by_field.setdefault(str(rec.field_name), []).append(rec)
    for items in by_field.values():
        items.sort(key=lambda r: (r.created_at, r.log_id))

    values: dict[str, Any] = {}
    untracked: list[str] = []
    restored: list[str] = []
    for code in wanted:
        if code not in TRACKABLE_FIELDS:
            untracked.append(code)
            values[code] = None
            continue
        history = by_field.get(code) or []
        if not history:
            # 无留痕 → 取当前值，并**显式列出**（接口 §2.2 API-69 备注的要求）
            untracked.append(code)
            values[code] = current_values.get(code)
            continue
        after = [r for r in history if r.created_at > at]
        if after:
            oldest_after = after[0]  # 已按时间升序
            values[code] = _coerce_tracked(code, oldest_after.before_value)
            if values[code] != current_values.get(code):
                restored.append(code)
        else:
            latest = history[-1]
            values[code] = _coerce_tracked(code, latest.after_value)

    # ---- 生效区间与「之后还有几次变更」----
    at_or_before = [r for r in records if r.created_at <= at]
    after_all = [r for r in records if r.created_at > at]
    effective_from = (
        max(at_or_before, key=lambda r: (r.created_at, r.log_id)).created_at
        if at_or_before
        else created_at
    )
    effective_to = min(after_all, key=lambda r: (r.created_at, r.log_id)).created_at if after_all else None
    from_id = (
        max(at_or_before, key=lambda r: (r.created_at, r.log_id)).log_id if at_or_before else None
    )

    return SnapshotResult(
        values=values,
        effective_from=effective_from,
        effective_to=effective_to,
        changes_after=len(after_all),
        untracked_fields=sorted({*untracked, *unknown}),
        snapshot_from_change_log_id=from_id,
        restored_fields=sorted(restored),
    )


# ============================================================================
# 四、处理时限统计（API-80）—— 合规数字的口径
# ============================================================================
@dataclass(frozen=True, slots=True)
class RequestRecord:
    """一条权利请求的**纯数据形态**。"""

    request_type: int
    status: int
    created_at: datetime
    sla_hours: int
    processed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TypeSummary:
    request_type: int
    request_type_name: str
    total: int
    pending: int
    approved: int
    rejected: int
    overdue: int
    avg_processing_hours: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_type": self.request_type,
            "request_type_name": self.request_type_name,
            "total": self.total,
            "pending": self.pending,
            "approved": self.approved,
            "rejected": self.rejected,
            "overdue": self.overdue,
            "avg_processing_hours": self.avg_processing_hours,
        }


@dataclass(frozen=True, slots=True)
class SlaSummary:
    """API-80 的处理时限统计（**总览 + 分类型**）。"""

    total: int
    pending: int
    approved: int
    rejected: int
    processing: int
    overdue: int
    overdue_ratio: float
    avg_processing_hours: float | None
    max_processing_hours: float | None
    sla_hours: int
    by_type: list[TypeSummary] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "pending": self.pending,
            "approved": self.approved,
            "rejected": self.rejected,
            "processing": self.processing,
            "overdue": self.overdue,
            "overdue_ratio": self.overdue_ratio,
            "avg_processing_hours": self.avg_processing_hours,
            "max_processing_hours": self.max_processing_hours,
            "sla_hours": self.sla_hours,
            "by_type": [t.as_dict() for t in self.by_type],
        }


def _hours(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 3600.0, 2)


#: 非终态（对外仍是「没有结果」）——与 :func:`summarize_requests` 的口径 ① 共用
OPEN_STATUSES: frozenset[int] = frozenset({0, 3})


def waited_hours(created_at: datetime, now: datetime) -> float:
    """已等待时长（小时，2 位小数）。"""
    return _hours(created_at, now)


def is_overdue(
    *,
    status: int,
    created_at: datetime,
    sla_hours: int,
    now: datetime,
    processed_at: datetime | None = None,
) -> bool:
    """单条请求是否「当前超时」（**单行展示与总览统计共用同一口径**）。

    🔴 已终态的单子**永远不算超时**（哪怕它当时确实处理超时了）：
    行内标记的用途是「提醒现在该处理谁」，不是「记过」。历史超时只体现在
    ``summarize_requests`` 的处理时长分布里 —— 两处口径分开才不会被互相污染。
    """
    if int(status) not in OPEN_STATUSES:
        return False
    end = processed_at or now
    return waited_hours(created_at, end) > float(sla_hours or 0)


def summarize_requests(
    records: list[RequestRecord],
    *,
    now: datetime,
    sla_hours: int,
    type_names: dict[int, str] | None = None,
) -> SlaSummary:
    """按 SM-3 的口径统计处理时限（纯函数）。

    三条口径（写死在这里，避免「同一批数据算出两个合规数字」）：

    1. **超时**只对**未终态**（``待处理`` / ``处理中``）的单子计算 ——
       已通过 / 已驳回的单子即便当时超时，也不该在今天继续「计为超时」
       （否则历史欠账会让当期指标永远红着，指标就没人看了）；
       ``超时`` 的判据是「已等待时长 > 该单自己提交时快照的 ``sla_hours``」——
       用**单子自己的**时限而不是当前配置，否则改一次配置会让历史单据的判定集体反转。
    2. **平均 / 最长处理时长**只统计**终态**单子（``processed_at - created_at``）——
       未终态的单子还没有「处理时长」这个量，算进去就是编数字。
    3. ``pending`` 不含 ``processing``（内部态）：对外它仍是「没有结果」，
       但两者分列，便于运维发现「卡在处理中」的异常单。
    """
    names = type_names or {}
    total = len(records)
    pending = processed = processing = approved = rejected = 0
    overdue = 0
    durations: list[float] = []
    by_type_acc: dict[int, dict[str, Any]] = {}

    for rec in records:
        bucket = by_type_acc.setdefault(
            rec.request_type,
            {
                "total": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
                "overdue": 0,
                "durations": [],
            },
        )
        bucket["total"] += 1
        if rec.status == 0:
            pending += 1
            bucket["pending"] += 1
        elif rec.status == 3:
            processing += 1
        elif rec.status == 1:
            approved += 1
            bucket["approved"] += 1
        elif rec.status == 2:
            rejected += 1
            bucket["rejected"] += 1

        terminal = rec.status in (1, 2)
        if terminal:
            processed += 1
            if rec.processed_at is not None:
                hours = _hours(rec.created_at, rec.processed_at)
                durations.append(hours)
                bucket["durations"].append(hours)
        else:
            waited = _hours(rec.created_at, now)
            if waited > float(rec.sla_hours or sla_hours):
                overdue += 1
                bucket["overdue"] += 1

    by_type: list[TypeSummary] = []
    for type_code in sorted(by_type_acc):
        bucket = by_type_acc[type_code]
        durs = bucket.pop("durations")
        by_type.append(
            TypeSummary(
                request_type=type_code,
                request_type_name=names.get(type_code, str(type_code)),
                avg_processing_hours=(round(sum(durs) / len(durs), 2) if durs else None),
                **bucket,
            )
        )

    return SlaSummary(
        total=total,
        pending=pending,
        approved=approved,
        rejected=rejected,
        processing=processing,
        overdue=overdue,
        overdue_ratio=(round(overdue / total, 4) if total else 0.0),
        avg_processing_hours=(round(sum(durations) / len(durations), 2) if durations else None),
        max_processing_hours=(max(durations) if durations else None),
        sla_hours=int(sla_hours),
        by_type=by_type,
    )


# ============================================================================
# 五、请求号与通知文案（确定性生成，便于排障与核对）
# ============================================================================
def build_request_no(prefix_date: str, seq: int) -> str:
    """``PRVyyyymmddNNN``（与导入任务 ``IMPyyyymmddNNN`` 同构）。"""
    return f"PRV{prefix_date}{int(seq):03d}"


def build_notify_message(
    *,
    request_type: int,
    request_no: str,
    status: int,
    reject_reason: str | None = None,
) -> str:
    """处理结果通知正文（站内信本期无表，先落 ``privacy_request.notify_message``）。

    🔴 驳回**必须**带上理由（SM-3 红线②）：不带理由的驳回通知等于「无限重提」的起点。
    """
    type_name = PrivacyRequestType.name_of(request_type) or "权利请求"
    status_name = PrivacyRequestStatus.name_of(status) or "已处理"
    if status == int(PrivacyRequestStatus.REJECTED):
        tail = f"，理由：{reject_reason}" if reject_reason else "，理由：未说明"
        return f"您的{type_name}申请（{request_no}）已驳回{tail}。"
    if status == int(PrivacyRequestStatus.APPROVED):
        suffix = ""
        if request_type == int(PrivacyRequestType.EXPORT):
            suffix = "数据包已生成，可在个人权利页下载。"
        return f"您的{type_name}申请（{request_no}）已通过并处理完成。{suffix}"
    return f"您的{type_name}申请（{request_no}）{status_name}。"


__all__ = [
    "CORRECTABLE_FIELDS",
    "CORRECTABLE_FIELD_CODES",
    "CORRECTION_EXCLUDED",
    "FIELD_LABELS",
    "MASKED_FIELD_CODES",
    "OPEN_STATUSES",
    "SOURCE_LABELS",
    "TRACKABLE_FIELDS",
    "ChangeRecord",
    "CorrectableField",
    "RequestRecord",
    "SlaSummary",
    "SnapshotResult",
    "TypeSummary",
    "build_notify_message",
    "build_request_no",
    "build_snapshot",
    "coerce_corrected_value",
    "field_label",
    "get_correctable",
    "is_overdue",
    "log_field_value",
    "render_value",
    "summarize_requests",
    "waited_hours",
]
