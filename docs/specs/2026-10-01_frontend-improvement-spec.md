# 前端操作流程补齐与设计优化 Spec · Claude 接手草案

日期：2026-10-01。状态：**三段均已确认（见 §7.1），等待「开始」指令，未授权编码**。

修订记录：2026-10-01 按 Claude 复核意见（`docs/reviews/2026-10-01_frontend-spec-review-claude.md`）与 Codex 核对结果（`docs/reviews/2026-10-01_codex-to-claude-frontend-review-handoff.md`）修订 §3 W1、§4.1、新增 §4.1.1、§4.3、§5.2、§5.3、§6.1、§6.2、§6.7、§7 D2/D4/D5、§8 AC01/AC03/AC05/AC06/AC08，新增 AC01a。同日按 Codex 第二轮审查（`docs/reviews/2026-10-01_codex-to-claude-frontend-review-round2-handoff.md` N1–N3）重写 §4.1.1 为条件更新方案，修订 §4.3 现状与恢复规则、AC01a、AC03。同日完成三段确认，记录见 §7.1。

配套证据入口：[前端审查交接报告](2026-10-01_frontend-audit-handoff.md)。本 Spec 引用报告 F01–F15，不重复将审美判断包装成生产故障。源码基线 `868b9e77c2dc76ec4d80de70dd7d3794cbe0b4cc`，`main`，本地版本 1.62.0；接手重新核对。

## 1. 目标、范围与确认门禁

目标：员工能看清对象和状态，找到当前允许的操作，在失败后继续处理，并看到操作最终属于保存、排队、平台受理还是需要人工核验。

用户要求编写文档准备交接，未批准 15 项建议全部实施。确认分三段：

1. **证据**：确认交接报告的复现与代码现状；有源码变化时更新受影响部分。
2. **功能范围**：选择本 Spec 的工作包，允许按包实施；所有 F 编号均保留已选、未选或延期映射。
3. **风险决策**：确认 §7 D1–D7 中与已选工作包相关的项。完整 Spec 确认后，等待明确「开始」再编码。

实施中证伪方案、发现新的数据/外部边界时，更新并确认受影响 Spec，不将未授权业务改动当成顺手修复。已取得的本轮有效授权不重复询问。

### 1.1 不进入本次范围

- 不替换 Jinja、FastAPI、原生表单、公共 CSS/JS；不引入 SPA、组件库、状态管理框架、图表库或实时通信系统。
- 不改变知识检索排序、证据门、模型提示、退款赔偿决策、真实房态/价格权威来源、合并事务机制或正常消息回复口吻。
- 不自动重发诊断消息，不新增后台人工发送入口，不把入队或平台受理称为客人收件。
- 不新增回收站、版本历史、批量删除知识、自动审核知识、自动合并客户。
- 不改变普通员工既有权限，不展示更多密码、密钥、原始诊断正文或外部身份。
- 不提交、推送、部署、发版、删除真实知识、执行生产写入或调用真实 DeepSeek/Hostex/企业微信。此类行为需要当前明确授权。
- 不读受保护的 `YuMi民宿AI项目总结.txt`。

## 2. 推荐路线及备选

**推荐：在现有页面与真实服务链上逐包完成闭环。** 优先 W1；知识 W2 在同一内容管理边界规划并验收；W3–W6 独立验收。每个工作包必须能运行并有对应行为证据，不先把所有模板改完再统一补后端。

| 路线 | 收益与代价 | 结论 |
| --- | --- | --- |
| 现有能力复用并补齐必要契约 | 改动限定到实际表单、投影和会话装配；解决本文已确认反例 | 推荐 |
| 只修改颜色、排版与按钮文案 | 成本小，但不能修复发送正文、JSON 失败或条目删除；可独立完成 W6 | 不能替代流程修复 |
| 整体重写前端或建设通用状态机 | 引入迁移、角色与表单安全风险，当前没有需求证明其必要性 | 不采用 |

公共边界：UI 展示资格，后端独立验证；表单提交只调用既有业务服务；生产的 Session* 服务必须完整承接所有新参数和方法。不要为这些有限工作包创建通用状态引擎、通用 CRUD 或第二套文件清理队列。

## 3. 工作包与修改定位

路径均相对于仓库根目录；完整源码前缀为 `src/homestay_bot/`。表格中的类、函数、宏或表单是定位依据，行号见报告。只修改实际选中工作包的必要文件。

| 工作包 | 问题与边界 | 预计修改位置 | 验证目标 |
| --- | --- | --- | --- |
| W1 客诉与表单恢复 | F01/F02/F03；发送正文、状态资格、在途判定与回写归属、失败可恢复 | `templates/complaints/edit.html::content`；`routes/complaints.py::_action/complaint_send`；`services/complaint_admin_service.py::send/return_for_analysis/cancel`；`repositories/complaints.py::update_draft/mark_send_queued/mark_returned/mark_cancelled/mark_delivery_sent/mark_delivery_failed`；`repositories/jobs.py::status_for_dedupe_key`（复用）；`application.py::_record_complaint_delivery/_run_worker_loop.send_guest/SessionComplaintAdminService`（触及装配，按项目规则提交前本地全量）；`routes/knowledge.py::_fields/update_knowledge/create_knowledge/convert_candidate`；`templates/knowledge/detail.html/index.html/scope_fields.html`；`routes/page_errors.py` 与 `web.py` 仅需复用或最小扩展；`main.py` 仅在局部路径不能满足时调整处理器 | 当前编辑正文等于待发送正文；状态非法无持久化；HTML/API 失败呈现正确；新令牌可纠正重试 |
| W2 知识管理 | F04/F08/F11、知识部分 F10；删除需 D3/D4 | `routes/knowledge.py::KnowledgeAdminServicePort/KnowledgeAdminService/knowledge_index/knowledge_detail/update_knowledge/toggle_knowledge` 及新的删除路由；`application.py::SessionKnowledgeAdminService`；`templates/knowledge/index.html/detail.html/scope_fields.html`；`domain/models.py::KnowledgeCandidate` 仅确认需要迁移时改；复用 `repositories/jobs.py`、`_build_attachment_cleanup_handler` | 适用条件不误导；分区、筛选、分页和回跳贯通；删除、审计、关联与清理 job 同事务 |
| W3 未送达定位 | F05；只读诊断深链接，需 D5 | `repositories/admin_diagnostics.py::DeliveryChain/delivery_failure_rollup`；`services/admin_diagnostics_service.py::DiagnosticsRepositoryPort/snapshot`；`application.py::SessionAdminDiagnosticsRepository`；`templates/admin/diagnostics.html::delivery-chains` | 点击进入正确的授权客户对话；缺关联降级；诊断仍脱敏；统计不变 |
| W4 客户与房源操作 | F06/F10/F13/F14；内联佐证及作业状态需 D5/D7 | `templates/customers/index.html/detail.html/merge.html`；`routes/customers.py::customer_index/customer_detail/customer_merge_detail/_customer_redirect/refresh_customer_context`；`services/customer_admin_service.py::get_merge_detail/refresh_context/MergeCustomerCard`；`repositories/customers.py::_safe_merge_customer/latest_context_refresh_at` 与最小作业状态读取；`application.py::SessionCustomerAdminService`；`templates/properties/index.html`、`routes/properties.py::property_index`；任务详情返回路径按实际需要适配 | 合并证据可查看且不扩大内联隐私；保留列表/页签来源；停用房源可识别；排队反馈不冒充完成 |
| W5 审批核验 | F09；D6 单独确认前只交接，不编码 | `templates/approvals/detail.html::状态处理`；`routes/approvals.py`；`services/approval_page_service.py::list_pending/confirm/reject`；`services/booking_service.py::confirm_and_create` 只在已确认状态方案确实要求时修改；装配、模型、迁移按确认后的最小范围 | 外部订单已存在、确认不存在、结果未知分别处理；不能重复建单或虚构本地订单结果 |
| W6 可读性与语义 | F07/F15；不改业务状态 | `static/app.css::.cal-m__day--free/.cal-m__dnum`；`templates/components/ui.html::_room_timeline_mobile/badge`；`templates/complaints/edit.html`；`templates/tasks/detail.html`；`templates/customers/index.html`；`web.py::status_zh/_STATUS_LABELS/datetime_zh` | 日期最终对比度、信息完整、状态颜色一致、消息来源和真实时间可读 |

F12 任务批量选择作为 W4 的独立子项：`templates/tasks/index.html::selection-actions/data-select-all`、`static/admin.js::selectableBoxes/syncMirroredSelection/selectionForms`，验证手机、桌面与断点切换。不能让这一子项依赖客户或房源写功能。

## 4. W1：发送与失败恢复的功能契约

### 4.1 保存/发送资格

下表是推荐 UI，保存/发送能力与当前仓储一致。重新分析/关闭的终态规则依 D2 确认；不要声称下表已是后端完整规则。

| 状态 | 回复输入 | 保存 | 发送 | 页面反馈 |
| --- | --- | --- | --- | --- |
| pending_analysis / returned | 只读 | 不提供 | 不提供 | 等待分析；展示当前状态，允许手动刷新 |
| analysis_failed | 只读 | 不提供 | 不提供 | 分析失败及安全错误说明；提供当前流程允许的重新分析 |
| ready_for_review / editing | 可编辑 | 提供 | 提供 | 发送前确认实际正文 |
| send_queued | 只读 | 不提供 | 不提供 | 已排队，等待平台处理；不能显示已送达。关闭、退回不提供（在途，见 §4.1.1） |
| delivery_failed，原任务仍在途 | 只读 | 不提供 | 不提供 | 「系统正在自动重试」；关闭、退回、重发均不提供（§4.1.1） |
| delivery_failed，原任务已终止 | D1 采用同表单时可编辑 | 不提供独立保存（当前后端不接受） | 提供“重试发送” | 受控失败原因；明确提交当前内容重试 |
| sent / cancelled | 只读 | 不提供 | 不提供 | 展示已记录状态；重新分析/关闭按 D2，不擅自改变业务 |

前端隐藏或禁用按钮不替代服务端限制。过期版本、非法状态、空正文必须在持久化发送记录前拒绝；异步发送登记、版本更新、审计保持同事务。禁止“加一个按钮但后端仍先登记副作用再拒绝”的结果。

### 4.1.1 在途判定与回写归属（正确性修复，不属于 D2）

现状证据见报告 F01「复核补充」与 Codex 探针 E1–E4、E10、E11。以下规则复用已有字段、任务状态和项目已有的条件更新写法，不新增表、迁移、行锁框架或并行出站系统。

**规则一：客诉状态变更一律用带条件的原子更新。**

- 现状缺口：`SQLAlchemyComplaintRepository.get` 用 `session.get` 读取，`_require/_check_version` 比较的是本次会话读到的对象，没有行锁也没有条件更新。两个请求交错时，拿旧版本的关闭能覆盖已提交的发送（探针 E10：最终 CANCELLED、version=2、发送任务仍 PENDING）。
- 改法：发送、关闭、退回、保存草稿，以及 worker 的投递回写，都改为一条 `UPDATE complaint_reviews SET ...，version = version + 1 WHERE id = :id AND <条件>`，再检查 `rowcount == 1`，不是 1 就拒绝。
  - 条件：管理员操作带 `version = :expected_version AND status IN (<该操作允许的状态>)`；投递回写带 `delivery_outbox_id = :outbox_id AND status IN (SEND_QUEUED, DELIVERY_FAILED)`（见规则三），不带版本。
  - 先例：`repositories/runtime_config.py`（配置候选按状态条件更新并检查 rowcount，120–134 行）、`repositories/admin_credentials.py`（按 session_version 条件更新）。该写法在 SQLite 与 PostgreSQL 上语义一致；`with_for_update` 在 SQLite 上不生效，不能用 SQLite 测试证明互斥，因此不选行锁。
  - 所有要改的字段（状态、草稿、outbox_id、错误码、时间）都放进这条 UPDATE 的 values，不在同一会话里再改 ORM 对象，避免之后的 flush 用旧对象覆盖；更新后需要对象时重新读取。
- 发送的顺序：同一事务内先登记出站任务，再执行条件更新；更新失败则抛受控拒绝，事务回滚，已登记的任务一并撤销。拒绝的请求不写审计、不改草稿、不留任务。
- 各操作允许的状态：保存为 READY_FOR_REVIEW/EDITING；发送为 READY_FOR_REVIEW/EDITING/DELIVERY_FAILED；关闭、退回至少排除 SEND_QUEUED，SENT、CANCELLED 是否允许见 D2。

**规则二：原任务仍在自动重试时，拒绝重发、关闭、退回。**

- 条件更新只能保证「页面加载后客诉没变」，挡不住这个窗口：`_run_worker_loop.send_guest` 先把客诉写成 DELIVERY_FAILED，再对连接失败与 45009 抛可重试异常；`SQLAlchemyJobRepository.mark_failed` 把原任务放回 PENDING（探针 E1、E2）。管理员刷新后拿到新版本，条件更新会通过。
- 判定：客诉的 `delivery_outbox_id` 非空，且 `SQLAlchemyJobRepository.status_for_dedupe_key(delivery_outbox_id)` 为 PENDING 或 RUNNING，即为在途。出站任务的去重键就是 outbox_id（`TransactionalOutboxWeCom._enqueue_guest_text`），不需另存关联。
- 在 `ComplaintAdminService.send/return_for_analysis/cancel` 入口判定，在途时抛受控拒绝，不登记出站或审计。
- 这一判定不需要额外的并发保护：看到 PENDING/RUNNING 就拒绝是保守的；任务一旦进入 COMPLETED/FAILED 不会自动回到 PENDING。唯一从非排队回到 PENDING 的路径是 `recover_stale`（RUNNING→PENDING），此时判定本来就是在途。任务行被保留期清理后 `status_for_dedupe_key` 为 None，按不在途处理。
- 页面按同一判定隐藏按钮并显示「正在发送／正在自动重试」；以服务端判定为准。

**规则三：投递回写只认当前这次发送。**

- `_record_complaint_delivery` 增加参数 outbox_id（`send_guest` 已持有 `payload["outbox_id"]`），回写用规则一的条件更新，条件含 `delivery_outbox_id = :outbox_id`。不匹配时只记日志、不改客诉。这样旧任务的迟到结果不会覆盖新尝试，且比较与更新在同一条语句里，没有先比较后覆盖的空档。
- `mark_delivery_failed_by_outbox_id` 已按 outbox_id 匹配，改为同一条件更新写法；`mark_delivery_failed_by_external_message_id` 按真实消息编号匹配，同样改为条件更新。
- 实施前检查 `_record_complaint_delivery` 的全部调用点（`send_guest` 内三处，含过时跳过分支）。

**任务去重的职责不变。** `SQLAlchemyJobRepository.enqueue` 碰到同键时返回已有任务、唯一键竞争在保存点内吞掉（探针 E11），它只负责「不产生第二个同键任务」，不负责让另一个业务操作失败，也不保证抛出可捕获异常。不为适配本方案修改这一共享契约；并发重发的互斥由规则一的版本条件承担。

### 4.2 正文唯一来源（推荐 D1-A）

1. 保留一个可编辑 draft 控件，通过同一表单的不同提交按钮区分保存、发送，不再单独维护初始化隐藏 draft。
2. 点击发送展示将提交的当前正文；取消确认后保留编辑内容、选择和按钮可用性。
3. 后端沿用 `ComplaintAdminService.send` 的清理及版本约束；若确认面板展示的是原始输入，说明规范化处理，不能另生成摘要当最终正文。
4. 发送提交成功只显示“已排队”，实际结果来自既有 worker 回写。重复点击与同版本重放不能新建第二个有效 outbox。
5. 当前页面版本过期时不自动覆盖别人的更新，不用旧令牌静默重发；展示冲突并允许重新加载确认。
6. 无 JavaScript 时仍保留原生表单、服务端资格与确认策略。若最终正文确认依赖 JS，应给出原生可达的确认步骤或采用 D1-B 的先保存策略；不得让脚本缺失绕过新确认要求。

备选 D1-B：编辑发生后禁用发送，提示先保存；保存成功加载新版本后才能发送已保存正文。优点是最小保留后端语义，代价为两步操作，发送失败状态的编辑重试还需明确方案。只能选一种正文契约。

### 4.3 HTML 表单错误

1. 最小覆盖已确认的知识范围/日期错误和客诉失败；新增操作按同模式处理。不要借此重写所有 API、认证或未知异常。修复后不得再出现可预期业务错误的 500。客诉失败的现状分三种，不能混写：

   | 场景 | 当前行为 | 依据 |
   | --- | --- | --- |
   | 版本过期 | `_action` 捕获 ComplaintVersionConflict，转成 HTTPException(409)；不是 500，但 HTML 下只得到错误响应，编辑内容丢失 | `routes/complaints.py::_action` 146 行 |
   | 非法保存/发送状态、无可用正文、重复出站 | ValueError 未被捕获，得到 500；事务未提交，数据不被写坏；一次性令牌已消费 | `repositories/complaints.py::update_draft/mark_send_queued`；`services/complaint_admin_service.py::send` 94、101 行 |
   | 在途关闭/退回 | 当前不拒绝，操作直接成功，导致投递记录失真 | `mark_returned/mark_cancelled` 无状态条件 |

   「无可用正文」不等于本次提交为空：`ComplaintAdminService.send` 在提交为空时沿用已保存草稿，只有两者都为空才拒绝。§4.1.1 新增的条件更新失败与在途拒绝尚未实现，实现后同样按本节处理。
2. 恢复方式按「请求里有没有未保存的输入」决定，不按错误类别决定：
   - **没有待恢复输入**（关闭、退回、不带正文的操作）：在服务或路由边界转成 `OperationRefused`（`return_to` 为当前详情页），由现有 `handle_operation_refused` 以 PRG 回跳，显示最新状态与原因。转换前检查 ValueError 的全部调用方，不把所有 ValueError 原文直接展示。
   - **带未保存输入**（客诉保存/发送，包括版本过期、非法状态；知识范围/日期错误）：PRG 不保留 textarea，必须在本次已认证的失败响应中原地重渲染，同时呈现最新状态与员工刚提交的内容，返回 HTML 409 或 422，并附错误摘要与字段级错误。
   - 恢复的内容只供员工核对、复制或重新确认：不写入数据库，不覆盖最新草稿，不沿用旧版本或旧令牌静默重发。「保留输入」不等于「允许再次发送」：客诉已进入终态或在途时，恢复内容以只读方式展示，发送资格仍按 §4.1、§4.1.1 判定。
   - 知识表单三个入口分别恢复：详情页编辑失败重渲染详情页；列表页新建、候选转换失败重渲染列表页，重建当前分页、筛选与候选上下文，展开出错的新建表单或对应候选。不为此新增独立页面。错误不保存知识；成功仍走现有 PRG。
3. 重渲染前仍核验登录与管理员权限；服务端验证长度、日期、房间与 scope。未经授权、无效 CSRF 和未通过边界校验的值不能被当成可信页面上下文。
4. 签发新的合法一次性令牌，旧令牌仍失效；只在同一次已认证失败响应中恢复允许的输入。
5. 不把双语长答案放入签名 Cookie/会话闪存：可能超出 Cookie 大小，也会扩大正文暴露。需要原地重渲染；不为此新增持久化草稿表。
6. API 请求保留 JSON 与原失败状态。可预期业务错误可使用受控提示；未知异常只显示通用文案和追踪号，不 `str(error)` 回显 SQL、路径或秘密。
7. 失败页错误摘要可获得焦点，对应字段设置 aria-invalid 与说明关联；页面不锁死。错误恢复后修改并提交成功，新版本/状态可见。
8. 已有 `handle_operation_refused` 的 PRG 只表示已有领域拒绝可回跳，不能宣称它自动保留所有表单输入。若扩展共享处理器，检查所有调用方及 API 反例。

## 5. W2：知识管理的功能与数据契约

### 5.1 状态与分区

- 启停与审核范围独立展示：“启用 · 待审核（暂不参与回答）”“启用 · 指定房间”“停用”等。
- 列表/详情展示 scope 中文名称、房间、有效期；触发与排除词保留可查入口。有效期标签注明日期条件，不能一概宣布目标日期之外永久无效。
- 不新增统一“生效”布尔值，不复制 `KnowledgeService.retrieve_detailed` 的房间/日期/触发评分逻辑；展示来自已持久化字段。
- 建议 `/employee/knowledge?view=entries|candidates` 原生 GET 分区，默认 entries；错误分区值稳定拒绝或回默认，不能隐藏异常空白。只在确认的范围内增加参数。
- 现有搜索、启停、分类、房间、page/candidate_page 保留；修改候选流程后，不再要求非当前分区始终加载完整内容。保留旧不带 view 的 URL 可用。
- 普通员工保留现有知识只读权限，不开放候选、编辑与删除。候选摘要不添加客户身份；长表单按需展开或进入已确认的详情流程。
- 分区数量只能展示真实总量或明确“本页 N 项”，不能把分页结果长度冒充总数。

### 5.2 永久删除（仅 D3/D4 确认后）

推荐语义：停用用于暂停，永久删除用于错误/无用条目的清理。只做单条管理员删除，不增加批量、回收站或恢复系统。

用户可见流程：

1. 在现有条目动作及详情可达位置提供“永久删除”，与启停视觉区分，使用现有危险确认样式。
2. 确认展示条目编号/安全标题及关联配图数量，说明记录无法恢复；审计不复制标题、答案或文件内容。
3. 删除完成回到当前分区、筛选和分页，显示成功通知；末页删空时不跳到 404 或空白页，可回合法前一页。
4. 普通员工无入口；伪造 POST 仍被拒绝；一次性 CSRF、对象归属和当前管理员身份由路由验证。不存在或重复删除返回稳定无副作用结果。

事务与清理：

- 新增删除路由与 `KnowledgeAdminServicePort` 能力时，同步迁移 `KnowledgeAdminService`、`SessionKnowledgeAdminService` 和真实调用方；不能只更新测试桩。
- 路由遮挡：现有 `toggle_knowledge` 注册为 `POST /{entry_id}/{action}`，非 enable/disable 返回 404。删除路由必须注册在它之前，或使用不与之冲突的路径；验收用真实 HTTP 请求打到真实路由表，不只直接调用处理函数（Codex 探针 E9）。
- 复用既有 KnowledgeEntry 行锁边界，核查图片上传、移动与候选转换等竞争；删除与并发上传不留下已登记但无父条目的图片。失败上传沿用现有文件补偿。
- 在同一数据库事务中读取关联 file_id、按已确认 D4 处理候选引用、删除条目/派生记录、登记文件清理 job、写最小审计，再提交。
- 图片和向量有 CASCADE 声明；真实 SQLite 测试必须开启外键或明确验证删除策略，不能依赖关闭外键的替身通过。候选引用当前没有 CASCADE。
- 推荐复用 `ATTACHMENT_CLEANUP_JOB_TYPE` 的 `{file_ids: [...]}` 载荷和现有 worker；去重键为固定短前缀、条目编号加排序后文件编号的 SHA-256 摘要，符合已有限长约束。（2026-10-01 更正：原写「固定短前缀与条目编号」，Codex M4 证伪——SQLite 会复用被删的最大编号，第二次删除撞上旧任务、漏登记新文件。）
- 私有文件在数据库提交后由已有 worker 清理；事务失败时不能先删除文件，不能留下已提交清理任务。清理失败按现有有限重试与诊断处理，不静默吞掉。
- 原数据表定义已能表达推荐“解除引用并保留候选状态”，不预设必须新增迁移。若 D4 选择不同语义，则单独确认数据兼容、迁移与回滚。
- 删除后新查询不返回该条目、向量或配图。只检查失效和关联；不顺带重算全部向量。
- 历史或已经入队的回复不在删除按钮的撤回承诺内。接手核查在途固定回答/配图任务携带方式，若产品要求取消它们，扩大的是消息链路范围，须另确认。

### 5.3 候选引用（D4 推荐）

**现状约束**（报告 F08 复核补充，Codex 探针 E5）：`get_or_create` 按 canonical_key 复用旧候选；`add_occurrence` 对非 OPEN 候选不计数；`reopen_expired` 只重开 SNOOZED。删除知识时若保留 CONVERTED，同一规范化问题此后不再计数和提醒。语义近似但规范化结果不同的问题不受影响，不能扩大成「所有相关问题都消失」。

**三个选项**（待用户在 D4 中选择）：

| 选项 | 行为 | 后果 |
| --- | --- | --- |
| A（推荐） | 同事务解除引用，并把候选改回 OPEN | 被删知识对应的问题可以重新被发现；删除本身不立即提醒 |
| B | 解除引用，保留 CONVERTED | 该问题永久不再计数和提醒；删除确认框必须明确告知 |
| C | 仍有候选引用时拒绝删除 | 这类条目只能停用，删除需求部分落空 |

**选 A 时的字段语义**：直接沿用现有的重开先例 `SQLAlchemyFaqCandidateRepository.reopen_expired`（416 行起），它把 SNOOZED 改回 OPEN 时的重置规则就是「新一轮从零开始」，不另定一套：

| 字段 | 取值 | 依据 |
| --- | --- | --- |
| status / knowledge_entry_id | OPEN / NULL | 本次新增 |
| total_occurrences、last_threshold_total、last_reminded_total | 0 | 与 `reopen_expired` 一致；`FrequentFaqService._track_eligible` 要求最近窗口至少三次且增量至少三次，从零起算即「重新攒满三次才提醒」，删除本身不触发提醒 |
| last_seen_at、last_reminded_at | NULL | 与 `reopen_expired` 一致；不沿用旧冷却（`_enqueue_trigger` 按 last_reminded_at 算冷却） |
| notification_pending | False | 与 `reopen_expired` 一致 |
| examples、draft_status、draft_payload、draft_attempts、draft_examples_version | 已由 `convert` 时的 `_clear_private_content` 清空 | 转换时已清；重开时再调用一次 `_clear_private_content`，保证不复用已删知识的草稿 |
| draft_generation | 只增不减（`_clear_private_content` 内 +1） | 使已排队的旧代次任务被 `FaqDraftJobService.handle` 的代次校验丢弃；不得重置为 0 |
| 出现明细 | 已由 `convert` 删除 | 无需再处理 |

- 写一条最小审计，格式与 `reopen_expired` 的系统审计一致，不复制问题、示例或草稿正文。
- 字段已能承载这一变化，不需要迁移；实施前仍核查存量 CONVERTED 候选数据，不把「无需迁移」推广成「已有数据无需核查」。
- 不再存在的知识链接不渲染为死链接。

## 6. W3–W6：其余页面行为

### 6.1 未送达定位（F05，D5）

- 最小安全投影增加可用 customer_id，必要时附内部 conversation_id；从 root Message 的关联查询取得，不由浏览器拼外部身份。
- 提供“查看客户对话”链接进入现有 `/employee/customers/{id}?tab=chat`，目标页继续独立管理员认证。
- 链接复用现有消息游标：`/employee/customers/{id}?tab=chat&before_message_id={root_id + 1}`。`SQLAlchemyCustomerRepository.customer_messages` 按 `Message.id < before_message_id` 取页，目标仍可查询时就是该页最新一条。不新增 API、全文搜索或滚动机制。
- 游标只对仍可查询的目标有效。带 `cleared_by` 标记（「清空测试数据」留下）的行被查询排除，目标不在页面中（Codex 探针 E6）；正文为空但无该标记的行仍可查到（E7）。不能承诺「一律显示占位」，也不能用页面的隐藏总数推断某条目标的清理情况。
- 消息编号继续标为“系统记录编号”，近期失败时间协助定位。已清理消息、无客户关联或已合并客户用受控说明降级，不能错误跳到别的客户。
- 查询保持有界、批量关联，不能为每条链再查一次；原阶段、计数、截断说明与单项故障降级不变。
- 不返回 content、external_userid、open_kfid、电话或完整 job payload。深链接新增关联字段属于 D5 明确确认的诊断白名单变更。

### 6.2 合并判断（F06，D5）

- 推荐增加来源与目标档案的管理员查看链接，并内联两侧电话；保留名字、内部编号与关联数量。
- 电话展示依据：`CustomerAdminService._display_phone` 返回完整号码是 1.41.0 起的既定决策，客户列表、详情和合并目标搜索已向同一批管理员展示。合并复核页加电话不扩大受众，但仍属 D5 待确认选项。
- 投影适配：`repositories/customers.py::_safe_merge_customer` 返回字典且只查 ID、姓名；`_display_phone` 用 getattr 取值，直接给字典加键再传入取不到值。应在仓储查询中补电话明文列与密文列，在服务映射处复用 `_display_phone` 的同一解密逻辑生成展示值；不加载整个 Customer ORM，不另写一份解密实现，不加入备注、聊天正文或订单明细。
- 展示值与 `_display_phone` 一致：明文、存量密文解密、无号码显示「未登记」。
- 查看完整档案应能返回该次合并复核，保留来源→目标方向；拒绝/确认仍只提交现有建议 ID、令牌与受控决定。
- 合并仍由管理员确认，不按字段相似度自动执行。

### 6.3 来源上下文（F10）

- 补齐知识列表→详情→保存/启停/删除→来源列表、客户列表→详情→列表、客户 service 页签→任务→来源页签，以及合并复核→档案→复核。
- 路由接收有长度边界的 return_to，经现有 `safe_return_path` 验证；所有页签和表单继续携带。缺少来源用本页合法默认，不跳到别的模块。
- 保留 query、页码、分区和锚点；不依赖 Referer；外部 URL、带 scheme/netloc 的地址拒绝或回安全默认。
- 返回链接是原生 a，操作成功后服务端重定向；不能仅依赖 history.back，刷新/直接访问仍有确定返回路径。

### 6.4 批量选择（F12）

- 桌面/手机均能全选本页与清空；显示“已选 N 项”。N 按实际可提交且唯一的 task_id 计算，不能把隐藏副本或全部页数计入。
- 复用现有镜像同步。增加第二个可见全选入口时同步两个入口的不确定态，而不是各建独立选择状态。
- 零选择时禁用相关动作或给予明确无选择反馈；取消确认不清空选择，不锁死按钮。
- 选择资格混合时说明哪些动作不适用；不改变服务端整批拒绝契约，也不自动替用户缩小范围。页面已有资格不足以逐项预判时只做计数与说明，不添加一套后台资格推断。
- 断点切换保留选择、不重复提交。永久删除的手输数量与实际唯一数量一致，归档、分派、取消保留各自确认文案。

### 6.5 房源启停（F13）

- 桌面和手机均显示已停用标识，启停与本地准备状态并列，不能把“停用”改成“维修中”或“不可入住”。
- 增加启停筛选时默认仍是全部，使用 GET 并保留详情来源。可优先在当前有界投影过滤；若现有规模/契约要求查询过滤，采用同一服务和 Session 装配，不另建房源数据源。
- 不改房源 is_active 值，不发 Hostex 修改请求，不清除凭证。

### 6.6 重新整理反馈（F14，D7）

- 最小方案复用 `set_page_notice` 显示“已排队，完成后更新接手要点”，仍回 memory 页签；成功通知只能在入队/事务提交成功后出现。
- 冷却拒绝与排队失败显示真实受控原因，不说“已完成”；保留已有锁、冷却与去重。
- 如选状态展示，读取该客户最近一次手动 customer_context_refresh 作业的安全元数据：待执行、执行中、完成、失败及时间，不返回 payload/last_error 原文。
- 摘要更新时间不能单独证明该作业完成。未知作业或清理后的历史显示“暂无本次作业状态”，不伪造成功。
- 第一版可手动刷新，不新增 WebSocket、持续轮询或额外后台任务；重试沿用现有冷却规则，不绕过冷却。

### 6.7 文字与视觉语义（F07/F15）

- 空闲日期采用现有次级正文色，普通日期最终计算对比度达到 4.5:1；保留今天、重叠、异常及占用条的非颜色提示。
- 原日期条对读屏隐藏的分工不因换色破坏；订单卡仍包含相同真实日期信息，放大文字不会截断。
- 完成/取消/失效等任务终态使用中性或已有语义色；客户 in_house 在两个布局使用相同语义色。保留状态文本，不能只用颜色区分。
- `customers/detail.html:15` 的「脱敏电话」改为「电话」，与 `_display_phone` 实际返回完整号码一致；字段名 masked_phone 本轮不改。
- 客诉 MessageOrigin 映射为“客人／人工客服／机器人”，risk_level 只映射受控枚举/允许值；未知值显示“待核实”或受控原码，不能误归低风险。
- 消息时间使用已有真实发生时间字段与 `datetime_zh`，缺失显示“时间未知”；发送时刻与接收/页面加载时刻不能混用。
- 手机频繁操作优先扩大可点击区域至约 44px，作为触控优化目标；不得把所有小于 44px 的元素自动声明为 WCAG AA 违规。

## 7. 待确认风险与决策

下表明确了选项，避免把“需要确认”写成无内容占位。未回复、时间经过或交接文件存在都不是确认。

| 决策 | 推荐项 | 备选与实际影响 | 影响范围 |
| --- | --- | --- | --- |
| D1 客诉发送正文 | A：同一表单发送当前编辑正文，明确最终内容确认 | B：编辑后禁止发送，先保存再发；更保守但多一步，失败重试编辑需单独约定 | W1/F02 |
| D2 终态重开范围 | SENT、CANCELLED 页只读，不提供退回重新分析与关闭 | 允许从 SENT 或 CANCELLED 重开时，必须同时定义新发送阶段（当前非失败状态下出站编号不带阶段后缀，重开后的新草稿会撞去重键发不出去）与旧任务隔离。在途时的拒绝已移入 §4.1.1 缺陷修复，不在本项选择范围内 | W1/F01；不可仅改按钮掩盖后端规则 |
| D3 知识删除语义 | 单条永久删除与停用并存，不新增回收站；数据库记录删除，文件清理异步提交 | 仅保留停用则不实现 F08，明确延期；软删除/恢复另扩展数据模型，当前不推荐 | W2/F08 |
| D4 候选关联处理 | A：删除知识时解除引用并把候选改回 OPEN，计数与提醒字段按 `reopen_expired` 先例归零（§5.3） | B：解除引用、保留 CONVERTED，该问题永久静默，确认框须告知；C：有引用时拒绝删除 | W2/F08 |
| D5 安全信息与定位 | 诊断增加内部客户关联，链接带 `before_message_id` 游标；合并页增加两侧档案链接与电话（§6.1、§6.2） | 合并页只加档案链接不加电话；住宿等其他内联佐证另定字段白名单 | W3/F05、W4/F06 |
| D6 审批核验回填 | 本轮先保留只读核验说明，将 F09 作为待单独确认的业务扩展 | 如本轮实施，必须决定：如何证明外部订单已存在、结果未知怎么保持待核验、不存在时是否重试/新建。不能直接启用确认按钮 | W5/F09 |
| D7 客户作业反馈 | 第一小步只补已排队通知；状态查询单独选择后才扩展 | 同轮增加最近手动作业只读状态，范围仍限定现有 jobs；不增加实时系统 | W4/F14 |

确认记录由实施方在取得用户答复后补充：选中工作包、对应 D 选项、原话/日期与“开始”指令。

### 7.1 确认记录（2026-10-01）

| 段落 | 用户原话 | 确认内容 |
| --- | --- | --- |
| 第一段 证据 | 「认可」 | F01–F15 的证据与分类：确定性缺陷 F01/F02/F03/F04/F07/F13/F15；功能缺口 F05/F06/F08/F10/F12/F14；设计判断 F09/F11（未经 Claude 逐行复核） |
| 第二段 范围 | 「按建议」 | 实施 W1、W2、W3、W4、F12、W6；W5（F09）不做，留待单独立项。顺序 W1 → W2 → W3/W4/F12，W6 随相关页面一并验收 |
| 第三段 决策 | 「d7加强。其他默认」 | D1-A 同表单发送当前编辑内容并确认正文；D2 SENT/CANCELLED 只读；D3 单条永久删除，与停用并存；D4-A 解除引用并改回 OPEN，计数按 `reopen_expired` 先例归零；D5 诊断链接带 `before_message_id` 游标，合并页加两侧档案链接与电话；D6 本轮不做（随 W5）；D7 加强：已排队提示，另显示最近一次手动整理作业的只读状态（§6.6：待执行、执行中、完成、失败及时间，不返回 payload 或错误原文，无记录显示「暂无本次作业状态」，不新增轮询或实时推送） |

「开始」指令：尚未收到。收到前不修改业务代码。

## 8. 验收场景、现有测试与最小补充

只实现已选工作包对应的验收；没有覆盖不写“全部通过”。优先在既有文件扩展有判别力的行为测试；真实页面联调确需新文件时可以新增 `tests/browser/test_frontend_workflows.py`，复用现有登录/临时数据库装配与 Playwright，避免每项各造桩页面。

| 验收编号 / 问题 | 场景与必须证明的结果 | 优先测试位置 |
| --- | --- | --- |
| AC01 / F01 | 覆盖所有 ComplaintReviewStatus；非法保存/发送不可见或有说明，直接构造 POST 同样被拒绝；未增加发送 job。发送失败可按 D1 重试。可预期业务拒绝不再得到 500 | `tests/integration/test_complaint_repository.py`，真实 SessionComplaintAdminService 与临时 SQLite；页面行为用 browser |
| AC01a / F01 在途与并发 | 顺序场景：① SEND_QUEUED 下直接 POST 关闭、退回、重发被拒绝，状态、版本、草稿与任务数量不变；② 连接失败或 45009 后原任务仍 PENDING/RUNNING 时，重发、关闭、退回同样被拒绝，不产生第二个在途任务；③ 原任务终态失败后允许一次手动重试，只登记一个新阶段，重复请求不新增任务。交错场景（两个 Session 按固定顺序交错）：④ 同版本发送与关闭交错，后到者被拒；⑤ 发送与退回交错；⑥ 两次重发交错，只有一个有效任务；⑦ 旧任务迟到回写与新尝试交错，旧结果不改客诉。每项断言最终状态、版本、有效任务数、当前 outbox_id 与待发正文一致 | 真实 SessionComplaintAdminService、`_run_worker_loop` 与临时 SQLite，请求与 worker 回写用不同 Session；企业微信用 ConnectError 替身；探针 E1–E4、E10 可作为修复前复现。条件更新在 SQLite 与 PostgreSQL 上语义一致，交错场景至少在 PostgreSQL 上再跑一遍 |
| AC02 / F02 | 输入旧稿→修改为新稿→取消确认仍保留新稿→确认发送；outbox 正文符合选中 D1 契约，重复提交无第二个有效 outbox；无 JS 路径同样安全 | `tests/browser/test_frontend_workflows.py`（确需新增时）；复用 `test_admin_interactions.py::test_cross_form_submission` 的已知防护，不以隐藏字段源码断言代替 |
| AC03 / F03 | scope=property 缺房间、日期反向、客诉无可用正文、客诉过期版本：HTML 错误可改，未保存输入未丢，新令牌可纠正成功；旧令牌仍拒绝；API 状态/JSON 保留；未知错误不泄密。不带输入的操作被拒后回跳并显示最新状态与原因。版本过期专项：A 先更新，B 带未保存正文按旧版本提交，数据库与 outbox 未被 B 改写，页面同时显示最新版本与 B 的正文，新令牌只能用于当前允许的操作。知识详情编辑、列表新建、候选转换三个入口各测一例 | `tests/integration/test_knowledge_routes.py`、`test_complaint_repository.py`；真实浏览器修改重试 |
| AC04 / F04 | enabled+unreviewed、disabled+reviewed、房间限定、未来/历史有效期条件均显示正确；展示变化不改变 retrieve_detailed 对目标日期/房间的既有结果 | `test_knowledge_routes.py`；如触及共享读取，复用 `tests/unit/test_knowledge_scope.py` 的真实条件反例 |
| AC05 / F05 | 未送达链关联客户 A，点击进入 A 且目标消息是该页最新一条；目标仅正文为空时仍在页面中；目标带 cleared_by、没有关联、客户已合并时有明确降级；普通员工直访无权；返回投影没有正文或外部身份，统计仍按链 | `tests/integration/test_admin_diagnostics_repository.py`、`test_admin_dashboard_routes.py`，新 browser 工作流按需 |
| AC06 / F06 | 来源/目标均可查看并返回同一建议，方向未反转；无身份佐证不冒充匹配。若 D5 选内联电话：两侧电话对应正确客户，明文、存量密文、无号码三种情况分别显示号码或「未登记」，断言的是已确认的展示契约与管理员权限，不要求遮罩；不复制备注/正文到审计 | `tests/integration/test_customer_routes.py::test_merge_review_explains_direction_and_safe_association_counts` 及实际页面行为 |
| AC07 / F07 | 浏览器计算空闲日期最终颜色/背景与对比度达目标；今天和冲突仍辨识；390px、文字放大时没有裁切 | `tests/browser/test_admin_interactions.py` 的 timeline fixture 或真实页面；不写 CSS 字符串快照 |
| AC08 / F08 | 合成条目含配图、向量、已转候选；按 D4 删除后关联符合约定、审计及清理 job 一并提交；注入失败全部回滚，文件还在；清理 handler 执行后文件删除；权限/旧令牌/重复删除拒绝且无额外 job。删除请求经真实 HTTP 路由到达删除处理器而不是 404。选 D4-A 时：删除不立即提醒；同一规范化问题再出现会计数，攒满阈值后重新提醒；重复入站不重复计数；旧代次草稿任务不回填 | `tests/integration/test_knowledge_image_admin.py` 的真实会话/私有目录；`test_knowledge_routes.py`；必要时隔离 PostgreSQL 验证外键和上传竞争 |
| AC09 / F09 | 只有 D6 确认实施才执行：外部已建单、未建单、未知结果三类各有确定结果；无重复 Hostex 创建，不自动把未知变成功 | `tests/integration/test_approval_routes.py`，并按最终业务方案补真实 BookingService/平台桩测试 |
| AC10 / F10 | 从带筛选/页码列表进入详情、执行操作再返回来源；客户 service→任务→service 与合并→档案→合并；外部来源回安全默认 | `test_knowledge_routes.py`、`test_customer_routes.py`、`test_task_routes.py`，browser 验证用户真实点击路径 |
| AC11 / F11 | 默认现有知识分区，不被候选长表单挤走；切换、刷新、翻页和转换后保留 view；普通员工无候选写入口；旧 URL 可访问 | `test_knowledge_routes.py` 与 browser；替换旧顺序断言为分区行为 |
| AC12 / F12 | 手机/桌面全选→取消单项→计数正确；断点切换不重复；零选有反馈；混合资格仍整批拒绝；确认数字与提交唯一 ID 一致 | `test_admin_interactions.py` 的既有选择用例、`test_task_routes.py` |
| AC13 / F13 | 同时有启用/停用合成房源，两个布局正确识别；筛选默认全部、操作返回来源；没有改变 is_active 或访问外部接口 | `tests/integration/test_property_routes.py`，现有真实服务集成优先 |
| AC14 / F14 | 入队成功后才通知；失败/冷却无假成功；若选状态展示，独立请求读取对应 job 的四态；自动摘要更新时间不冒充本次完成 | `tests/integration/test_customer_routes.py`、`tests/unit/test_customer_admin_service.py`；跨请求真实仓储测试按需 |
| AC15 / F15 | 客诉来源、风险和真实时间可读；未知值不误判；同一任务/住宿状态桌面和手机语义一致 | browser 工作流，既有 status_zh 单测按实际新增映射补正常/未知值反例 |

### 8.1 风险驱动执行规则

- 文档编写本身不跑业务全量。本报告中已有 10 项通过只作为未改源码基线，未来不能当成新功能已经验收。
- 为可稳定复现的新缺陷补能捕获原问题的最小回归，不为每个模板或函数机械加测试。
- 页面行为用真实浏览器和实际提交结果；事务/清理/对象归属用真实服务、临时 SQLite 和独立请求/会话。测试替身只用于控制外部失败，不代替生产装配。
- 修改 `application.py` 的 Session* 装配、依赖、构建配置，或无法界定影响范围时，按项目规则在提交前本地跑一次全量；未触发则默认只跑受影响范围。
- 若变化只展示知识元数据、不改检索/主模型提示/公共回复流程，不机械运行真实模型。核对 `scripts/release/reply_gate.sh::REPLY_PATHS` 与项目门禁；实际改到知识检索或公共回复时按规则全量门禁，不能凭“前端任务”跳过。
- 真实模型、高成本调用、生产页面写入、外部收件仍需当前授权。实际门禁未获授权时明确记录未执行，不能称已完成发布验收。
- 数据迁移、外键、锁或事务变化按实际边界补 PostgreSQL 等验证；不以 SQLite 数量代替目标数据库证明。
- 验证完成后只因源代码、测试、依赖、命令、环境或验收目标变化重跑受影响项；不为冻结、交接或汇报重复全量。

### 8.2 命令入口（实施后按已选包选用）

以下是可运行的现有入口，不表示已经执行所有测试，也不要求全部机械执行。

```sh
# W1：已有状态与页面路由；新增实际反例后运行同范围。
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_complaint_repository.py tests/integration/test_knowledge_routes.py
# W2：真实会话、图片文件、删除清理及知识范围（仅受影响时）。
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_knowledge_image_admin.py tests/unit/test_knowledge_scope.py
# W3：诊断投影、降级、页面和授权。
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_admin_diagnostics_repository.py tests/unit/test_admin_diagnostics_service.py tests/integration/test_admin_dashboard_routes.py
# W4：按所选客户、房源、任务子项裁剪到对应文件或用例。
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_customer_routes.py tests/integration/test_property_routes.py tests/integration/test_task_routes.py
# W5：保留非待审批无真实下单表单等保护；新增核验场景后运行。
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_approval_routes.py
# W6 / 公共 JS：选择、抽屉、确认与时间轴；可进一步选具体受影响用例。
.venv/bin/python -m pytest -q tests/browser/test_admin_interactions.py
# 只有实际新增真实页面工作流文件后才运行该文件，不将不存在文件写作已通过。
# 若触发项目全量门禁：
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q --tb=short
git diff --check
```

## 9. 实施顺序、交付与回退

1. 确认当前证据、所选包与对应 D 决策，记录有效授权。当前不改 `tasks/todo.md`；中等及以上实施确认后使用项目既有 todo，不覆盖其他任务。
2. 推荐先 W1：实际正文与状态/失败恢复端到端完成。W1 不依赖知识删除或审批新业务规则。
3. W2 统一内容管理方案：状态、分区、回跳先联通，D3/D4 确认后接删除和清理事务；共同验收 F04/F08/F10/F11。
4. W3、W4 按选中边界独立交付；F12、F13、F14 互不要求强制同时做。W5 未确认前保留建议。W6 与相关页变更合并验收，无需先重建全局设计系统。
5. 每包完成时记录问题编号→最终文件/符号→验收编号→实际结果与未覆盖。完整任务结论必须与选中范围一致，不能用测试总量替代各闭环。
6. 对共享函数检查所有调用方并自审简化。保留中文职责/约束注释，不重构无关模块；删除本次替代的孤儿隐藏字段或脚本分支。
7. 不自动提交/发版。获准后按既有 Git/Ponytail 门禁做最终差异审查；版本升级另维护 CHANGELOG 和 docs/releases 正式记录，部署、真实页面及外部收件各自提供证据。

回退边界：

- 纯模板/CSS/导航优化可回退相应源码差异，保留数据库数据；不要把整仓 hard reset 当回退。
- HTML 错误处理变化回退对应处理器/路由，确认仍保留原服务端数据校验与 API 语义。
- 永久删除不能靠回退代码恢复记录与私有文件；上线前必须有当前授权、可恢复备份和恢复验证。异步清理完成后还原须同时恢复数据及文件。
- 若 D4 引入迁移，需给出已有数据、锁与迁移回滚方案；推荐无新表无迁移，但不预先宣布最终实现一定无迁移。
- 审批核验涉及真实订单时，仅回退 UI 不能撤销外部结果，必须按最终确认的对账/补偿方案处理，禁止自动删单。

## 10. 完成标准

- 所选 F 项均满足对应 AC；未选/延期项明确列出原因。
- 真实页面点击能到达实际服务装配；正确状态、正文、来源、失败恢复与反馈均有直接证据。
- 权限、一次性令牌、版本、审计、去重、事务、诊断脱敏未退化；相关变更的目标数据库边界已验证或明确未覆盖。
- 工作区无无关改动，无受保护文件摄入，无秘密/真实客户数据进入文档或日志。
- 明确区分本地源码验证、当前部署副本、运行态和真实外部验收；未获授权的发布/外部步骤不假称完成。

本 Spec 写成不代表实施开始；交接报告的 15 项发现也不代表用户已经选择全部实现。

## 11. 实施记录（2026-10-01）

用户「开始全部做完」后按 §7.1 范围实施，W5（F09）未做。未提交、未推送、未部署。

### 11.1 与本 Spec 的偏差及原因

| 位置 | 偏差 | 原因 |
| --- | --- | --- |
| §4.1 退回资格 | DELIVERY_FAILED 也不允许退回（表格原未写明） | 首次发送的出站编号不带阶段后缀，已发送过再退回，新草稿会撞去重键永远发不出去；关闭仍允许 |
| §4.2 D1-A | 回复框为空时直接拒绝，不再沿用已保存草稿 | 「发送框里的当前内容」与「为空时悄悄发旧稿」矛盾，后者正是 F02 的来源 |
| §4.2 第 6 条 | 无脚本确认采用服务端确认步骤：未带 confirmed=1 的发送请求只渲染确认面板，不登记任务 | 现有 data-confirm 全站依赖脚本；这里另加原生可达的确认，接口调用同样需要 confirmed=1 |
| §5.2 删除确认 | 列表页确认框不显示配图张数，详情页显示 | 列表查询不带配图计数，为一句文案新增批量计数接口不值得；审计里仍记录张数 |
| §6.3 来源 | 房源详情页不接收来源，从筛选后的房源列表进详情再返回会回到全部房源 | 房源详情的页签与写表单多，F13 只要求列表能识别和筛选停用房源 |
| §6.3 实现方式 | 客户详情的写操作通过统一跳转函数读取已解析表单里的 return_to，而不是逐个路由加参数 | 8 个写路由共用一个出口，改一处即可，来源仍经 safe_return_path 校验 |
| §4.1.1 规则三 | `mark_delivery_failed_by_outbox_id` 当前没有调用方，仍按条件更新改写 | 与同族方法保持一致；是否删除这段遗留代码不在本次范围 |

### 11.2 验证

- 静态检查：Ruff、mypy（147 个源文件）、`git diff --check` 均通过。
- 本地全量（改动 `application.py` 装配，按项目规则执行），带本机临时 PostgreSQL 16 隔离测试库：2343 passed、15 skipped、6 warnings，退出码 0。跳过项全部是需要真实 DeepSeek、百居易、企业微信的契约测试。
- PostgreSQL 专项：客诉交错 6 条与真正并发 2 条（同版本两次发送只登记一个任务；发送与关闭同时提交结果一致），知识删除外键顺序 1 条，均通过；临时库用后已删除。
- 修复前复现：空闲日期对比度用例在修改前得到 2.36:1 失败、修改后通过。
- 真实模型门禁：本次未改动 `REPLY_PATHS` 中任何文件，不需要运行。
- 未覆盖：生产页面登录验收、真实企业微信收件、部署后运行态；W5 未实施。

### 11.3 Codex 实施审查后的修复（2026-10-01）

依据 `docs/reviews/2026-10-01_codex-to-claude-frontend-implementation-review-handoff.md` M1–M4，四项均成立（M4 只影响 SQLite，生产 PostgreSQL 序列不复用编号）。§11.2 的验证结论被以下修复取代。

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| M1 | 无脚本确认步骤把过期版本换成最新版本，绕过冲突处理 | `routes/complaints.py::_render_detail` 新增 `confirm_version`：进入确认步骤时版本不符即按冲突恢复（409、保留原文、不出确认面板）；确认表单沿用最初提交的版本，确认后又有人保存时最终发送仍被拒 |
| M2 | `mark_ready` 仍是读出再赋值，迟到的分析会重新打开已关闭客诉；卡片先于就绪登记 | `mark_ready` 改为条件更新（待分析类状态＋开始分析时的版本），未写入返回 None；`ComplaintReviewJobService.handle` 先写就绪、成功才登记卡片，两步包在 `atomic`（生产装配传 `session.begin_nested`）里——worker 失败时不回滚，必须靠保存点撤销 |
| M3 | 空正文在 FastAPI 必填绑定处变成 JSON 422 | `complaint_save/complaint_send` 的 draft 默认空串；服务层保存与发送空正文都以 422 受控拒绝并原地重渲染；回复框加 `required` |
| M4 | 清理任务去重键只用条目编号 | 键改为 `knowledge-entry-cleanup:{id}:{sha256(排序后的文件编号)}`；共享 `enqueue` 契约不变 |

验证：Ruff、mypy、`git diff --check` 通过；带本机临时 PostgreSQL 的本地全量 2354 passed、15 skipped（均为真实外部契约），退出码 0；客诉 PostgreSQL 专项 10 条（含分析回写 2 条）与知识删除 1 条通过。M4 新用例在还原旧键时失败、修复后通过。
