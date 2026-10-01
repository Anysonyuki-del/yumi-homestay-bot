# 前端操作流程补齐 · 实施交接与审查请求 · Claude → Codex

日期：2026-10-01。状态：本地实现与验证完成，**未提交、未推送、未部署**。

请 Codex 做只读代码审查：按第 5 节的重点逐项核对实现是否正确、有无遗漏的调用方或竞争窗口，每条结论给出「成立／不成立／部分成立」和文件、符号依据。本次审查授权只到读代码、跑本地测试；不修改业务代码、不提交、不调用真实 DeepSeek／百居易／企业微信。

## 1. 基线、范围与依据

- 源码基线：`main`，HEAD `868b9e77c2dc76ec4d80de70dd7d3794cbe0b4cc`，版本 1.62.0。全部改动都在工作区，未入库。
- 依据：`docs/specs/2026-10-01_frontend-improvement-spec.md`。三段确认记录在 §7.1，实施记录与偏差在 §11。前序评审：`docs/specs/2026-10-01_frontend-audit-handoff.md`、`docs/reviews/2026-10-01_frontend-spec-review-claude.md`、`docs/reviews/2026-10-01_codex-to-claude-frontend-review-handoff.md`、`docs/reviews/2026-10-01_codex-to-claude-frontend-review-round2-handoff.md`。
- 用户确认的范围：W1、W2、W3、W4、F12、W6；W5（F09 审批回填）不做。
- 用户确认的决策：
  - D1-A：同一表单发送回复框的当前内容；
  - D2：SENT、CANCELLED 只读；
  - D3：单条永久删除；
  - D4-A：删除知识时，候选改回 OPEN 并从零计数；
  - D5：诊断页链接带消息游标，合并页显示电话和档案链接；
  - D6：本轮不做；
  - D7：加强版，即「已排队」提示加最近一次手动整理的作业状态。
- 改动规模：39 个已跟踪文件，+2036／−352。另有新增文件：
  - 模板 `templates/knowledge/entry_state.html`；
  - 测试 `test_complaint_delivery_guards.py`、`test_complaint_delivery_postgresql.py`、`test_knowledge_delete_postgresql.py`。

## 2. 改动清单（文件、符号、改前改后）

路径省略前缀 `src/homestay_bot/`。

### 2.1 W1 客诉（F01、F02，Spec §4.1、§4.1.1、§4.2、§4.3）

| 文件与符号 | 改前 | 改后 |
| --- | --- | --- |
| `repositories/complaints.py::_transition`（新增） | 各方法先读对象、比较版本、再赋值、flush | 一条带条件的 `UPDATE … WHERE id AND status IN (…) [AND version = :v] [AND delivery_outbox_id = :o] [AND delivery_external_message_id = :e]`，SET 里 `version + 1`；检查 rowcount；成功后 `session.get(..., populate_existing=True)` 刷新缓存对象 |
| `_refuse`（新增） | — | 条件更新没改到行时重新读取：不存在抛 LookupError，版本不符抛 ComplaintVersionConflict，否则抛 ComplaintStateRefused |
| `update_draft`、`mark_send_queued`（新增 `draft` 参数）、`mark_returned`、`mark_cancelled` | 只校验版本；退回、关闭没有状态白名单 | 全部改为条件更新；状态集合抽成模块常量 `EDITABLE/SENDABLE/RETURNABLE/CANCELLABLE_STATUSES`，页面与服务共用 |
| `mark_delivery_sent`、`mark_delivery_failed`（新增 `outbox_id` 参数） | 只按客诉主键回写，不区分是哪次发送 | 给出 outbox_id 时，条件里带 `delivery_outbox_id = :o`，旧任务的迟到结果改不动新尝试 |
| `mark_delivery_failed_by_external_message_id`、`mark_delivery_failed_by_outbox_id` | 读出再改 | 先查编号再条件更新；前者语义保持：找不到客诉返回 None，找到但状态不符原样返回 |
| `ComplaintVersionConflict` | `ValueError` | `OperationRefused, ValueError`（文案给员工看，按 ValueError 捕获的调用方不受影响）；新增 `ComplaintStateRefused` |
| `services/complaint_admin_service.py::_delivery_in_flight`（新增） | — | `delivery_outbox_id` 非空，且 `status_for_dedupe_key(outbox_id)` 为 PENDING 或 RUNNING，即为在途 |
| `_actions`（新增）、`get_detail` | — | 返回页面按钮资格 `actions` 与 `delivery_in_flight` |
| `send` | 提交为空时沿用已保存草稿；先登记任务，再由仓储做非原子的状态检查 | 版本、在途、状态先做预检（给出可读原因）；发送的是本次提交的正文，为空拒绝（422）；先登记任务，再条件更新，更新失败时事务回滚，任务一并撤销 |
| `return_for_analysis`、`cancel` | 无在途判定 | 先 `_refuse_in_flight` |
| `application.py::_record_complaint_delivery` 及 `_run_worker_loop.send_guest` 三处调用 | 无 outbox_id | 传 `payload["outbox_id"]` |
| `routes/complaints.py::_action`、`_render_detail`（新增）、`complaint_send` | 只捕获版本冲突，其余 ValueError 变成 500 | 保存、发送失败时原地重渲染（409／422），带最新状态和员工提交的原文；退回、关闭失败时转 OperationRefused，PRG 回详情页；发送不带 `confirmed=1` 时只渲染确认面板，不登记任务（无脚本时的原生确认） |
| `templates/complaints/edit.html` | 保存表单与发送表单分开，发送表单里藏一份旧草稿 | 单一表单，靠 `formaction` 区分保存与发送；按 `actions` 显示按钮；确认框里带 `{draft}`；风险等级与消息来源显示中文，并显示消息时间 |
| `static/admin.js::fillConfirmPlaceholders`、form 确认处理 | — | `{draft}` 占位符；确认通过后把提交按钮的值改为 `data-confirm-accept-value` |
| `web.py` | MessageOrigin 没有中文映射 | 新增 MessageOrigin 映射与 `complaint_risk_zh` 过滤器，未知值显示「待核实」 |

### 2.2 W1 知识表单与 W2 知识管理（F03、F04、F08、F10、F11）

| 文件与符号 | 改前 | 改后 |
| --- | --- | --- |
| `routes/knowledge.py::KnowledgeFormError`（新增，OperationRefused，422） | `_fields`、`_validate_scope` 抛 HTTPException(422) JSON | 抛 KnowledgeFormError；页面请求原地重渲染，接口请求仍得到 422 JSON（`_form_error_response`） |
| `knowledge_index`、`_render_index`（新增） | 候选与现有知识在同一长页，两组都加载 | 新增 `view=entries|candidates` 分区，默认 entries，只加载当前分区；普通员工看不到候选；`failed_form` 用于回填出错的新建表单或候选表单 |
| `knowledge_detail`、`_render_detail`（新增） | 返回按钮固定回列表 | 接收 `return_to`，经 `safe_return_path` 校验；编辑失败时用 `form_entry` 回填提交值 |
| `_submitted_form`、`_index_params`（新增） | — | 回填对象；从 return_to 还原列表的分页、筛选和分区参数，并逐项收窄 |
| `create_knowledge`、`convert_candidate`、`update_knowledge` | 先消费令牌，再在 `_fields` 抛 422 | 捕获 KnowledgeFormError，按入口原地恢复（新建、转换回列表页，编辑回详情页） |
| `delete_knowledge`（新增路由，注册在 `/{entry_id}/{action}` 之前） | 没有删除 | 管理员、一次性令牌；成功后 `set_page_notice` 并回到来源；从详情页删除时改回列表 |
| `KnowledgeAdminService.delete_entry`（新增）＋ Port ＋ `application.py::SessionKnowledgeAdminService.delete_entry` | — | 先 `with_for_update` 锁条目；读配图 file_id；有候选时先 `reopen_after_entry_deleted` 并写候选审计；显式删除配图与向量，再删条目；有文件时登记 `ATTACHMENT_CLEANUP_JOB_TYPE`，去重键为 `knowledge-entry-cleanup:{id}`；写 `knowledge.delete` 审计（记编号与配图数）；全部同一事务 |
| `repositories/faq_candidates.py::reopen_after_entry_deleted`（新增） | — | OPEN、解除引用；计数、提醒字段按 `reopen_expired` 先例归零；调用 `_clear_private_content`（draft_generation 加一）；删除出现明细 |
| `templates/knowledge/index.html`、`detail.html`、`entry_state.html`（新增） | 只有「已启用／已停用」 | 启停与审核范围分开显示（「已启用 · 范围待审核，暂不参与回答」）；显示指定房间、有效期（按所问日期判断）、条件词；页签用现有 `.tab-nav`；详情链接带 return_to；删除按钮 |

### 2.3 W3 诊断（F05）

| 文件与符号 | 改后 |
| --- | --- |
| `repositories/admin_diagnostics.py::DeliveryChain` | 新增 `customer_id: int | None = None` |
| `delivery_failure_rollup` | 外连接 Conversation、Customer，`coalesce(merged_into_customer_id, customer_id)` 跟到合并目标 |
| `_roll_up_delivery_chains` | 取链根消息的 customer_id |
| `templates/admin/diagnostics.html` | 新增「客人对话」列，链接为 `/employee/customers/{id}?tab=chat&before_message_id={root_id+1}`；没有关联时显示「未关联客户」；告警文案改为先点「查看对话」 |

### 2.4 W4 客户、房源与 F12

| 文件与符号 | 改后 |
| --- | --- |
| `repositories/customers.py::_safe_merge_customer` | 增加 `phone`、`phone_ciphertext` 两列（仍不查 note） |
| `services/customer_admin_service.py::MergeCustomerCard.phone`、`get_merge_detail`、`_merge_card` | 用 `_display_phone(SimpleNamespace(**dict))` 生成展示值，卡片里没有密文 |
| `templates/customers/merge.html` | 两侧显示电话和「查看完整档案」（带 return_to 回到本复核页） |
| `routes/customers.py::customer_index` | 新增 `current_view`，列表链接带上它 |
| `customer_detail` | 接收 `return_to`；读取 `pop_page_notice` |
| `_customer_redirect` | 改为 async，读取框架已解析的表单里的 return_to 并带回；8 个写路由都改为 `await _customer_redirect(request, …)` |
| `refresh_customer_context` | 冷却时 `set_page_error` 后 PRG，不再返回 409 JSON；成功后 `set_page_notice("已排队…")` |
| `templates/customers/detail.html` | 页签、对话翻页、全部写表单、合并目标搜索、服务记录里的任务链接与批量表单都带 return_to；返回按钮按来源显示「返回合并复核」或「返回客户列表」；「脱敏电话」改为「电话」；记忆页签显示最近一次手动整理的状态 |
| `repositories/customers.py::latest_context_refresh_job`（新增）＋ Port；`get_detail` 只在 memory 页签调用 | 只选 status、created_at、updated_at；按 `customer-context-refresh:{id}:` 前缀识别手动作业 |
| `templates/tasks/detail.html` | 返回按钮按来源显示文案；状态徽章随状态变色 |
| `routes/properties.py::property_index` | 新增 `active=active|inactive` 筛选，默认全部，在内存里过滤 |
| `templates/properties/index.html` | 桌面与手机都显示「已停用」；新增筛选表单 |
| `templates/tasks/index.html`、`static/admin.js` 选择逻辑 | 操作栏新增全选、清空、`已选 N 项`（仅在脚本可用时显示）；两个全选入口共用状态；`selectedTaskIds` 按编号去重；在捕获阶段拦截零选择的提交 |
| `templates/customers/index.html` | 手机端 in_house 徽章颜色与桌面一致 |

### 2.5 W6 样式（F07）

`static/app.css`：`.cal-m__day--free .cal-m__dnum` 的颜色由 `--line-soft` 改为 `--muted`。另新增 `.entry-state`、`.selection-bar`，以及 `.selection-bar[hidden]`（`display:flex` 会盖过 hidden 属性）。

## 3. 与 Spec 的偏差

摘自 Spec §11.1，请一并审查是否合理：

1. **DELIVERY_FAILED 不允许退回。** 首次发送不带阶段后缀，已发送过再退回，新草稿会撞上去重键。
2. **回复框为空直接拒绝**，不再沿用已保存草稿。
3. **无脚本时由服务端出确认步骤。** 接口调用同样需要 `confirmed=1`，这是对程序化调用方的行为变化；目前已知的调用方只有页面表单。
4. **列表页的删除确认不显示配图张数**，详情页显示。
5. **房源详情页不接收来源。**
6. **客户来源通过统一跳转函数读取已解析的表单**，没有逐个路由加参数。
7. **`mark_delivery_failed_by_outbox_id` 当前没有调用方**，仍按条件更新改写。

D4-A 的计数规则与 Codex 第一轮建议不同：Codex 建议保留历史计数，这里归零。理由见 Spec §5.3 字段表：照搬现有的 `reopen_expired` 先例。

## 4. 测试改动与验证证据

### 4.1 新增测试

- **`tests/integration/test_complaint_delivery_guards.py`**：真实 SessionComplaintAdminService、文件型 SQLite、请求与回写各用独立 Session。覆盖：
  - 旧版本关闭不能覆盖已提交的发送（E10 场景）；
  - 同版本保存与发送交错；
  - 自动重试窗口内拒绝重发、关闭、退回；
  - 原任务终态失败后只允许一次重试；
  - 旧任务迟到的回写被忽略；
  - SENT、SEND_QUEUED 只读；
  - 路由层：确认步骤后发送的是编辑后的正文；过期保存时原地重渲染并保留正文；关闭被拒时 PRG。
- **`tests/integration/test_complaint_delivery_postgresql.py`**：复用上面 6 条交错用例；另加 2 条用 `asyncio.gather` 真正并发提交的用例：同版本两次发送；发送与关闭。
- **`tests/integration/test_knowledge_delete_postgresql.py`**：外键顺序、候选改回待处理、清理任务、审计。
- **知识**：
  - `test_knowledge_routes.py`：分区、员工权限、非法分区返回 422、三个入口的出错恢复、接口仍是 422 JSON、删除路由不被截走、来源回跳、待审核条目标识；
  - `test_knowledge_image_admin.py`：真实会话删除与清理 handler 执行；注入失败时整笔回滚、文件保留；删除后同一问题能重新计数。
- **诊断**：`test_admin_diagnostics_repository.py`（客户关联、空关联、合并跟随，不含正文与外部身份）；`test_admin_dashboard_routes.py`（链接）。
- **客户**：
  - `test_customer_routes.py`：来源回跳、合并复核来源、重新整理提示与冷却、徽章颜色；
  - `test_customer_repository.py`：作业状态；
  - `test_customer_admin_service.py`：电话解密且卡片不带密文。
- **其他**：`test_property_routes.py`（停用标识与筛选）、`test_task_routes.py`（状态颜色）、`test_template_helpers.py`（中文映射）。
- **浏览器** `test_admin_interactions.py`：
  - 用真实模板渲染客诉页：确认框显示正文；取消确认后保留编辑内容；保存不弹发送确认。
  - 经真实路由渲染任务列表：手机全选与计数、两个全选入口同步、零选择拦截。
  - 空闲日期对比度：按计算后的颜色求值；修改前测得 2.36，失败；修改后通过。

### 4.2 改了原有断言的测试

这些测试按旧规则写死了断言，请重点确认改动没有放松应有的约束：

| 测试 | 改动 | 依据 |
| --- | --- | --- |
| `test_complaint_repository.py::test_complaint_delivery_state_tracks_queue_failure_and_success` | sent_at 统一时区后再比较 | 条件更新后会重新读取，SQLite 读回的时间不带时区 |
| `test_complaint_repository.py::test_complaint_page_uses_shell_and_safe_editing_controls` | 替身补上 enum 状态与 actions；发送改为断言 formaction 和 `{draft}` | D1-A 单表单 |
| `test_application.py::test_complaint_delivery_callback_updates_real_result` | 替身方法接受 `outbox_id` | 新增参数；断言不变 |
| `test_knowledge_routes.py`：原 `test_knowledge_index_orders_filters_candidates_and_entries` 与分页测试 | 改为断言分区行为 | F11 与 AC11 明确要求替换旧的顺序断言 |
| `test_admin_dashboard_routes.py::test_the_delivery_board_never_renders_message_content` | 字段白名单加入 `customer_id` | D5 |
| `test_customer_repository.py::test_merge_detail_returns_only_safe_association_counts` | 白名单加入电话两列；检查前先取出 phone_ciphertext；放开 SQL 不得查 phone_ciphertext 的断言，保留不得查 note | D5；密文只在仓储与服务之间传递 |
| `test_customer_admin_service.py::test_merge_detail_returns_dedicated_safe_cards` | 卡片加入 phone；删去「repr 里不含 phone」 | D5 |
| `test_customer_routes.py` 合并复核测试与第 413 行附近 | 合并页由「不得出现号码」改为「显示号码，不出现备注和密文」 | D5 |
| `test_customer_chat_history.py` 两条 | 链接格式允许多出 return_to | F10 |

### 4.3 运行结果

本次环境：Python `.venv`，本机临时 PostgreSQL 16。用后已删除。

- `ruff check .`、`mypy`（147 个源文件）、`git diff --check`：全部通过。
- 本地全量（设置 `YUMI_TEST_POSTGRES_URL` 指向本机隔离库 `yumi_test_retention`，并已迁移到 0034）：**2343 passed、15 skipped、6 warnings，退出码 0**。跳过项全部是需要真实 DeepSeek、百居易、企业微信的契约测试。
- 第一次全量（未设 PostgreSQL）有 3 条失败：两条对话链接格式、一条 CSS 注释里的字面色值触发令牌扫描。已修正，见 4.2，最终全量已覆盖。
- 真实模型门禁：未改动 `scripts/release/reply_gate.sh::REPLY_PATHS` 中任何文件，没有运行。

复现 PostgreSQL 用例：按 `tests/integration/test_retention_postgresql.py::validated_test_url` 的要求，准备一个本机回环地址上的库 `yumi_test_retention`，角色为 `yumi_test`，用 Alembic 迁移到 head。然后：

```sh
YUMI_TEST_POSTGRES_URL='postgresql+asyncpg://yumi_test:<合成密码>@127.0.0.1:<端口>/yumi_test_retention' \
  RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q \
  tests/integration/test_complaint_delivery_postgresql.py tests/integration/test_knowledge_delete_postgresql.py
```

## 5. 请重点审查

1. **`_transition` 与 ORM 会话的交互。**
   - 服务层 `get_detail` 先把 review 读进了身份映射，之后 `_transition` 用 `synchronize_session=False` 的 Core UPDATE。
   - 成功后用 `populate_existing` 刷新缓存对象；失败时 `_refuse` 也会刷新。
   - 请确认同一会话里没有对 ComplaintReview 的未刷新 ORM 修改会在之后的 flush 里覆盖条件更新的结果。重点看 `ComplaintAdminService.send` 不再设置 `review.draft`，以及 `mark_ready` 仍走旧写法。
2. **在途判定的窗口。** 依据是任务进入 COMPLETED／FAILED 后不会自动回到 PENDING；只有 `recover_stale` 会把 RUNNING 放回 PENDING。请核查有没有别的路径会把终态任务重新排队，或者在 worker 的 `send_guest` 已发出请求、`mark_failed` 尚未执行时，判定是否可能看到非在途状态。
3. **回写归属。**
   - `_record_complaint_delivery` 在 payload 没有 outbox_id 时退回不带条件。请确认所有客诉出站任务的 payload 都带 outbox_id（`_enqueue_guest_text`）。
   - 另外，superseded_before_send 分支现在也只回写到当前这次发送。
4. **`_customer_redirect` 读取 `await request.form()`。** 依赖 Starlette 缓存已解析的表单。请确认在 FastAPI 的 Form 参数路径下不会二次读取请求体，并且对 multipart 与 urlencoded 都成立。
5. **`admin.js` 零选择拦截。** 它在捕获阶段对 submit 调用 `stopImmediatePropagation`。请确认不会破坏同一表单上的 dirtyForms、提交中状态和手输确认流程，并且没有带 `task_ids` 的表单需要合法地零选择提交。
6. **知识删除与上传的竞争。** PostgreSQL 上靠 `with_for_update` 串行；SQLite 上这个锁不起作用，这一点只有推理，没有测试。另外请看 `session.delete(entry)` 在已经用 Core 删除配图之后，是否会因 ORM 关系去加载或置空子行。
7. **`_index_params`** 从 return_to 还原参数时，是否有能把页面渲染成异常状态的输入。
8. **诊断的 `coalesce`。** 合并链超过一层时会落到一个已合并客户，页面打开是 404，没有误跳到别的客户。请确认这样处理可以接受。
9. **D4-A** 归零规则与提醒冷却。删除前若刚提醒过，归零后会不会在短时间内再次提醒。
10. **第 3 节偏差 3**：接口调用发送客诉现在需要 `confirmed=1`。

## 6. 未覆盖

- 生产页面登录验收、测试号真实收发、部署后运行态均未做。
- 在真实浏览器里点完整工作流：只覆盖了确认框、选择栏和对比度，其余页面行为由 TestClient 覆盖。
- W5（F09）未实施；房源详情页的来源保留未做（偏差 5）。
- 版本号、CHANGELOG、`docs/releases/` 发布记录还没写，等用户授权发布时一并处理。


## 7. 2026-10-01 更正：Codex 审查 M1–M4 后的修复

Codex 审查（`docs/reviews/2026-10-01_codex-to-claude-frontend-implementation-review-handoff.md`）的四项均成立，上文「全部完成」与第 4.3 节的验证结论被本节取代。修复与验证见 Spec §11.3。

| 编号 | 判断 | 说明 |
| --- | --- | --- |
| M1 | 成立 | 新增确认步骤引入的缺陷；第 5 节未列为审查重点，属遗漏 |
| M2 | 成立 | 第 5 节第 1 条提到 `mark_ready` 仍走旧写法但未修，现已改为条件更新；另发现 worker 失败不回滚，只调换「通知／就绪」顺序会让卡片永远发不出，因此加保存点 |
| M3 | 成立 | 必填表单绑定先于业务判断；旧版保存空稿也因同一原因被 JSON 422 拒绝 |
| M4 | 部分成立 | 反例只在 SQLite 成立，生产 PostgreSQL 序列不复用编号；仍按摘要键修复，并更正 Spec §5.2 的推荐 |

新增测试：`test_complaint_delivery_guards.py` 增加过期版本进确认步骤、确认期间被保存、空正文保存／发送、迟到分析不重开、卡片登记失败回滚 5 类用例（后两类同步进 PostgreSQL 组）；`test_complaint_review_job.py` 补迟到分析用例并用保存点替身改写通知失败用例；`test_knowledge_image_admin.py` 补编号复用后的两轮清理用例。最终全量 2354 passed、15 skipped。
