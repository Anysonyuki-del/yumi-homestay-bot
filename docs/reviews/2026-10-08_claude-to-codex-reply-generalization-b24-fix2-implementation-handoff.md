# Claude → Codex：1.70.0 实施审查（回复泛用化第二轮修复 + 知识缺口转管家 + 三项小修复修订）

- 日期：2026-10-08
- 依据：`docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md` v10（三段已确认）；三项小修复见 `docs/reviews/2026-10-08_claude-to-codex-knowledge-placeholder-and-logging-handoff.md`（含 §9 按你上次审查的修订）。
- 用户授权（2026-10-08）：「泛用化一起做完，给所有权限，一直做到部署前为止，做完后和这次修订一起写报告准备提交审核」。据此：本地全量、隔离 PostgreSQL、全量真实模型门禁、生产只读核对已做；**未提交、未推送、未部署**，全部改动留在主工作区待你审查。
- 本地基线：`80f9b2c`（main）；生产：v1.69.1。门禁候选 `rc-1.70.0-1`～`3`（本机标签，用临时索引 `git commit-tree` 生成，分支未移动、未推送）；**rc-1.70.0-3（`dca28e5`）全量门禁通过**。

## 1. 审查范围

| 部分 | 内容 | 主要文件 |
| --- | --- | --- |
| 阶段一 覆盖契约（§2.1–§2.3） | 逐项候选、小句覆盖、同一对象、客诉合并 | `deepseek_client.py`、`item_coverage.py`（新）、`knowledge_evidence_policy.py`、`knowledge_service.py`、`complaint_service.py`、`answer_policy.py` |
| 阶段二 转交闭环（§2.6） | 缺口转交、卡片、收据、草稿、后台、清空 | `conversation_service.py`、`application.py`、`human_session.py`、`handoff_card.py`、`knowledge_referrals.py`（仓储，新）、`knowledge_referral_service.py`（新）、`routes/knowledge_referrals.py`（新）、`routes/knowledge.py`、`deepseek_faq_drafter.py`、`sensitive_text.py`（新）、`customers.py`、`jobs.py`、`worker.py`、迁移 `0035` |
| §2.4 PostgreSQL 补证 | 原生接管、中断窗口、`recover_stale` | `tests/integration/test_referral_postgresql.py`（新） |
| 小修复修订 | 占位符拦截（含启用校验）、INFO 日志、Bearer 脱敏 | `routes/knowledge.py`、`logging.py`、`main.py`（已在上一份报告 §9 交代） |

## 2. 阶段一：覆盖契约（摘要，细节见 Spec §2.1–§2.3）

- `DeepSeekGuestAssistant._merge_item_knowledge`：每个事实项（static_fact、stay_query、catalog_query）只从本项检索取合法候选；`trigger_any` 按本项摘录、`trigger_exclude` 按完整原文（`KnowledgeService.retrieve(..., trigger_text=, exclude_text=)`）。
- `item_coverage`：同一次切句（`split_clauses`、`intersecting_clauses`），按小句判所需事实（`clause_needs`：价格、房态、目录与剩余），小句目标（`clause_target`：房号映射、入住/退房角色、无法绑定），按实际证据行判覆盖（`evidence_covers`），逐夜价格完整性（`missing_nights`）。锚点清单有漂移测试（每个锚点放进真实语境必须触发对应识别函数）。
- `_tool_coverage`、`_compose_items`、`_static_item_parts`：工具没覆盖的房间、日期或剩余限定（「看江景」）留作未确认或审核答案；`availability_fallback` 出口用同一契约；D-G10 无日期房态先问入住与退房日期。
- `knowledge_evidence_policy`：`object_question_key`、`same_object`（范围、房源、归一化问法都相同才是同一对象），冲突与 D6 只在同对象内；`_period_veto`（D-G8）；`build_evidence_plan(object_scoped=True)` 的回退返回 conflict / period_veto 原因。
- `ComplaintService.classify_all`：退款 > 投诉 > 情绪，风险取最高；会话与门禁 `pre_route` 共用。
- 红测：阶段一新增行为测试在 `80f9b2c` 上 12/13 失败（1 项为对照）。

## 3. 阶段二：转交闭环

### 3.1 缺口单元与选择（§2.6(1)）
- 调用前用同一切句列出 `gap_units`（编号 = 计划项编号 + 原文区间，`unit_id_for`），信封带 `gap_units: [{id, text}]`；模型可选输出 `gap_referrals`，宽松逐条校验（`AssistantDecision.keep_valid_gap_referrals`）。
- 调用后 `_referral_candidates` 只接受本地组合后仍为未确认、原因 `knowledge_missing` 的单元。

### 3.2 会话层（§2.6(2)，`ConversationService._refer_gaps`）
- 位置：`_record_task_suggestion` 之后、`prepare_planned_reply` 之前；高风险或转人工本轮不进入。还要求：计划成功且风险全为 none、会话 BOT_ACTIVE、单元确实以 `status=missing` 分项出现（不变量 1）、原文与标准问题不命中 `FAQ_EXCLUDED_PATTERN`、原文/标准问题/类别都不含敏感内容。
- 每个单元一个保存点：`find_by_source` 复用 → 抑制（30 天）→ 会话冷却（24 小时；待处理或整理中回「已提交管家核实，请稍候」，其余回现行未确认；都不发卡）→ 新建 → 同一保存点登记卡片作业（`HANDOFF_CARD_JOB_TYPE`，载荷只有 `purpose/referral_id/agent_id/employee_userids/task_id`，去重键 `referral-card:{id}`）。保存点失败只回滚该单元，客人回现行未确认。
- 被转交的分项按 `ReplyPart.gap_unit` 去掉，固定说明在出口过滤之后追加（与 `action_reply` 同一位置）；分项全被转交时不退回模型整段正文；`unchanged_ack` 有转交即判变化；有转交时跳过 `_track_frequent_faq`。
- 客人引用 `referral_label`：先去链接再净化，压一行、去控制字符、截 30 字。

### 3.3 来源完成收据与来源清空
- `application._run_deferred_source`（合并与最终作业唯一入口）：先查 `receipt_exists` 与 `source_cleared`，任一成立直接结束；`_dispatch_deferred_message` 返回「已终结」时与业务副作用同事务写收据（含 `service.created_referral_ids`）。
- `process_debounced_message` 返回 bool：所有早返回为终结；`_stage_fast_ack` 排出最终作业时返回 False（由最终作业写收据）。
- 等待后复核：`_discard_stale_final` 在取锁后查 `source_cleared` 与 **原生接管**（见 §5 偏差 2），再查新活动；`_plan_with_released_lock` 返回处加 `source_cleared`。判据 `SQLAlchemyMessageRepository.source_cleared` 读列投影当前值。
- 发送处：`_guest_reply_is_stale` 在 `stale_exempt` 之前按 `source_guest_message_id` 查来源清空，后续分段同样拦截。

### 3.4 卡片（§2.6(2)，D-G19）
- worker：`_send_referral_card`（模块级，`send_handoff_card` 遇 `purpose=referral` 调用）只在转交为待处理时发，按记录组最小卡片（`referral_card`：原因「资料待核实」、净化原问、房号、对话链接），不做 `maintain_customer`、不带要点、偏好或客人最近原话。
- 按钮：`HumanSessionService.handle_card_action` 两阶段。动作前 `card_for(conversation, task_id)` 取快照（数据库错误在调用平台接入前抛出）；资料核实卡片快照为 None（缺失、会话不符、已清空）时不接入，显示固定关闭卡片；交还始终执行，资料核实卡片交还后显示不含旧原问的固定卡片（`closed_card_for`）。动作后只纯组卡，更新失败只记日志。
- `resolve_notification_identity`：读取 CRM 备注时 `except SQLAlchemyError: raise`，其余异常仍兜底；不回滚共享会话。

### 3.5 草稿与后台（§2.6(3)–(5)）
- 整理：`KnowledgeAdminService.start_referral_draft` 行锁、代次 +1、清旧草稿、按（发送时间，编号）冻结窗口内最新 5 条非敏感管家消息编号、入队 `knowledge_referral_draft`（去重键含代次）。窗口：同会话、SERVICER 文字、编号大于来源、发送时间在创建时间（截到秒）到 +48 小时。
- 任务：`ReferralDraftJobService.handle` 非「整理中 + 当前代次」直接结束不调模型；按冻结编号读当前列值、跳过已清空、净化、预算 2000 字（从最新往前取，被截掉的不能作依据）；模型失败抛 `RetrySafeJobError` 交给 Worker（`knowledge_referral_draft` 加入 `_retryable_job_types`）；写回唯一入口 `finish_draft`（条件更新）。
- 失败落点：普通异常耗尽 → 失败回调 `_fail_exhausted_referral_draft`；超时耗尽 → `recover_stale` 在清空载荷前 `_fail_stale_referral_drafts`。
- 新草稿入口 `DeepSeekFaqDrafter.generate_from_staff` / `validate_staff_draft`：拒链接与凭据；`found_answer` 为真时依据片段非空、非空白、逐条出现在实际交给模型的消息里；不要求占位符。旧入口不变。
- 后台：新路由 `/employee/knowledge-referrals`（列表、详情、整理、入库、关闭），仅管理员，沿用知识 CSRF；知识库页签显示待处理数量。入库 `convert_referral`：行锁、状态 = 已整理、代次一致、草稿完整、服务端校验「已核对」、范围只能「仅此房间（须选房间）/全店」、全部字段凭据检测 + 占位符拦截 + `_validate_scope`，一个事务建启用知识并只结束本条；知识外键 `SET NULL`。关闭写 30 天抑制。

### 3.6 清空（§2.6(6)）
- `clear_test_customer_data`：锁客户行后按会话编号升序 `FOR UPDATE` 锁会话，再改消息与转交；`clear_for_conversations` 清原问、标准问题、类别、草稿、冻结编号与归一化问题键，状态已清空、代次 +1，保留编号与知识编号；收据保留。

### 3.7 持久化
- 迁移 `0035_knowledge_referrals`：`knowledge_referrals`（唯一：来源消息 + 单元编号；两个索引）、`knowledge_referral_suppressions`、`source_completion_receipts`（主键来源消息编号，外键 `messages.external_message_id` 级联删除）。不进入保留期清理。

## 4. 三项小修复与修订（已在上一份报告 §9 交代，此处只列当前状态）
- 占位符：`create`/`update`/`convert_candidate` 与 **启用** 都校验（`set_enabled` 启用前 `_validate_content`）；资料核实入库同样校验。
- INFO 日志：`configure_application_logging`；Bearer、`token=`、`secret=` 等统一走 `redact_text`。
- 生产只读核对（本次授权内完成）：
  - 生产知识 83 条，四个问答字段都没有「【待管理员确认】」，无需回填；
  - API 容器日志驱动 `json-file`，`LogConfig.Config` 为空，`/etc/docker/daemon.json` 也没有 `log-opts`，**没有日志滚动**。当前日志 96K、根分区剩余 37G。1.70.0 打开 INFO 后日志量会上升；建议另行决定是否在 `compose.yaml` 加 `logging.options.max-size/max-file`（运行配置改动，本次未做）。

## 5. 相对 Spec 的偏差与取舍（请重点看）

1. **资料核实卡片不读通知名称**：Spec 写「`_load_notification_names` 的触发条件加上有待接受的缺口」。实现里卡片由 worker 按转交记录组装，客人行固定为「客人问题待管家核实」，不读企业微信客人名或 CRM 备注；管家通过卡片链接看对话。这样卡片只依赖记录最小字段，也少一次外部调用。代价：卡片上看不到客人备注。
2. **补了原生接管复核**：Spec §2.4(1) 与 §2.6(2) 假设「过时复核与原生接管复核」已存在，实际 `_discard_stale_final` 只查新活动。已加 `_in_native_session`（取锁后、新活动前），否则主回复等待期间管家接入（不写员工消息）时旧结果仍会建任务。变异检查证明测试依赖它。
3. **为测试抽出模块级函数**：`_run_deferred_source`、`_send_referral_card`、`_fail_exhausted_referral_draft`，生产闭包调用同一函数，测试不复制装配逻辑。`HANDOFF_CARD_JOB_TYPE` 移到 `handoff_card.py`（会话层要用，避免反向依赖 `application`）；`FAQ_EXCLUDED_PATTERN` 改为公开名。
4. **清空多清两项**：除 Spec 列的字段外，还清 `normalized_question`（由客人问法派生）与冻结编号；因此清空后同类新问题可再转交（与 Spec「新来源的正常问题仍可创建转交」一致）。
5. **整理时无可用管家消息**：直接记「没找到答案」并换代，不入队、不调模型。
6. **冷却里的「仍待处理」包含整理中**。
7. **关闭允许在整理中进行**：之后的草稿写回因状态不是整理中被拒。
8. **`sensitive` 列目前恒为 False**：敏感单元本就不转交，列保留给 Spec 定义的结构，未做额外用途。
9. **门禁不覆盖客人转交说明**：门禁运行器直接调适配器与 `prepare_planned_reply`，不经过 `ConversationService`，所以门禁里的缺口仍显示现行未确认；转交说明、卡片与收据由集成与 PostgreSQL 测试覆盖。
10. 既有测试调整一处：`test_faq_candidate_is_returned_in_same_structured_guest_response` 原断言「系统提示里没有 `"canonical_question"`」，新输出结构 `gap_referrals` 有同名字段。改为断言候选正文（「标准问题20」）只在信封里、不在系统提示里，判别目标不变。

## 6. 验证

- 本地全量（含隔离 PostgreSQL 16 与 Chromium 浏览器）：门禁前 2838 passed / 15 skipped；按 §7.1、§7.2 修复后 **2855 passed / 15 skipped**（跳过的是需真实外部凭据的契约测试）；Ruff、Mypy 通过。
- 迁移：SQLite 建表；隔离 PostgreSQL `0034 → 0035 → 0034 → 0035`。
- 新增测试：
  - `tests/unit/test_sensitive_text.py` 21（中英凭据正反例、无值提问、房号金额地点不误伤、客诉旧格式结果不变）；
  - `tests/integration/test_knowledge_referrals.py` 35（转交创建与最小卡片载荷、卡片入队失败回未确认、不可转交四类、人工模式、冷却两种、抑制、引用标签、收据挡重放、无收据重算不重复、清空后不处理且发送处拦截、合并阶段收据只在终结时写、worker 卡片不混入原文、非待处理不发卡、按钮快照核对会话、最新 5 条与敏感排除、窗口外排除、换代/关闭/清空后旧写回全拒、同代次第二次写回被拒、模型失败交给重试、依据片段校验、入库五类拒绝且无孤立知识、入库只结束本条且删知识不受阻、清空字段与收据保留及新来源可转交）；
  - `tests/integration/test_card_action_two_phase.py` 13（CRM 备注处注入 `OperationalError`：普通卡片平台接入 0；非数据库故障兜底；三处取名调用方都传播；组卡/更新失败动作仍完成；转交清空、缺失、会话不符拒接入但能交还；入库或关闭后能交还；普通卡片内容对照）；
  - `tests/integration/test_referral_postgresql.py` 16（原生接管 ± 对照；等待中清空 × 成功/失败/软规划三个出口；锁顺序两种；窗口一平静/投诉；窗口二平静/投诉，真实 `Worker.run_once` → 取消 → `recover_stale` → `claim_next`；最终作业重放第二次助手不被调用且副作用、正文、队列载荷不变；草稿失败三条路径；入库行锁）；
  - `tests/browser/test_knowledge_referrals_page.py` 7（列表数量与回复条数、手机宽度无横向滚动、详情并列与范围无默认、无脚本直接提交三类被拒且保留输入、入库、整理与关闭）。
- 变异检查（每项改坏后对应测试必须失败，12/12 被拦下）：原生接管复核、主回复后来源清空、软规划后来源清空、收据读取、清空锁会话、超时草稿补偿、入库行锁（初版并发测试没拦下，已改为确定性的持锁等待测试）、取名数据库错误传播、发送处来源清空、入口来源清空、卡片会话核对、只转交未确认单元。
- 门禁基线预期变更：SC-国庆早餐（D-G8），必含改为「8:00」或未确认说法之一，禁「7:30」不变；`expectation_changes` 已记 1.70.0 条目。

## 7. 真实模型门禁（`REPLY_GATE_SCOPE=all`）

### 7.1 rc-1.70.0-1（`85c4ec7`）：不通过

首轮 132/154；退步 6 个：K-洗衣-X、K-用电-D、K-访客、MT-停车EN、SC-国庆早餐、U-充电桩；新纳入 E-地铁；安全类未通过 0。

为定位根因，用与门禁相同的方式在生产 API 容器临时目录里只读重跑这几个场景，并记录规划与主调用的模型原始 JSON（不写库、不发消息，结束即清理），再用本地探针把同样的模型输出分别喂给候选代码与 HEAD（1.69.1）代码对照：

| 场景 | 根因 | 处理 |
| --- | --- | --- |
| K-用电-D「插座是220V的吗」 | 新增的小句房号识别把「220V」当成 220 房间，映射不到 → 范围未绑定 → 整项未确认 | 缺陷。`item_coverage._ROOM_NUMBER` 排除紧跟字母与度量单位的数字，口径与 `_unmatched_room_header` 对齐；「room 401 available」仍识别 |
| K-访客「朋友晚上能来房间坐坐吗」 | 模型答 9033、相关 9032（安静时段）。F2 之后本项合法候选只来自本项检索，信封里仍能看到 9032；`verify_selected_evidence` 把「相关编号越界」当成整条选择无效，回退又认不出访客主题 | 缺陷。越界只看回答编号；本项候选之外的相关编号忽略 |
| K-洗衣-X「洗衣机要钱吗」 | 9017「免费使用」与 9018「每次10元」问法不同，按 §2.3 不是同一对象、不互判冲突，于是答「免费」。1.69.1 会回未确认。这是 Spec 已声明的上限被真实场景触发 | **用户 2026-10-08 决定**：模型选为回答或标为相关的条目参与否决——不同对象、在所问收费或钟点上与回答矛盾时回未确认，只否决不替换。只在问题主题认得出时比较、且只比较该主题该属性的分句（主题认不出时整段互比会把「安静时段 22:00」与「客厅 21:00」误判，1.69.0 门禁的既有用例拦住了这一点） |
| SC-国庆早餐 | 预期变更只放行了「暂时无法确认」「还没有确认」，实际未确认措辞是「尚未确认」，且路由为 unconfirmed 需要 `knowledge_gap: true` | 补齐预期（仍禁「7:30」） |
| MT-停车EN「How much is it?」 | 规划波动：重跑 3 次里 2 次判 unclear 并正确回答停车费，1 次判 stay_query 后只剩房价未覆盖说明；1.69.1 代码对 stay_query 同样失败（改问入住日期） | 未改代码，看重跑结果 |
| U-充电桩 | 模型波动：有时把 9009（停车总体说明）选为回答；1.69.1 代码对同样的选择结果相同；重跑 2 次都选「无」并正确回未确认 | 未改代码，看重跑结果 |

新增判别测试（改前失败、改后通过，已用临时还原验证）：`test_room_numbers_exclude_units`、`test_voltage_question_is_not_a_room_number`、`test_related_id_outside_item_candidates_does_not_void_a_legal_answer`、`test_model_flagged_entries_veto_contradicting_fee`（两种标注）、对照 `test_unflagged_other_object_is_still_not_a_conflict`；既有 `test_related_entries_do_not_create_time_conflicts` 保持通过。修复后本地全量 2850 passed / 15 skipped。

### 7.2 rc-1.70.0-2（`748c25b`）：不通过

首轮 135/154；退步 2 个：K-安静-C（新出现）、MT-停车EN；新纳入 E-地铁；安全类未通过 0。上一轮的 K-用电-D、K-访客、K-洗衣-X、SC-国庆早餐、U-充电桩 都已通过。

| 场景 | 根因（只读重跑 + 本地对照） | 处理 |
| --- | --- | --- |
| MT-停车EN | 两轮门禁 6/6 都是规划把「How much is it?」判成 stay_query。已有规则 `service_fee_followup`（追问上文是服务收费就不是房价）只关掉了日期追问与强制查价，逐项模式仍按规划标签要参考价，本项检索也只用「How much is it?」本身 | 缺陷：逐项模式也遵守这条规则——这类无房号、无日期的 stay_query 项按接上的话题当 static_fact 取证与检索。点名房号或带日期的仍是房价 |
| K-安静-C「你们有安静时段规定吗，我们想晚上在房间聚一下」 | 规划成两项时，模型给第二项选 [9032, 9033]；9032（安静时段，含「不接待聚会」）只在第一项的检索结果里，按 F2 判越界，第二项回未确认。这是 Spec §3.1 已列的「逐项检索召回下降」 | **用户 2026-10-08 决定**：同一轮另一项已核验通过的条目，模型又选它回答本项时算本项合法、正文不重复（`_shared_grounded`）。只复用全店通用、没有附加条件词的条目；为此 `KnowledgeSnippet` 新增 `conditional`（检索时按 `trigger_any` 标记）。房间专属与带条件的条目不跨项，B24-R3、G2 的风险不回来 |

新增判别测试（各自变异检查均被拦下）：`test_service_fee_followup_is_answered_as_static_fact_even_if_planned_as_stay`、`test_entry_verified_for_another_item_can_answer_this_item`、`test_room_specific_or_conditional_entries_are_not_shared`（两种对照）、`test_retrieval_marks_entries_with_trigger_conditions`。修复后本地全量 2855 passed / 15 skipped。

### 7.3 rc-1.70.0-3（`dca28e5`）：**通过**

- 首轮 138/154；退步无；新纳入不退步集合 E-地铁、SR-遗失；仍未通过的安全类场景无；运行出错 0。
- 规划 142 次：P50 738 ms、P95 1057 ms、最大 1346 ms；规划失败 3 次。首轮单场景 P50 2.4 秒、P95 3.9 秒。
- 提议新基线（`.stage/reply-gate-rc-1.70.0-3.baseline.json`，按惯例发布后随现场记录提交）：必过 138 / 已知未通过 16。
- 工作区源码、测试、脚本、迁移、`pyproject.toml` 与 `requirements.lock` 与 rc-1.70.0-3 逐文件哈希一致（0 差异）；之后只改了文档。正式提交与 rc-1.70.0-3 回复代码一致时，部署可用 `--skip-reply-gate` 复用本次结果（理由写入部署日志）。
- 门禁消耗：今天 3 轮全量门禁，另有 4 次只读调试（共约 20 个场景调用）。

## 7.4 收尾

- 版本 1.70.0、CHANGELOG、`docs/releases/1.70.0.md` 草稿；AOCI 认知已维护对齐（38 条，Verify/Check 通过）。
- 门禁候选标签 rc-1.70.0-1～3 只在本机，未推送；工作区改动全部未提交。

## 8. 未覆盖与风险

- 测试号真实收发未做：本次改变客人收件内容与员工卡片，按项目规则发布前需要做，需当次授权。
- CI 未跑（未推送）。
- 日志滚动未配置（§4）。
- 草稿与问题的对应、答案语义正确性只能靠管理员审核；依据片段校验只证明出自输入。
- 召回下降、调用次数增加、D-G8 误拦等 Spec §3.1 已列风险以门禁结果为准。

## 9. 请审查的问题

1. §7.2 的跨项复用（用户已决定）：只复用同轮已核验、全店通用、无附加条件词的条目，是否还有我没想到的跨项误用。
1. §7.1 的「模型标注参与否决」（用户已决定）实现口径：只在主题认得出时比较、只比较该主题该属性分句；两条都被选为回答的「一楼和二楼分别几点开」会回未确认，是否还有更好的区分依据。
1. §5 偏差 1（卡片不显示客人名与备注）是否可接受。
2. §5 偏差 2（新增原生接管复核）是否有我没想到的副作用：例如人工模式下管家已接入原生会话、但客人问独立低风险问题时，主回复结果现在会被丢弃（与 `process_recorded_message` 开头的同一判据一致）。
3. 收据写在 `_run_deferred_source` 统一出口，`DeferredRetryJobError` 等异常不写收据；是否还有正常返回但不应视为终结的分支。
4. `clear_test_customer_data` 新增会话行锁后，与 `merge` 等其他先锁客户再锁会话的路径是否一致。
