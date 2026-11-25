---
version: "2.0"
name: YuMi-warm-precision-hospitality
description: YuMi 民宿运营后台、认证页与微信小程序的统一「暖调精工 SaaS（A+E 融合）」设计规范：清透暖白画布、纯白轻盈卡片与侧栏、系统中文无衬线与等宽数字、唯一 WCAG AA 深陶红动作色，以及无脚本可降级的页内温和弹窗。
colors:
  primary: "#c24322"
  primary-hover: "#a73619"
  primary-active: "#8f2d14"
  primary-soft: "#fbede8"
  primary-border: "#eabeb0"
  on-primary: "#ffffff"
  link: "#b53a1b"
  focus: "#c24322"
  page: "#f5f2ed"
  surface: "#ffffff"
  surface-sunk: "#faf8f5"
  surface-well: "#f2ede6"
  ink-strong: "#1e1b18"
  text: "#2a2622"
  ink-mid: "#433e38"
  ink-soft: "#58524a"
  muted: "#5c554d"
  line: "#dcd4c8"
  line-strong: "#c5baa9"
  control-border: "#787066"
  line-soft: "#9c9388"
  disabled-bg: "#efebe5"
  disabled-ink: "#6e675e"
  disabled-border: "#d6cec2"
  success: "#0f5c3a"
  success-soft: "#ecfdf3"
  success-border: "#b3e6c9"
  warning: "#8a4f06"
  warning-soft: "#fff9e6"
  warning-border: "#ecd08c"
  danger: "#9f1d1d"
  danger-soft: "#fef2f2"
  danger-border: "#f2bcbc"
  info: "#1c4c70"
  info-soft: "#eff5fb"
  info-border: "#bed6eb"
  toast-bg: "#1e1b18"
typography:
  micro:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 11px
    fontWeight: 600
    lineHeight: 1.3
  caption:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.45
  small:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.5
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
  lead:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.45
  control-mobile:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.45
  subhead-mobile:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 18px
    fontWeight: 700
    lineHeight: 1.3
  card-title-mobile:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 19px
    fontWeight: 700
    lineHeight: 1.3
  card-title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 20px
    fontWeight: 700
    lineHeight: 1.25
  countdown:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 22px
    fontWeight: 700
    lineHeight: 1.2
  page-title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 24px
    fontWeight: 700
    lineHeight: 1.2
  metric-value:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 30px
    fontWeight: 700
    lineHeight: 1.08
rounded:
  xs: 6px
  control: 9px
  panel: 12px
  dialog: 16px
  pill: 999px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.control}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.control}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-mid}"
    borderColor: "{colors.line-strong}"
    rounded: "{rounded.control}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.danger}"
    borderColor: "{colors.danger-border}"
    rounded: "{rounded.control}"
  button-disabled:
    backgroundColor: "{colors.disabled-bg}"
    textColor: "{colors.disabled-ink}"
    borderColor: "{colors.disabled-border}"
    rounded: "{rounded.control}"
---

# YuMi 统一设计规范（暖调精工 SaaS · Warm Precision Hospitality）

## Overview

本规范覆盖 **Web 运营后台、认证页**（`src/homestay_bot/static/app.css`、`src/homestay_bot/static/admin.js`、`src/homestay_bot/templates/`）与 **微信小程序住客自助端**（`clients/wechat-miniprogram/`）。Tauri 桌面/安卓原生外壳不在视觉重设计范围内。

产品采用单一统一的 **A+E 暖调精工融合（Warm Precision Hospitality）** 体系，已移除旧版经典/暖色双主题切换机制（删除 `theme.js`、`ui.theme_selector` 与 `[data-theme="warm"]` 分支），所有页面首帧直接进入统一视觉基线，无需客户端偏好恢复或构建工具链（保持零 Node/零构建与严格 `EMPLOYEE_CSP` 兼容）。

核心设计原则：
- **暖调但不臃肿**：以清透暖白亚麻底（`#f5f2ed`）承托纯白卡片与纯白侧栏（`#ffffff`），辅以沉降暖面（`#faf8f5`）和暖石浅槽（`#f2ede6`）建立层次。
- **精工高密度**：全系统采用中文系统无衬线字体与等宽数字（`tabular-nums`），严禁外部字体 CDN、品牌装饰衬线、重黑块、木纹材质或花哨渐变。
- **动作色单一权威**：深陶红（`#c24322`，白字对比度约 `5.2:1`）仅用于真实可操作的 CTA、当前激活导航/页签、站内链接与键盘焦点环；禁用态彻底去色（`#efebe5` / `#6e675e` / `#d6cec2`），杜绝“看起来像能点”的伪活跃状态。
- **业务真实边界不变**：所有权限、CSRF、房态与价格权威来源（百居易/Hostex）、准备记录与实际入住区分、平台受理不等于客人收件、以及无脚本服务端回退均与视觉层解耦并严格保留。

## Colors

### 表面与墨色层级
- **画布与表面**：画布 `--page: #f5f2ed`，主卡片与桌面侧栏 `--surface: #ffffff`，表头/内嵌子卡/日历日期标尺 `--surface-sunk: #faf8f5`，悬停底/徽标默认底/轨道底槽 `--surface-well: #f2ede6`。
- **墨色层级**：主标题与数字 `--ink-strong: #1e1b18`，正文 `--text: #2a2622`，次标题与表单标签 `--ink-mid: #433e38`，导航与辅助字 `--ink-soft: #58524a`，说明与时间戳 `--muted: #5c554d`。所有正文与辅助说明在 `#ffffff`、`#faf8f5`、`#f5f2ed` 上均满足 WCAG AA `>= 4.5:1`。

### 线条与控件边界
- 结构细线 `--line: #dcd4c8`（相对纯白 `1.47:1`，高于 `1.2:1` 日历网格可读底线），外框加强线 `--line-strong: #c5baa9`，次要高亮线 `--line-soft: #9c9388`。
- 可编辑表单边界专用 `--control-border: #787066`（相对纯白与暖白画布均 `>= 4.3:1`，满足 WCAG 非文本控件 `>= 3:1` 要求），作用于可见 `input`（除 `hidden`/`checkbox`/`radio`）、`select` 与 `textarea`。

### 动作色与业务状态色
- **唯一动作色**：`--primary: #c24322`、`--primary-hover: #a73619`、`--primary-active: #8f2d14`、`--primary-soft: #fbede8`、`--primary-border: #eabeb0`、`--link: #b53a1b`、`--focus: #c24322`。
- **业务状态语义色**（独立于陶红动作色，互不混淆）：
  - 成功/可入住：`--success: #0f5c3a` / `--success-soft: #ecfdf3` / `--success-border: #b3e6c9`
  - 警示/今日离店/待检查：`--warning: #8a4f06` / `--warning-soft: #fff9e6` / `--warning-border: #ecd08c`
  - 危险/逾期/维修/失败：`--danger: #9f1d1d` / `--danger-soft: #fef2f2` / `--danger-border: #f2bcbc`
  - 信息/已分派：`--info: #1c4c70` / `--info-soft: #eff5fb` / `--info-border: #bed6eb`
- **日历占用条配色**：全部 6 组 `--cal-*` 均提供 sRGB 静态回退与 `color-mix(in srgb, ...)` 成对声明，并同步由 `tests/unit/test_admin_assets.py` 守卫。

## Typography

- **统一字体栈**：`-apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif`（零外链字体，符合严格 `EMPLOYEE_CSP`）。
- **字号尺规**：页面标题 `24px`（`700`）、指标大数 `30px`（`700`，`tabular-nums`）、房卡标题 `20px`（手机 `19px`）、面板标题 `17px`（`--t-lg`）、子标题 `15px`（`--t-md`）、正文 `14px`（行高 `1.55`）、辅助文字 `13px`（`--t-sm`）、徽章与表头 `12px`（`--t-xs`）、导航分组与星期小标 `11px`（`--t-2xs`）。移动端表单控件保持 `16px` 避免 iOS 自动放大。
- **数字排版**：表格、分页、日期标尺、倒计时、同步时间、统计指标统一启用 `font-variant-numeric: tabular-nums`。

## Layout

- **桌面端外壳（`>= 1024px`）**：左侧纯白侧栏（`224px` 宽，右侧 `1px solid var(--line)` 分隔，当前导航使用 `#fbede8` 胶囊 + `#eabeb0` 描边 + 左侧 `3px` 圆丸陶红指示条 + `#b53a1b` 文字）；顶部 `52px` 粘性纯白顶栏承载功能分组面包屑（`YuMi 运营台 / 运营|客户与内容|系统管理`，不与唯一 `<h1>` 重复）与药丸账号入口；右侧暖白画布工作区，最大内容宽度 `1260px`（入住安排页加宽至 `1560px`）。
- **复合页头与工作台结构**：`.content-header` 左侧为唯一页面标题 `<h1>`，右侧承载视图分段页签（如 `workbench.views`、知识库分区）与元信息胶囊；今日工作台采用独立浮起 KPI 卡片（风险卡片以 `--danger-soft` 底色 + `--danger-border` 描边警示）、内嵌双栏 `.stay-pane` 及纯 CSS 占比条 `.readiness-bar`。
- **详情页双栏吸顶工作台（`>= 1024px`）**：任务、房源、客户、预订审批、投诉复核与知识详情统一采用 `.detail-layout`（左事实/时间线 `1.55fr` + 右侧吸顶操作栏 `0.95fr`，`< 1024px` 自动单列堆叠）；客户详情顶部配备 `.profile-hero` 单字头像横幅与内嵌页签导航。
- **移动端外壳（`< 1024px`）**：顶部粘性纯白导航栏（`52px` 高）+ 左侧抽屉导航；无脚本时显示 `<details class="no-script-nav">` 完整可达导航。
- **响应式与 320px 安全**：全页在扣除真实滚动条后的 `320px`–`1440px` 视口下零横向溢出；表格在 `< 768px` 自动切换 `.mobile-card-list`；任务页固定底部批量栏（`< 768px`）自动预留实际高度避免遮挡。
- **入住安排日历**：
  - 桌面端（容器 `> 620px`）：完整圆角外框（`1px solid var(--line-strong)`），纵排星期/日期双行标尺（`min-height: 58px`），今天列以 `--primary-soft` 底色 + `3px` 底部深陶红定位线标示；默认折叠 3 轨，支持展开全部轨道。
  - 移动端（容器 `<= 620px`）：`minmax(44px, 1fr)` 日期条配合内描边 `box-shadow: inset 0 0 0 1px`（确保 `360px` 6 天零溢出且 18 天每格内宽 `>= 44px`）+ 迷你占用轨 + 整卡点击订单项。
- **触控与鼠标密度（Hallmark H03）**：触屏设备（`@media (any-pointer: coarse)`）下导航、页签、顶栏账号入口与按钮统一 `>= 44×44px`；纯鼠标桌面保持紧凑操作密度（`.tab-nav a` 高 `36px`）。

## Elevation & Depth

- 默认面板仅依靠 `#f5f2ed` 画布与 `#ffffff` 卡片的明度差和 `1px solid var(--line)` 描边建立边界，不使用宽阴影。
- 仅三处使用克制的暖炭投影：次级按钮轻微托起 `--shadow-raised`（`0 1px 2px`）、独立认证浮卡 `--shadow-card`（`0 10px 24px`）、抽屉/弹窗/轻提示浮层 `--shadow-overlay`（`0 12px 24px` + `0 2px 6px`）。

## Shapes

- 徽章与状态胶囊：`999px` 全圆角。
- 按钮、输入框、下拉框、内嵌提示与日历条：`9px`（`--r-sm`）；内部小标签：`6px`（`--r-xs`）。
- 主面板、表格容器与卡片：`12px`（`--radius`）。
- 认证浮卡与页内确认弹窗：`16px`。

## Components

### 页内温和确认弹窗与操作轻提示
- `templates/layouts/admin.html` 统一挂载共享原生 `<dialog class="confirm-dialog" data-confirm-dialog>` 与 `<div class="toast-region" data-toast-region role="status" aria-live="polite">`；校验错误提示使用 `aria-live="assertive"`，不占用页面级 `[role="alert"]`。
- `static/admin.js` 拦截 `data-confirm`、`data-danger-confirm`、`data-typed-confirm` 及跨表单未保存提醒，以样式化 `<dialog>` 替代浏览器原生 `window.confirm` / `window.prompt`：
  - 支持 `{n}`、`{employee}`、`{draft}`、`{reservation_code}` 占位替换与自定义 `data-confirm-accept-value`；
  - 危险操作按钮采用 `.button--danger`，手输条数确认（`data-typed-confirm`）在弹窗内原地校验并显示红字错误，支持 `Esc` 与遮罩取消，关闭后自动归还焦点；
  - 保留无脚本服务端回退（如客诉二次确认页、批量手输服务端 `confirm_count` 校验）。
- 复制诊断报告（`[data-copy-target]`）与空选批量操作提示接入温和浮层 Toast，不阻塞操作流。
- `templates/layouts/auth.html` 严格不加载 `admin.js`，认证表单保持零脚本原生提交。

### 微信小程序住客自助端统一规范
- `clients/wechat-miniprogram/app.json`、`app.wxss` 及 6 个页面样式全面对齐 A+E 暖调精工令牌：`#f5f2ed` 画布、`#ffffff` 卡片、`#dcd4c8` 清晰边框、`#1e1b18` / `#58524a` 深炭暖墨层级、`#c24322` 主色配 `#ffffff` 白字（对比度 `5.2:1`）。
- `app.wxss` 同步映射 `--td-brand-color: #c24322`、`--td-brand-color-active: #a73619` 与 `--td-brand-color-light: #fbede8`，全部 6 枚 81×81 标签栏图标统一重着色为 `#c24322`（选中）与 `#58524a`（未选中）。
- 所有「问管家」入口严格保留原生 `<button open-type="contact">`，不改为 JS 调起或拨号兜底；不显示价格、房态或评分。
