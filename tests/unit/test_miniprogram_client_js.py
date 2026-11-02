"""在常规 pytest 门禁里运行小程序客户端（登录、住客自助内容与页面）的 Node 行为测试。"""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_miniprogram_auth_client_behaviour() -> None:
    """测试只加载客户端自己的 JS，不依赖 TDesign 等 npm 包，直接用 Node 内置测试运行。

    本机没有 Node 时跳过。
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("未安装 Node，无法运行小程序客户端测试")
    project_root = Path(__file__).resolve().parents[2]
    # 逐个列出测试文件：较旧的 Node 的 --test 不支持目录或通配参数。
    test_files = sorted(
        str(path.relative_to(project_root))
        for path in (project_root / "tests/miniprogram").glob("*.test.js")
    )
    assert test_files, "没有找到小程序客户端测试"
    result = subprocess.run(
        [node, "--test", *test_files],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
