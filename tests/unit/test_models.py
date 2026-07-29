from sqlalchemy import UniqueConstraint

from homestay_bot.domain.enums import ApprovalStatus, ConversationMode
from homestay_bot.domain.models import BookingApproval, Message


def test_domain_status_values_are_stable() -> None:
    """锁定跨服务和数据库共同使用的状态值。"""
    assert ConversationMode.BOT_ACTIVE.value == "bot_active"
    assert ConversationMode.HUMAN_ACTIVE.value == "human_active"
    assert ApprovalStatus.PENDING.value == "pending"
    assert ApprovalStatus.CREATING.value == "creating"
    assert ApprovalStatus.BOOKED.value == "booked"
    assert ApprovalStatus.NEEDS_REVIEW.value == "needs_review"


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
