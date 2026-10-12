# Codex → Claude：回复泛用化第二轮修复 Spec v4 复审报告

- 日期：2026-10-08。作者：Codex。
- 审查对象：[Claude v4 返回稿](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v4-review-handoff.md)和[修复 Spec v4](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
- 对照：[上一轮 v3 审查报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v3-review-handoff.md)。
- 本地 HEAD：`80f9b2c309547c36c15cfd142d5dd30b98d87406`。本轮重新核对 Spec、返回稿和相关源码摘要，绑定见 §7。
- 本轮仅审查、离线探针和报告写作。没有修改业务代码、测试或 Spec，没有连接数据库、生产或真实 DeepSeek/Hostex/企业微信，没有提交、推送、部署或发送外部消息。
- 返回稿记录 D-G9、D-G10 的选择，本报告审查其技术可行性；该叙述不授予本会话实施或外部操作权限。

## 1. 结论

**v4 主要修订认可，暂不通过：还有 1 项 P2，粗粒度项的唯一房源/日期不能自动成为每个小句的回答范围。** 需在现有逐项覆盖契约中补小句与目标范围的一致性检查，无需新建规划器、实体框架或通用语法解析器。

| 上轮问题 | v4 修订及本轮判断 |
| --- | --- |
| V3-R1：房态或参考价存在即覆盖整项 | §2.1(2)改为房价、房态分别要求对应分项，全部小句和事实都回答才覆盖；正常出口与 availability_fallback 同约束。类型混用根因在设计层闭合，但房源/日期仍写成“该项”，见 R1 |
| V3-R2：目录用主题为空清掉未知设施 | D-G9 要目录正向命中且删去目录词、形式词后无剩余；“看江景”保留缺口。接受设计修订 |
| C1：D-G8 仍会误拦不同楼层等平时答案 | §2.3 明确是保守否决，F5 在此子范围未消除。接受已声明取舍，不再当作未承认的修复承诺 |
| C2：不同标准问法的同对象冲突仍可能漏检 | §2.3 明确包括两条都被选中的情况。接受已声明上限；模型选择不证明对象身份 |
| 窗口二只手工完成作业 | §2.4 改为真实 Worker.run_once、独立队列/业务会话、提交后取消与 recover_stale。方案接受；本轮未运行 PostgreSQL 验收 |

“设计层闭合”不表示实现或生产已修复。当前业务源码未变，本报告不能作为新版本验收或上线证明。

## 2. B24-V4-R1 · P2：小句覆盖继承单个 PlanItem 的目标，仍可能把其他房间或日期算成已回答

**Spec 依据。** [§2.1(2)第 98–103 行](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:98)按相交小句判断所需事实，但房价/房态分项只要求“该项房源、日期相符”。[逐项候选第 82 行](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:82)同样只绑定该项目标。一个项含多个明确目标时，两者都没有规定哪些小句不能继承这个单值目标。

**源码依据。**

- [turn_plan.py：PlanItem](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:45)只有一个 target_room 和一对入住/退房日期。
- [verify_turn_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:176)核验摘录、类型、位置等；第 218 行附近的房号核验只要求房号出现在原文，并不拒绝一个摘录同时含 401、402 或两个入住目标。
- [deepseek_client.py：_tool_reply_parts](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2362)从实际工具行生成文字；第 2477 行附近的 ReplyEvidence 绑定该行 property_id、日期。它已经保留精确范围，但一行 401 的房态不能作 402 或另一晚的证据。
- [_merge_item_knowledge](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1451)按单个 item.target_room 查房源；[_item_dates](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1443)同样返回一组目标。新增小句覆盖不能把该组范围无条件扩大。

**E-V4-1 / E-V4-2：真实计划核验与真实工具分项，加 v4 按项目标条件的代理。**

| 客人原问，均核验为一个有效 stay_query | 项目标 | 真实工具正文 | 只按项目标匹配的代理 |
| --- | --- | --- | --- |
| 10月10日401有房吗，402有房吗 | 401，10月10日入住/11日退房 | 10月10日入住、10月11日退房；可订：401 | 两个小句均 true，但 402 未回答 |
| 10月10日401有房吗，10月11日401有房吗 | 同上 | 同上 | 两个小句均 true，但 11 日入住未回答 |

两个反例都通过真实 verify_turn_plan；没有用无效摘录、未知房号或真实接口制造条件。工具正文明确只说 401 的 10 日这一晚。第二例中“11 日退房”也不能当成“11 日入住可订”。

**证据边界。** 这证明 v4 明文所给的按项目标条件不足，属于契约反例；不是新版覆盖器或新版最终客人正文的动态复现。实际工具文字与证据是当前函数产物。若实现另有小句范围核验，该保护需写入 Spec 并给出行为断言，不能依靠“规划器应该会拆开”。

**最小修订方案。**

1. 在现有一次切句、保留位置的结果上，把“事实种类”与“目标范围”一起核对。小句明确点名的房号/日期不得被项的另一个目标覆盖。复用现有房号匹配、房源映射、日期识别和实际调用参数；不修改 PlanItem 对外结构，不追加规划或检索调用。
2. 小句没有独立目标、且与项目标没有矛盾时，才继承已验证的项目标。含多个明确目标、目标互相冲突或无法可靠绑定时，不证明全部覆盖；现有工具结果照常保留，未绑定部分回针对该范围的未确认。
3. 剩余事实走静态证据时也遵守此范围限制：402 小句不能使用 401 的合法候选；另一入住日期不能使用项内另一日期的知识。当前预算内没有该范围候选就未确认，不借整轮池，也不为修复放宽四项/四条/3000 字预算。
4. 把这一判断放在正常 JSON、工具后失败回执和日期澄清共用的内部覆盖位置；不能只拦工具文字而仍清掉缺口标记。

**最低验收。** 上述两例强制使用一个粗粒度项，只有 401/10 日的结果时保留 402/11 日缺口；细拆成正确两项且各有结果时不误拦。加同一小句多目标、一个房号映射不到、一个工具失败对照。断言最终正文、来源范围和剩余问题，不只断言工具调用或 covered=true。

## 3. 四个重点问题的答复与实施约束

### 3.1 D-G9 形式词：方向接受，英文用完整词边界，最便宜走房价

[返回稿第 34 行](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v4-review-handoff.md:34)列出的代词、疑问及语气形式词可作为窄清单。英文按完整单词/短语边界删除，中文长短语先后明确；不得把领域词、否定、数值或事实限定当作形式词剥掉。漏收普通形式词允许保守未确认，不能改成“主题、属性没命中就算纯目录”。

“哪种房间最便宜”中的“最便宜”不是形式词。当前 [asks_room_price](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:243)确实能识别它；由房价流程接管合理。无日期先澄清；有日期也必须有实际参考价结果，目录本身不能证明最低价。当前 [_tool_reply_parts 的最低价分支](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2382)要求完整逐夜价格并保留不完整提示，实施不得丢掉这个约束。

### 3.2 无逗号的价格加房态：两类结果必要，仍需验证范围与未知剩余

“401住一晚多少钱还有房吗”要求房价和房态各有结果，接受 v4 的“可多类”。但布尔识别只证明包含某类请求，不能证明该小句只有这些请求。

**E-V4-C1。** 当前切句规则把“10月10日401房间住一晚多少钱而且能看江景吗”保持为一个小句；真实识别结果为 price=true、availability=false、topics=[]、asked_attributes={fee}。“看江景”没有变成额外主题或属性。实际 [asks_room_price](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:243)、[asked_attributes](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:340)都只作有限模式识别。

v4 [第 102 行](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:102)已经明确禁止用“没信号”证明没有剩余，因此这里**不重复登记为新的 P2**。实施必须把这条限制落实：不能只枚举识别到的事实然后 all()。无法证明未知剩余已回答时保留该缺口；不补“江景”词表解决一个例子。

第 104 行宜表述为“不对已覆盖的同一事实再发未确认”。同一小句问价格和江景时，价格结果加江景未确认是合法部分覆盖；不能以避免重复为由清掉江景问题。

### 3.3 D-G10：在当前修复范围内，复用出口但保留原有有效日期来源

当前 [_price_question_needs_dates](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1732)先判断房价，房态无日期不会命中。[房价提前返回与 finish](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2679)还有“全是 priced 项就早退”的分支。

**E-V4-C2。** 在真实 respond 中输入“401有房吗”和有效计划：房价追问 guard=false，主回复替身被调用一次，当前最终正文是替身提供的日期追问。只证明现状确实仍经过主调用，不证明真实模型必然这样回答，也不是 D-G10 已实施。

接受扩展现有澄清出口到房态，使用对应文案，不另建流程。实施检查正常组合和提前返回；粗粒度项还含其他本店问题时，不因所有项都叫 stay_query 或都命中房价就提前返回。已有合法日期上下文或实际查询参数时沿用现有契约，不能只看 item.check_in_date 为空就多追问。两种日期澄清都不能覆盖 R1 中另一个明确房号/日期，或 C1 中未知设施问题。

### 3.4 availability_fallback：可沿用模型回答项留空，不新增统一本店未确认

当前 [_respond_with_plan 的 availability_fallback](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3286)是独立回执对象，工具后领域异常或协议错误都可能走它；当前代码仍可能拼整轮证据计划，v4 已要求替换为逐项契约。

接受“需模型回答的项没有可用答案则留空”的现有取舍。工具成功/失败说明和本店事实的候选答案/未确认照常保留，不能发出“全部问题已回答”的完成判断。统一给普通旅游/通用项套本店未确认会改变已选行为，本轮没有必要。正常出口与失败出口仍需共同接受 R1、C1 的覆盖约束。

## 4. 泛用性判断与优化顺序

本轮问题不是缺几个客户关键词。模型做选择题限制了可选证据编号，但“选择合法”“摘录存在”“类型识别命中”分别不证明对象相同、目标相同或问题已答完。

保留 Claude 的逐项候选、一次切句、类型分项与 D-G9 无剩余方向；补上 R1 的目标一致性，落实 C1 已写明的未知剩余失败关闭。这样修改共用边界即可，优先级高于新增关键词、再加规划调用或放宽检索预算。若保守策略增加未确认，应记录真实漏答/误拦样本，再决定是否扩大能力；不能为了回复看起来完整而消除缺口。

## 5. 后续验证要求

| 行为 | 最低有判别力的验证 |
| --- | --- |
| 多房号/多入住日期粗粒度项 | R1 两例、正确细拆、缺一个结果、错误范围候选；断言最终正文与来源 |
| 一个小句价格加房态 | 两结果都成功、任一失败、无日期；缺一类时不能算全覆盖 |
| 已识别房价加未知限定 | C1 含/不含逗号对照；价格不能清掉江景未确认 |
| 纯目录与限定目录 | 中文/英文纯目录、江景、健身房、无障碍、最便宜；无剩余只证明目录请求 |
| 主模型在工具后失败 | 预算耗尽、协议失败两出口保留实际工具结果与剩余本店缺口 |
| 日期澄清 | 纯问价、纯房态、二者并问、混合未知设施、合法既有日期上下文；检查早退 |
| PostgreSQL 中断恢复 | 按 Spec §2.4 真实 Worker、真实队列仓储、业务会话、取消等待、第三会话与 recover_stale 精确计数 |

[Worker.run_once](/Volumes/02/code/homestay-bot/src/homestay_bot/worker.py:357)确实先 checkpoint 提交 RUNNING，再等待 handler，成功才 mark_completed；取消等待不走 except Exception 的普通重试分支。v4 的窗口二构造与此顺序相符。完整生产生命周期不是必要测试装配；手动标记仍只能作仓储对照。

本轮探针退出 0，四组输出见 §6；退出 0 表示反例/现状断言成立，不表示修复通过。既有 93 项相关回归未重复运行，三份相关测试文件及业务源码摘要未变；历史通过不证明 v4 新契约。未跑全量 pytest、真实模型门禁、PostgreSQL 或生产验收。文档收尾已校验 20 个本地文件链接、14 个摘要绑定与 1 段复现代码语法；空白检查通过。AOCI 收尾另行维护。

## 6. 可复现离线命令

从仓库根运行以下命令。只使用合成工具数据和现有本地测试替身，不创建数据库或真实外部请求。`item_bound_proxy`仅代理 §2.1 的按项目标条件，不代表新版实现。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/unit .venv/bin/python - <<'PY'
import asyncio
import json
import re
from datetime import date
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant as A
from homestay_bot.services.answer_policy import asks_room_price, asks_stay_availability
from homestay_bot.services.knowledge_evidence_policy import asked_attributes
from homestay_bot.services.knowledge_service import detect_property_topics
from homestay_bot.services.turn_plan import verify_turn_plan
from test_deepseek_turn_plan import _assistant, _decision

SPLIT = r"[，,。；;！？!?\n]|另外|以及|同时"
ARGS = {"check_in_date": "2026-10-10", "check_out_date": "2026-10-11"}
ROOM_ID = 1401

def plan_for(text, dated=True):
    """构造当前核验器接受的粗粒度项；只观察现状，不把有效摘录当作语义完整证明。"""
    entry = {"id": 1, "kind": "stay_query", "question": text,
             "quote": text, "start": 0, "target_room": "401", "risk": "none"}
    if dated:
        entry.update(ARGS)
    outcome = verify_turn_plan(json.dumps({"items": [entry]}, ensure_ascii=False),
                               text, today_provider=lambda: date(2026, 10, 8))
    assert outcome.status == "ok"
    return outcome

def availability(text):
    """生成真实工具回复分项；合成数据仅含 401 在 10 月 10 日这一晚的房态。"""
    rows = [{"property_id": ROOM_ID, "property_title": "401",
             **ARGS, "stay_available": True}]
    return A._tool_reply_parts("search_availability", ARGS, rows,
                              Language.ZH, "offline", question=text)

def item_bound_proxy(item, clause, parts):
    """只代理 v4 按项房源和日期匹配的条件，不实现新版覆盖器或断言最终回复。"""
    return asks_stay_availability(clause) and any(
        evidence.source_kind == "availability"
        and evidence.property_id == ROOM_ID
        and evidence.target_date == item.check_in_date
        and evidence.target_end_date == item.check_out_date
        for part in parts for evidence in part.evidence
    )

mixed = "10月10日401房间住一晚多少钱而且能看江景吗"
clauses = [s for s in re.split(SPLIT, mixed) if s]
assert len(clauses) == 1 and asks_room_price(mixed)
assert detect_property_topics(mixed) == [] and asked_attributes(mixed) == {"fee"}
print("E-V4-C1", json.dumps({"clauses": clauses, "price": asks_room_price(mixed),
    "availability": asks_stay_availability(mixed), "topics": [],
    "attributes": sorted(asked_attributes(mixed)),
    "limitation": "布尔命中和 fee 属性不能证明江景问题已回答；无剩余检查尚未实现"}, ensure_ascii=False))

for label, text, unanswered in [
    ("E-V4-1", "10月10日401有房吗，402有房吗", "402"),
    ("E-V4-2", "10月10日401有房吗，10月11日401有房吗", "10月11日入住"),
]:
    outcome = plan_for(text)
    item = outcome.plan.valid_items[0]
    parts = availability(text)
    clauses = [s for s in re.split(SPLIT, text) if s]
    flags = [item_bound_proxy(item, s, parts) for s in clauses]
    assert len(clauses) == 2 and flags == [True, True]
    print(label, json.dumps({"plan": outcome.status, "items": 1,
        "target_room": item.target_room, "clauses": clauses,
        "per_item_binding_proxy": flags, "actual_tool_body": parts[0].text,
        "actual_evidence": [{"property_id": e.property_id,
            "start": str(e.target_date), "end": str(e.target_end_date)}
            for e in parts[0].evidence],
        "unanswered": unanswered}, ensure_ascii=False))

async def no_date():
    """只用现有测试替身走真实 respond，核对房态无日期并不命中房价追问入口。"""
    question = "401有房吗"
    assistant, completions = _assistant([_decision(
        reply_text="请问您计划哪天入住、住几晚？", item_answers=[]
    )], plan_turns=False)
    outcome = plan_for(question, dated=False)
    needs_price_dates = A._price_question_needs_dates(
        question, [{"role": "user", "content": question}], None)
    result = await assistant.respond(guest_identifier="synthetic-review",
        language=Language.ZH, messages=[{"role":"user","content":question}],
        turn_plan=outcome)
    assert needs_price_dates is False and len(completions.requests) == 1
    print("E-V4-C2", json.dumps({"price_date_guard": needs_price_dates,
        "stub_model_calls": len(completions.requests), "actual_reply": result.reply_text,
        "limit": "只证明当前出口；不是 D-G10 实施或真实模型验收"}, ensure_ascii=False))

asyncio.run(no_date())
PY
```

关键输出：E-V4-1、E-V4-2 的 per_item_binding_proxy 均为 [true,true]，实际工具证据均仅为 property_id=1401、2026-10-10 至 2026-10-11；E-V4-C1 只有 fee；E-V4-C2 guard=false、替身主调用次数=1。

## 7. 证据绑定、工作区归属与交接

| 文件 | 本轮 SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 06cb6e51a9a7357b9340970ab05477908a19ec82a8212290736219d1a3239792 |
| docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v4-review-handoff.md | d6e9a5a16e2582fad1b743436b16cd75ec8e1bc01c7c08e18119e8fa3d7979b0 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v3-review-handoff.md | 2dbdaa5ce84ba2361e59e2ea6bc7c15f0cf487b593d0f2c13f389e74e406ab83 |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/answer_policy.py | 01561d21304f2058132de2ba7603ca4ae20546d949417f929ebbbd2143a4e4fe |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/knowledge_service.py | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| src/homestay_bot/services/turn_plan.py | 4f2f93caa43929ba50cc762811b0b26bd279cd3be6918841081e907f42d873d6 |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| src/homestay_bot/application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| tests/integration/test_guest_reply_postgresql.py | 33151cd8f979211b88f0218c85777f5166b8e14d16ba712abe952325a380b440 |
| tests/unit/test_turn_plan.py | 0397407966c04fb13cff99d852713eb7c3ef6ae0914768e762d26f5f609bb7c9 |
| tests/unit/test_deepseek_turn_plan.py | 0527ece822ceeb253d9f7632945b40b602e3e13818ead063466b830c3d46fb1c |
| tests/unit/test_conversation_plan_flows.py | 925413407f560abf24951f1cad334f05450c48cc2bc24e5eb8d7afbc1dc5dccb |

本次新增文件仅为本报告；AOCI 托管索引/基线按当前批次维护。已有 Claude 返回稿、既有 Codex 报告、Spec、上轮 Claude 补充、索引改动和 .impeccable/critique/ 均继续保留，不归入本报告的业务修改。

Claude 下一步只需把 R1 的范围继承条件写清，并把 C1、日期早退与失败出口列入实施验收；无需重开已明确接受的 D-G8/C2 上限。完成后再按项目 Spec 门禁确认。此次报告不要求也不授权编码、模型门禁、提交、推送、部署、外部发送或生产写入。
