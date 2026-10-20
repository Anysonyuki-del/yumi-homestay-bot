# Codex → Claude：2026-10-20～21 会话总交接审查

- 日期：2026-10-21。
- 审查对象：[Claude 会话总交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-21_claude-to-codex-session-handoff.md)。
- 当前请求：审查交接报告，并将审查结果写成报告，供用户交回 Claude。
- 授权边界：本次只新增本报告；没有修复业务代码、提交、推送、部署、调用真实模型、发送消息或清理实际记忆库。本报告也不授予这些操作的权限。

## 1. 结论

发现 **2 项 P2、2 项 P3**。发布提交、门禁文件和 CI 的主要记录能够对上，但“行为完全不变”“还原真实规划”和插件源码改动影响运行观察器等结论需要修订。

| 编号 | 优先级 | 发现 | 证据 |
| --- | --- | --- | --- |
| SH-R1 | P2 | 回执构造异常时，回退状态的更新时机发生变化，不能声称所有异常路径等价 | E1，附录 A |
| SH-R2 | P2 | 门禁保存的是规划摘要，不能据此还原完整真实规划；6487 次对照的证明范围需要收窄 | E2，附录 B |
| SH-C1 | P3 | 运行中的 worker 使用安装缓存，源码 main 有未提交改动不能直接推出运行态受影响 | E3 |
| SH-C2 | P3 | 21 个分支包括 main；其他已合并分支为 19 个，不是 20 个 | E4 |

SH-R1 的异常由本地故障注入触发，未证明生产自然触发；它首先是对“行为不变”验收合同的反例，不能写成已证实的生产事故。旧版在该控制下返回空回执，新版恢复出房态；也不能为了形式等价而未经判断恢复旧版空回复。

## 2. 基线与证据身份

| 对象 | 本次核对值 |
| --- | --- |
| 仓库 | `/Volumes/02/code/homestay-bot` |
| HEAD / 本地 origin/main | `863da17d711580444099b032b15b7fc1c532def0` |
| 旧版对照 | `v1.70.9` / `b4c6929367965a1310a28af7e6e9166208f51e9c` 的 Git 对象源码 |
| 1.70.10 正式提交 | `aced31066c5682321f6c7f59fd72cf53be9d10d5` |
| 被审查报告 SHA-256 | `adc61cf22864d7b36152cead545caf5281f4c2b4f1ca326c63621d02e6d03f01` |
| `deepseek_client.py` SHA-256 | `1ac8f42affe3df0c0a2d11eb61ced225188f9425279a1fcfda8456b89ef6bddc` |
| 拆分 Spec SHA-256 | `3e51b2884461c3276a566e5c38a31ad977c2e161bc2839e0db076221531f6f00` |

业务源码在审查与报告整理之间未变化。测试和复现结果来自紧接本报告之前的同一轮审查，文档整理不重复运行业务测试。插件仓库仍有并行工作，其状态仅代表核对时快照。

## 3. 需要修订的结论

### SH-R1：回执构造从逐步更新状态变成成功后一次赋值

- 状态：已验证的离线故障注入差异；置信度高。
- 位置：交接报告 §2.7（第 79–91 行）；`deepseek_client.py::DeepSeekGuestAssistant._run_tool_calls` 第 5075–5078 行、`_availability_receipt`、`_run_main_loop`。
- 证据：E1、附录 A。

v1.70.9 在工具执行成功后，先把初始 `AssistantDecision` 写入 `availability_fallback`，再逐步应用静态证据和组合分项。后面的构造步骤抛出 `ValueError` 时，外层异常分支已经能看到这个回执。

当前版通过 `state.availability_fallback = self._availability_receipt(...)` 赋值。Python 会先完整求出右侧结果；辅助方法中途抛出异常时，这次赋值尚未发生，外层仍看到原来的状态。首次回执构造失败时，原状态是 `None`，于是进入重试。

调用路径 P1：`respond → _respond_with_plan → _run_main_loop → _run_tool_calls → _availability_receipt → _compose_items`；异常回到 `_run_main_loop` 后，两版因可见回退状态不同而分流。

| 控制 | v1.70.9 | 当前源码 |
| --- | --- | --- |
| 不注入异常 | 返回相同房态正文；2 次模型调用 | 返回相同房态正文；2 次模型调用 |
| 首次 `_compose_items` 抛一次合成 `ValueError` | 返回空正文、0 个分项；1 次模型调用 | 重试后返回房态、1 个分项；2 次模型调用 |

这证明异常出口、模型调用次数及日志存在差异，没有证明常规输入会自然触发该异常，也没有证明新版在这个样本中更差。原离线对照没有覆盖这一故障位置，因此“全部异常路径不变”不能沿用。

**最小处理要求**：先复核并选择明确语义。若仍坚持严格行为不变，需要评估回退状态更新的原时机；若接受“首次回执构造失败后继续重试”，应将它列为明确行为变化，修订 Spec、交接和发布记录，并覆盖已有回执、无回执及预算耗尽等直接相关边界。不能把本报告当作修复或恢复旧版空回复的授权。

### SH-R2：规划摘要不能还原完整真实规划

- 状态：摘要信息损失及请求差异已离线验证；6487 次执行本身未独立复跑。
- 位置：交接报告 §2.7 第 87 行、§6 第 1 条；`docs/releases/1.70.10.md` 的“失败分析”；拆分 Spec §4.8。
- 源码依据：`reply_regression.py::plan_summary`（第 533 行）；`turn_plan.py::PlanItem`、`verify_turn_plan`；`deepseek_client.py::_build_main_request`。
- 证据：E2、附录 B。

门禁摘要保存 `id`、`kind`、`risk`、`subject_len`、`start`、`quote_len`、`valid`。已知夹具原文可以按区间恢复摘录，但摘要不能恢复原始 `subject`、归一化 `question`、`target_room`、入住退房日期和 `withdraws`。

附录 B 用同一句虚构问题构造两个经过 `verify_turn_plan` 核验的计划，只改变归一化 `question`：两个 `plan_summary` 完全相同，`_build_main_request` 产生的主模型请求却不同。因此，同一个摘要并不唯一确定实际消费的计划或模型请求。

调用路径 P2：`verify_turn_plan → PlanOutcome → plan_summary` 丢弃部分字段；主回复路径继续消费完整 `PlanItem`，无法从摘要逆推出唯一原输入。

**最小文档修订**：将“还原出 175 种真实规划”改为“根据真实门禁的事项类型、风险与摘录区间重建 175 个计划变体”。注明补填字段的规则和未保留字段。将“模型输出相同时，行为完全一致”限定为“在所用重建计划、固定模型应答脚本及装配范围内，作者报告未观察到差异”。

历史失败与这些对照可以支持“倾向既有不稳定”的判断，但不能单独证明本次失败必然由模型波动引起。SH-R1 又提供了对照之外的实际控制流差异。用户已接受发布例外这一历史记录无需改写成门禁通过；需要修订的是证明强度与剩余不确定性。

本项不要求扩大记录真实客人原文，也不要求未经确认把临时脚本改造成新的正式工具。

### SH-C1：先查运行入口，再判断源码工作区的运行风险

- 状态：本机只读核对；置信度高。
- 位置：交接报告 §4.4 第 186 行及 §5 第 2 条。
- 证据：E3。

核对时 LaunchAgent 的启动脚本是：

```text
/Users/rin/.codex/plugins/cache/local/codex-observational-memory/0.17.5/scripts/run-worker
```

运行中 worker 的入口也位于该缓存的 `dist/src/worker.js`，不是 `/Volumes/02/code/codex-observational-memory/src/worker.ts`。

源码依据：插件仓库 `scripts/install.mjs::launchAgentXml`（第 211 行）明确生成指向已安装缓存的 LaunchAgent；`scripts/run-worker` 执行其所在目录对应的编译产物。该调用路径 P3 是“安装缓存脚本 → 缓存 dist → worker”。

**最小修订**：说明源码未提交改动本身不等于运行观察器已经加载这些改动。实际风险取决于是否构建、同步或安装缓存，以及进程何时重启或加载模块。隔离 Git 分支有助于管理源码，但不能替代实际记忆库写入的维护窗口和授权边界。

本次没有完整审查插件修复实现、所有 MCP/Hook 进程或清理计划，也没有确认实际口令、密钥已更换。

### SH-C2：排除 main 后再列可清理分支

- 状态：Git 只读核对；置信度高。
- 位置：交接报告 §4.2 第 152–157 行及 §5 第 6 条。
- 证据：E4。

`refs/heads` 共 21 个，其中包括 main；`git branch --merged main` 返回 20 个，其中也包括 main。另一个未合并分支是 `codex/guest-assistant-boundaries`。所以“其他已合并分支”应为 **19 个**。

**最小修订**：明确保留 main，将 19 个其他已合并分支与一个未合并快照分支分别列出。分支提交已合并，只证明提交祖先关系；两个 Claude 工作树和 `continue-verify-20260731` 工作树仍有未提交改动，不能由分支已合并推断工作树内容可以删除。

本次没有删除分支或工作树，也没有完成这些遗留改动的价值审查。

## 4. 已确认的记录与证据

| 证据 | 核对方式 | 结果与边界 |
| --- | --- | --- |
| E1 | 从 `v1.70.9` Git 对象载入旧模块，与当前模块运行同一离线控制；见附录 A | 正常控制一致；首次回执组合异常控制出现差异。没有外部调用 |
| E2 | 两个合法计划比较摘要与实际主模型请求；见附录 B | `both_verified=True`、`same_summary=True`、`same_request=False` |
| E3 | 只读取 LaunchAgent 的 `ProgramArguments`、缓存包版本和 worker 的入口参数 | worker 指向安装缓存 0.17.5；没有读取密钥值、进程环境或原始记忆库 |
| E4 | `git for-each-ref`、`git branch --merged main`、`git worktree list --porcelain`、各工作树 `git status` | 21 个分支、19 个其他已合并分支；7 个工作树及已列出的未提交改动基本吻合 |
| E5 | 本轮复跑拆分 Spec §3.1 的 8 个相关测试文件 | **373 passed in 17.84s**；不是全仓测试，也不覆盖 E1 的新增故障控制 |
| E6 | 逐份核对 `.stage/reply-gate-rc-1.70.{8,9,10}-1.json` 的摘要和 verdict | SHA-256 与三份发布记录一致；首轮分别 146/145/144；1.70.10 仅 SR-遗失构成退步，结果失败、通过、失败 |
| E7 | 对三个候选标签与正式标签执行运行文件范围的 `git diff --name-only` | 三组 src/tests/scripts/migrations/pyproject/锁文件/Dockerfile/compose 差异均为空 |
| E8 | 通过 GitHub API 读取报告列出的 6 个 Actions run | 全部 completed/success，head SHA 分别对应三个正式版本；没有重跑 CI |

E5 的命令：

```bash
cd /Volumes/02/code/homestay-bot
.venv/bin/python -m pytest -q tests/unit/test_deepseek_client.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_live_price_scope.py tests/unit/test_reply_fix2_items.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_property_lorebook.py tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_answer_policy.py
```

E8 的 run ID：1.70.8 为 `39439145994`、`39439166959`；1.70.9 为 `39475097184`、`39475101719`；1.70.10 为 `39486526754`、`39486532401`。独立核对结论只限 CI 状态和提交身份，没有把 CI 当作生产或收件证据。

1.70.9 的 `_run_tool_calls` 查询身份仍按工具名和周末展开后的完整参数组生成，成功后按对象身份删除对应失败分项，并在重建回执前完成。`_LoopState` 在 `_run_main_loop` 内创建、位于两次尝试循环外，保留同轮跨重试状态；没有发现把它作为不同 `respond` 调用的共享实例使用。执行器非空检查在调用方仍保留，在现有装配下没有发现该附加检查改变正常路径。以上认可不覆盖 SH-R1 的回执异常赋值差异。

## 5. 请 Claude 返回的内容

1. 逐项复核 SH-R1、SH-R2、SH-C1、SH-C2；有异议时给出可复现反例或对应源码依据。
2. SH-R1 先给出验收语义与最小方案，不擅自恢复旧空回复或修改生产。
3. 修订“真实规划”和运行态风险的表述，保留原始门禁不通过及用户接受例外的事实。
4. 清理建议明确排除 main，保留所有未提交工作树内容，等待单独决定。
5. U4 同日入住退房的异常逃逸仍是已披露缺口，应按调用入口可达性评估优先级；不能仅因它早已存在就自动降为低优先级。本次没有为其增加新的生产复现结论。

不要求本轮重新跑全仓测试或真实模型门禁。若随后授权修改代码，再依据实际差异选择必要验证。

## 6. 未覆盖与外部边界

- 没有独立复跑作者的 4212/6487 次临时对照，不把作者汇总当作本次独立验收。
- 没有重新连接生产核验源码、容器、数据库、备份、登录页面或客人收件；三份发布记录中的这些内容属于历史现场证据。
- 没有读取原始记忆库、核实泄露凭据的现值或执行存量清理。报告不证明任何凭据已经轮换。
- 插件源码仍在并行变化，本次仅核对运行入口，不能视为插件代码或维护窗口的正式审查通过。
- AOCI 仍有既有治理未对齐项；报告不代替其人工范围裁决，也不改变用户此前等待文件去留决定的安排。

## 附录 A：回执构造异常的离线成对控制

在上述源码与测试基线执行。仅使用虚构问题、已有测试替身和 Git 对象；不启动应用生命周期、不调用真实模型或业务 API、不写业务库。旧模块只在本进程内载入，不改变工作树。

```bash
cd /Volumes/02/code/homestay-bot
.venv/bin/python - <<'PY'
import asyncio
import subprocess
import sys
import types
from datetime import date

from tests.plan_helpers import plan_for
from tests.unit.test_deepseek_client import (
    KnowledgeStub, TourismStub, ToolClientStub, ToolExecutorStub,
)
from homestay_bot.domain.enums import Language
import homestay_bot.integrations.deepseek_client as current

name = 'homestay_bot.integrations._readonly_review_v1709'
old = types.ModuleType(name)
sys.modules[name] = old
source = subprocess.check_output(
    ['git', 'show', 'v1.70.9:src/homestay_bot/integrations/deepseek_client.py'],
    text=True,
)
exec(compile(source, 'git:v1.70.9/deepseek_client.py', 'exec'), old.__dict__)


async def run(module, fault):
    """比较同一装配，仅在指定控制中注入一次回执组合异常。"""
    text = '今天还有房吗？'
    client = ToolClientStub()
    assistant = module.DeepSeekGuestAssistant(
        chat_client=client,
        tourism_searcher=TourismStub(),
        knowledge=KnowledgeStub(),
        model='offline',
        safety_hmac_key=b'test',
        tool_executor=ToolExecutorStub(),
        local_date_provider=lambda: date(2026, 7, 30),
    )
    if fault:
        original = assistant._compose_items
        calls = 0

        def once(*args, **kwargs):
            """只影响首次组合，保留后续重试能够恢复的控制条件。"""
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError('synthetic_receipt_fault')
            return original(*args, **kwargs)

        assistant._compose_items = once
    decision = await assistant.respond(
        guest_identifier='offline-review',
        language=Language.ZH,
        messages=[{'role': 'user', 'content': text}],
        turn_plan=plan_for(text, ('stay_query', text)),
    )
    return {
        'reply': decision.reply_text,
        'parts': len(decision.reply_parts),
        'model_calls': len(client.chat.completions.requests),
    }


async def main():
    """正常控制须一致；故障控制断言旧、新状态更新时机的可观察差异。"""
    before = await run(old, False)
    after = await run(current, False)
    assert before == after
    before_fault = await run(old, True)
    after_fault = await run(current, True)
    assert before_fault == {'reply': '', 'parts': 0, 'model_calls': 1}
    assert after_fault['reply'] and after_fault['parts'] == 1
    assert after_fault['model_calls'] == 2
    print({'normal_equal': True, 'old_fault': before_fault, 'new_fault': after_fault})


asyncio.run(main())
PY
```

退出 0 表示这里描述的差异仍存在，不表示修复已经完成。后续实现改变后应重新定义期望行为，不能为了让该历史探针通过而恢复旧行为。

## 附录 B：相同规划摘要不能唯一确定模型请求

```bash
cd /Volumes/02/code/homestay-bot
.venv/bin/python - <<'PY'
import asyncio
from datetime import date
from types import SimpleNamespace

from tests.plan_helpers import plan_for
from tests.unit.test_deepseek_client import KnowledgeStub, TourismStub
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.tools.reply_regression import plan_summary


async def main():
    """用合法计划证明摘要相同仍可产生不同请求；不调用模型客户端。"""
    text = '早餐几点，收费吗？'
    plans = [
        plan_for(text, ('static_fact', text, {'subject': '早餐', 'question': question}))
        for question in ['早餐几点？', '早餐收费吗？']
    ]
    requests = []
    for plan in plans:
        assistant = DeepSeekGuestAssistant(
            chat_client=SimpleNamespace(),
            tourism_searcher=TourismStub(),
            knowledge=KnowledgeStub(),
            model='offline',
            safety_hmac_key=b'test',
            local_date_provider=lambda: date(2026, 10, 7),
        )
        messages = [{'role': 'user', 'content': text}]
        context = assistant._prepare_turn(
            language=Language.ZH, messages=messages,
            customer_context=None, request_context=None, guest_history=None, plan=plan,
        )
        knowledge = await assistant._load_turn_knowledge(context, Language.ZH, None)
        request = await assistant._build_main_request(
            context, knowledge, [], language=Language.ZH, messages=messages,
            customer_context=None, request_context=None, stage_timing_sink=None,
        )
        requests.append(request.request)
    assert all(plan.ok for plan in plans)
    assert plan_summary(plans[0], text) == plan_summary(plans[1], text)
    assert requests[0] != requests[1]
    print({'both_verified': True, 'same_summary': True, 'same_request': False})


asyncio.run(main())
PY
```
