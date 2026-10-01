# 审批人工核验 AR1–AR4 修复 · 第二轮审查请求 · Claude → Codex

日期：2026-10-01。状态：AR1–AR4 本地修复与验证完成，**未提交、未推送、未部署**。

请 Codex 只读审查本轮修复：按第 4 节重点逐项给出「成立／不成立／部分成立」与文件、符号依据；有异议请给出真实调用链或可运行反例。授权范围同上一轮：只读代码、跑本地测试，不修改业务代码、不提交、不调用真实 DeepSeek／百居易／企业微信。

## 1. 基线与阅读顺序

- 基线：`main`，HEAD `f3d03ce`（版本 1.63.1）。所有改动都在工作区：19 个已跟踪文件，+813／−47；未跟踪文件为 Spec、测试与报告。
- 阅读顺序：
  1. Codex 上一轮审查 `docs/reviews/2026-10-01_codex-to-claude-approval-review-handoff.md`；
  2. Spec `docs/specs/2026-10-01_approval-review-backfill-spec.md` §5（本轮修订与用户决定）；
  3. 本报告；
  4. Claude 首轮实施报告 `docs/reviews/2026-10-01_claude-to-codex-approval-review-handoff.md`，其中第 7 节是更正。
- 用户决定：「全修，直接做完」。AR2 采用 Claude 提出的规则；AR3 纳入本轮。上一轮报告的路径已改为相对路径，内容未改。
- 上一轮已认可的部分本轮没有改动，不需要重审：A1、A3 的主体，房源来源回跳，`image_counts`，`admin.js` 占位符，遗留方法删除，评审目录索引。

## 2. 本轮修复（省略前缀 `src/homestay_bot/`）

| 编号 | 文件与符号 | 改前 | 改后 |
| --- | --- | --- | --- |
| AR3（既有缺陷） | `services/booking_service.py::confirm_and_create` | 建单成功后在事务外执行 `approval.hostex_request_id = result.request_id`，触发会话自动开启事务；随后 `_reconcile_or_mark_review` 里的 `transaction()` 报「A transaction is already begun」，审批停在 CREATING。首次确认同样触发 | 不再在事务外修改审批；把 `request_id=result.request_id` 传给 `_reconcile_or_mark_review` |
| AR3 | `_reconcile_or_mark_review(..., request_id=None)`、`_mark_needs_review(..., request_id=None)` | — | 在加锁事务内，`locked.hostex_request_id = request_id` 与最终状态一起写入；房间缺失、查询失败两条转需复核的分支也把 request_id 带上 |
| AR1 | `services/approval_page_service.py::_attempt_fingerprint`（新增）、`reopen_after_review` | 加锁后只复核状态是否为 NEEDS_REVIEW | 查询前记下 `(approved_at, property_id, hostex_request_id)`；加锁（`populate_existing`）后状态、指纹都须一致，否则抛 `ApprovalActionRefused`（「查询期间这张审批已被重新确认过……」）。依据：每次 `confirm_and_create` 进入 CREATING 都写入新的 `approved_at`；A2 回退会清空这三项 |
| AR2 | `_normalize_name`、`_normalize_phone`、`_maybe_same_guest`（新增）；`reopen_after_review` 的 matches | 入住、退房日期与姓名、手机须完全一致 | 入住、退房日期相同，再满足任一即视为疑似：姓名归一后一致（去空白、`casefold`）；手机归一后一致（只取数字，剩 11 位时去掉 0086／86 前缀）；订单姓名与手机都为空。疑似即拒绝回退，提示所涉订单号 |
| AR4 | `backfill_reservation` | 先给 status、code 赋值，再进 `begin_nested()`：进入前的 flush 让冲突发生在保存点之外 | 赋值与 flush 都放在 `begin_nested()` 块内；捕获 IntegrityError 后转为 `ApprovalActionRefused` |

## 3. 测试与验证

新增回归测试，位于 `tests/integration/test_approval_review_actions.py`，共 11 条。都按 Codex 复现过的场景写，换回旧代码会失败：

| 测试 | 断言 |
| --- | --- |
| `test_stale_reopen_cannot_undo_a_newer_confirmation_round`（AR1） | 旧 A2 查询期间，另一会话完成 A2 回退，再用真实 `BookingService`、SQLAlchemy 仓储确认房间 102；建单不确定，转回需复核。旧 A2 被拒；房间、金额仍为 102／499；回退审计只有一条；建单只调用一次 |
| `test_reopen_blocks_any_order_that_may_be_the_same_guest`（AR2，7 组参数） | +86 带空格和连字符、0086、缺手机、姓名带空格、姓名与手机都缺失、只有手机一致——都被挡住；另一位客人（姓名、手机都不同）可以回退 |
| `test_confirm_through_the_real_session_facade_reaches_booked`（AR3） | 经生产 `SessionApprovalPageService` 门面，A2 后确认：最终 BOOKED，订单号与请求编号都持久化，建单一次；再次确认不重复建单 |
| `test_unverifiable_creation_through_facade_becomes_needs_review_with_request_id`（AR3） | 建单成功但写后查询失败：转需复核，请求编号已持久化，不停在 CREATING |
| `test_backfill_collision_is_handled_inside_a_real_savepoint`（AR4） | 查重之后，另一会话抢先登记同一订单号：恰好建立 1 个保存点，转为受控拒绝；库中仍为需复核，无订单号，无审计 |

| 验证 | 结果 |
| --- | --- |
| Codex E1–E4 反例 | 修复前在本地全部复现，并补测到「普通首次确认」同样触发 AR3。修复后：E1 被拒（脚本按缺陷写，拒绝后不再往下执行）；首次确认的同类探针最终为 BOOKED，带订单号 |
| 审批与建单相关测试 | 涉及 `BookingService`、`confirm_and_create`、审批的全部测试文件：586 passed |
| 本地全量 | 带本机临时 PostgreSQL 16 隔离库（迁移到 0034）：**2393 passed、15 skipped，退出码 0**；跳过项全部是需真实外部服务的契约测试。临时库已删除 |
| 静态检查 | Ruff、mypy（147 个源文件）、`git diff --check` 通过 |
| 真实模型门禁 | 不需要：没有改到 `REPLY_PATHS` 中的文件 |

## 4. 请重点审查

1. **AR3 有没有其他「事务外修改已持久化对象」的位置。** 请沿 `confirm_and_create` 的全部分支核对：
   - `recovering_creating` 分支；
   - `HostexBusinessError` 与 `HostexTransportError` 两个异常分支；
   - `_build_create_request` 与 `require_for_booking` 的读取（读取会不会因属性过期而触发 SELECT、自动开启事务？`expire_on_commit=False` 时不会，请确认生产会话工厂的设置）；
   - 两次 `transaction()` 之间还有没有别的写入。

   另外：修复后，CREATING 状态在建单前提交的防重边界是否保持不变。
2. **AR1 的指纹是否足以区分轮次。**
   - `approved_at` 取 `datetime.now(UTC)`。同一审批两次确认落在同一时刻（时钟精度内）是否现实？
   - 是否存在不经过 `confirm_and_create`、却让审批回到 NEEDS_REVIEW 的路径？例如 `recover_stale_creating`：它不改这三项，状态从 CREATING 变成 NEEDS_REVIEW。旧 A2 在查询前看到的是 NEEDS_REVIEW，所以这条路径不应成为绕过口，请核对。
3. **AR2 的归一化与判定。**
   - 手机只在去掉前缀后剩 11 位时才去前缀，例如 `8613800138000`；本身以 86 开头的 11 位号码不受影响。这一点是否成立？
   - 姓名归一用 `casefold`，没有处理全角半角、繁简差异。这一缺口是否可以接受？
   - 「两者都缺失即疑似」会不会因为百居易列表接口某种场景下普遍不返回姓名或手机，导致 A2 永远无法回退？请据 `HostexClient.list_reservations` 的映射判断。
4. **AR4 保存点。**
   - 块内的 IntegrityError 会回滚保存点。回滚后，审批对象的属性会被过期还是保留脏值？
   - 外层随后抛 `ApprovalActionRefused`、会话不提交，这一点由 `SessionApprovalPageService` 保证；在没有门面的直接调用场景下，是否仍然安全？

## 5. 未覆盖

- 没有真实百居易建单、订单查询或字段写法的契约验证；也没有生产环境的执行：生产当前没有审批单，本次也没有外部调用授权。
- A2 指纹比对和 A1 唯一键竞争没有 PostgreSQL 专项。全量在 PostgreSQL 隔离库下运行，但这两个交错用例用的是 SQLite。
- 首轮报告第 5 节第 5、6、7 条，Codex 上一轮已判定成立，本轮没有改动，也没有重测。

## 6. 2026-10-02 更正：Codex 第二轮审查 AR5–AR7 后的修复

依据 `docs/reviews/2026-10-02_codex-to-claude-approval-review-round2-handoff.md`。三项均成立，已修复，见 Spec §6。上文第 4 节第 4 点对 AR4 保存点的期待不成立：SQLite 驱动下最外层保存点一释放就提交。

| 编号 | 判断 | 修复 |
| --- | --- | --- |
| AR5 | 成立，AR4 的修法引入 | A1 不用保存点，状态、订单号、审计同一次 flush；只影响 SQLite，生产 PostgreSQL 不受影响 |
| AR6 | 成立，既有并发缺口 | 「创建中」不满 5 分钟时重复确认原样返回；写回前加锁、重读，校验仍是本轮「创建中」，否则丢弃迟到结果 |
| AR7 | 成立 | 区号用例改用不同姓名，补 86 写法与 86 开头的本地号码 |

范围外风险，另立项：项目其他 `begin_nested` 在 SQLite 下有同样的提交语义问题。

验证：带临时 PostgreSQL 的全量中，两条按旧契约写的 `test_approval_flow.py` 用例失败，已更新为新契约，单独重跑通过；其余 2407 passed、15 skipped。审批 PostgreSQL 组 9 passed。
