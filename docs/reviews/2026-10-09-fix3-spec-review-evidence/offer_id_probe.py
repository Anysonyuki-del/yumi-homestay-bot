"""核验 v2 小句缺口与现有模型选择编号的衔接；退出零表示契约遗漏成立。"""

from types import SimpleNamespace

from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    DeepSeekGuestAssistant,
    GapReferral,
)
from homestay_bot.services.item_coverage import (
    GapUnit,
    clause_needs,
    split_clauses,
    unit_id_for,
)
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import _decision


def main():
    """真实编号生成和选择过滤配拟定小句缺口；不模拟完整 v2 实现或外部请求。"""
    text = "10月10日健身房几点开门，10月11日早餐几点开始"
    plan = plan_for(text, ("static_fact", text, {
        "check_in_date": "2026-10-10", "check_out_date": "2026-10-11",
    }))
    assert plan.status == "ok"
    item = plan.plan.valid_items[0]
    clauses = split_clauses(text)
    entry = SimpleNamespace(
        item=item, clauses=clauses, needs=[clause_needs(c.text) for c in clauses],
    )
    [offer] = DeepSeekGuestAssistant._gap_unit_offers({item.id: entry})
    first = clauses[0]
    clause_id = unit_id_for(item.id, first.start, first.end)
    assert offer["id"] != clause_id
    # v2 把未绑定的第二句排除后，第一句若确实缺资料，会得到自己的原文区间编号。
    unit = GapUnit(
        unit_id=clause_id, item_id=item.id, text=first.text,
        reason="knowledge_missing", property_id=None,
    )
    choice = GapReferral(
        unit_id=offer["id"], canonical_question="健身房几点开门？", category="设施",
    )
    decision = AssistantDecision.model_validate_json(_decision()).model_copy(
        update={"gap_units": [unit], "gap_referrals": [choice]},
    )
    assert DeepSeekGuestAssistant._referral_candidates(decision) == []
    control = decision.model_copy(update={
        "gap_referrals": [choice.model_copy(update={"unit_id": clause_id})],
    })
    assert len(DeepSeekGuestAssistant._referral_candidates(control)) == 1
    print({
        "offered_whole_item_id": offer["id"], "proposed_clause_gap_id": clause_id,
        "accepted_with_offered_id": 0, "accepted_with_matching_control": 1,
    })
    print("Spec v2 clause-gap contract proxy; not an implemented v2 or real model test")


if __name__ == "__main__":
    main()
