"""F176 离线审查证据：断言当前观测缺口；退出零表示反例成立，不表示修复通过。"""

import asyncio
import json
from datetime import date
from unittest.mock import patch

from homestay_bot.integrations.deepseek_client import (
    AssistantUnavailableError,
    DeepSeekGuestAssistant,
)
from homestay_bot.services.answer_policy import resolve_handoff_reason
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.turn_plan import verify_turn_plan
from homestay_bot.tools import reply_regression as rr
from tests.plan_helpers import plan_for
from tests.unit.test_reply_regression import _synthetic_runner


async def main() -> None:
    """只用合成输入与模型替身验证异常摘要、自由文本字段和验收分流。"""
    text = "早餐几点"
    outcome = plan_for(text, ("static_fact", text, {}))

    async def fail(*args, **kwargs):
        """主回复失败时沿生产异常契约携带已完成计划。"""
        raise AssistantUnavailableError("synthetic-only", plan_outcome=outcome)

    with patch.object(DeepSeekGuestAssistant, "respond", fail):
        record = await _synthetic_runner().run({
            "id": "diag", "messages": [{"role": "user", "content": text}],
        })
    assert record["route"] == "error" and "plan" not in record
    print("F176-R1: completed plan lost on AssistantUnavailableError")

    secret = "SYNTHETIC_SECRET_123"
    text = "我的密码是 " + secret
    raw = json.dumps({"items": [{"id": 1, "kind": "unclear", "quote": text,
                                "start": 0, "subject": text, "risk": "none"}]})
    outcome = verify_turn_plan(raw, text, today_provider=lambda: date(2026, 10, 18))
    assert outcome.ok and secret in json.dumps(rr.plan_summary(outcome, text))
    print("F176-R2: free-text subject retains synthetic secret")

    cases = [
        ("下水又堵了！！！你们怎么搞的", "agitated", None),
        ("房间太脏了！！！我要投诉", "complaint", "complaint"),
        ("热水一直没有，太差了！！", None, None),
    ]
    for text, local_reason, handoff in cases:
        calm = plan_for(text, ("facility_fault", text, {"risk": "none"}))
        angry = plan_for(text, ("facility_fault", text, {"risk": "complaint"}))
        assert ComplaintService.classify(text).reason == local_reason
        assert resolve_handoff_reason(text, calm) == handoff
        if local_reason == "agitated":
            assert resolve_handoff_reason(text, angry) == "agitated"
        elif local_reason is None:
            assert resolve_handoff_reason(text, angry) is None
    print("F176-R3: hard, soft and untriggered complaint controls have different contracts")


if __name__ == "__main__":
    asyncio.run(main())
