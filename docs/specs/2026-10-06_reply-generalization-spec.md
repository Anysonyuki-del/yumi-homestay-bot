# 回复判定泛用化 Spec（v7.1，已确认，待「开始」）

- 日期：2026-10-06 v1；2026-10-07 v2（回应 R1–R5）、v3（回应 G1–G4）、v4（回应 V3-R1、V3-R2、V3-R5）、v5（回应 V4-R1–V4-R3、V4-R5–V4-R7）、v6（回应 V5-R2、V5-R3 与 D14 澄清）、v7（回应 V6-R1–V6-R3 与 C1）、v7.1（回应 v7 复审的失效撤回条文冲突）。作者：Claude。
- 请求依据：用户要求处理 1.68.0 门禁中 47 个已知失败场景，并明确「需要考虑到泛用性，不能客户稍微改几个字就认不出来再出问题」；之后「该方向写spec，我会让codex审核方案」；看完复审报告后要求「改出v3，准备提交给codex审核」。
- 审查记录：`docs/reviews/2026-10-07_codex-to-claude-reply-generalization-spec-review-handoff.md`（R1–R5）、`docs/reviews/2026-10-07_codex-to-claude-reply-generalization-comparison-handoff.md`（G1–G4）、`docs/reviews/2026-10-07_codex-to-claude-reply-generalization-v3-and-emergency-spec-review-handoff.md`（V3-R1–V3-R5）、`docs/reviews/2026-10-07_codex-to-claude-reply-generalization-v4-and-emergency-v2-spec-review-handoff.md`（V4-R1–V4-R7）、`docs/reviews/2026-10-07_codex-to-claude-reply-generalization-v5-and-emergency-v3-spec-review-handoff.md`（V5-R1–V5-R3）、`docs/reviews/2026-10-07_codex-to-claude-reply-generalization-v6-spec-review-handoff.md`（V6-R1–V6-R3、C1）；v7 复审结论由用户于 2026-10-07 转述（上一轮两项 P1 已闭合，余一处失效撤回条文冲突，记为 V7-R1）。逐项回应见 §5。
- 关联：询问豁免整句生效导致的现场危险漏判，拆为独立小修复 `docs/specs/2026-10-07_emergency-exemption-scope-spec.md`（V3-R3、V3-R4、V4-R4、V5-R1 在该文回应），本 Spec 不重复。
- 状态：2026-10-07 用户选择跳过第一、二段逐段讲解，按 v7.1 确认第一段（现状证据）与第二段（方案），并对第三段 D1–D13 作出决定（§3.3，D14 此前已定）。三段均已确认，未改代码，须用户回复「开始」后实施。
- 证据限度：47 个场景数据来自 `.stage/reply-gate-v1.68.0.json`（2026-10-04 全量门禁首轮，历史模型结果）与 `tests/fixtures/guest_reply_regression_baseline.json`。离线探针只用夹具与构造输入调用本地函数，不调用模型或外部服务，也不证明拟议方案的模型质量、延迟或费用；合法选项、编号与原话摘录只缩小生成范围，不能单独证明当前授权或回答适用，本方案不声称消除语义误判。2026-10-07 复核时，相关源码与夹具的 SHA-256 与三份审查报告记录一致。

## 1. 第一段：现状证据（待确认）

### 1.1 根因：多个环节各自用词表决定业务行为

一句客人消息要依次经过下面几个环节，每个环节都用自己的词面规则独立做决定。任何一环没认出来，就会否决其他环节的正确理解；词面误中又会放行错误动作。

| 环节 | 位置 | 词面判定 |
| --- | --- | --- |
| 模型前分流 | `ConversationService.handle_message` / `process_debounced_message`：`EmergencyService.classify` → `ComplaintService.classify` → `_handoff_pattern` → `is_homestay_related` | 危险、客诉、转人工、无关 |
| 工具开放 | `DeepSeekGuestAssistant._allowed_tool_names`：`_should_force_availability`、`_should_force_property_catalog`、`asks_room_price` | 模型调用前决定开放哪些只读工具 |
| 知识证据 | `knowledge_evidence_policy._build_evidence_plan_for_period`：`detect_property_topics`（32 个 `PropertyTopic`）、`asked_attributes`、`_covers_attribute` | 主题、属性、覆盖与冲突 |
| 模型后校验 | `DeepSeekGuestAssistant._validate_decision`：`if not is_service_request(question_text): task_suggestion = None` 等 | 删除模型建议 |
| 任务登记 | `ConversationService._record_task_suggestion`：`requested = is_service_request(...) or is_booking_action_request(...) or _is_facility_issue(...)`；模型没有建议时自动补 `SPECIAL_SERVICE` | 自行决定登记 |

修补历史：`knowledge_evidence_policy` 9 次、`knowledge_service` 10 次、`emergency_service` 8 次、`answer_policy` 13 次改动；另有 9 个提交专为补同义说法。

### 1.2 离线反例（2026-10-07 复核）

**工具开放**（`_allowed_tool_names(question, "")`）：

| 问题 | 开放的工具 |
| --- | --- |
| 明天能住吗 | search_availability |
| 明天有没有地方落脚 | 无 |
| 你们有哪些房型 | list_properties |
| 把可选的住处给我看看 | 无 |

**模型后校验与登记**（`_validate_decision` 输入构造的服务申请决定；`_record_task_suggestion` 输入 `intent="chat"`、无建议的决定，内存替身端口）：

| 问题 | 本地认定服务申请 | 校验后保留建议 | 登记端行为 |
| --- | --- | --- | --- |
| 麻烦送两条毛巾 | 是 | 是 | — |
| 劳驾把浴巾拿两条过来 | 否 | 否（模型建议被删） | 不登记 |
| 请帮我收一下垃圾 | 否 | 否 | — |
| 不用送毛巾了 | 是 | 是 | 登记任务并通知管家 |
| 上次请帮我送两条毛巾 | 是 | 是 | 登记任务并通知管家 |

**危险与客诉**（`EmergencyService.classify` / `ComplaintService.classify`）：

| 输入 | 结果 | 性质 |
| --- | --- | --- |
| 厨房是燃气灶还是电磁炉？ | 确定危险 gas | 误报 |
| 房间提供煤气炉吗 | 确定危险 gas | 误报 |
| Can I smoke on the balcony? | 确定危险 fire | 误报 |
| 房间往外窜白烟了 | 非紧急 | 漏报（词面未命中） |
| 房间的插座冒烟了还有燃气灶吗 | 非紧急 | 漏报（豁免越界，见独立 Spec） |
| 你们不接受宠物吗 | 激烈客诉 | 误报 |
| 第一次来太开心了!!! | 激烈客诉 | 误报 |

**两条危险路径的差异**：`_escalate_emergency` 发 `EmergencyService.safety_reply` 的分类处置并 `_activate_human`；`_answer_possible_danger` 只回「请先避开可能有危险的位置。现在具体是什么情况？」并通知员工，不接管。差异在客人处置内容，不只是会话模式。

**人工模式**：仅 `HUMAN_ACTIVE` 时仍回答独立低风险问题；只有 `_in_native_session` 判断的原生人工会话才完全静默。

### 1.3 47 个场景分类

| 类 | 数 | 场景 | 机制 |
| --- | --- | --- | --- |
| A 证据门误拒 | 14 | E-地铁、K-代收-C、K-借物、K-公区-D、K-吹风机、K-床品-C、K-清洁-C、K-清洁-D、K-网络-C、K-访客、K-距离-C、L-美食、M-儿童早餐、T-吸烟 | 除 T-吸烟（「抽菸」未召回）外，检索已命中对应条目，计划为 `topic_unrecognized`、`partial` 或 `borrowing_unconfirmed` |
| A′ 房间与期间 | 3 | SC-401无上下文、SC-401有上下文、SC-国庆早餐 | 见 1.4 |
| B 模型前分流误判 | 8 | C-宠物、F-堵了（→客诉）；EM-打电话、K-厨房-C、K-吸烟-EN、K-消防-EN（→紧急）；E-冰箱、UN-股票EN（英文无关判定，后者以中文回复英文客人） | 1.2 |
| C 意图、工具与动作 | 12 | AV-102、CA-家庭（未开放工具）；CH-猫、W-推荐（闲聊或推荐被当成本店问题）；K-入住-C、K-公区-C、K-接送-C、K-距离-D、L-火车站（本店问题走自由回答或联网）；SR-垃圾、SR-毛巾、SR-遗失（服务动作，见 1.5） | 1.1 各环节的词面判定 |
| D 门禁判定 | 9 | U-gym-EN、U-保险箱、U-健身房、U-充电桩、U-麻将、K-洗衣-X（路由口径）；CH-谢谢、UN-代码（预期待定）；F-空调（动作判别缺口） | 见 1.6 |
| E 其他 | 1 | K-卫浴-P | 多分句只答了浴缸，丢失热水属性 |

### 1.4 房间与期间

- **SC-国庆早餐**：`reply_regression._Runner._respond` 不读 `expect.context.confirmed_stay`；即使传入房源 202、10-03 至 10-05，常规早餐 9011 与国庆政策 9046 同时合法，计划仍为 `partial`，且没有期间政策优先级规则。
- **SC-401有上下文**：`retrieve_detailed(property_id=401)` 在默认 `limit=8` 时不含 401 专属的 9045；`limit=30` 时排第 13 位。
- **SC-401无上下文**：问题点名 401，但 `property_id=None`，9045 在评分前就被房间范围过滤掉。

### 1.5 服务动作现状

- `is_service_request`：毛巾 True、收垃圾 False、遗失 False；三者 `handoff_reason` 均为 None。
- 登记成功后由 `_notify_employee` 通知，客人收尾按 `GuestActionResult` 生成；是否切人工另由归一化接管理由决定。场景期望 `handoff`，与「登记加通知、不一定接管」的现行设计不一定一致。
- `AssistantDecision.intent: str` 已存在，但只作提示，没有接入工具开放、校验与登记。

### 1.6 门禁判定

- `observed_route` 把 `knowledge_gap` 记为 `unconfirmed`，`judge` 只接受 `knowledge`/`model`；6 个知识缺口场景实际回复正确。
- `judge` 只看路由与片段：构造的 F-空调空正文、SR-毛巾动作失败记录都得到 `ok=True`。
- `_Runner._respond` 的设施分支不经过 `_record_task_suggestion`，总用默认成功收尾。`_Runner` 也不覆盖消息合并、outbox、过时回复或实际收件。

### 1.7 分流顺序与提前返回（V3-R2）

- **启用延迟合并时的生产路径**：
  1. `handle_message`：原生人工静默 → 危险 → 核对资料 → 客诉 → 紧急后续 → `HUMAN_ACTIVE` 高风险 → 转人工或非文本 → 入队等待合并；
  2. `process_debounced_message`：原生静默 → 合并 → 危险 → 客诉 → 高风险 → 转人工 → `is_homestay_related` → `_stage_fast_ack`（不调用模型：有设施信号时不发首响，联网问题发固定文案 `_LIVE_SEARCH_ACK`，其他问题不发首响；然后把最终回复任务入队，载荷含 `fast_ack_sha256` 等。`respond_ack` 只有适配器、协议与测试替身定义，生产链路没有调用）；
  3. `application._deferred_message_from_payload` 把载荷还原为消息 → `process_recorded_message` → `_process_model_reply` → `GuestAssistantPort.respond`。

  未启用合并时，`handle_message` 直接经无关判定进入 `_process_model_reply`。
- **软分流在 assistant 之前提前返回**：「你们不接受宠物吗？」「第一次来太开心了!!!」被判为 agitated 客诉；「Is the fridge stocked with water?」被 `is_homestay_related` 判为无关。规划如果放在 `respond` 里，这些输入根本到不了规划。
- **`respond` 内部也有整轮提前返回**：`_price_question_needs_dates` 为真时直接返回问日期。「房价多少早餐几点」的早餐子问因此被吞掉。

### 1.8 下游接管出口与任务写入口（V4-R1、V4-R3，2026-10-07 复核）

- **接管理由在多处各自重算**：`answer_policy.handoff_reason` 的 `_HIGH_RISK_PATTERNS` 含 `refund`、`complaint`、`agitated`、`early_check_in`，另有议价 `price`；`agitated` 包括「!!!」。下列位置都会重新计算并生效：
  - `handle_message` 与 `process_debounced_message` 的 `HUMAN_ACTIVE` 高风险护栏；
  - `process_recorded_message` 在 `HUMAN_ACTIVE` 下按 `_determine_handoff_reason` 早退；
  - `DeepSeekGuestAssistant._validate_decision` 用 `determine_handoff_reason` 覆盖模型的接管理由；
  - `_process_model_reply_body` 在模型后用 `local_handoff_reason` 决定切人工。

  因此只放行模型前的客诉分流不够：Codex 探针中「第一次来太开心了!!!」在 `_validate_decision` 里仍被改为 `agitated`，最终切人工。
- **设施与预订路径同样绕过当前请求判断**：
  - `has_facility_fault_signal` 对「上次房间空调坏了，现在已经修好了」「房间空调没有故障，不用维修」为真；助手不可用时，`_process_model_reply_body` 的设施兜底仍会经 `_handle_facility_issue` 登记维修任务并发「我已提交管家人工处理」。
  - `is_booking_action_request` 对「不用帮我订房了」「上次请帮我订房」为真；`_record_task_suggestion` 会据此自动补 `SPECIAL_SERVICE` 并登记。
- **活动锁**：`handle_message`、`process_debounced_message` 先 `lock_activity`（`SELECT FOR UPDATE`）再分类；最终回复阶段 `_discard_stale_final` 在模型返回后才加锁复查。若在软分流处直接等待模型，锁会覆盖整个模型等待。
- **无标点多问**：`respond` 的子句拆分用 `[，,。；;！？!?\n]|另外|以及|同时`，「房价多少早餐几点」拆不开。

## 2. 第二段：方案（待确认）

### 2.1 总原则

1. **一次理解，处处复用**：每轮由同一份「轮次计划」给出子问题、意图与必要参数，工具开放、知识检索与选择、模型后校验、任务登记都读同一份计划，不再各自用词表否决。
2. **模型选择或提取，本地核验和执行**：模型能选的、本地必须掌握的，按下表划分。

   | 环节 | 模型可选择或提取 | 本地必须掌握 |
   | --- | --- | --- |
   | 意图 | 当前申请、政策咨询、撤回、历史提及、不明确；一句话可含多项 | 类型白名单；引用原话核验；歧义不默认为申请 |
   | 证据 | 从合法候选里选回答条目和相关条目，或选「无足够依据」 | 候选编号、审核状态、房间与日期范围、冲突检查、期间政策优先级 |
   | 只读查询 | 选择查询种类，提取房间与日期 | 工具白名单、参数校验、调用预算 |
   | 服务动作 | 提出本轮低风险请求 | 类型白名单、可信住宿、幂等、事务、实际登记与通知结果 |
   | 最终回复 | 组织普通表达 | 来源绑定（`ReplyEvidence`）、动作结果（`GuestActionResult`）、发送安全门 |

3. **词表降级**：现有词表保留为确定性下限（必须开放的工具、必须进入的安全路径）、召回辅助与模型失败时的兜底，不再拥有否决正常表达或独立创造服务授权的权力。
4. **安全只升不降**：明确危险的固定处置在任何模型判断之前执行；模型只能补漏和升级，不能取消处置、员工通知或冲突拒绝。原生人工静默、凭证、退款赔偿、真实下单等既有边界不变。
5. **认不出时往安全方向失败**：静态事实回未确认，含糊的服务请求不自动登记，危险按现有规则处理。降级到旧规则只能证明有退路，不能宣称退路同样泛用。
6. **复用现有结构**：复用 `AssistantDecision`、`ReplyPart`、`ReplyEvidence`、`GuestActionResult`，只新增计划所需的最小模型，不建平行决策引擎。

### 2.2 P0 门禁判定修正

- `reply_regression.judge`：`expect.knowledge_gap=true` 时，路由 `unconfirmed` 视为符合；`final` 为空一律失败。
- `reply_regression._Runner._respond`：设施分支传固定占位 `action_reply`，不再生成成功收尾；支持 `context.confirmed_stay`，与线上住宿确认同一入口。A′ 两个场景仍依赖 P2，不在 P0 宣称修复。
- F-空调保留「已提交」禁用；动作正确性改由真实 `ConversationService` 加临时 SQLite 的集成测试证明，两类证据分开报告。
- 预期变更写入基线 `expectation_changes`，注明原因；CH-谢谢、UN-代码按 D2 接受普通模型路径；SR-垃圾、SR-毛巾、SR-遗失按 D7 改为「登记并通知、不转人工」。

### 2.3 P1 轮次计划契约（共同意图）

**数据模型**：在 `deepseek_client.py` 新增最小的 `TurnPlan`、`PlanItem` 与 `PlanOutcome`（状态为 ok 或 failed，附失败原因）。`AssistantDecision` 新增仅本地填写的 `turn_plan`，不接受主模型回传未经核验的计划。

| 字段 | 含义 | 本地核验 |
| --- | --- | --- |
| `kind` | `static_fact`（本店事实，包括服务、设施的政策咨询，如「保洁几点来」「能借雨伞吗」）、`stay_query`（房态、价格）、`catalog_query`（房型推荐）、`booking_request`（本轮要求现在办理的订房）、`service_request`（本轮要求现在执行的服务）、`lost_item_report`、`facility_fault`（当前存在的故障）、`request_withdraw`（撤回某事项）、`history_mention`、`external_info`、`chitchat`、`unrelated`、`unclear` | 枚举白名单；政策咨询统一归 `static_fact`；否定、已修好、过去发生的故障归 `history_mention` |
| `id` | 本计划内的项编号（从 1 开始） | 计划内唯一 |
| `subject` | 事项对象的简短名称（如「毛巾」「空调」「订房」），仅用于展示与员工通知 | 不作为关联依据 |
| `withdraws` | 仅 `request_withdraw` 项使用：被撤回的本计划申请项 `id`；模型只能从本计划已有申请项中选择；本计划内没有位置更早的申请项时为空，表示撤回对象不在本计划 | 非空时必须指向本计划内、原文位置在它之前的申请项；为空时须核验确实没有位置更早的申请项，否则视为无法关联 |
| `question` | 该项的规范化子问题，用于检索 | 长度上限 |
| `quote` | 客人原话中对应该项的连续摘录 | 与 `start` 一起核验 |
| `start` | `quote` 在规划所用冻结正文（规范化后；合并批次以换行连接）中的起始字符位置 | 必须满足 `正文[start:start+len(quote)] == quote`，否则该项无效；项的原文顺序以 `start` 为准，不用 `text.find` 推断 |
| `target_room` | 问题中点名的房号 | 必须是已知房源；只作检索目标，不认定住宿，不解锁凭证或订单 |
| `dates` | 原文日期表达及规范化结果 | 经 `stay_date_range.validate_stay_date_range` 校验 |
| `risk` | `none`、`possible_hazard`、`current_hazard:<类别>`、`complaint` | 只用于危险升级与情绪词复核（§2.6） |

**调用顺序**：

1. 以下保持确定性并先于规划，不等待模型：原生人工静默、确定性危险（`EmergencyService.classify`）、核对资料、紧急后续、转人工或非文本，以及硬接管理由（退款、平台投诉、议价、提前入住）。
2. **软判定**只有两类：仅命中 `agitated` 的客诉或接管理由，以及 `is_homestay_related` 判为无关。命中时同步规划，按 §2.6 复核后再决定；未命中时首响照常，规划在最终回复任务里由 `respond` 在检索前产生。
3. 同一份计划贯穿下游：软判定复核、`respond` 的工具开放、检索与证据选择、`_validate_decision`、接管理由判定（§2.6），以及统一任务写入口（§2.5）。

**同步规划的执行位置与事务边界**（V4-R2、V5-R2）：

- **只在持久作业里规划，不在即时入口就地规划**：客人消息的合并任务（phase `debounce`，由 `_enqueue_debounce` 登记）和最终回复任务（phase `final`，由 `_stage_fast_ack` 登记）都是可重放作业，`SQLAlchemyJobRepository.recover_stale` 会把中断的 RUNNING 任务重新排队。软判定需要同步规划时，都在这两类作业内执行：
  - 启用合并时：软判定在 `process_debounced_message`（debounce 作业）内命中，就在该作业里规划。
  - 未启用合并的即时入口 `handle_message`：软判定命中时，不在请求里规划，而是在记录入站消息的同一事务中登记一个 debounce 作业（复用 `_enqueue_debounce` 与现有作业去重键），然后返回，由后台按合并入口处理。没有作业设施（`self._jobs is None`）时不规划，沿用现行规则，作为已知上限。
  - `process_recorded_message`（final 作业）在 `HUMAN_ACTIVE` 下只命中 agitated 时，在该作业内规划。
- **作业内的锁与事务**：
  - 规划前：固定来源消息或合并批次（msgid 与冻结正文的 `source_sha256`），通过该作业业务会话的提交边界提交并释放活动锁。此时作业仍为 RUNNING，进程中断后可被 `recover_stale` 恢复重放；入站消息的去重只拦重复入站，不拦作业重放。确定性危险的处置在此之前已完成。
  - 规划中：不持有活动锁或数据库事务；新客人消息、员工接管、危险处置可以正常推进。
  - 规划后：重新 `lock_activity`，刷新会话模式、原生人工状态与最新活动。模式已变、出现比来源更新的活动，或来源已并入新批次时，按现有过时纪律（`_discard_stale_final` 同一判据）丢弃本结果。判定仍有效时分两个出口（V6-R2）：
    - **直接处置并结束**（进入客诉、固定婉拒等）：在本次锁内的短事务中完成副作用并提交。
    - **继续主回复**（计划判定不是客诉、需要正常回答）：只保留冻结输入与已核验计划，**先提交释放这次重新取得的锁**，再等待 `respond`。主回复结束或失败后，沿现有 `_discard_stale_final` 再次加锁，重查模式、原生人工与最新活动，然后才执行任务、客诉或出站副作用。第二次释放不能代替这最后一次复核。
  - 重放幂等：客诉复核（`SQLAlchemyComplaintRepository.create_or_get` 按来源去重）、任务（一条来源消息一个任务键）、出站与最终回复任务（作业去重键）沿用现有去重，重放不重复产生副作用。
- 三个入口都调用同一个 `ConversationService._plan_with_released_lock`，不各自实现。
- **提交边界装配**：`application.py` 给 deferred 业务会话注入提交边界（当前 `commit_boundary=session.commit if not deferred else None`），并在两处调用：规划前释放、规划后继续主回复前释放。复用现有事务与活动锁，不新增全局锁或并行事务框架。未经软判定的 final 作业，`respond` 内的规划与主回复等待和现状一样不持活动锁；业务会话在等待期间的事务状态，实施时按目标数据库核对。

**生产、传递与失效**：

- **单一入口**：`DeepSeekGuestAssistant.plan_turn(...) -> PlanOutcome`，`GuestAssistantPort` 增加该方法；会话层与 `respond` 只调用它，不另建决策引擎。
- **传递**：合并阶段产生的计划写入最终回复任务载荷 `turn_plan`，由 `application._deferred_message_from_payload` 还原到消息元数据，`process_recorded_message` 传给 `respond(turn_plan=...)`。
- **复用与失效**：`source_sha256` 与本轮正文一致时复用；不一致或缺失时重新规划。
- **留存**：`quote` 是客人原文子串，按现有作业载荷留存规则处理，不新增其他敏感字段。

**逐项处理与无计划时的多问**（V4-R7）：

- 有计划时，无日期问价只对该项生成澄清分项（`ReplyPart(status="clarification")`），其他项照常回答；某项查询失败时该项为 `query_failed`。
- 无计划或规划失败时，删除 `respond` 中无日期问价的整轮提前返回，改为保留完整原问，继续走现行静态证据与主回复流程；本轮不开放参考价工具，并追加一条房价日期澄清分项。有早餐证据时回答早餐；没有证据时，早餐按现行证据计划回未确认。不靠增加分隔词宣称能拆分无标点多问。

**按能力分别定义失败回退**（规划失败指超时、格式错误、Schema 不符或全部 quote 失效）：

| 能力 | 规划成功 | 规划失败 |
| --- | --- | --- |
| 明确危险 | 确定性处置先行，计划只能升级 | 确定性处置，不受影响 |
| 硬接管理由（退款、平台投诉、议价、提前入住） | 确定性 | 确定性 |
| 软判定（仅情绪词、无关） | 按计划复核（§2.6） | 沿用现行规则，偏保守 |
| 静态事实 | 按计划检索并选择证据 | 现行证据计划，保守回未确认 |
| 只读查询 | 旧规则 ∪ 计划 | 旧规则 |
| 服务、遗失、订房意向登记 | 按 §2.5 统一写入口逐事项判定 | **不新建任务**；词面命中时回固定确认话术（D13），不发成功登记收尾 |
| 设施故障 | 按 §2.5 判定是否为当前故障；即时安全提示与是否建任务分开 | 有故障信号时给安全提示，**不直接建任务**，请客人确认后再登记（D14，用户 2026-10-07 决定） |
| 订房审批、付款、真实建单 | 现有确定性安全门不变；计划不作下单授权 | 同左 |

单项 quote 或 `start` 失效时，只作废该项，不回退到词面授权，其他有效项照常处理。例外：撤回项的 quote 或 `start` 失效时，无法可靠判断顺序，不能再用其坐标判断有没有更早的申请；同轮有任何申请项，则不登记并转确认，失效撤回不被通用过滤删除。位置有效但 `withdraws` 为空或无效时，按 §2.5 的关联规则处理。

**规划失败与主回复失败要分开**（D14 澄清）：

- **规划失败或没有计划**：设施有故障信号时给安全提示，请客人确认后再登记，不发「已提交」（D14）。
- **规划成功、主回复随后失败**（V6-R1，第三批实现）：
  - `AssistantUnavailableError` 增加可选字段 `plan_outcome`，只由适配器本地填写。`respond` 内规划已完成时，后续主回复失败抛出的异常必须带上这份结果；规划未完成时为空，即「无计划」。
  - 会话层捕获异常后取出 `plan_outcome`，核对其 `source_sha256` 与冻结正文一致，然后显式传给 `_handle_facility_issue`（新增关键字参数 `plan_outcome`，与现有的 `advice` 参数分开）和 `resolve_task_request`。摘要不一致时按无计划处理。
  - 计划已判定非当前故障时，不建任务、不回「已提交」，只在有当前故障信号时给安全提示；判定为当前故障时照常登记，成功文案与实际任务、通知结果一致。
  - 不为取得计划重复调用模型，不把计划存在助手实例的共享可变字段里，不从主模型回传字段补造计划。

**V-a、V-b、V-c**：

- **V-a（评估基线）**：按上文，软判定命中时在合并阶段释放锁后同步规划，其余在 `respond` 检索前规划。
- **V-b**：主调用开放全部只读工具，并在 JSON 中回传计划；回传计划经与 V-a 相同的本地核验后写入 `turn_plan`。软判定命中时仍须在合并阶段先规划一次；检索只能按整句。
- **V-c（暂缓）**：生产首响不调用模型（§1.7），把规划引入首响阶段会给所有消息增加首响等待，不能省掉现有调用，暂不纳入比较。
- 先以 V-a 的完整端到端契约满足正确性、危险下限、实际动作与子问题覆盖，再在同一冻结样本上比较 V-b 的调用次数、费用与 P95（D10）。目前没有真实测量，不声称哪种更快或更准。

### 2.4 P2 静态知识与只读查询接入计划

**工具开放**：`_allowed_tool_names(question, previous_context, request_context, plan=None)`，开放集合为「旧规则结果」与「计划中 `stay_query`/`catalog_query` 项映射出的工具」的并集。旧规则命中仍按现状强制调用，计划只能增加开放，不能关闭旧规则要求的强制工具。工具参数仍经现有 `HostexReadOnlyToolExecutor.execute` 与 `stay_date_range` 校验。

**检索**：`DeepSeekGuestAssistant.respond` 对每个 `static_fact` 项分别调用 `KnowledgeService.retrieve_detailed`，再与整句检索结果合并去重；`target_room` 作为该项的 `property_id`。

- 目标房间的 `scope=property` 条目在评分之外单独保留名额（D8），不靠整体放大 limit。
- 员工配置的触发词过滤与审核状态过滤保持不变。
- 合并后的预算按「覆盖全部子问题所需」实测确定。
- 现有语义检索（`SemanticRankerPort`，默认关闭）是否开启，按既有开启门槛单独决定（D12），不在本 Spec 默认开启。

**证据选择（并入主调用）**：主调用为每个 `static_fact` 项回传 `answer_ids`（回答该项的条目）与 `related_ids`（相关但不直接回答的条目），或「无」。不新增单独的选择器调用。

**本地核验**：在 `knowledge_evidence_policy.py` 新增 `verify_selected_evidence`，确定性执行：

1. 全部编号必须属于该项合法候选，出现越界编号时该项回到现行证据计划。
2. 冲突检查的范围是「answer_ids ∪ related_ids ∪ 与之同主题、同属性分组的全部合法候选」，沿用 `_conflicting_fee_claims`、时间与金额集合检查。规则无法分组的新主题，在 answer_ids 与 related_ids 之间互查；查不出冲突也不等于证明没有冲突（残余风险见 §3.1）。
3. 期间特殊政策与常规政策按 D6 的确定性规则决定，不由模型二选一。
4. 保留 `_INSTRUCTION_INJECTION`、长度与拆分规则。

核验通过的条目以审核原文整条发出，不截断、不改写，条件与否定随原文保留。`ReplyPart`/`ReplyEvidence` 只由本地构造；`_validate_decision` 继续清空模型回传的 `reply_parts` 与 `action_result`，信任边界不变。

**结果矩阵**：

| 情况 | 结果 |
| --- | --- |
| 各项均有核验通过的条目 | 发审核原文 |
| 部分项无条目、选「无」或漏选 | 有条目的照答，其余项回未确认（`missing`） |
| 冲突，或期间规则无法决定 | 该项回未确认 |
| 主调用不回传选择或格式错误 | 回到现行证据计划 |

`topic_unrecognized`、`partial` 不再单独拥有拒绝权；`PROPERTY_TOPICS` 与属性正则保留召回、排序、冲突分组和兜底。

### 2.5 P3 统一任务写入口（V4-R3、V4-R5）

- **单一判定**：在 `answer_policy.py` 新增 `resolve_task_request(plan_outcome, text) -> TaskResolution`（字段：`register`、`task_type`、`subjects`、`ask_confirm`、`safety_tip`）。以下实际写入口都先调用它，相同输入必须得到相同结论，加测试约束：
  - `DeepSeekGuestAssistant._validate_decision`（是否保留 `task_suggestion`）；
  - `ConversationService._record_task_suggestion`（服务、遗失、订房意向，含自动补 `SPECIAL_SERVICE`）；
  - `ConversationService._handle_facility_issue` 及 `_process_model_reply_body` 中模型可用与不可用两条设施分支。
- **同一事项按原文顺序取最终状态**：撤回项通过 `withdraws` 指向它撤回的申请项，不靠 `subject` 名称相等关联。项的顺序以核验通过的 `start` 为准，合并批次使用同一份冻结正文坐标。每个申请项的最终状态，由原文位置在它之后、指向它的撤回项决定；再次申请是一个新的申请项。

  | 情况 | 例 | 结论 |
  | --- | --- | --- |
  | 只有申请 | 请送两条毛巾 | 登记 |
  | 申请后同事项撤回 | 请送两条毛巾，算了，毛巾不用送了；「请送毛巾\n不用送毛巾了」 | 该事项不登记 |
  | 撤回在前、本计划内没有更早申请，之后重新申请 | 不用送毛巾了……还是送两条吧 | 撤回的 `withdraws` 为空（对象不在本计划），本轮不创建也不取消旧任务；后面的申请独立判定 → 登记 |
  | 撤回在前、与后面申请为不同事项 | 不用送毛巾了，麻烦送两瓶水 | 同上，撤回对象不在本计划 → 只登记水 |
  | 本计划内有位置更早的申请项，但撤回的 `withdraws` 为空或无效 | 请送毛巾，那个不用了（模型未给出关联） | 无法可靠关联：不登记，回确认话术 |
  | 撤回项 quote 或 `start` 失效 | — | 无法判断顺序：同轮有申请项时不登记，回确认话术（失效撤回不被通用过滤删除） |
  | 重复措辞 | 请送毛巾，不用送毛巾了，还是请送毛巾 | 三项 `start` 分别为 0、5、14；撤回指向第一项，最后一项是新申请 → 登记 |
  | 重复措辞但 `start` 无法唯一核验 | 同上，模型给错 `start` | 相关项无效 → 若有撤回或申请无法确定顺序，转确认，不猜测 |
  | 只有 `history_mention`、`static_fact`、`chitchat` 等 | 上次请帮我送两条毛巾；房间空调没有故障，不用维修 | 不登记，即使词面信号为真 |
  | 只有 `unclear` | — | 不登记，回确认话术 |

- **任务类型**：同一条来源消息仍只登记一个任务（沿用现有任务键），多个有效事项合并描述；类型沿用现有规则（多事项或订房意向为 `SPECIAL_SERVICE`，单个设施故障为维修类）。
- **设施的安全提示与建任务分开**：`safety_tip` 只要有当前故障信号就给（停止使用、不要拆卸），不依赖是否建任务；只有判定为当前故障时才建任务并使用「已提交」收尾，否则正文不出现登记结论。
- **规划失败**：服务、遗失、订房意向不新建任务，词面命中时回确认话术（D13）；设施有故障信号时给安全提示并请客人确认，确认后再登记（D14）。
- **两个独立语义**：「不新建已撤回的请求」由本判定保证；「撤回不取消已有任务」见 D11，本 Spec 不扩展任务取消功能。
- 登记失败、通知失败沿用 `GuestActionResult` 文案；登记后不转人工（D7）；撤回时通知管家（D11）。订房审批、付款与真实建单的安全门不变。

### 2.6 P4 危险、接管与客诉分流

- **确定性下限**（不变）：模型前 `EmergencyService.classify` 命中即按现行路径处置；豁免越界由独立小修复解决。
- **语义补漏**：计划项 `risk=current_hazard:<类别>` 而词面未命中时（如「窜白烟」「报警器在哪里一直在响」），升级为 `_escalate_emergency`（完整分类处置加接管）；`possible_hazard` 走 `_answer_possible_danger`。只升不降。
- **咨询形态误报**（燃气灶、Can I smoke、fire extinguisher）：v2 的条件式处置方案保留为候选，由确定性规则判定咨询形态，不交给模型；混合句、同句事故一律按危险处理。客人仍收到完整分类处置，员工仍被通知，不自动转人工（D3a），并通知员工、标「安全相关咨询」（D3b）。
- **统一接管理由判定**（V4-R1）：在 `answer_policy.py` 新增 `resolve_handoff_reason(text, plan_outcome)`，替换以下各处对 `handoff_reason`、`_determine_handoff_reason` 的独立调用：
  - `handle_message`、`process_debounced_message` 的客诉分流与 `HUMAN_ACTIVE` 高风险护栏；
  - `process_recorded_message` 的 `HUMAN_ACTIVE` 早退；
  - `_validate_decision` 的 `local_handoff_reason`；
  - `_process_model_reply_body` 的模型后切人工判断。

  规则如下：
  - 硬理由（`refund`、`complaint`、`price`、`early_check_in`，以及 `ComplaintService` 的退款与平台投诉）保持确定性，不受计划影响。
  - 只有 `agitated` 可复核：计划成功且 `risk=complaint` 时保留；计划成功且非 complaint 时去除，后续所有出口都不得重新生效；规划失败时保留（现行保守行为）。
  - `HUMAN_ACTIVE` 下只命中 `agitated` 时，按 §2.3 释放锁后同步规划再判定；否则沿用现有早退。
  - 计划成功但判为非客诉时不通知员工（D9）。
- **无关判定**：`is_homestay_related` 判为无关时同步规划：全部项为 `unrelated` 时，按客人语言回固定婉拒；有任何本店或店外信息项时继续正常流程。规划失败时沿用现行规则。

### 2.7 泛用性验收

- **独立编写与冻结**：实现候选冻结后，由未参与实现的一方编写并冻结验收输入（D1）；实现中看到或用于修复的样本转为回归集，不再称盲测。
- **业务等价改写**：同义、错字、语序、礼貌词、中英混合、间接问法、标点变化、消息拆分与合并。来源、应有动作与风险处理应一致，正文不要求逐字相同。
- **意义变化最小对照**：请送/不用送、现在/上次、房间设施/私人物品、政策咨询/申请、假设/现场事故、燃气灶/燃气味。预期动作必须随意义变化。
- **组合与局部失败**：无标点多问、不同房间与日期、独立问题新增、模型漏选、越界编号、规划或主调用超时、证据冲突、登记或通知失败。
- **规划失败与服务**（V3-R1）：规划超时、Schema 错误、全部 quote 失效、撤回项失效但其他项有效时，「不用送毛巾了」「上次请帮我送两条毛巾」均不产生新服务任务或成功登记收尾；同时断言实际动作与客人最终正文。
- **软判定与逐项处理**（V3-R2）：走真实 `ConversationService` 链路（即时、合并、后台三个入口），证明「你们不接受宠物吗？」「第一次来太开心了!!!」「Is the fridge stocked with water?」进入计划复核；「房价多少早餐几点」回答早餐并只对房价追问日期。只直接调用 `plan_turn` 的测试不算。
- **判定一致**（V3-R5）：`resolve_task_request` 的各写入口对相同输入与计划得到相同结论；Schema 允许的每种计划组合都有明确默认，政策咨询与失效项不经空白分支恢复词面授权。
- **接管出口一致**（V4-R1）：BOT 与 HUMAN_ACTIVE 两种模式下，「第一次来太开心了!!!」在计划 `risk=none` 时继续普通回复，`risk=complaint` 时进入客诉，规划失败时按现行规则；退款等硬理由不受影响。断言最终正文、实际路由、会话模式与通知结果。
- **规划与主回复等待都不持锁**（V4-R2、V6-R2）：在隔离 PostgreSQL 合成测试库上走真实 deferred 装配，分别暂停规划阶段与主回复阶段，证明新客人活动与员工接管可以推进；恢复后过时结果不建任务、不登记出站。复用或补充提交后取消、服务重建、作业恢复的用例，核对任务与出站去重。只针对新增事务边界，不扩大为无关数据库测试。
- **主回复失败带回计划**（V6-R1）：在真实 `DeepSeekGuestAssistant.respond` 的离线装配中注入「规划成功、主回复失败」，经过真实会话的设施出口：历史已修好、明确否定均无任务，正文没有「已提交」；当前故障按计划登记。另覆盖规划失败与来源摘要不一致，不得复用错误计划。
- **任务写入口**（V4-R3、V4-R5）：
  - 设施：「上次房间空调坏了，现在已经修好了」「房间空调没有故障，不用维修」在规划成功时不建任务、不出现「已提交」；真实当前故障建任务，并给安全提示。
  - 订房：「不用帮我订房了」「上次请帮我订房」不建任务。
  - 同事项撤回：覆盖同项撤回、不同项撤回、撤回后重新申请、跨消息批次与部分无效项（§2.5 表）。
- **中断续接**（V5-R2）：软判定在即时入口与合并入口命中、提交后暂停规划时取消或重建服务，同一来源仍由持久作业续接；重复拉取、作业重放、恢复后，任务、客诉与出站入队保持幂等，断言最终正文与实际任务、通知结果。显式核对 `application_lifespan` 中 `handle_message` 的 deferred 装配，不能只在提交边界非空的测试替身中验证。
- **摘录位置与撤回**（V5-R3、V6-R3）：先申请后撤回不登记；撤回在前、之后重新申请登记；「不用送毛巾了，麻烦送两瓶水」只登记水；先申请但撤回关联不明转确认；「请送毛巾，不用送毛巾了，还是请送毛巾」撤回第一项后重新申请登记；跨消息合并的相同顺序结论一致；无效位置转确认。断言实际任务写入、通知与最终客人正文，不只断言排序或布尔结论；旧任务不被自动取消。
- **主回复失败**（D14 澄清）：规划成功、主回复失败时，「上次房间空调坏了，现在已经修好了」不建任务、不回「已提交」。
- **无计划多问**（V4-R7）：规划失败且无标点混合问时，有依据的早餐内容保留，房价只澄清日期、不编造；没有早餐证据时明确未确认。
- **分开报告**：候选召回、子问题覆盖、错用事实或条件、服务漏登记与误登记、危险漏检、最终正文、实际动作，并注明样本数量。关键安全错误在验收样本中须为零；有限样本不能证明所有表达都安全。
- **验证分层**：
  - 离线单测：计划核验、编号核验、冲突、期间规则、工具并集、服务判定；
  - 真实 `ConversationService` 加临时 SQLite：任务、通知、会话模式与客人正文；
  - 全量真实模型门禁：模型决策与正文；
  - 生产、真实外部工具与客人收件：各需独立证据和当次授权。

## 3. 第三段：风险、计划与决策（待确认）

### 3.1 风险

- **延迟与费用**：V-a 每轮增加一次规划调用，软判定命中时还让合并阶段等待一次模型；证据选择并入主调用，不再另加调用。调用次数、费用与 P95 须在门禁环境实测后与 V-b 比较（D5、D10）。
- **设施在规划失败时多一轮确认（D14）**：规划失败时，真实设施故障不直接建任务，客人确认后才登记，故障处理会晚一轮；安全提示照常即时给出。须监控规划失败率。规划成功、主回复失败时按计划判定（§2.3）。
- **没有作业设施时不做同步规划**：即时入口在 `self._jobs is None` 时沿用现行软判定规则，作为已知上限。
- **释放锁后等待模型**：等待期间会话可能已被新消息或员工接管改变，结果须经重新加锁复核才可执行；复核失败时本轮软判定结果作废，由新批次处理。
- **规划失败时服务申请需客人确认**：规划失败期间，真实的服务申请会多一轮确认，体验变差但不会误建任务；须监控规划失败率。
- **语义误判**：计划或选择出错时，可能错用条件或收费政策。冲突检查只覆盖规则能分组的范围及模型标记的相关集，新主题的冲突可能漏查。缓解靠反例集和保守回退，不声称全覆盖。
- **引用核验的局限**：只证明摘录来自本轮输入，不证明语义就是当前申请；否定和条件理解仍可能出错，模型置信度不是授权。
- **影响面**：P1、P2、P3 触及主模型提示、问题分类、知识检索与公共回复流程，每个候选都须全量真实模型门禁（执行前取得当次授权）；P1 需改 `application._deferred_message_from_payload` 传递计划，属于装配改动，提交前本地跑一次全量。
- **口径变化**：P0 后历史通过数不能与旧数直接比较。

### 3.2 实施批次与各批安全承诺

每批单独确认文件与符号清单、单独验证、单独发布。紧急豁免修复按用户决定并入第一批；其 Spec 已单独确认。每批只承诺下表所列内容，不能借后续批次的护栏宣称本批已闭环。

| 批次 | 内容 | 本批承诺 | 本批明确不承诺（已知现状保留） |
| --- | --- | --- | --- |
| 1 | P0 门禁判定 + 紧急豁免范围修复（用户 2026-10-07 决定并入本批） | 门禁不再假通过空正文与设施成功收尾；知识缺口路由口径正确；紧急分类豁免只作用于咨询短语自身及前缀直接引出的命中（按已确认的紧急 Spec） | 除紧急分类外不改其他回复行为 |
| 2 | P1 计划契约（在 `respond` 内产生）+ P2 知识与只读查询 + 无日期问价逐项化 | 计划只增开放工具、不关闭强制工具；证据只发核验通过的审核原文；冲突检查覆盖同组候选；规划失败回到现行证据计划；不新增任何任务授权路径 | 服务、设施、订房误登记（§1.8）与情绪词转人工仍按现状 |
| 3 | P3 统一任务写入口 | 规划成功时（包括之后主回复失败），撤回、历史、否定不建任务；同事项按 `withdraws` 与 `start` 取最终状态；规划失败时服务类不新建任务；设施安全提示与建任务分开 | 规划失败时设施改为确认后登记（D14），真实故障多一轮确认；软判定仍按现状 |
| 4 | P4 软判定与接管：释放锁后同步规划、计划经载荷传递、`resolve_handoff_reason` 贯通全部出口、危险语义补漏只升不降 | 情绪词在计划判为非客诉后不在任何出口重新生效；规划等待不持锁；危险处置下限不变 | 咨询形态误报按 D3a、D3b 另定 |

### 3.3 决策（2026-10-07 用户确认）

- D1：改写集与反例集由**独立的 Codex 会话**编写（不看实现），写完冻结后交付验收。
- D2：「好的谢谢」「帮我写爬虫代码」这类闲聊或无关问题，由模型正常作答且内容合格时**算通过**；CH-谢谢、UN-代码的预期在第一批写入基线 `expectation_changes`。
- D3a：命中危险词但按确定规则认出是咨询时，**不自动转人工**；发条件式完整安全处置，再正常回答。
- D3b：上述咨询形态**通知员工**，标「安全相关咨询」。
- D4：改写集通过率目标 **95%**；危险漏判、编造事实、误登记等关键安全错误在验收样本中必须为零。
- D5：规划调用**超时 6 秒、P95 ≤ 3 秒**；超时按规划失败处理，实测超标再调整。
- D6：同主题的特殊时期政策**优先于**平时政策（有效期覆盖目标日期的条目优先于无有效期条目）；跨越边界的入住按日期分段回答。
- D7：低风险服务申请、遗失物报备登记并通知后**不转人工**；SR-垃圾、SR-毛巾、SR-遗失的门禁预期改为「登记并通知」，写入 `expectation_changes`。
- D8：目标房间专属知识在检索中单独保留 **3 条**名额。
- D9：只命中情绪词、计划判定非客诉时**不通知员工**；规划失败时仍按现行规则进入客诉模式。
- D10：**先实现 V-a**，满足正确性与安全要求后，在同一冻结样本上实测比较 V-b 的调用次数、费用与 P95。
- D11：客人撤回服务时**通知管家**（不自动取消已有任务）；意图不明确的请求先请客人确认再登记。
- D12：语义检索**另行评估，不纳入**本方案。
- D13：规划失败而词面命中服务申请时，回「请问需要我们现在为您安排什么？确认后我马上登记」，**不通知管家**。
- D14（此前已定）：规划失败或没有计划时，设施故障给安全提示，请客人确认后再登记；规划成功、主回复失败按计划判定。

## 4. 实施文件与符号清单（确认后细化到每批）

| 批次 | 文件 | 符号 |
| --- | --- | --- |
| 1 | `src/homestay_bot/tools/reply_regression.py`；`tests/fixtures/guest_reply_regression_baseline.json`；`tests/unit/test_reply_regression.py` | `judge`、`observed_route`、`_Runner._respond` |
| 2 | `src/homestay_bot/integrations/deepseek_client.py`；`src/homestay_bot/services/model_budget.py`；`src/homestay_bot/services/knowledge_service.py`；`src/homestay_bot/services/knowledge_evidence_policy.py` | 新增 `TurnPlan`、`PlanItem`、`PlanOutcome`、`plan_turn`；`AssistantDecision.turn_plan`；`respond`（检索前规划、删除无日期问价整轮早退）；`_allowed_tool_names`；`_static_evidence_plan`、`_apply_evidence_plan`；`retrieve_detailed` 目标房间名额；新增 `verify_selected_evidence`；`ModelBudget` 规划预算 |
| 3 | `src/homestay_bot/services/answer_policy.py`；`deepseek_client.py`；`src/homestay_bot/services/conversation_service.py` | 新增 `resolve_task_request`；`PlanItem.id`、`withdraws`、`start` 的核验；`_validate_decision`；`_record_task_suggestion`；`_handle_facility_issue`（新增 `plan_outcome` 关键字参数）；`_process_model_reply_body` 的两条设施分支；`AssistantUnavailableError.plan_outcome` 及 `respond` 内的填写出口、会话层的摘要核对与消费 |
| 4 | `answer_policy.py`；`conversation_service.py`；`deepseek_client.py`；`src/homestay_bot/application.py`；`src/homestay_bot/services/emergency_service.py`（只读引用） | 新增 `resolve_handoff_reason`；`GuestAssistantPort.plan_turn`；新增 `ConversationService._plan_with_released_lock`（两个出口：直接处置、提交释放后继续主回复）；即时入口软判定命中时经 `_enqueue_debounce` 登记作业；`handle_message`、`process_debounced_message`、`process_recorded_message`、`_validate_decision`、`_process_model_reply_body` 的接管判定；`_stage_fast_ack` 载荷写入计划；`_deferred_message_from_payload` 还原；`application_lifespan` 给 deferred 业务会话注入提交边界；计划风险升级接入 `_escalate_emergency`、`_answer_possible_danger`。改装配，提交前本地跑全量 |
| 各批 | `scripts/release/reply_gate.sh` | 新增回复模块须补进 `REPLY_PATHS`（`tests/unit/test_release_scripts.py` 会核对） |

## 5. 对 Codex 审查的逐项回应

| 编号 | 结论 | 处理 |
| --- | --- | --- |
| R1 | 接受 | 删除模型降级；确定性处置下限；咨询形态由规则判定，只调整接管（§2.6） |
| R2 | 接受 | 选择结果只由本地核验后使用；冲突检查覆盖同组全部合法候选；期间规则交 D6（§2.4） |
| R3 | 接受 | 空正文失败、设施收尾占位、动作正确性改由集成测试证明（§2.2） |
| R4 | 接受 | A′ 单列；目标房间只作检索目标，专属条目保留名额（§1.4、§2.4） |
| R5 | 接受 | 服务意图分类与动作表；复用 `intent` 的思路改为本地 `turn_plan`，主模型回传的 `intent` 不作授权（§2.3、§2.5） |
| G1 | 接受（已复核） | 工具开放取「旧规则 ∪ 计划」，计划只增不减；校验与登记共用统一判定（v5 为 `resolve_task_request`，§2.4、§2.5） |
| G2 | 接受（已复核） | 撤回、历史提及由计划否决词面误登记；自动补建议只在判定为登记时执行；不自动取消已有任务（§2.5、D11） |
| G3 | 接受 | 选择并入主调用，冲突范围扩到 answer ∪ related ∪ 同组候选；明确规则覆盖上限与残余风险；召回改按子问题检索并保留房间名额（§2.4、§3.1） |
| G4 | 接受（已复核，并发现更多同类漏判） | 豁免越界拆为独立小修复先行；词面漏检由计划 `risk` 补漏，只升不降；客诉情绪词由计划复核（§2.6） |
| V3-R1 | 接受（已复核） | 回退按能力分别定义；规划失败时不新建服务任务，词面命中只回确认话术；单项失效不回退到词面（§2.3、§2.5、D13） |
| V3-R2 | 接受（已复核分流顺序与问价提前返回） | 写明调用顺序；软分流触发时在合并阶段同步规划，并经任务载荷传给后台入口，复用以 `source_sha256` 为准；无日期问价改为逐项（§1.7、§2.3、§2.6） |
| V3-R3、V3-R4 | 接受 | 在紧急豁免修复 Spec v2 中回应 |
| V3-R5 | 接受 | 政策咨询统一归 `static_fact`，不另设枚举；按计划项逐项给出全部组合的默认结论（§2.3、§2.5） |
| V4-R1 | 接受（已复核 `handoff_reason` 对「!!!」返回 agitated） | 新增 `resolve_handoff_reason` 贯通全部接管出口，只有 agitated 可复核，硬理由不变（§1.8、§2.6） |
| V4-R2 | 接受 | 同步规划前提交并释放锁，返回后重新加锁、按现有过时纪律复核（§2.3）；PostgreSQL 竞争在实施阶段验证 |
| V4-R3 | 接受（已复核设施与订房词面误中） | 统一任务写入口覆盖服务、遗失、订房意向与设施；安全提示与建任务分开；规划失败时的设施兜底列为已知上限并交 D14（§2.5、§3.1） |
| V4-R4 | 接受 | 在紧急豁免修复 Spec v3 中回应 |
| V4-R5 | 接受 | 新增 `subject` 字段，同事项按原文顺序取最终状态；无法关联时确认，不机械忽略失效撤回（§2.5） |
| V4-R6 | 接受（已确认生产无 `respond_ack` 调用） | 更正 §1.7；V-c 暂缓（§2.3） |
| V4-R7 | 接受（已复核拆分结果） | 无计划时保留完整原问，追加房价日期澄清，不再声称能拆分无标点多问（§2.3） |
| V5-R1 | 接受 | 在紧急豁免修复 Spec v4 中回应 |
| V5-R2 | 接受（已复核入站去重会拦截重放，以及 debounce/final 作业可被 `recover_stale` 重放） | 同步规划只在持久作业内执行；即时入口软判定命中时登记 debounce 作业后返回；补主回复等待的事务核对与中断续接验收（§2.3、§2.7、§3.1） |
| V5-R3 | 接受 | `PlanItem` 新增 `id`、`start` 与撤回项 `withdraws`；顺序以核验的 `start` 为准，关联以 `withdraws` 为准；失效撤回优先转确认（§2.3、§2.5） |
| D14 澄清 | 接受 | 区分规划失败与主回复失败；后者按已有计划判定（§2.3、§3.1、§3.3） |
| V6-R1 | 接受（已复核 `AssistantUnavailableError` 无计划字段、`_handle_facility_issue` 第三参数为 `advice`） | 第三批给异常增加适配器本地填写的 `plan_outcome`，会话层核对摘要后传给设施与任务判定（§2.3、§4） |
| V6-R2 | 接受（已复核 deferred 装配 `commit_boundary` 为 None） | 软判定复核分「直接处置」「继续主回复」两个出口，后者先提交释放再等主回复，结束后沿 `_discard_stale_final` 再复核；装配注入提交边界（§2.3、§4） |
| V6-R3 | 接受 | 本计划内无更早申请时允许 `withdraws` 为空，表示撤回对象不在本计划；有更早申请但关联不明、或位置失效时仍转确认（§2.3、§2.5） |
| C1 | 接受 | D14 已由用户于 2026-10-07 决定：规划失败时设施给安全提示、确认后登记；本表与复审请求的待决表述均已作废 |
| V7-R1 | 接受 | 失效撤回的确认条件统一为「同轮有任何申请项即转确认」，不再用失效坐标判断先后（§2.3，与 §2.5 一致） |

## 6. 请 Codex 复审的重点

1. §2.3 `AssistantUnavailableError.plan_outcome` 的生产与消费契约是否闭合，第三批是否不再依赖第四批的能力。
2. §2.3 软判定复核的两个出口与两处提交释放，能否兑现「规划和主回复等待都不持锁、副作用前必复核」。
3. §2.3、§2.5 撤回规则在「对象不在本计划」「关联不明」「位置失效」三种情况下的结论是否唯一。
