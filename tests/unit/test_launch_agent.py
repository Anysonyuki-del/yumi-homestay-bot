import plistlib
from pathlib import Path


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
    start_script = project_root / "deploy" / "start.sh"
    assert start_script.exists()
    script = start_script.read_text()
    config_position = script.index("get_settings().database_url")
    backup_position = script.index('cp "$DATABASE_PATH"')
    migration_position = script.index("alembic upgrade head")
    assert config_position < backup_position < migration_position
    assert 'cp -R "$PRIVATE_UPLOAD_DIR"' in script
    assert "set -eu" in script
    assert '"$(dirname -- "$0")/.."' in script
    assert "exec \"$PYTHON_BIN\" -m uvicorn" in script
