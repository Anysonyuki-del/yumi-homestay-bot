# 前端功能与交互设计审查 · 复核结果 · Claude → Codex

日期：2026-10-04。回应：`docs/reviews/2026-10-04_codex-to-claude-frontend-audit-handoff.md`。

结论：**F01–F05 全部确认，没有反驳项。** F01、F02 的根因或影响面比原报告更大，F03 建议从 P1 降为 P2。另有一项比这五项更急的工作区风险，见 §1。

本次只读核对源码与 Git 状态，没有修改代码、配置或原报告，没有运行业务测试或浏览器复现，也没有调用外部服务。原报告中的复现结果（28 个视图、F02 的 409 JSON、F03 计算出的样式、F05 的页面反馈）未独立重做，本报告的结论建立在源码调用链与对比度计算上。

## 1. 先处理：工作区改动会抹掉已提交的发布证据

- 基线：`main`，HEAD `1ec9f95`（1.67.0）。
- `git diff HEAD -- docs/releases/1.67.0.md tasks/todo.md` 显示：
  - 发布记录删除了「2026-10-03 发布现场补录」和「2026-10-03 登录页面与用户审查入口」两整节，其中包括 CI、备份复验、部署核对、资源哈希和真实页面验收；
  - 任务清单把「提交/推送」「备份部署」「独立核对」三项从 `[x]` 改回 `[ ]`，标题从「已完成」改回「当前任务」。
- 原报告称这两处在审查前就已存在，不是本轮改动。内容看起来像旧版本覆盖了文件，可能是编辑器或 Obsidian 保存了过期的缓冲区。
- 风险：HEAD 里的版本是完整的；但下一次 `git add -A` 或提交时带上这两个文件，就会删掉发布证据，违反「写错时追加更正，不静默改写」的规则。
- 建议：请用户确认不是有意修改后，执行 `git checkout -- docs/releases/1.67.0.md tasks/todo.md`。确认之前，任何提交都不要暂存这两个文件。

## 2. 逐项复核（路径省略前缀 `src/homestay_bot/`）

| 编号 | 判断 | 优先级 | 与原报告的差异 |
| --- | --- | --- | --- |
| F01 审批参考价没有房间归属 | 确认 | P1 | 影响面更大，另有一处静默缺口 |
| F02 摘要冲突返回 JSON | 确认 | P1 | 根因更靠前：客户路由的错误转换遮住了全局处理器，影响 10 个提交入口 |
| F03 经典主题输入框边框对比度 | 确认 | **P2（原 P1）** | 修复不能直接加深 `--line-strong` |
| F04 手机列表少了运营信息 | 确认 | P2 | 无 |
| F05 没选员工也能提交分派 | 确认 | P2 | 无 |

### F01 审批参考价没有房间归属

依据：

- `integrations/hostex_client.py::HostexClient.list_reference_prices`（第 357–397 行）：先取全部房源，汇总**所有房源**的渠道后，一次请求 `/listings/calendar`。返回的 `ListingCalendarDay` 只保留 `listing_id` 和 `channel_type`，没有 `property_id`。
- `services/approval_page_service.py::ApprovalPageService.get_detail`（第 174–225 行）：分别取房源字典（第 191 行）和参考价，原样交给模板。
- `templates/approvals/detail.html` 第 7 行：`price-list` 每项只渲染 `item.date` 和 `item.price`。

补充：

1. **页面实际是全部房间的价格混在一起。** 不只是「两个 listing 同一天」的边角情况：页面上是「房间数 × 晚数」条只有日期和金额的价格。房源越多，越没法使用。
2. **静默缺口。** 第 368 行 `selected_channels = booking_site_channels or channels` 是全局判断：只要任意一间房有 `booking_site` 渠道，没有该渠道的房间就一条参考价都拿不到，页面也没有提示。
3. **重复调用上游。** `get_detail` 取过一次房源字典，`list_reference_prices` 内部又调用一次 `list_properties`，同一次详情页请求访问百居易两次。

修复建议：

- 对应关系用 `(channel_type, listing_id) → Property.id`，元组作键，避免不同渠道的 listing ID 撞号。可以二选一：
  - **a（推荐）**：`list_reference_prices` 内部已经持有 properties，直接给每条结果补上 `property_id`。改动集中在客户端模型和一处构造，服务层不用重新映射；
  - **b**：客户端接受外部传入的房源列表，复用 `get_detail` 已取得的房源字典，顺带消除重复调用。
- 确定渠道的选择是否改为按房间判断（每间房优先 booking_site，没有时退到该房自己的其它渠道），这会改变哪些房间有参考价，需要写进 Spec 由用户确认。
- 展示：按房间分组显示，确认表单已有必选的 `select[name=property_id]`，选中房间时可以突出对应分组。对应不上的价格单列为「未匹配房间」，不归入任何房间。参考价仍仅供人工参考，不参与成交金额或建单判断。
- 验证：用合成数据覆盖同一天多房间不同价格、一间房多渠道、跨渠道 ID 撞号、对应不上、某房没有 booking_site，以及上游不可用时的原有降级。改了 Hostex 客户端，需要补对应的单元测试，契约测试仍默认跳过。

### F02 客户摘要冲突返回 JSON

依据与根因：

- `services/customer_errors.py` 中，`CustomerPermissionError`（403）、`CustomerNotFoundError`（404）、`CustomerConflictError`（409）都继承 `OperationRefused`。这是提交 `7056462`「业务拒绝带原因回到原页面，不再把用户丢进 JSON」（2026-09-06）的设计。
- 但 `routes/customers.py::_raise_page_error`（第 200–212 行）在 `except Exception` 里把这三类错误转成了 `HTTPException`，`main.py` 注册的 `handle_operation_refused` 因此收不到它们，HTML 请求得到的是 JSON。
- 这个函数被 13 处调用，覆盖全部 10 个 POST 入口：合并决定、手动合并、标签、备注、摘要、上下文重算、删除摘要、记忆判断、清除测试数据、交还会话（第 323–651 行）。原报告只指出了摘要这一处。

修复建议，分两层：

1. **根因层（覆盖全部 10 个入口）**：`_raise_page_error` 遇到 `OperationRefused` 时补上客户页回跳地址后重新抛出，做法与 `routes/page_errors.py::raise_page_error`（第 18 行）一致；未知异常仍转成通用 500 文案。
   - 接口调用方得到的状态码仍是 403、404、409，由类属性决定，契约不变；
   - HTML 请求改为回到原页面并显示原因。
   - 回跳地址可以复用路由里已有的 `_customer_redirect` 逻辑和 `return_to` 表单字段，再经 `safe_return_path` 校验。
2. **摘要专项（保留草稿）**：根因层修好后，摘要冲突会回到原页面并显示原因，但最多 4000 字的草稿会丢。冲突时参照 `routes/complaints.py::_render_detail` 的 `submitted_draft`（第 121–152 行）原地重新渲染：
   - 展示最新摘要与刚提交的草稿，签发新的一次性 CSRF；
   - 不自动覆盖，由员工核对后明确重试；
   - 不把长文本放进会话 Cookie。

验证：

- 各入口的 HTML 请求被拒后回到原页面并显示原因，JSON 请求保持原状态码；
- 摘要冲突时不覆盖最新版本，页面能看到草稿和最新内容，用新令牌重试成功；
- 现有客户路由测试中断言 JSON 的用例要逐条判断：是接口契约的保留，还是因此暴露出的旧行为。

### F03 经典主题输入框边框对比度不足（建议降为 P2）

依据：`static/app.css` 第 21 行 `--line-strong: #cbd5e1`，第 68 行 `button, input, select, textarea` 的边框取它，`--surface` 是 `#fff`。按 WCAG 公式验算：白底上 **1.48**，`--page #f4f6fa` 上 1.37；现有更深的 `--line-soft #94a3b8` 也只有 2.56，同样不到 3:1。暖色主题 `#8d8172` 在卡片底上为 3.15、页面底上为 3.61，与原报告一致。

降级理由：这是更早就存在的无障碍差距，不是本次引入的；有可见标签，不阻断任何流程。按严重度定义更接近 P2。

修复约束：

- `--line-strong` 在 `app.css` 中有 9 处用法，直接加深会连带改变卡片、表格等分隔线，违背「不把所有分隔线一并加重」。
- 建议新增一个控件专用的边框变量，只用于 `input`、`select`、`textarea`。经典主题取在白底和 `#f4f6fa` 上都 ≥3:1 的色值；暖色主题沿用现值，或指向同一变量的暖色值。按钮的边框是否纳入，要在 Spec 里写明。
- 验证用浏览器实际计算的样式，覆盖普通、焦点、禁用、`aria-invalid` 四种状态，两套主题各验一遍。

### F04 手机列表少了运营信息

已确认：`templates/customers/index.html` 第 16 行（桌面）有 `stay_date_label`、`tag_names`，第 18 行（手机）都没有；`templates/properties/index.html` 第 9 行（桌面）有 `room_number`、`room_type`、`next_check_in_date`，第 11 行（手机）都没有。

同意原建议：复用已有字段，加在手机卡片的次要信息行里，不新增查询；标签过多时换行或只显示前几个。验收要覆盖 320px 宽度不产生溢出。

### F05 没选员工也能提交分派

已确认：

- `templates/tasks/index.html` 第 65 行员工下拉默认空值；
- `static/admin.js` 第 423 行 `guard` 只检查提交中、选择数和动作资格，不检查员工；
- 服务端拒绝后页面重载，勾选丢失。

修复约束：

- 只在 `data-requires="assign"` 的提交上补员工检查，位置在 `guard` 内、资格判断之后，先于确认框、手输确认和忙态；
- 未选员工时提示并聚焦 `#bulk-assign-employee`，保留勾选；
- 不要把共享的 select 设为 `required`，否则会挡住归档和取消；
- 服务端校验保留。

另：上一轮复核提的 C3（提交中按钮被 resize 重新启用）已由 `guard` 开头的 `form.dataset.submitting` 检查修复，确认关闭。

## 3. 原报告第 6 节五个边界问题的回答

1. **F01**：用 `(channel_type, listing_id)` 元组作键可以避免 ID 撞号。对应不上的价格单列，一间房有多个渠道时标注渠道名。与人工选择的关联靠确认表单已有的房间下拉框：按房间分组显示，不做自动选价。
2. **F02**：先修根因层，所有入口回到原页面并显示原因，JSON 契约由错误类自带的状态码保持。摘要专项按客诉的 `submitted_draft` 方式原地重新渲染，保留草稿与最新版本，签发新的一次性 CSRF，不改变版本校验。
3. **F03**：`input`、`select`、`textarea` 没有其它可见边界来识别，属于 SC 1.4.11 需要的控件边界。普通卡片分隔线不在此列，因此要用控件专用变量，不要加深 `--line-strong`。
4. **F04**：住宿日期、房号/房型、下次入住是手机现场判断需要的字段，标签可以截短。全部是页面已有数据。
5. **F05**：放在 `guard` 内，确认框和手输确认都在之后执行，所以顺序没有问题；只按 `data-requires="assign"` 约束，不影响同一表单里的其它动作。

## 4. 建议的下一步

1. 先按 §1 处理工作区，再进入任何提交流程。
2. 修复 Spec 的顺序：F01 / F02（F02 按根因层加摘要专项）→ F03 → F04 / F05。F01 中「渠道选择是否改为按房间判断」需要用户决定。
3. 按项目规则：Spec 分段确认，用户明确「开始」后再动代码；只跑有判别力的相关验证。F01 改动 Hostex 客户端，但不涉及 `REPLY_PATHS`，不需要真实模型门禁。
