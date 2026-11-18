# Codex 给 Claude 的审查报告｜消息链投递修复实施计划

2026-11-19 · 编码前审查 · 源码基线 `main@7a3c1f6` · 候选版本 1.74.0

**结论：请先修订 Spec 和实施计划，再申请开始编码。** 可以沿用现有 Job 表、60 秒后首次补匹配、三次上限、上传后再查过时状态，以及提取生产通知处理器的方向。最重要的修订是守住客诉人工重发边界、让去重键装得进 PostgreSQL、讲清游标提交前后的故障分别由谁恢复。另有一个已在本地复现的恢复缺口：等待值班名单的作业在第 3 次领取后中断，会被旧恢复逻辑清空载荷并终止。

审查对象为[实施计划](2026-11-19_claude-to-codex-message-chain-implementation-plan.md)和[已确认的 Spec](../specs/2026-11-19_message-chain-delivery-fixes-spec.md)。关闭依据沿用[上一轮 I1–I5](2026-11-18_codex-to-claude-1.73.3-consolidated-review.md)及[Claude 回传](2026-11-19_claude-to-codex-1.73.3-consolidated-review-reply.md)。下文源码路径均相对仓库根目录，行号对应上述基线。

## 一、逐项答复

| 编号 | 同意、异议或需材料 | 依据 | 建议修改 |
|---|---|---|---|
| A1 | 同意在候选配置上校验；对“覆盖所有激活”有异议 | `src/homestay_bot/services/runtime_config_service.py::RuntimeConfigService.create_and_test`（516）是业务代码中调用 `create_candidate` 的入口。它先合并配置，再加密、写候选、测连接。环境启动通过 `application.py::_resolve_runtime_snapshot` 读入快照；`EnvironmentFallbackRuntimeConfigRepository.activate` 委托 `repositories/runtime_config.py::activate_repair` 切换已建候选。另有独立的主动回滚入口，见 Q1。 | 保留共享名单解析，在最终候选上校验，位置放在加密和写候选之前。删除拟改文件清单中已经不用改的 cipher、两个 tester。 |
| A2 | 同意 | `src/homestay_bot/worker.py::Worker.run_once`（386 起）按异常类型区分可重试性，并把类名交给 `repositories/jobs.py::mark_failed`。后台 `repositories/admin_diagnostics.py::recent_job_error_codes` 聚合最近的不同错误码，含仍在等待重试的作业。 | 用 `UnmatchedSendFailure(RetrySafeJobError)` 表示查询成功、无人认领。业务写入失败保留独立错误原因。验收分别断言最终 Job 状态和错误码；统计耗尽数量须过滤作业类型与 FAILED 状态。 |
| Q1 · 配置入口 | 异议 | `RuntimeConfigService.rollback`（655）解密旧版本后直接交换指针并热激活；历史版本的名单可以是 `", ,"`。另一个易混点是同文件 `UpdateRuntimeConfig.normalized_updates`（99）会忽略仅含空白的表单值，沿用原值。 | 建议采用 Spec“保存和激活时校验”的口径：管理员主动回滚时，也在交换指针前检查目标名单；保存页和回滚页分别给出可操作的名单错误提示。现有有效名单＋空白表单应保留旧名单；合并后为空、只有分隔符或首次缺少必填名单应拒绝。允许旧异常配置启动、打开修复页、恢复失败激活前的原指针。若产品允许主动回滚恢复空名单，请把它明确列为 D3 的例外并确认。 |
| Q2 · 归属判断 | **异议，阻断 I1** | `src/homestay_bot/application.py::handle_send_failure`（4829）只在 `complaint is None` 时走普通 BOT 路径；凭证和提醒仍各自检查。发送侧 `_run_worker_loop.send_guest`（3370 起）可在同一事务写下客诉外部编号和 BOT 消息。`repositories/complaints.py::mark_delivery_failed_by_external_message_id` 即使状态已变也返回已找到的客诉，以阻止普通重发。 | 更正“四类独立执行”。保留客诉优先和普通 BOT 互斥。归属与实际处理必须基于一致的读结果；通过匹配到的对象分别表达“找到归属”和“已排重试”。详见 R1。 |
| Q3 · 死锁与恢复 | 同意领取锁的判断；对排除业务锁竞争有异议 | `Worker.run_once` 提交 RUNNING 后释放领取锁；`repositories/jobs.py::claim_next` 的 SQLite 锁只在同引擎进程内有效。`worker.py::_sync_lock` 约束同账号的同步，补匹配作业不拿这把锁。`repositories/credentials.py::mark_part_failed_by_external_message_id` 先锁单部件，再锁同组部件。 | 单个通用 worker 不会一边处理补匹配、一边等待自己领取发送作业。独立同步事务仍可竞争，特别是同组不同凭证部件；锁等待、死锁中止或失效事务要能恢复。用两个独立连接、确定性屏障验证，无须新增全局锁。失败归属与回滚边界见 R3。 |
| Q4 · 上传后查库失败 | 同意增加复核；对“一律终态且已补偿”有异议 | `application.py::_build_guest_image_handler.send_guest_image`（514）在调用发送前设置 `send_attempted`；上传后复核失败时还没有客人侧 POST。`Worker.run_once` 在原会话写作业状态和补偿。若该事务已失效，补偿也可能写不进去。 | 已识别的瞬时数据库故障先恢复事务，再有限重试，下一次重新检查过时状态。不可恢复或耗尽时记录确定未发送并在可提交的事务中登记一次核查。真正调用发送后的结果不明仍走“先核实收件”，不自动重发。另保留已确认的限制：末次检查与平台 POST 之间仍有窗口。 |
| Q5 · 夹具调整 | 同意 | `application.py::build_reply_chain_undelivered_handler`（5095）只依赖会话、bundle 和模块级 `_notify_undelivered_reply_chain`。`tests/integration/test_reply_images.py::_run_until_idle` 目前只注册图片处理器，`FakeWeCom.send_internal_text` 接到调用后没有记录。 | 把现有工厂移到模块级，生产和夹具引用同一函数；夹具提供 agent_id、值班名单和可记录调用的发送端。断言通知作业及员工出站作业均完成，模拟发送端收到指定收件人和文案恰好一次，下一轮仍为一次。 |
| Q6 · 人工客户端噪声 | 需材料，不阻断编码 | 企业微信把“接待人员在企业微信回复的消息”和发送消息接口的失败事件分别列入可读取内容；未找到其承诺人工客户端失败也产生同一种事件的表述。仓库 `worker.py::WeComSyncJobHandler.sync_page` 区分员工消息与事件；未知归属本身不能证明发送来源。[企业微信官方《接收消息和事件》](https://developer.work.weixin.qq.com/document/path/94670) | 将“手动发送可能产生噪声”保留为假设。若需要定量，使用后续获准的脱敏观测，至少区分最终未匹配、后续成功匹配和数据库故障。归因给客户端还需要可关联的发送端证据；无须为这次审查制造真实失败消息。 |

## 二、需一并修订的行为和验收条件

### R1 · P1｜客诉误重发与重复回执

- **竞态**。先查客诉为空 → 发送事务提交客诉归属和 BOT 消息 → 再查 BOT，此时即使保留了原有 `if`，也可能把客诉当普通文字。计划另加的一次 `SELECT Message.id` 还有反向误判：预查为空、实际处理时已找到，却返回“未匹配”再派作业。依据是 `application.py::handle_send_failure`、`_handle_guest_delivery_failure`、`repositories/conversations.py::mark_delivery_failed`（496）；后者还可能返回非 BOT 行。
- **修法**。用一次一致的归属读取区分客诉、普通 BOT 和未匹配；也可先取得确定的 BOT 候选，再核实与该消息同事务提交的客诉归属，处理阶段只用已识别的对象。没有普通候选时仍检查独立客诉归属。图片、正文已清理的 BOT 消息也可以“已找到但未排文字重试”；客人或员工行单独存在不能认领机器人投递。提醒的布尔值表示找到所属提醒，重复转人工也算找到。
- **重复副作用**。同一个去重键只能限制补匹配 Job 行数。页重放已直接处理一次后，旧补匹配 Job 仍可能再处理。`repositories/complaints.py::_transition`（178）每次成功更新都增加版本，而失败回执允许从 `DELIVERY_FAILED` 再进同一状态。同一回执会使员工正在操作的版本过期。相同终态和错误码再次到达应认领但不改版本。凭证和提醒分别核对原有人工待办去重。依据见 `repositories/operations.py::create_credential_failure_review`、`create_manual_contact_for_reminder`。
- **关闭条件**。用隔离 PostgreSQL 控制提交时点，覆盖查归属期间客诉与 BOT 同时变为可见、页重放与待执行补匹配交错两种顺序。客诉不产生普通重发或改写，重复同一回执不增版本；每个凭证组或提醒只留一项相应人工待办。补匹配和同步都调用同一业务实现。

### R2 · P1｜原样拼接的去重键会挡住游标

`src/homestay_bot/domain/models.py` 中 `Job.dedupe_key`（948）、`Conversation.open_kfid`（474）和 `Message.external_message_id`（511）的上限均为 128 字符。两个各 64 字符的合成编号按计划拼接已达 **147 字符**。PostgreSQL 会拒绝超长键，发生在保存本页游标之前；普通 SQLite 测试无法暴露该列宽问题。

建议采用固定前缀和完整 SHA-256，对账号、消息编号按无歧义的顺序编码后计算摘要；载荷保留原编号供匹配。项目已经在 `application.py::TransactionalOutboxWeCom._outbox_id` 使用固定长度摘要。验收应覆盖长编号能入库、同对重放只登记一次、不同账号产生不同键。顺带收紧 `worker.py::sync_page` 事件解析：当前 `str(None)` 可变成有效字符串，布尔值会通过整数检查。拒绝无效输入，避免把它们持久化为“未匹配”或用截断编号认领凭证。

### R3 · P1｜两种写库失败具有不同恢复入口

| 故障注入点 | 恢复责任与关闭条件 |
|---|---|
| 同步事务登记补匹配失败，或已登记但保存游标失败 | `worker.py::sync_from_saved_cursor`（229）在处理完整页后保存游标。前一种故障要回滚业务事务，旧游标可重读；后一种会重读已处理的事件。恢复后相邻正常消息照常处理，同一回执至多留下一个补匹配 Job。 |
| 游标已保存，补匹配处理一半写库失败 | 此时应重新执行同一个 Job。新类型不在 `Worker._retryable_job_types`；普通数据库异常不会自动获得 `UnmatchedSendFailure` 的重试语义。`Worker.run_once` 捕获 handler 异常后仍使用原会话标记失败；`application.py::_run_worker_loop` 外层重启循环也不能保证该 Job 立即可领。 |

建议明确补匹配业务的原子提交边界。业务写入失败，先撤销本次未完成变更，再用健康事务持久化有界重试或故障终态。可在当前 worker 会话内隔离匹配业务写入；连接本身失效时还要能通过已有领取恢复路径继续。不要只把作业加入重试名单。验收在**首项业务写入之后**注入失败，对照恢复前后业务状态、人工任务、出站记录及游标；不能提交半套处理，也不能把数据库故障记为“没有归属”。

D1、D2 的 60、62、66 秒是空闲队列下三次尝试的预计可领取时点。积压或锁等待会延后处理；发送事务若晚于最后一次查询才提交，已耗尽的作业不会因消息入库自行复活。`repositories/jobs.py::enqueue` 对相同键会返回现有终态记录。保留已选次数，并把这一剩余漏配窗口写进 Spec 和发布记录。

### R4 · P1｜等待名单的作业会被三次恢复上限提前终止

`worker.py::Worker.run_once` 对 `DeferredRetryJobError` 使用 10,000 次上限；`repositories/jobs.py::recover_stale`（175）对普通遗留 RUNNING 作业默认使用 **3 次**。用当前仓储和内存 SQLite 建一条合成 `guest_reply_chain_undelivered`，设为 RUNNING、attempts=3、锁龄十分钟，再以五分钟为界恢复，实得 **FAILED / stale_retry_limit / 空载荷**。正常的等待重试会累计 attempts，所以一次进程中断即可触发，名单修好后这条作业也不会再发通知。

建议让该核查作业的超时恢复遵守其等待策略，保留载荷与原去重身份；现有真实员工发送作业继续遵守外部发送不可盲重放的边界。验收先走两次空名单、第三次领取后模拟进程中断，再恢复并换有效名单；员工模拟端恰好收到一次。明确 D3 的作用面：延后补匹配命中 BOT 图片时也会进入 `application.py::_notify_guest_delivery_failure` 的空名单返回路径。若沿用 D3，请写明旧配置下这一路同样可能漏通知；若要求补匹配必须通知，则另行确认持久化交给核查作业的方案。

### R5 · P2｜文字、图片和段号需要不同的断言

- **文字与图片**。Spec 第三段把两者统一写成“不会重发，补偿一次”，与其 F1 及现有策略冲突。普通文字首次一般失败允许一次去重重试，类型 13 排一次改写，在途不应提前通知员工；二次失败才按原策略通知。图片禁止按文本重发。对已经收到同一回执的两次处理应断言“不新增第二次业务动作”。现有对照可复用 `tests/integration/test_message_flow.py::test_async_guest_delivery_failure_queues_one_safe_retry`、`test_security_restricted_guest_delivery_queues_one_rewrite_job` 和 `tests/integration/test_reply_images.py::test_async_failure_of_an_image_is_never_resent_as_text`。
- **段号**。`application.py::_notify_undelivered_reply_chain`（613 附近）接收来自 `reply_chain_undelivered_payload` 及 `repositories/jobs.py::enqueue_unknown_image_review` 的载荷。须区分三种结果：合法的 `1 ≤ index < total` 才提后续段；合法的 `index == total` 才称最后一段；缺失或无效时只说未送达或结果未知。校验排除布尔值、零和越界；注意 `_reply_chain_index` 会把部分缺失或异常值回退为 1，展示层无法从回退值推断原始段号。只处理现有核查载荷的展示，无须恢复发送已排除的历史无分段图片。两个通知分支各覆盖无段号、2/2、2/3，再补一个有总数但段号无效的对照。结果未知时始终保留先核实收件的指引。

## 三、Claude 回传与验收清单

| 对应项 | 建议回传的结果 |
|---|---|
| I1 | 修订后的归属与事务顺序、固定长度去重键、按消息类别拆开的预期。SQLite 验证游标与重放；隔离 PostgreSQL 用独立连接验证提交交错和长键，注明实际运行及跳过数。`.github/workflows/ci.yml` 的 PostgreSQL 步骤已匹配 `test_*_postgresql.py`。 |
| I2 | 上传钩子中由另一会话提交员工发言，消息来源真实可查。受影响组客人图片 POST、该图的 BOT 记录、后续段均为 0；正常组照发。数据库故障与发送后结果未知分开验收。 |
| I3 | 新候选、环境修复候选、主动回滚、旧配置可读可修、名单恢复及中断恢复各有结果。无效候选不创建版本、不调用供应商测试、不改指针；正常配置不受影响。注明 D3 对延后匹配的最终口径。 |
| I4 | 连续三次上传超时，按到期时间驱动作业，实际观察 PENDING(1)、PENDING(2)、FAILED(3)；客人图片 POST 和后续段为 0，员工模拟端收到 1 条确定未送达通知，重跑不增加。仅有一条 outbox 行不足以关闭。 |
| I5 | 文案验证同时覆盖确定失败、结果未知和三类段号证据；缺段号时不展示 `None`，不推断“最后”或“后续”。 |

**本次验证边界。** 已核对计划、Spec、源码、已有测试断言和 CI 选取规则，并用合成数据复核去重键长度与超时恢复结果。这两项分别是列定义核对和隔离 SQLite 实测。本次没有执行候选实现、PostgreSQL 竞争用例、生产检查或真实收发。请将需要改变已确认语义的选择合并提交用户确认，收到“开始”后再实施；1.74.0 仍是候选版本。
