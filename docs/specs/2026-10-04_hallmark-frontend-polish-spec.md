<!-- Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4 -->

# Hallmark 前端审查修复 Spec

- 日期：2026-10-04
- 版本：R3（Claude 复审修订见 §4；Codex 补修范围见 §6）
- 状态：2026-10-05 按 §4 实施；2026-10-06 用户明确「开始修复 H01，并纳入经典主题指标重叠」，§6 补修已完成本地验证；不含暂存、提交、推送或部署。
- 源码基线：`894fd5b`。上一轮只读浏览器审查显示应用版本为 `1.68.0`；该观察不替代未来修复后的生产验收。
- 请求依据：用户要求安装 Hallmark 并审查前端，收到 5 项发现后要求「写spec」；初稿仅编写文档，后续实施授权与范围分别见 §4、§6。

## 1. 第一段：现状证据与改动范围（待确认）

### 1.1 证据来源与限度

上一轮对照 [DESIGN.md](../../DESIGN.md) 检查已登录工作台、入住安排、任务中心，以及房源、客户、审批和知识库列表。工作台、入住安排、任务中心检查了 320、375、414、768px；经典主题另作抽查。

检查范围内未发现整页横向溢出，抽查的静态文字对比度达标。仅查看页面与 DOM，没有提交经营表单；这些结果不证明写操作、真实消息或所有错误状态已通过验收。本轮重新核对下列源码及共享选择器的调用方，没有重新执行生产浏览器验收。

Hallmark 审查结果为 0 critical、2 major、3 minor；major/minor 是本次设计审查的优先级，不等同于后端故障等级。H01 使用其按钮折行反模式；H02/H03 来自响应式与交互检查；H04 来自信息节制检查；H05 是链接提示不足，不是客户档案入口失效。保留 DESIGN.md 已确认的双主题、字体及真实运营分组。

### 1.2 五项现状

下列路径均相对仓库根目录，行号对应本次基线。

| 编号 | 优先级 | 复现或源码事实 | 文件与符号依据 |
| --- | --- | --- | --- |
| H01 | major | 320px 工作台「查看全部房间」折行；768px 入住安排「4 项待关注」折行。320px 入住说明还被右侧入口挤窄。 | `src/homestay_bot/templates/admin/dashboard.html:14` 的 `.workbench-first > .section-heading`；`src/homestay_bot/templates/admin/operations.html:8–10` 的 `.operations-heading > .section-heading`；`src/homestay_bot/static/app.css:174` 的 `.section-heading` 无换行配置，`:523` 仅在 ≤520px 给运营按钮设置 `nowrap`。 |
| H02 | major | 320px 与 768px 的倒计时时长出现孤立的「钟」字。768px 实测某时长块高约 70.4px，行高约 35.2px，确为两行。 | `src/homestay_bot/static/app.css:599–606`：`.room-event > dd` 始终为等宽两列，`.countdown` 固定在第二列跨两行；`:750–753` 的手机规则只调整外层列数、间距和字号。 |
| H03 | minor | 手机抽屉菜单高 40px，页签链接实测约 42px；手机顶部账号入口约 38×40px。低于 Hallmark 的 44×44px 触控目标。 | `src/homestay_bot/static/app.css:265` 的 `.tab-nav a`、`:354` 的 `.side-nav a, .account-link`、`:369` 的 `.topbar__account`。主题 select 与打开/关闭抽屉按钮已有 44px，不需要重复处理。 |
| H04 | minor | 工作台「先处理」中的 3 行房间链接出现同名重复。房号缺失时主位回退为房名，辅助位又无条件显示一次房名。 | `src/homestay_bot/templates/admin/dashboard.html:17` 的 `.first-list__room`；`src/homestay_bot/services/admin_operations_service.py::AdminOperationsService._room_items` 将 `room_number`、`room_title` 分别传入 `RoomOperationItem`，没有要求二者相同。 |
| H05 | minor | 暖色客人姓名链接与正文同色，默认去掉下划线，仅悬停恢复；触屏上不易发现档案入口。链接本身可点击。 | `src/homestay_bot/static/app.css:601–602` 的 `.room-event__guest a`；暖色 `:781` 的 `--link` 与 `--text` 同为正文色。`src/homestay_bot/templates/components/ui.html::stay_guest` 按 `customer_id` 输出真实链接或未关联提示。 |

### 1.3 范围

同一批修复包含 H01–H05：改善既有页面的排版、触控和可发现性。实施代码限于共享 CSS 与工作台房间行的 Jinja 展示条件，使用原有数据和路由。

约束：

- 遵循 DESIGN.md 的经典/暖色两套令牌、字体、珊瑚动作和状态色；不增加主题、外部字体、依赖、动画或装饰。
- 保留房间排序、风险计数、操作资格、表单确认、CSRF、未保存提示、批量勾选、抽屉焦点与无 JS 可达路径。
- 倒计时仍由 `src/homestay_bot/static/admin.js::initOperationsCountdown` 的 `serverNow`、`formatDuration`、`renderCell` 计算，不改时区、计时、计划节点刷新或到点待确认语义。
- 房号去重只影响工作台展示，不补写房号、不合并房源、不改变数据库或客户关联。
- 真实房态、价格、客人回复、接口与生产配置均不属于此次修复。

## 2. 第二段：功能点与实施方案（待确认）

### 2.1 方案选择

| 方案 | 做法 | 影响 |
| --- | --- | --- |
| A · 定向修复（建议） | H01 限定工作台重点区与入住安排头部；H02 复用房间容器；H03 限定触控导航；H04/H05 修改对应展示。 | 主要改 2 个运行文件，保留其它标题、状态徽标与操作组的排布。 |
| B · 重定共享标题组件 | 统一修改所有 `.section-heading` 的布局契约并迁移调用方。 | 调用方还包括知识、客诉、任务、房源、审批与关注页，需扩大逐页验收。本批没有统一这些不同内容形态的需求。 |

本草案按 A 编写，尚未把建议视为用户批准。

### 2.2 H01：标题与入口不互相挤压

修改 `src/homestay_bot/static/app.css`：

1. 仅对 `.workbench-first > .section-heading` 与 `.operations-heading > .section-heading` 提供整组换行能力；文字组可收缩，按钮不参与收缩。
2. 这两个区域的直接按钮入口使用 `white-space: nowrap`，保留原文、计数与 href。
3. ≤520px 时，文字组与入口上下排列，入口左对齐；保留日期、完整房态来源说明和重点区的暖色标题带。
4. >520px 时，空间足够维持同排；空间不足时整组入口换到下一行。不能用省略号、隐藏说明或裁切按钮解决问题。

验收：两主题下，320px 的「先处理 · N 间」「查看全部房间」和 768px 的「N 项待关注」完整可读；按钮标签保持一行，无重叠或整页横向滚动。其它 `.section-heading` 调用方保持原布局契约。

### 2.3 H02：倒计时按可用内容宽度排布

修改 `src/homestay_bot/static/app.css` 的 `.room-event > dd`、`.room-event .countdown`、`.room-event__abs`：

1. 事件内部默认使用单列，取消强制第二列和跨行定位，依现有 DOM 顺序显示客人、倒计时、绝对计划时间。
2. 复用 `.room-operation-card` 已有的 `container-type: inline-size`；卡片内容宽度达到 760px 时恢复现有双列布局。判断的是卡片内容盒，不是整个窗口宽度。
3. 复核内容宽度 744、760、776px 的边界以及事件外层自动分栏；必要的门槛微调只能服务于下列验收约束，不能加入 JS 测宽或另一套计时器。
4. 正常字号下，完整时长块获得足够宽度，不能把「小时」「分钟」拆开或留下单字尾行。放大字体时允许在现有空格处自然换行，但「小时」「分钟」保持词内完整；仅在 `.countdown strong` 内调整换行规则，不能靠截断或缩小字号省略信息。
5. 不改 `.room-events` 的业务分组、客人归属、`.cal` 的日期轴布局，以及 `.countdown` 的属性、内容计算和状态词。无容器查询支持时使用默认单列。

验收内容包括「不足 1 分钟」「59 分钟」「1 小时 27 分钟」「23 小时 59 分钟」「2 天 23 小时」，以及已退房、退房待确认、到店待确认和多笔安排。正常字号下时长完整，放大字体后无信息丢失、覆盖或整页溢出。

### 2.4 H03：触屏导航至少 44×44px

修改 `src/homestay_bot/static/app.css`，在 `@media (any-pointer: coarse)` 下定向覆盖：

- `.side-nav a`
- `.sidebar-footer .account-link`
- `.topbar__account`
- `.tab-nav a`

链接高度至少 44px，账号图标入口宽度也至少 44px；保持图标与文字居中。使用实际元素尺寸，不以互相覆盖的透明伪元素扩大点击区。桌面纯鼠标环境保留现有紧凑密度。

保留页签内部受控横向滚动和 `aria-current`。抽屉内容增高后仍可滚动到主题与账号入口；打开、Escape 关闭、焦点返回及页面 `inert` 继续有效。原生 checkbox/radio、批量选择与其它经营按钮不在本项改动范围内。

验收同时覆盖手机、768px 触屏、带鼠标的触屏设备及 1280px 纯鼠标环境。指针能力由浏览器媒体查询确认，不能只用窗口宽度代替触控判断。

### 2.5 H04：房间名称只展示一次

修改 `src/homestay_bot/templates/admin/dashboard.html` 的 `.first-list__room`，仅在模板内派生去首尾空白的房号展示值，不写回数据：

| 输入 | 主位 | 辅助位 |
| --- | --- | --- |
| 房号与房名不同且非空 | 房号 | 房名 |
| 房号为 null、空串或仅空白 | 房名 | 房号待补充 |
| 房号与房名去首尾空白后相同 | 房名，显示一次 | 不重复渲染 |

三种状态均保留原房源详情 href、房间顺序、行动原因与操作入口。去重仅限这条房间链接，不把别的分区中合法重复出现的房名删掉。新增展示分支附中文注释，说明缺房号回退与同名去重的原因。

### 2.6 H05：客人档案入口默认可辨识

修改 `src/homestay_bot/static/app.css` 的 `.room-event__guest a`，默认显示细下划线；沿用 `--link` 和现有 3px 下划线偏移，不新建颜色或附加按钮。

hover/focus 下提示继续可见，保留现有焦点环与 44px 高度。仅作用于入住/退房摘要中的姓名链接，不改变导航、日历条和其它 `stay_guest` 调用方。`ui.html::stay_guest` 保持：有关联时链接真实档案，未关联时提示「未关联客人」，多笔安排不被伪装成某一个客人的档案入口。

## 3. 第三段：风险、精确计划与验收（待确认）

### 3.1 文件与职责

| 文件 | 改动位置/职责 | 验证目标 |
| --- | --- | --- |
| `src/homestay_bot/static/app.css` | H01 两处标题选择器；H02 事件内部网格及容器查询；H03 四类导航触控覆盖；H05 姓名链接默认提示。关键局部覆盖附中文注释。 | 不折断关键内容、44px 点击区、主题与其它布局不串色、不遮挡。 |
| `src/homestay_bot/templates/admin/dashboard.html` | H04 `.first-list__room` 的房号缺失/不同/同名分支。 | 同名不重复、未知房号有准确提示、href 与行动保留。 |
| `tests/browser/test_admin_theme.py` | 复用 `theme_site`、`_context`，补关键渲染与去重回归；按需扩展合成输入。 | 实际 HTTP 页面、本站静态资源与临时 SQLite 上的最终展示。 |

业务路由、服务、模型、数据库、`admin.js` 和 `theme.js` 不需要修改。已有 `tests/browser/test_admin_batch_workbench.py::admin_client` 装配了正式仪表盘/运营服务；扩展输入时优先使用每个测试独立的 SQLite 合成房源，保留既有用例数据，不新增手写运营服务替身。

### 3.2 实施顺序

- [ ] H01/H02：先补能捕获原缺陷的浏览器几何回归，再改标题与事件布局；两主题通过后继续。
- [ ] H03/H05：调整触控覆盖与默认链接提示，检查抽屉、页签、焦点和触屏/鼠标差异。
- [ ] H04：实现模板去重，用合成输入覆盖三种展示状态及 href。
- [ ] 一次综合复核最终 CSS、模板、测试和本 Spec；证据仍覆盖最终内容时直接复用。

完整确认并收到「开始」后才更新 `tasks/todo.md` 跟踪本批任务。每项完成即更新进度，普通 CSS 数值和断点调优在上述契约内自主完成；若需新增运行文件、改计时语义或扩大业务边界，先修订 Spec 并重新确认。

### 3.3 必要回归与验收方法

计划在 `tests/browser/test_admin_theme.py` 增加下列有判别力的用例；名字是未来实现目标，当前没有新增测试代码：

| 用例 | 输入与断言 |
| --- | --- |
| `test_hallmark_heading_actions_keep_full_labels` | 两主题，320/768px，真实工作台与运营页。用文本 Range 的行片段和实际边界判定按钮标签一行、完整标题与说明不重叠；断言原 href。原样式应被用例捕获。 |
| `test_hallmark_event_duration_fits_available_card_width` | 两主题，320/375/414/768/1280px，设置受控服务器观察时刻与合成计划时间生成上述时长。正常字号确认时长一行、完全位于事件块内，邻接姓名/绝对时间不覆盖；以真实容器宽度验证 744/760/776px 边界；扩大字体时验证词组与内容保留。原等宽布局应在 320/768px 失败。 |
| `test_hallmark_touch_navigation_targets_remain_reachable` | 使用触屏浏览器上下文并确认 `any-pointer: coarse`，测量四类入口宽高；打开导航后滚动访问底部控件，Escape 后焦点回到打开按钮。纯鼠标上下文确认没有应用触控扩高。现有 40px 菜单应被用例捕获。 |
| `test_hallmark_first_room_identity_is_not_duplicated` | 对隔离的合成房源分别设置 null、空串、空白、同名和不同房号，检查 `.first-list__room` 内房名的出现次数、缺失提示和原详情 href；保留房间行动。原无条件辅助房名应在缺房号/同名时失败。 |

H05 不新增逐句复述 `text-decoration` 源码的测试：在两主题截图与触屏界面确认默认提示，键盘聚焦后打开合成客户档案并确认落点；未关联与多笔安排保持非链接提示。

综合范围：

- 工作台、入住安排、任务中心：两主题 × 320/375/414/768/1280px。正常字号检查关键内容和入口；窄屏与事件分栏边界另查 200% 字体/缩放可读性。
- 导航触控媒体查询：两主题，触屏与纯鼠标分别检查；抽屉底部可达，焦点返回有效。
- 复用 `test_theme_pages_fit_scrollbar_width_and_show_one_control`、`test_switch_preserves_selection_draft_and_focus`、`test_warm_text_and_action_contrast_uses_actual_computed_surfaces`，保留真实垂直滚动条槽位，补入本次需要的断点。
- 根元素 `scrollWidth <= clientWidth`；没有以裁切/隐藏内容掩盖溢出。日历、页签等已有模块内的横向滚动保留。
- 截图只使用合成数据；以字段完整、操作可见、无覆盖为判据，不要求系统字体跨平台像素一致。

实施后目标命令：

```bash
.venv/bin/python -m pytest -q tests/browser/test_admin_theme.py -k 'hallmark or theme_pages_fit_scrollbar_width_and_show_one_control or switch_preserves_selection_draft_and_focus or warm_text_and_action_contrast_uses_actual_computed_surfaces'
git diff --check
```

如果实际浏览器装配或新增用例的位置调整，只更新对应命令与证据，不用无关用例数量替代这些约束。新增/修改测试函数同样写明中文职责注释。

本轮为文档：仅执行文档差异、链接与一致性自审，不跑上述浏览器/业务测试。实施范围没有 `application.py`、依赖或构建变化，也不触及 `REPLY_PATHS`；在范围保持可界定时，采用上述相关验证，不启动全仓测试、真实模型门禁或测试号收发验收。

### 3.4 风险与处理

| 风险 | 约束与处理 |
| --- | --- |
| 标题布局波及其它页面 | H01 使用两个已有区域的局部选择器，不改变共享 `.section-heading` 的契约。 |
| 容器查询看错内容宽度，或外层分栏后再次挤压 | H02 复用现有房间容器，测实际内容盒与阈值两侧；不影响 `.cal` 的既有查询。普通字段可自然换行，关键时长不能被裁切。 |
| 44px 菜单让抽屉变长 | 保留侧栏内部滚动，验收底部主题/账号、关闭按钮和焦点；不增加顶部固定层。 |
| 空白/同名房号被错误解释 | H04 只对展示值做去首尾空白；只有真实缺失显示「房号待补充」，同名仅去重；不回写资料。 |
| 姓名链接提示变成配色重设计 | H05 只增加细下划线，使用当前主题令牌与现有 href，不改变客户关联规则。 |
| 旧截图或本地验证被当成上线证明 | 源码、临时服务、未来生产验收分别记录。以后如获发布授权，按项目流程单独维护版本/发布记录、备份和部署证据；本 Spec 不授权发布。 |

回退边界为本批新增的 CSS 规则和工作台展示分支；不涉及数据回滚。若撤回其中一项，仍保留本批其它已验证修复和原有业务保护。

### 3.5 分段决策记录

| 段落 | 用户需要确认的内容 | 当前状态 |
| --- | --- | --- |
| 第一段 | H01–H05 为本批范围；接受现状证据与只修展示/交互提示的边界。 | 待确认 |
| 第二段 | 采用方案 A；窄屏标题堆叠、事件默认单列/宽容器双列、仅触屏导航扩高、房号三种分支、默认姓名下划线。 | 待确认 |
| 第三段 | 接受文件范围、相关浏览器回归、风险与发布边界。 | 待确认 |
| 实施门禁 | 完整确认后用户明确回复「开始」。 | 未授权 |

约束依据：[AGENTS.md「渐进式 Spec 与实施门禁」](../../AGENTS.md)、[DESIGN.md](../../DESIGN.md)、开发手册「高密度运营界面」。

## 4. Claude 复审修订与实施授权（R2，2026-10-05）

用户审阅 Claude 对 R1 的审查后指示「你直接修好然后写交接报告」。以下修订取代第 2、3 段中对应内容，其余条款不变。

| 编号 | R1 内容 | 修订 | 依据 |
| --- | --- | --- | --- |
| C1（H02） | 按 `.room-operation-card` 内容宽度 760px 恢复双列 | 改为不设门槛的内在换行：`.room-event > dd` 用 `flex-wrap`，倒计时 `margin-left: auto`，放不下时整块换到下一行；绝对计划时间独占一行；`.countdown strong` 用 `word-break: keep-all` 并覆盖父级的 `overflow-wrap: anywhere`，「小时」「分钟」不被拆开 | `.room-event` 是 `.room-events`（`auto-fit, minmax(280px, 1fr)`）的一格，宽度只有卡片的 1/2–1/3；卡片内容 760px 时事件块约 374px，桌面 1280px 下约 330px，按卡片门槛恢复双列会让缺陷在宽屏重现 |
| C2（H02 验收） | 测卡片内容 744/760/776px 边界 | 不再有门槛，改为在两主题 320/375/414/768/1280px 的真实事件块里放入最长时长文本，断言时长单行、位于事件块内、不与姓名和绝对时间重叠 | 同上 |
| C3（H04） | 房号缺失时辅助位显示「房号待补充」 | 房号缺失（null、空串、仅空白）或与房名相同时只显示房名一次，不加辅助位；不同时保持「房号 + 房名」。「房号待补充」的数据质量提示继续由入住安排页与房源页负责 | 生产三行全部重复，说明房号大面积缺失或与房名相同；R1 方案会把重复房名换成每行一句「房号待补充」，违背 H04 的信息节制目的 |
| C4（H01） | 新增 nowrap 规则 | 同时删除 ≤520px 媒体查询里已有的 `.operations-heading .button { white-space: nowrap; }`，由新规则统一负责，不留两处 | `app.css` 第 523 行 |
| C5（H03） | `any-pointer: coarse` | 保持。已验证 Playwright Chromium 在 `has_touch=True` 时 `any-pointer: coarse` 与 `pointer: coarse` 都为 true，无触屏时为 false，可以直接用于回归 | 本地探针 |

实施仍限于 `src/homestay_bot/static/app.css`、`src/homestay_bot/templates/admin/dashboard.html` 与 `tests/browser/test_admin_theme.py`。

## 5. 实施记录（2026-10-05，Claude）

- 实施范围与 §4 一致：`src/homestay_bot/static/app.css`、`src/homestay_bot/templates/admin/dashboard.html`、`tests/browser/test_admin_theme.py`。
- 新增回归 `test_hallmark_heading_actions_keep_full_labels`、`test_hallmark_event_duration_fits_available_width`、`test_hallmark_touch_navigation_targets_remain_reachable`、`test_hallmark_first_room_identity_is_not_duplicated`，共 6 个参数化用例，修复前全部失败、修复后连续三次通过。
- 更正 Claude 复审中的一处判断：桌面 1280px 下 H02 并不会重现。每张房间卡最多只有退房、入住两个事件，`auto-fit` 折叠空轨道，事件块约为卡片宽度的一半，而不是 1/3。卡片内容 760px 时事件块约 374px 的问题仍然成立，§4 C1 的内在换行方案不受影响。
- 本地合成数据下，H01 工作台 320px 的「查看全部房间」在修复前没有折行，只有入住安排页 768px 的「N 项待关注」复现了折行；回归仍覆盖两页、五档宽度。
- 新发现，不在本批范围：经典主题 320px 下，工作台「待我关注」指标的说明小字与「进入待我关注」链接重叠，基线代码上同样存在；暖色主题此前已修复。

## 6. Codex 复核补修与授权（R3，2026-10-06）

用户明确选择「开始修复 H01，并纳入经典主题指标重叠」。本节补充本次实施范围，取代上述对应的待确认及范围外状态；其余 H02–H05 的 R2 决策保留。不包含暂存、提交、推送、部署或真实外部调用。

### 6.1 现状与最小修正

- H01：两主题、两页在 480/520px 下仍为标题和入口同排，未满足 §2.2 的 ≤520px 上下排列。`app.css` 的两处局部 `.section-heading` 仅允许内在换行，未强制窄屏排列。只在已有 ≤520px 查询中将其改为纵向排列，文字组恢复自然高度并使用可用宽度，按钮沿用左对齐和单行文案；宽屏规则保留。
- 经典主题指标重叠：`templates/admin/dashboard.html` 的「待我关注」指标同时有 `small` 和 `a`；`app.css` 的 `.metric-item > small, .metric-item > a` 共用 `meta` 格位。320px 下当前和基线 CSS 均复现；暖色独立的 `action` 行无重叠。将这套已有手机/桌面格位移入公共 `.metric-item`，说明使用 `meta`、入口使用 `action`，移除被替代的暖色重复规则。适用于该组件所有调用方，不加单个指标特判。

运行代码只改 `src/homestay_bot/static/app.css`。不改指标内容、计数、href、模板或业务脚本；保留双主题字体、配色和响应式分栏。

### 6.2 相关验证与记录

- 扩展 `tests/browser/test_admin_theme.py::test_hallmark_heading_actions_keep_full_labels`：补入 480/520/521px，≤520px 断言按钮在文字组下方且左对齐，同时保留标签单行、原 href、无重叠和根元素无横向溢出的检查。
- 新增 `test_dashboard_metric_content_does_not_overlap`：两主题、320/375/414/480/520/521/768/1280px，读取真实指标子元素边界，检查任意两个内容块不重叠、位于指标内，重点确认说明和入口均保留。
- 使用现有真实同源 HTTP、临时 SQLite 和合成数据；先确认新增断言捕获原缺陷，再修 CSS。综合复用主题切换、页面宽度和对比度回归，只运行本次相关项；不跑全仓或真实模型门禁。
- 更新 `tasks/todo.md` 的本次进度和结果。稳定后维护 AOCI 受管理认知；其它已有工作区改动保留。

### 6.3 本地实施与验收（2026-10-06）

- 已按 §6.1 修改公共指标格位及两处局部窄屏标题排列；移除被公共规则替代的暖色重复样式。运行改动仅在 `app.css`，业务脚本和模板保持本轮开始时的状态。
- 红测：扩展后的 H01 与指标回归得到 **3 failed、1 passed**；分别捕获两主题 480px 下未上下排列、经典主题 320px 指标重叠，暖色指标通过。
- 绿测：H01、指标、页面宽度、主题切换草稿/焦点保留及暖色文本/动作对比度，共 **9 passed、16 deselected**。测试使用真实同源临时服务、临时 SQLite 和合成数据；请求拦截确认没有写请求。既有 Starlette TestClient 弃用警告不影响结果。
- `ruff check tests/browser/test_admin_theme.py` 和 `git diff --check` 通过。经典 320px、暖色 480px 的本地工作台截图已复核；说明与入口可独立阅读，标题按钮左对齐。
- 未跑全仓或真实模型门禁：本轮只改局部布局，不影响回复链、应用装配、依赖或构建。没有生产、真机、系统字体或读屏验收，截图中的合成健康提示不是生产状态证据。未暂存、提交、推送或部署。
