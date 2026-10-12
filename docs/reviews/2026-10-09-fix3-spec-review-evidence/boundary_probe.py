"""离线核验 fix3 Spec 的值边界、区间内日期和空来源状态，退出零表示反例成立。"""

import asyncio
import importlib.util
import re
from datetime import date
from pathlib import Path

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import KnowledgeReferralStatus, Language
from homestay_bot.domain.models import Base, KnowledgeEntry
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.routes.knowledge import KnowledgeAdminService
from homestay_bot.services import sensitive_text
from homestay_bot.services.complaint_review_job import SQLAlchemyComplaintMessageContext
from homestay_bot.services.knowledge_service import KnowledgeService
from tests.integration.test_knowledge_referrals import (
    _jobs,
    _referral_with_staff_replies,
    _referrals,
)


def credential_probe():
    """只模拟明确冒号后的连续 ASCII 值规则，经过真实客诉净化调用方检查尾部。"""
    rules = sensitive_text._REDACTION_RULES
    proxy = re.compile(
        r"\b(?:(?:door|lock|room|wi-?fi)\s*)?password\b\s*[:=：]\s*[!-~]{4,}",
        re.IGNORECASE,
    )
    try:
        sensitive_text._REDACTION_RULES = ((proxy, "[credential removed]"), *rules)
        quoted = 'Wi-Fi password: "A1! SYNTHETIC_VALUE_TAIL"'
        actual = SQLAlchemyComplaintMessageContext._sanitize(quoted)
        assert "SYNTHETIC_VALUE_TAIL" in actual
        control = SQLAlchemyComplaintMessageContext._sanitize("Wi-Fi password: A!b2C#d4")
        assert "A!b2C#d4" not in control
        print("R1_SPEC_PROXY", actual)
    finally:
        sensitive_text._REDACTION_RULES = rules


async def date_probe():
    """复用前轮真实适配器与最终入队探针，检查 Spec 允许的区间内另一日问法。"""
    path = Path("docs/reviews/2026-10-08-fix2-implementation-review-evidence/coverage_probe.py")
    spec = importlib.util.spec_from_file_location("fix2_date_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ARGS = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"}
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory() as session:
            session.add(KnowledgeEntry(
                id=6001, category="早餐", scope="global", is_enabled=True,
                question_zh="早餐几点开始？", answer_zh="早餐7:30开始。",
                question_en="When is breakfast?", answer_en="Breakfast starts at 7:30.",
                keywords=["早餐"], valid_from=date(2026, 10, 10),
                valid_until=date(2026, 10, 10),
            ))
            await session.commit()
            knowledge = KnowledgeService(SQLAlchemyKnowledgeRepository(session))
            rows = [module._availability_row(1401, "2026-10-10", "2026-10-12")]
            for fallback in (False, True):
                text = "10月10日住2晚有房吗，10月11日早餐几点开始"
                decision = await module.answer(
                    text, "search_availability", rows, knowledge, fallback=fallback,
                )
                # 这里只判 Spec 明写的区间外守卫；区间内输入不会触发新拒绝。
                spec_rejects = not date(2026, 10, 10) <= date(2026, 10, 11) <= date(2026, 10, 11)
                assert not spec_rejects
                await module.show(f"inside_range_{fallback}", text, decision)
                assert "2026-10-10 — 2026-10-10：早餐7:30开始" in decision.reply_text
                assert any(g.text == "10月11日早餐几点开始" for g in decision.gap_units)
                print("R3_SPEC_GUARD", spec_rejects)
            found = await knowledge.retrieve(
                Language.ZH, "10月11日早餐几点开始", target_date=date(2026, 10, 11),
            )
            assert found == []
    finally:
        await engine.dispose()


async def empty_source_probe():
    """核验改 CAS 前必须保留的真实空来源分支，不模拟尚未实现的 CAS。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        await _referral_with_staff_replies(factory)
        async with factory() as session:
            await KnowledgeAdminService(session).start_referral_draft(1, employee_id=None)
        [referral] = await _referrals(factory)
        jobs = await _jobs(factory, "knowledge_referral_draft")
        assert referral.status is KnowledgeReferralStatus.NO_ANSWER
        assert referral.source_message_ids == [] and not jobs
        print("R6_CURRENT_CONTROL", referral.status.value, "draft_jobs", len(jobs))
    finally:
        await engine.dispose()


async def main():
    """依次运行三个互不写生产数据的最小证据检查。"""
    credential_probe()
    await date_probe()
    await empty_source_probe()


if __name__ == "__main__":
    asyncio.run(main())
