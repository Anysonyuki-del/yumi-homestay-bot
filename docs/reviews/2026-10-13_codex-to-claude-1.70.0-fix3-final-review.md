# Codex → Claude：1.70.0 fix3 终审复核

- 日期：2026-10-13；工作区 `/Volumes/02/code/homestay-bot`，main，HEAD `80f9b2c309547c36c15cfd142d5dd30b98d87406`。
- 审查对象：[Claude 终审交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-12_claude-to-codex-1.70.0-fix3-final-review-handoff.md)、[fix3 Spec v2](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix3-spec.md)，以及当前实现、调用链、测试和 rc3/rc4 原始门禁结果。
- 最新结论（2026-10-13）：**日期修复及已确认Spec §6闭合；303项相关用例和rc7全量模型门禁通过，可以进入已授权提交推送部署。** §1–§5、§6及§7保留历史审查与阻断，§8–§9给出最终修复、自审与门禁证据。
- 首次审查仅新增报告与离线反例；用户后续授权修复、Spec §6开始及发布的实施过程按§6–§9追加。截至§9已做真实模型门禁及生产只读基线核对，提交与部署待执行，未发送真实测试消息。AOCI托管资产按收尾规则维护。

## 1. P1：相对日期小句继承整个住宿区间，错日期答案进入最终回复

### 依据与反例

固定业务日期为 2026-10-07。知识库只有 2026-10-10 有效的“早餐7:30开始”，10月11日没有早餐资料。问法均使用经生产 `verify_turn_plan` 核验通过的同一个粗粒度 `stay_query`，入住10月10日、退房10月12日；模型和房态工具使用合成响应，知识查询、助手取证、会话、事务发件箱是真实实现。

| 客人问法末句 | 正常路径最终结果 | 协议失败回退 | 正确边界 |
| --- | --- | --- | --- |
| 10月11日早餐几点开始 | 不包含7:30；资料缺失，模型选择后转交一条 | 不包含7:30；未确认，零转交 | 当天缺资料可以转交 |
| 明天早餐几点开始 | 包含标为10月10日的7:30；同时转交一条 | 仍包含10月10日7:30；零转交 | 明天是10月8日，位于既有检索范围外，应未绑定、零转交 |
| 第二天早餐几点开始 | 包含标为10月10日的7:30；同时转交一条 | 仍包含10月10日7:30；零转交 | 若明确指入住第二天，只能用10月11日资料；无法绑定时回未确认，不能列出第一天早餐 |

正常路径直接在助手实际发出的 `gap_units` 信封里选择早餐编号，没有编造候选编号。故“明天”的错误不只影响正文，也会把范围外问题标成 `knowledge_missing` 并真实写入一条合成转交记录。回退路径的三条 JSONDecodeError 是脚本故意返回非JSON响应所触发，不是外部接口故障。

复核脚本：[date_inheritance_probe.py](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13-fix3-final-review-evidence/date_inheritance_probe.py)。退出0表示以上反例与对照成立，**不表示修复通过**。

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python docs/reviews/2026-10-13-fix3-final-review-evidence/date_inheritance_probe.py
```

### 根因与牵连路径

1. [item_coverage.py::_RELATIVE_DATE](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:107) 认出“明天”，没有认出“第二天”。[static_clause_dates](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:228) 只拒绝**同一小句**里相对词与绝对日期同时出现的情况；纯相对日期小句没有日期端点，落到 `return dates`。未识别的“第二天”也被当成不点日期。
2. [deepseek_client.py::DeepSeekGuestAssistant._item_dates](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1906) 合法地返回粗粒度项的住宿范围10月10日至11日，但这只能证明候选检索范围，不能证明后面每个静态小句都问这个范围。
3. [_fact_layout](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1495) 把错误范围存入 `static_dates`；[_item_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1648) 用它调用 `_static_item_parts`，输出10月10日答案和另一日的资料缺口。[_gap_unit_offers](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1569) 也沿同一错误布局给出可转交选项；不是仅修改最终正文就能解决。
4. 已有 [item_coverage.py::clause_target](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:321) 对工具覆盖设置了“多种绝对日期与相对日期不能盲目继承”的限制。静态取证新路径没有消费这个父项约束，形成两个不同的日期继承合同。`_shared_grounded` 同样消费 `static_dates`，后续修复必须覆盖跨项复用。

这属于 fix3 日期修复的残留缺口；rc3 的静态取证也没有完整日期守卫，不能把该反例称为已证明由 rc4 新引入的回归。Claude“未发现新阻断”与我此前自审均漏掉了这个边界。[Spec §2.3](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix3-spec.md:65) 要求日期无法绑定时回 `scope_unbound`、不转交，此处未实现完整。

### 建议最小修复

先修订 Spec，明确**父项检索日期与小句所问日期是两种资格**，然后在 `_fact_layout` 形成共享绑定结果时修复。复用现有 `clause_dates`、`clause_target` 的继承约束及现有本地日期来源，不在最终正文、转交和回退里分别补规则。

- 区分“不含日期”“已绑定到某一天”“含日期但未绑定”。只有第一类可以继承整个项范围。相对日期尚未解析，不能等同于没点日期。
- 对粗粒度项中的相对日期小句，没有足够证据确定目标日时，保守记 `scope_unbound`，不用父区间作答、不提供转交编号；若能确定单日，只允许核验既有检索范围内那一天。日历相对日期使用本地今日，住宿序数日必须有明确入住锚点，不能用父项入住日代替“明天”。
- 将“第二天/次日”等明确日期引用纳入同一绑定合同。只向正则增加一个词仍不足：即使识别成功，当前纯相对日期分支仍然会返回整个区间。保守未绑定可以作为本轮最小闭合，不要求新增通用日期解析器或模型调用。
- 保留真正无日期的跨期政策回答、合法绝对单日、入住/退房端点、独立细分计划项。不要为修复粗项而一律拒绝所有相对日期，或删除现有整段住宿回答能力。

必要验收集中在同一个单元：上述三种问法 × 正常/回退、粗粒度/细分计划；当天缺资料的合法转交；范围外的零候选/零记录/零卡片；跨项复用；真正无日期的跨期答案。断言最终发件箱没有错日答案，不能只断言额外出现了“未确认”。补充“住两晚，只问第二天”且两天答案不同的正反对照，证明不是仅靠日期标签隐藏错误。

修复会改变公共回复取证路径：离线相关验证通过后，rc4 模型门禁对新源码失效，需要新的 `REPLY_GATE_SCOPE=all` 证据；真实外部调用仍须当次授权。

## 2. P2：门禁首轮差异说明错误，但当前通过判定可以复算

[Claude 报告61行](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-12_claude-to-codex-1.70.0-fix3-final-review-handoff.md:61) 和上一交接§9.3写“差额都在已知未通过集合内”，原始结果不支持：

| 场景 | rc3 首轮 | rc4 结果 | 当前基线归属 |
| --- | --- | --- | --- |
| K-厨房-C | 通过 | 失败、通过、通过 | 必过；补跑后无退步 |
| MT-延迟周六 | 通过 | 失败、通过、通过 | 必过；补跑后无退步 |
| SR-遗失 | 三次通过 | 仅一次失败，最终正文只剩动作占位，缺30天 | 已知未通过；rc3提议晋升但尚未提交基线 |
| SC-401有上下文 | 通过 | 仅一次失败 | 已知未通过 |
| U-充电桩 | 失败 | 通过 | 必过；本轮首轮恢复 |

所以是4项首轮通过→失败，1项失败→通过，净减少3项。[reply_regression.py::is_regressed/needs_rerun/gate_verdict](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py:134) 按现行已提交基线补跑必过项，厨房和延迟退房各仅失败1/3；“无基线退步”成立。

本次离线复算 `gate_verdict` 与 rc4 原始 `verdict` 完全相同；154项记录的 `judge.ok` 与 `outcomes` 一致、场景文件 SHA-256 匹配，提议基线匹配。无记录层面的运行错误。首轮数字下降本身不要求再跑整个真实模型门禁；SR-遗失的漏答应如实保留，不能从一次失败推断“已稳定失败”，也不能把它写成已通过。

建议更正两份报告中的差额表述，保留实际补跑序列。不要为回避漏答修改判据或缩小必过集合。若要确认 SR-遗失是否为模型波动，应做该场景的有界复核；当前有日期阻断，优先在修复后的新全量门禁中观察，不再单独重复旧候选全量。

## 3. P3：引号密码的保证措辞需要限定，不重开 D-H3

[Spec51行](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix3-spec.md:51) 的“冒号或引号赋值照常遮盖”和上一交接§8.3“冒号/引号赋值仍遮”过于宽泛：

```text
password is 'needed'  → 原样；contains_sensitive_text=False
password is 'needed.' → [credential removed]；contains_sensitive_text=True
密码 OK 吗            → [口令已过滤] 吗；contains_sensitive_text=True
```

[sensitive_text.py::_redact_english_credential](/Volumes/02/code/homestay-bot/src/homestay_bot/services/sensitive_text.py:34) 去除外层引号后判断是否含数字/符号，因此带引号的纯字母 `is` 值也在 D-H3 的漏识别范围内。[complaint_review_job.py::SQLAlchemyComplaintMessageContext._sanitize](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_review_job.py:67) 和管理员入库校验都消费该共享规则，不能声明引号单独构成可靠遮盖保证。

建议仅澄清文档：冒号/等号的引号值完整遮盖；`is` 的引号值去掉引号后仍须含数字/符号，纯字母仍可能漏识别。D-H3 已明确接受此取舍，**本次不把它另列为代码阻断，也不自行改行为**。“密码 OK 吗”属保守误遮，影响可读性和来源可用性；当前证据不要求为这两个字新增消歧白名单。

## 4. 已核验与未覆盖

- rc4 标签解析为 `92fb52cdca0307407924ef775961892ce4b04f9d`。逐个读取候选树中 `src/`、`tests/`、`scripts/`、`migrations/`、`pyproject.toml`、`requirements.lock` 的400个文件与工作区原始字节比较，全部一致；这些范围内未跟踪但未纳入候选的额外文件为零。本次没有改这些输入。
- [KnowledgeAdminService.start_referral_draft/convert_referral/close_referral](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:652) 的状态/代次条件更新、失败回滚和空来源零作业，以及 [SQLAlchemyKnowledgeReferralRepository.list_recent/count_open](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/knowledge_referrals.py:163) 的开放优先分页和数据库计数，本轮源码复核未发现新增问题。复用先前有关事务与分页的本地证据，没有重新跑全仓。
- 2926 passed/15 skipped 是此前 Codex 验证；220 passed 是 Claude 报告的复跑记录，不能写成本次 Codex新跑的用例数。本次新增证据是六组日期最终出站反例、三组凭据对照、候选字节核对和离线门禁判定复算。
- 即使门禁源码匹配，门禁仍只证明运行器覆盖的决策；没有覆盖本次相对日期反例、最终实际投递与客户收件。D1 独立改写集仍缺失，154个公开固定场景不能证明改几个字都能正确识别。新增定向反例只能成为回归，不能冒充独立留出。
- 门禁可复用的前提是最终代码、场景、判据、基线、依赖与有关运行配置仍被有效证据覆盖。本次原候选满足源码/场景核对，但 **F3-FINAL-R1修复后必须更新门禁，不能沿用 rc4 的 `--skip-reply-gate` 建议**。
- 生产 Docker 日志容量与滚动配置、迁移0035现场状态、CI、部署和真实收件，本次均未核验。旧生产只读记录不能证明10月13日现场状态；后续发布与真实验收须分别授权。

## 5. 交接顺序

1. Claude复核 F3-FINAL-R1，修订一份统一的日期绑定 Spec，写明保守未绑定与可确定单日的边界及上述验收，不分别修正文、卡片和回退。
2. 更正门禁差额和引号密码两处证据表述。保留已接受的 D-H1/D-H3，不以修文档为名改变业务取舍。
3. 按项目确认与“开始”门禁实施；相关离线验证及修复后全量真实模型门禁完成后，再给出可发布结论。

本报告是审查与修复建议，不授予修改业务代码、提交、推送、部署、外部调用或生产写入权限。

## 6. 用户「修复」后的本地实施交接（2026-10-13）

用户当前明确授权修复，按已有审查的保守方案更新fix3 Spec §5并实施；不重复询问已授权的本地步骤。业务只改 `services/item_coverage.py::static_clause_dates/_RELATIVE_DATE` 与 `integrations/deepseek_client.py::DeepSeekGuestAssistant._fact_layout/respond`：日历单日按本轮冻结的今日解析并核验已有范围，未解析序数日/范围不再走无日期继承分支。正常、回退、缺口选项和跨项复用继续消费共享布局，无新依赖、迁移或模型调用。

| 闭合项 | 修复后证据 |
| --- | --- |
| 明天在10月10日至11日范围外 | 实际今日10月7日，最终无7:30、无候选、无转交记录/卡片，缺口为scope_unbound；正常/回退均覆盖 |
| 第二天、第2天、次日、英文next day | 无可靠住宿锚点时未绑定；不混入第一天事实、不给转交选项 |
| 明天在已有范围内 | 10月7日入住、10月10日退房，只答10月8日8:30，不混10月7日7:30；粗项/细分项与回退对照 |
| 合法单日资料缺失 | 范围内明天缺资料为knowledge_missing；模型从实际信封选择后恰好一条记录/卡片；回退无模型选择，零转交 |
| 跨项复用及午夜 | 模型选已核验的今天条目也不能借给明天/第二天；检索等待跨午夜后仍使用本轮开始的日期 |

回归位于 [test_item_coverage.py](/Volumes/02/code/homestay-bot/tests/unit/test_item_coverage.py)、[test_reply_fix2_items.py](/Volumes/02/code/homestay-bot/tests/unit/test_reply_fix2_items.py)、[test_reply_fix3_boundaries.py](/Volumes/02/code/homestay-bot/tests/integration/test_reply_fix3_boundaries.py)。修复前新增最终出站回归首两项失败，具体断言为7:30残留，证明测试捕获原缺陷。修复后最终相关验证：

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q tests/unit/test_item_coverage.py tests/unit/test_reply_fix2_items.py tests/unit/test_deepseek_client.py tests/integration/test_reply_fix3_boundaries.py -k 'not conversion and not clear and not draft and not competing and not cancelled and not stale' --tb=short
# 283 passed / 12 deselected，11.86秒；排除未受影响的状态竞争等路径。
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q tests/unit/test_reply_fix2_items.py -k keeps_the_turn_clock --tb=short
# 最后新增跨午夜回归：1 passed / 47 deselected，2.96秒。
```

共覆盖284个不同用例；不把之前44项定向结果或历史全量重复累加。五份源码/测试文件Ruff通过，两份业务源码Mypy通过，diff-check通过。最后仅换行格式化，没有改变已测行为，因此复用相关结果。未改装配、依赖、持久化或前端；没有重复全仓、PG并发和浏览器验收。主代理按最终增量自审调用点、取证/复用/转交消费点及简化边界；不称为另一代理的独立终审。

两份交接的门禁差额与旧门禁复用说明、Spec的引号密码保证已更正；D-H3行为不变。CHANGELOG与1.70.0发布草稿已补记日期修复。§1的脚本保留修复前反例身份，不能用其缺陷断言作为修复后通过要求。

最终业务源码摘要：

| 文件 | SHA-256 |
| --- | --- |
| `services/item_coverage.py` | `4e7188a944a827acdefae557ea82b2e4e1ca28790c4061b766331f3586f00238` |
| `integrations/deepseek_client.py` | `0f83bac0aaa74e59295e08a002f153833d64353c0232cfa6500cf0b852695b63` |

HEAD仍为80f9b2c，工作区已有改动保留，未提交/推送/部署。rc4源码已失效，新的all真实模型门禁、生产日志/迁移现场核对与真实收件均未执行，需当次授权。住宿序数日与周末等未解析范围仍回未确认，这是本轮保守闭合，不声明任意改写或日期表达均可回答；后续支持可靠锚点应修改同一日期合同，不再逐出口打补丁。AOCI以最终维护回执为准。

## 7. 提交推送部署授权后的门禁阻断（2026-10-13）

用户本轮明确授权“提交推送部署”。最终Ponytail自审核对rc4后实际增量、调用方与检索/复用/转交出口，未发现当时已覆盖范围的新阻断；复核两份业务与三份测试摘要一致，284项本地证据仍有效，全仓Ruff、155份源码Mypy通过。候选标签`rc-1.70.0-5`绑定`05f9b5a03f5447d69255a9909983e7363becaf48`，由临时Git索引创建，main未移动，无关Impeccable评审未纳入。

新的`REPLY_GATE_SCOPE=all`结果：154场景首轮138通过，基线136必过/18已知未通过；退步`AV-102`、`K-延迟-X`，新增稳定E-地铁/SR-遗失，安全类未通过0。原始结果保留在本机暂存目录；未晋升提议基线，未推送或部署。

| 场景 | 三次实际结果与依据 | 判断 |
| --- | --- | --- |
| K-延迟-X：“这周六能延迟退房到下午两点吗？” | 三次均未确认；资料9003明确每周六不提供延迟退房。`static_clause_dates`把`这周六`识别为未解析相对日期，返回None；固定今日9月25日、父范围为今日或9月26日的rc4/当前函数对照均成立。 | 当前修复扩大关闭了确定周内单日，稳定影响已有政策答复；应补共享日期合同，不回退盲目继承或改低预期。 |
| AV-102：“我想订102，今晚和明晚两晚” | 第一次AssistantUnavailableError（运行日志JSONDecodeError重试），第二次没调房态工具、仅表示准备查询，第三次调用search_availability并正确答不可订。 | 不能把调用异常归为确定性代码回归；仍不能据2次失败宣称可发布，需独立诊断。 |

日期修订方案、文件符号、约束及验证矩阵已写入fix3 Spec §6草案：只补确定周内单日和独立静态项自身单日目标，取证前确定后共享；粗住宿范围外、序数日无锚点、未解析范围仍未确认。会改变日期目标规则，按AGENTS渐进Spec门禁等待确认和开始，不先改代码。

生产仅做部署前只读核对：服务器仍1.69.1，Alembic current/head均0034，API重启0，数据库容器未变；已登录Chrome诊断确认降级来自百居易回调从未收到，另外8项正常，运行配置revision3。日志无滚动配置为既有状态；未修改运行配置或发送测试消息。部署用本机脚本已补配置/源码/私有上传备份与实际落盘归档复验，语法检查通过，但备份步骤和部署尚未执行。未因门禁失败改变生产。

## 8. 周内单日修订实施（2026-10-13）

用户已确认fix3 Spec §6并明确开始，要求修复后继续发布。`item_coverage.relative_calendar_day/static_clause_dates`只增加可靠的本周/这周单日解析；`DeepSeekGuestAssistant._item_dates/_fact_layout/_merge_item_knowledge/_item_evidence_plan/_shared_grounded`共用检索前冻结的`_ItemLayout.item_dates`。原四处日期重算合并为一处，删除失效参数和重复房号取数；未增加模型调用、依赖或持久化。

独立静态项没有模型日期时按客人明确日历单日建立自身目标；模型错误单日仍被拦截。粗住宿范围外、未解析范围、无锚点序数日保持未确认和零转交，已核验条目不能跨项借错日期。星期一至日/天、跨年和同周已过去的日期、跨午夜、正常与有工具结果的协议回退已覆盖。纯静态主模型完全不可用的现行异常不改，不能把新增测试的替身误解为产品提供了该回退。

修复前周内单日新增回归前两项失败；修复后原相关四份测试命令297项通过，随后新增真实SQLite检索和最终发件箱6项通过，共303个不同用例。后6项使用同一天分别有效的7:30/9:30/8:30条目，正确周六仅8:30，错误模型日期零答案零记录零卡片；两份源码Mypy及相关Ruff通过。最终自审覆盖唯一日期计算调用方、检索/布局/核验一致性和失败出口，真实all仍待新候选结果，不沿用rc5。

## 9. 门禁复跑与最终发布前自审（2026-10-13）

§8修复已经用户确认并开始，303项相关验证、Ruff/Mypy有效。rc6全量首轮137/154，K-安静-C与PI-门锁退步，周六例外和AV-102首轮通过；未改预期或136/18基线。对两场景各两次真实模型诊断所得四组实际计划/回复，在rc4与当前源码离线重放完全一致且通过，只证明对应样本，不冒充原失败样本的确定归因。请求快照转存失败，计划与回复保留于本机诊断日志；重放重建SDK外壳。

用户“try again”后同一提交`7bcec9db714194fbdbdd8086ff2d1ac265db2d3c`执行一次有界all复跑rc7：通过，154场景首轮138，不退步集合136无退步，新稳定E-地铁、SR-遗失，安全类未通过0。失败记录仍保留；不是154场全过或独立改写集验收。详见发布记录追加表与绑定摘要。

Ponytail最终自审：日期只在`_fact_layout`调用`_item_dates`一次，取证前冻结；`_merge_item_knowledge`、`_item_evidence_plan`、`_shared_grounded`读取同一目标，三处正常/本地/协议回退调用参数已迁移。静态小句越界仍禁止整项取证及转交，接收项跨项复用仍再核日期。周内全部单日、跨年、已过去的同周日期、错误模型目标、粗住宿范围、序数日和跨午夜覆盖有判别力；真实SQLite最终出站证明只发对应日期资料。无新阻断发现，无新增依赖或额外模型请求。

结论：可在已授权范围内提交推送，并在精确提交CI通过、完整备份复验后部署。该结论为主代理自审与真实模型门禁；Claude未审最终日期增量，实际客人/员工收件不在此次授权内。CI、源码部署、数据库、容器与登录页面证据将在发布记录独立追加。
