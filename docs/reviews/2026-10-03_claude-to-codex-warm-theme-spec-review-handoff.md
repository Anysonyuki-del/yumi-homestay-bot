# 后台可切换暖色主题 Spec · 审查结果 · Claude → Codex

日期：2026-10-03。审查对象：`docs/specs/2026-10-03_admin-warm-wood-theme-spec.md`（基线 `094c707`，应用 1.66.1，状态为「v6 视觉已确认，F1/F2/F4 与 D2–D5 待确认」）。

结论：**方向和门禁正确，事实论断全部属实。有 3 个 P2，建议在用户回复「开始」前写进 Spec；另有 6 个 P3。**

审查只读进行：没有修改 Spec、代码或配置，没有运行业务测试，也没有调用外部服务。

## 1. 核实属实的部分

| 论断 | 核实方式 | 结果 |
| --- | --- | --- |
| §1.1、§3 F3 的 7 组对比度 | 按 WCAG sRGB 相对亮度公式逐组计算 | 全部吻合：3D3D3A/E8E0D2 8.32、3D3D3A/F5F0E8 9.61、6C6A64/F5F0E8 4.77、3D3D3A/EFE9DE 9.02、A9583E/E8E0D2 3.86、CC785C+141413 5.63、A9583E+FAF9F5 4.80。参考原配的珊瑚底白字只有 3.28，Spec 改用深墨字是正确修正；v6 草图上按钮仍是白字，以 Spec 为准 |
| §2：CSP 只有 `frame-ancestors` | `middleware.py::AdminNoStoreMiddleware.__call__` 第 35 行 | 属实 |
| §2：认证页只加载 CSS | `templates/layouts/auth.html` 第 8 行，没有 `<script>` | 属实 |
| §2：`js-enabled` 由 admin.js 设置 | `static/admin.js` 第 3 行 | 属实 |
| §2：浏览器装配用 `set_content` 加手工注入脚本 | `tests/browser/test_admin_batch_workbench.py::load_page` 第 128、137 行 | 属实，因此主题首帧与刷新必须通过正常同源加载来验证 |
| §1：getdesign 没有引入依赖 | 仓库根目录没有 `package.json`、`package-lock.json`、`node_modules` | 属实 |

## 2. P2：建议在「开始」前写进 Spec

### T1 暖色底上，现有警告色、成功色和标题带小字的对比度不达标

- 计算结果：
  - `--warning #b45309` 在卡片底 `#EFE9DE` 上为 **4.16**；
  - `--success #15803d` 为 **4.15**；
  - `--danger #b42318` 为 5.44，达标；
  - `--muted #6C6A64` 在重点标题带 `#E8E0D2` 上为 **4.13**。
- 触发位置：
  - 直接用语义色写正文或说明的地方，例如 `.first-list__why--urgent` 等直接引用 `var(--warning/--success/--danger)` 的规则，暖色主题下底色变成奶油或卡片色；
  - `templates/admin/dashboard.html::workbench-first` 标题里的日期 `<p class="eyebrow">`。`.eyebrow` 取 `--muted`（`app.css` 第 101 行），会落在标题带上。
- 冲突：F4 同时要求「状态映射不改」和「正文对比度 ≥4.5」，暖色底上两条无法同时满足。F3 的配色表没有列出语义色。
- 建议：
  - F3 增加暖色主题的语义文字色，单独定义更深的 warning、success（只换色值，不改映射）；
  - 写明 `*-soft` 徽标底在暖色下是否保持；
  - 写明标题带上的 eyebrow 和辅助小字一律用 `#3D3D3A`；
  - §6 第 5 条加上这几组的实测验收。

### T2 根目录 DESIGN.md 会成为全项目的设计权威，包括经典主题

- 现状：根目录 `DESIGN.md` 共 589 行，是 getdesign 对 Claude 官网的分析。开头元数据写明 `name: Claude-design-analysis`，描述 Anthropic 品牌、深色产品界面和品牌标识，令牌里有 `on-primary: "#ffffff"`，并含 `surface-dark` 等深色系。
- 影响：impeccable 等工具会把根目录 `DESIGN.md` 当作项目的视觉权威，读取开头的令牌。§5 计划在文末追加「YuMi 主题适配」一节，但工具读不到末尾的说明，以后对经典主题或其它页面的设计工作仍会被引向 Claude 官网风格。白字珊瑚按钮、深色区块这些 Spec 已明确否定的做法，也会经由令牌被重新带回来。
- 建议（二选一，写进 D5 或新增一条 D7）：
  - **a（推荐）**：把 getdesign 原文移到 `docs/design/references/claude-design-analysis.md`，保留来源；根目录另写一份简短的 YuMi `DESIGN.md`，同时说明经典与暖色两套主题、令牌取 Spec 修正后的值；
  - **b**：保留原文，但在开头的元数据和首段声明「仅作暖色主题参考」，并把令牌改成 Spec 修正后的值，例如 `on-primary` 改为 `#141413`。

### T3 宋体标题在多数员工设备上不会出现

- 现状：F3 拟用 `Songti SC / STSong / SimSun / Noto Serif CJK SC / serif`。
  - 多数安卓手机没有中文衬线字体，会回退成系统黑体；
  - Windows 的 SimSun 是细笔画字体，28–32px 加粗时只能伪加粗，渲染发虚。
- 影响：v6 编辑式标题的视觉，只在苹果设备上成立。员工主要用手机，看到的会是另一套标题，和已确认的视觉不一致。
- 建议：在 D2–D5 里增加一条字体决策，请用户确认：
  - 接受安卓回退为黑体，作为已知差异记入 Spec；
  - 字体栈去掉 SimSun；
  - 衬线标题使用常规字重，不加粗，避免伪粗体；
  - 验收至少包含一台安卓真机截图。

## 3. P3

| 编号 | 问题 | 建议 |
| --- | --- | --- |
| T4 | v6 图里「先处理」每行是珊瑚色主按钮；现模板为 `button--secondary`（`dashboard.html::first-list`）。F3 写「主入口采用珊瑚色」，但 §5 文件清单没有 `dashboard.html`。如果只靠暖色 CSS 把它变成主按钮，两套主题的按钮层级会不一致，也和「稀疏强调」矛盾 | 明确保留次级按钮（推荐），或者两套主题一起改模板并补进 §5 |
| T5 | D3 在 `<head>` 中同步加载外部 `theme.js`，缓存未命中时每页多一次阻塞首屏的请求。当前 CSP 允许内联脚本 | 可选：读取偏好、设置 `data-theme` 用共享 Jinja 片段内联，控件绑定仍放外部脚本；代价是以后收紧 `script-src` 时需要补哈希。保持外部文件也可以，在 D3 写明理由即可 |
| T6 | `layouts/auth.html` 没有 `<meta name="theme-color">`（只有 `admin.html` 第 9 行有） | `theme.js` 更新 theme-color 时要能处理标签不存在；或者认证页补上这个 meta |
| T7 | 层次主要靠很小的色差：卡片与页面底 1.15，hairline 与卡片 1.09。用户之前反馈过「内容区层次太少」 | §6 验收加上手机真机截图比对，不以计算比值代替观感 |
| T8 | 珊瑚 `#CC785C` 与危险红 `#b42318` 色相接近，两者间对比度 2.01。危险按钮是红色描边（`app.css` 第 113 行），主按钮是珊瑚填充，靠填充方式能区分；但「逾期」红色徽标紧挨珊瑚按钮时容易混淆 | §6 第 5 条加一项：灰度与色弱模拟下，主操作、危险操作和逾期徽标能互相区分 |
| T9 | D5 的工作量没有量化：`app.css` 中 `:root` 之外有 30 处写死的颜色，`:root` 的 `--shadow-raised`、`--shadow-card`、`--shadow-control`、`--shadow-overlay` 都用冷蓝灰 `rgb(15 23 42 / …)` | 写进 D5：暖色主题要一并覆盖阴影色和 30 处字面量，`.button--danger:hover` 的 `#991b1b` 等按语义处理，否则会残留蓝灰阴影与冷色交互状态 |

另外两项提交范围提示，不是 Spec 缺陷：

- `docs/specs/assets/2026-10-03_admin-warm-wood-theme/` 下 v3–v6 四张参考图约 6.5MB。建议只提交 v6（约 1.5MB），或者压缩后再提交，v3–v5 用文字记录过程。
- `.impeccable/config.json` 新增了 `design-system-font` 的忽略规则。这是工具配置，与主题功能无关，提交时单独决定是否纳入。

## 4. 建议的处理顺序

1. 按 Spec §7，下一轮请用户确认 F1、F2、F4。确认时一并拍板 T2（DESIGN.md 的位置）和 T3（标题字体取舍）。
2. 把 T1 写进 F3 与 §6，T4–T9 写进相应条目，或注明不采纳的理由。
3. 进入 D2–D5 确认，冻结完整 Spec 后等待「开始」。
4. 本阶段仍只改文档，执行 `git diff --check` 和文档自审，不跑业务测试。
