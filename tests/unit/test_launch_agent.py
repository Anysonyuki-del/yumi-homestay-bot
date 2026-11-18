import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


def test_launch_agent_keeps_local_bot_running() -> None:
    """本地守护配置应在登录后启动服务，并在意外退出后自动重启。"""
    project_root = Path(__file__).resolve().parents[2]
    plist_path = project_root / "deploy" / "com.rin.homestay-bot.plist"
    # LaunchAgent 属于指定 macOS 账户，测试固定部署契约而不是当前 Runner 的 HOME。
    runtime_root = Path("/Users/rin/Library/Application Support/HomestayBot")

    with plist_path.open("rb") as plist_file:
        config = plistlib.load(plist_file)

    assert config["Label"] == "com.rin.homestay-bot"
    assert config["RunAtLoad"] is True
    assert config["KeepAlive"] is True
    assert config["WorkingDirectory"] == str(runtime_root)
    assert config["EnvironmentVariables"]["PYTHONPATH"] == str(
        runtime_root / "src"
    )
    assert config["ProgramArguments"] == [
        str(runtime_root / "deploy" / "start.sh"),
    ]


@pytest.fixture
def launch_root(tmp_path: Path) -> Path:
    """隔离部署目录与基础配置，仅替换最后的 Uvicorn 入口，避免监听真实端口。"""
    project_root = Path(__file__).resolve().parents[2]
    (tmp_path / "deploy").mkdir()
    shutil.copy2(project_root / "deploy/start.sh", tmp_path / "deploy/start.sh")
    (tmp_path / "alembic.ini").symlink_to(project_root / "alembic.ini")
    (tmp_path / "migrations").symlink_to(project_root / "migrations")
    bin_dir = tmp_path / ".venv/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text('#!/bin/sh\nexec "$TEST_PYTHON" "$@"\n')
    (bin_dir / "python").chmod(0o755)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=sqlite+aiosqlite:///state.db\n"
        "PUBLIC_BASE_URL=http://localhost\n"
        f"SESSION_SECRET={'s' * 32}\nDATA_ENCRYPTION_KEY={'a' * 43}=\n"
        "PRIVATE_UPLOAD_DIR='private files'\n"
    )
    (tmp_path / "uvicorn.py").write_text(
        'import sys\nfrom pathlib import Path\n'
        'Path("server-target").write_text(sys.argv[1])\n'
    )
    return tmp_path


def _launch(root: Path) -> subprocess.CompletedProcess[str]:
    """只传入测试所用解释器和模块目录，防止本机业务环境变量掩盖缺陷。"""
    return subprocess.run(
        [str(root / "deploy/start.sh")],
        cwd=root,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "TEST_PYTHON": sys.executable,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_launch_backs_up_committed_wal_and_uploads_with_only_bootstrap(launch_root: Path) -> None:
    """迁移前保留仍在 WAL 的已提交行与附件；达到 head 后重启应复用已有备份。"""
    uploads = launch_root / "private files"
    uploads.mkdir()
    (uploads / "sample").write_bytes(b"fixture")
    with closing(sqlite3.connect(launch_root / "state.db")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE sample (id INTEGER)")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO sample VALUES (17)")
        writer.commit()
        assert (launch_root / "state.db-wal").stat().st_size > 0
        result = _launch(launch_root)
        assert result.returncode == 0, result.stderr
        assert writer.execute("SELECT version_num FROM alembic_version").fetchone()

    (backup,) = (launch_root / ".backups").iterdir()
    with closing(sqlite3.connect(backup / "state.db")) as restored:
        assert restored.execute("SELECT id FROM sample").fetchall() == [(17,)]
        assert restored.execute(
            "SELECT name FROM sqlite_master WHERE name='customers'"
        ).fetchone() is None
    assert (backup / "private_uploads/sample").read_bytes() == b"fixture"
    assert (launch_root / "server-target").read_text() == "homestay_bot.main:app"
    second = _launch(launch_root)
    assert second.returncode == 0, second.stderr
    assert list((launch_root / ".backups").iterdir()) == [backup]


def test_launch_aborts_before_backup_on_version_check_failure(launch_root: Path) -> None:
    """损坏的 SQLite 无法读取版本时应立即退出，避免产生误导性的备份。"""
    (launch_root / "state.db").write_bytes(b"not a sqlite database")
    result = _launch(launch_root)
    assert result.returncode != 0
    assert "file is not a database" in result.stderr
    assert not (launch_root / ".backups").exists()
    assert not (launch_root / "server-target").exists()


def test_launch_aborts_before_migration_when_backup_unavailable(launch_root: Path) -> None:
    """备份位置不可用时，数据库必须保留迁移前的结构。"""
    (launch_root / ".backups").write_bytes(b"occupied")
    result = _launch(launch_root)
    assert result.returncode != 0
    with closing(sqlite3.connect(launch_root / "state.db")) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert not (launch_root / "server-target").exists()
