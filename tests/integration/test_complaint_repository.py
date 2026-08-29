from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from admin_auth_helpers import RouteAdminVerifierStub, configure_admin_auth
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import (
    ComplaintReviewStatus,
    EmployeeRole,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    Base,
    ComplaintReview,
    Conversation,
    Customer,
    Job,
    Message,
)
from homestay_bot.repositories.complaints import (
    ComplaintVersionConflict,
    SQLAlchemyComplaintRepository,
)
from homestay_bot.routes.complaints import router as complaint_router
from homestay_bot.services.complaint_admin_service import ComplaintAdminService


def _install_versioned_admin_session(
    app: FastAPI,
) -> RouteAdminVerifierStub:
    """为客诉路由测试装配完整管理员会话与真实复核器。"""
    verifier = configure_admin_auth(app, EmployeeRole.ADMIN)

    @app.get("/test/session")
    async def seed_session(request: Request) -> dict[str, str]:
        """写入生产认证所需的全部版本化会话字段。"""
        request.session["employee_id"] = 1
        request.session["employee_role"] = "admin"
        request.session["admin_id"] = 1
        request.session["admin_session_version"] = 1
        request.session["last_activity_at"] = datetime.now(UTC).isoformat()
        request.session["complaint_csrf"] = {"7": "valid-token"}
        return {"status": "seeded"}

    return verifier


@pytest.fixture
async def repository():
    """创建独立数据库，验证客诉记录边界。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        customer = Customer(display_name="投诉客户")
        session.add(customer)
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="wk-test",
            external_userid="wm-test",
        )
        session.add(conversation)
        await session.flush()
        yield SQLAlchemyComplaintRepository(session), session, conversation.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_complaint_review_is_idempotent_by_source_message(repository) -> None:
    """同一来源消息只能创建一条客诉记录。"""
    complaints, session, conversation_id = repository
    first = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-1",
        reason="complaint",
        risk_level="high",
    )
    repeated = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-1",
        reason="refund",
        risk_level="critical",
    )

    assert repeated.id == first.id
    assert repeated.reason == "complaint"
    assert repeated.status is ComplaintReviewStatus.PENDING_ANALYSIS
    await session.commit()


@pytest.mark.asyncio
async def test_duplicate_create_uses_savepoint_without_rolling_back_other_changes(
    repository, monkeypatch
) -> None:
    """并发幂等冲突只回滚保存点，不得丢失同事务中的其他写入。"""
    complaints, session, conversation_id = repository
    first = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-race",
        reason="complaint",
        risk_level="high",
    )
    await session.commit()

    # 模拟两个 worker 同时通过首次查询、随后由唯一约束发现重复。
    original_scalar = session.scalar
    scalar_calls = 0

    async def scalar_with_race(statement):
        nonlocal scalar_calls
        scalar_calls += 1
        if scalar_calls == 1:
            return None
        return await original_scalar(statement)

    monkeypatch.setattr(session, "scalar", scalar_with_race)
    marker = Customer(display_name="同事务保留写入")
    session.add(marker)

    repeated = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id=first.source_message_id,
        reason="refund",
        risk_level="critical",
    )

    assert repeated.id == first.id
    await session.flush()
    assert marker.id is not None
    await session.commit()


@pytest.mark.asyncio
async def test_complaint_review_version_and_status_are_guarded(repository) -> None:
    """编辑和发送必须校验版本，不能覆盖其他员工的更新。"""
    complaints, session, conversation_id = repository
    review = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-2",
        reason="refund",
        risk_level="high",
    )
    await complaints.mark_ready(
        review.id,
        analysis={"core_issue": "延迟入住", "refund_requested": True},
        draft="我会尽快为您核实。",
    )
    updated = await complaints.update_draft(
        review.id,
        expected_version=review.version,
        draft="我已经收到您的情况，会尽快为您核实。",
    )
    assert updated.version == 2
    assert updated.status is ComplaintReviewStatus.EDITING

    with pytest.raises(ComplaintVersionConflict):
        await complaints.update_draft(
            review.id,
            expected_version=1,
            draft="过期内容",
        )

    sent = await complaints.mark_sent(
        review.id,
        expected_version=updated.version,
        sent_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert sent.status is ComplaintReviewStatus.SENT
    await session.commit()


@pytest.mark.asyncio
async def test_complaint_delivery_state_tracks_queue_failure_and_success(repository) -> None:
    """客诉发送应先进入队列，实际投递结果再决定最终状态。"""
    complaints, session, conversation_id = repository
    review = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-delivery-state",
        reason="complaint",
        risk_level="high",
    )
    await complaints.mark_ready(
        review.id,
        analysis={"core_issue": "待核实"},
        draft="我已收到您的反馈，正在为您核实。",
    )
    queued = await complaints.mark_send_queued(
        review.id,
        expected_version=review.version,
        outbox_id="outbox:complaint-1",
    )
    assert queued.status is ComplaintReviewStatus.SEND_QUEUED
    assert queued.delivery_outbox_id == "outbox:complaint-1"
    queued_version = queued.version

    failed = await complaints.mark_delivery_failed(
        review.id,
        error_code="WeComApiError",
    )
    assert failed.status is ComplaintReviewStatus.DELIVERY_FAILED
    assert failed.delivery_error_code == "WeComApiError"
    assert failed.version == queued_version + 1

    sent = await complaints.mark_delivery_sent(
        review.id,
        sent_at=datetime(2026, 8, 2, tzinfo=UTC),
        external_message_id="wecom-msg-1",
    )
    assert sent.status is ComplaintReviewStatus.SENT
    assert sent.sent_at == datetime(2026, 8, 2, tzinfo=UTC)
    assert sent.delivery_error_code is None
    assert sent.delivery_external_message_id == "wecom-msg-1"
    await session.commit()


@pytest.mark.asyncio
async def test_async_wecom_failure_can_find_sent_complaint_by_external_message_id(
    repository,
) -> None:
    """企业微信异步失败事件应把已受理的客诉回写为投递失败。"""
    complaints, session, conversation_id = repository
    review = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-async-fail",
        reason="complaint",
        risk_level="high",
    )
    await complaints.mark_ready(
        review.id,
        analysis={"core_issue": "待核实"},
        draft="我已收到您的反馈。",
    )
    await complaints.mark_send_queued(
        review.id,
        expected_version=review.version,
        outbox_id="outbox:async-fail",
    )
    await complaints.mark_delivery_sent(
        review.id,
        sent_at=datetime(2026, 8, 2, tzinfo=UTC),
        external_message_id="wecom-async-fail",
    )

    failed = await complaints.mark_delivery_failed_by_external_message_id(
        "wecom-async-fail",
        error_code="wecom_async_10",
    )

    assert failed is not None
    assert failed.status is ComplaintReviewStatus.DELIVERY_FAILED
    assert failed.sent_at is None
    assert failed.delivery_error_code == "wecom_async_10"
    await session.commit()


@pytest.mark.asyncio
async def test_failed_complaint_can_be_requeued_and_manual_draft_is_sanitized(
    repository,
) -> None:
    """失败客诉允许生成新出站任务，员工草稿仍经过敏感信息脱敏。"""
    _, session, conversation_id = repository
    reviews = SQLAlchemyComplaintRepository(session)
    review = await reviews.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-retry",
        reason="complaint",
        risk_level="high",
    )
    await reviews.mark_ready(
        review.id,
        analysis={"core_issue": "待核实"},
        draft="原始草稿",
    )

    class Sender:
        """返回可追踪的出站任务编号。"""

        def __init__(self) -> None:
            """初始化发送编号序列。"""
            self.calls = 0

        async def send_text(self, open_kfid, external_userid, content):
            """模拟事务型 outbox 入队。"""
            self.calls += 1
            return f"outbox:retry-{self.calls}"

    sender = Sender()
    service = ComplaintAdminService(session, sender)
    await service.send(
        review.id,
        review.version,
        "手机号 13800138000，请尽快处理",
        employee_id=1,
    )
    assert "13800138000" not in (review.draft or "")
    assert review.status is ComplaintReviewStatus.SEND_QUEUED

    await reviews.mark_delivery_failed(review.id, error_code="WeComApiError")
    await service.send(
        review.id,
        review.version,
        "请继续跟进",
        employee_id=1,
    )

    assert sender.calls == 2
    assert review.status is ComplaintReviewStatus.SEND_QUEUED
    assert review.delivery_outbox_id == "outbox:retry-2"
    await session.commit()


@pytest.mark.asyncio
async def test_complaint_detail_bounds_conversation_messages(repository) -> None:
    """客诉详情不能一次加载无界历史，避免后台页面和数据库查询膨胀。"""
    _, session, conversation_id = repository
    review = await SQLAlchemyComplaintRepository(session).create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-detail-bound",
        reason="complaint",
        risk_level="high",
    )
    conversation = await session.get(Conversation, conversation_id)
    assert conversation is not None
    session.add_all(
        [
            Message(
                conversation_id=conversation_id,
                external_message_id=f"detail-{index}",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content=f"历史消息 {index}",
                sent_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            for index in range(250)
        ]
    )
    await session.flush()

    service = ComplaintAdminService(session, object())
    detail = await service.get_detail(review.id)

    assert len(detail["messages"]) == 200
    assert detail["messages"][0].content == "历史消息 50"
    assert detail["messages"][-1].content == "历史消息 249"
    assert detail["has_older_messages"] is True

    older = await service.get_detail(
        review.id,
        before_message_id=detail["messages"][0].id,
    )

    assert len(older["messages"]) == 50
    assert older["messages"][0].content == "历史消息 0"
    assert older["messages"][-1].content == "历史消息 49"
    assert older["has_older_messages"] is False


@pytest.mark.asyncio
async def test_return_for_analysis_enqueues_new_review_job(repository) -> None:
    """员工退回客诉后必须创建新的可去重分析任务。"""
    _, session, conversation_id = repository
    reviews = SQLAlchemyComplaintRepository(session)
    review = await reviews.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-return",
        reason="complaint",
        risk_level="high",
    )
    await reviews.mark_ready(
        review.id,
        analysis={"core_issue": "待核实"},
        draft="请核实",
    )

    await ComplaintAdminService(session, object()).return_for_analysis(
        review.id,
        review.version,
        employee_id=1,
    )
    jobs = list(
        (
            await session.scalars(
                select(Job).where(Job.job_type == "complaint_review_generate")
            )
        ).all()
    )

    assert review.status is ComplaintReviewStatus.RETURNED
    assert len(jobs) == 1
    assert jobs[0].payload == {"review_id": review.id}
    await session.commit()


def test_complaint_route_rejects_oversized_draft_before_service() -> None:
    """超长客诉草稿必须由请求校验拒绝，不能进入业务服务。"""

    class ServiceStub:
        """记录草稿更新是否被调用。"""

        def __init__(self) -> None:
            """初始化调用记录。"""
            self.called = False

        async def update_draft(self, *args, **kwargs) -> None:
            """标记路由错误地放行了超长草稿。"""
            self.called = True

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="complaint-form-test-secret-at-least-32",
    )
    app.include_router(complaint_router)
    service = ServiceStub()
    app.state.complaint_admin_service = service
    verifier = _install_versioned_admin_session(app)

    with TestClient(app) as client:
        client.get("/test/session")
        response = client.post(
            "/employee/complaints/7/save",
            data={
                "version": "1",
                "draft": "过长草稿" * 1001,
                "csrf_token": "valid-token",
            },
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert service.called is False
    assert verifier.calls == []


def test_complaint_route_valid_form_reaches_service_after_admin_reverification() -> None:
    """有效表单必须真正经过版本化管理员会话复核，不能只靠 422 假通过。"""

    class ServiceStub:
        """记录通过认证后的有效草稿更新。"""

        def __init__(self) -> None:
            """初始化调用状态。"""
            self.called = False

        async def update_draft(self, *args, **kwargs) -> None:
            """记录路由已进入业务服务。"""
            self.called = True

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="complaint-valid-form-test-secret-at-least-32",
    )
    app.include_router(complaint_router)
    service = ServiceStub()
    app.state.complaint_admin_service = service
    verifier = _install_versioned_admin_session(app)

    with TestClient(app) as client:
        client.get("/test/session")
        response = client.post(
            "/employee/complaints/7/save",
            data={
                "version": "1",
                "draft": "请继续核实",
                "csrf_token": "valid-token",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert service.called is True
    assert verifier.calls == [(1, 1)]


def test_complaint_page_uses_shell_and_safe_editing_controls() -> None:
    """客诉复核页应安全换行长文本并保护保存、发送、退回和关闭。"""

    class PageServiceStub:
        """返回固定客诉详情供真实路由渲染。"""

        async def list_open(self, *, offset: int, limit: int):
            """返回一条可从交班列表重新发现的待复核客诉。"""
            assert (offset, limit) == (0, 51)
            return [
                SimpleNamespace(
                    id=7,
                    status="ready_for_review",
                    risk_level="high",
                    reason="complaint",
                    updated_at="2026-08-29 10:00",
                )
            ]

        async def get_detail(self, review_id: int, **kwargs):
            """返回不含敏感身份的分页详情。"""
            return {
                "review": SimpleNamespace(
                    id=review_id,
                    version=2,
                    status="ready_for_review",
                    risk_level="high",
                    analysis={
                        "core_issue": "入住延迟",
                        "customer_request": "尽快入住",
                        "known_facts": ["房间仍在检查"],
                        "facts_to_verify": ["预计完成时间"],
                        "responsibility_risk": "待核实",
                        "refund_or_compensation": False,
                        "platform_escalation_risk": True,
                    },
                    draft="请允许我们继续核实。",
                ),
                "messages": [SimpleNamespace(origin="guest", content="很长的客诉内容")],
                "has_older_messages": False,
                "older_before_message_id": None,
                "is_latest_message_page": True,
            }

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="complaint-page-test-secret-at-least-32")
    app.include_router(complaint_router)
    app.state.complaint_admin_service = PageServiceStub()
    _install_versioned_admin_session(app)

    with TestClient(app) as client:
        client.get("/test/session")
        index = client.get("/employee/complaints")
        response = client.get("/employee/complaints/7")

    assert index.status_code == 200
    assert 'href="/employee/complaints/7"' in index.text
    assert "待处理客诉" in index.text
    assert response.status_code == 200
    assert '/static/admin.js' in response.text
    assert 'data-safe-pre' not in response.text
    assert "核心问题" in response.text
    assert "待核实事实" in response.text
    assert "平台升级风险" in response.text
    assert 'data-unsaved-warning' in response.text
    for action in ("send", "return", "cancel"):
        assert f'action="/employee/complaints/7/{action}" data-confirm=' in response.text


@pytest.mark.asyncio
async def test_complaint_review_does_not_persist_raw_guest_content(repository) -> None:
    """客诉记录只保存脱敏分析和草稿，不接受原始客人正文。"""
    complaints, session, conversation_id = repository
    review = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-3",
        reason="compensation",
        risk_level="high",
    )
    await complaints.mark_ready(
        review.id,
        analysis={"core_issue": "延迟入住", "phone": "13800138000"},
        draft="我会尽快为您核实。",
    )

    stored = await session.get(ComplaintReview, review.id)
    assert stored is not None
    assert "13800138000" not in str(stored.analysis)
    assert not hasattr(stored, "raw_content")
