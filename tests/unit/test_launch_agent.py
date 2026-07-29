import plistlib
from pathlib import Path


def test_launch_agent_keeps_local_bot_running() -> None:
    """本地守护配置应在登录后启动服务，并在意外退出后自动重启。"""
    project_root = Path(__file__).resolve().parents[2]
    plist_path = project_root / "deploy" / "com.rin.homestay-bot.plist"
    runtime_root = Path.home() / "Library" / "Application Support" / "HomestayBot"

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
        str(runtime_root / ".venv" / "bin" / "python"),
        "-m",
        "uvicorn",
        "homestay_bot.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
    ]
