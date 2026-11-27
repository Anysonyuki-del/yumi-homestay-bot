# 黑曜钴蓝配色与玻璃光效（拟发 1.79.0）｜Gemini → Claude 独立交付审查与视觉改进方案

- **日期**：2026-11-28
- **审查对象**：工作区未提交改动（基线 `main` = `origin/main` = `d0e71a3`，生产 `v1.78.0` = `8daf96a`；22 个业务/测试/文档文件，不含 `.impeccable/` 与 `PRODUCT.md`）；交接文档 `docs/reviews/2026-11-28_claude-to-gemini-obsidian-cobalt-glass-delivery-review-handoff.md`
- **审查方式**：全程只读（未修改任何业务代码、未提交、未触碰生产及外部服务）。通读 `src/homestay_bot/static/app.css` 全文（1200 行）、`clients/wechat-miniprogram/app.wxss`、`theme.json`、`pages/{home,services}/index.wxss`、`docs/specs/2026-11-28_optical-prism-visual-redesign-spec.md` §9、`tests/unit/test_admin_assets.py`、`tests/browser/test_admin_theme.py`；逐张目视审查 `.stage/1.79.0-preview/` 全部 10 张 1440px / 390px 浅暗预览图；使用 Python 标准库直接解码预览 PNG 抽样真实合成像素对比度，并在本地 Playwright Chromium 实测 `mask-composite` 合成与圆角几何。
- **合规边界**：全部视觉改进建议均为原创物理光学与令牌设计，不含 `RMvdesi/obsidian-prism`（GPL-3.0）任何源码或素材。

---

## 一、总体结论

**不建议按当前视觉方案进入 1.79.0 发版。**

1. **视觉与质感结论（核心问题）**：当前版本之所以被用户否定，并非「光效还不够猛」，恰恰相反——是**过度堆叠多色彩虹光斑与泛化毛玻璃**，导致「暗色黑位发灰发脏、浅色玻璃隐身且底色泛廉价蓝紫雾、实色内嵌块与玻璃外卡材质割裂、所有控件争抢发光焦点」。继续把透明度和光斑往大了拧只会更廉价。
2. **工程正确性结论**：存在 **2 项 P1**（真实合成对比度在暗色卡片左上角跌破 4.5:1 且测试存在 alpha 盲区；棱镜 `::before` 缺 `@supports` 守卫在旧浏览器有盖死卡片风险）、**5 项 P2**（文末光学层覆盖掉 `.metric-item--risk` 红色发光导致风险卡发蓝光、`.workbench-first`/`.danger-panel` 语义边框被洗掉、几十张卡同时 22px blur 滚动性能风险、手机端无 blur 致点阵穿透半透明卡、聚焦输入框双圈霓虹过载与 `.confirm-dialog::before` 几何内缩）以及 **3 项 P3**。

---

## 二、为什么「配色不认可、质感仍显廉价」？（基于 10 张预览图实查）

| 维度 | 预览图直接可见现象 | 代码根因（文件与行号） |
| :--- | :--- | :--- |
| **1. 配色撞脸 AI 模板且色相打架** | `admin-dark.png` / `tasks-dark.png` 呈蓝 + 紫 + 青三色极光，叠上顶部的暗土黄告警条、红色风险卡与五彩徽章，整体像通用监控/AI SaaS 模板，完全丢失精品民宿管家的沉静品质感。 | `app.css:9` `#2563eb`（通用 Tailwind 蓝）；`app.css:241-246` `--aura` 同时叠蓝（46%）+ 紫（16%+34%）+ 青（26%）三色大面积光斑。 |
| **2. 暗色黑位不纯（发灰发雾）** | 真正的黑曜石感靠「极纯深墨黑位 + 极窄锐利高光」；当前 `admin-dark.png` 上半屏整片被 46% 蓝光与 55% 玻璃染成浑浊蓝灰（`rgb(36, 52, 79)` / `rgb(38, 40, 53)`），失去通透与厚重感。 | `app.css:243,249,255` 高透明主光叠 55% `--glass-bg` 再叠 13%+7% `--glass-sheen`。 |
| **3. 浅色玻璃隐身、只剩蓝紫雾影** | `admin-light.png` / `login-light.png` 卡片左上实测像素为 `rgb(253, 253, 255)`（99.2% 纯白），根本看不出玻璃；背后却是饱和蓝紫云雾，卡片下方拖着一大团深蓝色雾影，显得脏而旧。 | `app.css:117,123` 50% 白底叠加 85% + 70% 白色 `--glass-sheen` 把透明度糊死；`app.css:138` `--shadow-card` 使用 `rgb(30 64 175 / 32%)` 重蓝影。 |
| **4. 实色贴纸粘在玻璃板上（材质割裂）** | ① `admin-dark.png` 死黑实色 `.topbar`（`#141418`）在 y=52px 处把下方 46% 亮蓝光斑硬生生腰斩；② 顶部 `.alert--warning` 是扁平暗棕块；③ `ops-dark.png` 玻璃房卡里嵌着两块不透明死黑的 `.room-event` 和 `.cal`，像亚克力板上贴黑不干胶。 | `app.css:602,821` 实色顶栏；`app.css:633` 实色告警条；`app.css:908,934` 未纳入光学阶梯的实色 `.room-event` 与 `.cal`。 |
| **5. 伪光学边与全域发光疲劳** | ① 四色彩虹 140° `--rim` 让卡片左上死白、右下发青，像塑料描边滤镜；② `--shadow-card` 给每张普通卡都加 `48px` 蓝色外辉，像发光灯箱；③ 侧栏当前项、页签、主按钮（20% 白条 + 70% 外辉）、输入框双蓝圈全部同时发光，失去主次焦点。 | `app.css:248` `--rim`；`app.css:261-268` `--keycap-glow` / `--glow-primary` / `--shadow-card`；`app.css:299,1170` `:focus-visible` 与 `--focus-glow` 叠加。 |

---

## 三、工程正确性与回归问题清单（P1 / P2 / P3）

### P1（必须修复）

#### P1-1 · 暗色真实合成对比度跌破 WCAG AA（4.5:1），且浏览器测试存在 Alpha 盲区
- **位置**：`src/homestay_bot/static/app.css:241-256`、`tests/browser/test_admin_theme.py:228,302`
- **实测证据**（直接解码 `.stage/1.79.0-preview/admin-dark.png` 抽样）：
  - 「先处理」卡片左上角 `(290, 235)`（日期文字 `2026年10月2日 · 优先处理` 正背后真实像素）为 `rgb(38, 40, 53)`，`--muted (#8a8a98)` 在其上对比度仅 **`4.29:1`**（< 4.5:1）；
  - 右上顶栏下蓝光画布 `(1300, 62)` 真实像素为 `rgb(36, 52, 79)`，任何直接落在画布上的 `--muted` 文字（如分页、辅助说明）对比度仅 **`3.67:1`**；
  - 理论合成最坏点（卡片叠加左上 `--glass-sheen` 落在右上主光处 `rgb(68, 81, 109)`）：`--muted` 仅 `2.33:1`、`--ink-soft` 仅 `3.38:1`、`--danger` 仅 `2.75:1`；
  - 浅色最浓蓝光处（`admin-light.png` 右上 `.tab-nav`，5% `--glass-well` 叠 36% 蓝光）合成底 `rgb(161, 185, 235)`，未激活页签 `--ink-soft (#475569)` 仅 **`3.84:1`**。
- **测试盲区根因**：`test_admin_theme.py:228` 与 `302` 均以 `value.match(/[\d.]+/g).slice(0, 3)` 截取前三个 RGB 通道并丢弃了 alpha，也未计入 `background-image` 渐变光层，导致半透明卡片测试假绿。
- **修复要求**：① 大幅压低背景光晕与顶部漫反射强度（见第四节），确保最亮叠加处 `--muted >= 4.8:1`；② 升级 `test_admin_theme.py` 对 `rgba()` 按父层链逐层做 Alpha 合成计算（或对含 `backdrop-filter`/半透明的重点卡直接校验不透明度下限与最坏合成值）。

#### P1-2 · 棱镜描边 `::before` 缺 `@supports` 守卫，旧浏览器存在整卡被渐变盖死风险
- **位置**：`src/homestay_bot/static/app.css:1146-1150`
- **复现条件**：任何识别 `position: absolute; inset: -1px; background: var(--rim)` 但不支持 `mask-composite: exclude`（或在简写 `mask:` 中不认 `exclude` 关键字）的旧版浏览器 / Android WebView（含项目 `admin.js:189` 兼容基线 Safari 15.4 边缘场景）。`::before` 未设 `z-index` 但具绝对定位，会画在普通文档流文字之上；遮罩合成失效时，整张卡片会被高不透明蓝白渐变直接盖住、正文完全不可读。
- **修复要求**：整段 `::before` 棱镜边规则必须包裹在 `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` 中，并将标准属性拆分书写（`mask-image` / `mask-clip` / `mask-composite: exclude`）与 `-webkit-*` 一一对应，避免标准简写解析分歧。

---

### P2（视觉 Bug、性能与体验问题）

#### P2-1 · 文末光学层误覆盖 `.metric-item--risk`、`.workbench-first`、`.danger-panel` 语义样式
- **位置**：`app.css:1155` vs `app.css:420, 539, 661, 1196`
- **实证**：
  - `.metric-item--risk` 在 `L661` 设置的 `box-shadow: var(--glow-danger)` 被 `L1155` `.metric-item { box-shadow: var(--glass-edge), var(--shadow-card); }` 覆盖；`L1196` 重写 `.metric-item--risk` 时忘了补回 `box-shadow`，导致 `admin-dark.png` 中「待我关注 1」风险卡散发的是**蓝色外辉**（来自 `--shadow-card`）而非红色告警余晖；
  - `.workbench-first`（`L539`）与 `.danger-panel`（`L420`）均附着于 `.panel`，其语义描边与阴影被 `L1155` 全部覆盖回普通玻璃边与蓝青棱镜边。
- **修复要求**：在最终光学层统一按语义变体收口（如 `.metric-item--risk`、`.danger-panel` 显式覆写对应语义边与投影），或将光学样式直接内聚到组件定义处而非文末用低优先级单类粗暴覆盖。

#### P2-2 · 长列表页多卡并发 `backdrop-filter: blur(22px)` + `background-attachment: fixed` 存在明显掉帧风险
- **位置**：`app.css:1138, 1141, 1159`
- **影响**：房源管理、入住安排、任务中心等页面一次渲染 10–30+ 张 `.room-operation-card` / `.panel` / `.metric-item`，每张卡片创建独立合成层并逐帧对视口背景做 22px 高斯模糊，叠加 `background-attachment: fixed` 与 `position: fixed` 点阵，在 Windows WebView2 / Tauri 客户端及中端 Android WebView 上滚动极易掉帧与高功耗。
- **修复要求**：将 `backdrop-filter` 收敛至**仅 3 类真正悬浮层**（认证 `.card`、`<dialog>` 确认弹窗、`.toast-region`）；常规 `.panel` / `.room-operation-card` / `.metric-item` 改用高定不透明/高不透光学切面（见第四节）。

#### P2-3 · 手机端（`<768px`）关闭 `backdrop-filter` 却保留 50%/55% 半透明底，点阵刺穿卡片
- **位置**：`app.css:1141, 1155, 1158-1160`
- **实证**：`admin-dark-390.png` 清晰可见 12px 点阵未经模糊直接穿透进半透明卡片内部，文字背景布满噪点；`admin-light-390.png` 卡片失去质感显薄。
- **修复要求**：直接取消全局 12px 点阵层，且无 `backdrop-filter` 的面板统一采用实色/高不透光学切面，彻底消灭双端体验断层。

#### P2-4 · 聚焦输入框双蓝圈过载 + `.confirm-dialog::before` 几何圆角错位
- **位置**：`app.css:284, 299, 1151, 1170`
- **实证**：
  - `login-dark.png` 账号输入框聚焦时同时叠加 `border-color: var(--focus)` + `outline: 2.5px solid var(--focus); outline-offset: 2px` + `0 0 0 3px var(--focus-ring)` + `22px` `--focus-glow`，呈现厚重粗糙的双层霓虹套环；
  - `.confirm-dialog::before` 设 `inset: 0; border-radius: inherit;`（`16px`），因绝对定位包含块为 padding-box（外边框内侧，理论内圆角 `15px`），高光边内缩 1px 且与自身实边框形成双线。
- **修复要求**：输入框 `:focus` 只保留单层锐利边框 + 克制 `3px` 柔环（去掉 22px 模糊 `--focus-glow`），`:focus-visible` 不与内聚焦重复叠厚框；`.confirm-dialog` 自身边框设为 `border: 1px solid transparent` 并令 `::before` 统一 `inset: -1px`（或直接用单层高光阴影替代伪元素边）。

#### P2-5 · `.panel` 层叠上下文核查结论
- **位置**：`app.css:1158-1160`
- **核查结果**：经扫描全仓 `app.css` 全部 `fixed/absolute/sticky` 选择器，当前页面内的全局浮层（`.topbar`、`.admin-sidebar`、`.toast-region`、`.skip-link`）均不在 `.panel` 内部，`.selection-actions[data-fixed-bulk]` 仅在 `<=767.98px` 固定，当前页面**尚未触发**浮层被后续 `.panel` 遮挡；但只要未来在 `.panel` 内引入任何绝对定位弹出菜单/气泡提示，就会被后续兄弟 `.panel` 遮盖。按 P2-2 收敛掉常规 `.panel` 的 `backdrop-filter` 可一并根除该风险。

---

### P3（一致性与细节清理）

1. **P3-1 · 小程序注释残留与玻璃策略**：`clients/wechat-miniprogram/pages/home/index.wxss:1,15,46` 仍残留旧版「暖纸画布」「暖石细描边」注释。小程序**不建议**给卡片大面积加 `backdrop-filter`（微信真机尤其部分 Android 机型与 Skyline/WebView 切换下易闪屏、圆角裁切异常及掉帧），建议仅在 `.bottom-bar` / `.ask-host` 做 `@supports (backdrop-filter: blur(1px))` 渐进尝试，卡片本体与 Web L1 保持同一套「极净底色 + 1rpx 顶边锐利高光 + 双层物理投影 + 克制单源环境光」体系。
2. **P3-2 · 禁用态权重隐患**：`app.css:553`（`.first-list .button`，权重 `0,2,0`）与 `app.css:981`（`.room-operation-card .room-next-action .button`，权重 `0,3,0`）优先级均高于 `app.css:286`（`button:disabled`，权重 `0,1,1`），应补充 `:not(:disabled)` 防止未来禁用按钮被强染主色。
3. **P3-3 · 闲置令牌**：`--primary-active`（`app.css:11, 174`）已声明且受单测对比度保护，但主按钮 `:active`（`app.css:335`）仅缩放未切 `background-color: var(--primary-active)`，建议补齐按压色沉反馈。

---

## 四、视觉 / 质感 / 光效彻底改进方案（可直接落地）

### 4.1 配色方向重构（3 选 1，彻底告别「AI 模板蓝紫」与「1.78.0 暗红棕」）

全部核心色对均已用 Python 按 WCAG 2.1 实算通过（正文/链接/按钮 `>= 4.5:1`，填充主色对卡片 `>= 3.0:1`）：

#### 方案 A1（首推 · 精品民宿专属调性）：**曜黑冷银 × 深海松石（Obsidian Slate × Alpine Teal）**
- **气质**：沉静、克制、自带精品酒店与度假建筑的呼吸感；与成功绿、警示琥珀、危险红清晰区分，彻底摆脱 Tailwind 蓝 AI 模板味。
- **核心令牌**：
  - **浅色（建筑石膏白）**：`--page: #f5f6f7`、`--sidebar-bg: #eceef1`、`--surface: #ffffff`、`--surface-sunk: #f1f3f6`、`--surface-well: #e6e9ee`、`--ink-strong: #111317`、`--text: #1f242d`、`--muted: #555f6e`（`6.46:1`）；主动作填充 `--primary: #0d6e6e`（配白字 `6.05:1`）、`--primary-hover: #0a5959`（`8.12:1`）、`--primary-active: #084848`、`--link: #0b6161`（`7.25:1`）、`--link-hover: #084a4a`、`--primary-soft: #e6f4f3`。
  - **暗色（纯净曜黑）**：`--page: #09090b`、`--sidebar-bg: #060608`、`--surface: #121216`、`--surface-sunk: #0e0e12`、`--surface-well: #19191f`、`--ink-strong: #f4f4f6`、`--text: #e2e2e7`、`--muted: #8d8d9b`（`5.71:1`）；主动作填充 `--primary: #0f766e`（配白字 `5.47:1`、对卡片 `3.41:1`）、`--primary-hover: #0d655e`（`6.91:1`）、`--primary-active: #0a524c`、`--link: #5eead4`（`12.63:1`）、`--link-hover: #99f6e4`、`--primary-soft: #0d2424`。

#### 方案 A2（高定极简 · 最难出错）：**曜黑单色 × 铂金银白（Monochrome Obsidian × Platinum Alabaster）**
- **气质**：Linear / Apple Pro / Leica 相机级精密仪器风——主动作走反相高定黑白铂金，**全部彩色仅留给业务状态（成功/警告/危险）**，零色相冲突，质感上限最高。
- **核心令牌**：
  - **浅色**：画布 `#f5f6f7`、侧栏 `#eceef1`、卡片 `#ffffff`；主按钮 `--primary: #16161a` + `--on-primary: #ffffff`（`18.04:1`）、`--primary-hover: #2b2b33`（`14.04:1`）；文字链接 `--link: #2c4c7c`（`8.64:1`）、`--primary-soft: #eef1f6`。
  - **暗色**：画布 `#09090b`、侧栏 `#060608`、卡片 `#121216`；主按钮采用反相高定铂银按键 `--primary: #e8e9ee` + `--on-primary: #0c0c0f`（`16.11:1`，对卡片 `15.41:1`）、`--primary-hover: #ffffff`（`19.53:1`）；文字链接 `--link: #a8c5f0`（`10.60:1`）、`--primary-soft: #1b202b`。

#### 方案 B（若坚持冷蓝系 · 去廉价修正版）：**极净黑曜 × 石墨钛蓝（Stealth Graphite × Steel Blue）**
- **气质**：保留用户此前选过的冷蓝方向，但**彻底删除紫色与青色霓虹**，把刺眼 `#2563eb` 收敛为沉稳低饱和石墨钛蓝，把黑位纯度还给黑曜石。
- **核心令牌**：
  - **浅色**：`--page: #f5f6f8`、`--sidebar-bg: #ebedf2`、`--surface: #ffffff`、`--primary: #234ea6`（`7.74:1`）、`--primary-hover: #1c3f8a`（`9.87:1`）、`--link: #1e4596`（`8.95:1`）。
  - **暗色**：`--page: #09090b`、`--sidebar-bg: #060608`、`--surface: #121216`、`--primary: #3263c4`（配白字 `5.65:1`、对卡片 `3.31:1`）、`--primary-hover: #2955ad`（`7.01:1`）、`--primary-active: #224791`（`8.83:1`）、`--link: #93b4f5`（`8.98:1`）。

---

### 4.2 质感与光效的 6 项物理级改法（按投入产出排序，三套配色通用）

1. **【ROI #1】砍掉 5 层彩虹雾与 12px 点阵，改用「单一穹顶漫射光 + 纯净黑位」**
   - 删除 `.admin-shell::before` 12px 点阵（解决桌面被模糊浪费、手机刺穿卡片）。
   - 重写 `--aura` 为**克制单主光源**：
     - 暗色：`radial-gradient(ellipse 68% 36% at 52% -8%, rgb(255 255 255 / 7%), transparent 68%), radial-gradient(ellipse 46% 32% at 86% -4%, rgb(<accent-rgb> / 12%), transparent 72%)`（把最大光斑透明度从 46% 压到 12%，最亮处画布不超过 `rgb(20, 23, 28)`，黑位深邃且 `--muted` 恒大于 `5.0:1`）。
     - 浅色：`radial-gradient(ellipse 72% 38% at 50% -10%, rgb(255 255 255 / 90%), transparent 70%), radial-gradient(ellipse 42% 28% at 88% -6%, rgb(<accent-rgb> / 6%), transparent 72%)`（洁净建筑漫光，彻底告别蓝紫脏云）。
2. **【ROI #2】建立 3 层材质秩序（L0 哑光外壳 / L1 精工切面卡 / L2 真玻璃浮层）**
   - **L0 外壳（`.admin-sidebar`、`.topbar`）**：保持实色哑光底（满足 R-02 顶栏不透字），去掉顶栏彩虹发线与侧栏霓虹竖线，改为极克制结构细线（暗色 `rgb(255 255 255 / 7%)`）+ 顶部 `1px` 微光边（`inset 0 1px 0 rgb(255 255 255 / 4%)`），彻底消除顶栏腰斩光斑的视觉断层。
   - **L1 内容面板与列表卡（`.panel`、`.metric-item`、`.room-operation-card`）**：**关闭每张卡片的 `backdrop-filter`**，改为精密光学切面材质：
     - 暗色：`background: linear-gradient(180deg, #17171c 0%, #121216 100%)`（极轻微的顶亮底暗体量渐变，不透明、文字对比度绝对稳健、滚屏稳 60/120fps）；
     - 浅色：`background: linear-gradient(180deg, #ffffff 0%, #fafbfc 100%)`（在微灰 `#f5f6f7` 画布上清晰立起，不再需要重蓝影强行区分）。
   - **L2 真·磨砂玻璃浮层（仅 3 处）**：认证浮卡 `.card`、原生确认弹窗 `.confirm-dialog`、轻提示 `.toast-region` 保留 `backdrop-filter: blur(24px) saturate(145%)`（暗色 `rgb(20 20 26 / 76%)`，浅色 `rgb(255 255 255 / 80%)`）。只有真正浮在内容之上的界面才用玻璃，层级与高级感立刻立住。
3. **【ROI #3】把四色彩虹 `--rim` 升级为「单色物理顶光菲涅尔刃口（True Top-Rim）」+「双层物理投影」**
   - 用 `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` 包裹 1px 棱镜边，改为**单一顶光源物理衰减**：
     - 暗色：`--rim: linear-gradient(180deg, rgb(255 255 255 / 20%) 0%, rgb(255 255 255 / 7%) 34%, rgb(255 255 255 / 3%) 100%);`（仅风险卡 `.metric-item--risk::before` 顶边用柔和危险色 `linear-gradient(180deg, rgb(240 113 106 / 52%) 0%, rgb(240 113 106 / 14%) 45%, rgb(240 113 106 / 5%) 100%)`）；
     - 浅色无需遮罩伪元素，直接用 `border: 1px solid rgb(15 23 42 / 8%)` + `inset 0 1px 0 rgb(255 255 255 / 100%)` 即可呈现极干净的实体瓷面刃口。
   - **投影去霓虹化**：普通卡片 `--shadow-card` 彻底删除 `48px` 彩色外辉，改为双层真实物理投影：
     - 暗色：`0 1px 2px rgb(0 0 0 / 55%), 0 12px 32px -12px rgb(0 0 0 / 75%)`；
     - 浅色：`0 1px 2px rgb(15 23 42 / 5%), 0 8px 24px -10px rgb(15 23 42 / 9%)`。
4. **【ROI #4】统一内嵌组件材质，消灭「黑贴纸」**
   - `.room-event`、`.stay-pane`、`.filter-toolbar`、`.empty-state`、`.cal` 纳入同一套 L1 沉降阶梯：暗色外卡 `#121216` → 内嵌槽 `#0d0d11` + `1px solid rgb(255 255 255 / 6%)` + 底部 `inset 0 -1px 0 rgb(255 255 255 / 3%)`（模拟物理内凹槽底边承光）；
   - `.alert--warning`（系统降级条）暗色改为 `background: linear-gradient(90deg, rgb(242 184 75 / 11%) 0%, #15151a 46%)` + `border: 1px solid rgb(242 184 75 / 26%)`，告别突兀土棕块。
5. **【ROI #5】收敛控件光效：从「霓虹按钮/双圈输入框」升级为「精工机械键帽」**
   - **主按钮**：去掉 `20%` 白条与 `70%` 强蓝外发光；改用 `box-shadow: inset 0 1px 0 rgb(255 255 255 / 18%), inset 0 -1px 0 rgb(0 0 0 / 22%), 0 1px 2px rgb(0 0 0 / 35%)`（暗色悬浮微提亮或给极克制 `0 4px 12px -4px rgb(<accent> / 35%)`），`:active` 同步切换 `background-color: var(--primary-active)`；
   - **侧栏当前项与页签**：暗色用 `rgb(255 255 255 / 7%)` 哑光浮层 + 顶部 `1px` `rgb(255 255 255 / 14%)` 锐边 + 左侧 `2.5px` 干净主色细条（无外糊光）；浅色纯白键帽 + 细描边 + 轻投影；
   - **输入框聚焦**：保留单层 `border-color: var(--focus)` + `0 0 0 3px var(--focus-ring)`，删除 22px 模糊 `--focus-glow`，避免与 `:focus-visible` 叠成双圈。
6. **【ROI #6】可选点睛：同源极微噪点纹理（消除暗色渐变色带）**
   - 符合 `EMPLOYEE_CSP`（`img-src 'self'`，不使用 `data:` URI）：可在 `src/homestay_bot/static/` 放置一张 `128×128` 约 `1.5KB` 的单色无损 `noise.png`，仅在暗色 `body::after` 以 `opacity: 0.025; pointer-events: none; position: fixed; inset: 0; z-index: 0`（或叠加在光晕伪层）铺平，瞬间赋予暗色切面「阳极氧化铝 / 喷砂曜石」细腻颗粒感并消除 8-bit 渐变色带。

---

## 五、对交接文档第六节 7 个重点问题的逐条答复

1. **质感根因与按 ROI 排序的改法**：见第二节（5 大根因）与第四节（6 项物理级升级）。核心结论不是「玻璃不够透或光不够亮」，而是**彩虹光斑过浓污染黑位、浅色高光糊死透明度、万物皆糊破坏层级、内嵌实块材质割裂**。
2. **`mask-composite` 退化风险**：成立，标为 **P1-2**，必须用 `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` 包裹并拆分标准属性书写。
3. **层叠与布局**：
   - `.panel` 开 `backdrop-filter` 在当前模板暂未造成已有浮层被遮，但留有未来子浮层被后续 `.panel` 遮挡的隐患（P2-5），收敛掉普通 `.panel` 的 blur 可根除；
   - `.panel::before` 的 `inset: -1px; border-radius: inherit` 实测几何对齐外框圆角，但 `.confirm-dialog::before` 的 `inset: 0; border-radius: inherit` 存在 1px 内缩双线问题（P2-4）；
   - `:where(...) { position: relative }` 优先级为 0，经核查未破坏任何已有 `sticky` / `fixed` 元素（P3-2）。
4. **滚动性能**：成立，标为 **P2-2**，强烈建议收敛 `backdrop-filter` 至 L2 浮层（认证卡、弹窗、Toast），释放长列表页与 WebView 滚屏性能。
5. **真实合成可读性**：**不满足 4.5:1**，已实锤为 **P1-1**（`admin-dark.png` 卡片左上 `--muted` 实测 `4.29:1`，右上画布 `3.67:1`，浅色右上页签槽 `--ink-soft` 理论 `3.84:1`）。
6. **小程序一致性与玻璃取舍**：见 **P3-1**。不建议小程序卡片大面积开 `backdrop-filter`；只需同步新定调色阶、顶边 `1rpx` 锐利高光、双层物理投影与单源克制顶光，就能与 Web L1 保持高度一致。
7. **其他正确性问题**：见 **P2-1**（`.metric-item--risk` / `.workbench-first` / `.danger-panel` 被文末规则覆盖）、**P2-3**（手机端无 blur 点阵穿透）、**P3-2**（`.first-list .button` 等高于禁用态权重）、**P3-3**（`--primary-active` 未消费）。

---

## 六、建议下一步（待用户拍板）

请用户先在 **4.1 三套配色方案**中择一（或指定新方向），并确认是否采纳 **4.2 的 3 层材质收敛（L1 切面实面 + L2 真玻璃浮层 + 去掉彩虹光晕与点阵）** 与 **可选同源微噪点 `noise.png`**：
1. **方案 A1（首推）**：曜黑冷银 × 深海松石（精品民宿度假感，零 AI 蓝紫味）
2. **方案 A2（高定极简）**：曜黑单色 × 铂金银白（反相黑白铂金主按钮，彩色只留业务状态，高级感最稳）
3. **方案 B（去廉价冷蓝）**：极净黑曜 × 石墨钛蓝（去掉紫/青霓虹与过浓蓝雾，收敛为低饱和专业蓝）

拍板后再由 Claude（或我）先更新 Spec §9 并产出 2–3 张代表页对比预览图，确认眼见满意后再收尾发版。
