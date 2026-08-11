import os
import sqlite3
import subprocess
from pathlib import Path


def test_postgresql_offline_upgrade_sql_reaches_head() -> None:
    """PostgreSQL 离线迁移 SQL 必须完整生成到当前唯一迁移头。"""
    project_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["DATABASE_URL"] = "postgresql+asyncpg://offline:offline@localhost/offline"

    result = subprocess.run(
        [str(project_root / ".venv/bin/alembic"), "upgrade", "head", "--sql"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "0016_admin_dashboard_indexes" in result.stdout
    assert "admin_credentials" in result.stdout
    assert "admin_csrf_nonces" in result.stdout
    assert "admin_csrf_quota" in result.stdout
    assert "runtime_config_versions" in result.stdout
    assert "runtime_config_state" in result.stdout
    assert "ck_admin_credentials_singleton" in result.stdout
    assert "ck_admin_csrf_quota_singleton" in result.stdout
    assert "ck_admin_csrf_quota_nonnegative" in result.stdout
    assert "ck_runtime_config_state_singleton" in result.stdout
    assert "CREATE UNIQUE INDEX ix_admin_credentials_username" in result.stdout
    assert "CREATE UNIQUE INDEX ix_admin_csrf_nonces_token_hash" in result.stdout
    assert "CREATE INDEX ix_admin_csrf_nonces_expires_at" in result.stdout
    assert "CREATE INDEX ix_stay_orders_check_in_status" in result.stdout
    assert "CREATE INDEX ix_stay_orders_check_out_status" in result.stdout
    assert "FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE CASCADE" in (result.stdout)


def test_sqlite_admin_migrations_replay_through_runtime_config_lifecycle(
    tmp_path: Path,
) -> None:
    """真实 SQLite 必须支持升级、逐级降级 0017 后再次升级。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "migration-replay.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    def run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        """在隔离 SQLite 数据库运行一次真实 Alembic 命令。"""
        return subprocess.run(
            [alembic, *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    upgrade = run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    with sqlite3.connect(database_path) as connection:
        tables_after_upgrade = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        admin_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("admin_credentials",),
        ).fetchone()[0]
        state_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("runtime_config_state",),
        ).fetchone()[0]
        version_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("runtime_config_versions",),
        ).fetchone()[0]
        state_row = connection.execute(
            "SELECT active_version_id, previous_version_id, revision "
            "FROM runtime_config_state WHERE id = 1"
        ).fetchone()
        quota_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("admin_csrf_quota",),
        ).fetchone()[0]
        quota_count = connection.execute(
            "SELECT active_count FROM admin_csrf_quota WHERE id = 1"
        ).fetchone()[0]
        admin_indexes = connection.execute("PRAGMA index_list('admin_credentials')").fetchall()
        csrf_indexes = connection.execute("PRAGMA index_list('admin_csrf_nonces')").fetchall()
        stay_indexes = connection.execute("PRAGMA index_list('stay_orders')").fetchall()
        csrf_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('admin_csrf_nonces')"
        ).fetchall()
        admin_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('admin_credentials')"
        ).fetchall()
        state_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('runtime_config_state')"
        ).fetchall()
    assert {
        "admin_credentials",
        "admin_csrf_nonces",
        "admin_csrf_quota",
        "runtime_config_versions",
        "runtime_config_state",
    } <= tables_after_upgrade
    assert "ck_admin_credentials_singleton" in admin_ddl
    assert "ck_admin_csrf_quota_singleton" in quota_ddl
    assert "ck_admin_csrf_quota_nonnegative" in quota_ddl
    assert quota_count == 0
    assert "ck_runtime_config_state_singleton" in state_ddl
    assert "ck_runtime_config_state_revision_nonnegative" in state_ddl
    assert "ck_runtime_config_state_distinct_pointers" in state_ddl
    assert "fk_runtime_config_versions_based_on" in version_ddl
    assert state_row == (None, None, 0)
    assert any(row[1] == "ix_admin_credentials_username" and row[2] for row in admin_indexes)
    assert any(row[1] == "ix_admin_csrf_nonces_token_hash" and row[2] for row in csrf_indexes)
    assert any(row[1] == "ix_admin_csrf_nonces_expires_at" for row in csrf_indexes)
    assert any(row[1] == "ix_stay_orders_check_in_status" for row in stay_indexes)
    assert any(row[1] == "ix_stay_orders_check_out_status" for row in stay_indexes)
    assert any(row[2] == "employees" and row[3] == "employee_id" for row in admin_foreign_keys)
    assert any(row[2] == "admin_credentials" and row[3] == "admin_id" for row in csrf_foreign_keys)
    assert sum(row[2] == "runtime_config_versions" for row in state_foreign_keys) == 2

    lifecycle_downgrade = run_alembic(
        "downgrade",
        "0016_admin_dashboard_indexes",
    )
    assert lifecycle_downgrade.returncode == 0, lifecycle_downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('runtime_config_versions')")
        }
    assert "status" not in version_columns
    assert "based_on_revision" not in version_columns

    dashboard_downgrade = run_alembic("downgrade", "0015_admin_runtime_config")
    assert dashboard_downgrade.returncode == 0, dashboard_downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        stay_indexes_after_dashboard_downgrade = connection.execute(
            "PRAGMA index_list('stay_orders')"
        ).fetchall()
    assert not any(
        row[1] in {"ix_stay_orders_check_in_status", "ix_stay_orders_check_out_status"}
        for row in stay_indexes_after_dashboard_downgrade
    )

    downgrade = run_alembic("downgrade", "0014_query_indexes")
    assert downgrade.returncode == 0, downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        tables_after_downgrade = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "admin_credentials" not in tables_after_downgrade
    assert "admin_csrf_nonces" not in tables_after_downgrade
    assert "admin_csrf_quota" not in tables_after_downgrade
    assert "runtime_config_versions" not in tables_after_downgrade
    assert "runtime_config_state" not in tables_after_downgrade

    second_upgrade = run_alembic("upgrade", "head")
    assert second_upgrade.returncode == 0, second_upgrade.stderr
    current = run_alembic("current")
    assert current.returncode == 0, current.stderr
    assert "0017_runtime_config_lifecycle (head)" in current.stdout


def test_runtime_config_lifecycle_backfills_existing_versions_with_orm_enum_name(
    tmp_path: Path,
) -> None:
    """0017 必须用 SQLAlchemy Enum 可读取的大写成员名回填既有版本。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "runtime-config-existing-row.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    before = subprocess.run(
        [alembic, "upgrade", "0016_admin_dashboard_indexes"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO runtime_config_versions "
            "(encrypted_payload, masked_summary, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (b"legacy-cipher", "{}"),
        )
        connection.commit()

    after = subprocess.run(
        [alembic, "upgrade", "head"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert after.returncode == 0, after.stderr
    with sqlite3.connect(database_path) as connection:
        status_value = connection.execute(
            "SELECT status FROM runtime_config_versions LIMIT 1"
        ).fetchone()[0]

    assert status_value == "CANDIDATE"
