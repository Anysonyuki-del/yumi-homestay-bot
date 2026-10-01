"""客诉发送在途判定、条件更新与回写归属（Spec §4.1.1、AC01a、AC02、AC03）。

全部走生产装配的 SessionComplaintAdminService 与真实事务型 outbox，请求与 worker
回写各用独立 Session，避免共享对象掩盖跨事务的丢更新。
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware
from test_complaint_repository import _install_versioned_admin_session

from homestay_bot import application
from homestay_bot.domain.enums import ComplaintReviewStatus, EmployeeRole, JobStatus
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    Base,
    ComplaintReview,
    Conversation,
    Customer,
    Employee,
    Job,
)
from homestay_bot.repositories.complaints import (
    ComplaintStateRefused,
    ComplaintVersionConflict,
)
from homestay_bot.routes.complaints import router as complaint_router
from homestay_bot.routes.page_errors import handle_operation_refused
from homestay_bot.services.complaint_admin_service import ComplaintAdminService

REVIEW_ID = 7


async def seed_review(session_factory) -> None:
    """放入一位客户、一个会话和一条待复核客诉（编号固定为 REVIEW_ID）。

    PostgreSQL 版本（test_complaint_delivery_postgresql）复用同一份数据。
    """
    async with session_factory() as session:
        # 审计记录指向员工；PostgreSQL 会检查这条外键，SQLite 测试库默认不检查。
        session.add_all(
            [
                Employee(id=1, wecom_userid="synthetic-a", name="管理员甲",
                         role=EmployeeRole.ADMIN, is_active=True),
                Employee(id=2, wecom_userid="synthetic-b", name="管理员乙",
                         role=EmployeeRole.ADMIN, is_active=True),
            ]
        )
        customer = Customer(display_name="投诉客户")
        session.add(customer)
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id, open_kfid="wk-test", external_userid="wm-test"
        )
        session.add(conversation)
        await session.flush()
        session.add(
            ComplaintReview(
                id=REVIEW_ID,
                conversation_id=conversation.id,
                source_message_id="msg-guard",
                reason="complaint",
                risk_level="high",
                status=ComplaintReviewStatus.READY_FOR_REVIEW,
                draft="已保存的旧稿",
                version=1,
            )
        )
        await session.commit()


@pytest.fixture
async def factory(tmp_path):
    """文件型 SQLite：多个 Session 真正各自开事务，才能复现交错。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'complaints.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_review(session_factory)
    yield session_factory
    await engine.dispose()


async def _review(session_factory) -> ComplaintReview:
    """在独立 Session 里读取最新客诉。"""
    async with session_factory() as session:
        review = await session.get(ComplaintReview, REVIEW_ID)
        assert review is not None
        return review


async def _send_jobs(session_factory) -> list[Job]:
    """列出全部客人出站任务。"""
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Job).where(Job.job_type == "wecom_send_text").order_by(Job.id)
                )
            ).all()
        )


async def _set_job_status(session_factory, outbox_id: str, status: JobStatus) -> None:
    """模拟 worker 对原任务的处理结果（mark_failed 放回 PENDING 或终态 FAILED）。"""
    async with session_factory() as session:
        job = await session.scalar(select(Job).where(Job.dedupe_key == outbox_id))
        assert job is not None
        job.status = status
        await session.commit()


async def _writeback(session_factory, outbox_id: str, *, delivered: bool) -> None:
    """以 worker 的方式回写投递结果。"""
    async with session_factory() as session:
        await application._record_complaint_delivery(
            session,
            f"complaint:{REVIEW_ID}",
            delivered=delivered,
            error_code=None if delivered else "ConnectError",
            external_message_id="wecom-msg" if delivered else None,
            outbox_id=outbox_id,
        )
        await session.commit()


@pytest.mark.asyncio
async def test_stale_cancel_cannot_override_committed_send(factory) -> None:
    """E10：B 读到旧版本后，A 已提交发送；B 的关闭必须被拒，不能盖掉发送。"""
    service = application.SessionComplaintAdminService(factory)
    async with factory() as stale_session:
        stale = await stale_session.get(ComplaintReview, REVIEW_ID)
        assert stale is not None and stale.version == 1

        await service.send(REVIEW_ID, 1, "新写的回复", employee_id=1)

        with pytest.raises(ComplaintVersionConflict):
            # B 仍持有旧对象：条件更新按数据库当前版本判定，旧版本被拒。
            await ComplaintAdminService(stale_session, sender=None).cancel(  # type: ignore[arg-type]
                REVIEW_ID, 1, employee_id=2
            )

    review = await _review(factory)
    assert review.status is ComplaintReviewStatus.SEND_QUEUED
    assert review.version == 2
    assert review.draft == "新写的回复"
    jobs = await _send_jobs(factory)
    assert [job.status for job in jobs] == [JobStatus.PENDING]


@pytest.mark.asyncio
async def test_stale_version_send_is_rejected_after_concurrent_save(factory) -> None:
    """同版本交错：A 先保存，B 拿旧版本发送被拒，数据库与 outbox 都不被 B 改写。"""
    service = application.SessionComplaintAdminService(factory)
    await service.update_draft(REVIEW_ID, 1, "A 保存的内容")

    with pytest.raises(ComplaintVersionConflict):
        await service.send(REVIEW_ID, 1, "B 未保存的内容", employee_id=2)

    review = await _review(factory)
    assert (review.status, review.version, review.draft) == (
        ComplaintReviewStatus.EDITING,
        2,
        "A 保存的内容",
    )
    assert await _send_jobs(factory) == []


@pytest.mark.asyncio
async def test_resend_close_and_return_refused_while_original_job_retries(factory) -> None:
    """E1/E2：连接失败后原任务仍在自动重试，手动重发、关闭、退回都要拒绝。"""
    service = application.SessionComplaintAdminService(factory)
    await service.send(REVIEW_ID, 1, "第一次发送", employee_id=1)
    first = (await _send_jobs(factory))[0]
    # worker 先写失败、再把原任务放回 PENDING（jobs.mark_failed 的可重试分支）。
    await _writeback(factory, first.dedupe_key, delivered=False)
    await _set_job_status(factory, first.dedupe_key, JobStatus.PENDING)
    review = await _review(factory)
    assert review.status is ComplaintReviewStatus.DELIVERY_FAILED

    for attempt in (
        service.send(REVIEW_ID, review.version, "手动重发", employee_id=1),
        service.cancel(REVIEW_ID, review.version, employee_id=1),
        service.return_for_analysis(REVIEW_ID, review.version, employee_id=1),
    ):
        with pytest.raises(ComplaintStateRefused):
            await attempt

    assert len(await _send_jobs(factory)) == 1
    after = await _review(factory)
    assert (after.status, after.version) == (review.status, review.version)


@pytest.mark.asyncio
async def test_retry_after_terminal_failure_registers_one_new_attempt(factory) -> None:
    """原任务终态失败后允许一次手动重试；同版本重复提交不再新增任务。"""
    service = application.SessionComplaintAdminService(factory)
    await service.send(REVIEW_ID, 1, "第一次发送", employee_id=1)
    first = (await _send_jobs(factory))[0]
    await _writeback(factory, first.dedupe_key, delivered=False)
    await _set_job_status(factory, first.dedupe_key, JobStatus.FAILED)
    failed = await _review(factory)

    await service.send(REVIEW_ID, failed.version, "改过的重试内容", employee_id=1)
    with pytest.raises((ComplaintVersionConflict, ComplaintStateRefused)):
        await service.send(REVIEW_ID, failed.version, "改过的重试内容", employee_id=1)

    jobs = await _send_jobs(factory)
    assert len(jobs) == 2
    review = await _review(factory)
    assert review.status is ComplaintReviewStatus.SEND_QUEUED
    assert review.delivery_outbox_id == jobs[1].dedupe_key
    assert jobs[1].payload["content"] == "改过的重试内容"


@pytest.mark.asyncio
async def test_late_result_of_old_attempt_does_not_override_new_attempt(factory) -> None:
    """旧任务迟到的成功回写不能把新尝试标成已发送；新任务的回写照常生效。"""
    service = application.SessionComplaintAdminService(factory)
    await service.send(REVIEW_ID, 1, "第一次发送", employee_id=1)
    old = (await _send_jobs(factory))[0]
    await _writeback(factory, old.dedupe_key, delivered=False)
    await _set_job_status(factory, old.dedupe_key, JobStatus.FAILED)
    await service.send(REVIEW_ID, (await _review(factory)).version, "重试", employee_id=1)
    new = (await _send_jobs(factory))[1]

    await _writeback(factory, old.dedupe_key, delivered=True)
    assert (await _review(factory)).status is ComplaintReviewStatus.SEND_QUEUED

    await _writeback(factory, new.dedupe_key, delivered=True)
    assert (await _review(factory)).status is ComplaintReviewStatus.SENT


@pytest.mark.asyncio
async def test_sent_and_cancelled_reviews_are_read_only(factory) -> None:
    """D2：已发送、已关闭的客诉不能再退回或关闭；SEND_QUEUED 也不能关闭或退回。"""
    service = application.SessionComplaintAdminService(factory)
    await service.send(REVIEW_ID, 1, "发送", employee_id=1)
    queued = await _review(factory)
    for attempt in (
        service.cancel(REVIEW_ID, queued.version, employee_id=1),
        service.return_for_analysis(REVIEW_ID, queued.version, employee_id=1),
    ):
        with pytest.raises(ComplaintStateRefused):
            await attempt

    job = (await _send_jobs(factory))[0]
    await _set_job_status(factory, job.dedupe_key, JobStatus.COMPLETED)
    await _writeback(factory, job.dedupe_key, delivered=True)
    sent = await _review(factory)
    assert sent.status is ComplaintReviewStatus.SENT
    for attempt in (
        service.cancel(REVIEW_ID, sent.version, employee_id=1),
        service.return_for_analysis(REVIEW_ID, sent.version, employee_id=1),
    ):
        with pytest.raises(ComplaintStateRefused):
            await attempt


def _client(session_factory) -> TestClient:
    """装配真实客诉服务与生产异常处理器的最小应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="complaint-guard-test-secret-at-least-32")
    app.include_router(complaint_router)
    app.add_exception_handler(OperationRefused, handle_operation_refused)
    app.state.complaint_admin_service = application.SessionComplaintAdminService(
        session_factory
    )
    _install_versioned_admin_session(app)
    return TestClient(app)


def _token(html: str) -> str:
    """从页面取出新签发的一次性令牌。"""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


HTML = {"accept": "text/html"}


@pytest.mark.asyncio
async def test_send_without_confirmation_renders_confirm_step_then_sends_edited_text(
    factory,
) -> None:
    """D1-A：发送的是回复框里的当前内容；未经确认先展示正文，确认后才登记任务。"""
    with _client(factory) as client:
        token = client.get("/test/session").json()["csrf_token"]
        step = client.post(
            f"/employee/complaints/{REVIEW_ID}/send",
            data={"version": "1", "draft": "改过但没保存的回复", "csrf_token": token},
            headers=HTML,
        )
        assert step.status_code == 200
        assert "确认发送" in step.text and "改过但没保存的回复" in step.text
        assert await _send_jobs(factory) == []

        done = client.post(
            f"/employee/complaints/{REVIEW_ID}/send",
            data={
                "version": "1",
                "draft": "改过但没保存的回复",
                "csrf_token": _token(step.text),
                "confirmed": "1",
            },
            headers=HTML,
            follow_redirects=False,
        )
    assert done.status_code == 303
    jobs = await _send_jobs(factory)
    assert [job.payload["content"] for job in jobs] == ["改过但没保存的回复"]
    assert (await _review(factory)).draft == "改过但没保存的回复"


@pytest.mark.asyncio
async def test_stale_save_rerenders_latest_state_with_unsaved_text(factory) -> None:
    """AC03：旧版本保存被拒时，页面给出最新版本，同时保留员工未保存的正文。"""
    await application.SessionComplaintAdminService(factory).update_draft(
        REVIEW_ID, 1, "别人刚保存的内容"
    )
    with _client(factory) as client:
        token = client.get("/test/session").json()["csrf_token"]
        response = client.post(
            f"/employee/complaints/{REVIEW_ID}/save",
            data={"version": "1", "draft": "我还没保存的修改", "csrf_token": token},
            headers=HTML,
        )
    assert response.status_code == 409
    assert "已被其他员工更新" in response.text
    assert "我还没保存的修改" in response.text
    assert 'name="version" value="2"' in response.text
    assert (await _review(factory)).draft == "别人刚保存的内容"


@pytest.mark.asyncio
async def test_cancel_refusal_redirects_with_reason_instead_of_500(factory) -> None:
    """不带输入的操作被拒时回跳详情页显示原因，不再是 500。"""
    await application.SessionComplaintAdminService(factory).send(
        REVIEW_ID, 1, "发送", employee_id=1
    )
    with _client(factory) as client:
        token = client.get("/test/session").json()["csrf_token"]
        response = client.post(
            f"/employee/complaints/{REVIEW_ID}/cancel",
            data={"version": "2", "csrf_token": token},
            headers=HTML,
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/employee/complaints/{REVIEW_ID}"
        page = client.get(response.headers["location"], headers=HTML)
    assert "正在发送或自动重试中" in page.text
    assert (await _review(factory)).status is ComplaintReviewStatus.SEND_QUEUED
    async with factory() as session:
        count = await session.scalar(select(func.count(Job.id)))
    assert count == 1


class _Analyzer:
    """合成分析器；on_generate 在「模型等待期间」执行，用来插入别的请求。"""

    def __init__(self, on_generate=None) -> None:
        self._on_generate = on_generate

    async def generate(self, **kwargs):
        from homestay_bot.integrations.deepseek_complaint import ComplaintDraft

        if self._on_generate is not None:
            await self._on_generate()
        return ComplaintDraft(
            core_issue="设施问题", customer_request="尽快处理", emotion_level="高",
            customer_claims=["不能用"], known_facts=["已收到"], facts_to_verify=["核实"],
            responsibility_risk="待核实", reply_tone="温和", reply_draft="迟到的分析稿",
        )


class _Context:
    """合成对话上下文，不读真实消息。"""

    async def list_context(self, conversation_id, source_message_id):
        return [{"role": "user", "content": "设施坏了"}]


async def _set_pending_analysis(session_factory) -> None:
    """把合成客诉放回待分析状态，模拟刚登记或刚退回。"""
    async with session_factory() as session:
        review = await session.get(ComplaintReview, REVIEW_ID)
        review.status = ComplaintReviewStatus.PENDING_ANALYSIS
        await session.commit()


async def _run_review_job(session_factory, analyzer, notifications=None) -> None:
    """按生产装配运行一次客诉分析任务：同一 worker 会话、事务型通知、保存点。"""
    from homestay_bot.services.complaint_review_job import ComplaintReviewJobService

    async with session_factory() as session:
        from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository

        service = ComplaintReviewJobService(
            reviews=SQLAlchemyComplaintRepository(session),
            analyzer=analyzer,
            messages=_Context(),
            notifications=notifications
            or application.TransactionalOutboxWeCom(
                session, source_message_id=f"complaint-review:{REVIEW_ID}"
            ),
            employee_userids=["synthetic-admin"],
            agent_id=1,
            edit_url="https://example.test/employee/complaints",
            atomic=session.begin_nested,
        )
        try:
            await service.handle({"review_id": REVIEW_ID})
        finally:
            # worker 无论成败都会提交（失败时提交的是失败标记），这里同样提交。
            await session.commit()


async def _card_jobs(session_factory) -> int:
    """登记了几张员工复核卡片。"""
    async with session_factory() as session:
        return len(
            list(
                await session.scalars(
                    select(Job).where(Job.job_type == "wecom_send_internal_card")
                )
            )
        )


@pytest.mark.asyncio
async def test_late_analysis_cannot_reopen_a_cancelled_review(factory) -> None:
    """M2：分析等待期间管理员关闭了客诉；迟到的结果不改状态、草稿、版本，也不发卡片。"""
    await _set_pending_analysis(factory)
    before = await _review(factory)

    async def admin_cancels() -> None:
        await application.SessionComplaintAdminService(factory).cancel(
            REVIEW_ID, before.version, employee_id=1
        )

    await _run_review_job(factory, _Analyzer(on_generate=admin_cancels))

    after = await _review(factory)
    assert after.status is ComplaintReviewStatus.CANCELLED
    assert after.version == before.version + 1
    assert after.draft == "已保存的旧稿"
    assert await _card_jobs(factory) == 0


@pytest.mark.asyncio
async def test_failed_card_registration_rolls_back_ready_state(factory) -> None:
    """卡片登记失败时保存点撤销就绪状态；任务提交的只有失败，重试能重新生成入口。"""
    await _set_pending_analysis(factory)

    class BrokenNotifications:
        async def send_internal_card(self, **kwargs):
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        await _run_review_job(factory, _Analyzer(), BrokenNotifications())

    review = await _review(factory)
    assert review.status is ComplaintReviewStatus.PENDING_ANALYSIS
    assert review.draft == "已保存的旧稿"

    await _run_review_job(factory, _Analyzer())
    ready = await _review(factory)
    assert ready.status is ComplaintReviewStatus.READY_FOR_REVIEW
    assert ready.draft == "迟到的分析稿"
    assert await _card_jobs(factory) == 1


@pytest.mark.asyncio
async def test_stale_version_cannot_enter_the_confirm_step(factory) -> None:
    """M1：B 拿过期版本点发送，不出确认面板，按冲突恢复；A 的草稿不被覆盖。"""
    await application.SessionComplaintAdminService(factory).update_draft(
        REVIEW_ID, 1, "A 刚保存的内容"
    )
    with _client(factory) as client:
        token = client.get("/test/session").json()["csrf_token"]
        response = client.post(
            f"/employee/complaints/{REVIEW_ID}/send",
            data={"version": "1", "draft": "B 的旧稿", "csrf_token": token},
            headers=HTML,
        )
    assert response.status_code == 409
    assert "已被其他员工更新" in response.text
    assert "B 的旧稿" in response.text
    assert 'id="confirm-send-title"' not in response.text
    assert await _send_jobs(factory) == []
    assert (await _review(factory)).draft == "A 刚保存的内容"


@pytest.mark.asyncio
async def test_a_save_between_confirm_and_send_is_rejected(factory) -> None:
    """M1：确认面板打开后又有人保存，最终发送仍按原版本被拒，不登记任务。"""
    with _client(factory) as client:
        token = client.get("/test/session").json()["csrf_token"]
        step = client.post(
            f"/employee/complaints/{REVIEW_ID}/send",
            data={"version": "1", "draft": "B 确认中的回复", "csrf_token": token},
            headers=HTML,
        )
        assert step.status_code == 200 and 'id="confirm-send-title"' in step.text
        await application.SessionComplaintAdminService(factory).update_draft(
            REVIEW_ID, 1, "A 在此期间保存"
        )
        final = client.post(
            f"/employee/complaints/{REVIEW_ID}/send",
            data={
                "version": re.search(
                    r'id="confirm-send-title".*?name="version" value="(\d+)"', step.text, re.S
                ).group(1),
                "draft": "B 确认中的回复",
                "csrf_token": _token(step.text),
                "confirmed": "1",
            },
            headers=HTML,
        )
    assert final.status_code == 409
    assert await _send_jobs(factory) == []
    assert (await _review(factory)).draft == "A 在此期间保存"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["save", "send"])
async def test_empty_reply_gets_a_recoverable_page_not_json(factory, action) -> None:
    """M3：清空回复框后保存或发送，得到可继续修改的页面（422），不登记任务；改好后能提交。"""
    with _client(factory) as client:
        token = client.get("/test/session").json()["csrf_token"]
        empty = client.post(
            f"/employee/complaints/{REVIEW_ID}/{action}",
            data={"version": "1", "draft": "", "csrf_token": token, "confirmed": "1"},
            headers=HTML,
        )
        assert empty.status_code == 422
        assert empty.headers["content-type"].startswith("text/html")
        assert "回复内容不能为空" in empty.text
        assert await _send_jobs(factory) == []

        fixed = client.post(
            f"/employee/complaints/{REVIEW_ID}/{action}",
            data={
                "version": "1",
                "draft": "补好的回复",
                "csrf_token": _token(empty.text),
                "confirmed": "1",
            },
            headers=HTML,
            follow_redirects=False,
        )
    assert fixed.status_code == 303
    assert (await _review(factory)).draft == "补好的回复"


@pytest.mark.asyncio
async def test_cleared_message_bodies_collapse_into_one_line_not_none(factory) -> None:
    """正文已清除的消息不再一条条显示成「None」，连续的合并成一行说明；有正文的照常显示。"""
    from datetime import UTC, datetime, timedelta

    from homestay_bot.domain.enums import MessageOrigin
    from homestay_bot.domain.models import Message

    start = datetime(2026, 9, 25, 15, 30, tzinfo=UTC)
    async with factory() as session:
        review = await session.get(ComplaintReview, REVIEW_ID)
        for index, (origin, content) in enumerate(
            [
                (MessageOrigin.GUEST, None),
                (MessageOrigin.BOT, None),
                (MessageOrigin.GUEST, None),
                (MessageOrigin.GUEST, "我要退钱"),
                (MessageOrigin.BOT, None),
            ]
        ):
            session.add(
                Message(
                    conversation_id=review.conversation_id,
                    external_message_id=f"synthetic-{index}",
                    origin=origin,
                    message_type="text",
                    content=content,
                    sent_at=start + timedelta(minutes=index),
                )
            )
        await session.commit()

    with _client(factory) as client:
        client.get("/test/session")
        text = client.get(f"/employee/complaints/{REVIEW_ID}", headers=HTML).text

    conversation = text.split("完整对话", 1)[1].split("回复客人", 1)[0]
    assert ">None<" not in conversation
    assert "我要退钱" in conversation
    assert conversation.count("message-cleared") == 2
    assert "（3 条消息的正文已清除" in conversation
    assert "（1 条消息的正文已清除" in conversation
    assert conversation.index("3 条消息") < conversation.index("我要退钱") < conversation.index(
        "1 条消息"
    )
