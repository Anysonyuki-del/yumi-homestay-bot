# Codex → Claude：回复泛用化第二至四批实施审查与修复建议

- 日期：2026-10-07。审查方：Codex。
- 用户请求：「审查交接报告」，随后「写审查报告，写清楚依据，建议修复方案等等」。
- 审查对象：[Claude 第二至四批实施交接报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:1)、[Spec v7.1](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:1) 与对应 v1.69.0 实现。
- 工作区：`/Volumes/02/code/homestay-bot`，`main`，审查及整理报告时 HEAD 均为 `2e2f33b0bc257679e2eb5c0e83a8d161693114e5`；正式标签对应实现提交 `fcfb83886457fda15cdd423da68062234084b8d3`。
- 结论：**本轮实施审查不通过。确认 3 项 P1、3 项 P2；先修复共同安全与证据边界，再复审。**
- 边界：本报告是审查证据和待确认修复建议，不是修复完成证明，也不构成编码、提交、推送、部署、真实模型调用或生产写入授权。v1.69.0 已部署是原交接报告的历史记录，本轮没有独立核验当前生产。

## 1. 结论与依据范围

“模型做选择题”的方向可以保留：模型选择有限意图、证据编号和关联对象，本地核验输入来源、适用范围、风险下限及实际动作。当前缺陷集中在选择结果进入本地流程后的约束，而不是简单的同义词不足。

| 编号 | 优先级 | 已确认缺陷 | 证据 |
| --- | --- | --- | --- |
| B24-R1 | P1 | 主回复失败时不消费成功计划中的危险，退回普通失败话术 | E1：同一火情计划，成功与失败出口的安全正文不同 |
| B24-R2 | P1 | 先命中的情绪词遮住后续文本变体里的退款硬理由 | E2：跨行退款被平静计划清除，合并入口没有进入客诉 |
| B24-R3 | P1 | 整轮候选池被当成每个子问题的合法候选，允许房间证据串用 | E3：401/402 交换知识编号后仍被核验为 grounded |
| B24-R4 | P2 | 静态证据分项覆盖整轮回复，删除已经回答的其他问题 | E4：吹风机加景点推荐，最终客人正文只剩吹风机 |
| B24-R5 | P2 | 非法 risk 类型抛出 TypeError，没有返回 failed 计划 | E5：risk 为数组或对象时越过约定失败回退 |
| B24-R6 | P2 | X14 将 related 排除出冲突检查，漏掉新主题的同属性真冲突 | E6：同一健身房开放时间 8:00/10:00，改变标签即可放行 |

P1 表示安全处置、经营风险路由或本店事实适用边界被破坏；P2 表示多问覆盖或失败契约等重要行为有缺陷。优先级依据实际反例，不依据测试总数。

本轮以 `8b15ad8..HEAD` 定位第二至四批实现，追踪适配器、会话入口、知识服务、证据核验与最终正文出口。该范围共 31 个文件、3187 行增加、255 行删除，包含发布及 AOCI 资产；这些数字只是归属范围，不作为质量指标。报告整理前核对 HEAD 和第 7 节源码摘要未变，因此复用已完成的审查结果，没有为写文档重复运行业务测试。

### 证据分类与限制

| 证据 | 已证明的事实 | 实际限制 |
| --- | --- | --- |
| 当前源码、Git、摘要 | 实现分支、调用关系、报告和 Spec 的对应关系 | 不证明当前部署副本或运行态 |
| E1、E2、E4 会话探针 | 真实 ConversationService 流程中的路由、模式、通知和模拟客人正文 | 助手/平台端口为离线替身，不证明真实收件；E4 使用真实 DeepSeekGuestAssistant、模拟模型响应 |
| E3 适配器探针 | 真实 KnowledgeService 过滤、真实适配器选证据和最终 ReplyPart 错配 | 合成知识仓储、模拟模型；没有进行真实数据库写入或平台发送 |
| E5、E6 边界探针 | 真实规划入口异常、真实证据核验函数的放行结果 | 不测供应商实际输出频率或生产事故发生率 |
| 本轮相关回归 | 62 项既有用例通过 | 没有覆盖 E1–E6 的全部反例 |
| 本地门禁产物 | 保存的门禁结果和候选源码身份 | 本轮没有重新调用真实模型，也没有核验实际投递 |

E1–E6 是本会话离线探针观察，没有另存独立原始输出文件。以下分别给出输入、端口安排、生产符号和观测结果，供接手方构造最小回归；不能把它们描述为真实生产客人事故。

## 2. 六项发现、根因与最小修复

### B24-R1 / P1：主回复失败必须保留已核验的危险处置

**业务与报告依据：** Spec [§2.6](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:287) 要求计划中的当前危险升级为完整分类处置和接管，可能危险走提醒，只升不降。交接报告 [§2.4 危险补漏](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:112) 声称已经接入；其 [§2.1](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:46) 说明主回复失败会带回计划。

**源码依据与路径：** [ConversationService._process_model_reply_body](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1551) 捕获 `AssistantUnavailableError`，用 `usable_plan` 核验异常计划，但只调用任务判定、词面设施分支或 `_escalate_assistant_failure`。计划风险读取和 `_escalate_emergency` 升级位于 [成功返回之后](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1605)。因此“异常带回字段”已经实现，却没有闭合危险消费。

**E1：** 使用 `tests.plan_helpers.plan_for` 经生产 `verify_turn_plan` 构造如下计划，分别给 `tests.unit.test_conversation_service.AssistantStub` 与 `FailingAssistantStub`，通过 `tests.unit.test_conversation_plan_flows._guest_turn` 进入真实会话服务。

- 正文：`插座那边窜白烟`。
- 有效项：`kind=facility_fault`、`quote=完整正文`、`risk=current_hazard:fire`；来源摘要匹配。
- `EmergencyService.classify` 未识别明确或可能危险，词面设施故障信号也未命中。

| 主回复结果 | 模拟客人最终正文 | 员工通知类型 |
| --- | --- | --- |
| 成功 | 含“请立即离开房间并前往安全区域”与“119” | 紧急事件：火情烟雾 |
| 抛 AssistantUnavailableError，带相同成功计划 | “抱歉，刚才查询没有顺利完成。我会立即联系管家来处理，请您稍等。” | 模型服务暂时不可用 |

两条路径都转为 HUMAN_ACTIVE 且没有建普通设施任务，但失败路径缺少危险处置，不能因转人工就视为安全等价。

**最小修复建议：** 在会话层复用一处已核验计划风险处置，使正常返回与主回复失败都先消费相同风险；可以在已持有可信计划时提前处置，或在两个结果出口调用同一逻辑。保留来源摘要核验、确定性危险下限与现有过时复核，不重复调用规划器，也不把危险退成普通设施建议。

**验收要求：** 同一当前危险计划分别搭配主回复成功、失败，断言最终安全正文、实际紧急路由、接管模式和员工通知类型；补可能危险，以及失配摘要/失败计划的对照。仅断言异常含 `plan_outcome` 不足以验收。

### B24-R2 / P1：硬接管理由须先于情绪复核，跨文本变体保持一致

**业务与报告依据：** Spec [§2.6 接管规则](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:292) 与交接报告 [§2.4](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:97) 要求退款、平台投诉、议价不受计划影响，只有 agitated 可复核。工程手册“连续消息静默合并”要求合并后复核拆分的风险信号。

**源码依据与路径：** [answer_policy.resolve_handoff_reason](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:357) 使用 `next` 取 `policy_variants` 中第一个非空理由，再允许计划清除 agitated。与此同时，[ConversationService.process_debounced_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:975) 在第一个 `is_complaint` 分类处停止；若只是 agitated，规划复核后也不会再检查剩余变体里的硬风险。

**E2：** 使用 `tests.unit.test_conversation_plan_flows._deferred`，助手返回 `plan_for(text, ("chitchat", text))`，运行真实 `process_debounced_message`。

- 正文：`第一次来太开心了!!! 退\n款`，其中 `\n` 表示实际换行。
- 原文与空白折叠版本先识别 agitated；去掉空白的版本含连续“退款”，能识别 refund。
- `resolve_handoff_reason(text, calm_plan)` 却返回 None。
- 实际合并入口保持 BOT_ACTIVE、登记 `final:msg-1` 作业，员工通知为 0，没有进入退款客诉路径。

原有 first-match 结构在情绪词始终转人工时仍会保守接管；本次允许清除情绪词后，该结构变成硬理由绕过。因此不能仅认为它是既有代码、与本次无关。

**最小修复建议：** 对全部文本变体完成硬理由检查，按已有业务优先级收敛结果；只有不存在硬理由时才允许 agitated 复核。客诉分类入口应复用相同优先级，保留 `ComplaintService.classify` 的风险等级计算，不能只修统一 helper 而留下合并入口的 first-match。

**验收要求：** 开心感叹加跨行退款，在 BOT_ACTIVE 与 HUMAN_ACTIVE 均保持退款路由，不受平静计划影响；同时保留纯开心感叹继续普通回复的正例。断言实际路由、会话模式、通知以及最终正文，不只测 helper 返回值。

### B24-R3 / P1：每个子问题必须拥有自己的合法证据集合

**业务与报告依据：** Spec [§2.4 本地核验](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:240) 明确要求“全部编号必须属于该项合法候选”。交接报告 [§2.2](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:57) 将其改述为“本轮合法候选”，这个差别影响房间、日期和触发条件的信任边界。

**源码依据与路径：** [DeepSeekGuestAssistant._merge_item_knowledge](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1412) 按每项房间检索，然后将所有结果汇成 `merged`。[_selection_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1342) 对每项传入同一个 `knowledge`；[verify_selected_evidence](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:924) 只检查编号是否在这个池中，没有重新限定该项房源。正确的检索过滤在合并后失去约束。

**E3：** 合成仓储提供两条 `scope=property` 知识，真实 `KnowledgeService` 负责检索过滤，房号查询分别返回 401、402。真实适配器使用模拟规划与主回复，调用 `respond`。

| 知识编号 | 房源 | 问题 | 审核答案 |
| --- | --- | --- | --- |
| 401 | 401 | 房间吹风机在哪里 | 401房间的吹风机在抽屉。 |
| 402 | 402 | 房间吹风机在哪里 | 402房间的吹风机在衣柜。 |

- 正文：`401房间吹风机在哪里，402房间吹风机在哪里`。
- 计划：两个有效 static_fact 项分别点名 401、402。
- 主回复选择：项 1 `answer_ids=[402]`；项 2 `answer_ids=[401]`；related 均为空。
- 主调用候选确实含 `(source_id=401, property_id=401)` 和 `(402, 402)`；检索本身没有错误。
- 最终决定中，401 子问题的 grounded 分项绑定 402 房源及衣柜答案，402 子问题绑定 401 房源及抽屉答案，两项均通过核验。

这是“用另一间房已审核事实回答本房间”，不是无来源编造；仅检查有证据不能拦住它。此探针证明适配器最终分项错配，未进行真实客人投递。

**最小修复建议：** 在本次分项检索时保留 item_id → 合法候选集合与本地解析范围；主调用可见信封仍可合并，但每项的选择、同组冲突和现行计划回退都必须使用该项集合。复用现有知识服务的房源、有效期、审核及触发条件过滤，不另建并行检索器。房号无法解析时，应避免将明确指向未知房间的问题当成当前确认房间事实；该相关边界需补对照，当前 E3 只直接证明已知两房串用。

**验收要求：** 对调两房证据编号必须拒绝或按该项正确候选回退；正确编号正常回答。补显式未知房号与已确认住宿房间并存、不同日期及触发条件的最小对照。最终分项的 question、property_id、source_id 和客人正文必须对应一致。

### B24-R4 / P2：证据接管必须限定到静态子问题

**业务与报告依据：** Spec [§2.7 组合与局部失败](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:310) 要求独立问题新增、多问与局部失败仍保持分项覆盖。交接报告 [§6](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:160) 已披露服务申请被静态计划覆盖；E4 证明覆盖问题还会影响已经正确回答的店外独立问题。

**源码依据与路径：** [_selection_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1337) 只构造 static_fact 分项；[_validate_decision](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1251) 采用该计划；[_apply_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1753) 将整份 `reply_text`、`reply_parts` 替换为这些静态分项。既有工具或联网结果可在其他步骤追加，但普通非联网剩余回答没有因此得到保留。

**E4：** 用 `tests.unit.test_deepseek_turn_plan._assistant`、`_Knowledge`、`HAIRDRYER` 构造真实适配器，以模拟模型响应配合真实 `ConversationService` 的 `_guest_turn`。

- 正文：`房间有吹风机吗，武汉有哪些经典景点推荐`。
- 规划：第一项 static_fact，第二项 external_info，摘录均经生产核验。
- 主回复原文：`每间房都有吹风机。武汉经典景点可以逛黄鹤楼、东湖和省博物馆。`
- 证据选择：第一项选 9040，审核答案为 `每间房卫生间都配有吹风机，放在洗手台下方抽屉里。`。
- 模拟客人最终正文只有这条审核答案，景点推荐消失。

模型已经回答了第二问；删除发生在本地证据组合阶段，因此不能仅追加“不要漏答”的提示词。

**最小修复建议：** 用现有 ReplyPart 逐项组合，静态计划只替换对应项。让主回复明确提供剩余非静态项的回答，或在主调用输入中明确其只负责剩余项，再与本地审核知识组合。不得把整段未限定的模型原文直接追加回来，以免重复回答或恢复未经审核的本店事实。该修复应一并核对已披露的 SR-毛巾、SR-遗失覆盖路径。

**验收要求：** 静态知识加稳定店外问题，最终正文同时包含审核答案和独立回答；再以实际受影响路径选择服务申请、无日期问价或实时工具的最小组合对照。遗漏、冲突或失败只影响对应分项；任务状态仍由实际登记结果决定。

### B24-R5 / P2：输入类型错误必须转成规划失败

**业务与报告依据：** 交接报告 [§2.1](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:36) 说明风险不合规整份计划失败，规划入口“所有异常与超时都返回 failed”。`plan_turn` 自身注释也包含格式/Schema 错误不抛异常的承诺。

**源码依据与路径：** [turn_plan._verify_risk](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:129) 在字符串类型判断前执行集合成员比较；数组和对象不可哈希。`verify_turn_plan` 消费它时没有将该类型错误转换为失败；[DeepSeekGuestAssistant.plan_turn](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:857) 的 try/except 只覆盖调用和取响应，`else` 中的结果核验不在异常处理范围。

**E5：** 使用 `_assistant` 的模拟 completion，让一次 `plan_turn(text="请帮我送水", language=ZH)` 收到以下 JSON。该输入没有外部调用。

```json
{"items":[{"id":1,"kind":"service_request","quote":"请帮我送水","start":0,"risk":[]}]}
```

实际抛出 `TypeError: unhashable type: 'list'`；将 risk 换成对象也会触发不可哈希异常，没有返回 PlanOutcome(status=failed)。本反例直接证明规划失败契约失效；没有测生产 worker 的后续终态，不宣称已经造成真实消息丢失。

**最小修复建议：** `_verify_risk` 先处理既有默认值，再严格验证字符串类型，再做白名单匹配；非法类型返回不合规结果，由 `verify_turn_plan` 产生 failed。核对同一 JSON 边界中其他枚举字段的类型处理，避免相同成员判断遗漏。若规划入口需要捕获结果格式异常，只捕获预期验证错误，保留异常类别；不以无条件吞掉程序缺陷代替输入校验。

**验收要求：** 数组、对象、数值、布尔及未知字符串风险均返回 failed，不抛出；合法风险保持原语义。至少一个服务申请经真实会话链路证明失败后采用约定确认回退，没有任务或“已提交”正文。

### B24-R6 / P2：X14 需要保留跨主题保护，同时恢复新主题真冲突检查

**业务与报告依据：** Spec [§2.4](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:243) 要求不能分组的新主题在 answer 与 related 间互查。交接报告 [候选门禁修正第 2 项](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md:148) 已明确披露 X14 削弱这一规则，并请求审查。

**源码依据与路径：** [verify_selected_evidence](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:943) 只将回答条目与词表识别的同主题候选加入 group，related 仅参与编号/有效期核验；识别不出主题时 same_group 为空。模型的 related 标签因此实际获得排除冲突证据的权限。

**E6：** 直接调用真实 `verify_selected_evidence`，两条 global 知识的标准问题均为 `健身房几点开放？`，答案分别为 `健身房每天8:00开放。` 与 `健身房每天10:00开放。`。`detect_property_topics` 对该问法返回空集合。

| 选择 | 实际结果 |
| --- | --- |
| answer=[1]、related=[2] | grounded，发出 8:00 的答案 |
| answer=[1,2]、related=[] | missing，reason=conflict_time |

事实、问题和适用范围相同，仅改变模型标签就改变安全判定。这是所问钟点的真冲突，不属于“其他属性暂不识别”的已知天花板。

**对 Claude 方案的判断：** “只检查所问属性”和“避免把其他主题顺带提及的钟点拉入冲突”有实证支持，应保留；`test_related_entries_do_not_create_time_conflicts` 的安静时段/客厅开放时间对照也应保留。不能据此将所有 related 排除，因为模型标签本身未经语义证明。**X14 不能作为安全等价调整直接接受。**

**最小修复建议：** 复用现有主题/属性过滤；至少将标准问题归一化后相同、适用范围一致的候选纳入同组，不因 related 标签跳过。对无法分组的新主题，若相关证据出现所问钟点/收费矛盾且不能确定属于不同主体，返回缺失或交回保守处理。规则无法证明区分时不选定一个冲突值；无需新增第二次模型调用、健身房专用词表或通用语义框架。

**验收要求：** 同问题健身房 8:00/10:00 无论 answer/related 如何分配均不能 grounded；安静时段与客厅开放时间仍正常回答。再按影响面补同主体收费冲突与不同主体收费不冲突的对照，避免恢复原来的跨主题误伤。

## 3. 上一轮闭合情况与可接受取舍

| 项目 | 本轮判断 | 源码或测试依据 |
| --- | --- | --- |
| V6-R1 成功计划随异常带回 | 字段传递已实现；危险消费仍有 B24-R1 缺口 | [AssistantUnavailableError.plan_outcome](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:294)；_process_model_reply_body 异常入口；[test_main_reply_failure_carries_the_completed_plan](/Volumes/02/code/homestay-bot/tests/unit/test_deepseek_turn_plan.py:184) |
| V6-R2 规划后继续主回复前释放锁 | 源码已有第二次提交；真实数据库验收范围仍需补齐 | [process_recorded_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1032)；[test_human_active_calm_exclamation_is_answered_in_the_final_job](/Volumes/02/code/homestay-bot/tests/unit/test_conversation_plan_flows.py:264) |
| V6-R3 独立撤回与后续新申请 | 本轮未发现该旧契约矛盾继续存在，相关对照已通过 | [resolve_task_request](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:406)；test_withdraw_before_a_new_request_keeps_the_new_request；test_withdraw_then_other_request_registers_only_the_new_item |
| X1 独立 turn_plan 模块 | 分层理由成立 | [answer_policy 的计划依赖](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:1)，避免服务层反向依赖集成层 |
| X2 唯一摘录位置改正 | 可保留 | [turn_plan._locate_quote](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:144) 保留唯一出现与重复歧义的区别，相关回归通过 |
| X9 丢弃主模型回传 turn_plan | 应保留 | [_validate_decision](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1112) 的信任边界：采用本地计划，而非主模型同名字段 |
| X13 有效期核验 | 应保留 | [verify_selected_evidence](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:933) 的 outside_validity 与 period_boundary 分支 |
| X14 冲突范围 | 部分修正有依据，整体排除 related 不接受 | B24-R6 与安静/客厅对照 |
| D13/D14 规划失败先确认 | 属于已决定的业务取舍，非本轮缺陷 | [resolve_task_request](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:406) 与本轮相关回归 |
| D11 撤回通知但不取消已有任务 | 属于明确范围，非本轮缺陷 | resolve_task_request、[_notify_withdrawn](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1801)；不得把本次修复扩为任务取消功能 |
| X16 条件式停用提示 | 追加条件措辞有门禁依据 | [prepare_facility_advice_reply(safety_tip=)](/Volumes/02/code/homestay-bot/src/homestay_bot/services/guest_reply_policy.py:526)；避免挤掉其他设施建议 |

以上接受的是相应实现或取舍，不表示所有组合路径或真实外部验收完成。X5 的词面申请兜底仍是泛用性上限，但本轮未取得新增独立反例，未把它列为第七项已确认缺陷。

## 4. 证据不足的两项结论

### 4.1 事务释放和持久续接：实现依据与验收证据分开

新增 [test_soft_judgment_planning_holds_no_lock_and_drops_stale_results](/Volumes/02/code/homestay-bot/tests/integration/test_guest_reply_postgresql.py:139) 使用真实会话/作业仓储、事务和出站装配，暂停的是 **plan_turn**；另一事务锁会话并写新消息，恢复后丢弃过时结果。这是有判别力的规划等待证据。

然而，[HUMAN_ACTIVE 平静感叹的 final 测试](/Volumes/02/code/homestay-bot/tests/unit/test_conversation_plan_flows.py:264) 断言的是替身提交日志 `['commit', 'commit']`，不能单独证明重新加锁后的主回复等待在 PostgreSQL 中无锁，也不能证明中途提交后的取消/重建/恢复去重。

Spec [§2.7](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:315) 明确要求规划和主回复阶段分别暂停，以及提交后取消、服务重建、作业恢复核对。建议补充实际新增事务边界的专项证据：

1. HUMAN_ACTIVE/agitated 规划复核通过后，暂停主回复；另一事务的新客人活动和员工接管都能推进；恢复后旧结果不得建任务或登记出站。
2. 在真实 deferred 装配的中途提交之后取消，重建服务并恢复作业，核对 final 作业、客诉、通知、任务及出站的去重。复用已有效的既有用例，缺哪条补哪条。

这是**验收证据缺口**，本报告没有据此声称当前源码仍然持锁或重放已经产生重复副作用。本轮也没有重新启动 PostgreSQL 或执行该专项验收。

### 4.2 泛用性指标：回归门禁不能代替独立改写集

交接报告已如实说明 D1 独立改写集未编写，全部样本已用于回归或修复。这个边界应继续保留；136/154 首轮通过、无退步或安全已知失败为 0，都不能推导陌生改写通过率 95%。

修复候选冻结之后再按 Spec §2.7 的独立编写与冻结要求验收。应覆盖错字、中英混合、消息拆分、多房间多日期和局部失败，同时包含会改变实际动作的最小对照。E1–E6 已公开，只能转入回归，不能再次充当盲测输入。

## 5. 修复范围与复审交付建议

建议把六项修复整理成一个候选，复用现有计划、知识服务、ReplyPart 与会话处理入口，完成一次综合验收。当前报告提供方案，不替代项目要求的修复 Spec 和明确“开始”门禁。

| 修复落点 | 关联项 | 必须保持的边界 |
| --- | --- | --- |
| conversation_service._process_model_reply_body | B24-R1 | 成功/失败共用计划风险；保留摘要与过时校验，危险只升不降 |
| answer_policy.resolve_handoff_reason 与合并客诉入口 | B24-R2 | 全部变体先检查硬理由；只复核情绪；保留客诉风险等级 |
| deepseek_client 分项检索、选择与组合 | B24-R3、R4 | 逐项合法候选；静态知识只接管该项；剩余回答及实际动作不丢失 |
| turn_plan 的边界类型核验 | B24-R5 | 非法输入统一 failed，不重新授予词面建任务权限 |
| knowledge_evidence_policy.verify_selected_evidence | B24-R6 | 同主体所问属性真冲突必须拦；跨主题时钟/费用差异不误拦 |

接手方复审交付应包含：每项根因与最终修改符号、能捕获原缺陷的最小回归、最终客人正文和实际动作证据，以及第 4 节专项证据的完成或仍未完成状态。源码、测试或装配变化后，只重跑受影响的验证；涉及 application.py 装配时按项目门禁判断本地全量。真实模型门禁涉及授权和项目 REPLY_PATHS/范围规则，不能引用原报告的历史授权自行调用。

无需为这些修复新增第二个规划器、平行知识检索器、通用语法解析框架或专门补齐每个新问法的词表。根因在共同边界；局部特殊词补丁会继续暴露同类问题。

## 6. 本轮验证与外部边界

本轮审查阶段执行的相关回归命令如下，在项目根目录运行；整理报告时相关输入未变，复用该结果。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/unit .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_turn_plan.py \
  tests/unit/test_deepseek_turn_plan.py \
  tests/unit/test_conversation_plan_flows.py
```

结果：**62 passed，5.69 秒**。E1–E6 是额外的合成端口/函数反例，既有测试通过没有否定这些反例；本报告没有把它们伪称为已入库的永久回归测试。

本地 `.stage/reply-gate-v1.69.0.json` 保存的结果为 passed=true、regressions=0、newly_stable=20、safety_known_failures=0；建议基线为 must_pass=135、known_failures=19。正式 v1.69.0 与 rc-1.69.0-4 的 `src/homestay_bot` Git tree 均为 `77b65fa7d321e84074d9e718a38e9fd70b9504bc`，支持源码树一致这一报告说法。这里是核对保存的记录与 Git 对象，不是本轮再次执行真实模型门禁。

作者所报全量 2607 passed / 62 skipped、PostgreSQL 22 passed、CI、备份、部署副本、容器和公网检查，作为原交接报告的历史证据引用，本轮没有独立重做，不能合并计入本轮验证。实际客人收件仍未获得证明。

本轮没有调用真实 DeepSeek、Hostex 或企业微信，没有生产写入、修复业务代码、提交、推送或部署。报告整理只新增本报告及按项目规则维护必要的 AOCI 托管资产；原有未跟踪 `.impeccable/critique/` 保留，属于审查前已有状态。

## 7. 证据绑定与交接基线

下列 SHA256 于报告整理时核对，用于判断本会话审查证据是否仍适用于接手方的文件。它们不是生产安装包摘要。

| 文件 | SHA256 |
| --- | --- |
| docs/reviews/2026-10-07_claude-to-codex-reply-generalization-batches2-4-implementation-handoff.md | d164c491dd01431f07c4367eddb08865b4402b114dd7d0196114769884804b87 |
| docs/specs/2026-10-06_reply-generalization-spec.md | a3a2f96dfb78b3aa445897791cee1760c77ae7fd5b86fc18b295f10afd471937 |
| src/homestay_bot/services/turn_plan.py | 11bd1c8b8ad99ca34b63b2a292ae355cb6227c13b1f81d8cbd7bd32033099870 |
| src/homestay_bot/services/answer_policy.py | 15091ef29ce74d1bf8ac75c9a007bf2c6236b3f9e83596092bc24759eae2e9ca |
| src/homestay_bot/services/conversation_service.py | 78c87beda0a19791ed3a541076d1578981515a0419a29ec094da24fe239506d2 |
| src/homestay_bot/integrations/deepseek_client.py | e3d57b04d930da47b6f3489a11ac3c692737c8cbb22d64cc9abb0ee0f66eea93 |
| src/homestay_bot/services/knowledge_evidence_policy.py | d871ce46c14622db218adaea053386ff1c97abd35f8b37901567ee80ee6db096 |
| tests/plan_helpers.py | 04bf97f0baa21303f91ea4642fecd15ee1e8d95d7745a424e8d8d4bc127b216c |
| tests/unit/test_conversation_plan_flows.py | dfe21ef4f962acbf1ff97b4db62daf38e2e94665bbfedd62ef8fc765db18f334 |
| tests/unit/test_deepseek_turn_plan.py | ee50b080eecfbad79ba57182a2eb7c7c8f7c65747592efc6455675d395916c53 |
| tests/unit/test_conversation_service.py | bf65331bbcbb381959172f2fd9ae130a1f621c6150e89128861b209e0dc27c23 |
| tests/unit/test_knowledge_service.py | 9bf2a7236a8fe45acfcfcd30a3faf64a404bad423e764dd53ca20b9c533d7be1 |

接手时先核对实现版本与对应符号；变化后的旧行号和旧证据不得直接当成当前结论。复审通过需逐项关闭 B24-R1–R6，并准确说明第 4 节验收缺口；不得把文档回复、测试数量增加或原门禁通过替代修复证明。
