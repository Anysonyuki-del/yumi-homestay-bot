from datetime import UTC, datetime

import pytest

from homestay_bot.domain.enums import Language, MessageOrigin
from homestay_bot.domain.models import Conversation, Message
from homestay_bot.integrations.deepseek_delivery_rewriter import (
    DeliveryRewriteUnavailableError,
)
from homestay_bot.repositories.conversations import DeliveryRewriteContext
from homestay_bot.services.delivery_rewrite_job import (
    GuestDeliveryRewriteJobService,
    _deterministic_fact_fallback,
)


async def no_op_checkpoint() -> None:
    """模拟模型调用前的持久化提交。"""


def rewrite_context() -> DeliveryRewriteContext:
    """构造一条首次安全拦截的天气回复上下文。"""
    conversation = Conversation(
        id=7,
        open_kfid="wk-1",
        external_userid="wm-1",
        language=Language.ZH,
    )
    guest = Message(
        id=10,
        conversation_id=7,
        external_message_id="guest-1",
        origin=MessageOrigin.GUEST,
        message_type="text",
        content="明天天气",
        message_metadata={},
        sent_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    failed = Message(
        id=11,
        conversation_id=7,
        external_message_id="bot-1",
        origin=MessageOrigin.BOT,
        message_type="text",
        content=(
            "武汉8月22日有阵雨，气温25～31℃，午后降雨概率70%。"
            "我们民宿有伞可借用。出门记得带伞。"
        ),
        message_metadata={
            "delivery_status": "failed",
            "delivery_error_code": "wecom_async_13",
            "delivery_retry_count": 0,
            "delivery_rewrite_pending": True,
        },
        sent_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    return DeliveryRewriteContext(failed, guest, conversation)


class RepositoryStub:
    """提供并记录安全改写上下文。"""

    def __init__(self, context: DeliveryRewriteContext | None) -> None:
        """保存待返回上下文。"""
        self.context = context
        self.saved: dict[str, object] | None = None

    async def get_delivery_rewrite_context(self, failed_bot_id: int):
        """按内部消息编号返回上下文。"""
        assert failed_bot_id == 11
        return self.context

    async def save_delivery_rewrite_metadata(self, message, metadata):
        """记录改写完成后的审计字段。"""
        self.saved = dict(metadata)


class RewriterStub:
    """返回指定的安全改写或领域异常。"""

    def __init__(self, result: str | Exception) -> None:
        """保存预期结果。"""
        self.result = result
        self.calls = 0

    async def rewrite(self, **kwargs) -> str:
        """记录一次调用并返回结果。"""
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class OutboxStub:
    """记录客人二次投递和员工通知。"""

    def __init__(self) -> None:
        """初始化记录列表。"""
        self.guest_sends: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.staff_sends: list[dict[str, object]] = []

    async def send_text(self, *args, **kwargs) -> str:
        """记录客人正文并返回稳定出站编号。"""
        self.guest_sends.append((args, kwargs))
        return "outbox-rewrite-11"

    async def send_internal_text(self, **kwargs) -> None:
        """记录脱敏员工跟进通知。"""
        self.staff_sends.append(kwargs)


@pytest.mark.asyncio
async def test_rewrite_job_sends_full_reorganized_reply_once() -> None:
    """有效模型改写应完整发送一次，并关联原失败回复。"""
    context = rewrite_context()
    repository = RepositoryStub(context)
    rewriter = RewriterStub(
        "武汉8月22日有阵雨，气温25～31℃，午后降雨概率70%。出门记得带伞。"
    )
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    assert rewriter.calls == 1
    assert len(outbox.guest_sends) == 1
    args, kwargs = outbox.guest_sends[0]
    assert args[2].startswith("武汉8月22日有阵雨")
    assert kwargs == {
        "delivery_retry_count": 1,
        "retry_of_message_id": "11",
    }
    assert outbox.staff_sends == []
    assert repository.saved is not None
    assert repository.saved["delivery_retry_count"] == 1
    assert repository.saved["delivery_rewrite_pending"] is False


@pytest.mark.asyncio
async def test_rewrite_failure_uses_weather_fact_fallback_and_notifies_staff() -> None:
    """模型改写无效时才发送天气事实短兜底，并登记人工跟进。"""
    context = rewrite_context()
    repository = RepositoryStub(context)
    rewriter = RewriterStub(DeliveryRewriteUnavailableError("invalid"))
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    content = str(outbox.guest_sends[0][0][2])
    assert "8月22日" in content
    assert "25～31℃" in content
    assert "70%" in content
    assert "我们民宿" not in content
    assert "正在为您核实" not in content
    assert len(outbox.staff_sends) == 1
    assert repository.saved is not None
    assert repository.saved["delivery_rewrite_fallback_used"] is True


@pytest.mark.asyncio
async def test_production_weather_fallback_is_short_plain_text_without_sources() -> None:
    """生产失败正文的本地兜底不得保留来源收尾、列表符或重复开场。"""
    context = rewrite_context()
    context.failed_bot.content = (
        "我帮您看了一下，我帮您看了一下武汉明天（2026年8月22日，周六）的天气。"
        "• 多云，午后局地有阵雨或雷阵雨\n"
        "• 气温：最低27℃，最高36℃（部分预报为26～34℃，"
        "以武汉市气象台最新预报为准）\n"
        "• 降雨概率约10%，午后阵雨为局地性热雷雨\n"
        "• 偏北风2～3级，阵风4～5级\n\n"
        "提醒您：明天是闷热桑拿天，出门带把晴雨伞。"
        "这是我今天（8月21日）帮您查到的最新预报，主要参考了"
        "武汉-天气预报、AccuWeather等公开信息。"
        "天气可能临时变化，出门前可以再看一眼实时情况。"
    )
    repository = RepositoryStub(context)
    rewriter = RewriterStub(DeliveryRewriteUnavailableError("invalid"))
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    content = str(outbox.guest_sends[0][0][2])
    assert content.count("我帮您看了一下") <= 1
    assert "•" not in content
    assert "。；" not in content
    assert "参考" not in content
    assert "AccuWeather" not in content
    assert "8月22日" in content
    assert "27℃" in content
    assert len(content) <= 350


@pytest.mark.parametrize(
    ("blocked_reply", "expected_fact"),
    [
        ("据武汉市气象台预报，武汉明天多云，气温27℃。", "武汉明天多云"),
        ("武汉市气象台发布的预报显示，武汉明天有阵雨，气温27℃。", "武汉明天有阵雨"),
        ("武汉明天多云，气温27℃。（数据来自武汉市气象台）", "气温27℃"),
        ("武汉明天多云。据武汉市气象台预报，降雨概率10%，气温27℃。", "降雨概率10%"),
        ("我帮您看了一下，据武汉市气象台预报，武汉明天气温27℃。", "武汉明天气温27℃"),
        ("据武汉市气象台，武汉明天多云，气温27℃。", "武汉明天多云"),
        ("信息来自武汉市气象台，武汉明天有阵雨，气温27℃。", "武汉明天有阵雨"),
        ("武汉市气象台称，武汉明天多云，气温27℃。", "武汉明天多云"),
        ("武汉市气象台预报，武汉明天多云，气温27℃。", "武汉明天多云"),
        ("武汉市气象台发布，武汉明天有阵雨，气温27℃。", "武汉明天有阵雨"),
        (
            "According to Wuhan Meteorological Service, Wuhan will be cloudy at 27°C.",
            "Wuhan will be cloudy",
        ),
        (
            "As reported by Wuhan Meteorological Service, Wuhan will be cloudy at 27°C.",
            "Wuhan will be cloudy",
        ),
    ],
)
def test_fact_fallback_removes_common_source_attribution(
    blocked_reply: str,
    expected_fact: str,
) -> None:
    """常见来源表达不得进入二次发送，同时保留后续天气事实。"""
    language = (
        Language.EN
        if blocked_reply.startswith(("According", "As reported"))
        else Language.ZH
    )
    question = "Weather tomorrow" if language is Language.EN else "明天天气"
    reply = _deterministic_fact_fallback(question, blocked_reply, language)

    assert expected_fact in reply
    assert "气象台" not in reply
    assert "数据来自" not in reply
    assert "预报显示" not in reply
    assert "According to" not in reply
    assert "As reported by" not in reply


@pytest.mark.parametrize(
    ("blocked_reply", "expected_subject"),
    [
        ("武汉明天天气预报显示，多云，气温27℃。", "武汉明天天气"),
        ("黄鹤楼开放信息显示，8:30开放，门票70元。", "黄鹤楼开放信息"),
    ],
)
def test_fact_fallback_keeps_non_source_fact_subject(
    blocked_reply: str,
    expected_subject: str,
) -> None:
    """普通事实主语中的“预报/信息显示”不得被误判为来源。"""
    reply = _deterministic_fact_fallback("实时信息", blocked_reply, Language.ZH)

    assert expected_subject in reply


def test_fact_fallback_preserves_facts_inside_attribution_parentheses() -> None:
    """括号中的来源引导可删除，但门票和开放时间必须保留。"""
    reply = _deterministic_fact_fallback(
        "黄鹤楼门票",
        "黄鹤楼（据景区公告，门票70元，8:30开放）建议提前预约。",
        Language.ZH,
    )

    assert "景区公告" not in reply
    assert "门票70元" in reply
    assert "8:30开放" in reply


@pytest.mark.parametrize(
    ("blocked_reply", "place"),
    [
        ("黄鹤楼景区称，门票70元，8:30开放。", "黄鹤楼"),
        ("东湖景区指出，今天正常开放。", "东湖"),
        ("黄鹤楼景区发布的信息显示，门票70元，8:30开放。", "黄鹤楼"),
        ("黄鹤楼景区信息显示，门票70元，8:30开放。", "黄鹤楼"),
        ("据黄鹤楼景区称，门票70元，8:30开放。", "黄鹤楼"),
    ],
)
def test_fact_fallback_keeps_attraction_subject_when_removing_attribution(
    blocked_reply: str,
    place: str,
) -> None:
    """景点兼作来源时只去机构后缀和归因动词，保留地点主体。"""
    reply = _deterministic_fact_fallback("景点开放", blocked_reply, Language.ZH)

    assert place in reply
    assert "景区称" not in reply
    assert "景区指出" not in reply


@pytest.mark.parametrize(
    "blocked_reply",
    [
        "According to Wuhan Meteorological Service: Wuhan will be cloudy, with a high of 27°C.",
        "According to the ticket office: Yellow Crane Tower is open, with tickets at 70 yuan.",
    ],
)
def test_fact_fallback_english_colon_attribution_preserves_following_fact(
    blocked_reply: str,
) -> None:
    """英文冒号来源只删除冒号前归因，不得吞掉冒号后的天气或票务事实。"""
    reply = _deterministic_fact_fallback("live information", blocked_reply, Language.EN)

    assert "According to" not in reply
    assert ("Wuhan will be cloudy" in reply) or ("Yellow Crane Tower is open" in reply)


@pytest.mark.parametrize(
    "blocked_reply",
    [
        "黄鹤楼门票70元，具体以景区预约信息为准。",
        "活动19:30开始，时间以主办方最新信息为准。",
    ],
)
def test_fact_fallback_normalizes_source_dependent_caveat(
    blocked_reply: str,
) -> None:
    """来源依赖提醒应改成自然确认提示，不得留下“具体”等残句。"""
    reply = _deterministic_fact_fallback("票务活动", blocked_reply, Language.ZH)

    assert "为准" not in reply
    assert "具体。" not in reply
    assert "活动时间。" not in reply
    assert "请再确认" in reply


@pytest.mark.parametrize("marker", ["-", "*", "+", "•", "●", "▪", "·", "1."])
def test_fact_fallback_removes_common_list_markers(marker: str) -> None:
    """二次发送必须去掉 Markdown、圆点和编号列表标记。"""
    blocked_reply = f"{marker} 多云\n{marker} 气温27℃\n{marker} 降雨概率10%"

    reply = _deterministic_fact_fallback("明天天气", blocked_reply, Language.ZH)

    assert marker not in reply
    assert "多云" in reply
    assert "27℃" in reply


@pytest.mark.parametrize("marker", ["*", "+", "•", "●", "▪", "·"])
def test_fact_fallback_removes_inline_list_separators(marker: str) -> None:
    """带空格的单行列表分隔符也必须转换为普通标点。"""
    reply = _deterministic_fact_fallback(
        "明天天气",
        f"多云 {marker} 气温27℃ {marker} 降雨概率10%。",
        Language.ZH,
    )

    assert marker not in reply
    assert "27℃" in reply


def test_fact_fallback_final_reply_never_exceeds_320_characters() -> None:
    """分类标签追加后仍必须满足二次发送的最终长度上限。"""
    blocked_reply = "武汉明天多云，气温27℃，" + "午后局地阵雨，" * 60 + "出门带伞。"

    reply = _deterministic_fact_fallback("明天天气", blocked_reply, Language.ZH)

    assert len(reply) <= 320
    assert reply.endswith(("。", "！", "？"))


@pytest.mark.asyncio
async def test_rewrite_failure_without_facts_uses_english_generic_fallback() -> None:
    """英文原回复无法可靠抽取事实时才使用英文通用兜底。"""
    context = rewrite_context()
    context.conversation.language = Language.EN
    context.source_guest.content = "Could you help?"
    context.failed_bot.content = "Our homestay can take care of it for you."
    repository = RepositoryStub(context)
    rewriter = RewriterStub(DeliveryRewriteUnavailableError("invalid"))
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    assert outbox.guest_sends[0][0][2] == (
        "I received your question and am checking the details for you."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "blocked_reply", "expected_facts"),
    [
        (
            "黄鹤楼门票和开放时间",
            "黄鹤楼门票70元，开放时间8:30～17:00。",
            ("70元", "8:30～17:00"),
        ),
        (
            "从民宿怎么去东湖",
            "到东湖约12公里，乘地铁大约45分钟。",
            ("12公里", "45分钟"),
        ),
    ],
)
async def test_rewrite_failure_keeps_ticket_and_route_facts(
    question: str,
    blocked_reply: str,
    expected_facts: tuple[str, ...],
) -> None:
    """票务与路线改写失败时仍应保留原答案中的核心事实。"""
    context = rewrite_context()
    context.source_guest.content = question
    context.failed_bot.content = blocked_reply
    repository = RepositoryStub(context)
    rewriter = RewriterStub(DeliveryRewriteUnavailableError("invalid"))
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    content = str(outbox.guest_sends[0][0][2])
    assert all(fact in content for fact in expected_facts)


@pytest.mark.asyncio
async def test_fact_fallback_removes_private_data_and_unconfirmed_commitment() -> None:
    """本地兜底不得重发联系方式、订单号或承诺工作人员上门。"""
    context = rewrite_context()
    context.failed_bot.content = (
        "武汉8月22日有阵雨，气温25～31℃。"
        "联系电话13800138000。订单号ABC123456。"
        "我们会在10分钟内安排工作人员上门。"
    )
    repository = RepositoryStub(context)
    rewriter = RewriterStub(DeliveryRewriteUnavailableError("invalid"))
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    content = str(outbox.guest_sends[0][0][2])
    assert "武汉8月22日有阵雨" in content
    assert "13800138000" not in content
    assert "ABC123456" not in content
    assert "10分钟" not in content
    assert "工作人员上门" not in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "question", "blocked_reply", "expected_prefix"),
    [
        (
            Language.ZH,
            "明天天气",
            "武汉8月22日有阵雨，气温25～31℃。",
            "天气简报：",
        ),
        (
            Language.EN,
            "What is the weather tomorrow?",
            "Wuhan weather on August 22: Showers, 25-31°C.",
            "Weather summary: ",
        ),
    ],
)
async def test_fact_fallback_never_resends_identical_blocked_text(
    language: Language,
    question: str,
    blocked_reply: str,
    expected_prefix: str,
) -> None:
    """纯事实正文也必须转换为分类短句，不能原样二次发送。"""
    context = rewrite_context()
    context.conversation.language = language
    context.source_guest.content = question
    context.failed_bot.content = blocked_reply
    repository = RepositoryStub(context)
    rewriter = RewriterStub(DeliveryRewriteUnavailableError("invalid"))
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    content = str(outbox.guest_sends[0][0][2])
    assert content != blocked_reply
    assert content.startswith(expected_prefix)


@pytest.mark.asyncio
async def test_replayed_started_job_skips_second_model_call() -> None:
    """进程中断后的任务重放只能走本地兜底，不能再次调用模型。"""
    context = rewrite_context()
    metadata = dict(context.failed_bot.message_metadata)
    metadata["delivery_rewrite_started"] = True
    context.failed_bot.message_metadata = metadata
    repository = RepositoryStub(context)
    rewriter = RewriterStub("不应再次调用")
    outbox = OutboxStub()
    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=lambda _message_id: no_op_checkpoint(),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    assert rewriter.calls == 0
    assert len(outbox.guest_sends) == 1
    assert "8月22日" in str(outbox.guest_sends[0][0][2])


@pytest.mark.asyncio
async def test_missing_rewrite_context_triggers_terminal_compensation() -> None:
    """上下文丢失不得静默完成，应触发基于消息编号的人工补偿。"""
    repository = RepositoryStub(None)
    rewriter = RewriterStub("不应调用")
    outbox = OutboxStub()
    compensated: list[int] = []

    async def compensate(message_id: int) -> None:
        """记录待补偿的失败消息。"""
        compensated.append(message_id)

    service = GuestDeliveryRewriteJobService(
        repository=repository,
        rewriter=rewriter,
        outbox_factory=lambda _message_id, _guest_message_id: outbox,
        before_model=no_op_checkpoint,
        on_unavailable=compensate,
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
    )

    await service.handle({"message_id": 11})

    assert compensated == [11]
    assert rewriter.calls == 0
    assert outbox.guest_sends == []
