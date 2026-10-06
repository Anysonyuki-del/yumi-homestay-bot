# Hallmark R3 补修 · Codex → Claude 审查交接

日期：2026-10-06。状态：**H01 窄屏排列与经典主题指标重叠已完成本地修复和相关验证，待 Claude 独立复审**。未暂存、提交、推送、部署或升级版本；未调用真实 Hostex、DeepSeek、企业微信，未写生产数据。

## 1. 依据与本轮范围

- 用户明确选择：「开始修复 H01，并纳入经典主题指标重叠」；随后要求「交接报告」。
- 实施依据：[Spec R3 §6](../specs/2026-10-04_hallmark-frontend-polish-spec.md#6-codex-复核补修与授权r32026-10-06)。它补充窄屏排列要求，并将上一份报告中范围外的经典指标重叠纳入本轮。
- 上一份 [Claude 实施交接](2026-10-05_claude-to-codex-hallmark-polish-implementation-handoff.md) 的 H02–H05 方案保留；本报告补充其 H01 契约遗漏及 §6 指标问题，不改写历史观察。
- 当前分支 `main`，HEAD `894fd5b`（`fix(release): 修复 Bash 中文变量解析并补录 1.68.0 验收`）。这是源码基线，不是本轮上线证明。

**本轮运行代码只改 `src/homestay_bot/static/app.css`。** 测试改动在 `tests/browser/test_admin_theme.py`，实施与授权记录在 Spec §6、`tasks/todo.md` 首段。没有修改模板、业务脚本、路由、服务、数据结构、依赖或运行配置；工作区中的同文件旧差异不能全部归因于本轮。

## 2. 根因与最终实现

### 2.1 H01：≤520px 必须上下排列

**复核事实：** R2 在 `app.css` 中给 `.workbench-first > .section-heading` 和 `.operations-heading > .section-heading` 设置 `flex-wrap`，文字组使用 `flex: 1 1 16rem`，按钮不收缩且标签不换行。这能防止按钮文字被挤断，但两主题、两页在 480/520px 仍可同排，未满足 Spec §2.2 的 ≤520px 上下排列约束。

**修正位置：** `src/homestay_bot/static/app.css:535`，已有 `@media (max-width: 520px)`。

- 仅这两处局部标题组设置 `flex-direction: column`。
- 直接文字组设置 `flex: initial; width: 100%`，使用自然高度和可用宽度。
- 保留按钮单行与左对齐，>520px 继续按可用空间内在换行。

不能只加纵排：横排时的 `16rem` flex 基准会变成纵向高度，制造不必要的留白。对应中文注释解释了重置原因；其它 `.section-heading` 调用方未改。

### 2.2 经典主题指标：在公共组件修根因

**复核事实：** `src/homestay_bot/templates/admin/dashboard.html:31` 的「待我关注」指标同时有说明 `small` 和入口 `a`。公共 `.metric-item` 曾将两者放进同一 `meta` 格位，经典主题 320px 产生重叠；基线也有此问题。暖色主题已有独立 `action` 行。

**修正位置：** `src/homestay_bot/static/app.css:411` 的 `.metric-item`，以及 `@media (min-width: 768px)` 的同组件布局。

- 将暖色已采用的格位方案移到公共组件：手机为 `label/value`、`meta/value`、`action/value`；桌面为 `label`、`value`、`meta`、`action` 四行。
- `small` 使用 `meta`，`a` 使用 `action`；所有指标调用方共用该规则。
- 删除被公共规则替代的暖色三处重复声明，保留暖色数值字号、配色与链接提示。

没有删除说明、改计数、改 href、隐藏内容或给单个指标加特判。暖色布局应保持既有效果，经典主题获得同样的内容隔离。

## 3. 浏览器回归及判别力

沿用 `theme_site`、`_context`：真实同源临时 HTTP 服务加载本站 CSS/JS，后端使用临时 SQLite 与合成数据。浏览器阻断并记录写请求，相关用例断言 `writes == []`。这不是生产或真实供应商验收。

| 用例与位置 | 本轮改动 | 验收约束 |
| --- | --- | --- |
| `tests/browser/test_admin_theme.py:492`，`test_hallmark_heading_actions_keep_full_labels` | 扩展既有用例，加入 480、520、521px | 两主题、工作台和入住安排；≤520px 按钮在文字组下方并左对齐；继续检查标签单行、原 href、无覆盖与整页无横向溢出 |
| `tests/browser/test_admin_theme.py:533`，`test_dashboard_metric_content_does_not_overlap` | 新增两主题参数化回归 | 所有真实指标子元素均位于组件内、任意两块无重叠；明确保留首项说明和原关注页入口；整页无横向溢出 |

两项回归都测 320、375、414、480、520、521、768、1280px。断言读取实际文本 Range 和元素几何，采用 0.5px 亚像素容差，不逐句复述 CSS 源码。

**修复前红测：**

```bash
.venv/bin/python -m pytest -q tests/browser/test_admin_theme.py -k 'hallmark_heading or dashboard_metric_content'
```

结果：**3 failed、1 passed、21 deselected**。两主题 H01 在 480px 的纵排断言失败；经典指标在 320px 的重叠断言失败；暖色指标通过。证明新增约束能够捕获原缺陷。

**修复后综合相关验证：**

```bash
.venv/bin/python -m pytest -q tests/browser/test_admin_theme.py -k 'hallmark_heading or dashboard_metric_content or theme_pages_fit_scrollbar_width_and_show_one_control or switch_preserves_selection_draft_and_focus or warm_text_and_action_contrast_uses_actual_computed_surfaces'
.venv/bin/ruff check tests/browser/test_admin_theme.py
git diff --check
```

结果：**9 passed、16 deselected、1 个既有 Starlette TestClient 弃用警告**；Ruff、差异检查通过。覆盖两项修复，以及已有页面宽度、主题切换草稿/焦点保留、暖色文本/动作对比度约束。不要把这 9 项与上一份报告的 141 项或红测用例累加。

本地截图另复核了经典 320px、暖色 480px 的工作台：标题按钮上下排列并左对齐，指标说明与入口独立可读。截图使用合成日期和数据；其中降级提示来自临时夹具，不能用来判断生产健康。

## 4. 证据复用与未覆盖

本次编写报告没有改运行代码或测试，复用上一轮已通过的 9 项浏览器结果和 Ruff 结果，没有为写文档重复执行。报告编写时重新读取最终差异及 Spec，并执行差异检查；以下源码摘要用于后续判断证据是否仍有效：

| 文件 | SHA256 |
| --- | --- |
| `src/homestay_bot/static/app.css` | `13097515e1fdabd3547eb4eb4019d4f3684ba5c410487fed86120f7ac16c910a` |
| `tests/browser/test_admin_theme.py` | `28d4fc2b7b74d344f09734f3147021b874eab8779c767103e13510600e5aeac0` |

- 未跑全仓：影响可界定为局部 CSS，没有装配、依赖或构建变化；相关浏览器验证覆盖本轮行为。
- 未跑真实模型门禁或测试号收发：本轮未触及回复链或 `REPLY_PATHS`。
- 未进行登录后生产验收、真机触控、Mobile Safari、Android/Windows 系统字体、系统缩放或读屏验收。
- R2 关于工作台 320px 原按钮折行未本地复现的限制继续保留；本轮新增的失败证据是 **480px 未满足纵排契约**，不能改写成已复现原生产折行。
- 不修改 `admin.js::initOperationsCountdown` 的 `serverNow`、时区、时长格式、刷新或到点「待确认」语义；本轮不重新宣称这些业务行为完成验收。

## 5. 工作区归属与 AOCI

`git diff` 相对 HEAD 同时包含 Claude 的 R2 实施和 Codex 的 R3 补修，应按 §2/§3 判断本轮差异：

- `src/homestay_bot/static/app.css`：原 H01/H02/H03/H05 与本轮两项补修同处一个未提交文件。
- `src/homestay_bot/templates/admin/dashboard.html`：H04 房名去重来自上一轮 Claude；本轮保留。
- `tests/browser/test_admin_theme.py`：原夹具扩展和 H01–H04 用例来自上一轮；本轮仅加强 H01 并新增指标回归。
- Spec 与 `tasks/todo.md`：包含历史记录，本轮追加 R3 授权、实现与证据，保留历史内容。
- `.gitignore`、`AGENTS.md`、`.gitattributes`、AGENTS 备份及 `.impeccable/critique/` 是既有或同期改动，未在本轮补修中修改；来源不能只凭文件名推断。
- `.mcp.json` 在实施期间同期出现，本轮未创建或修改它；来源未另行核实。`docs/reviews/2026-10-06_aoci-index-claude-sqlite-handoff.md` 属同期交接，不属于本轮前端交付。

上一轮按项目规则更新了 `aoci.code.txt` 及 `.aoci/` 托管状态，复核并确认测试文件的 observe 指纹；4 项机器候选整批应用后，Verify/Check/Guide 证明 `aligned`、`complete=true`、`next_action=none`。其中 `.mcp.json` 仅登记已有配置的认知，没有改配置或连接真实服务。本报告新增后另按项目规则维护认知，以最新工具回执为准。AOCI 托管资产须与业务差异分别审查，不能据它已对齐推定页面或生产验收通过。

受保护的未跟踪项目总结未读取、摄入、修改或暂存。当前没有新的提交或发布授权；请勿套用历史报告的暂存名单进行整仓提交。

## 6. 请 Claude 复审

1. 独立核对两处标题组在 480/520px 的上下排列和左对齐，以及 521px 以上的既有换行规则；检查 `flex: initial` 是否避免纵排留白。
2. 检查 `.metric-item` 的公共格位是否完整保留经典/暖色指标内容、桌面三列分栏和暖色字号/链接效果，确认没有遗漏暖色重复规则或引入覆盖。
3. 复核新增几何断言的判别力及合成数据边界；按影响补必要检查，保留未验收项，不将本地结果写成生产通过。
4. 仅审查本轮补修，发现无关问题时单独列出；本报告不授权追加业务代码、提交、推送、部署或真实外部调用。

回退时仅撤销本轮 §2 的 CSS 差异及对应回归调整，保留 Claude 的 R2 实施和其它工作区内容；不得对整个文件使用 `git restore`。无数据迁移或数据回滚。
