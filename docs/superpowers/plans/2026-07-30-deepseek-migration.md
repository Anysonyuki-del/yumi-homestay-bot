# DeepSeek V4 Flash Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 DeepSeek V4 Flash 完整替换 Fenno，同时保留结构化客服决定、百居易只读工具、武汉实时旅游搜索和安全人工升级。

**Architecture:** 使用 OpenAI SDK 调用 DeepSeek Chat Completions，处理普通客服、JSON 决定和百居易工具；使用 Anthropic SDK 调用同一 DeepSeek 模型的原生 Web Search，处理武汉旅游咨询。应用层通过统一 `DeepSeekGuestAssistant` 选择路径，所有外部失败转换为领域异常，由 `ConversationService` 给客人固定提示、通知员工并切换人工。

**Tech Stack:** Python 3.12、OpenAI Python SDK、Anthropic Python SDK、DeepSeek V4 Flash、Pydantic、FastAPI、SQLAlchemy、pytest、Ruff、mypy

---

## 文件边界

- 新建 `src/homestay_bot/integrations/deepseek_client.py`
  - 定义 `AssistantDecision`、`BookingFields`、`AssistantUnavailableError`。
  - 调用 DeepSeek Chat Completions。
  - 执行百居易只读工具闭环。
  - 调用旅游搜索端口并归一化客服决定。
- 新建 `src/homestay_bot/integrations/deepseek_tourism.py`
  - 调用 DeepSeek Anthropic Web Search。
  - 提取搜索证据并生成无链接旅游回复。
- 修改 `src/homestay_bot/integrations/tourism.py`
  - 删除 Fenno Responses 专用工具和引用解析。
  - 保留意图识别、搜索状态和无链接格式化。
- 修改 `src/homestay_bot/config.py`
  - 把模型配置改为 DeepSeek 专用字段。
- 修改 `src/homestay_bot/application.py`
  - 装配 DeepSeek Chat 与 Anthropic 两个客户端。
- 修改 `src/homestay_bot/services/conversation_service.py`
  - 捕获普通模型不可用异常并安全切换人工。
- 修改 `pyproject.toml`
  - 增加 Anthropic SDK。
- 新建 `tests/unit/test_deepseek_client.py`
  - 覆盖 JSON、风险归一化、工具调用、隐私和重试。
- 新建 `tests/unit/test_deepseek_tourism.py`
  - 覆盖 Web Search、证据提取、无链接和失败分类。
- 修改 `tests/unit/test_conversation_service.py`
  - 覆盖普通模型失败的固定回复和人工升级。
- 修改 `tests/integration/test_runtime_startup.py`
  - 覆盖 DeepSeek 双客户端装配。
- 新建 `tests/contract/test_deepseek_contract.py`
  - 显式启用真实 DeepSeek 契约。
- 删除 `tests/unit/test_openai_client.py`、
  `tests/contract/test_fenno_fallback_contract.py` 和
  `tests/contract/test_fenno_web_search_contract.py`。
- 删除 `src/homestay_bot/integrations/openai_client.py`。
- 修改 `tasks/todo.md`
  - 记录测试、部署和企业微信验收证据。

## Task 1：切换配置模型并增加 Anthropic 依赖

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/homestay_bot/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1：写 DeepSeek 配置失败测试**

在 `tests/unit/test_config.py` 使用完整最小环境构造 `Settings`：

```python
environment = {
    "DATABASE_URL": database_url,
    "PUBLIC_BASE_URL": "https://local.example",
    "DEEPSEEK_API_KEY": "test-deepseek-key",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.test",
    "DEEPSEEK_MODEL": "deepseek-v4-flash",
    "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
    "WECOM_CORP_ID": "corp-id",
    "WECOM_KF_SECRET": "kf-secret",
    "WECOM_CALLBACK_TOKEN": "callback-token",
    "WECOM_ENCODING_AES_KEY": "A" * 43,
    "WECOM_AGENT_ID": "100001",
    "WECOM_AGENT_SECRET": "agent-secret",
    "WECOM_DUTY_USERIDS": "staff-1",
    "SESSION_SECRET": "local-test-session-secret-at-least-32",
}
```

```python
settings = Settings()
assert settings.deepseek_api_key == "test-deepseek-key"
assert settings.deepseek_model == "deepseek-v4-flash"
assert settings.deepseek_anthropic_base_url == (
    "https://api.deepseek.test/anthropic"
)
```

- [ ] **Step 2：运行测试确认失败**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_config.py -v
```

Expected: FAIL，`Settings` 尚无 `deepseek_api_key`。

- [ ] **Step 3：修改依赖和配置字段**

在 `pyproject.toml` 的运行依赖中加入：

```toml
"anthropic>=0.52,<1",
```

把 `Settings` 中三个模型字段替换为：

```python
deepseek_api_key: str
deepseek_base_url: str = "https://api.deepseek.com"
deepseek_model: str = "deepseek-v4-flash"

@property
def deepseek_anthropic_base_url(self) -> str:
    """从唯一 DeepSeek 根地址派生 Anthropic 兼容地址。"""
    return f"{self.deepseek_base_url.rstrip('/')}/anthropic"
```

- [ ] **Step 4：安装开发环境新依赖**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pip" install \
  "anthropic>=0.52,<1"
```

Expected: 安装成功，且
`"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/python" -c "import anthropic"`
退出码为 0。

- [ ] **Step 5：运行配置测试**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_config.py -v
```

Expected: PASS。

- [ ] **Step 6：提交配置边界**

```bash
git add pyproject.toml src/homestay_bot/config.py tests/unit/test_config.py
git commit -m "chore: configure DeepSeek model clients"
```

## Task 2：实现 DeepSeek Chat 结构化客服决定

**Files:**

- Create: `src/homestay_bot/integrations/deepseek_client.py`
- Create: `tests/unit/test_deepseek_client.py`

- [ ] **Step 1：写普通 JSON 回复失败测试**

测试桩通过 `client.chat.completions.create()` 返回：

```python
payload = {
    "reply_text": "下午三点后可以入住。",
    "language": "zh",
    "intent": "faq",
    "confidence": 0.98,
    "handoff_reason": None,
    "booking_fields": None,
    "knowledge_gap": False,
    "knowledge_gap_topic": None,
    "staff_confirmation_required": False,
    "staff_confirmation_reason": None,
}
message = SimpleNamespace(
    content=json.dumps(payload, ensure_ascii=False),
    tool_calls=None,
)
return SimpleNamespace(choices=[SimpleNamespace(message=message)])
```

核心断言：

```python
decision = await assistant.respond(
    guest_identifier="wm-sensitive-id",
    language=Language.ZH,
    messages=[{"role": "user", "content": "几点入住？"}],
)
request = client.chat.completions.requests[0]
assert decision.reply_text == "下午三点后可以入住。"
assert request["model"] == "deepseek-v4-flash"
assert request["response_format"] == {"type": "json_object"}
assert request["extra_body"] == {"thinking": {"type": "disabled"}}
assert "wm-sensitive-id" not in json.dumps(request, ensure_ascii=False)
```

- [ ] **Step 2：写 JSON 空内容只重试一次的失败测试**

配置桩第一次返回 `content=""`，第二次返回合法决定，并断言：

```python
assert len(client.chat.completions.requests) == 2
assert decision.intent == "faq"
```

配置两次都返回空内容，并断言：

```python
with pytest.raises(AssistantUnavailableError):
    await assistant.respond(
        guest_identifier="wm-guest",
        language=Language.ZH,
        messages=[{"role": "user", "content": "几点入住？"}],
    )
assert len(client.chat.completions.requests) == 2
```

- [ ] **Step 3：运行测试确认失败**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_client.py -k "json or retry" -v
```

Expected: FAIL，`deepseek_client` 尚不存在。

- [ ] **Step 4：实现决定模型、提示词和 JSON 校验**

创建以下公开类型：

```python
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
```

`DeepSeekGuestAssistant` 构造函数固定为：

```python
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
```

普通请求必须包含：

```python
request = {
    "model": self._model,
    "messages": [{"role": "system", "content": system_prompt}, *minimized],
    "response_format": {"type": "json_object"},
    "extra_body": {"thinking": {"type": "disabled"}},
}
```

系统提示必须明确要求输出 JSON，并包含
`json.dumps(assistant_decision_schema(), ensure_ascii=False)`。解析使用
`AssistantDecision.model_validate_json(content)`；空内容、Pydantic 校验失败和
DeepSeek SDK 异常统一重试一次，第二次抛出 `AssistantUnavailableError`。

- [ ] **Step 5：迁移风险归一化和隐私最小化**

从现有实现迁移并保留这些签名：

```python
def _validate_decision(
    self,
    output_text: str,
    question_text: str,
) -> AssistantDecision:

@staticmethod
def _minimize_personal_data(
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
```

归一化优先级保持：

1. `staff_confirmation_required`；
2. 低置信度交易问题；
3. 低置信度民宿专属问题；
4. 普通低置信度问题不产生提醒。

- [ ] **Step 6：运行结构化客服测试**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_client.py -k "json or retry or confidence or personal" -v
```

Expected: 全部通过。

- [ ] **Step 7：提交普通客服适配器**

```bash
git add src/homestay_bot/integrations/deepseek_client.py tests/unit/test_deepseek_client.py
git commit -m "feat: add DeepSeek structured guest assistant"
```

## Task 3：迁移百居易只读工具闭环

**Files:**

- Modify: `src/homestay_bot/integrations/deepseek_client.py`
- Modify: `tests/unit/test_deepseek_client.py`

- [ ] **Step 1：写 Chat Completions 工具调用失败测试**

第一轮返回：

```python
tool_call = SimpleNamespace(
    id="call-1",
    type="function",
    function=SimpleNamespace(
        name="search_availability",
        arguments=(
            '{"check_in_date":"2026-07-30",'
            '"check_out_date":"2026-07-31"}'
        ),
    ),
)
message = SimpleNamespace(content=None, tool_calls=[tool_call])
```

第二轮返回合法 JSON。断言：

```python
assert executor.calls == [
    (
        "search_availability",
        {
            "check_in_date": "2026-07-30",
            "check_out_date": "2026-07-31",
        },
    )
]
second_messages = client.chat.completions.requests[1]["messages"]
assert second_messages[-1] == {
    "role": "tool",
    "tool_call_id": "call-1",
    "content": '{"available": true, "rooms": 1}',
}
```

另加白名单测试：

```python
with pytest.raises(ValueError, match="不允许"):
    await executor.execute("create_reservation", {})
```

- [ ] **Step 2：运行测试确认失败**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_client.py -k "tool" -v
```

Expected: FAIL，当前适配器未处理 `message.tool_calls`。

- [ ] **Step 3：实现 DeepSeek 工具定义**

工具结构必须是：

```python
{
    "type": "function",
    "function": {
        "name": "search_availability",
        "description": "查询指定入住和退房日期的物理房间可用性。",
        "parameters": {
            "type": "object",
            "properties": {
                "check_in_date": {"type": "string", "format": "date"},
                "check_out_date": {"type": "string", "format": "date"},
            },
            "required": ["check_in_date", "check_out_date"],
            "additionalProperties": False,
        },
    },
}
```

`search_reference_price` 使用相同日期参数。不得定义
`lookup_reservation` 或 `create_reservation`。

- [ ] **Step 4：实现最多四轮工具闭环**

每轮按以下顺序处理：

```python
assistant_message = response.choices[0].message
if not assistant_message.tool_calls:
    return self._validate_decision(
        assistant_message.content or "",
        question_text,
    )

messages.append(assistant_message.model_dump(exclude_none=True))
for call in assistant_message.tool_calls:
    arguments = json.loads(call.function.arguments)
    result = await self._tool_executor.execute(call.function.name, arguments)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(result, ensure_ascii=False),
        }
    )
```

第四轮后仍有工具调用时抛出 `AssistantUnavailableError`。工具执行异常转换为
同一异常，错误正文不得进入客人回复。

- [ ] **Step 5：迁移 Hostex 只读执行器**

迁移 `HostexReadOnlyClient` 和 `HostexReadOnlyToolExecutor`，保留公开签名及
以下分支：

```python
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
```

- [ ] **Step 6：运行工具与相对日期测试**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_client.py -k "tool or relative_date or missing_dates" -v
```

Expected: 全部通过；“今天入住明天退房”提示仍包含武汉准确日期映射。

- [ ] **Step 7：提交工具迁移**

```bash
git add src/homestay_bot/integrations/deepseek_client.py tests/unit/test_deepseek_client.py
git commit -m "feat: run Hostex tools through DeepSeek chat"
```

## Task 4：实现 DeepSeek Anthropic 武汉实时搜索

**Files:**

- Create: `src/homestay_bot/integrations/deepseek_tourism.py`
- Create: `tests/unit/test_deepseek_tourism.py`
- Modify: `src/homestay_bot/integrations/tourism.py`
- Modify: `tests/unit/test_tourism.py`

- [ ] **Step 1：写原生 Web Search 请求失败测试**

构造 Anthropic 桩，返回三个内容块：

```python
SimpleNamespace(type="server_tool_use", name="web_search"),
SimpleNamespace(
    type="web_search_tool_result",
    content=[
        SimpleNamespace(
            type="web_search_result",
            title="武汉市文化和旅游局",
            url="https://wlj.wuhan.gov.cn/example",
        )
    ],
),
SimpleNamespace(
    type="text",
    text="推荐黄鹤楼、东湖和湖北省博物馆。",
),
```

断言：

```python
result = await searcher.search(
    question="武汉近期有什么好玩的？",
    language=Language.ZH,
    queried_on=date(2026, 7, 30),
)
request = client.messages.requests[0]
assert request["model"] == "deepseek-v4-flash"
assert request["messages"] == [
    {"role": "user", "content": "武汉近期有什么好玩的？"}
]
assert request["tools"] == [
    {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 2,
        "user_location": {
            "type": "approximate",
            "country": "CN",
            "city": "Wuhan",
            "region": "Hubei",
        },
    }
]
assert "参考来源：武汉市文化和旅游局" in result
assert "https://" not in result
```

- [ ] **Step 2：写无证据和外部失败测试**

无 `web_search_tool_result` 时：

```python
with pytest.raises(TourismSearchError) as error:
    await searcher.search(
        question="武汉近期有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )
assert error.value.status == "degraded"
assert statuses == ["degraded"]
```

SDK 抛出参数不支持错误时：

```python
assert error.value.status == "unsupported"
assert statuses == ["unsupported"]
```

- [ ] **Step 3：运行测试确认失败**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_tourism.py -v
```

Expected: FAIL，旅游搜索适配器尚不存在。

- [ ] **Step 4：实现旅游搜索端口和适配器**

公开端口：

```python
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
```

实现类：

```python
class DeepSeekTourismSearcher:
    """通过 DeepSeek Anthropic 原生 Web Search 回答武汉旅游问题。"""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        status_setter: Callable[[WebSearchStatus], None] | None = None,
    ) -> None:
```

`search()` 使用 `max_tokens=1800`、最多两次搜索，并要求优先官方来源。输出解析
必须递归读取 `web_search_tool_result.content`，收集
`(title, url)`；没有来源或正文时抛出 `TourismSearchError("degraded")`。

- [ ] **Step 5：移除 Fenno Responses 专用旅游代码**

从 `tourism.py` 删除：

```python
web_search_tool
extract_url_citations
```

保留并继续测试：

```python
latest_user_question
is_tourism_query
format_tourism_reply
WebSearchState
TourismSearchError
```

更新 `format_tourism_reply`，当来源显示名为空时抛出
`TourismSearchError("degraded")`，避免输出空的“参考来源”。

- [ ] **Step 6：把旅游路径接入组合助手**

在 `DeepSeekGuestAssistant.respond()` 开头保留本地意图门控：

```python
question_text = latest_user_question(messages)["content"]
if is_tourism_query(messages):
    minimized = self._minimize_personal_data(
        [{"role": "user", "content": question_text}]
    )
    return AssistantDecision(
        reply_text=await self._tourism_searcher.search(
            question=minimized[0]["content"],
            language=language,
            queried_on=self._local_date_provider(),
        ),
        language=language,
        intent="tourism",
        confidence=0.95,
    )
```

- [ ] **Step 7：运行全部旅游测试**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_tourism.py \
  tests/unit/test_tourism.py \
  tests/unit/test_deepseek_client.py -k "tourism or source or link" -v
```

Expected: 全部通过，客人回复不包含 `http://`、`https://` 或 Markdown 链接。

- [ ] **Step 8：提交 DeepSeek 旅游搜索**

```bash
git add \
  src/homestay_bot/integrations/deepseek_tourism.py \
  src/homestay_bot/integrations/deepseek_client.py \
  src/homestay_bot/integrations/tourism.py \
  tests/unit/test_deepseek_tourism.py \
  tests/unit/test_deepseek_client.py \
  tests/unit/test_tourism.py
git commit -m "feat: search Wuhan tourism with DeepSeek"
```

## Task 5：增加普通模型失败的安全人工升级

**Files:**

- Modify: `src/homestay_bot/services/conversation_service.py`
- Modify: `tests/unit/test_conversation_service.py`

- [ ] **Step 1：写普通模型失败测试**

新增助手桩：

```python
class FailingAssistantStub(AssistantStub):
    """模拟 DeepSeek 普通客服连接或结构化输出失败。"""

    async def respond(self, **kwargs) -> AssistantDecision:
        """抛出不包含外部错误正文的统一异常。"""
        self.calls += 1
        raise AssistantUnavailableError()
```

中文测试断言：

```python
await service.handle_message(incoming(content="几点入住？"))
assert "暂时无法处理" in wecom.guest_messages[0]
assert "模型服务暂时不可用" in wecom.internal_messages[0]
assert conversations.conversation.mode is ConversationMode.HUMAN_ACTIVE
```

英文测试断言回复包含 `temporarily unable to process`。

- [ ] **Step 2：运行测试确认失败**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_conversation_service.py -k "model_failure" -v
```

Expected: FAIL，异常尚未转换为客人回复。

- [ ] **Step 3：实现统一失败分支**

在 `handle_message()` 中增加：

```python
except AssistantUnavailableError:
    await self._escalate_assistant_failure(conversation, message)
    return
```

新增：

```python
async def _escalate_assistant_failure(
    self,
    conversation: Conversation,
    message: IncomingMessage,
) -> None:
    """告知普通模型暂不可用，再切人工并通知值班员工。"""
    reply = (
        "I’m temporarily unable to process this request. "
        "A staff member has been notified to help you."
        if conversation.language is Language.EN
        else "暂时无法处理这个问题，已为您通知工作人员协助，请稍候。"
    )
    await self._send_guest_reply(conversation, reply)
    conversation.mode = ConversationMode.HUMAN_ACTIVE
    await self._conversations.save(conversation)
    await self._notify_employee(
        conversation,
        message,
        "模型服务暂时不可用",
    )
```

- [ ] **Step 4：运行会话安全回归**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_conversation_service.py -v
```

Expected: 全部通过；知识缺口和业务待确认仍保持 `BOT_ACTIVE`，只有模型失败、
旅游失败、明确人工、紧急、媒体和审批切换 `HUMAN_ACTIVE`。

- [ ] **Step 5：提交失败处理**

```bash
git add src/homestay_bot/services/conversation_service.py \
  tests/unit/test_conversation_service.py
git commit -m "feat: escalate DeepSeek service failures safely"
```

## Task 6：装配 DeepSeek 双客户端并删除 Fenno 实现

**Files:**

- Modify: `src/homestay_bot/application.py`
- Modify: `tests/integration/test_runtime_startup.py`
- Delete: `src/homestay_bot/integrations/openai_client.py`
- Delete: `tests/unit/test_openai_client.py`
- Delete: `tests/contract/test_fenno_fallback_contract.py`
- Delete: `tests/contract/test_fenno_web_search_contract.py`

- [ ] **Step 1：完成生命周期双客户端测试**

测试使用：

```python
monkeypatch.setattr("homestay_bot.application.AsyncOpenAI", FakeOpenAI)
monkeypatch.setattr("homestay_bot.application.AsyncAnthropic", FakeAnthropic)
```

两个桩都实现 `async def close(self) -> None`。断言应用退出后两个客户端的
`closed` 标记均为 `True`。

- [ ] **Step 2：运行生命周期测试确认失败**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/integration/test_runtime_startup.py -v
```

Expected: FAIL，应用仍引用 `GuestAssistant` 和旧模型配置。

- [ ] **Step 3：修改应用装配**

导入：

```python
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from homestay_bot.integrations.deepseek_client import (
    DeepSeekGuestAssistant,
    HostexReadOnlyToolExecutor,
)
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
```

在 `application_lifespan()` 创建：

```python
deepseek_chat = AsyncOpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
)
deepseek_anthropic = AsyncAnthropic(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_anthropic_base_url,
)
web_search_state = WebSearchState()
tourism_searcher = DeepSeekTourismSearcher(
    client=deepseek_anthropic,
    model=settings.deepseek_model,
    status_setter=web_search_state.set,
)
assistant = DeepSeekGuestAssistant(
    chat_client=deepseek_chat,
    tourism_searcher=tourism_searcher,
    knowledge=knowledge,
    model=settings.deepseek_model,
    safety_hmac_key=settings.session_secret.encode(),
    tool_executor=HostexReadOnlyToolExecutor(hostex),
)
```

生命周期退出时分别执行：

```python
await deepseek_chat.close()
await deepseek_anthropic.close()
```

- [ ] **Step 4：更新全部类型导入**

把以下文件中的
`homestay_bot.integrations.openai_client` 改为
`homestay_bot.integrations.deepseek_client`：

```text
src/homestay_bot/services/conversation_service.py
tests/unit/test_conversation_service.py
```

- [ ] **Step 5：删除旧实现和 Fenno 契约**

删除：

```text
src/homestay_bot/integrations/openai_client.py
tests/unit/test_openai_client.py
tests/contract/test_fenno_fallback_contract.py
tests/contract/test_fenno_web_search_contract.py
```

运行：

```bash
rg -n "Fenno|fenno|openai_client|OPENAI_API_KEY|OPENAI_BASE_URL|OPENAI_MODEL" \
  src tests pyproject.toml
```

Expected: 无匹配。

- [ ] **Step 6：运行生命周期和导入回归**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/integration/test_runtime_startup.py \
  tests/unit/test_conversation_service.py \
  tests/unit/test_deepseek_client.py \
  tests/unit/test_deepseek_tourism.py -v
```

Expected: 全部通过。

- [ ] **Step 7：提交应用切换**

```bash
git add -A
git commit -m "refactor: replace Fenno runtime with DeepSeek"
```

## Task 7：增加并运行真实 DeepSeek 契约

**Files:**

- Create: `tests/contract/test_deepseek_contract.py`

- [ ] **Step 1：写显式启用的真实契约**

文件级开关：

```python
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_CONTRACT") != "1",
    reason="需要显式启用真实 DeepSeek 契约测试",
)
```

真实客户端：

```python
settings = Settings()  # type: ignore[call-arg]
chat = AsyncOpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
)
anthropic = AsyncAnthropic(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_anthropic_base_url,
)
```

必须包含六个测试：

```text
test_general_question_returns_structured_decision
test_property_question_marks_knowledge_gap
test_refund_question_requires_staff_confirmation
test_relative_dates_call_read_only_availability_tool
test_wuhan_tourism_uses_web_search_evidence
test_tourism_reply_contains_no_links
```

关键断言：

```python
assert decision.handoff_reason is None
assert decision.knowledge_gap is True
assert refund.staff_confirmation_required is True
assert executor.calls[0][0] == "search_availability"
assert "参考来源：" in tourism.reply_text
assert "http://" not in tourism.reply_text
assert "https://" not in tourism.reply_text
```

- [ ] **Step 2：确认默认不访问网络**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/contract/test_deepseek_contract.py -v
```

Expected: 6 skipped。

- [ ] **Step 3：备份配置并暂存 DeepSeek 变量**

先创建固定、可恢复且不覆盖现有数据的备份目录：

```bash
test ! -e "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730"
mkdir -p "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730"
cp \
  "/Users/rin/Library/Application Support/HomestayBot/.env" \
  "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730/.env"
ditto \
  "/Users/rin/Library/Application Support/HomestayBot/src" \
  "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730/src"
```

使用受批准的文件编辑把当前任务上下文中的 DeepSeek 密钥写入本机 `.env`，
同时写入：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

此时保留原 `OPENAI_*` 变量且不重启服务，所以正在运行的旧版本不受影响。
检查时只输出 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`，不得打印密钥。

- [ ] **Step 4：运行真实契约**

工作区 `.env` 指向本机配置，直接运行：

```bash
RUN_DEEPSEEK_CONTRACT=1 \
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/contract/test_deepseek_contract.py -v -s
```

Expected: 6 passed。若 Web Search 返回 `unsupported`，停止迁移并保留当前本机
运行版本，不得切换配置或引入第三方搜索。

- [ ] **Step 5：提交真实契约**

```bash
git add tests/contract/test_deepseek_contract.py
git commit -m "test: verify real DeepSeek guest workflows"
```

## Task 8：全量质量与安全回归

**Files:**

- Modify only if a failing check exposes a defect in files listed above.

- [ ] **Step 1：运行全量测试**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" -q
```

Expected: 全部本地测试通过；6 个 DeepSeek 外部契约默认跳过。

- [ ] **Step 2：运行静态检查**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/ruff" check src tests
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/mypy" src
```

Expected: Ruff `All checks passed!`；mypy `Success: no issues found`。

- [ ] **Step 3：运行关键安全回归**

Run:

```bash
"/Volumes/02/obsidian codex/homestay-bot/.venv/bin/pytest" \
  tests/unit/test_deepseek_client.py \
  tests/unit/test_deepseek_tourism.py \
  tests/unit/test_conversation_service.py \
  tests/unit/test_emergency_service.py \
  tests/integration/test_message_flow.py -v
```

Expected:

- 普通低置信度问题不切人工；
- 知识缺口和业务待确认只通知一次且保持 `BOT_ACTIVE`；
- DeepSeek 普通失败和旅游失败切换人工；
- 百居易工具保持只读；
- 客人身份和手机号不发送给模型；
- 上下文按处理顺序排列；
- 旅游回复无链接。

- [ ] **Step 4：确认工作区**

Run:

```bash
git diff --check
git status --short
```

Expected: 无空白错误，只保留待记录的 `tasks/todo.md` 更新。

## Task 9：安全写入配置并部署本机

**Files/State:**

- Source: `/Volumes/02/obsidian codex/homestay-bot/.worktrees/wuhan-tourism-search`
- Runtime: `/Users/rin/Library/Application Support/HomestayBot`
- Runtime config: `/Users/rin/Library/Application Support/HomestayBot/.env`
- Health: `http://127.0.0.1:8010/health`

- [ ] **Step 1：确认可恢复备份**

确认 Task 7 创建的固定备份完整：

```bash
test -f "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730/.env"
test -d "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730/src"
```

- [ ] **Step 2：写入 DeepSeek 配置**

保留 Task 7 已安全写入的 `DEEPSEEK_*` 配置，并从本机 `.env` 删除：

```text
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
```

写入后只检查变量名和模型值，不打印 `DEEPSEEK_API_KEY`。

- [ ] **Step 3：同步源码并重启**

Run:

```bash
ditto \
  "/Volumes/02/obsidian codex/homestay-bot/.worktrees/wuhan-tourism-search/src" \
  "/Users/rin/Library/Application Support/HomestayBot/src"
launchctl kickstart -k "gui/501/com.rin.homestay-bot"
curl --retry 5 --retry-connrefused --retry-delay 1 \
  -sS -i "http://127.0.0.1:8010/health"
```

Expected: HTTP 200，`database`、`worker_heartbeat`、`wecom_polling`、
`configuration` 均为 `ok`，首次搜索前 `web_search` 为 `unknown`。

- [ ] **Step 4：部署失败时回滚**

仅当启动或健康检查失败时：

```bash
cp \
  "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730/.env" \
  "/Users/rin/Library/Application Support/HomestayBot/.env"
ditto \
  "/Users/rin/Library/Application Support/HomestayBot/.backups/deepseek-precutover-20260730/src" \
  "/Users/rin/Library/Application Support/HomestayBot/src"
launchctl kickstart -k "gui/501/com.rin.homestay-bot"
```

回滚后健康检查必须恢复 HTTP 200。

## Task 10：企业微信端到端验收并记录证据

**Files:**

- Modify: `tasks/todo.md`

- [ ] **Step 1：恢复测试会话机器人模式**

只把明确用于本轮测试的会话设置为 `BOT_ACTIVE`，不得批量修改其他客人会话。

- [ ] **Step 2：依次发送验收消息**

```text
和朋友旅行时怎样更高效地协调行程？
你们有停车场吗？
这个订单能退款多少？
今天入住明天退房，请问还有几间房？
还有房吗？
武汉近期有什么好玩的？
```

每条等待上一条处理完成后再发送。

- [ ] **Step 3：核对客人和员工结果**

逐条确认：

```text
普通问题：实用回答；无员工提醒；BOT_ACTIVE
停车问题：未确认 + 替代建议；知识库待补充；BOT_ACTIVE
退款问题：不猜金额；业务待确认；BOT_ACTIVE
相对日期：直接换算武汉日期并查房；无重复追问
缺少日期：只追问入住和退房日期；无缺口提醒
旅游问题：实时回答 + 来源名称；无网址；BOT_ACTIVE
```

- [ ] **Step 4：验证失败路径**

使用测试桩或临时不可达地址在隔离测试中验证，不修改正在接待的生产配置：

```text
普通模型失败：固定提示 + 员工提醒 + HUMAN_ACTIVE
旅游搜索失败：固定提示 + 员工提醒 + HUMAN_ACTIVE
```

- [ ] **Step 5：记录最终证据**

在 `tasks/todo.md` Review 中记录：

- 全量测试通过与跳过数量；
- Ruff 与 mypy 结果；
- 真实 DeepSeek 六项契约结果；
- 健康检查结果；
- 六条企业微信客人回复摘要；
- 两类员工提醒数量；
- 最终会话模式；
- Fenno 运行时引用扫描结果。

- [ ] **Step 6：提交验收记录**

```bash
git add tasks/todo.md
git commit -m "chore: record DeepSeek migration verification"
```

## 自检结论

- Spec 覆盖：DeepSeek Chat、Anthropic Web Search、同一密钥、Fenno 弃用、
  JSON 重试、只读工具、隐私、人工升级、真实契约、部署备份和回滚均有明确
  任务。
- 类型一致：全计划统一使用 `DeepSeekGuestAssistant`、
  `DeepSeekTourismSearcher`、`AssistantUnavailableError` 和
  `TourismSearchError`。
- 安全边界：普通模型不获得搜索工具，旅游模型不获得百居易工具，百居易不
  暴露写操作，失败时不生成虚假答案。
- 迁移门槛：真实 DeepSeek Web Search 未通过时不得写入生产配置或删除本机
  可恢复备份。
