# Codex → Claude：回复泛用化第二轮修复 Spec v2 复审报告

- 日期：2026-10-08。作者：Codex。
- 审查对象：[Claude v2 返回稿](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v2-review-handoff.md)与[修复 Spec v2](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
- 对照基线：[上一轮 G1–G5 报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-review-handoff.md)及其绑定的源码。
- 本地 HEAD：`80f9b2c309547c36c15cfd142d5dd30b98d87406`。相关业务源码和三个相关单测文件摘要与上一轮一致；Spec 已修订，旧 Spec 摘要失效。
- 本轮授权：审查并写报告。未修改业务代码、测试或 Spec，未连接数据库、生产或真实外部服务，未提交、推送、部署或向其他会话发送消息。
- 返回稿记录 D-G4–D-G7 已由用户决定；本报告据此评估技术契约，不把返回稿的授权叙述视为本会话的编码或外部操作许可。

## 1. 结论与上一轮闭合情况

**v2 方向认可，暂不通过。** 尚有 4 项需要修订：1 项 P1、3 项 P2。前三项来自同一个剩余根因：**房源范围、主题、字面包含和有效期都不能单独证明“同一主体的同一政策”**。第四项是 PostgreSQL 验收方案的分支和装配缺口。

| 上一轮 | v2 已补内容 | 本轮结论 |
| --- | --- | --- |
| G1：误分类型仍走整轮事实回退 | §2.1(2)取消计划成功时的整轮证据计划，事实类项统一按项回退 | 房间候选边界在设计层闭合；回退的主体分组仍有 V2-R3 |
| G2：短摘录漏掉排除条件 | 正向只认客人摘录；排除按完整冻结原文否决；两个检索接口同步传参 | 设计层闭合；另一项排除词造成的召回下降已明示 |
| G3：局部联网被当作整项完成 | 复用小句位置与实际查询映射；不能映射不提前返回；组合排序上限明示 | 设计层方向成立；须落实下文 §4 的覆盖判据，尚未实施验收 |
| G4：同主题入口绕过主体约束 | 增加范围/房源一致及包含判据 | 未闭合：包含判据和期间例外仍可跨主体，见 V2-R1/R2 |
| G5：追问词表删掉实义词 | 改用专用归一化，保留否定、收费限定、楼层等 | 不复用追问词表的方向成立；保留字符不等于包含分组保留其意义，见 V2-R2 |
| F4：客诉变体取首个硬理由 | `classify_all`集中合并原因、风险、退款标记，门禁同步 | 接受该设计；无须增加会话层优先级表 |
| PostgreSQL 补证 | 明确两窗口、实际恢复、精确计数 | 范围补齐，但窗口二不能原样覆盖投诉分支，见 V2-R4 |

上述“闭合”只表示 **Spec 契约已补上**，不表示代码修复或生产验收完成。原 F1–F5 及 E-S1–E-S5 已公开，应作回归资料；不能转称新的独立改写留出集。

## 2. 需要修订的发现

### B24-V2-R1 · P1：特殊时期例外仍能把另一个主体的政策替换成答案

**依据。** Spec §2.3(2)第 105–108 行规定：范围、房源一致后，同主题带有效期条目始终入组，绕过问法包含条件。这个例外只证明“此条知识在目标日期有效”，没有证明它描述回答条目的同一个早餐厅。

现行 [knowledge_evidence_policy.py:`verify_selected_evidence`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:960)第 1003–1009 行在普通与期间条目发生冲突时，把答案替换为期间条目。错误条目一旦入组，会改变最终取出的审核原文，而不只是增加误拦截。

**本轮证据 E-V2-1（v2 入组代理 + 真实选择核验函数）。**

- 全域普通条目：一楼早餐厅 8:00 开放。
- 同范围、同房源字段的期间条目：二楼早餐厅 10:00 开放，2026-10-01 至 07 有效。
- 2026-10-03 问一楼，模型选择一楼条目。
- 两条归一化问法互不包含。没有有效期时，v2 代理只纳入一楼，输出一楼 8:00；给二楼条目加有效期后，代理纳入二楼，真实核验返回 `grounded/selected`，正文变为 **二楼 10:00**。

这证明 v2 的例外本身允许跨主体覆盖。代理没有实现新适配器，不是 v2 最终客户端复现；完整命令见 §5。

**建议修订。**

1. D6 优先顺序限定为：**先确认同一政策主体与所问属性，再处理有效期和特殊时期优先**。有效期不得成为跳过主体约束的理由。
2. 最小保守方案是仅在已有证据能确认同主体时自动覆盖；主体无法确认时回未确认，不把另一主体的期间条目写成答案。沿用日期分段，无需新增规划器或主体框架。
3. 不得仅用“D6 单测通过”证明所有旧 D6 场景保留：现有 [test_deepseek_turn_plan.py:`_breakfast`及期间政策测试](/Volumes/02/code/homestay-bot/tests/unit/test_deepseek_turn_plan.py:288)让平时和期间条目的标准问题完全相同；[guest_reply_scenarios.json 的早餐资料](/Volumes/02/code/homestay-bot/tests/fixtures/guest_reply_scenarios.json:234)与[国庆资料](/Volumes/02/code/homestay-bot/tests/fixtures/guest_reply_scenarios.json:960)却使用不同标准问题。Claude 应明确后一类如何保留 D6，无法可靠绑定时应如实声明边界，不能继续按主题一律替换。

**验收。** 同主体普通/期间政策仍按 D6；不同楼层、区域、套餐的期间政策不得覆盖；只改有效期字段不能把“另一件事”变为答案；跨期仍分段。断言选出的来源编号、最终正文和适用日期，不能只看 `grounded`。

### B24-V2-R2 · P2：一方包含另一方仍会吞掉否定或限定，和 G5 的验收要求矛盾

**依据。** Spec 第 102 行要求保留否定、收费限定等差异；第 107 行却以一方包含另一方允许合组；第 157 行又要求“包含/不包含不合组”。三条不能同时由当前包含判据满足。

**本轮证据 E-V2-2。** 两条同范围知识：

- “包含早餐的套餐，早餐收费吗？”→ 包含套餐早餐免费。
- “不包含早餐的套餐，早餐收费吗？”→ 不包含套餐早餐 30 元。

按 v2 专用归一化分别得到：

```text
包含早餐的套餐早餐收费
不包含早餐的套餐早餐收费
```

第一串完整包含在第二串内。“不”虽然保留下来，仍不参与包含关系的否决。v2 代理把两者入组，真实 `verify_selected_evidence` 返回 `missing/conflict_fee`，无法分别作答。

同理，返回稿 §3 问到的“早餐”与“一楼早餐厅”会合组；这可作为保守冲突策略，但不能同时宣称已证明主体一致或“不同区域各自作答”。

**建议修订。**

- 不把裸包含关系作为同主体证明。最小方向是复用 D-G1 的非空归一化同问法判据；包含关系只作为相关线索，不能单独授权冲突合组或期间替换。
- 若仍保留 D-G5 的包含入口，应先给出能保留差异的明确约束与反例，不要靠补“套餐、楼层、早餐”案例词表绕过本问题。
- 多个回答条目描述不同主体时，也须按相同契约分别检查。现行 [`verify_selected_evidence`第 980 行](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:980)把全部 `answers`合成一个组；只过滤旁观候选，仍不能保证粗粒度项中两个已选主体分别作答。
- 这会调整 D-G5 的技术判据，须按项目规则更新并确认 Spec；本报告不擅自替用户变更已决定的取舍。

**验收。** 包含/不包含、免费/收费、不同楼层/区域均不得因公共后缀而合组；同主体真冲突仍在所有 answer/related 标签分配下拦截；粗粒度一项问两个主体也应分别取证。中英文各保留正反对照，不以降低冲突阈值替代。

### B24-V2-R3 · P2：新主体规则只写在选择核验，缺选择回退仍走旧的主题冲突和期间优先

**依据。** Spec §2.1(2)第 63 行把选择缺失、为空、坏格式、越界或跨期统一交给 `build_evidence_plan`；§2.3(2)的修改位置只指定 `verify_selected_evidence` 的 group 构造。

真实调用链：

1. [knowledge_service.py:`retrieve_detailed`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:705)按审核、房源、日期、触发词筛选后评分；同一房源内不同楼层仍可能同时成为某项检索候选。
2. [deepseek_client.py:`_supporting_knowledge`](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2073)按答案是否描述该主题及收费范围取支持条目，未按两条标准问题的主体分组。
3. [knowledge_evidence_policy.py:`_build_evidence_plan_for_period`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:758)对同主题、同属性的全部 covering 条目检查冲突，并在第 802–811 行让期间条目优先。

**本轮证据 E-V2-3。**

- 真实 KnowledgeService 单独检索“一楼早餐厅几点开放”，上限 4 条/3000 字，确实得到一楼与二楼两条候选。这不是借用整轮种子的反例。
- 使用 v2 选择入组代理并选择一楼：只取一楼，`grounded`，8:00。
- 对相同逐项候选直接运行真实 `build_evidence_plan`：`insufficient`，产生 missing 分项。
- 二楼加有效期时，该回退直接返回二楼 10:00；此错误后果与 V2-R1 共用根因，不另计一个 P1。

因此，模型是否回传选择仍会改变“同一主体”的判断。去掉整轮池只修复了房源/条件越界，没有修复检索到多个近似主体时的回退一致性。

**建议修订。**

- 把“主体分组 → 同属性冲突 → 同主体期间优先”写成选择与回退两条路径共同遵守的契约，落在证据策略层的最小共享位置。
- 复用现有排序、范围校验与日期分段；回退确定候选后，也应按同一组边界核对，不能把所有同主题候选一锅比较或覆盖。没有可靠主体依据时按未确认处理。
- 不必统一重写两个算法或新建检索服务；只补共同的入组/覆盖约束，保留各自职责。

**验收。** 对同一问题、同一合法候选，分别测试合法选择、缺字段、空选择、坏格式、越界、跨期；这些形式不能造成另一楼层政策覆盖或仅回退路径误冲突。最终正文与来源范围都要断言。

### B24-V2-R4 · P2：窗口二只对应普通分支，投诉持久化重放与原生接管装配仍需明确

**依据。** Spec §2.4第 120–125 行把普通与投诉分支放在同一组精确计数里，窗口二统一指定 `_stage_fast_ack` 提交后、Worker 标记完成前。

真实流程不同：

- [conversation_service.py:`process_debounced_message`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:975)投诉分支调用 `_enter_complaint_mode` 后第 989 行直接返回，**不会进入**第 1025 行的 `_stage_fast_ack`。
- [`_enter_complaint_mode`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1120)登记客人确认、员工通知、客诉复核与分析作业。
- 生产 [application.py:`application_lifespan`内 `handle_message`](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4387)使用独立业务会话，并在第 4446 行提交；[`handle_deferred_message`](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4448)完成后，外层 [worker.py:`Worker.run_once`](/Volumes/02/code/homestay-bot/src/homestay_bot/worker.py:384)才标记来源作业完成。这才是投诉副作用已持久化、来源仍 RUNNING 的真实窗口。
- 当前 PG [test_guest_reply_postgresql.py:`_service_for`](/Volumes/02/code/homestay-bot/tests/integration/test_guest_reply_postgresql.py:261)没有注入 `audit_events`、`business_tasks`；现有 `_seed_guest_message`也未绑定 Customer。原样沿用无法证明原生接管判据或“业务任务恰好 1”。
- [conversation_service.py:`_in_native_session`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:2126)需要审计仓储；[operations.py:`native_session_active`/`handoff_accepted`](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/operations.py:1660)确实以 HUMAN_ACTIVE 与最新接管 `accepted_by`判定。写 accepted_by 的方向正确，接线缺失时仍查不到它。

这是源码与测试构造审查，**未运行 PostgreSQL**，不宣称已复现 PG 竞争缺陷。

**建议修订与验收。**

1. 窗口二拆成同一用例表的两行：普通分支在 `_stage_fast_ack`真实提交后中断；投诉分支在独立业务会话真实提交后、来源 Worker 标记完成前中断。不能在投诉路径人为调用 `_stage_fast_ack`制造不存在的窗口。
2. 测试 Worker 队列会话与业务会话的真实分离，取消并等待退出后各自回滚/关闭；由第三个会话证明来源 RUNNING、各类副作用已落库，再执行新会话的实际 `recover_stale`、领取和处理。恢复后提交状态转换，再领取；断言同编号、同载荷及精确去重键。
3. 原生接管用例注入真实审计仓储，可复用 `accept_handoff`写入一致状态。SERVICER 消息会同时触发“更新活动”，须用变异检查证明原生接管判断确实参与，而非只靠新消息导致丢弃；可用原先就是 HUMAN_ACTIVE、只改变接管审计的对照隔离该判据。
4. 任务重放对照显式绑定合成 Customer、真实 BusinessTaskService 与已核验服务申请计划，再执行 final 阶段；闲聊计划本就不应产生业务任务。普通分支若要验证员工任务通知，其预期也须写成非零精确值。
5. 投诉计数包含客人 `complaint_ack` 出站（现行分支会登记）、员工通知、复核、分析作业和最终任务 0；来源作业与派生作业按类型、来源和键分别统计。不得只写“上述数量不变”而遗漏投诉客人出站。
6. 延续原完整提交后重放对照，不能把它当作中断恢复。不会发送任何真实 outbox；这些断言只证明本地持久化，不证明平台受理或客人收件。

## 3. 建议的最小整体方案

保持 v2 已成立的部分：逐项房间/日期/条件检索、正向与排除拆分、小句实际覆盖、客诉变体集中合并。只补证据策略的共享主体边界与分支真实验收。

| Claude v2 | 本轮优化建议 | 原因 |
| --- | --- | --- |
| 范围/房源一致后按问法包含合组 | 同问法可确定合组；包含仅作线索，主体不确定不自动合组或替换 | 房源一致不等于楼层、区域、套餐一致 |
| 带有效期的同主题政策例外入组 | 同主体判定之后再应用 D6 | 防止日期变成跨主体授权 |
| 选择路径收紧，回退保留原算法 | 两条路径共享入组和期间覆盖约束，继续复用现有排序/分段 | 防止模型缺字段时保护失效 |
| 两分支共用 stage 后中断点 | 普通 stage 提交窗口 + 投诉独立业务提交窗口 | 验证实际存在的提交边界 |
| 全部依赖“已检索/已查询”的标志 | 只把能绑定当前项及所问事实的本地分项算覆盖 | 检索或工具调用发生不等于这项已答完 |

这是对同一个修复单元的优化，不建议新建实体识别框架、第二个规划器、通用工作流状态机或按反例加业务词表。D-G5 若改变，须由 Claude 更新三段 Spec 并确认；没有“开始”仍不写业务代码。

## 4. 对返回稿五个重点问题的直接回答

1. **工具覆盖判断时点**：应在工具循环结束并拿到本地分项后、最终组合前判定；对该项选择“实际工具/澄清结果”或“逐项静态证据”之一，不能先发未确认、再无条件追加工具。现行 [deepseek_client.py:`_tool_reply_parts`](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2512)的 question 是工具名，[ReplyPart](/Volumes/02/code/homestay-bot/src/homestay_bot/services/reply_plan.py:25)没有 item_id；应在适配器内部保留项与调用参数、房源、日期、属性的关联，不凭存在任一工具分项就覆盖全部事实项。目录结果不能覆盖退房时刻，无日期房价澄清不能覆盖同项早餐问题。无法确认覆盖的剩余事实继续取证/未确认，不新增对外模型即可实现该关联。补两房两日期、目录加退房时间、粗粒度房价加本店事实的对照。
2. **小句映射是否过严**：不能映射便多一次主调用是已声明的保守代价，方向可接受。用原文区间的相交小句核对，不能只数“完整落在摘录内”的小句；没有任何匹配小句时不能用空集合的 all=True 提前返回。部分覆盖时只让主调用答未查内容；已查成功或失败说明都须保留，不能重复改写。
3. **“早餐”和“一楼早餐厅”包含是否符合预期**：裸包含会合组，可导致保守误拦截；一旦叠加 D6，还会反向替换答案。不能把这同时描述为主体已证或各区域独立，见 R1/R2。
4. **“可以”“有没有”等是否纯虚词**：不能按所有句子一律保证无语义。专用归一化只能作为受限同问法近似，不能改变客人原问或其他分类输入；英文短词按完整词/短语边界处理，保留空键不匹配，避免删房名中的 is/can 或把不同泛问归一为空后合组。收费、否定、数量等语义不得被包含分组再次吞掉。不要继续追加领域词修补碰撞。
5. **窗口二/恢复是否足够**：普通 stage 窗口成立；投诉窗口、装配及出站计数须按 R4 修订。取消、新会话和实际 recover_stale 的链路方向成立，尚无执行证据。

**验证规则文字也应校正。** Spec 第 159 行以“公共回复流程”直接要求提交前本地全量，未说明为何无法界定离线影响面。按本次 AGENTS.md，先选相关知识/适配器/会话/路由/PG验证；只有装配、依赖、构建改变或确实无法界定范围时才跑本地全量，并写具体理由。知识检索与公共回复变化需要全量真实模型门禁是另一个明确项目门禁，须当前授权；不能把两者混成“改核心就自动全量”。

## 5. 本轮验证证据与可执行入口

本轮只做新假设所需的两条离线 Python 探针，未新增测试文件、未运行 pytest 或真实模型门禁。

- 第一条退出 0，约 2.98 秒，打印 E-V2-1～3：期间跨主体替换、包含误冲突、当前回退误冲突/错用期间政策。
- 第二条退出 0，约 1.32 秒，真实单项检索返回两楼层候选并触发现行回退误冲突。
- 第一条仅在内存暂时替换 `_same_question`为 v2 的严格同问法判据，finally 恢复；同主题组通过预筛候选模拟 v2 明文契约。英文归一化使用完整词边界，未采用更弱实现制造反例。
- 未实现 v2 完整适配器，不能将这些结果写成新版最终客户端失败。R4 是源码与验收方案核对，未用 SQLite 替代 PostgreSQL。
- 上轮 93 passed / 6.31 秒仅为三个相关离线文件的历史结果。本轮核对相同源码/测试摘要后复用其范围判断，未重复跑、未累加为新通过数；它们没有验证新 Spec 或这些新增假设。
- 报告静态核验：21 个本地链接及行号、15 项 SHA-256 绑定、两段内嵌 Python 语法均通过；工作区与新报告的差异空白检查通过。仅检验文档与复现入口，不验证业务修复。

以下命令在仓库根执行。断言是“缺口仍成立”；退出 0 不表示修复通过。

### 5.1 v2 入组代理与现行回退

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import json, re
from dataclasses import replace
from datetime import date
from homestay_bot.services import knowledge_evidence_policy as p
from homestay_bot.services.knowledge_service import KnowledgeSnippet, normalize_text, detect_property_topics
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant

# 仅将 v2 写明的归一化与入组判据作为内存代理；不修改源码。
zh = "什么时候 何时 几点钟 几点 多久 多少钱 多少 哪里 在哪儿 在哪 怎么 如何 是否 能否 可不可以 可以 有没有 吗 呢 吧 啊 呀 请问".split()
en = ["what time", "when", "where", "how much", "how long", "can", "could", "is", "are", "do", "does", "please"]
def key(text):
    """英文采用完整词边界，避免用最弱实现制造反例。"""
    text = normalize_text(text)
    text = re.sub("|".join(map(re.escape, sorted(zh, key=len, reverse=True))), "", text)
    text = re.sub(r"\b(?:" + "|".join(map(re.escape, sorted(en, key=len, reverse=True))) + r")\b", "", text)
    return re.sub(r"[^\w]", "", text)
def compatible(a, b):
    return (a.scope, a.property_id) == (b.scope, b.property_id)
def contains(a,b):
    left,right=key(a.question),key(b.question)
    return bool(left and right and (left in right or right in left))
def group(answer,candidates):
    """按 v2 §2.3(2) 对回答条目构造主题组。"""
    names={t.name for t in detect_property_topics(answer.question)}
    return [e for e in candidates if compatible(e,answer) and (
        key(e.question)==key(answer.question) or
        bool(names & {t.name for t in detect_property_topics(e.question)}) and
        (contains(e,answer) or e.valid_from is not None or e.valid_until is not None))]
def verdict(question,answer,candidates):
    selected=group(answer,candidates)
    result=p.verify_selected_evidence(question,[answer.source_id],[],selected,target_date=date(2026,10,3))
    return {"ids":[e.source_id for e in selected],"status":result.status,"reason":result.reason,
            "texts":[e.text for e in result.parts]}
first=KnowledgeSnippet(1,"早餐","一楼早餐厅几点开放？","一楼早餐厅开放时间为8:00。",scope="global")
second=KnowledgeSnippet(2,"早餐","二楼早餐厅几点开放？","二楼早餐厅开放时间为10:00。",scope="global")
holiday=replace(second,valid_from=date(2026,10,1),valid_until=date(2026,10,7))
original=p._same_question
p._same_question=lambda a,b: compatible(a,b) and bool(key(a.question)) and key(a.question)==key(b.question)
try:
    control=verdict(first.question,first,[first,second])
    actual=verdict(first.question,first,[first,holiday])
    assert control["texts"]==[first.answer]
    assert actual["texts"]==[holiday.answer]
    print(json.dumps({"probe":"V2-P1-period-exception","contains":contains(first,holiday),"control":control,"proxy":actual},ensure_ascii=False))
    included=KnowledgeSnippet(3,"早餐","包含早餐的套餐，早餐收费吗？","包含早餐的套餐，早餐免费。",scope="global")
    excluded=KnowledgeSnippet(4,"早餐","不包含早餐的套餐，早餐收费吗？","不包含早餐的套餐，早餐收费30元。",scope="global")
    actual=verdict(included.question,included,[included,excluded])
    assert contains(included,excluded) and actual["status"]=="missing"
    print(json.dumps({"probe":"V2-P2-containment","keys":[key(included.question),key(excluded.question)],"proxy":actual},ensure_ascii=False))
    fallback=p.build_evidence_plan(first.question,[first,second],supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,is_property_question=True,target_date=date(2026,10,3))
    fallback_period=p.build_evidence_plan(first.question,[first,holiday],supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,is_property_question=True,target_date=date(2026,10,3))
    assert fallback.status=="insufficient"
    assert holiday.answer in fallback_period.answers
    print(json.dumps({"probe":"V2-P3-fallback","selected_proxy":control,
        "current_fallback":{"status":fallback.status,"parts":[[x.status,x.text] for x in fallback.parts]},
        "current_period_fallback":{"status":fallback_period.status,"answers":fallback_period.answers}},ensure_ascii=False))
finally:
    p._same_question=original
PY
```

### 5.2 真实逐项检索的候选对照

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio,json
from datetime import date
from types import SimpleNamespace
from homestay_bot.domain.enums import Language
from homestay_bot.services.knowledge_service import KnowledgeService
from homestay_bot.services.knowledge_evidence_policy import build_evidence_plan
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
rows=[SimpleNamespace(id=i,category="早餐",question_zh=q,answer_zh=a,question_en=q,answer_en=a,
                      keywords=["早餐","早餐厅"],scope="global",property_id=None,
                      valid_from=None,valid_until=None,trigger_any=None,trigger_exclude=None)
      for i,q,a in [(1,"一楼早餐厅几点开放？","一楼早餐厅开放时间为8:00。"),
                    (2,"二楼早餐厅几点开放？","二楼早餐厅开放时间为10:00。")]]
class Repo:
    async def list_active(self):
        """提供两条已审核启用的合成行，无数据库连接。"""
        return rows
async def main():
    q=rows[0].question_zh
    found=await KnowledgeService(Repo()).retrieve(Language.ZH,q,limit=4,char_budget=3000,
                                                 target_date=date(2026,10,3))
    assert {e.source_id for e in found}=={1,2}
    result=build_evidence_plan(q,found,supporting_for_topic=DeepSeekGuestAssistant._supporting_knowledge,
                              is_property_question=True,target_date=date(2026,10,3))
    assert result.status=="insufficient"
    print(json.dumps({"probe":"V2-P3-real-retrieval","item_candidates":[e.source_id for e in found],
                      "status":result.status,"parts":[[p.status,p.text] for p in result.parts]},ensure_ascii=False))
asyncio.run(main())
PY
```

## 6. 工作区归属、证据绑定与接手要求

- Codex 本轮新增：本报告。AOCI 收尾仅维护该稳定报告的认知条目与基线，独立于业务修复。
- Claude 已有修改：Spec v2、新 v2 返回稿，以及此前返回稿补充。
- 已有 Codex 报告、Claude v1 返回稿及 AOCI 资产继续保留；`.impeccable/critique/`不属于本轮。不得整文件回退、清理或顺手提交这些内容。
- 本轮没有业务实现。不能凭报告或 AOCI 对齐宣称已经修好、允许发布或当前生产健康。

写报告时重新计算的 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v2-review-handoff.md | 5a6e0452e31f47fce10e2b44ba2be439c8b6f79017934f6422f644bd51f29566 |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | c6fbab0f889de7ea433b7318ab29180252013b60b091d7d5bca4c0b7c21b0efc |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-review-handoff.md | f8d3a0cb7d7f9940becf34fe097e32162154d9a82b0f0fe66bfedf54d9aa073d |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_service.py | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| src/homestay_bot/repositories/operations.py | fb358c59cb96b301448333b4b5e9fd8aa2bca0249177194fb43a2a247f5f2486 |
| src/homestay_bot/services/reply_plan.py | 7ba2118cbdf33db96c9d0f1e95751eefdf0c86775bb8ae69841166545ea9e226 |
| src/homestay_bot/repositories/jobs.py | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| tests/integration/test_guest_reply_postgresql.py | 33151cd8f979211b88f0218c85777f5166b8e14d16ba712abe952325a380b440 |
| tests/unit/test_deepseek_turn_plan.py | 0527ece822ceeb253d9f7632945b40b602e3e13818ead063466b830c3d46fb1c |
| tests/fixtures/guest_reply_scenarios.json | 8a1a3cb0c2d553c02ef48c6aeb69dae646a9987b526311296e6a382ce0b58b62 |

接手顺序：先在同一 Spec 内修订 R1–R4及覆盖/归一化细则，明确 D-G5 的新边界并按项目规则确认；获“开始”后再实施共同证据边界和真实验收装配。最终交接应分别给出本地行为、PG、获授权模型门禁与实际收件的证据，未做的明确留空，不用其中一种替代另一种。
