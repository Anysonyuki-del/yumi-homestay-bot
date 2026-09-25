"""发布脚本的静态守护：真实模型回归门禁不写死地址与密钥，失败必清理，改动范围判定不漏项。"""

import re
import subprocess
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "scripts" / "release" / "reply_gate.sh"


def _text() -> str:
    """读取门禁脚本。"""
    return GATE.read_text(encoding="utf-8")


def test_gate_script_is_strict_and_always_cleans_up() -> None:
    """严格模式运行；临时目录无论成败都经 trap 清理。"""
    text = _text()

    assert "set -euo pipefail" in text
    assert re.search(r"trap cleanup EXIT", text)
    assert "rm -rf" in text
    assert subprocess.run(["bash", "-n", str(GATE)], check=False).returncode == 0


def test_gate_script_embeds_no_server_address_or_key() -> None:
    """服务器地址和密钥只能由调用方传入：仓库是公开的。"""
    text = _text()

    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"root@|\.ssh/|BEGIN [A-Z ]*PRIVATE KEY", text)
    assert '"${DEPLOY_HOST:?' in text and '"${DEPLOY_KEY:?' in text


def test_gate_covers_every_reply_chain_path() -> None:
    """改动范围判定覆盖回复链路的全部入口；版本号变化不算依赖改动。"""
    text = _text()

    for path in (
        "src/homestay_bot/integrations",
        "src/homestay_bot/services",
        "src/homestay_bot/application.py",
        "src/homestay_bot/tools/reply_regression.py",
        "tests/fixtures/guest_reply_scenarios.json",
        "tests/fixtures/guest_reply_regression_baseline.json",
        "requirements.lock",
    ):
        assert path in text, path
    assert "pyproject.toml" in text and "version = " in text


def test_gate_tests_the_tagged_code_not_the_working_tree() -> None:
    """候选源码、资料和基线一律取自标签，保证测的就是要部署的内容。"""
    text = _text()

    assert 'git archive "$TAG" src/homestay_bot' in text
    assert 'git show "$TAG:tests/fixtures/guest_reply_scenarios.json"' in text
    assert 'git show "$TAG:tests/fixtures/guest_reply_regression_baseline.json"' in text
