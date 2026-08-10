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
        "CONFIG_ENCRYPTION_KEY": "MTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTE=",
        "ADMIN_BOOTSTRAP_USERNAME": "admin",
        "ADMIN_BOOTSTRAP_PASSWORD_HASH": "$argon2id$v=19$m=65536,t=3,p=4$YWJj$ZGVm",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.deepseek_api_key == "test-deepseek-key"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.data_encryption_key.startswith("MDAw")
    assert settings.config_encryption_key.startswith("MTEx")
    assert settings.admin_bootstrap_username == "admin"
    assert settings.admin_bootstrap_password_hash.startswith("$argon2id$")
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


def test_settings_default_to_sixty_second_wecom_polling(monkeypatch) -> None:
    """Webhook 保持实时，补拉默认一分钟以免耗尽企业微信额度。"""
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

    assert settings.wecom_poll_interval_seconds == 60


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


def test_settings_reject_plaintext_bootstrap_password(monkeypatch) -> None:
    """引导密码配置必须是预生成 Argon2id 哈希，不能接收明文。"""
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", "plaintext-password")

    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite+aiosqlite:///test.db",
            public_base_url="https://local.example",
            deepseek_api_key="key",
            hostex_access_token="token",
            hostex_webhook_secret_token="webhook-secret",
            wecom_corp_id="corp",
            wecom_kf_secret="kf",
            wecom_callback_token="callback",
            wecom_encoding_aes_key="A" * 43,
            wecom_agent_id=1,
            wecom_agent_secret="agent",
            wecom_duty_userids="staff",
            session_secret="s" * 32,
            data_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            _env_file=None,
        )


def test_settings_reject_forged_argon2id_prefix(monkeypatch) -> None:
    """仅伪造 Argon2id 前缀但不含合法编码结构的字符串必须被拒绝。"""
    monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", "$argon2id$plaintext")

    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite+aiosqlite:///test.db",
            public_base_url="https://local.example",
            deepseek_api_key="key",
            hostex_access_token="token",
            hostex_webhook_secret_token="webhook-secret",
            wecom_corp_id="corp",
            wecom_kf_secret="kf",
            wecom_callback_token="callback",
            wecom_encoding_aes_key="A" * 43,
            wecom_agent_id=1,
            wecom_agent_secret="agent",
            wecom_duty_userids="staff",
            session_secret="s" * 32,
            data_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            _env_file=None,
        )
