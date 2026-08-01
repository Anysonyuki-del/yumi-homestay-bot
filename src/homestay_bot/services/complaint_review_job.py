import re
from typing import Any, Protocol

from homestay_bot.domain.enums import ComplaintReviewStatus
from homestay_bot.integrations.deepseek_complaint import DeepSeekComplaintAnalyzer


class ComplaintReviewRepositoryPort(Protocol):
    """定义客诉后台任务需要的持久化边界。"""

    async def get(self, review_id: int) -> Any | None:
        """读取客诉记录。"""

    async def mark_ready(
        self, review_id: int, *, analysis: dict[str, Any], draft: str
    ) -> Any:
        """保存脱敏分析和回复草稿。"""


class ComplaintMessageContextPort(Protocol):
    """定义按来源消息读取客诉上下文的边界。"""

    async def list_context(
        self, conversation_id: int, source_message_id: str
    ) -> list[dict[str, str]]:
        """返回不含身份信息的最近对话。"""


class SQLAlchemyComplaintMessageContext:
    """把消息仓储转换为客诉分析所需的最小上下文。"""

    def __init__(self, repository: Any) -> None:
        """绑定 SQLAlchemy 消息仓储。"""
        self._repository = repository

    async def list_context(
        self, conversation_id: int, source_message_id: str
    ) -> list[dict[str, str]]:
        """只读取来源消息之前的文本，并丢弃数据库身份字段。"""
        messages = await self._repository.list_recent(
            conversation_id,
            12,
            through_external_message_id=source_message_id,
        )
        result: list[dict[str, str]] = []
        for message in messages:
            if not message.content:
                continue
            role = "user" if str(message.origin) == "guest" else "assistant"
            result.append(
                {
                    "role": role,
                    "content": self._sanitize(str(message.content))[:800],
                }
            )
        return result

    @staticmethod
    def _sanitize(content: str) -> str:
        """在模型边界遮盖手机号、邮箱和长编号。"""
        content = re.sub(r"(?<!\d)\d{11}(?!\d)", "[手机号已脱敏]", content)
        content = re.sub(
            r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
            "[邮箱已脱敏]",
            content,
            flags=re.IGNORECASE,
        )
        return re.sub(r"(?<!\d)\d{12,}(?!\d)", "[编号已脱敏]", content)


class ComplaintNotificationPort(Protocol):
    """定义员工通知发送边界。"""

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """登记一条员工内部通知。"""

    async def send_internal_card(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        title: str,
        description: str,
        url: str,
    ) -> None:
        """登记一条只能打开后台的员工卡片。"""


class ComplaintReviewJobService:
    """异步生成客诉分析，完成后只通知员工进入后台复核。"""

    def __init__(
        self,
        *,
        reviews: ComplaintReviewRepositoryPort,
        analyzer: DeepSeekComplaintAnalyzer,
        messages: ComplaintMessageContextPort,
        notifications: ComplaintNotificationPort,
        employee_userids: list[str],
        agent_id: int,
        edit_url: str,
    ) -> None:
        """注入客诉记录、分析器、上下文读取和事务型通知。"""
        self._reviews = reviews
        self._analyzer = analyzer
        self._messages = messages
        self._notifications = notifications
        self._employee_userids = employee_userids
        self._agent_id = agent_id
        self._edit_url = edit_url.rstrip("/")

    async def handle(self, payload: dict[str, Any]) -> None:
        """按客诉编号幂等生成分析并发送后台复核提醒。"""
        review_id = int(payload["review_id"])
        review = await self._reviews.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        if review.status is not ComplaintReviewStatus.PENDING_ANALYSIS and str(
            review.status
        ) != ComplaintReviewStatus.PENDING_ANALYSIS.value:
            return
        context = await self._messages.list_context(
            review.conversation_id,
            review.source_message_id,
        )
        draft = await self._analyzer.generate(
            reason=review.reason,
            risk_level=review.risk_level,
            messages=context,
            customer_context={},
        )
        await self._reviews.mark_ready(
            review_id,
            analysis=draft.model_dump(exclude={"reply_draft"}),
            draft=draft.reply_draft,
        )
        if not self._employee_userids:
            return
        analysis = draft.model_dump()
        await self._notifications.send_internal_card(
            agent_id=self._agent_id,
            employee_userids=self._employee_userids,
            title="客诉待复核",
            description=(
                f"风险：{review.risk_level}；核心：{analysis['core_issue']}；"
                f"诉求：{analysis['customer_request']}"
            ),
            url=f"{self._edit_url}/{review_id}",
        )
