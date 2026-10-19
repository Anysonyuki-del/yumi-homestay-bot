"""同一时段 A/B（规划误判 Spec §9）：统计各句的规划结果分布，只调用规划，虚构文本。

用法：在 API 容器的临时目录内、候选源码在 PYTHONPATH、当前目录有 fixture.json 时，
python planner_ab.py <标签>。HEAD 与候选紧挨着各跑一次，结果见 ab_same_window_results.txt。
"""

import asyncio
import collections
import json
import sys
from datetime import date
from pathlib import Path

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.tools import reply_regression as rr

CASES = [
    ("卫生间下水堵了！！！", 30),
    ("房间停电了！！！", 20),
    ("下水又堵了！！！你们怎么搞的", 20),
    ("The AC is broken AGAIN!!! This is unacceptable", 20),
    ("插座一直滋滋响还发烫！！！太离谱了", 20),
]


async def main() -> None:
    """每句并发规划 N 次，打印（失败原因 或 各事项类型/风险）的计数。"""
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
        safety_hmac_key=b"plan-ab",
        tool_executor=None,
        local_date_provider=lambda: date.fromisoformat(fixture["today"]),
        plan_turns=True,
    )
    for text, n in CASES:
        outcomes = await asyncio.gather(
            *[assistant.plan_turn(text=text, language=Language.ZH) for _ in range(n)]
        )
        tally = collections.Counter(
            f"failed:{o.reason}" if o.status != "ok" or o.plan is None
            else ",".join(f"{i.kind}/{i.risk}" for i in o.plan.items)
            for o in outcomes
        )
        print("AB", sys.argv[1], text, n, json.dumps(dict(tally), ensure_ascii=False))


asyncio.run(main())
