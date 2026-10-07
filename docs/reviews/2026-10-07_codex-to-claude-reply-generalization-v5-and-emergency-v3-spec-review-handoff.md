# Codex → Claude：回复泛用化 v5 与紧急豁免 v3 修改建议报告

- 日期：2026-10-07。
- 工作区：`/Volumes/02/code/homestay-bot`，分支 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 用户请求：审查返回的文档，并整理修改建议报告。
- 结论：共同轮次计划、统一任务判定和限定紧急豁免范围的方向成立；**仍有 2 项 P1、1 项 P2，建议修订 Spec 后复审，再进入确认与实施流程。**
- 本轮未实施业务方案；只新增本报告，并按仓库规则维护其 AOCI 条目。报告和建议不构成编码、提交、推送、部署或真实外部调用授权。

## 1. 审查对象与证据边界

1. [回复判定泛用化 Spec v5](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:1)。
2. [紧急询问豁免范围修复 Spec v3](/Volumes/02/code/homestay-bot/docs/specs/2026-10-07_emergency-exemption-scope-spec.md:1)。
3. [Claude v5 复审请求](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v5-review-handoff.md:1)。
4. 对照 [Codex v4 与紧急 v2 返回报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_codex-to-claude-reply-generalization-v4-and-emergency-v2-spec-review-handoff.md:1)。

证据分三类：

- **源码事实**：沿现有调用链读取，引用文件与符号。
- **离线观察**：使用构造输入、现有真实服务/仓储、内存 SQLite 和助手替身；没有真实模型或平台请求。
- **契约反例**：按拟议规则构造的最小代理或文本推演。新 `TurnPlan`、`_plan_with_released_lock`、`resolve_task_request` 尚未实现，不能将代理结果写成新版实现验收。

整理报告时重新核对第 7 节摘要：两份 Spec、Claude 请求和已冻结的四个核心源码摘要与刚完成的复审相同；其余探针依赖的源码和测试无工作区改动。因此复用该轮离线证据，不为文档整理重跑业务测试。

## 2. 已接受的改进与泛用性方向

v5 已在文档层补充这些内容：

- §2.6 将情绪词复核传入后续接管出口，区分可复核的 `agitated` 与退款、平台投诉、议价等硬边界。
- §2.5 将服务、遗失、订房意向和设施两条分支纳入统一任务判定，区分安全提示与任务登记。
- §1.7 更正首响基线，承认生产没有 `respond_ack` 调用，暂缓依赖该前提的 V-c。
- §2.3 规划失败时保留完整原问，并追加房价日期澄清，避免宣称仅靠分隔符解决无标点多问。
- §3.2 明确四批各自的承诺和保留边界。

这些是 Spec 改进，实施效果仍需以后验证。事务释放、豁免范围和撤回顺序虽已补充，具体契约仍有下列缺口。

“让模型做选择题”应贯穿到业务关系：模型选择有限的意图和候选证据，撤回时优先选择本轮已有申请；本地核验来源、位置、引用关系、权限和最终副作用。自由填写一个事项名称或给出原文子串，并不能自动证明它关联了正确的申请或正确的出现位置。

最小优化仍是复用现有轮次计划、持久作业、消息去重和任务入口；本轮不建议新增平行决策引擎、通用语法分析器或更多兜底关键词。

## 3. 三项修改建议

| 编号 | 优先级 | 问题 | 证据性质 |
| --- | --- | --- | --- |
| V5-R1 | P1 | 最近危险命中不能代表假设、引用或询问的语义范围 | 源码 + 拟议规则代理 |
| V5-R2 | P1 | 规划前提前提交缺少持久续接，中断后重放被去重跳过 | 真实 SQLite 仓储/服务 + 提交与取消替身 |
| V5-R3 | P2 | 重复摘录无法唯一决定申请与撤回的顺序 | 契约反例 |

### V5-R1 / P1：豁免应绑定明确的非现场范围

**修改位置**：[紧急 Spec §2.2 第 4 条](/Volumes/02/code/homestay-bot/docs/specs/2026-10-07_emergency-exemption-scope-spec.md:71)、§2.1、§2.3 验证。

**源码依据**：[EmergencyService._noncurrent_mention](/Volumes/02/code/homestay-bot/src/homestay_bot/services/emergency_service.py:154) 在 167–175 行因前缀含假设、引用或 `what is/are` 排除危险；[classify](/Volumes/02/code/homestay-bot/src/homestay_bot/services/emergency_service.py:181) 逐类别、逐分句、逐命中调用它。

v3 把前缀作用范围缩成“最近一次危险及直接并列命中”，但最近位置并不证明该命中是非现场表达。

**离线观察**：进程内临时替换豁免判断，按第 4 条模拟“标记后的最近命中及列出的直接并列命中”。保持现有危险模式与分类入口，执行后恢复原函数。这只是作用范围代理。

| 构造输入 | 当前分类 | 第 4 条代理 | 应有语义 |
| --- | --- | --- | --- |
| `What is causing smoke to come out of the socket` | 非紧急 | 非紧急 | 当前插座冒烟，按现场危险处理 |
| `What is causing this gas smell` | 非紧急 | 非紧急 | 当前燃气异味，按现场危险处理 |
| `Smoke is coming out of the socket` | 火灾类危险 | 火灾类危险 | 现场危险对照 |
| 如果着火导致漏电怎么办 | 非紧急 | 触电类危险 | 整个条件链是预案咨询 |
| 如果着火或者冒烟怎么办 | 非紧急 | 非紧急 | 纯假设并列对照 |

前两句只有一个危险命中，它恰好是 `what is` 后最近的命中，所以仍被豁免。第四句的“漏电”属于同一假设后果，但连接词“导致”不属于 Spec 的直接并列表，因而被当成现场危险。

**请修订**：

1. 通用 `what is/are` 不能独立授予非现场豁免；设备和政策咨询继续按其明确短语范围判断。
2. 假设与引用的范围必须有可执行的判定边界；“最近一次”只可作为定位辅助，不能充当语义证明。明确能识别的非现场范围才豁免，范围不可靠时保守处置，并披露支持上限。
3. 补入上述现场询问、完整条件链和纯咨询对照，保留 v3 已列的同句混合危险样例。不能只让当前几个样例通过就宣称任意句子结构已覆盖。
4. 同步更正 §2.1 第 61 行“假设、引用前缀保持不变”，使其与第 4 条实际修改范围一致。

**验收**：真实 `EmergencyService.classify` 分别断言危险与非紧急对照；真实 `ConversationService` 断言现场样例的最终安全正文、实际路线、模式和员工通知。代理结果不能替代这些验收。

### V5-R2 / P1：提交入站事实时同时保证处理可续接

**修改位置**：[泛用化 Spec §2.3 同步规划事务边界](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:165)、§3.1 风险、第四批符号与验收清单。

**源码依据**：

- [ConversationService.handle_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:775) 在 785–786 行发现入站编号已存在即返回。
- [MessageService.record_incoming](/Volumes/02/code/homestay-bot/src/homestay_bot/services/message_service.py:88) 和 [SQLAlchemyMessageRepository.exists](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/conversations.py:345) 依据消息行是否存在去重，不区分“已接收但尚未处理完”。
- [application_lifespan 内 handle_message](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4366) 使用独立业务会话；4421 行仅非 deferred 入口传入 `session.commit`，4431 行正常返回后提交。
- [_enqueue_debounce](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1184) 和 [_stage_fast_ack](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1136) 已有持久处理作业；[SQLAlchemyJobRepository.enqueue](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py:57) 提供作业去重，后台 [process_recorded_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:976) 可以处理已落库的消息。

**失败窗口**：按 v5 第 167 行先提交入站消息，随后规划等待被取消或进程中断。如果提交时没有续接作业，重新拉取同一消息会在去重出口返回，已接收的请求不再被处理。普通规划超时回退不覆盖取消、崩溃和重启。

**离线观察**：通过项目 `create_engine` 建立内存 SQLite，创建真实 ORM 表；使用真实会话/消息仓储、`MessageService` 和现有 `ConversationService`。助手替身在 `respond` 内可选提交当前业务会话，随后抛 `asyncio.CancelledError`；关闭该会话后，用新会话、新服务重放同一构造消息编号。

| 条件 | 重放助手调用 | 重放客人回复 | 最终消息行 | 持久作业 |
| --- | --- | --- | --- | --- |
| 取消前已提交 | 0 | 0 | 1 条入站 | 0 |
| 取消前未提交，对照 | 1 | 1 | 入站与机器人各 1 条 | 0 |

提交被放在助手替身中，用来隔离“入站提交后中断”的现有生命周期。探针没有实现新 planner/helper，也没有模拟真实供应商；结果证明当前去重与提前提交组合存在续接缺口，不能写成已观测到新版生产故障。

**建议最小方案**：复用现有持久作业，让入站与必要续接任务在同一事务提交，再由后台记录入口执行规划及后续处理。具体 phase、执行者和领取/恢复规则写进 Spec；如果保留同步执行，必须同样证明存在可恢复的处理记录，并避免同步与后台双重执行。来源编号及摘要绑定继续保留，结果返回后重锁复核。

已有 `wecom_sync` 等上游任务的重试不能自动解决本问题：回调重新进入 `handle_message` 后仍会遇到消息去重。消息存在去重仍应保留，防止已完成消息重放造成重复回复和任务。

**验收**：

1. 从真实即时入口触发软判定，提交后暂停规划，取消/重建服务；同一来源仍可由持久记录续接。
2. 重复拉取、重试、恢复后，任务、客诉及出站入队保持幂等；断言最终正文和实际任务/通知结果。
3. 规划暂停期间，新客人消息和员工接管能推进，过时结果不能产生副作用。行锁竞争由目标 PostgreSQL 专项验证，SQLite 证据不替代它。
4. 显式核对 `application_lifespan.handle_message` 的 deferred 装配，不能只在提交边界非空的测试替身中验证三个入口。

另一个实施核查点：现有 [_process_model_reply_body](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1401) 在主回复 `respond` 前没有提交。若新 helper 重锁后继续等待主回复，仍须说明该等待的事务边界；“规划期间不持锁”只证明规划这一段，不证明整个回复链。此项是既有调用链与新接线的核查条件，未另计为新增缺陷，也未作 PostgreSQL 动态竞争结论。

### V5-R3 / P2：绑定摘录的唯一出现位置

**修改位置**：[PlanItem.quote 契约](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:154)、[§2.5 同事项最终状态](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:245)、任务验证矩阵。

**契约反例**：输入 `请送毛巾，不用送毛巾了，还是请送毛巾`。

| 原文事项 | kind | subject | quote | 摘录可能的起始位置 |
| --- | --- | --- | --- | --- |
| 首次申请 | service_request | 毛巾 | 请送毛巾 | 0、14 |
| 撤回 | request_withdraw | 毛巾 | 不用送毛巾了 | 5 |
| 再次申请 | service_request | 毛巾 | 请送毛巾 | 0、14 |

三个摘录都是合法原文子串，两个申请的摘录完全相同。用 `text.find(quote)` 排序会得到两个申请在前、撤回在后，错误结论是不登记；客人的最终状态实际是重新申请。使用首次位置只是一个合法实现示例，**当前没有新 resolver，未声称其已经采用该算法**。

**请修订**：

1. 在规范化正文中绑定可校验的 `start/end` 或出现序号；验证对应切片与 quote 一致。字段方案择一即可。
2. 重复摘录无法唯一绑定时，不依据猜测决定任务状态，转确认；合并消息按同一冻结正文坐标核验。
3. 同事项关联可优先让撤回项选择本轮已有申请编号，或明确可验证的关联规则；`subject` 可用于展示，但自由名称相等不应是关联的全部依据。
4. 明确 §2.3“失效项作废”与 §2.5“失效撤回导致确认”的优先级，保留后者的保护，避免通用过滤先删除撤回项后误登记。

**验收**：先申请后撤回不登记；撤回后再申请登记；重复摘录和跨消息重复措辞得到正确最终状态，或明确要求确认；不同事项撤回只影响对应申请。断言真实任务写入及最终正文，不能只断言解析后的排序数组。

## 4. D14 设施兜底的建议与未决边界

[泛用化 Spec D14](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md:344) 尚待用户选择，本报告不代替决定。

建议选择：**规划无法确认时给安全提示并请客人确认，再登记任务**。这符合本次减少否定、历史、已修好误登记的目标；代价是真实设施请求在规划失败时多一轮确认，应明确披露。

需要把两种失败分开：

- 规划失败或没有计划：按最终确认的 D14 处理。
- 规划已成功、主回复随后失败：保留已经核验的 `PlanOutcome`，继续使用统一任务判定。计划已认定非当前故障时，主回复失败不能使旧词面重新授权建任务。

当前 [AssistantUnavailableError 分支](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1421) 会以 `decision=None` 调用设施处理；v5 第 309、344 行又把“规划失败”与“助手不可用”合并描述。请把可用计划的传递和异常保留条件写清楚，使第三批“规划成功时历史、否定不建任务”的承诺与失败分支一致。该事项列为取舍及合同澄清，不重复计为第四项发现。

## 5. 建议 Claude 返回的修订内容

1. 逐项回应 V5-R1–R3：接受或不接受、依据、修改后的章节/字段/符号、必要验收目标。
2. 紧急 Spec 给出豁免范围的可执行规则及支持上限，补齐现场询问、条件链和原有混合危险对照。
3. 泛用化 Spec 补齐同事务续接、执行者、三个入口的装配、取消/恢复及重复执行边界；重复摘录选择一个最小可校验的位置契约。
4. 将 D14 保持为待决事项，清楚区分规划结果失败与主回复失败，写明对第三批承诺的影响。
5. 更新各批文件/符号与验收清单，确保新增边界在该批已具备，不借后续批次的能力证明当前批安全。

本轮没有新增业务需求；无需顺带扩展已有任务取消、真实订房授权、语义检索开关或后台界面。修订后仍按项目 Spec 确认与“开始”门禁进入编码。

## 6. 验证、归属与禁止后续动作

- 已完成：源码调用链复核、紧急范围代理、真实内存 SQLite 提交/取消/重放对照、重复摘录反例。
- 文档整理：复用有效离线证据；只需静态检查和受影响认知维护，不跑业务全量或真实模型门禁。
- 未覆盖：新 planner/helper/resolver 实现、PostgreSQL 竞争、真实模型泛化率与延迟、外部工具契约、生产状态、真实任务/订单、客人实际收件。
- 本次新增：本报告；托管维护涉及其 AOCI 条目与 baseline。既有两份 Spec、其他交接报告、`.impeccable/critique/` 和之前的 AOCI 改动不归为本次创作。
- 业务源码、测试、场景预期和回归基线未修改。历史报告保持原样，不能按当前 Spec 行号重新解释其旧结论。
- 不读取、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。
- 不执行提交、推送、部署、生产写入、真实模型/Hostex/企业微信调用或向其他会话发送消息；本报告是可供用户交接的本地文件。

## 7. 审查快照（SHA-256）

这些摘要绑定本报告所审查的文件版本，不证明实施、部署或运行健康。

| 文件 | SHA-256 |
| --- | --- |
| `docs/specs/2026-10-06_reply-generalization-spec.md` | `5ba2a1f0f32b554d8b8fd78b3af27c06c375085532748dcdcbec239012951618` |
| `docs/specs/2026-10-07_emergency-exemption-scope-spec.md` | `dd6b6c13dc17617d8125d857cf7ebf31b1a5104d7b0fa31ea0856a6e5e4f7439` |
| `docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v5-review-handoff.md` | `6cc650536739ee51e8f47e92be5647fc116ad9a774c9fef7761edbe3224b8b24` |
| `src/homestay_bot/services/conversation_service.py` | `de2b6181756452dbf0ac354dff23285261d12d2e6b25066377ea46a8be335254` |
| `src/homestay_bot/services/emergency_service.py` | `b8fecd9e78f3666ac57ce63457108de7c42a8fdad186aada672efab29ba8a213` |
| `src/homestay_bot/services/message_service.py` | `68f186b8a8983aaf512232c036df9707cd474958ccb58fbd3b9a25f84dcd6443` |
| `src/homestay_bot/application.py` | `270ab4a44183febde5616965677cfad0abb23b7db4ec3da3c52ef8632585abde` |
| `src/homestay_bot/repositories/conversations.py` | `e5c6dee2b19df73b561a539506e5c3c58583f1df01c46793a46467c8a0cbfef4` |
| `src/homestay_bot/repositories/jobs.py` | `dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33` |
| `src/homestay_bot/worker.py` | `95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257` |
| `src/homestay_bot/db.py` | `4b1af8100173fac02d841dbb5fb0f669688344ddeb60d9b80aa057b2e2da9909` |
| `src/homestay_bot/domain/models.py` | `f906c9297f9fa71ff2c25dfdd1afad2ba32b08eaa8f5e3198894595322f1e27b` |
| `tests/unit/test_conversation_service.py` | `0eab2f50c52cf2dd0930f98edd8789e695fd14aa895d471c831ed2b8c4a87f1d` |
