"""发布脚本的静态守护：真实模型回归门禁不写死地址与密钥，失败必清理，改动范围判定不漏项。"""

import ast
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


# 门禁运行器会加载、但不影响回复正文的基础设施模块：改它们不必跑真实模型门禁。
_GATE_INFRASTRUCTURE = {
    "src/homestay_bot/__init__.py",
    "src/homestay_bot/config.py",
    "src/homestay_bot/db.py",
    "src/homestay_bot/worker.py",
    "src/homestay_bot/domain/enums.py",
    "src/homestay_bot/domain/errors.py",
    "src/homestay_bot/domain/models.py",
    "src/homestay_bot/domain/runtime_config.py",
    "src/homestay_bot/domain/schemas.py",
    "src/homestay_bot/domain/task_lifecycle.py",
    "src/homestay_bot/integrations/wecom/api_client.py",
    "src/homestay_bot/integrations/wecom/schemas.py",
    "src/homestay_bot/repositories/operations.py",
    "src/homestay_bot/repositories/runtime_config.py",
    # 员工转人工卡片与固定状态提示，不经过模型也不改写机器人回复（1.58.0）。
    "src/homestay_bot/services/handoff_card.py",
    "src/homestay_bot/services/runtime_config_cipher.py",
}


def _gate_import_closure() -> set[str]:
    """门禁运行器 reply_regression 的站内导入闭包（含函数内的延迟导入）。"""
    root = GATE.parents[2] / "src"

    def path_of(module: str) -> Path | None:
        for candidate in (
            root / f"{module.replace('.', '/')}.py",
            root / module.replace(".", "/") / "__init__.py",
        ):
            if candidate.exists():
                return candidate
        return None

    seen: set[str] = set()
    stack = ["homestay_bot.tools.reply_regression"]
    while stack:
        module = stack.pop()
        path = path_of(module)
        if module in seen or path is None:
            continue
        seen.add(module)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("homestay_bot"):
                stack.append(str(node.module))
                stack.extend(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Import):
                stack.extend(a.name for a in node.names if a.name.startswith("homestay_bot"))
    return {
        str(path.relative_to(GATE.parents[2]))
        for module in seen
        if (path := path_of(module)) is not None
    }


def test_gate_covers_every_module_that_shapes_replies() -> None:
    """门禁运行器加载的模块，除基础设施外都必须在触发清单里：新增回复模块不会被漏掉。"""
    block = re.search(r"REPLY_PATHS=\((.*?)\n\)", _text(), re.S)
    assert block is not None
    listed = {line.strip() for line in block.group(1).splitlines() if line.strip()}

    missing = _gate_import_closure() - _GATE_INFRASTRUCTURE - listed
    assert not missing, f"门禁触发清单漏了：{sorted(missing)}"
    for fixture in ("tests/fixtures/guest_reply_scenarios.json", "requirements.lock"):
        assert fixture in listed
    # 后台页面、仓储和应用装配不触发门禁。
    assert "src/homestay_bot/application.py" not in listed
    assert "pyproject.toml" in _text() and "version = " in _text()


def test_gate_tests_the_tagged_code_not_the_working_tree() -> None:
    """候选源码、资料和基线一律取自标签，保证测的就是要部署的内容。"""
    text = _text()

    assert 'git archive "$TAG" src/homestay_bot' in text
    assert 'git show "$TAG:tests/fixtures/guest_reply_scenarios.json"' in text
    assert 'git show "$TAG:tests/fixtures/guest_reply_regression_baseline.json"' in text
