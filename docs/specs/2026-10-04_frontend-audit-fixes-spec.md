# 前端功能与交互缺口修复 Spec

日期：2026-10-04。源码基线：`main` / `1ec9f95b17d0aca3a205588b20a306753b5c6226` / 1.67.0。

状态：**v4，D4 已完成本地实施与相关验证；D3 外部统计仍待独立授权。** 原 v2 按委托完成 F01–F05，v3 完成 P3。用户随后明确「做完吧」，据此采纳并实施 §8 D4 的既定统一方案；上一条不调用真实外部服务、不提交、推送、部署的限制保持。D3 的统计授权不由笼统完成指令代替。

输入：

- 用户要求先核实两份工作区文件来源，未经确认不暂存；以 Claude 复核 §2 为输入，按 F01/F02 → F03 → F04/F05 编写并分段确认。
- `docs/reviews/2026-10-04_codex-to-claude-frontend-audit-handoff.md`：初审与本地复现。
- `docs/reviews/2026-10-04_claude-to-codex-frontend-audit-review-handoff.md`：源码复核；该复核没有独立重跑浏览器和业务测试。
- `docs/reviews/2026-10-04_claude-to-codex-frontend-audit-fixes-review-handoff.md`：实施复审；报告中 277 + 145 个通过用例为 Claude 独立执行的证据，不计为本轮 Codex 新执行结果。
- `AGENTS.md`、`DESIGN.md` 与开发手册「后台管理台安全边界 / 高密度运营界面」。

## 1. 工作区文件来源与保护

### 1.1 已验证事实

| 文件 | 恢复前工作区内容 | 与 Git 历史的对应 | 被回退的 HEAD 内容 |
| --- | --- | --- | --- |
| `docs/releases/1.67.0.md` | blob `27fb36dbb43bdccc95046f59983b906c879bfbef` | 与 `74c904d` 中该文件逐字相同 | 删除 2026-10-03 发布现场补录、登录页面与审查入口两节 |
| `tasks/todo.md` | blob `6995098a97f76a4e85864e4bdc802b88ce0f70b4` | 与 `74c904d` 中该文件逐字相同 | 完成标题回到当前任务，发布三项由已完成改回未完成，并删去现场说明 |

验证依据：`git diff 74c904d -- <上述两文件>` 无差异；工作区 `git hash-object` 与 `git rev-parse 74c904d:<文件>` 分别一致。HEAD `1ec9f95` 保存了完整补录。

两文件 mtime / ctime 均为本机时间 2026-10-03 21:20:22；在本会话对应时间窗口的工具调用记录中未找到这两个文件的写入。**内容来自旧 Git 版本已确认；是谁、经何种软件保存尚未确认。**「Obsidian 或编辑器覆盖过期缓冲区」只是可能解释，不作为事实。

### 1.2 处置 W1（已完成）

用户说明未改文件并委托判断完成，已仅在工作区恢复两文件到 HEAD；发布证据恢复完整，旧内容仍可由 `74c904d` 取回。保存者未知，不推断用户或软件责任。`tasks/todo.md` 保留历史记录并增加本次进度。未暂存；既有审查报告及 `.impeccable/critique/` 保留。

## 2. 第一段：现状与改动边界（已采纳）

### 2.1 修复前源码基线的逐项复核

| 编号 | 修复前源码证据 | 规划优先级与影响 |
| --- | --- | --- |
| F01 | `src/homestay_bot/integrations/hostex_client.py::HostexClient.list_reference_prices` 汇总全部房源渠道，以全局 booking_site 优先；`services/approval_page_service.py::ApprovalPageService.get_detail` 分别读取房源和价格；`templates/approvals/detail.html::price-list` 仅显示日期、金额 | P1。所有房间的价格缺少归属；有些房间会因全局渠道选择而静默缺价 |
| F02 | `src/homestay_bot/services/customer_errors.py` 的三类错误继承 `OperationRefused`；`routes/customers.py::_raise_page_error` 却转成 HTTPException；`main.py` 已注册 `handle_operation_refused` | P1。须修公共 POST 错误传播，另做摘要冲突草稿恢复 |
| F03 | `src/homestay_bot/static/app.css::--line-strong` 为 `#cbd5e1`，通用控件边界引用它；白底约 1.48:1 | 采用复核的 P2。流程有可见标签且可完成，但必要边界仍有无障碍差距；不把历史来源本身当成降级依据 |
| F04 | `src/homestay_bot/templates/customers/index.html` 手机卡片省略住宿日期、标签；`templates/properties/index.html` 省略房号、房型、下次入住 | P2。手机列表缺少比较信息，字段已在页面数据中 |
| F05 | `src/homestay_bot/static/admin.js::selectionForms.guard` 检查选择、资格和提交锁，不检查员工；`routes/tasks.py::assign_selected_tasks` 最终拒绝空员工 | P2。前置校验不足，回跳后需要重新勾选；服务端拒绝本身正确 |

F02 的调用方已按 AST 核对：`_raise_page_error` 有 13 处调用，分别为 3 个 GET 和 10 个 POST 入口。根因修复不能不加区分地把 GET 错误也重定向回原页面，否则可能形成缺失档案或合并页的循环。

F01 另有一次详情重复读取房源字典的既有开销：`get_detail` 读取一次，`list_reference_prices` 内部再次读取。本文记录该现状，候选最小方案不为消除此开销新增共享接口参数；不声称本轮已优化上游请求次数。

### 2.2 与 Claude 复核的必要修正

Claude 上一份报告 §4 的「Hostex 客户端不涉及 REPLY_PATHS，因此无需真实模型门禁」结论**作废**；最新实施复审 §1 也已明确撤回。核对当前最终工作区：

- `scripts/release/reply_gate.sh::REPLY_PATHS` 包含 `src/homestay_bot/integrations/hostex_client.py`、`src/homestay_bot/integrations/deepseek_client.py`、`src/homestay_bot/tools/reply_regression.py`；三者均有工作区改动。
- `HostexClient.list_reference_prices` 已改为按房间选渠道；`HostexReadOnlyToolExecutor._reference_prices` 已用 `(channel_type, listing_id)` 做归属与单房渠道固定，并进行币种筛选；`FakeHostexClient.list_properties` 的合成渠道已声明 CNY。

因此，本批改动已命中回复门禁，**发布前固定按 `REPLY_GATE_SCOPE=all` 规划**，不沿用跳过或缩小范围的结论。门禁须覆盖最终确认后的源码，包括未来获准实施的 P2；本阶段只规划，不调用真实模型或上游。

### 2.3 本轮实施边界

- 顺序固定为 F01/F02 → F03 → F04/F05，一份 Spec、一次整体验收规划，功能按当前委托采纳。
- 保留现有 Jinja、原生表单、共享 CSS/JS、两套主题、业务状态映射、危险操作确认、权限、一次性 CSRF、事务和版本保护。
- 不改建单金额决策、实时房态来源、订单状态机、客户合并业务规则或客户摘要内容生成规则；不新增持久化草稿、数据库迁移、依赖或前端框架。
- 不改 `application.py` 的装配、全局错误处理器契约。D1=B 时不改回复链实现；D1=A 时，共享渠道规则及客人侧必要的价格身份转换兼容已纳入委托范围，不包含客人回复其它问题的修复。
- 当前用户已委托判断并完成，按本版范围进入实现。

## 3. 功能点（已采纳）

### 3.1 F01 · 房间归属与渠道缺价

基础功能：

1. 使用现有房源 channels，以 `(channel_type, listing_id)` 对应物理房间；不能只用 listing ID。
2. 参考价按房间分组，每行可辨认渠道、日期、渠道币种及金额，保留明确的「仅供人工参考」说明。
3. 无法匹配或同一键对应多个房间的价格列到「未匹配房间」，不静默归属、不删除原价格行；不让它们成为自动定价依据。
4. 无参考价的房间明确显示缺价状态，保留上游逐项降级、确认资格及拒绝入口。价格仍不等于房态或已收款金额。
5. 房间下拉仍要求员工明确选择；不自动选择房间、金额或渠道，不自动填写成交金额。基础修复不新增选择联动脚本；≤520px 渠道资料与金额分行，长编号不挤压金额。

技术方案：

- **采用：审批服务层映射。** `ApprovalPageService.get_detail` 已有 JSON 化房源字典和渠道价格，用现有字典构造页面分组；不增加公共客户端模型字段。修改范围集中在审批服务与模板。
- **备选：客户端补 property_id。** 可以利用客户端已经取得的房源字典，但会改变共享 `ListingCalendarDay` 的序列化形状，必须迁移替身、检查所有消费者及回复门禁。当前不把它作为默认方案。

渠道规则独立列为 D1，不由上述映射方案自动决定。重复取房源字典的优化留在现有状态，除非用户在决策段另行纳入；不为未来扩展增加 optional 参数或并行接口。

### 3.2 F02 · 根因层与摘要专项

#### A. 根因层：10 个 POST 入口

`routes/customers.py::_raise_page_error` 对 POST 捕获的 `OperationRefused` 保留原错误、状态码及可展示消息，设置明确安全的回跳目标后重新抛出，交给已注册处理器。未知异常仍使用通用 500 文案，不回显内部细节。

JSON 请求保留 403/404/409 和 detail 契约；HTML 业务拒绝用现有 303 + 一次性错误提示恢复。GET 的三处错误转换保留现有 HTTP 行为，不指向自身重试。

| POST 符号（均在 `routes/customers.py`） | 业务拒绝后的默认落点 |
| --- | --- |
| `review_customer_merge` | 当前合并复核页；建议不存在时客户列表 |
| `create_manual_customer_merge` | 来源客户的 governance 页签 |
| `update_customer_tags`、`update_customer_note` | 来源客户的 overview 页签 |
| `update_customer_summary` | memory；HTML 摘要版本冲突走下面专项 |
| `refresh_customer_context`、`delete_customer_summary`、`review_customer_memory` | memory；保留重算冷却提示已有路径 |
| `clear_customer_test_data`、`release_customer_conversation` | service 页签 |

回跳目标复用 `_customer_redirect` 当前的客户、页签与来源语义：必要时提取 `_customer_return_path`，只服务成功跳转及业务拒绝这两个真实调用方。列表来源 `return_to` 继续经 `safe_return_path` 校验并作为来源参数携带，不把它直接当作当前编辑页。目标已删除时安全返回客户列表。

权限校验与 CSRF 消费仍在当前位置。此次范围是服务 / 仓储业务拒绝的传播，不覆盖框架输入校验、认证失败或未知 500 的全部页面化；不把它们自动塞进业务拒绝类型。

#### B. 摘要专项：原地核对、明确重试

HTML 摘要版本冲突时返回状态 409 的 memory 页签，展示数据库最新摘要，同时把本次两个输入原样回填编辑框，并明确说明草稿尚未保存。冲突时自动展开编辑区，员工无需再找折叠入口。

- 保留现有两字段各 4000 字的路由 / 表单约束，不改变仓储版本条件和内容裁剪。
- 当前摘要区展示最新内容；编辑框展示提交草稿，二者不能覆盖混同。重试表单使用当前读取版本，新签发客户对象与浏览器绑定的一次性 CSRF；员工核对后明确提交。
- 若在核对后再次并发更新，仍拒绝并再次展示最新内容与草稿；不自动强制覆盖。
- 若客户已不存在或权限不允许再次读取，不输出草稿 / 档案给未授权主体，沿根因层的安全拒绝路径处理。
- 草稿只留在本次认证响应中，不写数据库、不放 session Cookie、不写日志；模板保持自动转义，不用 safe 渲染用户正文。
- 复用 GET 和失败页共有的详情上下文，必要时提取 `_render_customer_detail`，避免复制一套获取客户数据与签发令牌的逻辑。不改变其它页签装配。

### 3.3 F03 · 控件专用边界色

- 新增 `--control-border`，经典值 `#64748b`，暖色 `#8d8172`；按 sRGB 公式，经典在白底 / 页面底上约 4.76 / 4.40，暖色在卡片 / 页面底上约 3.15 / 3.61。实施后仍需在实际计算样式上验收必要边界 ≥3:1。
- 仅让文本、数字、日期、搜索、URL、密码、文件等可见 input 及 select / textarea 使用该变量。hidden 不涉及展示；checkbox / radio 保留原生呈现；按钮仍沿用现有按钮令牌。
- 不加深 `--line-strong`，不改变卡片、表格、日历和底部操作栏的分隔线，不修改控件圆角、尺寸、主题切换或危险状态语义。
- 在 DESIGN.md 同步记录 classic-control-line 及控件变量职责，避免令牌与规范失配；只在开始实施后修改。
- 普通、焦点和 aria-invalid 状态验证实际边界与焦点；不借此新建字段校验系统。禁用状态检查可辨识及不可操作，不机械套用活动控件 3:1 门槛。

### 3.4 F04 · 手机运营信息补齐

- 客户卡片增加住宿日期及已有标签；标签完整换行，不设置只截断前几项但没有展开入口的规则。
- 房源卡片增加房号 / 房型及存在时的下次入住日期；缺失房号 / 房型沿用桌面提示，不编造日期。
- 全部复用当前投影和格式化过滤器；不加查询、排序或分页规则，不改桌面列与点击目的地。
- 信息放在次要行；长名称、多个标签和 320px 下可换行，保留任务数与状态，不用隐藏溢出来遮住内容。

### 3.5 F05 · 分派目标前置校验

- 在 `selectionForms.guard` 中按当前 submitter / action 识别 assign；先完成任务数与资格判断，再检查员工，之后才允许确认、手输与忙态。
- 缺员工时取消事件和后续监听，复用 assign 提示区说明「请先选择要分派给哪位员工」，聚焦员工 select；已选任务和数量保持。
- 员工选择有效后清理过期缺员工提示，现有资格提示仍按真实任务刷新；不让二者互相遮盖。
- 覆盖 click 和 requestSubmit；没有 submitter 时保持原默认动作。取消、归档不要求员工，不给共享 select 全局添加 required。
- 保留服务端校验、一次性 CSRF、重复提交锁、镜像去重、手机固定操作区及无脚本路径。没有 JavaScript 时仍由后端安全拒绝，不承诺浏览器端保留选择。

## 4. 第三段：风险与决策（已采纳）

### D1 · F01 是否按每间房选择渠道（用户已委托判断）

| 选项 | 行为 | 影响与验证 |
| --- | --- | --- |
| A · 按房间选择（已选） | 每间房优先其 booking_site；该房没有时取其其它渠道，多渠道分别标注；没有任何渠道的房间明确无参考价 | 解决静默漏房，但修改 HostexClient 共享行为，客人回复参考价资料也可能增加；需要客户端、客人侧元组归属 / 单房渠道选择兼容与门禁验证 |
| B · 保留全局选择 | 继续「任意房有 booking_site，则只取全部 booking_site」；页面标示本房在当前优先渠道没有参考价，不把它当成房态不可用 | 客人回复输入保持；审批服务层即可完成归属与缺价展示。静默缺口变为显式说明，未补其它渠道价格 |

**D1 已选择 A。** 采用每房优先直订渠道，审批服务层分组与客人侧元组身份兼容。选择 A 不自动授权真实模型、上游调用、真实消息或发布。

选择 A 时，`HostexReadOnlyToolExecutor._reference_prices` 的房间归属与固定渠道均须用 `(channel_type, listing_id)`，避免新增回退渠道被错误归到另一间房或混合为同房的价格。客人侧仍采用单房一个渠道的既有表达，不增加最低价 / 跨渠道比较逻辑；对应不上的记录仍不能进入客人事实资料。`tools/reply_regression.py::FakeHostexClient.list_properties` 同步为合成人民币场景声明 CNY，避免旧替身缺币种掩盖正式报价路径。审批按字典币种显示金额，未知标明「币种未确认」，不换算。新增回退渠道仅在字典明确 CNY 时进入客人报价；既有直订缺币种保留人民币兼容约定，明确外币仍排除。该限定防止已固定人民币文案的客人输出误用外币。该兼容范围随 D1 一并纳入委托。

### D2 · 兼容、数据与验证取舍

- 采用服务层分组方案，不修改客户端公共模型；按钮及装饰线排除在 F03 边界色修改之外；手机标签完整换行。
- 现有摘要并发校验、CSRF、错误类状态及 JSON 契约保持；只在 HTML 对应路径恢复。
- 业务与登录校验发生在读取草稿 / 最新档案前；再次权限拒绝不继续渲染敏感档案。
- 前述最小取舍已采纳；实施出现不同边界时先更新 Spec，不自行扩大到回复链、装配或通用错误平台。

## 5. 实施顺序、文件与验证目标

下表为当前授权范围内的端到端步骤。

| 次序 | 文件 / 符号 | 修改目标与相关验证 |
| --- | --- | --- |
| 1 · F01 | `src/homestay_bot/services/approval_page_service.py::get_detail`；`templates/approvals/detail.html::price-list`；仅 D1=A 时 `integrations/hostex_client.py::list_reference_prices` 与 `integrations/deepseek_client.py::HostexReadOnlyToolExecutor._reference_prices` | 同日多房价、多渠道、跨渠道同 ID、无匹配 / 多归属、缺价和逐项上游降级。复用 `tests/unit/test_approval_page_service.py`、`test_approval_page_degradation.py`、`tests/integration/test_approval_routes.py`；共享路径改动才增加 `tests/unit/test_hostex_client.py`、`tests/unit/test_deepseek_client.py` 的必要身份转换 / 单房渠道覆盖 |
| 2 · F02 根因 | `src/homestay_bot/routes/customers.py::_raise_page_error / _customer_redirect` 及表列 10 个 POST；必要新 helper `_customer_return_path` | 各入口服务拒绝的 HTML 落点 / 提示与 JSON 状态；3 个 GET 不循环；恶意 return_to 被限制；未知异常不泄漏。`tests/integration/test_customer_routes.py::build_client` 接入生产实际异常处理器，不能只改替身断言 |
| 3 · F02 摘要 | `routes/customers.py::customer_detail / update_customer_summary`，必要新 helper `_render_customer_detail`；`templates/customers/detail.html::memory-edit` | 复用 `tests/browser/test_admin_batch_workbench.py::admin_client` 的真实会话门面、临时 SQLite，验证过期版本不覆盖、草稿和最新内容同页、新 CSRF 与新版本明确重试、再次冲突；保留正常保存及 JSON 契约 |
| 4 · F03 | `src/homestay_bot/static/app.css::root / warm / 控件边界`；`DESIGN.md::Colors`；`tests/browser/test_admin_theme.py` | 两主题普通、焦点、禁用及 aria-invalid 计算样式；经典白底 / 页面底、暖色卡片 / 页面底；边界达标且卡片 / 按钮样式不被改变 |
| 5 · F04 | `src/homestay_bot/templates/customers/index.html::mobile-card-list`、`templates/properties/index.html::mobile-card-list`；复用 `tests/browser` 真实路由设施 | 带住宿日期 / 标签 / 下一次入住的合成数据在手机可见；长值与空值；320px 不产生页面级溢出；桌面与详情入口保持 |
| 6 · F05 | `src/homestay_bot/static/admin.js::selectionForms.guard / refresh`；`tests/browser/test_admin_batch_workbench.py` 与 `test_admin_interactions.py` | 空员工时无请求 / 无确认 / 不忙态且保留选择与正确焦点；选员工后可提交；取消 / 归档、无 submitter、镜像及重复入口不回归 |

只补能区分缺陷的行为验证，不逐句断言样式或脚本源码，不为每个 helper 另写镜像测试。复用已有结果必须确认最终源码输入仍被覆盖。

### 5.1 运行与发布门禁边界

- 当前实施本地修复，运行相关离线和浏览器验证，不调用真实外部服务。
- 完成实施后仅运行所改行为及调用方相关测试。本方案不改 application.py、依赖或构建装配，不因此自动全量；若边界改变，按项目规则重新评估。
- D1=A 已实施且三份 REPLY_PATHS 文件有改动，未来发布前运行 `REPLY_GATE_SCOPE=all` 的真实模型全量门禁，记录最终源码及门禁结果。本轮不执行；执行前须另获真实 DeepSeek 调用的当前授权，离线用例不替代该门禁。
- 未来提交前必须对最终差异做新鲜 Ponytail 审查。提交 / 推送 / 部署分别等待当前授权，不自动暂存本轮文件。
- 当前不升级版本、不改 CHANGELOG、不创建新发布记录；正式发布时再按最终变更性质维护版本、CHANGELOG 和发布记录，不能静默删除 1.67.0 已提交证据。

## 6. 授权与决策记录

2026-10-04 用户明确「我没有动过文件，按你的判断来做完，做完后写交接文件」。当前指令优先，授权恢复 W1、采纳 §2–§5 最小方案、实施验证和写交接；D1=A，D2 采纳。仍不暂存、提交、推送、部署或调用真实模型、Hostex、企业微信。

2026-10-04 用户最新指令要求撤回错误门禁结论、以 all 规划发布门禁、先把 P2 的 Hostex 统计授权和币种统一方案写入 Spec 待确认，并允许顺手处理 P3。此指令约束本轮：原委托不延伸为 P2 的实施或外部调用授权。

随后用户明确「做完吧」，授权按已经给出的 D4 方案完成本地实现、验证与交接，不增加默认币种范围。D4 已采纳并开始；不调用真实外部服务的限制尚未被明确解除，D3 保持待授权，真实模型 all 门禁继续只规划。已单独询问是否授权一次 Hostex 只读统计，未获明确答复前不执行。

## 7. v2 实施结果（本轮前证据）

F01–F05 已完成，216 个不同相关用例有有效通过证据，Ruff、5 个源码文件 Mypy、JS 语法及差异检查通过。最终源文件及浏览器证据、Ponytail 自审与未覆盖边界见 `docs/reviews/2026-10-04_codex-to-claude-frontend-audit-fixes-implementation-handoff.md`。未暂存、提交、推送、部署或运行真实模型门禁。

## 8. 实施复审回应与待决策项

### 8.1 P2 修复前的证据与未验证事实

- `src/homestay_bot/integrations/hostex_client.py::Channel.currency` 可省略，默认 None；`HostexClient.list_properties` 固定读取 `offset=0, limit=100`，经模型解析后，缺字段和显式 null 都得到 None。`HostexClient._request` 默认最多三次尝试，`retry_safe=False` 才限制为一次。
- `src/homestay_bot/integrations/deepseek_client.py::HostexReadOnlyToolExecutor._reference_prices` 接受显式 CNY，或 `booking_site` 且 currency 为 None；显式外币和其它缺币种渠道不进入人民币报价资料。
- `src/homestay_bot/services/approval_page_service.py::ApprovalPageService._price_groups` 对所有缺币种使用「币种未确认」；`src/homestay_bot/templates/approvals/detail.html::price-list` 直接显示该标签。这与客人侧的直订人民币兼容约定不一致，P2 成立。
- 当前未调用真实上游；不能确认真实账号的渠道构成、currency 是否返回或其取值。合成测试不能证明该契约。若账号全为缺币种的 OTA，旧报价可能因新的币种保护被排除；这是条件风险，不是已确认的生产现象。

### D3 · 一次只读 Hostex 统计（待用户授权）

待决策：是否授权**一次**真实 Hostex `GET /properties?offset=0&limit=100`，统计渠道类型与 currency 字段。本轮不执行，也不新增采样代码。

获准后的执行与证据边界：

- 复用现有 Hostex 认证、信封解析及十秒超时；显式关闭重试（`retry_safe=False`），不自动翻页，不追加探针、价格日历或其它服务请求。失败即停止；额外请求须再获授权。
- 对这一次响应的原始 `channels[]` 统计各 `channel_type` 的记录数；分别统计 currency 字段缺失、null、空值及非空取值分布，避免模型默认值抹去字段存在性的证据。统计为渠道记录数，不冒充去重后的房间数。
- 仅保留统计时间、请求范围、响应房间数、分布及可确认的覆盖范围。若响应提供总数则记录总数；没有总数或未覆盖全部页时标注「本次样本」，不能把样本未见直订写成整个账号没有直订。
- 不保存原始响应、凭据、房源标题、房间或 listing 编号、客人信息；不写生产数据库或改线上配置。结果补入本 Spec；失败只记录脱敏原因，不把失败当成零渠道。
- 样本若显示 OTA 缺币种，继续排除其人民币报价；是否接受该范围、或另行补充经核实的币种来源，需用户独立决策。统计授权不包含该行为改变。

### D4 · 审批页与客人侧的币种口径统一（已采纳，本地完成）

建议沿用既有直订兼容约定：**仅 `booking_site` 且缺币种时默认人民币，并在审批页明确标注来源。** 这是一项兼容政策，不能表述成 Hostex 已返回人民币的事实；显式币种优先，其余未知币种不推定为人民币。

| 渠道 / 原始币种 | 统一的有效币种 | 审批页 | 客人侧人民币参考价 |
| --- | --- | --- | --- |
| 任意渠道 / CNY | CNY | 人民币金额 | 可纳入 |
| booking_site / 字段缺失或 null | CNY，直订兼容约定 | 如「¥399（直订默认人民币）」 | 可纳入，保持既有行为 |
| 其它渠道 / 字段缺失或 null | 未确认 | 「币种未确认 399」 | 排除 |
| 任意渠道 / USD 等显式非 CNY | 原币种 | 显示原币种及金额，不换算 | 排除 |
| 任意渠道 / 空字符串 | 未确认 | 标明未确认，不套用直订默认 | 排除；不得为恢复报价放宽校验 |

已采纳的实施方案：

- 在 `src/homestay_bot/integrations/hostex_client.py` 定义一个最小共享函数 `reference_price_currency(channel_type, currency)`，负责有效币种判定；复用现有 Channel，不新增模型字段、持久化配置或汇率依赖。
- 非字符串等不合法字段仍由现有模型边界校验并触发参考数据降级，不为了套用默认币种强制转换。
- `ApprovalPageService._price_groups` 和 `HostexReadOnlyToolExecutor._reference_prices` 两个调用方使用该函数。客人侧只接受其返回的 CNY；审批页依据返回值展示币种，对原始币种缺失而判定为 CNY 的记录补「直订默认人民币」说明，不再各写一份默认规则。
- `templates/approvals/detail.html::price-list` 调整金额与来源说明的呈现；保留完整渠道身份、未匹配房间区、单房固定渠道、住宿日期约束与人工建单金额。归属不唯一的记录不能成为客人事实资料。
- 相关验证：复用 `tests/unit/test_hostex_client.py`、`test_approval_page_degradation.py`、`test_deepseek_client.py` 和 `tests/integration/test_approval_routes.py`，覆盖上表及跨渠道撞号 / 多归属。审批页的现有真实服务浏览器用例位于 `tests/browser/test_admin_batch_workbench.py::test_approval_price_groups_remain_readable_on_phone`，复用该用例在两主题验证 320px 下人民币来源说明可读且不溢出，不在主题测试中另建重复设施。只补缺失的行为用例。
- **用户「做完吧」已确认并启动 D4 的本地实施。** D3 的外部统计与 D4 的代码实施互不自动授权。发布前仍须完成真实数据的覆盖判断和 all 模型门禁。

### 8.2 P3 的 v3 处置

- 公开 HTML 判断函数：`src/homestay_bot/routes/page_errors.py::_wants_html` 改名为 `wants_html`；同步两个异常处理器和 `routes/customers.py::update_customer_summary` 的三个现有调用点。保留 Accept 权重、状态、回跳及权限行为，不留无调用方的旧别名。
- `routes/complaints.py` 与 `routes/knowledge.py` 的同名私有函数各有本模块调用，未纳入本轮合并。
- `ApprovalPageService.get_detail` 与 `HostexClient.list_reference_prices` 的两次房源读取保留。此项已披露且非阻塞，不为减少一次请求新增缓存或修改公共接口。
- v3 验证只覆盖公开名称迁移影响的 HTML / JSON 拒绝、503 协商和摘要冲突路径；当时未执行 P2 代码、真实数据统计或真实模型门禁。

### 8.3 v3 的执行与验证记录（本轮前证据）

P3 的公开名称迁移已完成；三个调用点均使用 `wants_html`，无共享旧名称的遗留引用。原函数判断体未变，未修改 P2 的共享客户端、报价执行器、审批服务或模板；D3 / D4 仍待用户决策，重复房源读取保留。

本轮本地 **27 passed**：`tests/integration/test_admin_unavailable.py` 的 9 项；`test_customer_routes.py` 中全部 POST 的 HTML 拒绝、JSON / 删除对象落点、冲突后拒绝读取与正常保存的 16 项；`test_task_routes.py::test_refusal_with_html_quality_zero_keeps_json_contract` 的 1 项；`tests/browser/test_admin_batch_workbench.py::test_summary_conflict_keeps_draft_and_requires_explicit_retry` 的 1 项。后一项使用真实页面与临时 SQLite，覆盖再次冲突、显式重试和 JSON 状态。保留一个既有 Starlette 测试依赖弃用警告。

两份修改源码的 Ruff、Mypy 通过；`git diff --check` 通过。没有新增测试，没有因文档或机械改名重复跑全仓、报价测试或真实模型门禁。当前索引为空，`docs/releases/1.67.0.md` 与 HEAD 无差异；未暂存、提交、推送、部署或调用真实外部服务。门禁作废结论、待决策方案已写入本文，不能把 P3 的通过结果当作 P2 或生产验收通过。

### 8.4 v4 的执行与验证记录

D4 本地完成：`hostex_client.py::reference_price_currency` 是唯一币种判定；`ApprovalPageService._price_groups` 与 `HostexReadOnlyToolExecutor._reference_prices` 均已调用。审批投影保留原币种或未知标签，人民币以 ¥ 展示，仅缺币种直订附 `currency_note=直订默认人民币`；`templates/approvals/detail.html::price-list` 格式化为「¥399（直订默认人民币）」。客人侧的 CNY 筛选、完整渠道身份、单房固定渠道和日期边界保持。

本轮 **65 passed**：

| 验证 | 结果 |
| --- | --- |
| `.venv/bin/pytest -q tests/unit/test_approval_page_degradation.py tests/unit/test_hostex_client.py tests/unit/test_approval_page_service.py tests/integration/test_approval_routes.py --tb=short` | 55 passed；包含 10 项同一数据经过审批服务和正式报价工具入口的币种矩阵 |
| `.venv/bin/pytest -q tests/unit/test_deepseek_client.py -k 'reference_price or price_question or tool_executor' --tb=short` | 8 passed；包含撞号 / 多归属 / 不混合夜价与本地替身的报价正文证据 |
| `.venv/bin/pytest -q tests/browser/test_admin_batch_workbench.py -k approval_price_groups --tb=short` | 2 passed；真实服务响应、临时 SQLite、正式模板与 CSS，经典 / 暖色各在 320px 验证默认来源、外币、未知金额、空房、长编号及未自动填写建单字段 |

新增币种用例在修复前复现直订缺币种被审批标为未知；其它没有备注的记录用 `.get` 检查缺省值，不要求为已有视图强加字段。最终矩阵均通过。三个修改源码 Mypy、五份修改源码 / 测试 Ruff 及差异检查通过；一个既有 Starlette 弃用警告保留。两个手机截图已人工查看：`/tmp/yumi-frontend-audit-fixes/approval-currency-classic-320.png`、`approval-currency-warm-320.png`。浏览器只检查选定主题的金额说明布局；主题切换 / 存储仍沿用既有同源验证证据，不将此用例描述成新的切换验收。

简化自审：共享函数确有两个调用方；未新增模型字段、配置、缓存、依赖或接口参数。模板新增注释后仅做差异检查，不重复业务测试。v2 的 216、v3 的 27 和本轮 65 分别保留各自验证范围，不相加冒充新的最终全量结果。

D3 未执行，真实渠道构成 / 币种分布仍未验证；发布前固定 `REPLY_GATE_SCOPE=all`，尚未执行。本地口径统一不关闭真实契约的发布前风险。交接已补入 `docs/reviews/2026-10-04_codex-to-claude-frontend-audit-fixes-implementation-handoff.md`；未暂存、提交、推送、部署或调用真实外部服务。
