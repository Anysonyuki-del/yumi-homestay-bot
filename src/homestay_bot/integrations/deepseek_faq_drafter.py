import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from homestay_bot.services.answer_policy import is_property_specific
from homestay_bot.services.knowledge_service import (
    ADMIN_CONFIRMATION_PLACEHOLDER as _ADMIN_CONFIRMATION_PLACEHOLDER,
)
from homestay_bot.services.sensitive_text import contains_sensitive_text

_LINK_PATTERN = re.compile(r"https?://|\[[^\]]+\]\([^)]+\)", re.IGNORECASE)


class FaqDraftUnavailableError(RuntimeError):
    """表示 DeepSeek 未能生成可安全交给管理员的 FAQ 草稿。"""


class FaqDraft(BaseModel):
    """保存管理员可编辑的中英文 FAQ 草稿。"""

    category: str = Field(min_length=1, max_length=64)
    question_zh: str = Field(min_length=1, max_length=300)
    answer_zh: str = Field(min_length=1, max_length=1500)
    question_en: str = Field(min_length=1, max_length=500)
    answer_en: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    verification_items: list[str] = Field(default_factory=list, max_length=8)

    @field_validator(
        "category",
        "question_zh",
        "answer_zh",
        "question_en",
        "answer_en",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        """删除字段首尾空白，避免空壳草稿进入管理页面。"""
        return value.strip()

    @field_validator("keywords", "verification_items")
    @classmethod
    def _clean_list(cls, values: list[str]) -> list[str]:
        """清理、去重并限制列表内单项长度。"""
        cleaned = [item.strip()[:100] for item in values if item.strip()]
        return list(dict.fromkeys(cleaned))


class StaffDraft(BaseModel):
    """人工来源草稿：只用管家消息里的事实整理（修复 Spec 2026-10-08 fix2 §2.6(4)）。

    `found_answer` 为假时其余字段可以为空，调用方只记「没找到答案」。
    """

    found_answer: bool
    guest_only: bool = False
    category: str = Field(default="", max_length=64)
    question_zh: str = Field(default="", max_length=300)
    answer_zh: str = Field(default="", max_length=1500)
    question_en: str = Field(default="", max_length=500)
    answer_en: str = Field(default="", max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    source_quotes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("category", "question_zh", "answer_zh", "question_en", "answer_en")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        """删除字段首尾空白。"""
        return value.strip()

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, values: list[str]) -> list[str]:
        """清理、去重并限制关键词长度。"""
        cleaned = [item.strip()[:100] for item in values if item.strip()]
        return list(dict.fromkeys(cleaned))


class DeepSeekFaqDrafter:
    """通过 DeepSeek 生成仅供管理员审核的 FAQ 参考草稿。"""

    def __init__(self, *, client: Any, model: str) -> None:
        """注入 OpenAI 兼容客户端和模型名称。"""
        self._client = client
        self._model = model

    @classmethod
    def _validate_safety(
        cls,
        draft: FaqDraft,
        *,
        canonical_question: str,
        category: str,
    ) -> None:
        """拒绝链接和没有待确认占位的未核实专属事实。"""
        serialized = json.dumps(draft.model_dump(), ensure_ascii=False)
        if _LINK_PATTERN.search(serialized):
            raise FaqDraftUnavailableError()
        if (
            draft.verification_items
            and _ADMIN_CONFIRMATION_PLACEHOLDER not in draft.answer_zh
        ):
            raise FaqDraftUnavailableError()
        if cls._is_property_specific_draft(
            draft,
            canonical_question=canonical_question,
            category=category,
        ) and (
            not draft.verification_items
            or _ADMIN_CONFIRMATION_PLACEHOLDER not in draft.answer_zh
        ):
            raise FaqDraftUnavailableError()

    @staticmethod
    def _is_property_specific_draft(
        draft: FaqDraft,
        *,
        canonical_question: str,
        category: str,
    ) -> bool:
        """判断草稿是否涉及必须由管理员最终确认的民宿专属事实。"""
        subject = "\n".join(
            (
                canonical_question,
                category,
                draft.question_zh,
                draft.answer_zh,
                draft.question_en,
                draft.answer_en,
            )
        )
        return is_property_specific(subject)

    async def generate(
        self,
        *,
        canonical_question: str,
        category: str,
        examples: list[str],
        approved_knowledge: list[dict[str, str]],
    ) -> FaqDraft:
        """使用脱敏示例和审核知识生成严格 JSON 草稿。"""
        system_prompt = (
            "你是武汉一家7间房民宿的FAQ编辑，只为管理员生成参考草稿。"
            "不得参考其他民宿、行业惯例或常识推测本店事实。"
            "凡涉及本店停车数量、收费、时间、设施或政策等专属事实，"
            "即使审核知识中出现相似内容，也必须在中文答案对应位置写"
            "“【待管理员确认】”，并列入verification_items，由管理员"
            "最终确认。生成完整中英文问答，但不得输出网址、"
            "Markdown链接或客人身份。草稿不会直接回复客人。"
            "只输出JSON，字段为category、question_zh、answer_zh、"
            "question_en、answer_en、keywords、verification_items。"
        )
        payload = {
            "canonical_question": canonical_question.strip(),
            "suggested_category": category.strip(),
            "redacted_examples": examples[:3],
            "approved_knowledge": approved_knowledge[:100],
        }
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                max_tokens=1200,
            )
            content = response.choices[0].message.content or ""
            draft = FaqDraft.model_validate_json(content)
            self._validate_safety(
                draft,
                canonical_question=canonical_question,
                category=category,
            )
            return draft
        except FaqDraftUnavailableError:
            raise
        except Exception as error:
            # 统一为无正文领域异常，禁止把模型响应或审核知识写入日志。
            raise FaqDraftUnavailableError() from error

    @staticmethod
    def validate_staff_draft(draft: StaffDraft, staff_messages: list[str]) -> None:
        """人工来源草稿校验：拒绝链接与凭据，依据片段必须逐条来自本次输入的管家消息。

        只证明引用片段来自输入，不证明答案语义、否定、金额或适用范围正确（C1），这些由
        管理员审核确认。不要求「待管理员确认」占位符：答案本就来自管家原话。
        """
        if not draft.found_answer:
            return
        texts = (
            draft.category, draft.question_zh, draft.answer_zh,
            draft.question_en, draft.answer_en, *draft.keywords,
        )
        if not all((draft.question_zh, draft.answer_zh, draft.question_en, draft.answer_en)):
            raise FaqDraftUnavailableError()
        if any(_LINK_PATTERN.search(text) for text in texts):
            raise FaqDraftUnavailableError()
        if any(contains_sensitive_text(text) for text in texts):
            raise FaqDraftUnavailableError()
        quotes = [quote.strip() for quote in draft.source_quotes]
        if not quotes or any(
            not quote or not any(quote in message for message in staff_messages)
            for quote in quotes
        ):
            raise FaqDraftUnavailableError()

    async def generate_from_staff(
        self,
        *,
        guest_question: str,
        canonical_question: str,
        staff_messages: list[str],
    ) -> StaffDraft:
        """按管家消息整理双语草稿；输入由调用方净化并按预算截断。"""
        system_prompt = (
            "你是武汉一家7间房民宿的资料编辑，只为管理员整理参考草稿。客人问过一个资料里"
            "没有的问题，下面是管家随后在会话里的回复。只用管家消息里明确说出的事实回答"
            "这个问题：不补充、不推测、不推广到其他房间，不写有效期。管家消息里找不到"
            "答案时 found_answer 为 false。答案只对这位客人或这次入住成立时 guest_only 为 "
            "true。source_quotes 逐字摘录答案所依据的管家原话片段（必须是原文中连续的一段）。"
            "不得输出网址、Markdown链接、密码、验证码或客人身份。只输出JSON，字段为"
            "found_answer、guest_only、category、question_zh、answer_zh、question_en、"
            "answer_en、keywords、source_quotes。"
        )
        payload = {
            "guest_question": guest_question.strip(),
            "canonical_question": canonical_question.strip(),
            "staff_messages": staff_messages,
        }
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                max_tokens=1200,
            )
            content = response.choices[0].message.content or ""
            draft = StaffDraft.model_validate_json(content)
            self.validate_staff_draft(draft, staff_messages)
            return draft
        except FaqDraftUnavailableError:
            raise
        except Exception as error:
            # 统一为无正文领域异常，禁止把模型响应或管家消息写入日志。
            raise FaqDraftUnavailableError() from error
