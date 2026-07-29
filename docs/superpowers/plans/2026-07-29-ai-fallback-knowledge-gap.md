# AI Fallback and Knowledge Gap Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库未覆盖的普通问题由大模型合理回答，让民宿专属知识缺口提醒员工补充，让未解决交易问题提醒员工核实，同时保持机器人接待。

**Architecture:** 新增无副作用的本地回答策略模块，对交易问题和民宿专属问题做确定性分类；`GuestAssistant` 继续生成唯一结构化回复，并在应用层归一化低置信度和提醒状态。`ConversationService` 发送客人回复后，根据结构化状态创建员工提醒，但不切换 `HUMAN_ACTIVE`。

**Tech Stack:** Python 3.12、OpenAI Responses API、Pydantic、FastAPI、SQLAlchemy、pytest、Ruff、mypy

---

## 文件边界

- 新建 `src/homestay_bot/services/answer_policy.py`：交易和民宿专属问题的纯本地分类。
- 新建 `tests/unit/test_answer_policy.py`：策略分类边界测试。
- 修改 `src/homestay_bot/integrations/openai_client.py`：扩展结构化决定、提示词和结果归一化。
- 修改 `tests/unit/test_openai_client.py`：严格 Schema、通用兜底、知识缺口和交易确认测试。
- 修改 `src/homestay_bot/services/conversation_service.py`：发送知识库补充和业务确认提醒，不切换人工。
- 修改 `tests/unit/test_conversation_service.py`：提醒优先级、会话状态和去重回归。
- 新建 `tests/contract/test_fenno_fallback_contract.py`：真实 Fenno 普通问题、知识缺口和交易确认契约。
- 修改 `tasks/todo.md`：记录最终验证证据。

## Task 1：建立本地回答风险分类

**Files:**

- Create: `src/homestay_bot/services/answer_policy.py`
- Create: `tests/unit/test_answer_policy.py`

- [ ] **Step 1：写失败测试**

```python
from homestay_bot.services.answer_policy import (
    is_property_specific,
    is_transaction_sensitive,
)


def test_transaction_questions_are_detected() -> None:
    """价格、房态和售后交易问题必须进入交易安全边界。"""
    assert is_transaction_sensitive("今天还有房吗？")
    assert is_transaction_sensitive("这个订单能退款多少？")
    assert is_transaction_sensitive("可以取消或改期吗？")
    assert is_transaction_sensitive("付款后多久确认？")
    assert not is_transaction_sensitive("武汉地铁一般几点停运？")


def test_property_specific_questions_are_detected() -> None:
    """设施、服务和民宿距离属于专属事实。"""
    assert is_property_specific("你们有停车场吗？")
    assert is_property_specific("提供早餐和宠物用品吗？")
    assert is_property_specific("民宿离黄鹤楼有多远？")
    assert is_property_specific("可以寄存行李吗？")
    assert not is_property_specific("武汉有哪些地方好玩？")


def test_transaction_has_priority_over_property_specific() -> None:
    """发票金额属于交易问题，即使也涉及民宿服务。"""
    text = "你们能开多少钱的发票？"
    assert is_transaction_sensitive(text)
    assert is_property_specific(text)
```

- [ ] **Step 2：运行测试确认失败**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/unit/test_answer_policy.py -v`

Expected: FAIL，提示 `homestay_bot.services.answer_policy` 不存在。

- [ ] **Step 3：实现纯策略模块**

```python
import re

_TRANSACTION_PATTERN = re.compile(
    r"房态|有房|可订|价格|房价|多少钱|参考价|退款|退多少|"
    r"取消|改期|付款|支付|到账|订单|预订状态|发票金额|"
    r"availability|room rate|price|refund|cancel|reschedule|"
    r"payment|reservation status|invoice amount",
    re.IGNORECASE,
)

_PROPERTY_SPECIFIC_PATTERN = re.compile(
    r"你们|你家|民宿|房间|店里|停车|早餐|宠物|加床|电梯|"
    r"厨房|洗衣|发票|接送|无障碍|吸烟|行李寄存|寄存行李|"
    r"离.+(?:多远|多久)|距离|设施|服务|"
    r"your homestay|your property|parking|breakfast|pet|extra bed|"
    r"elevator|kitchen|laundry|invoice|pickup|accessible|smoking|"
    r"luggage storage|distance",
    re.IGNORECASE,
)


def is_transaction_sensitive(text: str) -> bool:
    """判断文本是否涉及不能依靠模型猜测的交易事实。"""
    return _TRANSACTION_PATTERN.search(text) is not None


def is_property_specific(text: str) -> bool:
    """判断文本是否要求回答本民宿专属事实。"""
    return _PROPERTY_SPECIFIC_PATTERN.search(text) is not None
```

- [ ] **Step 4：运行测试确认通过**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/unit/test_answer_policy.py -v`

Expected: 3 passed。

- [ ] **Step 5：提交策略模块**

```bash
git add src/homestay_bot/services/answer_policy.py tests/unit/test_answer_policy.py
git commit -m "feat: classify fallback answer risks"
```

## Task 2：扩展结构化决定并归一化模型结果

**Files:**

- Modify: `src/homestay_bot/integrations/openai_client.py:104-165`
- Modify: `src/homestay_bot/integrations/openai_client.py:230-440`
- Modify: `tests/unit/test_openai_client.py`

- [ ] **Step 1：写严格 Schema 和低置信度行为测试**

在 `tests/unit/test_openai_client.py` 增加：

```python
def test_decision_schema_requires_fallback_status_fields() -> None:
    """兼容端点的严格 Schema 必须要求全部知识缺口字段。"""
    schema = assistant_decision_schema()
    required = set(schema["required"])
    assert {
        "knowledge_gap",
        "knowledge_gap_topic",
        "staff_confirmation_required",
        "staff_confirmation_reason",
    } <= required


@pytest.mark.asyncio
async def test_low_confidence_general_answer_does_not_handoff() -> None:
    """普通问题低置信度时保留谨慎回答，但不得自动切人工。"""
    assistant = GuestAssistant(
        client=LowConfidenceOpenAIStub(),
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )
    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "怎样和同行朋友协调旅行安排？"}],
    )
    assert decision.handoff_reason is None
    assert decision.knowledge_gap is False
    assert decision.staff_confirmation_required is False


@pytest.mark.asyncio
async def test_low_confidence_property_question_becomes_knowledge_gap() -> None:
    """民宿专属问题低置信度时应提醒补知识，而不是转人工。"""
    assistant = GuestAssistant(
        client=LowConfidenceOpenAIStub(),
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )
    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "你们有停车场吗？"}],
    )
    assert decision.handoff_reason is None
    assert decision.knowledge_gap is True
    assert decision.knowledge_gap_topic == "property_information"
    assert decision.staff_confirmation_required is False


@pytest.mark.asyncio
async def test_low_confidence_transaction_requires_staff_confirmation() -> None:
    """未解决交易问题应通知员工，但不切换人工会话。"""
    assistant = GuestAssistant(
        client=LowConfidenceOpenAIStub(),
        knowledge=KnowledgeStub(),
        model="gpt-5.4-mini",
        safety_hmac_key=b"test-key",
    )
    decision = await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "这个订单能退款多少？"}],
    )
    assert decision.handoff_reason is None
    assert decision.knowledge_gap is False
    assert decision.staff_confirmation_required is True
    assert decision.staff_confirmation_reason == "low_confidence_transaction"
```

- [ ] **Step 2：运行新增测试确认失败**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/unit/test_openai_client.py -k "fallback_status or low_confidence" -v`

Expected: 新字段和新行为测试 FAIL；原 `test_low_confidence_response_forces_human_handoff` 需由上述普通问题测试替代。

- [ ] **Step 3：扩展 Pydantic 模型和严格 Schema**

`AssistantDecision` 增加带安全默认值的字段：

```python
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
```

`assistant_decision_schema()` 的 `properties` 增加：

```python
"knowledge_gap": {"type": "boolean"},
"knowledge_gap_topic": nullable_string,
"staff_confirmation_required": {"type": "boolean"},
"staff_confirmation_reason": nullable_string,
```

现有 `required: list(properties)` 会让四个字段自动成为严格必填字段；测试桩可依赖 Pydantic 默认值，真实 Fenno 必须返回全部字段。

- [ ] **Step 4：实现本地结果归一化**

导入：

```python
from homestay_bot.services.answer_policy import (
    is_property_specific,
    is_transaction_sensitive,
)
```

把 `_validate_decision` 改为：

```python
def _validate_decision(
    self,
    output_text: str,
    question_text: str,
) -> AssistantDecision:
    """校验模型结果，并用本地风险分类约束低置信度处理。"""
    decision = AssistantDecision.model_validate_json(output_text)
    updates: dict[str, Any] = {"handoff_reason": None}

    if decision.staff_confirmation_required:
        updates.update(
            {
                "knowledge_gap": False,
                "knowledge_gap_topic": None,
            }
        )
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
```

在 `respond()` 开头保存最后一条客人问题：

```python
question_text = latest_user_question(messages)["content"]
```

把旅游分支和普通分支的 `_validate_decision(response.output_text)` 全部改为：

```python
self._validate_decision(response.output_text, question_text)
```

- [ ] **Step 5：更新模型提示词**

在 `system_prompt` 中加入：

```python
"审核知识未覆盖普通常识时，可以使用通用知识给出合理、谨慎的回答，"
"不得仅因知识库没有答案而追问或转人工；"
"涉及本民宿设施、服务、政策或距离且审核知识未确认时，"
"必须明确说明当前资料未确认，提供不依赖未知事实的替代建议，"
"并设置 knowledge_gap=true 和简短的 knowledge_gap_topic；"
"涉及价格、房态、退款、取消、改期、付款或订单状态且知识和工具无法确认时，"
"不得猜测，必须设置 staff_confirmation_required=true，"
"并填写 staff_confirmation_reason；"
"缺少房态或价格查询的必要日期时可以追问，这不属于知识缺口；"
```

- [ ] **Step 6：运行助手测试**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/unit/test_openai_client.py -v`

Expected: 全部通过，旅游联网和相对日期测试保持 PASS。

- [ ] **Step 7：提交结构化决定调整**

```bash
git add src/homestay_bot/integrations/openai_client.py tests/unit/test_openai_client.py
git commit -m "feat: add AI fallback decision states"
```

## Task 3：发送员工提醒但保持机器人模式

**Files:**

- Modify: `src/homestay_bot/services/conversation_service.py:151-173`
- Modify: `tests/unit/test_conversation_service.py`

- [ ] **Step 1：写知识缺口和业务确认失败测试**

在 `tests/unit/test_conversation_service.py` 增加：

```python
@pytest.mark.asyncio
async def test_knowledge_gap_notifies_staff_and_keeps_bot_active() -> None:
    """专属信息缺失时应提供替代建议并提醒补知识。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text=(
                "当前资料暂未确认是否有专属停车场，"
                "建议先使用附近公共停车场并留意现场标识。"
            ),
            language=Language.ZH,
            intent="property_facility",
            confidence=0.62,
            knowledge_gap=True,
            knowledge_gap_topic="停车",
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)
    await service.handle_message(incoming(content="你们有停车场吗？"))
    assert "公共停车场" in wecom.guest_messages[0]
    assert len(wecom.internal_messages) == 1
    assert "知识库待补充" in wecom.internal_messages[0]
    assert "停车" in wecom.internal_messages[0]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_transaction_confirmation_notifies_staff_and_keeps_bot_active() -> None:
    """交易结论无法确认时应通知员工核实，但不停止机器人。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="退款金额需要工作人员结合订单核实，我已为您发起确认。",
            language=Language.ZH,
            intent="refund",
            confidence=0.55,
            staff_confirmation_required=True,
            staff_confirmation_reason="refund_amount_unconfirmed",
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)
    await service.handle_message(incoming(content="这个订单能退款多少？"))
    assert "已为您发起确认" in wecom.guest_messages[0]
    assert len(wecom.internal_messages) == 1
    assert "业务待确认" in wecom.internal_messages[0]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_transaction_confirmation_has_priority_over_knowledge_gap() -> None:
    """同轮两个标记并存时只发送一次业务提醒。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="需要工作人员核实。",
            language=Language.ZH,
            intent="refund",
            confidence=0.5,
            knowledge_gap=True,
            knowledge_gap_topic="退款",
            staff_confirmation_required=True,
            staff_confirmation_reason="refund_policy_unconfirmed",
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)
    await service.handle_message(incoming(content="退款政策是什么？"))
    assert len(wecom.internal_messages) == 1
    assert "业务待确认" in wecom.internal_messages[0]
    assert "知识库待补充" not in wecom.internal_messages[0]
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_grounded_property_answer_does_not_notify_staff() -> None:
    """已有审核答案的专属问题不得误报知识库缺口。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="民宿提供早餐，供应时间以审核知识中的说明为准。",
            language=Language.ZH,
            intent="property_facility",
            confidence=0.95,
            knowledge_gap=False,
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)
    await service.handle_message(incoming(content="你们提供早餐吗？"))
    assert wecom.internal_messages == []
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE


@pytest.mark.asyncio
async def test_missing_dates_clarification_does_not_create_gap_alert() -> None:
    """房态查询缺少必要日期时允许追问，但不得提醒补知识。"""
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="请告诉我入住日期和退房日期，我马上帮您查询。",
            language=Language.ZH,
            intent="availability",
            confidence=0.96,
        )
    )
    service, conversations, _, wecom = build_service(assistant=assistant)
    await service.handle_message(incoming(content="还有房吗？"))
    assert "入住日期" in wecom.guest_messages[0]
    assert wecom.internal_messages == []
    assert conversations.conversation.mode is ConversationMode.BOT_ACTIVE
```

- [ ] **Step 2：运行测试确认失败**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/unit/test_conversation_service.py -k "knowledge_gap or transaction_confirmation" -v`

Expected: FAIL，当前服务尚未发送新类型提醒，且旧 `handoff_reason` 路径可能切换人工。

- [ ] **Step 3：实现提醒优先级**

在发送客人回复和处理预订审批后，使用以下顺序替换原模型 `handoff_reason` 接管分支：

```python
if decision.staff_confirmation_required:
    await self._notify_employee(
        conversation,
        message,
        (
            "业务待确认"
            f"\n原因：{decision.staff_confirmation_reason or 'transaction_unconfirmed'}"
        ),
    )
    return

if decision.knowledge_gap:
    await self._notify_employee(
        conversation,
        message,
        (
            "知识库待补充"
            f"\n缺失主题：{decision.knowledge_gap_topic or 'property_information'}"
            "\n请在知识管理页面新增并启用审核答案"
        ),
    )
```

此处不修改 `conversation.mode`。员工真正从企业微信回复时，方法开头的 `MessageOrigin.SERVICER` 分支仍会切换 `HUMAN_ACTIVE`。

- [ ] **Step 4：运行全部会话测试**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/unit/test_conversation_service.py -v`

Expected: 全部通过；紧急事件、主动人工、媒体消息、预订审批和旅游搜索失败测试保持 PASS。

- [ ] **Step 5：提交员工提醒逻辑**

```bash
git add src/homestay_bot/services/conversation_service.py tests/unit/test_conversation_service.py
git commit -m "feat: notify staff about unresolved answers"
```

## Task 4：增加真实 Fenno 兜底契约

**Files:**

- Create: `tests/contract/test_fenno_fallback_contract.py`

- [ ] **Step 1：写显式启用的真实契约测试**

```python
import os

import pytest
from openai import AsyncOpenAI

from homestay_bot.config import Settings
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.openai_client import GuestAssistant
from homestay_bot.services.knowledge_service import KnowledgeSnippet

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FENNO_FALLBACK_CONTRACT") != "1",
    reason="需要显式启用真实 Fenno 兜底契约测试",
)


class EmptyKnowledge:
    """返回空审核知识，验证真实模型兜底行为。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """确保回答不依赖本地知识条目。"""
        return []


async def build_assistant() -> tuple[GuestAssistant, AsyncOpenAI]:
    """使用当前 `.env` 构造真实 Fenno 助手。"""
    settings = Settings()  # type: ignore[call-arg]
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    assistant = GuestAssistant(
        client=client,
        knowledge=EmptyKnowledge(),  # type: ignore[arg-type]
        model=settings.openai_model,
        safety_hmac_key=settings.session_secret.encode(),
    )
    return assistant, client


@pytest.mark.asyncio
async def test_general_question_uses_model_fallback() -> None:
    """普通问题应直接获得合理回复，不产生员工提醒状态。"""
    assistant, client = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-general-fallback",
            language=Language.ZH,
            messages=[
                {
                    "role": "user",
                    "content": "和朋友旅行时怎样更高效地协调行程？",
                }
            ],
        )
    finally:
        await client.close()
    assert len(decision.reply_text) >= 20
    assert decision.handoff_reason is None
    assert decision.knowledge_gap is False
    assert decision.staff_confirmation_required is False


@pytest.mark.asyncio
async def test_property_question_marks_knowledge_gap() -> None:
    """空知识下的停车问题应给替代建议并标记知识缺口。"""
    assistant, client = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-property-gap",
            language=Language.ZH,
            messages=[{"role": "user", "content": "你们有停车场吗？"}],
        )
    finally:
        await client.close()
    assert "未确认" in decision.reply_text or "暂未" in decision.reply_text
    assert "停车" in decision.reply_text
    assert decision.knowledge_gap is True
    assert decision.knowledge_gap_topic
    assert decision.staff_confirmation_required is False
    assert decision.handoff_reason is None


@pytest.mark.asyncio
async def test_unresolved_refund_requires_staff_confirmation() -> None:
    """空知识下的退款金额不得猜测，必须标记员工核实。"""
    assistant, client = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-transaction-confirmation",
            language=Language.ZH,
            messages=[{"role": "user", "content": "这个订单能退款多少？"}],
        )
    finally:
        await client.close()
    assert "核实" in decision.reply_text or "确认" in decision.reply_text
    assert decision.staff_confirmation_required is True
    assert decision.staff_confirmation_reason
    assert decision.knowledge_gap is False
    assert decision.handoff_reason is None
```

- [ ] **Step 2：确认默认不访问网络**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/contract/test_fenno_fallback_contract.py -v`

Expected: 3 skipped。

- [ ] **Step 3：显式运行真实 Fenno 契约**

Run: `RUN_FENNO_FALLBACK_CONTRACT=1 "/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" tests/contract/test_fenno_fallback_contract.py -v -s`

Expected: 3 passed。若某项失败，保存结构化决定中的非敏感字段，按实际兼容行为修正提示词或本地归一化，不放宽安全边界。

- [ ] **Step 4：提交契约测试**

```bash
git add tests/contract/test_fenno_fallback_contract.py
git commit -m "test: verify Fenno fallback answer policy"
```

## Task 5：全量质量与安全回归

**Files:**

- Modify only if a failing check exposes a defect in the files listed above.

- [ ] **Step 1：运行全量测试**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" -q`

Expected: 全部单元和集成测试通过；真实外部契约在未设置开关时跳过。

- [ ] **Step 2：运行 Ruff 和 mypy**

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/ruff" check src tests`

Expected: `All checks passed!`

Run: `"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/mypy" src`

Expected: `Success: no issues found`

- [ ] **Step 3：运行关键安全回归**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_conversation_service.py \
  tests/unit/test_openai_client.py \
  tests/unit/test_emergency_service.py \
  -v
```

Expected:

- 普通低置信度问题不切人工。
- 知识缺口和业务确认只通知一次且保持 `BOT_ACTIVE`。
- 主动人工、紧急事件、媒体消息和预订审批仍切人工。
- 百居易工具仍只有只读房态和参考价。

- [ ] **Step 4：确认工作区状态**

Run: `git diff --check && git status --short`

Expected: 无空白错误；只保留预期任务追踪更新。

## Task 6：部署并执行企业微信验收

**Files/State:**

- Source: `/Volumes/02/obsidian codex/homestay-bot/.worktrees/wuhan-tourism-search`
- Runtime: `/Users/rin/Library/Application Support/HomestayBot`
- Health: `http://127.0.0.1:8010/health`

- [ ] **Step 1：同步源码并重启**

Run:

```bash
ditto \
  "/Volumes/02/obsidian codex/homestay-bot/.worktrees/wuhan-tourism-search/src" \
  "/Users/rin/Library/Application Support/HomestayBot/src"
launchctl kickstart -k "gui/501/com.rin.homestay-bot"
curl --retry 5 --retry-connrefused --retry-delay 1 \
  -sS -i "http://127.0.0.1:8010/health"
```

Expected: HTTP 200；数据库、worker、企业微信轮询和配置均为 `ok`。

- [ ] **Step 2：验证普通问题**

企业微信发送：`和朋友旅行时怎样更高效地协调行程？`

Expected:

- 收到大模型生成的实用回答。
- 不要求补充数据库资料。
- 不发送员工提醒。
- 会话保持 `BOT_ACTIVE`。

- [ ] **Step 3：验证民宿专属知识缺口**

在确认知识库没有停车答案后发送：`你们有停车场吗？`

Expected:

- 客人收到“当前资料未确认”及公共停车替代建议。
- 员工 `XuKuang` 收到一次“知识库待补充”提醒。
- 会话保持 `BOT_ACTIVE`。

- [ ] **Step 4：验证交易待确认**

企业微信发送：`这个订单能退款多少？`

Expected:

- 客人收到需要工作人员核实的说明，不出现退款金额猜测。
- 员工 `XuKuang` 收到一次“业务待确认”提醒。
- 会话保持 `BOT_ACTIVE`。

- [ ] **Step 5：验证必要追问不受影响**

企业微信发送：`还有房吗？`

Expected: 机器人只追问入住和退房日期，不发送知识库缺口提醒。

- [ ] **Step 6：记录最终证据**

在 `tasks/todo.md` 的 Review 中记录：

- 全量测试通过数量。
- 三项真实 Fenno 契约结果。
- 四项企业微信消息、客人回复、员工提醒数量和最终会话状态。
- 健康检查结果。

```bash
git add tasks/todo.md
git commit -m "chore: record AI fallback verification"
```

## 自检结论

- Spec 覆盖：普通问题兜底、专属知识缺口、替代建议、员工补知识提醒、交易待确认、会话保持、必要追问、消息去重和原人工边界均有对应任务。
- 类型一致：全计划统一使用 `knowledge_gap`、`knowledge_gap_topic`、`staff_confirmation_required` 和 `staff_confirmation_reason`。
- 风险优先级：交易待确认优先于知识缺口，一条消息最多发送一种新提醒。
- 非目标：不自动写知识库、不开放百居易写操作、不合并不同消息提醒、不改变旅游联网和审批架构。
