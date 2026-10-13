# 1.70.1 静态问题「周末」日期绑定 Spec（v4，待确认与开始）

目标：可确定的中文周末静态问题按所问日期使用审核资料，保持住宿范围、资料冲突和转交边界，避免只补解析后引出错日作答或错误转交。

- 依据：[Claude 发布审查](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13_claude-to-codex-1.70.0-release-review.md) RV-1–RV-3，以及 [Codex Spec 审查](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13_codex-to-claude-weekend-static-date-spec-review.md) W-R1–W-R4。
- 本地证据快照：2026-10-13，main HEAD `04d8a1f4055077a02605cd3b1fe272f6c10fefc2`；正式业务标签 `v1.70.0` 指向 `2ff00794f72dac54eb9054605dfce5aa16c861d7`。本轮未重新检查生产。
- v2范围：共享周末日期绑定、分段阻断原因汇总、缺失日期展示、新场景阻断与基线继承。复用现有布局、证据计划、GapUnit和门禁工具，无持久化结构变化。
- v3（2026-10-13，Claude审查v2后修订）：§2.4 单日分段日期写法改为待决D-W6；§2.5 新增虚构房源离线预检与回退；§3.4 按用户已选D-W3改写授权。其余沿用v2。
- v4（2026-10-14，按[Codex v3复审](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-14_codex-to-claude-weekend-static-date-spec-v3-review.md) V3-R1–V3-R3修订；Claude已复跑v3_probe.py退出0并核对代码）：D-W6改为单日 `YYYY-MM-DD：`；K-周末分日改用「日期＋对应答案」组合匹配及场景负例，来源归属改由离线断言；预检失败先定位分支再决定换房；更正分段条件为“两个及以上分段”。
- 当前仅修订Spec；业务代码、回复夹具、基线和版本未修改。既有审查文件、AOCI改动及无关critique保留。

## 1. 第一段：现状证据

| 编号 | 已核实事实 | 文件与符号 |
| --- | --- | --- |
| W1 | 无期限早餐资料普通问法可答，加“周末”后未确认；无法解析的相对范围不借父日期 | [item_coverage.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py)，static_clause_dates、relative_calendar_day |
| W2 | 独立静态项当前只为日历单日自行定目标；模型日期优先保留供小句核验 | [deepseek_client.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py)，_item_dates、_fact_layout |
| W3 | 分段最终状态检查所有段，reason却只取第一段；周六正常、周日冲突的投影被映射为knowledge_missing，候选筛选允许转交 | [knowledge_evidence_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py)，build_evidence_plan；deepseek_client._gap_reason、_item_evidence_plan、_referral_candidates |
| W4 | 同一对象的期限例外可分段；不同标准问题的期间矛盾可整项period_veto，合法选择与选择回退不能承诺相同可答范围 | knowledge_evidence_policy.verify_selected_evidence、_period_veto；deepseek_client._static_item_parts |
| W5 | 只加入fixture的新编号三次失败仍可能不阻断，或不进入建议基线 | [reply_regression.py](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py)，gate_verdict、_gate、needs_rerun |
| W6 | 问房周末先定位周五，另给周六入住方案；下周先加周偏移。静态自然日不能照搬住宿区间 | deepseek_client.weekend_stay_options、_weekend_argument_options、_weekend_reply_parts |
| W7 | 分段只给非空正文加日期；missing为空，之后按主题生成未确认文案会丢掉缺失日期 | knowledge_evidence_policy.build_evidence_plan；deepseek_client._apply_evidence_plan、_unconfirmed_reply |

[离线探针](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13-weekend-static-date-spec-evidence/boundary_probe.py) E0–E4已核验审查反例。E1是v1公式推演，E2/E3的范围为契约投影；未实现新解析器、未写转交或实发卡片。详细复现由审查报告保存，退出0不代表修复通过。

## 2. 第二段：功能与修复方案

### 2.1 共享静态周末解析（W-R1）

在 `services/item_coverage.py` 新增纯函数 `relative_calendar_weekend(text, today)`，返回含首尾的自然日范围或None；时钟由调用方传入。

1. 以周一为起点，先计算完整本周六、日。
2. “下周末／下个周末”在完整范围上加7天，始终包含下周六至日。
3. “周末／这周末／本周末”取本周六至日；仅今日周日时按D-W2裁为今天。不得裁完本周再+7天求下周。
4. 同一小句出现绝对日期、其他日历单日、住宿序数日、其他相对范围或不同周末指示时拒绝。先校验完整日期表达，再选择单日或周末；成功解析子串不能绕过混用拦截。
5. “上周末／上个周末／下下周末”等未支持限定拒绝，不得截出“周末”或“下周末”。本周三个别名视为同一指示；本周与下周并存拒绝。

| 冻结今日 | 周末／这周末／本周末 | 下周末／下个周末 |
| --- | --- | --- |
| 2026-10-05 周一 | 10-10至10-11 | 10-17至10-18 |
| 2026-10-06 周二 | 10-10至10-11 | 10-17至10-18 |
| 2026-10-07 周三 | 10-10至10-11 | 10-17至10-18 |
| 2026-10-08 周四 | 10-10至10-11 | 10-17至10-18 |
| 2026-10-09 周五 | 10-10至10-11 | 10-17至10-18 |
| 2026-10-10 周六 | 10-10至10-11 | 10-17至10-18 |
| 2026-10-11 周日 | 10-11至10-11 | 10-17至10-18 |
| 2026-10-31 周六 | 10-31至11-01 | 11-07至11-08 |
| 2026-12-31 周四 | 2027-01-02至01-03 | 2027-01-09至01-10 |

`static_clause_dates` 与 `_item_dates` 消费同一个周末解析结果，不另写助手私有公式。

### 2.2 检索前冻结目标与范围

- `static_clause_dates` 保留既有单日、绝对范围和房间约束。周末必须完整包含在项目标内才绑定，不能只取相交一天；不符返回None。
- `_item_dates` 保留模型明确的入住与退房范围，静态末日为退房前一天，不替模型改正日期。独立static_fact无模型日期且周末可解析时自行定周末目标，不受无关已确认住宿日期限制。
- 粗stay_query/catalog_query不因周末静态残留扩展住宿范围。范围外、混用和未解析保持scope_unbound，不取整项资料、不提供转交选项。
- `_fact_layout` 在检索前冻结today、项目标及小句绑定；检索、调用前后选项、正常取证、已有工具结果协议回退及跨项复用共用，跨午夜不重算。
- `_shared_grounded` 的全店无条件条目复用边界不变；接收项再次核日期、属性和房源，不因另一项已核验而使用失效条目。

### 2.3 所有分段的阻断原因与预算（W-R2）

修改 `knowledge_evidence_policy.build_evidence_plan`，保留EvidencePlan结构、有效期切段及各段可用答案。

- 任一段insufficient则最终insufficient；reason从所有段汇总，不用第一段代表整项。
- 阻断诊断采用固定优先级：answer_needs_review／borrowing_unconfirmed，其次conflict*，其次period_veto，其次reply_exceeds_message_limit／reply_budget_exceeded。同级按日期顺序取首个，保留原字符串。优先级只选代表原因，不降低其他阻断效力。
- 无阻断时取第一个insufficient段的原原因；全部可答才保留covered等成功原因。普通缺失不能被前段covered遮住。
- `_gap_reason` 沿用既有映射，映射前保留阻断原因。任一段有冲突、期间否决、安全复核或预算阻断，整个原文GapUnit不得作为knowledge_missing转交，即使另一段确实缺资料。
- 不按日期新建unit_id。调用前选项与调用后缺口仍使用相同原文区间；沿用模型选择、模式、风险、隐私、冷却及保存点校验，同来源同单元最多一条转交。
- 合并审核正文按既有消息容量与静态合计预算再检查，不能逐段通过后拼成超限内容；不新增预算常量。整体超限参与上述阻断优先级，关闭合并答案，不截断条件、不转交。

单段安全／预算阻断关闭该段，其他合格段可保留；整体合并预算阻断关闭整个合并答案。失败结果仍需有missing分项与预算原因，禁止无正文却当作成功。验收最终正文与持久化转交，不只看reason。

### 2.4 对象与缺失日期展示（W-R3）

同一对象沿用 `same_object/object_question_key`，不放宽问法归一化、范围或房源身份。

- 成功例：同一标准问题“早餐几点开始？”，平时7:30，限周六条目9:30；跨生效边界后由既有分段给周六9:30、周日7:30，日期明确、不混用。
- 不同对象例：“早餐几点开始？”与“周六早餐几点开始？”。继续D-G8；合法模型选择遭期间否决可整项未确认，未选中／越界／已有工具结果协议回退走原分段核验。允许保守可答范围差异，不为统一结果绕过否决。
- 多日期计划中，build_evidence_plan在missing分项现有question中保留本地确定日期前缀，使用与grounded正文相同的日期格式（见D-W6）；某段因复核或预算阻断没有parts时，以原问题构造该段missing分项，保留其日期。_apply_evidence_plan生成未确认正文时保留该前缀。不新增ReplyPart、模型或数据库字段，不用模型任意文字作日期依据。
- 文案生成须先剥离本地标准日期前缀、按原问题执行主题和凭据专门拒绝，再给最终正文加前缀。日期标注不能让“门锁凭证”失去专门拒绝或变成可转交知识缺失；gap_unit、原文GapUnit.text和模型选项不变。
- 周六可答、周日缺资料且未成功转交时，最终正文明确周六答案及周日未确认，例如按D-W6写成“2026-10-11：当前…尚未确认…”。成功转交沿用整个原文单元的核实说明，已知日答案保留，不承诺分别建日期记录。
- D-W6（待确认，推荐单日ISO）：现行 `knowledge_evidence_policy.build_evidence_plan` 在边界点多于两个（即两个及以上分段，条件 `len(ordered) > 2`）时给grounded分项加 `YYYY-MM-DD — YYYY-MM-DD：` 前缀，单日也写成“2026-10-11 — 2026-10-11：”。周末拆分后每段都是单日，客人会更常看到。该函数无语言参数，调用方含英文和非周末跨期问题（V3-E1：英文跨年两段已带前缀）。推荐：单日段写成 `YYYY-MM-DD：`（如“2026-10-11：”），多日段保持现行ISO范围；年份完整、语言中性，不加语言参数，不从问题文字猜语言。grounded与missing共用同一个本地格式函数，日期只取冻结的本地段边界，不在等待模型后重读时钟；前缀识别与展示不改变主题、凭据专门拒绝、gap_unit或原文单元。备选：保持现行写法，只在发布记录注明观感上限。不采用“10月11日（周日）”：会丢年份，并给英文答案加中文前缀。
- 纯静态主模型完全不可用仍走现行异常，不新增吞错回答。转交失败回未确认；来源清空、过时和人工接管保护不变。

### 2.5 新场景必验与基线继承（W-R4）

沿用reply_regression及reply_gate.sh，新增 `K-周末早餐`、`K-周末分日` 两项：

1. K-周末早餐使用既有无期限早餐资料，问“这周末早餐几点开始？”，要求审核时刻、知识作答、无知识缺口或接管。原缺陷红测用离线完整回复链，不额外调用真实模型证明旧缺陷。
2. K-周末分日沿用fixture冻结今日2026-09-25，问“9999房间这周末早餐几点开始？”。新增虚构房源专属条目9901/9902，同一标准问题，仅9月26日8:30、9月27日9:00有效。判定（V3-R2）：现有 `reply_regression.judge` 逐片段匹配must_include、不建立对应关系、不读source_ids，分开要求日期和时刻时“周六9:00、周日8:30”互换仍会通过。因此must_include用完整“日期前缀＋对应审核答案”，按D-W6写成 `2026-09-26：早餐8:30开始`、`2026-09-27：早餐9:00开始`（沿用既有空白归一；如允许多种合法格式，在各组合内给有证据的替代项，仍须拦住互换）；在该场景的must_not_include或must_not_match加平时“7:30”负例，不加入全局禁词。来源归属不交给judge，见本节第4步离线断言。
3. 新条目scope=property、property_id=9999，经现有_MemoryKnowledge.find_property_by_room定位；不改共享global早餐资料、冻结今日和旧期望，不增加Hostex房源或配置接口。实施时复核房号和编号未占用，验证无目标房源的旧场景排除新专属条目。
4. 虚构房源离线预检与来源断言（接入真实门禁前必做）：现有门禁 `FakeHostexClient` 的房源只有101–401，9999只存在于知识资料，经 `_MemoryKnowledge.find_property_by_room` 映射（V3-E3已用10月绝对日期证明该路径可出两段答案、来源9901/9902、无工具调用）。先用合成模型走完整离线回复链做冒烟：房号解析到9999、两条专属条目进入候选、不走查房或“没有这个房间”分支、不调用房态工具；并逐个ReplyPart断言ReplyEvidence的source_id、target_date/target_end_date与正文时刻一一对应（9901↔2026-09-26↔8:30，9902↔2026-09-27↔9:00），不能只看两个来源编号出现过。预检失败时冻结失败输入并定位到具体分支（日期绑定、知识预算、scope过滤、测试装配、合成响应或规划决定）：属代码缺陷按已确认Spec修复，需扩大范围先更新Spec；只有证明确属虚构房源装配问题才改用302，换房后重新做完整正向预检，并核对提及302的相关旧场景候选和最终行为，之后才进入真实门禁。所选房号、预检及失败定位写进发布记录。
5. 新编号先进入known_failures，标“新增必验、待真实测量”，保持fixture/基线集合覆盖；这不是生产失败结论，也不是通过证据。
6. 获当前真实模型授权后，现有gate限定新编号运行，两项各三次全部通过且结果完整才完成独立验收。已有needs_rerun会对通过的known_failures补至三次；首次失败可停止验收，不能把gate的passed当作新项通过。缺结果、任何一次失败均使本次新增功能验收失败，禁止发布。
7. 三次稳定后按建议基线将新编号晋升must_pass；最终all前复核并继承rc7稳定E-地铁、SR-遗失，保留所有旧must_pass。不得用新一轮build_baseline覆盖降低稳定集合。
8. 最终候选执行REPLY_GATE_SCOPE=all。此时新增编号已是must_pass，缺结果或按现有退步规则失败必阻断。限定验证只作新增独立验收，不替代all；非all范围理由记录为“新周末编号三次稳定验收，另执行最终all”。
9. 保存代码身份、fixture SHA256、新项三次outcomes、最终all及基线增量依据。旧measured_on保持历史身份，新测量由本次结果和发布记录绑定，不冒充所有旧场景重测。相关输入未变不因冻结或汇报重跑；改动新项输入或期望时仅重跑受影响项，并重新判断all证据有效性。

离线补测新项失败、无结果、旧稳定退步以及集合覆盖，证明上述独立验收条件和最终基线有判别力。真实门禁不经会话持久化，不能代替转交数据库验收。

### 2.6 发布文档

实施验收后维护拟发布1.70.1的版本、CHANGELOG和docs/releases/1.70.1.md，记录改动、依据、验证、保守上限和回滚；获准发布后再补CI、备份、部署与现场结果，不预写成功。RV-2在docs/releases/1.70.0.md追加带日期影响更正，不改历史原文。本次写Spec尚不修改这些文件。

## 3. 第三段：风险、验收与待确认决策

### 3.1 保留边界与风险

- 问房／问价周末住法和clause_target不变；房态、价格仍信只读权威工具。
- 序数日、今明两天／明后天仍未绑定；英文weekend保持既有行为，不扩大英语能力。
- “每逢周末／你们周末也有早餐吗”按本周末核验，周日仅当天。期间资料只证该具体周末，不能声称长期每个周末都如此；此近似待确认。
- 多日相对静态范围本次只支持周末，不扩任意日期理解。不同对象的期间矛盾仍可能关闭整项。
- 汇总修复位于共享函数，亦影响既有非周末跨期静态问题；须有非周末跨期控制。合并预算保护会使原先超限内容转为未确认，应作为修复验收，不能静默放行。
- 无迁移、权限、装配、并发写或外部协议变化；实施若必须扩大这些边界，先修订确认Spec。

### 3.2 文件、符号与实施顺序

| 文件 | 修改或复核符号 | 验证目标 |
| --- | --- | --- |
| src/homestay_bot/services/item_coverage.py | 新增relative_calendar_weekend；static_clause_dates | 日期矩阵、混用、完整包含 |
| src/homestay_bot/integrations/deepseek_client.py | _item_dates、_fact_layout、_apply_evidence_plan；复核_static_item_parts、_gap_reason、_item_evidence_plan、_shared_grounded | 冻结范围、日期文案、编号、跨项适用 |
| src/homestay_bot/services/knowledge_evidence_policy.py | build_evidence_plan；单日段日期格式（D-W6） | 所有段阻断、missing日期、合并预算、单日写法 |
| tests/unit/test_item_coverage.py、tests/unit/test_reply_fix2_items.py、tests/unit/test_knowledge_evidence_gate.py | 复用现有日期、对象和取证测试 | 单日／问房控制、先后次序、对象差异 |
| tests/integration/test_reply_fix3_boundaries.py | 周末真实检索和最终发件箱回归 | 临时SQLite、正文、转交和卡片作业 |
| tests/unit/test_reply_regression.py；tests/fixtures/guest_reply_scenarios.json、tests/fixtures/guest_reply_regression_baseline.json | 新编号、集合覆盖、基线增量 | 三次独立验收、晋升、缺结果和退步 |
| pyproject.toml、CHANGELOG.md、docs/releases/1.70.0.md、docs/releases/1.70.1.md | 版本及§2.6记录 | 验收后维护，发布后据实补录 |

实施顺序：原缺陷最小回归 → 共享范围和汇总 → 最终回复／转交边界 → 场景与基线验收 → 自审和AOCI收尾。开始实施时更新tasks/todo.md；当前只写Spec，不改任务历史。

### 3.3 验收矩阵与验证范围

| 编号 | 必须观察到的行为 |
| --- | --- |
| A1 | 本周／下周七天及跨月年矩阵正确；周日下周末含下周六 |
| A2 | 日期混用与上个／下下周末拒绝；粗住宿范围外零错日答案、零转交 |
| A3 | 独立无模型日期自行绑定；模型范围含完整周末可用、只含周六不可用；不借无关住宿日期 |
| A4 | 无期限可答，同对象例外分段；缺失日最终文案有日期、正常日答案保留；中英文各覆盖单日段、跨年、多日段、正常＋缺失日，单日为 `YYYY-MM-DD：`、多日不变、年份完整；带日期前缀的门锁凭据仍为out_of_scope专门拒绝 |
| A5 | 正常＋冲突及反向、缺失＋冲突、review、period_veto、预算不变成普通缺失；非周末跨期同样成立 |
| A6 | 模型选择实际提供的缺口：阻断单元零转交记录零卡片作业；纯缺资料正例恰一条记录和作业；最终回复不虚报成功 |
| A7 | 同／异对象的选中、未选、越界及已有工具结果协议回退按§2.4；纯静态模型不可用仍异常 |
| A8 | 模型等待跨午夜不变日期；跨项不借失效、别房源或带条件条目 |
| A9 | 分段各合法但整体超限被挡；日期标注不截断条件、不绕过凭据拒绝 |
| A10 | 新项三次必过，旧稳定保护不减，fixture/基线覆盖一致；最终all缺结果或必过退步阻断；K-周末分日正确答案通过，时刻互换、错日期、夹带7:30、空结果判失败；离线断言错来源失败 |

本地相关验证（外部契约关闭）：

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q tests/unit/test_item_coverage.py tests/unit/test_reply_fix2_items.py tests/unit/test_knowledge_evidence_gate.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py
.venv/bin/ruff check src/homestay_bot/services/item_coverage.py src/homestay_bot/services/knowledge_evidence_policy.py src/homestay_bot/integrations/deepseek_client.py tests/unit/test_item_coverage.py tests/unit/test_reply_fix2_items.py tests/unit/test_knowledge_evidence_gate.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py
.venv/bin/mypy src/homestay_bot
```

默认不跑全仓、全部PostgreSQL并发或浏览器，当前未变对应边界；真实SQLite最终链路必须覆盖新转交正反例。问房控制复用仍有效相关结果。若触及装配、依赖、构建或数据库竞争，按AGENTS重新选验证。当前文档阶段仅静态检查和AOCI维护，不运行上述业务测试。

发布前满足项目部署两路径之一：有效的本地最终全量，或获准推送后本次CI通过，不机械要求两者重复。真实模型all独立执行；接口受理不代替实际收件，是否需要测试号收发依最终影响和既有证据判断，仍需当前授权。

### 3.4 待确认决策与授权

v1记录D-W1、D-W2、D-W3的选择；本次不把文档里的历史选择当作当前外部许可。v2完整方案仍待确认，尤其以下取舍：

- D-W1/W2沿用：中文日历范围；周日只裁本周末，泛指周末的近似按§3.1。
- D-W4：不同对象保留D-G8整项保守否决，不新建逐日期模型选择；原文单元任一段阻断均不可转交。
- D-W5：新增两项独立三次必过，再晋升must_pass并执行最终all；继承既有稳定集合。
- D-W3授权：用户已选“做到部署前”。Spec完整确认且用户明确回复“开始”后，执行本地实施、相关测试、§2.5的限定真实模型验收与最终all门禁（调用真实DeepSeek，不涉及Hostex、企业微信或生产写入），以及生产只读核对。提交、推送、部署、测试号收发不在此授权内，届时分别当面确认。
- D-W6：单日分段日期写法，见§2.4，推荐单日 `YYYY-MM-DD：`、多日保持现行ISO范围。

本地撤销限于本次补丁，不整文件回退已有fix3修复或用户改动。无新迁移；获准发布后的代码回滚及数据恢复边界由发布记录据实说明。


## 实施期修订（2026-10-14，用户决定）

- 候选门禁 rc-1.70.1-1：K-周末早餐三次全过，晋升 must_pass。
- K-周末分日 1/2 次通过。第 2 次失败已定位到「规划决定」：规划模型没给「9999房间…」填 `target_room`，`_fact_layout` 按 1.70.0 起就有的房号保守规则，把小句记为 scope_unbound。周末日期本身没问题，离线预检证明周末分天作答、来源与日期一一对应。没有证据表明是虚构房源装配问题，所以没有换用 302。
- 用户决定把 K-周末分日留作已知未通过，并写明原因，不阻断发布。§2.5 第 6 步「任何一次失败均禁止发布」只对此项例外。规划漏填房号的问题另行评估，本版不改。
