# 消息链投递修复 Spec（1.74.0 候选）

**状态（2026-11-19）：三段全部由用户确认（原话依次为「确认第一段」「确认第二段」「按建议确认」，D1–D3 按建议执行）。Codex 审查后新增的第四段（M1–M9、D4、D5）已由用户确认（原话「按建议确认」）。用户随后回复「开始」，已按 I5、I2、I4、I3、I1 顺序在本地实施并完成离线验证（SQLite 全量与独立 PostgreSQL 16 专项）；尚未提交、发版或部署。**

> **2026-11-19 补记**：Codex 审查了[实施方案](../reviews/2026-11-19_claude-to-codex-message-chain-implementation-plan.md)，审查意见见[审查报告](../reviews/2026-11-19_codex-to-claude-message-chain-implementation-plan-review.md)。据此新增**第四段「审查后修订」**，其中 M1–M9 和新决定 D4、D5 **待用户确认**。前三段中凡与第四段冲突的地方，以第四段为准，冲突处已标注「→ Mx」。全部确认、并且用户回复「开始」之后才写代码。

- **来源**：[Codex 综合复审](../reviews/2026-11-18_codex-to-claude-1.73.3-consolidated-review.md) 的 I1–I5，以及 [Claude 回传](../reviews/2026-11-19_claude-to-codex-1.73.3-consolidated-review-reply.md)。
- **用户决定**：2026-11-19 回复「先写消息链 Spec」。
- **范围**：只处理 I1–I5。B1、B2、O1–O3 不在本 Spec 内。

---

## 第一段 · 现状证据

### E1 · 失败回执比消息入库先到时会被丢弃（I1）

**回执怎么进来**
- `worker.WeComSyncJobHandler.sync_page` 逐条处理一页事件。遇到 `msg_send_fail`，就用 `fail_msgid` 和 `fail_type` 调用 `_handle_send_failure`。
- `sync_from_saved_cursor` 每处理完一页，立即通过 `SQLAlchemySyncCursorStore.save` 在独立短事务里保存游标。保存之后，这一页不会再被读到。
- 同步有两个入口：回调触发的 `wecom_sync` 作业，以及 `WeComMessagePoller.run_once` 的定时补拉。两者共用 `_sync_lock(open_kfid)` 串行执行，但和发送作业并行。

**回执分给谁**
- 由 `application.py` 运行时装配闭包里的 `handle_send_failure`（第 4829 行起）处理，在一个独立事务里依次按外部消息编号查四类归属：
  1. 客诉投递：`SQLAlchemyComplaintRepository.mark_delivery_failed_by_external_message_id`
  2. 普通机器人消息：`_handle_guest_delivery_failure`。它调用 `SQLAlchemyMessageRepository.mark_delivery_failed`；没有匹配时调用 `_notify_guest_delivery_failure`，但传入的消息为空，所以不会产生通知。
  3. 入住凭证部件：`SQLAlchemyCredentialDeliveryRepository.mark_part_failed_by_external_message_id`
  4. 生命周期提醒：`LifecycleReminderService.handle_send_failure`
- 四类都查不到时，函数**没有任何记录或日志**就提交了。随后游标照常推进。

**消息什么时候入库**
- 每类消息都是发送成功之后才写入外部消息编号，写入在发送作业自己的事务里，直到作业完成才提交：
  - 文字回复：`_run_worker_loop.send_guest`。先 `client.send_text`，再调用 `_record_complaint_delivery`、`replace_external_message_id` 和 `record_bot`。
  - 回复配图：`_build_guest_image_handler.send_guest_image`。先 `client.send_image`，再 `record_bot`。
  - 生命周期提醒和凭证部件：同样是发送后才回写编号（`lifecycle_reminders.py` 第 302 行，`credential_delivery.py` 第 363–384 行）。
- 所以，从「平台已受理」到「发送事务提交」之间存在一个窗口。这期间到达的失败回执会被永久丢弃，对应消息一直停在 `accepted`。

**为什么现有测试发现不了**
- 现有用例（`test_reply_images.py::test_async_failure_of_an_image_is_never_resent_as_text`、`test_message_flow.py`、`test_worker.py`）都是先入库、后收回执。
- Codex 在隔离环境复现了「回执先到」：结果为 `accepted`，员工通知 0 条。

**缺口**：生产上是否真的发生过，目前无法判断，因为查不到归属的回执没有留下任何记录。

### E2 · 上传图片期间管家接手，当前这张图仍会发出（I2）

- `send_guest_image` 只在上传素材之前调用一次 `_guest_reply_is_stale`。`upload_temporary_image` 返回后直接 `send_image`，中间不再判断。
- `_guest_reply_is_stale` 会检查三种情况：管家已接入原生人工会话、来源消息已清空、出现了更新的会话活动（`stale_exempt` 可以豁免最后一项）。
- PostgreSQL 在 READ COMMITTED 下每条语句取新快照；SQLite 在 legacy 事务模式下，SELECT 不会开启事务。所以在上传之后再判断一次，能读到这段时间里新提交的数据。
- 文字发送没有上传这一步，判断紧挨着发送，所以不受影响。

### E3 · 值班名单分割后为空时，核查作业显示完成却没有通知任何人（I3）

- `RuntimeConfigSnapshot.validate` 只检查 `wecom_duty_userids.strip()` 不为空，`", ,"` 这样的值能通过。
- `runtime_clients.py` 第 489–494 行按逗号分割、去空白、去重，`", ,"` 会得到一个空元组。
- `_notify_undelivered_reply_chain` 和 `_notify_failed_bot_message` 在收件人为空时都返回 False。但它们的调用方 `build_reply_chain_undelivered_handler.handle` 和 `handle_send_failure` 都忽略了这个返回值，worker 照常把作业记为完成。
- `validate` 不只在保存时调用，加载时也会调用：`RuntimeConfigSnapshot.from_dict`（解密加载）、`runtime_clients.py` 第 380 行（装配客户端）、`RuntimeConfigCipher.encrypt`（保存）、`LocalRuntimeConfigTester.test`（激活前测试）都会调用它。如果直接收紧 `validate`，已有的异常配置会连加载都失败，修复页也就打不开了。
- **缺口**：生产名单是否正常，需要登录后台查看，本 Spec 没有核对。

### E4 · 测试夹具没有注册核查通知处理器（I4）

- `test_reply_images.py::_run_until_idle` 只注册了 `GUEST_IMAGE_JOB_TYPE` 一个处理器。生成的 `guest_reply_chain_undelivered` 作业会因为 `unknown_job_type` 直接失败。
- 现有断言只检查「核查作业有一行」，检查不到员工通知的内容和条数。
- 重试耗尽后的图片作业（`RetrySafeJobError`，`worker.Worker.run_once` 中 `max_attempts=3`，`SQLAlchemyJobRepository.mark_failed` 按 2 秒、4 秒退避）会进入「确定未送达」通知，这条路径目前仓库里没有用例覆盖。Codex 在隔离环境验证过，结果正确。

### E5 · 最后一段配图的通知里仍写「后续段已停止」（I5）

- `_notify_undelivered_reply_chain` 里，「结果未知」分支只要有 `index` 就写后续段；「确定未送达」分支（「从第 N 段起未成功送达，后续段已停止发送」）则完全不判断段号。
- 所有配图作业都由 `_enqueue_guest_reply_continuation` 生成，载荷里一定带 `reply_chain`。欢迎图片也是追加在 `images` 末尾（`conversation_service.py` 第 2416–2418 行），所以「一段文字加一张欢迎图」会形成 2/2，这是常见情况，不是边角情况。
- **更正**：我在 1.73.3 复审 L2 里说「欢迎图片没有 `reply_chain`」，这个前提是错的。没有分段信息的图片载荷只可能来自历史遗留作业，当前代码不会再生成。

---

## 第二段 · 功能点（已确认）

### F1 · 查不到归属的失败回执，延后重新匹配（I1）

- **拆出匹配函数**（归属判定方式 → M1）：把 `handle_send_failure` 中按四类归属查找并处理的逻辑拆成一个函数，返回「是否找到归属」。`LifecycleReminderService.handle_send_failure` 改为返回 bool；普通消息这一类，以 `mark_delivery_failed` 是否找到机器人消息为准。各类找到之后怎么处理，保持现状。
- **登记补匹配作业**：四类都没找到时，在同一事务里登记一个 `wecom_send_failure_rematch` 作业。
  - 载荷只存 `external_message_id`、`fail_type`、`open_kfid`，都是平台给的不透明编号，不含客人信息。
  - 去重键为 `send-fail-rematch:{open_kfid}:{external_message_id}`（→ M2，改为固定长度摘要）。
  - `available_at` 设为当前时间加 60 秒。
  - 这个作业和回执处理在同一事务里提交，提交完成后游标才会保存，所以作业不会因为游标推进而丢失。
- **补匹配作业的处理**：调用同一个匹配函数。
  - 找到归属：照原有策略处理。文字按原来的规则重试或改写；图片只记失败并通知员工，不按文字重发；客诉、凭证、提醒都转人工。
  - 仍然找不到：抛 `RetrySafeJobError`，按现有退避再试两次。用完后作业进入 FAILED，错误码为 `UnmatchedSendFailure`，可以在现有任务诊断里查到，并记一条 INFO 日志（只写编号）。
- **`sync_page` 需要把 `open_kfid` 传给失败处理回调**，用于去重键和诊断。
- **由哪个 worker 执行**：通用 worker 只排除 `_MODEL_JOB_TYPES`（`application.py` 第 265 行），新作业类型不在其中，会自动由通用 worker 领取，分片配置不用改。处理器在 `build_worker_bindings` 里注册，和其他发送类处理器使用同一份运行时客户端。

### F2 · 上传之后、发送之前再判一次是否过时（I2）

- 在 `send_guest_image` 拿到 `media_id` 之后、调用 `send_image` 之前，再调用一次 `_guest_reply_is_stale`。
- 如果判定为过时，按现有的过时跳过路径处理：本张和后续段都不发，不记作已发送。（复核自身出错时 → M4）

### F3 · 值班名单为空时不能把核查作业记为完成（I3）

- **保存和激活时校验**（校验位置 → M5）：新增按分割结果判空的校验，只在保存（`RuntimeConfigCipher.encrypt`）和激活前测试（`services/runtime_config_tester.py::RuntimeConfigTester.test` 与 `application.py::LocalRuntimeConfigTester.test`）中执行，名单无效时拒绝。加载、解密和装配客户端仍然只用原来的 `validate`，所以已有的异常配置仍能加载，也能打开修复页。
- **核查作业延后重试**（中断恢复 → M6）：`build_reply_chain_undelivered_handler.handle` 在收件人为空时抛 `DeferredRetryJobError`，复用「长期等待管理员配置」的按小时低频重试。名单修好后，下一次重试会发出通知，并按原去重键只发一次。
- **同步回执路径不改**：`handle_send_failure` 中收件人为空的情况，不能在同步路径里阻塞游标。它依赖第一条的保存校验来防止新配置出错；已有异常配置的情况记入第三段风险。
- **历史无分段图片载荷**：明确不在支持范围内，不新增代码（见 E5）。

### F4 · 测试夹具接入真实通知处理器（I4）

- `test_reply_images._run_until_idle` 改为同时注册生产用的 `guest_reply_chain_undelivered` 处理器和一个模拟员工发送端。
- 在同一个用例里断言：素材上传连续 3 次超时 → 作业状态 PENDING(1) → PENDING(2) → FAILED(3)；客人侧图片 POST 为 0；续链停止；核查作业完成；员工通知恰好 1 条，内容写的是「未送达」。再跑一轮，通知不会重复。

### F5 · 只有确实还有后续段时才写「后续段」（I5）

- `_notify_undelivered_reply_chain` 的两个分支都改为：段号有效并且 `index < total` 时，才写「后续段已停止发送」（段号校验细则 → M8）。

---

## 第三段 · 风险、决策与验证（已确认）

### 需要用户决定

| 编号 | 问题 | 建议 |
|---|---|---|
| D1 | I1 用现有作业表补匹配，还是像 Codex 建议的那样新建一张「未匹配回执表」？ | **用作业表**：不需要迁移，去重、退避、诊断、保留期清理都是现成的。代价是：重新匹配只在固定时间点进行，不是消息一入库就立刻关联，最坏情况下要等约 66 秒才发现失败。 |
| D2 | 补匹配的首次延迟和总次数 | 首次 60 秒，加上现有退避共 3 次（约 60s、62s、66s）。之所以选 60 秒：凭证一个作业会连发多个部件，从发送到提交可能需要几秒；崩溃中断的发送作业 5 分钟后由 `recover_stale` 走终态补偿，不依赖回执。 |
| D3 | 名单为空时，同步回执路径里的员工通知怎么处理 | 本轮只靠保存校验来防止新配置出错，不额外处理。 |

### 风险

- **正常的无归属回执会变成失败作业**：比如管家在企业微信客户端手动发的消息，如果平台也会推送失败回执，现在会多出一条 `UnmatchedSendFailure`。目前没有生产数据能说明这种情况多不多。上线后需要观察数量；如果噪声大，再改为完成状态加日志。
- **仍有一个无法消除的窗口**：上传后判断过时，到 POST 真正发出之间还有一点时间，不能保证两者原子执行。
- **老配置的风险**：已有的异常名单在修复之前，同步回执路径里的员工通知仍然会被跳过。

### 验证计划

- **新增或修改的用例**（SQLite 加隔离 PostgreSQL）：
  - **I1**（按类别的断言 → M7）：回执先到、消息先到、两个事务交错、重复回执、补匹配作业处理期间写库失败后重读，这几种情况下，文字和图片都要各测一遍。断言：最终状态为 failed；员工补偿只登记一次；客人侧不会收到重发；查不到归属时作业以 `UnmatchedSendFailure` 结束，相邻的正常消息照常处理。
  - **I2**：用上传钩子在上传期间提交一条员工发言，断言客人侧图片 POST 为 0；另设一个正常对照组。
  - **I3**：名单分别为空白、只有分隔符、旧配置能加载、正常名单；名单修好后，通知只发一次。
  - **I4**：同 F4。
  - **I5**：分别测无段号、2/2、2/3，「确定未送达」和「结果未知」两个分支都要覆盖。
- **保留的对照**：限流可以重试；发送后读超时不重发；回执格式异常进入核查；入住凭证仍转人工；图片恢复只登记一次；5 分钟的严格过期线；两个分片相互隔离；30 秒的恢复节拍。
- **范围**：涉及 `application.py` 的装配和 worker 处理器，按项目规则提交前本地跑一次全量。不改 `REPLY_PATHS` 里的文件，真实模型门禁预计会自动跳过，实施后以 `git diff` 为准再核对一次。
- **真实收件验收**：这里的失败情况无法在测试号上人为制造，所以不做，理由写进发布记录。

### 拟改文件

`src/homestay_bot/worker.py`（`sync_page` 传 `open_kfid`）、`src/homestay_bot/application.py`（`handle_send_failure`，补匹配处理器及其装配，`send_guest_image`，`_notify_undelivered_reply_chain`，`build_reply_chain_undelivered_handler`）、`src/homestay_bot/services/lifecycle_reminders.py`（`handle_send_failure` 返回 bool）、`src/homestay_bot/services/runtime_config_cipher.py`、`src/homestay_bot/services/runtime_config_tester.py`、`src/homestay_bot/application.py::LocalRuntimeConfigTester`、`src/homestay_bot/domain/runtime_config.py`（新增名单解析校验），以及对应的测试文件。不涉及数据库迁移。

---

## 第四段 · 审查后修订（已确认）

依据：[Codex 实施方案审查](../reviews/2026-11-19_codex-to-claude-message-chain-implementation-plan-review.md)。以下每条都已对照代码核实。凡与前三段冲突的地方，以本段为准。

### M1 · 回执归属：客诉优先，按一致的顺序读取（I1，来自 Q2、R1）

- **前文的错误**：实施方案说「四类归属各自独立执行」，这不对。`application.py::handle_send_failure` 只有在找不到客诉时才走普通机器人消息的路径；凭证和提醒才是各自独立检查的。这个互斥关系要保留。
- **竞态**：先查客诉没找到，此时发送事务提交（同一事务里写入客诉的外部编号和机器人消息），再查机器人消息就找到了，于是客诉被当作普通文字重发或改写。
- **改法**：调整读取顺序。
  1. 先按外部编号查 `origin=BOT` 的消息，作为候选。
  2. 再查客诉。
  3. 客诉和机器人消息是在同一个事务里写入的，所以只要第 1 步看到了消息，第 2 步就一定能看到对应的客诉。
- **判定**：
  - 有客诉：走客诉路径。
  - 没有客诉，但有机器人消息：走普通消息路径，并视为「找到归属」。对图片、正文已清理的消息也一样：算找到归属，只是不排文字重试。
  - 两者都没有：再看凭证和提醒，仍然各自独立检查；全部没找到才算未匹配。
  - 客人或员工发的消息不能认领机器人的投递回执。
- **提醒**：`LifecycleReminderService.handle_send_failure` 改为返回 bool。只要找到所属提醒就返回 True，重复转人工也算。
- **重复回执不产生第二次副作用**：
  - 客诉：`complaints.py::mark_delivery_failed_by_external_message_id` 的 `allowed` 去掉 `DELIVERY_FAILED`。同一编号的回执再次到达时，仍然认领这条客诉，但不再改状态，也不增加版本号。
    - 现状：每来一次重复回执，版本号就加一，员工正在操作的页面会因此过期。
    - 为什么能这样改：重发会产生新的外部编号，所以「同一编号又失败一次」只能是重复回执。
  - 凭证和提醒：人工待办本来就按 `credential-failure:{delivery_id}`、`lifecycle-manual:{reminder.id}` 去重（见 `operations.py`），不用改。

### M2 · 去重键改为固定长度摘要，并收紧事件解析（I1，来自 R2）

- **问题**：`Job.dedupe_key`、`Conversation.open_kfid`、`Message.external_message_id` 的长度上限都是 128。按原方案直接拼接，最长会达到 147 个字符。PostgreSQL 会拒绝写入，而且这发生在保存游标之前，同步会一直卡在这一页。
- **改法**：去重键改为 `send-fail-rematch:` 加上 SHA-256 摘要，摘要的输入是按长度前缀编码的账号和消息编号，沿用 `TransactionalOutboxWeCom._outbox_id` 的做法。载荷里仍然保存原始编号。
- **顺带收紧事件解析**（`worker.py::sync_page`）：
  - `fail_msgid` 必须是非空字符串。现在 `str(None)` 会变成 `"None"`。
  - `fail_type` 必须是整数，而且不能是布尔值。
  - 不合格的事件直接忽略，不登记为未匹配。

### M3 · 两类写库失败分别由谁恢复（I1，来自 R3）

- **同步路径**：登记补匹配作业的写入失败时，业务事务回滚，游标不会保存，下次会重新读这一页（现状就是这样）。如果作业已经登记、但保存游标失败，重读时去重键会挡住重复登记。
- **补匹配作业**：匹配逻辑放在 `session.begin_nested()` 保存点里执行。
  - 出现 `SQLAlchemyError` 时，先回滚保存点，再抛 `SendFailureRematchStorageError(RetrySafeJobError)`。worker 会在仍然可用的会话里把作业标记为重试。重试次数和退避与 `UnmatchedSendFailure` 一样，但错误码不同，以免把数据库故障记成「没有归属」。
  - 如果连接本身已经断开，作业会停在 RUNNING，由现有的 `recover_stale` 在 attempts 小于 3 时改回 PENDING。
- **验收**：在第一项业务写入之后注入失败。断言不会提交一半的处理结果，人工任务和出站记录都没有残留。
- **剩余窗口**：60、62、66 秒只是队列空闲时的预计时间点。如果发送事务比最后一次匹配还晚提交，已经耗尽的补匹配作业不会再被触发，这条回执仍然会漏掉。这个窗口写进发布记录。

### M4 · 上传后复核过时状态时出错，改为有界重试（I2，来自 Q4）

- 上传后的那次 `_guest_reply_is_stale` 放在保存点里执行。出现 `SQLAlchemyError` 时，先回滚保存点，再抛 `RetrySafeJobError`。这时还没有向客人发过 POST，重试是安全的，下一次会重新上传并重新复核。
- 3 次都失败后，作业进入终态，在 worker 仍然可用的会话里由现有逻辑登记一次「未送达」核查。
- 已经调用 `send_image` 之后出现的未知结果，仍然走「先核实客人是否收到」的流程，不自动重发。

### M5 · 名单校验的位置（I3，来自 A1、Q1；取代实施方案的 A1）

- 新增 `domain/runtime_config.py::parse_duty_userids`，保存校验和 `runtime_clients` 共用这一个解析函数。
- 在两个位置拒绝空名单：
  1. `RuntimeConfigService.create_and_test`：合并出最终候选之后、加密和写入候选之前。
  2. `RuntimeConfigService.rollback`：解密出目标版本之后、交换指针之前（D5）。
- 两处都要给出能指导操作的名单错误提示。
- 表单里这一项留空时，`normalized_updates` 会沿用原来的名单，这是正确行为，保持不变。合并后为空、只有分隔符，或者首次配置没填名单，都要拒绝。
- 不在其他地方加校验：旧的异常配置仍然可以启动、打开修复页；激活失败时，恢复原来指针的补偿流程也不受影响。
- 加密器和两个 Tester 都不改，从拟改文件清单中删掉。

### M6 · 等待中的作业中断后不能被清空（I3，来自 R4，D4）

- **复现**：`repositories/jobs.py::recover_stale` 对超时的普通作业，只要 attempts 达到 3，就标记为 FAILED 并清空载荷。可是用 `DeferredRetryJobError` 长期等待的作业，attempts 本来就会一直累加，所以只要进程中断一次，这条作业就被永久终止，名单修好后也不会再发通知。
- **这是已有问题**：除了本次的核查作业，`faq_draft_job` 的「没有启用管理员」和 `conversation_service` 的「快速安抚仍在发送中」也用这种长期等待，都有同样的隐患。
- **改法（D4，建议采用通用修复）**：
  - `recover_stale` 识别 `last_error_code == "DeferredRetryJobError"` 的超时作业，按 worker 的长期等待上限（10,000 次）判断是否还能重试，并保留载荷和去重键。这个判断成立的前提是：领取作业时不会清掉 `last_error_code`（`claim_next` 第 137–139 行）。
  - 不可重放的外部发送类型仍然按原规则终止，不受影响。
- **验收**：名单为空时先跑两次，第三次领取后模拟进程中断；恢复后换成有效名单，员工模拟端恰好收到 1 条通知。另外用 FAQ 草稿作业做一个对照，验证同一条规则。

### M7 · 按消息类别分别断言（I1，来自 R5）

第三段里「不会重发，补偿只登记一次」的写法过于笼统，改为按类别分别断言：

- **普通文字**：
  - 第一次失败：排一次去重重试，在途期间不通知员工。
  - `fail_type=13`：排一次改写。
  - 第二次失败：按原有规则通知员工。
- **图片**：不按文字重发，只记失败并通知员工。
- **同一回执处理两次**：不会产生第二次业务动作。
- 复用现有的对照用例：`test_message_flow.py::test_async_guest_delivery_failure_queues_one_safe_retry`、`test_security_restricted_guest_delivery_queues_one_rewrite_job`，以及 `test_reply_images.py::test_async_failure_of_an_image_is_never_resent_as_text`。

### M8 · 段号的三种结果（I5，来自 R5）

- 段号有效的条件：`index` 和 `total` 都是整数（不能是布尔值），并且 `1 ≤ index ≤ total`。
  - `index < total`：写「后续段已停止发送」。
  - `index == total`：写「最后一段」。
  - 缺失或无效：只说「未送达」或「结果未知」，不推断是否为最后一段，也不推断有没有后续段。
- 注意 `_reply_chain_index` 遇到缺失或异常值时会回退为 1。展示层要读载荷里原始的 `index`，不能用回退后的值。
- 两个通知分支都覆盖：无段号、2/2、2/3、有总数但段号无效。「结果未知」分支始终保留「先核实客人是否收到」的提示。

### M9 · 测试夹具与 D3 的影响范围（I4、I3，来自 Q5、R4）

- **夹具**：把 `build_reply_chain_undelivered_handler` 提到模块级，生产代码和测试夹具引用同一个函数。夹具提供 `agent_id`、值班名单，以及一个会记录调用的员工发送端。断言：通知作业和员工出站作业都已完成；模拟发送端按指定收件人和文案恰好收到 1 次；再跑一轮仍然只有 1 次。
- **D3 的影响范围要写清楚**：延后补匹配如果命中的是机器人发的图片，会走 `_notify_guest_delivery_failure`。名单为空时，这一路同样会漏掉通知。按 D3，这次不处理，只依靠保存时的校验来预防。
- **Q6（人工客户端发出的消息会不会推送失败回执）**：继续作为未验证的假设记录，不阻断编码。上线后的观测要能区分三种情况：最终仍未匹配、后来匹配成功、数据库故障。

### 新决定

| 编号 | 问题 | 建议 |
|---|---|---|
| D4 | 等待中的作业中断后被清空：只修本次的核查作业，还是在 `recover_stale` 里统一修？ | **统一修**。三类长期等待的作业都会受益，修复位置也正是这几条路径汇合的地方。改动只放宽「等待中断后能否继续」这一条，不影响外部发送。 |
| D5 | 管理员回滚到一个名单为空的旧版本，是拦下还是作为例外放行？ | **拦下**，提示先修好名单再回滚。 |

### 拟改文件（取代第三段清单）

- `src/homestay_bot/worker.py`：`sync_page` 增加回调参数并做事件校验。
- `src/homestay_bot/application.py`：`handle_send_failure`、匹配函数、补匹配处理器及其注册、`send_guest_image`、`_notify_undelivered_reply_chain`、模块级的 `_build_reply_chain_undelivered_handler`。
- `src/homestay_bot/services/lifecycle_reminders.py`
- `src/homestay_bot/repositories/complaints.py`
- `src/homestay_bot/repositories/jobs.py`：`recover_stale`
- `src/homestay_bot/domain/runtime_config.py`：`parse_duty_userids`
- `src/homestay_bot/services/runtime_clients.py`
- `src/homestay_bot/services/runtime_config_service.py`：`create_and_test`、`rollback`
- 对应的测试文件，新增 `tests/integration/test_send_failure_rematch_postgresql.py`。

不涉及数据库迁移。
