from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    """加载禁止网页修改且足以启动登录和配置修复页的基础参数。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    public_base_url: str
    session_secret: str = Field(min_length=32)
    data_encryption_key: str = Field(min_length=44, max_length=44)
    config_encryption_key: str | None = Field(default=None, min_length=44, max_length=44)
    admin_bootstrap_username: str | None = Field(default=None, min_length=1, max_length=128)
    admin_bootstrap_password_hash: str | None = None
    private_upload_dir: Path = Path("data/private_uploads")
    private_upload_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=25 * 1024 * 1024,
    )


class RuntimeEnvironmentSettings(BaseSettings):
    """加载可进入数据库加密快照的外部业务 API 参数。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    @property
    def deepseek_anthropic_base_url(self) -> str:
        """从唯一 DeepSeek 根地址派生 Anthropic 兼容地址。"""
        return f"{self.deepseek_base_url.rstrip('/')}/anthropic"


class Settings(BootstrapSettings, RuntimeEnvironmentSettings):
    """兼容既有完整启动路径的基础参数与外部运行参数联合模型。"""


@lru_cache
def get_settings() -> Settings:
    """缓存经过校验的运行配置，避免每次请求重复读取环境。"""
    return Settings()  # type: ignore[call-arg]
