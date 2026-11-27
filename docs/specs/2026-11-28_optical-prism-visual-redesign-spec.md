# YuMi 前端光学视觉重设计规格（暖纸光学 × 暖炭光学｜Warm Paper Optical × Warm Charcoal Optical）

- **日期**：2026-11-28
- **状态**：终版待实施确认（已消化 Claude 独立审查全部意见与用户全部裁决，零业务代码改动；待用户审阅确认并回复「开始」后方可进入编码）
- **基线提交**：`HEAD` = `origin/main` = `c12ecc1`（最新标签 `v1.77.1`，由 `5ae95f3` 修复 + `c12ecc1` 发布记录构成，未触及前端文件）
- **参考美学来源**：公开仓库 `RMvdesi/obsidian-prism`（GPL-3.0）公开 `README.md` 设计理念（光学切面、侧栏与工作区明度景深、硼硅质感指标容器、0fps 静态边缘余晖、阻尼弹簧微动效与系统减动效归零；底色与明度层级色值参考其公开配色表作为事实数据，不含其任何代码或素材）
- **用户已拍板方向与裁决（2026-11-28）**：
  1. **明暗模式**：浅色为主 + 纯 CSS `@media (prefers-color-scheme: dark)` 自动跟随系统暗色（零闪屏、不加手动切换器）。
  2. **唯一动作强调色**：暖琥珀 / 晶陶红光学色阶（浅色维持 `#c24322`，暗色切换暖琥珀高对比前景色与晶陶实心填充）。
  3. **覆盖范围**：Web 运营后台、认证页与微信小程序三端同步升级为统一光学美学体系；克制运营向，不引入像素吉祥物、视频壁纸等娱乐组件。
  4. **P1-1（Safari 15.4–16.1 降级口径）= A**：保留旧版 Safari 降级，改用 `@supports` / `@supports not` 实现真正生效的 `linear()` 与 `color-mix()` 回退，并重写单测验算真正起作用的回退分支。
  5. **P3-2（浅色主动作色）= A**：浅色主色保持 `#c24322`（免改小程序浅色 10 阶主色与 6 枚浅色 PNG，仅需新增 6 枚暗色 PNG）。
  6. **M2（按钮按压动效）= A**：删掉 `--ease-spring-snappy`，按钮按压保持 `--ease-out`；弹簧仅保留 2 条由标准二阶阻尼振子公式推导的曲线（`ui`、`panel`）。
  7. **既有工作区 `src/homestay_bot/static/app.css:889–891` 3 行未提交改动 = B**：知识库目录房号/条数防竖排换行修复随本次视觉重设计一起提交，并在 B5 与后续发布变更记录中单列出处。
  8. **D1 / D2**：D1 小程序浅色 TabBar 底色 `#ffffff`、暗色 `#100f0e`；D2 认证页保留 7%–10% 顶部静态微光晕（纯 CSS，0fps）。

---

## §0 硬边界与合规红线（实施全程不得突破）

| # | 硬边界 | 判定依据与验收口径 |
| :--- | :--- | :--- |
| B1 | **GPL-3.0 零代码拷贝合规** | 参考项目 `RMvdesi/obsidian-prism` 为 GPL-3.0 协议，本项目 `LICENSE` 为专有软件。底色与明度层级色值参考其公开 README 配色表（事实数据，非代码）；**全部 CSS、HTML、JS、测试代码与图标资产均在本仓库基于既有实现从零独立编写**，严禁复制、改写或移植其任何 CSS、JS、SVG、PNG 或算法代码；`DESIGN.md` 注明美学灵感来源。 |
| B2 | **零后端业务改动与零迁移** | 不修改 `src/homestay_bot/domain/`、`src/homestay_bot/repositories/`、`src/homestay_bot/services/`、`src/homestay_bot/routes/`、`src/homestay_bot/application.py` 与 `migrations/`；仅触及前端静态资产、两套布局模板、微信小程序视觉文件、设计文档与前端测试守卫。 |
| B3 | **不触碰 `REPLY_PATHS`** | 本次全部改动文件与 `scripts/release/reply_gate.sh::REPLY_PATHS` 交集必须为空，不影响客人 AI 客服回复链路。 |
| B4 | **零构建纯 SSR 与严格 CSP** | 不引入 Node/Vite/Tailwind 前端构建链，保持现有 `Dockerfile`、零构建 SSR 交付与 `?v={{ app_version }}` 缓存失效机制；不放宽 `middleware.py::EMPLOYEE_CSP`（零外链字体 CDN、零内联脚本、零 `on*` 事件属性）；`layouts/auth.html` 严格不加载 `admin.js`。 |
| B5 | **工作区既有资产与改动边界** | 严禁读取、摄入或提交受保护未跟踪文件 `YuMi民宿AI项目总结.txt`。`src/homestay_bot/static/app.css:889–891` 既有的 3 行用户未提交改动（`.kb-room-no, .kb-room-flag, .kb-count { flex: 0 0 auto; white-space: nowrap; }` 与 `.kb-room-name { min-width: 0; }`，知识库目录防竖排换行）按用户裁决 **B** 随本次重设计一并提交，并在发布变更记录中单列来源与出处；工作区其余既有 `.impeccable/` 与 `PRODUCT.md` 等未入库文件不混入无关业务提交。 |
| B6 | **无障碍、前景用量、性能与 Tauri 外壳边界** | ① 浅暗双态所有正文/按钮/徽章/链接满足 WCAG AA `>= 4.5:1`，图标/控件边界/焦点环/占比条分段上沿 `>= 3.0:1`，日历网格线与迷你轨未来态相对各自轨道 `>= 1.2:1`；② **硬规则：`--primary` / `--primary-hover` / `--primary-active` 只用作填充底色或描边，绝不作为文字或图标前景色**（仅 `caret-color` 与 `accent-color` 允许作为明确例外），单测守卫强制拦截 `color:` 引用 `--primary` / `--primary-hover`；③ 严禁 `transition: all` 与 `overflow-x: clip/hidden`；业务状态点与占比条严禁借用 `--primary`/`--focus`/`--link`；④ 不加常驻大面积 `backdrop-filter`（遵守 R-02 顶栏不透字结论）与常驻循环动画（空闲 `0fps`）；⑤ `clients/yumi/src-tauri/build.rs:19-20` 生产入口加载同一 Web 后台（`https://akros.icu/employee/admin`），本次不修改 Tauri 原生代码，但桌面/Android 外壳内 WebView 会自动跟随系统暗色，上线后须留意真机观感。 |

---

## §1 现状证据（基于当前仓库代码实查）

### 1.1 Web 后台与认证页现状
- **视觉基线**：`DESIGN.md`（v2.0）与 `src/homestay_bot/static/app.css`（`HEAD` 为 `920` 行；当前工作区含用户既有 `L889–891` 3 行 `.kb-room-*` 防折行改动，共 `923` 行）当前为单一浅色「暖调精工 SaaS（A+E 融合）」，`:root` 声明 `color-scheme: light`、`--page: #f5f2ed`、`--surface: #ffffff`、`--primary: #c24322`；桌面侧栏 `.admin-sidebar` 与主卡片 `.panel` 同为纯白 `#ffffff`，缺乏明度纵深。
- **双外壳模板**：`src/homestay_bot/templates/layouts/admin.html`（L11–12）与 `src/homestay_bot/templates/layouts/auth.html`（L7–8）目前写死 `<meta name="color-scheme" content="light">` 与单条 `<meta name="theme-color" content="#f5f2ed">`。
- **已核实的暗色化与兼容性隐患**：
  1. **同名自定义属性双写回退不生效（P1-1）**：`app.css:731–745` `.cal` 的 `--cal-*` 采用「先声明静态 hex、下一行声明 `color-mix()`」的双写方式。CSS 自定义属性在解析期不校验值语义，后一条声明恒覆盖前一条，导致不支持 `color-mix()` 的 Safari 15.4–16.1（项目 `admin.js:189` 注释明确兼容 Safari 15.4）在计算值阶段失效（IACVT），退为透明底与 `currentcolor` 边框，而非回退到上一行静态色。
  2. **隐式依赖 `--surface` 为纯白（R1）**：`app.css:564` `.toast-region { color: var(--surface); background: var(--toast-bg); }`、`app.css:138` `.skip-link { color: var(--surface); background: var(--ink-strong); }`、`app.css:168` `.button--danger:hover { color: var(--surface); background: var(--danger); }` 在暗色下会失真或融合。
  3. **暗色前景误用 `--primary` / `--primary-hover`（P1-2）**：`a:hover`（`app.css:108`）、`a.cal-m__hit:hover .cal-m__name`（`app.css:827`）使用 `--primary-hover`，`.side-nav a[aria-current="page"] .icon`（`app.css:416`）与 `::before`（`app.css:415`）使用 `--primary`；若暗色 `--primary: #b84018` / `--primary-hover: #c74a1f`，对比度仅 `2.97:1`–`3.62:1`，违反 B6。
  4. **暗色图片预览底问题（P2-3）**：`app.css:218` `.image-list img` 背景为 `var(--surface-sunk)`，用于房源详情（`properties/detail.html:49,77`）、知识库配图（`knowledge/detail.html:47`）与小程序横幅管理（`admin/miniprogram_home.html:31`）；暗色下 `#181614` 会导致深色线条的透明 PNG 不可见。
  5. **迷你轨未来填充低对比（P2-2）**：`app.css:813` `.cal-m__fill--future` 使用 `--cal-future-line` 填充 `--surface-well`，浅色对比度仅 `1.12:1`、暗色仅 `1.11:1`。
  6. **减动效漏掉 `::backdrop`（P2-6）**：`app.css:657–660` 仅覆盖 `*, *::before, *::after`，未含 `::backdrop`。

### 1.2 微信小程序现状
- `clients/wechat-miniprogram/app.json`：未配置 `"darkmode": true` 与 `"themeLocation": "theme.json"`，`window` 与 `tabBar` 均为浅色字面量（`#f5f2ed` / `#ffffff` / `#58524a` / `#c24322`）。
- `clients/wechat-miniprogram/app.wxss`（97 行）当前仅声明浅色 `--yumi-*` 与 27 个 `--td-*` 变量，无 `@media (prefers-color-scheme: dark)`。
- **TDesign 1.17.0 暗色机制核查（P1-3）**：`miniprogram_npm/tdesign-miniprogram/common/style/index.wxss` 未引入 `theme/_dark.wxss`，6 个实际使用组件（`t-button`、`t-swiper`、`t-icon`、`t-collapse`、`t-collapse-panel`、`t-skeleton` 及其子组件 `loading`、`swiper-nav`）共包含大量带浅色字面兜底的 `--td-*` 变量（如 `var(--td-bg-color-container, #fff)`），且全量继承 `_dark.wxss` 的基础语义基座变量；同时 `.gitignore` 排除了 `miniprogram_npm/` 且 CI 不执行 `npm install`，因此必须在 `app.wxss` 暗色块内显式覆写完整 TDesign 暗色基础语义基座。
- **WXML 硬编码图标色（实查共 16 处）**：`pages/home/index.wxml`（L37、L41、L79 共 3 处）、`pages/me/index.wxml`（L4、L24、L26、L32、L34、L37、L39 共 7 处）、`pages/me/about.wxml`（L12、L20、L25、L28 共 4 处）、`pages/services/index.wxml`（L16、L27 共 2 处）；`tdesign-miniprogram` 1.17.0 `icon.js::setIconStyle` 仅在显式传 `color` 属性时才写内联 `style`，省略 `color` 即由 `.t-icon` 自动继承父元素 `color`（P3-6）。
- **图标资产**：`clients/wechat-miniprogram/assets/tab/` 现有 6 枚 `81×81` 浅色 PNG（`#c24322` 与 `#58524a`），仓库无矢量源文件且无 Pillow 依赖（P3-5）。

### 1.3 现有自动化测试守卫现状
- `tests/unit/test_admin_assets.py`：包含 `#c24322` 硬编码检查（L50–61）、仅核对浅色 `:root` 日历网格线（L243–275）、依赖 `@media (max-width: 520px) {\n  .room-operation-card { padding` 结束锚点的 `_room_card_block`（L313–340）、按过时双写规则校验 `--cal-*` 的正则（L343–364）、全量 `var(--*)` 已声明校验（L408–419）与状态色不借用动作色守卫（L446–460）。
- `tests/browser/test_admin_theme.py`：硬编码首帧背景 `rgb(245, 242, 237)` 与单条 `meta[name="theme-color"] == "#f5f2ed"`（L81–128），覆盖 WCAG AA、Hallmark 几何与无 JS 降级；`tests/browser/test_admin_interactions.py::test_motion_feedback_and_reduced_motion` 验证动画名与减动效关闭。
- `tests/miniprogram/auth.test.js`、`content.test.js`：共 41 项 `node --test` 用例，尚无 `theme.json`、双态图标或 TDesign 暗色基座覆写测试。

---

## §2 双态光学设计系统与令牌规格

### 2.1 色彩与光学表面令牌（Web `:root` 与 `@media (prefers-color-scheme: dark)`）

所有色值均经 WCAG 2.1 相对亮度公式逐一实算通过：

| 令牌 | 浅色：暖纸光学（`:root`） | 暗色：暖炭光学（`prefers-color-scheme: dark`） | 实算对比度 / 用途说明 |
| :--- | :--- | :--- | :--- |
| `color-scheme` | `light dark` | *(不重复声明)* | 仅 `:root` 声明一次，通知原生控件与 `<dialog>` 跟随系统双态 |
| `--sidebar-bg` | `#ebe6dc` | `#100f0e` | 桌面/抽屉侧栏底色，相对主画布压暗建立光学景深 |
| `--page` | `#f6f4ee` | `#151413` | 主工作区画布与 `theme-color` |
| `--surface` | `#ffffff` | `#1d1b19` | 主面板、浮卡、日历轨道、确认弹窗表面 |
| `--surface-sunk` | `#f2eee6` | `#181614` | 表头、内嵌卡片、日历日期标尺、事件块 |
| `--surface-well` | `#e8e2d7` | `#272420` | 控制台凹槽（页签底槽、占比管槽、徽章中性底、房号键帽） |
| `--media-well` | `#ece8e1` | `#ece8e1` | **双态固定浅中性底**：专供 `.image-list img`（欢迎图、横幅、配图预览），防止暗底隐去深色透明 PNG |
| `--ink-strong` | `#1c1916` | `#f5f1ea` | 标题、KPI 大数、强提示 |
| `--text` | `#282420` | `#e4ded4` | 正文 |
| `--ink-mid` | `#413b35` | `#ccc4b8` | 次标题、表单标签、次级按钮字 |
| `--ink-soft` | `#575048` | `#afa69a` | 侧栏导航文字、辅助信息 |
| `--muted` | `#5e574f` | `#9f968a` | 说明与时间戳（浅色侧栏 `5.72:1` / 暗色卡片 `5.89:1`） |
| `--line` | `#dad2c5` | `#36312c` | 结构细线与日历网格线（浅 `1.50:1`、暗 `1.33:1`，均 `>= 1.2:1`） |
| `--line-strong` | `#c2b7a6` | `#4a443c` | 卡片加强边、表头底线、日历外框 |
| `--control-border` | `#766e64` | `#7e756a` | 可编辑控件边框（浅 `5.02:1`、暗 `3.79:1`，均 `>= 3.0:1`） |
| `--line-soft` | `#968d82` | `#665e55` | 悬停外框、面包屑分隔符 |
| `--disabled-bg` / `--disabled-ink` / `--disabled-border` | `#ece7df` / `#6b645b` / `#d5cdc0` | `#22201d` / `#857d73` / `#332f2a` | 禁用态彻底去色、零高光 |
| `--primary` | `#c24322` | `#b84018` | **仅作填充底色或描边（禁作文字/图标前景色）**：配 `--on-primary: #ffffff`，浅 `5.10:1`（对 `--page` `4.63:1`）、暗 `5.55:1`（对 `--surface` `3.09:1`） |
| `--primary-hover` | `#a73619` | `#c74a1f` | 按钮悬停填充（浅 `6.59:1`、暗 `4.74:1`），禁作前景色 |
| `--primary-active` | `#8f2d14` | `#9e3512` | 按钮按下压沉填充（浅 `8.22:1`、暗 `7.07:1`） |
| `--primary-soft` / `--primary-border` | `#fbede8` / `#eabeb0` | `#2c1b14` / `#663924` | 激活导航/页签、今日标尺柔底与边框 |
| `--on-primary` / `--on-primary-hover` | `#ffffff` / `#ffffff` | `#ffffff` / `#ffffff` | 主按钮前景色 |
| `--link` | `#b53a1b` | `#f09a5e` | 链接、侧栏当前项文字与图标、日历未来字、今日底标（浅 `5.86:1` / 柔底 `5.13:1`，暗 `7.76:1` / 柔底 `7.45:1`） |
| `--link-hover` | `#962f15` | `#f7b887` | 链接与可点行悬停前景色（浅 `7.74:1` / 柔底 `6.77:1`，暗 `9.94:1` / 柔底 `9.55:1`） |
| `--focus` | `#c24322` | `#f09a5e` | 键盘焦点外环专用色（浅 `5.10:1`、暗 `7.76:1`） |
| `--focus-ring` | `rgb(194 67 34 / 18%)` | `rgb(240 154 94 / 24%)` | 输入框聚焦柔和外光环 |
| `--success` / `-soft` / `-border` | `#0f5c3a` / `#eaf8f0` / `#b0e3c6` | `#63d297` / `#13271e` / `#24543d` | 成功/可入住（浅 `7.34:1`、暗 `8.38:1`） |
| `--warning` / `-soft` / `-border` | `#874c05` / `#fdf6e3` / `#e8ca82` | `#f0b85c` / `#292012` / `#5f461d` | 警示/待检/待审（浅 `6.35:1`、暗 `8.95:1`） |
| `--danger` / `-soft` / `-border` / `--on-danger` | `#9f1d1d` / `#fdf0f0` / `#efb8b8` / `#ffffff` | `#f28b82` / `#2d1616` / `#662c2c` / `#1d1b19` | 危险/逾期/维修（浅 `7.08:1`、暗常规 `7.10:1`、暗 hover `7.19:1`） |
| `--info` / `-soft` / `-border` | `#1b4b6e` / `#edf4fa` / `#bad3e8` | `#7eb9e8` / `#15222e` / `#294966` | 信息/已分派（浅 `8.31:1`、暗 `7.69:1`） |
| `--scope-global-{bg,fg,bd}` | `#e6f3ee` / `#1a5a44` / `#b6dbce` | `#152822` / `#6cd0a8` / `#295647` | 知识全店通用（浅 `7.11:1`、暗 `8.26:1`） |
| `--scope-room-{bg,fg,bd}` | `#eaeef8` / `#2d3e78` / `#c3cde8` | `#1b2033` / `#9cb0f5` / `#33416b` | 知识指定房间（浅 `8.73:1`、暗 `7.64:1`） |
| `--scope-public-{bg,fg,bd}` | `#eeeae3` / `#524c44` / `#d5cdc1` | `#25221e` / `#c4bbb0` / `#474139` | 知识店外周边（浅 `7.07:1`、暗 `8.36:1`） |
| `--toast-bg` / `--toast-ink` | `#1c1916` / `#ffffff` | `#282420` / `#f5f1ea` | 轻提示专用色对（暗色 `13.68:1`），不再复用 `--surface` |
| `--on-ink-strong` | `#ffffff` | `#151413` | `.skip-link` 专用反相文字色，消除对 `--surface` 的隐式依赖 |
| `--selection-bg` / `--tap-highlight` / `--backdrop` | `#fbede8` / `rgb(194 67 34 / 14%)` / `rgb(28 25 22 / 46%)` | `rgb(240 154 94 / 24%)` / `rgb(240 154 94 / 18%)` / `rgb(8 7 6 / 68%)` | 浏览器系统选区、触控高亮与弹窗遮罩 |

### 2.2 光学材质、高光、阴影与阻尼弹簧动效令牌

| 令牌 | 浅色值 | 暗色值 | 用途 |
| :--- | :--- | :--- | :--- |
| `--facet-highlight` | `inset 0 1px 0 rgb(255 255 255 / 92%)` | `inset 0 1px 0 rgb(255 248 240 / 8%)` | 面板、卡片、弹窗、激活键帽的 1px 光学顶部镜面高光 |
| `--primary-highlight` | `inset 0 1px 0 rgb(255 242 232 / 34%)` | `inset 0 1px 0 rgb(255 232 214 / 26%)` | 主动作按钮的晶体顶边折射光泽 |
| `--well-inset` | `inset 0 1px 2px rgb(28 25 22 / 6%)` | `inset 0 1px 3px rgb(0 0 0 / 38%)` | 控制台内凹槽（页签槽、筛选条、占比管槽）物理凹陷感 |
| `--vial-sheen` | `linear-gradient(180deg, rgb(255 255 255 / 16%) 0%, rgb(255 255 255 / 0%) 58%)` | `linear-gradient(180deg, rgb(255 255 255 / 16%) 0%, rgb(255 255 255 / 0%) 58%)` | 硼硅玻璃准备占比条顶光（`16%` 覆盖 `--success` 后对槽底 `#e8e2d7` 实算 `4.19:1 >= 3:1`；暗色 `9.08:1`） |
| `--auth-glow` | `radial-gradient(ellipse 68% 48% at 50% 0%, rgb(194 67 34 / 7%), transparent 72%)` | `radial-gradient(ellipse 68% 48% at 50% 0%, rgb(240 154 94 / 10%), transparent 72%)` | 认证页顶部极克制的静态环境光晕（纯 CSS，0fps） |
| `--shadow-raised` | `0 1px 2px rgb(28 25 22 / 5%), var(--facet-highlight)` | `0 1px 2px rgb(0 0 0 / 36%), var(--facet-highlight)` | 次级键帽、激活页签、KPI 卡片 |
| `--shadow-facet` | `0 2px 6px rgb(28 25 22 / 4%), var(--facet-highlight)` | `0 2px 8px rgb(0 0 0 / 32%), var(--facet-highlight)` | 面板、房间卡的光学切面质感 |
| `--shadow-card` | `0 14px 32px rgb(28 25 22 / 10%), var(--facet-highlight)` | `0 18px 40px rgb(0 0 0 / 56%), var(--facet-highlight)` | 认证居中浮卡 |
| `--shadow-overlay` | `0 16px 34px rgb(28 25 22 / 18%), 0 2px 8px rgb(28 25 22 / 8%), var(--facet-highlight)` | `0 20px 44px rgb(0 0 0 / 68%), 0 2px 10px rgb(0 0 0 / 42%), var(--facet-highlight)` | 抽屉、`<dialog>` 弹窗、Toast |
| `--glow-danger` | `0 4px 16px rgb(159 29 29 / 8%), var(--facet-highlight)` | `0 4px 18px rgb(242 139 130 / 12%), var(--facet-highlight)` | 风险 KPI 卡静态外发光余晖（去掉圆角 `inset 3px` 月牙竖条，M5） |
| `--glow-warning` | `0 4px 14px rgb(135 76 5 / 7%), var(--facet-highlight)` | `0 4px 16px rgb(240 184 92 / 10%), var(--facet-highlight)` | 待审核告警横幅静态外发光余晖（M5） |

**动效体系：2 条物理公式推导阻尼弹簧曲线 + 系统减动效 `off`（删掉 `snappy`，按压维持 `--ease-out`）**
- **时长与基础缓动**：
  - `--motion-fast: 180ms;`（颜色、边框、阴影、按钮按压、折叠箭头）
  - `--motion-ui: 220ms;`（卡片微抬、Toast 入场）
  - `--motion-panel: 260ms;`（移动抽屉、`<dialog>` 弹窗入场）
  - `--ease-out: cubic-bezier(.22, 1, .36, 1);`
  - `--ease-in: cubic-bezier(.4, 0, 1, 1);`（一切出场、关闭、遮罩淡出）
- **渐进增强降级写法（P1-1）**：`:root` 仅声明兼容旧浏览器的 `cubic-bezier(.22, 1, .36, 1)`：
  ```css
  :root {
    --ease-spring-ui: cubic-bezier(.22, 1, .36, 1);
    --ease-spring-panel: cubic-bezier(.22, 1, .36, 1);
  }
  @supports (transition-timing-function: linear(0, 1)) {
    :root {
      --ease-spring-ui: linear(0, 0.063, 0.204, 0.371, 0.532, 0.672, 0.785, 0.87, 0.93, 0.971, 0.996, 1.009, 1.016, 1.017, 1.016, 1.014, 1.011, 1.008, 1.006, 1);
      --ease-spring-panel: linear(0, 0.051, 0.166, 0.301, 0.436, 0.557, 0.661, 0.745, 0.812, 0.864, 0.903, 0.932, 0.954, 0.969, 0.979, 0.987, 0.992, 0.995, 0.997, 1);
    }
  }
  ```
- **物理公式与单测复算口径（M4）**：两条曲线均按标准二阶欠阻尼振子单位阶跃响应公式在 $t_i = i / 19$（$i = 0 \dots 19$，首尾分别强制钳制为 `0` / `1`，保留 3 位小数）等距采样生成：
  $$y(t) = 1 - e^{-\zeta \omega_n t}\left(\cos(\omega_d t) + \frac{\zeta}{\sqrt{1 - \zeta^2}}\sin(\omega_d t)\right), \quad \omega_d = \omega_n\sqrt{1 - \zeta^2}$$
  - `--ease-spring-ui`：$\zeta = 0.79,\ \omega_n = 7.5$（微 1.70% 过冲，配合 `--motion-ui: 220ms`）；
  - `--ease-spring-panel`：$\zeta = 0.93,\ \omega_n = 6.8$（0.00% 无明显过冲、平滑收尾，配合 `--motion-panel: 260ms`）。

### 2.3 双态日历 `--cal-*` 精确派生表与 `@supports not` 降级机制

| 令牌 | 派生公式（主 `.cal` 块） | 浅色静态回退（`#c24322`） | 暗色静态回退（`#b84018`） |
| :--- | :--- | :--- | :--- |
| `--cal-future-bg` | `var(--primary-soft)` | `#fbede8` | `#2c1b14` |
| `--cal-future-line` | `color-mix(in srgb, var(--primary) 25%, var(--surface))` | `#f0d0c8` | `#442419` |
| `--cal-future-ink` | `var(--link)`（替代原 `--primary-hover`） | `#b53a1b`（`5.13:1`） | `#f09a5e`（`7.45:1`） |
| `--cal-current-bg` | `color-mix(in srgb, var(--primary) 15%, var(--surface))` | `#f6e3de` | `#342119` |
| `--cal-current-line` | `color-mix(in srgb, var(--primary) 42%, var(--surface))` | `#e5b0a2` | `#5e2b19` |
| `--cal-current-ink` | `color-mix(in srgb, var(--primary) 50%, var(--ink-strong))` | `#6f2e1c`（`8.14:1`） | `#d69881`（`6.29:1`） |
| `--cal-overlap-stripe` | `color-mix(in srgb, var(--warning) 45%, var(--surface))` | `#c9ae8e` | `#7c6237` |
| `--cal-warn-line` | `color-mix(in srgb, var(--warning) 30%, var(--surface))` | `#dbc9b4` | `#5c4a2d` |

**CSS 结构硬约束（P1-1 + 锚点位置）**：
1. 主 `.cal` 块**只声明 `var()` 与 `color-mix()`**，绝不写静态字面 hex 声明；
2. 紧接 `.cal` 之后、且**必须置于 `_room_card_block` 结束锚点 `@media (max-width: 520px) {\n  .room-operation-card { padding` 之前**，编写 `@supports not (color: color-mix(in srgb, red 50%, blue))` 回退块：
   ```css
   @supports not (color: color-mix(in srgb, red 50%, blue)) {
     .cal {
       --cal-future-line: #f0d0c8;
       --cal-current-bg: #f6e3de;
       --cal-current-line: #e5b0a2;
       --cal-current-ink: #6f2e1c;
       --cal-overlap-stripe: #c9ae8e;
       --cal-warn-line: #dbc9b4;
     }
     @media (prefers-color-scheme: dark) {
       .cal {
         --cal-future-line: #442419;
         --cal-current-bg: #342119;
         --cal-current-line: #5e2b19;
         --cal-current-ink: #d69881;
         --cal-overlap-stripe: #7c6237;
         --cal-warn-line: #5c4a2d;
       }
     }
   }
   ```

---

## §3 功能规格细则（F1–F5）

### F1 · 光学切面外壳与双态 `<meta>`
- `templates/layouts/admin.html` 与 `templates/layouts/auth.html`：
  - 将 `<meta name="color-scheme" content="light">` 更新为 `<meta name="color-scheme" content="light dark">`；
  - 将单条 `<meta name="theme-color" content="#f5f2ed">` 替换为成对声明：
    ```html
    <meta name="theme-color" media="(prefers-color-scheme: light)" content="#f6f4ee">
    <meta name="theme-color" media="(prefers-color-scheme: dark)" content="#151413">
    ```
  - 不新增任何内联 `<script>`、`style` 属性或主题切换控件；`auth.html` 继续零加载 `admin.js`。
- `.admin-sidebar`：背景改用 `var(--sidebar-bg)`，右边界使用 `1px solid var(--line)`；激活导航 `.side-nav a[aria-current="page"]` 使用 `background: var(--surface)`（暗色切换 `var(--primary-soft)`）+ `box-shadow: inset 0 0 0 1px var(--primary-border), var(--shadow-raised)` + 左侧 `3px` 指示条与 `.icon` 均改用 `var(--link)`（遵守 B6 前景规则）。
- 全局链接与悬停：`a:hover` 与 `a.cal-m__hit:hover .cal-m__name` 均改为 `color: var(--link-hover)`。
- `.topbar`：保持实色 `background: var(--surface)`（严守既有 R-02 顶栏不透字回归测试，禁用半透明 `backdrop-filter`），底部 `1px solid var(--line)` + `var(--facet-highlight)`。
- `.panel`、`.room-operation-card`、`.card`、`.confirm-dialog`：统一使用 `var(--shadow-facet)` / `var(--shadow-card)` / `var(--shadow-overlay)`。
- `.image-list img`：背景改为 `var(--media-well)`（双态固定 `#ece8e1`，确保透明深色 PNG 在暗色下可见）。
- `body:not(.admin-body)`（认证页）：声明实色 `background-color: var(--page); background-image: var(--auth-glow); background-repeat: no-repeat;`。

### F2 · 控制台内凹槽与精工触感控件
- `.tab-nav`、`.filter-toolbar`、`.selection-actions`、`.kb-room-directory`：使用内凹槽（`var(--surface-well)` 或 `var(--surface-sunk)` + `var(--well-inset)`）；`.tab-nav a[aria-current="page"]` 在槽内浮起成实体键帽（`var(--surface)` / 暗色 `var(--primary-soft)` + `var(--primary-border)` + `var(--shadow-raised)` + `color: var(--link)`）。
- 按钮与焦点：主按钮叠加 `var(--primary-highlight)`；次级按钮保留 `var(--shadow-raised)`；`.button--danger:hover` 改用 `color: var(--on-danger)`；禁用态明确 `box-shadow: none`；控件 `:focus` 同时设 `border-color: var(--focus)` 与 `box-shadow: 0 0 0 3px var(--focus-ring)`，保留 `:focus-visible` `outline: 2.5px solid var(--focus)`。
- `.toast-region` 改为 `color: var(--toast-ink); background: var(--toast-bg); border: 1px solid var(--line-strong);`；`.skip-link` 改为 `color: var(--on-ink-strong)`。

### F3 · 光学语义遥测件与 0fps 静态边缘余晖
- **硼硅玻璃准备占比条（`.readiness-bar`）**：
  - 槽体增高至 `12px`，`padding: 2px`，`border: 1px solid var(--line)`，`background: var(--surface-well)`，`box-shadow: var(--well-inset)`，配 `::after` 伪元素叠加 `var(--vial-sheen)`（`16%` 白光梯度，`pointer-events: none`）；
  - `.readiness-bar__seg--*` 分段严格保留既有语义色（`--success` / `--muted` / `--warning` / `--danger`），不借用 `--primary`/`--focus`/`--link`。
- **0fps 静态边缘余晖**：
  - `.metric-item--risk` 应用 `box-shadow: var(--glow-danger)`（无内嵌月牙竖条）；
  - `.kb-banner` 应用 `box-shadow: var(--glow-warning)`（无内嵌月牙竖条）；
  - `.workbench-first` 保留柔晶陶描边并叠加 `var(--shadow-facet)`；
  - `.first-list__item--urgent`（无圆角矩形）继续支持左侧 `inset 3px 0 0 var(--danger)` 与淡红底色。
- **7 日时间轴与手机迷你轨**：
  - `.cal` 严格采用 §2.3 的主块 `color-mix()` + 锚点前 `@supports not` 双态静态回退；
  - `.cal__date--today` 底部标线继续用 `var(--link)`，不借用 `--focus`；
  - 手机迷你轨 `.cal-m__fill--future` 改用 `background: var(--cal-current-line); box-shadow: inset 0 0 0 1px var(--line-strong);`（纯色相对 `--surface-well` 浅 `1.47:1`、暗 `1.35:1`，均满足 `>= 1.2:1`）。

### F4 · 阻尼弹簧微动效与无障碍护栏
- **弹簧作用域收紧（P2-5 / M2 / M3）**：
  - `--ease-spring-ui`（`220ms`）仅用于组件**入场**或显式位移动效：Toast 入场动画、`@media (hover: hover)` 卡片微抬（`transform`）；
  - `--ease-spring-panel`（`260ms`）仅用于移动侧栏抽屉滑入（`transform`）与 `<dialog>` 确认弹窗打开（`@keyframes dialog-enter`，仅 `opacity` + `translateY(6px) scale(.985)`）；
  - 按钮按压（`:active` `scale(.985)`）、颜色/背景/边框/阴影过渡以及 `<details>` 折叠箭头旋转均使用 `--ease-out`（`--motion-fast: 180ms`）；
  - 一切出场/收起/遮罩淡出保持 `--ease-in`（不做弹簧过冲）。
- **无障碍与性能护栏**：零 `transition: all`；零常驻循环动画（仅保留 `button.is-submitting::before` 提交旋转）；`@media (prefers-reduced-motion: reduce)` 选择器扩展至 `*, *::before, *::after, ::backdrop`，关闭一切过渡与动画并重置 `scroll-behavior: auto`。

### F5 · 微信小程序三端同步光学升级
- `clients/wechat-miniprogram/app.json`：开启 `"darkmode": true` 与 `"themeLocation": "theme.json"`；`window` 与 `tabBar` 颜色及 6 处图标路径全部引用 `theme.json` 变量（`@navBgColor` 等）。
- 新增 `clients/wechat-miniprogram/theme.json`：
  - `light`：导航与页面 `#f6f4ee`、`black`/`dark`，TabBar 底 `#ffffff`、`borderStyle: "black"`，未选 `#575048`，选中 `#c24322`（裁决 P3-2=A），指向既有 6 枚浅色 PNG；
  - `dark`：导航与页面 `#151413`、`white`/`light`，TabBar 底 `#100f0e`、`borderStyle: "white"`，未选 `#afa69a`，选中 `#f09a5e`，指向 6 枚暗色 PNG（`*-dark.png`）。
- 新增 6 枚暗色 `81×81` 标签栏 PNG（`clients/wechat-miniprogram/assets/tab/*-dark.png`）：通过临时一次性脚本读取现有 6 枚浅色 PNG 的 Alpha 通道分别按 `#afa69a` 与 `#f09a5e` 重新填色生成（不增项目依赖、脚本不入库；浅色 6 枚保留不动）。
- `clients/wechat-miniprogram/app.wxss` 与 6 个页面 `.wxss`：
  - 浅色主动作维持 `#c24322` 并微调表面底色与高亮/凹槽变量（新增 `--yumi-facet-highlight`、`--yumi-well-inset`、`--yumi-shadow-card`）；
  - `@media (prefers-color-scheme: dark) { page { ... } }` 完整覆写全部 `--yumi-*` 与 **TDesign 1.17.0 暗色基础语义基座（与 `tdesign-miniprogram/common/style/theme/_dark.wxss` 同源集合，映射为暖炭光学色阶）**：
    - `--td-bg-color-page` / `-container` / `-container-active` / `-secondarycontainer` / `-secondarycontainer-active` / `-component` / `-component-active` / `-component-disabled` / `-secondarycomponent` / `-secondarycomponent-active` / `-specialcomponent`
    - `--td-text-color-primary` / `-secondary` / `-placeholder` / `-disabled` / `-anti` / `-brand` / `-link`
    - `--td-gray-color-1` ~ `--td-gray-color-14`、`--td-font-white-1` ~ `-4`、`--td-font-gray-1` ~ `-4`
    - `--td-component-stroke` / `--td-component-border` / `--td-border-level-1-color` / `--td-border-level-2-color` / `--td-mask-active` / `--td-mask-disabled`
    - `--td-brand-color` / `-active` / `-disabled` / `-focus` / `-light` / `-light-active` 及 `--td-primary-color-*`、`--td-error-color*`、`--td-warning-color*`、`--td-success-color*`
    - 实际在用组件特化令牌（`--td-collapse-*`、`--td-skeleton-*`、`--td-button-*`、`--td-swiper-nav-*`、`--td-loading-*`）。
- **清理 16 处 WXML 硬编码图标色（P3-6）**：删除 4 个页面 WXML（`pages/home/index.wxml`、`pages/me/index.wxml`、`pages/me/about.wxml`、`pages/services/index.wxml`）中全部 16 处 `<t-icon ... color="#c24322|#58524a" />` 属性，改由其外层语义容器或图标包装类声明 `color: var(--yumi-primary)` / `color: var(--yumi-muted)`，通过 `.t-icon` 继承 `currentColor`（最终在微信开发者工具暗色切换中实机确认渲染）。

---

## §4 风险矩阵与已拍板决策

### 4.1 风险矩阵与缓解措施

| 编号 | 风险 / 已实查陷阱 | 影响等级 | 缓解措施（硬性要求） |
| :--- | :--- | :--- | :--- |
| R1 | **暗色隐式依赖 `--surface` 为白导致文字融底**（`.toast-region`、`.skip-link`、`.button--danger:hover`） | **高（P1）** | 拆出 `--toast-ink`、`--on-ink-strong`、`--on-danger`；浏览器暗色计算样式对比度必检。 |
| R2 | **CSS 同名双写回退失效（P1-1）与暗色日历网格/文字守卫** | **高（P1）** | `.cal` 主块仅写 `color-mix()`，老 Safari 回退放于锚点前的 `@supports not` 块并嵌套暗色覆盖；单测改审 `@supports not` 块 + 迷你轨。 |
| R3 | **暗色前景误用 `--primary` / `--primary-hover`（P1-2）** | **高（P1）** | `a:hover` 等改用 `--link-hover`；`.side-nav … .icon` 改用 `--link`；单测禁用 `color:` 引用 `--primary` / `--primary-hover`。 |
| R4 | **微信小程序 TDesign 1.17.0 未引入 `_dark.wxss` 导致白块（P1-3）** | **高（P1）** | `app.wxss` 暗色块显式覆写完整 TDesign 暗色基座；两层 Node 测试（必跑完整基座清单 + 本机 `miniprogram_npm` 6 组件源码扫描）；真机/开发者工具复核。 |
| R5 | **顶栏半透明或圆角内嵌竖条造成视觉或 R-02 回归（R3/P2-1/P2-3/M5）** | **中（P2）** | 顶栏实色不加 `backdrop-filter`；`--vial-sheen` 降为 `16%`；图片预览固定 `--media-well`；余晖去掉圆角 `inset 3px` 竖条。 |
| R6 | **老浏览器不支持 `linear()` 导致整个过渡消失（P1-1）** | **中（P2）** | `:root` 默认只写 `cubic-bezier(.22, 1, .36, 1)`，包裹在 `@supports (transition-timing-function: linear(0, 1))` 内升级为 20 点公式曲线。 |
| R7 | **微信小程序与 Tauri 原生客户端不随 API 容器部署即时生效** | **低（发布口径）** | Web 随 API 容器部署上线；小程序须用户在微信开发者工具「构建 npm」并上传审核；Tauri 外壳不改原生代码但桌面/Android WebView 自动跟随系统暗色。 |

### 4.2 全部已拍板决策总表（无未决项）

| 决策点 | 最终拍板 | 依据与影响摘要 |
| :--- | :--- | :--- |
| **D1 · 小程序浅色 TabBar 底色** | `#ffffff`（暗色 `#100f0e`） | 保持住客端极简轻盈与卡片一致，暗色对齐 Web 侧栏。 |
| **D2 · 认证页静态环境光晕** | 保留 `7%–10%` 顶部静态微光晕（0fps） | 首帧建立光学层次，纯 CSS 不影响可读性与零 JS 边界。 |
| **P1-1 · Safari 15.4–16.1 降级** | **选 A（保留降级，用 `@supports` 修复）** | 修复原本就无效的双写回退，对齐 `admin.js:189` Safari 15.4 基线。 |
| **P3-2 · 浅色主动作色** | **选 A（保持 `#c24322`）** | 仅浅微调 `--primary-hover`/`--link` 为高亮阶，免改 10 阶小程序色与 6 枚浅色 PNG。 |
| **M2 · 按钮按压动效** | **选 A（删掉 `snappy`，保持 `--ease-out`）** | 减少非必要过冲与令牌面，阻尼弹簧保留 `ui` 与 `panel` 2 条公式曲线。 |
| **工作区 `app.css:889–891` 3 行改动** | **选 B（随本次重设计一起提交）** | 实施与发版时在改动清单和发布变更记录中单列出处（知识库目录防竖排换行）。 |

---

## §5 精确改动文件清单与实施顺序

*(实施前流程前置：读取 `.agents/skills/impeccable/reference/craft-floor.md`，更新 `.impeccable/surfaces/src-homestay-bot-templates.md` Direction contract 记入 seed key `7fbcdaf2` 并执行 telemetry ping。)*

| 阶段 | 文件 | 改动摘要 |
| :--- | :--- | :--- |
| Step 1 | `src/homestay_bot/templates/layouts/admin.html`、`src/homestay_bot/templates/layouts/auth.html` | 更新 `meta[name="color-scheme"]` 为 `light dark`，拆分浅/暗成对 `meta[name="theme-color"]`。 |
| Step 2 | `src/homestay_bot/static/app.css` | ① 保留用户既有的 `L889–891` 3 行 `.kb-room-*` 修复；② `:root` 保持 `#c24322`、升级光学表面/凹槽/`--media-well`/`--link-hover`/`--toast-ink`/`--on-danger`/`--on-ink-strong` 与 2 条 `cubic-bezier` 基础弹簧 + `@supports` 20 点物理公式 `linear()`；③ 新增 `@media (prefers-color-scheme: dark)` 暖炭光学全套令牌；④ 侧栏景深、`a:hover`/`.side-nav … .icon` 改走 `--link-hover`/`--link`、硼硅 16% 光泽占比条、无内嵌月牙的 0fps 余晖、认证页静光晕；⑤ `.cal` 主块仅留 `color-mix()` 并在 `_room_card_block` 结束锚点前补全双态 `@supports not` 回退，增强 `.cal-m__fill--future` 并使 `.cal__date--today` 底标用 `--link`；⑥ 减动效规则补上 `::backdrop`。 |
| Step 3 | `clients/wechat-miniprogram/app.json`、`theme.json`（新增）、`app.wxss`、`pages/*/*.wxss`（6 个）、`pages/{home,me,services}/*.wxml`（4 个，16 处 `t-icon`）、`assets/tab/*-dark.png`（新增 6 枚） | 开启 `darkmode` + `theme.json`，保留 6 枚浅色 PNG 并基于其 alpha 通道生成 6 枚暗色 PNG；`app.wxss` 暗色块覆写完整 TDesign 暗色基座 + 6 页面光学质感样式；移除 16 处 WXML 硬编码 `color`，改用父容器 CSS 继承。 |
| Step 4 | `tests/unit/test_admin_assets.py`、`tests/browser/test_admin_theme.py`、`tests/miniprogram/content.test.js` | ① `test_admin_assets.py`：双态对比度（含 `--link-hover`/`--toast-ink`/`--on-danger`/`--on-ink-strong`）、`@supports not` 块按浅/暗 `_srgb_mix` 验算 + `.cal` 主块无字面色 + 位置在锚点前、双态网格线与迷你轨对比度、`color:` 禁引 `--primary`/`--primary-hover`、物理公式复算 20 点 `linear()`；② `test_admin_theme.py`：Playwright `color_scheme="light"|"dark"` 双态覆盖首帧、无 JS、Toast/图片预览 `--media-well`/占比条上沿 16% 光泽计算对比度、暗色+减动效 `.confirm-dialog[open]` `animationName=="none"`；③ `content.test.js`：`theme.json`、12 枚 81×81 PNG 像素色值、WXML 零硬编码色与两层 TDesign 暗色基座守卫（必跑语义基座清单 + 本机存在 `miniprogram_npm` 时扫 6 组件 `.wxss`）。 |
| Step 5 | `DESIGN.md` | 更新为双态光学设计规范，注明浅/暗对比度、物理弹簧公式、`--primary` 前景禁令与公开配色表灵感来源。 |

---

## §6 测试与验收方案（风险驱动）

1. **静态与结构守卫**：
   - `.venv/bin/ruff check .`、`node --check src/homestay_bot/static/admin.js`、`git diff --check`；
   - `.venv/bin/pytest -q tests/unit/test_admin_assets.py`（双态 WCAG、`@supports not` 日历公式、迷你轨、公式复算 `linear()`、零未声明变量、`color:` 禁引 `--primary*`、状态色不借动作色）。
2. **真实浏览器双态验收（浅色 + 暗色，1440px + 390px + 320px）**：
   - `.venv/bin/pytest -q tests/browser/test_admin_theme.py tests/browser/test_admin_interactions.py tests/browser/test_knowledge_workbench.py`；
   - 分别输出浅色与暗色桌面（1440px）与手机（390px）代表页面截图供人工视觉复核。
3. **微信小程序离线测试**：
   - `node --test tests/miniprogram/*.test.js`（既有 41 项 + 新增双态与 TDesign 暗色守卫）。
4. **判别力检查（实施阶段自证）**：
   - 临时将暗色 `a:hover` 改用 `var(--primary-hover)`，确认单测前景守卫必红；
   - 临时删掉 `app.wxss` 暗色块内一项 TDesign 基座变量或破坏 `@supports not` 暗色 `.cal` 值，确认对应单测必红；
   - 临时将 `.readiness-bar__seg--occupied` 改用 `var(--primary)` 或把 `--vial-sheen` 调高至 `48%`，确认状态色守卫或浏览器占比条对比度测试必红。
5. **全量与跳过边界**：
   - 因 `app.css` 与 `layouts/admin.html` 影响全部后台页面，最终定稿交付前按项目规则跑一次本地全量离线测试；
   - **真实模型回归门禁与测试号真实收发**：不触及 `REPLY_PATHS` 与回复/出站链路，不运行。

---

## §7 实施门禁与回滚

- **实施门禁**：本终版 Spec 待用户确认并明确回复「**开始**」后，方可开始修改业务前端与测试文件；未获「开始」指令前严禁编码。
- **回滚**：无数据库迁移、无后端接口变动、无运行依赖变更；Web 端直接重新部署上一版本即可无损回滚，小程序端不上传或回退线上版本即可。

---

## §8 附录：Claude 独立审查意见逐条消化表

| 审查编号 | 审查意见摘要 | 本终版处置 | 落地章节 |
| :--- | :--- | :--- | :--- |
| 基线核对 | `HEAD` 实际为 `c12ecc1` / `v1.77.1`；`app.css` 工作区含既有 3 行 | 已更正基线，按用户裁决 B 纳入本次提交并在 B5 与未来发布记录单列出处 | 元信息、§0 B5、§1.1 |
| **P1-1** | 同名 CSS 变量双写回退在计算值阶段失效（IACVT） | 按用户裁决 A 采用 `@supports` 包装 `linear()` 与锚点前 `@supports not` 包装双态 `.cal` 回退，单测改审 `@supports not` 块 | §2.2、§2.3、§5 |
| **P1-2** | 暗色 `a:hover`、`.cal-m__hit:hover`、`.side-nav … .icon` 前景对比度 `2.97–3.62` | 新增 `--link-hover`（暗 `#f7b887` `9.94:1`），`.side-nav … .icon` 改 `--link`，B6 增加禁止 `color:` 引用 `--primary*` 单测守卫 | §0 B6、§2.1、§3 F1、§5 |
| **P1-3** | TDesign 1.17.0 未载 `_dark.wxss`，6 组件大量 `--td-*` 退回浅白 | 在 `app.wxss` 暗色块显式覆写完整 TDesign 暗色语义基座，并增加两层 Node 守卫（必跑基座清单 + 本机 `miniprogram_npm` 扫描） | §1.2、§3 F5、§5 |
| **P2-1** | `--vial-sheen` 48% 白光盖在 `--success` 上对比度降到 `~2.2:1` | 降至 `16%`（浅色对槽底实算 `4.19:1`、暗色 `9.08:1`），浏览器实测分段上沿对比度 | §2.2、§3 F3、§5 |
| **P2-2** | `.cal-m__fill--future` 相对槽底仅 `1.12`/`1.11` | 改用 `--cal-current-line` + `1px` `--line-strong` 内描边（纯色浅 `1.47:1`、暗 `1.35:1`）并纳入单测 | §3 F3、§5 |
| **P2-3** | 暗色 `.image-list img` 用 `--surface-sunk` 使深色透明 PNG 看不清 | 新增固定浅灰底 `--media-well: #ece8e1` 专供图片预览 | §2.1、§3 F1、§5 |
| **P2-4 / M1** | 「四档」与「3 条曲线」名称数量不匹配 | 改为「2 条物理公式曲线 + 系统减动效 `off`」 | §2.2、§3 F4 |
| **P2-5 / M2 / M3** | 弹簧误用于颜色与箭头旋转；按钮 1.8% 缩放用弹簧无感；出场不用弹簧 | 按用户裁决 M2=A 删掉 `snappy`；弹簧限 `transform`/`opacity` 入场，按压/颜色/箭头用 `--ease-out`，出场全 `--ease-in` | §2.2、§3 F4 |
| **P2-6** | 减动效规则缺 `::backdrop` | 选择器补上 `::backdrop`，浏览器增测暗色减动效弹窗 `animationName=="none"` | §3 F4、§5 |
| **P3-1** | B1 浅暗底色参考 README 与「全部从零原创」表述冲突，体系命名重复 | 改自有体系名「暖纸光学 × 暖炭光学」，B1 注明配色表作事实数据、代码图标全自写，`DESIGN.md` 标灵感来源 | 标题、§0 B1、§5 |
| **P3-2** | 浅色 `#c24322` → `#be441c` 收益微小却牵连小程序 10 阶色与 6 PNG | 按用户裁决 P3-2=A 浅色保持 `#c24322` | §2.1、§2.3、§3 F5 |
| **P3-3** | `.cal__date--today` 不应借用 `--focus` 做装饰 | 改回 `--link`（不占焦点环辨识度） | §3 F3 |
| **P3-4** | `color-scheme` 在暗色块重复声明 | 仅 `:root` 声明 `color-scheme: light dark;` | §2.1 |
| **P3-5** | 仓库无矢量源与 Pillow，需写明 PNG 生成机制与像素校验 | 写明一次性脚本读 Alpha 通道重新填色（不入库不增依赖），Node 测试核对像素色 | §1.2、§3 F5、§5 |
| **P3-6** | `t-icon` 实现支持父元素 `color` 继承 `currentColor` | 确定删除 16 处 WXML `color` 属性由父容器类声明，开发者工具暗色切换实机复核 | §1.2、§3 F5 |
| **M4** | 曲线从 6–7 个手填点改为二阶阻尼振子公式 ≥16 点采样 + 单测复算 | 用标准公式导出 2 条 20 点精确采样点（`ui` ζ=0.79 / 220ms，`panel` ζ=0.93 / 260ms）并接入单测公式复算 | §2.2、§5 |
| **M5** | 圆角卡片上 `inset 3px` 竖条会弯成月牙 | `--glow-danger` / `--glow-warning` 移除 `inset 3px` 竖条仅留外发光，无圆角列表项保持现状 | §2.2、§3 F3 |
| **Tauri 影响** | Tauri 壳加载线上同一 Web 后台，系统暗色会自动生效 | 已核对 `clients/yumi/src-tauri/build.rs:19-20`，纳入 B6 与 R7 说明范围 | §0 B6、§4.1 R7 |
