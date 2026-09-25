"""「不得编造事实」全局底层规则：判定样本与架构守护。

判定样本取自 1.39.12 至 1.39.14 在生产容器用真实 DeepSeek 对比时得到的回复原句，
见 docs/specs/2026-09-24_fact-source-rule-spec.md §4。
"""

from pathlib import Path

import pytest

from homestay_bot.services.fact_policy import (
    FACT_SOURCE_RULE_EN,
    FACT_SOURCE_RULE_ZH,
    is_supported_by,
    is_unsourced_external_state_claim,
    is_unsourced_homestay_claim,
)
from homestay_bot.services.guest_reply_policy import remove_ungrounded_property_claims

SRC = Path(__file__).resolve().parents[2] / "src" / "homestay_bot"


@pytest.mark.parametrize(
    "sentence",
    [
        # 位置：模型没有民宿地址，任何相对位置都是编造。
        "您住的是江汉路附近的话，武昌、汉口、汉阳三镇都能覆盖。",
        "需要的话，我可以帮您看看民宿附近最近的地铁站怎么走。",
        "从民宿出发，最快一般是打车或网约车，直接导航“汉口站”即可，平峰约20到30分钟。",
        "从民宿去汉口站，最快通常是打车走二环或三环，白天非高峰约三十到四十分钟。",
        "从民宿去汉口站，最省心的方式通常是先坐地铁再换乘到2号线，汉口站有地铁直达。",
        # 设施与物品。
        "若您很在意看月亮，可在民宿露台或窗边备茶和月饼。",
        "雨具、烘干衣架前台都备着，需要随时找我拿。",
        "玄关置物篮有备用伞和一次性雨衣，随手取用。",
        "我们备有雨伞、拖鞋和热茶。",
        # 默认不放行：提到民宿、又不属于任何放行句式。
        "建议改为室内安排，如逛馆、喝热茶、在民宿看老电影。",
    ],
)
def test_unsourced_homestay_claims_are_rejected(sentence: str) -> None:
    """说的是民宿、又说不出来源的句子，一律判为编造。"""
    assert is_unsourced_homestay_claim(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "祝您住得安心，中秋安康。",
        "您好，我是您在武汉的民宿管家。",
        "如果您告诉我出发地（如光谷、武昌或民宿所在片区），我可以帮您规划路线。",
        "需要按您住的位置排一条半日看展路线，可以告诉我。",
        "您可以先用地图软件把民宿设为起点、汉口站设为终点。",
        "雨大时可改去博物馆、美术馆、咖啡馆，或回民宿休息。",
        "从民宿去汉口站，最快的方式通常要看您出发的时段和当时路况。",
        "房间内不要使用大功率电器，出门请关好门窗。",
        "【客房服务】",
        # 不提民宿的店外信息不受这条规则判定（由分流和来源约束）。
        "湖北省博物馆免费开放，周一闭馆。",
    ],
)
def test_questions_wishes_and_place_only_mentions_are_kept(sentence: str) -> None:
    """问句、请求、祝福、自我介绍、只把民宿当去处、禁止性提醒：不陈述民宿事实，保留。"""
    assert not is_unsourced_homestay_claim(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Our homestay is a short walk from the metro station.",
        "We have umbrellas at the front desk for you to borrow.",
        "Your room is in a quiet area near Jianghan Road.",
    ],
)
def test_english_unsourced_homestay_claims_are_rejected(sentence: str) -> None:
    """英文按同样结构判定（尚无真实英文语料，此为构造用例）。"""
    assert is_unsourced_homestay_claim(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Enjoy your stay in Wuhan!",
        "Let me know where you will start from, and I can plan a route.",
        "Please do not use high-power appliances in the room.",
        "The Hubei Provincial Museum is free to visit.",
    ],
)
def test_english_non_assertions_are_kept(sentence: str) -> None:
    """英文的祝福、请求、禁止性提醒和店外信息保留。"""
    assert not is_unsourced_homestay_claim(sentence)


# 调用模型、且输出会发给客人的模块，必须在提示词里引用唯一的规则常量。
GUEST_FACING_MODEL_MODULES = {
    "integrations/deepseek_client.py",
    "integrations/deepseek_tourism.py",
    "integrations/deepseek_delivery_rewriter.py",
}
# 调用模型、但输出不直接发给客人的模块；新增时必须说明理由并登记在这里。
NON_GUEST_MODEL_MODULES = {
    "integrations/deepseek_complaint.py": "客诉草稿，只给管家审核",
    "integrations/deepseek_faq_drafter.py": "FAQ 草稿，只给管理员审核",
    "integrations/deepseek_context_summarizer.py": "客户摘要，进入记忆治理而非客人消息",
    "services/admin_debug_service.py": "后台调试入口，只返回给管理员",
    "services/runtime_config_tester.py": "接口连通性探测，固定提问",
}


def _model_calling_modules() -> set[str]:
    """扫描源码中直接调用模型接口的模块。"""
    found = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "completions.create(" in text or "messages.create(" in text:
            found.add(path.relative_to(SRC).as_posix())
    return found


def test_every_model_call_site_is_registered() -> None:
    """新增的模型调用必须登记为面向客人（受规则约束）或非客人用途，不能悄悄绕过。"""
    unregistered = _model_calling_modules() - GUEST_FACING_MODEL_MODULES - set(
        NON_GUEST_MODEL_MODULES
    )
    assert unregistered == set()


@pytest.mark.parametrize("module", sorted(GUEST_FACING_MODEL_MODULES))
def test_guest_facing_prompts_reference_the_single_rule(module: str) -> None:
    """面向客人的模型提示词引用唯一的规则常量，不另写一份会走样的禁令。"""
    text = (SRC / module).read_text(encoding="utf-8")
    assert "FACT_SOURCE_RULE_ZH" in text


def test_rule_text_covers_every_fact_category() -> None:
    """规则文字点名全部事实类别，中英文一致。"""
    for category in ("位置与周边", "设施", "物品", "服务", "规则", "价格", "房态", "人员安排"):
        assert category in FACT_SOURCE_RULE_ZH
    for category in ("location", "facilities", "services", "prices", "availability", "staff"):
        assert category in FACT_SOURCE_RULE_EN


# 虚构的审核答案（芸栖小楼是测试用的虚构民宿）。1.39.15 起，这类答案在投递改写和
# 非专属问题的回复里会被当成「说不出来源的民宿自述」删掉：47 条里 35 条被改、11 条被删空。
REVIEWED_ANSWERS = {
    "便利店": "民宿附近的芸栖路上有一家24小时便利店，步行约2分钟；巷口的“芸记热干面”早上6:30开门。"
    "这些都是周边商户，与本店无关，营业时间以商户当天为准。",
    "消防": "每层楼梯口都有灭火器，每个房间都装有烟雾报警器。安全出口是一楼正门和通往院子的后门，"
    "房门背面贴有逃生路线图。",
    "网络": "全楼是300M光纤宽带，每个房间一台独立路由器，视频会议和看高清视频一般没问题；"
    "四楼401信号稍弱，可以用房间里的有线网口。",
    "停车": "民宿没有自有停车位，可以停在芸栖路公共停车场，入口在芸栖路20号，走到民宿约3分钟。"
    "民宿门口只能临时停车10分钟上下行李，不能过夜停放。",
    "禁烟": "房间、楼道、露台和阳台都禁止吸烟，包括电子烟。需要吸烟请到一楼院子门口的吸烟点，"
    "那里有烟灰缸。在室内吸烟需要支付300元深度清洁费。",
    "电器": "房间插座是220V国标两孔和三孔，床头有USB充电口。请不要使用1500W以上的大功率电器，"
    "例如电煮锅、电暖器。",
}


@pytest.mark.parametrize("topic", sorted(REVIEWED_ANSWERS))
def test_a_reviewed_answer_is_never_removed_when_it_is_its_own_source(topic: str) -> None:
    """不变量：以审核答案本身为依据时，过滤不得删掉其中任何一句。"""
    answer = REVIEWED_ANSWERS[topic]

    assert remove_ungrounded_property_claims(answer, grounded_in=answer) == answer


def test_a_reworded_fact_from_the_source_is_kept() -> None:
    """换了说法、数字都对得上的本店事实有出处，保留。"""
    source = REVIEWED_ANSWERS["便利店"]
    reply = "民宿附近芸栖路上有家24小时便利店，走路2分钟左右。"

    assert is_supported_by(reply, source)
    assert remove_ungrounded_property_claims(reply, grounded_in=source) == reply


def test_a_changed_number_is_not_supported() -> None:
    """数字改了就不算有出处：2 分钟写成 5 分钟照删。"""
    source = REVIEWED_ANSWERS["便利店"]
    reply = "民宿附近芸栖路上有家24小时便利店，走路5分钟左右。"

    assert not is_supported_by(reply, source)
    assert remove_ungrounded_property_claims(reply, grounded_in=source) == ""


def test_a_homestay_fact_missing_from_the_source_is_still_removed() -> None:
    """依据里没有的本店事实照删，与不传依据时一致。"""
    source = REVIEWED_ANSWERS["便利店"]
    reply = "附近有24小时便利店，步行约2分钟。民宿楼下还有自助洗衣房，24小时开放。"

    assert (
        remove_ungrounded_property_claims(reply, grounded_in=source)
        == "附近有24小时便利店，步行约2分钟。"
    )


def test_an_empty_source_supports_nothing() -> None:
    """没有依据时不放行任何本店断言。"""
    assert not is_supported_by("民宿楼下有便利店。", "")


@pytest.mark.parametrize(
    "sentence",
    [
        # 1.39.16 测试号验收：只查了房态、没有查天气，回复却写了这句。
        "这几天武汉早晚偏凉，带件薄外套会舒服些。",
        "这几天早晚偏凉，建议带件外套。",
        "最近东湖人很多，周末去要排队。",
        "明天应该是晴天，适合出门。",
        "It has been quite chilly these days, so bring a jacket.",
    ],
)
def test_unsourced_changing_external_state_is_detected(sentence: str) -> None:
    """带时间指向、断言会变化的店外状态，又没有本轮实时查询作依据：判为推测。"""
    assert is_unsourced_external_state_claim(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "建议出发前看一下天气预报。",
        "武汉明天的具体天气我这边暂时查不到。",
        "明天下雨的话，可以改去省博物馆。",
        "出发前留意一下明天是否下雨。",
        "黄鹤楼在武昌区，江汉路步行街在汉口。",
        "武汉夏天通常比较热。",
        "明天入住、9月27日退房，还有两间房。",
        "明天会下雨吗？",
    ],
)
def test_advice_inability_conditions_and_stable_facts_are_kept(sentence: str) -> None:
    """建议、查不到的说明、条件句、提问、稳定常识和房态事实都不算推测。"""
    assert not is_unsourced_external_state_claim(sentence)
