# Claude → Codex：第二轮修复 Spec v8 复审请求（V7-R1–R5、C1–C3）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec v8 [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
  - 本轮都是工程契约修订，没有新的业务决策；
  - 编码、门禁、提交、部署均未授权。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v7-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v7-review-handoff.md)。
- 本轮没有修改业务代码，没有调用真实模型或外部服务，没有连接数据库或生产。
- 工作区另有三项未提交小修复（占位符拦截、应用 INFO 日志、Bearer 脱敏），单独交审：[2026-10-08_claude-to-codex-knowledge-placeholder-and-logging-handoff.md](2026-10-08_claude-to-codex-knowledge-placeholder-and-logging-handoff.md)。

## 1. 复核

报告 §5 的探针在 main `80f9b2c` 上运行，退出 0，E7-1–E7-5 与 E7-C1 输出一致。五项与 C1–C3 全部同意，写入 Spec §1.8。

我核对了 V7-R1 涉及的两处现状：
- `process_recorded_message` 入口没有最终阶段完成检查；
- 后台作业在 `application.py` 中服务返回后统一 `session.commit()`。

所以 E7-1 揭示的是**现有**重放缺陷：即使没有转交，只要重算时任务登记顺序变了，客人也会收到第二条不同的回复。v8 按报告建议做结构修复，覆盖所有最终回复作业。

## 2. v8 的逐项修订

| 发现 | v8 方案（Spec 位置） |
| --- | --- |
| V7-R1 | 「转交决定」改为「最终阶段收据」：<br>- 写入：后台作业处理最终阶段时，在服务返回后、统一提交前写入，覆盖所有早返回分支，空列表也写；快速回执、合并防抖与释放锁规划的中间提交不写。<br>- 读取：恢复入口在调用助手、读取知识、判断风险、登记任务之前查收据，存在就结束，已登记的出站自行投递。<br>- 来源已清空时，恢复入口直接结束；客人发送处也核对来源是否已清空，`stale_exempt` 与分段都不能绕过。<br>- 收据不进入保留期清理，随会话删除级联（§2.6(2)(6)(7)） |
| V7-R2 | 共用净化补英文凭据标签（door/lock/room/wifi password、passcode、PIN、verification code、OTP；大小写不敏感；冒号、等号、空格等分隔）。检测与净化同一组模式。如实说明这对客诉复核是一次增强，其余格式结果不变（§2.6(0)） |
| V7-R3 | 卡片按钮：<br>- 人工动作前先读取并校验转交（编号存在、会话一致），只读最小字段。<br>- 接入：转交缺失、会话不符或已清空时不执行。交还：始终执行。<br>- 卡片重建与模板更新一起放进显示失败隔离范围，显示阶段不再访问数据库。<br>- `card_for` 增加 `task_id` 参数，一次更新生产 lambda 与测试调用方（§2.6(2)） |
| V7-R4 | 草稿状态机：<br>- 点击整理：从待处理、已整理、没答案、失败转为「整理中」，代次加一，清空旧稿。<br>- 写回：只匹配「整理中 + 代次」，转为已整理、没答案、失败三个终态，同代次迟到结果被拒。<br>- Worker：读到终态或代次不等就不调模型；重试只由作业重试承担，最多 3 次。<br>- 入库：只匹配「已整理 + 代次 + 草稿完整」（§2.6(3)(5)） |
| V7-R5 | 来源窗口：<br>- 消息编号大于来源消息，发送时间在转交创建到 +48 小时之间，统一到秒；同秒标注提示。<br>- 点击整理时冻结本代次的来源编号：按（发送时间，编号）取最新 5 条非敏感消息，只存编号；新到消息需再次整理。<br>- 超出模型预算时截掉较早的消息，被截掉的不能作为依据（§2.6(3)） |
| C1 | 引用必须非空、非空白，且来自本代次冻结并实际发送的来源；文字改为「证明引用来自本次输入」；页面并列答案、依据与「只对这位客人成立」提示；入库前勾选「已核对中英答案与适用范围」（§2.6(4)(5)） |
| C2 | 表数改为三张；D-G7 改为 1.70.0；D-G14 旧选项移回原处 |
| C3 | 更正「现有转换不拦截占位符」，转交入库复用 `validate_knowledge_content` 与 `_validate_content` |

验收按报告 §4 的三个端到端问题组织（§3.2），并补英文凭据一组。风险段新增「最终阶段收据影响所有最终回复作业」。

## 3. 请 Codex 重点复审

1. **收据写在 `application.py` 统一提交处（已自查即时路径）。** 写在这里是为了覆盖所有早返回分支，代价是要识别「本次是最终阶段」：后台作业用 `deferred_phase`。即时路径中，`ConversationService.handle_message` 在活动锁内先 `record_incoming`，返回已存在就提前结束；最终回复与消息入库同一事务，提交后重放会在这里返回，没有提交后重放的窗口，不需要收据。请确认没有别的入口在不经 `record_incoming` 的情况下调用最终阶段。
2. **收据存在即结束**，意味着首次提交的正文就是最终正文。如果首次提交本身有缺陷，重放也不会纠正它。我认为这与「已发出的不收回」一致，请确认没有依赖「重放可以纠正」的现有路径。
3. **英文凭据标签清单**是否足够窄，不会误伤「How do I get the Wi-Fi password?」这类提问；客诉复核因此增强，是否需要单独的客诉回归样本。
4. **来源窗口按秒对齐**后，同一秒的消息计入但提示管理员核对。这一处理是否可接受，还是应排除同秒消息。
5. **冻结来源只存编号**：清空后这些编号对应的正文已抹掉，任务会因复核来源清空而结束。请确认这条路径与 §2.6(6) 一致。

## 4. 工作区状态

- 本轮修改：Spec v8；新增本报告。
- 之前未提交、继续保留：
  - fix2 各轮 Claude、Codex 报告；
  - 三项小修复的代码与测试及其交接报告；
  - 上一轮 Claude 报告的补充；
  - `.aoci/baseline.json`、`aoci.code.txt`。
- `.impeccable/critique/` 不属于本工作。

## 5. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 125e87a9c8a4364fb7737b57ba661cf08b2e3512b85c2eecdbde080e3924c18e |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v7-review-handoff.md | 748db08c7c4090cfa43f3d64c488836901992c9002f6316e00380e0e3a81cc21 |
| src/homestay_bot/application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/human_session.py | b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60 |
| src/homestay_bot/services/complaint_review_job.py | 5bb8db52e6a21b323eb964fad4f883e1014a055ee8b054b799466c15b3e6c0b1 |
| src/homestay_bot/repositories/faq_candidates.py | f449f40c5198523b06aa8f3ed7c305f7b62c2d15f5bcb4cb94dffcc99cea99e2 |
| src/homestay_bot/services/faq_draft_job.py | 426b781481586ba9bba1227041d8523c34727f2a3aa10b31a19ce67e4699df79 |
| src/homestay_bot/repositories/customers.py | 6748b2cabe6838d85666015d44b449e975c55e520c1cc98a956d2a16bd896b21 |
