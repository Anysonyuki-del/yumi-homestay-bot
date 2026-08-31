import re
from pathlib import Path


def _locked_requirement_blocks(content: str) -> list[str]:
    """提取锁文件中的依赖块，忽略文件头和生成器注释。"""
    return [
        block
        for block in re.split(r"\n(?=[A-Za-z0-9])", content)
        if block and not block.startswith("#")
    ]


def test_api_port_is_only_published_on_loopback() -> None:
    """生产入口必须经由 Nginx，禁止 Docker 直接公开 HTTP 端口。"""
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8000:8000"' in compose
    assert '\n      - "8000:8000"' not in compose


def test_private_uploads_use_one_persistent_container_path() -> None:
    """API 私有上传必须落到可配置宿主机目录，不能留在容器可写层。"""
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "PRIVATE_UPLOAD_DIR: /app/data/private_uploads" in compose
    assert (
        '"${PRIVATE_UPLOAD_HOST_DIR:-./data/private_uploads}:'
        '/app/data/private_uploads"'
    ) in compose


def test_python_base_image_is_pinned_to_digest() -> None:
    """基础镜像必须固定到完整 digest，避免同一标签产生不同运行制品。"""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)


def test_dependency_lock_uses_exact_versions_and_hashes() -> None:
    """生产依赖锁必须同时固定版本和制品哈希，且不得声明可信主机。"""
    lock_path = Path("requirements.lock")

    assert lock_path.is_file()
    content = lock_path.read_text(encoding="utf-8")
    blocks = _locked_requirement_blocks(content)

    assert blocks
    assert "--trusted-host" not in content
    for block in blocks:
        first_line = block.splitlines()[0]
        assert "==" in first_line, f"依赖没有固定版本: {first_line}"
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), f"依赖没有哈希: {first_line}"


def test_cryptography_constraint_excludes_known_vulnerable_series() -> None:
    """加密依赖源约束必须排除已命中安全公告的 45.x 及更早版本。"""
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"cryptography>=50.0.1,<51"' in pyproject


def test_docker_build_installs_the_hash_lock_over_tls() -> None:
    """生产镜像只能从 TLS 索引按哈希锁安装，并禁止重新解析项目依赖。"""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG PIP_INDEX_URL=https://pypi.org/simple" in dockerfile
    assert "PIP_TRUSTED_HOST" not in dockerfile
    assert "--trusted-host" not in dockerfile
    assert "COPY requirements.lock ./" in dockerfile
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert "--no-deps --no-build-isolation ." in dockerfile


def test_api_image_declares_a_non_root_runtime_user() -> None:
    """API 运行阶段必须显式切换到固定的非 root UID/GID。"""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^USER ([0-9]+):([0-9]+)$", dockerfile, re.MULTILINE)

    assert match is not None
    assert match.groups() != ("0", "0")


def test_dockerignore_excludes_sensitive_and_local_state() -> None:
    """构建上下文不得包含密钥、版本库、运行数据、备份或受保护总结。"""
    ignore_path = Path(".dockerignore")

    assert ignore_path.is_file()
    patterns = {
        line.strip()
        for line in ignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required_patterns = {
        ".env",
        ".env.*",
        ".git",
        ".venv",
        "data",
        ".backups",
        "backups",
        ".worktrees",
        "YuMi民宿AI项目总结.txt",
    }

    assert required_patterns <= patterns


def test_ci_has_minimal_permissions_and_pinned_actions() -> None:
    """最小 CI 必须使用只读权限、固定 Action SHA 并关闭真实外联测试。"""
    workflow_path = Path(".github/workflows/ci.yml")

    assert workflow_path.is_file()
    workflow = workflow_path.read_text(encoding="utf-8")
    action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)

    assert re.search(r"permissions:\s*\n\s+contents:\s+read", workflow)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert "RUN_LIVE_CONTRACT_TESTS: \"0\"" in workflow
    assert "python -m ruff check ." in workflow
    assert "python -m mypy" in workflow
    assert "python -m pytest" in workflow
    assert "alembic upgrade head" in workflow
    assert "docker build" in workflow
