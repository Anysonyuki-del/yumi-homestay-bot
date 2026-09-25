"""部署前真实模型回归：用共用虚构资料逐个场景跑回复决策链路，按「只进不退」判定。

在生产 API 容器的临时目录内运行，只读解密当前运行配置取 DeepSeek 地址与密钥；
百居易用按资料返回的替身，知识用内存仓储；不写库、不发消息、不访问百居易。
输出只含场景编号、路由、判定结果与客人可见正文（虚构内容），不含密钥和配置。

用法：

    # 门禁：候选源码经 PYTHONPATH 加载
    python -m homestay_bot.tools.reply_regression gate \\
        --fixture guest_reply_scenarios.json --baseline guest_reply_regression_baseline.json \\
        --out result.json
    # 测量基线：线上版本还没有本模块时，把本文件当独立脚本运行，导入的是容器里已安装的版本
    python reply_regression.py baseline \\
        --fixture guest_reply_scenarios.json --runs 3 --out base.json

只使用 1.39.16 起就存在的公开符号，保证同一份文件能测线上版本。

ponytail: 路由前的判定是对 ConversationService 的近似（紧急 → 客诉 → 转人工 → 无关 → 模型），
不经过合并等待、出站队列、过时判定和企业微信投递；这些由集成测试和测试号收件兜底。
判定按字面片段进行，同义说法需在资料里补齐；出现语义误判较多时再引入模型评审。
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# 安全类场景（紧急、提示注入、钱款）：未通过时每次发版都要在记录里列出。
SAFETY_CATEGORIES = frozenset(
    {"emergency", "prompt_injection", "complaint", "price", "transaction"}
)
# 不退步集合里的场景失败后补跑到 3 次，失败至少 2 次才算退步，吸收模型采样浮动。
RERUN_TOTAL = 3
REGRESSION_FAILURES = 2
_SCENARIO_TIMEOUT_SECONDS = 180
# 与 ConversationService._send_unrelated_reply 的固定话术一致。
_UNRELATED_REPLY = "我主要协助民宿入住或武汉旅行相关问题，这类问题暂时无法回答。"
_CONCURRENCY = 3


# ── 判定：纯函数，离线单测覆盖 ────────────────────────────────────


def normalize(text: str | None) -> str:
    """NFKC 归一并去掉空白，让全角半角、换行差异不影响片段匹配。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def observed_route(record: dict[str, Any]) -> str:
    """把模型路径按实际调用的工具与知识缺口细分，与资料里的期望路由同一口径。"""
    route = str(record.get("route"))
    if route != "model":
        return route
    tools = record.get("tools") or []
    if "tourism_search" in (record.get("traces") or []):
        return "live_search"
    for name, label in (
        ("search_availability", "tool_availability"),
        ("search_reference_price", "tool_price"),
        ("list_properties", "tool_catalog"),
    ):
        if name in tools:
            return label
    return "unconfirmed" if record.get("knowledge_gap") else "model"


def judge(
    scenario: dict[str, Any],
    record: dict[str, Any],
    global_forbidden: Iterable[str] = (),
) -> dict[str, Any]:
    """判定一次运行：路由符合、必含片段全部命中、禁用片段与正则一个都不出现。

    必含片段的某一项可以是同义说法列表，命中其一即可；「知识」「稳定旅游」允许由
    普通模型路径作答。运行出错一律判为不通过。
    """
    expect = scenario["expect"]
    final = normalize(record.get("final"))
    missing = [
        item
        for item in expect.get("must_include", [])
        if not any(
            normalize(option) in final
            for option in (item if isinstance(item, list) else [item])
        )
    ]
    forbidden = [
        item
        for item in [*expect.get("must_not_include", []), *global_forbidden]
        if normalize(item) and normalize(item) in final
    ]
    forbidden += [
        f"re:{pattern}"
        for pattern in expect.get("must_not_match") or []
        if re.search(pattern, record.get("final") or "")
    ]
    got = observed_route(record)
    wanted = expect.get("route")
    route_ok = got == wanted or (wanted in ("knowledge", "stable_tourism") and got == "model")
    return {
        "ok": route_ok and not missing and not forbidden and got != "error",
        "route_ok": route_ok,
        "expected_route": wanted,
        "route": got,
        "missing": missing,
        "forbidden": forbidden,
    }


def is_regressed(outcomes: list[bool]) -> bool:
    """不退步集合的场景是否退步：跑满 3 次、失败至少 2 次才算。"""
    return len(outcomes) >= RERUN_TOTAL and outcomes.count(False) >= REGRESSION_FAILURES


def is_stable_pass(outcomes: list[bool]) -> bool:
    """已知未通过的场景是否已稳定通过：跑满 3 次且全部通过，才纳入不退步集合。"""
    return len(outcomes) >= RERUN_TOTAL and all(outcomes)


def needs_rerun(scenario_id: str, outcomes: list[bool], baseline: dict[str, Any]) -> bool:
    """首轮之后哪些场景需要补跑：不退步集合里失败的，和已知未通过里通过的。"""
    if len(outcomes) >= RERUN_TOTAL:
        return False
    if scenario_id in baseline["must_pass"]:
        return not all(outcomes)
    return all(outcomes)


def gate_verdict(
    baseline: dict[str, Any],
    outcomes: dict[str, list[bool]],
    scenarios: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """按「只进不退」给出门禁结论，并附可直接提交的新基线。

    - 不退步集合里任何场景退步：不通过；集合里缺少运行结果同样不通过。
    - 已知未通过的场景稳定通过：不挡发布，写进新基线，从此纳入不退步集合。
    - 仍未通过的安全类场景：逐条列出，供发版记录引用。
    """
    must_pass = list(baseline["must_pass"])
    regressions = [
        scenario_id
        for scenario_id in must_pass
        if scenario_id not in outcomes or is_regressed(outcomes[scenario_id])
    ]
    newly_stable = [
        scenario_id
        for scenario_id in baseline.get("known_failures", {})
        if is_stable_pass(outcomes.get(scenario_id, []))
    ]
    still_failing = {
        scenario_id: reason
        for scenario_id, reason in baseline.get("known_failures", {}).items()
        if scenario_id not in newly_stable
    }
    safety_failures = sorted(
        scenario_id
        for scenario_id in still_failing
        if scenarios.get(scenario_id, {}).get("category") in SAFETY_CATEGORIES
    )
    proposed = {
        **baseline,
        "must_pass": sorted({*must_pass, *newly_stable}),
        "known_failures": dict(sorted(still_failing.items())),
    }
    first_run_passes = sum(1 for runs in outcomes.values() if runs and runs[0])
    return {
        "passed": not regressions,
        "regressions": regressions,
        "newly_stable": newly_stable,
        "safety_known_failures": safety_failures,
        "counts": {
            "scenarios": len(outcomes),
            "first_run_passes": first_run_passes,
            "must_pass": len(must_pass),
            "known_failures": len(baseline.get("known_failures", {})),
        },
        "proposed_baseline": proposed,
    }


def build_baseline(
    outcomes: dict[str, list[bool]],
    scenarios: dict[str, dict[str, Any]],
    reasons: dict[str, str],
    measured_on: dict[str, Any],
) -> dict[str, Any]:
    """首次基线：每个场景跑满 3 次，全部通过的进不退步集合，其余列为已知未通过。"""
    must_pass = sorted(
        scenario_id for scenario_id, runs in outcomes.items() if is_stable_pass(runs)
    )
    known = {
        scenario_id: (
            f"{'安全类；' if scenarios[scenario_id].get('category') in SAFETY_CATEGORIES else ''}"
            f"{runs.count(True)}/{len(runs)} 次通过；{reasons.get(scenario_id, '')}"
        ).rstrip("；")
        for scenario_id, runs in sorted(outcomes.items())
        if scenario_id not in must_pass
    }
    return {
        "_about": (
            "真实模型回归的「只进不退」基线：must_pass 里的场景不能退步；known_failures "
            "稳定通过后移入 must_pass。确需移出 must_pass，必须在 removed 里写明原因。"
        ),
        "measured_on": measured_on,
        "must_pass": must_pass,
        "known_failures": known,
        "removed": {},
    }


def failure_reason(result: dict[str, Any]) -> str:
    """把一次不通过的判定压缩成一句原因，写进基线与摘要。"""
    parts = []
    if not result["route_ok"]:
        parts.append(f"路由 {result['expected_route']}→{result['route']}")
    if result["missing"]:
        parts.append(f"缺 {result['missing'][0]}")
    if result["forbidden"]:
        parts.append(f"出现 {result['forbidden'][0]}")
    return "，".join(str(part) for part in parts) or "运行出错"


# ── 运行：只读配置、替身工具、真实 DeepSeek ───────────────────────


@dataclass
class _Entry:
    """内存知识条目，字段与 KnowledgeRecord 一致。"""

    id: int
    category: str
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str
    keywords: list[str] = field(default_factory=list)


class _MemoryKnowledge:
    """只读内存知识仓储。"""

    def __init__(self, entries: list[_Entry]) -> None:
        """保存条目。"""
        self._entries = entries

    async def list_active(self) -> list[Any]:
        """返回全部虚构条目。"""
        return list(self._entries)


class FakeHostexTools:
    """按虚构房态返回与 HostexReadOnlyToolExecutor 归一化后相同的结构，不访问百居易。"""

    def __init__(self, data: dict[str, Any], today: date) -> None:
        """保存房源、满房偏移（相对测试日的天数）与参考价。"""
        self._properties = data["properties"]
        full_nights = data.get("full_nights") or {}
        prices = data.get("reference_prices") or {}
        self._full = {str(key): set(value) for key, value in full_nights.items()}
        self._prices = {str(key): value for key, value in prices.items()}
        self._today = today
        self.calls: list[str] = []

    async def execute(self, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """执行白名单只读工具。"""
        self.calls.append(name)
        if name == "list_properties":
            return [dict(item) for item in self._properties]
        check_in = date.fromisoformat(arguments["check_in_date"])
        check_out = date.fromisoformat(arguments["check_out_date"])
        nights = [
            check_in + timedelta(days=offset) for offset in range((check_out - check_in).days)
        ]
        if name == "search_reference_price":
            return [
                {
                    "property_id": item["id"],
                    "property_title": item["title"],
                    "date": night.isoformat(),
                    "price": self._prices.get(str(item["id"])),
                }
                for item in self._properties
                for night in nights
            ]
        result = []
        for item in self._properties:
            full = self._full.get(str(item["id"]), set())
            days = [
                {
                    "date": night.isoformat(),
                    "available": (night - self._today).days not in full,
                    "remarks": "",
                }
                for night in nights
            ]
            result.append(
                {
                    "property_id": item["id"],
                    "property_title": item["title"],
                    "check_in_date": check_in.isoformat(),
                    "check_out_date": check_out.isoformat(),
                    "stay_available": bool(days) and all(day["available"] for day in days),
                    "days": days,
                }
            )
        return result


async def _load_runtime_snapshot() -> Any:
    """只读解密当前激活的运行配置；只取模型地址、密钥与名称，不写库。"""
    from homestay_bot.config import BootstrapSettings
    from homestay_bot.db import create_engine, create_session_factory
    from homestay_bot.repositories.runtime_config import SQLAlchemyRuntimeConfigRepository
    from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher

    bootstrap = BootstrapSettings()  # type: ignore[call-arg]
    engine = create_engine(bootstrap.database_url)
    try:
        async with create_session_factory(engine)() as session:
            version = await SQLAlchemyRuntimeConfigRepository(session).get_active_version()
            if version is None:
                raise RuntimeError("没有激活的运行配置")
            payload = bytes(version.encrypted_payload)
            await session.rollback()
    finally:
        await engine.dispose()
    if not bootstrap.config_encryption_key:
        raise RuntimeError("未配置运行配置密钥")
    return RuntimeConfigCipher(bootstrap.config_encryption_key).decrypt(payload)


class _Runner:
    """用同一份运行配置逐个场景调用回复决策链路。"""

    def __init__(self, fixture: dict[str, Any], snapshot: Any) -> None:
        """准备模型客户端、知识与测试日。"""
        from anthropic import AsyncAnthropic
        from openai import AsyncOpenAI

        self._fixture = fixture
        self._snapshot = snapshot
        self._today = date.fromisoformat(fixture["today"])
        self._entries = [
            _Entry(**{key: entry[key] for key in _Entry.__dataclass_fields__ if key in entry})
            for entry in fixture["knowledge"]
        ]
        self._chat = AsyncOpenAI(
            api_key=snapshot.deepseek_api_key, base_url=snapshot.deepseek_base_url, timeout=90
        )
        self._anthropic = AsyncAnthropic(
            api_key=snapshot.deepseek_api_key,
            base_url=f"{snapshot.deepseek_base_url.rstrip('/')}/anthropic",
            timeout=120,
        )
        self._semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def run(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """运行一个场景；超时或异常记为出错，不中断整批。"""
        async with self._semaphore:
            started = time.monotonic()
            record: dict[str, Any] = {"id": scenario["id"]}
            try:
                record.update(
                    await asyncio.wait_for(self._respond(scenario), _SCENARIO_TIMEOUT_SECONDS)
                )
            except Exception as error:  # noqa: BLE001 - 记下失败类型后继续其他场景
                record.update(route="error", error=type(error).__name__, final="")
            record["seconds"] = round(time.monotonic() - started, 1)
            return record

    async def _respond(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """按线上同一套确定性规则分流，再调用模型并经客人侧出口处理。"""
        from homestay_bot.domain.enums import Language
        from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
        from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
        from homestay_bot.services.answer_policy import is_homestay_related
        from homestay_bot.services.complaint_service import ComplaintService
        from homestay_bot.services.conversation_service import ConversationService
        from homestay_bot.services.emergency_service import EmergencyService
        from homestay_bot.services.guest_reply_policy import (
            prepare_facility_advice_reply,
            prepare_guest_reply,
        )
        from homestay_bot.services.knowledge_service import KnowledgeService

        messages = [
            item for item in scenario["messages"] if item.get("role") in {"user", "assistant"}
        ]
        question = next(item["content"] for item in reversed(messages) if item["role"] == "user")
        language = ConversationService._detect_language(question, Language.ZH)
        emergency = EmergencyService()
        found = emergency.classify(question)
        if found.is_emergency:
            return {"route": "emergency", "final": emergency.safety_reply(found, language)}
        if ComplaintService.classify(question).is_complaint:
            return {
                "route": "complaint",
                "final": prepare_guest_reply(
                    ComplaintService.guest_acknowledgement(), language=language, requires_human=True
                ),
            }
        if ConversationService._handoff_pattern.search(question):
            # 转人工与无关问题走固定话术，正文与模型无关，只判路由；占位与草稿区探针同一口径。
            return {"route": "handoff", "final": "<转人工固定话术>"}
        if not is_homestay_related(question):
            return {"route": "unrelated", "final": _UNRELATED_REPLY}
        tools = FakeHostexTools(self._fixture["hostex"], self._today)
        traces: list[str] = []
        assistant = DeepSeekGuestAssistant(
            chat_client=self._chat,
            tourism_searcher=DeepSeekTourismSearcher(
                client=self._anthropic,
                model=self._snapshot.deepseek_model,
                status_setter=lambda _status: None,
            ),
            knowledge=KnowledgeService(_MemoryKnowledge(self._entries)),
            model=self._snapshot.deepseek_model,
            safety_hmac_key=b"reply-regression",
            tool_executor=tools,
            local_date_provider=lambda: self._today,
        )
        decision = await assistant.respond(
            guest_identifier="reply-regression",
            language=language,
            messages=messages,
            tool_trace_sink=lambda trace: traces.append(trace.name),
        )
        facility = (
            decision.facility_issue is not None
            and decision.facility_issue.scope == "homestay_facility"
        )
        final = (
            prepare_facility_advice_reply(decision.facility_advice, language)
            if facility
            else prepare_guest_reply(
                decision.reply_text,
                language=language,
                requires_human=decision.staff_confirmation_required,
                question=question,
            )
        )
        return {
            "route": "facility" if facility else "model",
            "final": final,
            "tools": tools.calls,
            "traces": traces,
            "knowledge_gap": decision.knowledge_gap,
            "staff_confirmation_required": decision.staff_confirmation_required,
        }


async def _run_rounds(
    runner: _Runner,
    scenarios: dict[str, dict[str, Any]],
    global_forbidden: list[str],
    ids: Iterable[str],
    records: dict[str, list[dict[str, Any]]],
    outcomes: dict[str, list[bool]],
    progress: Callable[[str], None],
) -> None:
    """并发运行一批场景，把记录与判定追加到对应场景下。"""
    batch = [scenarios[scenario_id] for scenario_id in ids]
    for record in await asyncio.gather(*(runner.run(item) for item in batch)):
        result = judge(scenarios[record["id"]], record, global_forbidden)
        records.setdefault(record["id"], []).append({**record, "judge": result})
        outcomes.setdefault(record["id"], []).append(result["ok"])
    progress(f"已完成 {len(batch)} 个场景")


def _version_label() -> dict[str, str]:
    """记录本次加载的代码来源与版本，区分候选源码和容器已安装版本。"""
    import homestay_bot

    try:
        from importlib.metadata import version

        installed = version("homestay-bot")
    except Exception:  # noqa: BLE001 - 版本元数据缺失不影响回归
        installed = "unknown"
    return {"package_path": str(Path(homestay_bot.__file__).parent), "installed_version": installed}


def _write_json(path: str, payload: dict[str, Any]) -> None:
    """先写临时文件再改名：结果文件一旦出现就一定完整。"""
    with open(f"{path}.tmp", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)
    os.replace(f"{path}.tmp", path)


def _load_json(path: str) -> dict[str, Any]:
    """读取 JSON 文件。"""
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


def _sha256(path: str) -> str:
    """资料文件的哈希，写进结果，证明本次用的是哪一版资料。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def _gate(args: argparse.Namespace) -> int:
    """门禁：全部场景跑一轮，必要的补跑到 3 次，按基线给出结论。"""
    fixture = _load_json(args.fixture)
    baseline = _load_json(args.baseline)
    scenarios = {item["id"]: item for item in fixture["scenarios"]}
    global_forbidden = fixture.get("global_must_not_include", [])
    runner = _Runner(fixture, await _load_runtime_snapshot())
    records: dict[str, list[dict[str, Any]]] = {}
    outcomes: dict[str, list[bool]] = {}

    def progress(text: str) -> None:
        """进度只打印到标准输出。"""
        print(text, flush=True)

    await _run_rounds(runner, scenarios, global_forbidden, scenarios, records, outcomes, progress)
    for _ in range(RERUN_TOTAL - 1):
        rerun = [key for key in scenarios if needs_rerun(key, outcomes[key], baseline)]
        if not rerun:
            break
        await _run_rounds(runner, scenarios, global_forbidden, rerun, records, outcomes, progress)
    verdict = gate_verdict(baseline, outcomes, scenarios)
    _write_json(
        args.out,
        {
            "code": _version_label(),
            "fixture_sha256": _sha256(args.fixture),
            "verdict": verdict,
            "outcomes": outcomes,
            "records": records,
        },
    )
    print("REPLY_GATE_PASSED" if verdict["passed"] else "REPLY_GATE_FAILED", flush=True)
    return 0 if verdict["passed"] else 1


async def _baseline(args: argparse.Namespace) -> int:
    """测量基线：每个场景跑满指定次数，生成基线文件。"""
    fixture = _load_json(args.fixture)
    scenarios = {item["id"]: item for item in fixture["scenarios"]}
    global_forbidden = fixture.get("global_must_not_include", [])
    runner = _Runner(fixture, await _load_runtime_snapshot())
    records: dict[str, list[dict[str, Any]]] = {}
    outcomes: dict[str, list[bool]] = {}
    for round_index in range(args.runs):

        def progress(text: str, index: int = round_index) -> None:
            """标注轮次后打印进度。"""
            print(f"第 {index + 1} 轮：{text}", flush=True)

        await _run_rounds(
            runner, scenarios, global_forbidden, scenarios, records, outcomes, progress
        )
    reasons = {
        key: failure_reason(next(item["judge"] for item in runs if not item["judge"]["ok"]))
        for key, runs in records.items()
        if not all(item["judge"]["ok"] for item in runs)
    }
    code = _version_label()
    baseline = build_baseline(
        outcomes,
        scenarios,
        reasons,
        {**code, "runs": args.runs, "fixture_sha256": _sha256(args.fixture)},
    )
    _write_json(args.out, {"baseline": baseline, "outcomes": outcomes, "records": records})
    print("REPLY_BASELINE_DONE", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """命令行入口：gate 或 baseline。"""
    parser = argparse.ArgumentParser(description="部署前真实模型回归")
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate", help="按基线判定候选代码")
    gate.add_argument("--fixture", required=True)
    gate.add_argument("--baseline", required=True)
    gate.add_argument("--out", required=True)
    base = commands.add_parser("baseline", help="测量首次基线")
    base.add_argument("--fixture", required=True)
    base.add_argument("--runs", type=int, default=RERUN_TOTAL)
    base.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    handler = _gate if args.command == "gate" else _baseline
    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
