# Hallmark 前端审查修复 H01–H05 · 实施交接 · Claude → Codex

日期：2026-10-05。状态：**本地实现与相关验证完成，供 Codex 复审**。未暂存、提交、推送或部署，未调用真实外部服务，未写生产数据。

## 1. 依据与授权

- Spec：`docs/specs/2026-10-04_hallmark-frontend-polish-spec.md`。Codex 写的 R1 是第 1–3 段；Claude 的复审修订为 §4（R2），实施记录为 §5。
- 用户在 Claude 审查 R1 后指示：「你直接修好然后写交接报告」。授权范围是按 R2 实施并写本报告，不含提交、推送、部署或版本升级。
- 基线：`main`，HEAD `894fd5b`（1.68.0）。

## 2. 相对 R1 的方案修订（R2 §4）

| 编号 | 修订 | 理由 |
| --- | --- | --- |
| C1（H02） | 不按 `.room-operation-card` 内容宽度 760px 恢复双列，改为 `.room-event > dd` 内在换行：flex-wrap，倒计时 `margin-left: auto`，计划时间独占一行 | 事件块是 `.room-events` 自动分栏的一格；卡片内容 760px 时事件块约 374px，按卡片门槛恢复双列，「23 小时 59 分钟」（22px 约 175px）放不下，会在中等宽度重现缺陷 |
| C2 | H02 验收改为在真实事件块里测量最长时长，不再测 744/760/776px 卡片边界 | 方案不再有门槛 |
| C3（H04） | 房号缺失或与房名相同时只显示房名，不加「房号待补充」 | 生产三行全部重复，说明房号大面积缺失或与房名相同；R1 方案会让每行多一句提示，违背 H04 的信息节制目的。该提示仍由入住安排页、房源页负责 |
| C4（H01） | 删除 ≤520px 媒体查询里已有的 `.operations-heading .button { white-space: nowrap; }` | 由新规则统一负责，不留两处 |
| C5（H03） | 保留 `any-pointer: coarse` | 已验证 Playwright Chromium 在 `has_touch=True` 时该查询为 true、无触屏时为 false，可以直接回归 |

**更正 Claude 复审中的一处错误判断：** 复审报告说「桌面 1280px 下 H02 也会重现」，依据是假设事件块会分成三栏。实测不成立：每张房间卡最多只有「本次退房」「下一单入住」两个事件，`auto-fit` 折叠空轨道，事件块约为卡片宽度的一半。以修复前代码只测 1280px 和 768px，均未失败。C1 的理由（卡片内容 760px 时事件块约 374px）不受这处更正影响。

## 3. 改动（路径省略前缀 `src/homestay_bot/`）

| 编号 | 文件与位置 | 改前 → 改后 |
| --- | --- | --- |
| H01 | `static/app.css`，`.section-heading` 之后新增三条，作用于 `.workbench-first > .section-heading` 与 `.operations-heading > .section-heading` | 入口按钮会被挤成两行 → 标题区 `flex-wrap: wrap`，文字组 `flex: 1 1 16rem; min-width: 0`，按钮 `flex: none; white-space: nowrap`；放不下时整个按钮换到下一行。其它 `.section-heading` 调用方不受影响 |
| H01 | `static/app.css`，≤520px 媒体查询 | 删除重复的 `.operations-heading .button { white-space: nowrap; }` |
| H02 | `static/app.css` 的 `.room-event > dd`、`.room-event__guest`、`.room-event .countdown`、`.countdown strong`、`.room-event__abs` | 等宽两列 grid 加倒计时跨行 → flex-wrap；客人 `flex: 1 1 auto`；倒计时 `margin-left: auto`；`.countdown strong` 用 `word-break: keep-all; overflow-wrap: normal`，「小时」「分钟」不会被拆开；计划时间 `flex-basis: 100%`。不改 `admin.js` 的计时、格式和刷新逻辑，也不改 `.cal` |
| H03 | `static/app.css`，`.topbar__account span` 之后新增 `@media (any-pointer: coarse)` | 触屏下 `.side-nav a`、`.sidebar-footer .account-link` 至少 44px 高；`.tab-nav a` 改为 `inline-flex` 居中并至少 44px 高；`.topbar__account` 至少 44×44 并居中。纯鼠标桌面不变 |
| H04 | `templates/admin/dashboard.html` 的 `.first-list__room` | 无条件输出「房号或房名 + 房名」→ 模板内派生 `room_no = (room.room_number or "") \| trim`：非空且不等于去空白后的房名时输出「房号 + 房名」，否则只输出房名。href、行动原因、入口按钮不变；没有回写数据 |
| H05 | `static/app.css` 的 `.room-event__guest a` | 默认无下划线、仅悬停出现 → 默认 1px 下划线（沿用全局 3px 偏移和 `--link`），悬停时加粗为 2px |

所有新增规则和模板分支都附了中文注释，说明原因与作用范围。

## 4. 测试

`tests/browser/test_admin_theme.py`：

- `_context` 新增 `has_touch` 参数（默认 False，既有调用不变）。
- 新增 `_seed_rooms`：向本测试独立的 SQLite（`admin_client` 的 `tmp_path/admin.db`）追加合成房间和今日退房订单，不改共享夹具。
- 新增 `_LINE_COUNT`：用文本 Range 的行片段数统计实际行数，不复述样式源码。
- 新增 `_FREEZE_ELAPSED`：冻结 `performance.now`，避免测量期间越过 12:00 退房节点触发自动刷新。

| 用例 | 断言 | 修复前 |
| --- | --- | --- |
| `test_hallmark_heading_actions_keep_full_labels[classic/warm]` | 两页 × 320/375/414/768/1280px：入口按钮单行、与标题文字组不重叠、href 不变、根元素不横向溢出 | 失败：入住安排页 768px「1 项待关注」两行 |
| `test_hallmark_event_duration_fits_available_width[classic/warm]` | 五档宽度 × 五种时长（不足 1 分钟、59 分钟、1 小时 27 分钟、23 小时 59 分钟、2 天 23 小时）：时长单行、位于事件块内、不压住姓名和计划时间 | 失败：320px「1 小时 27 分钟」两行 |
| `test_hallmark_touch_navigation_targets_remain_reachable` | 触屏 390/768px：账号入口 ≥44×44，页签、侧栏导航、底部账号入口 ≥44px（0.5px 亚像素容差）；抽屉滑入完成后底部入口可滚到可点位置，且没有被其它层盖住；Escape 后焦点回到菜单按钮。纯鼠标 1280px：`any-pointer: coarse` 为 false，页签保持 <44px | 失败：账号入口 38×40 |
| `test_hallmark_first_room_identity_is_not_duplicated` | null、空串、空白、同名四种房号只显示房名一次且没有「房号待补充」；`A12` 与既有 `101` 保持「房号 + 房名」；每行保留行动原因 | 失败：房名重复两次 |

H05 按 R1 约定不写复述样式的测试，由截图人工复核。

## 5. 验证结果

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/browser/test_admin_theme.py -k hallmark`（修复前） | 6 failed，失败点如 §4 |
| 同上（修复后，连续三次） | 每次 6 passed |
| `.venv/bin/python -m pytest -q tests/browser/test_admin_theme.py tests/browser/test_admin_batch_workbench.py tests/browser/test_admin_interactions.py tests/integration/test_admin_dashboard_routes.py` | 141 passed，1 个既有 Starlette 弃用警告 |
| `.venv/bin/ruff check tests/browser/test_admin_theme.py` | 通过 |
| `git diff --check` | 通过 |

调整测试的过程中有两次失败，原因都在测试本身，不是产品缺陷，已修正：一次是抽屉滑入过渡中途就去测量位置（改为等抽屉完全进入视口）；另一次是 43.99998px 的亚像素取整（加 0.5px 容差）。焦点归还改为按状态等待，而不是固定延时。

人工复核：用临时脚本截取两主题下入住安排页与工作台在 320/768/1280px 的画面（合成数据，截图只在本机临时目录，脚本已删除、未入库）：

- 窄屏下「1 项待关注」「查看全部房间」整块换行，768px 下仍在右侧同排；
- 时长完整，客人姓名有默认下划线；
- 工作台去重符合预期。

没有修改 `application.py`、依赖、构建、`admin.js`、`theme.js` 或任何 `REPLY_PATHS` 文件，因此不跑全仓测试和真实模型门禁。

## 6. 未覆盖与说明

- **H01 工作台 320px 没有在本地复现。** 修复前，本地合成数据下工作台的「查看全部房间」没有折行，只复现了入住安排页 768px 的问题。回归覆盖两页、五档宽度，但工作台这一项的「修复前失败」证据不足；生产上的折行可能与真实数据或字体有关，需要在生产页面再确认一次。
- **字体放大没有自动验证。** 倒计时用的是 px 字号，浏览器文字缩放无法在测试中模拟。`keep-all` 保证「小时」「分钟」不被拆开，但放大后只能在空格处换行，可能出现「23 小时 59」「分钟」这样的分行。如需更严，要在 `admin.js::formatDuration` 里把数字与单位之间的空格改成不换行空格，这超出 Spec 的「不改计时」边界，没有做。
- **H05 只做了截图复核**，读屏、真机触控、安卓/Windows、Mobile Safari 都没有验证。
- **新发现，不在本批范围**：经典主题 320px 下，工作台「待我关注」指标卡的说明小字和「进入待我关注」链接重叠。已在基线代码上用浏览器实测确认同样存在：小字 y≈559、链接 y≈558，两者高度都约 20px。暖色主题不重叠，1.67.0 只修了暖色主题。建议另开一项修复。

## 7. 工作区里不属于本次的改动

本次审查开始时工作区只有 `.impeccable/critique/` 和这份 Spec。实施期间出现了下列内容，不是 Claude 改的，没有触碰：

- 已修改：`.gitignore`（+4 行）、`AGENTS.md`（+104 行，新增「AOCI 仓库认知」一节）；
- 未跟踪：`.aoci/`、`.gitattributes`、`aoci.txt`、`aoci.code.txt`、`aoci.meta.txt`、`AGENTS.md.backup.20261005_165806.*`。

看起来是同期安装了 AOCI。提交本批修复时只暂存以下 5 个文件：

- `src/homestay_bot/static/app.css`
- `src/homestay_bot/templates/admin/dashboard.html`
- `tests/browser/test_admin_theme.py`
- `docs/specs/2026-10-04_hallmark-frontend-polish-spec.md`
- `tasks/todo.md`

外加本报告。AOCI 相关文件由安装方单独决定。

## 8. 请 Codex 复审

1. H02 的内在换行在你那边的生产截图宽度（320、768px）下是否消除了孤立的「钟」字；`margin-left: auto` 让倒计时换行后右对齐，是否符合 DESIGN.md。
2. H03 在 `any-pointer: coarse` 下侧栏变高后，桌面触屏笔记本的侧栏密度是否可以接受。
3. H04 不显示「房号待补充」的取舍；如果你认为工作台需要数据质量提示，请回到 Spec 由用户决定。
4. 如获发布授权：
   - 补版本号、CHANGELOG 与 `docs/releases/`；
   - 部署门禁按项目规则二选一：本地全量，或 CI 通过；
   - 生产登录后补做 H01 工作台 320px 的页面验收。
