"""部署前真实模型回归的判定逻辑：只测纯函数与替身工具，不调用模型。"""

import asyncio
import json
from datetime import date
from pathlib import Path

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
    """替身房态按资料的满房晚计算整段可住，与生产执行器归一化后的结构一致。"""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    property_id = str(fixture["hostex"]["properties"][0]["id"])
    # 满房晚以相对测试日的天数表示：第 1 晚（9月26日）满房。
    fixture["hostex"]["full_nights"][property_id] = [1]
    tools = rr.FakeHostexTools(fixture["hostex"], date.fromisoformat(fixture["today"]))

    result = asyncio.run(
        tools.execute(
            "search_availability",
            {"check_in_date": "2026-09-25", "check_out_date": "2026-09-27"},
        )
    )
    first = next(item for item in result if str(item["property_id"]) == property_id)
    assert [day["available"] for day in first["days"]] == [True, False]
    assert first["stay_available"] is False
    assert tools.calls == ["search_availability"]


def test_the_committed_baseline_covers_every_scenario_exactly_once() -> None:
    """入库基线必须覆盖全部场景且互不重叠，否则门禁会漏判或重复判。"""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    ids = {item["id"] for item in fixture["scenarios"]}
    must_pass = set(baseline["must_pass"])
    known = set(baseline["known_failures"])

    assert must_pass | known | set(baseline.get("removed", {})) == ids
    assert not must_pass & known
