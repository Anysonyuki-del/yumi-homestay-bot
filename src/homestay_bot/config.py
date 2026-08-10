from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中加载外部服务密钥和运行参数。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    public_base_url: str
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    hostex_access_token: str
    hostex_webhook_secret_token: str = Field(min_length=8)
    hostex_reconcile_interval_seconds: float = Field(default=900, ge=60, le=86400)
    wecom_corp_id: str
    wecom_kf_secret: str
    wecom_callback_token: str
    wecom_encoding_aes_key: str
    wecom_agent_id: int = Field(gt=0)
    wecom_agent_secret: str
    wecom_contact_secret: str | None = None
    wecom_duty_userids: str
    wecom_poll_interval_seconds: float = Field(default=60, ge=5, le=300)
    session_secret: str = Field(min_length=32)
    data_encryption_key: str = Field(min_length=44, max_length=44)
    config_encryption_key: str | None = Field(
        default=None, min_length=44, max_length=44
    )
    admin_bootstrap_username: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    admin_bootstrap_password_hash: str | None = None
    private_upload_dir: Path = Path("data/private_uploads")
    private_upload_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=25 * 1024 * 1024,
    )

    @property
    def deepseek_anthropic_base_url(self) -> str:
        """从唯一 DeepSeek 根地址派生 Anthropic 兼容地址。"""
        return f"{self.deepseek_base_url.rstrip('/')}/anthropic"

    @model_validator(mode="after")
    def validate_admin_bootstrap(self) -> "Settings":
        """要求引导用户名与预生成 Argon2id 哈希成对出现，拒绝明文密码。"""
        username_set = self.admin_bootstrap_username is not None
        password_hash = self.admin_bootstrap_password_hash
        password_hash_set = password_hash is not None
        if username_set != password_hash_set:
            raise ValueError("管理员引导用户名和密码哈希必须同时配置")
        if password_hash is not None and not password_hash.startswith("$argon2id$"):
            raise ValueError("管理员引导密码必须是预生成 Argon2id 哈希")
        return self


@lru_cache
def get_settings() -> Settings:
    """缓存经过校验的运行配置，避免每次请求重复读取环境。"""
    return Settings()  # type: ignore[call-arg]
