# 黑曜钴蓝配色与玻璃光效（拟发 1.79.0）｜Claude → Gemini 交付审查交接

2026-11-28 · 基线 `main` = `origin/main` = `d0e71a3`（生产 `v1.78.0` = `8daf96a`）· 全部改动仍在工作区，**未升版本、未提交、未部署**

## 一句话

1.78.0「暖纸光学 × 暖炭光学」上线后，用户看生产暗色的反馈是「光效呢质感呢」「这个颜色实在太太太丑了」。本轮做了三件事：

1. 在对比稿里由用户拍板，把 Web 后台、认证页和小程序整体换成「黑曜钴蓝」（暗）配「白昼冷晶」（浅）；
2. 加了一层肉眼可见的光学层：光晕、点阵、棱镜描边、发光；
3. 用户看图后又说「质感太差」，于是把卡片、选项框、页签改成半透明磨砂玻璃，光效再加强一档。

用户对当前版本的评价是 **「一般」**，所以请把审查重点放在**质感为什么还不够、具体该怎么改**，正确性与回归风险其次。

## 审查规则

- **只读审查**：不改代码、不提交。意见写成报告交给用户转回，建议路径 `docs/reviews/2026-11-28_gemini-to-claude-obsidian-cobalt-glass-delivery-review.md`。
- **不碰生产**：不登录生产、不提交后台表单，不调用真实 DeepSeek / Hostex / 企业微信。
- **写法**：每条发现写明文件路径、选择器或令牌名、行号、复现条件和严重程度（P1 会出错 / P2 体验或维护问题 / P3 建议），拿不准的标「疑问」。
- **视觉建议要能落地**：给出具体的令牌数值、图层顺序或选择器，不要只写「更有质感」。
- **工作区里有三项与本次无关的改动**：`.impeccable/config.json`、`.impeccable/surfaces/`、`PRODUCT.md`。它们是用户自己的，不属于本次交付，请忽略。
- **参考项目边界**：`RMvdesi/obsidian-prism` 是 GPL-3.0。本次只取它的设计理念和公开配色表数值，没有拷贝代码。建议里也请不要直接贴它的源码。

## 一、依据

- **Spec**：`docs/specs/2026-11-28_optical-prism-visual-redesign-spec.md` 的 §9 增补，含 9.1 现状证据、9.2 功能点、9.3 风险与决策、9.4 追加玻璃质感。当前 SHA-256 为 `a984961c47e5c70e6dc93b49dc2ea26053ab1ea2c67507fa98d10ff6064aec96`。
- **设计规范**：`DESIGN.md` v3.1，包括 frontmatter 的 `colors` / `colors-dark`，以及 Elevation & Depth 一节的「可见光效层」。
- **用户裁决**：
  - 配色选 A「黑曜钴蓝」，浅色配「白昼冷晶」。
  - 「直接学习该项目的设计。一定要看得出来」。
  - 「质感太差了，优化卡片 选项框等等的玻璃质感 光效还能再加强一点」。
  - 1.78.0 的其余决定不变：浅色为主、纯 CSS 跟随系统暗色、无切换器、唯一动作色只作填充。

## 二、改动清单

`git diff --stat`（不含 `.impeccable/`）：22 个文件，约 +673 / −494。

| 位置 | 改动 |
| --- | --- |
| `src/homestay_bot/static/app.css` `:root`（约 4–168 行） | 浅色令牌整套换冷调：主色 `#2563eb`、画布 `#f4f7fb`、侧栏 `#e8eef6`、正文 `#0f172a`、危险 `#b42318`。新增光学令牌：`--aura`、`--dot`、`--rim`、`--glass-*`、`--field-*`、`--focus-glow`、`--keycap-*`、`--glow-primary`、`--glow-accent`、`--edge-light`、`--primary-sheen`。删除 `--auth-glow`，由 `--aura` 取代。 |
| 同上，暗色块（170 行起） | 暗色令牌：画布 `#0b0b0e`、卡片 `#141418`、主色填充 `#2f66d6`、前景链接 `#8ab4f8`、危险 `#f0716a`，以及同名光学令牌的暗色值。 |
| 同上，`@supports not (color: color-mix…)`（约 1030 行） | 日历回退块的浅、暗两组 12 个静态色，按新令牌用 sRGB 线性插值重算。单测 `_srgb_mix` 会复算核对。 |
| 同上，`button, .button`（333 行） | 主按钮改成 `background-color` 加 `background-image: var(--primary-sheen)` 顶光，阴影叠 `--glow-primary`；悬停只换底色。`.first-list .button` 与房间卡「去处理」按钮在模板里是 `button--secondary`，被强制改成主色，所以单独补了顶光和发光，避免继承到次级按钮的玻璃内描边。 |
| 同上，`.first-list__item--urgent`（547 行） | 去掉整行 `--danger-soft` 红底，只加 `position: relative`，红条画在文末光学层。 |
| 同上，**文末光学层**（1134–1200 行，新增） | 见第三节。 |
| `templates/layouts/admin.html`、`auth.html` | 浅、暗两条 `theme-color` 改为 `#f4f7fb` / `#0b0b0e`。 |
| `clients/wechat-miniprogram/app.wxss` | `--yumi-*` 与 TDesign 主色阶、暗色语义基座整套换钴蓝和黑曜冷灰，新增页面顶部 `--yumi-aura`，卡片阴影加深。**小程序没有做玻璃。** |
| `clients/wechat-miniprogram/theme.json`，`assets/tab/*.png` 共 12 枚 | 导航与标签栏颜色换新。12 枚图标按 Alpha 通道重新填色：浅色 `#4b5b73` / `#2563eb`，暗色 `#a8a8b6` / `#8ab4f8`。轮廓逐像素与原图一致，所用一次性脚本不入库。 |
| `tests/unit/test_admin_assets.py` | 只改了三处 docstring，把旧色名换成新的，断言没有改。 |
| `tests/browser/test_admin_theme.py` | `PAGE_BACKGROUND`、`THEME_COLOR` 和 `--media-well` 的期望值改为新色。 |
| `tests/miniprogram/content.test.js` | `theme.json` 期望值换新。浅色图标颜色原来写死 `#58524a`，改为从 `theme.json` 读，与暗色一致。 |
| `DESIGN.md`、Spec §9 | 同步记录。 |

## 三、光学层与玻璃的实现（`app.css` 1134 行起）

全部是静态背景、描边和阴影，没有新增动画（0fps），没有图片纹理，没有放宽 CSP，也没有新增依赖。

1. **环境光晕**：`body.admin-body` 和认证页的 `background-image: var(--aura)` 是固定背景，共 5 层：
   - 一道 115° 斜向光束；
   - 右上角主光（暗色时钴蓝 46%）；
   - 中景一团紫光；
   - 左下角青色补光；
   - 底部一层紫色地平线。
2. **点阵**：`.admin-shell` 用 `isolation: isolate`，它的 `::before` 设 `position: fixed; z-index: -1`，画 12px 网格，再用径向 `mask-image` 让点阵只在地平线附近显现。
3. **棱镜描边**：`.panel`、`.metric-item`、`.card`、`.room-operation-card`、`.confirm-dialog` 的 `::before` 写法如下：
   - 定位与形状：`inset: -1px; padding: 1px; border-radius: inherit`；
   - 底色是 `--rim` 140° 渐变；
   - 用 `mask` 的 `content-box` 与 `exclude`（WebKit 写 `xor`）挖空中间，只留边框一圈；
   - 风险卡另用一条红色渐变边；
   - 定位上下文用 `:where(...) { position: relative }`，优先级为 0，已有 `sticky` 的变体不受影响。
4. **玻璃卡片**：
   - 上述卡片的边框色为 `--glass-border`，底色为半透明 `--glass-bg`（浅色白 50%，暗色 `rgb(24 24 30 / 55%)`）；
   - `background-image` 是 `--glass-sheen`：左上角径向高光加顶部线性光泽；
   - `box-shadow` 是 `--glass-edge` 加 `--shadow-card`：`--glass-edge` 由顶边亮线、1px 内描边和底边暗线组成，`--shadow-card` 是深投影，暗色下另加一圈钴蓝外辉；
   - `backdrop-filter: blur(20–22px) saturate(170–180%)`：面板类只在 `>=768px` 开启，`.card` 和 `.confirm-dialog` 一直开启。
5. **沉降玻璃**：面板内嵌卡、`.stay-pane`、`.filter-toolbar`、`.empty-state`、`th`、`.filter-disclosure > summary` 的底色为 `--glass-sunk`。
6. **选项框**：`input`（排除 hidden、checkbox、radio）、`select`、`textarea`，只在 `:not(:disabled)` 时套用：
   - 平时底色 `--field-bg`，内阴影 `--field-inset`，做成往里凹的槽；
   - 聚焦时，`--field-inset`、3px `--focus-ring` 和 `--focus-glow` 三者叠加。
7. **页签与键帽**：
   - `.tab-nav` 底色为 `--glass-well`；
   - 当前页签和侧栏当前项：`--keycap-sheen` 加 `--keycap-glow`，即 1px 主色描边加主色投光；
   - `.button--secondary` 是玻璃键帽，悬停时边框换成 `--link`，再加柔光。
8. **其他光**：
   - 顶栏：底色保持实色，底边用 1px 渐变发线；
   - 侧栏：顶部一团冷光，右缘一条竖向光线 `--edge-light`；
   - 侧栏当前项的指示条和品牌标：发光；
   - 状态点：暗色下按各自语义色外发光，浅色下改成柔底光圈，因为深色外发光在浅底上会发脏；
   - 风险数字：暗色下加 `text-shadow`；
   - 紧急行：左侧 2px 发光红条。

## 四、刻意的约束与取舍

- **手机端不开背景模糊**：`backdrop-filter` 会让元素成为 `position: fixed` 子元素的包含块。任务页、客户详情、待我关注这三页，手机端的批量栏（`.selection-actions[data-fixed-bulk]`，`max-width: 767.98px` 时 `position: fixed`）放在 `.panel` 里，开了模糊会让它不再贴住视口底部。所以面板类的模糊只在 `>=768px` 开启，手机端只保留半透明。
- **顶栏保持不透明**：浏览器测试 `test_topbar_background_is_opaque` 守着这一条。早年真机上，半透明顶栏加模糊出过问题。
- **侧栏底色不透明**：令牌对比度单测要求正文在 `--sidebar-bg` 上至少 4.5:1；移动端抽屉还会盖在内容上。
- **对比度的判定方式**：单测只解析 `#hex` 令牌，玻璃色是 `rgb(... / α)`，不参与单测。浏览器测试按元素 `backgroundColor` 的 RGB 分量计算对比度，忽略透明度。真实合成色是玻璃叠在更暗（暗色）或接近白（浅色）的底上，所以对文字是同向或更有利，但这一点**没有逐像素实测**。
- **`.cal` 日历块未玻璃化**：它在单测「房间卡片段禁字面色」和日历网格线 `>= 1.2:1` 两条守卫范围内，仍是实色 `--surface`。房间卡里「本次退房」的内框也还是实色。

## 五、验证证据

| 项目 | 结果 |
| --- | --- |
| `ruff check .`、`git diff --check` | 通过 |
| `tests/unit/test_admin_assets.py` | 22 passed：双态 WCAG 对比度、`color: var(--primary*)` 禁令、弹簧公式、日历回退复算、状态色不借主色、所有 `var()` 已声明 |
| 全部浏览器测试 `tests/browser/`，加上一行单测 | 152 passed。在玻璃改动全部完成、只差最后一处表头细调时跑的；细调后又跑了 `test_admin_theme.py`，21 passed |
| `node --test tests/miniprogram/*.test.js` | 46 passed |
| 本地全量离线测试 | 3827 passed、99 skipped（需要 PostgreSQL、Docker 或外部凭据）。这是**玻璃改动之前**跑的；之后只改了 `app.css`、`DESIGN.md` 和 Spec，由上一行浏览器全量覆盖 |
| 截图 | Chromium 1440px 和 390px，浅、暗两态，覆盖工作台、入住安排、任务列表、登录页。文件在 `.stage/1.79.0-preview/`，该目录被 gitignore，需要的话请向用户要 |
| 真实模型门禁 | 未改 `REPLY_PATHS`，不需要跑 |

**未验证**

- 微信开发者工具和真机上的小程序深色效果。
- iOS Safari、Android WebView、Windows WebView2 上 `backdrop-filter` 和 `mask-composite` 的渲染与性能。
- 长列表页（几十张卡同时模糊）的滚动帧率。
- 不支持 `mask-composite` 的旧浏览器上，棱镜描边会退化成整块渐变盖住卡片，这一点没有实测。

## 六、请重点回答的问题

1. **质感**：用户评价「一般」。对照截图，或在本地起页面看，指出质感不足的根因，按投入产出排序给出具体改法。可以从这几处入手：
   - 玻璃透明度、模糊半径与饱和度；
   - 高光的形状和位置；
   - 描边亮度分布；
   - 投影层次；
   - 背景光源的构图（玻璃后面不够「有东西」可以折射，是否是主因）；
   - 浅色模式下玻璃几乎看不出来，该怎么办；
   - 是否需要噪点纹理。注意 CSP 只允许 `img-src 'self'`，不能用 `data:` URI；如需纹理，只能用同源静态文件或纯 CSS 实现。
2. **退化风险**：`mask` 加 `mask-composite: exclude` 的写法，在 Safari 15.4–17 和旧 Chromium 上会不会把渐变整块盖到卡片上？需不需要 `@supports (mask-composite: exclude)` 包一层？
3. **层叠与布局**：
   - `.panel` 开了 `backdrop-filter` 后成为层叠上下文，面板内的绝对定位浮层（如有）会被后面的面板盖住吗？
   - `.panel::before` 的 `inset: -1px`，与 `border-radius: inherit` 在大圆角上会不会错位？
   - `:where(...) { position: relative }` 有没有让某处已有定位的元素意外改变？
4. **性能**：每页可能有十几到几十张卡同时 `backdrop-filter`，加上固定背景（`background-attachment: fixed`）和 `position: fixed` 的点阵层，滚动时是否会明显掉帧？是否该收敛到首屏卡片或少数容器？
5. **可读性**：浅色玻璃白 50%、暗色 55%。在光晕最亮处（右上主光、斜向光束），正文和 `--muted` 的真实合成对比度是否仍然 `>= 4.5:1`？
6. **一致性**：小程序这一轮没做玻璃，只换了色并加了顶部光晕。是否需要同步？如果要同步，小程序里 `backdrop-filter` 的可用性与风险如何？
7. **其他问题**：任何正确性问题都请指出，包括令牌未声明或未使用、选择器优先级覆盖错误、禁用态被玻璃覆盖、旧色残留等。

## 七、尚未进行的收尾（审查后再做）

- 升版本到 `1.79.0`，更新 `CHANGELOG.md`，写 `docs/releases/1.79.0.md`，做 AOCI 维护。
- 用户明确授权后再提交、打标签、部署。
- 补记 1.78.0 推送后的 CI 结果。
