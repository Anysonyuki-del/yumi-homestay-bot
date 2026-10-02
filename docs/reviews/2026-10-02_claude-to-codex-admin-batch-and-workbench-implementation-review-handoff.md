# 任务批量操作、工作台行动与后台可靠性 · 实施审查结果 · Claude → Codex

日期：2026-10-02。状态：Claude 只读审查完成，**无 P0–P2，有 3 个 P3**。改动仍在工作区，未提交、未推送、未部署。

回应的是 `docs/reviews/2026-10-02_codex-to-claude-admin-batch-and-workbench-implementation-handoff.md`。审查期间只读代码、跑本地测试，没有修改任何文件，也没有调用真实外部服务。

## 1. 基线与审查范围

- 分支 `codex/admin-batch-workbench`，HEAD `8c3ff95`。工作区有 37 个已跟踪文件改动（+1014／−387）。另有未跟踪文件：`display.py`、`templates/errors/unavailable.html`、两个新测试文件、Spec、`docs/releases/1.66.0.md` 和交接报告，均已单独读过。
- 阅读顺序：Spec 的 D1–D16、F1–F12 与 §8–§10 → Codex 交接报告 → 完整 diff。按报告第 3 节的 8 条调用链逐条核对：模板 → `admin.js` → 路由 → `SessionTaskPageService` → 服务与仓储。
- 独立复跑报告列出的全部相关测试，共 15 个文件：

  ```sh
  .venv/bin/pytest -q tests/unit/test_task_page_service.py tests/unit/test_customer_admin_service.py \
    tests/unit/test_template_helpers.py tests/integration/test_operations_repository.py \
    tests/integration/test_customer_repository.py tests/integration/test_task_routes.py \
    tests/integration/test_admin_dashboard_routes.py tests/unit/test_admin_operations_service.py \
    tests/unit/test_timeline_geometry.py tests/integration/test_admin_unavailable.py \
    tests/integration/test_customer_routes.py tests/integration/test_knowledge_routes.py \
    tests/integration/test_property_routes.py tests/browser/test_admin_interactions.py \
    tests/browser/test_admin_batch_workbench.py
  ```

  结果：**430 passed，1 warning（既有的 Starlette TestClient 弃用提示），35.32 秒**，与报告的 276 + 90 + 64 一致。没有跑全仓：`application.py` 装配未改，同意报告的范围判断。

## 2. 逐条核对结论（路径省略前缀 `src/homestay_bot/`）

| 调用链 | 结论 | 依据 |
| --- | --- | --- |
| 1 页面资格 → 四种动作 | 成立 | `services/task_page_service.py::task_bulk_eligibility` 与 `require_assignable`、`cancel_many`、`archive_selected`、`require_purgeable` 同口径。客户页经 `repositories/customers.py::customer_detail` 补的 `property_id`、`archived_at` 计算，不从队列推断归档。`_localize_detail` 遇到未知状态时 `status_enum=None`，不给任何资格。服务端整批拒绝保留 |
| 2 事件顺序与提交入口 | 成立 | `admin.js` 中 `selectionForms` 的 `guard` 在点击和 `submit` 两条捕获阶段注册，先于 `typedForms`（submit 捕获）执行；普通 `data-confirm`（冒泡）、未保存提示和忙态锁都排在后面。`requestSubmit()` 不带 submitter 时，按 `data-default-action` 判断资格。`refresh` 会把 `data-confirm-count` 清零，旧确认数不能复用。没有资格标记的表单仍走原有空选择提示 |
| 3 拒绝格式与查询 | 成立 | 5 个出口都 await `describe_refused_tasks`，只查一次 `PropertyProfile` 的 id、房号、标题。`display.py::refusal_message` 先缩短房间名、再减少列出条数，最后退到只保留原因、总数和下一步，长度不超过 `PAGE_MESSAGE_MAX_LENGTH`。不含说明或客人资料。`LookupError` 的语义不变 |
| 4 数量、反馈与回跳 | 成立 | 会话门面原本就返回 `int`，路由直接使用。分派、取消、按勾选归档的成功和拒绝都经过 `safe_return_path`；我原稿发现的取消成功时使用原始 `return_to` 的问题已修复。0 条时不报「已归档」。客户页的 `customer_detail` 原本就会取出提示 |
| 5 来源过期与排序 | 成立 | `_next_step` 在过期时依次走：逾期 → 维修 → 开放任务 → 保洁或待检查 → 不给 URL；入住、退房、在住、下一位到店的分支都被跳过。同步正常时行为不变。`admin_dashboard` 只过滤 `attention_rooms`，读取失败时另给 `operations_error`。`_room_risk_sort_key` 未改，与 Spec §8 R1 一致 |
| 6 503 注册 | 成立 | `routes/page_errors.py::handle_http_exception` 的条件是 503、GET、`/employee/` 前缀、非健康检查、明确接受 HTML 且 q>0；其余请求交给 FastAPI 的 `http_exception_handler`。模板只显示固定文案，保留 `exc.headers`。`_wants_html` 收紧后，我核对了它另一个调用方 `handle_operation_refused`：浏览器表单的 Accept 仍判为 HTML |
| 7 手机与可访问性 | 成立（缺口见 §4） | `page-enter` 改为 `backwards`，结束帧不再保留 transform，`position: fixed` 不再受包含块影响。`.card-select` 至少 44×44，并与卡片链接并列。`TimelineBar.start_date/end_date` 取自真实的 `start`/`end`，不是裁剪后的 `clipped_*`；桌面条、无链接条的隐藏文本和行程列表都使用它 |
| 8 草稿与公共脚本 | 成立 | 房态表单和新建任务表单加了 `data-unsaved-warning`，进入原有 `dirtyForms`。`initOperationsCountdown` 已有的脏表单判断（`admin.js` 约第 543 行）因此生效，没有新增并行的状态机制 |

## 3. 需要处理的问题（都是 P3，均为本次引入）

### C1 知识库列表留下孤儿查询

- 位置：`routes/knowledge.py:838–839`、`:873` 的 `image_counts`；接口为 `routes/knowledge.py:119`（协议）与 `:419`（服务）；会话门面 `application.py:2734`；测试替身 `tests/integration/test_knowledge_routes.py:143`。
- 触发：管理员每次打开知识列表。
- 影响：F9 删掉列表上的永久删除表单后，模板里已没有任何地方使用 `image_counts`，原注释也写明它只为删除确认框服务。现在每次加载列表都多一次 IN 查询，结果直接丢弃。不影响正确性。
- 修复方向（二选一）：
  - **a（推荐）**：只删路由里的查询和传给模板的 `image_counts`。不碰 `application.py`，验证只需 `tests/integration/test_knowledge_routes.py`。接口、门面与测试替身里的 `image_counts` 方法会变成无调用方，写一行 `ponytail:` 注释说明，下次改知识门面时一并删除。
  - **b**：连同协议、服务方法、会话门面和测试替身一起删干净。这会碰到 `application.py`，按项目规则提交前要跑一次本地全量。

### C2 资格文字在已归档行与客户页上有误导

- 位置：`templates/components/ui.html:13` 的 `task_eligibility(actions)`；调用方是 `tasks/index.html:54/56` 和 `customers/detail.html:61/70`。
- 触发：
  - 任务中心「已归档」视图里，已归档的终态任务资格为 `{archive, purge}`，显示「可归档 可删除」；
  - 客户服务页列出已归档任务时也显示「可删除」，但这一页不提供永久删除，页面上还写着「永久删除请到任务中心操作」。
- 影响：资格本身与服务端一致（重复归档是成功的零改动），问题只在逐项文字。它报的是「服务端会接受的动作」，不是「本页提供的动作」，员工会去找不存在的按钮。
- 修复方向：宏增加 `offered` 参数，只显示本页实际提供的动作：
  - 任务中心常规视图：assign、cancel、archive；
  - 已归档视图：purge；
  - 客户页：archive、cancel。

  `data-eligible` 不变，脚本行为不受影响。验证：两页的模板断言，或在现有浏览器用例里加一条已归档行的文字检查。

### C3 提交中的按钮可能被重新启用

- 位置：`admin.js:367` 的 `refresh`，其中 `:382` 重算 `button.disabled` 并覆盖 `textContent`；`:442` 在 `resize` 时调用 `refresh`。忙态由 `:260` 的 `setSubmittingState` 设置，`:281` 标记 `form.dataset.submitting`。
- 触发：提交后、页面跳转完成前，窗口尺寸发生变化（手机地址栏伸缩就会触发 `resize`）。
- 影响：「正在处理…」按钮恢复可用，文字被改回原样。再点一次会通过 guard，取消或删除还会再弹一次手输确认，之后才被冒泡阶段的 `submitting` 检查拦下。即使漏过，批量 CSRF 令牌只能用一次，服务端也会拒绝。没有数据风险，只是体验不一致。
- 修复方向：`refresh` 开头遇到 `form.dataset.submitting === "true"` 直接返回，或者只跳过按钮状态那一段。验证：在 `tests/browser/test_admin_batch_workbench.py` 加一条「提交后触发 resize，按钮仍为禁用且显示忙态」。

## 4. 审查中确认、但不算缺陷的点

- `display.py::refusal_message` 最后一个兜底分支没有再截断，因为原因和下一步都是固定短文案，长度可控。如果以后接受外部输入，需要补上截断。
- 永久删除被拒时的下一步是「请先完成或取消这些任务后归档，再永久删除」，对已完成但未归档的任务略不准确。但永久删除只在已归档视图提供，正常操作碰不到，不单列问题。
- `tasks/index.html` 的资格说明仍是「混入不合格项会整批拒绝」，和 Spec F9 建议的措辞不同，但含义满足 F9「保留服务端可能拒绝」的要求，可以接受。

## 5. 未覆盖（同意报告第 5 节，补充一条）

- 320px 没有实测（最低 360px）；本机安装的包元数据为 1.4.0，没有重新打包，不能用源码版本代替。
- 真机触控、Mobile Safari、读屏软件，PostgreSQL 并发，以及生产页面、部署副本和外部同步都未验证。
- 补充：本次审查只复跑了测试，没有另做隔离浏览器走查。手机固定栏和日期显示的视觉结论依据 Codex 的走查记录和浏览器用例。

## 6. 建议的下一步

1. 按 C1a、C2、C3 修复。都是局部改动，不碰 `application.py`。
2. 只重跑 `tests/integration/test_knowledge_routes.py`、`tests/integration/test_task_routes.py`、`tests/integration/test_customer_routes.py` 和两个浏览器文件。其余 430 项结果的输入没有变化，可以复用。
3. 提交、推送、部署仍需用户当前明确授权。部署门禁按项目规则二选一：本地全量，或者推送后 CI 通过。
