"""内建枚举（数据库设计 §6.1）。

🔴 **跨文档最大的不一致来源就是枚举"三种叫法"**（需求文档写「审核中」/ DDL 写「待审核」/
前端写「审核中」）。本文件是**唯一**枚举定义处：

- 值：与 DDL 注释**逐字一致**
- 名称：``*_name`` 由本文件生成并随响应下发（接口 §1.1：枚举一律 ``*_code`` + ``*_name`` 同回）
- 前端**不得**自建映射表

命名遵循「与字段/业务语义对应」，``EDUCATION_LEVEL`` 属业务字典（``dict_item``），
不在本文件硬编码（F-1.7 字典可视化维护）。
"""

from __future__ import annotations

from enum import IntEnum
from typing import TypeVar

_E = TypeVar("_E", bound=IntEnum)


class LabeledIntEnum(IntEnum):
    """带中文名的 IntEnum：值入库存 int，名称供响应展示。"""

    @property
    def label(self) -> str:
        return LABELS[type(self)][int(self)]  # type: ignore[index]

    @classmethod
    def name_of(cls, value: int | None) -> str | None:
        """取 ``*_name``；未知值返回 ``None``（不抛异常 —— 容忍历史脏数据）。"""
        if value is None:
            return None
        return LABELS.get(cls, {}).get(int(value))  # type: ignore[call-overload]

    @classmethod
    def options(cls) -> list[dict[str, int | str]]:
        """枚举字典，供前端渲染下拉（接口 §2.2 字典类接口复用）。"""
        return [{"code": int(m), "name": LABELS[cls][int(m)]} for m in cls]  # type: ignore[index]


# ============================================================================
# TBL-02 / TBL-03 / TBL-04 权限基座
# ============================================================================
class SysUserStatus(LabeledIntEnum):
    """``sys_user.status``"""

    DISABLED = 0
    ENABLED = 1


class EnabledStatus(LabeledIntEnum):
    """通用的「启用状态」（``0 停用 / 1 启用``）。

    ⚠️ **为什么不直接复用 :class:`SysUserStatus`** —— 两者都是 0/1，但**中文名不同**：

    | | 0 | 1 |
    | --- | --- | --- |
    | :class:`SysUserStatus`（``sys_user.status``） | **禁用** | 启用 |
    | :class:`EnabledStatus`（字典 / 组织 / 岗位） | **停用** | 启用 |

    「禁用」是**对人的处罚**（账号被封禁），「停用」是**对配置项的临时关闭**
    （字典项/部门不再出现在下拉里）。两者在界面上给用户的暗示不同，
    混用会让「停用了一个学历档位」显示成「禁用了」，读起来像误操作。

    典型用处：``dict_type.status`` / ``dict_item.status``（DDL 注释：``0 停用 1 启用``）。

    TODO(CU-08/CU-16): ``org_dept.status`` / ``position.status`` 目前仍用
    :class:`SysUserStatus` 取值渲染 ``status_name``（显示成「禁用」），
    与 DDL 注释的「停用」不一致 —— 已登记进评审文档 §7.3 批次 4 遗留，
    随下一次触碰组织模块时一并切换（改 ``_to_node`` / ``_to_position_out`` 两处即可）。
    """

    DISABLED = 0
    ENABLED = 1


class RoleDataScope(LabeledIntEnum):
    """``sys_role.data_scope`` —— 行级 + 字段级裁剪依据（接口 §1.7）。"""

    ALL = 1  # 全部
    OWN_DEPT = 2  # 本部门
    SELF = 3  # 本人
    STATS_ONLY = 4  # 仅统计值（不可见明细）


class PermType(LabeledIntEnum):
    """``sys_permission.perm_type``"""

    MENU = 1
    BUTTON = 2
    API = 3


# ============================================================================
# TBL-11 人才档案
# ============================================================================
class Gender(LabeledIntEnum):
    """``talent_base.gender``"""

    UNKNOWN = 0
    MALE = 1
    FEMALE = 2


class TalentDataStatus(LabeledIntEnum):
    """``talent_base.data_status`` —— 状态机见文档 SM-1。

    ``MERGED`` 与 ``DELETED`` 均为**软删**（``is_deleted=1``），区别在语义：
    前者可由「合并回滚」还原（API-61），后者是业务侧终态。
    """

    PENDING_REVIEW = 0  # 待复核
    ACTIVE = 1  # 有效
    MERGED = 2  # 已合并
    ARCHIVED = 3  # 已归档
    DELETED = 4  # 已删除


class ParseReviewStatus(LabeledIntEnum):
    """``talent_parse_field.review_status`` —— 复核漏斗四态（SQ-6 / SM-1）。"""

    PENDING = 0  # 待复核
    CONFIRMED = 1  # 已确认
    CORRECTED = 2  # 已修正
    SKIPPED = 3  # 免复核


class DupPairStatus(LabeledIntEnum):
    """``talent_dup_pair.status``"""

    PENDING = 0
    MERGED = 1
    IGNORED = 2


# ============================================================================
# TBL-18 / TBL-19 标签体系
# ============================================================================
class TagDictStatus(LabeledIntEnum):
    """``talent_tag_dict.status`` —— 🔴 AI 只能建议，不能造词（F-3.4.1 / SQ-8）。"""

    PENDING_AUDIT = 0  # 待审核（AI 建议落此处）
    ENABLED = 1  # 启用（**唯一可被打标的取值**）
    DISABLED = 2  # 停用（不物理删）


class TagSource(LabeledIntEnum):
    """``talent_tag_dict.source`` / ``talent_tag_rel.source``"""

    MANUAL = 0  # 人工
    AI = 1  # AI


# ============================================================================
# TBL-20 / TBL-21 导入
# ============================================================================
class ImportTaskStatus(LabeledIntEnum):
    """``import_task.status`` —— 状态机见文档 SM-2。

    🔴 ``PARTIAL_FAILED`` 与 ``FAILED`` **平级**（都从 ``PARSING`` 直达），
    「有 1 个文件失败」≠ 整批失败（F-3.1 单文件失败不阻塞整批）。
    """

    PENDING = 0
    PARSING = 1
    COMPLETED = 2
    PARTIAL_FAILED = 3
    FAILED = 4


class ImportFileParseStatus(LabeledIntEnum):
    """``import_file.parse_status``"""

    PENDING = 0
    SUCCESS = 1
    FAILED = 2


# ============================================================================
# TBL-22 变更留痕
# ============================================================================
class ChangeSource(LabeledIntEnum):
    """``talent_change_log.change_source`` —— 变更来源，必须可区分（F-3.10）。"""

    AI_PARSE = 0  # AI 解析
    MANUAL_REVIEW = 1  # 人工复核
    MERGE = 2  # 合并（合并快照落此处，回滚依赖它）
    REPARSE = 3  # 重解析


# ============================================================================
# TBL-23 Prompt / TBL-24 AI 调用日志
# ============================================================================
class PromptStatus(LabeledIntEnum):
    """``prompt_template.status``"""

    DRAFT = 0
    ENABLED = 1
    DISABLED = 2


class AiCallStatus(LabeledIntEnum):
    """``ai_call_log.status``"""

    FAILED = 0
    SUCCESS = 1


# ============================================================================
# TBL-25 / TBL-27 Agent（F-A0）
# ============================================================================
class AgentTaskStatus(LabeledIntEnum):
    """``agent_task.status`` —— **任务级**状态（SM-2 补充）。

    🔴 与 :class:`AgentInterruptStatus`（**待办级**）是两个字段：
    一个任务可产生多个待办，❌ 不可共用一个字段（类图 CL-5 红线）。
    """

    PENDING = 0
    RUNNING = 1
    WAITING_HUMAN = 2  # 等待人工介入
    SUCCESS = 3
    FAILED = 4


class AgentInterruptStatus(LabeledIntEnum):
    """``agent_interrupt.status`` —— **待办级**状态。"""

    PENDING = 0
    APPROVED = 1
    REJECTED = 2


# ============================================================================
# TBL-30 个人信息权利请求（F-3.11 / SM-3）
# ============================================================================
class PrivacyRequestType(LabeledIntEnum):
    """``privacy_request.request_type`` —— 四项权利中「会产请求单」的三类。

    ⚠️ **查阅不产生请求单**（SM-3 四类请求差异表：API-71 直接返回）
    —— 故取值只有三个，不是四个。
    """

    CORRECTION = 0  # 更正（API-72/74）
    DELETION = 1  # 删除（API-75/77）
    EXPORT = 2  # 导出（API-78/79）


class PrivacyRequestStatus(LabeledIntEnum):
    """``privacy_request.status`` —— SM-3 状态机。

    🔴 ``PROCESSING = 3`` 是**内部临时态**（SM-3：「不对外暴露」）：
    只出现在「删除审批」的 CAS 窗口内 —— 两个 ADM 同时审批时，
    后到者的条件更新影响行数为 0，从而被识别为**并发双审**（40902）而不是重复删除。
    ``CAS`` 失败后申请回到 ``0 待处理``（向量清理失败），或进入 ``1/2`` 终态。

    ⚠️ 另一个取值风险：把「处理中」当成对外状态下发，
    会让前端在**秒级窗口**里显示一个随后消失的状态，用户会以为系统在反复重试。
    故出参只下发 ``0/1/2``；``3`` 若被读到（正在被另一事务处理），
    映射为 ``待处理``（对用户而言「还没出结果」才是事实）。
    """

    PENDING = 0  # 待处理
    APPROVED = 1  # 已通过
    REJECTED = 2  # 已驳回
    PROCESSING = 3  # 处理中（内部态）


# ============================================================================
# 名称表（与枚举类一一对应；`*_name` 的唯一来源）
# ============================================================================
LABELS: dict[type[IntEnum], dict[int, str]] = {
    SysUserStatus: {0: "禁用", 1: "启用"},
    EnabledStatus: {0: "停用", 1: "启用"},
    RoleDataScope: {1: "全部", 2: "本部门", 3: "本人", 4: "仅统计值"},
    PermType: {1: "菜单", 2: "按钮", 3: "接口"},
    Gender: {0: "未知", 1: "男", 2: "女"},
    TalentDataStatus: {
        0: "待复核",
        1: "有效",
        2: "已合并",
        3: "已归档",
        4: "已删除",
    },
    ParseReviewStatus: {0: "待复核", 1: "已确认", 2: "已修正", 3: "免复核"},
    DupPairStatus: {0: "待处理", 1: "已合并", 2: "已忽略"},
    TagDictStatus: {0: "待审核", 1: "启用", 2: "停用"},
    TagSource: {0: "人工", 1: "AI"},
    ImportTaskStatus: {0: "待处理", 1: "解析中", 2: "已完成", 3: "部分失败", 4: "失败"},
    ImportFileParseStatus: {0: "待解析", 1: "成功", 2: "失败"},
    ChangeSource: {0: "AI 解析", 1: "人工复核", 2: "合并", 3: "重解析"},
    PromptStatus: {0: "草稿", 1: "启用", 2: "停用"},
    AiCallStatus: {0: "失败", 1: "成功"},
    AgentTaskStatus: {
        0: "pending",
        1: "running",
        2: "waiting_human",
        3: "success",
        4: "failed",
    },
    AgentInterruptStatus: {0: "pending", 1: "approved", 2: "rejected"},
    PrivacyRequestType: {0: "更正", 1: "删除", 2: "导出"},
    PrivacyRequestStatus: {0: "待处理", 1: "已通过", 2: "已驳回", 3: "处理中"},
}


def label_of(enum_cls: type[_E], value: int | None) -> str | None:
    """取枚举中文名（``*_name`` 字段生成器）。未知值返回 ``None``。"""
    if value is None:
        return None
    return LABELS.get(enum_cls, {}).get(int(value))
