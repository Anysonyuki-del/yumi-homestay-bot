"""规划误判复测（规划误判 Spec §7.4、§6.4）：逐次输出 JSON 行，不存密钥或配置快照。

用法：在 API 容器的临时目录内、候选源码在 PYTHONPATH、当前目录有门禁夹具 fixture.json 时，
python planner_retest.py > results.jsonl。只调用规划，虚构文本，不写库、不发消息。
"""

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.services.answer_policy import agitated_cleared_by_plan
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.services.turn_plan import PLANNER_PROMPT_ZH
from homestay_bot.tools import reply_regression as rr

# （组, 编号, 原文, 次数）。组决定逐次断言，见 judge。
SAMPLES = [
    ("repair", "R1", "卫生间下水堵了！！！", 20),
    ("repair", "R2", "空调坏了！！！快来人", 10),
    ("repair", "R3", "房间停电了！！！", 10),
    ("repair", "R4", "热水器不出热水了！！！", 10),
    ("repair", "R5", "门锁打不开！！！急", 10),
    ("complaint", "C1", "下水又堵了！！！你们怎么搞的", 5),
    ("complaint", "C2", "说了三次热水还是没有！！！", 5),
    ("complaint", "C3", "空调坏了！！！住得太糟心了", 5),
    ("complaint", "C4", "毛巾送两条，空调又坏了！！！什么破房间", 5),
    ("complaint", "C5", "The AC is broken AGAIN!!! This is unacceptable", 5),
    ("negative", "N1", "下水堵了！！！别误会，就是着急", 5),
    ("hazard", "H1", "插座一直滋滋响还发烫！！！太离谱了", 10),
]


def judge(group: str, summary: dict, cleared: bool) -> bool:
    """逐次判定：规划须成功、对得上正文且事项都有效，再按组检查类型与风险。"""
    items = summary["items"]
    if summary["status"] != "ok" or not summary["matches"] or not items:
        return False
    if not all(item["valid"] for item in items):
        return False
    risks = [item["risk"] for item in items]
    faults = [item for item in items if item["kind"] == "facility_fault"]
    if group in {"repair", "negative"}:
        return bool(faults) and all(r == "none" for r in risks) and cleared
    if group == "complaint":
        return "complaint" in risks and not cleared
    # 危险对照：故障项保留危险，不能只剩 complaint。
    return any(item["risk"] in {"possible_hazard", "current_hazard:electric"} for item in faults)


async def main() -> None:
    """按样本表并发规划，逐次打印一行 JSON。"""
    fixture_text = Path("fixture.json").read_text(encoding="utf-8")
    fixture = json.loads(fixture_text)
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
        safety_hmac_key=b"plan-retest",
        tool_executor=None,
        local_date_provider=lambda: date.fromisoformat(fixture["today"]),
        plan_turns=True,
    )
    meta = {
        "fixture_sha256": hashlib.sha256(fixture_text.encode()).hexdigest()[:16],
        "prompt_sha256": hashlib.sha256(PLANNER_PROMPT_ZH.encode()).hexdigest()[:16],
        "model": runner._snapshot.deepseek_model,
        "date": date.today().isoformat(),
    }
    for group, sid, text, n in SAMPLES:
        outcomes = await asyncio.gather(
            *[assistant.plan_turn(text=text, language=Language.ZH) for _ in range(n)]
        )
        for index, outcome in enumerate(outcomes, start=1):
            summary = rr.plan_summary(outcome, text) or {
                "status": "", "reason": "", "matches": False, "items": []
            }
            cleared = agitated_cleared_by_plan(outcome, text)
            print(json.dumps({
                **meta, "group": group, "id": sid, "round": index, "plan": summary,
                "cleared": cleared, "ok": judge(group, summary, cleared),
            }, ensure_ascii=False))


asyncio.run(main())
