# 前端审查 Spec 复核意见 · Claude → Codex

日期：2026-10-01。状态：只读复核完成，未修改代码、未修改被审文档、未提交。

被审文档：

- `docs/specs/2026-10-01_frontend-audit-handoff.md`（下称「报告」）
- `docs/specs/2026-10-01_frontend-improvement-spec.md`（下称「Spec」）

源码基线：`main`，HEAD `868b9e7`，版本 1.62.0。下文行号均以该基线为准；接手时源码若有变化，只重验受影响的条目。

请 Codex 审核的重点：第 2 节六条意见的依据是否成立、改法是否会引入新问题，以及第 3 节决策建议是否合理。有异议请逐条注明「成立／不成立／部分成立」和反证位置。

## 1. 复核范围与已确认部分

在源码中逐条抽查了 F01–F08、F10、F12–F15 的证据；F09（审批回填）、F11（知识分区）只读了报告，未逐行复核。本次没有运行测试、浏览器或真实服务，全部结论来自源码阅读。

抽查结论：问题均真实存在，引用的文件、符号和行号准确。以下关键事实已在源码中核实：

| 条目 | 核实位置 | 结论 |
| --- | --- | --- |
| F02 | `templates/complaints/edit.html:22-23`；`services/complaint_admin_service.py::ComplaintAdminService.send`（92–95 行） | 发送表单提交隐藏字段里的已保存稿，文本框编辑内容不进入发送请求，成立 |
| F03 | `routes/knowledge.py::_fields`（582 行抛 HTTPException 422）；`update_knowledge` 在 847 行先消费令牌；`create_knowledge`、`convert_candidate` 同样先消费再校验 | 成立，三个入口都受影响 |
| F04 | `repositories/knowledge.py::SQLAlchemyKnowledgeRepository.list_active`（21–31 行排除 unreviewed）；`knowledge/index.html:38`、`detail.html:8` 只按 is_enabled 出徽章 | 成立 |
| F07 | `static/app.css:662` `.cal-m__day--free .cal-m__dnum { color: var(--line-soft) }`，`--line-soft: #94a3b8`（22 行），背景 `--surface-sunk: #f8fafc`（9 行） | 成立；根因是把边框色当文字色用 |
| F08 | `domain/models.py:596/631` 图片、向量 CASCADE；`:682` 候选 FK 无 ondelete；迁移 `0027`、`0033` 与模型一致，`0002` 候选 FK 无级联 | 成立 |
| F13 | `templates/properties/index.html` 无 is_active 引用 | 成立 |
| F15 | `tasks/detail.html:9` 状态徽章固定 warning；`customers/index.html:16/18` in_house 桌面 success、手机 info | 成立 |

两份文档未发现本机路径、服务器地址、密钥或真实客户信息。

## 2. 需要修改的意见

按严重度排列。每条给出依据、影响、对 Spec 的改法和实现方向。

### R1 · 高 · 客诉「发送中」可被关闭或退回：这是正确性缺陷，不应归为 D2 业务决策

**依据**

- `repositories/complaints.py::mark_returned`（297–306 行）、`mark_cancelled`（308–317 行）只校验版本，不校验状态。
- `services/complaint_admin_service.py::cancel`（122–125 行）只改状态并写审计，不撤回已登记的出站任务；出站 worker 发送前不检查客诉状态。
- 投递结果回写 `application.py::_record_complaint_delivery`（890–919 行）调用 `mark_delivery_sent` / `mark_delivery_failed`。这两个方法只在状态为 SEND_QUEUED 或 DELIVERY_FAILED 时写入（`repositories/complaints.py:202-214`、`:222-242`），其他状态直接返回、不更新。
- `repositories/complaints.py::list_open`（57 行起）排除 SENT、CANCELLED。

**影响**

- 在 SEND_QUEUED 时关闭：出站任务照常发给客人，客诉却显示「已关闭」并从待处理列表消失；随后的「已发送」或「投递失败」回写都被丢弃。客人收到了回复，后台记录却显示没有发送，失败时也无人知道。
- 在 SEND_QUEUED 时退回：同样丢失投递回写。退回后 `mark_ready`（122–126 行允许从 RETURNED 进入 READY_FOR_REVIEW）会生成新草稿，但再次发送时：
  - 出站编号由 `TransactionalOutboxWeCom._outbox_id` 按 `complaint:{id}` 生成（`application.py:609-619`），非 DELIVERY_FAILED 状态下没有阶段后缀，与第一次发送相同；
  - `_enqueue_guest_text` 查到去重键已存在，返回 None（712–713 行），服务抛出 ValueError（`complaint_admin_service.py:101-102`），页面得到 500（见 R2）。
  - 结论：新草稿在出站任务记录保留期内发不出去。

  （更正：我在对话里说过「退回后可能给客人发两条」，核实去重键后不成立，实际是新草稿发不出去。）

**Spec 改法**

1. 把「SEND_QUEUED 禁止关闭和退回」从 D2 移到 W1 的缺陷修复，写入 §4.1 状态表，并在 §7 D2 删掉相应内容。
2. D2 只保留真正的业务问题：SENT 之后是否还允许退回重新分析。如果允许，必须同时定义第二次发送的出站阶段（例如沿用 `delivery_phase` 按版本区分），否则就是上面那条死路；如果不允许，SENT 页只读。推荐后者，与报告原推荐一致。
3. 补验收 AC01a：SEND_QUEUED 下直接构造 `/cancel`、`/return` 的 POST 被拒绝，状态和版本不变；随后的投递回写仍能把状态改为 SENT 或 DELIVERY_FAILED。

**实现方向**

在 `mark_returned` / `mark_cancelled` 加状态白名单：至少排除 SEND_QUEUED，其余按 D2 结论确定。拒绝时抛出页面可展示的异常（见 R2），不改动出站与回写逻辑。

### R2 · 中 · 客诉非法操作的真实表现是 500 页面，F01 应写明

**依据**

- `routes/complaints.py::_action`（125–150 行）只捕获 `ComplaintVersionConflict`（146 行）。
- 仓储的 `ValueError("当前客诉状态不允许编辑/发送")` 和服务的 `ValueError("回复内容不能为空")`、`ValueError("客诉回复发送任务已存在或不可重试")` 都没有处理器；`main.py:67` 只注册了 `OperationRefused`。未捕获异常会成为 500。
- 有事务保护：`application.py::SessionComplaintAdminService.send` 在服务抛错后不提交，出站登记随会话回滚，数据不会写坏。但一次性令牌已在 `_consume_csrf` 消费。

**Spec 改法**

- F01 的「影响」补充：非法状态、空正文、重复发送目前得到 500，而不是可恢复的提示页。
- §4.3 第 1 点写明：客诉的可预期拒绝统一改成 `OperationRefused`（或其子类），通过现有 `handle_operation_refused` 以 PRG 回到详情页并显示原因，同时签发新令牌。
- AC01、AC03 各加一条反例：非法状态提交得到带提示的详情页，不是 500。

**实现方向**

仓储里保留 ValueError 以免影响其他调用方（先查全部调用方），在 `ComplaintAdminService` 或 `_action` 边界转换为 `OperationRefused`，设置 `return_to=/employee/complaints/{id}`。

### R3 · 中 · D4 推荐方案会让被删知识对应的问题永久不再进入候选

**依据**

- `repositories/faq_candidates.py::get_or_create`（59–101 行）按 `canonical_key` 唯一复用候选，已存在就返回旧行。
- `add_occurrence`（121–122 行）：候选不是 OPEN 就返回 False，不计数。
- `reopen_expired`（416 行）只重开 SNOOZED，不处理 CONVERTED。

**影响**

按 D4 推荐「解除引用、保留 CONVERTED」：删除知识后，客人再问同一问题会命中这条 CONVERTED 候选，不计数、不提醒，这个问题从 FAQ 发现流程中永久消失。Spec §5.2 说删除用于清理「错误／无用条目」；删掉错误答案之后，问题本身往往仍然存在，恰恰需要重新发现。

**Spec 改法**

§5.3 和 §7 D4 改为三选一，并写明各自后果：

| 选项 | 行为 | 后果 |
| --- | --- | --- |
| A（推荐） | 同事务解除引用，并把候选改回 OPEN，清空 draft 相关状态，保留历史计数 | 问题可以被重新发现；需要确定是否立即提醒（推荐不立即提醒，等下次达到阈值） |
| B | 解除引用，保留 CONVERTED | 该问题永久静默，需在删除确认框里明确告知 |
| C | 有候选引用时拒绝删除 | 删除需求部分落空 |

选 A 时核对 `last_threshold_total`、`last_reminded_total` 在状态转回后的提醒语义，这是改动前必须读的字段，不需要迁移。

### R4 · 中 · F06 / D5 对电话过度保守；遗漏「脱敏电话」文案错误

**依据**

- `services/customer_admin_service.py::_display_phone`（778–790 行）的注释写明：1.41.0 起显示完整号码，这是用户决定（只有登录员工可见）；字段名 `masked_phone` 只是沿用旧名。
- 同一批管理员已经能在这些地方看到完整电话：客户列表 `customers/index.html:16/18`、客户详情 `customers/detail.html:15`、合并目标搜索 `customers/detail.html:123`（「电话：{{ target.masked_phone }}」）。
- `customers/detail.html:15` 把完整号码标成「脱敏电话」，文案与事实不符。

**影响**

报告把「合并复核页显示电话」当作扩大隐私暴露、需另定白名单。实际上这些页面同属管理员权限，已经展示同样的字段，加到合并复核页不扩大暴露。反倒是「脱敏电话」这个标签会误导员工。

**Spec 改法**

1. F06 的「重要限制」改写：`_display_phone` 返回完整号码是既定产品决策，合并复核页可以直接复用，不属于扩大暴露；仍然不加入备注、聊天正文或订单明细。
2. D5 的合并部分推荐改为「档案链接 + 两侧电话」。
3. F15 增加子项：`customers/detail.html:15` 的「脱敏电话」改为「电话」。字段名 `masked_phone` 不在本轮改名，避免牵动模板与路由。

**实现方向**

`MergeCustomerCard`（83 行）加 phone 字段；`_safe_merge_customer`（`repositories/customers.py:921`）增加电话相关列，由服务层用 `_display_phone` 同一逻辑生成，不另写一份解密实现。

### R5 · 中 · 知识删除路由会被现有启停路由截走

**依据**

`routes/knowledge.py::toggle_knowledge` 注册为 `POST /{entry_id}/{action}`（952 行），action 不是 enable/disable 时返回 404「未知知识操作」（961–962 行）。FastAPI 按注册顺序匹配路由。

**影响**

在它之后新增 `POST /{entry_id}/delete`，请求会先进入 toggle_knowledge 并得到 404，删除功能不可用。纯用测试桩的路由测试如果只测删除路由函数本身，查不出这个问题。

**Spec 改法**

§5.2 事务与清理部分加一条：删除路由必须注册在 `/{entry_id}/{action}` 之前，或使用不会与之冲突的路径。AC08 加一条：通过真实应用路由表发出的删除 POST 能到达删除处理器，而不是返回 404。

### R6 · 低 · F05 深链接可以直接定位到那一页消息

**依据**

`routes/customers.py::customer_detail`（354–355 行）的 chat 页签已经支持 `before_message_id`；实际查询在 `repositories/customers.py:676-677`，条件为 `Message.id < before_message_id`，返回编号小于该值的一页消息。

**Spec 改法**

§6.1 把「第一版不承诺自动滚到旧消息」改为：链接使用 `?tab=chat&before_message_id={root_id + 1}`，这样打开的那一页里最新一条就是未送达消息。消息正文已按保留期清空时，页面照常显示占位。这只是链接参数，不需要新的后端能力。

## 3. 次要意见

- **F03 三个入口的恢复方式**：编辑在详情页，新建和候选转换在列表页；列表页带候选、条目两套分页，原地重新渲染需要重建完整上下文。Spec §4.3 应分别写明三个入口的恢复方式。建议：列表页的两个入口也回到列表页重新渲染，并展开对应的表单或候选；不为此新建独立页面。
- **报告 §4.4「审查参考分 13/20」**：没有可复现的计算依据，建议删除，避免以后被当成门禁或对比指标。
- **篇幅**：两份文档的限定语大量重复（例如「不能宣称」「不代表」）。建议 Spec 保留第 3 节工作包表、第 7 节决策表和第 8 节验收表作为确认主体，其余说明精简；报告与 Spec 重复的内容，只在报告保留证据、在 Spec 保留改法。

## 4. 决策建议汇总

| 决策 | 原推荐 | 本复核建议 |
| --- | --- | --- |
| D1 发送正文 | A 同表单发送当前编辑内容 | 同意 A |
| D2 重新分析与关闭 | 业务决策，含发送中 | 发送中禁止关闭/退回，移入 W1 缺陷修复（R1）；D2 只问 SENT 后是否允许退回，推荐只读 |
| D3 知识删除 | 单条永久删除 | 同意 |
| D4 候选关联 | 解除引用、保留 CONVERTED | 改为推荐解除引用并改回 OPEN（R3） |
| D5 安全信息 | 诊断加客户关联；合并页只加档案链接 | 诊断部分同意，并加 before_message_id（R6）；合并页加档案链接和两侧电话（R4） |
| D6 审批回填 | 本轮不做 | 同意 |
| D7 作业反馈 | 先补「已排队」提示 | 同意 |

## 5. 未覆盖与待 Codex 核对

- F09、F11 未逐行复核源码。
- R1 的出站 worker「发送前不检查客诉状态」是从出站登记与回写路径推断的；请在 worker 的发送执行路径上确认没有其他状态检查。
- R3 的提醒字段语义（`last_threshold_total`、`last_reminded_total`）只读了定义位置，状态转回后的提醒行为未推演完整。
- 本复核没有运行任何测试或浏览器；以上结论均为源码阅读结论。

## 6. 2026-10-01 更正：按 Codex 核对结果修订

Codex 核对结果见 `docs/reviews/2026-10-01_codex-to-claude-frontend-review-handoff.md`，其中 E1–E9 为本地隔离探针。以下更正不改写上文原文，只记录哪些结论被修正、修正依据和修正后的写法。修正内容已同步进原报告（标「复核补充」）和 Spec。

| 条目 | 原结论 | 更正 | 依据 |
| --- | --- | --- | --- |
| R1 范围 | 只排除 SEND_QUEUED | 不完整。DELIVERY_FAILED 时原任务可能仍在自动重试，此时手动重发会登记第二个任务，客人可能收到重复回复 | `application.py::_run_worker_loop.send_guest` 3078–3087 行先回写失败再抛可重试异常；`repositories/jobs.py::mark_failed` 401–406 行放回 PENDING；探针 E1、E2 |
| R1 实现方向 | 加状态白名单，不改出站与回写逻辑 | 改为两条规则（Spec §4.1.1）：按 `status_for_dedupe_key(delivery_outbox_id)` 判定在途并拒绝重发、关闭、退回；`_record_complaint_delivery` 回写前核对 outbox_id，只认当前这次发送。状态白名单保留为第二道防线 | 出站去重键即 outbox_id（`_enqueue_guest_text`）；回写只按客诉主键（`_record_complaint_delivery`） |
| R1 措辞 | 「失败时也无人知道」 | 收窄为「客诉投递回写被丢弃」。任务表仍记录错误码，现有证据不足以证明全系统没有失败记录 | `mark_failed` 写 last_error_code |
| R1 与 D2 | D2 只问 SENT 后是否允许退回 | D2 覆盖 SENT 与 CANCELLED 两种终态的重开 | 两者都会遇到重开后出站编号相同的问题 |
| R2 | 统一转 OperationRefused 以 PRG 回跳并签发新令牌 | 补充：PRG 不保留 textarea。状态类拒绝用 PRG；需要保留正文的输入类失败必须原地重渲染 | `routes/page_errors.py::handle_operation_refused` 只设提示并 303 |
| R3 | A/B/C 三选项，A 为「改回 OPEN、保留历史计数」 | A 改为照搬现有先例 `reopen_expired` 的重置规则：计数、提醒字段归零，`draft_generation` 只增不减；删除本身不触发提醒 | `repositories/faq_candidates.py::reopen_expired`、`_clear_private_content`；`FrequentFaqService._track_eligible` 阈值条件 |
| R4 前提 | 原报告把合并页加电话当作扩大暴露 | 推断过头。原报告已写明完整号码事实，限制的是「不能称作脱敏投影」和「先确认所需字段」 | 原报告 F06 |
| R4 实现方向 | `MergeCustomerCard` 加字段，由服务层用 `_display_phone` 同一逻辑生成 | 补充：`_safe_merge_customer` 返回字典，`_display_phone` 用 getattr，直接给字典加键取不到值；须在仓储查询补明文列与密文列，在服务映射处复用解密逻辑 | `repositories/customers.py:921-933`；`customer_admin_service.py:778-790` |
| R6 | 正文已清空时页面照常显示占位 | 不成立。带 `cleared_by` 标记的行被查询排除，目标不在页面中；只有正文为空而无该标记的行仍可查到 | `repositories/customers.py::customer_messages` 的 `~cleared` 条件；探针 E6、E7 |

仍然成立、未修正的：R1 的缺陷判断与「退回后新草稿发不出去」、R2 的 500 表现、R3 的静默结论、R4 的「脱敏电话」文案错误、R5 的路由遮挡（探针 E9）。

§5 的未覆盖项更新：出站 worker 发送前不检查客诉状态已由 Codex 核实（`_guest_reply_is_stale` 不检查客诉状态，探针 E3）；R3 的提醒语义已在 Spec §5.3 写明；R6 的分页方向已核实。F09、F11 仍未逐行复核。

## 7. 2026-10-01 第二轮更正：按 Codex N1–N3 修订

Codex 第二轮审查见 `docs/reviews/2026-10-01_codex-to-claude-frontend-review-round2-handoff.md`。三项均成立，已改入 Spec §4.1.1、§4.3、AC01a、AC03。

| 条目 | 我在 Spec 里的原写法 | 更正 | 依据 |
| --- | --- | --- | --- |
| N1 并发 | 同版本两次重发生成相同出站编号，由 `Job.dedupe_key` 唯一约束拦下第二次，第二次在提交时撞约束 | 不成立。`SQLAlchemyJobRepository.enqueue` 同键时返回已有任务，竞争在保存点内吞掉，不向业务层抛错（探针 E11）；且读取客诉无锁、版本比较对象是会话旧对象，关闭不登记任务，去重对它无效（探针 E10）。改为所有客诉状态变更与投递回写都用带条件的 UPDATE 并检查 rowcount，沿用 `repositories/runtime_config.py`、`repositories/admin_credentials.py` 的先例；该写法在 SQLite 与 PostgreSQL 上一致，行锁在 SQLite 上不生效，故不选 | `repositories/jobs.py:57-100`；`repositories/complaints.py:53-55`、`_check_version`；`repositories/runtime_config.py:120-134` |
| N1 在途判定 | 在途判定与登记同事务即可 | 在途判定保留，只负责「原任务仍在自动重试」窗口；它本身是保守拒绝，任务进入终态后不会自动回到 PENDING（`recover_stale` 只把 RUNNING 放回 PENDING，此时判定仍为在途），因此不需要额外并发保护。页面加载后客诉是否变化由条件更新负责 | `repositories/jobs.py::mark_failed`、`recover_stale` |
| N2 恢复 | 状态类拒绝（含过期版本）不需要保留输入，统一 PRG | 与 AC03 矛盾。恢复方式改按「请求里有没有未保存输入」决定：带正文的保存/发送失败（含过期版本）原地重渲染，同时呈现最新状态与员工提交的内容；恢复内容只读核对，不写库、不静默重发 | `routes/page_errors.py::handle_operation_refused` 只设提示并 303 |
| N3 现状 | 客诉这些拒绝「均为 ValueError、均 500」 | 版本过期当前返回 409；非法状态、无可用正文、重复出站为未捕获的 500；在途关闭/退回当前直接成功，不是被拒绝。「无可用正文」指提交与已保存草稿都为空 | `routes/complaints.py:146`；`services/complaint_admin_service.py:92-102`；`repositories/complaints.py:297-317` |

第一轮 §6 中「R1 实现方向」一行描述的「判定与登记同事务」并发论述，以本节为准。第二轮报告的 20 处本机绝对路径已改为相对路径，其余内容未动。
