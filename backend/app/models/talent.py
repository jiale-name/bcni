"""人才档案域（TBL-11/16/18/19/20/21/22，CU-14 ~ CU-17，F-3.1 ~ F-3.4.1）。

🔴 **唯一事实来源**（数据库 §0）：本文件 + Alembic 迁移 ``0005_talent``；
数据库设计文档 §4.3~4.5 的 DDL 仅作字段参考。本批相对 DDL 的**有意偏离**：

1. ``talent_parse_field`` 增加 ``source_file`` / ``source_locator`` 两列 ——
   API-45/46 的出参承诺了「原文 ↔ 抽取值并排 + 定位」，而 DDL 没有承载这两项的列
   （上游缺口，已在评审文档登记）；不加列则复核详情只能造假。
2. ``talent_parse_field`` 增加 ``impact_level`` 列 —— API-44 要求按影响面过滤/统计。
   影响面语义上可由 ``HIGH_IMPACT_FIELDS`` 推导，但「队列按列过滤/分组」是高频 SQL，
   推导式过滤（``field_name IN (...)``）会让索引退化，故物化。
3. ``import_task`` 增加 ``parse_strategy`` / ``auto_dedup`` / ``auto_tag`` / ``remark`` ——
   API-36 的表单字段，DDL 漏了（否则提交时丢配置）。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    DATE,
    DECIMAL,
    BigInteger,
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import TINYINT, VARBINARY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    MYSQL_TABLE_KWARGS,
    AppendOnlyMixin,
    Base,
    IdTenantCommonMixin,
    SnowflakeIdMixin,
    TenantColumnMixin,
    TimestampMixin,
)
from app.models.enums import (
    ImportFileParseStatus,
    ImportTaskStatus,
    ParseReviewStatus,
    TagDictStatus,
    TagSource,
    TalentDataStatus,
)


# ============================================================================
# TBL-11 人才主档
# ============================================================================
class TalentBase(IdTenantCommonMixin, Base):
    """TBL-11 人才主档。

    🔴 ``data_status=0 待复核`` 是 CU-15 解析入库后的**初始状态**：
    字段级复核（API-44~49）全部清零后才流转 ``1 有效``（本批实现），
    查重合并（``2``）/ 归档（``3``）/ 删除（``4``）属 CU-19 / CU-22。

    ⚠️ ``name`` 为 NOT NULL：解析不出姓名的文件走**文件级**人工介入
    （Agent 图 ``field_review`` 中断），不会带着空姓名入库 ——
    空档案比少一份档案危险得多（后续检索/合并都建立在「有一份档案」上）。
    """

    __tablename__ = "talent_base"
    __table_args__ = (
        # ⚠️ 唯一键带 is_deleted（数据库 §0 原则 4）；is_deleted 只有 0/1，
        #    意味着同一 id_card_hash 最多一条软删行 —— 与字典同源的已知写法坑，
        #    本期安全（档案删除走物理删除流程，属 CU-22），已登记评审文档遗留。
        UniqueConstraint("tenant_id", "id_card_hash", "is_deleted", name="uk_tenant_idcard"),
        Index("idx_tenant_org", "tenant_id", "org_id"),
        Index("idx_tenant_status", "tenant_id", "data_status"),
        Index("idx_tenant_created", "tenant_id", "created_at"),
        Index("idx_tenant_phone", "tenant_id", "phone_hash"),
        Index("idx_parse_task", "parse_task_id"),
        MYSQL_TABLE_KWARGS,
    )

    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联用户（外部候选人可空）")
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="归属组织（行级权限字段）")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="姓名")
    gender: Mapped[int | None] = mapped_column(TINYINT, nullable=True, comment="性别 0 未知 1 男 2 女")
    birth_date: Mapped[date | None] = mapped_column(DATE, nullable=True, comment="出生年月")
    id_card_enc: Mapped[bytes | None] = mapped_column(VARBINARY(256), nullable=True, comment="身份证（AES-GCM 加密）")
    id_card_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, comment="身份证盐化哈希（精确去重）")
    phone_enc: Mapped[bytes | None] = mapped_column(VARBINARY(256), nullable=True, comment="手机号（AES-GCM 加密）")
    phone_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, comment="手机号盐化哈希（精确去重）")
    email_enc: Mapped[bytes | None] = mapped_column(VARBINARY(256), nullable=True, comment="邮箱（AES-GCM 加密）")
    education_level: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="学历（dict_item 码）")
    school: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="院校")
    major: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="专业")
    work_years: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="工作年限")
    profile_vec_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="Milvus 向量主键（CU-18）")
    data_status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(TalentDataStatus.PENDING_REVIEW),
        server_default=text("0"), comment="数据状态 0 待复核 1 有效 2 已合并 3 已归档 4 已删除",
    )
    merged_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="合并去向（F-3.5 回滚依据）")
    parse_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="来源导入任务 ID")
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="版本号（F-3.10 自增）"
    )

    def __repr__(self) -> str:
        return f"<TalentBase id={self.id} name={self.name!r} status={self.data_status}>"


# ============================================================================
# TBL-12 / TBL-13 履历明细
# ============================================================================
class TalentWorkExp(IdTenantCommonMixin, Base):
    """TBL-12 工作经历明细。

    ``seq_no`` is assigned by the parser or an editor and is the stable display
    order.  Detail rows are independently soft-deletable so a reparse can
    preserve the old rows while replacing the current set in one transaction.
    """

    __tablename__ = "talent_work_exp"
    __table_args__ = (
        Index("idx_tenant_talent", "tenant_id", "talent_id", "seq_no"),
        MYSQL_TABLE_KWARGS,
    )

    talent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="人才 ID")
    seq_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="序号（时间倒序）"
    )
    company: Mapped[str] = mapped_column(String(128), nullable=False, comment="公司名称")
    dept_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="部门")
    position_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="职位")
    start_date: Mapped[date | None] = mapped_column(DATE, nullable=True, comment="开始日期")
    end_date: Mapped[date | None] = mapped_column(DATE, nullable=True, comment="结束日期")
    is_current: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, server_default=text("0"), comment="是否在职"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="工作描述")
    source: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=0, server_default=text("0"), comment="来源 0 AI 解析 1 人工"
    )

    def __repr__(self) -> str:
        return f"<TalentWorkExp id={self.id} talent={self.talent_id} seq={self.seq_no}>"


class TalentProjectExp(IdTenantCommonMixin, Base):
    """TBL-13 项目经历明细。"""

    __tablename__ = "talent_project_exp"
    __table_args__ = (
        Index("idx_tenant_talent", "tenant_id", "talent_id", "seq_no"),
        MYSQL_TABLE_KWARGS,
    )

    talent_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="人才 ID")
    seq_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="序号"
    )
    project_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="项目名称")
    role_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="担任角色")
    start_date: Mapped[date | None] = mapped_column(DATE, nullable=True, comment="开始日期")
    end_date: Mapped[date | None] = mapped_column(DATE, nullable=True, comment="结束日期")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="项目描述")
    tech_stack: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="技术栈数组")
    source: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=0, server_default=text("0"), comment="来源 0 AI 解析 1 人工"
    )

    def __repr__(self) -> str:
        return f"<TalentProjectExp id={self.id} talent={self.talent_id} seq={self.seq_no}>"


# ============================================================================
# TBL-16 字段级解析与复核
# ============================================================================
class TalentParseField(IdTenantCommonMixin, Base):
    """TBL-16 字段级解析与复核（F-3.3 复核漏斗的落点）。

    ``review_status`` 四态：``0 待复核``（入队列）→ ``1 已确认`` / ``2 已修正``
    / ``3 免复核``。入库时即分流：命中 ThresholdEngine 的字段 = 0，其余 = 3 ——
    「免复核」也是一次**显式判定**（免的是「不用人工看」，不是「没看就放过」）。
    """

    __tablename__ = "talent_parse_field"
    __table_args__ = (
        Index("idx_tenant_talent_field", "tenant_id", "talent_id", "field_name"),
        Index("idx_tenant_review", "tenant_id", "review_status"),
        Index("idx_tenant_talent_conf", "tenant_id", "talent_id", "confidence"),
        # API-44 的队列按 (租户, 复核状态, 影响面, 置信度) 过滤 —— 高频路径单独给索引
        Index("idx_tenant_review_impact", "tenant_id", "review_status", "impact_level", "confidence"),
        MYSQL_TABLE_KWARGS,
    )

    talent_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="人才 ID")
    field_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="字段名（字段级唯一键的一半）")
    raw_text: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="原文片段")
    parsed_value: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="抽取值")
    confidence: Mapped[float | None] = mapped_column(
        DECIMAL(4, 3), nullable=True, comment="置信度 0.000~1.000"
    )
    impact_level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="LOW", server_default="LOW", comment="影响面 HIGH/LOW"
    )
    extract_model: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="抽取器标识与版本")
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="来源文件名（API-45 并排展示）")
    source_locator: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="原文定位（如 page=1,block=3）")
    review_status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(ParseReviewStatus.PENDING),
        server_default=text("0"), comment="复核状态 0 待复核 1 已确认 2 已修正 3 免复核",
    )
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="复核人")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="复核时间")

    def __repr__(self) -> str:
        return (
            f"<TalentParseField id={self.id} talent={self.talent_id} "
            f"field={self.field_name!r} conf={self.confidence} status={self.review_status}>"
        )


# ============================================================================
# TBL-18 受控标签词表 / TBL-19 人才-标签关系
# ============================================================================
class TalentTagDict(IdTenantCommonMixin, Base):
    """TBL-18 受控标签词表（F-3.4.1）。

    🔴 **AI 不得自由造词**（SQ-8）：AI 建议落 ``status=0 待审核``，
    只有 ``status=1 启用`` 的标签可被 ``talent_tag_rel`` 引用 ——
    应用层硬约束（不建物理外键），由 :class:`app.core.tagdict.TagDictValidator` 保证。
    """

    __tablename__ = "talent_tag_dict"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tag_code", "is_deleted", name="uk_tenant_tag_code"),
        Index("idx_tenant_status", "tenant_id", "status"),
        Index("idx_tenant_category", "tenant_id", "category_l1", "category_l2"),
        MYSQL_TABLE_KWARGS,
    )

    tag_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="标签编码（大写+下划线）")
    tag_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="标签名")
    category_l1: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="一级分类")
    category_l2: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="二级分类")
    category_l3: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="三级分类")
    synonyms: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="同义词数组（选词归一用）")
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(TagDictStatus.PENDING_AUDIT),
        server_default=text("0"), comment="状态 0 待审核 1 启用 2 停用",
    )
    source: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(TagSource.MANUAL),
        server_default=text("0"), comment="来源 0 人工 1 AI 建议",
    )
    suggested_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="建议人（AI 触发为空）")
    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="审核人")
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="审核通过时间（未过审不得被引用）"
    )

    def __repr__(self) -> str:
        return f"<TalentTagDict {self.tag_code!r} status={self.status}>"


class TalentTagRel(IdTenantCommonMixin, Base):
    """TBL-19 人才-标签关系。

    ``tag_code`` 必须取自 ``talent_tag_dict`` 的**启用项**（应用层硬约束）。
    ``source`` 区分 0 AI / 1 人工：API-55 出参据此展示来源，人工覆盖 AI 是允许的
    （同一 ``(talent, tag)`` 只保留一行，由唯一键保证）。
    """

    __tablename__ = "talent_tag_rel"
    __table_args__ = (
        UniqueConstraint("tenant_id", "talent_id", "tag_code", "is_deleted", name="uk_tenant_talent_tag"),
        Index("idx_tenant_tag", "tenant_id", "tag_code"),
        MYSQL_TABLE_KWARGS,
    )

    talent_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="人才 ID")
    tag_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="标签编码（必须是启用项）")
    confidence: Mapped[float | None] = mapped_column(DECIMAL(4, 3), nullable=True, comment="打标置信度（AI 打标才有）")
    source: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(TagSource.MANUAL),
        server_default=text("0"), comment="来源 0 AI 解析 1 人工指定",
    )

    def __repr__(self) -> str:
        return f"<TalentTagRel talent={self.talent_id} tag={self.tag_code!r} src={self.source}>"


# ============================================================================
# TBL-20 导入任务 / TBL-21 导入文件明细
# ============================================================================
class ImportTask(IdTenantCommonMixin, Base):
    """TBL-20 导入任务（API-36~43 的主体）。

    🔴 ``uk_tenant_idem(tenant_id, idempotent_key)`` 是 **API-36 强制幂等**的库级保证：
    服务层「先查后插」只防正常并发，唯一索引兜住竞态穿透（两个请求同时查不到 → 同时插）。
    """

    __tablename__ = "import_task"
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_no", "is_deleted", name="uk_tenant_task_no"),
        # 🔴 idempotent_key 可空（NULL 不参与 MySQL 唯一约束）—— 非幂等路径允许 NULL
        UniqueConstraint("tenant_id", "idempotent_key", "is_deleted", name="uk_tenant_idem"),
        Index("idx_tenant_status", "tenant_id", "status"),
        Index("idx_tenant_created", "tenant_id", "created_at"),
        MYSQL_TABLE_KWARGS,
    )

    task_no: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务号 IMPyyyymmddNNN")
    source: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=0, server_default=text("0"), comment="来源 0 Web 上传 1 离线目录 2 API"
    )
    parse_strategy: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="解析策略 auto/ocr_force/text_only")
    auto_dedup: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=1, server_default=text("1"), comment="解析后自动查重（F-3.5，CU-19 消费）"
    )
    auto_tag: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=1, server_default=text("1"), comment="自动打标签（只能选受控词表启用项）"
    )
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="备注")
    total_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="文件总数"
    )
    success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="成功数"
    )
    fail_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="失败数"
    )
    status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(ImportTaskStatus.PENDING),
        server_default=text("0"), comment="状态 0 待处理 1 解析中 2 已完成 3 部分失败 4 失败",
    )
    idempotent_key: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="幂等键（API-36 强制）")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="发起人")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="完成时间"
    )
    error_msg: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="整体失败原因")

    def __repr__(self) -> str:
        return f"<ImportTask {self.task_no!r} status={self.status} {self.success_count}/{self.total_count}>"


class ImportFile(IdTenantCommonMixin, Base):
    """TBL-21 导入文件明细（逐文件解析状态的唯一事实）。

    ``file_hash``：SHA-256 指纹，租户 + 任务内唯一（重复上传同一文件 → 40901）；
    ``resume_id``：文件在**本任务内**的序号（金标准查询集用它对齐期望人才，
    见批次 5 遗留 ④「CU-14 必须保留 resume_id → talent_id 映射」）。
    """

    __tablename__ = "import_file"
    __table_args__ = (
        UniqueConstraint("tenant_id", "task_id", "file_hash", "is_deleted", name="uk_tenant_task_hash"),
        Index("idx_tenant_task", "tenant_id", "task_id", "parse_status"),
        Index("idx_talent", "talent_id"),
        MYSQL_TABLE_KWARGS,
    )

    task_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="导入任务 ID")
    resume_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="文件序号（本任务内从 1 起；金标准对齐键）"
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="文件名")
    file_ext: Mapped[str | None] = mapped_column(String(16), nullable=True, comment="扩展名")
    file_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="文件大小（字节）"
    )
    file_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True, comment="文件 SHA-256 指纹")
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="存储路径（MinIO/本地）")
    parse_status: Mapped[int] = mapped_column(
        TINYINT, nullable=False, default=int(ImportFileParseStatus.PENDING),
        server_default=text("0"), comment="解析状态 0 待解析 1 成功 2 失败",
    )
    parse_error: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="单文件失败原因（原因码）")
    talent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="解析生成的档案 ID")
    agent_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="本文件的 Agent 任务 ID")
    ocr_used: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, server_default=text("0"), comment="是否走 OCR（图片/扫描件）"
    )

    def __repr__(self) -> str:
        return f"<ImportFile {self.file_name!r} status={self.parse_status} talent={self.talent_id}>"


# ============================================================================
# TBL-22 档案变更留痕（只追加）
# ============================================================================
class TalentChangeLog(AppendOnlyMixin, Base):
    """TBL-22 档案变更留痕（F-3.10 的地基）。

    🔴 **只追加**：无 ``is_deleted`` / 无 ``updated_at``（:class:`AppendOnlyMixin`），
    ``created_at`` 为业务列显式声明（同 :class:`~app.models.audit.AuditLog` 的口径，
    带应用侧默认值），**不继承 TimestampMixin**（混进 ``updated_at`` 会让 ORM
    查询炸出 Unknown column —— 与「只追加三层保证」的 ① 直接冲突）。
    DAO 层只导出 insert/list/count（同审计日志的三层结构保证）。
    ⚠️ 与 audit_log 不同，本表**不做分区**（量级 10⁶ < 审计 10⁷，12 个月留存靠归档脚本即可）。
    """

    __tablename__ = "talent_change_log"
    __table_args__ = (
        Index("idx_tenant_talent_time", "tenant_id", "talent_id", "created_at"),
        Index("idx_tenant_source", "tenant_id", "change_source"),
        MYSQL_TABLE_KWARGS,
    )

    talent_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="人才 ID")
    change_source: Mapped[int] = mapped_column(TINYINT, nullable=False, comment="来源 0 AI 解析 1 人工复核 2 合并 3 重解析")
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="变更字段（整体变更可空）")
    before_value: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="变更前值")
    after_value: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="变更后值")
    snapshot_ref: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="完整快照引用（大范围变更）")
    operator_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="操作人（AI 触发记系统账号）")
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="关联 Agent 任务或导入任务 ID")
    #: 业务列 = 变更时间（应用侧默认值，同 AuditLog 口径）
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="变更时间",
    )

    def __repr__(self) -> str:
        return f"<TalentChangeLog talent={self.talent_id} field={self.field_name!r} src={self.change_source}>"
