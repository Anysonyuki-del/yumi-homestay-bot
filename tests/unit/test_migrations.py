import os
import subprocess
from pathlib import Path


def test_postgresql_offline_upgrade_sql_reaches_head() -> None:
    """PostgreSQL 离线迁移 SQL 必须完整生成到当前唯一迁移头。"""
    project_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["DATABASE_URL"] = (
        "postgresql+asyncpg://offline:offline@localhost/offline"
    )

    result = subprocess.run(
        [str(project_root / ".venv/bin/alembic"), "upgrade", "head", "--sql"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "0015_admin_runtime_config" in result.stdout
    assert "admin_credentials" in result.stdout
    assert "runtime_config_versions" in result.stdout
    assert "runtime_config_state" in result.stdout
