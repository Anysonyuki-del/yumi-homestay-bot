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
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from homestay_bot.domain.enums import (
    ApprovalStatus,
    ConversationMode,
    CustomerIdentityProvider,
    CustomerMergeStatus,
    EmployeeRole,
    JobStatus,
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
    Language,
    MessageOrigin,
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
        default=EmployeeRole.CUSTOMER_SERVICE,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Customer(TimestampMixin, Base):
    """保存跨渠道共享的客户主档和加密联系方式。"""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
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
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="customer"
    )


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
    wecom_tag_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
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
    last_sync_error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
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
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False
    )
    target_customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CustomerMergeStatus] = mapped_column(
        Enum(CustomerMergeStatus, native_enum=False, length=16),
        default=CustomerMergeStatus.PENDING,
        nullable=False,
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    short_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    long_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


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
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    short_summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


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
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occurrences: Mapped[list["KnowledgeCandidateOccurrence"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class KnowledgeCandidateOccurrence(Base):
    """保存候选的一次去重出现，不复制客人正文或身份。"""

    __tablename__ = "knowledge_candidate_occurrences"
    __table_args__ = (
        Index("ix_candidate_occurrence_window", "candidate_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_candidates.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
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
    guest_name: Mapped[str] = mapped_column(String(100), nullable=False)
    guest_mobile: Mapped[str] = mapped_column(String(32), nullable=False)
    room_type_preference: Mapped[str] = mapped_column(String(128), nullable=False)
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    __table_args__ = (Index("ix_jobs_claim", "status", "available_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )
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
