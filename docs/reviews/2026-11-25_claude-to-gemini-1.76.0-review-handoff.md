# 1.76.0 前端重构复核修正｜Claude → Gemini 审查交接

2026-11-25 · 基线 `main` = `origin/main` = `ba40755` · 本文未入库

## 一句话

你交付的 A+E 前端重构，我对照全部 48 个改动文件复核后，在其上修了 6 处问题，与你的改动一起作为 1.76.0 发布并部署到了生产。请审查**我加的这 6 处修正**是否正确、有没有破坏你的设计意图，以及有没有我漏掉的问题。

- 提交：`e691efd`（你的重构 + 我的修正合在这一个提交里），`ba40755`（只补部署记录与 AOCI）。
- 标签：`v1.76.0`，已推送 GitHub，已部署生产。
- 完整发布记录：`docs/releases/1.76.0.md`（含复核结论、改动清单、验证证据、部署与登录后核对）。

## 审查规则

- **只读审查**，不要改代码、不要提交。审查意见写成报告交给用户转回，建议路径 `docs/reviews/2026-11-25_gemini-to-claude-1.76.0-review.md`。
- **不要碰生产**：不要登录、不要提交后台表单、不要调用真实 DeepSeek / Hostex / 企业微信。
- 每条发现请写明：文件路径 + 函数/选择器/符号、复现条件、为什么是问题、严重程度（P1 会出错 / P2 体验或维护问题 / P3 建议）。没把握的标成「疑问」，不要当成结论。
- 由于你的改动和我的修正在同一个提交里，`git show e691efd` 会混在一起。下面第二节逐条列出我加的部分，按那里的位置看即可。

## 一、你的工作我确认没问题的部分

这些不需要你再证明，列出来是为了让你知道我核过什么：

- 14 个模板的表单 `action`、字段名、`csrf_token`、`data-confirm*` 文案逐项比对，全部保留；只删了主题下拉。
- 模板没有内联脚本、`on*` 属性和外部资源，生产 CSP（`middleware.py::EMPLOYEE_CSP`）不需要放宽；`theme.js` 删干净了，没有残留引用。
- 页内弹窗的提交顺序正确：第一次 submit 被拦下时提交锁不会提前锁死；确认后用原 submitter 重新提交，客诉「发送给客人」仍带 `confirmed=1`。
- 1.75.0 的修复和 4 条结构守卫都在；测试改动是随功能迁移，没有放松断言。
- 小程序 `.wxml` 只改了图标颜色，6 枚标签图标仍是 81×81。

## 二、我加的 6 处修正（请重点审）

| # | 问题 | 修正位置 | 修法 |
| --- | --- | --- | --- |
| 1 | 首页「待办任务」说明写「开放中的保洁与维修」，但 `services/admin_dashboard_service.py::AdminDashboardService._count_pending_tasks` 统计的是全部 7 类未关闭任务 | `templates/admin/dashboard.html` 第 72 行 | 改为「所有未关闭的任务」 |
| 2 | 「已入住」用了主操作色陶红，违背 `DESIGN.md` 里主色只给操作的约定；1.75.0 已定已入住为中性 | `static/app.css`：`.readiness-bar__seg--occupied`（第 490 行）、`.status-dot--occupied`（第 500 行） | 改用 `var(--muted)`；新增守卫 `tests/unit/test_admin_assets.py::test_status_colors_never_borrow_the_action_color` |
| 3 | 详情页右栏吸顶但不限高。矮屏上右栏比视口高时，它底部的表单要等左栏滚完才露出来 | `static/app.css` 第 623 行，≥1024px 下的 `.detail-aside` | 加 `max-height: calc(100vh - var(--topbar-height) - 28px)`、`overflow-y: auto`、`overscroll-behavior: contain`；`padding: 4px; margin: -4px` 是为了不裁掉卡片阴影和焦点环 |
| 4 | 页内弹窗依赖 `HTMLDialogElement.showModal`（iOS Safari 15.4 起才有），更老的浏览器上需要确认的按钮点了没反应 | `static/admin.js::nativeConfirmFallback`（第 192 行）、`openConfirmDialog` 开头（第 208–216 行）；`static/app.css` 第 539 行 `.confirm-dialog:not([open]) { display: none; }` | 没有弹窗或没有 `showModal` 时，退回 `window.confirm`，手输数量的场景退回 `window.prompt`；CSS 保证不支持 `<dialog>` 的浏览器里弹窗不会作为普通块显示出来 |
| 5 | 弹窗和轻提示的结构在 `layouts/admin.html` 和 `admin.js`（`ensureConfirmDialog` 等动态构建）各写了一份 | 新增 `templates/components/confirm_dialog.html`；`layouts/admin.html` 第 89 行 include；`admin.js` 删除动态构建，`showToast` 找不到提示区直接返回 | 结构只定义一次。浏览器测试里手写的页面通过 `tests/browser/test_admin_interactions.py::_confirm_dialog_html` 渲染这份真实片段 |
| 6 | 手机首页「先处理」列表的分隔线跟着行圆角弯曲，像露出半张卡片边 | `static/app.css` 第 359 行 `.first-list li` | 去掉 `border-radius` |

### 第 4 项实现时踩到的坑（请确认我的理解）

兜底确认框一开始是在 submit 事件处理里**同步**弹出的。用户点确认后调用 `form.requestSubmit(submitter)`，但此时表单仍处于 submit 事件派发中，浏览器会忽略这次 `requestSubmit`，提交被**静默丢弃**。

现在的做法是 `window.setTimeout(..., 0)` 放到下一个任务再弹（`admin.js` 第 212–215 行）。用例 `test_browsers_without_dialog_fall_back_to_native_confirm`（确认 / 取消两种参数）覆盖了这一点：把 `setTimeout` 改回同步，「确认」那一例会失败。

## 三、我想请你特别判断的点

1. **兜底路径与提交确认链的配合**（`admin.js` 第 323–397 行的 submit 监听）
   - 确认后先写 `form.dataset.dialogConfirmed = "true"`，再 `requestSubmit`。若这次 `requestSubmit` 因为约束校验失败等原因没有产生 submit 事件，这个标记会留在表单上，下一次用户提交就会跳过确认。
   - 我的判断是风险低：第一次 submit 能到达监听器，说明当时校验已经通过；弹窗打开期间用户改不了表单；手输数量只改一个隐藏的计数字段。请你判断是否成立，以及是否值得在 `requestSubmit` 之后清掉标记。
2. **右栏栏内滚动是否违背你的设计**：矮屏上右栏会出现自己的滚动条。这是我有意的取舍，宽高充足时与你的设计一致。如果你有更好的做法（比如矮屏取消吸顶），请提出来。
3. **`.confirm-dialog:not([open])` 的隐藏**：弹窗目前没有开合动效，这条规则不会截断动画。若你计划给弹窗加关闭动画，这条规则需要配合调整，请在意见里说明。
4. **`.first-list li` 去掉圆角后**，桌面端 hover 背景变成直角，是否和你的视觉规范冲突？
5. **第 2 项的取色**：`--muted` 是我选的，你的令牌体系里如果有更合适的中性状态色，请指出。

## 四、验证证据

- **判别力**：在内存里分别撤回右栏限高、`showModal` 兜底、兜底延后弹出三处修复，对应用例都失败；把已入住改回陶红，守卫能报出 `.status-dot--occupied`。
- **本地全量**（最终代码）：3762 passed、94 skipped，比复核时你的原样工作区（3758 passed）多 4 条，即我新增的用例。跳过项是本机没有 PostgreSQL 和 Docker Compose 的用例。
- **小程序**：`node --test tests/miniprogram/*.test.js` 41 项通过。
- **浏览器测试**：真实服务 + 临时 SQLite，Chromium 执行和生产同一份 CSP。新增 `test_sticky_detail_aside_stays_reachable_on_short_screens`，在 1280×560 视口打开客诉编辑页验证右栏底部可达。
- **静态检查**：Ruff、`git diff --check`、`node --check admin.js` 通过。
- **生产**（部署后只读）：服务器源码、容器内包版本都是 1.76.0；CSP 与 1.75.0 相同；`/static/theme.js` 返回 404；登录后工作台的文案和已入住灰色已生效；1366×768 下房源详情右栏有 `max-height: 688px`；控制台没有 CSP 报错。

复跑命令（本地，不碰外部服务）：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/browser/test_admin_interactions.py tests/unit/test_admin_assets.py tests/browser/test_admin_theme.py
```

```bash
node --test tests/miniprogram/*.test.js
```

## 五、没有覆盖的地方

- 生产上没有点开确认弹窗，以免误提交；弹窗确认、取消和兜底三条路径只由本地浏览器测试覆盖。
- 只在 Chromium 里跑过；Safari、Firefox 和 Windows 桌面客户端（WebView2）里的实际显示没有验收。
- 「先处理」列表多行状态下的分隔线：截图数据只有一间待处理房间，没有目视核对多行，修法是去掉行圆角这个根因。
- 小程序配色改动还没上传，需要用户在微信开发者工具里构建上传。
- 除工作台 390px、1440px 两张截图外，其他页面的视觉效果依据的是你交接报告里的截图，我没有逐页亲眼核对。
