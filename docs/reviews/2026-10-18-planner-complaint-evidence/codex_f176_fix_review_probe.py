"""复审危险与客诉混合分项：退出零表示现有顺序缺口复现，非修复通过。"""

import asyncio

from homestay_bot.application import _deferred_message_from_payload
from homestay_bot.services.answer_policy import agitated_cleared_by_plan
from tests.plan_helpers import plan_for
from tests.unit.test_conversation_plan_flows import _deferred
from tests.unit.test_conversation_service import AssistantStub


async def main() -> None:
    """用真实会话编排加合成计划，对照单危险项与危险加客诉两种合法分项。"""
    text = "插座一直滋滋响还发烫！！！太离谱了"
    for risk, marker in [("possible_hazard", "避开可能有危险"),
                         ("current_hazard:electric", "不要触碰")]:
        for split in (False, True):
            def make_plan(t, risk=risk, split=split):
                """危险故障单独成项；混合控制另保留客人不满，不改变故障项。"""
                items = [("facility_fault", "插座一直滋滋响还发烫", {"risk": risk})]
                if split:
                    items.append(("unclear", "太离谱了", {"risk": "complaint"}))
                return plan_for(t, *items)

            plan = make_plan(text)
            assert plan.ok and all(item.valid for item in plan.plan.items)
            service, _, wecom, jobs, source, _ = _deferred(
                text, AssistantStub(plan=make_plan)
            )
            await service.process_debounced_message(source)
            queued = len(jobs.jobs)
            for _, payload, key, _ in list(jobs.jobs):
                if key == f"final:{source.msgid}":
                    await service.process_recorded_message(_deferred_message_from_payload(payload))
            warned = any(marker in reply for reply in wecom.guest_messages)
            assert warned is (not split)
            assert agitated_cleared_by_plan(plan, text) is (not split)
            if split:
                assert queued == 0 and len(wecom.guest_messages) == 1
                assert "您的情况我已记录" in wecom.guest_messages[0]
            print(risk, "split=", split, "safety_sent=", warned, "queued=", queued)


if __name__ == "__main__":
    asyncio.run(main())
