# DeepSeek V4 Flash Migration Design

## 目标

将民宿客服机器人的模型供应商完整切换为 DeepSeek，使用
`deepseek-v4-flash` 处理普通客服、知识库问答、百居易只读查询和武汉实时
旅游搜索。运行时不得再调用 Fenno；模型或搜索失败时明确告知客人、通知
值班员工并切换人工接待。

## 已确认范围

- 使用用户提供的 DeepSeek API 密钥，仅写入本机运行环境。
- 普通客服使用 DeepSeek OpenAI 兼容 Chat Completions API。
- 武汉实时旅游搜索使用 DeepSeek Anthropic 兼容 API 的原生 Web Search。
- 两种接口使用同一个 DeepSeek 密钥和 `deepseek-v4-flash` 模型。
- 保留知识缺口提醒、业务待确认、相对日期理解和百居易只读工具。
- 客人可见旅游回复不包含 Markdown 链接、裸网址或其他可点击链接。
- DeepSeek 失败时不回退 Fenno。

## 现状与依据

### 当前代码

- `src/homestay_bot/config.py::Settings` 使用
  `openai_api_key`、`openai_base_url` 和 `openai_model`。
- `src/homestay_bot/application.py::application_lifespan` 创建一个
  `AsyncOpenAI`，并把它注入 `GuestAssistant`。
- `src/homestay_bot/integrations/openai_client.py::GuestAssistant.respond`
  使用 Responses API 完成结构化输出、百居易工具闭环和旅游 Web Search。
- `src/homestay_bot/integrations/tourism.py::extract_url_citations` 读取
  Responses API 的引用或搜索来源。
- `src/homestay_bot/services/conversation_service.py::handle_message`
  只对 `TourismSearchError` 设置了固定失败回复；普通模型异常目前会返回
  worker，由后台重试。
- `src/homestay_bot/integrations/openai_client.py::HostexReadOnlyToolExecutor`
  只允许查询房态和参考价，不暴露任何百居易写操作。

### 官方接口依据

- DeepSeek V4 Flash 的 OpenAI 兼容基础地址是
  `https://api.deepseek.com`，模型名是 `deepseek-v4-flash`。
- DeepSeek OpenAI 兼容接口提供 Chat Completions、JSON Output 和工具调用，
  但不提供当前代码依赖的 Responses API。
- DeepSeek Anthropic 兼容接口地址是
  `https://api.deepseek.com/anthropic`，支持服务端 Web Search 输出类型。

官方资料：

- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/api/create-chat-completion
- https://api-docs.deepseek.com/guides/json_mode/
- https://api-docs.deepseek.com/guides/tool_calls
- https://api-docs.deepseek.com/guides/anthropic_api

## 方案比较

### 方案 A：DeepSeek 双接口（采用）

- Chat Completions 负责普通客服、结构化决定和百居易只读工具。
- Anthropic Messages 负责 DeepSeek 原生 Web Search。
- 优点：同一供应商、同一密钥，同时保留结构化输出和实时旅游能力。
- 代价：需要维护两个轻量协议适配器。

### 方案 B：全部使用 Anthropic Messages

- 优点：协议表面统一。
- 缺点：现有严格 JSON 决定需要改造成强制工具结果，结构化输出迁移风险更高。

### 方案 C：Chat Completions 加第三方搜索

- 优点：普通模型接口统一。
- 缺点：需要额外搜索服务、密钥、费用和隐私边界，不符合当前目标。

## 架构

### DeepSeek 普通客服适配器

新增 DeepSeek Chat Completions 适配层，承担以下职责：

1. 把现有系统提示、审核知识和有限对话上下文转换为 Chat Completions
   `messages`。
2. 对非旅游问题启用 JSON Output，并把内容校验为现有
   `AssistantDecision`。
3. 保留应用层回答风险归一化：
   - 普通常识可以谨慎回答；
   - 民宿专属信息缺失标记 `knowledge_gap`；
   - 未确认交易事实标记 `staff_confirmation_required`；
   - 不因普通低置信度自动切人工。
4. 把房态和参考价定义转换为 Chat Completions 函数工具。
5. 只执行 `HostexReadOnlyToolExecutor` 白名单内工具，并以
   `assistant.tool_calls` 与 `tool` 消息完成最多四轮闭环。
6. 最终结构化内容为空或校验失败时重试一次；第二次失败抛出统一的
   `AssistantUnavailableError`。

普通客服固定使用非思考模式，降低企业微信回复延迟。

### DeepSeek 旅游搜索适配器

新增 DeepSeek Anthropic Messages 旅游搜索适配层，承担以下职责：

1. 只接收脱敏后的最新一条旅游问题，不发送完整历史、姓名、手机号或
   企业微信原始用户 ID。
2. 调用 DeepSeek 原生 Web Search，并限制搜索次数。
3. 系统提示固定武汉、湖北、中国位置语境，优先政府、场馆、景区和主办方
   来源。
4. 从 `server_tool_use` 和 `web_search_tool_result` 中提取搜索证据。
5. 要求回复提供三至五项推荐或半日/一日路线。
6. 客人正文保留查询日期和去重来源名称，但删除全部网址和 Markdown 链接。
7. 没有有效搜索证据、API 不支持工具或输出异常时抛出
   `TourismSearchError`。

### 应用装配

`application_lifespan` 创建：

- 一个指向 `https://api.deepseek.com` 的 OpenAI SDK 客户端；
- 一个指向 `https://api.deepseek.com/anthropic` 的 Anthropic SDK 客户端；
- 一个组合助手，根据本地旅游意图分类选择对应适配器。

模型选择在应用层完成，模型不得自行把普通问题升级为联网搜索。

### 会话失败处理

`ConversationService.handle_message` 增加普通模型失败边界：

- 捕获 `AssistantUnavailableError`；
- 向客人发送固定的中英文暂时不可用提示；
- 通知值班员工 `XuKuang`；
- 将会话切换为 `HUMAN_ACTIVE`。

旅游搜索继续使用现有 `TourismSearchError` 分支。两类失败都不调用 Fenno。

## 配置

运行时使用：

```text
DEEPSEEK_API_KEY=<本机密钥>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

`Settings` 不再要求 Fenno/OpenAI模型配置。DeepSeek Anthropic 地址由
`DEEPSEEK_BASE_URL` 稳定派生为 `/anthropic`，避免配置两个可能不一致的
地址。

密钥要求：

- 不进入 Git；
- 不写入测试文件；
- 不出现在日志、错误正文、健康页或员工提醒；
- 切换配置前备份当前本机 `.env`。

## 安全边界

- 百居易仍只允许房态和参考价查询。
- 客人明确确认完整预订资料后仍只创建待审批单。
- 客人姓名、手机号和企业微信 ID 继续执行数据最小化。
- 外部网页内容视为不可信资料，不得改变系统规则或触发写操作。
- 普通模型请求不获得 Web Search 工具。
- 旅游请求不获得百居易工具。
- 一条未解决回复最多产生一种员工提醒。

## 错误与重试

- JSON 空内容或格式错误：最多重试一次。
- DeepSeek 连接、鉴权、余额、限流或服务端错误：转换为统一领域异常，
  不把外部错误正文发给客人。
- 百居易只读工具失败：不得生成虚假房态或价格，升级人工。
- 旅游搜索没有有效结果：按旅游搜索失败处理。
- worker 仍保留消息去重，避免恢复后重复回复。

## 验证

### 单元与集成测试

- Chat Completions 请求格式、JSON 解析和一次重试。
- 工具调用重放、工具白名单和最多四轮限制。
- 普通问题、知识缺口和交易待确认归一化。
- Anthropic Web Search 请求、证据提取和链接移除。
- 普通模型失败与旅游失败的客人提示、员工提醒和会话状态。
- 隐私脱敏、消息去重和上下文处理顺序回归。

### 真实 DeepSeek 契约

部署前必须使用真实 DeepSeek 密钥通过：

1. 普通客服 JSON 决定；
2. 民宿停车知识缺口；
3. 未确认退款业务提醒；
4. 相对日期触发百居易工具；
5. 武汉实时旅游 Web Search；
6. 客人旅游回复不包含链接。

任何一项失败都不得切换本机生产配置。

### 企业微信验收

切换后重新验证：

- 普通旅行协调问题；
- 停车知识缺口和员工提醒；
- 退款待确认和员工提醒；
- “今天入住明天退房”的房态查询；
- “还有房吗”的必要日期追问；
- 武汉近期旅游实时搜索；
- 模型失败固定提示与人工接管。

## 部署与回滚

1. 完成测试并通过真实 DeepSeek 契约。
2. 备份本机 `.env` 和当前运行源码。
3. 写入 DeepSeek 配置并同步新源码。
4. 重启 LaunchAgent，检查健康页。
5. 完成企业微信验收。
6. 若健康检查或真实消息失败，恢复切换前源码与配置并重启。

回滚只用于部署故障恢复；正常运行时不得自动回退 Fenno。

## 非目标

- 不增加第三方搜索服务。
- 不保留 Fenno 运行时回退。
- 不改变百居易写入审批架构。
- 不让模型自动写入知识库。
- 不修改企业微信回调、可信 IP 或公网隧道配置。
