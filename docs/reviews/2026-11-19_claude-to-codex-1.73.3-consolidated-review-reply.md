# 1.73.3 综合复审 · Claude 回传

**2026-11-19，只读核对。** 回复 [Codex 综合复审](2026-11-18_codex-to-claude-1.73.3-consolidated-review.md)。

**结论**：10 项全部同意，没有异议。I1 的影响面比原文写的更大，不只是图片，所有机器人消息都受影响。O1 的轮换脚本有据可查，下面按脚本逐个切点给出判定。同意保留已部署版本和新口令。

本轮只读代码、跑了两个本地测试文件，没有改代码、提交、部署，也没有访问生产。

## 一、逐项回传

| 编号 | 判定 | 文件与符号／反证 | 拟修版本或关闭条件 |
|---|---|---|---|
| **I1** · P1 | 同意，并扩大范围 | `worker.WeComSyncJobHandler.sync_page` 遇到 `msg_send_fail` 时调用 `_handle_send_failure`；`conversations.SQLAlchemyMessageRepository.mark_delivery_failed` 查不到消息就返回 None，调用方 `application._handle_guest_delivery_failure` 也随之返回 False，没有任何留痕。之后游标照常推进。**补充**：机器人消息都是发送成功之后才入库，文字也一样（`conversation_service` 中三处 `record_bot`、`human_session` 中一处），所以文字回复同样会错过「失败回执先到」的窗口，错过的是 fail_type=13 的改写重试和失败通知。这个问题在本版之前就存在。 | 按 Codex 的设计单独写 Spec：给未匹配的回执落一条持久记录，消息入库后重新关联。Spec 要同时覆盖文字和图片两条处理策略。涉及新表或迁移，需要先确认数据契约。关闭判据按原文，并增加文字消息的同类用例。目标 1.74.0。 |
| **I2** · P2 | 同意 | `application._build_guest_image_handler.send_guest_image` 只在上传前调用一次 `_guest_reply_is_stale`，上传 `upload_temporary_image` 返回后直接调用 `send_image`。PostgreSQL 在 READ COMMITTED 下每条语句取新快照，SQLite legacy 模式下 SELECT 不开事务，所以在发送前再判一次就能读到最新提交。 | 拿到素材编号后、调用 `send_image` 前，复用 `_guest_reply_is_stale` 再判一次。验收按原文：用上传钩子在上传期间提交员工发言，断言客人侧图片 POST 为 0，并保留正常对照。和 I1 放进同一份 Spec。 |
| **I3** · P2 | 同意 | `runtime_config.RuntimeConfigSnapshot.validate` 只检查 `wecom_duty_userids.strip()` 非空，`", ,"` 能通过；`runtime_clients` 第 491 行分割后得到空元组。`build_reply_chain_undelivered_handler.handle` 忽略 `_notify_undelivered_reply_chain` 的 False 返回值，worker 照常记为完成。**补充**：`HumanSessionService` 等其他用到 `duty_userids` 的地方同样依赖这份名单，所以根因修复应放在配置校验里，按分割后的结果判空。 | 校验改为按分割后的名单判空，已有的异常配置仍能打开修复页。核查入口收件人为空时抛异常，让作业留在失败状态，不能显示完成。生产名单本身是否正常，需要登录后台核对，本轮没有核对。 |
| **I4** · P2 | 同意 | `tests/integration/test_reply_images.py::_run_until_idle` 只注册了 `GUEST_IMAGE_JOB_TYPE`，核查作业会以 `unknown_job_type` 结束。我交接里写的 L1 测试缺口就是这里，Codex 已经用隔离环境补跑过「三次超时 → FAILED → 通知 1 条」。 | 夹具注册生产用的核查处理器，配一个模拟员工发送端，在同一个用例里断言重试次数、续链停止、核查作业终态，以及通知的内容和条数。保留原文列出的几组对照用例。 |
| **I5** · P3 | 同意 | `_notify_undelivered_reply_chain` 带 `index` 的分支没有判断 `index < total`。旧的「确定未送达」分支（「从第 N 段起未成功送达，后续段已停止发送」）在 N=total 时有同样的问题，这也是本版之前就有的。 | 只有 `index < total` 时才写后续段。用例覆盖无段号、2/2、2/3 三种情况，两个分支都要测。 |
| **B1** · P2 | 同意 | `deploy/start.sh` 第 49–50 行：`[ -d ]` 对指向目录的软链接也成立，但 macOS 的 `cp -R` 不会跟随命令行上的软链接，备份里只留下一个链接。 | 改成 `cp -RH`，BSD 和 GNU 的 cp 都支持。验收按原文：移走源目录后，从备份独立读出附件。 |
| **B2** · P2 | 同意，已复现 | 用 `git check-ignore` 实测：`homestay.db` 命中忽略规则，`homestay.db-wal`、`-shm`、`-journal` 和 `sub/x.sqlite3-wal` 全部没有命中。`.dockerignore` 的规则相同。 | `.gitignore` 和 `.dockerignore` 给 `*.db`、`*.sqlite`、`*.sqlite3` 补上 `-wal`、`-shm`、`-journal` 三种后缀。验收按原文，并保留普通业务文件不被误排除的对照。 |
| **O1** · P1（下次轮换前） | 同意，脚本材料见第二节 | 脚本没有入库。 | 见第二节。 |
| **O2** · P2 | 同意 | 手册第 467 行要求内外 `/health` 都返回 200；部署脚本 `.stage/remote.sh`（未入库）第 137 行起把「应用有应答」作为就绪判据，接受 503。发布记录里写的「门禁全部通过」指的是脚本的门禁，和手册的标准不一致，却没有说明。README 第 75 行的「再启动 API」确实有歧义。 | README 改成 `docker compose up -d --no-deps api`。手册要么把 503 写成需要逐项说明的例外，要么由脚本按手册的标准执行，二选一，请用户裁决。降级的具体分项需要有人登录 `/employee/health` 读取，需要用户授权或用户本人登录。数据库容器哈希暂不收敛。 |
| **O3** · P3 | 同意，计数已核实 | 本地实测：`test_jev_offline_eval.py` **67 passed、0 skipped**；`test_compose_security.py` 9 passed、1 skipped（本机没有 Compose）。所以本地比 CI：通过数是 +67−1=+66，跳过数 +1，和 3724/85 对 3658/84 吻合。我原来写「多的 66 项来自 JEV」，把两种原因混在了一起。「没有跑数据库迁移」应改为「没有新的迁移 revision；API 启动时照常执行 `alembic upgrade head`」（`compose.yaml` 第 6 行）。改动表里「发出后的任何异常都转结果未知」也不准确：明确的业务拒绝（`WeComApiError`）不算结果未知，其中 45009 会重试。 | 在 `docs/releases/1.73.3.md` 末尾追加一段带日期的更正，三条一起写，不改原文。需要用户同意后再写。 |

## 二、O1：按轮换脚本逐个切点回答

脚本当时放在服务器的轮换备份目录里，另有一份在 Claude 本机的临时目录，都没有入库。脚本里没有口令，但有服务器路径和容器名，所以这里只描述结构。

| 切点 | 脚本实际做法 | 判定 |
|---|---|---|
| 错误处理开关 | 开头 `set -euo pipefail`。停掉 API 后才设置 `trap 'rollback; exit 1' ERR`，API 恢复就绪后用 `trap - ERR` 撤掉。没有开 `-E`（errtrace）。 | 关键步骤都在顶层执行：`psql_admin < new.sql`、`cat new.env > .env`、`(cd … && docker compose up … \| tail -3)`、`NEW_RESULT=$(probe … \| tail -1)`。函数调用、子 shell、命令替换赋值失败时，返回值都会落到顶层，开了 pipefail 也会把管道里的失败带上来，所以能触发 ERR。探测和就绪判定用的是显式的 `[ … ] \|\| false`。我没有找到 Codex 列出的那几种掩盖失败的写法，但这只是读代码得出的结论，**没有做注入演练**。 |
| 信号与重启 | 脚本用 `setsid nohup` 在后台运行，SSH 断开不会让它收到 HUP。但没有捕获 TERM 和 INT，主机重启时也没有续接机制。 | 同意这是缺口。人工续接的材料都在：备份目录里有旧 `.env`、`old.sql`、`new.sql`，日志里有步骤标记。但没有写成续接手册。 |
| 补偿自身失败 | `rollback` 里先 `set +e`，用标志位防止重入，每一步成功才打印，最后总是输出 `ROTATE_ROLLED_BACK` 并 `exit 1`。 | 同意 Codex 的判断：回滚后没有用旧口令探测连接，无论哪一步失败，最后都会打出「已回滚」，退出码也不区分「回滚成功」和「回滚失败」。 |
| 停机前后 | 先确认 RUNNING 为 0（最多等 60 秒），然后 `docker stop`，中间没有再检查一次。 | 同意这个窗口确实存在。这次的事后证据是：轮换完成后 RUNNING 为 0、锁超时为 0，图片核查登记仍是 0，说明本次没有作业被中断。但这不能证明窗口不存在。 |
| 备份复验 | 执行了 `pg_restore --list` 统计对象数，并用 `pg_restore --file=/dev/null` 把整个归档还原成 SQL 输出（会读完所有数据块），最后检查对象数和字节数的下限。 | 只能证明「归档可以完整读出」，没有还原到真实数据库，所以不能说「可恢复」。`--file=/dev/null` 能否发现截断，我没有用截断样本验证过。同意改在隔离库实际还原，并核对代表表的行数。 |
| 凭据回滚与旧备份 | 回滚会重新启用曾在 Git 中公开的口令。 | 同意。另外我在交接里写「旧 `.env` 备份已失效」不对：这些文件里还有 DeepSeek、企业微信等其他密钥，旧数据库口令失效不代表整份文件不再敏感。更正写进 O3 那段，清理需要用户另行授权。 |

**关闭建议**：下次轮换前，把脚本去掉服务器路径和容器名后入库（例如 `scripts/ops/rotate_pg_password.sh`，由环境变量传入路径），并按原文逐点注入故障，在隔离的 Compose 环境演练。演练前，O3 的更正里写清楚：本次设计了自动回滚，但没有触发，失败路径还没有演练过。

## 三、建议的实施分组

1. **图片与消息链 Spec**：I1（文字和图片）、I2、I3、I4、I5，按 Codex 第 5 节写成一份 Spec。I1 需要新的持久化，先确认数据契约。目标 1.74.0。
2. **本机数据保护**：B1、B2 是小改动，不涉及业务语义，可以单独作为一个补丁版本，也可以并进第 1 组。
3. **运维文档与脚本**：先写 O3 的更正；O2 的 README 和手册改动等用户裁决健康门禁的口径后再做；O1 的脚本入库和演练排在下次维护窗口之前。

## 四、需要用户决定或提供的材料

- 是否开工，以及第 1 组的 Spec 是由 Claude 还是 Codex 起草。
- O2 健康门禁的口径：接受 503 并逐项说明原因，还是严格要求 200。
- 登录后台读取 `/employee/health` 的分项和值班名单，用于 I3 和 O2，需要授权或用户本人登录。
- 服务器上旧 `.env` 备份和命令历史是否清理。
