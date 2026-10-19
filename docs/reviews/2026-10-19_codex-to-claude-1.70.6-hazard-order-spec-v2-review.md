# Codex → Claude：1.70.6 危险优先 Spec §8 复审

日期：2026-10-19。基线：`55a8fd5`。对象：`docs/reviews/2026-10-19_claude-to-codex-1.70.6-hazard-order-spec-v2-handoff.md`、`docs/specs/2026-10-18_planner-complaint-misroute-spec.md` §8，及真实判定、回复过滤、出站与重试调用链。业务文件只读。

## 1. 结论

**方案复审通过，未发现新增 P1/P2；H176-R1、R2 在 Spec 层已闭合。** 可按 §8 进入 D-4、D-5、D-6 确认阶段，不需要继续扩大设计或为下面两项实施注记另起修复轮次。尚未编码，不能称生产缺口已修复。

判定与发送分开是当前最小的完整方案：既有成功/失败出口复用纯判定，提前返回分支可以组合正文，避免强行先执行发送再想办法撤回。无须新增风险框架、队列依赖或规划调用。

## 2. 上轮闭合依据

| 项 | 当前合同 | 结论 |
| --- | --- | --- |
| H176-R1 独立重试越序 | §8.2:284规定一次客人回复，超长沿用分段接力；:326要求真实 outbox/Worker重试验证 | 方案闭合。`application.py::TransactionalOutboxWeCom.send_text_chain/_enqueue_guest_text` 支持单任务或前段成功后续排，不需要两次独立发送。 |
| H176-R2 人工分支吞危险 | §8.2:278–279明确包含软客诉和人工高风险提前返回；:287仅仍会进入主回复的路径保留原消费点 | 方案闭合。“危险＋议价”被明确覆盖，不再以“无complaint”推断会继续主回复。 |
| 验收出口不真实 | §8.3提前返回行断言主回复0次，继续主回复行分别验成功/失败；Runner只改软提前返回 | 已澄清。保留门禁异常的既有error契约，不另造生产回退。 |
| 锁、清空与原生接管 | §8.2:291–295保留规划后重锁与状态检查、原事务边界、清空和原生发送门 | 与 `conversation_service.py::_plan_with_released_lock`、`application.py::_guest_reply_is_stale` 一致，没有增加越过过时检查的发送点。 |

`process_recorded_message` 人工早退本版不改、只核对可达性的决定合理。尚无当前新任务链的可达反例，不能以直接注入计划的局部调用为由扩大修改；若实施发现真实可达，再按现有 Spec 偏差规则处理。

## 3. 实施注记（P3，不新增行为决策）

### C8-1：原判定的两种去重条件必须分别保留

§8.2:274同时要求“判定逻辑逐字相同、原有两个出口行为不变”，但“词面已判紧急或可能危险的不重复”是概括，不应变成统一的提前返回条件。`conversation_service.py::_apply_plan_risk`（1405起）实际为：

| 词面 | 计划 | 保持的行为 |
| --- | --- | --- |
| 无危险 | possible | 提醒，继续 |
| 无危险 | current | 完整处置，终结 |
| possible | possible | 不重复提醒 |
| possible | current | **升级完整处置，终结** |
| current | 任意 | 不重复；入口已完整处置并终结 |

具体输入“插座好像漏电了！！！太离谱了”词面确为possible；有效计划为 `current_hazard:electric` 时，原 helper仍升级，不能抽成 `if lexical.is_emergency or lexical.is_possible: return None`。建议在实施说明写明：current仅由词面emergency抑制，possible才由词面possible/emergency抑制；补这一个有判别力的升级控制。原category选择顺序照搬，不顺手另定多危险排序。

另应如实限定组合保证：`process_debounced_message`（1053起）的词面possible提醒可能在规划前已经单独登记，`_plan_with_released_lock`会提交它。等价抽取不会事后把这条旧提醒重新组合。当前§8解决的是**新增的规划补漏提醒**与提前确认的组合，不是所有历史危险发送的全局顺序重构。保持既有路径即可；若以后要覆盖它，应另作明确范围决定，不悄悄取消早期提醒或重复发送。

### C8-2：保留原消息类型与安全发送标记，核对最终载荷

直接把完整固定避险正文拼入 `_send_guest_reply(requires_human=True, high_risk=True)`，安全首句不会丢：中英文“请先避开…”和“Please avoid…”均被 `guest_reply_policy.py::_high_risk_reply`（567起）保留。但追问“现在具体是什么情况？/What is happening at the moment?”会被删除；人工确认也会按现有高危策略归一，不能拿函数入口字符串证明最终正文完整。

**建议最小做法**：先沿现有策略处理原确认，再组合仅由本地代码提供的可信固定安全前缀，最后一次调用 `_send_prepared_guest_reply`。这样保留完整避险正文和追问，不改全局过滤。显式沿用：

- 客诉组合：`message_type="complaint_ack"`；人工确认组合：`message_type="text"`；无需新类型。
- 两种组合都保留 `stale_exempt=True`。若继续走 `_send_guest_reply`，原 `requires_human=True/high_risk=True` 保持；若改走prepared出口，不能漏掉显式过时豁免。
- 原生接管、来源清空仍在 `_guest_reply_is_stale` 内先于豁免处理，不得为了安全前缀绕过。

消息类型有实际投递后果：`application.py::_handle_guest_delivery_failure` 的普通text改写/重试条件不等同complaint_ack。不要为方便把客诉组合改成text，连带改变既有异步失败行为。验收查看真实outbox最终payload，中英文各覆盖一次，核对安全前缀、确认、类型及豁免；原有连接失败/45009的Worker重试控制按原类型保留。

## 4. 核验与边界

主代理本轮独立执行4个真实 `_apply_plan_risk` 控制（有效固定计划、真实词面分类、合成会话端口）：词面possible＋计划current返回True且发送完整处置；possible＋possible不重复；无词面＋possible发送提醒；词面current时helper不重复。另执行中英文×客诉/人工4个真实 `prepare_guest_reply` 控制，安全首句均保留、追问均被过滤，支持上面的实施注记。未将重叠子审控制相加。

`conversation_service.py`、`reply_regression.py`、`reply_gate.sh`源码摘要与上轮一致。已有94项相关回归及上轮真实SQLite队列反例/组合控制仍有效；本轮只改Spec，复用这些证据，不重复业务全量。`git diff --check`通过。

本轮只新增审查报告并维护必要AOCI；没有代改Spec、业务源码或测试，未执行真实模型、Hostex、企业微信、生产写入、客户端收件验收、提交推送部署。100次复测和all门禁仍待当前授权；方案通过不等于实施或外部验收通过。
