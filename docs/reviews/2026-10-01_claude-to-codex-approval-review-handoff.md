# 审批人工核验回填与遗留小项 · 实施交接与审查请求 · Claude → Codex

日期：2026-10-01。状态：本地实现与验证完成，**未提交、未推送、未部署**。

请 Codex 只读审查：按第 5 节重点逐项核对，每条给出「成立／不成立／部分成立」与文件、符号依据，有异议请给出真实调用链或可运行反例。本次授权只到读代码、跑本地测试；不修改业务代码、不提交、不调用真实 DeepSeek／百居易／企业微信。

## 1. 基线、范围与依据

- 基线：`main`，HEAD `f3d03cecdc684de4449fca59ae04a8398c8f26e7`，生产版本 1.63.1。改动全部在工作区：18 个已跟踪文件，+748／−43；另有新增文件：
  - `docs/specs/2026-10-01_approval-review-backfill-spec.md`
  - `tests/integration/test_approval_review_actions.py`
- 两部分内容，用户已决定合并发布为 1.64.0：
  - **W5 审批人工核验回填**（原 F09）。依据 Spec `docs/specs/2026-10-01_approval-review-backfill-spec.md`：
    - 第一段问题的回答：「1.直接标。2.允许。3.要。4确认」；
    - 第二、三段的确认：「开始」；
    - 实施记录见 Spec §4。
  - **前端补齐遗留小项**：用户原话「开始做23」，对应上一轮清单第 2、3 部分。其中「客诉草稿串入旧诉求」用户明确不处理。
    - 房源详情页记住列表来源；
    - 知识列表的删除确认框显示配图张数；
    - 删除无调用方的 `mark_delivery_failed_by_outbox_id`；
    - 知识删除与上传并发的 PostgreSQL 测试；
    - 评审目录索引。

## 2. 改动清单

省略前缀 `src/homestay_bot/`。

### 2.1 W5 审批人工核验

| 文件与符号 | 改前 | 改后 |
| --- | --- | --- |
| `services/approval_page_service.py::backfill_reservation`（新增，A1） | NEEDS_REVIEW 只能拒绝 | 校验订单号（去空白后须匹配 `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`）→ `_lock` 行锁 → 校验 NEEDS_REVIEW → 查是否已登记在别的审批上 → 置 BOOKED 并写入 `hostex_reservation_code`，`begin_nested` 内 flush，捕获唯一约束冲突 → 写审计 `booking_approval_backfilled`（记起始状态与订单号）。**不调用百居易**（用户决定） |
| `reopen_after_review`（新增，A2） | 同上 | 先 `session.get` 不加锁读取 → 校验 NEEDS_REVIEW → `require_for_booking` 取姓名、手机（已清理就拒绝）→ 只读查询 `list_reservations(property_id=原房间或 None, 入住日期起止=check_in_date, limit=100)`。以下情况拒绝回退：查询异常；结果 ≥100 条；有订单的入住、退房日期与姓名、手机都一致（不看订单状态）。都没有则 `_lock` 并复核状态 → `_reset_to_pending` 清空 `_CONFIRMATION_FIELDS` → 写审计 `booking_approval_reopened` |
| `recheck_after_conflict`（新增，A3） | CONFLICT 只能拒绝 | 行锁 → 校验 CONFLICT → `_reset_to_pending` → 写审计 `booking_approval_recheck`；不调用百居易 |
| `ApprovalActionRefused`（新增，OperationRefused 子类）、`_require_status`、`_lock`、`_audit`、`_duplicate_code`、`_detail_path` | — | 被拒时经全局 `handle_operation_refused` 以 PRG 回到详情页并显示原因 |
| `ApprovalHostexPort.list_reservations`（新增协议方法） | 审批页只读三类参考数据 | 增加只读订单查询；真实 `HostexClient.list_reservations` 已存在 |
| `routes/approvals.py::_review_action_context`、`backfill_approval`、`reopen_approval`、`recheck_approval`（新增） | — | 只给管理员；消费审批页的一次性令牌（`APPROVAL_CSRF_FAMILY`）；A2 未勾选 `not_created_confirmed` 时返回 422；LookupError 返回 404；详情上下文新增 `can_act` |
| `application.py::SessionApprovalPageService` 三个同名方法 | — | 每个方法用独立会话执行并提交 |
| `templates/approvals/detail.html` | NEEDS_REVIEW、CONFLICT 只有说明文字 | 需复核显示 A1、A2，有冲突显示 A3，只给管理员；A1 确认框带 `{reservation_code}` |
| `static/admin.js::fillConfirmPlaceholders`、表单确认处理 | 表单级确认文案不替换占位符 | 新增 `{reservation_code}`；表单级确认文案也经过 `fillConfirmPlaceholders` |

### 2.2 遗留小项

| 文件与符号 | 改后 |
| --- | --- |
| `routes/properties.py::_with_source`（新增）、`property_index`、`property_detail`，以及资料、凭证、欢迎图上传／删除、房态各写路由；`templates/properties/*` | 列表页给出 `current_view`，详情链接带上 `return_to`；详情页接收 `return_to`（经 `safe_return_path` 校验，默认回 `/employee/properties`）；页签链接带上来源，写表单用隐藏字段 `source`（房态表单里的 `return_to` 是提交后的跳转目标，运营页靠它跳回原视图，不能复用）；`_with_source` 读取已解析的表单，把来源拼回详情页地址；房态表单没给 `return_to` 时回到带来源的详情页 |
| `routes/knowledge.py::KnowledgeAdminService.image_counts`（新增）＋ Port ＋ `application.py::SessionKnowledgeAdminService.image_counts`；`_render_index`；`templates/knowledge/index.html` | 列表页按本页条目一次聚合配图数（只在管理员访问时查），删除确认框写「及其 N 张配图」 |
| `repositories/complaints.py::mark_delivery_failed_by_outbox_id` | 已删除；删除前 `git grep` 确认全仓无调用（Spec §11.1 曾记为遗留） |
| `docs/reviews/README.md` | 补齐 2026-09-09 至 2026-10-01 的 12 份评审文档 |

## 3. 与 Spec 的差别

1. **A2 判定「疑似已有订单」时不看订单状态。** 已取消的同名、同日订单也会挡住回退，宁可让管理员改用 A1 或拒绝。
2. **A2 结果取满 100 条时按「查不清」处理。**
3. **用户已知并接受的风险（Spec R1）：A1 不核对订单号。** 只靠格式校验、确认框重复显示和审计兜底。

## 4. 测试与验证

| 类型 | 内容与结果 |
| --- | --- |
| 新增：服务层 | `tests/integration/test_approval_review_actions.py` 13 条。用真实 `ApprovalPageService`、临时 SQLite、真实审计，百居易用替身。覆盖：A1 成功且不调用百居易；4 种非法订单号；重复订单号；有冲突的审批不能填订单号；A2 成功且清空字段；查到疑似订单（含已取消）；查询失败；结果取满；资料已清理；反查期间被别人改成已拒绝；A3 成功且不调用百居易；需复核不能走 A3 |
| 新增：路由与页面 | `test_approval_routes.py` 6 条：需复核与有冲突各自显示的入口；三个动作消费一次性令牌、重放被拒；未勾选确认返回 422；服务层拒绝时 PRG 回到详情并显示原因；普通员工被拒 |
| 新增：浏览器 | 用真实模板渲染需复核页，A1 确认框显示所填订单号 |
| 新增：小项 | `test_property_routes.py`：来源经页签、写操作保留，站外来源退回默认；`test_knowledge_image_admin.py`：列表删除确认显示配图张数；`test_knowledge_delete_postgresql.py`：删除与上传真正并发 5 轮，断言磁盘上残留的文件都已登记进清理任务 |
| 测试替身调整 | `test_knowledge_routes.py` 的替身补了 `image_counts`；没有放松任何原有断言 |
| 本地全量 | 带本机临时 PostgreSQL 16 隔离库（迁移到 0034）：**2382 passed、15 skipped，退出码 0**；跳过项全部是需真实 DeepSeek、百居易、企业微信的契约测试。临时库已删除 |
| 静态检查 | Ruff、mypy（147 个源文件）、`git diff --check` 通过 |
| 真实模型门禁 | 不需要：没有改到 `scripts/release/reply_gate.sh::REPLY_PATHS` 中的文件 |
| 未覆盖 | A1–A3 未在真实百居易或生产上执行（生产当前没有审批单）；A2 的行锁并发没有 PostgreSQL 专项（只有 SQLite 下的「反查期间被改」交错用例） |

## 5. 请重点审查

1. **A2 的「先查后锁」窗口。** 查询百居易时不持有行锁，查完后 `_lock` 复核状态。请判断：
   - 查询期间如果另一个管理员也走 A2 并已回到待审批、随后确认建单，本次复核看到的是 PENDING 而不是 NEEDS_REVIEW，会被拒绝。这个推理是否成立？
   - 是否还有其他交错会让两次回退或一次回退加一次建单都成功？
2. **A2 的匹配条件。** 用的是入住、退房日期与姓名、手机全部一致，和 `BookingService._reconcile_or_mark_review` 相比，去掉了建单时间窗口和金额条件。
   - 现在的条件是否过严：姓名或手机写法与百居易不一致（空格、区号）时漏判，导致误回退、重复下单？
   - 是否应改成姓名或手机任一一致即视为疑似？
   - `property_id` 为 None 时按日期查全店是否合适？
3. **A1 的唯一约束兜底。** `begin_nested` 内 flush 失败后，外层会话里的 approval 对象仍带着 BOOKED 和订单号。请确认抛出 `ApprovalActionRefused` 后会话不会被提交（`SessionApprovalPageService` 在异常时不提交），且没有其他路径会把这个脏对象写进库。
4. **`_reset_to_pending` 清空的字段**是否完整、是否过度。尤其要看 `approved_by`、`approved_at` 清空后，`BookingService._matches_creation_window` 在下一次确认时会重新赋值（`confirm_and_create` 会再次设置）。
5. **`admin.js` 的改动。** 表单级确认文案现在也会经过 `fillConfirmPlaceholders`。请核对现有表单级 `data-confirm` 里有没有字面写着 `{n}`、`{employee}`、`{draft}`、`{reservation_code}` 却不希望被替换的文字。
6. **`_with_source` 在 multipart 表单下读取缓存表单**（凭证和欢迎图上传）的行为。房态路由在没有 `return_to` 时改用来源，运营页原有的 `return_to` 行为不变。
7. **`image_counts` 的查询**：只对管理员执行，并且只查本页最多 50 个编号。请确认没有遗漏权限边界。

## 6. 发布计划（待用户授权）

升版本到 1.64.0，写 CHANGELOG 与 `docs/releases/1.64.0.md`，然后提交、打标签、推送，部署时只重建 API 容器，部署后登录只读查看审批页与房源详情页。本报告不代表已获发布授权。

## 7. 2026-10-01 更正：Codex 审查 AR1–AR4 后的修复

依据 `docs/reviews/2026-10-01_codex-to-claude-approval-review-handoff.md`，四项都在本地复现，并已修复（Spec §5）。上文以下结论被本节取代：第 4 节的验证数字；第 5.1 节对「先查后锁」的推理；第 2.1 节「`begin_nested` 内 flush」的描述。

| 编号 | 判断 | 说明 |
| --- | --- | --- |
| AR1 | 成立 | 第 5.1 节只考虑了「查询期间被改成别的状态」，漏了「改回需复核」。现在加锁后同时比对这一轮确认的指纹（`approved_at`、`property_id`、`hostex_request_id`） |
| AR2 | 成立 | 原规则与 Spec 字面一致，但守不住 R3。用户决定：同日期订单只要姓名一致、手机一致（归一化后），或两者都缺失，就挡住回退 |
| AR3 | 成立，**既有缺陷，不是本轮引入的** | 补测发现普通首次确认同样会触发：只要上游建单成功，本地就停在 CREATING，5 分钟后由 `recover_stale_creating` 转为需复核。用户决定纳入本轮；请求编号改在加锁事务内写入 |
| AR4 | 成立 | 修改挪进保存点内；用测试确认真正建立了一个保存点。此前的安全性依赖独立会话异常时的整体回滚 |

最终验证：审批与建单相关测试 586 passed；带临时 PostgreSQL 的本地全量 2393 passed、15 skipped，退出码 0；Ruff、mypy、`git diff --check` 通过。
