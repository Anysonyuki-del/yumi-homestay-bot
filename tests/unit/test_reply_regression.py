
"""部署前真实模型回归的判定逻辑：只测纯函数与替身工具，不调用模型。"""

import asyncio
import json
from datetime import UTC, date
from pathlib import Path

import pytest

from homestay_bot.tools import reply_regression as rr

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "guest_reply_scenarios.json"
BASELINE = Path(__file__).resolve().parents[1] / "fixtures" / "guest_reply_regression_baseline.json"


def _scenario(**expect) -> dict:
    """构造一个最小场景。"""
    return {
        "id": "S",
        "category": "availability",
        "expect": {"route": "tool_availability", **expect},
    }


def test_any_synonym_satisfies_a_required_item() -> None:
    """必含片段的一项可以是同义说法列表，命中其一即可；全半角与空白不影响。"""
    scenario = _scenario(must_include=[["订满", "订出"]], must_not_include=[])
    record = {"route": "model", "tools": ["search_availability"], "final": "301 明晚已经订出 了"}

    assert rr.judge(scenario, record)["ok"]


def test_a_forbidden_phrase_or_pattern_fails_the_run() -> None:
    """禁用片段、禁用正则与全局禁用片段都是一票否决。"""
    scenario = _scenario(
        must_include=[], must_not_include=["维修"], must_not_match=[r"密码[是为]\d"]
    )
    base = {"route": "model", "tools": ["search_availability"]}

    assert not rr.judge(scenario, {**base, "final": "房间在维修"})["ok"]
    assert not rr.judge(scenario, {**base, "final": "密码是1234"})["ok"]
    assert not rr.judge(scenario, {**base, "final": "参考来源"}, ["参考来源"])["ok"]
    assert rr.judge(scenario, {**base, "final": "今晚还有房"})["ok"]


def test_route_is_observed_from_the_tools_actually_called() -> None:
    """路由按实际调用的工具细分；知识问题允许普通模型路径作答；出错一律不通过。"""
    assert rr.observed_route({"route": "model", "traces": ["tourism_search"]}) == "live_search"
    price = {"route": "model", "tools": ["search_reference_price"]}
    assert rr.observed_route(price) == "tool_price"
    assert rr.observed_route({"route": "model", "knowledge_gap": True}) == "unconfirmed"
    knowledge = {
        "id": "K",
        "expect": {"route": "knowledge", "must_include": [], "must_not_include": []},
    }
    assert rr.judge(knowledge, {"route": "model", "final": "好的"})["ok"]
    assert not rr.judge(knowledge, {"route": "error", "final": ""})["ok"]


def test_a_must_pass_scenario_regresses_only_when_it_fails_twice_in_three_runs() -> None:
    """吸收采样浮动：3 次中失败至少 2 次才算退步；没跑满 3 次不下结论。"""
    assert not rr.is_regressed([True])
    assert not rr.is_regressed([False, True, True])
    assert rr.is_regressed([False, False, True])
    assert not rr.is_regressed([False, False])


def test_reruns_target_must_pass_failures_and_known_failures_that_passed() -> None:
    """首轮之后只补跑两类：不退步集合里失败的，已知未通过里通过的。"""
    baseline = {"must_pass": ["A"], "known_failures": {"B": "x"}}

    assert rr.needs_rerun("A", [False], baseline)
    assert not rr.needs_rerun("A", [True], baseline)
    assert rr.needs_rerun("B", [True], baseline)
    assert not rr.needs_rerun("B", [False], baseline)
    assert not rr.needs_rerun("A", [False, False, True], baseline)


def test_gate_fails_on_regression_and_ratchets_stable_new_passes() -> None:
    """退步即不通过；已知未通过的场景稳定通过后写进新基线；仍失败的安全类逐条列出。"""
    scenarios = {
        "A": {"category": "knowledge_direct"},
        "B": {"category": "availability"},
        "C": {"category": "emergency"},
        "D": {"category": "knowledge_direct"},
    }
    baseline = {"must_pass": ["A", "D"], "known_failures": {"B": "b", "C": "c"}, "removed": {}}

    verdict = rr.gate_verdict(
        baseline,
        {"A": [True], "D": [False, True, True], "B": [True, True, True], "C": [False]},
        scenarios,
    )
    assert verdict["passed"]
    assert verdict["newly_stable"] == ["B"]
    assert verdict["safety_known_failures"] == ["C"]
    assert verdict["proposed_baseline"]["must_pass"] == ["A", "B", "D"]
    assert verdict["proposed_baseline"]["known_failures"] == {"C": "c"}

    failed = rr.gate_verdict(
        baseline, {"A": [False, False, True], "B": [False], "C": [False]}, scenarios
    )
    # A 退步；D 没有运行结果，同样算退步，不能因为漏跑而放行。
    assert not failed["passed"]
    assert failed["regressions"] == ["A", "D"]


def test_first_baseline_keeps_only_stable_passes() -> None:
    """首次基线：3 次全过才进不退步集合；其余写明通过次数与原因，安全类单独标出。"""
    scenarios = {"A": {"category": "x"}, "B": {"category": "emergency"}}

    baseline = rr.build_baseline(
        {"A": [True, True, True], "B": [True, False, True]},
        scenarios,
        {"B": "缺 撤离"},
        {"runs": 3},
    )
    assert baseline["must_pass"] == ["A"]
    assert baseline["known_failures"]["B"] == "安全类；2/3 次通过；缺 撤离"


def test_fake_hostex_follows_the_fixture_calendar() -> None:
    """替身按资料的满房晚返回房态，经线上执行器整理后整段是否可住正确。"""
    from homestay_bot.integrations.deepseek_client import HostexReadOnlyToolExecutor

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    property_id = str(fixture["hostex"]["properties"][0]["id"])
    # 满房晚以相对测试日的天数表示：第 1 晚（9月26日）满房。
    fixture["hostex"]["full_nights"][property_id] = [1]
    today = date.fromisoformat(fixture["today"])
    executor = HostexReadOnlyToolExecutor(
        rr.FakeHostexClient(fixture["hostex"], today), local_date_provider=lambda: today
    )

    result = asyncio.run(
        executor.execute(
            "search_availability",
            {"check_in_date": "2026-09-25", "check_out_date": "2026-09-27"},
        )
    )
    first = next(item for item in result if str(item["property_id"]) == property_id)
    assert [day["available"] for day in first["days"]] == [True, False]
    assert first["stay_available"] is False


def test_the_committed_baseline_covers_every_scenario_exactly_once() -> None:
    """入库基线必须覆盖全部场景且互不重叠，否则门禁会漏判或重复判。"""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    ids = {item["id"] for item in fixture["scenarios"]}
    must_pass = set(baseline["must_pass"])
    known = set(baseline["known_failures"])

    assert must_pass | known | set(baseline.get("removed", {})) == ids
    assert not must_pass & known


def test_a_model_decision_needing_staff_counts_as_handoff() -> None:
    """模型判为需要人工、最终回复是转人工话术时记为 handoff，与线上表现一致。"""
    record = {"route": "model", "tools": [], "staff_confirmation_required": True}

    assert rr.observed_route(record) == "handoff"


def test_back_to_back_guest_messages_carry_the_emergency_state() -> None:
    """连发消息逐条回放：第一条命中燃气，第二条「我们现在该怎么办」拿到固定处置答复。"""
    from homestay_bot.domain.enums import Language

    scenario = {
        "id": "EM",
        "messages": [
            {"role": "user", "content": "厨房好像有股煤气味"},
            {"role": "user", "content": "我们现在该怎么办"},
        ],
    }

    record = rr.pre_route(scenario, [], Language.ZH)

    assert record is not None
    assert record["route"] == "emergency"
    assert "开窗通风" in record["final"]


def test_fake_hostex_client_goes_through_the_production_executor() -> None:
    """替身只替换百居易客户端，房态整理与参考价换算走线上执行器；无主渠道的价格被丢弃。"""
    from homestay_bot.integrations.deepseek_client import HostexReadOnlyToolExecutor

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    today = date.fromisoformat(fixture["today"])
    executor = HostexReadOnlyToolExecutor(
        rr.FakeHostexClient(fixture["hostex"], today), local_date_provider=lambda: today
    )

    rows = asyncio.run(
        executor.execute(
            "search_reference_price",
            {"check_in_date": "2026-09-25", "check_out_date": "2026-09-26"},
        )
    )
    titles = {row["property_title"] for row in rows}
    assert "芸栖·102 庭院双床房" in titles
    text = json.dumps(rows, ensure_ascii=False)
    assert "hx-" not in text
    assert all(row["nightly_reference_prices"] for row in rows)


def test_runner_preserves_grounded_price_evidence(monkeypatch) -> None:
    """实测出口须保留取证价格，不能退回旧改写器抹掉事实。"""
    from datetime import datetime
    from types import SimpleNamespace

    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
    from homestay_bot.services.reply_plan import ReplyEvidence, ReplyPart

    handoff_reason = None

    async def respond(self, **kwargs):
        """仅替代模型调用，仍经过实测工具的完整输出处理。"""
        return SimpleNamespace(
            facility_issue=None,
            reply_text="旧出口不应出现",
            handoff_reason=handoff_reason,
            reply_parts=[
                ReplyPart(
                    question="价格",
                    status="grounded",
                    text="201 房参考价格为 299 元，以下单为准。",
                    evidence=(
                        ReplyEvidence(
                            source_kind="reference_price",
                            source_id="201",
                            fetched_at=datetime.now(UTC),
                        ),
                    ),
                )
            ],
            knowledge_gap=False,
            staff_confirmation_required=True,
        )

    monkeypatch.setattr(DeepSeekGuestAssistant, "respond", respond)
    fixture = json.loads(FIXTURE.read_text())
    runner = rr._Runner(
        fixture,
        SimpleNamespace(
            deepseek_api_key="synthetic-only",
            deepseek_base_url="https://example.invalid",
            deepseek_model="synthetic",
        ),
    )
    result = asyncio.run(
        runner._respond(
            {
                "messages": [{"role": "user", "content": "201 明晚多少钱？"}],
            }
        )
    )
    assert "299" in result["final"]
    assert rr.observed_route(result) == "model"
    assert "旧出口" not in result["final"]
    handoff_reason = "emergency:fire"
    result = asyncio.run(
        runner._respond(
            {
                "messages": [{"role": "user", "content": "201 明晚多少钱？"}],
            }
        )
    )
    assert result["route"] == "emergency" and "119" in result["final"]


def test_negative_availability_is_not_a_positive_fixture_match() -> None:
    """满房禁用规则不能把“不可订”匹配为“可订”。"""
    fixture = json.loads(FIXTURE.read_text())
    scenario = next(s for s in fixture["scenarios"] if s["id"] == "AV-国庆")
    record = {
        "route": "model",
        "tools": ["search_availability"],
        "final": "芸栖·101 庭院大床房（2026-10-01 — 2026-10-03）：不可订。",
    }
    assert rr.judge(scenario, record)["ok"]
    assert not rr.judge(scenario, {**record, "final": "101房可订。"})["ok"]


def test_mixed_weather_and_availability_keeps_inventory_route() -> None:
    """复合问题查询天气后继续查询房态，不应被视为漏答房态。"""
    assert (
        rr.observed_route(
            {"route": "model", "tools": ["search_availability"], "traces": ["tourism_search"]}
        )
        == "tool_availability"
    )


def test_scoped_gate_only_judges_scenarios_it_ran() -> None:
    """按范围运行：范围外的不退步场景没跑不算退步、已知未通过不会被纳入；范围内的
    退步照样拦下。写错的场景编号或类别直接报错，不能静默少跑（2026-09-30）。"""
    scenarios = {
        "W-天气": {"category": "live_search"},
        "K-退房": {"category": "knowledge_direct"},
        "K-早餐": {"category": "knowledge_direct"},
    }
    baseline = {"must_pass": ["W-天气", "K-退房"], "known_failures": {"K-早餐": "x"}}
    scope = rr.resolve_scope("live_search", scenarios)

    passed = rr.gate_verdict(baseline, {"W-天气": [True]}, scenarios, scope)
    failed = rr.gate_verdict(baseline, {"W-天气": [False, False, True]}, scenarios, scope)

    assert scope == {"W-天气"}
    assert passed["passed"] and passed["newly_stable"] == []
    assert not failed["passed"] and failed["regressions"] == ["W-天气"]
    assert rr.resolve_scope("", scenarios) is None
    with pytest.raises(ValueError):
        rr.resolve_scope("W-不存在", scenarios)


def test_expected_knowledge_gap_accepts_the_unconfirmed_route() -> None:
    """场景本身期望知识缺口时，回「尚未确认」的 unconfirmed 路径就是正确结果。"""
    gap = {
        "id": "U",
        "expect": {
            "route": "knowledge",
            "knowledge_gap": True,
            "must_include": [["尚未确认"]],
            "must_not_include": [],
        },
    }
    record = {"route": "model", "knowledge_gap": True, "final": "当前审核资料尚未确认这一信息。"}
    assert rr.judge(gap, record)["ok"]
    # 没有声明知识缺口的知识场景，回未确认仍是失败。
    plain = {"id": "K", "expect": {**gap["expect"], "knowledge_gap": False}}
    assert not rr.judge(plain, record)["ok"]


def test_an_empty_final_reply_never_passes() -> None:
    """空正文不是任何场景的合格回复，即使路由和禁用片段都符合。"""
    facility = {
        "id": "F",
        "expect": {"route": "facility", "must_include": [], "must_not_include": ["已提交"]},
    }
    assert not rr.judge(facility, {"route": "facility", "final": ""})["ok"]
    assert not rr.judge(facility, {"route": "facility", "final": "   "})["ok"]


def _synthetic_runner():
    """构造不连接任何外部服务的门禁运行器，供替换 respond 的用例使用。"""
    from types import SimpleNamespace

    fixture = json.loads(FIXTURE.read_text())
    return rr._Runner(
        fixture,
        SimpleNamespace(
            deepseek_api_key="synthetic-only",
            deepseek_base_url="https://example.invalid",
            deepseek_model="synthetic",
        ),
    )


def test_runner_facility_reply_does_not_claim_submission(monkeypatch) -> None:
    """门禁不登记任务，设施收尾不能用默认的「已提交」冒充动作已完成。"""
    from types import SimpleNamespace

    from homestay_bot.integrations.deepseek_client import (
        DeepSeekGuestAssistant,
        FacilityIssue,
    )

    async def respond(self, **kwargs):
        """仅替代模型调用，返回一条民宿设施故障决定。"""
        return SimpleNamespace(
            facility_issue=FacilityIssue(scope="homestay_facility"),
            facility_advice=["用遥控器确认模式为制冷"],
            reply_text="",
            handoff_reason=None,
            reply_parts=[],
            knowledge_gap=False,
            staff_confirmation_required=False,
        )

    monkeypatch.setattr(DeepSeekGuestAssistant, "respond", respond)
    result = asyncio.run(
        _synthetic_runner()._respond({"messages": [{"role": "user", "content": "空调不制冷了"}]})
    )
    assert result["route"] == "facility"
    assert "已提交" not in result["final"]
    assert "制冷" in result["final"]


def test_runner_passes_the_scenario_confirmed_stay(monkeypatch) -> None:
    """场景声明已确认住宿时，运行器按线上入口把房间与日期交给回复链路。"""
    from types import SimpleNamespace

    from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant

    seen: list[object] = []

    async def respond(self, **kwargs):
        """记录传入的客户上下文，返回最小的普通回复。"""
        seen.append(kwargs.get("customer_context"))
        return SimpleNamespace(
            facility_issue=None,
            reply_text="好的",
            handoff_reason=None,
            reply_parts=[],
            knowledge_gap=False,
            staff_confirmation_required=False,
        )

    monkeypatch.setattr(DeepSeekGuestAssistant, "respond", respond)
    stay = {"property_id": 202, "check_in_date": "2026-10-03", "check_out_date": "2026-10-05"}
    runner = _synthetic_runner()
    asyncio.run(
        runner._respond(
            {
                "messages": [{"role": "user", "content": "早餐几点开始？"}],
                "expect": {"route": "knowledge", "context": {"confirmed_stay": stay}},
            }
        )
    )
    asyncio.run(
        runner._respond(
            {"messages": [{"role": "user", "content": "早餐几点开始？"}], "expect": {}}
        )
    )
    assert seen[0].confirmed_stay == stay
    assert seen[1] is None
