from homestay_bot.config import Settings


def test_settings_load_deepseek_clients_from_one_base_url(monkeypatch) -> None:
    """单一 DeepSeek 根地址必须同时派生普通对话和旅游搜索接口。"""
    environment = {
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "PUBLIC_BASE_URL": "https://local.example",
        "DEEPSEEK_API_KEY": "test-deepseek-key",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.test/",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
        "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
        "WECOM_CORP_ID": "corp-id",
        "WECOM_KF_SECRET": "kf-secret",
        "WECOM_CALLBACK_TOKEN": "callback-token",
        "WECOM_ENCODING_AES_KEY": "A" * 43,
        "WECOM_AGENT_ID": "100001",
        "WECOM_AGENT_SECRET": "agent-secret",
        "WECOM_DUTY_USERIDS": "staff-1",
        "SESSION_SECRET": "local-test-session-secret-at-least-32",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.deepseek_api_key == "test-deepseek-key"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.deepseek_anthropic_base_url == (
        "https://api.deepseek.test/anthropic"
    )
