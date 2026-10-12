"""1.70.0 资料核实只读审查的三个合成反例；只写临时 SQLite，不调用外部服务。"""

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import KnowledgeReferralStatus
from homestay_bot.domain.models import (
    Base,
    Conversation,
    CustomerTag,
    CustomerTagLink,
    KnowledgeEntry,
    KnowledgeReferral,
)
from homestay_bot.integrations.deepseek_faq_drafter import DeepSeekFaqDrafter, StaffDraft
from homestay_bot.repositories.customers import TEST_ACCOUNT_TAG, SQLAlchemyCustomerRepository
from homestay_bot.repositories.knowledge_referrals import SQLAlchemyKnowledgeReferralRepository
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.services.sensitive_text import contains_sensitive_text, sanitize_referral_text
from tests.integration import test_knowledge_referrals as helpers


@asynccontextmanager
async def synthetic_database():
    """通过项目引擎创建隔离临时库，退出时关闭连接并删除全部合成数据。"""
    with tempfile.TemporaryDirectory(prefix="homestay-fix2-review-") as directory:
        engine = create_engine(f"sqlite+aiosqlite:///{directory}/synthetic.db")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            yield create_session_factory(engine)
        finally:
            await engine.dispose()


async def probe_credentials():
    """捕获真实草稿 SDK 请求，并验证漏检密码能经过生成校验与审核入库。"""
    secret_message = "Wi-Fi password: A!b2C#d4"
    assert not contains_sensitive_text(secret_message)
    assert sanitize_referral_text(secret_message) == secret_message
    assert not contains_sensitive_text("门锁密码：1#2#3#4")
    draft = StaffDraft(
        found_answer=True, category="设施", question_zh="Wi-Fi密码是什么？",
        answer_zh="Wi-Fi密码：A!b2C#d4", question_en="What is the Wi-Fi password?",
        answer_en=secret_message, keywords=["Wi-Fi"], source_quotes=[secret_message],
    )
    requests = []

    async def fake_create(**kwargs):
        """仅捕获实际请求；返回合成 JSON，不发起网络调用。"""
        requests.append(json.loads(kwargs["messages"][1]["content"]))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=draft.model_dump_json())
        )])

    async with synthetic_database() as factory:
        await helpers._referral_with_staff_replies(factory, secret_message)
        drafter = DeepSeekFaqDrafter(
            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=fake_create
            ))), model="synthetic-no-network",
        )
        await helpers._start_and_run_draft(factory, drafter)
        assert requests[0]["staff_messages"] == [secret_message]
        async with factory() as session:
            referral = await session.get(KnowledgeReferral, 1)
            assert referral.status is KnowledgeReferralStatus.DRAFTED
            entry = await KnowledgeAdminService(session).convert_referral(
                1, None, generation=1, reviewed=True,
                **helpers._convert_fields(
                    question_zh=draft.question_zh, answer_zh=draft.answer_zh,
                    question_en=draft.question_en, answer_en=draft.answer_en,
                    keywords=draft.keywords,
                ),
            )
            assert entry.is_enabled and "A!b2C#d4" in entry.answer_en
            print("MODEL_REQUEST", json.dumps(requests[0], ensure_ascii=False))
            print("ENABLED_ENTRY", entry.is_enabled, entry.answer_zh, entry.answer_en)


async def probe_stale_sqlite_conversion():
    """在房间范围校验后暂停旧代次转换，真实清空先提交，再观察旧转换写回。"""
    async with synthetic_database() as factory:
        await helpers._drafted(factory)
        async with factory() as session:
            conversation = await session.get(Conversation, 1)
            customer_id = conversation.customer_id
            tag = CustomerTag(name=TEST_ACCOUNT_TAG, is_active=True)
            session.add(tag)
            await session.flush()
            session.add(CustomerTagLink(customer_id=customer_id, tag_id=tag.id))
            await session.commit()

        reached = asyncio.Event()
        release = asyncio.Event()

        class PausedAdmin(KnowledgeAdminService):
            async def _validate_scope(self, fields):
                """执行真实房源读取后提供确定性交错点，不修改任何业务判断。"""
                await super()._validate_scope(fields)
                reached.set()
                await release.wait()

        async def convert():
            """沿用页面代次 1 与已核对字段，提交房间 401 范围。"""
            async with factory() as session:
                return await PausedAdmin(session).convert_referral(
                    1, None, generation=1, reviewed=True,
                    **helpers._convert_fields(scope="property", property_id=401),
                )

        task = asyncio.create_task(convert())
        try:
            await asyncio.wait_for(reached.wait(), timeout=10)
            async with factory() as session:
                await SQLAlchemyCustomerRepository(session).clear_test_customer_data(
                    customer_id, administrator_id=None,
                )
                await session.commit()
                referral = await SQLAlchemyKnowledgeReferralRepository(session).get(1)
                assert referral.status is KnowledgeReferralStatus.CLEARED
                assert referral.generation == 2 and referral.question is None
                print("AFTER_CLEAR", referral.status, referral.generation, referral.question)
            release.set()
            entry = await asyncio.wait_for(task, timeout=10)
            async with factory() as session:
                referral = await SQLAlchemyKnowledgeReferralRepository(session).get(1)
                persisted = await session.get(KnowledgeEntry, entry.id)
                assert referral.status is KnowledgeReferralStatus.CONVERTED
                assert referral.generation == 2 and referral.question is None
                assert persisted.property_id == 401 and persisted.is_enabled
                print("PROPERTY_SCOPE_STALE_CONVERT", referral.status,
                      referral.generation, persisted.property_id, persisted.is_enabled)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def probe_hidden_open_record():
    """唯一待处理记录比 100 条已关闭记录旧，统计有待处理而默认列表不可见。"""
    async with synthetic_database() as factory:
        conversation_id, _message = await helpers._seed(factory)
        async with factory() as session:
            now = datetime.now(UTC)
            for index in range(101):
                session.add(KnowledgeReferral(
                    conversation_id=conversation_id, source_message_id=f"list-{index}",
                    unit_id="1:0-1", plan_item_id=1, question="合成问题",
                    normalized_question="合成", created_at=now + timedelta(seconds=index),
                    status=(KnowledgeReferralStatus.PENDING if index == 0
                            else KnowledgeReferralStatus.CLOSED),
                ))
            await session.commit()
            repository = SQLAlchemyKnowledgeReferralRepository(session)
            rows = await repository.list_recent()
            open_count = await repository.count_open()
            open_visible = sum(row.status is KnowledgeReferralStatus.PENDING for row in rows)
            assert (len(rows), open_count, open_visible) == (100, 1, 0)
            print("LIST", len(rows), "open_count", open_count, "open_visible", open_visible)


async def main():
    """顺序执行三个已验证反例；退出 0 表示缺陷仍能复现，不表示修复通过。"""
    await probe_credentials()
    await probe_stale_sqlite_conversion()
    await probe_hidden_open_record()


if __name__ == "__main__":
    asyncio.run(main())
