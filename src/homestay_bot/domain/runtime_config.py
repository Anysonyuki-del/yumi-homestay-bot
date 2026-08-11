"""定义可加密持久化的完整运行配置快照及安全页面投影。"""

from dataclasses import asdict, dataclass, fields
from typing import Any

from homestay_bot.config import RuntimeEnvironmentSettings


@dataclass(frozen=True, slots=True)
class RuntimeConfigView:
    """只包含可公开设置项和掩码凭据的后台页面投影。"""

    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    hostex_access_token: str
    hostex_webhook_secret_token: str
    hostex_reconcile_interval_seconds: float
    wecom_corp_id: str
    wecom_kf_secret: str
    wecom_callback_token: str
    wecom_encoding_aes_key: str
    wecom_agent_id: int
    wecom_agent_secret: str
    wecom_contact_secret: str
    wecom_duty_userids: str
    wecom_poll_interval_seconds: float

    @classmethod
    def empty(cls) -> "RuntimeConfigView":
        """为外部环境完全缺失时提供不伪造默认凭据的修复表单投影。"""
        return cls(
            deepseek_api_key="未配置",
            deepseek_base_url="",
            deepseek_model="",
            hostex_access_token="未配置",
            hostex_webhook_secret_token="未配置",
            hostex_reconcile_interval_seconds=900.0,
            wecom_corp_id="未配置",
            wecom_kf_secret="未配置",
            wecom_callback_token="未配置",
            wecom_encoding_aes_key="未配置",
            wecom_agent_id=0,
            wecom_agent_secret="未配置",
            wecom_contact_secret="未配置",
            wecom_duty_userids="未配置",
            wecom_poll_interval_seconds=60.0,
        )

    def to_dict(self) -> dict[str, object]:
        """返回仅含安全页面字段的普通字典。"""
        return asdict(self)


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeConfigSnapshot:
    """保存构造全部外部客户端所需的不可变完整配置。"""

    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    hostex_access_token: str
    hostex_webhook_secret_token: str
    hostex_reconcile_interval_seconds: float
    wecom_corp_id: str
    wecom_kf_secret: str
    wecom_callback_token: str
    wecom_encoding_aes_key: str
    wecom_agent_id: int
    wecom_agent_secret: str
    wecom_contact_secret: str | None
    wecom_duty_userids: str
    wecom_poll_interval_seconds: float
    schema_version: int = 1

    def __repr__(self) -> str:
        """对象表示始终脱敏，避免异常或调试日志打印外部凭据。"""
        return "RuntimeConfigSnapshot(schema_version=1, values=<redacted>)"

    @classmethod
    def from_settings(cls, settings: RuntimeEnvironmentSettings) -> "RuntimeConfigSnapshot":
        """从不可网页修改的应用 Settings 提取初始环境快照。"""
        return cls(
            deepseek_api_key=settings.deepseek_api_key,
            deepseek_base_url=settings.deepseek_base_url,
            deepseek_model=settings.deepseek_model,
            hostex_access_token=settings.hostex_access_token,
            hostex_webhook_secret_token=settings.hostex_webhook_secret_token,
            hostex_reconcile_interval_seconds=(settings.hostex_reconcile_interval_seconds),
            wecom_corp_id=settings.wecom_corp_id,
            wecom_kf_secret=settings.wecom_kf_secret,
            wecom_callback_token=settings.wecom_callback_token,
            wecom_encoding_aes_key=settings.wecom_encoding_aes_key,
            wecom_agent_id=settings.wecom_agent_id,
            wecom_agent_secret=settings.wecom_agent_secret,
            wecom_contact_secret=settings.wecom_contact_secret,
            wecom_duty_userids=settings.wecom_duty_userids,
            wecom_poll_interval_seconds=settings.wecom_poll_interval_seconds,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeConfigSnapshot":
        """严格恢复固定版本结构，拒绝缺失字段和未知字段。"""
        expected = {field.name for field in fields(cls)}
        if set(payload) != expected:
            raise ValueError("运行配置快照结构无效")
        try:
            snapshot = cls(**payload)
        except (TypeError, ValueError) as error:
            raise ValueError("运行配置快照字段无效") from error
        snapshot.validate()
        return snapshot

    def to_dict(self) -> dict[str, object]:
        """返回供单密文序列化的完整结构。"""
        return asdict(self)

    def validate(self) -> None:
        """验证客户端构造所需的基本边界；外联地址策略留给批次五。"""
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("运行配置快照版本无效")
        string_fields: dict[str, str | None] = {
            "deepseek_api_key": self.deepseek_api_key,
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "hostex_access_token": self.hostex_access_token,
            "hostex_webhook_secret_token": self.hostex_webhook_secret_token,
            "wecom_corp_id": self.wecom_corp_id,
            "wecom_kf_secret": self.wecom_kf_secret,
            "wecom_callback_token": self.wecom_callback_token,
            "wecom_encoding_aes_key": self.wecom_encoding_aes_key,
            "wecom_agent_secret": self.wecom_agent_secret,
            "wecom_contact_secret": self.wecom_contact_secret,
            "wecom_duty_userids": self.wecom_duty_userids,
        }
        if any(value is not None and type(value) is not str for value in string_fields.values()):
            raise ValueError("运行配置文本字段类型无效")
        required_text = (
            self.deepseek_api_key,
            self.deepseek_base_url,
            self.deepseek_model,
            self.hostex_access_token,
            self.hostex_webhook_secret_token,
            self.wecom_corp_id,
            self.wecom_kf_secret,
            self.wecom_callback_token,
            self.wecom_encoding_aes_key,
            self.wecom_agent_secret,
            self.wecom_duty_userids,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError("运行配置必填字段不能为空")
        max_lengths = {
            "deepseek_api_key": 4096,
            "deepseek_base_url": 2048,
            "deepseek_model": 256,
            "hostex_access_token": 4096,
            "hostex_webhook_secret_token": 4096,
            "wecom_corp_id": 256,
            "wecom_kf_secret": 4096,
            "wecom_callback_token": 4096,
            "wecom_encoding_aes_key": 43,
            "wecom_agent_secret": 4096,
            "wecom_contact_secret": 4096,
            "wecom_duty_userids": 4096,
        }
        if any(
            value is not None and len(value) > max_lengths[name]
            for name, value in string_fields.items()
        ):
            raise ValueError("运行配置文本字段过长")
        if len(self.wecom_encoding_aes_key) != 43:
            raise ValueError("企业微信 EncodingAESKey 长度无效")
        if type(self.wecom_agent_id) is not int or self.wecom_agent_id <= 0:
            raise ValueError("企业微信 AgentId 无效")
        if type(self.hostex_reconcile_interval_seconds) is not float:
            raise ValueError("百居易对账间隔类型无效")
        if type(self.wecom_poll_interval_seconds) is not float:
            raise ValueError("企业微信补拉间隔类型无效")
        if not 60 <= self.hostex_reconcile_interval_seconds <= 86_400:
            raise ValueError("百居易对账间隔无效")
        if not 5 <= self.wecom_poll_interval_seconds <= 300:
            raise ValueError("企业微信补拉间隔无效")

    def merged(self, updates: dict[str, object | None]) -> "RuntimeConfigSnapshot":
        """只替换字典中明确存在的键；None 可用于清除唯一可选字段。"""
        allowed = {field.name for field in fields(self)}
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError("运行配置更新包含未知字段")
        payload = self.to_dict()
        payload.update(updates)
        return self.from_dict(payload)

    def masked_view(self) -> RuntimeConfigView:
        """构造绝不包含完整外部身份或秘密的页面投影。"""
        return RuntimeConfigView(
            deepseek_api_key=_mask(self.deepseek_api_key),
            deepseek_base_url=self.deepseek_base_url,
            deepseek_model=self.deepseek_model,
            hostex_access_token=_mask(self.hostex_access_token),
            hostex_webhook_secret_token=_mask(self.hostex_webhook_secret_token),
            hostex_reconcile_interval_seconds=self.hostex_reconcile_interval_seconds,
            wecom_corp_id=_mask(self.wecom_corp_id),
            wecom_kf_secret=_mask(self.wecom_kf_secret),
            wecom_callback_token=_mask(self.wecom_callback_token),
            wecom_encoding_aes_key=_mask(self.wecom_encoding_aes_key),
            wecom_agent_id=self.wecom_agent_id,
            wecom_agent_secret=_mask(self.wecom_agent_secret),
            wecom_contact_secret=_mask(self.wecom_contact_secret),
            wecom_duty_userids=_mask(self.wecom_duty_userids),
            wecom_poll_interval_seconds=self.wecom_poll_interval_seconds,
        )


def _mask(value: str | None) -> str:
    """仅显示配置状态和最多四位尾号，避免页面恢复完整凭据。"""
    if not value:
        return "未配置"
    suffix = value[-4:] if len(value) >= 4 else ""
    return f"已配置 ····{suffix}" if suffix else "已配置"
