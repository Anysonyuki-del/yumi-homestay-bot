# 前端光学视觉重设计 Spec｜Claude → Gemini 独立审查

- **日期**：2026-11-28
- **审查对象**：`docs/specs/2026-11-28_optical-prism-visual-redesign-spec.md`（SHA-256 `fb61a505…a235f5`，与交接文档一致）
- **审查方式**：只读。读了 Spec、交接文档、`app.css` 全文、相关测试、小程序源码与 TDesign 1.17.0 组件源码；参考项目只读了公开 README、`.github/ARCHITECTURE.md` 和 `motion-engine.js`，用来理解它的设计思路，本报告不含其代码。
- **验证**：Python 按 WCAG 公式重算全部关键色对；本地 Playwright Chromium（151）实测 CSS 自定义属性回退行为。没有改代码，没有提交，没有调用外部服务。

## 结论

方向可行，色值算得很扎实：日历 12 个派生值全部与 `color-mix` 公式一致，大部分对比度复算无误。但 Spec 有 **3 个 P1** 必须在终版前修正：

1. 自定义属性「先写回退、再写新语法」的降级机制不成立。Spec 的 R2、R4 两项缓解都建在它上面，现有 `.cal` 回退其实也一直没生效。
2. 暗色下仍有文字和图标把 `--primary` / `--primary-hover` 当前景色，对比度 2.97–3.62:1，不满足 Spec 自己的 B6。
3. TDesign 1.17.0 没有加载自带暗色主题，组件内大量 `--td-*` 令牌会退回浅色字面量。Spec 只覆写了其中一部分，小程序暗色会出现白块。

另有 6 条 P2、6 条 P3，以及参考 obsidian-prism 动效思路的 5 条优化建议（见第 4 节）。

---

## 1. 基线与工作区核对

| 项 | Spec 写法 | 现场事实 | 处理建议 |
| :--- | :--- | :--- | :--- |
| 基线 | `main` = `9b0518a`，线上 `v1.77.0` | `HEAD` = `c12ecc1`，标签 `v1.77.1`（`5ae95f3` 修复 + `c12ecc1` 文档） | 改为 `c12ecc1` / `v1.77.1`。1.77.1 没动前端，不影响方案 |
| `app.css` 行数 | 923 行，「代码零改动」 | `HEAD` 为 920 行；工作区有用户未提交的 3 行（`app.css:889–891`，知识库目录房号不换行修复） | B5 要把这 3 行列为「用户既有改动」，并写明实施时怎么处理：先单独提交，或者随本次一起提交并在发布记录中说明。不能静默混入 |

---

## 2. P1：必须在终版 Spec 中修正

### P1-1 自定义属性「双写回退」不生效，R2 / R4 的缓解措施不成立

- **Spec 位置**：§2.2「前一行保留 `cubic-bezier` 兜底」、§2.3、§4.1 R2、R4
- **源码**：`app.css:731–745` `.cal` 的 `--cal-*` 成对声明；`tests/unit/test_admin_assets.py::test_calendar_bar_palette_stays_derived_from_the_primary_token`
- **原因**：自定义属性在解析阶段不校验值，同名的后一条声明总会胜出。不认识 `linear()` 或 `color-mix()` 的浏览器拿到后一条，用到它的属性会在计算值阶段失效，不会退回前一行。
- **实测**（Chromium 151，用一个不存在的函数模拟「不认识的新语法」）：
  - `--c: #ff0000; --c: bogus-fn(1, 2);` 再写 `color: var(--c)`：得到继承色 `rgb(0, 128, 0)`，不是红色回退
  - `--e: cubic-bezier(...); --e: bogus-ease(0, 1);` 再写 `transition: opacity 200ms var(--e)`：`transition-duration` 变成 `0s`，整个过渡消失，没有退回平滑曲线
  - `border-color` 同理，退回 `currentcolor`
- **影响**：
  - R4 说老浏览器会「无损退回平滑减速曲线」，实际是所有用到 `--ease-spring-*` 的过渡全部消失。`linear()` 要 Safari 17.2+。
  - `.cal` 的静态回退从来没生效过。`color-mix()` 要 Safari 16.2+，而项目明确照顾 Safari 15.4（`admin.js:189` 注释）。在这类浏览器上，日历条现在就是透明底加 `currentcolor` 边框。
  - 现有单测守的是一个不起作用的机制。Spec 打算把它扩展到暗色，等于把缺陷翻倍。
- **建议修法**（需写进 Spec）：
  - 缓动：`:root` 只声明 `cubic-bezier` 版 `--ease-spring-*`，再用 `@supports (transition-timing-function: linear(0, 1)) { :root { --ease-spring-*: linear(...); } }` 整体覆盖。
  - 日历：`.cal` 只保留 `color-mix()` 声明，另起 `@supports not (color: color-mix(in srgb, red 50%, blue)) { .cal { 浅色字面值 } }`，再在其中嵌套 `@media (prefers-color-scheme: dark) { .cal { 暗色字面值 } }`。
  - 单测改成：解析 `@supports not` 块，分别用浅色 `:root` 和暗色 `:root` 令牌按 `_srgb_mix` 验算，并断言 `.cal` 主块里不再出现字面色。这样守的是真正起作用的分支。
- **用户裁决**：A. 按上面方式修，保留 Safari 15.4–16.1 降级（推荐）；B. 正式放弃这些旧版本，删除全部静态回退和对应测试，Spec 写明浏览器基线。

### P1-2 暗色下仍有前景元素直接使用 `--primary` / `--primary-hover`，不满足 B6

- **Spec 位置**：§2.1 `--primary` / `--primary-hover` 暗色值、B6、交接硬核点 3
- **实算**（暗色）：

| 选择器（源码） | 前景 → 背景 | 对比度 | 门槛 |
| :--- | :--- | :--- | :--- |
| `a:hover`（`app.css:108`） | `--primary-hover #c74a1f` → `--surface #1d1b19` | **3.62** | 4.5 |
| `a.cal-m__hit:hover .cal-m__name`（`app.css:827`） | 同上 | **3.62** | 4.5 |
| `.side-nav a[aria-current="page"] .icon`（`app.css:416`） | `--primary #b84018` → `--primary-soft #2c1b14` | **2.97** | 3.0（图标） |
| `.side-nav a[aria-current="page"]::before`（`app.css:415`） | 同上 | 2.97 | 3.0 |

  §3 F1 已把 `::before` 指示条改成 `--link`，但 `.icon` 和两处 `:hover` 文字没有覆盖。
- **建议**：
  - 新增 `--link-hover`：浅色沿用 `#a13614`（6.88:1）；暗色取比 `#f09a5e` 更亮的暖琥珀，实施时实算不低于 7:1。
  - `a:hover`、`.cal-m__hit:hover` 改用 `--link-hover`；`.side-nav … .icon` 改用 `--link`。
  - 在 Spec 中写一条规则：`--primary*` 只当填充底色或描边，不当文字或图标前景色。单测增加一条守卫：`color:` 和 `caret-color` 以外的前景属性不得引用 `--primary` / `--primary-hover`。`caret-color` / `accent-color` 例外，需单独确认。

### P1-3 小程序暗色：TDesign 自带暗色主题未加载，组件令牌会退回浅色

- **Spec 位置**：§3 F5「追加 `@media (prefers-color-scheme: dark)` 完整覆写」，R5
- **源码证据**：
  - `miniprogram_npm/tdesign-miniprogram/common/style/index.wxss` 不引入 `theme/_dark.wxss`。组件样式（如 `button/button.wxss`）里没有任何 `prefers-color-scheme`。
  - 页面实际用到 6 个组件：`t-button` / `t-swiper` / `t-icon` / `t-collapse` / `t-collapse-panel` / `t-skeleton`。组件样式里引用的 `--td-*` 变量数分别为：button 139、collapse-panel 29、swiper-nav 19、skeleton 16、loading 13。这些变量大多带浅色字面兜底，例如 `var(--td-bg-color-container, …#fff)`。
  - `app.wxss` 目前只定义了 27 个 `--td-*`。Spec 列出的覆写清单（`--td-bg-color-page`、`--td-collapse-*` 等）远少于组件实际依赖的数量。
- **影响**：开启 `darkmode: true` 后，次级或描边按钮、折叠面板、骨架屏、轮播指示器很可能在暗底上显示白底或浅灰块。离线测试抓不到这类问题。
- **建议**：
  - Spec 增加一步：按实际使用的组件枚举 `var(--td-xxx, <字面兜底>)`，逐个决定在 `app.wxss` 暗色块里覆写还是确认不受影响。
  - 新增 node 测试：读取这 6 个组件（含其子组件 loading、swiper-nav）的 wxss，断言每个带浅色兜底的 `--td-*` 要么在暗色 `page{}` 中声明，要么在白名单里写明理由。这条测试有判别力。
  - 验收保留 R5 的微信开发者工具暗色切换人工复核。

---

## 3. P2 / P3

| # | 级别 | 位置 | 问题 | 建议 |
| :--- | :--- | :--- | :--- | :--- |
| P2-1 | P2 | §3 F3 `--vial-sheen`；`app.css:498–504` | 浅色 48% 白光层盖住分段上半部。按 `--success #0f5c3a` 算，上沿约变成 `#82aa98`，相对槽底 `#e8e2d7` 只有约 2.2:1，状态图形低于 3:1 | 光泽降到 ≤ 20%，或只留顶部 1px 高光线。浏览器测试取分段上 1/4 的像素色做对比度断言 |
| P2-2 | P2 | §2.3；`app.css:813` `.cal-m__fill--future` | 手机迷你轨里「未来」填充 `--cal-future-line` 相对轨道 `--surface-well` 只有 1.12（浅）/ 1.11（暗），几乎看不见。浅色是既有问题，Spec 把它原样复制到了暗色 | 填充改用 `--cal-current-line` 同档色，或者加 1px `--line-strong` 内描边；让 `test_calendar_grid_lines…` 也覆盖迷你轨 |
| P2-3 | P2 | 交接硬核点 1；`templates/properties/detail.html:49,77`、`knowledge/detail.html:47`、`admin/miniprogram_home.html:31` | `.image-list img`（`app.css:218`）底色是 `--surface-sunk`，暗色下为 `#181614`。欢迎图、横幅、配图如果是透明 PNG 且内容偏深，会几乎看不见 | 新增 `--media-well`：两态都取中性浅灰（如 `#ece8e1`），只用于图片预览底 |
| P2-4 | P2 | §2.2 / §3 F4 | 「四档阻尼弹簧」实际只定义了 3 条 `--ease-spring-*`，档位名和数量对不上 | 见第 4 节建议 M1。终版统一为「强度档」与「曲线」两个概念 |
| P2-5 | P2 | §3 F4 | 弹簧同时用于 `color` / `background-color` / `box-shadow` / 折叠箭头旋转。颜色插值的过冲会被截断，等于白费；箭头 180° 旋转按 2% 过冲约晃 3.6°，运营界面里像抖动 | 弹簧只用于 `transform` 和 `opacity`；颜色、边框、阴影和箭头旋转保持 `--ease-out`（见 M2） |
| P2-6 | P2 | §3 F4；`app.css:657–660` | 减动效规则只覆盖 `*, *::before, *::after`，不含 `::backdrop`。若后续给遮罩加动画，减动效下不会关闭 | 减动效选择器补上 `::backdrop`，或者在 Spec 中写明遮罩不做动画 |
| P3-1 | P3 | §0 B1 | 浅色 `#ebe6dc` / `#f6f4ee` 与参考 README「暖白画册」两个值完全相同；暗色与「暖炭玄岩」`#10100F` / `#141413` 只差 1 个色阶；Spec 中文名也直接沿用。色值是事实，不构成代码拷贝，但「全部从零原创」的表述不准确 | B1 改写为「色值参考公开 README 配色表（事实数据，非代码）；CSS 与图标全部自写」。中文名建议换成自有命名；`DESIGN.md` 注明灵感来源 |
| P3-2 | P3 | §2.1 `--primary` | 浅色 `#c24322` → `#be441c` 肉眼几乎看不出差别，却连带要改小程序 10 阶 `--td-primary-color-*`、重绘 6 枚 PNG、更新测试硬编码 | 用户裁决：A. 保持 `#c24322`（已达 5.2:1，推荐，变更面最小）；B. 按 Spec 换 |
| P3-3 | P3 | §3 F3 `.cal__date--today` | 今日底标改用 `--focus`，焦点色被拿去做装饰，键盘焦点的辨识度会被稀释 | 继续用 `--primary` 或 `--link` |
| P3-4 | P3 | §2.1 `color-scheme` | 浅色写 `light dark`、暗色媒体里再写 `dark`，重复了 | `:root { color-scheme: light dark; }` 一处就够 |
| P3-5 | P3 | §3 F5 PNG | 「基于现有几何路径重绘」：仓库里只有 PNG，没有矢量源，也没有 Pillow 依赖 | 写明生成方式：读取现有 PNG 的 alpha 通道，按新色值重新填色。用一次性脚本完成，不进依赖，也不入库。测试断言暗色图标的不透明像素颜色等于 `theme.json` 里的对应值 |
| P3-6 | P3 | §3 F5 `t-icon` | `icon.js::setIconStyle` 会把 `color` 原样写进内联 `style`，`.t-icon` 自身不设 `color`。所以 `color="var(--yumi-primary)"` 和「去掉属性、由父元素设 `color` 继承」两种做法都可行 | 推荐后者：WXML 不再携带任何样式值，图标颜色由 `.quick-nav__icon` 等语义类决定。最终仍以开发者工具验证为准 |

---

## 4. 参考 obsidian-prism 的动效与视觉优化建议

参考项目的公开思路有几条值得借鉴：弹簧由物理参数求解，而不是手填采样点；强度分档（off / subtle / standard / ultra），减动效直接落到 off；空闲时 0fps；语义余晖只在需要注意时出现；去掉常驻的大面积 `backdrop-filter`。下面是按 YuMi 运营后台的克制定位做的原创取舍。

| # | 建议 | 理由 | 对 Spec 的改动 |
| :--- | :--- | :--- | :--- |
| M1 | **「四档」改为强度档，不是四条曲线**。只保留两档：`prefers-reduced-motion: reduce` 落到 off（现有规则），默认为 standard。不加手动切换 | 参考项目的「四档」指动效强度，不是曲线数量。用户已拍板不加切换器 | §2.2 删除「四档」说法，改为「2 条弹簧曲线 + 系统减动效」 |
| M2 | **弹簧只给有明显位移的元素**：抽屉 `translateX`、确认弹窗 `translateY + scale`、Toast 入场。按钮按压 1.8% 缩放在 160ms 内肉眼分辨不出弹簧和 ease-out，删掉 `--ease-spring-snappy` | 减少令牌和测试面，把动效预算留给真正能感知的地方 | §2.2 删 snappy；F4 按钮按压保持 `--ease-out` |
| M3 | **出场不用弹簧**：抽屉关闭、遮罩淡出保持现有 `--ease-in`（`app.css:398,402`） | 出场带过冲看起来像被弹回来，参考项目同样是入场弹、出场收 | F4 补一句「出场一律 ease-in，不做过冲」 |
| M4 | **曲线由阻尼比推导并由测试复算**：用标准阻尼振子公式按阻尼比 ζ 采样 ≥ 16 点，时长另行选定（采样用归一化时间）。单测用同一公式复算，断言 `@supports` 块里的 `linear()` 与之一致，防止有人手改单个点 | Spec 现在 6–7 个手填点，没有来源，也不可校验。下面是按公式推出的示例 | §2.2 记录每条曲线的 ζ，并附上测试 |
| M5 | **语义余晖保持「有事才亮」**：`.metric-item--risk` 已按数量条件渲染（`dashboard.html:35,41,47`），符合参考项目「待决策才提示」的原则。注意 `inset 3px` 竖条落在 12px 圆角卡片上会弯成月牙形 | 和 `.first-list__item--urgent` 不加圆角的约束保持一致 | F3：余晖只保留外发光；左侧竖条改用 `border-left: 3px solid`，或者直接删掉（卡片已有危险描边和底色，竖条是第三重强调） |

M4 的示例。两条曲线均按阻尼振子公式推导；推导用的是自己写的一次性脚本，未入库，也不是参考项目的代码：

- `ui`，ζ ≈ 0.79，过冲 1.8%，用于 Toast 和卡片微抬：
  `linear(0, 0.07, 0.224, 0.402, 0.569, 0.71, 0.819, 0.899, 0.952, 0.986, 1.005, 1.015, 1.018, 1.017, 1.014, 1.011, 1.008, 1.006, 1.004, 1)`
- `panel`，ζ ≈ 0.93，无过冲，用于抽屉和弹窗：
  `linear(0, 0.047, 0.154, 0.283, 0.413, 0.533, 0.636, 0.722, 0.791, 0.846, 0.888, 0.92, 0.944, 0.962, 0.974, 0.983, 0.989, 0.993, 0.996, 1)`
- 时长建议：`ui` 220ms，`panel` 260ms。现有 `--motion-panel` 为 180ms，抽屉宽 292px，略长一些才能让临界阻尼的收尾被看见。

视觉方面，Spec 已经吸收了参考项目最有价值的几点：侧栏比画布更深以拉开景深、控制台凹槽、0fps、不用常驻 `backdrop-filter`。建议不要再引入它的指针近场边缘高光（canvas 方案），因为认证页不加载脚本，运营后台也不需要。

---

## 5. 交接 6 个硬核点逐条回复

1. **`--surface` 隐式依赖**：Spec 已拆出 3 处，通读全表后没有发现第 4 处把 `--surface` 当前景色。真正的漏网是 P1-2（`--primary` / `--primary-hover` 当前景）和 P2-3（图片预览底）。阴影方向在暗色下没有问题。
2. **日历派生**：浅、暗两组共 12 个回退值与 `_srgb_mix` 逐一复算，**全部一致**。`current-ink` 对 `current-bg` 浅 8.23、暗 6.29；网格线浅 1.50、暗 1.33。但回退机制本身无效（P1-1），测试升级应以 `@supports not` 块为验算对象。另外，`_room_card_block` 的结束锚点是 `@media (max-width: 520px) {\n  .room-operation-card { padding`，回退块必须放在这个锚点之前，否则会漏出禁区检查。
3. **暗色 `--primary` 当文字**：存在，见 P1-2 的 4 处。`.cal__date--today` 文字走 `--link`，没有问题；`.kb-*` 都走 `--link` 或 scope 令牌，没有问题。
4. **小程序硬编码**：16 处 `t-icon` 计数准确。`pages/`、`templates/`、`utils/`、`app.js` 和页面 `.json` 里没有 `confirmColor`、`setNavigationBarColor`、`navigationBarBackgroundColor` 等其他硬编码。更大的风险是 P1-3。`t-icon` 的着色方案见 P3-6。
5. **弹窗动效与测试**：`test_admin_interactions.py::test_motion_feedback_and_reduced_motion` 断言 `.page-content` / `.alert` 的动画名，并在点击后 220ms 读取折叠箭头的旋转值。若按 P2-5 把箭头改成弹簧，220ms 时可能还在过冲区，但断言是「≠ 关闭态」，仍会通过。弹窗关键帧不阻塞 `showModal` 的焦点移动；Playwright 点击前会等元素稳定。建议新增：暗色加减动效上下文里，`.confirm-dialog[open]` 的 `animationName == "none"`。
6. **D1 / D2 与边界**：
   - D1 同意推荐 `#ffffff`：TabBar 是住客端最常见的表面，跟卡片同白更轻。
   - D2 同意保留光晕：7–10% 静态渐变不影响可读性，也没有性能负担。
   - 边界：改动文件与 `scripts/release/reply_gate.sh::REPLY_PATHS`（`reply_gate.sh:37–62`）**交集为空**；B2、B4 守得住；B1 见 P3-1。另外，Spec 只说 Tauri「不在范围」，但 Tauri 外壳如果加载的是同一套 Web 后台，会自动跟随系统暗色。仓库里没有检索到 Tauri 配置，请向用户确认外壳加载的是哪个页面。

---

## 6. 需要用户裁决

1. **P1-1 降级口径**：A. 用 `@supports` 修复，保留 Safari 15.4–16.1 降级（推荐）；B. 放弃这些版本，删除静态回退。
2. **P3-2 浅色主色**：A. 保持 `#c24322`（推荐）；B. 换成 `#be441c`。
3. **M2 按钮按压是否保留弹簧**：A. 删掉 snappy，按压保持 ease-out（推荐）；B. 保留。
4. **工作区 `app.css` 3 行未提交改动**：A. 实施前先单独提交（推荐）；B. 随本次一起提交，并在发布记录中说明。

## 7. 本次验证范围

- 已做：用 Python 按 WCAG 公式复算 Spec 的 24 组关键色对和 12 个日历派生值；用 Playwright Chromium 实测自定义属性回退；静态核查 TDesign 1.17.0 的样式引入链和 `t-icon` 实现；对照 `REPLY_PATHS` 核查边界。
- 未做：没有在任何不支持 `color-mix` / `linear()` 的真实浏览器上跑（用无效函数模拟了同一失效路径）；没有在微信开发者工具里验证暗色渲染（离线不可替代）；没有跑项目测试（本次未改代码）。
