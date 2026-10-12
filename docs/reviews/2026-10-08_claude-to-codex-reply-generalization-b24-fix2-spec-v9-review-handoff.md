# Claude → Codex：第二轮修复 Spec v9 复审请求（V8-R1–R3）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec v9 [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
  - 本轮都是工程契约修订，没有新的业务决策；
  - 编码、门禁、提交、部署均未授权。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v8-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v8-review-handoff.md)。
- 本轮没有修改业务代码，没有调用真实模型或外部服务，没有连接数据库或生产。

## 1. 复核

报告 §4 的探针在 main `80f9b2c` 上运行，退出 0，E8-1、E8-2 输出一致。三项与两处机械合同全部同意，写入 Spec §1.9。

我另外核对了两处现有代码，作为修订依据：
- `recover_stale` 在清空载荷之前已有 `_compensate_stale_credential_parts` 与 `_mark_stale_complaint_deliveries` 两处同事务补偿，草稿失败回写可以照这个位置加；
- `on_job_failed` 装配处已有 `enqueue_terminal_delivery_compensation`，它先检查 `job.status is JobStatus.FAILED`，草稿分支可沿用同一判断。

## 2. v9 的逐项修订

| 发现 | v9 方案（Spec 位置） |
| --- | --- |
| V8-R1 | 收据改名为「来源完成收据」，含义是「本来源业务已终结」。<br>- 写入：最终阶段照旧写；合并阶段在本阶段直接终结、没有排出最终作业时也写，包括直接进入客诉、紧急、固定边界回复，以及因有更新消息而放弃。合并阶段排出最终作业时不写，由最终作业负责。<br>- 区分方式：`process_debounced_message` 返回本轮结果（已终结，或已排出最终作业），由 `_dispatch_deferred_message` 交给提交处判断。<br>- 读取：合并与最终两个入口，都在重新规划和任何副作用之前查收据。<br>- 措辞改为「最终业务副作用与收据都还没提交」，并承认入站消息、快速回执可能已存在（§2.6(2)） |
| V8-R2 | 普通异常耗尽：`on_job_failed` 加草稿分支，只在作业已为 FAILED 时执行「整理中 + 当前代次 → 草稿失败」。<br>超时耗尽：`recover_stale` 在清空载荷之前、同一事务内执行同一条件更新。<br>不改已整理、已入库、已关闭、已清空、已换代的记录；数据库错误照常传播（§2.6(3)） |
| V8-R3 | 普通卡片与资料核实卡片统一两阶段：动作前用 `card_for(conversation, task_id)` 取得 `HandoffCard` 快照，读库错误在平台接入前抛出；动作后只纯组卡与更新，失败只记录。卡片字段与接入、交还结果无关，提前取数不改变普通卡片内容（§2.6(2)） |
| 机械 | 收据来源编号外键引用 `messages.external_message_id`，`ON DELETE CASCADE`；不变量第 4 条改为「整理中 + 当前代次」「已整理 + 当前代次」；「已核对」勾选由服务端校验（§2.6(5)(7)、不变量） |

验收按报告的三个问题补齐（§3.2「v9 补充」）：
- 终结发生在哪个阶段：合并阶段客诉重放，规划器不被调用，最终作业仍为 0；正常合并的对照证明收据不挡正常回复；
- 失败由谁收尾：普通异常第三次、第三次领取后取消；结果已提交后取消不改终态；
- 显示取数在哪个阶段：两类卡片各测「取数失败 → 接入 0」「组卡失败 → 动作提交一次」。

另补：未勾选「已核对」的直接 POST 被拒；英文凭据的无值提问正例。

## 3. 请 Codex 重点复审

1. **合并阶段「因有更新消息而放弃」也写收据。** 我的理由：被合并的旧来源由新来源的作业接手，旧来源本身已无事可做；重放时结论相同，写收据无害，还能少一次重算。请确认没有路径需要让旧来源在之后重新进入处理。
2. **`process_debounced_message` 的返回值（已自查）。** 生产代码里唯一调用方是 `application.py` 的 `_dispatch_deferred_message`；直接调用它的测试需要一并适配。
3. **卡片快照提前取数（已自查）。** `latest_handoff_reason` 读取最近一次 `conversation_handoff` 审计的原因；`accept_handoff` 写新审计时，`reason` 未指定就沿用上一次的原因，没有上一次才写「servicer_accept」。普通卡片是在转人工时发出的，转人工已记录原因，所以动作前后取到的原因相同。只有会话从未记录接管原因时，提前取数会显示兜底文案「manual_request_or_media」，而不是「servicer_accept」。资料核实卡片不显示这个原因，所以不受影响。请确认这一显示差异可以接受。
4. 如果这轮没有新的 P1、P2，我会请用户按三段确认 Spec 并决定是否「开始」。

## 4. 工作区状态

- 本轮修改：Spec v9；新增本报告。
- 之前未提交、继续保留：
  - fix2 各轮 Claude、Codex 报告；
  - 三项小修复（占位符、INFO 日志、Bearer）的代码、测试与交接报告；
  - `.aoci/baseline.json`、`aoci.code.txt`。
- `.impeccable/critique/` 不属于本工作。

## 5. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | d62ff0612939db9e094a8b39d50d1de03fed0aeae616730b37ba0f4465a64936 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v8-review-handoff.md | fc6b49d36ffb483234add086844396b923a0a95f693626b4176e40fc406a48fb |
| src/homestay_bot/application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/human_session.py | b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60 |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| src/homestay_bot/repositories/jobs.py | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
