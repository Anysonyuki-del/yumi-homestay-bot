import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from homestay_bot.domain.enums import Language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PropertyTopic:
    """一个民宿专属主题：名称、识别别名与只用于检索的提示。

    `aliases` 同时用于专属问题分类、证据门主题识别和检索扩展，所以只收明确
    指向该设施或政策的说法；`recall_hints` 只用于检索排序，可以更宽，但不参与
    判断问题是否在问本店事实，也不能作为设施存在的证据。
    """

    name: str
    aliases: re.Pattern[str]
    recall_terms: str
    recall_hints: re.Pattern[str] | None = None
    # 英文安全回复里的称呼；中文回复直接用 name。
    english: str = "this detail about the homestay"


def _topic_pattern(pattern: str) -> re.Pattern[str]:
    """编译大小写不敏感的主题别名。"""
    return re.compile(pattern, re.IGNORECASE)


# 顺序即优先级：多主题问题的安全回复只点名第一个主题。别名会参与「是否在问
# 本店事实」的分类，一旦误判，旅游或故障问题会被换成「尚未确认」的保守回复，
# 所以含义不唯一的词（猫、狗、lift、dryer、smoke 等）只放进 recall_hints。
# ponytail: 别名是按已知主题和校准集漏检手工维护的有限清单，识别不了没有列出
# 的说法和主题。评估集中同义问法持续漏检时，再扩充别名或进入 C2 语义检索。
PROPERTY_TOPICS: tuple[PropertyTopic, ...] = (
    PropertyTopic(
        "停车",
        _topic_pattern(
            # 前一字排除公交、出租、火车等，避免把问交通的问题当成问本店停车。
            r"停车|泊车|车位"
            r"|(?<![交租火巴约班动电货])车子?(?:要|能|可以|该|应该)?(?:停|放)(?:在|到)?哪"
            r"|(?<![交租火巴约班动电货])车.{0,6}停(?:在|到)?门口"
            r"|parking|car\s*park|park\s+(?:my|our|the)\s+car"
        ),
        "停车 车位 parking",
        english="parking",
    ),
    PropertyTopic(
        "早餐",
        _topic_pattern(r"早餐|早饭|breakfast"),
        "早餐 breakfast",
        english="breakfast",
        # 「早上吃什么」多半是在问武汉本地小吃，只帮检索排序，不据此判为专属问题。
        recall_hints=_topic_pattern(r"早上.{0,8}吃"),
    ),
    PropertyTopic(
        "宠物",
        _topic_pattern(
            r"宠物|(?:带|携带)(?:只|条)?(?:猫|狗)|(?:猫|狗)子?.{0,4}(?:一起住|能住|入住|能带|可以带)"
            r"|\bpets?\b|bring\s+(?:my|our|a)\s+(?:dog|cat|puppy|kitten)"
        ),
        "宠物 猫 狗 pet",
        english="pets",
        recall_hints=_topic_pattern(r"猫|狗|\bdogs?\b|\bcats?\b"),
    ),
    PropertyTopic(
        "加床",
        _topic_pattern(
            r"加床|加一张床|床.{0,8}(?:再|多)(?:加|要)?一张|extra\s+bed|rollaway|folding\s+bed"
        ),
        "加床 extra bed",
        english="extra beds",
    ),
    PropertyTopic(
        "电梯",
        _topic_pattern(r"电梯|elevator"),
        "电梯 elevator",
        english="the elevator",
        recall_hints=_topic_pattern(r"\blift\b"),
    ),
    PropertyTopic(
        "厨房",
        _topic_pattern(r"厨房|做饭|煮饭|烧饭|kitchen"),
        "厨房 kitchen",
        english="the kitchen",
        recall_hints=_topic_pattern(r"\bcook(?:ing)?\b"),
    ),
    PropertyTopic(
        "洗衣",
        _topic_pattern(
            r"洗衣|烘干机|衣服.{0,6}洗|laundry|washing\s+machine|clothes\s+dryer"
            r"|wash\s+(?:my|our|the)?\s*clothes"
        ),
        "洗衣 洗衣机 laundry washing machine",
        english="laundry",
    ),
    PropertyTopic(
        "发票",
        _topic_pattern(r"发票|invoice|fapiao"),
        "发票 invoice",
        english="invoices",
        recall_hints=_topic_pattern(r"receipt"),
    ),
    PropertyTopic(
        "接送",
        _topic_pattern(
            r"接送|接机|接站|送机|送站|有人接|pickup|pick-up|pick\s+(?:me|us)\s+up"
            r"|pick\s+up\s+service|shuttle"
        ),
        "接送 接站 pickup",
        english="pickup service",
    ),
    PropertyTopic(
        "无障碍",
        _topic_pattern(r"无障碍|轮椅|accessib|wheelchair"),
        "无障碍 accessible",
        english="accessibility",
    ),
    PropertyTopic(
        "吸烟",
        _topic_pattern(r"吸烟|抽烟|(?:来|抽)一?根烟|smoking|smoke\s+(?:in|inside)"),
        "吸烟 smoking",
        english="smoking",
    ),
    PropertyTopic(
        "行李寄存",
        _topic_pattern(
            r"寄存|存行李|放行李|(?:行李|箱子|行李箱).{0,6}(?:放|存|寄)|luggage|baggage"
            r"|\bbags?\b.{0,20}\b(?:leave|store|keep)\b"
            r"|\b(?:leave|store|keep)\b.{0,20}\bbags?\b"
        ),
        "行李 寄存 luggage",
        english="luggage storage",
    ),
    PropertyTopic(
        "距离",
        _topic_pattern(
            r"距离|离.{1,12}(?:多远|多久|远吗|近吗)"
            r"|how\s+far\s+is\s+(?:it|the\s+homestay|your)|distance"
        ),
        "距离 distance",
        english="the distance",
    ),
    PropertyTopic(
        "网络",
        _topic_pattern(r"wi-?fi|无线网|上网|网速|internet"),
        "wifi 网络 internet",
        english="Wi-Fi",
    ),
    PropertyTopic(
        "入住退房时间",
        _topic_pattern(
            r"(?:几点|什么时候|何时).{0,4}(?:入住|退房)|(?:入住|退房)(?:时间|几点)"
            r"|延迟退房|晚一?点退房|退房.{0,6}晚一?点|退房.{0,4}(?:延|推迟)"
            r"|late\s+check-?\s?out|check-?\s?(?:in|out)\s+time"
            r"|what\s+time\s+(?:is\s+|can\s+i\s+)?check-?\s?(?:in|out)"
        ),
        "入住 退房 check-in check-out",
        english="check-in and check-out times",
    ),
)


def normalize_text(content: str) -> str:
    """统一全角半角与大小写，供主题识别和词面评分共用。"""
    return unicodedata.normalize("NFKC", content).casefold()


def detect_property_topics(text: str) -> list[PropertyTopic]:
    """按固定优先级返回文本明确提到的全部民宿专属主题。"""
    normalized = normalize_text(text)
    return [topic for topic in PROPERTY_TOPICS if topic.aliases.search(normalized)]


def _recall_topics(text: str) -> list[PropertyTopic]:
    """返回检索时需要补充同义词的主题，包含只用于排序的宽提示。"""
    normalized = normalize_text(text)
    return [
        topic
        for topic in PROPERTY_TOPICS
        if topic.aliases.search(normalized)
        or (topic.recall_hints is not None and topic.recall_hints.search(normalized))
    ]


# 只出现在客人问题里的虚词不代表主题：「你们有…吗」「Do you have…」会让所有
# 同句式的知识都显得相关，把无答案问题错配到无关条目。
_QUERY_STOP_TOKENS = frozenset(
    {
        "你们",
        "我们",
        "民宿",
        "可以",
        "请问",
        "有没",
        "没有",
        "什么",
        "怎么",
        "是否",
        "一下",
        "能不",
        "不能",
        "吗",
        "呢",
        "the",
        "is",
        "are",
        "do",
        "does",
        "you",
        "your",
        "have",
        "has",
        "can",
        "could",
        "my",
        "me",
        "we",
        "our",
        "us",
        "any",
        "there",
        "it",
        "to",
        "of",
        "at",
        "in",
        "on",
        "for",
        "and",
        "or",
        "be",
        "what",
        "where",
        "when",
        "how",
        "if",
        "homestay",
        "please",
    }
)


class KnowledgeRecord(Protocol):
    """定义知识服务读取的最小条目字段。"""

    id: int
    category: str
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str
    keywords: list[str]


class ActiveKnowledgeRepository(Protocol):
    """定义只读取已启用知识的仓储接口。"""

    async def list_active(self) -> list[KnowledgeRecord]:
        """返回当前全部已启用且已审核的知识。"""


@dataclass(frozen=True)
class KnowledgeSnippet:
    """表示交给模型的一条最小化知识。"""

    source_id: int
    category: str
    question: str
    answer: str


@dataclass(frozen=True)
class KnowledgeRetrieval:
    """一次检索交给模型的证据，以及未入选的原因统计。

    「因预算跳过」表示有相关知识但完整问答放不进字符预算，不能当作知识库里
    没有这条知识；「没有匹配」才是词面上找不到。
    """

    snippets: list[KnowledgeSnippet]
    budget_skipped: int
    matched: int


class KnowledgeService:
    """把已审核知识转换为指定语言的模型上下文。"""

    def __init__(self, repository: ActiveKnowledgeRepository) -> None:
        """注入只读知识仓储。"""
        self._repository = repository

    @staticmethod
    def _tokens(content: str) -> set[str]:
        """提取英文词元和中文二元组，供确定性相关度评分使用。"""
        normalized = normalize_text(content)
        tokens = set(re.findall(r"[a-z0-9]{2,}", normalized))
        for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
            if len(segment) == 1:
                tokens.add(segment)
            else:
                tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
        return tokens

    @classmethod
    def _score(cls, query_tokens: set[str], entry: KnowledgeRecord, language: Language) -> int:
        """按问题、关键词、分类和答案的证据强度计算相关度。"""
        question = entry.question_en if language is Language.EN else entry.question_zh
        answer = entry.answer_en if language is Language.EN else entry.answer_zh
        alternate_question = (
            entry.question_zh if language is Language.EN else entry.question_en
        )
        alternate_answer = entry.answer_zh if language is Language.EN else entry.answer_en
        keyword_text = " ".join(str(item) for item in entry.keywords)
        return (
            len(query_tokens & cls._tokens(question)) * 6
            + len(query_tokens & cls._tokens(alternate_question)) * 4
            + len(query_tokens & cls._tokens(keyword_text)) * 5
            + len(query_tokens & cls._tokens(entry.category)) * 4
            + len(query_tokens & cls._tokens(answer))
            + len(query_tokens & cls._tokens(alternate_answer))
        )

    @classmethod
    def _query_tokens(cls, query: str) -> set[str]:
        """提取问题词元：去掉虚词，并补上识别到的主题的规范说法。

        只补规范说法（如「停车」「parking」），不把主题当作答案存在的证据；
        原始问题保持不变。
        """
        tokens = cls._tokens(query) - _QUERY_STOP_TOKENS
        for topic in _recall_topics(query):
            tokens |= cls._tokens(topic.recall_terms)
        return tokens

    async def retrieve(
        self,
        language: Language,
        query: str,
        *,
        limit: int = 8,
        char_budget: int = 12_000,
    ) -> list[KnowledgeSnippet]:
        """按当前问题返回相关且受字符预算约束的审核知识。"""
        retrieval = await self.retrieve_detailed(
            language,
            query,
            limit=limit,
            char_budget=char_budget,
        )
        return retrieval.snippets

    async def retrieve_detailed(
        self,
        language: Language,
        query: str,
        *,
        limit: int = 8,
        char_budget: int = 12_000,
    ) -> KnowledgeRetrieval:
        """按相关度选取完整问答单元，放不进预算的整条跳过并计数。

        一条审核问答是最小证据单元：截断会切掉答案尾部的收费、时间、否定或
        适用条件，把「需收费」截成「可以」，比不给证据更危险。因此只整条放入，
        剩余预算不够就跳过换下一条；同分时按编号排序只为结果可复现。
        """
        entries = await self._repository.list_active()
        query_tokens = self._query_tokens(query)
        ranked = sorted(
            (
                (self._score(query_tokens, entry, language), entry)
                for entry in entries
            ),
            key=lambda item: (item[0], item[1].id),
            reverse=True,
        )
        snippets: list[KnowledgeSnippet] = []
        used_chars = 0
        budget_skipped = 0
        matched = 0
        for score, entry in ranked:
            if score <= 0 or len(snippets) >= max(0, limit):
                break
            matched += 1
            question = (
                entry.question_en if language is Language.EN else entry.question_zh
            )
            answer = entry.answer_en if language is Language.EN else entry.answer_zh
            item_chars = len(entry.category) + len(question) + len(answer)
            if used_chars + item_chars > max(0, char_budget):
                budget_skipped += 1
                continue
            snippets.append(
                KnowledgeSnippet(
                    source_id=entry.id,
                    category=entry.category,
                    question=question,
                    answer=answer,
                )
            )
            used_chars += item_chars
        if budget_skipped:
            # 只记数量：相关知识因预算未能交给模型，不等于知识库缺这条知识。
            logger.info(
                "审核知识因字符预算未入选：budget_skipped=%s selected=%s",
                budget_skipped,
                len(snippets),
            )
        return KnowledgeRetrieval(
            snippets=snippets,
            budget_skipped=budget_skipped,
            matched=matched,
        )
