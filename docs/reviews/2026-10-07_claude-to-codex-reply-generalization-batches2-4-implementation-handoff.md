# Claude → Codex：回复泛用化第二至四批实施报告（v1.69.0，已上线）

- 日期：2026-10-07。作者：Claude。
- 用户指令原话：
  - 「开始，直接做完，全量模型也通过，发版部署上线也做，全部做完之后写报告写清楚你做了什么，依据是什么，我到时候发给codex审查」；
  - 第一批发布后纠正：「不是说了全部做完？」；
  - 随后：「为什么每批都要完整流程？不能一起改完一起测试吗」。
- 范围：
  - 回复泛用化 Spec v7.1 的**第二批**（轮次计划 + 知识与只读查询）、**第三批**（统一任务写入口）、**第四批**（软判定与接管）。
  - 三批一起实施，一次本地全量、一次 CI、一次全量真实模型门禁，一个版本发布。
  - 第一批已随 1.68.2 发布，报告见 [2026-10-07_claude-to-codex-reply-generalization-batch1-implementation-handoff.md](2026-10-07_claude-to-codex-reply-generalization-batch1-implementation-handoff.md)。
- 发布：
  - 提交 `fcfb83886457fda15cdd423da68062234084b8d3`，注解标签 `v1.69.0`，已部署生产（仅替换 API）。
  - 发布记录见 [docs/releases/1.69.0.md](../releases/1.69.0.md)。
- 请求：独立复审以下三点。
  - 实施是否忠实于 Spec v7.1；
  - §3.4 偏差 X1–X16 是否成立，尤其 X14 对正文规则的放宽；
  - 证据是否足以支撑「规划和主回复等待都不持锁」「撤回、历史、否定不建任务」「情绪词复核后不在任何出口重新生效」三个核心结论。

## 1. 依据

| 依据 | 位置 | 状态 |
| --- | --- | --- |
| 回复泛用化 Spec v7.1 | [docs/specs/2026-10-06_reply-generalization-spec.md](../specs/2026-10-06_reply-generalization-spec.md) | 三段经用户确认，D1–D14 已决定；§3.2 已补记合并发布决定，§3.4 新增实施偏差记录 |
| 紧急豁免范围修复 Spec v4 | [docs/specs/2026-10-07_emergency-exemption-scope-spec.md](../specs/2026-10-07_emergency-exemption-scope-spec.md) | 第一批已实施；本次只读引用 |
| Codex 历轮审查 | 本目录 `2026-10-07_codex-to-claude-reply-generalization-*` | 逐项回应见 Spec §5 |

各批承诺按 Spec §3.2 表分别核对；合并发布没有扩大任何一批的承诺。

## 2. 做了什么

### 2.1 计划契约（第二批，Spec §2.3）

- **新模块** `src/homestay_bot/services/turn_plan.py`，包含 `PlanItem`、`TurnPlan`、`PlanOutcome`、`verify_turn_plan`、`usable_plan`、`plan_source_sha256`、`planner_messages`。
  - 没有放进 `deepseek_client.py`（偏差 X1）：`answer_policy.resolve_task_request` 需要这些类型，而服务层不能反向依赖集成层。
- **本地核验**（`verify_turn_plan`）：
  - 13 种类型白名单；`id` 为正整数且唯一；`withdraws` 只允许撤回项使用；
  - 风险只接受 `none`、`possible_hazard`、`complaint`、`current_hazard:{fire,gas,electric,medical,violence}`。以上任何一项不合规，整份计划失败；
  - 摘录必须出现在冻结正文。给定位置精确命中即有效；不命中但摘录在正文里唯一出现时，按唯一位置改正（偏差 X2）；出现多次又给错位置时，该项无效；
  - 房号必须是原文里出现的数字（偏差 X3）；
  - 日期走 `validate_stay_date_range`，不合法就丢弃日期；
  - 全部摘录失效即规划失败。
- **规划入口** `DeepSeekGuestAssistant.plan_turn`：
  - 一次无工具短调用，`asyncio.wait_for` 6 秒（D5），所有异常与超时都返回 failed 结果；
  - 日志只记状态、原因类别、项数与耗时。
- **respond 里的规划与传递**：
  - `respond` 在内部探针之后、检索之前规划。外层负责：合并阶段传来的计划按 `source_sha256` 复用；返回的决定一律附上本地计划；主回复抛出 `AssistantUnavailableError` 时把计划写入 `plan_outcome`（V6-R1）。
  - 模型 JSON 里的同名字段在校验前丢弃（偏差 X9，构造用例证明否则整份决定校验失败）。
  - 开关为构造参数 `plan_turns`（偏差 X6）：生产 `runtime_clients.build_runtime_client_bundle` 与门禁运行器开启。

### 2.2 知识与只读查询（第二批，Spec §2.4）

- **工具开放**：
  - `_plan_tool_names` 把房态、订房、房型项映射成工具；`allowed = 旧规则 ∪ 计划`；
  - `tool_choice` 只按旧规则强制（`forced_tool_names`），计划新增的工具用 `auto`；
  - 执行仍经 `HostexReadOnlyToolExecutor` 与日期校验。
- **检索**：
  - `_merge_item_knowledge` 对每个 `static_fact` 项单独检索（最多 4 项，每项 4 条、3000 字），与整句结果合并去重，总量不超过 18000 字（偏差 X7）；
  - 项内房号经 `KnowledgeService.find_property_by_room`（生产查 `PropertyProfile.room_number`）映射为房源；
  - `retrieve_detailed(reserved_property_slots=3)` 让房间专属条目额外占名额并前置（D8）。
- **证据选择**：
  - 主调用在 `evidence_selection` 里为每项回传 `answer_ids`、`related_ids`；
  - `_selection_evidence_plan` 逐项调用 `verify_selected_evidence`：通过的发审核原文，选「无」或漏选的回未确认，越界的交回 `build_evidence_plan`；
  - 不回传选择时，整体回到现行证据计划。
- **verify_selected_evidence**（`knowledge_evidence_policy.py`）：
  - 编号必须是本轮合法候选；
  - 有效期不覆盖目标日期的条目不合法（偏差 X13）；
  - 跨越生效边界交回现行计划分段；
  - 冲突只查客人问到的钟点或收费，范围是「回答条目 ∪ 问法点名同一主题的合法候选」，`related_ids` 不进冲突分组（偏差 X14）；
  - D6：特殊时期与平时政策矛盾时取特殊时期（偏差 X4，现行证据计划同口径）；
  - 保留指令注入与长度规则。
- **无日期问价**：
  - 删除整轮早退；本轮收回参考价工具，追加日期澄清分项，系统提示要求模型不答房价；
  - 计划确认整轮只有无日期问价时，仍直接追问（偏差 X8）。
- **本店专属判定**：计划成功且没有本店事项时，「你们」不再单独让问题落入未确认（`_plan_property_specific`）。

### 2.3 统一任务写入口（第三批，Spec §2.5）

- `answer_policy.resolve_task_request(plan_outcome, text) -> TaskResolution`，字段为 `register`、`task_type`、`subjects`、`ask_confirm`、`safety_tip`、`current_fault`、`withdrawn`、`planned`。
- **计划成功时**：
  - 失效撤回且同轮有申请 → 确认；有撤回且有失效申请 → 确认；
  - 撤回指向在前的申请 → 取消该项；
  - 撤回在前、本计划内无更早申请 → 对象不在本计划，后续申请独立判定；
  - 有更早申请但 `withdraws` 为空或无效 → 确认；
  - 历史、否定、静态咨询不登记；
  - 只有不明确项且词面像申请时转确认（偏差 X5）。
- **规划失败时**：服务类不登记，词面命中回 D13 话术；设施有故障信号时给安全提示并请确认（D14）。
- **调用方**（相同输入同一结论）：
  - `_validate_decision`，决定是否保留 `task_suggestion`；
  - `ConversationService._record_task_suggestion`，类型取判定结果；撤回时 `_notify_withdrawn` 通知管家（D11），不取消已有任务；
  - `_handle_facility_issue(..., plan_outcome=)`；
  - `_process_model_reply_body` 的两条设施分支。主回复失败时取异常计划，摘要不一致按无计划。
- **安全提示**：`prepare_facility_advice_reply(safety_tip=)` 在模型建议没写停用时，追加「如涉及设备，请先停止使用，不要自行拆卸」（偏差 X16）。

### 2.4 软判定与接管（第四批，Spec §2.3、§2.6）

- **统一接管理由**：
  - `answer_policy.resolve_handoff_reason(text, plan)` 只允许复核 agitated；退款、平台投诉、议价不受计划影响；
  - 替换了 `handle_message` 的 `HUMAN_ACTIVE` 护栏、`process_debounced_message`、`process_recorded_message`、`_validate_decision` 与 `_process_model_reply_body` 的独立判定；
  - 删除了因此成为孤儿的 `ConversationService._determine_handoff_reason`。
- **`ConversationService._plan_with_released_lock`**：
  - 先 `commit_boundary()` 释放活动锁，再调用 `plan_turn`；
  - 之后重新 `lock_activity`（`populate_existing=True` 刷新会话行）；模式、原生人工会话或新活动有变化时返回 None，调用方丢弃本轮；
  - 没有提交边界或助手不支持规划时返回失败结果，沿用现行规则。
- **三个入口**：
  - `handle_message`：只命中情绪词的客诉、`HUMAN_ACTIVE` 下只命中情绪词、非合并模式的无关判定，在有作业设施时登记 debounce 作业后返回，不在请求里规划；
  - `process_debounced_message`：在作业内规划复核。计划判为投诉或规划失败时进客诉；判为非投诉时继续，不通知员工（D9）。无关判定只有计划全为 unrelated 或规划失败时才婉拒。计划随 `_stage_fast_ack` 写入最终任务载荷 `turn_plan`；
  - `process_recorded_message`：从载荷还原计划。`HUMAN_ACTIVE` 下只命中情绪词时规划复核；继续主回复前再提交一次，释放复核时重新取得的锁（V6-R2）；主回复后沿 `_discard_stale_final` 再复查。
- **装配**（`application.py`）：
  - 后台作业业务会话注入 `commit_boundary=session.commit`（原为 None）；
  - `_deferred_message_from_payload` 还原 `turn_plan`。
- **危险补漏**：
  - 计划 `current_hazard` 而词面未命中时 `_escalate_emergency`；`possible_hazard` 给可能危险提醒。只升不降；
  - 规划提示补了与主模型相同的危险口径（偏差 X15）。

### 2.5 门禁运行器与其他

- `tools/reply_regression.py` 与线上同序：
  - 情绪词客诉与无关判定先 `plan_turn` 复核；
  - 设施分支按 `_is_facility_issue` + 统一判定 + 安全提示；
  - 计划判为当前危险时走紧急路由；
  - 规划失败的服务申请附 D13 话术；
  - 记录规划耗时与计划状态。
- `scripts/release/reply_gate.sh` 的 `REPLY_PATHS` 补了 `turn_plan.py`；`model_budget.py` 增加规划预算。

## 3. 验证证据（分项，不互相替代）

| 层 | 结果 |
| --- | --- |
| 新增判别测试 | `test_turn_plan.py` 20、`test_deepseek_turn_plan.py` 14、`test_conversation_plan_flows.py` 18、PostgreSQL 新用例 1；计划经 `tests/plan_helpers.plan_for` 走生产核验 |
| 改前失败证明 | 在 v1.68.2 独立工作树里只补入计划数据模块后运行：`test_conversation_plan_flows.py` 19 个中 15 个失败，PostgreSQL 新用例失败。通过的 4 个是保持现状的对照：规划失败进客诉 ×2、规划失败的无关回复、无计划载荷。候选门禁暴露的缺陷各补用例（越界有效期、未问属性假冲突、相关条目假冲突、停用提示），先在修正前确认失败，再确认修正后通过 |
| 既有用例调整 | 依赖词面登记的会话用例改为声明计划（D13 下无计划不登记，是 Spec 规定的行为变化）；问价早退用例拆成「计划只有问价」「无计划多问」；生产装配用例的模型替身补规划应答 |
| 本地全量 | 2607 passed / 62 skipped；Ruff、Mypy 通过 |
| PostgreSQL | 本机临时 PostgreSQL 16，迁移到 head：22 passed。新用例在真实 `SQLAlchemyConversationRepository` 与作业仓储上暂停规划，另一事务立即 `SELECT FOR UPDATE` 会话行并写新消息（锁超时 5 秒，持锁即失败）；恢复后过时结果不建客诉、不排任务、模式不变 |
| CI | main 与 v1.69.0 success：2607 passed / 62 skipped；PostgreSQL 22 passed |
| 真实模型门禁 | 候选 rc-1.69.0-1～4（本机标签，未推送）逐轮修正（见 §4）；部署门禁对 v1.69.0 全量通过：首轮 136/154，无退步，新纳入 20 个，安全类未通过为 0。规划 P50 855 ms、P95 1173 ms、最大 1534 ms，无超时 |
| 部署与运行态 | 完整备份与部署前备份可读；服务器源码 `fcfb838`；新 API 容器 running、restarts=0；PostgreSQL 未动；Alembic 未变；10 个关键文件哈希在源码与安装包一致；公网版本 1.69.0；日志异常 0 |
| 容器内行为 | `plan_turns=True` 已装配；历史、撤回不登记；情绪词按计划复核；无计划服务申请只回确认 |
| 未做 | 测试号真实收发（需当次授权，未取得）；部署后至核对时无客人消息，生产日志尚无规划记录 |

## 4. 候选门禁暴露的问题与修正（请重点核对）

1. **rc-1.69.0-1：15 个退步。**
   - 早餐 5 个：门禁资料日期为 9 月 25 日，而检索按服务器当天（国庆期间）返回了国庆早餐条目，模型选中它，`verify_selected_evidence` 没按目标日期判有效期。修正为 X13。现行证据计划按日期分段，本来就排除它。
   - 婴儿床、行李、洗衣、延迟退房等 6 个：属性认不出时缺省同时查钟点和收费，且按答案正文分组，造成假冲突。修正为 X14，与现行证据计划同口径：只查问到的钟点或收费，按问法分组。
   - 跳闸、停电 2 个：规划把普通跳闸判为 `current_hazard:electric`，升级成紧急处置。修正为 X15，规划提示补危险口径。
   - 另见「它能烘干吗」被标为不明确后收到「需要我们现在为您安排什么」。修正为 X5，要求词面像申请。
2. **rc-1.69.0-2：1 个退步（K-安静-D）。** 模型把客厅开放时间标为 related，与安静时段判成钟点冲突。修正为 X14 放宽：related 不进冲突分组。**这削弱了 Spec「新主题在 answer 与 related 之间互查」的规则。** 理由是 related 按定义不直接回答；同主题的 related 已在同组候选里，其余是别的主题。代价是：主题认不出、且模型把真正矛盾的条目标为 related 时，不再被拦下。
3. **rc-1.69.0-3：1 个退步（F-跳闸）。** 路由已正确，但模型建议 3 次中 2 次没写停用。按 §2.5「safety_tip 有当前故障信号就给」由本地保证（X16）。先试的「补在最前、占一个名额」把噪音类问题的「关好门窗」挤掉且答非所问，改为带条件措辞追加。
4. **rc-1.69.0-4：通过。**

v1.69.0 的 `src/homestay_bot` 树与门禁资料和 rc-1.69.0-4 一致。部署脚本只接受 `--skip-reply-gate` 参数，所以部署时照常对正式标签又跑了一次全量，也通过。

## 5. 与 Spec 的差异（汇总，详见 Spec §3.4）

X1 计划模块位置；X2 摘录位置改正；X3 房号须在原文；X4 D6 只在矛盾时生效；X5 不明确项须词面像申请；X6 `plan_turns` 开关；X7 合并检索预算；X8 整轮只问价仍直接追问；X9 丢弃模型回传的 `turn_plan`；X10 D3a、D3b 未实施；X11 D10 V-b 未实施；X12 D1 改写集未编写；X13 有效期过滤；X14 冲突范围（含放宽）；X15 规划危险口径；X16 本地停用提示。

## 6. 已知未覆盖与后续

- **19 个已知未通过**：
  - SR-毛巾、SR-遗失：计划判为申请并登记，但静态证据计划按话题回「尚未确认」，覆盖了正文。下一步应让证据计划不接管只含服务申请的轮次；
  - SC-401 两个：检索已能取到 9045，有上下文的 3 次中 1 次通过，仍不稳定；
  - UN-股票EN：运行器固定中文婉拒，线上按会话语言回复，是运行器口径问题；
  - K-吸烟-EN、EM-打电话：确定性紧急误判，属于 D3a、D3b；
  - 其余见发布记录。
- **延迟**：每轮多一次规划调用，门禁首轮单场景 P50 从 1.3 秒升到 2.4 秒、P95 从 2.5 秒升到 3.8 秒。生产 P95 需上线后按日志实测。
- **规划失败的体验代价**：服务申请与设施报修多一轮确认（D13、D14）。需监控规划失败率（日志「轮次规划：status=failed」）。
- **没有作业设施时**：即时入口的软判定沿用现行规则（Spec §3.1 已知上限）。
- **冲突规则的上限**：只覆盖收费、金额、钟点三类，其他属性的矛盾查不出（代码中 `ponytail:` 注释已写明）。
- **D1 盲测**：独立改写集未编写，本次所有样本都属于回归集，不能称盲测，不能据此宣称 95% 改写通过率（D4）。

## 7. 请 Codex 复审的重点

1. X14 的放宽是否可接受；若不可接受，什么样的「related 冲突」规则既不误伤 K-安静-D 一类，又能拦下真实矛盾。
2. `_plan_with_released_lock` 与 `process_recorded_message` 两处提交，是否兑现「规划与主回复等待都不持锁、副作用前必复核」。特别是 debounce 作业中途提交后，作业失败重放时出站、客诉与最终任务的去重是否完整。
3. `resolve_task_request` 的分支是否覆盖 Spec §2.5 表全部情况且结论唯一；X5 的「词面像申请」是否让不明确申请漏掉确认。
4. 设施分支条件 `plan is None or resolution.safety_tip`：计划判为当前故障但被撤回时（安全提示为真、不登记），回复只有安全提示、没有确认话术，是否符合 D11、D14。
5. 门禁运行器与线上的差异是否引入了新的假通过（运行器仍不覆盖合并、出站、过时判定与实际收件）。

## 8. 工作区归属

- 未跟踪的 `.impeccable/critique/` 不属于本次工作，未暂存、未提交。
- 本机候选标签 rc-1.69.0-1～4 未推送，保留作门禁结果出处（`.stage/reply-gate-rc-1.69.0-*.json`）。
