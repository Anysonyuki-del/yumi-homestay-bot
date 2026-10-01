# 前端设计审查交接报告 · Codex → Claude

日期：2026-10-01。状态：审查与交接文档已完成，业务实现未开始。

修订记录：2026-10-01 按 Claude 复核意见（`docs/reviews/2026-10-01_frontend-spec-review-claude.md`）与 Codex 核对结果（`docs/reviews/2026-10-01_codex-to-claude-frontend-review-handoff.md`）补充 F01、F05、F06、F08、F15，删除 §4.4 参考分。补充内容均标「复核补充」；除删除参考分外，原文未改写。

## 1. 接手入口、用户要求与授权

用户本轮原话：

1. 「审查本项目的知识库内容，为什么没有删除按钮」
2. 「以前端设计师的身份审查该项目前端的所有设计，寻找如上面一样的功能点缺失，混乱等问题，提出优化修改意见」
3. 「写交接报告和spec，详细说明依据，准备交接给claude」

当前授权覆盖只读审查及本报告、配套 Spec 的编写；没有编码、提交、推送、部署、删除知识或真实外部调用授权。交接给 Claude 是准备可阅读的文件，不是已发送消息，也不是自动开始实现。

阅读顺序：项目 `AGENTS.md` → 本报告 → [配套 Spec](2026-10-01_frontend-improvement-spec.md)。Spec 是待确认草案，功能范围与风险决策未得到逐项确认。依项目规则，完整 Spec 确认并收到用户明确的「开始」后才编码。若接手时已取得本轮确认，沿用有效确认，不重复索要。

源码基线：

- 分支：`main`。
- HEAD：`868b9e77c2dc76ec4d80de70dd7d3794cbe0b4cc`。
- `pyproject.toml::project.version`：`1.62.0`。
- 写文档前 `git status --short` 为空；本轮只新增两份交接文件。
- 此基线只代表本地源码；没有核验当前 GitHub、CI、服务器、数据库或生产登录页面。
- 接手时重新读取 HEAD、差异和相关符号；源码变了只重验受影响结论，不把旧行号或测试数字当作当前验收。
- 不读取、摄入、暂存、修改或提交未跟踪的 `YuMi民宿AI项目总结.txt`。文档只含合成示例，不含真实客户、凭据或服务器信息。

## 2. 审查方法、覆盖与限制

本次使用项目 Impeccable 的 audit 流程，审阅真实模板与公共交互，再沿路由、会话服务、业务服务、仓储核对可操作能力。设计判断与确定性缺陷分别记录。

### 2.1 源码覆盖

完整审阅 30 个模板及 `src/homestay_bot/static/app.css`、`src/homestay_bot/static/admin.js`。下列路径均从仓库根目录起；模板定位使用 `page_actions/content` block、宏或对应表单作为符号依据。

| 区域 | 已审阅模板 | 本次结果 |
| --- | --- | --- |
| 公共外壳 | `src/homestay_bot/templates/layouts/admin.html`、`layouts/auth.html`；`components/ui.html`、`components/icons.html`、`components/workbench.html` | 分组导航、跳过链接、共享按钮、状态、移动抽屉已有基础；需保留 |
| 登录与账号 | `auth/login.html`、`auth/change_password.html`、`account/detail.html` | 未确认新的阻断性问题；未对密码管理做范围外改造 |
| 工作台与运营 | `admin/dashboard.html`、`admin/attention.html`、`admin/operations.html` | 来源可信度、本地准备记录与订单事实已有区分；涉及 F07 |
| 诊断与系统 | `admin/diagnostics.html`、`admin/audits.html`、`admin/debug.html`、`admin/settings.html`、`admin/config_versions.html` | 诊断脱敏及失败降级保留；未送达缺少定位入口 F05 |
| 任务 | `tasks/index.html`、`tasks/detail.html` | 批量选择反馈 F12、状态颜色 F15、来源回跳 F10 |
| 预订审批 | `approvals/index.html`、`approvals/detail.html` | 人工核验结果回填属于流程建议 F09，不能据此放开真实下单 |
| 客诉 | `complaints/index.html`、`complaints/edit.html` | 状态动作 F01、发送草稿 F02、阅读标识 F15 |
| 客户 | `customers/index.html`、`customers/detail.html`、`customers/merge.html` | 合并判断依据 F06、返回上下文 F10、异步反馈 F14、颜色 F15 |
| 知识 | `knowledge/index.html`、`knowledge/detail.html`、`knowledge/scope_fields.html` | 校验恢复 F03、有效条件 F04、删除 F08、返回 F10、密度 F11 |
| 房源 | `properties/index.html`、`properties/detail.html` | 停用房源标识 F13；凭证权限与秘密不回显保持原约束 |

### 2.2 本地页面复核

- 使用 `tests/integration` 现有 TestClient 装配和服务桩生成页面，使用真实模板、CSS、JS；客诉四种状态直接以合成数据渲染真实模板。
- 临时 HTTP 预览服务只允许 GET，POST 返回 405。没有对生产页面提交表单。
- 桌面 `1440×900` 与手机 `390×844`，33 组代表性页面/状态，共 66 次抽查；包括客户和房源页签、任务归档视图、客诉 `pending_analysis/send_queued/sent/delivery_failed`。
- 抽查未发现整页横向溢出，观察到每页一个 h1；这只覆盖对应模拟数据，不能推断所有房源数量、长文本、缩放比例或员工角色都通过。
- 实际观察到修改客诉 textarea 后，其值与发送表单隐藏 draft 不相等；没有点击真实发送，也没有验证外部收件。
- 待审核但启用的知识条目显示绿色「已启用」；后台检索过滤的事实由仓储与服务源码验证。
- 单候选加单条目知识页手机高度约 3297px；高度是模拟样本的观察值，不是性能门禁或固定验收阈值。
- 测试桩运营日期固定且重复，不能将其当成生产日期计算缺陷。
- 临时预览脚本和服务已移除/停止，浏览器审查标签已关闭、临时视口已恢复。没有保存可供 Claude 重用的截图文件或运行中的预览地址。

### 2.3 文档与标准依据

已按需检索 `YuMi民宿AI开发经验与防回归手册.md` 的「人工接管与客诉」「知识库与高频 FAQ」「客户 CRM、上下文与隐私」「后台管理台安全边界／高密度运营界面／系统诊断文案」。核心约束：

- 知识待审核不能用于回答；房间、日期与触发条件仍由现有检索边界决定。
- 客诉发送需权限、一次性 CSRF、版本与审计；入队不等于平台受理，平台受理不等于客人收到。
- 合并复核保持安全投影；诊断不加载或复制客人正文。
- 页面优化不能破坏角色、CSRF、确认、脏表单提示、分页与受控失败恢复。

知识删除的历史意图并不一致：`docs/specs/2026-09-23_knowledge-draft-import-spec.md::目标` 提到后台审核、修改、删除；`docs/specs/2026-09-29_knowledge-images-spec.md::六、实施记录` 明确说明后台没有条目删除入口。两者只作设计线索；当前事实以 `KnowledgeAdminService/toggle_knowledge` 为准。

日期文字对比度参考 [W3C WCAG 2.2 Contrast Minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)。没有做完整 WCAG 合规认证；不能仅根据颜色计算宣称整个产品不合规。

## 3. 问题清单及详细依据

沿用上一条审查回复的编号 F01–F15。P1 表示优先修复的显著操作风险或可读性问题，P2 表示有绕行方式的功能缺口/优化。严重度是本地设计审查判断，不是生产事故级别。F09 等建议不等于已证实违反既定业务规则。

### F01 · P1 · 客诉动作未随状态变化

- 证据：`src/homestay_bot/templates/complaints/edit.html::content` 第 22–23 行无状态分支，始终显示保存、发送、重新分析、关闭。
- 后端依据：`src/homestay_bot/repositories/complaints.py::SQLAlchemyComplaintRepository.update_draft`（135 行）只允许 READY_FOR_REVIEW、EDITING；`mark_send_queued`（177 行）另允许 DELIVERY_FAILED；`routes/complaints.py::_action`（125 行）只显式处理版本冲突。
- 本地观察：四种状态页面均展示同样的启用按钮。分析中、发送中、已发送的保存/发送与服务端限制不一致；发送失败也不能走普通保存入口。
- 限定：`mark_returned/mark_cancelled` 当前只校验版本，没有相同的状态白名单。不能声称所有四种动作都被后端拒绝，也不能未经决策收紧终态重新分析规则。
- 非法操作的真实表现（2026-10-01 复核补充）：`routes/complaints.py::_action`（146 行）只捕获 ComplaintVersionConflict。仓储的「当前客诉状态不允许编辑/发送」、服务的「回复内容不能为空」「客诉回复发送任务已存在或不可重试」均为 ValueError，没有处理器，页面得到 HTTP 500；一次性令牌已被消费。事务未提交，数据不被写坏。
- 在途操作造成投递记录失真（2026-10-01 复核补充，正确性缺陷，不属于 D2 业务选择）：
  - 发送中关闭或退回：出站任务不因客诉状态而停发（`application.py::_guest_reply_is_stale` 不检查客诉状态）；`repositories/complaints.py::mark_delivery_sent/mark_delivery_failed` 只在 SEND_QUEUED、DELIVERY_FAILED 时写回，于是客人照常收到回复，客诉仍显示已关闭或已退回，投递回写被丢弃。
  - 退回后再发送：非 DELIVERY_FAILED 状态下出站编号不带阶段后缀，与第一次相同，`_enqueue_guest_text` 判为重复，新草稿发不出去（500）。
  - 发送失败后的自动重试窗口：`_run_worker_loop::send_guest`（3078–3087 行）先把客诉写成 DELIVERY_FAILED，再对连接失败与 45009 限流抛可重试异常；`repositories/jobs.py::mark_failed`（401–406 行）把原任务放回 PENDING。此时管理员点重发，会以 `retry-{version}` 阶段登记第二个任务，两个任务都可能发出，客人收到重复回复。
  - 回写归属：`_record_complaint_delivery` 只按客诉主键回写，不区分是哪一次发送，旧任务的结果会覆盖新尝试。
  - 以上由 Codex 在临时 SQLite、真实 Session 服务与 worker 上复现，见 `docs/reviews/2026-10-01_codex-to-claude-frontend-review-handoff.md` §9 E1–E4、E8。
- 建议：页面反映已有保存/发送资格；在途判定与回写归属按 Spec §4.1.1 修复；终态重开范围见 Spec D2。服务端仍独立校验，非法/过期状态返回可恢复的页面。

### F02 · P1 · 回复编辑和发送的正文来源不同

- 证据：`templates/complaints/edit.html::人工回复草稿` 的 textarea 在 `/save` 表单；`::客诉处理` 的 `/send` 表单另含初始化的隐藏 `draft`。
- 调用链：`routes/complaints.py::complaint_send/_action` → `services/complaint_admin_service.py::ComplaintAdminService.send`（85 行）采用提交的 draft，然后登记发送。
- 本地反例：页面初始正文「本地模拟已保存回复」；编辑 textarea 为「本地模拟新修改回复」后，两值比较为 false。
- 现有防护：发送确认明确说「已保存草稿」，`static/admin.js::dirtyForms` 与跨表单检查有提醒。问题是正文来源和按钮距离造成认知负担，不是完全没有防护，也未证明已经发生误发。
- 建议：推荐同一表单保存/发送，发送当前编辑内容；确认正文与实际提交一致。另一安全选项为编辑后先禁用发送，明确要求保存，见 D1。

### F03 · P1 · 知识表单业务校验返回 JSON

- 证据：`routes/knowledge.py::update_knowledge`（827 行）消费令牌后调用 `_fields`（561 行）；范围/日期错误被转成 HTTPException(422)。
- `src/homestay_bot/main.py::app.add_exception_handler`（67 行）只注册 OperationRefused 页面处理；`routes/page_errors.py::handle_operation_refused`（92 行）不能覆盖该 HTTPException。
- 复现：管理员本地 TestClient 编辑 scope=property、property_id 留空，带 Accept:text/html；返回 422、Content-Type=application/json，知识数量未增加。
- 影响：服务端正确拒绝无效数据，但页面不能就地改错，一次性令牌又已消费。不能通过取消校验或复用旧令牌来“修复”。
- 建议：保留安全的非秘密字段、字段错误、重新签发令牌；未知异常仍脱敏。只覆盖本次确认的员工表单，不将所有 API/认证错误一律改为成功跳转。

### F04 · P1 · 启用与实际适用条件混淆

- 证据：`templates/knowledge/index.html::entry-card__actions`（38 行）、`knowledge/detail.html::section-heading`（8 行）仅以 is_enabled 生成徽章。
- 后端：`repositories/knowledge.py::SQLAlchemyKnowledgeRepository.list_active`（21 行）排除 unreviewed；`services/knowledge_service.py::KnowledgeService.retrieve_detailed` 还按房间、目标日期区间和 trigger 条件过滤。
- 反例：is_enabled=True、scope=unreviewed 显示绿色「已启用」，却不参与回答。
- 限定：不能根据「今天已过期」推断任何目标日期都不可引用；现有检索按目标日期区间判断。待审核排除则不依赖目标日期。
- 建议：分别展示启停、审核范围、有效期/触发条件，说明日期适用依查询目标而定；不为展示新增第二套检索算法。

### F05 · P1 · 未送达告警缺少定位处理对象的入口

- 证据：`templates/admin/diagnostics.html::delivery-chains`（80、96 行）要求按消息编号在企业微信回复，编号只有 root_id，没有客户或会话入口。
- 后端：`repositories/admin_diagnostics.py::DeliveryChain`（52 行）只有编号、阶段、次数、机器码、时间；`delivery_failure_rollup`（267 行）投影 Message.id，未提供页面定位关联。
- 影响：能发现风险，却无法从当前页面定位客人。本报告未证明企业微信客户端可以按这个内部编号检索，不能继续把此文案当成充分操作指引。
- 建议：提供经过管理员权限验证的客户对话深链接，只增加必要关联，不把原始正文/外部身份放进诊断结果或审计。暂不增加一键重发、自动人工回复或“已处理”写状态。
- 复核补充（2026-10-01）：客户对话页签已支持 `before_message_id`，`repositories/customers.py::SQLAlchemyCustomerRepository.customer_messages` 按 `Message.id < before_message_id` 取页。链接带 `root_id + 1` 可让目标消息成为该页最新一条。但同一查询排除带 `cleared_by` 标记（「清空测试数据」留下）的行，这类目标不会出现在页面中；正文为空而无该标记的行仍可查到（Codex 探针 E6、E7）。

### F06 · P1 · 合并最终确认缺少判断依据

- 证据：`templates/customers/merge.html::compare-grid`（6 行）只显示名字、内部 ID、关联数量，没有档案链接。
- 后端：`services/customer_admin_service.py::MergeCustomerCard/_merge_card/get_merge_detail`；`repositories/customers.py::_safe_merge_customer` 当前只查询 ID 与姓名。这是刻意的安全投影，不是漏传已有完整客户对象。
- 风险判断：同名与计数无法证明同一客户，最终操作又迁移大量关联。未观察实际错误合并，属于高风险决策页面的设计不足。
- 最小建议：先加两份既有管理员档案查看链接，保留方向和二次确认。内联电话或订单佐证须先确认安全字段范围。
- 重要限制：`CustomerAdminService._display_phone` 虽字段名 masked_phone，当前会返回完整号码，不能直接复用并称其为脱敏投影。
- 复核补充（2026-10-01）：完整号码是 1.41.0 起的既定展示决策（见 `_display_phone` 注释），客户列表、详情和合并目标搜索（`customers/detail.html:123`）已向同一批管理员展示。合并复核页内联两侧电话不扩大受众，可作为 D5 推荐。实现时注意 `repositories/customers.py::_safe_merge_customer` 返回字典且只查 ID、姓名，`_display_phone` 用 getattr 取值，不能直接给字典加键后传入；应在仓储—服务映射处补最小电话投影并复用已有解密能力。

### F07 · P1 · 手机空闲日期可读性不足

- 证据：`static/app.css::.cal-m__day/.cal-m__dnum/.cal-m__day--free .cal-m__dnum`（656–662 行）为 14px，前景 #94a3b8、背景 #f8fafc；按相对亮度公式约 2.45:1。
- 模板：`templates/components/ui.html::_room_timeline_mobile` 将日期条设为 aria-hidden，与后面的订单卡分工。不能把这理解为日期对视觉用户没有用途，也不能仅据 aria-hidden 宣称读屏缺失。
- 建议：用现有次级正文色表示日期，用背景、描边与状态文本表示空闲；检查今天标识和冲突标识仍清楚。P1 是可读性优先级，不是整页 WCAG 合规结论。

### F08 · P2 · 知识条目没有删除入口

- 证据：`templates/knowledge/index.html::entry-card__actions` 只有启停，详情只有配图删除；`routes/knowledge.py::toggle_knowledge`（953 行）仅允许 enable/disable；`KnowledgeAdminServicePort/KnowledgeAdminService` 无条目 delete 方法。
- 删除关联：`domain/models.py::KnowledgeImage.knowledge_entry_id`、`KnowledgeEmbedding.entry_id` 已声明 CASCADE；`KnowledgeCandidate.knowledge_entry_id` 无 ondelete，不能直接删主记录而忽略该关联。
- 已有可复用能力：`routes/knowledge.py::KnowledgeAdminService.delete_image`（370 行）在同一事务登记 ATTACHMENT_CLEANUP_JOB_TYPE、审计并提交；`application.py::SessionKnowledgeAdminService.delete_image/_build_attachment_cleanup_handler` 接到真实会话与文件清理。
- 建议：永久删除与停用分开，按 D3/D4 确认语义后，复用现有事务内清理登记；不新增另一套回收站/文件清理系统。
- 未覆盖：已经入队并携带旧文字/图片的回复能否撤回未检验；删除按钮不能承诺撤回历史或在途回复。
- 复核补充（2026-10-01）：
  - 路由遮挡：`routes/knowledge.py::toggle_knowledge` 注册为 `POST /{entry_id}/{action}`，非 enable/disable 返回 404。注册在它之后的 `/{entry_id}/delete` 会先被它截走（Codex 探针 E9）。
  - 候选静默：`repositories/faq_candidates.py::get_or_create` 按 canonical_key 复用旧候选，`add_occurrence` 对非 OPEN 候选不计数，`reopen_expired` 只重开 SNOOZED。删除知识时若保留 CONVERTED，同一规范化问题此后不再计数和提醒（Codex 探针 E5）。处理方式见 Spec D4。

### F09 · P2 · 审批人工核验后没有结果回填

- 证据：`templates/approvals/detail.html::状态处理`（35–43 行）只提示外部核验；needs_review/conflict 的本页写入口是拒绝。
- 后端：`services/approval_page_service.py::list_pending` 纳入这两类；`services/booking_service.py::BookingService.confirm_and_create` 对非 PENDING/CREATING/BOOKED 不继续创建，避免盲目重放。
- 分类：流程建议，未确认是现有需求遗漏；外部人工处理可能是原设计边界。
- 建议：先确认 D6。若需回填已存在订单或重新核验，单独定义证据、角色、幂等与状态转移；不能只放开确认按钮。

### F10 · P2 · 列表—详情—操作丢失来源

- 证据：`templates/knowledge/index.html::entry-card` 不传 return_to，`knowledge/detail.html::page_actions/content` 固定回列表且 edit 表单未带来源；`customers/index.html::列表链接`、`customers/detail.html::page_actions` 同样固定；客户服务页 `::task`（65、70 行）进入任务未传来源。
- 可复用：`templates/tasks/index.html::处理任务链接` 已传来源；`tasks/detail.html` 和相关路由沿用 return_to；`routes/page_errors.py::safe_return_path` 保留安全站内 query 和锚点。
- 建议：贯通筛选、页码、客户页签和锚点；来源不能靠 Referer 推断，应用已有 no-referrer 策略。

### F11 · P2 · 候选与现有知识挤在同一长页

- 证据：`knowledge/index.html::knowledge-candidates`（14–31 行）每个候选展开完整双语/范围表单，随后才出现现有知识。路由 `knowledge_index` 对候选和条目独立分页，每页上限 50。
- 观察：模拟单候选页面手机很长；这是信息架构判断，不是已测出的后端性能故障。
- 建议：URL 驱动的「现有知识／待审核候选」分区，默认现有知识，候选以摘要和可展开编辑呈现；保留独立分页与角色。
- 原测试 `test_knowledge_index_orders_filters_candidates_and_entries` 固化旧顺序；确认新设计后应更新为实际用户行为约束，而非机械维护旧字符串顺序。

### F12 · P2 · 批量选择缺少即时计数与手机全选

- 证据：`tasks/index.html::selection-actions`（58–69 行）展示本页总量及资格说明，没有实时已选计数；data-select-all 仅在 `.responsive-table` 表头。
- 可复用：`static/admin.js::selectableBoxes/syncMirroredSelection/selectionForms` 已处理镜像与唯一提交。旧重复勾选问题已修复，不能重报。
- 建议：基于当前可提交的唯一任务 ID 显示已选数量，移动端也能全选/清空；零选择给出明确反馈。混合资格操作仍由服务端整批校验，不悄悄过滤部分条目。

### F13 · P2 · 房源停用状态未显示

- 证据：`services/property_admin_service.py::PropertyOverview` 有 is_active；`list_all`（104 行）返回全部；`templates/properties/index.html::桌面表/移动卡片`（8、10 行）均未呈现 is_active。
- 建议：同一列表显示「已停用」和启停筛选，默认保留当前全部房源语义。不要用准备记录状态代替启停，也不要从页面删除 Hostex 房源。

### F14 · P2 · 客户重新整理缺少本次作业反馈

- 证据：`routes/customers.py::refresh_customer_context`（540 行）入队后直接回 memory 页签；`customers/detail.html::handover`（103–107 行）只要求稍等刷新。
- 后端：`CustomerAdminService.refresh_context`（346 行）已有冷却与持久化 job 去重；`repositories/customers.py::latest_context_refresh_at` 读取最新作业时间；不要新增另一套队列。
- 可复用：`web.py::set_page_notice/pop_page_notice`、`layouts/admin.html::notice` 已能展示一次成功提示。
- 建议：最小先补「已排队」；D7 确认后可只读展示对应作业状态和安全失败原因。摘要更新时间可能来自自动维护，不能当成本次作业已完成的唯一证据。

### F15 · P2 · 状态语言、时间与颜色不一致

- 证据：`complaints/edit.html::风险概览/完整对话`（8、21 行）直接展示 risk_level，对话 origin 经 status_zh 但 `web.py::_STATUS_LABELS` 不含 MessageOrigin，未显示消息时间；合成页呈现 high、guest。
- `tasks/detail.html::section-heading`（9 行）所有状态都使用 warning；`customers/index.html::桌面表/移动卡片` 对 in_house 分别用 success/info。
- 复核补充（2026-10-01）：`customers/detail.html:15` 把完整号码标为「脱敏电话」，与 `_display_phone` 的实际行为不符，改为「电话」；字段名 masked_phone 本轮不改。
- 建议：复用 `status_zh/datetime_zh` 的受控映射及现有 badge 宏；完成、取消等终态不再一律警告色。未知机器码保守显示，不将其伪装为成功；显示消息发生时间，不能用页面加载时刻补造。

## 4. 已有验证证据与可复现入口

### 4.1 上一审查轮已完成的检查

以下 10 项在本次会话的前端审查轮通过：**10 passed，1 条既有 Starlette/httpx 弃用警告，8.44 秒**。写文档没有重跑该组合；源码与测试未修改，可沿用为审查基线，不是未来实现验收。

```sh
.venv/bin/python -m pytest -q \
  tests/browser/test_admin_interactions.py::test_drawer_accessibility \
  tests/browser/test_admin_interactions.py::test_unchecking_a_visible_box_also_drops_its_hidden_copy \
  tests/browser/test_admin_interactions.py::test_selection_survives_a_layout_switch_without_duplicating \
  tests/browser/test_admin_interactions.py::test_cross_form_submission \
  tests/browser/test_admin_interactions.py::test_confirm_text_follows_the_action_that_was_clicked \
  tests/integration/test_task_routes.py::test_bulk_archive_carries_every_filter_the_list_applied \
  tests/integration/test_task_routes.py::test_assign_form_preselects_the_task_own_room_and_employee \
  tests/integration/test_approval_routes.py::test_non_pending_approval_cannot_render_real_order_form \
  tests/integration/test_complaint_repository.py::test_complaint_page_uses_shell_and_safe_editing_controls \
  tests/integration/test_knowledge_routes.py::test_knowledge_scope_form_rejects_roomless_property_and_invalid_dates
```

这些测试证明既有防护，并未覆盖本报告所有新反例。不得宣称 15 项问题已有完整回归覆盖。

### 4.2 文档编写轮重新执行的 F03 最小复现

只使用本地 TestClient 与合成服务，不接生产；输出不打印令牌、正文或 Cookie：

```sh
PYTHONPATH=src:tests:tests/integration .venv/bin/python - <<'PY'
import re
from test_knowledge_routes import build_client
from homestay_bot.domain.enums import EmployeeRole

client, service = build_client(EmployeeRole.ADMIN)
page = client.get('/employee/knowledge/1')
token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
response = client.post('/employee/knowledge/1/edit', headers={'accept': 'text/html'}, data={
    'csrf_token': token, 'category': '合成示例',
    'question_zh': '何时入住？', 'answer_zh': '下午三点后。',
    'question_en': 'Check-in?', 'answer_en': 'After 3 PM.',
    'scope': 'property', 'property_id': '',
})
print({'status': response.status_code,
       'content_type': response.headers['content-type'],
       'entry_count': len(service.entries)})
PY
```

2026-10-01 实测：`{'status': 422, 'content_type': 'application/json', 'entry_count': 1}`，命令成功退出，出现同一条既有弃用警告。确认的是当前错误呈现；模拟服务不证明真实应用的事务装配。

### 4.3 对比度复算

```sh
.venv/bin/python - <<'PY'
def luminance(color):
    rgb = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
a, b = map(luminance, ('94a3b8', 'f8fafc'))
print(round((max(a, b) + .05) / (min(a, b) + .05), 2))
PY
```

当前样式结果 2.45。未来修改后应读取浏览器最终计算颜色复算；不要把这一常量脚本写成会永远通过的回归测试。

### 4.4 检测器及旧问题

- Impeccable detector 对模板扫描报告 4 个警告，来自两个外壳共享样式入口的重复提示，涉及今日日期线与 repeating-gradient 条纹。
- 人工核对用途为日历网格、今日定位、冲突区分，判为本任务下的误报；不建议删除这些信息。
- 旧审查的桌面/手机重复提交、归档漏筛选、任务分派默认值、手机隐藏记录，已由当前源码及限定测试核对修复，不计入新问题。
- 原「审查参考分」已于 2026-10-01 复核时删除：没有可复现的计算依据，避免被当成门禁或对比指标。

## 5. Claude 接手工作与实施前决策

1. 核对基线及用户最新授权；先完整阅读配套 Spec 的 D1–D7，不能从本报告推断决策已确认。
2. 优先处理 F01/F02/F03 的最小闭环；知识 F04/F08/F11 在同一内容管理边界整体规划。诊断定位、客户佐证与审批回填分别验收，不强行串成一个通用状态框架。
3. 执行前核查所有受影响调用方，特别是 `application.py::SessionKnowledgeAdminService/SessionComplaintAdminService/SessionCustomerAdminService/SessionAdminDiagnosticsRepository`；历史上替身通过而生产会话装配漏方法的问题不能重现。
4. 涉及删除、终态操作、身份内联展示、审批核验、作业查询时按 Spec 明确规则；没有确认的工作包暂不实现，但已获授权且独立的工作可继续。
5. 编码后按风险运行受影响验证。已有结果仍覆盖最终代码时复用；不因写交接或提交阶段重复跑全量。`application.py` 装配等触发项目特殊全量门禁时另执行。
6. 本次没有全量业务测试、真实模型、隔离 PostgreSQL、生产登录浏览器、外部收件、发布/回滚验收。不可互相替代。

## 6. 可直接交给 Claude 的 Prompt

> 请先阅读本项目 AGENTS.md、docs/specs/2026-10-01_frontend-audit-handoff.md 和 docs/specs/2026-10-01_frontend-improvement-spec.md。目标是基于本地前端审查补齐操作流程，保留现有 Jinja、原生表单、公共 CSS/JS 和业务安全边界。先核对 HEAD 与差异，重验发生变化的证据；逐项区分确定性缺陷、设计判断及待确认业务建议。当前仅授权交接文档，Spec 尚未批准：按证据、功能、风险决策分段确认，完整确认并收到用户明确“开始”后才修改实现。若用户在接手前已确认本轮范围和开始指令，直接沿用授权。F01–F15 均需保持映射，不将未选工作包偷偷纳入实现。复用安全回跳、通知、CSRF、发送 outbox、清理 job 与状态映射；新增或修改函数与关键逻辑写清楚中文注释。优先用真实服务、临时 SQLite 和浏览器验证正文、状态、来源、权限与事务；不以模板字符串测试代替页面行为。未经本轮明确授权，不提交、推送、部署、删除真实知识、调用真实 DeepSeek/Hostex/企业微信或发送消息。不读取受保护的 YuMi民宿AI项目总结.txt。实施结束报告文件/符号、行为变化、验证与未覆盖风险；发版时另遵守正式更新日志、发布记录及提交门禁。
