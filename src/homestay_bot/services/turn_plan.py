"""轮次计划：模型只提取本轮子问题、意图和参数，本地核验后供下游共用（回复泛用化 Spec P1）。

计划由 `DeepSeekGuestAssistant.plan_turn` 产生；工具开放、知识检索与证据选择、任务写入口、
接管理由和危险补漏都读同一份已核验计划，不再各自用词表否决正常表达。模型给出的内容只是
候选：类型走白名单，摘录必须真实出现在本轮冻结正文里，房号必须出现在原文，日期走统一的
住宿日期校验。计划不认定住宿、不解锁凭证或订单，也不能授权下单或付款。
"""

import hashlib
import json
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from homestay_bot.services.stay_date_range import validate_stay_date_range

PlanKind = Literal[
    "static_fact",
    "stay_query",
    "catalog_query",
    "booking_request",
    "service_request",
    "lost_item_report",
    "facility_fault",
    "request_withdraw",
    "history_mention",
    "external_info",
    "chitchat",
    "unrelated",
    "unclear",
]
PLAN_KINDS: tuple[str, ...] = PlanKind.__args__  # type: ignore[attr-defined]
# 会产生登记的「本轮要求现在办理」类事项；撤回只能指向这些项。
REQUEST_KINDS = frozenset(
    {"booking_request", "service_request", "lost_item_report", "facility_fault"}
)
HAZARD_CATEGORIES = frozenset({"fire", "gas", "electric", "medical", "violence"})
PLAN_MAX_ITEMS = 8
_QUESTION_MAX_CHARS = 200
_SUBJECT_MAX_CHARS = 40


class PlanItem(BaseModel):
    """计划中的一个事项；`valid` 为假表示摘录或位置未通过核验，只用于撤回顺序判定。"""

    model_config = ConfigDict(frozen=True)

    id: int
    kind: PlanKind
    subject: str = ""
    question: str = ""
    quote: str
    start: int
    withdraws: int | None = None
    target_room: str | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None
    risk: str = "none"
    valid: bool = True


class TurnPlan(BaseModel):
    """一轮客人正文（单条或合并批次）的已核验计划。"""

    model_config = ConfigDict(frozen=True)

    source_sha256: str
    items: tuple[PlanItem, ...]

    @property
    def valid_items(self) -> tuple[PlanItem, ...]:
        """按原文位置排序的有效项；失效项不参与授权。"""
        return tuple(sorted((item for item in self.items if item.valid), key=lambda i: i.start))


class PlanOutcome(BaseModel):
    """规划结果：ok 时附计划；failed 时附原因，下游按各能力的失败回退处理。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "failed"]
    source_sha256: str
    reason: str = ""
    plan: TurnPlan | None = None

    @property
    def ok(self) -> bool:
        """规划成功且计划存在。"""
        return self.status == "ok" and self.plan is not None

    def matches(self, text: str) -> bool:
        """计划是否针对这份冻结正文；不一致时调用方必须按无计划处理或重新规划。"""
        return self.source_sha256 == plan_source_sha256(text)

    def to_payload(self) -> str:
        """序列化为作业载荷字符串；摘录是客人原文子串，沿用作业载荷留存规则。"""
        return self.model_dump_json()

    @classmethod
    def from_payload(cls, value: object) -> "PlanOutcome | None":
        """从作业载荷还原；格式不对时返回空，调用方重新规划，不猜测。"""
        if not isinstance(value, str) or not value:
            return None
        try:
            return cls.model_validate_json(value)
        except ValueError:
            return None


def plan_source_sha256(text: str) -> str:
    """冻结正文的摘要，计划复用与过时判定都以它为准。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def failed_plan(text: str, reason: str) -> PlanOutcome:
    """构造规划失败结果；原因只记类别，不记正文。"""
    return PlanOutcome(status="failed", source_sha256=plan_source_sha256(text), reason=reason)


def usable_plan(outcome: PlanOutcome | None, text: str) -> PlanOutcome | None:
    """只返回针对本轮正文且规划成功的计划；摘要不一致按无计划处理。"""
    if outcome is None or not outcome.ok or not outcome.matches(text):
        return None
    return outcome


def _verify_risk(value: object) -> str | None:
    """风险标签白名单；不认识的值让整份计划失败，不能被当成「无风险」去掉情绪词。"""
    if value is None or value == "none":
        return "none"
    if value in {"possible_hazard", "complaint"}:
        return str(value)
    if (
        isinstance(value, str)
        and value.startswith("current_hazard:")
        and value.split(":", 1)[1] in HAZARD_CATEGORIES
    ):
        return value
    return None


def _locate_quote(text: str, quote: str, start: object) -> tuple[int, bool]:
    """核验摘录位置：给定位置精确命中即有效；摘录在正文中只出现一次时按唯一位置改正。

    模型给的字符位置经常偏差，而唯一出现的摘录本身就确定了位置；出现多次又给错位置时
    无法判断指的是哪一处，该项无效（Spec §2.5「重复措辞但 start 无法唯一核验」）。
    """
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and 0 <= start <= len(text) - len(quote)
        and text[start : start + len(quote)] == quote
    ):
        return start, True
    first = text.find(quote)
    if first >= 0 and text.find(quote, first + 1) < 0:
        return first, True
    return (start if isinstance(start, int) and not isinstance(start, bool) else -1), False


def _verify_dates(
    raw: dict[str, Any], today_provider: Callable[[], date]
) -> tuple[date | None, date | None]:
    """日期只在入住与退房都合法时保留，走与外部查询相同的住宿日期校验。"""
    check_in, check_out = raw.get("check_in_date"), raw.get("check_out_date")
    if not isinstance(check_in, str) or not isinstance(check_out, str):
        return None, None
    try:
        return validate_stay_date_range(check_in, check_out, today_provider=today_provider)
    except ValueError:
        return None, None


def verify_turn_plan(
    raw_text: str,
    text: str,
    *,
    today_provider: Callable[[], date],
) -> PlanOutcome:
    """把模型返回的计划 JSON 核验为 `PlanOutcome`；任何结构问题都按规划失败处理。

    规划失败指格式错误、Schema 不符或全部摘录失效；单项摘录失效只作废该项。
    """
    try:
        raw = json.loads(raw_text)
    except (TypeError, ValueError):
        return failed_plan(text, "invalid_json")
    items_raw = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items_raw, list) or not 1 <= len(items_raw) <= PLAN_MAX_ITEMS:
        return failed_plan(text, "schema")
    items: list[PlanItem] = []
    seen_ids: set[int] = set()
    for entry in items_raw:
        if not isinstance(entry, dict):
            return failed_plan(text, "schema")
        item_id, kind, quote = entry.get("id"), entry.get("kind"), entry.get("quote")
        risk = _verify_risk(entry.get("risk"))
        withdraws = entry.get("withdraws")
        if (
            not isinstance(item_id, int)
            or isinstance(item_id, bool)
            or item_id < 1
            or item_id in seen_ids
            or kind not in PLAN_KINDS
            or not isinstance(quote, str)
            or not quote.strip()
            or risk is None
            or (
                withdraws is not None
                and (not isinstance(withdraws, int) or kind != "request_withdraw")
            )
        ):
            return failed_plan(text, "schema")
        seen_ids.add(item_id)
        start, valid = _locate_quote(text, quote, entry.get("start"))
        room = entry.get("target_room")
        # 房号只作检索目标：必须是原文里真实出现的数字房号，模型不能凭空指定房间。
        target_room = (
            room.strip()
            if isinstance(room, str) and room.strip().isdigit() and room.strip() in text
            else None
        )
        check_in, check_out = _verify_dates(entry, today_provider)
        question = entry.get("question")
        subject = entry.get("subject")
        items.append(
            PlanItem(
                id=item_id,
                kind=kind,
                subject=(subject.strip() if isinstance(subject, str) else "")[:_SUBJECT_MAX_CHARS],
                question=(
                    question.strip() if isinstance(question, str) and question.strip() else quote
                )[:_QUESTION_MAX_CHARS],
                quote=quote,
                start=start,
                withdraws=withdraws,
                target_room=target_room,
                check_in_date=check_in,
                check_out_date=check_out,
                risk=risk,
                valid=valid,
            )
        )
    if not any(item.valid for item in items):
        return failed_plan(text, "all_quotes_invalid")
    return PlanOutcome(
        status="ok",
        source_sha256=plan_source_sha256(text),
        plan=TurnPlan(source_sha256=plan_source_sha256(text), items=tuple(items)),
    )


def plan_kinds(outcome: PlanOutcome | None) -> set[str]:
    """有效项的类型集合；无计划时为空。"""
    if outcome is None or outcome.plan is None:
        return set()
    return {item.kind for item in outcome.plan.valid_items}


def plan_risks(outcome: PlanOutcome | None) -> set[str]:
    """有效项的风险标签集合；无计划时为空。"""
    if outcome is None or outcome.plan is None:
        return set()
    return {item.risk for item in outcome.plan.valid_items}


PLANNER_PROMPT_ZH = (
    "你是民宿客服的意图规划器，只把客人本轮正文拆成事项，不回答问题。只输出 JSON："
    '{"items":[{"id":1,"kind":"...","subject":"...","question":"...","quote":"...",'
    '"start":0,"withdraws":null,"target_room":null,"check_in_date":null,'
    '"check_out_date":null,"risk":"none"}]}。'
    "kind 只能取：static_fact（本店事实与政策咨询，包括服务、设施的规则，如保洁几点来、"
    "能借雨伞吗、能带宠物吗）；stay_query（房态、房价）；catalog_query（房型或房间推荐）；"
    "booking_request（本轮要求现在办理订房）；service_request（本轮要求现在执行的服务，"
    "如送毛巾、收垃圾、打扫）；lost_item_report（报失物品）；facility_fault（当前存在的设施"
    "故障或住宿环境问题）；request_withdraw（撤回某项申请，如不用送了、算了）；"
    "history_mention（过去发生、已经修好、明确否定的事，如上次空调坏了现在好了、"
    "空调没有故障不用修）；external_info（武汉景点、交通、天气、美食等店外信息）；"
    "chitchat（寒暄、感谢、夸奖）；unrelated（与住宿和武汉旅行都无关）；unclear（无法判断）。"
    "询问规则或能不能办，一律算 static_fact，不算申请。"
    "一句话可含多项；每项的 quote 必须是客人原文中连续的一段，原样照抄，start 是 quote "
    "在原文中的起始字符位置（从 0 开始）。subject 写事项对象的简短名称（如毛巾、空调、订房）。"
    "request_withdraw 的 withdraws 填它撤回的、原文位置在它之前的申请项 id；撤回的对象不在本轮"
    "原文里时填 null。target_room 只填原文点名的数字房号。日期只在原文给出入住和退房时按 "
    "YYYY-MM-DD 填写，否则为 null。risk 取 none、possible_hazard（可能有危险但不确定）、"
    "current_hazard:fire/gas/electric/medical/violence（此刻真实存在的危险）或 complaint（客人"
    "在投诉、表达不满或要求处理纠纷）；咨询、假设、引用、否定和开心激动都不是危险或投诉。"
)


def planner_messages(text: str, today: date) -> list[dict[str, str]]:
    """规划请求的消息；正文作为数据放在用户消息里，不混入系统指令。"""
    return [
        {
            "role": "system",
            "content": PLANNER_PROMPT_ZH + f"武汉当前日期：{today.isoformat()}。",
        },
        {"role": "user", "content": json.dumps({"guest_text": text}, ensure_ascii=False)},
    ]
