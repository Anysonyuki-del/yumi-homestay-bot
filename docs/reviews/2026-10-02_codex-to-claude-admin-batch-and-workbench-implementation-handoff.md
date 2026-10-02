# Codex → Claude：任务批量操作、工作台行动与后台可靠性实施审查交接

日期：2026-10-02。状态：F1–F12 已完成本地实现与相关验证，等待 Claude 独立审查；改动未提交、未暂存，未推送、打标签、发布或部署。

本轮让任务中心与客户页按同一资格提示批量动作，拒绝混合批次并给出可定位的原因，成功后反馈实际数量；同时处理来源过期时的本地行动、草稿保护、手机操作区、真实住宿日期和安全 HTML 503。请以当前代码和真实调用链核对这些行为，测试通过与生产验收分别判断。

## 1. 依据、Git 基线与完整范围

- 用户在 Spec 复审后明确回复「开始改代码吧」，授权 F1–F12 和本轮三项审查修订。本次仅准备交接报告，供 Claude 审查。
- 实施依据：[统一 Spec](</Volumes/02/obsidian codex/homestay-bot/docs/specs/2026-10-02_admin-batch-and-workbench-spec.md:101>) 的 D1–D16、F1–F12、风险与验证条件。§1 是修改前证据，§9 是前次试做回退记录；[§10](</Volumes/02/obsidian codex/homestay-bot/docs/specs/2026-10-02_admin-batch-and-workbench-spec.md:379>) 才是本次完成记录。
- 候选版本与验证摘要：[CHANGELOG.md](</Volumes/02/obsidian codex/homestay-bot/CHANGELOG.md:1>)、[1.66.0 本地候选记录](</Volumes/02/obsidian codex/homestay-bot/docs/releases/1.66.0.md:1>)；执行清单见 [tasks/todo.md](</Volumes/02/obsidian codex/homestay-bot/tasks/todo.md:1>) 顶部。
- 工作目录：`/Volumes/02/obsidian codex/homestay-bot`；分支：`codex/admin-batch-workbench`；HEAD：`8c3ff95fb517b6af94db00bab48d4298f3171666`。本轮实现位于 HEAD 之上的工作区，不能描述成已提交的 Codex commit。
- 写报告前：37 个已跟踪文件修改，1014 行增加、387 行删除。普通 `git diff` 不包括下列新增文件，也不包括本报告。

| 必须单独阅读的未跟踪文件 | 内容 |
| --- | --- |
| [display.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/display.py:1>) | 既有中文映射迁移、共享提示长度、可读拒绝格式 |
| [errors/unavailable.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/errors/unavailable.html:1>) | 不依赖管理员导航的固定安全错误页 |
| [test_admin_batch_workbench.py](</Volumes/02/obsidian codex/homestay-bot/tests/browser/test_admin_batch_workbench.py:1>) | 真实路由、正式会话门面、临时 SQLite 的浏览器回归 |
| [test_admin_unavailable.py](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_admin_unavailable.py:1>) | 实际 main.py::app 异常注册与响应边界 |
| 上述 Spec、候选记录及本报告 | 实施依据、验收证据和审查入口 |

`.impeccable/critique/` 是此前已有的审查材料，保留原状；它不代表本次最终代码验收。不读取、摄入、暂存或提交未跟踪的 `YuMi民宿AI项目总结.txt`。本次文档交接只新增本报告。

建议先运行以下只读命令，再单独打开上表新增代码与测试：

```sh
git status --short
git diff --stat HEAD
git diff HEAD -- src/homestay_bot tests pyproject.toml CHANGELOG.md
```

## 2. 实际修改与关键契约

| 功能 | 文件与符号 | 最终行为与约束 |
| --- | --- | --- |
| F1 / F12：资格与安全摘要 | [task_page_service.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/task_page_service.py:27>)：task_bulk_eligibility、TaskListItem、TaskPageService.list_for、_summary | 公共纯函数只提供页面资格提示。待确认/待分派且房间、日期齐全才提示可分派；非终态可取消，归档按 ARCHIVABLE_TASK_STATUSES，已归档可永久删除。摘要先对完整说明脱敏，再压缩空白、限制为 80 字；缺说明显示「暂无说明」 |
| F1 / F6：客户页投影与未知状态 | [repositories/customers.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/repositories/customers.py>)：customer_detail；[customer_admin_service.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/customer_admin_service.py:597>)：_localize_detail | 真实查询补 property_id、archived_at，保持客户归属、排序与 50 条限制。客户页调用同一资格函数；未知状态无动作、显示待核实。未为客户页摘要新增原始说明查询 |
| F2：选择、拦截与确认 | [admin.js](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/static/admin.js:326>)：selectionForms、selectedTaskIds、eligibleIds、guard、typedForms | 读取服务端 data-eligible，按启用且已选编号去重；混选禁用动作并显示合格/所选数量。只有点击「只保留…」才过滤。点击与 submit 捕获先校验，再进入确认与忙态；无 submitter 使用默认归档，空队列同样保护。手输确认在 submit 捕获阶段执行，选择/布局变化使旧确认失效 |
| F3：五处可读拒绝 | [repositories/operations.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/repositories/operations.py:651>)：describe_refused_tasks、require_assignable、require_purgeable、archive_selected；TaskPageService.cancel_many | 使用已加载的不合格任务，按有限条目一次查房间；房间停用仍能定位。拒绝格式只含编号、类型、房间、完整日期、状态，最多点名三项；按 200 字预算先缩房间名再减条数，保留原因、总数/剩余数与下一步。没有透传原始说明或客人资料 |
| F4：数量、反馈与回跳 | [routes/tasks.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/routes/tasks.py:343>)：task_index、assign_selected_tasks、cancel_selected_tasks、archive_selected_tasks、archive_filtered_tasks、purge_selected_tasks | 当前页资格计数排除第 51 条分页哨兵；成功提示只读一次，使用实际执行数量，0 条不谎报改动。分派、取消、所选归档经 safe_return_path 返回安全来源；整批归档、永久删除保留归档落点。分派失败携带来源；不新增员工姓名查询 |
| F5：手机操作区 | [app.css](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/static/app.css:535>)：card-select、selection-actions、page-enter；admin.js 的 measureToolbar | 选择区 44×44、框体 20px，与卡片链接分开。≤768px 且有选择才固定，ResizeObserver 测量真实高度预留空间；短屏内部滚动、安全区与焦点滚动共同保证可达。page-enter 结束时不保留 transform 包含块，未选/桌面/无脚本保持静态 |
| F6 / F11：共享展示宏 | [components/ui.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/components/ui.html:6>)：task_status_badge、task_eligibility、bulk_feedback、stay_status、_calendar_segments、_room_timeline_mobile、room_timeline | 列表、详情、客户页使用同一状态语义，保留文字。桌面/手机住宿可访问名称使用真实日期与住宿状态；无客户链接的 span 另有隐藏文本。完整行程显示原始日期，窗口延续提示仍保留 |
| F7 / F11：本地行动与原始日期 | [admin_operations_service.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/admin_operations_service.py:257>)：_next_step、TimelineBar、_timeline_bars；[routes/admin.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/routes/admin.py>)：admin_dashboard | 来源过期时只按本地逾期、维修、开放任务、清洁/待检查给行动，忽略入住/退房等上游安排；工作台直接过滤现有 attention_rooms，无新投影框架。无本地行动仍显示来源提示，快照读取失败有明确失败态。内部 TimelineBar 补 start_date/end_date，几何、原延续标签与持久化不变 |
| F9：安全 HTML 503 | [page_errors.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/routes/page_errors.py:60>)：_wants_html、handle_http_exception；[main.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/main.py>)：app 异常注册 | 仅抛出的 503、GET、/employee/ 路径、显式接受 text/html 且 q>0、非 /employee/health 时返回固定 HTML。其他请求沿用默认异常响应；保留状态、异常响应头和既有 no-store。错误模板继承 layouts/auth.html，不复用可能失败的管理员导航，不公开异常 detail |
| F1 / F4 / F5 / F10：两页模板 | [tasks/index.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/tasks/index.html>)、[customers/detail.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/customers/detail.html>) | 桌面/手机副本带唯一描述性名称、编号、资格与缺字段原因；按钮带 data-requires、表单默认归档。任务新建表单加现有脏表单标记 |
| F7 / F8 / F10 / F11：工作台与入住安排 | [admin/dashboard.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/admin/dashboard.html>)、[admin/operations.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/admin/operations.html>)、[components/workbench.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/components/workbench.html>)、[layouts/admin.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/layouts/admin.html>) | 统一「今天 / 入住安排 / 任务中心」，删除重复标题。「待我关注」只改名称和说明，计数口径不变。房间行动在日历前，日历默认可见；保留房间锚点、文章标题关联。房态表单复用 dirtyForms/原倒计时，跨节点不丢草稿 |
| F9 / F12：相关页面表达 | [knowledge/index.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/knowledge/index.html>)、[complaints/index.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/complaints/index.html>)、[properties/detail.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/properties/detail.html>)、[tasks/detail.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/tasks/detail.html>) | 知识列表只保留启停，永久删除入口仍在详情；客诉风险中文化，房源开放任务数量可跳转。任务状态共用徽标，取消确认说明不可撤回；降级状态与真实错误分别采用 status / alert |
| 格式迁移兼容 | [web.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/web.py:10>)、display.py | 既有 status_zh/date_zh 映射移到纯展示模块，Web 继续导入原名称并注册原过滤器；提示上限共用 PAGE_MESSAGE_MAX_LENGTH。仓储没有反向依赖 Web |

服务端权限、一次性 CSRF、手输数量、状态机、整批拒绝、事务提交及既有锁规则仍是最终执行边界，页面提示不授予操作权限。本轮没有数据库迁移、新依赖、新定时器、持久化草稿、客人回复路径或 application.py 生命周期装配变更。

## 3. 建议优先审查的完整调用链

1. **页面资格 → 实际四动作**：从 TaskPageService.list_for 与 customer_detail → _localize_detail → 两页模板 → 共用脚本 → 正式 SessionTaskPageService → 仓储/状态机，寻找资格与最终执行不同的反例。两页投影不应从当前队列推断 archived_at；混合批次拒绝后所有任务状态、归档标记均应不变。
2. **事件顺序与所有提交入口**：空选择、空队列、合格/混选、requestSubmit(button)、requestSubmit()、键盘、双布局 resize。资格保护应在 confirm、手输和 busy 前；缺默认动作按钮或提示节点时也应给正确原因。手输确认不能沿用上次数字，无脚本由服务端拒绝不可逆操作。未带资格标记的已有选择表单保留原行为。
3. **拒绝格式与异步查询**：五个出口都 await describe_refused_tasks；一次有限房源查询，无成功路径额外查询。超长房间名、多条任务、缺字段仍满足 200 字预算，保留原因与下一步；缺任务仍按既有 LookupError 处理。永久删除的下一步应先完成/取消、归档，再删除。
4. **数量、失败与重定向**：真实会话门面返回值 → 路由提示 → 一次读取；校验 0 条、失败、当前筛选/分页/客户服务页签、外站 return_to。分派员工占位值实际为 `""`，FastAPI 本轮实测将其映射为默认 0 后走正确拒绝与安全回跳，未新增表单归一化代码。
5. **来源过期与行动排序**：_next_step 在过期时只使用本地事实；admin_dashboard 过滤已有快照，无行动与读取失败区分。_room_risk_sort_key 未改：既有过期分支已令 occupancy 为 UNKNOWN；不要要求新增无效排序或把「待我关注」改成房间风险计数。
6. **正式应用 503 注册**：从 main.py::app 的实际注册验证条件内外，包括 JSON、默认 Accept、text/html;q=0、POST、健康、非后台、404 和原有直接 HTML 503；同时核对 OperationRefused 的 HTML/JSON 边界。不通过启动外部服务证明该处理器。
7. **手机与可访问性**：首条选择立即可见、末条/新建表单不遮挡、短屏动作可达、清空与断点恢复；检查动画结束后的实际 fixed 包含块和焦点上下遮挡。跨窗口住宿的真实日期、状态需要存在于链接可访问名称/无链接可读文本和完整行程，不能只依赖 title。
8. **草稿与公共脚本调用方**：房态/新建标记进入原 dirtyForms；跨计划节点不刷新有草稿页面，无草稿仍沿原流程刷新。检查公共脚本现有确认、提交忙态、提醒选择与日历交互，没有新增并行状态机制。

实际审查中已修正：空队列与缺提示节点保护、503 模板布局、跨窗口日期/住宿状态、page-enter 保留 transform 导致固定栏错位、手机焦点遮挡。最终内部复核未发现待修 P0–P2；这一结论仅作交接记录，仍需 Claude 独立判断。

## 4. 验证证据、复现命令与有效性

以下是实施阶段的结果，本次写报告仅核对记录与文档，没有重跑业务测试。430 是不同相关用例的合计，重复复验不增加计数，也不是一次全仓测试。

| 范围 | 结果 | 证明与限制 |
| --- | --- | --- |
| 相关后端 10 文件 | 276 passed，1 warning，12.63 秒 | 真实 ORM 四动作资格矩阵、混批整批不写、客户投影、摘要、拒绝预算、路由反馈、过期行动、日期几何、实际应用 503 |
| 客户/知识/房源路由 3 文件 | 90 个用例此前通过，复用有效结果 | 来源是更早的混合批次，不是独立一次「90 passed」命令；该批次其他文件的失败已由最终 276 组覆盖修复。后续输入变化未影响这 90 项 |
| 公共交互与新增浏览器 2 文件 | 64 passed，1 warning，21.13 秒 | 新增三组/五个参数化用例覆盖任务中心与客户页混选、空队列、明确过滤、真实 POST 与一次反馈、手机固定栏、草稿；同时保留既有公共交互回归 |
| 最后焦点/手机日期变化后复验 | 30 passed，34 deselected，24.32 秒 | 只重跑混选、手机工具栏、跨节点、timeline/calendar 相关输入；其他结果继续复用 |
| 最后员工占位值断言复验 | 1 passed，79 deselected，3.04 秒 | 真实提交空字符串，校验拒绝说明和安全来源；属于上述后端组的同一用例 |
| 隔离走查 | 两页 360/390/768/1280px 共 8 个状态通过 | 无横向溢出、提交编号唯一、选中固定栏可见；无脚本增强区隐藏、确认数仍为 0；原始日期与默认可见日历核对 |
| 静态检查 | Ruff、11 源文件 Mypy、node --check、git diff --check 通过 | 检查范围与受影响 Python、JavaScript、差异一致，不证明包装资产或生产运行 |

后端最终组的可复现命令（在仓库根目录执行）：

```sh
.venv/bin/pytest -q \
  tests/unit/test_task_page_service.py \
  tests/unit/test_customer_admin_service.py \
  tests/unit/test_template_helpers.py \
  tests/integration/test_operations_repository.py \
  tests/integration/test_customer_repository.py \
  tests/integration/test_task_routes.py \
  tests/integration/test_admin_dashboard_routes.py \
  tests/unit/test_admin_operations_service.py \
  tests/unit/test_timeline_geometry.py \
  tests/integration/test_admin_unavailable.py
```

对应仍有效的路由范围与浏览器范围可分别复查；不要求为审查机械重跑所有用例：

```sh
.venv/bin/pytest -q \
  tests/integration/test_customer_routes.py \
  tests/integration/test_knowledge_routes.py \
  tests/integration/test_property_routes.py

.venv/bin/pytest -q \
  tests/browser/test_admin_interactions.py \
  tests/browser/test_admin_batch_workbench.py

.venv/bin/pytest -q \
  tests/browser/test_admin_batch_workbench.py \
  tests/browser/test_admin_interactions.py \
  -k 'mixed_selection or mobile_toolbar or crossing_checkout or timeline or calendar'

.venv/bin/pytest -q tests/integration/test_task_routes.py -k zero_archive
```

测试判别力与设施：

- `tests/integration/test_operations_repository.py::test_bulk_eligibility_matches_real_execution` 使用真实 ORM、管理员与员工，按状态/字段/归档组合核对四个动作；各动作独立建数据，拒绝后检查整批未变。另有已归档再次归档 0 条、有限查询和停用房间定位。
- `tests/unit/test_task_page_service.py::test_task_list_summary_redacts_before_truncation_and_normalizes_whitespace` 与相关页面断言覆盖敏感内容跨摘要边界。客户仓储测试检查真实归属、排序、限量；客户服务测试检查未知状态。
- `tests/integration/test_task_routes.py::test_bulk_success_notices_keep_safe_origin_and_use_actual_counts` 与 `test_zero_archive_and_missing_employee_refusal_do_not_claim_changes` 验证实际数量、一次性反馈、安全来源和空员工值。已有角色、CSRF、手输数量、外站地址反例继续覆盖。
- 新浏览器文件使用正式路由、SessionTaskPageService、SessionCustomerAdminService、SessionAdminOperationsService、项目 SQLite 工厂、临时文件数据库与合成数据。仅认证/时间受控，页面请求阻止出网；成功操作提交到同一 TestClient 并核对实际响应。
- 浏览器加载正式模板 HTML，注入当前源码 CSS/JS。它证明本次交互，**没有证明真实 HTTP 静态资源、包版本查询或缓存刷新**。实际 503 注册用例使用 main.py::app 且不运行 lifespan，避免启动真实外部服务。
- 既有路由替身原为 SimpleNamespace/字典，已经显式补资格/摘要或按当前字段重算；仅给 TaskListItem 添加默认值不足以迁移这些替身。请检查测试没有因为替身遗漏而放过真实页面缺字段。

本轮日志保存在本机临时文件：`/tmp/admin-backend-complete.txt`、`/tmp/admin-browser-complete.txt`、`/tmp/admin-browser-last.txt`、`/tmp/admin-empty-employee-final.txt`。较早的 `/tmp/admin-related-tests.txt` 含已修复的中间失败，不是最终失败清单。临时文件可能清理，复现入口以上述仓库测试为准。

未跑全仓业务测试：本轮未改 application.py、依赖或版本号以外的构建配置，影响范围由上述调用方和数据流限定；公共前端脚本另有真实浏览器回归。未触及 REPLY_PATHS，不运行真实模型门禁。Starlette/httpx 的一条既有弃用提示保留记录，未新增依赖处理它。

## 5. 尚未证明的事实与范围边界

- [pyproject.toml](</Volumes/02/obsidian codex/homestay-bot/pyproject.toml>) 的源码候选版本为 **1.66.0**；实施收口时本地已安装包元数据实测为 **1.4.0**，没有重新安装/打包。源码版本不能替代已安装包或生产运行版本；后续打包/安装时仍需核对静态资源版本与缓存失效。
- 没有生产登录页面、部署副本、数据库或外部同步验收；没有真实 DeepSeek、Hostex、企业微信、客人/员工消息、真实订单或生产写入。
- 浏览器是隔离 Chromium；真机触控、Mobile Safari、真实辅助技术尚未验证。Spec 提到的 **320px** 走查本轮没有记录，最低实测为 360px，应保留这个验收缺口。
- 未改变事务/锁规则，本轮没有 PostgreSQL 并发压力证据，不能从 SQLite 测试推断生产竞争行为。
- 「待我关注」口径、新任务路线视图、404/403 HTML 化、自动保存、持久化草稿、全站自定义确认框、日历标题时间窗争议等仍按 Spec 排除；如发现其既有问题，单独标记，不能算本次应交付功能。
- 本次相关回归不替代部署门禁；后续获准部署时仍须按项目规则提供本地全量或推送 CI 通过证据。提交、推送、部署、外部发送和生产写入分别需要当前明确授权。

## 6. 可直接交给 Claude 的审查请求

> 请只读审查当前 `codex/admin-batch-workbench` 工作区的任务批量操作、工作台行动与后台可靠性实现。先阅读统一 Spec 的决定、F1–F12、风险条件及 §10，再看本交接报告；基线 HEAD 为 `8c3ff95fb517b6af94db00bab48d4298f3171666`。
>
> 检查完整已跟踪 diff，并单独阅读未跟踪的 display.py、errors/unavailable.html、新浏览器回归和实际应用 503 回归。沿模板 → 共享 JS → 路由 → 正式会话门面 → 服务/仓储追踪，不只比较源码字样或相信测试数量。优先核对资格与执行一致、整批拒绝、确认/忙态顺序、程序化提交、错误隐私与长度、安全回跳、来源过期本地行动、手机遮挡、真实日期及草稿保护。
>
> 结果按 P0–P3 列出有证据、可复现的问题，给出文件/符号/当前行号、触发条件、影响、最小修复方向及必要验证；区分本次引入、原有问题和验收缺口。若无可行动问题，请明确说明，并保留打包/生产/真机/320px 等未覆盖边界。需要验证时只选择能区分问题的隔离相关检查，不机械重复已有有效结果。
>
> 本次授权是审查：不改代码、不提交/推送/部署、不调用真实外部服务或写生产数据，不读取受保护的未跟踪项目总结；保留用户/Claude 已有文件。不要把源码候选版本或隔离浏览器证据当成生产验收。
