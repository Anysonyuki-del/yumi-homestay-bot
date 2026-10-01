# 预订审批人工核验回填 Spec（W5 / F09）

日期：2026-10-01。状态：三段已确认，已实施并通过本地验证（见 §4），未提交。

来源：`docs/specs/2026-10-01_frontend-audit-handoff.md` F09；`docs/specs/2026-10-01_frontend-improvement-spec.md` D6（当时决定本轮不做，留待单独立项）。

## 1. 第一段：现状证据（已确认）

- `services/booking_service.py::BookingService.confirm_and_create`：仅 PENDING 可建单。确认时先查实时房态：
  - 不满足：置 CONFLICT，不建单，且不写入 property_id；
  - 满足：置 CREATING，向百居易发一次建单请求（不自动重放），再按房间、日期、姓名、手机、金额、建单时间窗口反查。唯一命中置 BOOKED，否则 NEEDS_REVIEW。
  - 非 PENDING 一律不再建单，这是防重复下单的刻意设计。
- `templates/approvals/detail.html`：NEEDS_REVIEW、CONFLICT 只提示人工核对，唯一的写入口是拒绝。
- `services/approval_page_service.py::reject` 是现成的写操作范式：路由校验管理员角色与一次性令牌，服务层行锁读取、校验状态、写审计。
- `domain/models.py::BookingApproval.hostex_reservation_code` 有唯一约束 `uq_booking_hostex_reservation_code`。BOOKED 在下游不触发任何动作。
- `integrations/hostex_client.py::ReservationQuery` 支持按房间、入住日期、订单号查询。
- 生产当前没有审批单（2026-10-01 只读查看）。

### 用户决定（2026-10-01）

| 问题 | 原话 | 决定 |
| --- | --- | --- |
| 需复核且百居易已有这单 | 「1.直接标」 | 管理员填入订单号即标为已预订，不再向百居易核对 |
| 需复核但确认百居易没建成 | 「2.允许」 | 允许回到待审批重新确认，回退前再查一遍防重 |
| 有冲突 | 「3.要」 | 提供重新检查房态的入口 |
| 结果查不清 | 「4确认」 | 保持需复核 |

## 2. 第二段：功能点（已确认）

| 编号 | 起始状态 | 动作 | 结果 |
| --- | --- | --- | --- |
| A1 回填订单号 | NEEDS_REVIEW | 管理员填写百居易订单号并确认 | BOOKED，写入 `hostex_reservation_code`；不调用百居易 |
| A2 重新确认 | NEEDS_REVIEW | 管理员勾选「已在百居易后台确认没有这笔订单」 | 系统先按入住日期（有房间时加上房间）查百居易订单：查到日期、姓名、手机都一致的订单就拒绝回退，并提示改用 A1；查询失败也拒绝回退；都没有才回到 PENDING，清空上次确认填写的房间、金额、收款方式、审批人、请求编号和失败信息，由管理员重新填写确认 |
| A3 重新检查房态 | CONFLICT | 管理员点「回到待审批重新确认」 | 回到 PENDING，不调用百居易；房态在下一次确认时照常实时检查，不满足会再次成为 CONFLICT |

公共规则：
- 三个动作只给管理员，沿用审批页的一次性令牌和拒绝动作的写法：行锁读取、校验状态、同一事务写审计。
- 审计只记动作、起止状态和订单号，不记客人信息。
- 拒绝入口保持不变。

## 3. 第三段：风险与决策（已确认）

| 编号 | 风险 | 处理 |
| --- | --- | --- |
| R1 | A1 不核对，填错订单号会把审批标成已预订，但实际没有这单 | 按用户决定不核对；页面写明「请从百居易后台复制订单号」，并在确认框里重复显示所填订单号；审计记下订单号，便于追查 |
| R2 | 同一订单号被回填到两张审批单 | 提交前先查重并给出可读提示；并发时由数据库唯一约束兜底，转成同样的提示，不报 500 |
| R3 | A2 回退后再次确认，可能重复下单 | 回退前按日期、姓名、手机反查；有疑似订单或查询失败一律不回退；回退后再确认仍走现有房态检查与建单后反查 |
| R4 | A2 的反查要调用百居易只读接口 | 属于产品正常功能（与建单后反查同一接口），不是本次开发的外部调用；本地测试一律用替身 |
| R5 | 两位管理员同时操作同一审批 | 行锁串行，后者按新状态校验，状态不对则拒绝 |
| R6 | 回退会清掉上次确认填写的金额与房间 | 有意为之：防止拿旧金额直接重复下单；审计保留回退记录 |

数据与迁移：不新增表和字段，无迁移。

验证计划：
- 服务层：三个动作的状态转移、拒绝条件、审计；A2 用百居易替身覆盖三种情况——查到疑似订单、查询失败、没有订单；A1 覆盖重复订单号；
- 路由：权限、一次性令牌、错误提示不出现 500；
- 页面：需复核、有冲突两种状态下显示对应入口。
- 改动不在真实模型门禁清单内；若改到 `application.py` 装配，提交前跑本地全量。

确认记录：2026-10-01 用户对第一段问题逐项回复「1.直接标。2.允许。3.要。4确认」；第二、三段回复「开始」。

## 4. 实施记录（2026-10-01）

省略公共前缀 `src/homestay_bot/`。

| 文件与符号 | 改动 |
| --- | --- |
| `services/approval_page_service.py::backfill_reservation` | A1：订单号去空白后按字母数字、连字符、下划线校验；加锁后校验 NEEDS_REVIEW；先查重，并发时在保存点里捕获唯一约束；标为 BOOKED 并写审计 `booking_approval_backfilled` |
| `services/approval_page_service.py::reopen_after_review` | A2：先不加锁读取，校验 NEEDS_REVIEW 与客人资料未清理；按入住日期（有房间时带上房间）只读查询百居易；查到入住、退房日期与姓名、手机都一致的订单（不论订单状态）、查询失败或结果取满 100 条，都拒绝；然后加锁复核状态，回到 PENDING，清空上次确认字段，写审计 `booking_approval_reopened` |
| `services/approval_page_service.py::recheck_after_conflict` | A3：加锁校验 CONFLICT，回到 PENDING，清空上次确认字段，写审计 `booking_approval_recheck`；不调用百居易 |
| `ApprovalActionRefused`（OperationRefused 子类）、`ApprovalHostexPort.list_reservations` | 拒绝文案写给管理员看，经全局处理器回到详情页并显示原因；审批页的百居易接口加上只读订单查询 |
| `routes/approvals.py::backfill_approval/reopen_approval/recheck_approval` | 只给管理员；消费审批页一次性令牌；A2 要求勾选「已确认没有这笔订单」 |
| `application.py::SessionApprovalPageService` | 三个方法各用独立会话，提交审计 |
| `templates/approvals/detail.html`；`static/admin.js::fillConfirmPlaceholders` | 需复核页显示 A1、A2，有冲突页显示 A3，只给管理员；确认框重复显示所填订单号（表单级确认文案也支持占位符） |

与本 Spec 的差别：
- A2 的疑似订单判定不看订单状态，已取消的同名同日订单也会挡住回退——宁可让管理员改用 A1 或拒绝；
- A2 同一入住日订单取满 100 条时按查不清处理。

验证：
- 新增 `tests/integration/test_approval_review_actions.py` 13 条，用真实服务和临时 SQLite，百居易用替身；
- `test_approval_routes.py` 新增 6 条；浏览器用例 1 条（确认框显示订单号）；
- 审批相关 57 条通过；Ruff、mypy、`git diff --check` 通过；
- 带临时 PostgreSQL 的本地全量 2382 passed、15 skipped（均为真实外部契约），退出码 0。

## 5. Codex 实施审查后的修订（2026-10-01）

依据 `docs/reviews/2026-10-01_codex-to-claude-approval-review-handoff.md` AR1–AR4，四项均在本地复现。用户决定「全修，直接做完」：AR2 按下述规则，AR3 纳入本轮。

| 编号 | 问题 | 修订 |
| --- | --- | --- |
| AR1 | A2 先查后锁只复核状态；查询期间另一轮「回退→确认→需复核」完成后，旧查询仍能回退，造成第二次建单 | 查询前记下这一轮确认的指纹（`approved_at`、`property_id`、`hostex_request_id`），加锁后指纹与状态都须一致，否则拒绝。每次确认都会写入新的 `approved_at`，不新增字段 |
| AR2 | A2 只把日期、姓名、手机全一致视为疑似，手机带区号或缺失时放行 | **修订 A2 与 R3**：入住、退房日期都相同的订单，满足任一即挡住回退——姓名一致（去空白、不分大小写）；手机一致（只比数字，去掉 +86／86／0086 前缀）；订单姓名与手机都缺失。代价：同日同名不同人也会被挡，管理员改用 A1 或拒绝 |
| AR3 | 既有缺陷，非本轮引入：建单成功后在事务外给审批写 `hostex_request_id`，会话自动开启事务，随后显式开事务报「A transaction is already begun」，审批停在 CREATING。首次确认同样触发 | 请求编号随写后核验在同一加锁事务内写入；事务外不再修改审批对象。建单前提交 CREATING 的防重边界不变 |
| AR4 | A1 的修改在建立保存点之前就已 flush，保存点没有真正建立 | 修改挪进保存点内；更正 §4 中「保存点内 flush」的描述：此前的安全性依赖异常时独立会话整体回滚 |

修订实施与验证：
- 代码：
  - `services/booking_service.py::confirm_and_create/_reconcile_or_mark_review/_mark_needs_review`（AR3，`request_id` 在加锁事务内写入）；
  - `services/approval_page_service.py`：`_attempt_fingerprint`、`_maybe_same_guest`、`_normalize_name`、`_normalize_phone`、`reopen_after_review`（AR1、AR2）、`backfill_reservation`（AR4）。
- 测试：`tests/integration/test_approval_review_actions.py` 新增 11 条。
  - AR1：完整的「需复核→待审批→创建中→需复核」交错；
  - AR2：7 种身份写法；
  - AR3：经生产会话门面确认到「已预订」、写后核验失败转「需复核」并保留请求编号、已预订不重复建单；
  - AR4：真实保存点里的唯一键竞争。
- 复现与结果：Codex 的 E1–E4 在修复前全部复现；修复后 E1 被拒，E4 的同类首次确认最终为 BOOKED。
- 测试结果：审批与建单相关测试 586 passed；带临时 PostgreSQL 的本地全量 2393 passed、15 skipped（均为真实外部契约），退出码 0；Ruff、mypy、`git diff --check` 通过。

## 6. Codex 第二轮审查后的修订（2026-10-02）

依据 `docs/reviews/2026-10-02_codex-to-claude-approval-review-round2-handoff.md` AR5–AR7。E5、E6 在本地复现。用户决定「直接改完」。

| 编号 | 问题 | 修订 |
| --- | --- | --- |
| AR5 | SQLite 的 Python 驱动不会因普通查询开启物理事务，A1 的保存点成为最外层，释放即提交；之后审计失败也无法撤回已写的 BOOKED。PostgreSQL 不受影响 | A1 不再使用保存点。状态、订单号与审计同一次 flush，唯一键冲突转为受控拒绝，由调用方会话整体回滚；提交前查重保留 |
| AR6 | 既有并发缺口：①原建单仍在进行时，再次确认会立刻走恢复核验，查不到就转需复核，进而可被 A2 回退、发起第二次建单；②写后核验加锁后不核对轮次，迟到结果会覆盖新一轮的订单号与请求编号 | **修订 R3、R5**：<br>①「创建中」且确认时间不满 5 分钟（与后台 `recover_stale_creating` 同一阈值）时，再次确认原样返回，不核验、不改状态；满 5 分钟才走恢复核验。<br>②写后核验与转需复核的两个出口在加锁后从数据库重读，只有仍是 CREATING、且确认时间与本轮一致时才写入；否则丢弃迟到结果，不改任何字段。确认时间比较前统一时区 |
| AR7 | 区号测试用例的姓名与审批相同，走姓名分支就命中，测不到手机归一化 | 区号用例改用不同姓名；补「本身以 86 开头的 11 位号码」不被去前缀的用例 |

已知范围外风险，另立项：项目中另有十余处 `begin_nested`，在 SQLite 下有同样的提交语义问题。生产 PostgreSQL 不受影响，本轮不改共享引擎。

修订实施与验证（2026-10-02）：
- 代码：
  - `services/booking_service.py`：`CREATING_STALE_AFTER`、`_as_utc`、`_same_round`；`confirm_and_create`、`_reconcile_or_mark_review`、`_mark_needs_review` 加入 attempt；
  - `repositories/approvals.py::get_for_update` 加 `populate_existing`；
  - `application.py` 的 `recover_stale_creating` 改用同一阈值；
  - `services/approval_page_service.py::backfill_reservation` 去掉保存点。
- 测试：
  - `test_approval_review_actions.py` 改用项目 `create_engine` 与 `create_session_factory`，新增 AR5 两条、AR6 三条，AR7 调整参数并补两组；
  - 新增 `test_approval_review_postgresql.py`，在 PostgreSQL 上复用 9 条交错用例；
  - `test_approval_flow.py`：两条遗留恢复用例的确认时间改为超过阈值（新契约），并补「不满阈值原样返回」用例。
- 复现与反向验证：E5、E6 修复前复现，修复后不再成立；临时去掉 `_same_round` 后，AR6② 的两条用例失败。
- 测试结果：
  - 带临时 PostgreSQL 的全量：2407 passed、2 failed、15 skipped，两条失败即 `test_approval_flow.py` 中按旧契约写的用例；修改该文件后单独重跑 9 passed，其余源码未变，不重跑全量；
  - 审批 PostgreSQL 组 9 passed；
  - Ruff、mypy、`git diff --check` 通过。

测试补强（2026-10-02，依据 `docs/reviews/2026-10-02_codex-to-claude-approval-review-round3-handoff.md` AR8、AR9，均为测试判别力缺口，业务逻辑未改）：
- AR8：新增 `test_late_result_cannot_overwrite_a_newer_round_that_is_still_creating`（成功出口与失败出口两组）。旧一轮结果返回时，新一轮同样处在 CREATING、只是确认时间不同。断言旧结果被丢弃、新一轮全部字段不变、新一轮自己的结果照常写成 BOOKED。只去掉时间比较、保留状态检查时，两组都失败（旧结果写成 BOOKED／NEEDS_REVIEW）。该用例也加入 PostgreSQL 组：11 passed。
- AR9：新增 `test_an_eleven_digit_number_starting_with_86_keeps_its_prefix`；去掉前缀长度保护时该用例失败。
- 结果：审批相关测试 66 passed；Ruff、`git diff --check` 通过。本次只改测试，源码与上次全量相同，不重跑全量。
