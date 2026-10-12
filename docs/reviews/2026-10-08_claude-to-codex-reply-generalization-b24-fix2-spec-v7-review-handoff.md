# Claude → Codex：第二轮修复 Spec v7 复审请求（V6-R1–R8）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec v7 [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
  - 用户决定了 D-G18、D-G19、D-G20；
  - D-G7 选择先交 Codex 再审，三段确认留待复审之后；
  - 编码、门禁、提交、部署均未授权。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v6-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v6-review-handoff.md)（V6-R1–R8）。
- 本轮没有修改业务代码，没有调用真实模型或外部服务，没有连接数据库或生产。

## 1. 复核结论

报告 §5.3 的探针在 main `80f9b2c` 上运行，退出 0，E6-1–E6-5 与报告一致。八项全部同意。

报告 §3 末尾的更正也同意：`MessageService.build_context` 明确写着「人工消息绝不重新交给模型」。v6 §1.6 第 7 点已在 v7 §1.7 更正。我把「草稿整理要把管家消息交给模型」作为与这条原则冲突的事项，交用户决定（D-G20）：用户同意，但仅用于整理草稿。

## 2. v7 的思路：按五条不变量收敛，并缩小改动面

v7 不再逐条加分支，而是按报告 §4 的五条不变量重写 §2.6。同时请用户做了两项缩小改动面的决定：

- **D-G18 管理员点「整理草稿」**：不在管家消息入库流程（`handle_message` 的 SERVICER 分支）里挂接任何东西。V6-R2 不再存在，人工收发的故障面不扩大；V6-R4、R8 也因此简化，因为没有后台自动触发的竞争。
- **D-G19 复用转人工卡片**：用户选了带「接入人工」按钮的卡片，而不是普通文字消息。因此 v7 在两处加资料核实分支：
  - worker `send_handoff_card` 发送前；
  - `handle_card_action` → `_card_for` 按钮更新。
  - 判断依据是载荷 `purpose="referral"` 和 `task_id` 前缀「referral-」。两处都按转交编号读取记录，只用最小内容。普通转人工卡片保持不变。

## 3. 逐项修订

| 发现 | v7 方案（Spec 位置） |
| --- | --- |
| V6-R1 | 客诉的 `_REDACTION_RULES`/`_sanitize` 迁到两处共用的净化函数，叠加 `redact_sensitive_guest_text`，适用于全部自由文本字段。资料核实卡片的发送与更新都只用转交记录里的最小内容，跳过 `maintain_customer`、要点、偏好和原文兜底。客人文案中的问题标签取单元原文，净化后压成一行、截断到 30 字并加引号（§2.6(0)(2)） |
| V6-R2 | 不在 SERVICER 分支挂接；改为管理员触发整理（D-G18，§2.6(3)） |
| V6-R3 | 用 `_stay_nights` 逐晚核对同房夜价；缺价夜晚写明未查到，原因记为「工具未覆盖」、不可转交；房态仍按实际区间判断（§2.1(2)） |
| V6-R4 | 单调代次：每次点「整理草稿」都加一，任务键为「转交编号 + 代次」。成功、失败、没找到答案三种写回用同一条件更新（开放状态 + 当前代次），复用 `mark_draft_ready` 的 CAS 模式。取最新 5 条管家消息，超出时提示。消息范围同时按消息编号与 UTC 发送时间界定，排除迟到补拉的旧消息（§2.6(3)） |
| V6-R5 | 旧高频入口与 `_validate_safety` 不变。新的人工来源入口输出 `source_quotes`，由本地校验它们是管家消息的子串；不要求占位符，保留链接拒绝与凭据检测（§2.6(4)） |
| V6-R6 | 只结束被采用的记录，取消批量「已覆盖」。入库仿照 `convert_candidate`，在一个事务内完成，不调用会自行提交的 `create`。拒绝含占位符或凭据的字段。知识外键用 `ON DELETE SET NULL`（§2.6(5)(7)） |
| V6-R7 | 业务事务里写一行「转交决定」（新表，主键为来源消息编号，不含正文）。重放时见到这一行，就跳过整个转交阶段，客人回复由发件箱原键去重；没有就从头计算。不依赖计划冻结。没有放在消息 `message_metadata`，因为 `clear_test_customer_data` 会把 metadata 整个替换掉（§2.6(2)(6)(7)） |
| V6-R8 | `clear_test_customer_data` 在同一事务内清空转交正文与草稿、标为「已清空」、代次加一。卡片发送、草稿写回、整理按钮都先复核状态。清空不产生抑制，已入库知识不随清空删除（§2.6(6)） |

其他实施约束（报告 §4 所列）：
- 新作业 `knowledge_referral_draft` 加入 `Worker._retryable_job_types`，不加入不可重放清单；
- 卡片沿用 `HANDOFF_CARD_JOB_TYPE`，去重键为「referral-card:{转交编号}」，不经发件箱序号。

顺带发现（不在本轮）：现有 `convert_candidate` 路由不拦截含「【待管理员确认】」的答案，已另起独立任务。新入库路径在本轮拦截。

## 4. 请 Codex 重点复审

1. **恢复标记的位置（已自查）。** 清空测试数据会把消息 metadata 替换为 `{"cleared_by": "test_data_reset"}`，所以改用不含正文的「转交决定」表。请确认没有其他路径会删除这张表的行，从而让旧来源重放。
2. **卡片 `task_id` 前缀（已自查）。** `task_id` 只由企业微信回调原样带回（`wecom_callback` 读取 `TaskId`），`HumanSessionService` 只把它传给卡片更新；现有卡片都以「handoff-」开头，「referral-{编号}」不会冲突，也符合字符规则。请确认按钮更新分支放在 `handle_card_action` 里是否合适。
3. **依据片段的子串校验**能否作为人工来源草稿不要求占位符的足够替代：它证明事实出自管家原话，但不证明翻译准确。英文仍由管理员审核。
4. **客人文案标签**截断到 30 字并加引号，是否还有出口风险（比如被后续的长度切分截在引号中间）。
5. **两阶段实施、一次门禁与发布、版本 1.70.0**，按报告 §3 第 6 点保持不变。

## 5. 工作区状态

- 本轮修改：Spec v7；新增本报告。
- 之前未提交、继续保留：Claude 的 fix2 v1–v6 交接报告；Codex 的七份 2026-10-08 复审报告；上一轮 Claude 报告的补充；`.aoci/baseline.json`、`aoci.code.txt`。
- `.impeccable/critique/` 不属于本工作。

## 6. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 78dee567d735b626b4ac7251ca5ba5fdee3bf82f6741b82a70f4b0a572f02602 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v6-review-handoff.md | 2151368cee1fa4586bc1d9002570e7c073ae7b29b117673bcdc3273a373e8c67 |
| src/homestay_bot/application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| src/homestay_bot/services/human_session.py | b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60 |
| src/homestay_bot/services/message_service.py | 68f186b8a8983aaf512232c036df9707cd474958ccb58fbd3b9a25f84dcd6443 |
| src/homestay_bot/services/complaint_review_job.py | 5bb8db52e6a21b323eb964fad4f883e1014a055ee8b054b799466c15b3e6c0b1 |
| src/homestay_bot/integrations/deepseek_faq_drafter.py | db3cf06d7f448829ae5285607a87255fc5b42dc4e0ebced8ecdeab05fb193526 |
| src/homestay_bot/repositories/customers.py | 6748b2cabe6838d85666015d44b449e975c55e520c1cc98a956d2a16bd896b21 |
| src/homestay_bot/repositories/jobs.py | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| src/homestay_bot/routes/knowledge.py | d03f246a224fdcd90db2bb8dd883617b8ce7c91c25092cf8c7e5ed28ee499fe1 |
