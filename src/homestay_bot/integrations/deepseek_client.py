import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.tourism import (
    is_tourism_query,
    latest_user_question,
)
from homestay_bot.services.answer_policy import (
    is_property_specific,
    is_transaction_sensitive,
)
from homestay_bot.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

_ASSISTANT_FAILURE_REPLIES = {
    "暂时无法处理这个问题，已为您通知工作人员协助，请稍候。",
    "暂时无法查询实时旅游信息，已为您通知工作人员协助，请稍候。",
    (
        "I’m temporarily unable to process this request. "
        "A staff member has been notified to help you."
    ),
    (
        "I’m unable to check live travel information right now. "
        "A staff member has been notified to help you."
    ),
}


class AssistantUnavailableError(RuntimeError):
    """表示普通模型无法生成可安全发送的客服决定。"""


class BookingFields(BaseModel):
    """保存模型从对话中提取的非最终预订字段。"""

    check_in_date: str | None = None
    check_out_date: str | None = None
    number_of_guests: int | None = None
    guest_name: str | None = None
    guest_mobile: str | None = None
    room_type_preference: str | None = None
    special_requests: str | None = None


class AssistantDecision(BaseModel):
    """约束模型每轮回复、风险标记和员工提醒决定。"""

    reply_text: str
    language: Language
    intent: str
    confidence: float = Field(ge=0, le=1)
    handoff_reason: str | None = None
    booking_fields: BookingFields | None = None
    knowledge_gap: bool = False
    knowledge_gap_topic: str | None = None
    staff_confirmation_required: bool = False
    staff_confirmation_reason: str | None = None


class TourismSearcher(Protocol):
    """定义客服助手所需的实时旅游搜索边界。"""

    async def search(
        self,
        *,
        question: str,
        language: Language,
        queried_on: date,
    ) -> str:
        """返回带查询日期和来源名称的无链接旅游回复。"""


class ReadOnlyToolExecutor(Protocol):
    """定义模型允许调用的只读业务工具。"""

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """执行白名单内只读查询。"""


class HostexReadOnlyClient(Protocol):
    """定义模型查询所需的百居易只读接口。"""

    async def list_properties(self) -> list[Any]:
        """返回物理房间。"""

    async def list_availabilities(
        self,
        property_ids: list[int],
        start_date: str,
        end_date: str,
    ) -> list[Any]:
        """返回指定日期房态。"""

    async def list_reference_prices(
        self,
        start_date: str,
        end_date: str,
    ) -> list[Any]:
        """返回渠道日历参考价。"""


class HostexReadOnlyToolExecutor:
    """把 DeepSeek 工具映射到百居易只读查询。"""

    def __init__(self, hostex: HostexReadOnlyClient) -> None:
        """注入不包含写入能力的百居易客户端。"""
        self._hostex = hostex

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """执行白名单查询并返回可序列化结果。"""
        if name == "search_availability":
            properties = await self._hostex.list_properties()
            result = await self._hostex.list_availabilities(
                [item.id for item in properties],
                arguments["check_in_date"],
                arguments["check_out_date"],
            )
        elif name == "search_reference_price":
            result = await self._hostex.list_reference_prices(
                arguments["check_in_date"],
                arguments["check_out_date"],
            )
        else:
            raise ValueError(f"不允许执行工具: {name}")
        return [item.model_dump(mode="json") for item in result]


def assistant_decision_schema() -> dict[str, Any]:
    """返回供模型提示和本地校验共享的扁平 JSON 结构。"""
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "reply_text": {"type": "string"},
            "language": {"type": "string", "enum": ["zh", "en"]},
            "intent": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "handoff_reason": nullable_string,
            "booking_fields": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "check_in_date": nullable_string,
                            "check_out_date": nullable_string,
                            "number_of_guests": {
                                "anyOf": [
                                    {"type": "integer"},
                                    {"type": "null"},
                                ]
                            },
                            "guest_name": nullable_string,
                            "guest_mobile": nullable_string,
                            "room_type_preference": nullable_string,
                            "special_requests": nullable_string,
                        },
                    },
                    {"type": "null"},
                ]
            },
            "knowledge_gap": {"type": "boolean"},
            "knowledge_gap_topic": nullable_string,
            "staff_confirmation_required": {"type": "boolean"},
            "staff_confirmation_reason": nullable_string,
        },
    }


def _wuhan_today() -> date:
    """返回武汉时区当前自然日。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


class DeepSeekGuestAssistant:
    """使用 DeepSeek Chat 和独立旅游搜索生成客服决定。"""

    def __init__(
        self,
        *,
        chat_client: Any,
        tourism_searcher: TourismSearcher,
        knowledge: KnowledgeService,
        model: str,
        safety_hmac_key: bytes,
        tool_executor: ReadOnlyToolExecutor | None = None,
        local_date_provider: Callable[[], date] | None = None,
    ) -> None:
        """注入 DeepSeek、知识、旅游搜索和只读工具。"""
        self._chat_client = chat_client
        self._tourism_searcher = tourism_searcher
        self._knowledge = knowledge
        self._model = model
        self._safety_hmac_key = safety_hmac_key
        self._tool_executor = tool_executor
        self._local_date_provider = local_date_provider or _wuhan_today

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        """只暴露房态和参考价查询函数。"""
        date_parameters = {
            "type": "object",
            "properties": {
                "check_in_date": {"type": "string", "format": "date"},
                "check_out_date": {"type": "string", "format": "date"},
            },
            "required": ["check_in_date", "check_out_date"],
            "additionalProperties": False,
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_availability",
                    "description": "查询指定入住和退房日期的物理房间可用性。",
                    "parameters": date_parameters,
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_reference_price",
                    "description": "查询渠道日历参考价，结果不是最终成交价。",
                    "parameters": date_parameters,
                },
            },
        ]

    @staticmethod
    def _minimize_personal_data(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """移除失败轮次，并在非预订阶段隐藏姓名和手机号。"""
        cleaned: list[dict[str, str]] = []
        for item in messages:
            if (
                item.get("role") == "assistant"
                and item.get("content") in _ASSISTANT_FAILURE_REPLIES
            ):
                # 失败文案不是模型知识；连同其未成功回答的问题一起移除，
                # 防止下一轮模仿失败文案或回答过期问题。
                if cleaned and cleaned[-1].get("role") == "user":
                    cleaned.pop()
                continue
            cleaned.append(item)

        combined = "\n".join(item.get("content", "") for item in cleaned)
        if re.search(
            r"预订|订房|下单|booking|reservation|reserve",
            combined,
            re.IGNORECASE,
        ):
            return [dict(item) for item in cleaned]
        minimized: list[dict[str, str]] = []
        for item in cleaned:
            content = re.sub(
                r"(?<!\d)1[3-9]\d{9}(?!\d)",
                "[手机号已隐藏]",
                item.get("content", ""),
            )
            content = re.sub(
                r"(?:我叫|姓名(?:是|[:：])?)\s*[\u4e00-\u9fff]{2,4}",
                "[姓名已隐藏]",
                content,
            )
            minimized.append({**item, "content": content})
        return minimized

    def _validate_decision(
        self,
        output_text: str,
        question_text: str,
        *,
        property_knowledge_grounded: bool,
    ) -> AssistantDecision:
        """校验模型 JSON，并执行确定性风险归一化。"""
        decision = AssistantDecision.model_validate_json(output_text)
        updates: dict[str, Any] = {"handoff_reason": None}
        property_specific = is_property_specific(question_text)
        transaction_sensitive = is_transaction_sensitive(question_text)
        if not property_specific and not transaction_sensitive:
            updates.update(
                {
                    "knowledge_gap": False,
                    "knowledge_gap_topic": None,
                    "staff_confirmation_required": False,
                    "staff_confirmation_reason": None,
                }
            )
        elif property_specific and not property_knowledge_grounded:
            topic = self._property_topic(question_text)
            safe_reply = (
                f"当前审核资料尚未确认{topic}信息。"
                "建议到店前由工作人员进一步确认，"
                "并先准备不依赖该信息的替代安排。"
            )
            if topic == "停车":
                safe_reply = (
                    "当前审核资料尚未确认民宿停车信息。"
                    "建议先考虑附近公共停车场或合规停车位，"
                    "到店前再请工作人员确认周边停车安排。"
                )
            updates.update(
                {
                    "reply_text": safe_reply,
                    "knowledge_gap": True,
                    "knowledge_gap_topic": "property_information",
                    "staff_confirmation_required": False,
                    "staff_confirmation_reason": None,
                }
            )
        elif decision.staff_confirmation_required:
            updates.update({"knowledge_gap": False, "knowledge_gap_topic": None})
        elif decision.confidence < 0.7 and is_transaction_sensitive(question_text):
            updates.update(
                {
                    "knowledge_gap": False,
                    "knowledge_gap_topic": None,
                    "staff_confirmation_required": True,
                    "staff_confirmation_reason": "low_confidence_transaction",
                }
            )
        elif decision.confidence < 0.7 and is_property_specific(question_text):
            updates.update(
                {
                    "knowledge_gap": True,
                    "knowledge_gap_topic": (
                        decision.knowledge_gap_topic or "property_information"
                    ),
                    "staff_confirmation_required": False,
                    "staff_confirmation_reason": None,
                }
            )
        return decision.model_copy(update=updates)

    @staticmethod
    def _property_topic(question_text: str) -> str:
        """从专属问题中提取用于知识匹配和安全回复的主题。"""
        for topic in (
            "停车",
            "早餐",
            "宠物",
            "加床",
            "电梯",
            "厨房",
            "洗衣",
            "发票",
            "接送",
            "无障碍",
            "吸烟",
            "行李寄存",
            "距离",
        ):
            if topic in question_text:
                return topic
        return "民宿专属"

    @classmethod
    def _has_relevant_property_knowledge(
        cls,
        question_text: str,
        knowledge: list[Any],
    ) -> bool:
        """判断审核知识是否明确覆盖当前民宿专属主题。"""
        topic = cls._property_topic(question_text)
        if topic == "民宿专属":
            return False
        corpus = "\n".join(
            f"{item.question}\n{item.answer}" for item in knowledge
        )
        return topic in corpus

    @staticmethod
    def _should_force_availability(question_text: str) -> bool:
        """有完整入住退房信息的房态问题必须调用百居易。"""
        asks_availability = re.search(
            r"有房|几间房|房态|可订|availability",
            question_text,
            re.IGNORECASE,
        )
        has_stay_range = (
            ("入住" in question_text and "退房" in question_text)
            or (
                re.search(r"今天|今晚|今日", question_text) is not None
                and re.search(r"明天|明日|后天", question_text) is not None
            )
            or len(re.findall(r"\d{4}-\d{2}-\d{2}", question_text)) >= 2
        )
        return asks_availability is not None and has_stay_range

    async def respond(
        self,
        *,
        guest_identifier: str,
        language: Language,
        messages: list[dict[str, str]],
    ) -> AssistantDecision:
        """调用 DeepSeek，并把连续失败收敛为统一领域异常。"""
        question_text = latest_user_question(messages)["content"]
        local_today = self._local_date_provider()
        if is_tourism_query(messages):
            reply = await self._tourism_searcher.search(
                question=question_text,
                language=language,
                queried_on=local_today,
            )
            return AssistantDecision(
                reply_text=reply,
                language=language,
                intent="tourism",
                confidence=0.95,
            )

        knowledge = await self._knowledge.build_context(language)
        tomorrow = local_today + timedelta(days=1)
        day_after = local_today + timedelta(days=2)
        system_prompt = (
            "你是武汉一家7间房民宿的客服。请只输出 JSON，不要输出代码围栏。"
            "审核知识未覆盖普通常识时可以谨慎回答；民宿专属事实未确认时，"
            "明确说明未确认、提供替代建议并设置 knowledge_gap=true；"
            "价格、房态、退款、取消、改期、付款或订单状态无法确认时不得猜测，"
            "设置 staff_confirmation_required=true；缺少查询日期时允许追问。"
            f"武汉当前日期：{local_today.isoformat()}；"
            f"今天={local_today.isoformat()}，明天={tomorrow.isoformat()}，"
            f"后天={day_after.isoformat()}。相对日期必须自主换算。"
            f"审核知识：{json.dumps([item.__dict__ for item in knowledge], ensure_ascii=False)}"
            f"输出结构：{json.dumps(assistant_decision_schema(), ensure_ascii=False)}"
        )
        request = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *self._minimize_personal_data(messages),
            ],
            "response_format": {"type": "json_object"},
            "extra_body": {"thinking": {"type": "disabled"}},
            "tools": self.tool_definitions(),
            "tool_choice": (
                {
                    "type": "function",
                    "function": {"name": "search_availability"},
                }
                if self._should_force_availability(question_text)
                else "auto"
            ),
        }
        property_knowledge_grounded = self._has_relevant_property_knowledge(
            question_text,
            knowledge,
        )
        for attempt in range(1, 3):
            try:
                active_request = {**request}
                for _tool_round in range(4):
                    response = await self._chat_client.chat.completions.create(
                        **active_request
                    )
                    message = response.choices[0].message
                    tool_calls = list(message.tool_calls or [])
                    if not tool_calls:
                        return self._validate_decision(
                            message.content or "",
                            question_text,
                            property_knowledge_grounded=(
                                property_knowledge_grounded
                            ),
                        )
                    if self._tool_executor is None:
                        raise AssistantUnavailableError()
                    active_messages = list(active_request["messages"])
                    active_messages.append(
                        message.model_dump(exclude_none=True)
                    )
                    for call in tool_calls:
                        arguments = json.loads(call.function.arguments)
                        result = await self._tool_executor.execute(
                            call.function.name,
                            arguments,
                        )
                        active_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": json.dumps(
                                    result,
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    active_request = {
                        **request,
                        "messages": active_messages,
                        "tool_choice": "auto",
                    }
                raise AssistantUnavailableError()
            except AssistantUnavailableError:
                raise
            except (
                IndexError,
                TypeError,
                ValidationError,
                ValueError,
            ) as error:
                # 只记录异常类型，不写响应正文或请求参数，避免日志泄露客人信息。
                logger.warning(
                    "DeepSeek 对话调用失败，准备重试：attempt=%s error_type=%s",
                    attempt,
                    type(error).__name__,
                )
                continue
            except Exception as error:
                # 外部 SDK 异常同样只保留类型，供现场区分网络、限流和协议错误。
                logger.warning(
                    "DeepSeek 对话调用失败，准备重试：attempt=%s error_type=%s",
                    attempt,
                    type(error).__name__,
                )
                continue
        raise AssistantUnavailableError()
