"""规划误判诊断（规划误判 Spec F2）：只调用 plan_turn，统计状态、原因与各事项类型和风险。

用法：在 API 容器的临时目录内、候选源码在 PYTHONPATH、当前目录有门禁夹具 fixture.json 时，
python planner_complaint_diag.py。虚构文本，不写库、不发消息、不调用主回复。
"""

import asyncio
import collections
import json
from datetime import date
from pathlib import Path

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.services.answer_policy import agitated_cleared_by_plan
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.tools import reply_regression as rr

CASES = [
    ("卫生间下水堵了！！！", 20),
    ("卫生间下水堵了", 5),
    ("空调坏了！！快来人", 5),
    ("房间停电了！！！", 5),
]


async def main() -> None:
    """每句并发规划 N 次，按（状态, 原因, 事项, 是否放行）分组计数。"""
    fixture = json.loads(Path("fixture.json").read_text(encoding="utf-8"))
    runner = rr._Runner(fixture, await rr._load_runtime_snapshot())
    assistant = DeepSeekGuestAssistant(
        chat_client=runner._chat,
        tourism_searcher=DeepSeekTourismSearcher(
            client=runner._anthropic,
            model=runner._snapshot.deepseek_model,
            status_setter=lambda _status: None,
        ),
        knowledge=KnowledgeService(
            rr._MemoryKnowledge(runner._entries, fixture.get("property_profiles"))
        ),
        model=runner._snapshot.deepseek_model,
        safety_hmac_key=b"plan-diag",
        tool_executor=None,
        local_date_provider=lambda: date.fromisoformat(fixture["today"]),
        plan_turns=True,
    )
    for text, n in CASES:
        outcomes = await asyncio.gather(
            *[assistant.plan_turn(text=text, language=Language.ZH) for _ in range(n)]
        )
        tally: collections.Counter[tuple[str, str, str, bool]] = collections.Counter()
        for outcome in outcomes:
            summary = rr.plan_summary(outcome, text) or {"status": "", "reason": "", "items": []}
            items = ",".join(
                f"{i['kind']}/{i['risk']}/{'v' if i['valid'] else 'x'}" for i in summary["items"]
            )
            cleared = agitated_cleared_by_plan(outcome, text)
            tally[(summary["status"], summary["reason"], items, cleared)] += 1
        print("CASE", text, n)
        for (status, reason, items, cleared), count in tally.most_common():
            print("  ", count, f"status={status} reason={reason} items=[{items}] cleared={cleared}")


asyncio.run(main())
