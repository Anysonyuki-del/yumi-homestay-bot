from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中加载外部服务密钥和运行参数。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    public_base_url: str
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-terra"
    hostex_access_token: str
    wecom_corp_id: str
    wecom_kf_secret: str
    wecom_callback_token: str
    wecom_encoding_aes_key: str
    wecom_agent_id: int = Field(gt=0)
    wecom_agent_secret: str
    wecom_duty_userids: str
    session_secret: str = Field(min_length=32)


@lru_cache
def get_settings() -> Settings:
    """缓存经过校验的运行配置，避免每次请求重复读取环境。"""
    return Settings()  # type: ignore[call-arg]
