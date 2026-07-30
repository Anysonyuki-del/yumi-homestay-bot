import pytest
from pydantic import ValidationError

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
        "HOSTEX_WEBHOOK_SECRET_TOKEN": "test-webhook-secret",
        "WECOM_CORP_ID": "corp-id",
        "WECOM_KF_SECRET": "kf-secret",
        "WECOM_CALLBACK_TOKEN": "callback-token",
        "WECOM_ENCODING_AES_KEY": "A" * 43,
        "WECOM_AGENT_ID": "100001",
        "WECOM_AGENT_SECRET": "agent-secret",
        "WECOM_DUTY_USERIDS": "staff-1",
        "SESSION_SECRET": "local-test-session-secret-at-least-32",
        "DATA_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.deepseek_api_key == "test-deepseek-key"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.data_encryption_key.startswith("MDAw")
    assert settings.deepseek_anthropic_base_url == (
        "https://api.deepseek.test/anthropic"
    )
    assert settings.wecom_contact_secret is None


def test_settings_load_optional_wecom_contact_secret(monkeypatch) -> None:
    """客户联系 Secret 独立可选，配置后才启用外部联系人标签同步。"""
    environment = {
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "PUBLIC_BASE_URL": "https://local.example",
        "DEEPSEEK_API_KEY": "test-deepseek-key",
        "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
        "HOSTEX_WEBHOOK_SECRET_TOKEN": "test-webhook-secret",
        "WECOM_CORP_ID": "corp-id",
        "WECOM_KF_SECRET": "kf-secret",
        "WECOM_CALLBACK_TOKEN": "callback-token",
        "WECOM_ENCODING_AES_KEY": "A" * 43,
        "WECOM_AGENT_ID": "100001",
        "WECOM_AGENT_SECRET": "agent-secret",
        "WECOM_DUTY_USERIDS": "staff-1",
        "WECOM_CONTACT_SECRET": "contact-secret",
        "SESSION_SECRET": "local-test-session-secret-at-least-32",
        "DATA_ENCRYPTION_KEY": (
            "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        ),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.wecom_contact_secret == "contact-secret"


def test_settings_default_to_five_second_wecom_polling(monkeypatch) -> None:
    """未显式配置时采用五秒补拉，缩短客人等待时间。"""
    environment = {
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "PUBLIC_BASE_URL": "https://local.example",
        "DEEPSEEK_API_KEY": "test-deepseek-key",
        "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
        "HOSTEX_WEBHOOK_SECRET_TOKEN": "test-webhook-secret",
        "WECOM_CORP_ID": "corp-id",
        "WECOM_KF_SECRET": "kf-secret",
        "WECOM_CALLBACK_TOKEN": "callback-token",
        "WECOM_ENCODING_AES_KEY": "A" * 43,
        "WECOM_AGENT_ID": "100001",
        "WECOM_AGENT_SECRET": "agent-secret",
        "WECOM_DUTY_USERIDS": "staff-1",
        "SESSION_SECRET": "local-test-session-secret-at-least-32",
        "DATA_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("WECOM_POLL_INTERVAL_SECONDS", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.wecom_poll_interval_seconds == 5


def test_settings_require_independent_data_encryption_key(monkeypatch) -> None:
    """敏感数据密钥缺失时应用必须拒绝启动，不能复用会话密钥。"""
    environment = {
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "PUBLIC_BASE_URL": "https://local.example",
        "DEEPSEEK_API_KEY": "test-deepseek-key",
        "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
        "HOSTEX_WEBHOOK_SECRET_TOKEN": "test-webhook-secret",
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
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
