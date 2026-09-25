# 安全类回复送达与两处回归修复 Spec（1.39.16）

版本：R1，2026-09-25。状态：**用户回复「直接做完」；实施中。**

依据：`docs/reviews/2026-09-25_reply-rules-cross-review.md` 第 4.1 至 4.4 节，以及第 7 节确认的 Q2、Q3、Q19、Q21。本版在独立 worktree 中基于 main（1.39.15，`1300b13`）实施，不触碰边界 Spec 的在途工作区。

## 1. 要修的问题（已在代码中核实）

| 编号 | 现象 | 根因（文件与符号） |
| --- | --- | --- |
| 4.1 | 客人连发「燃气味好重」「我们现在该怎么办」，第一条的撤离提示被跳过 | `application.py::_guest_reply_is_stale` 对所有带来源消息的客人回复一视同仁；紧急提示也经 `TransactionalOutboxWeCom` 入队，排队期间只要来了新消息就整条跳过 |
| 4.2 | 客人已收到「会立即联系管家」，模型不可用时员工收不到任何通知 | `conversation_service.py::_enter_complaint_mode` 只登记 `complaint_review_generate`；`complaint_review_job.py::handle` 在 `analyzer.generate` 成功后才发员工卡片 |
| 4.3a | 「明晚301能住吗」「今晚4个人住，有合适的房吗」「那301今晚还有吗」「明天天气怎么样？还有空房吗」被送去联网，答成天气 | `tourism.py::classify_tourism_query` 的预订优先规则 `_BOOKING_PATTERN` 不认「能住」「空房」「几个人住」「房号 + 还有吗」；天气词先命中老的时效词表，1.39.13 新加的时间词规则又放大了这个缺口 |
| 4.3b | 审核知识的正文被删成孤句或删空。投递改写路径上，47 条审核答案有 35 条被改动、11 条被删空；「附近有便利店吗」只剩「它属于周边商户……」 | `guest_reply_policy.py::remove_ungrounded_property_claims` 不知道一句话有没有出处：1.39.15 起它把「说到民宿、说不出来源」的句子都删掉，调用方却从不告诉它本轮有哪些依据 |
| 4.4 | 1.39.11 的「分段发送中新接管即停发」和「失败通知显示会话编号」在线上不生效 | `TransactionalOutboxWeCom` 只在构造时接收 `conversation_id`，唯一的生产装配点（`application.py` 中 `ConversationService(wecom=TransactionalOutboxWeCom(...))`）没传；集成测试是自己构造时传了，所以一直是绿的 |

## 2. 功能点

### F1 安全与承诺类回复豁免出站过时判定（4.1，Q2）

- **范围**：会话层以 `high_risk=True` 发送的客人回复，恰好是 Q2 的三类：
  - 紧急安全提示：`_escalate_emergency`；
  - 客诉首响：`_enter_complaint_mode`；
  - 转人工确认：`_escalate_regular`，以及人工接待期间收到新高风险事项时的「我已收到您的诉求」。
- **做法**：
  - `WeComMessagingPort.send_text` 与 `ChainedGuestSenderPort.send_text_chain` 增加关键字参数 `stale_exempt: bool = False`。
  - `_send_guest_reply` 把 `high_risk` 传给它。
  - `TransactionalOutboxWeCom` 在载荷里写 `stale_exempt: true`，`_guest_reply_is_stale` 遇到这个标记直接返回「不过时」。
  - 直接发送的发送器没有排队，忽略这个参数。
- **不用 `message_type` 做标记的原因**：只有 `text` 类型的机器人消息会进入模型上下文和对话历史（`repositories/context.py`、`conversations.py` 中的 `Message.message_type == "text"`）。改类型会让模型看不到自己发过的安全提示。
- **不豁免**：
  - 普通问答和快速安抚：新消息来了会重新生成答案，旧答案不该再发。
  - 联网失败和模型失败时的道歉：会话已转人工、员工已收到通知，新消息会另行处理。
- **与边界 Spec 的关系**：边界 Spec 的 D01 写的是「过时保护保持」，需要补一句「安全与承诺类回复除外」，由你转达。

### F2 进入客诉时立即通知员工（4.2，Q3）

- 发出客诉首响后，立刻调用现有的 `_notify_employee`，原因写成「客诉待处理：退款或赔偿诉求 / 平台投诉或差评 / 客人情绪激动（风险：高 / 严重），分析卡片生成后另发」。这一步不依赖模型。
- 分析卡片的逻辑不变：分析成功后照常补发。
- 通知字段沿用现有格式：客服账号、客人称呼或备注、客人原话。Q18 要求的固定字段属于第 ② 块，写进边界 Spec 的 P9 以后再做。

### F3 预订优先规则补住宿意图（4.3a）

- `_BOOKING_PATTERN` 补上以下说法：
  - 空房、余房、剩房、满房、订满、可订；
  - 能住、还能住、可以住、住得下；
  - 「有合适的房」这类「有……的房」问法（实施时修订：原计划收「数字 + 人住」「住 + 数字 + 晚」，最终回归发现「我们三个人住201，能再加一张床不」因此被判成房态交易、加床知识被剔除，单说人数不算住宿意图）；
  - 三位房号后几个字内出现「还有」「能住」「空着」的问法，其中「还有票」除外。
- 命中这些说法后仍沿用现有逻辑：提到门票、演出等旅游对象时才走联网，其余一律交给百居易实时查询。
- **已知取舍**：「明天天气怎么样？还有空房吗？」这一轮只回答房态，天气不答。这是现有「预订查询优先」原则的延续。房态答错的代价大于天气没答。一句话拆成多个意图分别作答，属于边界 Spec 的回复计划（`reply_plan`）范围。
- **不在本版**：「民宿离地铁站多远」「公共客厅几点开放」等本店问题被老词表送去联网，是 1.39.13 之前就有的行为，交给边界 Spec 的位置与知识矩阵处理。

- **实施时扩展（2026-09-25，部署前真实模型回归发现）**：只补分流词表后，这几句不再联网，但模型仍调不到房态工具，只能追问人数或说查不到。原因是「在问房态」有 5 份各自维护的词表：
  - 联网分流 `_BOOKING_PATTERN`；
  - 交易判定 `_TRANSACTION_PATTERN`；
  - 工具开放 `_allowed_tool_names`、`_should_force_availability`、`_is_standalone_availability_query`。

  改为 `answer_policy.asks_stay_availability` 一处定义，各处共用。另外补了三点：
  - 明确日期补上「明晚」和英文 today、tonight、tomorrow；
  - 住宿意图补上英文 available rooms；
  - 「那301今晚还有吗」这类本句自带日期的追问，也开放房态工具。

### F4 过滤按依据判定（4.3b）

**规则**：本店事实句能在本轮依据里找到出处就保留；找不到的照删，行为与 1.39.15 相同。

**新增** `fact_policy.is_supported_by(sentence, source)`，同时满足两条才算有出处：

- 句子的中英文字符二元组，至少 60% 出现在依据文本里；
- 句子里的每个数字都出现在依据文本里。

`remove_ungrounded_property_claims(content, *, grounded_in="")`：句子命中「未经审核的民宿断言」且在 `grounded_in` 里找不到出处时才删。不传依据时，行为与现在完全一致。

各调用方传入的依据：

| 调用方 | 依据 |
| --- | --- |
| 主回复校验 `_validate_decision`，以及精炼后的再次过滤（`deepseek_client.py`） | 本轮实际交给模型的审核知识答案 |
| 投递改写 `deepseek_delivery_rewriter.py` | 被拦截的原文。原文在生成阶段已经过事实闸门，改写只能保留原文已有的事实，新增的本店事实仍然删除；数字、日期、否定、实体由现有的 `_validate_facts` 继续核对 |
| 改写失败后的本地兜底 `delivery_rewrite_job.py::_deterministic_fact_fallback` | 与生成阶段同一口径：本店专属问题的原文来自证据门，不再过滤；天气等非专属问题照旧过滤。（实施时修订：原计划整体删除这次过滤，但现有用例说明兜底也要为天气类内容把关，改为按问题类型区分） |
| 联网搜索回复、入住提醒的天气摘要 | 没有审核知识，不变 |

```
ponytail: 二元组覆盖率只看字面相近，看不出语义。
影响：
- 「有 24 小时便利店」和「没有 24 小时便利店」字面几乎一样。
- 改写路径有 _validate_facts 的否定核对兜底，生成路径没有。
- 本轮交给模型的审核知识条数多时，依据变长，判定会变宽。
升级条件：真实模型回归（1.39.17 的门禁）出现依据内的语义篡改时，换成逐句的蕴含判定。
```

### F5 生产出站载荷补会话编号（4.4）

- `TransactionalOutboxWeCom._enqueue_guest_text` 在构造时没拿到 `conversation_id` 时，用现有的 `SQLAlchemyMessageRepository.find_conversation_id(open_kfid, external_userid)` 查一次。
- 这是所有客人出站的汇合点，一处修改覆盖全部构造点。
- 查到后，分段回复会记下当时的接管边界 `handoff_id`，失败通知也能写出会话编号。

## 3. 红测计划

每条用例先在 main 上确认失败，再实施。

| 编号 | 位置 | 断言 |
| --- | --- | --- |
| T1 | `tests/integration/test_reply_chain_sending.py`，真实 worker 循环加 SQLite | 紧急提示入队后又来一条客人消息，提示仍然发出。对照组：同样情形下的普通回复仍被跳过 |
| T2 | `tests/unit/test_conversation_service.py` | 进入客诉时员工立即收到一条「客诉待处理」通知，分析器没有被调用 |
| T3 | `tests/unit/test_tourism.py` | 第 1 节的 4 句判为 `none`。对照组仍为 `live`：「今晚江滩有灯光秀吗」「明天下雨吗」「黄鹤楼今天几点关门」「帮我预订黄鹤楼门票」 |
| T4 | `tests/unit/test_fact_policy.py`、`test_guest_reply_policy.py` | ① 不变量：6 条虚构审核答案（便利店、消防、Wi-Fi、停车、禁烟、大功率电器）以自身为依据时原样保留；② 换了说法的保留；③ 改了数字的删除；④ 依据里没有的本店事实删除；⑤ 不传依据时现有测试全部不变 |
| T5 | `tests/unit/test_deepseek_client.py` | 「附近有便利店吗」：审核知识作依据时，便利店那句保留 |
| T6 | `tests/unit/test_deepseek_delivery_rewriter.py`、`test_delivery_rewrite_job.py` | 审核停车答案被拦截后，改写版本没有被删空；改写里新增的本店设施句仍被删除 |
| T7 | `tests/integration/test_reply_chain_sending.py` | 按生产方式构造出站（不传 `conversation_id`）：载荷带会话编号和 `handoff_id`；分段发送中新接管后，剩余段停发 |

## 4. 验证

- **本地**：
  - 全量单元与集成测试；
  - 用 47 条虚构审核答案对比改动前后「被改动」「被删空」的条数。
- **部署前**：在生产容器的临时目录，用候选代码重跑相关的真实模型场景，包括紧急连发、第 1 节的 4 句房态、便利店，以及 47 条审核知识类问题。条件同上次：只读配置，不写库、不发消息。这一步会调用真实 DeepSeek，届时单独请你授权。
- **发布**：
  - `CHANGELOG.md`、`docs/releases/1.39.16.md` 写清改动内容和依据；
  - 交叉评审报告随本版一起提交；
  - 部署检查通过后，用测试号确认实际收件。
- 本版没有数据库迁移。

## 5. 不在本版

- Q4、Q13（交还机器人）、Q5（危险分级）、Q6、Q14（语言规则）、Q8、Q15（身份信息）、Q18（通知字段）：第 ② 块，写进边界 Spec 的 P9 以后。
- Q16、Q17、Q20（部署门禁与共用虚构资料）：第 ③ 块，发 1.39.17。
- 「20 条审核答案被答成尚未确认」：走的是证据门（`_has_unsupported_property_claims`、`_unconfirmed_reply`），机制不同。部署前的回归里会看 F4 之后还剩多少条，剩下的交给边界 Spec 的知识范围（`knowledge_scope`）处理。

## 6. 风险

- **F1**：客人连发两条都命中紧急词的消息时，会收到两次安全提示。安全优先，接受。
- **F1**：发送端口的签名变了，测试里的发送替身要同步加参数。
- **F3**：新增的「能住」「可以住」可能把「这里能住几个人」这类容量问题也归为 `none`。本来就应该由本店知识回答，没有损失。
- **F4**：阈值来自原型实测：改写版覆盖率在 0.65 至 0.87 之间，编造句约 0.27。阈值由 T4 固定下来，偏差由 1.39.17 的真实回归门禁兜底。
- **F5**：每次客人出站多一次按唯一键查会话，开销可以忽略。
- **合并**：本版会改到 `application.py`、`conversation_service.py`、`deepseek_client.py`、`deepseek_delivery_rewriter.py`、`delivery_rewrite_job.py`、`guest_reply_policy.py`、`fact_policy.py`、`tourism.py`。边界 Spec 的在途工作区也改了其中几个文件，合并 main 时由实施方处理冲突。
