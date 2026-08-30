import os
import sqlite3
import subprocess
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_revision_ids_fit_alembic_version_column() -> None:
    """所有迁移编号必须适配 Alembic 默认的 32 字符版本字段。"""
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    revisions = tuple(ScriptDirectory.from_config(config).walk_revisions())

    oversized = tuple(
        revision.revision
        for revision in revisions
        if len(revision.revision) > 32
    )
    assert not oversized, f"迁移编号超过 32 字符：{oversized}"


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
    assert "checkout_observed_on" in result.stdout
    assert "0020_memory_trust_timeline" in result.stdout
    assert "0021_task_lifecycle" in result.stdout
    assert "origin_kind" in result.stdout
    assert "closure_reason_code" in result.stdout
    assert "ix_business_tasks_status_expires_at" in result.stdout
    assert "0022_approval_pii" in result.stdout
    assert "guest_name_ciphertext" in result.stdout
    assert "guest_mobile_ciphertext" in result.stdout
    assert "special_requests_ciphertext" in result.stdout
    assert "pii_purged_at" in result.stdout
    assert "0023_approval_pii_final" in result.stdout
    assert "审批敏感资料密文回填未完成" in result.stdout
    assert "DROP COLUMN guest_name" in result.stdout
    assert "DROP COLUMN guest_mobile" in result.stdout
    assert "DROP COLUMN special_requests" in result.stdout
    assert "customer_memory_events" in result.stdout
    assert "source_excerpt_hash" in result.stdout
    assert "uq_customer_memory_active_subject" in result.stdout
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

    upgrade = run_alembic("upgrade", "0022_approval_pii")
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
        memory_indexes = connection.execute(
            "PRAGMA index_list('customer_memory_items')"
        ).fetchall()
        memory_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('customer_memory_items')")
        }
        memory_event_indexes = connection.execute(
            "PRAGMA index_list('customer_memory_events')"
        ).fetchall()
        stay_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('stay_orders')")
        }
        message_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('messages')")
        }
        business_task_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("business_tasks",),
        ).fetchone()[0]
        business_task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('business_tasks')")
        }
        business_task_indexes = connection.execute(
            "PRAGMA index_list('business_tasks')"
        ).fetchall()
        business_task_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('business_tasks')"
        ).fetchall()
        csrf_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('admin_csrf_nonces')"
        ).fetchall()
        admin_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('admin_credentials')"
        ).fetchall()
        state_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('runtime_config_state')"
        ).fetchall()
        memory_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list('customer_memory_items')"
        ).fetchall()
    assert {
        "admin_credentials",
        "admin_csrf_nonces",
        "admin_csrf_quota",
        "runtime_config_versions",
        "runtime_config_state",
        "customer_memory_items",
        "customer_memory_events",
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
    assert any(
        row[1] == "ix_customer_memory_customer_status_review"
        for row in memory_indexes
    )
    assert any(
        row[1] == "ix_customer_memory_customer_subject"
        for row in memory_indexes
    )
    assert any(
        row[1] == "uq_customer_memory_active_subject" and row[2]
        for row in memory_indexes
    )
    assert {
        "source_excerpt",
        "source_excerpt_hash",
        "source_occurred_at",
        "verified_at",
        "version",
        "content_redacted_at",
    } <= memory_columns
    assert any(
        row[1] == "ix_customer_memory_event_customer_occurred"
        for row in memory_event_indexes
    )
    assert any(
        row[1] == "ix_customer_memory_event_memory_occurred"
        for row in memory_event_indexes
    )
    assert "checkout_observed_on" in stay_columns
    assert "memory_processed_at" in message_columns
    assert {
        "origin_kind",
        "expires_at",
        "closed_at",
        "closure_reason_code",
        "closure_source",
        "closed_by_employee_id",
    } <= business_task_columns
    assert "EXPIRED" in business_task_ddl
    assert any(
        row[1] == "ix_business_tasks_status_expires_at"
        for row in business_task_indexes
    )
    assert any(
        row[2] == "employees" and row[3] == "closed_by_employee_id"
        for row in business_task_foreign_keys
    )
    assert any(row[2] == "employees" and row[3] == "employee_id" for row in admin_foreign_keys)
    assert any(row[2] == "admin_credentials" and row[3] == "admin_id" for row in csrf_foreign_keys)
    assert sum(row[2] == "runtime_config_versions" for row in state_foreign_keys) == 2
    assert any(
        row[2] == "customers" and row[3] == "customer_id"
        for row in memory_foreign_keys
    )
    assert any(
        row[2] == "messages" and row[3] == "source_message_id"
        for row in memory_foreign_keys
    )

    lifecycle_downgrade = run_alembic(
        "downgrade",
        "0016_admin_dashboard_indexes",
    )
    assert lifecycle_downgrade.returncode == 0, lifecycle_downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('runtime_config_versions')")
        }
        stay_columns_after_lifecycle_downgrade = {
            row[1] for row in connection.execute("PRAGMA table_info('stay_orders')")
        }
        message_columns_after_lifecycle_downgrade = {
            row[1] for row in connection.execute("PRAGMA table_info('messages')")
        }
        tables_after_lifecycle_downgrade = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "status" not in version_columns
    assert "based_on_revision" not in version_columns
    assert "checkout_observed_on" not in stay_columns_after_lifecycle_downgrade
    assert "memory_processed_at" not in message_columns_after_lifecycle_downgrade
    assert "customer_memory_items" not in tables_after_lifecycle_downgrade

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

    second_upgrade = run_alembic("upgrade", "0022_approval_pii")
    assert second_upgrade.returncode == 0, second_upgrade.stderr
    current = run_alembic("current")
    assert current.returncode == 0, current.stderr
    assert current.stdout.strip() == "0022_approval_pii"


def test_sqlite_approval_pii_migration_downgrades_and_reupgrades(
    tmp_path: Path,
) -> None:
    """0022 必须支持 SQLite 升级、降级到 0021 后再次升级。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "approval-pii.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    def run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        """在隔离审批数据库执行一次 Alembic 命令。"""
        return subprocess.run(
            [alembic, *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    expected_columns = {
        "guest_name_ciphertext",
        "guest_mobile_ciphertext",
        "special_requests_ciphertext",
        "pii_purged_at",
    }
    first_upgrade = run_alembic("upgrade", "0022_approval_pii")
    assert first_upgrade.returncode == 0, first_upgrade.stderr
    with sqlite3.connect(database_path) as connection:
        first_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('booking_approvals')")
        }
    assert expected_columns <= first_columns

    downgrade = run_alembic("downgrade", "0021_task_lifecycle")
    assert downgrade.returncode == 0, downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        downgraded_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('booking_approvals')")
        }
    assert expected_columns.isdisjoint(downgraded_columns)

    second_upgrade = run_alembic("upgrade", "0022_approval_pii")
    assert second_upgrade.returncode == 0, second_upgrade.stderr
    with sqlite3.connect(database_path) as connection:
        second_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('booking_approvals')")
        }
    assert expected_columns <= second_columns


def test_sqlite_approval_pii_finalization_drops_plaintext_and_is_irreversible(
    tmp_path: Path,
) -> None:
    """0023 只在密文完整时删旧列，并明确拒绝普通 Alembic 降级。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "approval-pii-final.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    def run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        """在隔离数据库执行审批 PII 最终迁移命令。"""
        return subprocess.run(
            [alembic, *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    before = run_alembic("upgrade", "0022_approval_pii")
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO booking_approvals ("
            "approval_code, conversation_id, status, check_in_date, check_out_date, "
            "number_of_guests, guest_name, guest_mobile, guest_name_ciphertext, "
            "guest_mobile_ciphertext, room_type_preference, special_requests, "
            "special_requests_ciphertext"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "APP-FINAL",
                1,
                "PENDING",
                "2026-09-01",
                "2026-09-02",
                2,
                "张三",
                "13800138000",
                b"encrypted-name",
                b"encrypted-mobile",
                "江景房",
                "高楼层",
                b"encrypted-request",
            ),
        )
        connection.commit()

    upgrade = run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('booking_approvals')")
        }
        ciphertext = connection.execute(
            "SELECT guest_name_ciphertext, guest_mobile_ciphertext, "
            "special_requests_ciphertext FROM booking_approvals"
        ).fetchone()
    assert {"guest_name", "guest_mobile", "special_requests"}.isdisjoint(columns)
    assert {
        "guest_name_ciphertext",
        "guest_mobile_ciphertext",
        "special_requests_ciphertext",
        "pii_purged_at",
    } <= columns
    assert ciphertext == (b"encrypted-name", b"encrypted-mobile", b"encrypted-request")

    downgrade = run_alembic("downgrade", "0022_approval_pii")
    assert downgrade.returncode != 0
    assert "恢复升级前数据库备份" in downgrade.stderr
    current = run_alembic("current")
    assert "0023_approval_pii_final (head)" in current.stdout


def test_sqlite_approval_pii_finalization_rejects_missing_ciphertext(
    tmp_path: Path,
) -> None:
    """任一未清理审批缺少必需密文时，0023 必须保持 0022 结构。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "approval-pii-incomplete.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    def run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        """在隔离数据库执行缺失密文迁移命令。"""
        return subprocess.run(
            [alembic, *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    before = run_alembic("upgrade", "0022_approval_pii")
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO booking_approvals ("
            "approval_code, conversation_id, status, check_in_date, check_out_date, "
            "number_of_guests, guest_name, guest_mobile, room_type_preference"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "APP-INCOMPLETE",
                1,
                "PENDING",
                "2026-09-01",
                "2026-09-02",
                2,
                "张三",
                "13800138000",
                "江景房",
            ),
        )
        connection.commit()

    upgrade = run_alembic("upgrade", "head")
    assert upgrade.returncode != 0
    assert "审批敏感资料密文回填未完成" in upgrade.stderr
    current = run_alembic("current")
    assert current.stdout.strip() == "0022_approval_pii"
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('booking_approvals')")
        }
    assert {"guest_name", "guest_mobile", "special_requests"} <= columns


def test_customer_memory_trust_migration_quarantines_unverified_history(
    tmp_path: Path,
) -> None:
    """0020 只保留可证明的管理员批准记忆，并在建唯一索引前治理冲突。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "customer-memory-trust-existing.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    def run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
        """在隔离 SQLite 数据库运行记忆迁移生命周期。"""
        return subprocess.run(
            [alembic, *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    before = run_alembic("upgrade", "0019_customer_memory_items")
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO customers (id, display_name, created_at, updated_at) "
            "VALUES (1, '迁移客户', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        rows = (
            (1, "pet_dog_name", "客户的狗叫查理", "MODEL_INFERENCE"),
            (2, "quiet_preference", "客户偏好安静", "USER_EXPLICIT"),
            (3, "bed_preference", "客户偏好大床", "EMPLOYEE_CONFIRMED"),
            (4, "floor_preference", "客户偏好低楼层", "EMPLOYEE_CONFIRMED"),
            (5, "floor_preference", "客户偏好高楼层", "EMPLOYEE_CONFIRMED"),
            (6, "dietary_preference", "客户不吃花生", "EMPLOYEE_CONFIRMED"),
            (7, "dietary_preference", "  客户不吃花生  ", "EMPLOYEE_CONFIRMED"),
        )
        connection.executemany(
            "INSERT INTO customer_memory_items "
            "(id, customer_id, subject_key, category, statement, status, evidence_type, "
            "confidence, confirmed_at, review_at, expires_at, created_at, updated_at) "
            "VALUES (?, 1, ?, 'CONFIRMED_FACT', ?, 'ACTIVE', ?, 0.9, "
            "CURRENT_TIMESTAMP, '2030-01-01', '2031-01-01', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            rows,
        )
        for memory_id in (3, 4, 5, 6, 7):
            connection.execute(
                "INSERT INTO audit_logs "
                "(action, target_type, target_id, details, created_at) "
                "VALUES ('customer_memory_reviewed', 'customer', '1', ?, CURRENT_TIMESTAMP)",
                (f'{{"customer_id": 1, "memory_id": {memory_id}, "decision": "approve"}}',),
            )
        connection.commit()

    after = run_alembic("upgrade", "0022_approval_pii")
    assert after.returncode == 0, after.stderr
    with sqlite3.connect(database_path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT id, status FROM customer_memory_items ORDER BY id"
            ).fetchall()
        )
        reasons = dict(
            connection.execute(
                "SELECT id, status_reason FROM customer_memory_items ORDER BY id"
            ).fetchall()
        )
        events = connection.execute(
            "SELECT memory_item_id, event_type, new_status "
            "FROM customer_memory_events ORDER BY memory_item_id"
        ).fetchall()

        duplicate_active_rejected = False
        try:
            connection.execute(
                "INSERT INTO customer_memory_items "
                "(customer_id, subject_key, category, statement, status, evidence_type, "
                "confidence, review_at, expires_at, version, created_at, updated_at) "
                "VALUES (1, 'bed_preference', 'CONFIRMED_FACT', '冲突大床偏好', "
                "'ACTIVE', 'EMPLOYEE_CONFIRMED', 0.9, '2030-01-01', '2031-01-01', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        except sqlite3.IntegrityError:
            duplicate_active_rejected = True

    assert statuses == {
        1: "CANDIDATE",
        2: "CANDIDATE",
        3: "ACTIVE",
        4: "DISPUTED",
        5: "DISPUTED",
        6: "SUPERSEDED",
        7: "ACTIVE",
    }
    assert reasons[1] == "历史证据不可验证"
    assert reasons[2] == "历史证据不可验证"
    assert reasons[4] == "历史同主题存在冲突陈述"
    assert reasons[6] == "历史重复记忆已合并"
    assert events == [
        (memory_id, "legacy_migrated", statuses[memory_id])
        for memory_id in range(1, 8)
    ]
    assert duplicate_active_rejected is True

    downgrade = run_alembic("downgrade", "0019_customer_memory_items")
    assert downgrade.returncode == 0, downgrade.stderr
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info('customer_memory_items')")
        }
        indexes = connection.execute("PRAGMA index_list('customer_memory_items')").fetchall()

    assert "customer_memory_events" not in tables
    assert "source_excerpt" not in columns
    assert "version" not in columns
    assert not any(row[1] == "uq_customer_memory_active_subject" for row in indexes)


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


def test_checkout_observation_migration_backfills_existing_finished_orders(
    tmp_path: Path,
) -> None:
    """0018 必须按计划退房日回填历史退房订单，并保留其他订单为空。"""
    project_root = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "checkout-observation-existing-orders.db"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    alembic = str(project_root / ".venv/bin/alembic")

    before = subprocess.run(
        [alembic, "upgrade", "0017_runtime_config_lifecycle"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO property_profiles "
            "(id, title, is_active, created_at, updated_at) "
            "VALUES (1, '测试房间', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        rows = (
            ("R-FINISHED-1", "S-1", "2026-08-10", "2026-08-12", " Checked_Out "),
            ("R-FINISHED-2", "S-2", "2026-08-11", "2026-08-13", "COMPLETED"),
            ("R-ACTIVE", "S-3", "2026-08-14", "2026-08-16", "confirmed"),
        )
        connection.executemany(
            "INSERT INTO stay_orders "
            "(hostex_reservation_code, stay_code, property_id, check_in_date, "
            "check_out_date, status, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            rows,
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
        observations = dict(
            connection.execute(
                "SELECT hostex_reservation_code, checkout_observed_on "
                "FROM stay_orders ORDER BY hostex_reservation_code"
            ).fetchall()
        )

    assert observations == {
        "R-ACTIVE": None,
        "R-FINISHED-1": "2026-08-12",
        "R-FINISHED-2": "2026-08-13",
    }
