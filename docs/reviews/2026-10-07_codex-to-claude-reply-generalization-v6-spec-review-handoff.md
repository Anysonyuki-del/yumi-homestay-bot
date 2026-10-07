# Codex → Claude：回复泛用化 v6 审查与修复建议报告

- 日期：2026-10-07。
- 工作区：`/Volumes/02/code/homestay-bot`，分支 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 用户请求：「审查泛用化v6」，随后「写审查报告，审查报告应有依据，修复建议」。
- 结论：共同轮次计划、持久作业续接和明确的摘录位置方向成立；**仍有 2 项 P1、1 项 P2，需补齐计划异常传递、主回复等待前释放锁、独立撤回语义，再复审。**
- 状态：方案尚未实施。本报告只整理审查证据和建议，不构成编码、提交、推送、部署、生产写入或真实外部调用授权。

## 1. 审查范围与证据口径

审查对象是 [回复判定泛用化 Spec v6](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:1)，同时核对 [Claude v6 交接请求](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v6-review-handoff.md:1) 和 [上一轮 v5 修改建议报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_codex-to-claude-reply-generalization-v5-and-emergency-v3-spec-review-handoff.md:1)。本轮不对独立的紧急豁免 Spec v4 给出通过结论。

证据分为三类，不能互相替代：

| 类别 | 本报告覆盖的事实 | 不证明什么 |
| --- | --- | --- |
| 源码事实 | 当前端口、异常出口、活动锁及 deferred 提交装配 | 拟议 v6 已实现或生产当前状态 |
| 离线调用链观察 | 真实 `ConversationService.process_recorded_message`，注入助手和锁生命周期替身 | 新规划器正确性、PostgreSQL 实际竞争或真实消息收件 |
| 契约反例 | 按 v6 字段限制推导出的无法同时满足的示例 | 新 `resolve_task_request` 的运行结果 |

报告整理时重新核对第 7 节摘要：Spec、Claude 请求、核心源码与探针依赖未变化，复用刚完成的审查证据。没有为文档整理重复运行业务测试、全量测试或真实模型门禁。

## 2. 可保留的改进

1. **持久续接方向成立。** v6 §2.3 把软判定规划放进 debounce/final 作业，即时入口在入站同一事务里登记作业。当前 [SQLAlchemyJobRepository.recover_stale](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py:171) 的不可重放清单不包含 `wecom_process_message`，未达到尝试上限的过时 RUNNING 作业可恢复为 PENDING。该依据支持复用现有队列；新版入口、提交后的恢复和副作用幂等仍需实施验收，不能宣称无限重试或已经闭环。
2. **摘录位置和申请 ID 优于名称关联。** [PlanItem 契约](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:149) 使用核验后的 `start` 排序、`withdraws` 选择申请项，避免 `text.find` 总取第一次出现、`subject` 改写后关联失效。应保留，但需处理 V6-R3 的无前置申请情形。
3. **规划失败与主回复失败分开是必要的。** [§2.3 的失败策略](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:194) 不让词面命中重新授权建任务；V6-R1 要补的是已成功计划跨异常边界的实际传递。

泛用性方向仍是：模型选择有限的意图、证据和关联对象，本地核验来源、顺序、权限与实际动作。无需新增平行决策引擎、更多兜底关键词或通用语法解析框架。

## 3. 发现与修复建议

| 编号 | 优先级 | 缺口 | 直接影响 |
| --- | --- | --- | --- |
| V6-R1 | P1 | 正常规划成功后主回复失败，计划没有明确导出契约 | 第三批“历史、否定在主回复失败时仍不建任务”的承诺无法落实 |
| V6-R2 | P1 | final 软判定规划后重新加锁，继续主回复前没有释放边界 | 按该顺序实施可能在长模型等待期间阻塞新活动 |
| V6-R3 | P2 | 独立撤回没有更早的本轮申请可关联，却要求后续新申请登记 | 合法服务请求被整体转确认，规则与示例互相矛盾 |

### V6-R1 / P1：在第三批闭合成功计划的异常传递

**Spec 依据：**

- [§2.3 调用顺序](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:165) 与 V-a 规定：非软判定请求由 `respond` 在检索前规划。
- [§2.3 主回复失败](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:212) 却假定 `PlanOutcome` 已由会话层持有，要求异常分支继续按它判定。
- [第三批承诺和实施清单](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:346) 要在主回复失败后保留该保护，但 `GuestAssistantPort.plan_turn` 与会话层规划 helper 被列到第四批（第 374 行）。成功返回的 `AssistantDecision.turn_plan` 不能覆盖抛错出口。

**源码依据：** 当前 [GuestAssistantPort.respond](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:381) 返回 `AssistantDecision`；[AssistantUnavailableError](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:257) 是没有计划字段的异常。[ConversationService._process_model_reply_body](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1401) 等待 `respond`，失败后在第 1432 行调用 `_handle_facility_issue(..., None)`。当前第三个参数实际是 [advice](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1103)，不是可以保留计划的 `decision`，修订时须写清新增参数。

**离线观察 E1：** 助手替身在 `respond` 内构造局部的 `status=ok / kind=history_mention` 代理后抛出真实 `AssistantUnavailableError`；通过真实 `process_recorded_message` 处理「上次房间空调坏了，现在已经修好了」，在设施入口仅替换捕获函数。观察如下：

```json
{"adapter_created_plan":{"status":"ok","kind":"history_mention"},"error_exports_plan":false,"facility_received_advice":null,"facility_received_plan":null}
```

该探针只证明当前调用契约没有将适配器内部结果交回设施异常出口；局部字典不是已实现或已核验的 `TurnPlan`。发现成立于 v6 对生产者、消费者和批次依赖的矛盾，不能仅因新代码尚未存在就判为缺陷。

**最小修复建议：**

1. 保留正常请求在 `respond` 内规划的批次安排，第三批给 `AssistantUnavailableError` 增加可选的、仅适配器本地填写的 `plan_outcome`。规划完成后，后续主回复失败须保留该结果；未完成时明确为无计划或 failed。
2. 会话层捕获异常后取出计划，核对其 `source_sha256` 与冻结正文，再显式传入设施处理与 `resolve_task_request`。保留原异常原因，不从主模型回传字段补造计划。
3. 该字段、异常生产出口和会话消费端全部列入第三批；不依赖第四批 helper，不为取得计划重复调用模型，不把计划保存在助手实例的共享可变字段里。
4. 若选择把正常规划前移到会话层，也可成立，但须同时更新第三批端口、文件清单和模型调用预算；两条路径只选一条。

**验收要求：** 从真实 `DeepSeekGuestAssistant.respond` 的离线装配注入“规划成功、后续主回复失败”，再经过真实会话设施出口。历史已修好、明确否定均无任务且最终正文没有「已提交」；当前故障仍按计划登记，成功文案必须与实际任务和通知结果一致。另覆盖规划失败和来源摘要不一致，不能复用错误计划。只断言局部计划字段不足以验收。

### V6-R2 / P1：明确锁后有效分支继续主回复前的提交

**Spec 依据：** [§2.3 事务顺序](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:173) 规定 final 作业的 HUMAN_ACTIVE/agitated 情形先规划；规划后重新 `lock_activity` 并复核有效性。第 180 行又承诺 final 主回复等待不持活动锁。缺少的是：计划证明不是客诉、需要继续普通主回复时，何时释放重新取得的锁。

**源码依据：**

- [SQLAlchemyConversationRepository.lock_activity](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/conversations.py:90) 使用 `SELECT ... FOR UPDATE`，锁的生命周期属于数据库事务。
- [process_recorded_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:976) 继续调用主回复；其模型调用前没有第二个提交步骤。
- [_discard_stale_final](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1633) 明确模型完成后才加锁，锁由外层事务保持到提交。
- [application_lifespan 内的消息处理装配](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4420) 当前 deferred 服务的 `commit_boundary` 为 None，整体处理后才提交。v6 已要求改装配，但没有定义上述第二次释放。

**离线观察 E2：** 对真实 `process_recorded_message` 注入锁状态和提交回调替身，分别从“未锁”和“模拟 helper 锁后有效返回”进入；助手在 `respond` 入口记录锁状态：

| 初始状态 | `respond` 时替身锁状态 | final 链提交回调次数 |
| --- | --- | --- |
| 未持锁 | false | 0 |
| 模拟 helper 重新加锁后返回 | true | 0 |

这证明现有 final 链没有隐藏的释放步骤；它没有执行尚未实现的 helper，也不是 PostgreSQL 锁竞争实测。风险是按 v6 文字顺序新增重加锁后，把锁带入主回复等待。

**最小修复建议：**

1. helper 锁后复核分成“本轮直接处置并结束”和“继续主回复”两个明确出口。
2. 直接处置在短事务内完成副作用并提交；继续主回复只保留冻结输入及已验证计划，提交释放锁后再等待 `respond`。
3. 主回复结束或失败后，沿现有 `_discard_stale_final` 再加锁重查模式、原生人工和最新活动，再执行任务、客诉或出站副作用。第二次释放不能代替最后一次复核。
4. 明确 `application.py` 给 deferred 业务会话注入提交边界，并列出 helper 规划前释放、规划后继续主回复前释放两个调用点。复用现有事务与活动锁，不新增全局锁或并行事务框架。

**验收要求：** 在隔离 PostgreSQL 合成测试库通过真实 deferred 装配，暂停规划阶段和主回复阶段，分别证明新客人活动及员工接管可推进；恢复后过时结果不得建任务或登记出站。同时复用或补充提交后取消、服务重建、作业恢复的相关用例，核对任务与出站去重。该验证只针对新增事务边界，不扩大为无关数据库测试。

### V6-R3 / P2：允许没有本轮前置申请的独立撤回

**Spec 依据：** [PlanItem.withdraws](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:154) 只能选择本计划中位置更早的申请项；为空或无效即无法关联。[§2.5 表格](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:266) 又要求以下输入登记后续申请，但第 268 行规定无法关联的撤回与申请同轮时整体转确认。

**契约反例 E3：**

| 输入 | 首项撤回可选的更早申请 ID | 按第 268 行 | 表中要求 |
| --- | --- | --- | --- |
| 不用送毛巾了……还是送两条吧 | 空集合 | 不登记，确认 | 登记毛巾 |
| 不用送毛巾了，麻烦送两瓶水 | 空集合 | 不登记，确认 | 只登记水 |

即使模型完全正确、所有 `quote/start` 都有效，也不能给首项撤回填入合法的 `withdraws`。不能用前向引用，不能虚构本轮没有的旧申请，也不能靠名称相似补关联。离线契约代理计算得到两例都是 `confirm`，与表格的 `register` 要求冲突；尚未运行新 resolver。

**最小修复建议：**

1. 在 `quote/start` 有效、且本计划没有位置更早的申请项时，允许 `withdraws=None` 表示“撤回对象不在本计划”；本轮不因此创建或取消旧任务，后续明确的新申请独立判定。
2. 存在位置更早的本轮申请但撤回关联不明时，继续保守转确认；撤回摘录或位置失效、无法可靠判断顺序时也保留确认保护。
3. 有合法 `withdraws` 的撤回仅影响被选择的申请项；再次申请使用新 ID。无需增加主体名称匹配或已有任务取消能力。
4. 同步修改字段核验表、第 207 行失效项例外、§2.5 决策表和第三批验收要求，避免同一输入走不同结论。

**验收要求：** 保留上述两条应登记样例，并对照“先申请再撤回不登记”“先申请但撤回关联不明转确认”“重复措辞撤回第一项后重新申请登记”“跨消息合并的相同顺序”“无效位置转确认”。断言实际任务写入、通知及最终客人正文，不只断言排序或 `register` 布尔值；旧任务不应被自动取消。

## 4. 文档一致性修正

**C1 / P3：统一 D14 状态。** [Spec §3.3 D14](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:365) 已写“已决定，2026-10-07”，采用规划失败时先给安全提示、确认后登记；[§5 回应表](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:404) 和 [Claude 交接请求](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v6-review-handoff.md:26) 仍写两种建议待决定。请依据本次有效用户决定同步状态；本报告不以文档作者的声明替代授权，也不重新打开已经确定的业务取舍。

## 5. 建议返回的修订内容

1. 对 V6-R1–R3 逐项给出接受或不接受、依据、修订章节、受影响文件/符号、验收目标。
2. 第三批补全成功计划的异常生产和消费契约；第四批补全规划后继续主回复前的释放及最终再复核。
3. 给独立撤回、合法关联、无法关联和失效位置各自唯一结论，更新冲突示例。
4. 同步 D14 状态，保持每批只承诺当批已经具备的能力。不要用第四批的端口和 helper 证明第三批已安全。

上述建议服务于现有泛用性目标，不扩展已有任务取消、真实订房授权、语义检索开关或后台界面。修订与确认后，仍须用户明确回复“开始”才编码。

## 6. 验证、证据复用与工作区归属

- 本轮审查已完成：Spec 与源码调用链核对；E1 异常出口观察；E2 锁生命周期对照；E3 撤回契约冲突核对。三组离线观察均由同一个进程内命令执行，正常退出码为 0；该结果表示探针按预期观察到缺口，不是 v6 修复通过。
- 探针环境：仓库 `.venv/bin/python`，`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=src:tests/unit`；复用 `tests/unit/test_conversation_service.py` 的 `build_service`、`incoming`、`AssistantStub`、`MessageServiceStub`。替换仅存在于该进程中，没有业务文件、数据库或外部写入。
- 可复现方法：E1 在助手 `respond` 中创建局部计划代理后抛出 `AssistantUnavailableError`，在设施入口捕获参数；E2 给提交回调设置“释放锁”状态，给 `lock_activity` 设置“持锁”状态，记录主回复入口；E3 对两个样例枚举首项撤回之前的本轮申请 ID，结果均为空，再应用 v6 无效关联规则。
- 文档整理只需校验链接、行号、快照摘要、空白格式及受影响 AOCI 条目。相关输入未变化，复用既有探针；不重跑业务全量或付费模型门禁。
- 未覆盖：新 planner/helper/resolver 实现、PostgreSQL 竞争、真实模型改写集通过率、费用与 P95、真实外部工具、生产状态、实际订单/任务和客人收件。
- 本次新增只有本报告；按项目规则维护其 AOCI 条目与 baseline。既有 Spec、Claude 请求、历史 Codex 报告、`.impeccable/critique/` 和先前 AOCI 改动不归为本次创作。
- 不修改业务源码、测试、场景预期和回归基线；不读取、摄入、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。
- 不执行提交、推送、部署、真实 DeepSeek/Hostex/企业微信调用、生产写入或向其他会话发送消息。本文件供用户自行交接。

## 7. 审查快照（SHA-256）

这些摘要绑定审查版本，不证明实施或运行健康。后续文件变化时，只复核受影响的发现与证据。

| 文件 | SHA-256 |
| --- | --- |
| `docs/specs/2026-10-06_reply-generalization-spec.md` | `3bddf45072c7c354dcd591beb5474cf972849bcea789f78c9eed67f632f63d1f` |
| `docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v6-review-handoff.md` | `29fc42ea226f7db52827bcbe5f82140f20e07e83e5c02761e96574f3ad7119e4` |
| `src/homestay_bot/services/conversation_service.py` | `de2b6181756452dbf0ac354dff23285261d12d2e6b25066377ea46a8be335254` |
| `src/homestay_bot/application.py` | `270ab4a44183febde5616965677cfad0abb23b7db4ec3da3c52ef8632585abde` |
| `src/homestay_bot/integrations/deepseek_client.py` | `b185617cc0830befab18cbfba2d2ef04a54482fb83e13f72cba6ce51e27badda` |
| `src/homestay_bot/repositories/conversations.py` | `e5c6dee2b19df73b561a539506e5c3c58583f1df01c46793a46467c8a0cbfef4` |
| `tests/unit/test_conversation_service.py` | `0eab2f50c52cf2dd0930f98edd8789e695fd14aa895d471c831ed2b8c4a87f1d` |
