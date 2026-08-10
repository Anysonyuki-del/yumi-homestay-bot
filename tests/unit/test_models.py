from sqlalchemy import CheckConstraint, UniqueConstraint

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    ConversationMode,
    CredentialDeliveryStatus,
    CustomerIdentityProvider,
    CustomerMergeStatus,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    AdminCredential,
    BookingApproval,
    BusinessTask,
    CredentialDeliveryPart,
    CustomerIdentity,
    CustomerMergeSuggestion,
    HostexWebhookEvent,
    Message,
    RoomOperationalState,
    RuntimeConfigState,
    StayOrder,
)


def test_domain_status_values_are_stable() -> None:
    """锁定跨服务和数据库共同使用的状态值。"""
    assert ConversationMode.BOT_ACTIVE.value == "bot_active"
    assert ConversationMode.HUMAN_ACTIVE.value == "human_active"
    assert ApprovalStatus.PENDING.value == "pending"
    assert ApprovalStatus.CREATING.value == "creating"
    assert ApprovalStatus.BOOKED.value == "booked"
    assert ApprovalStatus.NEEDS_REVIEW.value == "needs_review"
    assert CustomerIdentityProvider.WECOM_KF.value == "wecom_kf"
    assert CustomerIdentityProvider.HOSTEX.value == "hostex"
    assert CustomerMergeStatus.PENDING.value == "pending"
    assert BusinessTaskType.CLEANING.value == "cleaning"
    assert BusinessTaskStatus.PENDING_CONFIRMATION.value == "pending_confirmation"
    assert RoomOperationalStatus.READY.value == "ready"
    assert CredentialDeliveryStatus.NEEDS_REVIEW.value == "needs_review"


def test_booking_approval_has_idempotency_constraints() -> None:
    """审批编号和百居易订单编号必须由数据库保证唯一。"""
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in BookingApproval.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("approval_code",) in unique_columns
    assert ("hostex_reservation_code",) in unique_columns


def test_external_message_id_is_unique() -> None:
    """同一条企业微信消息只能被持久化一次。"""
    assert Message.__table__.c.external_message_id.unique is True


def test_customer_identity_has_composite_unique_constraint() -> None:
    """客户渠道和外部身份组合必须由数据库保证唯一。"""
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in CustomerIdentity.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("provider", "external_id") in unique_columns


def test_customer_merge_rejects_self_merge_in_database() -> None:
    """合并建议必须在数据库层阻止来源客户等于目标客户。"""
    check_names = {
        constraint.name
        for constraint in CustomerMergeSuggestion.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_customer_merge_distinct" in check_names


def test_operations_models_define_required_unique_keys() -> None:
    """外部事件、订单、任务、房态和投递部件必须具备数据库幂等键。"""
    assert HostexWebhookEvent.__table__.c.event_key.unique is True
    assert StayOrder.__table__.c.hostex_reservation_code.unique is True
    assert BusinessTask.__table__.c.dedupe_key.unique is True
    assert BusinessTask.__table__.c.source_message_id.unique is True
    assert RoomOperationalState.__table__.c.property_id.primary_key is True

    part_unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in CredentialDeliveryPart.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("delivery_id", "part_type") in part_unique_columns


def test_admin_and_runtime_config_models_enforce_singletons() -> None:
    """管理员凭证和运行配置指针必须由数据库约束固定为唯一一行。"""
    admin_checks = {
        constraint.name
        for constraint in AdminCredential.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    state_checks = {
        constraint.name
        for constraint in RuntimeConfigState.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_admin_credentials_singleton" in admin_checks
    assert "ck_runtime_config_state_singleton" in state_checks
    assert AdminCredential.__table__.c.employee_id.unique is True
