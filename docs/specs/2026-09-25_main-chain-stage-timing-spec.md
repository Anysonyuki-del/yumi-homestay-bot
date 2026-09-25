# 普通回复主链分阶段耗时日志 Spec

版本：R2，2026-09-26。状态：**用户回复「开始」；已实施，发布 1.40.2。**

R2 修订：本 Spec 由另一个会话按 9 月 25 日的代码写成，R2 按 main `68417df`（1.40.1）重新核对：
- 行号更新；
- 补上 1.40.0 带来的三点变化；
- 验证部分补上部署时的回归门禁；
- D1 改为 1.40.2；
- D4 按用户 2026-09-26 的决定「开放1和3」改为记录 msgid 与内部会话编号。

目标：只增加耗时观测，不改变任何回复内容、分支走向、异常类型、重试或发送行为。拿到数据后再决定「知识检索与 FAQ 并行」「合并等待自适应」是否值得做。

## 1. 现状证据

| 事实 | 依据 |
| --- | --- |
| 生产正式回复调用 `respond` 时不传 `tool_trace_sink`，工具耗时只在后台调试页和回归工具里记录 | `services/conversation_service.py::ConversationService._process_model_reply`（901 行）；`services/admin_debug_service.py:211`；`tools/reply_regression.py:551` |
| `respond` 主链唯一相关日志是请求字符预算，没有耗时 | `integrations/deepseek_client.py::DeepSeekGuestAssistant.respond`（「DeepSeek 主链预算」日志） |
| 联网搜索自身已记 `duration_ms`，但日志不带消息关联，无法与单条回复对应 | `integrations/deepseek_tourism.py`（「旅游搜索完成」日志） |
| 知识检索与 FAQ 候选串行执行；知识检索内含向量接口调用（超时 3 秒） | `deepseek_client.py::respond`（1789–1791 行）；`services/knowledge_embeddings.py::SemanticRanker.rank` |
| 生产回复先写 outbox，真实企业微信发送在独立任务中，其耗时可从 `jobs` 表时间字段只读查到 | `conversation_service.py::_send_prepared_guest_reply`；`domain/models.py::Job` |
| 1.39.14 实测普通问题端到端 10～15 秒，其中企业微信送达 1～8 秒；`respond` 内部没有拆解数据 | `docs/releases/1.39.14.md` |
| 生产 `assistant` 就是 `DeepSeekGuestAssistant` 本体，无转发包装；测试替身与调试包装器都用 `**kwargs` 接收参数 | `services/runtime_clients.py:477`；`application.py:3811`；`admin_debug_service.py:106`；`tests/unit/test_conversation_service.py:220` 等 |
| `_process_model_reply` 只被 `handle_message` 与 `process_recorded_message` 调用，测试不直接引用 | `conversation_service.py:535`、`:626` |

| 1.40.0 起 `respond` 有两个在知识检索之前的提前返回：联网问题；问价没有日期时直接追问日期，不调用模型 | `deepseek_client.py::respond`（1722、1772 行） |
| 1.40.0 起，紧急情况进行中的求助类后续在 `handle_message` 中就回固定答复，不进 `_process_model_reply` | `conversation_service.py::_answer_emergency_follow_up` |
| 1.40.0 起参考价工具一次调用内部查 3 个百居易接口（房源、房态、渠道参考价） | `deepseek_client.py::HostexReadOnlyToolExecutor.execute` |

## 2. 功能点

### F1 `respond` 内部阶段计时

`DeepSeekGuestAssistant.respond` 新增可选参数 `stage_timing_sink: Callable[[str, int], None] | None = None`。为 `None` 时行为与现在完全一致。记录点（阶段名, 毫秒）：

| 阶段名 | 位置 |
| --- | --- |
| `tourism_search` | 联网搜索 `self._tourism_searcher.search` |
| `knowledge` | `self._knowledge.retrieve`（含向量接口） |
| `faq_context` | `self._build_faq_candidate_context` |
| `main_call` | 每次主链 `chat.completions.create`，可出现多次 |
| `tool:<工具名>` | 每次只读工具执行，复用已计算的耗时 |
| `refine` | `_refine_reply` 调用；未超 1000 字直接返回时接近 0 |

问价无日期的提前返回不产生任何阶段，结果类别记为 `replied`。`tool:search_reference_price` 包含内部的 3 个百居易接口调用，不再细分。

计时方式：`monotonic()` 前后取值，用 `try/finally` 只包住单个 `await`；不新增 `except`，不改变返回值、异常类型或重试次数。

### F2 接口声明

`conversation_service.py::GuestAssistantPort.respond` 增加同名可选参数，保证 mypy strict 通过。

### F3 每次正式处理输出一行汇总日志

`ConversationService._process_model_reply` 拆为薄外层（计时与记日志）和原有主体（逻辑不变，只在各返回点前标记结果类别）。外层在 `finally` 中输出一行 INFO，异常原样抛出：

```
主链耗时：outcome=replied conversation_id=1 msgid=msg-xxx total_ms=6120 since_sent_ms=4210 context_ms=35 respond_ms=5890 post_ms=190 merged=1 stages=knowledge:820,faq_context:12,main_call:2310,tool:search_availability:640,main_call:1900,refine:0
```

| 字段 | 含义 |
| --- | --- |
| `outcome` | `replied` / `facility` / `stale_discarded` / `tourism_failure` / `assistant_unavailable` / `error:<异常类型>` |
| `total_ms` | 进入 `_process_model_reply` 到结束 |
| `since_sent_ms` | 进入时距客人发送时间（企业微信时间戳，秒级精度，含送达、合并等待与排队），下限 0 |
| `context_ms` | 客户记忆与对话上下文加载 |
| `respond_ms` | `respond` 整体 |
| `post_ms` | `respond` 之后到结束（安全策略、写 outbox、FAQ 统计、任务建议、转人工等） |
| `conversation_id` | 内部会话编号，用来和数据库时间线对账 |
| `msgid` | 本次处理的客人消息编号（合并时为最后一条） |
| `merged` | 合并消息条数 |
| `stages` | F1 收集的阶段，按发生顺序 |

按用户 2026-09-26 的决定，服务器日志可以记录客人信息。这一行只多记会话编号和 msgid 用来对账，不记正文：正文和回复都在数据库里，按 msgid 就能查到。日志只留在服务器上，不进 GitHub。

### 不在本次范围

- 企业微信真实发送耗时：用 `jobs` 表只读查询，不改代码。
- 调试页、回归工具、快速安抚：不传 sink，不输出汇总。
- 并行化与合并等待调整：待数据出来后另立 Spec。
- 紧急情况后续的固定答复、客诉、转人工等不进 `_process_model_reply` 的路径：不输出汇总。

### 验证

1. 单元测试：
   - 用假 chat client 调 `respond`，断言 sink 收到的阶段名与顺序（普通问答、带工具、联网三条路径各一）；
   - 会话层：`replied`、`assistant_unavailable`、`stale_discarded` 各输出恰好一行汇总；
   - 异常路径：汇总照样输出，原异常类型不变地抛出；
   - 断言日志中不含客人问题原文与回复正文。
2. `ruff`、`mypy`（strict）、离线全量 `pytest`。
3. 本地开发阶段不调用真实 DeepSeek、百居易、企业微信。
4. 部署时会触发回归门禁，因为改了 `integrations/` 与 `services/`。门禁调用真实 DeepSeek，正好用来证明回复行为没有改变：不退步集合不能有退步。

## 3. 风险与决策

### 风险与防护

| 编号 | 风险 | 防护 |
| --- | --- | --- |
| R1 | 计时代码改变分支或异常走向 | 只用 `try/finally` 包单个 `await`；外层 `finally` 记日志后原样抛出；新增异常路径测试 |
| R2 | 日志内容 | 按用户决定允许记录客人信息。本行只多记会话编号和 msgid；日志不进 GitHub |
| R3 | 现有 12 个使用 `caplog` 的测试受新日志影响 | 跑离线全量测试 |
| R8 | 与边界 Spec 重构冲突：`respond`、`_process_model_reply` 正是它改动最多的函数 | 改动集中、标记点少；写进给 Codex 的衔接说明，合并时在新版本里补回各返回点的结果标记 |
| R4 | compose 未配置日志轮转，容器重建后日志丢失 | 分析前先导出；本次不改 compose |
| R5 | 样本少、模型耗时波动大（历史 9.9～32.3 秒） | 攒不少于 30 条普通回复后看中位数与 P90，再下结论 |
| R6 | `since_sent_ms` 依赖企业微信时间戳，秒级精度且时钟不同 | 标注为近似值，下限取 0；精确时间线仍以数据库为准 |
| R7 | 计时本身开销 | 每阶段两次 `monotonic()`，可忽略 |

### 待用户决定

| 编号 | 事项 | 建议 |
| --- | --- | --- |
| D1 | 版本号 | 1.40.2。只加观测、不影响客人和员工，按「只有小修加末位」的规则定 |
| D2 | 提交、推送、部署生产 | 编码与本地验证完成后，提交推送与部署分别再请你确认 |
| D3 | 汇总日志只在正式回复路径输出 | 是 |
| D4 | 是否记录 msgid 以便与数据库时间线对账 | 记录 msgid 与内部会话编号（用户决定「开放1和3」） |
