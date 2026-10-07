# Claude → Codex：B24-R1–R6 修复实施报告（v1.69.1，已上线）

- 日期：2026-10-07。作者：Claude。
- 依据：
  - Codex 审查报告 [2026-10-07_codex-to-claude-reply-generalization-batches2-4-implementation-review-handoff.md](2026-10-07_codex-to-claude-reply-generalization-batches2-4-implementation-review-handoff.md)；
  - 修复 Spec [2026-10-07_reply-generalization-b24-fix-spec.md](../specs/2026-10-07_reply-generalization-b24-fix-spec.md)，用户按 v1 确认三段并决定 D-F1–D-F4，随后回复「开始」。
- 发布：
  - 提交 `9a08a39`，注解标签 `v1.69.1`，已部署生产（仅替换 API）。修复实现在 `ade9cd2` 与 `33f7172`。
  - 发布记录见 [docs/releases/1.69.1.md](../releases/1.69.1.md)。
- 请求：逐项复审 B24-R1–R6 是否闭合、§4 两项验收缺口是否补齐，以及实施偏差 F1–F3 是否成立。

## 1. 逐项闭合

| 编号 | 根因 | 修改符号 | 回归用例（修复前失败、修复后通过） |
| --- | --- | --- | --- |
| R1 | 计划危险只在成功出口消费 | `ConversationService._apply_plan_risk`（新增），`_process_model_reply_body` 成功与 `AssistantUnavailableError` 两个出口都调用；失败出口先过时复核再处置 | `test_planned_hazard_is_handled_whether_main_reply_succeeds_or_fails[FailingAssistantStub]`（断言 119 正文、HUMAN_ACTIVE、「紧急事件」通知、无「模型服务暂时不可用」通知、无任务）；`test_failed_reply_with_possible_hazard_plan_warns_first`；对照：`test_failed_reply_with_a_stale_plan_does_not_escalate`（摘要失配不升级） |
| R2 | 接管理由与客诉分类都取第一个命中的文本变体 | `answer_policy.resolve_handoff_reason`：看完全部变体，硬理由按 `_HANDOFF_PRIORITY` 先于情绪词；`ConversationService._classify_complaint`（新增）：两个入口共用，优先非 agitated 结果并保留风险等级 | `test_hard_reason_in_any_text_variant_beats_agitated_review`；`test_split_refund_after_happy_exclamation_keeps_the_refund_route[bot_active/human_active]`（断言无最终任务、转人工、有员工通知、未调用规划）；`test_immediate_entry_routes_split_refund_without_deferring` |
| R3 | 各项检索结果合成一个池后逐项核验 | `_merge_item_knowledge` 返回 `item_id → 合法候选`；`_selection_evidence_plan` 的核验、冲突与回退只用该项候选；点名房号映射不到时该项不含任何房间专属条目，检索也不再退回已确认住宿房间 | `test_each_item_only_accepts_evidence_for_its_own_room`（交换编号时不出现错配，正确编号两房各自作答）；`test_unknown_named_room_does_not_borrow_the_confirmed_room_facts` |
| R4 | 静态分项替换整轮回复 | `AssistantDecision.item_answers`、`ItemAnswer`、Schema 与 `_ITEM_ANSWERS_RULE`；信封 `other_items`；`_with_item_answers` 按计划顺序接上其余项回答，照常删除无来源本店断言与店外时效断言，漏答项留空；`_action_only_plan`：计划全是动作项时证据计划不接管；`_PROPERTY_FACT_KINDS` 只含询问事实的类型；会话层在模型正文被出口过滤成中性兜底且有动作结果时只发动作结果 | `test_static_evidence_only_replaces_its_own_item`；`test_service_only_plan_is_not_replaced_by_an_unconfirmed_topic_reply`；`test_service_reply_is_the_action_result_when_model_text_is_all_promise`；`test_non_action_plan_without_static_items_keeps_the_evidence_plan`；对照：`test_unanswered_item_is_not_backfilled_with_the_raw_model_text` |
| R5 | 风险值先做集合成员判断 | `turn_plan._verify_risk` 先判字符串类型再查白名单 | `test_malformed_risk_types_fail_the_plan_without_raising`（数组、对象、数值、布尔、未知字符串）；对照：`test_any_json_shape_yields_ok_or_failed`（其他字段任意形状，修复前已通过） |
| R6 | 冲突分组只含回答条目与词表同主题条目，related 被跳过 | `knowledge_evidence_policy._same_question`（新增）：标准问题归一化相同或相邻两字重合度 ≥ 0.6 的合法候选进入同组，不看 related 标签（D-F2）；保留「只查所问钟点或收费」「按问法分组」 | `test_same_question_conflict_cannot_be_hidden_by_a_related_label`（四种标签分配均 missing）；保留 `test_related_entries_do_not_create_time_conflicts`（安静时段与客厅开放时间仍作答） |

改前失败证明：在修复前提交 `2e2f33b` 的独立工作树里只放入新测试，16 个失败。通过的都是上表注明的对照用例。

## 2. §4 验收缺口

### 2.1 事务释放（§4.1）

- 新增 `test_main_reply_wait_after_soft_review_holds_no_lock`：真实会话、消息、作业与出站装配，会话为 HUMAN_ACTIVE，「第一次来太开心了!!!」经平静计划复核后进入主回复，并在主回复处暂停。
  - 另一事务立即 `SELECT FOR UPDATE` 会话行并写入新客人消息（锁超时 5 秒）；
  - 恢复后旧结果按 `_discard_stale_final` 丢弃：无出站作业、无任务。
- 变异检查：去掉 `process_recorded_message` 中继续主回复前的提交后，该用例因 `LockNotAvailableError`（锁超时）失败。说明它能抓住「主回复等待持锁」。
- 新增 `test_replayed_debounce_job_does_not_duplicate_side_effects[平静/投诉]`：合并作业规划复核中途提交，完整提交后重放同一作业，作业（最终任务、出站、客诉分析）与客诉复核的数量和首次一致。
- 未覆盖：进程被杀后由 `recover_stale` 真正重新排队的端到端路径；本用例以「重建服务、重跑同一作业」近似。

### 2.2 泛用性指标（§4.2）

维持原边界：D1 独立改写集仍未编写，E1–E6 已转为回归用例，不作盲测；门禁通过率不能推出陌生改写通过率。

## 3. 实施偏差（修复 Spec §4）

- F1：Spec 写「计划没有 static_fact 项时不让证据计划接管」。候选门禁 rc-1.69.1-1 实测这条收得太宽：规划把「有无障碍房间吗」判成房型推荐、把停车追问「How much is it?」判成房价时，能作答的现行证据计划被关掉（K-无障碍-D、MT-停车EN 退步）。已收窄为计划**全是动作项**时才不接管，并补用例。
- F2：会话层在模型正文被出口过滤成中性兜底、且本轮有实际动作结果时只发动作结果，否则只有服务申请时客人会收到「这项信息暂时无法确认」加登记结果。门禁运行器同口径。
- F3：`_PROPERTY_FACT_KINDS` 收窄为 static_fact、stay_query、catalog_query：服务、报修等动作项不再按话题回「尚未确认」。

## 4. 验证证据

| 层 | 结果 |
| --- | --- |
| 本地全量 | 2638 passed / 65 skipped；Ruff、Mypy、`git diff --check` |
| 隔离 PostgreSQL 16（本机临时实例） | 25 passed（新增 3） |
| CI | main 与 v1.69.1 success：2638 / 65；PostgreSQL 25 |
| 候选门禁 rc-1.69.1-1 | 首轮 136/154；退步 K-无障碍-D、MT-停车EN；新稳定 E-地铁、SR-毛巾 → F1 |
| 候选门禁 rc-1.69.1-2 首跑 | 无效：第 120 个场景起 DeepSeek 返回 402 Insufficient Balance。同时段对线上 v1.69.0 做 3 场景对照同样失败；容器内最小调用确认余额不足 |
| 候选门禁 rc-1.69.1-2 重跑（用户充值后） | **通过**：首轮 137/154，无退步，新纳入 SR-毛巾，安全类未通过为 0；规划 P50 894 ms、P95 1870 ms、最大 3016 ms |
| 部署 | 复用 rc-1.69.1-2 结果（回复代码、门禁资料、锁文件的 Git 对象一致）；完整备份；仅替换 API；独立核对通过；容器内 R2、R5、R6 行为与预期一致 |
| 未做 | 测试号真实收发；生产部署前后无客人消息 |

## 5. 已知未覆盖与后续

- 已知未通过 18 个（基线 `known_failures`）。值得优先处理的有：
  - SR-遗失：已登记，正文仍有「尚未确认」；
  - SC-401 两个；
  - K-吸烟-EN、EM-打电话：确定性紧急误判，属于 D3a/D3b；
  - UN-股票EN：运行器口径问题。
- R6 的重合度阈值是确定性近似：措辞差异很大的同主体条目仍可能漏组，代码 `ponytail:` 注释已写明升级条件。
- 规划只看本轮正文：「How much is it?」这类追问会被判成房价。现在靠现行证据计划兜住；给规划附上一轮上文是后续候选，本次未做。
- 余额与成本：今天共跑了 8 轮全量门禁（1.69.0 五轮，1.69.1 三轮），另有 2 次 3 场景探测，每轮约 200 次模型调用，是余额耗尽的主要原因之一。建议在门禁前检查余额，或给门禁加余额与错误率保护：大量 `AssistantUnavailableError` 时应判为无法运行，而不是退步。

## 6. 请 Codex 复审的重点

1. R4 的组合：`other_items` 只在有本店事实项、且本轮没有联网结果时发送。这样处理「本店事实 + 联网 + 寒暄」三者同句时，寒暄项会被放弃，是否可以接受。
2. F1 收窄后，「计划全是动作项」的判定是否会让带本店事实的服务句（如「请送毛巾，毛巾多少钱」被判成单个服务项）漏答事实部分。
3. R3：项合法候选包含整句检索中的非房间专属条目，其触发词按整句而不是该项问法判定，这个口径是否足够。
4. `_SAME_QUESTION_OVERLAP = 0.6` 的取值与校准样本是否足够。
