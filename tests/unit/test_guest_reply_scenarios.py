"""共用虚构资料的离线校验：结构、引用完整性与个人信息扫描，不调用任何模型。

这份资料同时供离线契约测试和部署前真实模型回归使用，见
docs/specs/2026-09-25_reply-regression-gate-spec.md F1。
"""

import json
import re
from collections import Counter
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "guest_reply_scenarios.json"
# 回归运行器能够观察到的路由；期望路由不在其中时，判定永远无法通过。
KNOWN_ROUTES = {
    "knowledge",
    "stable_tourism",
    "live_search",
    "tool_availability",
    "tool_price",
    "tool_catalog",
    "handoff",
    "unrelated",
    "emergency",
    "complaint",
    "facility",
}
# 真实个人信息的典型形态：大陆手机号、身份证号、邮箱、网址。虚构资料里一律不该出现。
_PII_PATTERNS = {
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "身份证号": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "邮箱": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "网址": re.compile(r"https?://", re.IGNORECASE),
}


def _load() -> dict:
    """读取共用资料。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_declares_itself_fictional() -> None:
    """资料必须自带虚构声明，防止被当作真实房源事实导入。"""
    data = _load()

    assert "虚构" in data["_fictional"]
    assert data["today"] == "2026-09-25"


def test_scenario_ids_are_unique_and_routes_are_observable() -> None:
    """场景编号唯一；期望路由都是运行器能观察到的取值。"""
    scenarios = _load()["scenarios"]
    counts = Counter(item["id"] for item in scenarios)

    assert [key for key, count in counts.items() if count > 1] == []
    assert {item["expect"]["route"] for item in scenarios} <= KNOWN_ROUTES
    for item in scenarios:
        assert item["messages"][-1]["role"] == "user", item["id"]


def test_cited_knowledge_exists() -> None:
    """场景引用的知识编号都能在资料里找到。"""
    data = _load()
    knowledge_ids = {entry["id"] for entry in data["knowledge"]}

    missing = {
        item["id"]: sorted(set(item["expect"].get("source_ids", [])) - knowledge_ids)
        for item in data["scenarios"]
        if set(item["expect"].get("source_ids", [])) - knowledge_ids
    }
    assert missing == {}


def test_the_fixture_contains_no_real_personal_data() -> None:
    """全文不含手机号、身份证号、邮箱和网址。"""
    text = FIXTURE.read_text(encoding="utf-8")

    found = {name: pattern.findall(text)[:3] for name, pattern in _PII_PATTERNS.items()}
    assert {name: hits for name, hits in found.items() if hits} == {}
