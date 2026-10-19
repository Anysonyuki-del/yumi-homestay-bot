# Codex → Claude：1.70.6 实施与发布复审

- 日期：2026-10-19。
- 对象：`2026-10-19_claude-to-codex-1.70.6-release-handoff.md`、Spec §8/§9、正式提交 `60de4da` 与后续发布记录；当前 HEAD `8bc1113421cfa1fc100f95a046f1d521b24e78e7`。
- 本轮只读审查业务代码，仅新增本报告及必要 AOCI 资产；未修复、提交、推送、部署或调用真实模型、Hostex、企业微信。

## 1. 结论

**发现一项新增 P2（RV176-1），不建议把当前实现认定为全部审查通过。** 1.70.6 已发布；建议修订 Spec §9 的归一条件后做小范围补修，不撤回已经正确实现的危险优先与组合发送。

F1 规划摘要与危险顺序实施在本轮检查范围内通过。上轮 C8-1/C8-2 已落实，未发现新的事务、去重或状态变化回归。新增的 `kind=complaint → unclear` 归一方向可以保留，但现有条件过宽，不能概括为“投诉信号都保住”。

## 2. RV176-1 / P2：归一会把唯一投诉信号清成无风险

### 依据与反例

`src/homestay_bot/services/turn_plan.py::verify_turn_plan`（199—204 行）无条件把 `kind=complaint` 改为 `unclear`；随后 `_verify_risk`（129—140 行）把缺失或 null 风险解释为 `none`，显式 `none` 也合法。

因此，下列模型输出从旧版的 `failed/schema` 变成成功、有效的无投诉计划：

```json
{"items":[
  {"id":1,"kind":"facility_fault","quote":"下水又堵了","start":0,"risk":"none"},
  {"id":2,"kind":"complaint","quote":"你们怎么搞的","start":8,"risk":"none"}
]}
```

客人正文为「下水又堵了！！！你们怎么搞的」。第二项风险改为缺失或 null，同样成立。`answer_policy.py::agitated_cleared_by_plan`（353—359 行）只检查风险集合，因而放行；`resolve_handoff_reason` 不再保留 `agitated`。`conversation_service.py::process_debounced_message`（1061—1080 行）跳过客诉，继续排最终回复。

本轮主代理用真实核验函数和真实会话编排、合成端口复核：

| 第二项原始风险 | 计划结果 | 软客诉放行 | 会话模式 | 最终任务 | 员工通知 |
| --- | --- | --- | --- | --- | --- |
| `none` | ok | 是 | BOT_ACTIVE | 1 | 0 |
| 缺失 | ok | 是 | BOT_ACTIVE | 1 | 0 |
| null | ok | 是 | BOT_ACTIVE | 1 | 0 |
| `complaint`（正向控制） | ok | 否 | HUMAN_ACTIVE | 0 | 1 |
| 原结构失败回退（对照） | failed | 否 | HUMAN_ACTIVE | 0 | 1 |

独立子审还经 `handle_message → process_debounced_message → process_recorded_message` 跑完最终阶段：前三种均调用主回复、登记普通维修任务，没有客诉通知；两个对照均保持客诉路径。模型的结构误写因此改变了业务安全边界，不只是少一个标签。

这是本次新增放宽造成的可达回归；没有证据证明它已经在线上发生，也不能由合成反例估算发生率。现有 `tests/unit/test_turn_plan.py::test_complaint_written_as_kind_is_kept_as_unclear_item`（298—323 行）只覆盖风险已经正确写成 `complaint`，不能证明另外三种情况安全。

### 最小修复建议与验收

1. **推荐先收窄条件**：只有原始 `risk` 明确等于 `complaint` 时，才把 `kind=complaint` 归为 `unclear`；其他组合仍按 `schema` 失败。这样仍保留已覆盖的「独立危险项＋误写投诉项」成功路径，也保留含糊结构的原有保守回退。不要扩大未知类型白名单。
2. 放在 `verify_turn_plan` 这一模型输入核验边界合理。这里已经负责枚举、风险、摘录和撤回关系，不必另造解析层，也不要在各调用方分别补丁。
3. 如果希望连 `risk=none/缺失/null` 也接收，必须先明确如何保留 `kind` 自带的投诉含义，并处理与危险风险并存的取舍；不能继续把它直接解释成无风险。这比上述条件收窄范围更大，本轮不推荐。
4. 修订 Spec §9 的接受条件；补三个风险变体的参数化回归，以及真实会话的投诉分流断言。保留现有「危险项＋kind/risk 都为 complaint 的项」成功控制，验证其他未知类型和错误风险仍失败。覆盖机器人、人工模式，确认没有新增普通任务或静默放行。修复测试须在当前有缺陷实现上失败。

## 3. 本轮认可的实施与连带影响

- **危险去重**：`ConversationService._plan_hazard` 分别保留当前危险、可能危险的原去重条件；词面 possible 不会阻止计划 current 升级。`_apply_plan_risk` 的原成功/失败消费出口仍在。
- **组合发送**：`_send_combined_safety_reply` 先用原策略处理确认语，再拼本地固定安全正文，完整保留中英文追问；`_enter_complaint_mode` 保留 `complaint_ack`，人工确认保留 `text`，都显式 `stale_exempt=True`。没有全局放宽高危过滤或新消息类型。
- **出站和失败**：`TransactionalOutboxWeCom` 与 Worker 的相关回归通过，组合任务重试时没有独立确认先行；超长仍复用原分段接力。异步失败继续按原消息类型处理，未把客诉确认改成普通文字以扩大改写行为。没有据此声称所有发送路径均全局有序。
- **锁与过时**：`_plan_with_released_lock` 仍在规划后取锁检查模式、清空、原生接入和新活动；`_early_plan_hazard` 再核对计划来源。独立子审对两个分支、两种危险及四类状态变化共 16 个控制全部通过，没有新的回复、通知或最终任务。未增加首条发送前的额外提交。
- **F1 观测**：`reply_regression.py::plan_summary/_Runner.run/_Runner._respond` 保留局部观测、异常计划和未采集/未调用区别；自由文本只留长度。门禁仅同步约定的提前客诉分支，不额外改变主回复异常回退。

## 4. 发布与验证证据

- 本轮相关离线回归 **116 passed**：主代理 `test_turn_plan.py`、`test_reply_regression.py`、`test_release_scripts.py` 为 98 项；独立子审对 `test_hazard_combined_reply.py` 和 `test_conversation_plan_flows.py` 选择危险相关项为 18 项、23 项未选。两组无重叠，未复跑无关全量。
- 上述五项反例/对照及 16 项状态变化控制为额外行为探针，不加入 pytest 数量。外部模型和发送端口均为合成替身。
- `git diff --quiet rc-1.70.6-1 v1.70.6 -- src tests scripts migrations pyproject.toml requirements.lock` 返回 0，支持正式业务代码复用该候选门禁；不证明门禁本身覆盖 RV176-1。
- 本地保存的最终规划复测逐行核对：100 条、100 条 `ok=true`，分组为报修 60、投诉 25、否定 5、危险 10，提示词哈希均为 `0ab9e27034768cec`。它们是已有真实调用的记录，不是本轮重测；摘要未保存模型原始误写，无法估算本发现的频率。
- GitHub 当前查询确认正式提交的两次 CI 均成功：[运行一](https://github.com/Anysonyuki-del/yumi-homestay-bot/actions/runs/39185964077)、[运行二](https://github.com/Anysonyuki-del/yumi-homestay-bot/actions/runs/39185963491)。本轮只核对运行状态，没有重新声称其全部子项数量。
- 三处源码一致、备份、诊断页和客户端收件是发布报告记载的现场证据；本轮没有重新登录生产或复验实际收件。受理与实际收件仍分开解释。

源码绑定（SHA-256）：`turn_plan.py=3e2be55d8fbb52fd0745c8f86e52ec2a22cfcbb6f0aa71450a367f91fcd9c651`；`conversation_service.py=5b7066fd3e516654579212a29e1285f986be6b736e76b9d9c88b1847c6a829ef`；`reply_regression.py=553abfe1bd3e544c3640b679d6ce598a1eb6a5641846f877c3bc10e62330542e`。

## 5. 未做事项及建议顺序

1. **先处理 RV176-1**：收窄一处核验条件，补缺失风险边界与会话回归；不要为本发现重写危险流程、词表或规划器。修复后的真实门禁按项目公共规划路径规则执行，仍须当前授权。
2. **否定式投诉**：继续单独管理。后续沿 `ComplaintService.classify/classify_all → resolve_handoff_reason` 的硬理由边界制定 Spec，同时覆盖纯否定与否定后转折真实投诉；不能用删除“投诉”关键词的方式消除误报。本轮未对该另立事项补做实现验收。
3. **剩余结构失败与概率边界**：保留 A/B 中危险句 1/20 失败记录及已撤销的早期归因；没有逐次原始字段证据时不把后续 30 次成功当成原因已解。后续如专门诊断，可记录不含原文的失败字段路径和错误类别，避免为查原因摄入客户正文。本轮不额外新增遥测或真实调用。
4. `process_recorded_message` 早退和词面早期提醒仍按 §8 明确范围保留；不能把本版组合回复的保证扩展到这些路径。没有新可达反例，不要求本次顺带重构。

审查结论：危险顺序、组合发送与 F1 在本轮范围内通过；核验归一需按 RV176-1 补修。未修改业务代码，未执行任何发布或生产写入。
