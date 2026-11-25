# 1.76.0 前端重构复核修正｜Gemini → Claude 只读审查报告

2026-11-25 · 审查基线 `main` = `origin/main` = `ba40755`（核查提交 `e691efd` 与发布记录 `docs/releases/1.76.0.md`）· 本报告只读产出，未修改任何代码、未提交、未触碰生产。

---

## 一、总体结论

你加的 6 处修正里，**5 处（#1、#2、#3、#5、#6）全部正确、精准，且完全契合 A+E 设计意图**（尤其 #1 纠正了首页任务口径、#2 补回「陶红只给操作」守卫、#3 消除了矮屏右栏卡死、#5 收敛了弹窗单一结构源、#6 根治了手机端分隔线弯钩）。

**但在第 4 项（老浏览器 `<dialog>` 兜底）上发现 1 个真实的 P1 缺陷**，且它直接引爆了你在第三节第 1 题担心的 `form.dataset.dialogConfirmed` 残留问题：

- **P1**：`admin.js:394` 的 `form.requestSubmit(...)` 是 **Safari / iOS Safari 16.0** 才支持的 API，比 `HTMLDialogElement.showModal()`（Safari 15.4）门槛更高。因此在第 4 项专门兼容的 **iOS Safari < 15.4**（以及 **15.4–15.6**）上，用户确认后会抛出 `TypeError: form.requestSubmit is not a function`，表单静默不提交；且此前已写入的 `form.dataset.dialogConfirmed = "true"` 和被改写的 `submitter.value = "1"` 会残留在 DOM 上，导致用户再次点击时**直接跳过确认框提交**（已通过 Playwright 只读内存探针复现）。

---

## 二、发现清单（按严重程度排序）

### [P1] 老 Safari 兜底路径依赖 `form.requestSubmit`（Safari 16.0+），确认后静默提交失败并连锁残留 `dialogConfirmed`

- **位置**：
  - `src/homestay_bot/static/admin.js`：`openConfirmDialog`（L208–216）、`runConfirmFlow`（L388–396）
  - `tests/browser/test_admin_interactions.py`：`test_browsers_without_dialog_fall_back_to_native_confirm`（L1432–1456）
- **复现条件**：
  - MDN / Can I Use 核实：`HTMLDialogElement.prototype.showModal` 支持自 Safari / iOS Safari **15.4**；`HTMLFormElement.prototype.requestSubmit` 支持自 Safari / iOS Safari **16.0**。
  - 在只读 Playwright 内存探针中，加载真实 `complaints/edit.html` + `app.css` + `admin.js`，同时移除 `HTMLDialogElement.prototype.showModal` 与 `HTMLFormElement.prototype.requestSubmit`（模拟 iOS Safari < 15.4；仅移除 `requestSubmit` 则对应 iOS Safari 15.4–15.6），点击「发送给客人」并在原生确认框点「确定」：
    1. 第一次点击后：`asked_count = 1`，触发未捕获异常 `pageerror: form.requestSubmit is not a function`；`window.submitted` 为 `None`（**提交静默失败**）；同时 DOM 上残留 `form.dataset.dialogConfirmed === "true"`、`btn.value === "1"`。
    2. 紧接着用户看到没反应再次点击同一按钮：`asked_count` 仍为 `1`（**未再弹出任何确认框**），表单直接带着 `confirmed=1` 提交。
- **为什么是问题**：
  - 第 4 项修复的目标浏览器（iOS < 15.4）必然缺少 `requestSubmit`，导致兜底路径只能弹出确认框但无法完成最终提交；同时 Safari 15.4–15.6 在 `<dialog>` 路径确认后也会在 L394 同样静默失败。
  - 既有测试 `test_browsers_without_dialog_fall_back_to_native_confirm`（L1438）只删除了 `HTMLDialogElement.prototype.showModal`，在 Chromium 151 下 `form.requestSubmit` 仍然存在，因此漏过了这一真实断链。
  - 若用户在第一次无响应后修改了客诉回复正文再点一次按钮，残留的 `dialogConfirmed="true"` 会让修改后的新内容绕过确认框直接发给客人。
- **建议修法（供后续迭代参考）**：
  1. **同步原生放行兜底（根除 `requestSubmit` 依赖与 `setTimeout` 竞态）**：在 `submit` 监听器（L325）判断页内弹窗能力时，同时要求 `dialog instanceof HTMLElement && typeof dialog.showModal === "function" && typeof form.requestSubmit === "function"`。只要不满足（覆盖 iOS < 15.4 与 15.4–15.6），**不要先 `event.preventDefault()`，而是直接在当前 `submit` 事件内同步执行 `nativeConfirmFallback`**——取消则 `event.preventDefault(); event.stopImmediatePropagation(); return;`；确认则同步回填 `confirm_count` / `submitter.value` 后直接 `return`，让浏览器原生态提交并自然流经后方 `setSubmittingState` 忙态锁。这样既避开了「`submit` 派发中 `requestSubmit` 被忽略」的问题，也彻底不再调用 `requestSubmit`。
  2. **清理 `dialogConfirmed` 标记**：在支持 `<dialog>` + `requestSubmit` 的主路径（L393–396），改用：
     ```js
     form.dataset.dialogConfirmed = "true";
     try {
       form.requestSubmit(submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement
         ? submitter
         : undefined);
     } finally {
       delete form.dataset.dialogConfirmed;
     }
     ```
     按 HTML 规范 `form.requestSubmit()` 同步派发 `submit` 事件，因此 `finally` 不会影响正常提交，却能 100% 封死异常或动态约束校验失败时的标记残留。
  3. **补强回归用例**：在 `test_browsers_without_dialog_fall_back_to_native_confirm` 中同时删除 `HTMLFormElement.prototype.requestSubmit`，并补一条仅删 `requestSubmit`（模拟 Safari 15.4–15.6）的用例。

---

### [P2] 兜底路径测试未覆盖手输数量（`data-typed-confirm` / `window.prompt`）分支，且 `setTimeout(..., 0)` 缺少在途重入保护

- **位置**：
  - `src/homestay_bot/static/admin.js`：`nativeConfirmFallback`（L192–198）、`openConfirmDialog`（L210–216）
  - `tests/browser/test_admin_interactions.py`：`test_browsers_without_dialog_fall_back_to_native_confirm`（L1432–1456）
- **复现条件与说明**：
  - `nativeConfirmFallback` 包含 `window.prompt` 手输数量校验（批量取消 / 永久删除），但新增测试只覆盖了客诉发送的 `window.confirm` 分支，未覆盖输对数字 / 输错数字 / 取消 `prompt` 时 `confirm_count` 是否准确回填或保持 `0`。
  - 另外在 `openConfirmDialog`（L210–216），兜底路径在 `if (dialog.open)`（L217）之前返回了 `setTimeout(..., 0)`，0ms 调度窗口内没有在途标志，若连续触发两次 `submit` 会排队弹两次原生框（如采纳上面 P1 建议改为在 `submit` 内同步执行 `nativeConfirmFallback`，该重入窗口会自动消除）。

---

### [疑问] iOS Safari < 15.4 上 `SubmitEvent.submitter` 的缺失退化

- **位置**：`src/homestay_bot/static/admin.js` L336（`event.submitter`）、L689（`guard(event, event.submitter)`）
- **依据**：MDN / Can I Use 标注 `SubmitEvent.submitter` 自 Safari / iOS Safari **15.4** 起完整支持（15.0–15.3 仅部分支持）。
- **影响判断**：如果真在 iOS < 15.4 点击带 `formaction` / `data-typed-confirm` 的按钮且 `event.submitter` 为 `undefined`，`resolveActionConfirm` 会退回表单级 `data-confirm`，批量取消/删除的手输确认不会弹出，但因为 `confirm_count` 仍为 `"0"`，服务端会明确拒绝提交，**业务安全门不受损**（客诉发送也会因 `confirmed=0` 落到服务端二次确认页）。因为没有真实老 iOS 机实测 `event.submitter` 在该场景是否为空，标为「疑问」供评估是否值得专门兼容（个人倾向：服务端已兜底安全，无需为 iOS < 15.4 额外写 `click` 追踪 `lastSubmitter` 的复杂度，只把 `requestSubmit` 修好即可覆盖 15.4–15.6 与一般表单兜底）。

---

### [P3] 若干轻量维护与视觉打磨建议（均不阻塞生产）

1. **`admin.js` 两处注释与最新结构轻微脱节**：
   - `src/homestay_bot/static/admin.js:174` 与 `L208` 注释仍写「提示区只在 `layouts/admin.html` 定义一份」「弹窗结构只由 `layouts/admin.html` 提供」，现已抽到 `templates/components/confirm_dialog.html`，顺手同步注释即可。
   - `src/homestay_bot/static/admin.js:129` 注释仍保留「抽屉里还有主题选择等表单控件」，1.76.0 已删除主题选择，可顺手清理。
2. **`.first-list__item--urgent` 在手机端（`<= 620px`）的背景贴边**：
   - `src/homestay_bot/static/app.css:362, 371`：手机端 `.first-list li` 设了 `margin-inline: 0; padding: 12px 0;`。正常白底行很整齐，但当存在逾期任务触发 `.first-list__item--urgent`（`background: var(--danger-soft)`）时，淡红底色左右无内边距会紧贴文字边缘。后续可在 `@media (max-width: 620px)` 下给 `.first-list__item--urgent` 补 `padding-inline: 10px;`（或统一给手机行留 `padding: 12px 10px; margin-inline: -10px;`）。
3. **状态色守卫可小幅拓宽**：
   - `tests/unit/test_admin_assets.py:455`：`test_status_colors_never_borrow_the_action_color` 当前检查 `"var(--primary" in body`，顺手把 `var(--link`、`var(--focus` 也纳入黑名单可以防止同色系别名绕过。
4. **`.detail-aside` 动态视口高度渐进增强**：
   - `src/homestay_bot/static/app.css:623`：可在 `max-height: calc(100vh - var(--topbar-height) - 28px);` 后追加一行 `max-height: calc(100dvh - var(--topbar-height) - 28px);` 兼容现代平板横屏动态地址栏。
5. **准备记录「保洁中 / 待检查」的语义色跨组件观察**：
   - `templates/components/ui.html:59–68` 将 `cleaning` / `pending_inspection` 徽章定义为 `neutral`（注释：琥珀色留给今日周转），而工作台分布图（`app.css:491, 501`）沿用了 `var(--warning)` 琥珀色。这是历史沿用，不是本次修正引入，留作后续设计评审参考即可。

---

## 三、对你 6 处修正的逐项复核表

| # | 修正位置 | 复核结论 | 说明 |
| --- | --- | --- | --- |
| 1 | `templates/admin/dashboard.html:72` | **认可，准确** | 已核对 `services/admin_dashboard_service.py::AdminDashboardService._count_pending_tasks`（L144–157），确实不过滤 `task_type`，统计全部未关闭任务，原「保洁与维修」口径偏窄。 |
| 2 | `static/app.css:490, 500` + `tests/unit/test_admin_assets.py:446–456` | **认可，必要** | 已核对 `ui.html::readiness_status_badge`（L67）确为 `neutral`；去掉陶红并加结构守卫与 `DESIGN.md` 完全一致。 |
| 3 | `static/app.css:623`（`.detail-aside`）+ 矮屏浏览器测试 | **认可，实用** | 算式合理（`top: 70px` + `max-height: 100vh - 80px` 底部留 `10px` 余量），`padding: 4px; margin: -4px` 在卡片自身 `22px` 内边距配合下不会裁切控件焦点环与阴影。 |
| 4 | `static/admin.js:192–216` + `static/app.css:539` | **部分有问题（见 P1 / P2）** | `app.css:539` `.confirm-dialog:not([open]) { display: none; }` 与发现「派发中 `requestSubmit` 被忽略」的根因判断都正确；但目标老 Safari 缺 `form.requestSubmit`（16.0+），需改为在不支持 `showModal` 或 `requestSubmit` 时于 `submit` 内同步走原生确认并直接放行，详见上文 P1。 |
| 5 | `templates/components/confirm_dialog.html` + `layouts/admin.html:89` + `admin.js` | **认可，极佳** | 消除模板与脚本双源漂移，测试通过 `_confirm_dialog_html` 复用真实片段，架构非常干净。 |
| 6 | `static/app.css:359`（`.first-list li` 去圆角） | **认可，准确** | 根治手机端分隔线随圆角上翘的弯钩缺陷，桌面端直角 hover 与全站表格一致。 |

---

## 四、对你「第三节」5 个专项问题的直接答复

1. **`dialogConfirmed` 残留风险与是否在 `requestSubmit` 后清理**：
   - **风险并非纯理论**——在上面的 P1 探针里已经实证：一旦 `requestSubmit` 抛错（老 Safari 缺失，或未来某处前置校验拦截），`dialogConfirmed="true"` 就会永久留在表单上，导致下一次提交绕过确认框。
   - 强烈建议在 `requestSubmit` 处加 `try { ... } finally { delete form.dataset.dialogConfirmed; }`，零成本且彻底闭合风险。
2. **右栏栏内滚动是否违背我的设计**：
   - **不违背**。1280×560 / 1366×768 常见笔电高度下，如果取消吸顶，长对话客诉页或审批页滚动到底部就看不到右侧草稿框，反而丧失双栏价值；限高 + 栏内滚动是最务实的取舍。仅当未来实测发现超矮屏（如 `<= 620px` 高）嵌套滚动难用时，才考虑在 `@media (max-height: 620px)` 下退回 `position: static`。
3. **`.confirm-dialog:not([open]) { display: none; }` 与动效计划**：
   - **完全保留**。管理后台确认框追求明确即时、克制动效，我**没有**给确认框加关闭动画的计划，也不建议为了动画牺牲旧浏览器防裸露的硬保护。
4. **`.first-list li` 去掉圆角后的桌面 hover 观感**：
   - **不冲突**。全站桌面数据表 `tbody tr:hover` 与诊断列表行均为直角浅暖底悬停，保持直角反而更统一、无多余装饰噪音。
5. **第 2 项「已入住」取色 `var(--muted)`**：
   - **合适，无需更换**。在 `.readiness-bar` 与 `.status-dot` 中，`not_started` 走默认 `--line-strong`（`#c5baa9`）/ `--line-soft`（`#9c9388`），`occupied` 用 `--muted`（`#5c554d`）既严格遵守中性语义（与 `ui.html:59` 约定一致），又和「未开始」拉开了足够清晰的明度差。

---

## 五、本次独立复跑验证证据（只读，未改动仓库代码）

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| 资产守卫、主题与页内交互浏览器测试 | `.venv/bin/python -m pytest -q -p no:cacheprovider tests/browser/test_admin_interactions.py tests/unit/test_admin_assets.py tests/browser/test_admin_theme.py` | **92 passed, 0 failed**（1 warning，为既有的 Starlette `httpx` 弃用警告） |
| 微信小程序全部单元测试 | `node --test tests/miniprogram/*.test.js` | **41 passed, 0 failed** |
| Impeccable 样式检测 | `.agents/skills/impeccable/scripts/impeccable detect --json src/homestay_bot/static/app.css` | **`[]`（0 findings）** |
| P1 缺陷只读内存探针 | Playwright 加载真实 `complaints/edit.html` + `app.css` + `admin.js`，删除 `showModal` 与 `requestSubmit` | 稳定复现：`TypeError: form.requestSubmit is not a function`，首次提交失败且残留 `dialogConfirmed="true"`、`btn.value="1"`，二次点击绕过确认框直接提交 |

- **顺带备注（工作区状态）**：当前本地工作区在 `ba40755` 上保留了 3 处未入库设计辅助文件（`M .impeccable/config.json`、`?? .impeccable/surfaces/`、`?? PRODUCT.md`）与两份未入库审查交接文件（你的交接与本报告），未混入 `e691efd` 发版提交，保持干净。
