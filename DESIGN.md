---
version: alpha
name: YuMi-admin-themes
description: 民宿运营后台的双主题规范。默认经典；暖色为用户确认的 v6 奶油纸感与珊瑚动作。两套主题共用真实业务、状态和可访问性。
colors:
  classic-primary: "#2563eb"
  classic-primary-hover: "#1d4ed8"
  classic-canvas: "#f4f6fa"
  classic-surface: "#ffffff"
  classic-ink: "#1e293b"
  warm-primary: "#cc785c"
  warm-primary-hover: "#a9583e"
  warm-on-primary: "#141413"
  warm-on-primary-hover: "#faf9f5"
  warm-canvas: "#faf9f5"
  warm-surface: "#efe9de"
  warm-surface-soft: "#f5f0e8"
  warm-surface-strong: "#e8e0d2"
  warm-ink: "#141413"
  warm-body: "#3d3d3a"
  warm-muted-on-soft: "#6c6a64"
  warm-hairline: "#e6dfd8"
  warm-control-line: "#8d8172"
  warm-focus: "#a9583e"
  warm-success: "#166534"
  warm-success-soft: "#edf3e8"
  warm-warning: "#92400e"
  warm-warning-soft: "#f7edd8"
  warm-danger: "#b42318"
  warm-danger-soft: "#f6e6e2"
  warm-info: "#315a73"
  warm-info-soft: "#eaf1f4"
typography:
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
  classic-heading:
    fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI", sans-serif'
    fontSize: 24px
    fontWeight: 700
    lineHeight: 1.2
  warm-heading:
    fontFamily: '"Songti SC", "STSong", "SimSun", "Noto Serif CJK SC", serif'
    fontSize: 32px
    fontWeight: 400
    lineHeight: 1.25
  warm-heading-mobile:
    fontFamily: '"Songti SC", "STSong", "SimSun", "Noto Serif CJK SC", serif'
    fontSize: 28px
    fontWeight: 400
    lineHeight: 1.25
rounded:
  control: 8px
  panel: 12px
components:
  classic-button-primary:
    backgroundColor: "{colors.classic-primary}"
    textColor: "{colors.classic-surface}"
    rounded: "{rounded.control}"
  warm-button-primary:
    backgroundColor: "{colors.warm-primary}"
    textColor: "{colors.warm-on-primary}"
    rounded: "{rounded.control}"
  warm-button-primary-hover:
    backgroundColor: "{colors.warm-primary-hover}"
    textColor: "{colors.warm-on-primary-hover}"
    rounded: "{rounded.control}"
---

# YuMi 后台双主题设计规范

## Overview

作用域是 Web 运营后台与认证页。默认经典，用户可切换暖色；原生客户端外壳不在范围内。

经典保留现有蓝色、白色面板、中文系统无衬线、紧凑布局。暖色采用用户已确认的 v6 纯色纸感：奶油底、暖奶油分层、珊瑚动作。明确禁止恢复大块黑色重点区或增加木纹。两主题均保持真实内容、权限、按钮资格、确认和红黄绿状态语义。

设计依据为 [暖色主题 Spec](docs/specs/2026-10-03_admin-warm-wood-theme-spec.md)。getdesign 导入的 Claude 官网原文完整保存在 [参考分析](docs/design/references/claude-design-analysis.md)，它只提供历史设计参考，不约束经典主题或替代本规范。

## Colors

头部令牌按 classic/warm 明确区分，不能把暖色套到经典。暖色普通小字用 `#3d3d3a`；只有浅列表 `#f5f0e8` 上允许使用 `#6c6a64`。深奶油标题带上的日期也用正文色。

珊瑚配深墨按钮字；深陶土悬停配奶油字。小字链接用正文色，保留清楚的链接提示；焦点用深陶土。危险动作保留红色描边和明确文案，信息徽标采用独立灰蓝，不和珊瑚动作混用。

warning/success 使用加深的文字色，soft 底保留暖浅黄/暖浅绿；状态映射来自真实组件。普通正文至少 4.5:1，大字和必要边界/焦点至少 3:1。装饰 hairline 不承担必要控件边界。

## Typography

正文、导航、按钮和数字沿用中文系统无衬线。暖色页面/分区标题采用本机宋体衬线、400 字重，不合成粗体；保留 SimSun 的 Windows 回退。缺少中文衬线的设备允许系统字形差异，不承诺与苹果设备一致；不下载品牌字体或字体 CDN。

页面标题桌面 32px、手机 28px；分区约 20px，行标题约 16px；正文 14–16px，辅助约 13px。操作数据保持无衬线与表格数字。经典字号和字重不改变。

## Layout

保留既有工作台、表格/手机卡片、运营日期、客户详情和认证流程。先处理区以深奶油标题带、浅奶油列表、文字与间距表达优先级，仅其行内入口定向采用珊瑚；全局次级按钮保持次级。

风险、今日周转、入住与退房、本系统准备记录仍是原数据分组；准备记录不等于真实入住的说明保留。主要分区约 24px，内部 8–16px，不复制营销页的大留白或新增指标。

桌面主题控件在账号旁，手机在导航抽屉底部，认证页在业务表单外。原生 select、有标签、唯一 id、触控至少 44px。320px 按扣除滚动条的可用宽度布局，固定批量栏继续预留实际高度。

## Elevation & Depth

暖色普通面板使用色阶与细分隔，次级按钮不加冷蓝灰阴影。登录卡和抽屉仍可用暖灰柔和阴影表达浮起；不添加黑色内容块、渐变、图片材质或额外纹理请求。

## Shapes

控件约 8px、面板约 12px 圆角，运营卡沿用现有形状。状态徽标与图标沿用真实组件与 SVG 家族；不得用生成草图的图标颜色重新解释业务状态。

## Components

两主题覆盖加载、空、错误、成功、禁用、选中、悬停和键盘焦点。主题仅管理外观与本浏览器偏好，不提交业务、不清空草稿、不重载、不重复绑定业务脚本。

`theme.js` 在两个布局的 CSS 前同步恢复 `classic/warm`；默认经典。存储不可用或资源失败时页面保持可用，写入失败时真实提示当前页已切换但无法记住。没有 JavaScript 时隐藏选择控件。日历兼容回退与 color-mix 成对维护。
