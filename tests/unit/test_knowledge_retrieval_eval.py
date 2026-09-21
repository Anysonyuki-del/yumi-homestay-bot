"""知识检索离线评估：固定合成用例，分别度量召回、证据完整性与证据门放行。

用例在 tests/fixtures 下：`knowledge_retrieval_cases.json` 是校准集（用于调整
别名与权重），`knowledge_retrieval_holdout.json` 是另一作者编写的留出集（只在
调整结束后一次性检验）。每个用例自带一个小知识库，经真实 SQLite 仓储写入、
按 mutations 逐条提交后，再用新会话检索，因此停用、修改和候选隔离走的是生产
读取路径。

度量彼此独立：
- Recall@3：可回答问题至少一个正确来源进入前 3。
- 多主题覆盖：所需来源全部进入交给模型的证据。
- 证据完整：命中正确来源时，关键事实原文出现在证据里（检验截断）。
- 错误放行：本应「未确认」的专属问题被证据门判为已覆盖。放行按生产口径计算：
  问题被判为专属问题且证据门通过；未被判为专属的问题，回复里的本店自述会被
  逐句删除，等同未放行。
- 回复断言：模拟回复中不被证据支持的断言必须被拦下，被支持的不应误拦。
- 隔离与边界：停用、旧答案、候选草稿不进证据；实时与交易问题不由知识放行。
  另报告实时问题是否被交易分类或房态强制规则识别：那属于交易策略，不是检索
  改动的验收项，只如实列出。

模型只用确定性函数替身，不证明真实模型的最终回答准确率。

记录基线或查看报告（在仓库根目录执行，`--show-holdout` 才列出留出集逐条失败；
`--record-baseline-v2` 只把第二套留出集写进独立基线文件）：
    PYTHONPATH=src:tests .venv/bin/python \
        tests/unit/test_knowledge_retrieval_eval.py --report
    PYTHONPATH=src:tests .venv/bin/python \
        tests/unit/test_knowledge_retrieval_eval.py --record-baseline
"""

import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import Base, KnowledgeCandidate, KnowledgeEntry
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.services.answer_policy import is_property_specific, is_transaction_sensitive
from homestay_bot.services.knowledge_service import KnowledgeService, KnowledgeSnippet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASE_FILES = {
    "calibration": FIXTURES / "knowledge_retrieval_cases.json",
    "holdout": FIXTURES / "knowledge_retrieval_holdout.json",
    # 第二套留出集：调参结束、代码冻结后才看逐条结果，平时只出汇总。
    "holdout_v2": FIXTURES / "knowledge_retrieval_holdout_v2.json",
}
BASELINE_FILE = FIXTURES / "knowledge_retrieval_baseline.json"
BASELINE_V2_FILE = FIXTURES / "knowledge_retrieval_baseline_v2.json"
BLIND_SPLITS = ("holdout_v2",)
GROUPS = {
    "synonym",
    "cross_language",
    "long_answer",
    "no_answer",
    "isolation",
    "multi_topic",
    "boundary",
}
# 事先登记的验收目标（Spec 9.2）：调整期间不得据留出集结果改动。
RECALL_AT_3_TARGET = 0.90


def load_cases(split: str) -> list[dict[str, Any]]:
    """读取一个划分的用例；留出集文件不存在时返回空列表。"""
    path = CASE_FILES[split]
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["split"] == split
    return list(data["cases"])


@dataclass(frozen=True)
class CaseOutcome:
    """单个用例的判定结果；不适用的度量为 None。"""

    case_id: str
    split: str
    group: str
    direct: bool
    retrieved_ids: list[int]
    top3_hit: bool | None
    multi_complete: bool | None
    evidence_complete: bool | None
    isolation_ok: bool
    grounded: bool
    grounded_ok: bool
    false_admit: bool | None
    stub_accepted: bool | None
    stub_ok: bool | None
    boundary_ok: bool | None
    realtime_routed: bool | None
    elapsed_ms: float


def _knowledge_entry(raw: dict[str, Any]) -> KnowledgeEntry:
    """把用例里的知识条目转换为数据库行。"""
    return KnowledgeEntry(
        id=raw["id"],
        category=raw["category"],
        question_zh=raw["question_zh"],
        answer_zh=raw["answer_zh"],
        question_en=raw["question_en"],
        answer_en=raw["answer_en"],
        keywords=list(raw.get("keywords", [])),
        is_enabled=bool(raw.get("is_enabled", True)),
    )


async def _apply_mutation(session, mutation: dict[str, Any]) -> None:
    """执行一次管理员知识变更；调用方负责提交。"""
    action = mutation["action"]
    if action == "create":
        session.add(_knowledge_entry(mutation["entry"]))
        return
    entry = await session.get(KnowledgeEntry, mutation["id"])
    assert entry is not None, mutation
    if action == "update":
        for name, value in mutation["fields"].items():
            setattr(entry, name, value)
    elif action == "disable":
        entry.is_enabled = False
    elif action == "enable":
        entry.is_enabled = True
    else:
        raise ValueError(f"未知的知识变更：{action}")


def reply_accepted(
    question: str,
    snippets: list[KnowledgeSnippet],
    reply: str,
    *,
    grounded: bool,
) -> bool:
    """判断证据门是否会让这条模拟回复原样发给客人。

    主题已覆盖之外，回复里的免费说法和数字还要有证据支持。
    """
    return grounded and not DeepSeekGuestAssistant._has_unsupported_property_claims(
        reply,
        question,
        snippets,
    )


async def evaluate_case(case: dict[str, Any]) -> CaseOutcome:
    """在独立内存库里执行一个用例并给出各项判定。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all(_knowledge_entry(raw) for raw in case["knowledge"])
            for index, candidate in enumerate(case.get("candidates") or []):
                session.add(
                    KnowledgeCandidate(
                        canonical_key=f"eval-candidate-{index}",
                        canonical_question=candidate["canonical_question"],
                        category=candidate["category"],
                        draft_payload={
                            "question_zh": candidate["draft_question_zh"],
                            "answer_zh": candidate["draft_answer_zh"],
                        },
                    )
                )
            await session.commit()
        for mutation in case.get("mutations") or []:
            async with factory() as session:
                await _apply_mutation(session, mutation)
                await session.commit()
        async with factory() as session:
            service = KnowledgeService(SQLAlchemyKnowledgeRepository(session))
            started = time.perf_counter()
            retrieved = await service.retrieve(Language(case["language"]), case["question"])
            # 与生产一致：剔除后的才是交给模型的证据。
            snippets = DeepSeekGuestAssistant._scope_knowledge(case["question"], retrieved)
            elapsed_ms = (time.perf_counter() - started) * 1000
            # 保证评估读到的是提交后的状态，而不是会话缓存。
            assert await session.scalar(select(KnowledgeEntry.id).limit(1)) is not None
    finally:
        await engine.dispose()

    retrieved_ids = [item.source_id for item in snippets]
    evidence = "\n".join(
        f"{item.category}\n{item.question}\n{item.answer}" for item in snippets
    )
    expected = set(case["expected_source_ids"])
    required = set(case["required_source_ids"])
    answerable = bool(expected)
    top3_hit = bool(expected & set(retrieved_ids[:3])) if answerable else None
    multi_complete = required <= set(retrieved_ids) if required else None
    evidence_complete: bool | None = None
    if answerable and expected & set(retrieved_ids):
        evidence_complete = all(fact in evidence for fact in case["key_facts"])
    isolation_ok = not any(fact in evidence for fact in case["forbidden_facts"])
    grounded = is_property_specific(
        case["question"]
    ) and DeepSeekGuestAssistant._has_relevant_property_knowledge(
        case["question"],
        snippets,
    )
    should_ground = bool(case["expect_grounded"])
    false_admit = (
        grounded and not should_ground if case["property_specific"] else None
    )
    stub_accepted: bool | None = None
    stub_ok: bool | None = None
    if case.get("stub_reply"):
        stub_accepted = reply_accepted(
            case["question"],
            snippets,
            case["stub_reply"],
            grounded=grounded,
        )
        stub_ok = stub_accepted == bool(case["stub_reply_supported"])
    boundary_ok: bool | None = None
    realtime_routed: bool | None = None
    if case["realtime"]:
        boundary_ok = not grounded
        realtime_routed = is_transaction_sensitive(
            case["question"]
        ) or DeepSeekGuestAssistant._should_force_availability(case["question"])
    return CaseOutcome(
        case_id=case["case_id"],
        split=case["split"],
        group=case["group"],
        direct="direct" in case["tags"],
        retrieved_ids=retrieved_ids,
        top3_hit=top3_hit,
        multi_complete=multi_complete,
        evidence_complete=evidence_complete,
        isolation_ok=isolation_ok,
        grounded=grounded,
        grounded_ok=grounded == should_ground,
        false_admit=false_admit,
        stub_accepted=stub_accepted,
        stub_ok=stub_ok,
        boundary_ok=boundary_ok,
        realtime_routed=realtime_routed,
        elapsed_ms=elapsed_ms,
    )


def direct_passed(outcome: CaseOutcome) -> bool:
    """直接词面问法的通过条件：命中前 3 且证据门判断正确。"""
    return outcome.top3_hit is not False and outcome.grounded_ok


def _rate(values: list[bool]) -> float | None:
    """计算为真的比例；没有样本时返回 None。"""
    return sum(values) / len(values) if values else None


def summarize(outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """把用例结果汇总为互不混用的指标。"""
    latencies = sorted(item.elapsed_ms for item in outcomes)
    unsupported = [item for item in outcomes if item.stub_ok is not None and not item.stub_accepted]
    return {
        "cases": len(outcomes),
        "recall_at_3": _rate([item.top3_hit for item in outcomes if item.top3_hit is not None]),
        "multi_coverage": _rate(
            [item.multi_complete for item in outcomes if item.multi_complete is not None]
        ),
        "evidence_complete": _rate(
            [item.evidence_complete for item in outcomes if item.evidence_complete is not None]
        ),
        "grounding_accuracy": _rate([item.grounded_ok for item in outcomes]),
        "false_admits": sum(bool(item.false_admit) for item in outcomes),
        "false_admit_rate": _rate(
            [item.false_admit for item in outcomes if item.false_admit is not None]
        ),
        "stub_accuracy": _rate([item.stub_ok for item in outcomes if item.stub_ok is not None]),
        "stub_blocked": len(unsupported),
        "isolation_pass": _rate([item.isolation_ok for item in outcomes]),
        "boundary_pass": _rate(
            [item.boundary_ok for item in outcomes if item.boundary_ok is not None]
        ),
        "realtime_routed": _rate(
            [item.realtime_routed for item in outcomes if item.realtime_routed is not None]
        ),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": (
            latencies[max(0, round(0.95 * len(latencies)) - 1)] if latencies else None
        ),
    }


def failure_kinds(outcome: CaseOutcome) -> list[str]:
    """列出单个用例未通过的度量名，便于报告失败类型。"""
    kinds = []
    if outcome.top3_hit is False:
        kinds.append("recall")
    if outcome.multi_complete is False:
        kinds.append("multi")
    if outcome.evidence_complete is False:
        kinds.append("evidence")
    if not outcome.grounded_ok:
        kinds.append("false_admit" if outcome.false_admit else "grounding_miss")
    if outcome.stub_ok is False:
        kinds.append("stub")
    if not outcome.isolation_ok:
        kinds.append("isolation")
    if outcome.boundary_ok is False:
        kinds.append("boundary")
    if outcome.realtime_routed is False:
        kinds.append("realtime_unrouted")
    return kinds


async def evaluate_split(split: str) -> list[CaseOutcome]:
    """按顺序评估一个划分的全部用例。"""
    return [await evaluate_case(case) for case in load_cases(split)]


def _validate_case(case: dict[str, Any], split: str) -> None:
    """校验单个用例的结构与自洽性。"""
    assert case["split"] == split
    assert case["group"] in GROUPS, case["case_id"]
    assert case["language"] in {"zh", "en"}, case["case_id"]
    entries = {raw["id"]: dict(raw) for raw in case["knowledge"]}
    assert len(entries) == len(case["knowledge"]), case["case_id"]
    for mutation in case.get("mutations") or []:
        if mutation["action"] == "create":
            entries[mutation["entry"]["id"]] = dict(mutation["entry"])
        elif mutation["action"] == "update":
            entries[mutation["id"]].update(mutation["fields"])
    assert set(case["expected_source_ids"]) <= set(entries), case["case_id"]
    assert set(case["required_source_ids"]) <= set(entries), case["case_id"]
    answer_field = "answer_zh" if case["language"] == "zh" else "answer_en"
    for fact in case["key_facts"]:
        assert any(
            fact in entries[source_id][answer_field]
            for source_id in case["expected_source_ids"]
        ), (case["case_id"], fact)
    if case.get("stub_reply"):
        assert isinstance(case["stub_reply_supported"], bool), case["case_id"]


@pytest.mark.parametrize("split", ["calibration", "holdout", "holdout_v2"])
def test_eval_cases_are_well_formed(split: str) -> None:
    """用例结构完整、编号唯一，关键事实确实出自正确来源。"""
    cases = load_cases(split)
    if split != "calibration" and not cases:
        pytest.skip(f"{split} 尚未提供")
    assert len(cases) == 40
    assert len({case["case_id"] for case in cases}) == 40
    for case in cases:
        _validate_case(case, split)


def _split_outcomes(split: str) -> list[CaseOutcome]:
    """评估一个划分；留出集缺失时跳过。"""
    if not load_cases(split):
        pytest.skip(f"{split} 用例尚未提供")
    return asyncio.run(evaluate_split(split))


# 第一套留出集已公开并用于回归（2026-09-21），这里只作回归，不再代表泛化效果。
@pytest.mark.parametrize("split", ["calibration", "holdout"])
def test_retrieval_meets_preregistered_targets(split: str) -> None:
    """Spec 9.2 事先登记的目标：召回达标，隔离、边界与回复断言安全项全部通过。"""
    outcomes = _split_outcomes(split)
    summary = summarize(outcomes)
    failures = {
        item.case_id: failure_kinds(item) for item in outcomes if failure_kinds(item)
    }
    assert summary["recall_at_3"] >= RECALL_AT_3_TARGET, (summary, failures)
    assert summary["isolation_pass"] == 1.0, failures
    assert summary["boundary_pass"] == 1.0, failures
    assert summary["false_admits"] == 0, failures
    unsupported_accepted = [
        item.case_id
        for item in outcomes
        if item.stub_accepted and item.stub_ok is False
    ]
    assert unsupported_accepted == []


@pytest.mark.parametrize("split", ["calibration", "holdout"])
def test_direct_questions_do_not_regress_from_baseline(split: str) -> None:
    """改动前已经答对的直接词面问法，改动后仍须答对。"""
    outcomes = _split_outcomes(split)
    baseline = _load_baseline()
    regressed = [
        item.case_id
        for item in outcomes
        if item.direct
        and direct_passed(CaseOutcome(**{**baseline[item.case_id], "elapsed_ms": 0.0}))
        and not direct_passed(item)
    ]
    assert regressed == []


def _load_baseline() -> dict[str, dict[str, Any]]:
    """读取改动前记录的逐用例结果。"""
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return {item["case_id"]: item for item in data["outcomes"]}


def _record_blind_baseline(outcomes: list[CaseOutcome]) -> None:
    """只记录第二套留出集在当前代码上的逐条结果，写入独立文件，不覆盖第一版基线。"""
    selected = [item for item in outcomes if item.split in BLIND_SPLITS]
    payload = {
        "note": "C2 改动前（v1.37.0）第二套留出集的逐条结果；只在 C2 冻结后对比。",
        "summary": {
            key: value
            for key, value in summarize(selected).items()
            if not key.startswith("latency")
        },
        "outcomes": [
            {key: value for key, value in asdict(item).items() if key != "elapsed_ms"}
            for item in selected
        ],
    }
    BASELINE_V2_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _record_baseline(outcomes: list[CaseOutcome]) -> None:
    """写入改动前的逐用例结果，只含编号、来源、判定与汇总，不含耗时。"""
    payload = {
        "note": "C1 改动前的检索与证据门基线；耗时不入库，只在报告里输出。",
        "summary": {
            split: {
                key: value
                for key, value in summarize(
                    [item for item in outcomes if item.split == split]
                ).items()
                if not key.startswith("latency")
            }
            for split in ("calibration", "holdout")
        },
        "outcomes": [
            {
                key: value
                for key, value in asdict(item).items()
                if key != "elapsed_ms"
            }
            for item in outcomes
        ],
    }
    BASELINE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _print_report(outcomes: list[CaseOutcome], *, show_holdout: bool) -> None:
    """打印分划分汇总；留出集默认只给汇总，不给逐用例失败。"""
    for split in CASE_FILES:
        selected = [item for item in outcomes if item.split == split]
        if not selected:
            continue
        print(f"== {split}")
        for key, value in summarize(selected).items():
            print(f"  {key}: {value:.3f}" if isinstance(value, float) else f"  {key}: {value}")
        if split != "calibration" and not show_holdout:
            continue
        for item in selected:
            if kinds := failure_kinds(item):
                print(
                    f"  FAIL {item.case_id} [{item.group}] "
                    f"{','.join(kinds)} ids={item.retrieved_ids}"
                )


if __name__ == "__main__":
    all_outcomes: list[CaseOutcome] = []
    for split_name in CASE_FILES:
        all_outcomes.extend(asyncio.run(evaluate_split(split_name)))
    if "--record-baseline" in sys.argv:
        _record_baseline(all_outcomes)
    if "--record-baseline-v2" in sys.argv:
        _record_blind_baseline(all_outcomes)
    _print_report(all_outcomes, show_holdout="--show-holdout" in sys.argv)
