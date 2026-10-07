# Codex → Claude：回复泛用性根因、选择题方案与 v2 对比交接

- 日期：2026-10-07（Asia/Shanghai）。
- 用户目标：审查“客人稍微改几个字就认不出来”的泛用性问题，提出独立方案，与 Claude 的方案对比优化；随后明确要求写交接报告。
- 审查对象：[Claude v2 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md)，SHA-256 `f6e84f5f7dc0b01486c1b6a6ae16fa1d592d14d07ca9e5760678b6e0910b7684`。
- 工作区：`/Volumes/02/code/homestay-bot`，分支 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 状态：审查与方案建议，尚未实施；已向用户解释“选择题”的含义，具体业务取舍和编码尚未获批。

## 结论与接手目标

当前问题来自多个环节独立使用词面规则决定业务行为：检索与工具开放在模型前分流，模型返回后又按原句词表删除建议，任务登记端再按词表判断是否请求。语义理解在某一处成功，仍可能被其他环节否决；否定或历史提及也可能被词面规则误放行。继续扩充同义词无法证明整类问题已解决。

Claude v2 已吸收前轮关于危险降级、门禁假通过、住宿召回和实际动作结果的反馈，保留这些改进。下一版 Spec 应把 P3 的共同意图契约前移，与 P1 检索和选择接通，覆盖普通意图从理解到执行的整条路径。不要只增加后置知识选择器后就宣称泛用性问题已解决。

本报告是对 v2 的补充审查；[前轮 v1 审查报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_codex-to-claude-reply-generalization-spec-review-handoff.md) 保留历史发现。本次未修改 Claude Spec、业务源码、场景预期或回归基线。

## 已验证的现状与根因

### G1 · P1：模型建议仍受词面否决，工具在理解前被关闭

依据：[deepseek_client.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:946) 的 `DeepSeekGuestAssistant._validate_decision`、`_allowed_tool_names`、`_should_force_availability`、`_should_force_property_catalog`；[answer_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:267) 的 `is_service_request`。

离线向 `_validate_decision` 传入相同的构造模型决定：`intent="service_request"`、同一 `SPECIAL_SERVICE` 建议、置信度 1。只改变客人问题，结果如下。这证明本地校验行为，不代表真实模型已理解这些输入。

| 问题 | 本地认定服务申请 | 校验后保留任务建议 |
| --- | --- | --- |
| 麻烦送两条毛巾 | 是 | 是 |
| 劳驾把浴巾拿两条过来 | 否 | 否 |
| 请帮我收一下垃圾 | 否 | 否 |
| 不用送毛巾了 | 是 | 是 |
| 上次请帮我送两条毛巾 | 是 | 是 |

离线调用 `_allowed_tool_names(question, "")`，没有上文或后台显式日期补充：

| 问题 | 允许的工具 |
| --- | --- |
| 明天能住吗 | `search_availability` |
| 明天有没有地方落脚 | 空集合 |
| 你们有哪些房型 | `list_properties` |
| 把可选的住处给我看看 | 空集合 |

后两组是召回和意图识别反例；前两组含漏识别和误放行。选择器即使选对知识，也不能自动修复这些入口与出口。

### G2 · P1：登记端可以绕过模型的“没有任务建议”

依据：[conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1645) 的 `ConversationService._record_task_suggestion`。该函数先以 `is_service_request`、`is_booking_action_request` 或 `_is_facility_issue` 计算 `requested`；为真且模型建议为空时，自动生成 `SPECIAL_SERVICE` 建议。`_process_model_reply_body` 会调用该登记函数。

本轮实际调用该函数，使用内存任务与通知端口替身、无住宿确认端口、构造客户和消息。模型决定为 `intent="chat"`、`task_suggestion=None`：

| 问题 | 函数调用 | 返回的动作收尾 |
| --- | --- | --- |
| 不用送毛巾了 | 登记任务、通知员工 | 请求已登记，通知已提交，尚待确认 |
| 上次请帮我送两条毛巾 | 登记任务、通知员工 | 请求已登记，通知已提交，尚待确认 |
| 劳驾把浴巾拿两条过来 | 均未调用 | 空 |

该探针证明真实函数的调用决定与结果组装，没有真实持久化、事务提交、员工通知或客人发送证据。修订 Spec 必须同时覆盖登记端，不能只让模型输出更好的 `intent`。撤回和历史提及不应因此建立新的服务请求；是否撤销已有任务须另定契约，本报告不默认扩大到自动取消任务。

### G3 · P1：选择合法编号仍不能证明语义适用与冲突完备

依据：[knowledge_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:661) 的 `KnowledgeService.retrieve_detailed`，以及 [knowledge_evidence_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:340) 的 `asked_attributes`、`_build_evidence_plan_for_period`。

v2 P1-a 已将冲突检查扩大到选中条目及同组合法候选，解决了仅检查选中子集的缺口。但分组仍依赖 `PROPERTY_TOPICS` 和属性正则；新主题或改写属性可能不在规则覆盖内。“要求模型全选”是模型任务要求，不是程序已经证明选得完整。

编号合法、知识已审核、房间及日期合法、整条原文输出，分别证明这些显式边界；不能直接证明答案与问题相关、限定条件适用、否定关系正确或没有漏掉冲突。v2 将误选风险描述为“真实但不对题”仍偏窄，错误适用收费或条件也可能给客人错误结论。

`retrieve_detailed` 在选择前进行房间、有效期、员工配置的触发词过滤，并受条目数量与字数预算限制；后置选择器无法选到未召回的证据。员工配置的触发条件是既有契约，不得为提升召回直接删除或让模型绕过，若改变其含义须单独确认。

### G4 · P1：安全与客诉的词面补丁仍需语义反例约束

依据：[emergency_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/emergency_service.py:154) 的 `EmergencyService._noncurrent_mention`、`classify`；[complaint_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_service.py:34) 的 `ComplaintService.classify`。

| 问题 | 当前本地分类 |
| --- | --- |
| 房间冒烟了 | 确定危险 fire |
| 房间往外窜白烟了 | 非紧急、非可能危险 |
| 你们有燃气灶吗 | 非紧急 |
| 房间提供煤气炉吗 | 确定危险 gas |
| 房间的插座冒烟了还有燃气灶吗 | 非紧急、非可能危险 |
| 你们接受宠物吗 | 非客诉 |
| 你们不接受宠物吗 | 激烈客诉 agitated |
| 第一次来太开心了!!! | 激烈客诉 agitated |

无标点混合句会触发咨询排除并遮住同句事故，是当前分类函数反例；不能据此断言尚未实施的 v2 一定同样失败。后续模型可能补充识别紧急事件，但不能把这当作本地即时处置已覆盖的证明。

v2 保留完整分类处置下限是正确改进。下一版须明确：咨询、否定或假设豁免不能吞掉其他现场事件，包括同句、无标点和跨消息合并；词面未命中的危险也需要补漏。消除咨询误报与保持处置下限存在业务取舍，不能暗中缩小现有安全覆盖。

## “选择题”的共同契约

用户追问“这是不是之前说的让模型做选择题，而不是填空题”。答复已明确：核心相同，进一步要求选择结果贯穿检索、分流和登记。

建议流程：**原始消息 → 子问题与本轮意图 → 合法候选或只读工具 → 条件与来源核验 → 本地执行 → 按实际结果生成最终正文。**

| 环节 | 模型可以选择或提取 | 本地必须掌握 |
| --- | --- | --- |
| 意图 | 当前申请、政策咨询、撤回、历史提及、不明确；复合问题可多项 | 类型约束、原话来源、执行许可；歧义不默认申请 |
| 证据 | 从合法候选选相关条目，可多选或选“无足够依据” | 候选编号、审核状态、房间与日期范围、明确政策优先级 |
| 只读查询 | 选择查询种类，提取目标房间、日期等必要参数 | 工具白名单、参数合法性、费用与调用预算；实时事实来源 |
| 服务动作 | 提出本轮低风险待确认请求 | 类型白名单、可信住宿、幂等、事务与实际登记结果 |
| 最终回复 | 组织普通表达；不能自行宣告动作成功 | `ReplyEvidence` 的来源绑定、`GuestActionResult` 的实际结果及发送安全门 |

关键约束：

- 复用 `AssistantDecision`、`ReplyPart`、`ReplyEvidence`、`GuestActionResult`，只补当前契约必要的信息；不新建平行决策引擎、代理框架或全知识迁移。
- 子问题计划只表达待回答事项和待执行请求；模型自报的 `grounded`、`reply_parts` 来源或 `action_result` 不能直接变成可信证据。当前 `_validate_decision` 会清空模型分项与动作结果，改造须保留这项信任边界的目的。
- 原话引用只能证明摘录来自本轮输入，不能由代码证明其语义就是当前申请；自由文本的意图、条件和否定判断仍可能出错。模型置信度不是授权证明。
- 普通意图在各阶段复用同一判断；既有词表可作额外检测、召回辅助与有界兜底，不能继续各自否决正常表达或独立创造服务授权。
- 日期、数量等必要信息仍需提取并校验，并非所有输入都改为有限选项。查询房号不等于确认住宿，不解锁订单或凭证。
- 原生人工静默、敏感凭证、退款赔偿及真实下单等边界保留；明确危险固定处置仍先行，语义检测补漏和升级。取消既有安全动作须有已确认的新契约。
- 模型超时或格式错误回退旧规则，只能证明存在降级路径，不能宣称降级路径已经具备相同泛用性。静态问题保守分项回复，含糊服务请求不凭词面自动认定当前授权；具体失败矩阵须纳入 Spec。

## 与 Claude v2 的方案对比

| 事项 | v2 | 优化要求 |
| --- | --- | --- |
| P0 判据 | 空正文失败；动作正确性由真实服务集成测试证明 | 保留，并记录口径变化；不靠放宽成功措辞增加通过数 |
| P1-a 选择器 | 一次轻量调用选择合法知识编号 | 保留方向；与共同意图、语义子问题接通，不只覆盖后置知识选择 |
| P1-b 召回 | 房间专属保留名额；期间规则；错字方式待定 | 子问题各自检索后合并去重；预算按覆盖需求测量；优先评估已有语义检索 |
| 条件与冲突 | 同主题/属性规则检查 | 明确规则覆盖上限与语义残余风险；无法确认时保守，不宣称全覆盖 |
| P2 安全 | 完整分类处置；咨询形态另定通知与模式 | 保留下限；加入无标点混合事故和危险改写漏检反例 |
| P3 意图与动作 | P1/P2 后重测再细化 | 共同契约前移，同时明确 `_validate_decision` 与 `_record_task_suggestion` 如何迁移 |
| 调用成本 | 静态问答额外一次调用 | 比较复用现有模型轮次与新增规划调用；报告实际调用次数、费用和 P95，不承诺无额外成本 |
| 泛用验收 | 每场景至少五种改写，独立冻结 | 加业务等价关系和意义变化最小对照，分别衡量遗漏与误触发 |

三种路线的取舍：继续扩词表改动小，但保留已证明的串联失效；只加后置选择器能改善知识误拒，却覆盖不到入口和动作端；建议以现有结构贯通共同意图，影响面较大，须先确认完整 Spec，换取覆盖本次真实根因的能力。该建议尚无真实模型质量或性能比较结果。

## Spec 修订顺序与验收目标

建议顺序：**修正判据 → 明确共同意图契约 → 打通静态知识与只读查询 → 接入服务登记 → 单独调整危险与客诉分流。** 这是设计建议，不是已批准的执行计划；正式 Spec 仍按项目三段确认并等待“开始”。

Claude 接手时应交付：

1. 逐项回应 G1–G4，明确接受、不同意及源码/反例依据。统一语义结果如何传到工具开放、模型后校验和登记端，必须写到文件与符号；不能只增加模型提示。
2. 把“当前申请、咨询、撤回、历史、不明确”的行为写清。确定性词表 fallback 不得保留本报告中的撤回/历史误建任务；确认低风险登记后通知、是否切人工及失败时的正文。
3. 明确每个子问题的房间、日期、条件和证据关系。回答一项不得吞掉其他项，新增独立问题不得丢失原来可回答的部分；未知目标或指代需澄清。
4. 明确完整安全处置、员工通知和会话模式分别如何变化；同句或跨句事故优先。沿用 v2 D3、D6、D7 等待定业务决策，不把本报告建议当成用户已批准。
5. 给出模型调用预算、超时和失败矩阵；复用现有调用与新增调用需要真实测量后比较。
6. 先冻结实现候选，再由未参与实现的一方编写并冻结验收输入。实现中看到并用于修复的样本转为回归，不能继续称盲测。

验收至少包括：

- **业务等价改写**：同义、错字、语序、礼貌词、中英混合、间接问法、标点变化和消息拆合；来源、应有动作及风险处理保持一致，正文无需逐字相同。
- **意义变化最小对照**：请送/不用送、现在/上次、房间设施/私人物品、政策咨询/申请、假设/现场事故。预期动作必须随意义改变。
- **组合与局部失败**：无标点多问、不同房间日期、独立问题新增、模型漏选、错误编号、超时、证据冲突，以及登记或通知失败。
- **分开报告**：候选召回、子问题覆盖、错用事实/条件、服务漏登记与误登记、危险漏检、最终正文及实际动作。关键安全错误在验收样本中必须为零；有限样本不证明所有表达均安全。
- **验证分层**：模型门禁证明模型与正文；真实服务加临时 SQLite 证明任务、通知与会话契约；生产、实际外部工具和客人收件须各有独立证据并取得当次授权。

## 离线复现入口与证据限度

本轮已执行两组 Python stdin 探针：一组调用分类、模型后校验、工具白名单及 `judge`；一组调用真实 `_record_task_suggestion` 并使用内存端口替身。下列合并入口便于复核，只使用虚构问题与本地夹具，不连接数据库或外部服务。输出记录的是当前缺口，不能把脚本正常退出理解为产品验收通过。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/Volumes/02/code/homestay-bot/src" \
  "/Volumes/02/code/homestay-bot/.venv/bin/python" - <<'PY'
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from homestay_bot.domain.enums import BusinessTaskType, Language
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision, DeepSeekGuestAssistant, TaskSuggestion,
)
from homestay_bot.services.answer_policy import is_service_request
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.tools.reply_regression import judge

assistant = object.__new__(DeepSeekGuestAssistant)
for question in [
    '麻烦送两条毛巾', '劳驾把浴巾拿两条过来', '请帮我收一下垃圾',
    '不用送毛巾了', '上次请帮我送两条毛巾',
]:
    proposed = AssistantDecision(
        reply_text='收到您的需求。', language=Language.ZH,
        intent='service_request', confidence=1,
        task_suggestion=TaskSuggestion(
            task_type=BusinessTaskType.SPECIAL_SERVICE, description='当前服务需求',
        ),
    )
    checked = assistant._validate_decision(
        proposed.model_dump_json(), question,
        property_knowledge_grounded=False, faq_candidate_ids=set(),
        language=Language.ZH,
    )
    print(json.dumps({
        'kind': 'service', 'question': question,
        'local_service_request': is_service_request(question),
        'task_suggestion_kept': checked.task_suggestion is not None,
    }, ensure_ascii=False))

for question in [
    '明天能住吗', '明天有没有地方落脚', '你们有哪些房型', '把可选的住处给我看看',
]:
    print(json.dumps({
        'kind': 'tools', 'question': question,
        'allowed': sorted(DeepSeekGuestAssistant._allowed_tool_names(question, '')),
    }, ensure_ascii=False))

for question in [
    '房间冒烟了', '房间往外窜白烟了', '你们有燃气灶吗', '房间提供煤气炉吗',
    '房间的插座冒烟了还有燃气灶吗',
]:
    print(json.dumps({
        'kind': 'risk', 'question': question,
        'classification': asdict(EmergencyService().classify(question)),
    }, ensure_ascii=False))

for question in ['你们接受宠物吗', '你们不接受宠物吗', '第一次来太开心了!!!']:
    print(json.dumps({
        'kind': 'complaint', 'question': question,
        'classification': asdict(ComplaintService.classify(question)),
    }, ensure_ascii=False))

fixture = json.loads(Path(
    '/Volumes/02/code/homestay-bot/tests/fixtures/guest_reply_scenarios.json'
).read_text())
scenarios = {item['id']: item for item in fixture['scenarios']}
records = {
    'F-空调': {'route': 'facility', 'final': ''},
    'SR-毛巾': {
        'route': 'handoff', 'final': '收到，已登记。',
        'action_result': {'registered': False, 'notification_queued': False},
    },
}
for code, record in records.items():
    print(json.dumps({
        'kind': 'old_judge', 'id': code,
        'judgement': judge(scenarios[code], record, fixture['global_must_not_include']),
    }, ensure_ascii=False))

@asynccontextmanager
async def savepoint():
    """仅提供内存探针的上下文，不连接数据库或证明事务行为。"""
    yield

async def check_registration():
    """使用内存端口观察真实登记函数的调用决定，不执行真实业务写入。"""
    for question in ['不用送毛巾了', '上次请帮我送两条毛巾', '劳驾把浴巾拿两条过来']:
        calls = []

        async def record(**kwargs):
            """记录任务端口被调用，并返回最小虚构结果。"""
            calls.append('record_task')
            return SimpleNamespace(id=1, task_type=kwargs['task_type'])

        async def notify(*args, **kwargs):
            """记录通知端口被调用，不发送或入队消息。"""
            calls.append('notify')

        service = object.__new__(ConversationService)
        service._business_tasks = SimpleNamespace(record_ai_suggestion=record)
        service._conversations = object()
        service._savepoint_factory = savepoint
        service._notify_employee = notify
        decision = AssistantDecision(
            reply_text='已了解。', language=Language.ZH, intent='chat', confidence=1,
        )
        reply = await service._record_task_suggestion(
            SimpleNamespace(id=1, customer_id=1, language=Language.ZH),
            SimpleNamespace(content=question, msgid='synthetic-review'), decision,
        )
        print(json.dumps({
            'question': question, 'model_task_suggestion': None,
            'actual_function_calls': calls,
            'action_result': decision.action_result.model_dump(), 'action_reply': reply,
        }, ensure_ascii=False))

asyncio.run(check_registration())
PY
```

`F-空调` 空正文和 `SR-毛巾` 动作失败的构造 record 仍得到旧 `judge` 的 `ok=True`。这与 v2 P0 修正方向一致，不能通过放宽成功措辞来抹平。前轮报告误写的场景字段 `code` 已更正为 `id`。

验证复用：相关源码和夹具摘要未变，复用本轮两组探针结果及前轮八项相关单测，不在文档交付阶段重复业务测试。新合并脚本只做静态语法核验，未把它记成新增业务验收。报告和字段更正只做文档检查。

未验证：拟议共同意图/选择器的真实模型正确率、费用与延迟，独立泛用验收集，生产语义检索配置，真实数据库事务/竞争，部署副本、运行容器、登录后台、真实 Hostex/企业微信调用和客人实际收件。

## 证据绑定与工作区归属

以下为本次报告整理时的源码/夹具摘要；接手后若相关输入改变，只重验受影响项，不沿用为当前事实。

| 对象（均在 `/Volumes/02/code/homestay-bot`） | SHA-256 |
| --- | --- |
| `src/homestay_bot/integrations/deepseek_client.py` | `b185617cc0830befab18cbfba2d2ef04a54482fb83e13f72cba6ce51e27badda` |
| `src/homestay_bot/services/conversation_service.py` | `de2b6181756452dbf0ac354dff23285261d12d2e6b25066377ea46a8be335254` |
| `src/homestay_bot/services/answer_policy.py` | `3127dde04de8c7fec071cd958531112248fce0b6d4766223f57215219aa9d4a0` |
| `src/homestay_bot/services/emergency_service.py` | `b8fecd9e78f3666ac57ce63457108de7c42a8fdad186aada672efab29ba8a213` |
| `src/homestay_bot/services/complaint_service.py` | `4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef` |
| `src/homestay_bot/services/knowledge_service.py` | `6454c9f23252a5165d3afc827a213c35a1e585156d763af8fe6fcabd12488d20` |
| `src/homestay_bot/services/knowledge_evidence_policy.py` | `1fdfc57f07fdee54aea26186e46e4bcc3e29ef6bd97865d50988fb237d58b95e` |
| `src/homestay_bot/services/reply_plan.py` | `7ba2118cbdf33db96c9d0f1e95751eefdf0c86775bb8ae69841166545ea9e226` |
| `src/homestay_bot/tools/reply_regression.py` | `f5a0bfdc884a3d5bcfb834387f70fcfe73cbb49a2694a3129c01c839b3a5a343` |
| `tests/fixtures/guest_reply_scenarios.json` | `31029092d9f71bc536ee8db7feca691dd0268ed7d46fc7e1e508cd38f71062bc` |

本次文档改动：新增本报告；在前轮报告顶部追加 v1/v2 版本说明，并更正 R3 脚本字段。按项目规则维护受影响的 AOCI 条目和基线，托管资产与业务文件分开归因。

任务开始前工作区已有 `.aoci/baseline.json`、`aoci.code.txt` 的未提交改动，以及未跟踪的 `.impeccable/critique/`、前轮报告和 Claude Spec。本次不清理、不覆盖这些既有内容；索引文件包含前轮、本轮及其他会话的更新，不应整文件归为本轮业务实现。

禁止下一步：未确认完整 Spec 且未收到“开始”前不写业务代码；本报告不授权提交、推送、部署、真实模型/Hostex/企业微信调用、真实消息、订单操作或生产写入。不得把本地探针、历史门禁、索引对齐或报告完成当成新方案和生产验收。
