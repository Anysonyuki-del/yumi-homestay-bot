# Codex → Claude：回复判定泛用化 Spec 审查交接

- 日期：2026-10-07（Asia/Shanghai）。
- 审查对象：[2026-10-06_reply-generalization-spec.md](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md)，草案，三段均待确认，尚未实施。
- 工作区：`/Volumes/02/code/homestay-bot`，分支 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 本轮交付：审查与本报告；未修改业务源码、场景预期或回归基线。报告建议不构成实施、提交、推送、部署或真实外部调用授权。

版本说明（2026-10-07）：本报告保留 v1 草案的审查记录；Claude 已修订为 v2，对 v2 的进一步审查与方案对比见 [泛用性方案对比交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_codex-to-claude-reply-generalization-comparison-handoff.md)。R3 复现脚本的场景字段已从误写的 `code` 更正为夹具实际使用的 `id`，两例假通过结论经离线复核保持不变。

## 结论与接手目标

可以保留“语义理解加确定性边界”和独立改写/反例验收的方向，但当前草案不宜直接实施。需要先修订五项问题：危险降级会削弱客人处置；知识编号合法不足以证明事实适用；门禁没有核验实际动作且存在假通过；部分住宿场景同时有召回或证据冲突；服务登记与人工接管的目标尚未分清。

Claude 下一步先修订现有 Spec 的现状分类、功能契约、风险与决策，逐项回应 R1–R5；按项目规则分段确认，完整 Spec 获得确认且用户明确回复“开始”后才编码。不要把本报告中的建议直接当作已批准的业务取舍。

## 五项发现

### R1 · P1：确定危险降为可能危险，会丢掉关键处置

**证据类型：当前源码事实 + 未实施方案的风险推演。** Spec §2.4、§3.1 允许模型把关键词命中的确定危险降为咨询，并转入 `_answer_possible_danger`；“仍有安全提醒和员工通知”不能证明安全处置保持不变。

[conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:2077) 的 `ConversationService._answer_possible_danger` 只回复“请先避开可能有危险的位置。现在具体是什么情况？”并通知员工，不发撤离模板、不自动接管。`_escalate_emergency` 则调用 `EmergencyService.safety_reply` 给出分类处置，再切人工。二者并非只差会话模式；误降真实火情或燃气泄漏会失去对应的撤离、停止危险操作、报警等关键指导。

本轮离线调用 [emergency_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/emergency_service.py) 的 `EmergencyService.classify`，以下咨询与真实事故都命中危险：

| 输入 | 当前结果 |
| --- | --- |
| 厨房是燃气灶还是电磁炉？ | 确定危险，gas |
| 燃气灶在哪里，刚才闻到燃气味 | 确定危险，gas |
| Can I smoke on the balcony? | 确定危险，fire |
| Smoke is coming from the socket. Can you help? | 确定危险，fire |

**修订要求：**明确现场危险的确定性处置下限、混合句和不确定判断的失败路径。咨询误报可以单独设计受约束的排除规则；明确现场危险不能默认由模型降级。D3 需要把客人处置变化一并交给用户判断，不能只表述成“不再切人工”。

### R2 · P1：合法编号与审核原文，不能证明回答适用

**证据类型：当前源码事实 + 方案契约缺口。** Spec §2.3 让模型按子问题选择知识编号，§3.1 把选错描述成“最坏答非所问但不编造”。审核原文可能属于不同房间、日期、条件或相互冲突的政策；错误使用仍会向客人提供错误事实。

[knowledge_evidence_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:708) 的 `_build_evidence_plan_for_period` 当前会在属性对应的 `covering_items` 上检查免费/收费矛盾、时间集合与金额集合冲突（约 796–814 行），冲突时不选答案。此处依赖主题、属性与候选分组。取消属性正则的拒绝权后，必须明确这些边界由什么接住。

夹具 `K-洗衣-X` 同时包含 9017“免费”和 9018“每次 10 元”。如果新选择器只挑一条，再对选中条目检查冲突，两个编号都合法也会漏掉另一条矛盾政策。这是对方案的反例推演，尚未运行新选择器或证明其实际泄漏。

**修订要求：**写清“每个子问题 → 相关合法候选 → 条件与冲突判断 → 绑定来源 → 最终正文”的契约；冲突检查不能只覆盖模型选择后的子集。定义部分覆盖、多问、限定条件、否定语义、模型漏选/错选与超时后的结果。期间特殊政策如何覆盖常规政策须有明确业务规则；不能由模型自行二选一。优先复用 [reply_plan.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/reply_plan.py) 的 `ReplyPart`、`ReplyEvidence`。

### R3 · P1：门禁先补动作判别力，再调整预期

**证据类型：当前源码事实 + 已完成的离线反例。** [reply_regression.py](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py:543) 的 `_Runner._respond` 在设施分支直接调用 `prepare_facility_advice_reply`，没有经过实际任务登记和通知入队，因而会使用默认成功收尾。“我已提交管家人工处理”在该运行器里不是动作成功的证据。

真实 [conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1103) 的 `_handle_facility_issue` 先调用 `_record_task_suggestion`；只有实际 `action_result.notification_queued` 为真才使用默认成功收尾，失败使用实际结果文案。

`reply_regression.judge` 目前只核对路由、必含/禁用片段及正则。以当前夹具输入以下合成 record，本轮离线结果均为 `ok=True`：

| 场景 | 合成 record | 漏检 |
| --- | --- | --- |
| F-空调 | `route="facility", final=""` | 空正文也通过 |
| SR-毛巾 | `route="handoff", final="收到，已登记。", action_result={registered:false, notification_queued:false}` | 动作失败仍通过 |

复现入口如下，只读取虚构夹具，不调用模型或业务接口；输出 `True` 表示旧判据存在缺口，不表示产品正确：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path
from homestay_bot.tools.reply_regression import judge

fixture = json.loads(Path("tests/fixtures/guest_reply_scenarios.json").read_text())
scenarios = {item["id"]: item for item in fixture["scenarios"]}
records = {
    "F-空调": {"route": "facility", "final": ""},
    "SR-毛巾": {
        "route": "handoff", "final": "收到，已登记。",
        "action_result": {"registered": False, "notification_queued": False},
    },
}
for code, record in records.items():
    print(code, judge(scenarios[code], record, fixture["global_must_not_include"]))
PY
```

**修订要求：**把登记成功、登记失败、通知失败的最终客人正文和实际动作结果纳入有判别力的验证。真实服务加临时 SQLite 的集成测试证明登记与通知契约；模型门禁证明模型决策/正文，二者分别报告。F-空调只能在动作成功证据成立时允许成功收尾，不能全局删除“已提交”禁用来抹平问题。

六项纯路由误判的调整有当前历史记录依据：`U-gym-EN`、`U-保险箱`、`U-健身房`、`U-充电桩`、`U-麻将`、`K-洗衣-X` 的记录均为 `unconfirmed`，文本判据无 missing/forbidden，场景要求 `knowledge_gap=true`。CH-谢谢与 UN-代码的文本可接受、路由要求是否调整仍属 D2 决策。其他 D 类不能因此一并宣称修完。

### R4 · P2：补住宿上下文后，仍有召回和政策冲突

**证据类型：当前源码事实 + 已完成的离线检索/证据计划探针。** `_Runner._respond` 没有传入 `expect.context.confirmed_stay` 属实，但 Spec 把 `SC-国庆早餐`、`SC-401有上下文` 全归为门禁自身，漏掉了业务缺口。A 类的 `SC-401无上下文` 也不满足“正确条目已召回”的概括。

本轮用夹具知识、固定夹具日期及以下显式目标范围调用 `KnowledgeService.retrieve_detailed`，再走 `DeepSeekGuestAssistant._scope_knowledge` / `_static_evidence_plan`：

| 场景与查询条件 | 离线结果 | 分类含义 |
| --- | --- | --- |
| SC-国庆早餐：房源 202；入住 2026-10-03、退房 10-05；知识查询到 10-04 | 常规早餐 9011（7:30–9:30）与国庆政策 9046（8:00–10:00）同时合法；计划仍为 `insufficient/partial` | 上下文之外，还需明确期间特殊政策优先级 |
| SC-401有上下文：房源 401；入住 2026-09-28、退房 09-30；查询到 09-29 | 默认 limit=8 返回 `[9025,9020,9015,9037,9031,9053,9043,9033]`，缺少木梯提醒 9045；limit=30 才包含 9045 | 有召回排序/数量边界；只加后置选择器拿不到缺失条目 |
| SC-401无上下文：“401适合带5岁小孩住吗？”；property_id=None | 9045 在评分前被房间范围过滤，未召回；显式传 property_id=401 后可召回，但计划仍 `insufficient/partial` | 同时涉及问题目标解析、房间知识召回与证据覆盖 |

依据：[knowledge_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:661) 的 `KnowledgeService.retrieve_detailed` 先按范围、房间、日期过滤，再评分并受数量/预算约束；房间知识要求传入匹配的 `property_id`。

**修订要求：**重新拆分这些场景的根因与验收条件。问题中明确提到的房间可以作为查询目标的设计输入，但不能顺便认定客人实际住在那里，也不能解锁住宿确认或凭证。limit=30 只是定位召回缺口的探针结果，不是已批准的全局扩大方案。

### R5 · P2：服务登记、通知和人工模式需要分别定义

**证据类型：当前源码事实 + 已完成的本地分类探针。** Spec §1.2 将 SR-垃圾、SR-毛巾、SR-遗失统称“服务请求未转人工”，§2.5 又提出新增意图字段后强制工具或人工。当前已有服务登记路径和意图字段，目标需先校正。

本轮 `answer_policy.is_service_request` / `handoff_reason` 对原场景问题的结果：

| 问题 | is_service_request | handoff_reason |
| --- | --- | --- |
| 毛巾不够用了，能再给两条吗 | True | None |
| 能来收一下垃圾吗 | False | None |
| 我昨天退房把充电器落在房间了 | False | None |

[conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1645) 的 `_record_task_suggestion` 用当前请求判定触发登记，再按实际持久化与员工通知入队结果构造 `GuestActionResult`。毛巾请求可走登记/通知；切人工由归一化接管理由另行决定。因此不能把“未切人工”直接当成“没登记或没通知”。

[deepseek_client.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:299) 的 `AssistantDecision` 已有 `intent: str`。P3 应先确定现有字段能否收紧或扩展，不另建平行意图字段。

**修订要求：**逐项定义当前服务申请、服务政策咨询、历史提及、遗失物报备的期望动作，以及字段未知、模型失败、登记失败、通知失败的结果；分别核验任务、通知、会话模式与最终回复。低风险服务请求是否必须切人工是业务决策，不能由“服务请求”这一标签自动推出。P3 目前只定方向，仍需完成可实施的文件/符号计划与验收。

## 需同步纠正的现状描述

1. Spec §1.1“切人工后机器人静默”过度概括。[conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:775) 的 `handle_message`、`process_debounced_message`、`process_recorded_message` 在仅 `HUMAN_ACTIVE` 时仍允许独立低风险问题；`_in_native_session` 判断的原生人工会话才静默。
2. 运行器实际符号是 `reply_regression._Runner`，不是 `_LiveRunner`。
3. “问题必须先命中 topic”也过度概括。[knowledge_evidence_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:708) 的 `_build_evidence_plan_for_period` 在无 topic 时仍有 `special:` 限定覆盖分支，可能生成 grounded 计划。应按具体失败路径表述。
4. `.stage` 中的 1.68.0 门禁是历史模型结果。文件 `fixture_sha256` 与本轮夹具字节摘要匹配，只证明样本身份一致，不证明修订后的候选通过或当前生产状态。

## Spec 修订顺序与验收交付

1. **P0：校正门禁契约与分类。** 保留六项知识缺口路由的证据；将动作成功/失败与空正文反例补入验收设计，重新分配三个住宿场景根因。预期变更留原因与 `expectation_changes`，不批量接受成功字符串。
2. **P1：补齐语义选择器的事实契约。** 复用合法检索与来源模型，说明多问完整性、条件/否定、全体相关候选冲突、特殊期间政策及失败回退；选中合法编号不能作为唯一安全断言。
3. **P2：明确危险处置下限。** 给出咨询误报、明确现场危险、混合句、否定/假设、模型错误或超时的行为表，再确认 D3。
4. **P3：单独细化现有 intent 与动作。** 依据 R5 分开登记、通知和人工接管，先把目标与入口写清，再定实现范围。
5. **泛用性验收：保留独立编写与冻结。** 已知失败的改写可验证表达鲁棒性；独立反例同时覆盖危险/咨询、房间/期间、费用/条件、多问、服务动作成功/失败、模型超时/错误来源选择。分别报告正文、来源与动作正确性，注明样本数量和范围。“反例样本中零漏判”不能写成所有真实危险均无漏判的证明。

验证按最终影响面选择；不机械要求每个内部步骤重复全量和模型门禁。涉及主模型提示、问题分类、知识检索或公共回复流程的最终候选，按项目规则准备全量真实模型门禁；执行真实调用前仍需当前明确授权。独立验收集公开或用于修复后转为回归集，不能继续称盲测。

## 本轮验证与未覆盖边界

审查阶段已完成两组纯离线探针：夹具检索/证据计划/分类，以及 `judge` 假通过反例。探针未落为独立脚本；上文提供 `judge` 可重跑入口，检索探针的目标范围和判据已列出。没有调用真实 DeepSeek、Hostex 或企业微信。

审查阶段以下八个既有单元测试通过，结果为 **8 passed in 1.11s**，覆盖当前危险设施优先级、登记/通知失败文案、任务先登记后回复、人工状态低风险回复、原生人工静默及客诉即时通知：

```bash
RUN_LIVE_CONTRACT_TESTS=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  tests/unit/test_conversation_service.py::test_dangerous_equipment_fault_uses_emergency_flow_first \
  tests/unit/test_conversation_service.py::test_equipment_task_failure_never_claims_manual_submission \
  tests/unit/test_conversation_service.py::test_ai_task_is_recorded_before_guest_reply_and_notifies_staff \
  tests/unit/test_conversation_service.py::test_failed_registration_never_claims_staff_notified \
  tests/unit/test_conversation_service.py::test_unrelated_low_risk_question_gets_bot_reply_during_human_takeover \
  tests/unit/test_conversation_service.py::test_native_human_session_records_guest_messages_without_any_reply \
  tests/unit/test_conversation_service.py::test_complaint_notifies_staff_at_once_without_waiting_for_the_model \
  tests/unit/test_conversation_service.py::test_service_registration_precedes_reply_and_preserves_policy
```

整理报告时相关源码、夹具与测试文件没有变化，复用上述结果；文档交付只做静态核验。八项通过证明当前测试覆盖的边界，不证明拟议的语义选择器/危险降级已实现或通过。

未验证：新方案真实模型质量/延迟/费用、独立泛用性集合、生产语义检索开关、PostgreSQL竞争、部署副本与运行容器、登录后台、真实订单或消息。`_Runner` 不覆盖消息合并、outbox、过时回复或客户端实际收件；模型门禁通过也不能替代这些事实。

## 工作区归属与证据绑定

报告写入前没有 tracked diff；已有未跟踪项为 `.impeccable/critique/` 和被审 Spec，均不是本次报告新增。本次只新增本报告；AOCI 收尾如需更新认知资产，须与业务文件分开归因。不要整目录暂存，不要读取、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。

以下 SHA256 是本轮报告整理时核对的字节身份，用于判断旧证据是否仍适用，不能替代后续行为验证：

| 文件 | SHA256 |
| --- | --- |
| [回复泛用化 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md) | `fb53e220e7339265dd5b18c9ace604d3d794f076e1f18df3d7a3e862a165e916` |
| [conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py) | `de2b6181756452dbf0ac354dff23285261d12d2e6b25066377ea46a8be335254` |
| [emergency_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/emergency_service.py) | `b8fecd9e78f3666ac57ce63457108de7c42a8fdad186aada672efab29ba8a213` |
| [knowledge_evidence_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py) | `1fdfc57f07fdee54aea26186e46e4bcc3e29ef6bd97865d50988fb237d58b95e` |
| [knowledge_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py) | `6454c9f23252a5165d3afc827a213c35a1e585156d763af8fe6fcabd12488d20` |
| [deepseek_client.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py) | `b185617cc0830befab18cbfba2d2ef04a54482fb83e13f72cba6ce51e27badda` |
| [reply_regression.py](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py) | `f5a0bfdc884a3d5bcfb834387f70fcfe73cbb49a2694a3129c01c839b3a5a343` |
| [guest_reply_scenarios.json](/Volumes/02/code/homestay-bot/tests/fixtures/guest_reply_scenarios.json) | `31029092d9f71bc536ee8db7feca691dd0268ed7d46fc7e1e508cd38f71062bc` |
| [test_conversation_service.py](/Volumes/02/code/homestay-bot/tests/unit/test_conversation_service.py) | `0eab2f50c52cf2dd0930f98edd8789e695fd14aa895d471c831ed2b8c4a87f1d` |
| [1.68.0 历史门禁结果](/Volumes/02/code/homestay-bot/.stage/reply-gate-v1.68.0.json) | `7f2dc6333f52caa15ad69200bbea9aee94055cab1734f754e58d9c56d6416185` |

接手若发现相关输入变化，只重验受影响项并更新判断；不要继续引用失效证据，也不要将历史门禁、测试号或发布许可当成本轮授权。
