from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from homestay_bot.domain.enums import (
    ApprovalStatus,
    ConversationMode,
    EmployeeRole,
    JobStatus,
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


class Conversation(TimestampMixin, Base):
    """保存一个微信客服账号与一个外部联系人的会话状态。"""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("open_kfid", "external_userid", name="uq_conversation_participants"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
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
