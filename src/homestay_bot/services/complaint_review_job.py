import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any, Protocol

from homestay_bot.domain.enums import ComplaintReviewStatus
from homestay_bot.integrations.deepseek_complaint import DeepSeekComplaintAnalyzer
from homestay_bot.services.message_service import model_message_content


class ComplaintReviewRepositoryPort(Protocol):
    """定义客诉后台任务需要的持久化边界。"""

    async def get(self, review_id: int) -> Any | None:
        """读取客诉记录。"""

    async def mark_ready(
        self,
        review_id: int,
        *,
        analysis: dict[str, Any],
        draft: str,
        expected_version: int | None = None,
    ) -> Any | None:
        """保存脱敏分析和回复草稿；客诉已变化时返回 None。"""


class ComplaintMessageContextPort(Protocol):
    """定义按来源消息读取客诉上下文的边界。"""

    async def list_context(
        self, conversation_id: int, source_message_id: str
    ) -> list[dict[str, str]]:
        """返回不含身份信息的最近对话。"""


class SQLAlchemyComplaintMessageContext:
    """把消息仓储转换为客诉分析所需的最小上下文。"""

    # 这些规则只在发送给客诉分析模型前执行，避免凭证和可定位住址越过边界。
    _REDACTION_RULES = (
        (
            re.compile(
                r"(?:详细地址|地址)\s*[:：]?\s*"
                r"(?:湖北省)?武汉市?"
                r"(?:洪山区|武昌区|青山区|汉阳区|江汉区|江岸区|硚口区|"
                r"蔡甸区|东西湖区|黄陂区|新洲区|江夏区)"
                r"[^，。；;\n]{0,60}(?:路|街|大道|小区|号|栋|室)"
                r"[^，。；;\n]{0,20}"
            ),
            "[详细地址已脱敏]",
        ),
        (
            re.compile(
                r"(?:门锁|房门|开门)\s*密码\s*(?:是|为)?\s*[:：]?"
                r"[A-Za-z0-9_-]{4,}"
            ),
            "[门锁密码已过滤]",
        ),
        (
            re.compile(
                r"验证码\s*(?:是|为)?\s*[:：]?\s*[A-Za-z0-9_-]{4,}"
            ),
            "[验证码已过滤]",
        ),
        (
            re.compile(
                r"(?:二维码|QR\s*code)\s*[:：]?\s*"
                r"(?:https?://\S+|[A-Za-z0-9+/=_-]{4,})",
                re.IGNORECASE,
            ),
            "[二维码已过滤]",
        ),
        (
            re.compile(
                r"(?:入住指南|入住说明|入住凭证|开门指南)\s*[:：]?"
                r"[^\n。；;!?！？]*[。；;!?！？]?"
            ),
            "[入住凭证内容已过滤]",
        ),
        (
            re.compile(r"https?://\S+", re.IGNORECASE),
            "[链接已过滤]",
        ),
    )

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
                    "content": self._sanitize(model_message_content(message))[:800],
                }
            )
        return result

    @staticmethod
    def _sanitize(content: str) -> str:
        """在模型边界遮盖身份信息、凭证、地址和链接。"""
        content = re.sub(r"(?<!\d)\d{11}(?!\d)", "[手机号已脱敏]", content)
        content = re.sub(
            r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
            "[邮箱已脱敏]",
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(r"(?<!\d)\d{12,}(?!\d)", "[编号已脱敏]", content)
        for pattern, replacement in SQLAlchemyComplaintMessageContext._REDACTION_RULES:
            content = pattern.sub(replacement, content)
        return content


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
        atomic: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        """注入客诉记录、分析器、上下文读取和事务型通知。

        atomic 把「写入就绪」与「登记复核卡片」包成一个保存点：worker 在任务失败时
        不回滚，会连同已做的写入一起提交，两步之间任何一步失败都必须一起撤销。
        生产装配传入 session.begin_nested。
        """
        self._reviews = reviews
        self._analyzer = analyzer
        self._messages = messages
        self._notifications = notifications
        self._employee_userids = employee_userids
        self._agent_id = agent_id
        self._edit_url = edit_url.rstrip("/")
        self._atomic = atomic or nullcontext

    async def handle(self, payload: dict[str, Any]) -> None:
        """按客诉编号幂等生成分析并发送后台复核提醒。"""
        review_id = int(payload["review_id"])
        review = await self._reviews.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        if review.status not in {
            ComplaintReviewStatus.PENDING_ANALYSIS,
            ComplaintReviewStatus.RETURNED,
        } and str(review.status) not in {
            ComplaintReviewStatus.PENDING_ANALYSIS.value,
            ComplaintReviewStatus.RETURNED.value,
        }:
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
        analysis = draft.model_dump()
        async with self._atomic():
            # 先用条件更新写入就绪，成功才登记卡片：迟到的分析（客诉已关闭或已被
            # 改动）不会重新打开客诉，也不会发出一张指向过期内容的「待复核」卡片。
            # 卡片登记失败时保存点撤销就绪状态，任务重试会重新生成入口。
            ready = await self._reviews.mark_ready(
                review_id,
                analysis=draft.model_dump(exclude={"reply_draft"}),
                draft=draft.reply_draft,
                expected_version=review.version,
            )
            if ready is None or not self._employee_userids:
                return
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
