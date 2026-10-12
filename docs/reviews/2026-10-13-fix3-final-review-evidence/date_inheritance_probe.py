"""离线核对日期继承与最终转交；只创建合成内存库，退出零表示反例成立。"""
import asyncio
import json
from datetime import date
from types import SimpleNamespace

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import Base, KnowledgeEntry
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.services.sensitive_text import contains_sensitive_text, redact_credentials
from tests.integration.test_knowledge_referrals import (
    _Assistant,
    _jobs,
    _referrals,
    _run_final,
    _seed,
)
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _availability_row,
    _decision,
    _respond,
    _tool_message,
    _Tools,
)


async def probe(day: str, fallback: bool) -> dict:
    """每例独立数据库；真实知识检索、助手和最终事务出站，网络仅用固定响应替代。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
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
            text = f"10月10日入住10月12日退房有房吗，{day}早餐几点开始"
            args = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"}
            plan = plan_for(text, ("stay_query", text, args))
            assert plan.status == "ok"
            tool_message = _tool_message("search_availability", args)
            assistant, completions = _assistant([], knowledge, _Tools({
                "search_availability": [_availability_row(1401, *args.values())],
            }))
            offered = []

            async def choose(**kwargs):
                """从实际信封选早餐缺口，检验错日期是否连带生成转交候选。"""
                completions.requests.append(kwargs)
                if len(completions.requests) == 1:
                    envelope = json.loads(kwargs["messages"][-1]["content"])
                    offered.extend(u for u in envelope["gap_units"] if "早餐" in u["text"])
                    message = tool_message
                else:
                    selections = [{
                        "unit_id": u["id"], "canonical_question": "早餐几点开始",
                        "category": "早餐",
                    } for u in offered]
                    message = SimpleNamespace(
                        content="invalid" if fallback else _decision(gap_referrals=selections),
                        tool_calls=None,
                    )
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

            completions.create = choose
            decision = await _respond(assistant, text, plan)
            _, message = await _seed(factory, content=text)
            await _run_final(factory, _Assistant(decision), message)
            [job] = await _jobs(factory, "wecom_send_text")
            records = await _referrals(factory)
            body = job.payload["content"]
            wrong_day = "7:30" in body
            assert wrong_day == (day != "10月11日")
            assert len(records) == (0 if fallback else 1)
            assert all(g.reason == "knowledge_missing" for g in decision.gap_units)
            return {
                "day": day, "fallback": fallback, "final": body,
                "gaps": [(g.text, g.reason) for g in decision.gap_units],
                "offered": offered, "referrals": len(records),
                "knowledge_dates": [
                    (str(e.target_date), str(e.target_end_date))
                    for part in decision.reply_parts for e in part.evidence
                    if e.source_kind == "knowledge"
                ],
            }
    finally:
        await engine.dispose()


async def main() -> None:
    """对照绝对日期、日历相对日期、住宿序数日，并覆盖正常与协议回退出口。"""
    for fallback in (False, True):
        for day in ("10月11日", "明天", "第二天"):
            print(json.dumps(await probe(day, fallback), ensure_ascii=False))
    for text in ("password is 'needed'", "password is 'needed.'", "密码 OK 吗"):
        print(json.dumps({"input": text, "redacted": redact_credentials(text),
                          "sensitive": contains_sensitive_text(text)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
