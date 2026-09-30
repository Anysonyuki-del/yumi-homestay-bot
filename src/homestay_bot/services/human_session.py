"""管家接入与交还：企业微信原生人工会话、员工按钮卡片与给客人的状态提示。

2026-09-29 用户决定的流程：
- 客人要人工时机器人照常答复，管家收到按钮卡片（先整理接手要点），由管家点
  「接入人工」确认；此后会话进入企业微信「微信客服」原生人工接待，管家直接回复。
- 管家点「交还 AI 助手」、在会话里点「结束聊天」，或接入后空闲满时限，结束原生
  会话，客人收到结束语，机器人恢复。
- 管家一直没有接入，按原规则到时自动交还，客人收到「管家暂时没能及时回复」。

平台约束（测试号实测）：人工接待（状态 3）时机器人 send_msg 会被拒（95018），也不能
直接改回智能助手接待（95016），只能结束（状态 4）；状态变更返回的一次性 msg_code
是这时唯一能给客人发提示的途径。
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import Conversation
from homestay_bot.integrations.wecom.api_client import WeComApiError
from homestay_bot.repositories.conversations import SQLAlchemyMessageRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.handoff_card import (
    ACCEPT_KEY,
    ACCEPTED_TEXT,
    ENDED_TEXT,
    RELEASE_KEY,
    CardStage,
    HandoffCard,
    build_handoff_card,
)
from homestay_bot.services.message_service import MessageService

logger = logging.getLogger(__name__)


class KfSessionApi(Protocol):
    """人工会话流程用到的企业微信接口。"""

    async def get_service_state(self, open_kfid: str, external_userid: str) -> tuple[int, str]:
        """返回（会话状态，接待人员）。"""

    async def transfer_service_state(
        self, open_kfid: str, external_userid: str, employee_userid: str
    ) -> str:
        """转为人工接待，返回 msg_code。"""

    async def end_service_state(self, open_kfid: str, external_userid: str) -> str:
        """结束会话，返回 msg_code（可能为空）。"""

    async def send_event_text(self, code: str, content: str) -> str:
        """凭 msg_code 给客人发一句文字，返回 msgid。"""

    async def update_template_card(
        self,
        *,
        agent_id: int,
        userids: list[str],
        response_code: str,
        template_card: dict[str, Any],
    ) -> None:
        """用点击回调的 ResponseCode 更新卡片。"""


class HumanSessionService:
    """执行接入、交还与原生结束；数据库写入与调用方同一事务提交。"""

    def __init__(
        self,
        session: AsyncSession,
        api: KfSessionApi,
        *,
        agent_id: int,
        duty_userids: Sequence[str],
        card_for: Callable[[Conversation], Awaitable[HandoffCard]] | None = None,
    ) -> None:
        """`card_for` 按会话现状重建卡片内容，点击按钮后更新卡片时使用。"""
        self._session = session
        self._api = api
        self._agent_id = agent_id
        self._duty_userids = set(duty_userids)
        self._card_for = card_for
        self._operations = SQLAlchemyOperationsRepository(session)

    async def handle_card_action(self, payload: dict[str, Any]) -> None:
        """处理员工点击卡片按钮：只接受本应用、值班名单内的员工。"""
        userid = str(payload.get("userid", ""))
        if int(payload.get("agent_id") or 0) != self._agent_id or userid not in self._duty_userids:
            # 非值班员工或其他应用的回调不执行任何动作，也不改卡片。
            logger.warning("忽略非值班员工的卡片操作：agent_id=%s", payload.get("agent_id"))
            return
        action, _, raw_id = str(payload.get("key", "")).partition(":")
        conversation = (
            await self._session.get(Conversation, int(raw_id)) if raw_id.isdigit() else None
        )
        if conversation is None or action not in {ACCEPT_KEY, RELEASE_KEY}:
            return
        if action == ACCEPT_KEY:
            stage, note, replace_text = await self._accept(conversation, userid)
        else:
            stage, note, replace_text = await self._release(conversation)
        response_code = str(payload.get("response_code", ""))
        if not response_code or self._card_for is None:
            return
        card = await self._card_for(conversation)
        try:
            await self._api.update_template_card(
                agent_id=self._agent_id,
                userids=[userid],
                response_code=response_code,
                template_card=build_handoff_card(
                    card,
                    stage=stage,
                    note=note,
                    replace_text=replace_text,
                    task_id=str(payload.get("task_id", "")) or None,
                ),
            )
        except Exception as error:
            # 动作已经生效；卡片没更新只影响显示，不能因此回滚或重做接入。
            logger.warning("更新转人工卡片失败：error_type=%s", type(error).__name__)

    async def _accept(self, conversation: Conversation, userid: str) -> tuple[CardStage, str, str]:
        """把会话转给点按钮的管家；已被别人接入或平台拒绝时只更新卡片说明。"""
        state, servicer = await self._api.get_service_state(
            conversation.open_kfid, conversation.external_userid
        )
        if state == 3:
            if servicer != userid:
                return "closed", "这位客人已由其他管家接待。", "已有管家接入"
            if not await self._operations.native_session_active(conversation.id):
                await self._operations.accept_handoff(
                    conversation.id, servicer_userid=userid, now=datetime.now(UTC)
                )
            return "accepted", "", ""
        try:
            code = await self._api.transfer_service_state(
                conversation.open_kfid, conversation.external_userid, userid
            )
        except WeComApiError as error:
            logger.warning("接入人工失败：error_code=%s", error.error_code)
            return (
                "closed",
                f"接入失败（错误码 {error.error_code}）：请确认你是该微信客服账号的接待人员，"
                "且状态为「接待中」。",
                "接入失败",
            )
        await self._operations.accept_handoff(
            conversation.id, servicer_userid=userid, now=datetime.now(UTC)
        )
        await self._send_event_text(conversation, code, ACCEPTED_TEXT)
        return "accepted", "", ""

    async def _release(self, conversation: Conversation) -> tuple[CardStage, str, str]:
        """交还机器人：走统一的交还入口（会登记结束原生会话）；已交还时补一次结束复核。"""
        released = await self._operations.release_conversation(
            conversation.id, now=datetime.now(UTC), reason="employee_card"
        )
        if not released:
            # 会话已是机器人模式（自动交还过等），原生会话若仍挂着也要结束，客人才能收到回复。
            await self.end_native_session(conversation.id)
        return "closed", "已交还，客人下一条消息起由机器人回复。", "已交还 AI 助手"

    async def end_native_session(
        self, conversation_id: int, *, handoff_id: int | None = None
    ) -> None:
        """结束仍在人工接待的原生会话并发结束语；其他状态什么都不做（任务可重复执行）。

        `handoff_id` 是登记结束任务时对应的那次接管：任务延迟或重试期间若已有新的
        接入（最新接管编号变了），这个任务就不属于当前会话，跳过，不能结束新接入的
        管家会话（Codex 审查 H2）。卡片按钮对当前会话的直接交还不带编号。
        """
        conversation = await self._session.get(Conversation, conversation_id)
        if conversation is None:
            return
        if (
            handoff_id is not None
            and await self._operations.latest_handoff_id(conversation_id) != handoff_id
        ):
            logger.info("跳过过期的结束会话任务：conversation_id=%s", conversation_id)
            return
        state, _ = await self._api.get_service_state(
            conversation.open_kfid, conversation.external_userid
        )
        if state != 3:
            return
        code = await self._api.end_service_state(
            conversation.open_kfid, conversation.external_userid
        )
        await self._send_event_text(conversation, code, ENDED_TEXT)

    async def on_servicer_ended(
        self, open_kfid: str, external_userid: str, msg_code: str, occurred_at: datetime | None
    ) -> None:
        """管家在企业微信里点了「结束聊天」：交还机器人，并凭事件 msg_code 发结束语。

        只响应管家经按钮接入、仍在原生人工接待的会话：本系统自己调接口结束时会话已先
        切回机器人模式；补拉还可能带来很久以前的结束事件——1.58.1 测试号上，13:3x 的
        「结束聊天」事件在 14:21 客人刚转人工 11 秒后才被拉到，把新的转人工误交还了。
        1.58.2 又发现：空游标同步会重放几天内的全部事件，管家刚点「接入人工」，旧的
        结束事件就把这次接入结束了。所以还要求事件发生在这次接入之后（事件时间只到秒）。
        """
        conversation = await self._session.scalar(
            select(Conversation).where(
                Conversation.open_kfid == open_kfid,
                Conversation.external_userid == external_userid,
            )
        )
        if (
            conversation is None
            or occurred_at is None
            or not await self._operations.native_session_active(conversation.id)
        ):
            return
        accepted_at = await self._operations.accepted_at(conversation.id)
        if accepted_at is None or occurred_at < accepted_at.replace(microsecond=0):
            return
        await self._operations.release_conversation(
            conversation.id, now=datetime.now(UTC), reason="servicer_end"
        )
        await self._send_event_text(conversation, msg_code, ENDED_TEXT)

    async def _send_event_text(self, conversation: Conversation, code: str, text: str) -> None:
        """凭一次性 msg_code 发提示并记入对话记录；发送失败只记日志，不影响状态切换。"""
        if not code:
            return
        try:
            msgid = await self._api.send_event_text(code, text)
        except Exception as error:
            logger.warning("会话状态提示发送失败：error_type=%s", type(error).__name__)
            return
        if msgid:
            await MessageService(SQLAlchemyMessageRepository(self._session)).record_bot(
                conversation.id,
                msgid,
                text,
                metadata={"delivery_status": "accepted", "via": "session_event"},
            )
