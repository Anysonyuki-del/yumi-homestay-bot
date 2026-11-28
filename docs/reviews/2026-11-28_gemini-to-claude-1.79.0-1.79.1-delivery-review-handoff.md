# 1.79.0 & 1.79.1 视觉升级与外观三态切换交付｜Gemini → Claude 审查交接

2026-11-28 · 基线 `main` = `origin/main` = `913c324`（生产现行部署标签 `v1.79.1` = `cada999`；前序上线 `v1.79.0` = `4e8f956`）· 本文未入库

## 一句话

继你对 1.79.0 黑曜钴蓝玻璃候选给出交付审查交接、我完成只读复审定位 2 项 P1 / 5 项 P2 / 3 项 P3 后，按用户三轮视觉对比最终拍板与指令，我完整实施、验证并先后上线了两个版本：

1. **`v1.79.0`（象牙瓷白 × 曜黑银刃 · A2-Pro + 1A）**：重构 Web 管理后台、认证页与微信小程序的双态视觉体系，闭环全部 P1/P2/P3 缺陷（L0/L1/L2 三层材质、1A 刀锋银刃曜石键、同源微噪点 `noise.png`、`@supports` 菲涅尔锐边守卫、逐层 RGBA Alpha 合成对比度测试、小程序双态与 12 枚重绘图标）。
2. **`v1.79.1`（外观三态切换按钮）**：按用户新增要求，经 Spec 确认后为 Web 管理后台顶栏与认证页右上角增加「跟随系统 / 浅色 / 深色」三态智能切换按钮，新增同源首帧阻塞脚本 `theme.js` 防闪屏，同步升级 `app.css` 双路暗色规则与零漂移单测/浏览器回归。

两版均已完成本地全量离线门禁、GitHub Actions CI 全部 6 趟绿灯、官方脚本备份与 `--no-deps` 仅 API 生产部署、部署后独立 SSH 与公网核对及 AOCI 对齐。请你对 `d0e71a3..913c324` 全部改动与发布记录做独立只读审查。

- **基线提交**：`d0e71a3`（`v1.78.0` 部署补记）。
- **本次提交链**（共 5 笔，均已推送 `origin/main`，合计 36 files changed, +2005 / -660）：
  1. `4e8f956`（附注标签 `v1.79.0`，已部署生产）：象牙瓷白 × 曜黑银刃（A2-Pro + 1A）全量落地（Web + 小程序 + 测试加固 + `DESIGN.md` v3.2 + Spec §9.5 + 两份审查归档 + `1.79.0` 发版与 AOCI 对齐）。
  2. `5c5aba7`：补记 `docs/releases/1.79.0.md` 部署结果与独立核对，同步 AOCI 基线。
  3. `0409a76`：新增 Web 后台与认证页外观三态切换按钮（`theme.js`、`app.css`、`icons.html`、`admin.html`、`auth.html`、`DESIGN.md` v3.3、单测与浏览器回归及 AOCI 对齐）。
  4. `cada999`（附注标签 `v1.79.1`，当前生产运行版本）：升级 `1.79.1`、`CHANGELOG.md`、`docs/releases/1.79.1.md` 与 AOCI 对齐。
  5. `913c324`：补记 `docs/releases/1.79.1.md` 部署结果与独立核对，同步 AOCI 基线。
- **完整发布记录**：`docs/releases/1.79.0.md`、`docs/releases/1.79.1.md`、`CHANGELOG.md`。
- **工作区保留项**：`.impeccable/config.json`、`.impeccable/surfaces/`、`PRODUCT.md` 为用户既有未提交改动，全程未触碰、未混入任何提交；未触碰受保护文件。

## 审查规则

- **只读审查**，不要改代码、不要提交。审查意见写成报告交给用户转回，建议路径 `docs/reviews/2026-11-28_claude-to-gemini-1.79.0-1.79.1-delivery-review.md`。
- **不要碰生产**：不要登录、不要提交后台表单、不要调用真实 DeepSeek / Hostex / 企业微信。
- 每条发现请写明：文件路径 + 函数/符号/行号、复现条件、原因与严重程度（P1 会出错 / P2 体验或维护问题 / P3 建议）；拿不准的标成「疑问」。
- 本次 `d0e71a3..913c324` 全部改动均由我独立完成并已上线，请以**怀疑式只读审查**视角直击边界、回归与文档/证据准确性。

## 一、改动总览

### Part A：`v1.79.0` 象牙瓷白 × 曜黑银刃（A2-Pro + 1A）

| 模块 | 位置 | 改动要点 |
| --- | --- | --- |
| Web 样式与微噪点 | `src/homestay_bot/static/app.css`、`src/homestay_bot/static/noise.png`（新增 4,228B） | 双态令牌切换为象牙瓷白（`#f5f4f0` / `#18191f`）× 曜黑银刃（`#08080b` / `#111116` / 冷钛 `#536382` / 冷银蓝 `#b8cdf5`）；确立 L0 哑光外壳（`scroll` 单源顶光 + 暗色同源 `noise.png` 静态微噪点，去掉 12px 点阵与 `fixed` 光晕）/ L1 实面切面卡（彻底关闭 `backdrop-filter`，菲涅尔 `::before` 全部纳入 `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` 并分拆 `mask-*`）/ L2 真玻璃浮层（仅 `.card` / `.confirm-dialog` / `.toast-region`，`.confirm-dialog::before` 统一 `inset: -1px`）；主按钮升级为浅色钢琴黑曜键 + 暗色 1A 刀锋银刃曜石键（`#242733 → #191b23` + 外嵌黑缝 + 1px 顶部刀锋银线），补齐 `:active` 与 `:not(:disabled)`；逾期行改左侧 2px 发光红条；重算 `@supports not (color-mix)` 浅暗 12 组回退色。 |
| Web 外壳 | `src/homestay_bot/templates/layouts/admin.html`、`auth.html` | 双态 `theme-color` 更新为 `#f5f4f0` / `#08080b`。 |
| 微信小程序 | `clients/wechat-miniprogram/theme.json`、`app.wxss`、12 枚 `assets/tab/*.png`、`pages/{home,me,rooms}/*.wxss` | 同步象牙瓷白 × 曜黑银刃双态令牌与完整 `TDESIGN_DARK_BASE` 暗色基座；按原 Alpha 通道重绘 12 枚 81×81 标签栏图标；清理旧暖纸/暖石注释。 |
| 守卫测试 | `tests/unit/test_admin_assets.py`、`tests/browser/test_admin_theme.py`、`tests/miniprogram/content.test.js` | 新增 `test_prism_rim_mask_is_guarded_by_supports`、`test_content_cards_stay_solid_without_dot_grid_or_fixed_aura`；升级 `test_admin_theme.py::_CONTRAST_JS` 为沿祖先链逐层 RGBA Alpha 合成后算 WCAG 对比度；同步小程序色值校验。 |
| 规范与归档 | `DESIGN.md`、`docs/specs/2026-11-28_optical-prism-visual-redesign-spec.md`（§9.5）、两份 `obsidian-cobalt-glass` 审查文档、`docs/releases/1.79.0.md` | 记录审查依据、用户拍板、改动清单与生产核对结果。 |

### Part B：`v1.79.1` 外观三态切换按钮

| 模块 | 位置 | 改动要点 |
| --- | --- | --- |
| 首帧外观脚本 | `src/homestay_bot/static/theme.js`（新增，134 行） | 以同源阻塞脚本放在两套外壳 `<head>` 且早于 `app.css`（严守 `script-src 'self'` CSP）；首帧同步读 `localStorage["yumi-appearance"]`，写 `html[data-appearance]` 与 `html.theme-ready`，同步更新两条 `<meta name="theme-color">`；实现**智能三态循环**（`auto` 态首次点击必切系统反色，3 下遍历 `auto` / `light` / `dark`），支持跨标签页 `storage` 同步、`matchMedia` 监听与存储异常降级。 |
| 双路暗色 CSS 与控件 | `src/homestay_bot/static/app.css` | 4 处暗色规则（`:root` 令牌、日历 `@supports not` 回退、`body::after` 微噪点、暗色状态微辉）均改为成对维护：`@media (prefers-color-scheme: dark)` 内限定 `:root:not([data-appearance="light"])`，并配对 `:root[data-appearance="dark"]`；新增 `:root[data-appearance="light"] { color-scheme: light; }` 覆写暗色系统下原生控件/滚动条；新增 `.topbar__actions`、`.appearance-toggle`、`.auth-toolbar` 幽灵微边胶囊样式（窄屏仅显图标，触屏 `>= 44×44px`，无 JS 时 `html:not(.theme-ready)` 自动隐藏）。 |
| 图标与布局 | `src/homestay_bot/templates/components/icons.html`、`layouts/admin.html`、`layouts/auth.html` | 新增 `sun`、`moon`、`monitor` 3 枚线性 SVG；后台放顶栏右侧（`账号安全` 左侧），认证页放浮卡正上方右对齐 `.auth-toolbar`；两套外壳均带 `?v={{ app_version }}` 引用 `theme.js`，且 `auth.html` 仍**绝不加载 `admin.js`**。 |
| 防漂移与端到端回归 | `tests/unit/test_admin_assets.py`、`tests/browser/test_admin_theme.py` | 新增 `test_manual_and_system_dark_blocks_stay_in_sync` 断言手动/系统两份暗色令牌与日历回退 100% 一致；校验 `color-scheme` 仅出现在 3 处 `:root` 选择器、`theme.js?v={{ app_version }}` 缓存破坏、`auth.html` 零 `admin.js`；浏览器测试新增 `test_appearance_toggle_cycles_persists_and_renders_on_first_frame`（浅/暗两系统下验证首次点击反色、跨 `/employee/admin` ↔ `/employee/login` 首帧无闪屏、`color-scheme` 与 `theme-color` 同步、刷新持久、恢复 `auto` 清除键）并校验无 JS 隐藏按钮。 |

## 二、请你特别核查的 6 个重点

1. **双路暗色 CSS 的特异性、覆盖顺序与漏网之鱼**（`src/homestay_bot/static/app.css`）
   - 请检查全文件是否还有任何散落的暗色/浅色假设或媒体查询未适配 `data-appearance="light" | "dark"`（尤其是 `color-scheme`、滚动条、日历 `@supports not`、`body::after` 微噪点、状态微辉与 `.metric-item--risk strong`）。
   - 核查 `:root:not([data-appearance="light"])`（在 `@media (prefers-color-scheme: dark)` 内）与 `:root[data-appearance="dark"]` 的选择器权重，以及当系统为暗色且手动切到 `light` 时，是否所有属性与视觉层都 100% 回到象牙瓷白，没有残留暗色噪点、辉光或原生日历回退色。
   - 核查单测 `test_manual_and_system_dark_blocks_stay_in_sync` 是否漏掉了 `body::after` 或状态光块的漂移检查（目前只比对了 `:root` 令牌与日历 `@supports not` 块）。
2. **`theme.js` 的首帧时序、安全边界与边界状态**（`src/homestay_bot/static/theme.js`、`layouts/auth.html`）
   - `auth.html` 从 1.79.0 的零 JS 变为仅加载同源 `theme.js`（仍不加载 `admin.js`）。请核查 `theme.js` 是否完全不接触表单、CSRF、Cookie 或网络，且不会在禁用 `localStorage`、受限沙箱或异常 DOM 状态下抛未捕获异常阻塞页面渲染。
   - 核查 `nextPreference` 在「系统是浅色 → 手动锁 `dark` → 用户此时在系统设置里把 OS 改成深色 → 再点按钮」这类中途切 OS 偏好场景下，会不会出现连续点两次没切到 `light` 或状态跳转违背直觉的问题。
   - 核查 `syncThemeColorMeta` 在 `<script>` 同步执行时（此时两条 `<meta name="theme-color">` 已在它之前的 `<head>` 中解析完毕）是否可靠命中，以及 `meta[name="theme-color"]` 在 Safari / Chrome 移动端同时改两条 `content` 是否真的生效。
3. **320px 窄屏、触屏 44×44 与焦点环表现**（`src/homestay_bot/static/app.css`、`layouts/admin.html`、`layouts/auth.html`）
   - 顶栏右侧新增 `.topbar__theme` 后，请核查在 `320px` 宽（扣真实滚动条槽约 `305px`）且同时启用 `any-pointer: coarse`（按钮 44×44px）时，顶栏品牌文字与右侧两枚 44px 按钮是否会被挤压截断或导致任何页面横向溢出。
   - 核查认证页 `.auth-toolbar` 放在 `<main class="card">` 正上方后，在登录页、强制改密页、账号安全页、503 不可用页（共用 `auth.html`）的纵向居中与矮屏滚动下有无多余留白或布局瑕疵。
4. **A2-Pro + 1A 对前轮审查 P1/P2/P3 的闭环度**（`app.css`、`tests/browser/test_admin_theme.py`）
   - 核查 `_CONTRAST_JS` 的 `effectiveBg` / `compositeOver` / `contrastRgba` 逐层合成逻辑：当元素使用 `background-image: linear-gradient(...)` 或 `backdrop-filter` 时，仅取 `backgroundColor` 合成是否存在盲区？暗色下实际计算的文字/图标/边框对比度是否全项达标？
   - 核查 `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` 的双条件在老旧 WebKit / Blink 引擎上的实际匹配语义（是否存在支持 `-webkit-mask-composite: xor` 但 `mask-composite: exclude` 写法导致退化或失效的情况）。
5. **微信小程序双态同步**（`clients/wechat-miniprogram/theme.json`、`app.wxss`、`assets/tab/*.png`、`tests/miniprogram/content.test.js`）
   - 核查 `app.wxss` 暗色块中的 `TDESIGN_DARK_BASE` 覆写与 `theme.json` 浅暗色值是否协调、主按钮暗色 `#1f222c` + `#464e66` 边在 TDesign `t-button` 上是否真能生效而不被组件内联样式覆盖。
6. **发版记录、CI 与部署核对事实一致性**（`docs/releases/1.79.0.md`、`docs/releases/1.79.1.md`、`CHANGELOG.md`、`aoci.code.txt`）
   - 核对两份发布记录里的提交号、改动清单、验证数字、部署证据与未覆盖项是否准确无夸大，确认无任何敏感信息（服务器 IP、域名、密钥、本机路径）泄漏。

## 三、验证证据汇总

### 1. 本地测试与静态检查（均为最终源码实跑）
- **静态与语法**：`.venv/bin/ruff check .`（All checks passed）、`node --check src/homestay_bot/static/theme.js`、`node --check src/homestay_bot/static/admin.js`、`git diff --check` 全部通过。
- **专项测试**：
  - `.venv/bin/pytest -q tests/unit/test_admin_assets.py`：**25 passed**
  - `.venv/bin/pytest -q tests/browser/test_admin_theme.py`：**23 passed**
  - `node --test tests/miniprogram/*.test.js`：**46 passed**（`1.79.0` 改动小程序后实跑；`1.79.1` 未改小程序）
- **本地全量离线门禁（`v1.79.1` 最终源码）**：
  - `.venv/bin/pytest -q tests/unit/test_release_scripts.py tests/browser/`：**138 passed**（75.94s）
  - `.venv/bin/pytest -q --ignore=tests/browser`：**3700 passed, 99 skipped**（129.53s；99 skipped 为需隔离 PG 连接串、本机 Docker Compose 或真实外部凭据的用例），0 failed。
- **AOCI 终态证明**：`aoci verify --json`、`aoci check --json`、`aoci index agent guide --agent antigravity --json` 均通过（`complete=true, next_action=none`，`scope_object_count=750`）。

### 2. GitHub Actions CI 状态（全部 6 趟已绿）
- `4e8f956`（`v1.79.0`，run `46079985835` / `46079985931`）：**`success`**
- `5c5aba7`（1.79.0 补记，run `46080585082`）：**`success`**
- `cada999`（`v1.79.1`，run `46086113499` / `46086113566`）：**`success`**
- `913c324`（1.79.1 补记，run `46086643472`）：**`success`**
  （说明：部署时均走本地全量通过路径，两版发布记录里按部署核对当时如实写了 CI `in_progress`；现全部 6 趟已实查完成并通过。）

### 3. 生产部署与独立核对（`v1.79.0` 与 `v1.79.1` 均通过）
- **两版部署方式**：均经干净临时克隆副本执行官方 `run-deploy.command`（仅 `--no-deps` 重建 API 容器），均输出 `REPLY_GATE_SKIPPED` 与 `DEPLOY_OK`，部署后均已清理临时副本。
- **最新生产现行状态（`v1.79.1` / `cada999`）**：
  - 部署前备份：47 张表、447 个对象、1,115,918 字节，可读性复验通过；
  - 独立 SSH 核对：服务器 `HEAD=cada999`、`git describe --exact-match --tags` 精确 `v1.79.1`、0 脏改动；API 容器 `running`、`restarts=0`、非 root（`10001:10001`）、包版本 `1.79.1`、Alembic `0038_miniprogram_home (head)`；PostgreSQL 未重建未重启（自 `2026-10-21T20:23:57Z` 连续运行，`restarts=0`）；私有上传目录 13 个文件完好；近 15 分钟错误日志 `0` 行；
  - 公网独立核对：`/health` 为 `{"status":"degraded"}`（与部署前基线一致）；`/openapi.json` 版本 `1.79.1`；`/employee/login` 200 且安全响应头齐全，含 `theme.js?v=1.79.1`、`app.css?v=1.79.1`、`data-theme-control` 且不含 `admin.js`；`/static/theme.js?v=1.79.1` 200（5,421B）；`/static/app.css?v=1.79.1` 200（109,325B）；`/static/noise.png` 200（`image/png`, 4,228B，已于 `1.79.0` 核验）；未登录 `/employee/approvals` 返回 401。

### 4. 本地只读复跑命令
```bash
.venv/bin/ruff check . \
&& node --check src/homestay_bot/static/theme.js src/homestay_bot/static/admin.js \
&& .venv/bin/pytest -q tests/unit/test_admin_assets.py tests/browser/test_admin_theme.py \
&& node --test tests/miniprogram/*.test.js
```

## 四、审查对象 SHA-256 指纹绑定（`HEAD=913c324`）

```text
be8313e56a61b3eb40255f72dca013548368e053ba7d25a0afd4cbab8ce374fc  src/homestay_bot/static/app.css
cf899eb76ec3ab55dd1cf5fe95dbb1710cf5a9514aed070a60aa94ac517e08f1  src/homestay_bot/static/noise.png
ee36d5c7293d8a4c2784cbeb835cb2151e8689522111a062bb5f3e90c91f24e5  src/homestay_bot/static/theme.js
b06bb1d404a02dc9f01b61dcf18dbb7d03e952837f0b21b1bc43ee12648d34c5  src/homestay_bot/templates/components/icons.html
33e07d615770a9e657923b8ddce4a43e7c9bdcf2fe86f19b80a5ccd93b70ff98  src/homestay_bot/templates/layouts/admin.html
b0eb1ce02e74c9e8994c6c205562c2c28ade5ee6ab27b044928021a9fe21d5e6  src/homestay_bot/templates/layouts/auth.html
e9554db3420083c7c1532d427afd5e070fd6e49ab1909df201304a6d331cc107  clients/wechat-miniprogram/app.wxss
938d34d615bbbef4f92c8c780e101fd06a87457427d4dbce9c776d31a2db91c7  clients/wechat-miniprogram/theme.json
fbd95db5f1385dc35135bccb25471c7e86eac4e36a5e4e096edddb55adaf685f  tests/unit/test_admin_assets.py
a318be4dce7efe40b49611ad2f4d1b3128fe01094889d791c524e013f6838e6c  tests/browser/test_admin_theme.py
0020f38eada5dc86be9352f95aaed75c61bb06bd17cbdac724c7ba158173f3b4  tests/miniprogram/content.test.js
d42068812238111e59cf0cf160ce85fffe278f665fc9fc4f8a7608541270a3e8  DESIGN.md
be25ae5153c81f15435f1d6c3ff24b53385c4c883e15f576e47ec20934001c61  CHANGELOG.md
538a969f0108f14f84b8e36002da227792270aa322ec3aa4a57548f4325051af  docs/releases/1.79.0.md
eee722ae286f51111a682c8c770f711e34600f096f65394e2f4f8422b6da8fb2  docs/releases/1.79.1.md
```

## 五、未覆盖与已知限制

1. **生产登录后页面目视验收**：未登录生产管理员会话，线上后台浅/暗外观与点击顶栏切换键的最终目视由用户在已登录浏览器确认。
2. **真机与跨内核引擎**：自动化浏览器测试仅在 Chromium（320–1440px）执行，未在真实 iOS Safari、Android WebView、Windows WebView2 或读屏软件上实测。
3. **微信小程序发版**：小程序配色、图标与暗色基座已随 `v1.79.0` 入库但未在微信开发者工具构建 npm、目视或上传审核；微信平台本身不支持运行时覆盖 `theme.json`，因此 `v1.79.1` 未对小程序新增手动切换按钮。
4. **真实模型门禁与测试号收发**：两版均未修改 `REPLY_PATHS` 与后端业务逻辑，按规则跳过。
