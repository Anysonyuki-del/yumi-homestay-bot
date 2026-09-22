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

真实语义检索（只在手动运行时联网；key 只从环境变量或 ~/.config/yumi/siliconflow.env
读取，不打印；向量缓存在系统临时目录，调参时同一文本不重复计费）：
    PYTHONPATH=src:tests .venv/bin/python \
        tests/unit/test_knowledge_retrieval_eval.py --report --semantic [--min-similarity 0.5]
延迟验收另加 --latency，查询不使用缓存，知识向量仍复用缓存。缓存模式耗时不代表
生产；失败与超时也计入总体 P95，同时单列无缓存成功 P95 和超时率。

记录基线或查看报告（在仓库根目录执行，`--show-holdout` 才列出留出集逐条失败；
`--record-baseline-v2` 只把第二套留出集写进独立基线文件）：
    PYTHONPATH=src:tests .venv/bin/python \
        tests/unit/test_knowledge_retrieval_eval.py --report
    PYTHONPATH=src:tests .venv/bin/python \
        tests/unit/test_knowledge_retrieval_eval.py --record-baseline
"""

import asyncio
import hashlib
import json
import os
import statistics
import sys
import tempfile
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
from homestay_bot.services.knowledge_embeddings import (
    QUERY_EMBEDDING_TIMEOUT_SECONDS,
    SEMANTIC_MIN_SIMILARITY,
    KnowledgeEmbeddingSync,
    OpenAICompatibleEmbeddingClient,
    SemanticRanker,
)
from homestay_bot.services.knowledge_service import KnowledgeService, KnowledgeSnippet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASE_FILES = {
    "calibration": FIXTURES / "knowledge_retrieval_cases.json",
    # 语义检索专用校准集：换说法、语义相近的无答案干扰、多主题与周边范围；只用于调参。
    "calibration_v2": FIXTURES / "knowledge_retrieval_calibration_v2.json",
    "holdout": FIXTURES / "knowledge_retrieval_holdout.json",
    # 第二套已于 2026-09-22 揭示失败并用于修复，现仅作回归，不再是独立留出。
    "holdout_v2": FIXTURES / "knowledge_retrieval_holdout_v2.json",
    # 第三套独立留出集（Codex 编写）：语义检索调参结束、代码冻结后才看逐条结果，
    # 平时只出汇总；用来在同一版本上比较纯关键词与关键词 + 语义。
    "holdout_v3": FIXTURES / "knowledge_retrieval_holdout_v3.json",
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
    semantic_status: str = "disabled"
    query_requests: int = 0
    query_cache_hits: int = 0


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


@dataclass(frozen=True)
class SemanticEvalConfig:
    """手动评估时的真实语义检索设置。"""

    embedder: Any
    model: str
    min_similarity: float
    query_embedder: Any = None
    sdk: Any = None


class CachedEmbedder:
    """带磁盘缓存的向量化客户端：同一模型同一文本只向服务商请求一次。"""

    def __init__(self, inner: Any, model: str, path: Path) -> None:
        """包装真实客户端并加载缓存。"""
        self._inner = inner
        self._model = model
        self._path = path
        self.hits = 0
        self.requests = 0
        self._cache: dict[str, list[float]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def _key(self, text: str) -> str:
        """缓存键：模型与文本的摘要，缓存文件里不存正文。"""
        return hashlib.sha256(f"{self._model}\n{text}".encode()).hexdigest()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """先查缓存，缺的一次性请求后写回。"""
        missing = [text for text in texts if self._key(text) not in self._cache]
        self.hits += len(texts) - len(missing)
        if missing:
            self.requests += 1
            for text, vector in zip(missing, await self._inner.embed(missing), strict=True):
                self._cache[self._key(text)] = vector
            self._path.write_text(json.dumps(self._cache), encoding="utf-8")
        return [self._cache[self._key(text)] for text in texts]


class EvaluatedRanker(SemanticRanker):
    """仅供评估：在生产回退吞掉异常前记录原因，查询缓存与网络请求分开计数。"""

    def __init__(self, config: SemanticEvalConfig, store: Any) -> None:
        """质量模式复用缓存，延迟模式使用独立的无缓存查询客户端。"""
        self.client = (
            config.query_embedder if config.query_embedder is not None else config.embedder
        )
        self.status = "no_vectors"
        self.requests = 0
        self.cache_hits = 0
        self.queried = False
        super().__init__(self, store, config.model, min_similarity=config.min_similarity,
                         timeout_seconds=QUERY_EMBEDDING_TIMEOUT_SECONDS)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """只统计查询，不把建索引的请求算进查询时延和调用数。"""
        self.queried = True
        cached = isinstance(self.client, CachedEmbedder)
        before_hits = self.client.hits if cached else 0
        before_requests = self.client.requests if cached else 0
        try:
            return await self.client.embed(texts)
        finally:
            self.cache_hits += self.client.hits - before_hits if cached else 0
            self.requests += self.client.requests - before_requests if cached else 1

    async def rank(self, language, query, entries) -> list[int]:
        """沿用生产排序和回退，另记成功、无候选、超时及其他异常。"""
        try:
            result = await super().rank(language, query, entries)
        except Exception as error:
            self.status = "timeout" if isinstance(error, TimeoutError) else "error"
            raise
        self.status = ("success" if result else "no_candidates") if self.queried else "no_vectors"
        return result


def _load_semantic_config(argv: list[str]) -> SemanticEvalConfig | None:
    """解析 --semantic；key 只从环境变量或本机私有文件读取。"""
    if "--semantic" not in argv:
        if "--latency" in argv:
            raise SystemExit("--latency 必须与 --semantic 一起使用")
        return None
    key = os.environ.get("YUMI_EMBEDDING_API_KEY")
    key_file = Path.home() / ".config" / "yumi" / "siliconflow.env"
    if not key and key_file.exists():
        for line in key_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("YUMI_EMBEDDING_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("需要 YUMI_EMBEDDING_API_KEY 或 ~/.config/yumi/siliconflow.env")
    from openai import AsyncOpenAI

    from homestay_bot.config import DEFAULT_EMBEDDING_BASE_URL, DEFAULT_EMBEDDING_MODEL

    minimum = SEMANTIC_MIN_SIMILARITY
    if "--min-similarity" in argv:
        minimum = float(argv[argv.index("--min-similarity") + 1])
    from homestay_bot.services.outbound_url_policy import (
        OutboundUrlPolicy,
        build_public_https_client,
    )

    transport = build_public_https_client(OutboundUrlPolicy(), timeout_seconds=30.0)
    client = AsyncOpenAI(api_key=key, base_url=DEFAULT_EMBEDDING_BASE_URL,
                         http_client=transport, max_retries=0)
    raw = OpenAICompatibleEmbeddingClient(client, DEFAULT_EMBEDDING_MODEL)
    cache = Path(tempfile.gettempdir()) / "yumi-embedding-eval-cache.json"
    return SemanticEvalConfig(
        embedder=CachedEmbedder(
            raw,
            DEFAULT_EMBEDDING_MODEL,
            cache,
        ),
        model=DEFAULT_EMBEDDING_MODEL,
        min_similarity=minimum,
        query_embedder=raw if "--latency" in argv else None,
        sdk=client,
    )


async def evaluate_case(
    case: dict[str, Any],
    semantic: SemanticEvalConfig | None = None,
) -> CaseOutcome:
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
        if semantic is not None:
            # 与生产一致：先按内容哈希补齐向量，再检索。
            from homestay_bot.application import (
                SessionKnowledgeRepository,
                SessionKnowledgeVectorStore,
            )

            await KnowledgeEmbeddingSync(
                SessionKnowledgeRepository(factory),
                SessionKnowledgeVectorStore(factory),
                semantic.embedder,
                semantic.model,
            ).sync_once(limit=10_000)
        async with factory() as session:
            repository = SQLAlchemyKnowledgeRepository(session)
            service = KnowledgeService(repository)
            ranker = EvaluatedRanker(semantic, repository) if semantic is not None else None
            if ranker is not None:
                service = service.with_semantic(ranker)
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
        semantic_status=ranker.status if ranker else "disabled",
        query_requests=ranker.requests if ranker else 0,
        query_cache_hits=ranker.cache_hits if ranker else 0,
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
    queried = [item for item in outcomes if item.query_requests or item.query_cache_hits]
    live_success = sorted(
        item.elapsed_ms for item in outcomes
        if item.query_requests and not item.query_cache_hits
        and item.semantic_status in {"success", "no_candidates"}
    )
    unsupported = [item for item in outcomes if item.stub_ok is not None and not item.stub_accepted]
    return {
        "cases": len(outcomes),
        "query_requests": sum(item.query_requests for item in outcomes),
        "query_cache_hits": sum(item.query_cache_hits for item in outcomes),
        "semantic_success": sum(item.semantic_status == "success" for item in outcomes),
        "semantic_no_vectors": sum(item.semantic_status == "no_vectors" for item in outcomes),
        "semantic_no_candidates": sum(item.semantic_status == "no_candidates" for item in outcomes),
        "semantic_timeouts": sum(item.semantic_status == "timeout" for item in outcomes),
        "semantic_errors": sum(item.semantic_status == "error" for item in outcomes),
        "query_cache_hit_rate": _rate([item.query_cache_hits > 0 for item in queried]),
        "query_timeout_rate": _rate([item.semantic_status == "timeout" for item in queried]),
        "uncached_success_p95_ms": (
            live_success[max(0, round(0.95 * len(live_success)) - 1)] if live_success else None
        ),
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


async def evaluate_split(
    split: str,
    semantic: SemanticEvalConfig | None = None,
) -> list[CaseOutcome]:
    """按顺序评估一个划分的全部用例。"""
    return [await evaluate_case(case, semantic) for case in load_cases(split)]


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


@pytest.mark.parametrize(
    "split",
    ["calibration", "calibration_v2", "holdout", "holdout_v2", "holdout_v3"],
)
def test_eval_cases_are_well_formed(split: str) -> None:
    """用例结构完整、编号唯一，关键事实确实出自正确来源。"""
    cases = load_cases(split)
    if split != "calibration" and not cases:
        pytest.skip(f"{split} 尚未提供")
    assert len(cases) == (30 if split == "calibration_v2" else 40)
    assert len({case["case_id"] for case in cases}) == len(cases)
    for case in cases:
        _validate_case(case, split)


def _split_outcomes(split: str) -> list[CaseOutcome]:
    """评估一个划分；留出集缺失时跳过。"""
    if not load_cases(split):
        pytest.skip(f"{split} 用例尚未提供")
    return asyncio.run(evaluate_split(split))


# 第一套留出集已公开并用于回归（2026-09-21），这里只作回归，不再代表泛化效果。
@pytest.mark.parametrize("split", ["calibration", "holdout", "holdout_v2"])
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
        if not split.startswith("calibration") and not show_holdout:
            continue
        for item in selected:
            if kinds := failure_kinds(item):
                print(
                    f"  FAIL {item.case_id} [{item.group}] "
                    f"{','.join(kinds)} ids={item.retrieved_ids}"
                )




@pytest.mark.asyncio
async def test_latency_mode_bypasses_query_cache_and_counts_fallback(tmp_path) -> None:
    """质量模式允许缓存；延迟模式必须查询真实客户端，失败须单独计数。"""
    from unittest.mock import AsyncMock

    case = load_cases('calibration')[0]
    raw = AsyncMock()
    raw.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
    cached = CachedEmbedder(raw, 'test-model', tmp_path / 'vectors.json')
    quality = SemanticEvalConfig(cached, 'test-model', 0.5)
    await evaluate_case(case, quality)
    warm = await evaluate_case(case, quality)
    assert warm.query_cache_hits == 1
    assert warm.query_requests == 0
    latency = SemanticEvalConfig(cached, 'test-model', 0.5, query_embedder=raw)
    measured = await evaluate_case(case, latency)
    assert measured.query_requests == 1
    assert measured.query_cache_hits == 0
    assert measured.semantic_status == 'success'
    raw.embed.side_effect = TimeoutError
    failed = await evaluate_case(case, latency)
    assert failed.semantic_status == 'timeout'
    assert failed.retrieved_ids
    report = summarize([warm, measured, failed])
    assert report['semantic_timeouts'] == 1
    assert report['query_requests'] == 2
    assert report['query_cache_hits'] == 1


@pytest.mark.asyncio
async def test_eval_uses_production_deadline_and_reports_empty_vectors(monkeypatch) -> None:
    """评估沿用生产三秒截止；空向量库不请求服务，不冒充语义成功。"""
    from unittest.mock import AsyncMock

    from homestay_bot.services.knowledge_embeddings import StoredVector, content_hash

    case = load_cases('calibration')[0]
    entry = _knowledge_entry(case['knowledge'][0])
    config = SemanticEvalConfig(AsyncMock(), 'test-model', 0.5)
    config.embedder.embed.return_value = [[1.0, 0.0]]
    store = AsyncMock()
    store.list_vectors.return_value = []
    ranker = EvaluatedRanker(config, store)
    assert await ranker.rank(Language.ZH, '停车', [entry]) == []
    assert ranker.status == 'no_vectors'
    assert ranker.requests == 0
    from homestay_bot.services.knowledge_embeddings import embedding_text

    store.list_vectors.return_value = [StoredVector(
        entry.id, 'zh', content_hash(embedding_text(entry, Language.ZH), 'test-model'),
        [1.0, 0.0],
    )]
    original = asyncio.wait_for
    deadlines = []

    async def checked_wait_for(awaitable, timeout):
        """捕获实际查询截止，同时执行原协程，不等待真实网络。"""
        deadlines.append(timeout)
        return await original(awaitable, timeout)

    monkeypatch.setattr(asyncio, 'wait_for', checked_wait_for)
    await ranker.rank(Language.ZH, '停车', [entry])
    assert deadlines == [3.0]


def test_eval_sdk_matches_production_transport(monkeypatch) -> None:
    """真实评估只能用受控 HTTPS transport、零重试；构造测试不联网。"""
    from unittest.mock import Mock

    import openai

    from homestay_bot.services import outbound_url_policy

    transport = Mock()
    builder = Mock(return_value=transport)
    sdk = Mock()
    monkeypatch.setenv('YUMI_EMBEDDING_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(outbound_url_policy, 'build_public_https_client', builder)
    monkeypatch.setattr(openai, 'AsyncOpenAI', sdk)
    config = _load_semantic_config(['--semantic', '--latency'])
    assert config is not None and config.query_embedder is not None
    assert sdk.call_args.kwargs['max_retries'] == 0
    assert sdk.call_args.kwargs['http_client'] is transport
    assert builder.call_args.kwargs['timeout_seconds'] == 30.0


async def _main(argv: list[str]) -> None:
    """整个评估共用一个事件循环；结束时关闭 SDK，避免连接泄漏。"""
    config = _load_semantic_config(argv)
    try:
        splits = [name for name in CASE_FILES
                  if "--only" not in argv or name == argv[argv.index("--only") + 1]]
        if config is not None:
            print("mode: uncached-query latency" if "--latency" in argv
                  else "mode: cached quality; latency is NOT production evidence")
        outcomes = []
        for name in splits:
            outcomes.extend(await evaluate_split(name, config))
        if "--record-baseline" in argv:
            _record_baseline(outcomes)
        if "--record-baseline-v2" in argv:
            _record_blind_baseline(outcomes)
        _print_report(outcomes, show_holdout="--show-holdout" in argv)
    finally:
        if config is not None and config.sdk is not None:
            await config.sdk.close()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv))
