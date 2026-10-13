"""复现周末实施中的限定词截断：退出0表示缺陷成立，不表示修复通过。"""

import asyncio
import json
from datetime import date

from homestay_bot.services.item_coverage import relative_calendar_weekend, static_clause_dates
from tests.unit.test_reply_regression import _weekend_fixture_reply


async def main() -> None:
    """先核解析边界，再用真实门禁知识检索和助手取证复现错日最终正文；模型为合成响应。"""
    today = date(2026, 9, 25)
    current = (date(2026, 9, 26), date(2026, 9, 27))
    following = (date(2026, 10, 3), date(2026, 10, 4))
    assert relative_calendar_weekend("下周末早餐几点", today) == following
    for prefix in ["下周周末", "下周的周末", "上一个周末", "下个月周末"]:
        question = prefix + "早餐几点开始？"
        parsed = relative_calendar_weekend(question, today)
        assert parsed == current, (question, parsed)
        assert static_clause_dates(question, current, 2026, today=today) == current
        print(json.dumps({"case": "unsupported_qualifier", "question": question,
                          "wrong_dates": [str(d) for d in parsed]}, ensure_ascii=False))
    for prefix in ["下周末", "下周的周末", "上一个周末"]:
        question = "9999房间" + prefix + "早餐几点开始？"
        decision, tools = await _weekend_fixture_reply(question, [9901])
        facts = [
            (e.source_id, str(e.target_date), str(e.target_end_date))
            for part in decision.reply_parts for e in part.evidence
        ]
        assert tools.calls == []
        if prefix == "下周末":
            assert not facts and "8:30" not in decision.reply_text
        else:
            assert facts == [("9901", "2026-09-26", "2026-09-26"),
                             ("9902", "2026-09-27", "2026-09-27")]
            assert not decision.gap_units
            assert "2026-09-26：早餐8:30开始" in decision.reply_text
        print(json.dumps({"case": "assistant_final", "question": question,
                          "final": decision.reply_text, "sources": facts,
                          "gap_reasons": [g.reason for g in decision.gap_units]},
                         ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
