# 微信小程序基础登录：修复复审的回应与 CI 结果（Claude → Codex）

日期：2026-10-30。分支：`feat/wechat-miniprogram-login`。
本轮差异：`7c7468f → 7edf5c7`（修复）。`278219c` 只把 Codex 复审报告入库，没有代码改动。
远端分支已推送到 `278219c`，没有开 PR，没有合并，没有部署。

回应的报告：`docs/reviews/2026-10-30_codex-to-claude-wechat-miniprogram-login-fix-review.md`（下称「复审」）。

**结论**：复审的 F1、F2 和 §4 列出的措辞问题都已处理。复审 §2 R4 提出的「等待点证据不足」，已改为直接观测 PostgreSQL 等待事件。推送后 CI 全部成功，「隔离 PostgreSQL 回归」步骤 27 passed、没有跳过，其中包含小程序的 2 项。复审 §3 的限制 1、2 按复审意见留到下一阶段，本轮没有动。

## 1. 逐条回应

### F1 · CI 没有执行新的 PostgreSQL 专项 → 已修复

`.github/workflows/ci.yml:75`「隔离 PostgreSQL 回归」步骤的显式文件列表里加入了 `tests/integration/test_miniprogram_auth_postgresql.py`。没有改其他步骤，没有新增服务或变量。CI 的实际执行结果见 §3。

### F2 · 提交状态文档过时 → 已修复

已提交的历史段落没有改写，更正一律以追加的方式记录：

- 旧交接 `docs/reviews/2026-10-30_claude-to-codex-wechat-miniprogram-login-handoff.md` 追加 §10「复审后更正」。其中说明 §9 是提交前的快照，修复已提交为 `b631851`。
- 修复交接 `docs/reviews/2026-10-30_claude-to-codex-wechat-miniprogram-login-fix-handoff.md` 追加 §7。其中说明 §1 所说的「已更新」当时并不完整。
- `tasks/todo.md`：在 `7edf5c7` 中补了 `b631851`/`693b764`/`7c7468f` 的提交记录，去掉「待授权：提交本轮修复」。见 §5，本轮还有一处遗留，现已在工作区更正、尚未提交。

### 复审 §4 的措辞更正

| 复审指出的说法 | 处理 |
| --- | --- |
| Spec:135、旧交接 §9、todo:12 的「按字段名精确脱敏」 | Spec 第 135 行改为「按敏感字段模式遮住 `session_key` 及带前缀、大小写不同的变体，裸函数名不受影响」。todo 同步修改；旧交接在 §10 追加更正，不改原文。 |
| 旧交接 §9、修复交接 §1 的「B 确实阻塞在身份唯一键」 | 测试改为直接观测（见 §2），两份交接分别在 §10、§7 记录了原说法证据不足，以及现在的证据是什么。 |
| 旧交接 §3「离线行为符合 Spec」 | 在旧交接 §10 限定范围：这句话只代表当时列出的具体测试所覆盖的行为，不代表全部验收项；PostgreSQL、真机、真实微信当时都没有证据。 |
| todo:14 把 CI 未覆盖只归因于「分支未推送」 | 在旧交接 §10、修复交接 §7 和 todo 中都补上了 F1 接线遗漏这个原因。CI 已经跑过，todo 的这一条现在改写为 CI 结果。 |

## 2. 并发首登测试：从定时判断改为观测等待事件

文件 `tests/integration/test_miniprogram_auth_postgresql.py`。

**原做法**：复用保留期测试的 `_is_blocked`，睡眠 0.5 秒后判断任务还没完成。复审指出，这只能说明「没完成」，不能说明 B 停在哪里。

**现做法**：新增 `_wait_for_identity_insert_lock`。它每 50 毫秒新建一条独立连接，查一次 `pg_stat_activity`，最多等 3 秒，满足以下全部条件即返回真：

- `datname = current_database()`，且不是查询自身（`pid <> pg_backend_pid()`）；
- `wait_event_type = 'Lock'`，且 `wait_event = 'transactionid'`：唯一索引遇到对方未提交的同键行时，插入方等的就是对方事务号上的锁；
- `query ILIKE 'INSERT INTO customer_identities%'`：当前语句确实是身份表插入。

断言改为 `assert await _wait_for_identity_insert_lock(pg_engine)`，再加上 `assert not login_b.done()`。

为什么能认定等待方是 B：

- 仓库里写 `CustomerIdentity` 的有两处：`SQLAlchemyCustomerRepository.ensure_identity`（`src/homestay_bot/repositories/customers.py:102`），以及订单同步的 `upsert_reservation`（`src/homestay_bot/repositories/operations.py:1929`）。本测试只调用 `MiniProgramAuthService.login`，不走订单同步，所以被观测到的插入只能来自 `ensure_identity`。
- 此时 A 已经越过身份插入，停在 `create` 之后、提交之前（`create_holding_first` 补丁只是在原始 `create` 执行完之后等待放行）。
- `pg_engine` 夹具每个用例开始前都会清表，测试也是串行执行，不存在第三个写入方。

3 秒的上限小于夹具设置的 5 秒 `lock_timeout`。所以观测失败时，报出的会是这条断言，而不是 B 先因为锁超时出错。

**作者在本地临时 PostgreSQL 上的核对**（作者报告，Codex 可复跑）：

1. 改动后连续运行 5 次，全部通过。
2. 变异：让 A 不持有事务（补丁不再等待放行），观测断言失败。说明这条断言依赖两个事务真实重叠，不会恒为真。
3. 变异：去掉 `ensure_identity` 冲突后的回查、直接抛出，用例仍然失败。说明原来的数量与可解析性断言仍然有判别力。
4. 以上变异都已复原，复原后通过。

**证据边界**：

- 这条观测证明 B 的身份插入在等 A 的事务。之后「A 提交 → B 的保存点回退 → 回查复用」这条路径，是由放行后的业务结果间接证明的：客户 1、身份 1、会话 2、两个 token 都能解析。测试没有单独观测保存点回退。
- `query` 列显示的是该会话当前语句的文本。如果 SQLAlchemy 将来改变插入语句的写法（例如加 schema 前缀或改用 CTE），`ILIKE` 匹配会失效，测试会以失败而不是误报的形式暴露出来。

## 3. CI 结果

- Run `40949362579`：push 事件，head `278219c`，conclusion **success**。静态检查、全新数据库迁移、无外联测试、隔离 PostgreSQL 迁移、隔离 PostgreSQL 回归、构建生产镜像，每一步都是 success。
- 「无外联测试」：3575 passed，84 skipped。跳过的是真实契约测试，以及缺 `YUMI_TEST_POSTGRES_URL` 的 PostgreSQL 用例；该步骤本来就不设这个变量。
- 「隔离 PostgreSQL 回归」：**27 passed**，该步骤日志里没有 SKIPPED 行。本地 collect-only 核对：保留期 18 + 客人回复 7 + 小程序 2 = 27，所以小程序两项确实在 CI 的 PostgreSQL 上执行并通过了。
- 至此，复审 F1 所说「CI 绿色不能证明 R4 的 PostgreSQL 回归」的前提已经不成立：在这次运行里，绿色包含了这两项。

## 4. 范围外的观察（未处理，请 Codex 判断）

查 CI 日志时发现，与 F1 同类的问题在主干上已经存在：

- 以下文件读取 `YUMI_TEST_POSTGRES_URL`，但不在「隔离 PostgreSQL 回归」的文件列表里：
  - `tests/integration/test_approval_review_postgresql.py`
  - `tests/integration/test_complaint_delivery_postgresql.py`
  - `tests/integration/test_knowledge_delete_postgresql.py`
- 它们在「无外联测试」步骤里因为缺变量而跳过，例如日志中 `test_approval_review_actions.py:360/424/448/470` 的跳过原因就是「未设置 YUMI_TEST_POSTGRES_URL」。因此在 CI 里它们从来没有执行过。
- `tests/integration/test_referral_postgresql.py` 没有读取这个变量，它是怎么接入的，本轮没有核实。
- 这些文件早于本分支（例如 approval_review 相关测试来自 v1.65.0，已在 main 上），与小程序登录无关。本轮没有修改，避免扩大范围。
- 如果要补，最小改法与 F1 相同：把文件加进第 75 行的列表。但补上后它们可能第一次在 CI 上真正执行，结果未知，应作为独立任务处理。

## 5. 当前工作区：未提交

- `tasks/todo.md`：
  - 「复审后修复」一行原写「未提交」，已去掉。
  - b631851 那一行原写「未推送」，改为「已随下一行一并推送」。
  - 新增一行，记录 `7edf5c7`、`278219c` 已提交并推送，以及 CI 结果。
  - 「未覆盖」去掉「CI 上 PostgreSQL 专项尚未运行」。
  - 「待授权」改为：开 PR/合并、服务器 `.env`、生产迁移、部署、真机登录验收。
- 本报告。

两者都还没提交。仓库里已有的 JEV 文件和其他无关的未跟踪内容没有动。

## 6. 没做的与证据边界

- **按复审意见留到下一阶段**：复审 §3 的限制 1、2，即失效集合不跨重启、退出重试不再物理删除。其他页面接入 `ensureLogin` 之前必须处理。限制 3–5 按复审结论接受。
- **公开上线前**：仍须重新评估全局登录限频，按 Spec 的既有决定执行。
- **本轮没有跑**：
  - 本地全量测试。本轮只改了一个测试文件、CI 配置和文档，没有改 `application.py`、依赖或构建配置；CI 的「无外联测试」已在 `278219c` 上跑过全量无外联用例。
  - 开发者工具编译、模拟器或真机。
  - 真实微信 code2Session 交换。
  - 真实 DeepSeek、Hostex、企业微信的任何调用。
- **没有做的外部操作**：没有连接任何已有数据库或生产数据库，没有生产迁移，没有改服务器配置，没有部署，没有开 PR。
- **AOCI**：`aoci_maintain` 被与本分支无关的既有漂移阻断，本轮没有确认那些漂移，也没有写入索引。小程序相关文件的认知条目因此仍未更新，需要在漂移处理后补维护。
- 没有读取 `.env`、`project.private.config.json`、公众平台会话或受保护的项目总结文件。

## 7. 请 Codex 复核

1. `_wait_for_identity_insert_lock` 的观测条件，加上 §2 列出的推理，是否足以支持「B 的身份插入在等 A 的事务」？有没有能让它误报真的情况？
2. 两份交接追加的更正（旧交接 §10、修复交接 §7）、Spec 第 135 行和 `tasks/todo.md` 现在的状态，是否与 Git 和代码一致，还有没有遗留的矛盾？
3. CI 证据（§3）是否足以把 R4 记为「CI 验收通过」？
4. §4 的既有 CI 接线缺口，是否应该在本分支之外单独立项？
5. 从代码、离线证据和 CI 来看，这个分支是否可以进入「开 PR → 部署前准备」？部署本身仍需另行授权，并且要先做可恢复备份。
