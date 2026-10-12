"""ORM 实体层（架构 §5：``models/`` 是数据结构的唯一事实来源）。

🔴 **本模块必须显式 import 每一张表的模型** —— 否则该表不会注册到 ``Base.metadata``，
Alembic autogenerate 会认为它「不存在」并生成 DROP TABLE。

> ⚠️ 本文件曾漏 import :mod:`app.models.audit`（TBL-28）。因为 0003 迁移是手写 ``op.create_table``，
> 所以当时没暴露问题；但只要有人跑一次 ``alembic revision --autogenerate``，
> 就会得到一份「DROP TABLE audit_log」的迁移。**已补。**
> 新增任何模型时，务必同时在本文件 import 并加入 ``__all__``。

一期现有表 + 本批新增 TBL-12/TBL-13 的落地进度：

| 编号 | 表 | 模型 | 状态 |
| --- | --- | :-: | :-: |
| TBL-01 | ``tenant`` | :class:`~app.models.tenant.Tenant` | ✅ CU-02 |
| TBL-02 | ``sys_user`` | :class:`~app.models.user.SysUser` | ✅ CU-02 |
| TBL-03 | ``sys_role`` | :class:`~app.models.user.SysRole` | ✅ CU-02 |
| TBL-04 | ``sys_permission`` | :class:`~app.models.user.SysPermission` | ✅ CU-02 |
| TBL-05 | ``sys_user_role`` | :class:`~app.models.user.SysUserRole` | ✅ CU-02 |
| TBL-06 | ``sys_role_permission`` | :class:`~app.models.user.SysRolePermission` | ✅ CU-02 |
| TBL-07 | ``org_dept`` | :class:`~app.models.tenant.OrgDept` | ✅ CU-02 |
| TBL-08 | ``position`` | :class:`~app.models.tenant.Position` | ✅ CU-02 |
| TBL-09 ~ TBL-10 | ``dict_type`` / ``dict_item`` | :class:`~app.models.dict.DictType` / :class:`~app.models.dict.DictItem` | ✅ CU-07 |
| TBL-11 | ``talent_base`` | :class:`~app.models.talent.TalentBase` | ✅ CU-15 |
| TBL-12 ~ TBL-13 | 工作经历 / 项目经历 | :class:`~app.models.talent.TalentWorkExp` / :class:`~app.models.talent.TalentProjectExp` | ✅ 数据层（本批） |
| TBL-14 ~ TBL-15 | 技能 / 证书 | — | ⏳（结构化明细表未建模，数据现以 `talent_parse_field` 承载） |
| TBL-16 | ``talent_parse_field`` | :class:`~app.models.talent.TalentParseField` | ✅ CU-15 |
| TBL-17 | ``talent_dup_pair`` | :class:`~app.models.dup_pair.TalentDupPair` | ✅ CU-19 |
| TBL-18 ~ TBL-19 | 标签体系 | :class:`~app.models.talent.TalentTagDict` / :class:`~app.models.talent.TalentTagRel` | ✅ CU-17 |
| TBL-20 ~ TBL-21 | 导入任务 / 文件明细 | :class:`~app.models.talent.ImportTask` / :class:`~app.models.talent.ImportFile` | ✅ CU-14 |
| TBL-22 | ``talent_change_log`` | :class:`~app.models.talent.TalentChangeLog` | ✅ CU-16（首用） |
| TBL-23 ~ TBL-24 | Prompt / AI 日志 | :class:`~app.models.ai.PromptTemplate` / :class:`~app.models.ai.AiCallLog` | ✅ CU-11 / CU-12 |
| TBL-25 ~ TBL-27 | Agent 任务 / 步骤 / 待办 | :class:`~app.models.agent.AgentTask` 等 | ✅ CU-13 |
| TBL-28 | 审计日志 | :class:`~app.models.audit.AuditLog` | ✅ CU-08 |
| **TBL-29** | ``ai_model_config``（**本批新增**，见 :mod:`app.models.ai`） | :class:`~app.models.ai.AiModelConfig` | ✅ CU-10 |
| **TBL-30** | ``privacy_request``（**CU-22 新增**，见 :mod:`app.models.privacy`） | :class:`~app.models.privacy.PrivacyRequest` | ✅ CU-22 |
"""

from app.models.agent import AgentInterrupt, AgentStep, AgentTask
from app.models.assessment import (
    AssessmentAttempt,
    AssessmentAnswer,
    AssessmentEvent,
    AssessmentEventReview,
    AssessmentMonitorRule,
    AssessmentNotification,
    AssessmentPaper,
    AssessmentPaperQuestion,
    AssessmentScore,
    AssessmentTask,
    AssessmentTaskCandidate,
    OutboxEvent,
)
from app.models.ai import AiCallLog, AiModelConfig, PromptTemplate
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.dict import DictItem, DictType
from app.models.dup_pair import TalentDupPair
from app.models.enums import (
    AgentInterruptStatus,
    AgentTaskStatus,
    AiCallStatus,
    ChangeSource,
    DupPairStatus,
    EnabledStatus,
    Gender,
    ImportFileParseStatus,
    ImportTaskStatus,
    ParseReviewStatus,
    PermType,
    PrivacyRequestStatus,
    PrivacyRequestType,
    PromptStatus,
    RoleDataScope,
    SysUserStatus,
    TagDictStatus,
    TagSource,
    TalentDataStatus,
    label_of,
)
from app.models.privacy import PrivacyRequest
from app.models.talent import (
    ImportFile,
    ImportTask,
    TalentBase,
    TalentChangeLog,
    TalentParseField,
    TalentProjectExp,
    TalentTagDict,
    TalentTagRel,
    TalentWorkExp,
)
from app.models.tenant import OrgDept, Position, Tenant
from app.models.user import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole

__all__ = [
    "AgentInterrupt",
    "AgentInterruptStatus",
    "AgentStep",
    "AgentTask",
    "AgentTaskStatus",
    "AssessmentEventReview",
    "AssessmentEvent",
    "AssessmentAnswer",
    "AssessmentAttempt",
    "AssessmentMonitorRule",
    "AssessmentNotification",
    "AssessmentPaper",
    "AssessmentPaperQuestion",
    "AssessmentScore",
    "AssessmentTask",
    "AssessmentTaskCandidate",
    "AiCallLog",
    "AiCallStatus",
    "AiModelConfig",
    "AuditLog",
    "Base",
    "ChangeSource",
    "DictItem",
    "DictType",
    "DupPairStatus",
    "EnabledStatus",
    "Gender",
    "ImportFile",
    "ImportFileParseStatus",
    "ImportTask",
    "ImportTaskStatus",
    "OrgDept",
    "OutboxEvent",
    "ParseReviewStatus",
    "PermType",
    "Position",
    "PrivacyRequest",
    "PrivacyRequestStatus",
    "PrivacyRequestType",
    "PromptStatus",
    "PromptTemplate",
    "RoleDataScope",
    "SysPermission",
    "SysRole",
    "SysRolePermission",
    "SysUser",
    "SysUserRole",
    "SysUserStatus",
    "TagDictStatus",
    "TagSource",
    "TalentBase",
    "TalentChangeLog",
    "TalentDataStatus",
    "TalentDupPair",
    "TalentParseField",
    "TalentProjectExp",
    "TalentTagDict",
    "TalentTagRel",
    "TalentWorkExp",
    "Tenant",
    "label_of",
]
