import json
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_delivery_rewriter import (
    DeepSeekDeliveryRewriter,
    DeliveryRewriteUnavailableError,
)


class CompletionsStub:
    """记录改写请求并返回固定模型正文。"""

    def __init__(self, content: str) -> None:
        """保存单次模型输出。"""
        self.content = content
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """模拟 OpenAI 兼容的 JSON 响应。"""
        self.requests.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ClientStub:
    """暴露 chat.completions 测试替身。"""

    def __init__(self, content: str) -> None:
        """构造可记录请求的客户端。"""
        self.completions = CompletionsStub(content)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.mark.asyncio
async def test_rewriter_preserves_weather_facts_without_tools_or_sources() -> None:
    """改写只保留原天气事实，不携带来源、链接或工具配置。"""
    client = ClientStub(
        json.dumps(
            {
                "reply_text": (
                    "8月22日武汉有阵雨，气温25～31℃，午后降雨概率70%。"
                    "出门记得带伞。"
                )
            },
            ensure_ascii=False,
        )
    )
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    reply = await rewriter.rewrite(
        guest_question=(
            "我叫张三，身份证号420106199001011234，订单号ABC123，"
            "住在解放大道88号，邮箱a@b.com，明天天气"
        ),
        blocked_reply=(
                "武汉8月22日有阵雨，气温25～31℃，午后降雨概率70%。"
                "出门记得带伞。\n\n这是我今天（8月21日）帮您查到的最新天气信息，"
                "主要参考了武汉市气象台等公开信息。天气可能临时变化，"
                "出门前可以再看一眼实时情况。"
        ),
        language=Language.ZH,
    )

    assert "8月22日" in reply
    assert "25～31℃" in reply
    assert "70%" in reply
    assert "参考" not in reply
    request = client.completions.requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["max_tokens"] == 900
    assert "tools" not in request
    assert "主要参考了" not in request["messages"][1]["content"]
    model_input = request["messages"][1]["content"]
    for private_value in (
        "张三",
        "420106199001011234",
        "ABC123",
        "解放大道88号",
        "a@b.com",
    ):
        assert private_value not in model_input


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply_text",
    [
        "武汉8月22日有阵雨，气温25～31℃，详情见 https://example.com。",
        "武汉8月23日有阵雨，气温25～31℃。",
        "武汉8月22日有阵雨。",
        "查询日期：8月22日；参考来源：武汉市气象台。",
        "武汉8月22日有阵雨，气温25～31℃，可联系 host@example.com。",
        "武汉8月22日有阵雨，气温25～31℃，景区免费开放。",
    ],
)
async def test_rewriter_rejects_unsafe_or_fact_changing_output(reply_text: str) -> None:
    """链接、技术标签、新数字或关键数字丢失都不得进入二次投递。"""
    client = ClientStub(
        json.dumps({"reply_text": reply_text}, ensure_ascii=False)
    )
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="明天天气",
            blocked_reply="武汉8月22日有阵雨，气温25～31℃。",
            language=Language.ZH,
        )


@pytest.mark.asyncio
async def test_rewriter_rejects_unchanged_blocked_reply() -> None:
    """模型原样复述被拦截正文时不得再次发送相同内容。"""
    original = "武汉8月22日有阵雨，气温25～31℃。"
    client = ClientStub(
        json.dumps({"reply_text": original}, ensure_ascii=False)
    )
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="明天天气",
            blocked_reply=original,
            language=Language.ZH,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("北京8月22日有阵雨，气温25～31℃。", "上海8月22日有阵雨，气温25～31℃。"),
        ("武汉8月22日有阵雨，气温25～31℃。", "武汉8月22日有雷雨，气温25～31℃。"),
        ("武汉8月22日有阵雨，气温25～31℃。", "武汉22月8日有阵雨，气温25～31℃。"),
        (
            "武汉8月22日有阵雨，气温25～31℃。",
            "武汉8月22日有阵雨，气温25～31℃，推荐去黄鹤楼。",
        ),
        (
            "武汉8月22日有阵雨，气温25℃，降雨概率70%。",
            "武汉8月22日有阵雨，降雨概率25%，气温70℃。",
        ),
    ],
)
async def test_rewriter_rejects_semantic_fact_mutation(
    original: str,
    rewritten: str,
) -> None:
    """地点、天气、日期或新增推荐发生变化时必须转入本地兜底。"""
    client = ClientStub(
        json.dumps({"reply_text": rewritten}, ensure_ascii=False)
    )
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="明天天气",
            blocked_reply=original,
            language=Language.ZH,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        (
            "黄鹤楼门票70元，开放时间8:30～17:00。",
            "黄鹤楼门票70元，开放时间8:30～17:00，无需预约。",
        ),
        ("东湖距离约12公里。", "东湖距离约12公里，步行可达。"),
        ("黄鹤楼门票70元。", "黄鹤楼门票70元，支持刷卡。"),
        ("黄鹤楼开放时间8:30～17:00。", "黄鹤楼8:30～17:00正常开放。"),
        ("演出8月22日19:30开始。", "演出8月22日19:30开始，照常举行。"),
    ],
)
async def test_rewriter_rejects_new_non_numeric_core_fact(
    original: str,
    rewritten: str,
) -> None:
    """预约、开放、交通和活动状态不得在没有数字变化时凭空新增。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="请帮我重新说明",
            blocked_reply=original,
            language=Language.ZH,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten", "language"),
    [
        ("黄鹤楼门票70元。", "黄鹤楼门票70元，不必提前订票。", Language.ZH),
        ("黄鹤楼开放时间8:30～17:00。", "黄鹤楼开放时间8:30～17:00，目前营业正常。", Language.ZH),
        ("东湖距离约12公里。", "东湖距离约12公里，徒步就能到。", Language.ZH),
        ("演出8月22日19:30开始。", "演出8月22日19:30开始，将按计划举办。", Language.ZH),
        (
            "Wuhan will have showers tomorrow.",
            "Wuhan will have showers tomorrow. The air quality is excellent.",
            Language.EN,
        ),
    ],
)
async def test_rewriter_rejects_unlisted_new_claim(
    original: str,
    rewritten: str,
    language: Language,
) -> None:
    """固定短语之外的新营业、交通、活动或环境结论也不得放行。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="请安全改写",
            blocked_reply=original,
            language=language,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("The event is not cancelled.", "The event is cancelled."),
        ("No reservation is required.", "Reservation is required."),
        ("The museum is not open.", "The museum is open."),
        ("The route is not walkable.", "The route is walkable."),
        ("The event has not been cancelled.", "The event has been cancelled."),
        ("The museum does not open today.", "The museum does open today."),
        ("You do not need a reservation.", "You need a reservation."),
        ("You cannot walk there.", "You can walk there."),
    ],
)
async def test_rewriter_rejects_english_fact_polarity_change(
    original: str,
    rewritten: str,
) -> None:
    """英文否定词不得在改写中被删除并反转事实结论。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="Please rephrase this safely",
            blocked_reply=original,
            language=Language.EN,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("黄鹤楼不用预约。", "黄鹤楼要预约。"),
        ("东湖不能步行到达。", "东湖能步行到达。"),
    ],
)
async def test_rewriter_rejects_chinese_fact_polarity_change(
    original: str,
    rewritten: str,
) -> None:
    """中文否定词被删除时不得把原结论反转为肯定。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="请安全改写",
            blocked_reply=original,
            language=Language.ZH,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Paris will have showers tomorrow.", "London will have showers tomorrow."),
        ("Yellow Crane Tower is open.", "East Lake is open."),
    ],
)
async def test_rewriter_rejects_english_subject_location_change(
    original: str,
    rewritten: str,
) -> None:
    """句首或主语位置的英文专名发生替换时必须拒绝。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="Please rephrase this safely",
            blocked_reply=original,
            language=Language.EN,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("original", "rewritten", "language"),
    [
        ("东湖开放，黄鹤楼关闭。", "东湖关闭，黄鹤楼开放。", Language.ZH),
        (
            "Paris is open. London is closed.",
            "Paris is closed. London is open.",
            Language.EN,
        ),
        ("地铁不能直达，公交能直达。", "地铁能直达，公交不能直达。", Language.ZH),
        (
            "Breakfast is not included. Parking is available.",
            "Breakfast is included. Parking is not available.",
            Language.EN,
        ),
        (
            "The audio guide is not included. The cloakroom is available.",
            "The audio guide is included. The cloakroom is not available.",
            Language.EN,
        ),
        ("讲解器不能使用，寄存柜能使用。", "讲解器能使用，寄存柜不能使用。", Language.ZH),
        (
            "Breakfast is not served. Parking is permitted.",
            "Breakfast is served. Parking is not permitted.",
            Language.EN,
        ),
        ("地铁不能到达，公交能到达。", "地铁能到达，公交不能到达。", Language.ZH),
        (
            "The audio guide was not included. The cloakroom was available.",
            "The audio guide was included. The cloakroom was not available.",
            Language.EN,
        ),
        (
            "The museum remains inaccessible. The gallery remains available.",
            "The museum remains available. The gallery remains inaccessible.",
            Language.EN,
        ),
        (
            "An audio guide costs extra. A locker comes free.",
            "An audio guide comes free. A locker costs extra.",
            Language.EN,
        ),
        ("讲解服务暂停，寄存服务正常。", "讲解服务正常，寄存服务暂停。", Language.ZH),
        (
            "The museum does not allow bags but does allow cameras.",
            "The museum does allow bags but does not allow cameras.",
            Language.EN,
        ),
        (
            "Paris does not offer breakfast but does offer parking.",
            "Paris does offer breakfast but does not offer parking.",
            Language.EN,
        ),
        ("景区不能带宠物但能带相机。", "景区能带宠物但不能带相机。", Language.ZH),
        (
            "The museum does not allow bags and does allow cameras.",
            "The museum does allow bags and does not allow cameras.",
            Language.EN,
        ),
        ("景区不能带宠物且能带相机。", "景区能带宠物且不能带相机。", Language.ZH),
        ("景区不能带宠物也能带相机。", "景区能带宠物也不能带相机。", Language.ZH),
        (
            "The museum does not allow bags; it also allows cameras.",
            "The museum allows bags; it also does not allow cameras.",
            Language.EN,
        ),
        (
            "The museum does not allow bags. The museum allows cameras.",
            "The museum allows bags. The museum does not allow cameras.",
            Language.EN,
        ),
        ("景区不能带宠物。景区能带相机。", "景区能带宠物。景区不能带相机。", Language.ZH),
        ("景区允许带宠物。景区禁止带相机。", "景区禁止带宠物。景区允许带相机。", Language.ZH),
        (
            "Breakfast includes tea. Breakfast excludes coffee.",
            "Breakfast excludes tea. Breakfast includes coffee.",
            Language.EN,
        ),
        (
            "Parking permits cars. Parking prohibits motorcycles.",
            "Parking prohibits cars. Parking permits motorcycles.",
            Language.EN,
        ),
    ],
)
async def test_rewriter_rejects_claim_swapped_between_entities(
    original: str,
    rewritten: str,
    language: Language,
) -> None:
    """全局词汇守恒时也必须阻止属性在地点或交通主题之间互换。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="请安全改写",
            blocked_reply=original,
            language=language,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "original", "rewritten"),
    [
        (
            Language.EN,
            "Wuhan will have showers tomorrow.",
            "Showers are expected in Wuhan tomorrow.",
        ),
        (
            Language.EN,
            "Temperatures: 25-31°C. Showers are expected in Wuhan on August 22.",
            "Showers are expected in Wuhan on August 22. Temperatures: 25-31°C.",
        ),
    ],
)
async def test_rewriter_allows_fact_preserving_reorder(
    language: Language,
    original: str,
    rewritten: str,
) -> None:
    """事实多重集相同的中英文自然调序应保留模型改写价值。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    reply = await rewriter.rewrite(
        guest_question="Please rephrase this safely",
        blocked_reply=original,
        language=language,
    )

    assert reply == rewritten


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "original", "rewritten"),
    [
        (
            Language.ZH,
            "黄鹤楼门票70元，开放时间8:30～17:00。",
            "开放时间为8:30～17:00，黄鹤楼门票70元。",
        ),
        (
            Language.ZH,
            "武汉8月22日有阵雨，气温25℃，降雨概率70%。",
            "武汉8月22日降雨概率70%，有阵雨，气温25℃。",
        ),
    ],
)
async def test_rewriter_degrades_unprovable_multiclaim_reorder(
    language: Language,
    original: str,
    rewritten: str,
) -> None:
    """多事实关系无法逐项证明时应转入分类兜底，而不是冒险发送。"""
    client = ClientStub(json.dumps({"reply_text": rewritten}, ensure_ascii=False))
    rewriter = DeepSeekDeliveryRewriter(client=client, model="deepseek-v4-flash")

    with pytest.raises(DeliveryRewriteUnavailableError):
        await rewriter.rewrite(
            guest_question="请安全改写",
            blocked_reply=original,
            language=language,
        )
