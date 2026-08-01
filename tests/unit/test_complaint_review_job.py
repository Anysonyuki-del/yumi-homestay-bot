from dataclasses import dataclass

import pytest

from homestay_bot.integrations.deepseek_complaint import ComplaintDraft
from homestay_bot.services.complaint_review_job import (
    ComplaintReviewJobService,
    SQLAlchemyComplaintMessageContext,
)


@dataclass
class Review:
    id: int = 7
    conversation_id: int = 3
    source_message_id: str = "msg-1"
    reason: str = "complaint"
    risk_level: str = "high"
    status: str = "pending_analysis"
    version: int = 0


class Reviews:
    def __init__(self) -> None:
        self.review = Review()
        self.ready: tuple[dict, str] | None = None

    async def get(self, review_id: int):
        return self.review if review_id == self.review.id else None

    async def mark_ready(self, review_id: int, *, analysis: dict, draft: str):
        self.ready = (analysis, draft)
        self.review.status = "ready_for_review"
        return self.review


class Analyzer:
    async def generate(self, **kwargs):
        return ComplaintDraft(
            core_issue="房间设施问题",
            customer_request="希望解决",
            emotion_level="高",
            customer_claims=["设施不能使用"],
            known_facts=["已收到客诉"],
            facts_to_verify=["需要核实现场"],
            responsibility_risk="待核实",
            reply_tone="温和",
            reply_draft="很抱歉给您带来不便，我们正在核实并尽快处理。",
        )


class Messages:
    async def list_context(self, conversation_id: int, source_message_id: str):
        return [{"role": "user", "content": "房间设施坏了，手机号13800138000"}]


class Notifications:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_internal_text(self, **kwargs):
        self.calls.append(kwargs)

    async def send_internal_card(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_generates_review_and_notifies_without_guest_identity():
    reviews = Reviews()
    notifications = Notifications()
    service = ComplaintReviewJobService(
        reviews=reviews,
        analyzer=Analyzer(),
        messages=Messages(),
        notifications=notifications,
        employee_userids=["admin"],
        agent_id=1000002,
        edit_url="https://example.test/employee/complaints/7",
    )

    await service.handle({"review_id": 7})

    assert reviews.ready is not None
    assert "13800138000" not in str(notifications.calls[0])
    assert "房间设施问题" in notifications.calls[0]["description"]
    assert notifications.calls[0]["url"].endswith("/7")


@pytest.mark.asyncio
async def test_completed_review_is_idempotent():
    reviews = Reviews()
    reviews.review.status = "ready_for_review"
    notifications = Notifications()
    service = ComplaintReviewJobService(
        reviews=reviews,
        analyzer=Analyzer(),
        messages=Messages(),
        notifications=notifications,
        employee_userids=["admin"],
        agent_id=1000002,
        edit_url="https://example.test/employee/complaints/7",
    )

    await service.handle({"review_id": 7})

    assert notifications.calls == []


def test_sqlalchemy_context_sanitizes_identity_values():
    assert SQLAlchemyComplaintMessageContext._sanitize(
        "电话13800138000，邮箱guest@example.com，订单123456789012"
    ) == "电话[手机号已脱敏]，邮箱[邮箱已脱敏]，订单[编号已脱敏]"
