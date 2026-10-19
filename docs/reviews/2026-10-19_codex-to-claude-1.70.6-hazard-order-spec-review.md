# Codex → Claude：1.70.6 危险优先顺序 Spec 复审

日期：2026-10-19。基线：`55a8fd5`。审查对象：`docs/reviews/2026-10-19_claude-to-codex-1.70.6-hazard-order-spec-handoff.md`、`docs/specs/2026-10-18_planner-complaint-misroute-spec.md` §7，以及对应生产、门禁、出站和重试链。业务文件只读。

## 1. 结论

**方案方向可采用，但编码前应补齐以下两项 P2。** 上轮的预算、否定式投诉另立事项及末尾空行均已处理；危险优先不能只靠“先调用一个发送函数、再调用另一个”保证。人工接待高风险确认分支也已确认属于同类，不能继续留作实施时再决定。

建议在本 Spec 内一次定清分支、出站和验证边界，再确认 D-4、D-5、D-6。无需另建通用风险框架、通用队列依赖机制或新增规划调用。F1 观测代码结论不变，F3及本次生产顺序修复仍未实施。

## 2. H176-R1 / P2：独立出站的重试会打乱“先避险、再客诉”

**依据**：Spec §7.2:213、§7.3:233要求先提醒再客诉。`src/homestay_bot/services/conversation_service.py::_apply_plan_risk/_answer_possible_danger` 会登记安全正文和通知；随后 `_enter_complaint_mode` 另登记客诉确认。`src/homestay_bot/application.py::TransactionalOutboxWeCom._enqueue_guest_text`（724起）把每次发送登记为独立任务；只有同一 `send_text_chain` 的后续段才在前段成功后排入。

当前单API装配只有一个非模型 worker 处理发送（`application.py::application_lifespan`，5206起），正常领取有序，不能凭空假设两个发送worker。问题在失败后：真实发送将连接未建立及45009限流转为 `RetrySafeJobError`（`build_worker_bindings.send_guest`，3249–3252）；`worker.py::Worker.run_once` 调用 `repositories/jobs.py::SQLAlchemyJobRepository.mark_failed` 延后首条。`claim_next` 只按到期时间及编号取任务，不存在两次独立发送的前后依赖。

**复现**：临时内存SQLite、真实 outbox、真实 Worker，先排 `SAFETY` 再排 `COMPLAINT`，仅替换外部发送处理器，使首条抛可重试错误。顺序执行两次 worker：安全任务为 `pending`，客诉任务已 `completed`，模拟受理列表为 `["COMPLAINT"]`。因此只看 WeComStub 的调用顺序会漏掉这个失败路径；未证明生产实际发生过。

**最小修订**：在“可能危险＋客诉”这一条提前返回路径，将固定避险正文和既有客诉确认组合成**一次客人回复**；超长时复用已有分段接力链。危险和客诉的员工通知仍分别保留。复用现有危险分类与固定文案、客诉建档和接管逻辑，但不要强制先调用带发送副作用的 `_apply_plan_risk` 再独立发送客诉，导致无法组合或重复提醒。当前危险继续终结本轮，无投诉正常路线维持原消费点。

**验收**：用真实 outbox/Worker、临时SQLite验证首条可重试失败时不会单独发出客诉确认；补组合正文正常出口、只提醒一次、人工模式、两类员工通知及客诉作业记录。沿用清空/原生接管优先与安全正文过时豁免，不为本次改动放开原生发送门。

## 3. H176-R2 / P2：不能以“没有 complaint”决定不提前消费危险

**依据与可达输入**：Spec §7.2:210说凡基于计划提前返回先处置，:214又说“规划放行、没有投诉”维持后续消费，文件列表:222仍把人工分支留待决定。这两个条件并不等价。

“插座一直滋滋响还发烫！！！可以便宜点吗”可有两个通过真实核验的有效项：故障项为 `possible_hazard` 或 `current_hazard:electric`，议价项为 `static_fact/none`。没有 complaint，`agitated_cleared_by_plan=True`，但 `answer_policy.py::resolve_handoff_reason` 仍返回硬理由 `price`。

真实 `ConversationService.handle_message` 会先因软情绪排合并作业；`process_debounced_message` 完成同一份规划后：

| 会话初始模式 | 两种危险计划的当前结果 |
| --- | --- |
| BOT_ACTIVE | 排最终作业，最终链路有对应避险/电气处置 |
| HUMAN_ACTIVE、没有原生接入 | 1084起进入通用高风险确认并返回；主回复0次、最终作业0个、只有通用确认与1条通知，没有安全处置 |

这四个控制经过真实即时、合并和必要的最终处理入口，模型及持久化端口使用合成替身，规划仅1次。它证明人工分支必须同改；不是本轮新引入或生产发生率证据。

**最小修订**：把:214改为“**只有仍会继续进入主回复的放行分支，才保留原危险消费点**”。明确将 `process_debounced_message` 的人工高风险提前返回纳入 D-6：有同源有效危险计划就先处置，当前危险终结；可能危险与原人工确认按同样出站约束组合，并保留各自必要通知。硬理由的识别和无计划时的既有分流不变，不为它们额外规划；不能把“不改硬理由”解释为丢弃已经核验的危险。

**锁与失败合同**：沿用 `_plan_with_released_lock`（1429起）提交后规划、重新取活动锁并复核模式/清空/原生接入/更新活动的结果；返回None即丢弃。核验通过后到登记出站与接管状态间保持现有锁及事务边界，不能为发首响额外提前提交。补人工模式下这两个危险控制，以及规划期间清空、原生接入或新活动时零新副作用的断言。

`process_recorded_message`（1123起）的人工高风险早退也应在调用方审查中核对；直接注入计划调用可见同样结构，但目前未证明当前新任务链中，在没有过时/原生接入变化时该组合如何到达，**不把它另列已证实P2，也不据此扩大修复**。若实施确需改该点，应先复用 `_discard_stale_final` 锁内检查，不能在其现有过时检查之前直接发送危险正文。

## 4. 验收范围澄清（P3）及闭合项

- §7.3不要机械地把所有行乘“主回复成功/失败”：有 complaint 的三行应在主回复前结束，断言 `respond` 调用0次；只有实际继续主回复的无 complaint 行才验证其成功与失败出口。补本报告的人工硬理由提前返回控制与出站重试控制即可。
- `_Runner` 的“同样组合”明确限定本次提前返回改动。当前门禁主回复异常是 `route=error`、空正文；不要未经决策改成另一套生产回退。独立8次门禁控制显示：possible计划＋明确facility决定有条件停用提示，合法faq决定则没有，异常为空；不能泛称门禁完全没有避险，也不能拿这个既有近似差异要求本版重写Runner。生产完整安全行为由真实会话链控制证明。
- §7.4已能复算100次（20+40+25+5+10），D-5明确且all另计；样本数量本身不证明泛化，只据逐次记录及失败保留判定。无需再扩大真实样本。
- 否定式“投诉”已在 `tasks/todo.md` 另立，范围与上轮建议一致；当前 `git diff --check` 通过，末尾空行闭合。
- 员工通知合同区分：可能危险继续客诉/人工确认时保留两类必要通知；当前危险已终结时按紧急接管合同，不再暗示还会额外进入通用客诉流程。

## 5. 证据与未做范围

新增可复跑脚本：`docs/reviews/2026-10-18-planner-complaint-evidence/hazard_order_spec_probe.py`。

```sh
PYTHONPATH=src:.:tests .venv/bin/python docs/reviews/2026-10-18-planner-complaint-evidence/hazard_order_spec_probe.py
.venv/bin/ruff check docs/reviews/2026-10-18-planner-complaint-evidence/hazard_order_spec_probe.py
git diff --check
```

四个完整会话控制、独立出站重试反例及组合正文控制均成立，脚本退出0、Ruff通过。退出0表示缺口/机制控制得到证实，不是Spec实施通过；组合控制只证明不能越过首条，不证明真实企业微信收件。

本轮Spec与测试末尾空行不改变业务行为；`reply_regression.py`和`reply_gate.sh`源码摘要与上轮一致，复用上轮94项相关测试，不重复全量。另有独立子审的8次门禁合成控制，不与94相加。

只新增本报告及合成证据，按需维护AOCI；未修改业务源码、Spec和已有测试，未调用真实模型/Hostex/企业微信，未查生产或验证客户端，未提交、推送、部署。D-4、D-5、D-6仍需用户确认，本报告不是编码或外部操作授权。
