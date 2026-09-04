from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskOrigin,
    BusinessTaskStatus,
    BusinessTaskType,
    ComplaintReviewStatus,
    ConversationMode,
    CredentialDeliveryStatus,
    CustomerIdentityProvider,
    CustomerMemoryCategory,
    CustomerMemoryEvidenceType,
    CustomerMemoryStatus,
    CustomerMergeStatus,
    EmployeeRole,
    JobStatus,
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
    Language,
    MessageOrigin,
    ReminderStatus,
    ReminderType,
    RoomOperationalStatus,
    RuntimeConfigVersionStatus,
    TaskClosureReason,
    TaskClosureSource,
)


class Base(DeclarativeBase):
    """为全部 ORM 模型提供统一元数据。"""


class TimestampMixin:
    """为业务表提供统一的 UTC 创建与更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Employee(TimestampMixin, Base):
    """保存企业微信成员身份与本地授权角色。"""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    wecom_userid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[EmployeeRole] = mapped_column(
        Enum(EmployeeRole, native_enum=False, length=32),
        default=EmployeeRole.STAFF,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AdminCredential(TimestampMixin, Base):
    """保存唯一后台管理员的 Argon2id 凭证和会话失效版本。"""

    __tablename__ = "admin_credentials"
    __table_args__ = (CheckConstraint("id = 1", name="ck_admin_credentials_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AdminCsrfNonce(Base):
    """保存服务端一次性认证表单 nonce 的 SHA-256 摘要。"""

    __tablename__ = "admin_csrf_nonces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_credentials.id", ondelete="CASCADE"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminCsrfQuota(TimestampMixin, Base):
    """以数据库单例计数器约束跨实例活动 CSRF nonce 总量。"""

    __tablename__ = "admin_csrf_quota"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_admin_csrf_quota_singleton"),
        CheckConstraint("active_count >= 0", name="ck_admin_csrf_quota_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class RuntimeConfigVersion(Base):
    """保存不可变的加密运行配置快照及不含秘密的掩码摘要。"""

    __tablename__ = "runtime_config_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    masked_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    status: Mapped[RuntimeConfigVersionStatus] = mapped_column(
        Enum(RuntimeConfigVersionStatus, native_enum=False, length=32),
        default=RuntimeConfigVersionStatus.CANDIDATE,
        nullable=False,
    )
    test_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    based_on_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtime_config_versions.id"), nullable=True
    )
    based_on_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RuntimeConfigState(TimestampMixin, Base):
    """以单例指针保存当前、上一配置版本和乐观锁修订号。"""

    __tablename__ = "runtime_config_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_runtime_config_state_singleton"),
        CheckConstraint("revision >= 0", name="ck_runtime_config_state_revision_nonnegative"),
        CheckConstraint(
            "active_version_id IS NULL OR previous_version_id IS NULL "
            "OR active_version_id <> previous_version_id",
            name="ck_runtime_config_state_distinct_pointers",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtime_config_versions.id"), nullable=True
    )
    previous_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtime_config_versions.id"), nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Customer(TimestampMixin, Base):
    """保存跨渠道共享的客户主档和加密联系方式。"""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_into_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True
    )
    identities: Mapped[list["CustomerIdentity"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    tag_links: Mapped[list["CustomerTagLink"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="customer")


class CustomerIdentity(TimestampMixin, Base):
    """保存一个客户在企业微信、微信或百居易中的可靠身份。"""

    __tablename__ = "customer_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            name="uq_customer_identity_provider_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[CustomerIdentityProvider] = mapped_column(
        Enum(CustomerIdentityProvider, native_enum=False, length=32),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    customer: Mapped[Customer] = relationship(back_populates="identities")


class CustomerTag(TimestampMixin, Base):
    """保存 YuMi 内部客户标签及其可选企业微信标签映射。"""

    __tablename__ = "customer_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    wecom_tag_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    customer_links: Mapped[list["CustomerTagLink"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class CustomerTagLink(TimestampMixin, Base):
    """保存客户多选标签及企业微信同步状态。"""

    __tablename__ = "customer_tag_links"

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("customer_tags.id", ondelete="CASCADE"), primary_key=True
    )
    sync_pending: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer: Mapped[Customer] = relationship(back_populates="tag_links")
    tag: Mapped[CustomerTag] = relationship(back_populates="customer_links")


class CustomerMergeSuggestion(TimestampMixin, Base):
    """保存可靠身份匹配形成的待管理员确认合并建议。"""

    __tablename__ = "customer_merge_suggestions"
    __table_args__ = (
        CheckConstraint(
            "source_customer_id != target_customer_id",
            name="ck_customer_merge_distinct",
        ),
        Index("ix_customer_merge_status_created", "status", "created_at"),
        Index(
            "ix_customer_merge_source_target_status",
            "source_customer_id",
            "target_customer_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    target_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CustomerMergeStatus] = mapped_column(
        Enum(CustomerMergeStatus, native_enum=False, length=16),
        default=CustomerMergeStatus.PENDING,
        nullable=False,
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerContextSummary(TimestampMixin, Base):
    """保存客户七天内短摘要和七天外长期摘要。"""

    __tablename__ = "customer_context_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    short_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    long_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    unresolved_items: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    short_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    long_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CustomerMemoryItem(TimestampMixin, Base):
    """保存按客户隔离、带证据和生命周期的结构化记忆。"""

    __tablename__ = "customer_memory_items"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_customer_memory_confidence_range",
        ),
        Index(
            "ix_customer_memory_customer_status_review",
            "customer_id",
            "status",
            "review_at",
        ),
        Index(
            "ix_customer_memory_customer_subject",
            "customer_id",
            "subject_key",
        ),
        Index(
            "uq_customer_memory_active_subject",
            "customer_id",
            "subject_key",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    subject_key: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[CustomerMemoryCategory] = mapped_column(
        Enum(CustomerMemoryCategory, native_enum=False, length=32), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CustomerMemoryStatus] = mapped_column(
        Enum(CustomerMemoryStatus, native_enum=False, length=16),
        default=CustomerMemoryStatus.CANDIDATE,
        nullable=False,
    )
    evidence_type: Mapped[CustomerMemoryEvidenceType] = mapped_column(
        Enum(CustomerMemoryEvidenceType, native_enum=False, length=32), nullable=False
    )
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.external_message_id", ondelete="SET NULL"),
        nullable=True,
    )
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_excerpt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_memory_items.id", ondelete="SET NULL"), nullable=True
    )
    status_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_redacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CustomerMemoryEvent(Base):
    """保存客户记忆状态变化的可清理审计时间线。"""

    __tablename__ = "customer_memory_events"
    __table_args__ = (
        Index(
            "ix_customer_memory_event_customer_occurred",
            "customer_id",
            "occurred_at",
        ),
        Index(
            "ix_customer_memory_event_memory_occurred",
            "memory_item_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    memory_item_id: Mapped[int] = mapped_column(
        ForeignKey("customer_memory_items.id", ondelete="CASCADE"), nullable=False
    )
    subject_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    statement_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.external_message_id", ondelete="SET NULL"), nullable=True
    )
    actor_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    content_redacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Conversation(TimestampMixin, Base):
    """保存一个微信客服账号与一个外部联系人的会话状态。"""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("open_kfid", "external_userid", name="uq_conversation_participants"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    open_kfid: Mapped[str] = mapped_column(String(128), nullable=False)
    external_userid: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[Language] = mapped_column(
        Enum(Language, native_enum=False, length=8), default=Language.ZH, nullable=False
    )
    mode: Mapped[ConversationMode] = mapped_column(
        Enum(ConversationMode, native_enum=False, length=32),
        default=ConversationMode.BOT_ACTIVE,
        nullable=False,
    )
    assigned_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    customer: Mapped[Customer | None] = relationship(back_populates="conversations")


class Message(Base):
    """保存已去重的企业微信消息和机器人发送记录。"""

    __tablename__ = "messages"
    __table_args__ = (
        Index(
            "ix_messages_conversation_type_id",
            "conversation_id",
            "message_type",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    origin: Mapped[MessageOrigin] = mapped_column(
        Enum(MessageOrigin, native_enum=False, length=16), nullable=False
    )
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    short_summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    memory_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ComplaintReview(TimestampMixin, Base):
    """保存脱敏客诉分析和人工回复草稿，不复制客人原始正文。"""

    __tablename__ = "complaint_reviews"
    __table_args__ = (
        UniqueConstraint(
            "source_message_id",
            name="uq_complaint_review_source_message",
        ),
        Index("ix_complaint_review_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ComplaintReviewStatus] = mapped_column(
        Enum(ComplaintReviewStatus, native_enum=False, length=32),
        default=ComplaintReviewStatus.PENDING_ANALYSIS,
        nullable=False,
    )
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_outbox_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    delivery_external_message_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )


class KnowledgeEntry(TimestampMixin, Base):
    """保存经过人工审核的中英文民宿知识。"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    question_zh: Mapped[str] = mapped_column(Text, nullable=False)
    answer_zh: Mapped[str] = mapped_column(Text, nullable=False)
    question_en: Mapped[str] = mapped_column(Text, nullable=False)
    answer_en: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)


class KnowledgeCandidate(TimestampMixin, Base):
    """保存尚待管理员归纳的高频 FAQ 主题和脱敏草稿。"""

    __tablename__ = "knowledge_candidates"
    __table_args__ = (
        CheckConstraint("total_occurrences >= 0", name="ck_candidate_total_nonnegative"),
        CheckConstraint("last_threshold_total >= 0", name="ck_candidate_threshold_nonnegative"),
        CheckConstraint("last_reminded_total >= 0", name="ck_candidate_reminded_nonnegative"),
        CheckConstraint("examples_version >= 0", name="ck_candidate_examples_version_nonnegative"),
        CheckConstraint("draft_generation >= 0", name="ck_candidate_generation_nonnegative"),
        CheckConstraint("draft_attempts >= 0", name="ck_candidate_attempts_nonnegative"),
        Index("ix_candidate_status_snoozed", "status", "snoozed_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    canonical_question: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[KnowledgeCandidateStatus] = mapped_column(
        Enum(KnowledgeCandidateStatus, native_enum=False, length=24),
        default=KnowledgeCandidateStatus.OPEN,
        nullable=False,
    )
    total_occurrences: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_threshold_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminded_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    examples: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    examples_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_status: Mapped[KnowledgeCandidateDraftStatus] = mapped_column(
        Enum(KnowledgeCandidateDraftStatus, native_enum=False, length=24),
        default=KnowledgeCandidateDraftStatus.NONE,
        nullable=False,
    )
    draft_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_examples_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    draft_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    knowledge_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entries.id"), unique=True, nullable=True
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrences: Mapped[list["KnowledgeCandidateOccurrence"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class KnowledgeCandidateOccurrence(Base):
    """保存候选的一次去重出现，不复制客人正文或身份。"""

    __tablename__ = "knowledge_candidate_occurrences"
    __table_args__ = (Index("ix_candidate_occurrence_window", "candidate_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_candidates.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candidate: Mapped[KnowledgeCandidate] = relationship(back_populates="occurrences")


class BookingApproval(TimestampMixin, Base):
    """保存客人预订意向及员工最终审批结果。"""

    __tablename__ = "booking_approvals"
    __table_args__ = (
        UniqueConstraint("approval_code", name="uq_booking_approval_code"),
        UniqueConstraint("hostex_reservation_code", name="uq_booking_hostex_reservation_code"),
        Index("ix_booking_approval_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    approval_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, length=32),
        default=ApprovalStatus.PENDING,
        nullable=False,
    )
    check_in_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_out_date: Mapped[date] = mapped_column(Date, nullable=False)
    number_of_guests: Mapped[int] = mapped_column(Integer, nullable=False)
    guest_name_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    guest_mobile_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    room_type_preference: Mapped[str] = mapped_column(String(128), nullable=False)
    special_requests_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
    )
    pii_purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    property_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    final_rate_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    income_method_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hostex_reservation_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hostex_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExternalRequest(Base):
    """记录外部接口调用的非敏感结果，便于审计和排障。"""

    __tablename__ = "external_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    business_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Job(TimestampMixin, Base):
    """保存可恢复的后台任务及其重试状态。"""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "available_at"),
        Index("ix_jobs_type_claim", "status", "job_type", "available_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=16),
        default=JobStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditLog(Base):
    """记录员工和系统执行的关键业务动作。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HostexWebhookEvent(TimestampMixin, Base):
    """保存百居易 Webhook 的幂等事件和最小处理状态。"""

    __tablename__ = "hostex_webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reservation_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PropertyProfile(TimestampMixin, Base):
    """保存百居易物理房间和 YuMi 运营配置。"""

    __tablename__ = "property_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    room_number: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    room_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    district: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    parking_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StayOrder(TimestampMixin, Base):
    """保存从百居易同步的入住订单关键事实。"""

    __tablename__ = "stay_orders"
    __table_args__ = (
        Index(
            "ix_stay_orders_customer_status_checkin",
            "customer_id",
            "status",
            "check_in_date",
        ),
        Index("ix_stay_orders_check_in_status", "check_in_date", "status"),
        Index("ix_stay_orders_check_out_status", "check_out_date", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hostex_reservation_code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    stay_code: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    property_id: Mapped[int] = mapped_column(
        ForeignKey("property_profiles.id"), nullable=False, index=True
    )
    check_in_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_out_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checkout_observed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_hostex_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LifecycleReminder(TimestampMixin, Base):
    """保存一笔订单的一次入住生命周期主动提醒。"""

    __tablename__ = "lifecycle_reminders"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "reminder_type",
            "scheduled_local_date",
            name="uq_lifecycle_reminder_schedule",
        ),
        Index(
            "ix_lifecycle_reminder_status_schedule",
            "status",
            "scheduled_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("stay_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reminder_type: Mapped[ReminderType] = mapped_column(
        Enum(ReminderType, native_enum=False, length=32),
        nullable=False,
    )
    scheduled_local_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus, native_enum=False, length=32),
        default=ReminderStatus.SCHEDULED,
        nullable=False,
    )
    external_message_id: Mapped[str | None] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    platform_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    manual_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RoomOperationalState(TimestampMixin, Base):
    """保存每个物理房间唯一的当前运营状态。"""

    __tablename__ = "room_operational_states"

    property_id: Mapped[int] = mapped_column(ForeignKey("property_profiles.id"), primary_key=True)
    status: Mapped[RoomOperationalStatus] = mapped_column(
        Enum(RoomOperationalStatus, native_enum=False, length=32),
        default=RoomOperationalStatus.NOT_STARTED,
        nullable=False,
    )
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BusinessTask(TimestampMixin, Base):
    """保存保洁、维修、补给和特殊服务等运营任务。"""

    __tablename__ = "business_tasks"
    __table_args__ = (
        CheckConstraint(
            (
                "status IN ('PENDING_CONFIRMATION', 'CANCELLED', 'EXPIRED') "
                "OR (property_id IS NOT NULL AND service_date IS NOT NULL)"
            ),
            name="ck_business_task_execution_fields",
        ),
        Index(
            "ix_business_tasks_status_assignee_service_date",
            "status",
            "assigned_employee_id",
            "service_date",
        ),
        Index("ix_business_tasks_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    task_type: Mapped[BusinessTaskType] = mapped_column(
        Enum(BusinessTaskType, native_enum=False, length=32), nullable=False
    )
    status: Mapped[BusinessTaskStatus] = mapped_column(
        Enum(BusinessTaskStatus, native_enum=False, length=32), nullable=False
    )
    origin_kind: Mapped[BusinessTaskOrigin] = mapped_column(
        Enum(BusinessTaskOrigin, native_enum=False, length=32),
        default=BusinessTaskOrigin.UNKNOWN,
        server_default="UNKNOWN",
        nullable=False,
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("stay_orders.id"), nullable=True, index=True
    )
    property_id: Mapped[int | None] = mapped_column(
        ForeignKey("property_profiles.id"), nullable=True, index=True
    )
    service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assigned_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    checklist: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closure_reason_code: Mapped[TaskClosureReason | None] = mapped_column(
        Enum(TaskClosureReason, native_enum=False, length=32), nullable=True
    )
    closure_source: Mapped[TaskClosureSource | None] = mapped_column(
        Enum(TaskClosureSource, native_enum=False, length=16), nullable=True
    )
    closed_by_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    # 软归档只影响列表可见性，不参与状态机；终态任务才允许归档。
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_by_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )


class TaskAttachment(TimestampMixin, Base):
    """保存任务私有照片或文件的引用，不保存公网地址。"""

    __tablename__ = "task_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("business_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    private_file_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)


class RoomCredential(TimestampMixin, Base):
    """保存房间入住指南、密码密文和私有二维码引用。"""

    __tablename__ = "room_credentials"
    __table_args__ = (
        UniqueConstraint("property_id", "version", name="uq_room_credential_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property_profiles.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    password_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    guide_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    qr_file_id: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CredentialDelivery(TimestampMixin, Base):
    """保存一笔订单使用某版凭证的整体投递状态。"""

    __tablename__ = "credential_deliveries"
    __table_args__ = (UniqueConstraint("order_id", "credential_id", name="uq_credential_delivery"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("stay_orders.id"), nullable=False)
    credential_id: Mapped[int] = mapped_column(ForeignKey("room_credentials.id"), nullable=False)
    status: Mapped[CredentialDeliveryStatus] = mapped_column(
        Enum(CredentialDeliveryStatus, native_enum=False, length=32),
        default=CredentialDeliveryStatus.PENDING,
        nullable=False,
    )


class CredentialDeliveryPart(TimestampMixin, Base):
    """保存指南、密码和二维码各自独立的幂等发送结果。"""

    __tablename__ = "credential_delivery_parts"
    __table_args__ = (UniqueConstraint("delivery_id", "part_type", name="uq_delivery_part_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("credential_deliveries.id", ondelete="CASCADE"), nullable=False
    )
    part_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[CredentialDeliveryStatus] = mapped_column(
        Enum(CredentialDeliveryStatus, native_enum=False, length=32),
        default=CredentialDeliveryStatus.PENDING,
        nullable=False,
    )
    external_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
