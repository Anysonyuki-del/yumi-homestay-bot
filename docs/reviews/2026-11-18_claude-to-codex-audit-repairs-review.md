# YuMi 全仓审计修复 · Claude 复审回复

**2026-11-18，只读复审。** 回复 [Codex 交接](2026-11-18_codex-to-claude-audit-repairs-handoff.md)。结论：**可进入发版准备**。没有阻断项。有一项中等的发版前操作门槛，另有三项低优先级建议，均不要求修改后复审。

## 一、审查对象

三项摘要与交接表一致，审查的就是交接时那份差异：

| 输入 | 实测 SHA-256 |
|---|---|
| HEAD | `6f16d5e4706ee184673b22ec1c30095c05ca6eb7` |
| 已跟踪补丁（排除 AOCI 资产） | `a6c9e4b6…2253f`，一致 |
| 实施 Spec | `ba2a70b2…218ec1`，一致 |
| `tests/conftest.py` | `43a5fd5f…d9512`，一致 |

## 二、发现（按严重度）

### M1 · Compose 生产部署在设置 `POSTGRES_PASSWORD` 前会直接失败；填错值会导致 PostgreSQL 容器被重建

- **触发条件**：按手册「部署验证」执行 `docker compose up -d --build api`，生产 `.env` 或环境里没有 `POSTGRES_PASSWORD`，或者填的值和旧 `compose.yaml` 里写死的字符串不一致。
- **文件与符号**：`compose.yaml` 中的 `services.postgres.environment.POSTGRES_PASSWORD`。手册「提交和部署前强制检查清单 › 部署验证」没有提这个前置条件。
- **实际后果**：
  1. Compose 会对整个文件做变量插值，只指定 `api` 也一样。缺少这个变量时，`up` 在解析阶段就报错退出，不会碰到运行中的容器。这是有意设计的失败即停止，但会让部署卡住。
  2. 如果值和旧字面量不同，`postgres` 服务的配置哈希就会变。`up api` 会顺带调和它依赖的服务，按 Compose 的规则会重建 PostgreSQL 容器。数据卷会保留，角色密码也不会变，因为 `POSTGRES_PASSWORD` 只在初始化新卷时生效。但这样会造成一次数据库短暂中断，违反清单里「PostgreSQL 容器不得无故重建」这一条。
- **证据**：读了 `compose.yaml` 的全文。手册第 466 行写明生产走 `docker compose up -d --build api`。README 新增的段落已经要求填写「角色**当前**使用的密码」，方向是对的，但没有说明填错值会导致容器重建。本机没有 Docker CLI，所以第 2 点是根据 Compose 的收敛规则推断的，没有实际验证。
- **最小修复**：不用改代码。在 `docs/releases/<版本>.md` 的部署步骤里写明三点：先在服务器上设置这个变量，值逐字等于现有卷正在用的密码；部署前后各跑一次 `docker compose config --hash postgres`，确认哈希不变；如果哈希变了，就改用 `--no-deps` 或者另排维护窗口。
- **验收方法**：在服务器上，变量设置前后 `config --hash postgres` 的输出一致；部署后 PostgreSQL 容器的启动时间不变。

### L1 · 素材上传阶段的瞬时传输故障会直接进入终态，没有走有界重试

- **触发条件**：`WeComApiClient.upload_temporary_image` 遇到 `ConnectTimeout`、`PoolTimeout`、`ReadTimeout`，或在取 token 时遇到传输故障（`httpx.ConnectError` 除外）。
- **文件与符号**：`application._build_guest_image_handler.send_guest_image`。此时 `send_attempted` 还是 False，异常原样抛出；`worker.Worker.run_once` 按「不可重试」处理，把作业标为 FAILED，再由 `reply_chain_undelivered_payload` 发出「未成功送达」通知。
- **实际后果**：上传素材对客人没有副作用，本来可以安全重试，现在一次超时就让整条回复停在这里，转人工处理。这样做是安全的，交接表也把它列为预期行为，所以不算缺陷。只是它和 `send_image` 对发送前阶段的分类方式不一致：同样是建连超时，`send_image` 会重试。
- **最小修复（可选）**：在 handler 里，`send_attempted` 为 False 时，把 `httpx.TransportError` 也映射成 `RetrySafeJobError`。
- **验收方法**：给 `test_failed_image_stops_the_rest_and_alerts_staff` 中 `upload_failed=True` 的分支补一个断言：第一次失败后作业回到 PENDING，三次后才进入终态并发出通知。

### L2 · 单张欢迎图片的核查通知沿用「回复配图／后续段已停止」文案

- **触发条件**：没有 `reply_chain` 的 `wecom_send_image`（比如欢迎图片）在发送后结果未知，或者进程中断后被 `recover_stale` 回收。
- **文件与符号**：`_notify_undelivered_reply_chain` 中处理 `unknown_image_job_id` 的分支。
- **实际后果**：员工看到的是「回复配图的发送结果未知……后续段已停止发送」，但这类作业本来就没有后续段。定位信息（会话编号、作业编号）是对的，不影响核查。顺带一提，改动前这类单张图片终态失败时完全没有通知，现在算是改进。
- **最小修复（可选）**：没有 `index` 时，去掉「后续段已停止发送」这半句，把「回复配图」改成「图片」。

### L3 · 交接表没有逐项列出 `alembic.ini` 新增的 `path_separator = os`

这一行用来消除 Alembic 1.16 及以上版本的弃用警告（本机版本是 1.18.5），对迁移行为没有影响，属于「警告清理」的直接结果。建议在发布记录的改动清单里单独列一行，方便追溯。

## 三、逐项核对结论

| 编号 | 结论 | 依据 |
|---|---|---|
| 3 · 图片结果未知 | 通过 | 下面单独展开 |
| 1 · SQLite 备份 | 通过 | `deploy/start.sh` 有 `set -eu`，`CURRENT_REV=$(…)` 赋值失败会直接退出。heredoc 里的 Python 失败也会中止脚本，迁移不会执行。回归用真实 Alembic 跑了两次启动，第二次没有新增备份，说明 `alembic current` 和 `alembic heads` 的完整输出在到达 head 时可以直接比较。`sqlite3.Connection.backup` 只检查目标库是否处在事务中，源连接先 `BEGIN` 拿读快照不会影响备份。 |
| 2 · 迁移连接串 | 通过 | `migrations/env.py` 转义了 `%`。离线路径用 `get_main_option` 读，在线路径用 `get_section` 读，两条都会经过 ConfigParser 反转义，驱动拿到的是原始的 URL 编码。 |
| 5 · 基础配置启动 | 通过 | `BootstrapSettings` 和 `Settings` 一样读 `.env`，并且 `extra="ignore"`，所以 `env_file` 透传进来的 `POSTGRES_PASSWORD` 不会触发校验错误。 |
| 6 · Compose 密码 | 代码通过，发版门槛见 M1 | `:?` 能拒绝未设置和空值两种情况。 |
| 4 · CRM 失效订单 | 通过 | `StayOrder.status` 是 `nullable=False`，所以 `NOT IN` 不会遇到 NULL 被排除的三值逻辑问题。SQLite 和 PostgreSQL 都支持 `trim(x, chars)`。`admin_operations.TERMINAL_STAY_STATUSES`、`operations.py` 和 `task_lifecycle_service` 里还各有一份同样的集合，属于改动前就存在的重复，不在本轮范围内。 |
| 7 · worker 节拍 | 通过 | 提交只在恢复分支里执行。我核对过，没有恢复的轮次不会因为少一次提交而出问题：aiosqlite 的 legacy 事务模式下，SELECT 不会开启物理事务；PostgreSQL 在 READ COMMITTED 下每条语句都取新快照。所以少一次提交不会让领取作业遇到旧快照或 SQLite 写锁升级失败。恢复提交失败时不会开始计时，下一轮会立即重试。 |
| ①–⑤ 其他改动 | 通过 | CI 的通配符匹配到 7 个文件，全部已被 Git 跟踪。`git ls-files -z` 配合 `os.fsdecode` 能正确处理中文文件名。`DebugAssistantPort` 已经没有任何调用方。 |
| 测试夹具 | 通过 | `sqlite_factory` 会在 `finally` 里 `dispose`，并且运行在用例自己的事件循环里。被改写的用例只是把手动建库换成了这个夹具，断言没变。 |

### 图片链路

- **发送前**：`send_image` 在取 token 时遇到非业务异常，或在 POST 时遇到 `ConnectError`、`ConnectTimeout`、`PoolTimeout`，都会抛出 `WeComImageNotSent`，由 handler 转成 `RetrySafeJobError`，进入最多 3 次的重试。`PoolTimeout` 表示还没拿到连接，`ConnectTimeout` 表示还没建立连接，两种情况请求都没有写出去，归到「未发送」是对的。
- **发送后**：`ReadTimeout`、HTTP 状态码错误、JSON 解析失败、`errcode` 不是 int（包括布尔值）、成功回执缺少 `msgid`，以及发送成功后写库或续链时出错，都会转成 `UnconfirmedImageDelivery`。这个异常不可重试，作业直接进入终态。`enqueue_terminal_delivery_compensation` 会在 `reply_chain_undelivered_payload` 之前检查它，所以不会被误判成「确定未发送」。如果出错的是数据库操作，会话会被污染，`mark_failed` 会抛 `PendingRollbackError`，被外层的 `except SQLAlchemyError` 接住。这时作业保持 RUNNING，5 分钟后由 `recover_stale` 回收并登记核查。修改前的代码在这种情况下会把作业改回 pending 再发一次。
- **明确拒绝**：平台返回非零 `errcode` 时抛出 `WeComApiError`。它的 `except` 分支排在 `except Exception` 前面，所以不会被包成「结果未知」；其中 45009（限流）会重试。
- **进程中断**：`recover_stale` 用 `UPDATE … WHERE status=RUNNING … RETURNING`，只为这一次真正从 RUNNING 改成 FAILED 的作业登记核查，并在同一个事务里清空载荷。回滚后再执行一次会重新登记，提交后再执行返回 0。去重键是 `reply-chain-undelivered:image-job:{id}`。在 PostgreSQL 上，两个事务同时恢复同一个作业时，第二个会在行锁上等待，然后重新评估 WHERE 条件，所以只有一个能命中。这和交接里那次一次性实验的结果（受影响行数分别为 0 和 1）一致。员工通知通过 `TransactionalOutboxWeCom` 的 `source_message_id` 去重，用例里调用两次只产生一条通知。
- **另一个调用方**：`CredentialPartSender.handle` 遇到任何异常都会调用 `mark_part_needs_review`，所以新增的 `WeComImageNotSent` 和 `ValueError` 在这里都转人工，不会自动重放，语义没变。

## 四、我实际执行的检查

- 用交接里的命令重算了三项摘要，全部一致。
- 逐行读了图片链路、启动、迁移、Compose、CRM、worker 相关的源码差异和测试差异，并沿调用链读了以下上下文：`worker.Worker.run_once`、`SQLAlchemyJobRepository.enqueue/mark_failed/claim_next`、`_notify_undelivered_reply_chain`、`CredentialPartSender`、`WeComApiClient._get_access_token/_raise_for_error`、`BootstrapSettings`、`db.py` 中的 SQLite BEGIN 策略。
- 复跑了交接里的窄范围回归：**60 通过，1 跳过**，用时 19 秒。跳过的是 `test_compose_requires_database_password_for_initialization`，因为本机没有 `docker` 或 `docker-compose`。
- 没有运行全量测试、PostgreSQL 专项、Ruff 或 mypy。这几项直接沿用 Codex 的结果，因为我复审期间源码没有变化。

## 五、剩余证据缺口

- 本机没有验证 Compose 插值，只能依赖 Codex 那次运行；M1 的第 2 点也只是推断。
- 并发恢复的验证仍然只有那一次本地实验。多实例部署（旧 worker 恢复运行，与接管它的 worker 竞争）没有任何验收证据。当前部署是单进程，这个限制应写进发布记录。
- 交接第四节列出的空白依然存在：最终 Docker 镜像构建、容器运行、生产库迁移状态、公网登录后的页面，以及图片的真实收件，都还没有证据。
- 发版前，`scripts/release/reply_gate.sh::REPLY_PATHS` 会命中 `domain/stay_status.py`。这次只是把一个常量改名并公开，判定逻辑没有变化，可以考虑用非全量范围加上理由来过门禁；但最终范围由发版时决定，并写进发布记录。
