"""在常规 pytest 门禁里运行小程序登录客户端的 Node 行为测试。"""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_miniprogram_auth_client_behaviour() -> None:
    """小程序客户端没有 npm 依赖，直接用 Node 内置测试运行；本机没有 Node 时跳过。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("未安装 Node，无法运行小程序客户端测试")
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, "--test", "tests/miniprogram/auth.test.js"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
