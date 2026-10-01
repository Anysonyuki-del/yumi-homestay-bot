# 审批人工核验 AR5–AR7 修复 · 第三轮审查请求 · Claude → Codex

日期：2026-10-02。状态：AR5–AR7 本地修复与验证完成，**未提交、未推送、未部署**。

请 Codex 只读审查本轮修复：按第 4 节逐项给出「成立／不成立／部分成立」与文件、符号依据；有异议请给出真实调用链或可运行反例。授权范围同前两轮：只读代码、跑本地测试，不修改业务代码、不提交、不调用真实 DeepSeek／百居易／企业微信。

## 1. 基线与阅读顺序

- 基线：`main`，HEAD `f3d03ce`（版本 1.63.1）。所有改动都在工作区：21 个已跟踪文件，+915／−57；未跟踪文件为 Spec、两份测试文件和四份报告。
- 阅读顺序：
  1. Codex 第二轮审查 `docs/reviews/2026-10-02_codex-to-claude-approval-review-round2-handoff.md`；
  2. Spec `docs/specs/2026-10-01_approval-review-backfill-spec.md` §6（本轮修订、R3/R5 的契约变更与实施记录）；
  3. 本报告；
  4. Claude 第二轮报告 `docs/reviews/2026-10-01_claude-to-codex-approval-review-round2-handoff.md` 第 6 节（更正）。
- 用户决定：「直接改完」。之后选择先交 Codex 审查再发布（「b」）。
- 工作区说明：
  - 第二轮审查期间出现的两处非本人改动已按用户同意还原，现与 HEAD 一致：`docs/releases/1.63.1.md` 的表格重排；`2026-09-11_handoff-to-codex.md` 被移到 `docs/releases/`。
  - Codex 第二轮报告只把本机路径改为相对路径，内容未改。

## 2. 本轮修复（省略前缀 `src/homestay_bot/`）

| 编号 | 文件与符号 | 改前 | 改后 |
| --- | --- | --- | --- |
| AR5 | `services/approval_page_service.py::backfill_reservation` | AR4 修复后，状态与订单号在 `begin_nested()` 内 flush，审计在保存点之外。SQLite 驱动不因普通 SELECT 开启物理事务，最外层保存点 RELEASE 即提交，之后审计失败也撤不回状态 | 不用保存点：状态、订单号赋值，加上 `_audit`，在同一次 `flush()` 写入；`IntegrityError` 转为 `ApprovalActionRefused`，由调用方会话整体回滚。提交前的查重保留 |
| AR6① | `services/booking_service.py::CREATING_STALE_AFTER`（新增，5 分钟）、`confirm_and_create` | CREATING 再次确认会立即进入恢复核验；原建单仍在等上游时查不到订单，就转需复核，从而放开 A2 回退、发起第二次建单 | CREATING 且 `approved_at` 距今不满 `CREATING_STALE_AFTER` 时原样返回，不核验、不改状态；满阈值才恢复核验。`application.py` 中 `recover_stale_creating` 的阈值改为引用同一常量（原为字面量 5 分钟） |
| AR6② | `_same_round`、`_as_utc`（新增）；`_reconcile_or_mark_review(..., attempt)`、`_mark_needs_review(..., attempt)` | 加锁后不核对轮次，无条件写 `request_id`、状态与订单号 | 进入建单前记下 `attempt = _as_utc(approval.approved_at)`；两个写回出口加锁后，要求数据库现值仍为 CREATING、且 `_as_utc(locked.approved_at) == attempt`，否则丢弃结果、只记日志，不改任何字段。`_as_utc` 把 SQLite 读回的无时区时间按 UTC 解读 |
| AR6② | `repositories/approvals.py::get_for_update` | 加锁查询可能直接返回会话缓存里的旧对象属性 | 加 `execution_options(populate_existing=True)`，以数据库现值为准 |
| AR7 | `tests/integration/test_approval_review_actions.py` 参数用例 | 区号两组的姓名与审批相同，走姓名分支即命中 | +86、0086、86 三组改用不同姓名或无姓名，只有手机归一正确才命中；补「86 开头的 11 位本地号码」不被去前缀、不误配 |

## 3. 测试与验证

| 测试 | 断言 |
| --- | --- |
| `world` 夹具改为项目的 `create_engine` 与 `create_session_factory` | 用例在与生产装配一致的 SQLite 配置下运行：外键开启、`expire_on_commit=False`。初始数据抽成 `seed_world`、`new_sensitive`，供 PostgreSQL 组复用 |
| `test_backfill_audit_failure_leaves_nothing_behind`（AR5） | 经生产门面，审计 flush 失败：仍为需复核，无订单号，无回填审计（对应 E5） |
| `test_backfill_rolled_back_by_the_caller_is_fully_undone`（AR5） | 服务返回后调用方回滚：三项全部撤销；正常经门面提交：BOOKED、订单号、1 条审计 |
| `test_backfill_collision_is_refused_without_partial_writes`（AR4/AR5） | 查重后另一会话抢先登记同号：受控拒绝，库中无部分写入（不再断言保存点数量） |
| `test_repeat_confirm_while_creation_is_in_flight_changes_nothing`（AR6①） | 首次建单的 await 内再次确认：返回 CREATING；A2 被拒；只建单一次；首次建单返回后为 BOOKED，订单号与请求编号均属本轮 |
| `test_late_result_of_an_old_round_cannot_overwrite_the_new_round`（AR6②，success／rejected 两组） | 首次建单期间把 `approved_at` 推到 6 分钟前，超时恢复转需复核，A2 回退，新一轮房间 102 BOOKED；旧一轮成功或业务失败迟到返回后，状态、房间、金额、订单号、请求编号、失败信息仍属新一轮（对应 E6） |
| `tests/integration/test_approval_flow.py` | 两条「遗留 CREATING」用例原本把 `approved_at` 设为当前时刻，按新契约会原样返回，因此改为超过阈值；新增 `test_fresh_creating_is_left_alone_on_repeated_confirmation` |
| 新增 `tests/integration/test_approval_review_postgresql.py` | 在 PostgreSQL 隔离库上复用 9 条交错用例：A2 指纹、真实门面建单两条、A1 竞争、AR5 两条、AR6 三条 |

| 验证 | 结果 |
| --- | --- |
| E5、E6 反例 | 修复前在本地复现；修复后，E5 为需复核、无订单号、无审计，E6 在首次建单期间返回 CREATING，A2 被拒 |
| 反向验证 | 临时让 `_same_round` 恒为真，AR6② 两组用例失败，恢复后通过 |
| 审批 PostgreSQL 组 | 9 passed |
| 本地全量 | 带临时 PostgreSQL 16 隔离库（迁移到 0034）：2407 passed、2 failed、15 skipped。两条失败是 `test_approval_flow.py` 中按旧契约写的用例，修改后该文件单独重跑 9 passed。其余源码在全量之后没有变化，没有重跑全量。跳过项全部为真实外部契约测试；临时库已删除 |
| 静态检查 | Ruff、mypy（147 个源文件）、`git diff --check` 通过 |
| 真实模型门禁 | 不需要：没有改到 `REPLY_PATHS` 中的文件 |

## 4. 请重点审查

1. **AR6① 的 5 分钟阈值是否会让管理员无法处理。**
   - 原建单真的挂起时，确认页不显示确认按钮（模板只对 PENDING 显示）；后台每轮 `recover_stale_creating` 在满 5 分钟后转需复核。管理员最多等 5 分钟，加一个 worker 周期。请确认这一等待可以接受，且没有其他入口在「创建中」期间需要立即处理。
   - `HostexClient` 默认单次请求超时 10 秒（`integrations/hostex_client.py` 构造参数 `timeout_seconds=10.0`），远小于 5 分钟。请核对建单是否有重试或其他会叠加耗时的路径。若有，旧调用可能在恢复之后才返回，结果会由 AR6② 丢弃，但百居易里可能已多出一单，只能人工处理。
2. **AR6② 用 `approved_at` 识别轮次。**
   - `datetime.now(UTC)` 写入 PostgreSQL timestamptz 后精度是否完整保留，读回比较是否稳定相等？SQLite 下由 `_as_utc` 统一时区，测试已覆盖。
   - 恢复支路（满阈值）中，attempt 取自加锁读到的旧 `approved_at`，再与写回时比较。期间如果后台 `recover_stale_creating` 已转 NEEDS_REVIEW，写回会被丢弃，审批保持需复核。这一结果是否符合 R3/R5？
3. **AR6② 丢弃结果时的可观测性。** 现在只记一条 warning 日志，不写审计、不通知员工。若迟到的旧结果是「上游已建单成功」，百居易里会多一单，需要人工发现。是否应当至少写一条审计或员工通知？这会扩大范围，请给出建议。
4. **AR5 去掉保存点后的共享会话。** `backfill_reservation` 遇唯一键冲突时会话进入需回滚状态；生产门面不提交，直接调用方需自行回滚。请确认没有其他调用方在同一会话里继续使用。
5. **`get_for_update` 加 `populate_existing`** 会让 `confirm_and_create` 首次加锁时覆盖会话里已加载对象的未提交修改。当前调用路径在加锁前不修改审批，请确认没有遗漏的调用方依赖旧行为。

## 5. 未覆盖

- 真实百居易的建单耗时、超时、订单可见延迟与字段写法；没有生产执行，生产当前没有审批单，也没有外部调用授权。
- AR6 丢弃迟到结果后，「上游多出一单」的人工发现流程（见第 4 节第 3 点）。
- 范围外风险，另立项：项目其他十余处 `begin_nested` 在 SQLite 下有与 AR5 相同的提交语义问题；生产 PostgreSQL 不受影响，本轮不改共享引擎。

## 6. 2026-10-02 追加：Codex 第三轮审查 AR8、AR9

依据 `docs/reviews/2026-10-02_codex-to-claude-approval-review-round3-handoff.md`。两项都成立，都是测试判别力缺口，业务代码没改。E8、E9 两段反向验证在本地复现：原用例在去掉保护后仍然通过。

| 编号 | 补测 | 反向验证 |
| --- | --- | --- |
| AR8 | `test_late_result_cannot_overwrite_a_newer_round_that_is_still_creating`：新一轮处在 CREATING、只是确认时间不同；旧一轮的成功、失败两个出口都被丢弃；新一轮自己的结果照常写入。旧会话保留缓存对象，以验证加锁后从数据库重读 | 只去掉时间比较：两组断言失败，状态被写成 BOOKED／NEEDS_REVIEW |
| AR9 | `test_an_eleven_digit_number_starting_with_86_keeps_its_prefix` | 去掉长度保护：失败 |

上文第 3 节「去掉 `_same_round` 两组失败」这一证据，同时去掉了状态与时间两层保护，不能单独证明时间比较有效。时间比较的有效性以本节为准。

验证：
- 审批相关测试 66 passed；PostgreSQL 组（含 AR8 两组）11 passed；Ruff、`git diff --check` 通过。
- 只改了测试，源码与上次全量相同，复用该次全量结果，不重跑。

**本地修改与验证完成，可以交给 Codex 审查。**
