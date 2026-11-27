# 前端光学视觉重设计 Spec｜Gemini → Claude 审查交接

- **日期**：2026-11-28
- **基线**：`main` = `origin/main` = `9b0518a`（线上生产 `v1.77.0` = `8a0daa1`）· 本文与 Spec 均未入库，代码零改动
- **待审 Spec**：`docs/specs/2026-11-28_optical-prism-visual-redesign-spec.md`
- **待审 Spec SHA-256**：`fb61a505124f39661d34a78cd5869f1349064fa0f33e807b0956936b25a235f5`

## 一句话

用户要求参照公开项目 `RMvdesi/obsidian-prism`（GPL-3.0）的光学美学理念，重新设计 YuMi 前端的视觉效果，并已明确拍板三项方向：**① 浅色为主 + 纯 CSS 自动跟随系统暗色（暖白画册光学 × 暖炭玄岩光学，无手动切换器）；② 暖琥珀 / 晶陶红作为唯一动作强调色；③ Web 运营后台、认证页与微信小程序三端同步升级**（不加吉祥物/壁纸等娱乐组件，不引入前端构建链）。我已完成调研、对比度实算与整份 Spec 起草，请你按项目规则做独立只读审查。

## 审查规则

- **只读审查**，不要改代码、不要提交、不要触碰生产或调用外部服务。
- 审查意见请写成报告交给用户转回，建议路径：`docs/reviews/2026-11-28_claude-to-gemini-optical-prism-spec-review.md`。
- 每条意见请写明：Spec 章节与对应源码文件/符号、为什么是问题、严重程度（P1 会出错或违反硬边界 / P2 体验或维护风险 / P3 建议），以及需要用户裁决的选项。

## 请重点审查的 6 个硬核点

1. **暗色模式隐式依赖 `--surface` 为白的漏网选择器**（Spec §1.1、§2.1、§4.1 R1）
   - 我已查出并拆出独立令牌的三处：`app.css:564` `.toast-region`、`app.css:138` `.skip-link`、`app.css:168` `.button--danger:hover`。
   - 请再通读整份 `src/homestay_bot/static/app.css`（923 行）与 `templates/`，核查是否还有其它依赖「浅色背景 / `--surface` 为白 / 阴影方向 / 透明底图片在暗底发黑（如 `.image-list img`、房源欢迎图、知识库配图预览）」的暗色可读性隐患。
2. **日历 `--cal-*` 暗色派生与 `tests/unit/test_admin_assets.py` 正则边界**（Spec §2.3、§4.1 R2）
   - 请核对 §2.3 中 6 组浅/暗 `--cal-*` 静态回退值与 `_srgb_mix` 公式是否严格一致；以及当 `@media (prefers-color-scheme: dark)` 内声明第二组 `--cal-*` 时，`test_room_card_colors_all_go_through_design_tokens`（L313–340）的 `_room_card_block` 边界和 `test_calendar_bar_palette_stays_derived_from_the_primary_token`（L343–364）的解析方式该如何精确升级而不漏检。
3. **暗色模式下非 `color-mix` / 老浏览器的降级与对比度**（Spec §2.1、§4.1 R4）
   - 暗色主按钮采用 `--primary: #b84018`（白字 `5.55:1`，相对暗卡 `#1d1b19` `3.09:1`）、`--primary-hover: #c74a1f`（白字 `4.70:1`），链接与焦点环采用暖琥珀金 `--link` / `--focus: #f09a5e`（`7.45–7.76:1`）。请核查暗色下是否有某类元素同时把 `--primary` 当文字色放在暗底上（导致对比度不足 `4.5:1`），例如 `.side-nav a[aria-current="page"] .icon`、`.cal__date--today`、`a` 链接、或 `.kb-*` 组件。
4. **微信小程序 `darkmode` + `theme.json` 与 `<t-icon>` 着色机制**（Spec §3 F5、§4.1 R5）
   - 已扫描出 `pages/{home,me,services}/*.wxml` 共 16 处 `<t-icon color="#c24322|#58524a" />` 硬编码；请复核是否还有遗漏的 WXML/JS 硬编码色（例如 `wx.showModal` / `confirmColor` 或页面 `.json` 独立覆盖 `navigationBarBackgroundColor`），并评估 `tdesign-miniprogram` 1.17.0 在 `glass-easel` 下用 `color="var(--yumi-primary)"` 与 `t-class` 哪种方案最稳妥。
5. **四档 `linear()` 阻尼弹簧动效与既有测试/无障碍兼容性**（Spec §2.2、§3 F4）
   - 请复核 `@keyframes dialog-enter` 是否可能影响 `tests/browser/test_admin_interactions.py` 或 `test_knowledge_workbench.py` 中的弹窗焦点/点击稳定性；以及 `prefers-reduced-motion: reduce` 是否彻底关闭所有新增动效。
6. **两处待拍板细节（D1 / D2）与硬边界守卫**（Spec §0、§4.2）
   - 请一并评估 D1（小程序浅色 TabBar 用 `#ffffff` 还是 `#ebe6dc`）与 D2（认证页顶部静态径向微光晕是否保留），以及确认 Spec 是否 100% 守住 GPL-3.0 零拷贝、零后端改动、零 `REPLY_PATHS` 与零前端构建链边界。
