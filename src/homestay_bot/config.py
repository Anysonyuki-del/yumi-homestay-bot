from functools import lru_cache

from pydantic import Field
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
    wecom_corp_id: str
    wecom_kf_secret: str
    wecom_callback_token: str
    wecom_encoding_aes_key: str
    wecom_agent_id: int = Field(gt=0)
    wecom_agent_secret: str
    wecom_duty_userids: str
    wecom_poll_interval_seconds: float = Field(default=5, ge=5, le=300)
    session_secret: str = Field(min_length=32)
    data_encryption_key: str = Field(min_length=44, max_length=44)

    @property
    def deepseek_anthropic_base_url(self) -> str:
        """从唯一 DeepSeek 根地址派生 Anthropic 兼容地址。"""
        return f"{self.deepseek_base_url.rstrip('/')}/anthropic"


@lru_cache
def get_settings() -> Settings:
    """缓存经过校验的运行配置，避免每次请求重复读取环境。"""
    return Settings()  # type: ignore[call-arg]
