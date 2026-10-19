"""危险顺序 Spec 的离线反例：退出零证明当前缺口和出站组合控制成立。"""

import asyncio
import json

from sqlalchemy import select

from homestay_bot.application import TransactionalOutboxWeCom, _deferred_message_from_payload
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import ConversationMode, JobStatus
from homestay_bot.domain.models import Base, Job
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.services.answer_policy import agitated_cleared_by_plan, resolve_handoff_reason
from homestay_bot.worker import RetrySafeJobError, Worker
from tests.plan_helpers import plan_for
from tests.unit.test_conversation_plan_flows import _deferred
from tests.unit.test_conversation_service import AssistantStub


async def human_early_return() -> None:
    """从真实即时入口排合并任务，对照人工模式下议价分支吞掉已核验危险。"""
    text = "插座一直滋滋响还发烫！！！可以便宜点吗"
    for risk, marker in [("possible_hazard", "避开可能有危险"),
                         ("current_hazard:electric", "不要触碰")]:
        for mode in [ConversationMode.BOT_ACTIVE, ConversationMode.HUMAN_ACTIVE]:
            def make_plan(content, risk=risk):
                """同一原文内冻结故障与议价两项，仅风险标签随控制变化。"""
                return plan_for(content,
                                ("facility_fault", "插座一直滋滋响还发烫", {"risk": risk}),
                                ("static_fact", "可以便宜点吗", {}))

            plan = make_plan(text)
            assert plan.ok and all(item.valid for item in plan.plan.items)
            assert agitated_cleared_by_plan(plan, text)
            assert resolve_handoff_reason(text, plan) == "price"
            assistant = AssistantStub(plan=make_plan)
            service, conversations, wecom, jobs, source, messages = _deferred(text, assistant)
            conversations.conversation.mode = mode
            messages.recorded.clear()
            await service.handle_message(source)
            assert [job[2] for job in jobs.jobs] == [f"debounce:{source.msgid}"]
            await service.process_debounced_message(source)
            final_jobs = [job for job in jobs.jobs if job[2] == f"final:{source.msgid}"]
            for _, payload, _, _ in final_jobs:
                await service.process_recorded_message(_deferred_message_from_payload(payload))
            warned = any(marker in reply for reply in wecom.guest_messages)
            assert warned is (mode is ConversationMode.BOT_ACTIVE)
            assert assistant.plan_calls == 1
            assert assistant.calls == (1 if mode is ConversationMode.BOT_ACTIVE else 0)
            if mode is ConversationMode.HUMAN_ACTIVE:
                assert not final_jobs and len(wecom.guest_messages) == 1
                assert len(wecom.internal_messages) == 1
            print(json.dumps({"risk": risk, "mode": mode.value, "safety": warned,
                              "final_jobs": len(final_jobs)}, ensure_ascii=False))


async def retry_order(*, combined: bool) -> None:
    """用真实 SQLite、outbox、Worker 验证独立首条重试时后条能越过；组合正文没有此窗口。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with create_session_factory(engine)() as session:
            outbox = TransactionalOutboxWeCom(
                session, source_message_id="synthetic-order", conversation_id=1,
                delivery_phase="ack",
            )
            await outbox.send_text("k", "u", "SAFETY\n\nCOMPLAINT" if combined else "SAFETY",
                                   stale_exempt=True)
            if not combined:
                await outbox.send_text("k", "u", "COMPLAINT", message_type="complaint_ack",
                                       stale_exempt=True)
            await session.commit()
            accepted = []

            async def send(payload):
                """只替换外部发送：安全正文模拟可重试连接失败，其余记录受理。"""
                if payload["content"].startswith("SAFETY"):
                    raise RetrySafeJobError("synthetic-connect-error")
                accepted.append(payload["content"])

            worker = Worker(repository=SQLAlchemyJobRepository(session),
                            handlers={"wecom_send_text": send}, checkpoint=session.commit)
            assert await worker.run_once()
            if not combined:
                assert await worker.run_once()
            rows = (await session.scalars(select(Job).order_by(Job.id))).all()
            assert rows[0].status is JobStatus.PENDING and rows[0].attempts == 1
            assert accepted == ([] if combined else ["COMPLAINT"])
            assert len(rows) == (1 if combined else 2)
            print(json.dumps({"combined": combined, "accepted": accepted,
                              "statuses": [row.status.value for row in rows]}))
    finally:
        await engine.dispose()


async def main() -> None:
    """串行执行四个真实会话控制和两种实际队列布局，无外部请求。"""
    await human_early_return()
    await retry_order(combined=False)
    await retry_order(combined=True)


if __name__ == "__main__":
    asyncio.run(main())
