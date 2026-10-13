"""周末 Spec v3 的离线复审；退出0证明反例及控制成立，不证明新方案通过。"""

import asyncio
import json
from datetime import date
from pathlib import Path

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import EvidenceSelection
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.tools.reply_regression import _Entry, _MemoryKnowledge, is_stable_pass, judge
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _respond,
    _snippet,
    _Tools,
)


async def main() -> None:
    """用真实取证、判定和房源映射配合合成模型，区分实现证据与Spec格式推演。"""
    assistant, _ = _assistant([_decision()])
    base = _snippet(1, "What time is breakfast?", "Breakfast starts at 7:30.", category="早餐")
    special = _snippet(
        2, "What time is breakfast?", "Breakfast starts at 8:30.", category="早餐",
        valid_from=date(2027, 1, 1), valid_until=date(2027, 1, 1),
    )
    parts, reason = assistant._static_item_parts(
        "What time is breakfast?", [base, special], (date(2026, 12, 31), date(2027, 1, 1)),
        EvidenceSelection(item_id=1, answer_ids=[1]), Language.EN,
    )
    assert not reason and len(parts) == 2
    assert "2026-12-31" in parts[0].text and "2027-01-01" in parts[1].text
    print("V3-E1", [part.text for part in parts])

    correct = "9月26日（周六）：早餐8:30开始。\n9月27日（周日）：早餐9:00开始。"
    swapped = "9月26日（周六）：早餐9:00开始。\n9月27日（周日）：早餐8:30开始。"
    contaminated = correct + "\n周末也可以按平时7:30开始。"
    scenario = {"id": "K-周末分日", "expect": {
        "route": "knowledge", "must_include": ["9月26日", "8:30", "9月27日", "9:00"],
        "source_ids": [9901, 9902],
    }}
    # source_ids是资料标注，当前judge不核对；日期与时刻分开匹配也抓不到互换。
    for text in [correct, swapped]:
        assert judge(scenario, {"route": "knowledge", "final": text, "source_ids": [9011]}, [])[
            "ok"
        ]
    scenario["expect"]["must_include"] = [
        "9月26日（周六）：早餐8:30开始", "9月27日（周日）：早餐9:00开始",
    ]
    assert judge(scenario, {"route": "knowledge", "final": correct}, [])["ok"]
    assert not judge(scenario, {"route": "knowledge", "final": swapped}, [])["ok"]
    assert judge(scenario, {"route": "knowledge", "final": contaminated}, [])["ok"]
    scenario["expect"]["must_not_include"] = ["7:30"]
    assert not judge(scenario, {"route": "knowledge", "final": contaminated}, [])["ok"]
    assert judge(scenario, {"route": "knowledge", "final": correct}, [])["ok"]
    assert not is_stable_pass([True, True])
    assert not is_stable_pass([True, True, False])
    assert is_stable_pass([True, True, True])
    print("V3-E2", "分开匹配漏互换；组合匹配加场景负例可拦；来源需另行断言")

    fixture = json.loads(Path("tests/fixtures/guest_reply_scenarios.json").read_text())
    entries = [
        _Entry(**{key: entry[key] for key in _Entry.__dataclass_fields__ if key in entry})
        for entry in fixture["knowledge"]
    ]
    entries += [
        _Entry(
            id=9900 + offset, category="早餐", question_zh="早餐几点开始？",
            answer_zh=f"早餐{time}开始。", question_en="What time is breakfast?",
            answer_en=f"Breakfast starts at {time}.", keywords=["早餐"], scope="property",
            property_id=9999, valid_from=date(2026, 10, day), valid_until=date(2026, 10, day),
        )
        for offset, day, time in [(1, 10, "8:30"), (2, 11, "9:00")]
    ]
    tools = _Tools({})
    assistant, _ = _assistant(
        [_decision(evidence_selection=[{"item_id": 1, "answer_ids": [9901]}])],
        KnowledgeService(_MemoryKnowledge(entries)), tools,
    )
    # 新周末解析器尚未实现；用已支持的绝对日期只核房源映射、候选、来源和工具边界。
    text = "9999房间10月10日到12日早餐几点开始？"
    plan = plan_for(text, ("static_fact", text, {
        "target_room": "9999", "check_in_date": "2026-10-10",
        "check_out_date": "2026-10-12", "question": "9999房间早餐几点开始？",
    }))
    assert plan.status == "ok"
    result = await _respond(assistant, text, plan)
    sources = {e.source_id for part in result.reply_parts for e in part.evidence}
    assert {"9901", "9902"} <= sources and tools.calls == []
    assert "8:30" in result.reply_text and "9:00" in result.reply_text
    print("V3-E3", result.reply_text, "sources", sorted(sources), "tool_calls", tools.calls)


if __name__ == "__main__":
    asyncio.run(main())
