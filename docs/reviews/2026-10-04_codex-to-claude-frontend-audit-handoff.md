# 前端功能与交互设计审查交接 · Codex → Claude

日期：2026-10-04。用途：交给 Claude 独立核实本次审查结论，作为后续修复 Spec 的输入。

结论：确认 **3 项 P1、2 项 P2**，重点是审批参考价归属、摘要编辑失败恢复、经典主题控件对比度，以及手机列表信息与分派前置校验。现有暖色方向可以保留，不建议因此重做主题。本报告记录审查结果，五项问题均尚未修复。

## 1. 基线、工作区与执行边界

- 分支：`main`。
- 本地 HEAD：`1ec9f95b17d0aca3a205588b20a306753b5c6226`，提交标题为「docs(release): 补录 1.67.0 部署与主题页面验收」。
- 正式源码版本：`pyproject.toml::project.version = 1.67.0`；暖色主题功能提交为 `74c904d`。
- 编写本报告时，`git diff --name-only HEAD -- src tests pyproject.toml DESIGN.md` 无输出；前述审查证据对应的源码输入未变化。
- 审查及报告编写前已经存在的工作区状态：`docs/releases/1.67.0.md`、`tasks/todo.md` 已修改，`.impeccable/critique/` 未跟踪。它们没有被本轮修改、暂存或纳入审查成果。
- 用户先要求「进行前端页面功能点，交互页面设计检查」，随后要求「写交接报告」。本轮只新增本报告，没有修改业务代码、测试、设计令牌或工具忽略配置，没有提交、推送、部署或向其它会话发送消息。
- 本地验证只使用合成记录、临时 SQLite 和测试认证；审批参考数据使用测试替身。没有调用真实 DeepSeek、Hostex 或企业微信，没有执行生产业务表单、建单或消息发送。

本报告不是已确认的实施 Spec，也不构成修复、提交或部署授权。需要实施时按项目 AGENTS.md 完成对应 Spec 确认，再等待用户明确「开始」。

## 2. 检查范围与证据边界

采用 Impeccable 的 audit / operate 方法，结合模板、脚本、路由、服务和仓储核查。原有暖色设计已获用户认可，本轮不以静态工具告警重新否定该方向。

浏览器本地检查采用真实 HTTP 路由、项目静态资源、正式会话门面和临时 SQLite；认证及审批外部数据由测试设施提供，并非完整生产装配。

| 范围 | 实际查看的页面或页签 | 数量 |
| --- | --- | ---: |
| 工作台与入住安排 | `/employee/admin`、`/employee/admin/operations` | 2 |
| 任务 | `/employee/tasks`、`/employee/tasks/13` | 2 |
| 客户 | `/employee/customers`；合成客户 #1 的 overview、stays、service、memory 页签 | 5 |
| 房源 | `/employee/properties`；合成房源 #101 的 profile 页签 | 2 |
| 知识库 | `/employee/knowledge`、`/employee/knowledge/1` | 2 |
| 审批 | `/employee/approvals/1`，参考数据来自测试替身 | 1 |

上述 14 个页面或页签分别以 1280px、320px 宽度检查，共 **28 个视图**。记录中均有一个可见 h1，页面 `scrollWidth` 未超过 `clientWidth`，未发现无标签的可见表单控件。这里的标签存在性检查不等于完整的无障碍认证；没有横向溢出也不等于手机内容完整，F04 正是反例。

另以手机宽度查看任务选择及固定操作区，切换经典 / 暖色主题检查实际控件颜色。附带源码核查了客诉输入恢复、设置页风险提示、AI 调试和账号入口；这些附带路径未做完整浏览器验收。

上一轮线上实际检查时停在登录页，未完成登录后的生产页面验收。本次报告编写没有追加线上检查，不把当时登录状态当作现在的运行结论，也不以本地结果替代线上页面、真实建单或消息收件证据。

## 3. 待核实问题

### F01 · P1 · 审批参考价丢失房间与渠道身份

类别：操作信息与实现完整性。

源码调用链：

- `src/homestay_bot/integrations/hostex_client.py::HostexClient.list_reference_prices`，357–397 行：读取房源 channels，优先使用 booking_site 渠道；没有该渠道时使用其它渠道。把多房源渠道日历合并为 `ListingCalendarDay` 列表，保留 `listing_id` 和 `channel_type`。
- `src/homestay_bot/services/approval_page_service.py::ApprovalPageService.get_detail`，174–225 行：把房源与参考价分别交给页面，没有建立供展示的房间归属分组。
- `src/homestay_bot/templates/approvals/detail.html::price-list`，第 7 行：每项只渲染 `item.date` 和 `item.price`，不显示房间、渠道，也没有随所选房间对应展示。

本地复现：复用 `tests/integration/test_approval_routes.py::build_client` 与 `ApprovalPageStub`，让详情数据包含两个不同 listing，同一天的价格分别为 ¥399 和 ¥599，再通过真实审批路由渲染页面。响应为 200，价格区只得到两组日期和金额，没有 listing、渠道或房间身份。

影响：员工不能判断参考价属于哪间房，人工确认成交金额时可能选错参照。本次没有证明发生过生产错误定价，更没有证明系统自动按错误价格建单。

建议：利用已有 `properties.channels` 对应关系，按房间、渠道标注或分组；归属匹配应同时考虑渠道类型与 listing ID。页面可以关联所选房间显示，但参考价仍仅供人工参考，不能替代实时房态或自动决定成交金额。

建议验收：同一天、至少两间房、不同金额能够分辨归属；无法映射的价格明确标记，不能静默归到某间房；保留无参考价、上游不可用和人工最终金额的现有边界。优先使用合成数据，不需要真实 Hostex 调用。

### F02 · P1 · 客户摘要版本冲突后返回 JSON，缺少编辑恢复

类别：错误反馈与实现完整性。

源码调用链：

- `src/homestay_bot/templates/customers/detail.html::memory-edit`，100–101 行：摘要表单带 `expected_version`、CSRF 和 `data-unsaved-warning`。
- `src/homestay_bot/routes/customers.py::update_customer_summary`，530–554 行，经 `CustomerAdminService.update_summary` 调用仓储。
- `src/homestay_bot/repositories/customers.py::SQLAlchemyCustomerRepository.update_summary`，1123–1147 行：版本不匹配时抛出 `CustomerConflictError`，防止覆盖并发更新。
- `routes/customers.py::_raise_page_error`，200–207 行，把该错误转换为 HTTPException 409。
- `src/homestay_bot/routes/page_errors.py::handle_http_exception`，79–96 行：仅给特定 GET 503 返回 HTML，其它状态沿用默认异常响应，因此这次 POST 409 返回 JSON。
- `src/homestay_bot/static/admin.js::dirtyForms`，246–251 行：提交发出时清除当前表单的未保存提醒，没有服务端失败后的输入恢复机制。

本地复现：使用 `tests/browser/test_admin_batch_workbench.py::admin_client` 的临时 SQLite、真实客户服务及路由，并注册生产使用的 HTTP 异常处理器。取得新 CSRF 后，向合成客户 #1 的 summary 提交非空短摘要及过期 `expected_version=99`，请求接受 `text/html`。

实际结果：状态 409、`Content-Type: application/json`，正文为 `{"detail":"客户摘要已发生变化，请刷新后重试"}`。失败响应没有编辑表单、刚输入的草稿或页面内核对与重试入口。

影响：数据库版本保护有效，但员工需要自行恢复并重新核对输入。不能断言浏览器后退一定丢失草稿；可以确认的是失败响应没有保留或提供恢复路径。

建议：版本冲突时按 HTML 请求重新渲染编辑页，保留本次非秘密输入，展示最新摘要及冲突说明，并签发有效 CSRF；让员工核对后明确重试，不自动覆盖并发结果。可参考 `src/homestay_bot/routes/complaints.py::_render_detail / _action` 已有的 `submitted_draft` 恢复方式，不需要另建持久化草稿系统。

建议验收：过期版本会被拒绝且不覆盖最新摘要；失败页面可见提交草稿及最新内容；核对重试使用有效令牌；JSON 调用方保留状态契约；避免仅断言模板字符串存在。

### F03 · P1 · 经典主题空输入框边界对比度不足

类别：无障碍与主题。

源码位置：`src/homestay_bot/static/app.css:: :root --line-strong`，第 21 行；通用 `button, input, select, textarea` 规则，第 68 行。

实际计算样式：经典主题下，审批页空的「最终房费（人民币）」输入框背景为 `#ffffff`，周围 section 也是 `#ffffff`，1px 边框为 `#cbd5e1`，没有占位文字。边框与背景对比度约 **1.48:1**。这处控件需要边界帮助识别，却未达到 [WCAG 2.2 SC 1.4.11 的 3:1 要求](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)。

同一检查中，暖色控件边框 `#8d8172` 与底色 `#efe9de` 约 **3.15:1**。这只证明对应控件的组合，不代表整个暖色主题全部通过 WCAG。

建议：为经典主题输入框定义足够深的控件边界色，按实际相邻底色验证。不要为了这一项把所有卡片分隔线一并加重；保留当前暖色方向及焦点反馈。

建议验收：验证空输入框、文本域及相关实际控件背景上的非文字对比度；同时检查普通、焦点、错误状态。测试计算浏览器实际样式，而不是只检查 CSS 字面量。此问题可能早于暖色主题，不能归因为本轮设计回归。

### F04 · P2 · 手机列表遗漏桌面已有的运营信息

类别：响应式与信息层级。

源码证据：

- `src/homestay_bot/templates/customers/index.html` 第 16 行桌面行展示 `stay_date_label` 和 `tag_names`；第 18 行手机卡片不展示这两项。
- `src/homestay_bot/templates/properties/index.html` 第 9 行桌面行展示 `room_number`、`room_type` 与 `next_check_in_date`；第 11 行手机卡片省略这些字段。

影响：手机用户需要逐条打开详情才能比较住宿日期、房号及下次入住安排。存在详情入口，所以定为 P2，而不是整页功能不可用。

建议：在手机卡片以次要信息行保留住宿日期、房号 / 房型和下一次入住日期；标签按实际密度换行或适度收纳。优先复用已有页面数据，不新增查询或独立数据投影。

建议验收：以包含入住日期、标签、房号、房型和下次入住记录的合成数据检查手机可见信息；同时覆盖长名称、无订单、无标签及空列表。320px 下不因补字段重新产生页面级溢出，桌面行为保持。

### F05 · P2 · 批量分派未选员工仍能提交，失败后选择丢失

类别：前置校验与操作恢复。

源码调用链：

- `src/homestay_bot/templates/tasks/index.html`，65–67 行：分派员工默认空值「选择员工…」，没有分派专属的前置校验。
- `src/homestay_bot/static/admin.js::fillConfirmPlaceholders`，143–164 行：取选中 option 的文本代入确认句，空值情况下取到的是「选择员工…」。
- 同文件 `selectionForms` 内的 `guard`，423–449 行：检查任务选择、动作资格和重复提交，没有检查分派目标员工。
- `src/homestay_bot/routes/tasks.py::assign_selected_tasks`，539–550 行：消费 CSRF 后拒绝未选员工，交由业务拒绝处理器回跳页面。

本地复现：打开 `status_filter=pending_assignment` 队列，只勾选可分派的合成任务 #12，不选择员工。控件实际值为空，`required=false`，分派按钮仍启用。点击后的本地页面提示「请先选择要分派给哪位员工」，已选任务数为 0。

该次浏览器工具点击报告了超时，但后续 DOM 确认了拒绝提示和选择清空；不把工具超时当作产品缺陷，也没有把它计为完整原生确认框验收通过。确认句的占位文本结论来自上述源码。

影响：服务端拒绝正确，没有据此认定错误分派或权限漏洞；问题是用户走完无效操作后需要重新勾选。

建议：仅对分派动作，在确认与忙态之前检查员工，给出明确提示并聚焦选择框，保留当前任务选择。不能直接把共享员工 select 全局设为 required，因为取消、归档等动作不需要员工。

建议验收：选任务但未选员工时，没有请求和确认框，选择保持，提示可感知且焦点到达员工控件；选择有效员工后正常进入确认；取消及归档不被员工字段阻断；继续保留服务端校验、资格判断及重复提交保护。

## 4. 已有验证证据

以下均来自上一轮审查，同一源码输入仍有效。本次只新增 Markdown，不重复运行业务测试。

```sh
.venv/bin/pytest -q \
  tests/browser/test_admin_batch_workbench.py \
  tests/browser/test_admin_interactions.py \
  -k 'mixed_selection or mobile_toolbar or drawer_accessibility or cross_form_submission or select_all_never or selection_survives or confirm_text_follows or typed_confirm_counts or no_script_fallback'
```

结果：**11 passed, 61 deselected, 1 warning，19.68s**。覆盖混合资格、固定操作栏、抽屉焦点、跨表单未保存内容、镜像选择、断点切换、确认文案与无脚本回退。

```sh
.venv/bin/pytest -q tests/browser/test_admin_theme.py -k contrast
```

结果：**1 passed, 14 deselected, 1 warning，12.90s**。对应 `test_warm_text_and_action_contrast_uses_actual_computed_surfaces`；这项暖色测试不能反证 F03 的经典主题缺陷。

两次警告都是 Starlette TestClient 对 httpx 使用方式的弃用提示，没有作为本轮功能失败处理。合计 **12 项相关测试通过**；没有运行全量、真实模型门禁或真实客人收发，原因是本轮没有业务源码改动，审查范围可以界定。

额外证据是 F01 的合成多 listing 渲染、F02 的真实服务版本拒绝、F03 的实际样式计算、F05 的本地页面反馈，以及 28 个浏览器视图。F04 主要由桌面 / 手机模板数据差异确认。它们不等于五项缺陷已有专门的持久化回归用例。

临时本地审查服务已停止，审查标签已关闭，浏览器视口已恢复。复核者需自行重建合成环境，不能依赖该临时服务仍运行。

## 5. 设计工具结论与保留项

Impeccable 局部暂评为 **13/20**：无障碍 2、性能 3、响应式 3、主题 3、交互完整性 2。性能仅依据源码观察，未进行线上计时或 Lighthouse；评分是审查判断，不是性能或 WCAG 认证。

静态检测器记录 51 条 advisory：49 条设计系统颜色提示、2 条重复条纹提示，没有非 advisory 项。模板未完整渲染、继承样式和双主题范围会影响这类结果，不能直接当作 51 个真实缺陷或新增回归。

本轮修复 0 项，新增忽略 0 项，上述 F01–F05 留待处理；没有使用 ignore-file / ignore-rule。日历分格与「今天」标记具有日期定位语义，保留当前设计。已有的主题、导航分组、危险操作确认、资格提示与渐进增强基础应继续保留。

## 6. Claude 复核交付建议

请针对 F01–F05 分别返回「确认 / 反驳 / 需补证据」、优先级和源码依据；如发现反向证据或不同根因，指出哪一条前提失效。尤其复核以下边界：

1. F01 的 listing → 房间映射是否会遇到渠道 ID 重名、无法映射或多渠道；价格身份展示如何与人工选择关联。
2. F02 的 HTML 恢复如何保留草稿、最新摘要、版本及一次性 CSRF，且不改变 JSON 调用方契约或削弱并发保护。
3. F03 是否确实属于需要边界识别的作者自定义控件，并按实际相邻背景复算，不把普通卡片分隔线混入控件要求。
4. F04 哪些字段是手机现场判断所需，如何在既有数据与卡片结构中补齐。
5. F05 校验顺序是否在确认、手输及忙态之前，是否只约束分派而不影响同表单其它动作。

若用户随后授权修复，建议按 F01 / F02 → F03 → F04 / F05 排序，先形成对应 Spec，再实施最小修改并运行有判别力的相关验证。保留当前后端确定性保护，不扩展为重新设计全站、引入新前端框架或建设通用草稿平台。
