# 消息链投递修复 · 实施方案 · Claude 交 Codex 审查

**2026-11-19，编码前审查。** 依据：[消息链投递修复 Spec](../specs/2026-11-19_message-chain-delivery-fixes-spec.md)，用户已逐段确认三段，D1–D3 均按建议执行。本文把 Spec 细化到文件、符号和执行顺序，请 Codex 在编码前审查方案。

**现状**：没有改任何代码。用户还没有回复「开始」。Codex 审查通过、用户确认以下两处 Spec 修订以后才动手。

- **问题来源**：[Codex 综合复审](2026-11-18_codex-to-claude-1.73.3-consolidated-review.md) 的 I1–I5，[Claude 回传](2026-11-19_claude-to-codex-1.73.3-consolidated-review-reply.md)。
- **基线**：`main` 为 `7a3c1f6`，生产版本 1.73.3。
- **目标版本**：1.74.0。

## 一、对已确认 Spec 的两处修订（需 Codex 与用户确认）

细化方案时核对代码，发现 Spec 有两处写得不够准。按项目规则，执行中出现偏差要先改 Spec 再改代码。

| 编号 | Spec 原文 | 修订为 | 原因 |
|---|---|---|---|
| A1 | F3：名单校验放在 `RuntimeConfigCipher.encrypt` 和两个 Tester 的 `test` 里 | 只放在 `services/runtime_config_service.py::RuntimeConfigService.create_and_test` 一处，位置在算出 `candidate` 之后、调用 `self._cipher.encrypt(candidate)` 之前 | 后台保存配置只有 `create_and_test` 这一个入口，它在同一流程里先调 `encrypt`，再调 `tester.test`。放在它前面，一处就能同时覆盖保存和测试，加密器和两个 Tester 都不用改，加载、解密、装配客户端的路径也不受影响。 |
| A2 | F1 只写了「超出次数后作业进入 FAILED，错误码 `UnmatchedSendFailure`」 | 新增 `UnmatchedSendFailure(RetrySafeJobError)` 异常类 | `worker.Worker.run_once` 把 `type(error).__name__` 记作错误码。直接抛 `RetrySafeJobError`，诊断页只会显示 `RetrySafeJobError`，看不出是哪种失败。 |

## 二、逐项实施方案

### I1 · 失败回执延后重新匹配（F1）

1. **回调带上客服账号**（`worker.py`）
   - `WeComSyncJobHandler.sync_page` 调用 `_handle_send_failure(failed_message_id, fail_type)` 时，增加参数 `open_kfid`，取 `sync_page` 自己的入参。
   - 同步修改 `__init__` 的类型注解，以及 `application.py` 第 4880 行装配处的 lambda。
   - `tests/unit/test_worker.py` 第 530 行的替身改为接收 3 个参数。

2. **拆出匹配函数**（`application.py`）
   - 把现在闭包里的 `handle_send_failure` 主体拆成同一闭包内的 `match_send_failure(session, bundle, external_message_id, fail_type) -> bool`。四类归属的查找顺序和各自的处理逻辑原样保留。返回值表示「是否找到任何归属」，判定方式：
     - **客诉**：`mark_delivery_failed_by_external_message_id` 返回值不为 None。
     - **普通机器人消息**：调用 `_handle_guest_delivery_failure` 之前，先按 `external_message_id` 和 `origin=BOT` 查一次 `Message.id`，查到就算找到。
       - **为什么单独查**：`_handle_guest_delivery_failure` 的返回值表示的是「是否已排重试」，不是「是否找到」；它有 8 处测试直接调用，不改它的签名。
       - **代价**：多一次按唯一索引的查询。
     - **凭证部件**：`mark_part_failed_by_external_message_id` 返回值不为 None。
     - **提醒**：`LifecycleReminderService.handle_send_failure` 改为返回 bool，找到提醒并转人工时返回 True。
   - 四类之间仍然各自独立执行，不会因为前一类找到了就提前返回，和现在一样。

3. **同步路径**（`handle_send_failure`）
   - 在同一个会话里先调用 `match_send_failure`。如果返回 False，就调用 `SQLAlchemyJobRepository.enqueue` 登记补匹配作业：
     - 作业类型 `wecom_send_failure_rematch`。
     - 载荷 `{"external_message_id", "fail_type", "open_kfid"}`。
     - 去重键 `send-fail-rematch:{open_kfid}:{external_message_id}`。
     - `available_at = now + 60s`。
   - 记录和作业一起在原来的 `session.commit()` 中提交。这次提交发生在 `sync_page` 返回之前，所以早于 `sync_from_saved_cursor` 保存游标。

4. **补匹配处理器**
   - 在 `build_worker_bindings` 里注册 `wecom_send_failure_rematch`。它直接复用 worker 当前的会话和 bundle，调用 `match_send_failure`，不另开事务。
   - 返回 False 时，记一条 INFO 日志（只写 `external_message_id`），然后抛 `UnmatchedSendFailure`。
   - worker 本来就有的重试节奏：第一次在第 60 秒，失败后等 2 秒、再等 4 秒重试，第 3 次仍失败就进入 FAILED。
   - 这个作业类型不在 `reply_chain_undelivered_payload` 处理的类型里，进入终态时不会触发其他补偿。

5. **分片**：新类型不在 `_MODEL_JOB_TYPES` 里，由通用 worker 领取。

### I2 · 上传后、发送前再判一次是否过时（F2）

- 在 `send_guest_image` 中，`upload_temporary_image` 返回 `media_id` 之后、`send_attempted = True` 之前，再调用一次 `_guest_reply_is_stale(session, payload)`。如果判定过时，就按函数开头已有的跳过逻辑处理：记 INFO 日志，然后 `return`。作业正常完成，不登记续链，也不写 `record_bot`。
- 这次判断在 `try` 块内。判断本身抛出数据库异常时，`send_attempted` 仍是 False，而且这类异常不属于 `httpx.TransportError`，所以原样抛出，进入终态并发出「未送达」补偿。这一点和上传失败（非传输类）的处理一致。

### I3 · 值班名单（F3，含修订 A1）

1. **统一的解析函数**
   - 在 `domain/runtime_config.py` 新增 `parse_duty_userids(raw: str) -> tuple[str, ...]`，按逗号分割、去掉首尾空白、去重并保持顺序。
   - `services/runtime_clients.py` 第 488–494 行改为调用它，保证保存校验和运行时解析用的是同一套规则。

2. **保存校验**
   - 在 `RuntimeConfigService.create_and_test` 中，`candidate` 得到之后，如果 `parse_duty_userids(candidate.wecom_duty_userids)` 为空，就拒绝保存。
   - 怎么拒绝，沿用现有的配置校验失败路径：`from_dict` 抛 `ValueError` 时路由层如何处理，这里就如何处理。具体映射到哪个错误码，编码时以路由的实际行为为准。
   - 不改 `RuntimeConfigSnapshot.validate`。

3. **核查作业等待名单修复**
   - `build_reply_chain_undelivered_handler.handle` 在 `bundle.duty_userids` 为空时抛 `DeferredRetryJobError`，进入 worker 现有的低频重试（`max_attempts=10_000`，退避上限 1 小时）。
   - 每轮 worker 都会按当前生效的配置重新装配 bundle，所以名单修复后的下一次重试就会通知员工。
   - `TransactionalOutboxWeCom` 的 `source_message_id` 去重保证只通知一次。

### I4 · 测试夹具（F4）

- `tests/integration/test_reply_images.py::_run_until_idle` 的 `bindings` 增加注册 `guest_reply_chain_undelivered`，取自 `build_reply_chain_undelivered_handler` 实际使用的 `_notify_undelivered_reply_chain`，处理器包装方式与生产一致，再加一个模拟员工发送端。
  - 这个处理器定义在 `create_app` 的闭包里，测试拿不到。我倾向于把它提到模块级，作为 `_build_reply_chain_undelivered_handler(session, bundle)`，生产和测试共用一份。这属于为了能测试而做的最小结构调整，请 Codex 判断是否接受。
- 新用例（I4 回归）断言：
  - 上传连续 3 次 `ReadTimeout` 后，作业状态依次为 PENDING(1)、PENDING(2)、FAILED(3)；
  - 客人侧图片 POST 为 0；
  - 核查作业为 COMPLETED；
  - `wecom_send_internal_text` 恰好 1 条，正文含「未成功送达」；
  - 再跑一轮，通知不增加。

### I5 · 只在确有后续段时写「后续段」（F5）

- `_notify_undelivered_reply_chain` 增加一个局部判断 `has_more = isinstance(index, int) and isinstance(total, int) and index < total`，两个分支都按它决定是否拼接「后续段已停止发送」。
- 「确定未送达」分支在 `has_more` 为 False 时，改成「第 N 段（最后一段）未成功送达，请管家人工跟进」。

## 三、测试与验证

**红测**：先写测试，并确认它们在当前代码上失败，再改实现。

| 用例（文件::名称，暂定） | 复现目标 | 当前代码上的预期结果 |
|---|---|---|
| `test_send_failure_rematch.py::test_receipt_before_record_is_rematched[text\|image]` | 回执比消息先到 | 失败：消息停在 accepted，员工通知 0 条 |
| 同文件 `test_duplicate_receipt_enqueues_one_rematch` | 同一页重放 | 新行为，用来证明去重 |
| 同文件 `test_unmatched_receipt_ends_failed_without_blocking_cursor` | 一直匹配不到；前后各有一条正常消息 | 新行为 |
| 同文件 `test_rematch_db_error_then_reread` | 补匹配作业写库失败后重读 | 新行为 |
| `test_reply_images.py::test_staff_reply_during_upload_stops_image` 加正常对照 | 上传期间员工发言 | 失败：图片仍然 POST 了 1 次 |
| `test_reply_images.py::test_upload_retries_exhausted_notifies_staff_once` | 重试用完后的通知 | 夹具改好之后才能测 |
| `test_reply_chain_sending.py::test_last_part_notice_has_no_following_parts[none\|2/2\|2/3]`，两个分支都测 | 最后一段的文案 | 失败：2/2 也写了「后续段」 |
| `test_runtime_config_*`：只有分隔符或空白时拒绝保存；已有的异常配置仍能加载；核查作业在名单为空时进入延后重试，修复后只通知一次 | 值班名单 | 失败：配置能保存，作业显示完成 |

- **数据库**：
  - I1 的两个事务交错用例在 PostgreSQL 上也要跑。新增 `tests/integration/test_send_failure_rematch_postgresql.py`，CI 的 `test_*_postgresql.py` 通配会自动带上它。
  - 本地用临时的 PostgreSQL 16 跑。
- **保留的对照用例**：
  - 原有行为：限流 45009 重试、发送后读超时不重发、回执畸形时进核查、入住凭证转人工。
  - 恢复机制：图片恢复只登记一次、5 分钟的严格过期线、两分片隔离、30 秒恢复节拍。
  - 这些都已经有用例，不新增。
- **全量**：改动涉及 `application.py` 的装配，按项目规则，提交前在本地跑一次全量，再加 Ruff 和 Mypy。
- **门禁**：预计 `REPLY_PATHS` 不会被命中。实施后用 `git diff --name-only` 核对；如果命中，按项目规则再定范围。

## 四、执行顺序

1. 在 `tasks/todo.md` 建任务单。
2. 先写 I5 和 I2：改动最小，红测能稳定复现。
3. I4：夹具和处理器提到模块级。
4. I3：解析函数、保存校验、延后重试。
5. I1：签名、匹配函数、补匹配作业，以及 PostgreSQL 交错用例。
6. 全量测试、静态检查、AOCI 维护。
7. 向用户汇报。提交、发版、部署需要另外授权。

## 五、请 Codex 重点审查

1. **A1 的位置**：`create_and_test` 是不是唯一能写入新配置的入口？还有没有别的路径会产生新快照，比如环境变量引导、修复激活 `activate_repair`？
2. **I1 的「找到」判定**：普通消息用 `origin=BOT` 的消息是否存在来判定，会不会漏掉某类本来应该找到的回执，或者把不该算找到的回执算成找到？比如客诉消息本身也写了 `Message` 行，这时四类会不会被重复处理？现在的代码就是四类各自独立执行，我没打算改变这一点。
3. **补匹配和发送作业是否会死锁**：补匹配作业在通用 worker 里运行，发送作业也在通用 worker 里。SQLite 下领取作业要排他锁，补匹配作业持有的事务会不会和发送作业相互阻塞？我的判断是不会，因为 worker 一次只处理一个作业，领取锁在 checkpoint 之后就释放了。
4. **I2 的异常处理**：上传之后再判断一次是否过时，这次判断如果抛出数据库异常，我打算按终态失败处理，并发出「未送达」通知。你是否同意，还是认为应该归为可重试？
5. **I4 的结构调整**：把 `build_reply_chain_undelivered_handler` 提到模块级，你是否接受？
6. **噪声风险**：管家在客户端手动发出的消息，平台会不会推送 `msg_send_fail`？你是否有资料或日志能回答这个问题？如果会，这类回执在上线后会稳定地产生 `UnmatchedSendFailure`。

**回传格式**：编号｜同意、异议或需材料｜依据｜建议修改。只需回 A1、A2 和第五节的问题；方案中的其他部分有异议时请另外列出。
