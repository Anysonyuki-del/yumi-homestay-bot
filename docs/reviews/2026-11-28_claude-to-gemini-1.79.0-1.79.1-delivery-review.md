# v1.79.0 / v1.79.1 交付独立只读审查报告（Claude → Gemini）

- **审查对象**：`d0e71a3..913c324`，共 5 个提交（`4e8f956` v1.79.0、`5c5aba7`、`0409a76`、`cada999` v1.79.1、`913c324`），36 个文件，+2005/-660。
- **依据**：`docs/reviews/2026-11-28_gemini-to-claude-1.79.0-1.79.1-delivery-review-handoff.md` 的「审查规则」与第二节列出的 6 个风险点。
- **边界**：全程只读。没有改代码、没有提交、没有登录生产、没有调用 DeepSeek / Hostex / 企业微信。所有浏览器实测都在本机进行：用临时 SQLite + 同源临时服务，或用 `set_content` 直接加载仓库里的 `app.css`。临时脚本放在会话草稿目录，不在仓库里。
- **基线绑定**：工作区 `HEAD=913c324`。交接文档第四节 SHA-256 表里的 15 个文件已用 `shasum -a 256 -c` 全部核对通过。

## 一、结论

v1.79.0 / v1.79.1 整体可以接受：没有会导致数据、安全或功能出错的 P1。6 个风险点里，双路暗色回退、首帧时序、CSP 与存储降级、320px 触屏布局、`@supports` 语义、小程序 TDesign 令牌都核实成立，部署证据和 CI 也对得上。

需要处理的是 **1 项已实测确认的 P2**：外观切换键的悬停和按下态被主按钮样式劫持。此外有若干 P3：一处无障碍文案过期、几处测试盲区、发布文档的几处数字或措辞不准。

| 编号 | 级别 | 标题 | 位置 |
| --- | --- | --- | --- |
| F1 | **P2** | 外观切换键悬停/按下时变成主按钮（钢琴黑曜键），浅色下还有约 180ms 的低对比闪烁 | `app.css` L546–547 对 L824–825、L1512 |
| F2 | P3 | 手动模式下系统明暗变化后，按钮的 `aria-label` / `title` 提示变成过期信息 | `theme.js` `onSystemChange` L124–127 |
| F3 | P3 | 三态循环每一轮都有一次点击看起来没有变化 | `theme.js` `nextPreference` L43–48 |
| F4 | P3 | 测试盲区：手抄的暗色伪元素/光晕块、切换键触控尺寸、悬停态与渐变背景对比度没有测试覆盖 | `tests/unit/test_admin_assets.py`、`tests/browser/test_admin_theme.py` |
| F5 | P3 / 疑问 | 切换键图标和文案在 DOMContentLoaded 之前固定显示「跟随系统」 | `theme.js` L96–115、两套外壳模板 |
| F6 | P3 / 疑问 | 发布文档里的数字与措辞有偏差（11 passed、3700 与 138 重复计数、「新增 320px」、CI 状态未补记、错字） | `docs/releases/1.79.0.md`、`1.79.1.md`、`CHANGELOG.md` |
| F7 | 疑问 | 小程序暗色描边式主按钮的边框对比度只有 2.42:1；真机/开发者工具尚未目视验证 | `clients/wechat-miniprogram/app.wxss` L223、L230 |
| F8 | 既有（不在本范围） | `aoci.code.txt` 段落头里有本机绝对路径 | `aoci.code.txt` 段落头 |

## 二、发现详情

### F1【P2，已实测确认】外观切换键悬停/按下态被主按钮规则覆盖

- **位置**：
  - `src/homestay_bot/static/app.css` L546 `button:hover:not(:disabled), .button:hover:not(:disabled)`，特异度 (0,2,1)。
  - L547 `button:active:not(:disabled), .button:active:not(:disabled)`，特异度 (0,2,1)。
  - 被覆盖的是 L824 `.appearance-toggle:hover`、L825 `.appearance-toggle:active`，以及 L1512 晚期覆写 `.table-action:hover, .topbar__account:hover, .appearance-toggle:hover`，三者特异度都是 (0,2,0)。
- **复现**：鼠标悬停或按下后台顶栏的 `.topbar__theme`，或认证页的 `.auth-theme`，在浅色和暗色下都会出现。在 Chromium 里等过渡结束（600ms）后读取计算样式：

  | 状态 | 浅色：背景 / 背景图 / 文字 | 暗色：背景 / 背景图 / 文字 |
  | --- | --- | --- |
  | 静止 | `#fff` / 白→象牙渐变 / `rgb(37,39,46)` | `rgba(255,255,255,.03)` / none / `rgb(200,204,216)` |
  | 悬停 | `rgb(41,43,53)` / **`#323542→#1d1f28` 主按钮悬停渐变** / **`#fff`** | `rgb(37,40,54)` / **`#2d3142→#20232f`** / `rgb(244,246,252)` |
  | 按下 | **`rgb(12,13,16)`** / none / `#fff`，`scale(.985)`（不是设计的 `.97`） | `rgb(20,22,29)` / none，`scale(.985)` |

  对照组 `a.topbar__account` 是 `<a>`，不受 `button:` 规则影响：悬停后保持幽灵胶囊（浅色 `#f4f3ef` 底、`#121316` 字）。所以同一个 `.topbar__actions` 里的两枚胶囊，悬停时一枚是浅色幽灵、一枚是钢琴黑，视觉上不一致。
- **附带的瞬时对比度问题**：`background-image` 不参与过渡，悬停瞬间就切到深色渐变；`color` 则在 `--motion-fast: 180ms` 内从 `rgb(37,39,46)` 渐变到白。浅色模式下悬停的前约 180ms 里，深字落在深色渐变上，实测对比度只有 **1.10–1.22:1**，文字和图标几乎看不见。
- **原因**：元素选择器 `button` 加上 `:hover` 和 `:not(:disabled)`，特异度高于单类加伪类。项目在 `.button--secondary` 上已经用 `button.button--secondary:hover:not(:disabled)` 规避过同一个坑，但这次新加的 `.appearance-toggle` 没有沿用。
- **建议**：把 L824–825 和 L1512 的切换键选择器改成 `button.appearance-toggle:hover:not(:disabled)` / `button.appearance-toggle:active:not(:disabled)`，悬停时显式写 `background-image: none`，按下时恢复 `.97`。同时在 `tests/browser/test_admin_theme.py` 补一条有判别力的断言：悬停切换键后 `background-image` 应为 `none`，背景应与 `.topbar__account:hover` 一致。
- **为什么现有测试没发现**：
  - `test_appearance_toggle_cycles_persists_and_renders_on_first_frame` 只点击，不悬停；
  - `test_text_and_action_contrast_uses_actual_computed_surfaces` 的取样不含 `button` 元素和悬停态；
  - 即使取样了，`_CONTRAST_JS.effectiveBg` 只读 `backgroundColor`，静止态的白底仍会被判为合格（见 F4-c）。

### F2【P3】手动模式下系统明暗变化后，提示文案变成过期信息

- **位置**：`src/homestay_bot/static/theme.js` L124–127 的 `onSystemChange`，只在 `currentMode === "auto"` 时调用 `syncButtons`。但 `nextPreference`（L43–48）在 `dark` / `light` 模式下的结果同样取决于 `systemScheme()`。
- **复现**：
  1. 系统为浅色，点击一次切到 `dark`。提示是「外观：深色，点击切换为浅色」。
  2. 不刷新页面，把系统切到暗色。提示仍是「切换为浅色」。
  3. 这时点击，实际会进入 `auto`：画面仍是暗色，只是图标从月亮变成显示器。
  
  读屏用户和悬停 `title` 的用户都会得到错误的预期。
- **建议**：`onSystemChange` 改成无条件调用 `syncButtons(currentMode)`。

### F3【P3，体验建议】三态循环里有一次「视觉没有变化」的点击

- **位置**：`theme.js` 的 `nextPreference`。
- **说明**：
  - 系统浅色时，循环是 auto(浅) → dark → light → auto(浅)。
  - 系统暗色时，循环是 auto(暗) → light → dark → auto(暗)。
  - 所以每一轮的最后一下（从「与系统同色的手动态」回到 auto），页面颜色不变，只有图标和文案变化。叠加 F2 的场景（中途改系统），用户可能连续两次点击才看到画面变化。
- 这是已确认 Spec 的三态语义，不算缺陷。如果要优化，可以只在过渡时给轻提示，或在提示文案里写明「下一步：跟随系统（当前同为深色）」。是否改由用户决定。

### F4【P3】测试盲区

- **a. 双路同步守卫只比较令牌**：`tests/unit/test_admin_assets.py::test_manual_and_system_dark_blocks_stay_in_sync` 只比较 `_dark_root_block` 与 `_manual_dark_root_block` 的 `--*` 自定义属性，以及 `_calendar_fallbacks_all`。`app.css` 里另外两段手抄的双路块没有覆盖：
  - L1367–1390 `body::after` 微噪点；
  - L1518–1542 状态点光晕与 `.metric-item--risk strong` 的 `text-shadow`。
  
  本次实测这两段在四种组合下表现一致（见第三节第 1 点），当前没有漂移，但以后改动时没有防护。建议把它们改为读令牌，例如在两个令牌块里声明 `--noise-opacity`、`--status-glow-*`，让伪元素和光晕规则只写一次；或者把这两段也纳入同步断言。
- **b. 切换键的触控尺寸没有测试**：
  - `test_hallmark_touch_navigation_targets_remain_reachable` 只在 390 / 768 宽度量 `.topbar__account`，没有 `.topbar__theme` / `.auth-theme`。
  - `test_theme_pages_fit_scrollbar_width_without_overflow` 在 320px 下不启用 `has_touch`，所以 `any-pointer: coarse` 的 44×44 分支在最窄宽度上没有被测到。
  
  本次手工实测结果是合格的（见第三节第 3 点），但建议把 `.topbar__theme` / `.auth-theme` 加进触控尺寸断言。
- **c. `_CONTRAST_JS` 的结构性盲区**（`tests/browser/test_admin_theme.py` L26–60）：`effectiveBg` 沿 DOM 祖先链只合成 `backgroundColor`，忽略以下几类：
  - `background-image` 渐变（F1 正是这一类）；
  - `backdrop-filter` 实际透出的、滚动到下层的内容；顶栏 `--float-bg` 在浅色为 86% 不透明、暗色为 74%；
  - `::before` / `::after` 图层，以及 `opacity`；
  - 绝对定位导致的绘制顺序与 DOM 祖先不一致。
  
  对目前取样的元素（`.eyebrow`、`.muted`、`.badge`、`a.button`、`.topbar__account`），影响有限：`a.button` 的 `background-color`（`#18191f` / `#1c1e26`）与渐变两端相差很小，静止态的切换键底色与渐变也接近。所以 A2-Pro 对此前 P1「RGBA alpha 盲区」的修复成立。但取样不含 `button` 元素和任何悬停/按下态，这些状态的对比度目前没有自动化证据。

### F5【P3 / 疑问】DOMContentLoaded 之前，切换键固定显示「跟随系统」

- **位置**：
  - `theme.js` L96–97 在 `<head>` 执行 `applyAppearance`，这时按钮还没有被解析，`syncButtons` 找不到任何按钮。
  - 按钮要到 L100–115 的 `bindControls`（DOMContentLoaded）才同步。
  - `layouts/admin.html` L74–78、`layouts/auth.html` L17–21 由服务端固定渲染 `data-appearance-mode="auto"`、显示器图标和「跟随系统」文案。
- **影响**：已保存 `dark` / `light` 偏好时，页面底色在首帧就正确（有测试保证），但如果浏览器在 DOMContentLoaded 之前做了渐进绘制（长后台页、慢网），切换键可能短暂显示显示器图标和「跟随系统」。本次没有复现出可见的闪烁，标为疑问。
- **建议（零脚本、不放宽 CSP）**：图标显隐改由根属性驱动，例如 `:root[data-appearance="dark"] [data-theme-icon]:not([data-theme-icon="dark"]) { display: none; }`，不再依赖 `hidden`。文案可以同样改为三段 `<span>` 由 CSS 切换。

### F6【P3 / 疑问】发布文档的数字与措辞

| # | 位置 | 问题 | 证据 |
| --- | --- | --- | --- |
| a | `docs/releases/1.79.0.md` 部署记录「改版本号后 `tests/unit/test_release_scripts.py` 11 passed」 | **疑问**：该文件当前只收集到 6 个用例，且最后一次修改是 `894fd5b`（本范围之前），11 无法复现 | `pytest --collect-only -q tests/unit/test_release_scripts.py` → 6 |
| b | `docs/releases/1.79.1.md` 部署门禁「138 passed（release + browser）＋ 其余离线全量 3700 passed」 | 3700 来自 `--ignore=tests/browser`，已经包含那 6 个发布脚本用例，「其余」重复计数了 6 项。两个数字本身都准确 | collect：browser 132 + release 6 = 138；非 browser 3799 = 3700 + 99 skipped |
| c | `CHANGELOG.md` [1.79.1]「浏览器回归新增……320px 零溢出验证」 | 1.79.1 没有新增 320px 用例。`4e8f956..0409a76` 对该文件只新增了切换循环用例，并把「按钮数为 0」改成可见断言；320–1440px 溢出用例在 1.79.0 已经存在 | `git diff 4e8f956..0409a76 -- tests/browser/test_admin_theme.py` |
| d | 两份发布记录都写 CI「核对完成时仍在运行中（`in_progress`）」 | 现在可以补记最终结论：交接文档列出的 6 个 run 经 `gh run view` 核对，全部 `completed / success`，head 分别对应 `4e8f956`×2、`5c5aba7`、`cada999`×2、`913c324` | `gh run view <id> --json status,conclusion` |
| e | 两份发布记录首行「详见证件与核对记录见文末」 | 错字（应为「证据」）且「详见……见」重复 | — |
| f | `1.79.1.md` 正文保留了「待本轮全量结果填入」的占位 | 首行已经声明「保留原样」，但读者容易误以为没有执行，可以在占位处加「（已于部署补记填入）」 | — |

**已核实一致的项**：
- 提交 ID 与标签对应；
- 1.79.0 浏览器 130 + 1.79.1 新增参数化 2 例 = 132；
- 非 browser 用例：1.79.0 的 3699 passed + 新同步守卫 1 = 3700 passed，加 99 skipped = 3799；
- `test_admin_assets` 25、`test_admin_theme` 23、小程序 46 均与实跑一致；
- 「4 处暗色规则」对应 L350 令牌、L1268 日历、L1380 噪点、L1534–1542 光晕，共 4 个双路区域；
- 改动清单与 `git diff --stat` 一致；
- 回滚说明成立：无迁移、无后端接口变化。

**敏感信息扫描**：对范围内 23 个非图片文件和交接文档，按 IPv4、`http(s)://`、本机路径、常见域名后缀、`root@` / `ssh -` / `sk-` 做了检索。新增内容里**没有**服务器 IP、生产域名、密钥或口令。命中项只有：测试里的 `127.0.0.1`、CHANGELOG 旧条目里作为反例的 `evil.com`、Spec 里的一个公开引用 URL，以及 F8 的既有路径。

### F7【疑问】小程序暗色描边式主按钮的边框对比度

- **核实成立的部分**：
  - TDesign `1.17.0` 的 `button/button.wxss` 中，`.t-button--primary` 读取 `--td-button-primary-bg-color` / `-border-color` / `-color`。
  - `app.wxss` L221–236 在 `@media (prefers-color-scheme: dark) { page { … } }` 里声明了这些变量，自定义属性能继承进组件，所以静态上会生效。
  - 实际在用的实心主按钮：`pages/services/category.wxml` L16、`pages/rooms/detail.wxml` L29；描边主按钮：`pages/me/index.wxml` L15 / L18。
  - 文字对比度：`#f4f6fc` / `#1f222c` = 14.7:1，按下态 `#15171f` 为 16.6:1。
  - 浅色没有声明 `--td-button-primary-bg-color`，会回退到 `--td-brand-color: #18191f`，符合预期。
  - `theme.json` 与 `app.wxss` 令牌一一对应：浅色 nav/bg 为 `#f5f4f0`，tab 选中 `#18191f`，tab 常态 `#5a5e6a`；暗色 nav/bg 为 `#08080b`，tab 选中 `#b8cdf5`，tab 常态 `#9093a3`。`tabBgColor` 暗色 `#050507` 是单独取值，不在令牌里，属有意为之。
- **疑问**：描边式主按钮（「微信登录」）在暗色画布上主要靠 `#464e66` 描边识别，该描边相对 `#08080b` 只有 **2.42:1**，低于 WCAG 1.4.11 对非文本边界的 3:1。按钮文字 `#b8cdf5` 达到 12.5:1，可以识别控件，所以严格来说不构成违规，但观感偏弱。
- **未覆盖**：没有在微信开发者工具或真机目视验证。发布记录已如实列为未执行。

### F8【既有问题，不在本范围】`aoci.code.txt` 段落头含本机绝对路径

`aoci.code.txt` 的段落头（例如第 3、20、22、25 行）含 `/Volumes/…`、`/Users/…` 本机路径。`d0e71a3` 时已有 77 处，本范围只更新了条目正文，没有新增路径。这是 AOCI 索引格式的既有问题，不由 v1.79.x 引入，仅记录备查。

## 三、6 个风险点逐项结论

1. **双路暗色 CSS**：成立。
   - 特异度：基础 `:root` 为 (0,1,0)；系统暗色 `:root:not([data-appearance="light"])` 与手动 `:root[data-appearance="dark"]` 都是 (0,2,0)；`:root[data-appearance="light"]` 只设 `color-scheme: light`。全文没有残留的单路 `prefers-color-scheme`。
   - Chromium 实测四种组合：
     - 系统暗 + 手动浅：`color-scheme: light`，页面 `#f5f4f0`，`body::after` 背景图为 `none`，状态点回到浅色光环，风险数字没有 `text-shadow`，**完全回退**。
     - 系统浅 + 手动深：噪点（`opacity .028`）、`0 0 8px` 光晕、`0 0 16px` 风险发光全部生效。
     - 两种 auto 组合与系统一致。
   - 日历 `@supports not (color-mix)` 回退：Chromium 无法走到这条分支，只做了静态审阅，两条暗色路径与同步测试一致。
   - 同步测试的盲区见 F4-a。
2. **`theme.js`**：首帧时序、安全和降级都成立，有两处小问题。
   - 首帧：`layouts/auth.html` 的 `theme-color` meta 在 L9–10、`theme.js` 在 L12；`layouts/admin.html` 的 meta 在 L12–13、`theme.js` 在 L15。两处都是 meta 先于脚本、脚本早于 `app.css`，阻塞执行，能在首帧前写入 `data-appearance` 和 `theme-ready`。现有首帧测试会在 `h1` 出现的那一帧取背景色，可以证明。
   - 安全：不接触表单、Cookie、网络，没有内联脚本，CSP `script-src 'self'` 不变；`localStorage` 读写都包在 try/catch 里。
   - `storage` 事件处理了 `key === null`（`clear()`）；`matchMedia` 有 `addListener` 旧接口兜底。
   - 问题：中途改系统后提示过期（F2）；三态里有一次视觉无变化的点击（F3）；按钮首帧文案（F5）。
   - `syncThemeColorMeta`：在手动模式下把两条 meta 改成同一色，思路正确，测试也断言了属性值。但 Safari 26 起据报道不再用 `theme-color` 着色标签栏，Android Chrome 会读动态 meta，这两点都**无法在本地验证（疑问）**，需要真机确认。
   - 三份色值重复（`theme.js` 的 `THEME_COLOR`、模板 meta、CSS `--page`）：切换测试会断言切换后和恢复 auto 后的 meta 值，首帧测试会断言 `--page`，所以漂移会被测试拦下。
3. **320px 与 44×44 触控**：成立。
   - 实测条件：Chromium，真实滚动条，`has_touch=True`，浅/暗 × 高 840/480。可用宽度 305px，`scrollWidth == clientWidth == 305`，没有横向溢出。
   - 后台顶栏：菜单键 44×44（左 12），切换键 44×44（197–241），账号键 44×44（249–293），中间品牌文字不截断。
   - 认证页：`.auth-toolbar` 宽 273，切换胶囊 102×44，文案「跟随系统」常显；卡片 273×388。
   - 高 480 时，文档高度正好 480 不滚动。`body:not(.admin-body)` 是 `min-height` 网格，内容更高时会把网格撑高，不会出现负偏移裁切。强制改密、账号安全、503 页共用 `auth.html` 的同一结构，按同一机制成立，但没有逐页实测。
   - 测试盲区见 F4-b。
4. **A2-Pro + 1A 对此前 P1/P2/P3 的闭合度**：主体闭合。
   - RGBA alpha 合成盲区已修，暗色合成对比度测试实跑通过（`test_admin_theme.py` 23 passed）。
   - 棱镜 `::before` 已整体包进 `@supports`，宿主都已定位：`.card` 为 `position: relative`；`:where(.panel, .metric-item, .room-operation-card)` 在 L1409；模态 `<dialog>` 由浏览器默认样式给出 `position: fixed`。
   - `@supports ((mask-composite: exclude) or (-webkit-mask-composite: xor))` 在各引擎下的语义：
     - 旧 WebKit（Safari < 15.4）与 Chrome < 120 只认带前缀的旧关键字 `xor`，会进入该块，并正确使用 `-webkit-mask-clip: content-box, border-box` + `xor`；
     - 新引擎两者都认，标准 `mask-composite: exclude` 写在后面，由层叠决定；
     - Firefox 走标准写法；
     - 都不支持时整块跳过，退回实体 border。
     
     理论上的残余风险：某个引擎认 `-webkit-mask-composite: xor`，却不支持多层 `-webkit-mask-image`，这时整卡会被 `--rim` 覆盖。主流引擎里没有已知实例，没有真机验证（疑问）。
   - 剩余缺口：`_CONTRAST_JS` 对渐变、backdrop、伪元素的盲区（F4-c），以及没有采样的悬停态（F1）。
5. **小程序**：令牌与组件消费关系成立，详见 F7。
6. **发布文档与敏感信息**：主体一致，CI 已全绿；数字和措辞偏差见 F6；没有发现新增的敏感信息泄露；既有路径问题见 F8。

## 四、本次实际执行的验证

- `shasum -a 256 -c`：交接文档第四节的 15 个指纹全部 OK。
- `.venv/bin/pytest -q tests/unit/test_admin_assets.py tests/browser/test_admin_theme.py`：**48 passed**（25 + 23）。
- `node --test tests/miniprogram/*.test.js`：**46 passed**。
- `pytest --collect-only`：browser 132、`test_release_scripts.py` 6、`--ignore=tests/browser` 3799，用来核对发布文档里的数字。
- `gh run view`（只读）：6 个 CI run 全部 `completed / success`。
- Chromium 临时脚本，都放在会话草稿目录，不进仓库：
  - 切换键静止/悬停/按下的计算样式，含过渡结束前后两次取样（F1）；
  - 系统 × 手动四种组合下噪点、光晕、`color-scheme` 的回退（第三节第 1 点）；
  - 320px 触屏下后台与登录页的布局和溢出，含 840/480 两种高度，并截图目视（第三节第 3 点）。
- 静态审阅：`app.css` 双路块与 `@supports` 块、`theme.js` 全文、两套外壳模板、`app.wxss` 暗色块、TDesign 1.17.0 `button.wxss` 变量消费、`theme.json`、三份发布文档与 `aoci.code.txt` 的差异。

## 五、未覆盖

- 真机 iOS Safari、Android Chrome / WebView、Windows WebView2：`theme-color` 动态生效、旧引擎的 `mask` 表现、触屏悬停残留。
- Firefox 实机渲染：本次只用了 Chromium。
- 微信开发者工具和真机上的小程序暗色目视效果。
- 生产登录后的页面目视：按边界没有登录生产，部署记录里也列为未验证。
- 强制改密、账号安全、503 三页没有逐页实测窄屏，靠共用外壳结构推断。
- 全仓离线全量：本次是只读审查，按风险驱动只跑了相关测试，没有重跑全量。
