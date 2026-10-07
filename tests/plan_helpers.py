"""测试用轮次计划构造：经过与生产相同的本地核验，不手写跳过核验的计划对象。"""

import json
from datetime import date
from typing import Any

from homestay_bot.services.turn_plan import PlanOutcome, verify_turn_plan

PLAN_TODAY = date(2026, 10, 7)


def plan_for(text: str, *items: tuple[str, str] | tuple[str, str, dict[str, Any]]) -> PlanOutcome:
    """按（类型, 摘录[, 其他字段]）构造计划；摘录位置由核验按原文唯一出现处确定。"""
    raw = []
    for index, item in enumerate(items, start=1):
        kind, quote = item[0], item[1]
        extra = item[2] if len(item) > 2 else {}  # type: ignore[misc]
        raw.append(
            {
                "id": index,
                "kind": kind,
                "subject": extra.get("subject", quote[:6]),
                "question": extra.get("question", quote),
                "quote": quote,
                "start": extra.get("start", text.find(quote)),
                **{key: value for key, value in extra.items() if key not in {"start"}},
            }
        )
    outcome = verify_turn_plan(
        json.dumps({"items": raw}, ensure_ascii=False), text, today_provider=lambda: PLAN_TODAY
    )
    return outcome
