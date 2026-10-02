# 任务批量操作、工作台行动与后台可靠性优化 Spec

日期：2026-10-02。状态：**2026-10-02 用户在 Codex 审查后明确授权「开始改代码吧」。执行 F1–F12 与本轮三项审查修订；不提交、推送或部署。**

来源：

- Claude 提供的 impeccable critique 与原 Spec；评审归档在 `.impeccable/critique/2026-10-02T02-08-13Z__src-homestay-bot-templates.md`。原文记录的选择是「任务批量操作优先，P1+P2 全做，混合批次在前端拦住」，本次保留其产品决定。
- 本聊天的 Impeccable 4.4 审查与已确认的完整优化范围：未保存保护、日历日期、行动顺序、任务脱敏摘要、逐项资格、手机点击区、客诉风险中文。此前 Spec 在聊天中，尚无另一份落盘文件；本文件统一承载两边方案。
- Claude 原 Spec 记录的评审方式为临时 SQLite、演示数据、不加载外部服务配置及屏蔽出网代理；本聊天此前也使用隔离演示页面取证。本次重新核对源码和文档，没有重新运行浏览器验收。

源码基线：`8c3ff95`，`main`，版本 1.65.0。审查时工作区有未跟踪的 `.impeccable/critique/` 和本 Spec，暂无已跟踪文件改动；本 Spec 尚未进入 Git 提交。以下路径除 `docs/`、`tests/` 等明确标注者外，均相对于 `src/homestay_bot/`。历史评审与生产状态不替代这次源码核验。

与 1.63.0（`docs/specs/2026-10-01_frontend-improvement-spec.md`）的关系：

- 1.63.0 的 F12 已经做了已选计数、手机全选和清空，F15 统一了状态颜色。
- 本 Spec 在 F12 之上补「按动作资格选择」，并修复 F15 漏掉的任务列表状态色。不回退、不重做 1.63.0 已有的行为。

## 1. 第一段：现状证据

### E1 批量选择不区分动作资格

- `static/admin.js` 第 362–368 行：`selectionForms` 里的全选，对 `selectableBoxes(form)` 全部勾选。
- 批量按钮是否显示，只看本页有没有合格项：`templates/tasks/index.html` 第 62–70 行，依据 `assignable_ids`、`archivable_count`、`cancellable_count`。按钮不跟随实际勾选变化。
- 实测：开放队列点表头全选后，计数为「已选 9 项」，「分派勾选的任务」仍然可点。页面同时写着「5 条可分派」。
- 资格规则现在由路由和服务端执行层分别判断，前端没有逐项资格：
  - 可分派：`routes/tasks.py::task_index` 的 `assignable_ids`，即待确认、待分派，且有房间和服务日期；
  - 可取消：非终态，`cancellable_count`；
  - 可归档：`ARCHIVABLE_TASK_STATUSES`；
  - 可永久删除：已归档。
- 服务端校验混入不合格项就整批拒绝，这是有意设计（`repositories/operations.py::archive_selected` 的 docstring）。本 Spec 保留。

### E2 拒绝原因用任务编号，界面上看不到编号

用户已能看到拒绝原因：`main.py::app` 注册 `routes/page_errors.py::handle_operation_refused`，HTML 请求以 PRG 方式回跳并显示；接口仍返回 JSON。不能再以「被压成 409」作为现状。现在的问题是原因只给编号列表，共 5 处：

| 位置 | 现文案 |
| --- | --- |
| `repositories/operations.py` 第 677 行（批量分派） | `只有待确认或待分派的任务可以分派，以下状态不符：[3, 5]` |
| 同文件第 685 行（批量分派） | `以下任务缺少房间或服务日期，请先逐条补齐：[…]` |
| 同文件第 723 行，`require_purgeable` | `只有已归档的任务可以永久删除，以下尚未归档：[…]` |
| 同文件第 990 行，`archive_selected` | `…以下仍在处理中：[…]` |
| `services/task_page_service.py` 第 593 行，`cancel_many` | `以下任务已处于终态，无法取消：3、5` |

- 列表行与卡片都不显示任务编号。
- 表格勾选框的 `aria-label` 是「选择任务 {id}」（`tasks/index.html` 第 54 行）；手机卡片的勾选框只读作「选择」（第 56 行）。
- 5 处校验时都有 `BusinessTask` ORM 对象，但 `cancel_many` 当前把不合格对象先转成编号字符串；应保留对象供错误描述。房间号或标题需按 `PropertyProfile` 批量查询。
- `web.py::set_page_error` 把消息限制在 200 字符；直接拼接 5 条长房源名会截断原因或下一步。

### E3 批量操作成功没有反馈，落点不一致

- `routes/tasks.py::assign_selected_tasks` 成功后固定跳到 `/employee/tasks`，筛选丢失。
- `archive_selected_tasks` 成功后固定跳到 `?archived=true`。
- `cancel_selected_tasks` 回到 `return_to`。
- 三处都没有调用 `web.py::set_page_notice`，`task_index` 也不传 `notice`。`web.py::base_template_context` 只自动读取失败提示；客户页已主动读取成功提示。
- `TaskPageService.assign_many`、`cancel_many`、`archive_many` 和 `application.py::SessionTaskPageService` 对应方法已经返回实际数量，无须修改返回类型或会话门面。

### E4 手机批量栏

- 全局规则 `label > input { width: 100% }`（`app.css` 第 72 行）作用到了 `.selection-bar__all`（第 212 行）里的复选框。第 106 行的复选框宽度规则没有覆盖它，所以全选框被拉宽，「全选本页」文字被挤成竖排。
- `.card-select input` 只有 18px（第 533 行），整个勾选区实测约 55×24。勾选框在卡片外的 `<li>` 顶部，看不出属于哪张卡。
- `.selection-actions` 在列表最底部（第 534、538 行），不吸附。红色「取消勾选的任务」和「分派」并排。

### E5 状态色在列表和详情里不一致

- `tasks/index.html` 第 54、56 行：待确认、待分派、待检查用 warning，其余一律 neutral。
- `tasks/detail.html` 第 10 行：已完成 success，已分派和进行中 info，已取消和已失效 neutral，其余 warning。
- 同一个「进行中」在列表里是灰色，在详情里是蓝色。

### E6 工作台「先处理」在同步过期时没有出路

- 同步时间是全局的：`routes/admin.py::admin_dashboard` 把 `hostex_data_last_success` 传给 `AdminOperationsService.snapshot`。超过 6 小时，`_source_is_stale` 会把**所有**房间标为过期。
- `services/admin_operations_service.py::_next_step` 第 1123 行：同步过期时第一个分支就返回「先确认百居易实时房态」，并且不带 URL。逾期任务、开放任务、维修这些**本地数据**的动作也被盖住了。
- `RoomOperationItem.needs_attention` 第 245 行包含 `source_stale`，所以过期时所有在用房间都会进入「先处理」。`templates/admin/dashboard.html::first-list` 每行显示同一个原因，加一句「暂无可直接进入的入口」。
- 隔离演示数据能复现；本次没有核验生产同步状态，不推断生产此刻是否过期。
- 运营页 `admin/operations.html` 第 12 行已经有全局过期提示，工作台没有。
- 「需人工关注」（`templates/admin/dashboard.html::metrics-risk`）复用的是「待我关注」页的计数 `count_attention`，统计客人、客诉、待确认等人工事项，**不含**逾期任务。这是有意的口径（`services/admin_dashboard_service.py::_count_manual_attention` 的 docstring），只是标签没说清范围，旁边又紧挨着「逾期 4」，看起来像矛盾。

### E7 命名重复

- 侧边导航叫「工作台」，标签叫「今天 / 房间 / 全部任务」（`components/workbench.html`），H1 分别是「运营总览 / 入住安排 / 全部待办任务」。
- 无脚本导航（`layouts/admin.html` 第 67 行）还在用合并前的「总览 / 待我关注 / 房态与运营」。
- 任务页在 H1 下面还有 eyebrow「YuMi 任务中心」和 H2 `queue_heading`（`tasks/index.html` 第 8 行），手机上第一张卡出现在 y≈420px。
- 「开放任务」在运营页的卡片里可以点进去，在房源详情里不能点（`properties/detail.html` 第 17 行）。

### E8 错误态和危险操作

- 后台多处服务未装配时抛出 503，例如 `routes/approvals.py::_get_page_service`；默认 HTTP 异常处理会给浏览器 JSON。另有 `admin_debug.py`、`runtime_config.py` 已直接渲染 HTML 503，不应重写这些响应，也不能把所有 503 都归因于配置缺失。
- `knowledge/index.html` 第 55 行，每张卡上「停用」（启用状态时是 `button--danger`）和「永久删除」并排，都是红色。详情页已经有删除入口。
- 单条取消的确认文案是「确定取消这项任务吗？此操作会中止当前处理流程。」（`tasks/detail.html` 第 87 行），没写不可撤回。批量取消要求手输条数。
- `tasks/index.html` 第 69 行和 `customers/detail.html` 第 75 行的确认文案里写着「状态机没有回头路」。
- 降级横幅用的是 `role="alert"`（`layouts/admin.html` 第 70 行），每次进页面都会打断读屏。

### E9 与本聊天方案的缺口

- `templates/admin/operations.html::room-status-form` 与 `templates/tasks/index.html::create-task` 表单没有 `data-unsaved-warning`。`static/admin.js::dirtyForms` 只记录带标记的表单，`initOperationsCountdown` 跨计划节点时会刷新；隔离浏览器中已复现未保存房态选择被清空。
- `templates/components/ui.html::_calendar_segments` 的订单条只有姓名、晚数、延续状态，`room_timeline` 的行程列表也没有日期。移动宏 `_room_timeline_mobile` 已使用 `bar.start_label`、`end_label`，桌面可直接复用。
- `templates/admin/operations.html` 在完整时间轴之后才显示 `.room-next-action`；房间 `article` 的 `aria-labelledby` 指向自身，未指向房间标题。
- `services/task_page_service.py::TaskListItem` 没有摘要或归档字段；`list_for` 未返回说明，而 `detail_for` 已通过 `_safe_description` 隐藏手机号与详细地址。`repositories/customers.py::customer_detail` 的服务任务投影还缺 `property_id`、`archived_at`。
- `templates/complaints/index.html` 的桌面风险徽标与手机文字直接显示 `risk_level`；`web.py::complaint_risk_zh` 已有中文与未知值「待核实」映射。

## 2. 合并后的产品决定（2026-10-02）

D1–D12 保留 Claude 原 Spec 记录的决定；D13–D16 来自本聊天此前确认的范围。2026-10-02 用户已确认合并范围并明确授权开始编码；提交、推送与部署不在本次授权内。

| 编号 | 决定 |
| --- | --- |
| D1 | 资格判断以服务端为准：每行渲染资格属性，规则复用路由里现有的判定，脚本只读属性、不复制规则。服务端整批拒绝保留，作为最后一道防线 |
| D2 | 勾选里有不合格项时，在前端拦住：按钮显示「可执行数/已选数」并禁用，就地说明原因，并给一个「只保留可执行的」按钮。不在提交时静默过滤 |
| D3 | 被拒后**不恢复**勾选。拒绝文案改成「类型·房间·日期（当前状态）」 |
| D4 | 分派、归档、取消成功后**留在原队列**，并显示成功提示 |
| D5 | 同步过期时：工作台只显示**一条全局提示**；每个房间只按本地事实给动作；依赖订单数据的入住、退房原因先不显示 |
| D6 | 「待我关注」**不改统计口径**，只把标签和说明写清楚 |
| D7 | 每个目的地只用一个名称，删掉重复标题，状态徽标改用公共宏 |
| D8 | **一并修**：浏览器请求后台页面遇到 503 时，返回一个独立的 HTML 错误页；接口请求仍返回 JSON |
| D9 | 知识库列表去掉「永久删除」，「停用」改成次级按钮 |
| D10 | 文案：单条取消补「不可撤回」，去掉工程术语，降级横幅改成 `role="status"` |
| D11 | 客户详情页的批量区与任务页共用同一套资格机制 |
| D12 | 候选发布目标 1.66.0，实施完成后按发版规则同步正式版本、CHANGELOG 与 `docs/releases/1.66.0.md`；提交、推送、部署另需当前明确授权 |
| D13 | 房态调整、新建任务复用现有未保存保护；有草稿时跨节点只提示，不自动刷新。不做自动保存或持久化草稿 |
| D14 | 房间卡把下一步放到完整日历之前；日历默认可见，保留三条轨道和跨段规则，补真实起止日期与正确的房间可访问名称 |
| D15 | 任务列表显示逐项动作资格、原因和最多 80 字符的安全摘要；先脱敏再截断，保留原有角色可见性 |
| D16 | 手机控件使用至少 44×44 的点击区，客诉风险复用现有中文过滤器 |

## 3. 功能点

路径都相对于 `src/homestay_bot/`。

### F1 每行带资格属性（D1、D11）

- 在 `services/task_page_service.py` 增加公开纯函数 `task_bulk_eligibility`，输入状态、房间编号、服务日期、归档时间，返回 `frozenset[str]`。有任务页与客户页两个真实调用方，不互相导入路由，不新增通用权限框架。
  - `assign`：待确认或待分派，且房间、日期均非空；`cancel`：非终态；`archive`：`ARCHIVABLE_TASK_STATUSES` 中的终态；`purge`：`archived_at` 非空。
  - 不额外添加服务端没有的资格条件；已归档终态再次归档是现有的成功无改动路径，实际数量为 0。员工选择、管理员身份、CSRF 与提交时状态仍由现有执行入口校验。
- `TaskListItem` 增加 `eligible_actions`，由 `TaskPageService.list_for` 使用 ORM 的真实字段计算；无须为了前端判断把归档时间暴露到浏览器。`routes/tasks.py::task_index` 从这组资格汇总本页计数。
- 客户页资格实际出自 `services/customer_admin_service.py::CustomerAdminService._localize_detail`，改为调用同一函数。`repositories/customers.py::customer_detail` 的服务任务查询补 `property_id`、`archived_at` 两个已有数据库字段；保持最多 50 条、客户归属和原有排序，不做迁移。保留原有 `can_archive`、`can_cancel` 模板语义。
- 两页表格和手机勾选框渲染一致的 `data-eligible`；动作按钮加 `data-requires`，批量表单标记自己的默认动作，供没有 `event.submitter` 的提交判断。客户页仍只提供归档、取消，不增分派或永久删除入口。
- 每条任务旁显示可执行动作；缺房间、缺日期等不可分派原因就地说明，颜色之外有文字。勾选名称为「选择任务 #{id}，{类型}，{房源}，{日期}」，兼顾可理解与唯一性。
- 表格与卡片的任务名旁以次要样式显示 `#{id}`，让同房间、同日期、同类型的两项任务也能与拒绝提示对应；不新增数据字段。
- 前端资格用于提示，不替代 `require_assignable`、`cancel_many`、`archive_selected`、`require_purgeable` 的最终校验、锁与事务；不为此次展示改写这些执行规则。

### F2 按钮跟随勾选，混入不合格项时拦截（D2）

- `static/admin.js::selectionForms` 只对带资格标记的批量表单启用增强。在 `refresh` 中按 `selectedTaskIds` 去重，对每个 `button[data-requires]`：
  - 统计已选中、并且 `data-eligible` 包含该动作的条数，记为 m；已选总数记为 n。
  - m = n > 0：按钮可用，文案后加「（n）」。
  - 0 < m < n：按钮显示「{动作}（m/n）」并禁用，提示「已选 n 条，其中 n−m 条不能{动作}」，提供 `type="button"` 的「只保留可{动作}的 m 条」。由用户明确点击后取消不合格项，再同步镜像与计数；不在提交时过滤。
  - m = 0 且 n > 0：按钮禁用，提示「已选的任务都不能{动作}」。
  - n = 0：保持 1.63.0 的「请先勾选任务」拦截。
- 使用原生 `disabled`；原因放在按钮外的可读提示区，以 `aria-describedby` 关联、`aria-live="polite"` 宣告。不要依赖禁用按钮获得焦点后才能读到原因。按钮保存原始文案，刷新不累加数量后缀。
- 资格拦截同时覆盖按钮点击和表单 `submit` 捕获阶段，在普通确认、手输确认和提交忙态之前执行。按实际提交按钮的 `data-requires` 判断；键盘隐式提交按表单默认动作判断。无资格或空选择均不发请求、不弹确认、不进入忙态。
- `requestSubmit` 等触发提交事件的路径同样拦截；脚本被绕过或页面状态过期仍由服务端整批拒绝。保留原有管理员、CSRF、手输条数、重复提交与唯一任务编号约束。
- 全选、清空、明确保留合格项、桌面／手机断点切换后，选择、资格计数、按钮和确认数同步。取消或删除的旧确认数在选择变化后清零，避免复用过期确认。
- 无脚本时增强提示与过滤控件隐藏；分派、归档可提交并由服务端判断，取消和永久删除仍因无法手输确认而被服务端拒绝。不能为了无脚本可用性放宽不可逆操作门禁。

### F3 拒绝文案可读（D3）

- `web.py` 当前同时包含纯中文格式助手与 Jinja 环境，仓储不得反向导入它。将现有 `_STATUS_LABELS`、`status_zh`、`date_zh` 原样移到不依赖 Web 的 `display.py`；`web.py` 导入并继续注册原名过滤器。只移动这三项，不顺带抽离其余助手或修改标签含义。
- `SQLAlchemyOperationsRepository` 新增 **异步** `describe_refused_tasks`，`TaskPageRepository` 协议同步声明异步方法。只在拒绝路径使用已经加载的不合格任务，一次 IN 查询房源编号／标题，包含停用房源，缺失房源或日期写「房间待确认／日期待补齐」。不逐条重新查询任务，不加载客人姓名、说明或凭证。
- 五处拒绝保留原规则及下一步，补「任务 #{id}·类型·房间·完整日期（当前状态）」；最多展示 3 条，其余写「另 N 条」。完整消息按现有 200 字符上限预算生成：先缩短长房源名，再减少完整条目，保留拒绝原因、总数和下一步，不能拼成长串后任由 `set_page_error` 截断。
- `cancel_many` 保留不合格对象后调用同一仓储助手。`LookupError` 的缺失任务语义、现有权限判断、PRG 与失败后不恢复勾选均保留。
- 仅补协议替身所需方法；`application.py::SessionTaskPageService._service` 已使用同一仓储，无须新增外层门面或修改装配。展示格式迁移需验证现有过滤器输出仍一致。

### F4 成功提示并留在原队列（D4）

- `assign_selected_tasks` 增加 `return_to` 表单字段，模板里已经有这个隐藏字段，路由没接收。成功后回到 `safe_return_path(return_to)`。
- `archive_selected_tasks` 成功后回到 `return_to`，不再固定跳到 `?archived=true`。
- 三个路由成功时都调用 `set_page_notice`：
  - 分派：「已分派 N 条给{员工}」；
  - 归档：「已归档 N 条，可在『已归档』中恢复」；
  - 取消：「已取消 N 条」。
- N 直接取现有服务及会话门面已返回的实际数量，不改返回类型。员工名取不到时用「所选员工」，不为提示追加查询。勾选归档为 0 时提示「所选任务已归档，本次没有新增归档」；按筛选归档为 0 时提示「当前筛选没有新增可归档任务」，不声称有数据变更。
- `task_index` 增加 `"notice": pop_page_notice(request) or None`。客户详情页本来就会取出 `notice`，不用改。
- `purge_selected_tasks` 和 `archive_filtered_tasks` 也补成功提示，不改它们的落点。
- 分派、归档、取消的成功与 `OperationRefused` 均回到 `safe_return_path(return_to)`；包括缺员工的提前拒绝。修正取消成功当前直接使用原始 `return_to` 的路径，统一拒绝外站地址，保留合法分页、筛选与锚点。失败不写成功提示，成功提示下一次 GET 展示一次后消失。

### F5 手机批量栏（E4）

- `app.css`：
  - `.selection-bar__all input { width: 20px; min-height: 20px; }`，与第 106 行一致；
  - `.card-select` 改成卡片左侧一个至少 44×44 的点击区。模板把 `<label class="card-select">` 放进卡片网格，和 `.mobile-record-card` 链接并列，不嵌套在链接里，避免点勾选时进入详情。
- 列表底部元素仅设 `sticky; bottom: 0` 不能保证勾选首条后立即出现。改为：宽度 ≤768px 且有选择时，操作区固定在视口底部，带背景、边线和安全区间距；只把计数、员工选择与动作放入固定区域，详细资格说明留在普通流中。
- 使用原生 `ResizeObserver` 读取实际高度，为表单预留等高空间；无选择、桌面宽度或无脚本时恢复静态位置。窗口缩放或提示变化不得遮住最后一条任务、分页、焦点或新建表单。短屏中动作区限制高度并允许内部纵向滚动。
- 危险操作与普通操作留 ≥16px 间距；窄屏可独立成行，不强行挤到「最右」。任务与客户两页共用这套样式，控件可见点击区至少 44×44。
- 无脚本时保持现在的静态位置。

### F6 状态徽标公共宏（D7、E5）

- `components/ui.html` 新增 `task_status_badge(status, label=none)`，用 `tasks/detail.html` 第 10 行的配色。
- `tasks/index.html` 两处、`tasks/detail.html`、`customers/detail.html` 的任务状态都改用它。
- 任务页传枚举；客户页由 `_localize_detail` 将受控任务状态转为枚举后复用 F3 的 `status_zh`，宏允许传入已生成的中文标签。不直接把原始状态字符串交给 `status_zh`（它会原样返回字符串）。未知任务状态显示待核实，徽标始终保留文字，不只靠颜色。

### F7 工作台先处理与关注口径（D5、D6）

- `_next_step` 在同步过期时不再一开始就返回，改为只走本地事实，依次是：
  1. 有逾期任务：「优先处理 N 项逾期任务」，去逾期筛选；
  2. 房态是维修：去房间详情；
  3. 有开放任务：「推进 N 项开放任务」，去该房间的任务；
  4. 房态是保洁或待检查：去房间详情；
  5. 都不是：返回「同步恢复前不判断入住安排」，不带 URL。
- 入住、退房、在住、下一位到店这些分支依赖订单数据，同步过期时跳过。
- 复用 `OperationsSnapshot.attention_rooms`，**不新增 `first_rooms` 属性**。`routes/admin.py::admin_dashboard` 在来源过期时过滤没有可靠动作 URL 的房间，并沿用现有 `attention_rooms` 模板键；这只是同一快照的显示子集。
- `_room_risk_sort_key` 不改：同步过期时 `_occupancy_status` 对所有房间返回 UNKNOWN，排序已自然落到逾期、准备状态与开放任务（Claude 复审 R1）。
- 当前 `templates/admin/operations.html` 已直接循环 `snapshot.rooms`，没有关注／稳定分区；保留所有房间、完整日历和来源提示，不重新引入折叠分区。
- `routes/admin.py::admin_dashboard` 传来源状态与最近同步时间；读取运营快照失败时显示「房间待处理信息暂时无法读取」，不能把失败当作「没有待处理」。
- `templates/admin/dashboard.html` 在「先处理」区域外显示一条全局「入住信息待核实」提示，引用现有可信窗口口径，不硬编码「已超过 6 小时」：缺同步时间、时间异常也会被 `_source_is_stale` 判为不可信。即使可行动房间为零，提示仍出现，并提供系统诊断和全部房间入口。
- 过期时不把本地记录宣称为实时房态，不在逐房间行动原因中使用旧订单到店／离店信息；原有时间轴与计划事件保留「上次同步计划，待核实」说明。
- 指标「需人工关注」改名为「待我关注」，下面用小字写「客人、客诉、待确认事项」。数字不变。

### F8 命名与层级（D7）

- 工作台标签用「今天 / 入住安排 / 任务中心」。`routes/admin.py::admin_dashboard` 的页标题改成「今天」；运营页保持「入住安排」。任务中心的 H1 按当前队列显示 `queue_heading`，队列名是所在范围，不强行改成所有队列同一个 H1。
- 任务页：删掉第 8 行 section-heading 里的 eyebrow 和 H2，H1 只保留队列标题。「管理员调度 / 员工执行」徽标移到 `page_actions`。
- 无脚本导航和主导航用一样的名称：工作台、任务中心、预订审批、房源管理、客户管理、投诉处理、知识库、AI 调试台、系统诊断、接口设置、账号安全。
- `properties/detail.html` 的「开放任务 N」改成链接，指向该房源的任务筛选。URL、角色与队列规则不变；工作台、入住安排和任务列表各保留一个可见 H1，删掉同名重复的正文标题。

### F9 错误态、危险按钮与文案（D8、D9、D10）

- 503 HTML 错误页：
  - 处理函数放在 `routes/page_errors.py`，`main.py::app` 为 `starlette.exceptions.HTTPException` 注册它。只对抛出的 HTTP 异常生效，已有 HTML 503、健康响应与 `OperationRefused` 的专用处理不变。
  - 同时满足以下条件才返回 HTML：状态码 503、方法 GET、明确接受 HTML、路径以 `/employee/` 开头、路径不是 `/employee/health`。复用 `_wants_html`；补 `text/html;q=0` 不接受 HTML 的判断，并验证其现有业务拒绝调用方。默认 `*/*`、JSON、POST、站外前缀仍走默认 JSON 处理器。
  - 其他情况全部交给 FastAPI 默认处理器，JSON 行为不变。
  - 新增 `templates/errors/unavailable.html`，继承 `layouts/auth.html`，不调用失效的页面服务或后台导航。显示固定文案「页面暂时无法使用，请联系管理员检查服务配置」，以及返回工作台、系统诊断入口；不直接展示任意 `exc.detail`，也不为识别配置错误维护文案白名单。
  - 保留 503 状态与异常的响应头（例如 `Retry-After`），继续由现有中间件提供 no-store；修复后重新访问原页面。404、403 等不扩展 HTML 化。
- 知识库：`knowledge/index.html` 第 55 行删掉列表上的永久删除表单，「停用」改成 `button--secondary`。详情页的删除入口保持不变。
- 文案：
  - 单条取消改为「确定取消这项任务吗？取消后不可撤回，任务会停止处理。」；
  - 批量取消的 `data-typed-confirm-detail` 改为「即将取消 {n} 条任务，取消后不可撤回。」，任务页和客户页两处都改；
  - 资格说明简写为「每个动作要求全部所选任务符合条件，状态变化时请刷新后重试」。保留服务端可能拒绝的事实，不能因 F2 拦截就删除失败提示；无脚本与状态过期仍会进入拒绝路径。
- `layouts/admin.html` 第 70 行降级横幅改为 `role="status"`。`page_error` 和 `error` 保持 `role="alert"`。

### F10 未保存内容保护（D13）

- `templates/admin/operations.html::room-status-form`、`templates/tasks/index.html::create-task` 表单加 `data-unsaved-warning`，复用 `static/admin.js::dirtyForms` 和现有提交／离开提示；正常提交不误报离开警告。
- `initOperationsCountdown` 已能识别脏表单：编辑房态后跨计划节点、午夜或页面恢复可见时，仅显示安排可能变化的提示，不刷新、不清空输入；无草稿且未提交时仍按原机制刷新。
- 不新增定时器、localStorage、自动保存或草稿接口。无脚本没有自动刷新；不额外承诺浏览器关闭后的草稿恢复。

### F11 房间行动与日历日期（D14、D16）

- `templates/admin/operations.html` 将 `.room-next-action` 放在 `ui.room_timeline` 之前；保持时间轴默认可见、三轨道高度、额外订单展开、重叠提示、跨段规则与紧凑总览。
- 房间 `article` 的 `aria-labelledby` 指向对应 H3 的唯一 ID，保留现有 `#room-{id}` 回跳锚点。
- `components/ui.html::_calendar_segments` 的可访问名称与 `room_timeline` 行程列表补原始日期，不从裁剪后的条形位置反推。实现核验发现 `start_label/end_label` 跨窗时只写「更早／延续更晚」：保留它们和既有几何，在 `TimelineBar` 增加 `start_date/end_date` 内部投影，直接由原始区间生成，用于桌面名称与完整行程列表；没有持久化或接口变更。继续说明同一笔跨段、更早开始、延续更晚及来源过期。
- 链接提供含客人、入住／退房日期、晚数与状态的可访问名称；非链接条提供可被辅助技术读取的日期文本。`title` 只作补充，不能作为唯一信息来源；行程列表给所有订单提供可见起止日期。
- 房间下一步按钮、房态调整与紧凑总览的 summary 点击区至少 44×44；保留原生 details、键盘操作和焦点可见性。不重做日历布局或引入动画库。

### F12 安全摘要与风险中文（D15、D16）

- `TaskListItem` 增加 `safe_summary`；`TaskPageService.list_for` 先对完整 `task.description` 调用现有 `_safe_description`，再压成单行、截到最多 80 字符（含省略号）。空说明显示「暂无说明」。
- `templates/tasks/index.html` 桌面任务列与手机卡片显示摘要，HTML 仍自动转义；不把原始说明放入 DOM、隐藏字段、title 或 data 属性。员工仍只看到原来有权看到的任务。
- 客户页不为摘要新增原始说明查询；本次只有任务列表新增这一展示字段。列表与客户页共用的资格及状态徽标按 F1、F6 执行。
- `templates/complaints/index.html` 表格和手机卡片均用现有 `complaint_risk_zh`；保留风险语义色，未知值显示「待核实」，不推断成低风险。

## 4. 风险与验证

| 风险 | 处理 |
| --- | --- |
| 提示资格与最终执行不一致 | 使用真实仓储、有效管理员与员工，按状态／字段／归档组合核对四种动作。每个动作使用独立任务或回滚事务，避免上一动作改变下一动作的前提；覆盖已归档再次归档的 0 条结果 |
| 状态过期、混合批次或脚本被绕过 | 最终校验与整批拒绝保留；混合批次必须验证所有任务状态及归档标记都未改变，而非只断言报错。失败回原页并读取最新数据，不恢复选择 |
| 客户页投影缺字段或共用脚本被破坏 | 真正的 `customer_detail` 查询补字段，两页共享资格和镜像机制；无资格标记的其他表单维持原行为，员工不出现管理入口 |
| 格式助手反向依赖或错误 Cookie 截断 | `display.py` 只有既有纯映射；拒绝描述只在失败路径按有限条目查房源，整条消息 ≤200 字符，HTML 与 JSON 都没有原始说明或内部异常 |
| 自动刷新丢草稿、摘要截断泄露隐私 | 复用脏表单标记；证明跨节点不刷新。对完整说明先脱敏，手机号／地址跨越摘要边界也不能留下片段 |
| 过期订单影响行动与排序 | 过期分支只用本地数据，正常分支行为不变；工作台是同一快照的可行动子集，运营页保留全部房间。无可行动房间和快照读取失败分别验收 |
| 全局 503 处理器误伤接口或泄露 detail | 验证 HTML、JSON、默认 Accept、q=0、POST、健康、非后台路径与其他状态；未知 detail 用固定文案，保留状态、响应头、no-store 与原业务拒绝处理 |
| 固定栏遮挡焦点、末条任务或新建表单 | 按实际高度预留空间，覆盖选中首条时立即出现、最后一条、清空、短屏、断点切换与客户页；键盘焦点可见、所有动作可达 |
| 日期或名称改动损坏锚点、可访问性 | 使用原始日期标签，保留跨段语义与房间锚点；以可访问名称和可见行程列表验收，不只检查源码里是否出现 aria 属性 |

验证计划（按项目「风险驱动验证」）：

- 后端按实际触及行为选已有用例，不机械跑下列文件全部测试：
  - `tests/integration/test_operations_repository.py`、`test_task_routes.py` 与 `tests/unit/test_task_page_service.py`：四种资格、混合批次全不变、五处可读拒绝、缺任务、长标题／多任务消息上限；成功数量、零改动、一次性提示、分页筛选回跳与外站地址拒绝；保留角色、CSRF 和手输确认反例。
  - `tests/integration/test_customer_repository.py`、`test_customer_routes.py` 与 `tests/unit/test_customer_admin_service.py`：真实客户投影的字段、归属、资格及批量成功／拒绝后回到服务页签。
  - `tests/unit/test_admin_operations_service.py`、`test_admin_dashboard_service.py` 与 `tests/integration/test_admin_dashboard_routes.py`：过期时五种本地行动、正常时原有动作和排序、无动作仍显示来源提示、读取失败不报空态；「待我关注」计数不变。
  - `tests/integration/test_admin_auth_routes.py` 与 `tests/unit/test_health.py` 中相关入口，以及实际 `main.py::app` 的异常注册：F9 的响应矩阵、未知 detail、响应头、原 HTML 503 与 `OperationRefused` 处理；不能只测单独构造的路由 app。
  - 摘要验证使用合成手机号与详细地址，覆盖恰好位于截断边界的敏感内容；断言最终渲染页面无原始说明或泄露片段，原角色过滤不变。纯格式迁移复用现有展示测试，不新增逐句复述映射的测试。
- 浏览器自动回归只保留三组，参照 `test_approval_late_results.py` 的真实路由／临时 SQLite 方式，加载真实 CSS、JS并复用已有浏览器设施：
  - 任务／客户混合勾选拦截、明确保留合格项；同一组覆盖 `requestSubmit()` 无请求、无弹窗、无忙态及镜像编号一致。
  - 手机选择首条立即可见固定操作区，末条内容和焦点不被遮挡，清空和断点切换恢复。
  - 受控时间跨计划节点时保留房态草稿；原有无草稿刷新不受影响。
- 320、390 与桌面布局、无脚本、键盘、日期与客诉中文通过隔离走查核对，复用原有相关测试；真实触控／读屏未实测时报告缺口，不用后端集成测试证明 JavaScript 行为。
- 默认只做上述相关验证。F9 虽改全局异常注册，也必须用实际应用证明条件内外的响应；若 `application.py` 装配、依赖／构建配置变化，或影响范围无法可靠界定，按项目规则在提交前跑一次本地全量，不以文件名代替影响判断。
- Python 变化时做对应 Ruff／mypy 检查，前端行为做相关浏览器验证；文档修订当前只做格式、引用与变更范围检查，不跑业务测试。
- 实施后以同一隔离数据完成桌面和手机 Impeccable 复核，结论以具体行为、可读性和截图为准，评分不作为通过门槛。真实触控、Mobile Safari 或读屏若未实测，明确列为验收缺口。
- 按当前计划不触及 `scripts/release/reply_gate.sh::REPLY_PATHS`，不运行真实模型门禁或测试号收发；若实施范围改变，重新判断，真实外部调用仍需当前明确授权。

## 5. 不在本次范围

- 任务中心改成「今日路线」视图（评审中的启发性问题，没有做决定）。
- 快捷键、详情页「下一条」、导航数量徽标、AI 调试台图标重复、41 处原生 `confirm`/`prompt` 换成自定义对话框。
- 运营页「未来 3 天」的标签与实际显示 6 天不一致：需要先查明设计意图（时间轴包含过去几天）再决定，单独记录。
- 404、403 的 HTML 化。
- 「待我关注」的统计口径（D6 明确不改）。
- 自动保存、持久化草稿、任务状态机／仓储资格重构、新的任务投影框架、数据库迁移、依赖与客人回复链路。
- 不提交、推送、部署；不调用真实 DeepSeek、百居易、企业微信。

## 6. 对比与审查结论

| 原方案或疑问 | 审查后的决定 |
| --- | --- |
| Claude 的批量资格、成功反馈比聊天 Spec 更具体 | 保留 F1–F4，并补逐项资格原因、键盘提交拦截、镜像同步、确认数失效与安全回跳 |
| `_bulk_eligibility` 位置待实施时选择 | F1 明确放服务层，两个页面真实共用；补足投影字段，不从页面筛选猜归档状态；执行层仍独立校验 |
| 仓储调用 `web.py` 的中文助手 | F3 改为移动现有纯格式助手到 `display.py`，Web 继续注册原过滤器，不新增第二份映射；查询助手必须异步，消息适配 200 字符限制 |
| 分派／取消可能需要新增返回值 | 源码及会话门面已经返回数量，F4 直接复用，不改 `application.py` |
| 列表底部 `sticky` 就能让选中操作可见 | F5 改为选中时固定操作区和按实际高度预留空间，验收勾选首条、末条与焦点 |
| 工作台新增 `first_rooms`，运营页继续分区 | 当前运营页已无分区；F7 直接过滤已有快照，不新增单一调用方属性；现有过期排序无需修改 |
| 所有 503 detail 都可公开 | 不能据局部固定文案推断所有异常；F9 统一安全文字，保留已有 HTML 响应、默认 JSON、响应头与业务拒绝处理 |
| Claude Spec 未覆盖聊天已确认的草稿、日历和摘要 | 合入 F10–F12；F5、F11 共同完成手机点击区，不另起一套样式 |
| 固定全套测试与审美分数是否必要 | §4 改为按行为选相关用例，优先复用；文档当前只静态检查，实施验收不以分数代替证据 |

## 7. 实施顺序与完成条件

确认本修订版并收到「开始」后，按一次连续实施推进，使用 `tasks/todo.md` 跟踪：

1. **数据与展示基础**：F1、F3、F6、F12；同时迁移任务／客户模板和路由上下文，更新真实仓储及路由替身的资格和摘要字段，先验证资格、真实投影、错误长度与摘要脱敏，保持页面可运行。
2. **批量完整流程**：F2、F4、F5；涉及 `routes/tasks.py`、任务／客户模板、`static/admin.js`、`app.css`，验证提示到提交、确认、事务结果、回跳和成功反馈。
3. **房间行动与可靠编辑**：F7、F10、F11；涉及运营服务、管理路由、工作台／运营模板和 UI 宏，验证来源失效、输入保护、原始日期和保留日历。
4. **后台表达与异常入口**：F8、F9；涉及工作台宏、布局、房源／知识模板、`routes/page_errors.py`、`main.py` 与错误模板，验证名称、危险操作入口、实际应用的响应边界。
5. **综合验收**：只重跑最终变化影响的检查，完成 §4 的关键行为与一次隔离浏览器复核，报告实际未覆盖项。版本维护遵循 D12，不自动提交或发布。

完成标准：两页批量选择与实际执行一致，拒绝／成功可理解；未保存输入不被自动刷新清空；过期来源下仍能处理本地事项且不把计划冒充事实；日历日期、摘要、移动点击区和错误页均满足上述验收。若实施需要改变数据、业务规则或本 Spec 的重大取舍，先修订并确认 Spec。

## 8. Claude 复审答复（2026-10-02）

| 编号 | 判断 | 依据 | 修订 |
| --- | --- | --- | --- |
| R1 | F7 排序改动无效 | `services/admin_operations_service.py::_occupancy_status` 在来源过期时一律返回 UNKNOWN，`_room_risk_sort_key` 的周转项已经相同 | 删除排序改动及对应验证 |
| R2 | D13–D16 来自 Codex 对话 | Claude 侧看不到原确认；E9 现状已逐条核实属实 | Codex 对话已确认 F10–F12，本次开始授权包含这些功能，不重复确认范围 |
| R3 | 浏览器矩阵偏重 | 项目规则：只写有判别力的测试，按影响定范围 | 真实路由浏览器测试保留三组：混合勾选含 requestSubmit、手机固定栏、房态草稿；宽度和无脚本作隔离走查，服务端结果用集成测试验证 |

其余 Codex 修订（返回数量已存在、运营页无分区、503 固定文案与已有 HTML 503、200 字符预算、`display.py` 迁移、取消成功回跳未校验）均与源码一致，采纳。

## 9. 实施细化（2026-10-02，第 1 步试做后回退）

用户回复「开始」后，第 1 步改了 `web.py`、`services/task_page_service.py`、`services/customer_admin_service.py`、`repositories/customers.py`、`routes/tasks.py`，并新建了 `display.py`。用户随后要求只更新 Spec，以上改动已用 `git checkout` 回退，`display.py` 已删除，工作区回到基线 `8c3ff95`，只剩本 Spec 与 `.impeccable/critique/` 两个未跟踪文件。试做中确认了以下细节，下次实施直接照此执行。

### 9.1 F3 `display.py` 的内容与边界

- 迁出 `_STATUS_LABELS`、`status_zh`、`date_zh` 后，`web.py` 不再使用任何领域枚举，也不再使用 `Enum`、`date`，整段 `domain.enums` 导入随之移入 `display.py`。`web.py` 改为 `from homestay_bot.display import PAGE_MESSAGE_MAX_LENGTH, date_zh, status_zh`，过滤器注册名不变。
- 现有唯一外部引用是 `tests/unit/test_template_helpers.py` 的 `from homestay_bot.web import complaint_risk_zh, status_zh`。`web.py` 重新导出 `status_zh` 即可，测试不用改。
- 页面提示长度只保留一个来源：在 `display.py` 定义 `PAGE_MESSAGE_MAX_LENGTH = 200`，`web.py::_PAGE_ERROR_MAX_LENGTH` 改为引用它；生成拒绝文案的一方按同一常量做预算。
- 同在 `display.py` 新增两个纯函数，仓储与服务都调用它们，不各写一份：
  - `task_refusal_label(task_id, task_type, room, service_date, status, room_limit=12)`：输出「任务 #12·保洁·C502·2026年10月2日（进行中）」。房间名超长时截短并加「…」，缺房间写「房间待确认」，缺日期写「日期待补齐」。
  - `refusal_message(reason, labels, total, next_step)`：`labels` 是按房间名长度返回全部标签的回调。预算策略：房间名先用 12 字、再用 6 字；在每种长度下，点名条数从 3 条递减到 1 条；拼出「{原因}。以下 N 条不符合：{点名}，另 M 条。{下一步}」，第一次不超过 200 字就返回。都放不下时退回「{原因}。共 N 条不符合。{下一步}」。
- 仓储新增异步方法 `describe_refused_tasks(tasks, *, reason, next_step) -> str`，并在 `TaskPageRepository` 协议中声明。它一次 IN 查询 `PropertyProfile.id/room_number/title`（含停用房源），再调用上述两个函数。五处拒绝的原因与下一步：

  | 位置 | 原因 | 下一步 |
  | --- | --- | --- |
  | 分派，状态不符 | 只有待确认或待分派的任务可以分派 | 请取消勾选这些任务后重试 |
  | 分派，缺字段 | 这些任务缺少房间或服务日期 | 请先在任务详情补齐后再分派 |
  | 永久删除 | 只有已归档的任务可以永久删除 | 请先完成或取消这些任务后归档，再永久删除 |
  | 归档 | 只有已完成、已取消或已失效的任务可以归档 | 请取消勾选仍在处理中的任务后重试 |
  | 取消 | 已完成、已取消或已失效的任务无法取消 | 请取消勾选这些任务后重试 |

- `cancel_many` 的 `blocked` 现在是编号字符串列表（`services/task_page_service.py` 第 586 行附近），要改为保留 `BusinessTask` 对象再调用仓储方法。

### 9.2 F1 资格函数与列表字段

- 签名：`task_bulk_eligibility(*, status, property_id, service_date, archived_at) -> frozenset[str]`，放在 `services/task_page_service.py` 模块级。规则如下：
  - `assign`：待确认、待分派之一，且房间、日期都不为空；
  - 终态给 `archive`，非终态给 `cancel`，两者互斥；
  - `archived_at` 不为空时另加 `purge`。
- `TaskListItem` 新增两个带默认值的字段 `eligible_actions: frozenset[str] = frozenset()`、`safe_summary: str = ""`。默认值不替代测试装配迁移；当前路由替身使用 `SimpleNamespace` 和字典，需补资格、摘要并随状态变化重算，否则新汇总会失败。`list_for` 用 ORM 上的 `task.archived_at`、`task.description` 计算，不把归档时间传给浏览器。
- `routes/tasks.py::task_index` 删掉 `archivable_statuses`、`cancellable_count`、`assignable_ids` 三个上下文键，改为 `bulk_counts = {"assign"|"cancel"|"archive"|"purge": 条数}`，由各条 `eligible_actions` 汇总。`tasks/index.html` 第 61–70 行使用这三个旧键的地方要在同一步一起改，包括模板里自己用 `selectattr` 算出的 `archivable_count`，否则页面会渲染失败。
- 客户页：`repositories/customers.py::customer_detail` 的 service 投影补 `BusinessTask.property_id`、`BusinessTask.archived_at`。`CustomerAdminService._localize_detail` 用 `BusinessTaskStatus(status)` 把字符串转成枚举存为 `task["status_enum"]`：
  - 转换失败时 `status_enum=None`，不给任何资格，徽标显示「待核实」；
  - 资格写入 `task["eligible_actions"]`，`can_archive`、`can_cancel` 改由资格推出，模板语义不变；
  - 原来的 `ARCHIVABLE_TASK_STATUSES` 导入改为 `BusinessTaskStatus`，并导入 `task_bulk_eligibility`。试做中确认没有循环导入（`import homestay_bot.main` 通过）。

### 9.3 F12 摘要

- `TaskPageService._summary(description)`：
  - 空说明或只有空白时返回「暂无说明」；
  - 否则先对完整说明调用 `_safe_description` 脱敏，再用 `" ".join(text.split())` 压成一行；
  - 超过 80 字时截成 79 字加「…」。
- 脱敏用的占位符「[手机号已隐藏]」可能被截成半截，但这只是展示不完整，不会泄露号码。验收时以「渲染结果里没有号码残片」为准。

### 9.4 下次实施的顺序调整

第 1 步（F1 的路由上下文）和第 2 步（批量模板）都要改 `tasks/index.html` 里依赖旧上下文键的部分，而只做第 1 步时页面会渲染失败。因此 F1 的路由改动与 `tasks/index.html`、`customers/detail.html` 的模板改动合并到同一步完成，这一步完成后先跑 `tests/integration/test_task_routes.py` 和客户路由用例，再进入 F2。


## 10. 2026-10-02 实施与验证

用户已明确授权开始；F1–F12 已完成。代码位于本地分支 `codex/admin-batch-workbench`，基线 `8c3ff95`，没有提交、推送、标签或部署。

- 实现核验纠正：跨窗口的 `start_label/end_label` 不是原始日期，已保留延续提示并增加 `TimelineBar.start_date/end_date` 内部投影；没有数据迁移或公开接口变化。默认动作没有可见按钮、空队列的程序化提交也纳入同一资格保护。
- 独立审查发现的固定栏包含块、无提示节点/空队列、错误页布局、跨窗日期及可访问名称问题均已修复；最终质量与焦点补充复核未发现待修复的 P0–P2。
- 相关后端组 276 passed，已有客户/知识/房源路由组 90 passed，公共交互/新增浏览器组 64 passed。最后相关输入变化后复验浏览器 30 passed、路由 1 passed；计数不重复累计。
- 360/390/768/1280px 两页走查、无脚本、原始日期与默认可见日历通过；Ruff、11 源文件 Mypy、JS 语法与 diff 检查通过。
- 候选源码版本 1.66.0；正式日志与本地候选记录见 `CHANGELOG.md` 和 `docs/releases/1.66.0.md`。本地已安装包元数据实测为 1.4.0，尚未重新安装或打包，不以源码版本推断运行版本。
- 没有改动客人回复路径或调用真实外部服务；本次相关测试不替代部署门禁、生产登录页面、真机/读屏与外部同步验收。

## 11. Claude 实施复审补修（2026-10-03）

依据 `docs/reviews/2026-10-02_claude-to-codex-admin-batch-and-workbench-implementation-review-handoff.md` C1–C3；Codex 已对照当前代码确认三个 P3 均成立。用户回复「kaishi」，确认开始按下列方案本地补修，不提交、推送或部署。

| 项目 | 文件与符号 | 决策与验收 |
| --- | --- | --- |
| C1：删除本次产生的孤儿路径 | `src/homestay_bot/routes/knowledge.py` 的协议、KnowledgeAdminService.image_counts、列表渲染查询/上下文；`src/homestay_bot/application.py::SessionKnowledgeAdminService.image_counts`；`tests/integration/test_knowledge_routes.py::KnowledgeAdminStub.image_counts` | 完整删除图片计数方法及唯一调用，不用注释保留无调用方代码。知识详情配图与永久删除不变；保留其他配图数量上限查询。验证知识路由及调用方检索；application.py 发生修改，按项目规则执行一次最终离线全量 |
| C2：提示以本页和角色为准 | `src/homestay_bot/templates/components/ui.html::task_eligibility`；`templates/tasks/index.html`；`templates/customers/detail.html` | 宏显式接收 offered，只显示资格与本页动作的交集。任务常规队列提供 assign/cancel/archive，已归档只提供 purge；客户页只提供 archive/cancel；普通员工不显示管理批量动作提示。已归档队列的汇总提示同步只显示永久删除。data-eligible、资格函数与服务端执行规则不变；通过真实隔离页面验证开放、已归档、客户服务与员工视角 |
| C3：提交忙态不被重新计算覆盖 | `src/homestay_bot/static/admin.js` 的 selectionForms.refresh / guard；`tests/browser/test_admin_batch_workbench.py` | 提交中跳过动作按钮状态/文案重算，仍同步布局、镜像选择和实际操作栏高度；重复点击或程序化提交在确认前拦截。使用真实页面的提交事件验证 resize 后仍禁用且显示忙态，编号唯一、移动栏正常，重复提交不再弹窗 |

先用可稳定复现的页面反例证明 C2/C3，再修复；C1 复用知识路由测试与最终全量。仅在 final diff 完成后执行一次最终全量（关闭真实外部契约测试），同时检查受影响 Python、JavaScript 与差异。现有生产、打包、320px、真机/读屏与 PostgreSQL 并发验收缺口继续保留，不把本次离线全量扩大为这些验收。

全量发现四处与 F1/F3/F8/F9 直接相关的旧验证缺口，需一并迁移：`test_knowledge_image_admin.py` 将配图删除确认移到真实详情并验证列表无删除；`test_admin_assets.py` 同步工作台名称，取消对旧上下文变量名的强绑定，保留状态不重复定义和真实资格矩阵；`test_release_scripts.py::_GATE_INFRASTRUCTURE` 将纯后台 `display.py` 归为基础设施。已核对该模块只经后台 Web、客户管理和仓储拒绝格式调用，不生成客人回复，不修改 REPLY_PATHS 或真实门禁脚本。上述只更新测试与验证记录，不扩大运行行为范围；修改后仅重验这四个受影响用例，复用全量其余有效结果。

补修完成证据：C2/C3 的 5 项缺陷反例修改前均失败、修改后 5 passed；最终代码执行离线全量得到 2442 passed、4 failed、61 skipped、11 warnings（83.82 秒），仅上述四处验证迁移后专项 4 passed（7.98 秒）。本次共 2446 个不同用例有有效通过证据，非一次重新执行全量的输出；未变化的 2442 项直接复用。Ruff、两源文件 Mypy、JavaScript 语法与 diff 检查通过。61 项因真实外部契约关闭或未配置专用 PostgreSQL 跳过；依赖弃用、SQLite 连接/线程清理警告未在本次修复。候选版本仍为 1.66.0，没有提交、推送、打标签、部署或真实外部调用。
