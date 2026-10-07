# Codex → Claude：回复泛用化 v3 与紧急豁免修复 Spec 复审交接

- 日期：2026-10-07（Asia/Shanghai）；作者：Codex。
- 用户目标：审查两份方案，并将审查结果写成交接报告。
- 工作区：`/Volumes/02/code/homestay-bot`，分支 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 状态：方案复审完成；本轮交接只新增本文，不修改业务源码、两份 Spec、夹具或门禁基线。建议修订后再进入实施；本文不构成编码、提交、推送、部署或真实外部调用授权。

## 审查对象与结论

Claude 的[复审请求](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v3-review-handoff.md)明确指定以下两份文档：

1. [回复泛用化 Spec v3](/Volumes/02/code/homestay-bot/docs/specs/2026-10-06_reply-generalization-spec.md)。
2. [紧急分类询问豁免作用范围修复 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-07_emergency-exemption-scope-spec.md)。

方向可以保留，但目前仍有三项 P1 契约缺口、两项 P2 文档问题。以下使用 `V3-R1` 至 `V3-R5`，与前两轮 R1–R5、G1–G4 区分。

v3 值得保留的变化：共同意图由同一计划贯通；校验与登记共用判定；证据选择并入主调用；冲突检查扩到同组合法候选；明确危险只升级不降级；动作验收与模型门禁分开。将已复现的豁免越界拆成独立修复也合理。这些是对方案契约的评价，不是新实现已经通过验收。

## V3-R1 · P1：规划失败重新放行已证实的误登记

**证据：方案契约 + 当前函数复核 + 既有登记探针。** v3 §2.3 失败矩阵规定规划超时、格式错误或全部摘录失效时回旧规则；§2.5 判定表规定计划缺失时沿用现行规则。

本轮本地复核以下两句，[answer_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:267) 的 `is_service_request` 都返回 `True`：

| 当前输入 | 语义 | 旧规则结果 |
| --- | --- | --- |
| 不用送毛巾了 | 撤回 | 服务申请 |
| 上次请帮我送两条毛巾 | 历史提及 | 服务申请 |

[conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1645) 的 `ConversationService._record_task_suggestion` 在 `requested=True`、模型无建议时自动创建 `SPECIAL_SERVICE` 建议，再登记任务和通知。前轮 G2 已用内存端口复现这两句的登记与通知调用，本轮相关源码摘要未变，可复用该证据；它不证明真实写库或收件。

因此，按当前 Spec 的失败契约，正常计划能够否决撤回，规划失败却恢复误登记。这与 §2.1 的“含糊请求不自动登记”“词表不独立创造授权”矛盾。

**修订要求：**按能力分别定义回退。明确危险继续保留确定性处置；静态事实可保守回未确认；服务意图无法核验时不创建新任务，必要时请客人确认。不要整轮恢复旧规则的服务授权。既有故障和预订链路的确定性行为分别评估，不在本轮顺便放宽或关闭。

**验收目标：**规划超时、Schema 错误、全部 `quote` 失效、撤回项失效但其他项有效时，上述两句均不产生新的服务任务或成功登记收尾；实际动作与最终客人正文一起断言。

## V3-R2 · P1：规划入口晚于它需要纠正的分流

**证据：方案契约 + 真实调用链 + 本地分类探针。** v3 §2.3 将 `_plan_turn` 放在 `DeepSeekGuestAssistant.respond` 检索之前，§2.6 却要求会话层用计划复核情绪词客诉和英文无关判定。

当前 [conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:775) 的 `handle_message` 和 `process_debounced_message` 都在调用 assistant 之前执行客诉和无关分流，并可直接返回；assistant 直到 `_process_model_reply_body` 才通过 `GuestAssistantPort.respond` 被调用。

| 输入 | 当前提前分流 |
| --- | --- |
| 你们不接受宠物吗？ | `ComplaintService.classify` → `agitated` |
| 第一次来太开心了!!! | `ComplaintService.classify` → `agitated` |
| Is the fridge stocked with water? | `is_homestay_related=False` → 无关婉拒 |

按目前写明的入口位置，这些输入到不了规划。P4 虽列出会话分流修改，但未定义会话层从哪里取得计划、如何传入 assistant、如何避免重复规划。源码依据另见 [complaint_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_service.py:34) 的 `ComplaintService.classify`、[answer_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:245) 的 `is_homestay_related`。

同类提前返回还存在于 [deepseek_client.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2189) 的 `respond`：无日期问价会在检索前整轮返回。本地 `_price_question_needs_dates("房价多少早餐几点", ...)` 为 `True`。若规划仅插在检索处，早餐子问仍被吞掉，与逐项处理契约不符。

**修订要求：**补明确调用顺序：原生人工静默与明确危险先行；需要语义复核的软分流取得已核验计划后再决定；下游复用同一计划。覆盖即时入口、合并入口和 `process_recorded_message` 后台入口，写清计划的生产、传递、复用与失效边界。无日期、某项查询失败等应只影响对应子项，不能整轮提前返回。不要让明确危险等待模型，也不要为解决传递问题创建并行决策引擎。

**验收目标：**走真实 `ConversationService` 链路证明以上三个误分流输入能够进入计划复核；再证明“房价多少早餐几点”仍回答已有早餐证据，同时只对房价追问日期。仅直接调用 `_plan_turn` 的测试不足。

## V3-R3 · P1：末尾位置豁免同样越界，不能直接沿用

**证据：当前源码 + 进程内变异探针。** 紧急 Spec §2.2 要删除第一处整句短路，但报警器位置咨询项仍写“沿用末尾现有的位置咨询规则”。

[emergency_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/emergency_service.py:154) 的 `EmergencyService._noncurrent_mention` 有两处相关的整句判定：第一项短路，以及末尾的位置/安全政策咨询返回。末尾不检查 `match` 的位置，其现场信号清单还不含“着火”。

本轮仅在独立 Python 进程中删除第一项短路，源码文件保持不变，结果如下：

| 输入 | 当前源码 | 仅删除第一项短路 |
| --- | --- | --- |
| 房间的插座冒烟了还有燃气灶吗 | 非紧急 | fire |
| 厨房着火了报警器在哪里 | 非紧急 | **仍为非紧急** |
| 厨房着火了报警器位置在哪 | 非紧急 | **仍为非紧急** |

这证明第二处整句豁免必须一起限定。该探针只定位残留缺陷，不是拟议新规则的实现或验收。

**修订要求：**末尾位置咨询也按匹配区间核验，只有咨询短语自身对应的危险词可豁免，其他危险命中不能进入该豁免。不要仅在现场词表里补“着火”来代替作用范围修复。把“安全规定/政策”这一出口的处理也写清；否定、假设等另有范围的逻辑不因本报告自动扩大改动。

**验收目标：**保留普通位置咨询的非紧急结果，同时覆盖同句“着火 + 报警器位置咨询”“燃气味 + 设备咨询”等最小对照；验证最终分类处置和会话行为。

## V3-R4 · P2：报警器用例属于另一根因，跨消息说明也过宽

**证据：当前模式匹配 + 分类探针 + 合并入口源码。** 紧急 Spec §1.2 的“报警器在哪里一直在响”当前确实是非紧急，但 `EmergencyService._patterns` 对它没有任何命中，`_noncurrent_mention` 根本未被调用。将它归为豁免越界不准确。

| 输入 | 当前模式命中 | 仅删除第一项短路后的分类 |
| --- | --- | --- |
| 报警器在哪里一直在响 | 无 | 非紧急 |
| 烟雾报警器在哪里一直在响 | fire | fire |

**修订要求：**若保留“不改 `_patterns`”边界，将本补丁用例改为已命中的第二句；原句作为纯词面漏检归 v3 P4。若要把原句纳入本补丁，先在 Spec 中明确扩大范围及对应验证，不能用豁免函数无法达到的结果作验收。

紧急 Spec §1.2 还称 `_policy_questions`“拆分后逐条分类”。实际 [conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1234) 的 `_policy_questions` 生成原文、空白展平、去空白三种文本；`process_debounced_message` 找到确定危险就停止循环。对于“插座冒烟了\n还有燃气灶吗”，原文已检出 fire，后两种形式虽漏判，也不会覆盖前面的确定危险。对于“烟雾报警器在哪\n一直在响”，三种形式都漏判。

**修订要求：**将跨消息结论按实际输入和分类顺序限定。参见 [message_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/message_service.py:171) 的 `MessageService.build_guest_batch`，批次正文以换行连接。不要把无标点同句漏洞概括成所有连发消息均漏判。

## V3-R5 · P2：服务判定表使用了白名单之外的意图

**证据：文档内契约不一致。** v3 §2.5 用 `policy_inquiry` 表示“不登记”，§2.3 的 `PlanItem.kind` 白名单却没有它。合法计划无法按该枚举产生这一行。

**修订要求：**统一表示方式。可以明确政策咨询归 `static_fact`，也可以在确有独立职责时增加枚举；不要让各调用方自行解释。补齐有效计划只有静态咨询、申请与撤回混合、部分项作废、计划与旧规则冲突的默认结论。当前申请与撤回针对不同事项时，不能整轮一刀切。

**验收目标：**两处调用 `resolve_service_request` 对相同输入与计划得到相同结论；Schema 允许的每种计划组合都有明确默认方向，政策咨询和失效项不通过空白分支恢复旧词面授权。

## 方案优化与接手顺序

继续采用“模型选择，本地核验和执行”。合法选项、合法编号和原话摘录，只缩小生成范围，不能单独证明当前授权或回答适用；不要将这版描述成已经消除语义误判。

1. 先修订独立紧急 Spec 的 V3-R3、V3-R4，锁定完整豁免范围与真实可达到的验收输入。
2. 修订 v3 的 V3-R1、V3-R2、V3-R5，写完整调用顺序、服务失败矩阵和统一枚举。
3. 以 V-a 的完整端到端契约作为评估候选，再比较 V-b。V-b 开放全部只读工具、由主调用产生计划，与统一的“旧规则 ∪ 计划”和“不接受主模型回传计划”描述有差异，须明确该变体的原始输出核验和本地填写方式；不能只比较一次调用的成本。
4. 正确性、危险下限、实际动作和子问题覆盖先满足约定，再用同一冻结样本比较调用次数、费用与 P95。本轮没有真实模型测量，不能声称 V-a 或 V-b 更快、更准。
5. 修订后继续按项目三段确认与明确“开始”门禁实施。具体业务决定仍由两份 Spec 承载，本文不替用户裁决 D1–D12。

前轮[选择题方案与 v2 对比交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_codex-to-claude-reply-generalization-comparison-handoff.md)保留历史证据；本报告是上述 v3 摘要对应的复审，不把前轮 v2 的缺口整体重复认定为 v3 缺陷。

## 离线复现入口

从 `/Volumes/02/code/homestay-bot` 执行以下命令。只调用本地函数、读取虚构夹具并在进程内变异；无模型、真实接口、数据库或发送调用。断言描述当前旧缺陷，修复后不能为让此探针继续通过而恢复缺陷。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import ast
import inspect
import json
import re
import textwrap
from pathlib import Path
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.services.answer_policy import is_homestay_related, is_service_request
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService

for text in ['不用送毛巾了', '上次请帮我送两条毛巾']:
    assert is_service_request(text)
    print('旧服务判定', text, True)

fixture = json.loads(Path('tests/fixtures/guest_reply_scenarios.json').read_text())
for scenario in fixture['scenarios']:
    if scenario['id'] in {'E-冰箱', 'C-宠物', 'F-堵了'}:
        text = next(item['content'] for item in reversed(scenario['messages'])
                    if item.get('role') == 'user')
        print(scenario['id'], is_homestay_related(text), vars(ComplaintService.classify(text)))

text = '房价多少早餐几点'
assert DeepSeekGuestAssistant._price_question_needs_dates(
    text, [{'role': 'user', 'content': text}], None
)
service = EmergencyService()
text = '报警器在哪里一直在响'
assert not any(pattern.search(text) for _, pattern in service._patterns)
assert not service.classify(text).is_emergency
print('报警器无模式命中', text)

# 仅移除第一项整句短路，隔离末尾豁免的影响；不写文件，不冒充新规则实现。
tree = ast.parse(textwrap.dedent(inspect.getsource(EmergencyService._noncurrent_mention)))
function = tree.body[0]
function.decorator_list = []
assert isinstance(function.body[2], ast.If)
del function.body[2]
namespace = {'re': re}
exec(compile(tree, '<in-memory-exemption-probe>', 'exec'), namespace)
service._noncurrent_mention = namespace['_noncurrent_mention']
assert service.classify('房间的插座冒烟了还有燃气灶吗').is_emergency
for text in ['厨房着火了报警器在哪里', '厨房着火了报警器位置在哪']:
    assert not service.classify(text).is_emergency
    print('仅移除首项仍漏判', text)

# 使用新实例观察当前正式代码的跨消息候选，避免混入上面的进程内变异。
for text in ['插座冒烟了\n还有燃气灶吗', '烟雾报警器在哪\n一直在响']:
    print('批次候选', [(item, vars(EmergencyService().classify(item)))
          for item in ConversationService._policy_questions(text)])
PY
```

## 验证结果与未覆盖边界

- 审查阶段已执行纯本地服务/客诉/无关分类、问价提前返回、危险模式及分类探针，并执行进程内删除第一处短路的变异探针。结果见各发现；无真实外部调用。
- Git 历史确认：`git show` 与 `git blame` 将现有 `_noncurrent_mention` 两处相关判定归于 `22dbd7c`，提交日期 2026-09-28，提交主题为 v1.42.0 回复边界重构。历史归因不证明生产当前运行该源码。
- 前轮 G2 内存登记探针按未变化的源码指纹复用，未因交接重跑。本文合并复现命令在写报告时仅检查 Python 语法与本地链接；单项执行证据来自审查阶段，不宣称合并命令已整段再跑。
- 本轮只新增 Markdown：运行 `git diff --check`，检查文档自身空白、链接目标、代码围栏与 Python 片段语法。无业务行为变更，不重跑业务测试、全量测试或真实模型门禁。
- 未验证：新 `TurnPlan`/`resolve_service_request`/豁免规则实现、真实模型效果与费用/P95、目标数据库上的实际登记、生产运行、真实 Hostex/企业微信或客人收件。任何本地探针不能替代这些证据。

## 证据指纹与工作区归属

下列摘要在审查后和写报告前核对一致。源码或输入变化时，只重验受影响的结论；行号仅供该快照定位。

| 文件（相对上述工作区） | SHA-256 |
| --- | --- |
| docs/specs/2026-10-06_reply-generalization-spec.md | `1f4d87af8b88755ded6b60c57a0dcb44ac2d3f284665849351d08152ef68ba3c` |
| docs/specs/2026-10-07_emergency-exemption-scope-spec.md | `173b7f123502e9499f34fc2409c72926b9a24d17b522c6a070dde11cab4cbbf0` |
| docs/reviews/2026-10-07_claude-to-codex-reply-generalization-v3-review-handoff.md | `79d135d3261b7eb2dba6ad12f24471e6856a851e4dc510af0bf787828b4e44db` |
| src/homestay_bot/integrations/deepseek_client.py | `b185617cc0830befab18cbfba2d2ef04a54482fb83e13f72cba6ce51e27badda` |
| src/homestay_bot/services/conversation_service.py | `de2b6181756452dbf0ac354dff23285261d12d2e6b25066377ea46a8be335254` |
| src/homestay_bot/services/emergency_service.py | `b8fecd9e78f3666ac57ce63457108de7c42a8fdad186aada672efab29ba8a213` |
| src/homestay_bot/services/answer_policy.py | `3127dde04de8c7fec071cd958531112248fce0b6d4766223f57215219aa9d4a0` |
| src/homestay_bot/services/complaint_service.py | `4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef` |
| tests/fixtures/guest_reply_scenarios.json | `31029092d9f71bc536ee8db7feca691dd0268ed7d46fc7e1e508cd38f71062bc` |

写报告前已有：两份未跟踪 Spec、Claude 的 v3 请求、Codex 的前两轮报告、`.impeccable/critique/`，以及修改中的 `.aoci/baseline.json`、`aoci.code.txt`。本轮只新增本文；AOCI 收尾更新应与业务文件分开归属，不能把已有索引差异整体归到本轮。

接手时保留上述已有工作；不要整文件回退或顺便更改两份 Spec。不得读取、摄入、暂存、修改或提交受保护的 `YuMi民宿AI项目总结.txt`。未经当前明确授权，不提交、推送、部署、运行真实模型/Hostex/企业微信、发送消息或写生产数据。
