"""危险优先的组合回复走真实出站（规划误判 Spec §8.5 C8-2、H176-R1）：临时 SQLite、真实 outbox
与 Worker；只替换企业微信外部发送。"""

import pytest
from sqlalchemy import select

from homestay_bot.domain.enums import JobStatus, Language
from homestay_bot.domain.models import Conversation, Job
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.worker import RetrySafeJobError, Worker
from tests.integration.test_knowledge_referrals import _seed, _service, factory  # noqa: F401


async def _combined_job(factory, language: Language, message_type: str):  # noqa: F811
    """经真实会话服务登记一次组合回复，返回出站作业与会话服务。"""
    conversation_id, message = await _seed(factory)
    async with factory() as session:
        service = _service(session, None, message.msgid)
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.language = language
        prefix = service._possible_danger_text(language)
        ack = "您的情况我已记录。" if language is Language.ZH else "We have noted this."
        await service._send_combined_safety_reply(conversation, prefix, ack,
                                                  message_type=message_type)
        await session.commit()
    async with factory() as session:
        jobs = (await session.scalars(
            select(Job).where(Job.job_type == "wecom_send_text").order_by(Job.id)
        )).all()
    return jobs, prefix


@pytest.mark.asyncio
@pytest.mark.parametrize("language,message_type", [
    (Language.ZH, "complaint_ack"), (Language.EN, "complaint_ack"), (Language.ZH, "text"),
])
async def test_combined_reply_payload_keeps_prefix_type_and_exemption(factory, language,  # noqa: F811
                                                                      message_type):
    """最终出站只有一条：本地避险正文（含追问）在前、确认语在后；消息类型沿用原分支，
    显式过时豁免。"""
    jobs, prefix = await _combined_job(factory, language, message_type)
    assert len(jobs) == 1
    payload = jobs[0].payload
    assert payload["content"].startswith(prefix)
    assert len(payload["content"]) > len(prefix)
    assert payload.get("message_type", "text") == message_type
    assert payload.get("stale_exempt") is True


@pytest.mark.asyncio
async def test_retry_of_combined_reply_never_lets_the_ack_go_first(factory):  # noqa: F811
    """首条发送可重试失败（连接失败、45009）：只有这一条在重试，确认语不会单独先被受理。"""
    jobs, prefix = await _combined_job(factory, Language.ZH, "complaint_ack")
    accepted: list[str] = []

    async def send(payload):
        """模拟可重试的连接失败。"""
        raise RetrySafeJobError("synthetic-connect-error")

    async with factory() as session:
        worker = Worker(repository=SQLAlchemyJobRepository(session),
                        handlers={"wecom_send_text": send}, checkpoint=session.commit)
        assert await worker.run_once()
        assert not await worker.run_once()  # 唯一一条在退避中，没有可领取的独立确认任务
        rows = (await session.scalars(
            select(Job).where(Job.job_type == "wecom_send_text")
        )).all()
    assert len(rows) == 1 and rows[0].status is JobStatus.PENDING and rows[0].attempts == 1
    assert accepted == []
