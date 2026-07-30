import json
import re
from typing import Any

from pydantic import BaseModel, Field

from homestay_bot.services.context_retention import ContextSummaryResult


class ContextSummarySafetyError(RuntimeError):
    """表示摘要输出仍包含不可发送给模型上下文的敏感字段。"""


class _SummaryPayload(BaseModel):
    """校验 DeepSeek 分层摘要的固定 JSON 结构。"""

    summary: str = Field(min_length=1, max_length=2000)
    unresolved_items: list[str] = Field(default_factory=list, max_length=20)


class DeepSeekContextSummarizer:
    """先本地脱敏，再使用 DeepSeek 合并客户分层摘要。"""

    _SENSITIVE_PATTERNS = (
        re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)"),
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)"),
        re.compile(
            r"(?:地址\s*)?(?:湖北省)?武汉市?"
            r"(?:洪山区|武昌区|青山区|汉阳区|江汉区|江岸区|硚口区|"
            r"蔡甸区|东西湖区|黄陂区|新洲区|江夏区)"
            r"[^，。\n]{0,40}(?:路|街|大道|小区|号|栋|室)[^，。\n]{0,20}"
        ),
        re.compile(r"(?:门锁|房门|密码|验证码)\s*(?:密码|码)?\s*[:：]?\s*[A-Za-z0-9]{4,}"),
        re.compile(r"(?:二维码|QR\s*code)\s*[:：]?\s*\S+", re.IGNORECASE),
    )

    def __init__(self, client: Any, model: str) -> None:
        """注入 OpenAI 兼容客户端和 DeepSeek 模型名称。"""
        self._client = client
        self._model = model

    async def summarize(
        self,
        *,
        tier: str,
        existing_summary: str,
        messages: list[str],
    ) -> ContextSummaryResult:
        """合并脱敏消息，并拒绝任何重新出现敏感特征的输出。"""
        safe_existing = self._redact(existing_summary)
        safe_messages = [self._redact(item) for item in messages]
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你负责整理民宿客户上下文。只保留偏好、已确认事实和待确认项；"
                        "禁止输出手机号、身份证、详细地址、门锁密码、验证码或二维码。"
                        "只输出 JSON：summary 和 unresolved_items。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "tier": tier,
                            "existing_summary": safe_existing,
                            "messages": safe_messages,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        payload = _SummaryPayload.model_validate_json(
            response.choices[0].message.content or ""
        )
        output = payload.summary + "\n" + "\n".join(payload.unresolved_items)
        if self._contains_sensitive(output):
            raise ContextSummarySafetyError("摘要输出包含敏感字段")
        return ContextSummaryResult(
            summary=payload.summary,
            unresolved_items=payload.unresolved_items,
        )

    @classmethod
    def _redact(cls, text: str) -> str:
        """用固定占位符删除本地可识别的敏感片段。"""
        cleaned = text
        for pattern in cls._SENSITIVE_PATTERNS:
            cleaned = pattern.sub("[已脱敏]", cleaned)
        return cleaned

    @classmethod
    def _contains_sensitive(cls, text: str) -> bool:
        """检查摘要是否包含任何禁止的敏感特征。"""
        return any(pattern.search(text) for pattern in cls._SENSITIVE_PATTERNS)
