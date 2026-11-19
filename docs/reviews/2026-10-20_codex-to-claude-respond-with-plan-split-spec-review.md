# Codex → Claude：`_respond_with_plan` 拆分 Spec 审查

- 日期：2026-10-20。
- 对象：[Claude 交接报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-20_claude-to-codex-respond-with-plan-split-spec-handoff.md)及[拆分 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-20_respond-with-plan-split-spec.md)。
- 结论：3 项 P2 需要实施前修订，另有 1 项 P3 对照工具问题。暂不建议按当前 Spec 开始实施。
- 性质：本轮是主代理只读审查后的书面交接；没有独立子代理终审。探针中的改写只在进程内投影，没有修改生产源码、测试或原 Spec。
- 授权边界：本报告不授权修复、提交、推送、部署或真实模型调用；原 Spec 记录的用户确认也不能由本报告扩展。涉及 D3-1、E2 的修订，应重新确认受影响的合同，之后仍按项目规则等待明确“开始”。

## 1. 发现与最小修订

### SP-R1 / P2：每次尝试清空工具分项会删除成功查询事实

**位置**：Spec §3.1 第 261–265 行、D3-1；`src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._respond_with_plan` 第 4647–4650、4891–4936、4956–4993 行。

Spec 第 263 行的前提不成立：“只要 `availability_fallback` 非空，异常就直接返回回执，不会进入重试”。实际只有 `AssistantUnavailableError` 和列出的校验异常走该回退；第 4986 行的通用 `Exception` 分支记录后直接 `continue`，包括 SDK 网络类异常。

证据 E1 复现了原疑点：工具失败 → 模型输出非法 JSON → 重试后同一工具成功，3 次模型调用后返回 `[query_failed, grounded]`。因此阶段 A 有真实问题需要解决，不能取消。

证据 E2 同时证明了推荐修复的回归：

1. 第一次模型调用查询 7 月 30 日入住、7 月 31 日退房，工具成功。
2. 第二次模型调用抛出合成 `RuntimeError`，经过真实通用异常分支进入第二次尝试。
3. 第三次模型调用查询 8 月 1 日入住、8 月 2 日退房，工具成功；共享预算耗尽，返回查询回执。

| 运行版本 | 回执中的成功事实 |
| --- | --- |
| 当前源码 | 7 月 30 日与 8 月 1 日两组 |
| 仅在尝试开头清空 `tool_parts` 的进程内投影 | 只剩 8 月 1 日一组 |

这超出了 Spec 已披露的“首次失败提示不再展示”取舍：被删除的是已经查到的成功事实。

**最小修订**：重新定义重试时成功分项、失败分项与缓存回执各自的生命周期。建议仅清理首次尝试遗留的失败分项，保留已成功查询事实及现有共享预算；如果选择改变 SDK 异常的回退行为，应另列行为变更并确认。不要为修补这一点在阶段 B 中顺带改异常策略。

**验收**：原“先失败后成功”用例转绿；新增“成功 A → SDK 异常 → 成功 B”控制，确认两组成功事实都保留。查询参数、顺序与预算继续相同。

### SP-R2 / P2：`model_dump()` 会漏掉真实调用方使用的返回字段

**位置**：Spec §2.2 第 154 行、§2.5、§3.2；`deepseek_client.py::AssistantDecision` 第 444–450 行、`DeepSeekGuestAssistant._referral_candidates` 第 4099–4115 行；`src/homestay_bot/services/reply_plan.py::ReplyPart.gap_unit` 第 34 行。

`AssistantDecision.turn_plan`、`gap_units`、`referral_candidates` 均为 `exclude=True`；分项中的 `ReplyPart.gap_unit` 也排除序列化。因此只比较 `AssistantDecision.model_dump()`，不能实现交接报告声称的“全部字段不变”。

证据 E3 构造了两个合法决定：模型选择相同，客人正文相同，只删除一个决定的 `gap_units` 和分项 `gap_unit`。两份 dump 完全相同，但真实 `_referral_candidates` 结果从 1 条变为 0 条。

调用路径：`respond` → `_referral_candidates` → `ConversationService._process_model_reply_body` → `_refer_gaps`。会话层在 `src/homestay_bot/services/conversation_service.py` 第 1950 行消费 `decision.referral_candidates`，第 1953 行按 `part.gap_unit` 删除已转交的未确认分项。丢字段会改变管家转交和最终回复，不能视为内部实现细节。

**最小修订**：除常规 dump 外，快照显式保存并比较上述 3 个本地字段和每条分项的 `gap_unit`；保留顺序、原因、房源、计划来源身份等语义。不要通过修改生产模型的 `exclude=True` 来方便对照。

**验收**：对照工具必须能检出“清空 `gap_units`”“清空 `referral_candidates`”“清空分项 `gap_unit`”这些进程内变异；保留一个有效转交和一个禁止转交的控制。已有单测继续通过不能替代此检查。

### SP-R3 / P2：E2 的无副作用证明没有覆盖异常行为

**位置**：Spec §2.2 E2（第 163 行）、B5；`deepseek_client.py::DeepSeekGuestAssistant._static_evidence_plan` 第 3270 行、`_respond_with_plan` 第 4626–4641 行；`src/homestay_bot/services/knowledge_evidence_policy.py::build_evidence_plan` 第 660–670、710 行附近。

已读调用链中没有发现 E2 所担心的日志、缓存或实例写入，但“没有这些副作用”仍不足以证明可以删除一次调用：计算本身可能抛异常，而 §2.2 同时要求异常行为不变。

证据 E4 使用原话：`2026年10月10日入住2026年10月10日退房；武汉经典景点推荐`，并传入经过 `verify_turn_plan` 核验的合成 `external_info` 计划。该计划只证明协议可接受，不表示模型分类语义正确。整轮日期解析得到同日入住和退房，减去退房日后形成空区间；`build_evidence_plan` 没有生成任何期间计划，随后读取 `plans[0]` 抛出 `IndexError`。

| 运行版本 | 观察 |
| --- | --- |
| 当前源码 | 抛出 `IndexError`，主模型调用 0 次 |
| 仅把 E2 计算移入 `plan_ok is None` 条件的进程内投影 | 返回决定，主模型调用 1 次 |

这是当前异常处理缺口的证据，也是 E2 会改变行为的证据；不是未来重构已实现或生产事故的证明。

**最小修订**：本轮阶段 B 保留“先算后丢”，把 E2 从允许改写中移除。日期异常若要修复，单独定义期望行为、确认并验证。这样无需扩大本次重构范围，也不要求将旧异常当作长期正确设计。

**验收**：B 的对照中保留这一输入，验证主模型调用次数和异常仍与 B 基线相同；若另行接受异常修复，则更新基线和 Spec，不能继续称该增量为等价搬移。

### SP-C1 / P3：固定 `today` 尚不能让证据时间快照稳定

**位置**：Spec §2.5、§3.2 第 297 行；`deepseek_client.py::DeepSeekGuestAssistant._tool_reply_parts` 第 3985 行；`knowledge_evidence_policy.py::_knowledge_part` 第 546 行。

Spec 只要求归零耗时；两条事实证据构造路径还会调用 `datetime.now(UTC)` 产生 `fetched_at`。证据 E5 在没有任何源码变化、固定业务日期的情况下连续运行同一工具场景，两个 dump 已因 `fetched_at` 不同而不相等。这会制造持续的假差异。

**最小修订**：在临时对照装配中冻结相关证据时钟，或准确列出仅允许归一化的采集时间字段；不要忽略整个 evidence 对象。来源编号、房间、目标日期、条件和分项顺序仍必须逐项比较。并发搜索若记录完成事件，使用可控调度与固定响应，避免把调度噪声当成语义变化。

## 2. 对交接报告七项问题的回答

| 交接关注点 | 本次结论 |
| --- | --- |
| 1. 现状证据 | 当前 `_respond_with_plan` 确为第 4117–4994 行、878 行；AST 统计为 59 个 `if`、14 个 `return`、10 个 `await`，3 个嵌套函数和 8 处 `finish` 调用与报告一致。源码与交接哈希一致；实施前仍须在进行中改动稳定后复核。本轮没有重新统计 Git 历史中的 26/11 提交数量。 |
| 2. 缺陷 A | 原路径成立；无计划时 `_tool_targets` 返回 `None`，`_tool_rooms_for` 返回 `RoomResolution("none")` 对象，虽然其状态为 none，`is not None` 判断仍成立，失败分项会追加。清空方案有 SP-R1 回归，D3-1 需要重定。 |
| 3. E1–E3 | `target_end_date` 的写入都在 S1，E1 的赋值前提成立；E2 需按 SP-R3 删除或另立行为变更。E3 的纯搬移方向可接受，实际字节相同仍须在实施对照中证明。 |
| 4. 结构 | 3 个阶段数据类加循环状态、留在本文件的方向没有发现独立阻断；12 个符号的数量本身不是问题。`_LoopState` 的生命周期必须先解决 SP-R1；不应为 60/150 行目标引入额外跳转或重排语义。 |
| 5. 离线对照 | 复用门禁的真实知识服务和假业务数据可作基础；记录型知识包装须委托原服务，不能另写检索逻辑。补上 SP-R2、SP-C1；5 类脚本还须包含 SDK 异常和成功/失败组合。分支命中只证明走到，不等于该分支的数据与副作用都相同。 |
| 6. 验证范围 | 影响保持在已界定的助手流程、未改装配/依赖时，不跑本地全量而使用相关回归及当前推送 CI 的理由成立；不能由本报告预先保证未来改动仍满足条件。A/B 的公共回复改动按项目规则各自判断有效的 all 门禁证据，需当前授权。罕见失败路径不做测试号收发的理由可接受，但“行为不变已证明”须等修订后的对照通过才成立。 |
| 7. AOCI | 允许待进行中内容达到稳定状态后整体维护；“已提交”不是 AOCI 对齐的技术前提。不能绕过工具要求的 Evidence/Review，也不能把旧索引当成当前源码事实。此项不构成对阶段 A/B 的实施或发布授权。 |

## 3. 证据与验证范围

| Evidence | 方法 | 结果 / 所支持的结论 |
| --- | --- | --- |
| E1 | 真实助手，脚本化模型与工具；先失败、非法 JSON、后成功 | 现状保留失败与成功分项，原疑点成立 |
| E2 | 当前助手与仅加入清空语句的内存投影成对比较 | 成功 A → SDK 类异常 → 成功 B 时，投影丢失 A；支持 SP-R1 |
| E3 | 合法决定对象变异，调用真实 `_referral_candidates` | dump 相同、候选 1 → 0；支持 SP-R2 |
| E4 | 合法协议的合成计划，当前助手与 E2 条件化计算的内存投影比较 | `IndexError`/0 次模型调用 → 返回/1 次；支持 SP-R3 |
| E5 | 同源码连续运行固定业务日的房态回执 | `fetched_at` 不同、dump 不同；支持 SP-C1 |
| E6 | 下列 8 个相关测试文件 | 369 passed in 14.00s；是当前相关回归，不是这些新增反例的修复验收 |

E6 命令：

```bash
.venv/bin/python -m pytest -q tests/unit/test_deepseek_client.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_live_price_scope.py tests/unit/test_reply_fix2_items.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_property_lorebook.py tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_answer_policy.py
```

`git diff --check` 通过。新增本报告不改变业务行为，复用上轮已完成的 E1–E6 证据；写报告时重新核对 Spec 和助手源码哈希相同，没有重复运行业务测试。

报告成稿后，另执行了一次 §5 合并复现入口的可运行性校验：E1–E4 断言通过；E5 观察到同源码快照因 `fetched_at` 不同而不相等。命令退出 0，只证明反例成立。文档内本地链接、尾随空白和换行检查通过。

没有运行真实 DeepSeek/JEV/Hostex/企业微信，没有生产读写、真实收件、SDK 网络故障注入或完整重构后对照。`RuntimeError` 只用于触发与 SDK 异常相同的通用捕获分支；它不证明真实网络故障发生率。没有验证尚未实现的 B1–B6。

## 4. 审查快照与接手顺序

基线为 Git HEAD `564546ca0d3e558c7839e69d1211690372fc431e` 加当前工作区；助手文件与部分测试有既有未提交修改。报告新增不接管这些改动，不整文件回退。

| 文件 | SHA-256 |
| --- | --- |
| `docs/specs/2026-10-20_respond-with-plan-split-spec.md` | `f09a6397f4f1cdcacfb9cb31c7ef2a7af95ce5ef59542fd3b80659aac44f47b6` |
| `docs/reviews/2026-10-20_claude-to-codex-respond-with-plan-split-spec-handoff.md` | `d516163375b645c602daedc0dab700bc15e51ed3e14fd08c15227ff6b5a8f53b` |
| `src/homestay_bot/integrations/deepseek_client.py` | `11ae99f179d5b53ca07dde018534b6c2b610dc44939b2c6021df40d01a92a2d6` |
| `src/homestay_bot/services/knowledge_evidence_policy.py` | `2ddb865d59131471d268e670544f211b4f5f03bb01f848c807cb9f8f5571a9f6` |
| `src/homestay_bot/services/reply_plan.py` | `61172b8039928ced0188d190cdbf8747cfe9a85f869ac32ce2c709f8b19a30f0` |
| `src/homestay_bot/services/conversation_service.py` | `b380c3ffb782d3ef55235c63af65133a2a97adfd578dcf5a85a344d2da8c439c` |
| `src/homestay_bot/tools/reply_regression.py` | `553abfe1bd3e544c3640b679d6ce598a1eb6a5641846f877c3bc10e62330542e` |
| `src/homestay_bot/services/model_budget.py` | `c7e883a152f913fd2dc63beba341f00fdc8df48f84ff8253eb5fdf969e26ead2` |
| `tests/unit/test_deepseek_client.py` | `2e8405c8b388d1e2cf679a04c0df13671c1f3dc0d6e8e985e66da75dc990d2dd` |

建议接手顺序：先独立复核 SP-R1/SP-R3 的调用路径；修订 D3-1 与 E2；补全返回字段和时钟对照合同；更新阶段 A/B 的控制矩阵。只修改受影响的 Spec 段落并提交给用户重新确认，未收到“开始”前不改业务代码。

## 5. E1–E5 离线复现入口

在本仓库根目录执行下列命令。只导入本地测试替身，模型和工具响应均为合成数据，不访问外部服务、不修改文件。`Projected` 与 `E2Projected` 仅是进程内 Spec 投影；正常退出表示反例与控制成立，不表示已修复。

```bash
PYTHONPATH=src:. .venv/bin/python - <<'PY'
import ast
import asyncio
import copy
import inspect
import json
import textwrap
from datetime import date
from types import SimpleNamespace as NS

import homestay_bot.integrations.deepseek_client as m
from homestay_bot.domain.enums import Language
from homestay_bot.services.item_coverage import GapUnit
from homestay_bot.services.reply_plan import ReplyPart
from tests.plan_helpers import plan_for
from tests.unit.test_deepseek_client import (
    ChatClientStub, InvalidToolResultClientStub, KnowledgeStub,
    ToolExecutorStub, TourismStub, decision_payload,
)


def projected_method(kind):
    """只投影一处 Spec 改写，保留真实助手的其他逻辑。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(
        m.DeepSeekGuestAssistant._respond_with_plan
    )))
    body = tree.body[0].body
    if kind == 'clear':
        loop = next(node for node in body if isinstance(node, ast.For))
        loop.body.insert(0, ast.parse('tool_parts = []').body[0])
    else:
        assignment = next(node for node in body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'evidence_plan'
                    for target in node.targets))
        assignment.value = ast.IfExp(
            test=ast.Compare(left=ast.Name(id='plan_ok', ctx=ast.Load()),
                             ops=[ast.Is()], comparators=[ast.Constant(value=None)]),
            body=assignment.value, orelse=ast.Constant(value=None),
        )
    namespace = dict(m.__dict__)
    exec(compile(ast.fix_missing_locations(tree), '<spec-projection>', 'exec'), namespace)
    return namespace['_respond_with_plan']


class Projected(m.DeepSeekGuestAssistant):
    """模拟尝试开始时清空全部工具分项。"""
    _respond_with_plan = projected_method('clear')


class E2Projected(m.DeepSeekGuestAssistant):
    """模拟只有计划不可用时才计算整句证据计划。"""
    _respond_with_plan = projected_method('conditional')


class Script:
    """按次提供模型应答，第二次分别制造协议或 SDK 类异常。"""
    def __init__(self, mode):
        """保存脚本类型及请求记录。"""
        self.mode, self.requests = mode, []

    async def create(self, **kwargs):
        """只生成本地响应，不创建供应商客户端。"""
        self.requests.append(copy.deepcopy(kwargs))
        number = len(self.requests)
        if number == 2:
            if self.mode == 'failed_then_success':
                return NS(choices=[NS(message=NS(content='invalid json', tool_calls=None))])
            raise RuntimeError('synthetic SDK failure')
        first_date = number == 1 or self.mode == 'failed_then_success'
        arguments = json.dumps({
            'check_in_date': '2026-07-30' if first_date else '2026-08-01',
            'check_out_date': '2026-07-31' if first_date else '2026-08-02',
        })
        call = NS(id=f'call-{number}', type='function',
                  function=NS(name='search_availability', arguments=arguments))
        message = NS(content=None, tool_calls=[call], model_dump=lambda **_: {
            'role': 'assistant', 'tool_calls': [{
                'id': call.id, 'type': 'function',
                'function': {'name': call.function.name, 'arguments': arguments},
            }],
        })
        return NS(choices=[NS(message=message)])


class Executor(ToolExecutorStub):
    """仅在原缺陷控制中使首次工具调用失败。"""
    def __init__(self, mode):
        """保存模式和调用次数。"""
        super().__init__()
        self.mode, self.number = mode, 0

    async def execute(self, name, arguments):
        """其余调用沿用按入参日期返回结果的本地工具替身。"""
        self.number += 1
        if self.mode == 'failed_then_success' and self.number == 1:
            raise RuntimeError('synthetic tool failure')
        return await super().execute(name, arguments)


def assistant(cls, client, executor=None):
    """固定业务日期，全部外部依赖均为测试替身。"""
    return cls(chat_client=client, tourism_searcher=TourismStub(),
               knowledge=KnowledgeStub(), model='offline', safety_hmac_key=b'offline',
               tool_executor=executor, local_date_provider=lambda: date(2026, 7, 30))


async def main():
    """依次打印并断言审查发现，异常细节不含真实配置或客户数据。"""
    for mode in ('failed_then_success', 'success_sdk_error_success'):
        statuses = []
        for cls in (m.DeepSeekGuestAssistant, Projected):
            script = Script(mode)
            obj = assistant(cls, NS(chat=NS(completions=script)), Executor(mode))
            question = ('查询2026年7月30日入住、7月31日退房的房态'
                if mode == 'failed_then_success' else
                '分别查询2026年7月30日入住、7月31日退房，'
                '以及2026年8月1日入住、8月2日退房的房态')
            result = await obj.respond(guest_identifier='offline', language=Language.ZH,
                messages=[{'role': 'user', 'content': question}])
            states = [part.status for part in result.reply_parts]
            statuses.append(states)
            print(mode, cls.__name__, len(script.requests), states, result.reply_text)
        assert statuses == ([['query_failed', 'grounded'], ['grounded']]
            if mode == 'failed_then_success' else [['grounded', 'grounded'], ['grounded']])

    original = m.AssistantDecision.model_validate(decision_payload()).model_copy(update={
        'reply_parts': [ReplyPart(question='早餐', status='missing', text='尚未确认',
                                 gap_unit='unit-1')],
        'gap_units': [GapUnit(unit_id='unit-1', item_id=1, text='早餐几点',
                              reason='knowledge_missing')],
        'gap_referrals': [m.GapReferral(unit_id='unit-1', canonical_question='早餐几点')],
    })
    changed = original.model_copy(update={'gap_units': [], 'reply_parts': [
        part.model_copy(update={'gap_unit': None}) for part in original.reply_parts]})
    counts = [len(m.DeepSeekGuestAssistant._referral_candidates(item))
              for item in (original, changed)]
    assert original.model_dump() == changed.model_dump() and counts == [1, 0]
    print('E3 dump_equal=True referral_counts=', counts)

    question = '2026年10月10日入住2026年10月10日退房；武汉经典景点推荐'
    plan = plan_for(question, ('external_info', question))
    assert plan.ok
    outcomes = []
    for cls in (m.DeepSeekGuestAssistant, E2Projected):
        client = ChatClientStub([json.dumps(decision_payload(), ensure_ascii=False)])
        try:
            await assistant(cls, client).respond(guest_identifier='offline',
                language=Language.ZH, messages=[{'role': 'user', 'content': question}],
                turn_plan=plan)
            outcome = 'returned'
        except Exception as error:
            outcome = type(error).__name__
        outcomes.append((outcome, len(client.chat.completions.requests)))
    assert outcomes == [('IndexError', 0), ('returned', 1)]
    print('E4', outcomes)

    results = []
    for _ in range(2):
        result = await assistant(m.DeepSeekGuestAssistant, InvalidToolResultClientStub(),
            ToolExecutorStub()).respond(guest_identifier='offline', language=Language.ZH,
                messages=[{'role': 'user', 'content': '当前房间预订状况'}])
        results.append(result.model_dump(mode='json'))
    print('E5 repeated_dump_equal=', results[0] == results[1])
    print('E5 fetched_at=', [item['reply_parts'][0]['evidence'][0]['fetched_at']
                             for item in results])


asyncio.run(main())
PY
```
