"""周末日期 Spec v1 的离线反例；退出0表示缺口成立，不表示新方案已实施。"""

import asyncio
import json
from dataclasses import replace
from datetime import date, timedelta

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    EvidenceSelection,
    GapReferral,
    weekend_stay_options,
)
from homestay_bot.services.knowledge_evidence_policy import verify_selected_evidence
from homestay_bot.tools.reply_regression import gate_verdict
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant,
    _decision,
    _Knowledge,
    _respond,
    _snippet,
)


async def main() -> None:
    """用合成计划和条目调用现有实现，分开证明当前误拦与拟扩日期范围的牵连。"""
    plain = _snippet(1, "早餐几点开始？", "早餐7:30开始。", category="早餐")
    replies = {}
    for text in ["早餐几点开始", "周末早餐几点开始"]:
        assistant, _ = _assistant([
            _decision(evidence_selection=[{"item_id": 1, "answer_ids": [1]}]),
        ], _Knowledge([plain]))
        result = await _respond(assistant, text, plan_for(text, ("static_fact", text, {})))
        replies[text] = result.reply_text
    assert "7:30" in replies["早餐几点开始"]
    assert "7:30" not in replies["周末早餐几点开始"]
    print("E0", json.dumps(replies, ensure_ascii=False))

    today = date(2026, 10, 11)
    # 逐字按Spec的“周日只取今天，再加7天”计算，未实现拟议的新解析器。
    spec_next = (today + timedelta(days=7), today + timedelta(days=7))
    stays = weekend_stay_options("下周末有房吗", today)
    assert spec_next == (date(2026, 10, 18), date(2026, 10, 18))
    assert any(start == date(2026, 10, 17) for start, _ in stays)
    print("E1", spec_next, "existing_stays", stays)

    assistant, _ = _assistant([_decision()])
    text = "周末早餐几点开始"
    plan = plan_for(text, ("static_fact", text, {}))
    sat, sun = date(2026, 10, 10), date(2026, 10, 11)
    assert plan.plan is not None
    layout = await assistant._fact_layout(plan.plan.items, text, None, (sat, sun),
                                          date(2026, 10, 7))
    # 仅投影Spec承诺的已绑定布局；后续取证、原因汇总和候选筛选均为真实现有函数。
    layout[1] = replace(layout[1], item_dates=(sat, sun), static_dates=((sat, sun),),
                        split_static=False)
    entries = [
        _snippet(1, "早餐几点开始？", "早餐7:30开始。", category="早餐",
                 valid_from=sat, valid_until=sat),
        _snippet(2, "早餐几点开始？", "早餐9:30开始。", category="早餐",
                 valid_from=sun, valid_until=sun),
        _snippet(3, "早餐几点开始？", "早餐10:30开始。", category="早餐",
                 valid_from=sun, valid_until=sun),
    ]
    evidence, gaps = assistant._item_evidence_plan(
        [EvidenceSelection(item_id=1, answer_ids=[1])], layout, {1: entries}, Language.ZH,
    )
    assert evidence is not None and evidence.status == "insufficient"
    assert len(gaps) == 1 and gaps[0].reason == "knowledge_missing"
    decision = AssistantDecision(reply_text="", language=Language.ZH, intent="faq",
                                 confidence=0.9, gap_units=gaps, gap_referrals=[
        GapReferral(unit_id=gaps[0].unit_id, canonical_question="周末早餐几点开始",
                    category="早餐"),
    ])
    candidates = assistant._referral_candidates(decision)
    assert len(candidates) == 1
    print("E2", [(p.status, p.text) for p in evidence.parts],
          "gap", gaps[0].reason, "accepted_candidates", len(candidates))

    for question in ["早餐几点开始？", "周六早餐几点开始？"]:
        special = _snippet(2, question, "早餐9:30开始。", category="早餐",
                           valid_from=sat, valid_until=sat)
        verdict = verify_selected_evidence(text, [1], [], [plain, special],
                                           target_date=sat, target_end_date=sun)
        parts, reason = assistant._static_item_parts(
            text, [plain, special], (sat, sun), EvidenceSelection(item_id=1, answer_ids=[1]),
            Language.ZH,
        )
        if question == plain.question:
            assert not reason and all(p.status == "grounded" for p in parts)
            assert any("9:30" in p.text for p in parts)
            assert any("7:30" in p.text for p in parts)
        else:
            assert reason == "period_veto" and all(p.status == "missing" for p in parts)
        print("E3", question, verdict.status, verdict.reason, "final_reason", reason,
              [(p.status, p.text) for p in parts])

    verdict = gate_verdict(
        {"must_pass": ["OLD"], "known_failures": {}},
        {"OLD": [True], "NEW_WEEKEND": [False, False, False]},
        {"OLD": {"category": "knowledge"}, "NEW_WEEKEND": {"category": "knowledge"}},
    )
    assert verdict["passed"]
    assert "NEW_WEEKEND" not in verdict["proposed_baseline"]["must_pass"]
    assert "NEW_WEEKEND" not in verdict["proposed_baseline"]["known_failures"]
    print("E4", "new_case_failed_three_times", "gate_passed", verdict["passed"],
          "not_added_to_proposed_baseline")


if __name__ == "__main__":
    asyncio.run(main())
