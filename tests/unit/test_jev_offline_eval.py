"""JEV 离线评测工具：夹具隔离、请求不泄露标签、响应校验、调用纪律与结论边界。

全部使用假传输层，不联网、不读取真实密钥。
"""

import ast
import asyncio
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from homestay_bot.tools import jev_offline_eval as j

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/jev_offline_calibration.json"
PROBE = ROOT / "tests/fixtures/jev_offline_probe.json"
RETEST = ROOT / "docs/reviews/2026-10-18-planner-complaint-evidence"
MODEL = "jev-1.13.0"


@pytest.fixture(scope="module")
def calibration() -> j.Dataset:
    """读取校准夹具；预检不通过时整组失败。"""
    return j.load_dataset(FIXTURE, "calibration")


@pytest.fixture(scope="module")
def probe() -> j.Dataset:
    """读取探测样本；预检不通过时整组失败。"""
    return j.load_dataset(PROBE, "probe")


def _as_holdout(dataset: j.Dataset) -> j.Dataset:
    """借校准样本构造留出 split，只用于结论逻辑测试，不经过规模校验。"""
    return j.Dataset(split="holdout", cases=dataset.cases, sha256="holdout-test")


def _gold_lookup(dataset: j.Dataset) -> Callable[[Any, str], str]:
    """按远端 state 反查标准答案：假服务只能看到 state，看不到本地标签。"""
    by_text = {c.input["text"]: c.expected for c in dataset.track("intent")}
    by_claim: dict[tuple[str, str], str] = {}
    for case in dataset.track("evidence"):
        passages = json.dumps(case.input["passages"], ensure_ascii=False, sort_keys=True)
        by_claim[(case.input["claim"], passages)] = case.expected

    def label_for(state: Any, key: str) -> str:
        if key == "intent":
            return by_text[state["text"]]
        passages = json.dumps(state["passages"], ensure_ascii=False, sort_keys=True)
        claim = state["claim"] if key == "relation" else state["claims"][int(key.split("_")[1])]
        return by_claim[(claim, passages)]

    return label_for


def _fake_jev(
    label_for: Callable[[Any, str], str],
    seen: list[dict[str, Any]] | None = None,
    model: str = MODEL,
) -> Callable[[httpx.Request], httpx.Response]:
    """按官方响应形状回答：choice 为最高概率项，概率和为 1。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        answers = {}
        for key, question in body["questions"].items():
            options = list(question["criteria"])
            label = label_for(body["state"], key)
            rest = 0.03 / (len(options) - 1)
            probabilities = {o: (0.97 if o == label else rest) for o in options}
            answers[key] = {
                "type": "choice",
                "choice": label,
                "probabilities": probabilities,
                "confidence": 0.9,
            }
        return httpx.Response(
            200,
            json={"model": model, "answers": answers, "usage": {"input_tokens": 40}},
        )

    return handler


def _records(
    dataset: j.Dataset,
    answer: Callable[[j.Case, int], tuple[str, float] | None],
    *,
    latency: int = 100,
) -> list[dict[str, Any]]:
    """直接构造三次尝试的结果行；answer 返回 None 表示该次请求超时。"""
    cases = {case.id: case for case in dataset.cases}
    rows = []
    for unit in j.build_units(dataset, "single"):
        for attempt in (1, 2, 3):
            case = cases[unit.case_ids[0]]
            result = answer(case, attempt)
            row = j._record(unit, attempt, "ok" if result else "timeout")
            row["latency_ms"] = latency
            row["input_tokens"] = 10
            if result:
                row["answers"] = {case.id: {"label": result[0], "confidence": result[1]}}
            rows.append(row)
    return rows


def _planner(dataset: j.Dataset) -> j.PlannerReference:
    """与复测结论一致的满分规划记录：报修清除情绪标记，投诉保留。"""
    cleared = {
        case.planner_ref: (case.expected not in j.COMPLAINT_LABELS,) * 5
        for case in dataset.track("intent")
        if case.planner_ref
    }
    return j.PlannerReference(cleared, "ref-test")


# ---------------------------------------------------------------- 夹具


def test_planner_refs_point_to_the_same_sentences_as_the_retest(calibration: j.Dataset) -> None:
    """规划对照按编号取复测结果；夹具原文与复测样本表不一致时对照就失真。"""
    tree = ast.parse((RETEST / "planner_retest.py").read_text(encoding="utf-8"))
    samples = next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", "") == "SAMPLES" for target in node.targets)
    )
    texts = {sample_id: text for _, sample_id, text, _ in samples}
    referenced = [case for case in calibration.track("intent") if case.planner_ref]
    assert referenced
    for case in referenced:
        assert case.input["text"] == texts[case.planner_ref], case.id


def test_only_agitated_texts_are_scored(calibration: j.Dataset) -> None:
    """生产规则若改动，这些对照样本的分类会漂移，计分集合随之变化。"""
    unscored = {
        case.id
        for case in calibration.track("intent")
        if j.lexical_stage(case.input["text"]) != "agitated"
    }
    assert unscored == {"i-21", "i-22", "i-23", "i-24"}


def _mutated(
    mutate: Callable[[dict[str, Any]], None], tmp_path: Path, source: Path = FIXTURE
) -> Path:
    """复制夹具并做一处修改，返回新文件路径。"""
    raw = json.loads(source.read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["cases"][0].update(note="x"), "未知字段"),
        (lambda raw: raw["cases"][0]["input"].update(text="长" * 7000), "超过"),
        (lambda raw: raw["cases"][0].update(expected={"intent": "angry"}), "expected"),
        (lambda raw: raw["cases"].pop(), "须 24 条"),
        (lambda raw: raw["cases"][30]["annotation"].pop("category"), "category"),
        (lambda raw: raw.update(fictional=False), "fictional"),
    ],
)
def test_preflight_rejects_instead_of_truncating(
    mutate: Callable[[dict[str, Any]], None], message: str, tmp_path: Path
) -> None:
    """预检拒绝不合格夹具，不静默截断或跳过。"""
    with pytest.raises(j.DatasetError) as error:
        j.load_dataset(_mutated(mutate, tmp_path), "calibration")
    assert message in str(error.value)


def test_isolation_rejects_shared_family(calibration: j.Dataset) -> None:
    """同义改写或翻译共享家族，家族跨 split 会污染留出。"""
    first = calibration.cases[0]
    leaked = j.Dataset("holdout", (dataclasses.replace(first, id="h-01"),), "h")
    assert j.check_isolation(calibration, leaked) == [f"校准与留出共用家族：['{first.family_id}']"]


# ---------------------------------------------------------------- 请求与响应


@pytest.mark.parametrize("mode", ["single", "batched"])
def test_requests_carry_only_inputs(calibration: j.Dataset, mode: str) -> None:
    """state 只含 input；标签、理由、来源与编号都不出本机，问题键不编码样本。"""
    units = j.build_units(calibration, mode)
    covered = [case_id for unit in units for case_id in unit.case_ids]
    assert sorted(covered) == sorted(case.id for case in calibration.cases)
    secrets = {case.id for case in calibration.cases}
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    secrets |= {case["annotation"]["reason"] for case in raw["cases"]}
    secrets |= {ref for case in raw["cases"] for ref in case["source_refs"]}
    for unit in units:
        payload = json.dumps({"state": unit.state, "questions": unit.questions}, ensure_ascii=False)
        assert not [secret for secret in secrets if secret in payload]
        assert all(key == "intent" or key.startswith("relation") for key in unit.questions)
    if mode == "batched":
        assert len(units) < len(calibration.cases)


def _probs(**changes: float) -> dict[str, float]:
    """合法的五选项概率分布，可覆盖个别选项。"""
    base = {"service_only": 0.8, "complaint_only": 0.05, "both": 0.05, "neither": 0.05}
    return {**base, "unclear": 0.05, **changes}


def _body(**overrides: Any) -> dict[str, Any]:
    """合法的单题意图响应，可按需覆盖字段。"""
    answer = {"type": "choice", "choice": "service_only", "probabilities": _probs()}
    answer["confidence"] = 0.7
    answer.update(overrides)
    return {"model": MODEL, "answers": {"intent": answer}, "usage": {"input_tokens": 12}}


@pytest.mark.parametrize(
    ("body", "category"),
    [
        ({**_body(), "model": "jev-1.14.0"}, "model_mismatch"),
        ({**_body(), "answers": {}}, "question_keys"),
        (_body(probabilities={"service_only": 1.0}), "option_set"),
        (_body(probabilities=_probs(both=float("nan"))), "probability_value"),
        (_body(probabilities=_probs(both=0.5)), "probability_sum"),
        (_body(confidence=1.5), "confidence_value"),
        (_body(choice="both"), "choice_not_max"),
        (_body(confidence=True), "confidence_value"),
    ],
)
def test_malformed_answers_never_become_labels(
    calibration: j.Dataset, body: dict[str, Any], category: str
) -> None:
    """不合约的响应只记协议错误类别，不会落成默认标签。"""
    unit = j.build_units(calibration, "single")[0]
    with pytest.raises(j.ProtocolError) as error:
        j.parse_response(body, unit, MODEL)
    assert error.value.category == category


def test_valid_answer_and_missing_usage(calibration: j.Dataset) -> None:
    """合法响应解析为标签；usage 缺失记空，不填 0。"""
    unit = j.build_units(calibration, "single")[0]
    answers, tokens = j.parse_response(_body(), unit, MODEL)
    assert answers[unit.case_ids[0]].label == "service_only"
    assert tokens == 12
    assert j.parse_response({**_body(), "usage": {}}, unit, MODEL)[1] is None


# ---------------------------------------------------------------- 调用纪律


async def _execute(
    handler: Any, units: list[j.RequestUnit], tmp_path: Path, timeout: float = 1.0
) -> tuple[j.ExecutionReport, Path]:
    """用假传输层执行；返回报告与结果文件。"""
    path = tmp_path / "results.jsonl"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await j.execute(
            units,
            client=client,
            base_url="https://jev.example",
            api_key="test-key",
            model=MODEL,
            results_path=path,
            timeout=timeout,
        )
    return report, path


@pytest.mark.parametrize(("code", "status"), [(429, "rate_limited"), (529, "overloaded")])
async def test_failures_are_recorded_without_retry(
    calibration: j.Dataset, tmp_path: Path, code: int, status: str
) -> None:
    """限流与过载各占一次尝试，不隐式重试。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(code)

    units = j.build_units(calibration, "single")[:3]
    report, path = await _execute(handler, units, tmp_path)
    assert len(calls) == len(units) * j.ATTEMPTS
    assert {r["status"] for r in report.records} == {status}
    assert len(path.read_text(encoding="utf-8").splitlines()) == len(calls)


async def test_auth_failure_stops_the_run(calibration: j.Dataset, tmp_path: Path) -> None:
    """401 后不再领取新请求，剩余记 not_executed 而非失败重发。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)

    units = j.build_units(calibration, "single")[:4]
    report, _ = await _execute(handler, units, tmp_path)
    assert report.status == "auth_failed"
    assert len(calls) <= j.CONCURRENCY
    statuses = [r["status"] for r in report.records]
    assert statuses.count("not_executed") == len(units) * j.ATTEMPTS - len(calls)


async def test_timeout_and_redirect_are_failures(calibration: j.Dataset, tmp_path: Path) -> None:
    """超过单次时限记 timeout；重定向不跟随，按 HTTP 状态记失败。"""
    state = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            await asyncio.sleep(0.2)
        return httpx.Response(302, headers={"location": "https://elsewhere.example"})

    units = j.build_units(calibration, "single")[:1]
    report, _ = await _execute(handler, units, tmp_path, timeout=0.05)
    by_attempt = {r["attempt"]: r["status"] for r in report.records}
    assert by_attempt == {1: "timeout", 2: "http_302", 3: "http_302"}


async def test_results_hold_no_input_text(calibration: j.Dataset, tmp_path: Path) -> None:
    """结果文件只有编号、状态与数值，不含样本原文。"""
    units = j.build_units(calibration, "single")
    _, path = await _execute(_fake_jev(_gold_lookup(calibration)), units[:5], tmp_path)
    text = path.read_text(encoding="utf-8")
    for case in calibration.track("intent")[:5]:
        assert case.input["text"] not in text


@pytest.mark.parametrize("labels", [j.INTENT_LABELS, j.RELATION_LABELS])
def test_option_orders_lead_with_different_options(labels: tuple[str, ...]) -> None:
    """三种顺序都是同一选项全集的排列，且排第一的选项各不相同，首项偏向才测得出来。"""
    orders = [j.option_order(labels, attempt) for attempt in (1, 2, 3)]
    assert all(sorted(order) == sorted(labels) for order in orders)
    assert len({order[0] for order in orders}) == 3


async def test_each_attempt_sends_its_own_option_order(
    calibration: j.Dataset, tmp_path: Path
) -> None:
    """同一请求三次分别用原序、倒序、轮转；state、问题键和选项全集不变。"""
    seen: list[dict[str, Any]] = []
    units = j.build_units(calibration, "single")[:1]
    report, _ = await _execute(_fake_jev(_gold_lookup(calibration), seen), units, tmp_path)
    assert {tuple(body["questions"]["intent"]["criteria"]) for body in seen} == {
        tuple(j.option_order(j.INTENT_LABELS, attempt)) for attempt in (1, 2, 3)
    }
    assert {json.dumps(body["state"], ensure_ascii=False) for body in seen} == {
        json.dumps(units[0].state, ensure_ascii=False)
    }
    assert {r["attempt"]: r["option_order"] for r in report.records} == {
        1: "canonical",
        2: "reversed",
        3: "rotated",
    }


# ---------------------------------------------------------------- 统计与结论


def test_failures_stay_in_the_denominator(calibration: j.Dataset) -> None:
    """失败与 abstain 都占覆盖率分母，不映射成安全或正确。"""
    zh = [c for c in calibration.track("evidence") if c.language == "zh"]
    first_half = {c.id for c in zh[: len(zh) // 2]}
    records = _records(
        calibration,
        lambda case, attempt: (case.expected, 0.95) if case.id in first_half else None,
    )
    result = j.quality(zh, j.case_outcomes(records), 0.7)
    assert result["n"] == len(zh)
    assert result["accepted"] == len(first_half)
    assert result["coverage"] == round(len(first_half) / len(zh), 4)
    assert result["accuracy"] == 1.0


def test_threshold_prefers_coverage_then_the_higher_value(calibration: j.Dataset) -> None:
    """满足准确率的阈值里取覆盖最高者，覆盖相同取较高阈值。"""
    zh = [c for c in calibration.track("evidence") if c.language == "zh"]
    records = _records(calibration, lambda case, attempt: (case.expected, 0.95))
    selection = j.select_threshold(zh, j.case_outcomes(records))
    assert selection["selected"] == 0.9
    low = _records(calibration, lambda case, attempt: (case.expected, 0.75))
    assert j.select_threshold(zh, j.case_outcomes(low))["selected"] == 0.7


def test_p95_uses_ceiling_rank() -> None:
    """P95 取第 ceil(0.95N) 项；N 为 0 时为空。"""
    assert j.percentile_95(list(range(1, 21))) == 19
    assert j.percentile_95([5]) == 5
    assert j.percentile_95([]) is None


def _holdout_summary(
    calibration: j.Dataset,
    answer: Callable[[j.Case, int], tuple[str, float] | None],
    *,
    network: str = "production_like",
    planner: j.PlannerReference | None = None,
    latency: int = 100,
) -> dict[str, Any]:
    """在构造的留出 split 上用冻结阈值 0.7 计算结论。"""
    holdout = _as_holdout(calibration)
    return j.summarize(
        holdout,
        _records(holdout, answer, latency=latency),
        thresholds={"intent": 0.7, "evidence": 0.7},
        network=network,
        complete=True,
        planner=planner,
    )


def test_perfect_evidence_answers_can_be_candidate(calibration: j.Dataset) -> None:
    """全对、在生产同类网络测得时，E 轨能成为 candidate；修正跨越否定以外的类型。"""
    summary = _holdout_summary(calibration, lambda case, attempt: (case.expected, 0.95))
    evidence = summary["tracks"]["evidence"]
    assert evidence["verdict"] == {"result": "candidate", "reasons": []}
    assert evidence["comparison"]["non_negation_corrected_families"]


def test_planner_at_ceiling_leaves_no_room_for_intent(calibration: j.Dataset) -> None:
    """规划器已满分时，JEV 全对也拿不到修正，I 轨不能成为 candidate（审查 JR-7）。"""
    summary = _holdout_summary(
        calibration, lambda case, attempt: (case.expected, 0.95), planner=_planner(calibration)
    )
    verdict = summary["tracks"]["intent"]["verdict"]
    assert verdict["result"] == "not_candidate"
    assert "too_few_corrected_families" in verdict["reasons"]


def test_intent_without_planner_reference_is_inconclusive(calibration: j.Dataset) -> None:
    """留出阶段没有规划器对照时，质量再好也只能是 inconclusive。"""
    summary = _holdout_summary(calibration, lambda case, attempt: (case.expected, 0.95))
    assert summary["tracks"]["intent"]["verdict"] == {
        "result": "inconclusive",
        "reasons": ["no_comparable_baseline"],
    }


def test_safety_error_on_a_later_attempt_vetoes(calibration: j.Dataset) -> None:
    """第三次把非 supported 接纳成 supported，也取消 candidate。"""

    def answer(case: j.Case, attempt: int) -> tuple[str, float]:
        if case.id == "e-03" and attempt == 3:
            return "supported", 0.95
        return case.expected, 0.95

    evidence = _holdout_summary(calibration, answer)["tracks"]["evidence"]
    assert evidence["safety_errors"] == [{"case_id": "e-03", "attempt": 3, "label": "supported"}]
    assert "accepted_safety_error" in evidence["verdict"]["reasons"]


def test_english_cannot_carry_chinese(calibration: j.Dataset) -> None:
    """中文错 1 条即低于 95%，即便算上英文整体仍达标，也按中文子集判定（审查 JR-10）。"""

    def answer(case: j.Case, attempt: int) -> tuple[str, float]:
        # e-07 的错判不是安全关键错误，也不影响 supported 召回，只拉低中文准确率。
        return ("contradicted", 0.95) if case.id == "e-07" else (case.expected, 0.95)

    evidence = _holdout_summary(calibration, answer)["tracks"]["evidence"]
    assert evidence["quality"]["all"]["accuracy"] >= j.TARGET_ACCURACY
    assert evidence["verdict"] == {"result": "not_candidate", "reasons": ["zh_accuracy"]}


def test_negation_only_corrections_are_not_enough(calibration: j.Dataset) -> None:
    """只在否定类上胜过字面基线，不足以说明能泛化（审查 JR-2）。"""

    def answer(case: j.Case, attempt: int) -> tuple[str, float] | None:
        if case.track == "intent":
            return case.expected, 0.95
        if case.category == "negation" or j.evidence_baseline(case) == (
            case.expected == "supported"
        ):
            return case.expected, 0.95
        return None

    comparison = _holdout_summary(calibration, answer)["tracks"]["evidence"]["comparison"]
    assert comparison["corrected_families_by_category"].keys() == {"negation"}
    assert comparison["non_negation_corrected_families"] == []


def test_low_supported_recall_is_rejected(calibration: j.Dataset) -> None:
    """有出处的主张被判无出处，等于误删审核知识；召回低于字面基线即不通过（审查 JR-9）。"""

    def answer(case: j.Case, attempt: int) -> tuple[str, float]:
        if case.expected == "supported":
            return "insufficient", 0.95
        return case.expected, 0.95

    evidence = _holdout_summary(calibration, answer)["tracks"]["evidence"]
    assert evidence["supported_recall"]["composite"] == 0.0
    assert "supported_recall_below_baseline" in evidence["verdict"]["reasons"]


def test_latency_from_other_network_is_only_reference(calibration: j.Dataset) -> None:
    """不在生产同类网络测得的耗时不计入 candidate（审查 JR-3）。"""
    evidence = _holdout_summary(
        calibration, lambda case, attempt: (case.expected, 0.95), network="other", latency=9000
    )["tracks"]["evidence"]
    assert evidence["verdict"] == {
        "result": "inconclusive",
        "reasons": ["latency_not_production_network"],
    }
    slow = _holdout_summary(calibration, lambda case, attempt: (case.expected, 0.95), latency=2000)[
        "tracks"
    ]["evidence"]
    assert "latency_p95" in slow["verdict"]["reasons"]


# ---------------------------------------------------------------- 端到端与命令行


async def _run(
    calibration: j.Dataset, out: Path, mode: str, seen: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """用假 JEV 跑一次校准 split。"""
    transport = httpx.MockTransport(_fake_jev(_gold_lookup(calibration), seen))
    async with httpx.AsyncClient(transport=transport) as client:
        return await j.run_split(
            target=calibration,
            calibration=calibration,
            holdout=None,
            model=MODEL,
            network="production_like",
            evidence_mode=mode,
            base_url="https://jev.example",
            api_key="secret-test-key",
            out=out,
            client=client,
            thresholds=None,
            planner=j.load_planner_reference(RETEST / "retest_final.jsonl"),
        )


async def test_calibration_run_writes_bound_artifacts(
    calibration: j.Dataset, tmp_path: Path
) -> None:
    """校准运行写出绑定摘要与结果；不落密钥与本机路径；留出可据此取阈值，绑定变化即拒绝。"""
    out = tmp_path / "cal"
    seen: list[dict[str, Any]] = []
    summary = await _run(calibration, out, "single", seen)
    assert len(seen) == j.REQUEST_CAP["calibration"]
    assert summary["complete"] is True
    for name in ("manifest.json", "results.jsonl", "summary.json", "summary.md"):
        text = (out / name).read_text(encoding="utf-8")
        assert "secret-test-key" not in text
        assert str(tmp_path) not in text
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    expected = j.bindings(
        model=MODEL,
        evidence_mode="single",
        calibration=calibration,
        holdout=None,
        planner=None,
    )
    thresholds = j.load_calibration_thresholds(out, expected)
    assert thresholds == {
        name: summary["tracks"][name]["threshold"] for name in ("intent", "evidence")
    }
    with pytest.raises(j.UsageError, match="model"):
        j.load_calibration_thresholds(out, {**expected, "model": "jev-1.14.0"})


async def test_batched_mode_agrees_with_single(calibration: j.Dataset, tmp_path: Path) -> None:
    """批量模式用更少请求覆盖全部 E 轨主张，并可与逐条运行比较一致性。"""
    await _run(calibration, tmp_path / "single", "single")
    await _run(calibration, tmp_path / "batched", "batched")
    batched_lines = (tmp_path / "batched/results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(batched_lines) < j.REQUEST_CAP["calibration"]
    result = j.compare_runs(tmp_path / "single", tmp_path / "batched")
    assert result["modes"] == ["single", "batched"]
    assert result["compared"] == len(calibration.track("evidence"))
    assert result["agreement"] == 1.0


def _cli(*extra: str, out: Path) -> list[str]:
    """组装 run 命令参数。"""
    return [
        "run",
        "--split",
        "calibration",
        "--network",
        "production_like",
        "--calibration",
        str(FIXTURE),
        "--out",
        str(out),
        *extra,
    ]


@pytest.mark.parametrize(
    ("args", "env_key", "message"),
    [
        (["--model", MODEL], "k", "--live"),
        (["--live", "--model", "jev-latest"], "k", "固定版本"),
        (["--live", "--model", MODEL], "", "TYPESAFE_API_KEY"),
        (["--live", "--model", MODEL, "--split", "holdout"], "k", "--calibration-result"),
    ],
)
def test_cli_refuses_before_any_network(
    args: list[str],
    env_key: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """前提不满足时在构造客户端前拒绝。"""

    def no_client(*_: Any, **__: Any) -> None:
        raise AssertionError("不应构造外联客户端")

    monkeypatch.setattr(j, "build_public_https_client", no_client)
    monkeypatch.setenv("TYPESAFE_API_KEY", env_key)
    assert j.main(_cli(*args, out=tmp_path / "out")) == 1
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("inside_repo", [True, False])
def test_cli_rejects_unsafe_out_dir(
    inside_repo: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """输出目录不能在仓库内，也不能覆盖已有目录。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    out = ROOT / "jev-out-should-not-exist" if inside_repo else tmp_path
    assert j.main(_cli("--live", "--model", MODEL, out=out)) == 1
    assert "输出目录" in capsys.readouterr().err
    if inside_repo:
        assert not out.exists()


def test_validate_reports_counts_only(capsys: pytest.CaptureFixture[str]) -> None:
    """预检只输出数量与摘要，不输出样本原文。"""
    assert j.main(["validate", "--calibration", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["calibration"]["intent"]["scored_agitated_only"] == 20
    assert "卫生间" not in output


# ---------------------------------------------------------------- 前提探测


def test_probe_stays_within_cap_and_out_of_evaluation_sets(
    probe: j.Dataset, calibration: j.Dataset
) -> None:
    """探测请求数不超过上限，编号、家族、句子都不在校准集里。"""
    assert len(j.build_units(probe, "single")) <= j.PROBE_MAX_REQUESTS
    assert j.check_probe_isolation(probe, [calibration]) == []


def test_probe_sentence_reused_elsewhere_is_rejected(
    probe: j.Dataset, calibration: j.Dataset
) -> None:
    """探测句子即使换了编号和家族混进评测集，也按句子拒绝；资料段同样算。"""
    claim_case = probe.track("evidence")[0]
    passage_source = probe.track("evidence")[3]
    reused_claim = dataclasses.replace(claim_case, id="c-x1", family_id="x1")
    borrowed = calibration.track("evidence")[0]
    reused_passage = dataclasses.replace(
        borrowed,
        id="c-x2",
        family_id="x2",
        input={**borrowed.input, "passages": passage_source.input["passages"][:1]},
    )
    leaked = j.Dataset("calibration", (reused_claim, reused_passage), "x")
    assert j.check_probe_isolation(probe, [leaked]) == [
        f"探测句子出现在calibration中：{sorted([claim_case.id, passage_source.id])}"
    ]


def _extra_cases(raw: dict[str, Any]) -> None:
    """把探测样本扩到 11 条，超过请求上限。"""
    for number in range(3):
        extra = json.loads(json.dumps(raw["cases"][0]))
        extra["id"] = f"p-extra-{number}"
        extra["family_id"] = f"probe-extra-{number}"
        extra["input"]["text"] = f"探测补充句{number}！！！"
        raw["cases"].append(extra)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_extra_cases, "最多 10 条"),
        (lambda raw: raw["cases"][0].update(language="en"), "只用中文"),
        (lambda raw: raw.update(cases=raw["cases"][:4]), "每轨至少 1 条"),
    ],
)
def test_probe_preflight(
    mutate: Callable[[dict[str, Any]], None], message: str, tmp_path: Path
) -> None:
    """探测样本超量、混入英文或缺一轨时拒绝。"""
    with pytest.raises(j.DatasetError) as error:
        j.load_dataset(_mutated(mutate, tmp_path, PROBE), "probe")
    assert message in str(error.value)


def _probe_handler(kind: str, probe: j.Dataset) -> Callable[[httpx.Request], httpx.Response]:
    """按场景构造假 JEV：正常、断网、鉴权失败、版本不符、部分过载、整体答错。"""
    gold = _gold_lookup(probe)

    def wrong(state: Any, key: str) -> str:
        options = j.INTENT_LABELS if key == "intent" else j.RELATION_LABELS
        return next(option for option in options if option != gold(state, key))

    if kind == "passed":
        return _fake_jev(gold)
    if kind == "chinese_failing":
        return _fake_jev(wrong)
    if kind == "protocol_error":
        return _fake_jev(gold, model="jev-1.14.0")
    if kind == "auth_failed":
        return lambda request: httpx.Response(401)
    if kind == "unreachable":

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        return refuse
    if kind == "url_rejected":

        def reject(request: httpx.Request) -> httpx.Response:
            # 与真实外联策略一致：DNS 返回非公网地址时在连接前拒绝。
            raise j.OutboundUrlRejected("外联域名包含非公网地址")

        return reject
    answer = _fake_jev(gold)
    state = {"n": 0}

    def partly_overloaded(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(529) if state["n"] == 1 else answer(request)

    return partly_overloaded


async def _probe(probe: j.Dataset, handler: Any, out: Path) -> dict[str, Any]:
    """用假 JEV 跑一次探测。"""
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await j.run_probe(
            probe=probe,
            model=MODEL,
            network="other",
            base_url="https://jev.example",
            api_key="secret-test-key",
            out=out,
            client=client,
        )


@pytest.mark.parametrize(
    "kind",
    [
        "passed",
        "url_rejected",
        "unreachable",
        "auth_failed",
        "protocol_error",
        "incomplete",
        "chinese_failing",
    ],
)
async def test_probe_answers_only_the_preconditions(
    probe: j.Dataset, tmp_path: Path, kind: str
) -> None:
    """每条只发一次、不重试；判定区分本机拒绝、连通、鉴权、协议、未答完和中文明显失效。

    被本机外联策略拒绝的请求没有离开本机，不计入已发出请求数。
    """
    calls: list[int] = []
    inner = _probe_handler(kind, probe)

    def counted(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return inner(request)

    summary = await _probe(probe, counted, tmp_path / "probe")
    assert summary["probe"]["result"] == kind
    sent = summary["probe"]["requests_sent"]
    assert sent == (0 if kind == "url_rejected" else len(calls))
    if kind == "auth_failed":
        assert len(calls) <= j.CONCURRENCY
    else:
        assert len(calls) == len(probe.cases)


async def test_probe_results_cannot_become_quality_evidence(
    probe: j.Dataset, calibration: j.Dataset, tmp_path: Path
) -> None:
    """探测目录不能拿来取阈值或参与比较；输出不含密钥与本机路径。"""
    out = tmp_path / "probe"
    await _probe(probe, _probe_handler("passed", probe), out)
    for name in ("manifest.json", "results.jsonl", "summary.json", "summary.md"):
        text = (out / name).read_text(encoding="utf-8")
        assert "secret-test-key" not in text
        assert str(tmp_path) not in text
    expected = j.bindings(
        model=MODEL, evidence_mode="single", calibration=calibration, holdout=None, planner=None
    )
    with pytest.raises(j.UsageError, match="calibration"):
        j.load_calibration_thresholds(out, expected)
    with pytest.raises(j.UsageError, match="探测"):
        j.compare_runs(out, out)


def test_cli_probe_and_run_refuse_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """探测未加 --live 时拒绝；评测运行遇到与探测重合的样本也在联网前拒绝。"""

    def no_client(*_: Any, **__: Any) -> None:
        raise AssertionError("不应构造外联客户端")

    monkeypatch.setattr(j, "build_public_https_client", no_client)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    common = ["--model", MODEL, "--network", "other", "--calibration", str(FIXTURE)]
    assert j.main(["probe", *common, "--out", str(tmp_path / "a")]) == 1
    assert "--live" in capsys.readouterr().err

    overlapping = _mutated(
        lambda raw: raw["cases"][0]["input"].update(text="卫生间下水堵了！！！"), tmp_path, PROBE
    )
    run = ["run", "--live", "--split", "calibration", *common, "--probe", str(overlapping)]
    assert j.main([*run, "--out", str(tmp_path / "b")]) == 1
    assert "探测句子出现在calibration中" in capsys.readouterr().err
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_first_option_bias_shows_and_is_vetoed(calibration: j.Dataset) -> None:
    """总选排第一选项的模型：各顺序首项份额等于作答数，且原序下接纳安全关键错误，取消 candidate。"""

    def first_listed(case: j.Case, attempt: int) -> tuple[str, float]:
        return j.option_order(j._labels(case.track), attempt)[0], 0.95

    summary = _holdout_summary(calibration, first_listed)
    for name in ("intent", "evidence"):
        track = summary["tracks"][name]
        shares = track["stability"]["first_option"]
        assert all(row["picked_first"] == row["answered"] > 0 for row in shares.values())
        assert track["stability"]["by_order"]["reversed"]["agreement"] == 0.0
        assert "accepted_safety_error" in track["verdict"]["reasons"]


# ---------------------------------------------------------------- 按轨道运行


def _evidence_only_holdout(tmp_path: Path, drop: int = 0) -> Path:
    """合成只含 E 轨的留出夹具：36 条、四种关系各 9 条、前 8 条英文；drop 用来制造条数不对。"""
    categories = {
        "supported": "negation",
        "contradicted": "charge",
        "insufficient": "attribute",
        "conflicting": "conflict",
    }
    cases = []
    for index in range(36 - drop):
        relation = j.RELATION_LABELS[index % 4]
        cases.append(
            {
                "id": f"h-e-{index + 1:02d}",
                "family_id": f"h-fam-{index}",
                "track": "evidence",
                "language": "en" if index < 8 else "zh",
                "input": {
                    "claim": f"合成主张{index}",
                    "passages": [{"id": "p1", "text": f"资料{index}"}],
                },
                "expected": {"relation": relation},
                "annotation": {"reason": "合成", "category": categories[relation]},
                "source_refs": ["synthetic:test"],
            }
        )
    raw = {"schema_version": 1, "fictional": True, "split": "holdout", "cases": cases}
    path = tmp_path / f"holdout-{drop}.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_evidence_only_holdout_is_accepted_but_sizes_still_checked(tmp_path: Path) -> None:
    """缺席的轨道不算错；出现的轨道仍按规模校验。"""
    holdout = j.load_dataset(_evidence_only_holdout(tmp_path), "holdout")
    assert {case.track for case in holdout.cases} == {"evidence"}
    with pytest.raises(j.DatasetError, match="须 36 条"):
        j.load_dataset(_evidence_only_holdout(tmp_path, drop=1), "holdout")


async def test_evidence_only_runs_bind_tracks_end_to_end(
    calibration: j.Dataset, tmp_path: Path
) -> None:
    """只跑 E 轨：校准只发 E 轨请求并在绑定里记下轨道；留出按同一轨道取阈值并给出结论。"""
    seen: list[dict[str, Any]] = []
    cal_out = tmp_path / "cal"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_fake_jev(_gold_lookup(calibration), seen))
    ) as client:
        cal_summary = await j.run_split(
            target=calibration,
            calibration=calibration,
            holdout=None,
            model=MODEL,
            network="production_like",
            evidence_mode="single",
            base_url="https://jev.example",
            api_key="k",
            out=cal_out,
            client=client,
            thresholds=None,
            planner=None,
            tracks=("evidence",),
        )
    assert len(seen) == len(calibration.track("evidence")) * j.ATTEMPTS
    assert all("relation" in body["questions"] for body in seen)
    assert set(cal_summary["tracks"]) == {"evidence"}

    holdout = j.load_dataset(_evidence_only_holdout(tmp_path), "holdout")
    expected = j.bindings(
        model=MODEL,
        evidence_mode="single",
        calibration=calibration,
        holdout=holdout,
        planner=None,
        tracks=("evidence",),
    )
    thresholds = j.load_calibration_thresholds(cal_out, expected)
    assert set(thresholds) == {"evidence"}
    with pytest.raises(j.UsageError, match="tracks"):
        j.load_calibration_thresholds(cal_out, {**expected, "tracks": list(j.TRACKS)})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_fake_jev(_gold_lookup(holdout)))
    ) as client:
        summary = await j.run_split(
            target=holdout,
            calibration=calibration,
            holdout=holdout,
            model=MODEL,
            network="production_like",
            evidence_mode="single",
            base_url="https://jev.example",
            api_key="k",
            out=tmp_path / "hold",
            client=client,
            thresholds=thresholds,
            planner=None,
            tracks=("evidence",),
        )
    assert set(summary["tracks"]) == {"evidence"}
    assert summary["tracks"]["evidence"]["verdict"]["result"] in {
        "candidate",
        "not_candidate",
        "inconclusive",
    }
    with pytest.raises(j.UsageError, match="缺少所选轨道"):
        await j.run_split(
            target=holdout,
            calibration=calibration,
            holdout=holdout,
            model=MODEL,
            network="production_like",
            evidence_mode="single",
            base_url="https://jev.example",
            api_key="k",
            out=tmp_path / "hold2",
            client=client,
            thresholds=thresholds,
            planner=None,
            tracks=j.TRACKS,
        )


def test_threshold_with_any_order_safety_error_is_not_selected(calibration: j.Dataset) -> None:
    """只在倒序那次接纳了安全关键错误，覆盖最高的 0.5 档也不能选，改选 0.7。"""

    def answer(case: j.Case, attempt: int) -> tuple[str, float]:
        if case.id == "e-14" and attempt == 2:
            return "supported", 0.6
        if case.id == "e-02":
            return case.expected, 0.6  # 只在 0.5 档被接纳
        if case.id == "e-04":
            return case.expected, 0.8  # 在 0.5、0.7 档被接纳，三档覆盖依次递减
        return case.expected, 0.95

    evidence = calibration.track("evidence")
    zh = [case for case in evidence if case.language == "zh"]
    table = j.case_outcomes(_records(calibration, answer))
    naive = j.select_threshold(zh, table)
    strict = j.select_threshold(zh, table, evidence)
    assert naive["selected"] == 0.5
    assert strict["selected"] == 0.7
    assert strict["candidates"][0]["safety_errors"] == 1


def test_abstention_on_true_facts_falls_back_to_literal_baseline(calibration: j.Dataset) -> None:
    """JEV 对真实事实弃权时回落字面基线，组合召回等于基线，不触发召回门槛。"""

    def answer(case: j.Case, attempt: int) -> tuple[str, float]:
        return (case.expected, 0.3) if case.expected == "supported" else (case.expected, 0.95)

    evidence = _holdout_summary(calibration, answer)["tracks"]["evidence"]
    recall = evidence["supported_recall"]
    assert recall["jev_accepted_only"] == 0.0
    assert recall["composite"] == recall["baseline"]
    assert "supported_recall_below_baseline" not in evidence["verdict"]["reasons"]
