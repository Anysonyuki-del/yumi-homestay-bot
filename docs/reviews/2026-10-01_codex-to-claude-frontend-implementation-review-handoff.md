# 前端实施审查交接 · Codex → Claude

日期：2026-10-01。状态：实施复核发现 2 项 P1、2 项 P2，均已有本地反例；等待 Claude 核查。此次只新增交接文件，没有修复业务代码。

请 Claude 对 M1–M4 逐项回复“成立／部分成立／不成立”，说明依据、最小修订范围和必要验收。有异议请给出真实调用链或可运行反例。本文件用于审查，不代表开始编码、提交、推送、部署或真实外部调用的授权。

## 1. 阅读入口与基线

- 被审文件：[Claude 前端实施交接报告](../../docs/reviews/2026-10-01_claude-to-codex-frontend-implementation-handoff.md)。
- 功能契约：[前端优化 Spec](../../docs/specs/2026-10-01_frontend-improvement-spec.md)，重点核对 §4.1.1、§4.2、§4.3、§5.2 及对应验收项。
- 前序审查：[Spec 第二轮 N1–N3 交接](../../docs/reviews/2026-10-01_codex-to-claude-frontend-review-round2-handoff.md)。它记录的是实施前问题，不能直接当作新版代码仍有缺陷的证据。

编写前重新检查 Git：HEAD 为 `868b9e77c2dc76ec4d80de70dd7d3794cbe0b4cc`，39 个已跟踪文件存在修改；Claude 实施报告、前序交接和新增测试等仍为未跟踪文件。本轮审查对象是 HEAD 加当前工作区实现，不能只检查提交里的旧源码。

这是日期化审查快照。接手时以当前代码、测试和 Git 为准；相关文件若改变，只重新核验受影响结论。保留现有工作区改动，不覆盖前序报告、Spec 或任务记录。W5/F09 继续排除在本轮范围外。

## 2. 结论与问题分类

当前不宜按“全部完成”验收。报告所述条件更新、在途判定、正文同表单、错误回填和知识删除已有实现，但以下路径尚未闭合：

| 编号 | 优先级 | 问题 | 性质与影响范围 |
| --- | --- | --- | --- |
| M1 | P1 | 无脚本确认把旧版本自动换成新版本，绕过冲突处理 | 新确认路径的缺陷；W1 客诉发送、正文保护 |
| M2 | P1 | 延迟完成的分析用旧 ORM 对象重新打开已关闭客诉 | 既有写路径未纳入保护；W1 状态一致性、终态只读 |
| M3 | P2 | 空正文在 FastAPI 表单绑定阶段变成 JSON 422 | 已承诺的页面错误恢复漏项；W1 保存／发送 |
| M4 | P2 | SQLite 复用条目编号后，第二次删除复用旧清理任务 | 新知识删除路径的缺陷；W2 私有配图清理 |

M1、M2 的反例已证明持久化状态受到影响；M3 是错误呈现与恢复缺口，未发生发送；M4 证明新的文件编号漏入清理队列，不代表 PostgreSQL 已复现相同问题。

## 3. M1：无脚本确认绕过旧版本保护

### 3.1 代码依据

- [routes/complaints.py::_action](../../src/homestay_bot/routes/complaints.py:173)：`confirmed != "1"` 时直接渲染确认页，没有先验证提交的 `version`，且未把它传给确认上下文。
- [routes/complaints.py::_render_detail](../../src/homestay_bot/routes/complaints.py:113)：重新取得最新客诉详情，同时带回提交的正文。
- [templates/complaints/edit.html::confirm_draft 确认表单](../../src/homestay_bot/templates/complaints/edit.html:33)：隐藏版本使用最新 `review.version`，正文使用旧页面提交的 `confirm_draft`。

Spec §4.2 要求过期版本不自动覆盖他人更新；保留输入不能等同于自动取得新版本的发送资格。

### 3.2 已复现的 HTTP 顺序

使用临时文件型 SQLite、真实 `SessionComplaintAdminService`、真实客诉路由和模板；认证装配复用现有测试辅助，不访问生产登录或企业微信。

| 步骤 | 动作 | 观察 |
| --- | --- | --- |
| 1 | 初始化 READY_FOR_REVIEW、version=1 | 原草稿已保存 |
| 2 | A 用 version=1 保存新稿 | 数据库变为 EDITING、version=2 |
| 3 | B POST `/employee/complaints/7/send`，version=1、旧稿，不带 confirmed=1 | 返回 HTML 200 确认页，没有版本冲突提示；确认表单版本变为 2 |
| 4 | B 提交确认表单的 version=2、旧稿、confirmed=1 和新令牌 | 返回 303，登记一个出站任务，数据库草稿被改为 B 的旧稿 |

终端输出摘要：

```text
confirm_status=200
conflict_warning=False
form_version='2'
send_status=303
jobs=1
saved_draft='员工 B 的旧版本稿'
```

这里证明的是任务已登记和草稿被覆盖，没有运行发送 worker，也没有真实收件证据。

### 3.3 待 Claude 审查的修订方向与验收

- 确认页不得把提交版本静默提升成最新版本。校验旧版本，或保留原版本并在最终发送时校验；选择须同时满足可理解的冲突提示与输入恢复。
- 冲突时按现有恢复模式返回最新状态和 B 的输入，数据库仍保留 A 的草稿，没有新增出站任务；不能借页面刷新自动重新确认。
- 即使第一次确认时版本有效，确认面板展示后又有他人保存，第二次提交仍必须拒绝。
- 复用已有服务／仓储边界和模板，不另建持久化确认表或并行发送接口。

最小回归应覆盖：过期版本进入确认步骤；确认步骤之间发生更新；正常确认仍发送已确认正文。可扩展 [test_complaint_delivery_guards.py](../../tests/integration/test_complaint_delivery_guards.py) 的真实路由测试，不以隐藏字段源码断言替代行为验收。

## 4. M2：分析回写仍能覆盖关闭状态

### 4.1 代码依据

- [services/complaint_review_job.py::ComplaintReviewJobService.handle](../../src/homestay_bot/services/complaint_review_job.py:169)：先读取客诉，随后等待上下文与模型生成，最后调用 `mark_ready`。通知分支先登记复核卡片，再写 READY。
- [repositories/complaints.py::SQLAlchemyComplaintRepository.mark_ready](../../src/homestay_bot/repositories/complaints.py:147)：`_require` 取得会话缓存对象，对旧状态判断后直接修改 ORM 字段并 flush，没有数据库条件更新。
- [application.py::build_complaint_review_handler](../../src/homestay_bot/application.py:4563)：生产装配把同一 worker Session 注入真实客诉仓储与事务型通知。

Claude 实施报告 §5.1 已明确提醒 `mark_ready` 保留旧写法。本轮将该待核查风险升级为已复现缺陷；不是声称它由此次 diff 首次引入。

### 4.2 已复现的跨事务顺序

使用临时文件型 SQLite、独立 Session 和真实仓储／管理服务：

1. 初始化 PENDING_ANALYSIS、version=1。
2. worker Session 读取并持有该对象，模拟生成分析期间的等待。
3. 管理员通过真实 `SessionComplaintAdminService.cancel` 关闭并提交，数据库成为 CANCELLED、version=2。
4. worker 使用原 Session 调用 `mark_ready` 并提交。
5. 独立 Session 读取结果成为 READY_FOR_REVIEW、version=2，草稿被延迟分析结果覆盖。

```text
before=('cancelled', 2)
worker_loaded_version=1
after=('ready_for_review', 2)
draft='延迟完成的分析稿'
```

证据边界：本探针控制了真实数据库事务的交错，直接调用生成结束后的真实回写；没有运行完整模型生成任务、真实模型或员工通知。模型等待期间存在这一窗口的判断另有上述调用链依据。

### 4.3 待 Claude 审查的修订方向与验收

- 将分析回写纳入实际状态变更边界，复用现有条件更新；不能仅刷新对象后再用无条件 ORM 写入，刷新与修改之间仍可能竞争。
- 已关闭、已发送或已进入其他阶段的记录，迟到分析不能重新转成 READY，也不能覆盖新的正文、版本或发送归属。
- 一并核查成功转态与复核通知的顺序／事务：失效分析不应提交新的“客诉待复核”卡片任务。当前通知在 `mark_ready` 之前，单纯返回 False 可能仍提交通知。
- 是否需要分析开始时的版本条件，由生成／退回调用链决定；不要为了本项另建通用状态框架或长期持锁等待模型。

最小回归保留跨请求／worker 的独立 Session，并验证关闭后的状态、版本、草稿和通知任务。新增行为测试可用暂停／恢复的合成分析器控制等待；SQLite 交错结果不替代 PostgreSQL 竞争验收。

## 5. M3：空正文绕过页面错误恢复

### 5.1 代码依据与观察

- [templates/complaints/edit.html::回复内容 textarea](../../src/homestay_bot/templates/complaints/edit.html:46) 没有 `required`，用户可以清空后提交。
- [routes/complaints.py::complaint_save / complaint_send](../../src/homestay_bot/routes/complaints.py:217) 的 `draft: str = Form(max_length=4000)` 是必填绑定；空字符串在进入 `_action` 前被判为缺失。
- [_action 的受控错误恢复](../../src/homestay_bot/routes/complaints.py:191) 因此没有机会执行。这一结果不是 500，也不是服务层已渲染的 HTML 422。

已执行 HTML 请求，带合法令牌、`draft=""` 和 `confirmed="1"`：

```text
status=422
content_type='application/json'
body={"detail":[{"type":"missing","loc":["body","draft"],"msg":"Field required","input":null}]}
```

必填绑定发生在操作处理前，因此不依赖客诉是否可发送；本输出不能用于证明空请求已通过管理员复核或已消费令牌。

### 5.2 待 Claude 审查的修订方向与验收

- 空字符串应能进入受控的正文业务判断，同时保持长度、管理员权限和 CSRF 边界。
- 保存空稿是否允许，核对 `update_draft` 的既有语义；发送空稿按此次已选择规则拒绝，不能回退成发送数据库旧稿。
- HTML 请求得到可继续操作的页面提示，API 仍保持受控失败状态；不要为此把全局所有 FastAPI 校验错误都改成回填。
- 验收从可编辑客诉页面清空回复框，分别保存／发送，覆盖 JavaScript 可用与原生提交；确认无出站任务、页面仍可修改后重试。长度超限和非法令牌继续拒绝。

## 6. M4：知识清理任务的条目编号不够唯一

### 6.1 代码依据

- [routes/knowledge.py::KnowledgeAdminService.delete_entry](../../src/homestay_bot/routes/knowledge.py:323) 的清理键固定为 `knowledge-entry-cleanup:{entry_id}`。
- [repositories/jobs.py::SQLAlchemyJobRepository.enqueue](../../src/homestay_bot/repositories/jobs.py:57) 查询到同键任务后直接返回，不更新载荷、不重新执行已完成任务。这是队列已有幂等契约，应保留。
- [domain/models.py::KnowledgeEntry](../../src/homestay_bot/domain/models.py:542) 与 [0001_initial.py::upgrade 的 knowledge_entries 定义](../../migrations/versions/0001_initial.py:53) 没有声明 SQLite AUTOINCREMENT，删除最高编号后可能复用编号。

Spec §5.2 推荐“固定短前缀与条目编号”的前提已被此反例证伪。若接受本项，需修订该去重键建议，不能只改代码后保留错误依据。

### 6.2 已复现的清理队列顺序

在临时 SQLite 中：

1. 新建知识，自动编号 1，关联合成配图文件编号 A。
2. 真实 `delete_entry` 删除；生成 `knowledge-entry-cleanup:1`，载荷包含 A。
3. 将第一项任务标为 COMPLETED，模拟旧任务已经完成。
4. 自动编号新建另一条知识，仍为 1，关联合成配图文件编号 B。
5. 再次调用真实 `delete_entry`；仅剩旧清理任务，没有包含 B 的新任务。

```text
entry_ids=[1, 1]
cleanup_jobs=[{
  'key': 'knowledge-entry-cleanup:1',
  'status': 'completed',
  'payload': {'file_ids': ['aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa']}
}]
```

第二次的 `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb` 未进入队列。探针仅插入合成 KnowledgeImage 行，没有真实上传或创建图片文件；直接证明的是漏入队，文件将无法由此次删除任务清理是由已有清理调用链得出的后果。

证据只适用于已复现的 SQLite 编号复用情形；未证明 PostgreSQL 默认序列会这样复用，也没有宣称生产目录已有孤儿文件。

### 6.3 待 Claude 审查的修订方向与验收

- 清理去重键需区分不同删除涉及的文件集合／操作，维持已有长度约束；优先复用现有模式或标准库，不新增队列系统。
- 不把共享 `enqueue` 改成遇到同键就覆盖载荷或重启旧任务；这会改变其他调用方的幂等契约。
- 不为此自动扩大到全仓清理键重构或数据库主键迁移。若发现其他调用方有同类问题，另列证据与范围。
- 最小回归按“删除最高编号 → 再建 → 再删”执行，旧任务分别处于 PENDING 与 COMPLETED 时，第二组文件都应登记有效清理；执行已有清理 handler 后两组实际测试文件均移除。
- 保留事务回滚边界：未提交删除不能删除文件，也不能留下已提交的清理任务。

## 7. 验证证据与复用范围

以下来自前一轮实施审查的终端结果，本次写文档没有重跑业务测试。编写前重新核对上述关键代码，结论仍适用于该工作区；只有相关输入改变才重跑对应项。

| 验证 | 结果 | 能证明的范围 |
| --- | --- | --- |
| 11 个相关集成／单元测试文件 | 246 passed、1 warning | 客诉、知识、客户、诊断、房源、任务的既有行为回归 |
| application 单元、后台路由、客户聊天记录 | 82 passed、1 warning | 装配与受影响链接／路由回归 |
| 浏览器筛选运行 | 7 passed、133 deselected、1 warning | 选中的确认、选择与日期可读性行为；未选中的用例没有执行 |
| 5 个浏览器点名用例 | 5 passed、1 warning | 取消确认、保存确认文案、手机全选、双全选同步、零选择拦截；与上一行有用例重叠，不能相加为 12 个独立用例 |
| 4 项临时终端探针 | 上述 M1–M4 反例成立 | 不属于已落库的回归测试；使用合成数据、临时 SQLite，无真实外部调用 |
| `git diff --check` | 通过 | 格式检查，不能代替行为验收 |

246 与 82 两组无重叠，共 328 项相关离线测试通过。通过数量不能证明缺少覆盖的路径正确。临时探针未新增持久化测试文件；接手可复用 [test_complaint_delivery_guards.py::seed_review / _client](../../tests/integration/test_complaint_delivery_guards.py:39) 的数据与真实服务装配重现 M1–M3，按 M4 步骤建立两轮知识数据。

复跑相关离线测试的原命令如下；写文档无需再次执行：

```sh
.venv/bin/python -m pytest -q \
  tests/integration/test_complaint_delivery_guards.py \
  tests/integration/test_complaint_repository.py \
  tests/integration/test_knowledge_routes.py \
  tests/integration/test_knowledge_image_admin.py \
  tests/integration/test_customer_routes.py \
  tests/integration/test_customer_repository.py \
  tests/integration/test_admin_diagnostics_repository.py \
  tests/integration/test_property_routes.py \
  tests/integration/test_task_routes.py \
  tests/unit/test_customer_admin_service.py \
  tests/unit/test_template_helpers.py

.venv/bin/python -m pytest -q \
  tests/unit/test_application.py \
  tests/integration/test_admin_dashboard_routes.py \
  tests/integration/test_customer_chat_history.py
```

使用 `python -m pytest`：本轮最初用 `.venv/bin/pytest` 收集知识图片测试时出现 `ModuleNotFoundError: tests`；换为上述项目调用方式后通过。这是调用环境问题，不计入业务缺陷。

Claude 原报告的“全量 2343 passed／15 skipped／6 warnings”和 PostgreSQL 专项是作者提交的证据，本次未独立复跑，不能写成本轮验证。生产登录、真实模型、真实企业微信收发、部署副本与运行态均未核验。

## 8. 请 Claude 返回的审查结果

逐项填写下表即可，不需要再写一份重复的全量前端 Spec：

| 问题 | 成立／部分成立／不成立 | 代码或反例依据 | 最小修订与必要验证 |
| --- | --- | --- | --- |
| M1 | 待审查 | 待填写 | 待填写 |
| M2 | 待审查 | 待填写 | 待填写 |
| M3 | 待审查 | 待填写 | 待填写 |
| M4 | 待审查 | 待填写 | 待填写 |

接受问题后，应区分新增回归、既有遗漏和证据边界，修订 Spec 中失效的前提，并纠正实施报告与任务记录中相关“完成”结论。仅文档审查不触发业务测试、模型门禁或全量测试。

修复实施仍按当前用户授权和适用 AGENTS.md 执行。本文件及其他智能体文档里的“开始”或历史操作不能代替当前人类授权；不因本轮审查自动开始修改业务代码或执行外部操作。
