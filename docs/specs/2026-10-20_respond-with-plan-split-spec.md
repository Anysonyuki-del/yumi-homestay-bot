# `_respond_with_plan` 拆分 Spec（行为不变重构）

- 日期：2026-10-20
- 状态：三段于 2026-10-20 确认；两轮审查修订（§4.1、§4.3）已于同日全部重新确认（§4.2、§4.4）。尚未实施：按 D1-b，要等进行中的改动提交，并收到用户“开始”才动代码。
- 依据：用户 2026-10-20 要求根据 Jev 多维打分审查现状；审查认定本函数是缺陷最集中的超长函数。用户随后要求“写 _respond_with_plan 拆分 Spec 的第一段（现状证据），只读，不改代码”。
- 目标（待第二段细化）：在客人可见回复、工具调用、日志与异常完全不变的前提下，把单个 878 行函数拆成边界清楚的阶段，降低每次修复叠加分支的成本。不在本 Spec 内修改任何回复规则。

## 1. 第一段：现状证据

### 1.1 调查基线

| 项 | 值 |
| --- | --- |
| Git HEAD | `564546ca0d3e558c7839e69d1211690372fc431e` |
| 工作区 | `src/homestay_bot/integrations/deepseek_client.py` 有未提交改动（+9/−9），其中两处落在本函数内（约 4184–4188、4568–4570 行，旅游推荐约束相关）。本段按**工作区源码**描述，行号以工作区为准 |
| 目标符号 | `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._respond_with_plan`，第 4117–4994 行 |

工作区改动属于进行中的其他工作，不是本 Spec 的内容。第二段编写前，以及实施前，都要重新核对行号和符号。

### 1.2 选定依据

Jev 打分只作为线索：本函数第 6 段的综合分是 0.53，“单一职责”0.14。下表的度量和历史都是确定性数据，由 AST 统计和 `git log -L` 得出。

| 度量 | 值 |
| --- | --- |
| 行数 | 878（全仓最长的是 `application_lifespan`，1046 行，但它是组装根，见 §1.10） |
| 局部变量 / `if` / `return` / `await` | 89 / 59 / 14 / 10 |
| 嵌套函数 | `finish`（4239）、`search_group`（4294，46 行）、`tool_rooms_for`（4547） |
| 用到的 `self._*` 成员 | 40 个：34 个辅助方法，6 个依赖属性（知识、搜索、工具执行器、模型客户端、模型名、日期源） |
| 2026-09-01 以来改动本函数的提交 | 26 个，其中提交信息以 `fix` 开头的 11 个 |

叠加历史（摘自上述提交）：1.37–1.39 证据门与审核原文 → 1.40 预算与分阶段耗时 → 1.42 回复边界 → 1.44 周末两种住法 → 1.48 追问沿用话题、服务收费追问 → 1.59/1.60 一句多问联网 → 第二至四批轮次计划贯通（10-07） → 1.70.0 逐项回复 → 1.70.2 房号身份 → 1.70.4/1.70.5 省略追问与工具目标 → 10-19 两次旅游推荐约束修复。每一轮都直接在本函数里加分支或加局部变量。函数内的注释引用了十余个 Spec 条款（F1、F3、§2.1(2)(3)、§2.4、§2.7、§12 F12-1/F12-2、D-F1、D-G10、V4-R7、RV177-3 等）。

### 1.3 调用关系与对外契约

- 唯一调用方是同类的 `respond`（第 4033–4095 行，调用点在 4079）。`respond` 先拦截只问内部系统的问题（4056），然后复用或重新生成 `PlanOutcome`（4068–4077），调用本函数，再给结果附上 `turn_plan` 与 `referral_candidates`（4093）。`AssistantUnavailableError` 抛出时补带 `plan_outcome`（4089–4092）。
- 输入：`language`、`messages`、`customer_context`、`request_context`、`tool_trace_sink`、`stage_timing_sink`、`guest_history`、`plan`。
- 输出：`AssistantDecision`，或者抛出 `AssistantUnavailableError`。
- 函数内的副作用：调用知识检索、联网搜索、DeepSeek 主模型、只读工具执行器，以及 `_refine_reply`；写入 `tool_trace_sink`、`stage_timing_sink` 和日志。函数内不写数据库。
- 拆分必须保持不变的可观察面：返回的 `AssistantDecision` 全部字段、异常类型及 `plan_outcome`、工具调用顺序与参数、`tool_trace_sink` 与 `stage_timing_sink` 的事件序列、主模型请求体（system prompt 文本、信封、`tool_choice`、预算判断）、日志文本。

### 1.4 阶段划分（按现行代码顺序）

| 阶段 | 行 | 职责 | 产出并被后续阶段读取的值 |
| --- | --- | --- | --- |
| S1 轮次准备 | 4130–4181 | 取当前问题、判定计划是否可用、追问话题沿用、住宿来源、按正则解析显式或相对日期 | `question_text`、`plan_ok`、`plan_items`、`knowledge_question`、`local_today`、`confirmed_stay`、`property_id`、`target_date`、`target_end_date` |
| S2 切句与问价 | 4182–4237 | 只切一次句；本店事实与店外信息合同；服务收费追问改写 `plan_items`；无日期问价的澄清或**提前返回** | `turn_clauses`、`contract_items`、`public_clauses`、`service_fee_followup`、`price_clarification`；`plan_items` 在这里被改写 |
| S3 收尾闭包 | 4239–4243 | `finish` 统一追加房价日期澄清分项 | 被 8 个出口使用 |
| S4 知识与身份 | 4245–4281 | 固定选择日期；房号身份快照；逐项事实布局；知识检索与逐项知识合并 | `selection_dates`、`room_identities`、`turn_rooms`、`fact_layout`、`knowledge`、`item_knowledge` |
| S5 联网分组 | 4285–4403 | 店外实时小句分组并行搜索；多组时只补一次时效说明；分逐项和整句两种模式判断剩余问题是否已答，满足时**提前返回** | `public_parts`、`remaining_question` |
| S6 FAQ 候选 | 4405–4415 | 读取 FAQ 候选目录与编号；判断是否为独立房态查询 | `faq_candidates`、`faq_candidate_ids`、`standalone_availability` |
| S7 系统提示基底 | 4416–4487 | 约 70 行内联提示文本，含日期换算与输出结构 | `system_prompt` |
| S8 上下文与工具开放 | 4488–4549 | 历史最小化；强制与允许的工具；联网结果交给模型；需模型逐项回答的项；缺口候选；工具筛选目标 | `minimized_messages`、`forced_tool_names`、`allowed_tool_names`、`live_results`、`other_items`、`gap_offers`、`tool_needs`、`tool_rooms_for` |
| S9 请求组装 | 4551–4621 | 构建数据信封；按条件追加 6 段提示规则（4569–4582）；构建请求体与 `tool_choice` | `request` |
| S10 证据计划 | 4622–4641 | 判断本店知识是否接地；构建整句证据计划，计划可用时再置空 | `property_knowledge_grounded`、`evidence_plan` |
| S11 模型与工具循环 | 4642–4994 | 最多 2 次尝试 × 预算内多轮；无工具调用时校验并按 4 种组合方式返回；有工具调用时执行、记录 trace、处理周末两种住法、构建房态回执；按异常类别重试或返回回执 | — |

### 1.5 出口清单

| 行 | 条件 | 返回 |
| --- | --- | --- |
| 4230 | 整轮只问无日期房价或房态 | 直接返回日期澄清（不经过 `finish`） |
| 4401 | 联网分项加本地分项已覆盖剩余问题，且不是服务、交易或待确认住宿 | `finish(联网组合)` |
| 4671 | 字符预算超限，且已有房态回执 | `finish(availability_fallback)` |
| 4719 | 逐项模式组合成功 | `finish(composed)` |
| 4735 | 有工具分项或联网分项 | `finish(模型分项 + 工具分项 + 联网分项)` |
| 4753 | 证据计划或证据选择已接管 | `finish(decision)` |
| 4773 | 其余情况 | `finish(精炼后正文)` |
| 4958、4971 | 预算耗尽或校验异常，且已有房态回执 | `finish(availability_fallback)` |
| 4664、4672、4777、4779、4955、4994 | 预算耗尽、无工具执行器、工具轮次超限、尝试用尽 | 抛出 `AssistantUnavailableError` |
| 4785 | 模型请求了未授权工具 | 抛出 `ValueError`，在 4960 被捕获后重试或返回回执 |

### 1.6 两种模式交错

计划可用时（`plan_ok` 或 `fact_layout` 非空）走**逐项模式**，否则走**整句模式**。两种模式不是在入口处分开，而是在 5 处各自分支：

1. 4361–4393：联网后判断剩余问题是否已答。
2. 4528–4539：哪些项交给模型逐项回答（只在逐项模式计算）。
3. 4638–4641：整句证据计划先算出来，在逐项模式下再置空。
4. 4707–4719：无工具调用时的组合方式。
5. 4898–4936：工具成功后的房态回执构建方式。

因此修改一种模式时，必须同时读懂另一种模式在这 5 处的走向。

### 1.7 跨阶段共享的可变状态

- `plan_items` 在 S2（4202–4209）被改写，S4、S8、S9、S11 读取的都是改写后的值。
- `price_clarification` 在 S2 产生，经 `finish` 闭包影响 8 个出口，在 S8 移除参考价工具（4503–4506），在 S9 追加提示规则（4581）。
- S11 循环内的 `tool_parts`、`availability_fallback`、`model_calls`、`tool_result_rounds`、`cumulative_request_chars` 定义在两次尝试的循环外部，第二次尝试会继承它们（见 §1.8 第 5 条）。

### 1.8 已观察到的结构问题（事实，不含修复方案）

1. **同一个“退房日减一”算了 4 次**：4248（存入 `selection_dates`）、4268–4270、4380–4382、4631–4633，后 3 处没有复用 `selection_dates[1]`。
2. **整句证据计划可能先算后丢**：4626–4637 无条件构建 `evidence_plan`，4638–4641 在计划可用时置空。
3. **主提示词组装分散**：基底在 S7 是内联文本，6 段条件规则在 S9 追加，各规则常量定义在模块其他位置。凡是改到主提示，按 AGENTS.md 都必须跑全量真实模型门禁。
4. **日期解析内联**：4157–4181 用正则解析显式或相对日期。本段未核对它与规划器日期、`selection_dates` 以外的其他日期解析是否重复，留到第二段确认。
5. **疑点（静态推演可达，尚未用测试复现）**：第二次尝试会沿用第一次的 `tool_parts`。可达路径：
   - 第 1 次尝试中工具执行失败，向 `tool_parts` 追加 `query_failed`（4826–4837），此时不设置 `availability_fallback`；
   - 下一次模型响应校验失败（4960），因为没有回执，进入第 2 次尝试；
   - 第 2 次尝试中工具执行成功，追加成功分项并构建回执（4891–4936）；
   - 预算 `main_calls=3`（`src/homestay_bot/services/model_budget.py::ModelBudget`）耗尽，返回的回执同时包含“查询未成功”和“查询成功”两个分项。

   按测试函数名检索没有找到覆盖这条路径的用例。这只说明没找到，不能证明不存在。

   **2026-10-20 修订**：已复现，确认是缺陷。Codex 审查给出离线复现（E1），Claude 在本地重跑同一脚本后确认：现行代码返回 `[query_failed, grounded]`。另外更正一处推演：除 `AssistantUnavailableError` 和列出的校验异常外，第 4986–4993 行的通用 `except Exception` 分支（含 SDK 异常）也会直接 `continue` 进入重试，而且不看 `availability_fallback`。因此第一次尝试中已经成功的查询，在重试时同样会留在 `tool_parts` 里（Codex E2）。
6. **（2026-10-20 新增，Codex E4，Claude 本地重跑确认）同日入住退房的异常会逃出重试**：以原话“2026年10月10日入住2026年10月10日退房；武汉经典景点推荐”为例，带上核验通过的计划时，S10 的 `_static_evidence_plan`（4626）经由 `build_evidence_plan` 读取空计划列表，抛出 `IndexError`。这段代码位于 S11 的 `try` 之外，所以主模型调用 0 次，异常直接抛出 `respond`。这是现有的异常处理缺口，不属于本 Spec 的范围，需要另行定义期望行为后再处理；本 Spec 只要求拆分后保持它不变（见 §2.2 E2 撤回说明）。
7. **（2026-10-20 新增，观察）同一查询在两次尝试中都成功时**，`reply_parts` 会留下两个相同的成功分项，但客人可见正文只出现一次（组合正文时已去重）。这条不影响客人，只作记录，不在本 Spec 处理。

### 1.9 测试与门禁现状

- 直接调用 `.respond(` 的单测共 89 处：`test_deepseek_client.py` 62 处、`test_deepseek_turn_plan.py` 15 处、`test_live_price_scope.py` 4 处、`test_reply_fix2_items.py`、`test_reply_evidence_boundaries.py`、`test_property_lorebook.py` 各 2 处、`test_knowledge_retrieval_eval.py`、`test_answer_policy.py` 各 1 处。本轮没有运行这些测试，也没有测量行覆盖率。
- 与出口相关的已有用例（按名称）：
  - `test_deepseek_client.py`：`test_request_over_character_budget_stops_before_model_call`（1560）、`test_cumulative_character_budget_stops_before_second_model_call`（1589）、`test_invalid_tool_followup_returns_safe_availability_fallback`（1748）、`test_refinement_failure_keeps_original_reply_for_hard_limit_fallback`（1378）
  - `test_reply_fix2_items.py`：多处以 `fallback` 参数化的逐项用例
- 门禁：`deepseek_client.py` 在 `scripts/release/reply_gate.sh` 的 `REPLY_PATHS` 中。AGENTS.md 规定公共回复流程和主模型提示有改动时一律跑全量真实模型门禁；当前 `tests/fixtures/guest_reply_scenarios.json` 共 160 个场景。行为不变的重构同样会触发全量门禁。

### 1.10 相关但不纳入本 Spec 的热点

| 符号 | 行数 | 9 月以来提交 / fix | 不纳入的原因 |
| --- | --- | --- | --- |
| `application.py::application_lifespan` | 1046 | 22 / 6 | 组装根：内含 39 个嵌套函数（处理器构造与回调），结构规整；`application.py` 装配改动按 AGENTS.md 要求提交前跑本地全量，风险与收益不同 |
| `conversation_service.py::ConversationService._process_model_reply_body` | 367 | 10 / 3 | 同样在回复门禁内；缺陷密度明显较低，可在本 Spec 完成后另行评估 |

### 1.11 第一段待用户确认

1. 以上现状证据是否准确、是否有遗漏。
2. §1.8 第 5 条的疑点怎么处理：先写最小复现测试并作为独立缺陷修复（会改变行为），还是在重构中保持现状、另行处理。
3. 实施时机：建议等工作区里 `deepseek_client.py` 的进行中改动提交之后再开工。
4. 范围：只拆本函数及它独占的嵌套逻辑，不改 34 个辅助方法的签名和行为。是否同意。

## 2. 第二段：功能与拆分合同

### 2.0 第一段确认结论

用户 2026-10-20 原话：“第一段确认。疑点先写最小复现测试、作为独立缺陷处理；等进行中的改动提交后再实施；范围同意只拆本函数。”

| 编号 | 决定 |
| --- | --- |
| D1-a | §1.8 第 5 条疑点不并入重构：先写最小复现测试，作为独立缺陷修复（见 §2.1 阶段 A） |
| D1-b | 工作区里 `deepseek_client.py` 的进行中改动提交后，才开始阶段 A 和阶段 B |
| D1-c | 只拆 `_respond_with_plan` 及其独占逻辑。34 个辅助方法的签名和行为不变，`respond` 不变 |

### 2.1 交付顺序

两个阶段的目标与风险不同，分开提交、分开验收：

- **阶段 A，独立缺陷**：先写最小复现测试，证明 §1.8 第 5 条路径确实会产出互相矛盾的分项（测试先红）；再按 §3 确认的期望行为修复，测试转绿。A 会改变行为，属于回复流程改动。（2026-10-20 修订：缺陷已由 Codex E1 复现并经 Claude 本地重跑确认，阶段 A 不再取消；期望行为见 §3.1 的 D3-1′。）
- **阶段 B，行为不变重构**：基于阶段 A 合入后的代码（A 取消时基于当时的代码）开工。等价对照的基线就是 B 开工前的那份代码。

### 2.2 行为不变合同（阶段 B）

同一组输入下，拆分前后以下可观察量必须**逐项相同**：

1. **主模型请求**：每次 `chat.completions.create` 的完整参数，包括 `messages`（system prompt 全文和数据信封）、`tools` 及顺序、`tool_choice`、`response_format`、`max_tokens`、`extra_body`；调用次数与先后顺序。
2. **联网搜索**：`_tourism_searcher.search` 每次的参数（`question`、`language`、`queried_on`、`footer`）、调用次数；多组仍用 `asyncio.gather` 并发。
3. **工具执行**：`_tool_executor.execute` 的名称、参数和顺序；周末两种住法仍按顺序执行。
4. **知识检索**：`_knowledge.retrieve` 的全部参数，包括 `reserved_property_slots`。
5. **返回值**：`AssistantDecision.model_dump()` 完全相同，并且以下序列化时会被排除、但会话层要用的字段也必须逐项相同（2026-10-20 按 Codex SP-R2 修订）：
   - `gap_units`（`exclude=True`，第 448 行）
   - 经 `respond` 得出的 `referral_candidates`（第 450 行）与 `turn_plan`（第 444 行）
   - 每个 `ReplyPart` 的 `gap_unit`（`src/homestay_bot/services/reply_plan.py` 第 34 行）

   比较时保留顺序、原因、房源和计划来源身份。会话层在 `conversation_service.py` 第 1950–1953 行据此转交缺口、删除已转交的未确认分项，这些字段不能当作内部细节。不得为方便对照去改生产模型的 `exclude=True`。
6. **异常**：类型、重试条件和次数；`model_calls` 预算继续跨两次尝试共享。
7. **观测**：`tool_trace_sink` 事件序列（耗时字段除外）；`stage_timing_sink` 阶段名序列（`knowledge`、`tourism_search`、`faq_context`、`main_call`、`tool:*`、`refine`，耗时值除外）；日志模板和非耗时参数。

**允许的内部等价改写**（必须在 §2.5 的对照中覆盖）：

| 编号 | 改写 | 等价依据 |
| --- | --- | --- |
| E1 | 4 处“退房日减一”统一读 `selection_dates[1]` | `target_end_date` 只在 S1（4151–4181）赋值，之后不再改写；4 处表达式完全相同 |
| ~~E2~~（2026-10-20 撤回） | ~~整句证据计划只在计划不可用时计算~~ | 撤回原因：调用链虽然没有日志、缓存或实例写入，但计算本身可能抛异常（§1.8 第 6 条）。改成按需计算后，原来抛出 `IndexError` 的输入会变成正常返回，违反 §2.2 第 6 条。阶段 B 保留“先算后丢” |
| E3 | 系统提示基底（4416–4487）改成模块级函数返回 | 返回字符串逐字节相同；6 段条件规则的追加顺序不变 |

**不允许**：合并或重排两种模式的判断、改提示文本或常量、改并发方式、改预算、改辅助方法签名、改 `respond`、顺带修复其他问题。拆分过程中发现的新疑点，记录下来另行处理。

### 2.3 目标结构

新结构仍放在 `deepseek_client.py` 内。不新建模块的原因：新模块必须同时加进 `REPLY_PATHS`，并由 `tests/unit/test_release_scripts.py` 核对，会多出一处改动面。

**模块私有数据类**（`frozen=True`，只在本文件内使用）：

- `_TurnContext`：S1、S2 的产出。字段为 `question_text`、`knowledge_question`、`plan`、`plan_ok`、`plan_items`（已应用服务收费改写）、`local_today`、`property_id`、`target_date`、`target_end_date`、`selection_dates`、`turn_clauses`、`contract_items`、`public_clauses`、`service_fee_followup`、`price_clarification`、`early_reply`（4230 的提前返回；为 `None` 表示继续）。
- `_TurnKnowledge`：S4 的产出。字段为 `room_identities`、`turn_rooms`、`fact_layout`、`knowledge`、`item_knowledge`。
- `_MainRequest`：S6–S10 的产出。字段为 `request`、`forced_tool_names`、`allowed_tool_names`、`live_results`、`other_items`、`tool_needs`、`evidence_plan`、`property_knowledge_grounded`、`faq_candidate_ids`。

**循环状态**：`_LoopState`（可变，不冻结），字段为 `tool_parts`、`availability_fallback`、`model_calls`、`tool_result_rounds`、`cumulative_request_chars`。它把“每轮共享”与“每次尝试独有”的状态写清楚，阶段 A 的修复也落在这里。

**方法与函数**（S 编号对应 §1.4）：

| 新符号 | 来源阶段 | 职责 |
| --- | --- | --- |
| `_resolve_turn_dates(...)`，模块级纯函数 | S1，4148–4181 | 按请求上下文、已确认住宿和问题文本得出 `target_date` 与 `target_end_date` |
| `_main_system_prompt(language, local_today)`，模块级纯函数 | S7 | 返回系统提示基底（E3） |
| `_prepare_turn(...) -> _TurnContext` | S1、S2 | 轮次准备、切句、合同小句、服务收费改写、问价澄清 |
| `_with_price_clarification(ctx, decision)` | S3 | 替代 `finish` 闭包，用于原来的 8 个出口 |
| `_load_turn_knowledge(ctx, language, stage_timing_sink) -> _TurnKnowledge` | S4 | 房号身份、逐项布局、检索与合并；记录 `knowledge` 阶段 |
| `_search_live_group(...)` | S5 | 原嵌套函数 `search_group` |
| `_answer_live_clauses(...) -> tuple[list[ReplyPart], AssistantDecision \| None]` | S5 | 联网分组、时效说明、两种模式下的剩余问题判断；第二个返回值对应 4401 的提前返回 |
| `_build_main_request(...) -> _MainRequest` | S6–S10 | FAQ 候选、工具开放、信封、条件规则追加、请求体、`tool_choice`、整句证据计划（保留“先算后丢”，E2 已撤回） |
| `_run_main_loop(...) -> AssistantDecision` | S11 | 尝试循环与预算判断、异常分类和重试 |
| `_decision_without_tools(...)` | S11，4691–4775 | 校验结果，以及 4 种组合出口 |
| `_run_tool_calls(...)` | S11，4776–4954 | 执行工具、记录 trace、处理失败分项、周末两种住法、回填工具消息 |
| `_availability_receipt(...)` | S11，4891–4936 | 按两种模式构建房态回执 |

拆完后，`_respond_with_plan` 只负责按顺序调用这些方法并处理提前返回，目标在 60 行以内。新方法单个不超过 150 行（软目标）。两种模式的 5 个分支点各留在所属阶段方法里，本 Spec 不尝试统一两种模式。

### 2.4 实施步骤

每一步都可以单独运行，并保持 §2.2 的行为不变。每步完成后跑 §2.5 的离线对照，结果不一致就停在这一步排查，不进入下一步。

| 步骤 | 内容 |
| --- | --- |
| B1 | 抽出纯函数 `_resolve_turn_dates`、`_main_system_prompt`；E1 |
| B2 | 引入 `_TurnContext`、`_prepare_turn`、`_with_price_clarification` |
| B3 | 引入 `_TurnKnowledge`、`_load_turn_knowledge` |
| B4 | 引入 `_search_live_group`、`_answer_live_clauses` |
| B5 | 引入 `_MainRequest`、`_build_main_request`；保留整句证据计划的“先算后丢”（E2 已撤回） |
| B6 | 引入 `_LoopState`、`_run_main_loop`、`_decision_without_tools`、`_run_tool_calls`、`_availability_receipt` |

### 2.5 等价性验证合同（具体命令和范围在第三段）

1. **离线对照**：用一个临时脚本，不入库。它复用 `src/homestay_bot/tools/reply_regression.py::_Runner._respond` 的确定性装配（虚构知识、`FakeHostexClient`、固定 `today`），把主模型和联网搜索换成按脚本应答、并记录全部调用的替身。对 `tests/fixtures/guest_reply_scenarios.json` 的 160 个场景，各跑以下几种固定应答脚本：
   - 直接回答
   - 先调用工具再回答
   - 第一次返回非法 JSON 触发重试
   - 工具执行失败
   - 预算耗尽
   - （2026-10-20 按 Codex 补充）第二次模型调用抛出 SDK 类异常，走通用 `except Exception` 重试
   - （同上）成功与失败的组合：成功 A → SDK 异常 → 成功 B；失败 A → 非法 JSON → 成功 A

   逐项比较 §2.2 的可观察量。拆分前后差异必须为零；耗时类字段比较前统一置零。

   对照还必须满足以下三条（2026-10-20 按 Codex SP-R2、SP-R3、SP-C1 补充）：
   - **证据时钟冻结**：`_tool_reply_parts`（第 3985 行）和 `knowledge_evidence_policy.py::_knowledge_part`（第 546 行）会调用 `datetime.now(UTC)` 生成 `fetched_at`。在临时装配里把这两处的时钟冻结为固定值；只允许归一化 `fetched_at` 这一个采集时间字段，不得忽略整个 evidence 对象。来源编号、房间、目标日期、条件和分项顺序仍要逐项比较。
   - **变异自检**：对照工具必须能检出以下进程内变异：清空 `gap_units`、清空 `referral_candidates`、清空分项 `gap_unit`。另外各保留一个有效转交和一个禁止转交的控制样本。检不出这些变异的对照工具，不能作为等价证据。
   - **固定控制输入**：必须包含 §1.8 第 6 条的同日入住退房输入。拆分前后都应抛出 `IndexError`，且主模型调用次数都为 0。
2. **已有单测**：§1.9 列出的相关测试文件全部通过；阶段 A 新增的复现测试保持通过。
3. **真实模型全量门禁**：按 AGENTS.md 要求在发布前运行。它只作为发布证据，不能替代第 1 条的逐项对照：门禁只检查回复片段，看不到请求体是否相同。

### 2.6 非目标

- 不修改任何回复规则、提示文本、阈值或预算。
- 不统一逐项模式和整句模式。
- 不拆 `application_lifespan`、`_process_model_reply_body`，不改 34 个辅助方法。
- 不把离线对照脚本或其中的替身留在仓库里。
- 阶段 B 不发布新的客人可见行为；版本号与发布记录按第三段决定。

### 2.7 第二段待用户确认

1. §2.2 的不变合同与 E1–E3 三项等价改写是否接受。
2. §2.3 的结构：留在 `deepseek_client.py` 内，用 3 个冻结数据类加 1 个循环状态类，新增 12 个方法或函数。
3. §2.4 按 B1–B6 逐步推进、每步都对照。
4. §2.5 用不入库的临时脚本做离线对照，真实门禁只作发布证据。

## 3. 第三段：风险、验证与待确认决策

### 3.0 第二段确认结论

用户 2026-10-20 原话：“第二段确认，继续写第三段。”§2.2–§2.6 的合同、结构、步骤和验证方式均按草案执行。

### 3.1 阶段 A：精确修改点与验证

**复现与控制测试**（2026-10-20 按 Codex SP-R1 修订。缺陷已复现，阶段 A 不再有“测试不红就取消”的分支）

- 位置：`tests/unit/test_deepseek_client.py`。写法沿用同文件 `test_invalid_tool_followup_returns_safe_availability_fallback`（1748）的替身风格：不启用规划、问题带明确日期、主模型按次脚本化应答、工具执行器可以按次失败。

| 用例 | 脚本 | 断言 | 现行代码 |
| --- | --- | --- | --- |
| 复现：同一查询先失败后成功 | 工具调用（失败）→ 非法 JSON → 同参数工具调用（成功） | 只有该查询的成功分项，没有它的 `query_failed` | 红：`[query_failed, grounded]` |
| 控制：不同查询都成功 | 工具调用 A（成功）→ SDK 类异常 → 工具调用 B（成功） | A、B 两组成功事实都保留 | 绿 |
| 控制：失败的查询没有再查 | 工具调用 A（失败）→ SDK 类异常 → 工具调用 B（成功） | A 的 `query_failed` 与 B 的成功分项都保留 | 绿 |
| 复现：周末查询原始参数不同、展开后相同（2026-10-20 按 Codex 复审新增） | 问题只说“周末”；周五入住参数的调用（失败）→ 非法 JSON → 周六入住参数的调用（两种住法都成功） | 只有两组成功分项，没有 `query_failed` | 红：`query_failed` 与两组成功并存（Codex 离线投影） |

三个用例都要同时断言：模型调用次数、工具调用参数和顺序都与现行代码相同。

**修复**（D3-1′，2026-10-20 已确认）

- 位置：`deepseek_client.py::_respond_with_plan` S11 的工具调用处理（失败分项追加在 4826–4837 行，成功分项在 4859–4887 行）。
- 做法（2026-10-20 按 Codex 复审修订）：每次工具调用的查询身份 = 工具名 + **经 `_weekend_argument_options` 展开后**的完整参数组（每个住法的参数各自规范化成 JSON，按展开顺序组成元组）。不能用原始参数：周五入住和周六入住会被展开成同一组两种住法（`_weekend_argument_options`，4997–5020）。记录每个身份对应的失败分项。之后任一次尝试里，同一身份的整组查询全部成功时，删掉它此前的失败分项。一次调用中任一住法失败，整次调用都按失败处理（4797–4845），所以“整组成功”与现有语义一致。参数组只是部分重叠的（例如失败的是周末两组，后来只成功查了其中一个日期），不算同一查询，失败提示保留。其余分项一律保留，包括不同查询的成功事实、没有再查的失败提示，以及缓存的房态回执。在代码旁用中文注释说明：失败提示只在同一查询后来成功时才过时。删除必须发生在第 4891 行重建房态回执之前，保证回执与 `tool_parts` 一致。同一次尝试内的“先失败、后同参数成功”也按同一规则处理。
- 原先“每次尝试开头清空 `tool_parts`”的方案作废：Codex E2 证明它会删掉已经查到的成功事实。原文“回执非空就不会重试”的推演也有误，通用 `except Exception` 分支同样会重试（§1.8 第 5 条修订）。
- 不改异常策略：SDK 异常仍然重试，`availability_fallback` 的生命周期不变。`model_calls`、`tool_result_rounds`、`cumulative_request_chars` 继续跨尝试共享。§1.8 第 7 条的重复成功分项不在本次处理。

**发布文件**

- `pyproject.toml` 的版本号、`CHANGELOG.md`、`docs/releases/<版本>.md`。版本号按发布时实际可用的下一个补丁号取，预计 1.70.8。

**验证（只跑相关项）**

| 项 | 命令或做法 | 说明 |
| --- | --- | --- |
| 复现测试先红后绿，两个控制始终绿 | `.venv/bin/python -m pytest -q tests/unit/test_deepseek_client.py -k retry` | 红测输出写进发布记录 |
| 相关单测 | `.venv/bin/python -m pytest -q tests/unit/test_deepseek_client.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_live_price_scope.py tests/unit/test_reply_fix2_items.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_property_lorebook.py tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_answer_policy.py` | §1.9 中调用 `respond` 的全部测试文件 |
| 静态检查 | `.venv/bin/python -m ruff check src/homestay_bot/integrations/deepseek_client.py tests/unit/test_deepseek_client.py`，以及 `.venv/bin/python -m mypy`（按 pyproject 的 `packages` 配置） | — |
| 本地全量 | 不跑 | 没有改 `application.py`、依赖或构建配置，影响范围可以界定。按 AGENTS.md，部署前改为确认本次推送的 CI 通过 |
| 真实模型门禁 | `REPLY_GATE_SCOPE=all`，由部署脚本调用 `scripts/release/reply_gate.sh` | 改到公共回复流程，按 AGENTS.md 必须全量。会调用真实 DeepSeek，运行前需当次授权 |
| 测试号真实收发 | 不做 | 触发路径需要工具失败、非法 JSON、重试三者同时出现，真实环境无法稳定复现；离线复现测试已覆盖。在发布记录里写明理由 |

### 3.2 阶段 B：精确修改点与验证

**修改范围**

- 只改 `src/homestay_bot/integrations/deepseek_client.py`，新增和改动的符号以 §2.3 为准。
- 不改已有测试：§1.9 的测试都经由 `respond` 调用，没有引用 `_respond_with_plan` 或它的内部符号（检索确认）。
- 代码尽量原样搬移，让 `git diff --color-moved` 能清楚区分“搬移”和“改写”。真正的改写只有 E1、E3 和参数传递（E2 已撤回）。

**离线对照脚本**（放在会话临时目录，不入库）

- **装配**：照 `src/homestay_bot/tools/reply_regression.py::_Runner._respond` 的确定性部分装配 `DeepSeekGuestAssistant`，包括虚构知识 `_MemoryKnowledge`、`FakeHostexClient`、固定 `today`。主模型客户端和联网搜索器换成记录型替身。知识服务只做一层记录型包装，内部仍委托给真实的 `KnowledgeService`，不另写检索逻辑。日志用一个 handler 捕获。联网搜索使用固定响应，多组并发时按调用参数而不是完成顺序记录，避免把调度噪声当成差异。
- **时钟与字段**：按 §2.5 冻结证据时钟、比较排除字段，并通过变异自检。
- **两种模式**：
  - 整句模式：`plan_turns=False`。
  - 逐项模式：`plan_turns=True`，规划调用由替身按固定规则返回计划（每个小句一项，`kind` 用确定性判定给出）。这些计划只用来覆盖分支，不求语义正确。
- **应答脚本**：直接回答；先调用工具再回答；首次返回非法 JSON；工具执行失败；预算耗尽；SDK 类异常；成功与失败组合（见 §2.5）。另加 §1.8 第 6 条的固定控制输入。
- **快照**：B 开工前在基线代码上跑一次，生成 JSONL 快照。B1–B6 每一步之后重跑并逐场景比较 §2.2 的可观察量，耗时字段比较前统一置零。
- **分支命中计数**：同时统计 §1.5 的全部出口和 §1.6 的 5 个模式分支点各命中多少次。没有命中的出口，必须指明由哪个已有单测覆盖；如果没有，就在对照脚本里补一个临时场景。“各分支都被走到”是验收证据的一部分，不能只报“差异为零”。

**每一步（B1–B6）的验证**

- 离线对照差异为零。
- §3.1 表中的相关单测全部通过。

**最终验证**（B6 之后）

- ruff 与 mypy，同 §3.1。
- 用最终代码重跑一次离线对照和相关单测。
- 本地全量不跑，理由同阶段 A；部署前确认 CI 通过。
- 真实模型全量门禁，运行前需当次授权。
- 测试号真实收发不做：行为不变已由逐项对照证明，在发布记录里写明理由。

### 3.3 风险与缓解

| 风险 | 后果 | 缓解 |
| --- | --- | --- |
| 拆分时闭包捕获、变量遮蔽或 `await` 顺序被改变 | 请求体、阶段耗时序列或回复悄悄变化 | 离线对照比较完整请求体、调用顺序和阶段名序列，每步都比 |
| 替身装配与生产不一致（AGENTS.md 记录过多次“各层写对却点不通”） | 对照通过，线上却不同 | 装配直接复用门禁运行器；发布前跑真实模型全量门禁；`respond` 及以外的代码不动 |
| 对照脚本里的合成计划不代表真实计划分布 | 逐项模式的某些组合没有走到 | 用分支命中计数兜底；已有逐项单测（`test_reply_fix2_items.py` 等）继续把关；门禁用真实规划 |
| 进行中的改动与拆分冲突 | 反复变基，或把别人的改动搬进新结构时搬错 | 按 D1-b 等进行中的改动提交后再开工；开工时重新核对 §1 的行号和符号，出入较大就先更新 §1 再继续 |
| 差异很大（约 900 行搬移），评审成本高 | 漏看真正的改写 | 按步骤分别提交；大部分代码原样搬移；只把 E1、E3 和参数传递作为需要评审的改写点 |
| 阶段 A 改变了一条罕见路径的回复 | 同一查询先失败后成功时，客人不再看到矛盾的“查询未成功”；其他失败提示和成功事实不变 | D3-1′ 由你确认；复现与两个控制测试、发布记录写明行为前后对比 |

**回滚**：阶段 A 和阶段 B 各对应一个版本，没有数据库迁移、配置或数据改动。出问题时回退对应提交，重新部署上一个标签即可，不需要数据补偿。

### 3.4 退出条件

- ~~阶段 A 的复现测试在现行代码上不红：取消阶段 A~~（2026-10-20 失效：缺陷已复现）。
- 阶段 B 某一步对照不一致，两轮排查仍定位不到原因：停在上一步已通过的状态，回报差异样本，不带着差异继续。
- 进行中的改动大幅重写了本函数：作废 §1，重新取证后再确认。

### 3.5 待用户确认的决策

| 编号 | 决策 | 推荐 |
| --- | --- | --- |
| ~~D3-1~~ | ~~阶段 A 的期望行为~~ | 2026-10-20 作废，原方案会删除已成功的查询事实（Codex SP-R1） |
| D3-1′（2026-10-20 已确认；查询身份于同日按 Codex 复审改为“展开后参数组”，见 §4.3，已于 §4.4 确认） | 阶段 A 的期望行为 | 同一查询（工具名 + 展开后完整参数组）整组后来查询成功时，删掉它此前的失败分项；其余分项全部保留；异常策略与预算共享不变（§3.1） |
| E2 撤回（2026-10-20 已确认） | 阶段 B 的等价改写范围 | 保留整句证据计划的“先算后丢”，只允许 E1、E3（§2.2） |
| D3-2 | 版本与发布 | 阶段 A 单独发布一个补丁版本；阶段 B 再单独发布一个补丁版本，CHANGELOG 写“内部重构，客人可见行为不变”。备选：阶段 B 不单独部署，随下一次功能发布一起上线 |
| D3-3 | 阶段 B 的提交粒度 | B1–B6 各一个提交，便于二分定位 |
| D3-4 | 真实门禁范围 | 两个阶段都用 `REPLY_GATE_SCOPE=all`，符合 AGENTS.md 对公共回复流程的要求 |
| D3-5 | 外部操作 | 本 Spec 确认不等于授权提交、推送、部署或运行真实模型门禁。这些操作每次实施时都要另行授权 |

完整 Spec 确认后，还需要你回复“开始”才进入实施；按 D1-b，实施要等进行中的改动提交之后。

### 3.6 第三段确认结论

用户 2026-10-20 原话：“第三段确认，D3-1 到 D3-5 全部按推荐。”

| 编号 | 确定方案 |
| --- | --- |
| D3-1 | ~~阶段 A 在每次尝试开始时清空 `tool_parts`~~（2026-10-20 被 D3-1′ 取代，已确认） |
| D3-2 | 阶段 A、阶段 B 各自单独发布一个补丁版本；阶段 B 的 CHANGELOG 写“内部重构，客人可见行为不变” |
| D3-3 | 阶段 B 按 B1–B6 各一个提交 |
| D3-4 | 两个阶段的真实门禁都用 `REPLY_GATE_SCOPE=all` |
| D3-5 | 提交、推送、部署和真实模型门禁每次实施时另行授权；本 Spec 的确认不构成这些授权 |

实施前置条件：工作区里 `deepseek_client.py` 的进行中改动已经提交，且用户回复“开始”。开工时先按 §1.1 重新核对行号和符号，出入较大就先更新 §1。

## 4. 修订记录

### 4.1 2026-10-20 按 Codex 审查修订

依据：`docs/reviews/2026-10-20_codex-to-claude-respond-with-plan-split-spec-review.md`。Claude 在本地重跑了该报告 §5 的离线复现脚本（只用本地测试替身，不访问外部服务）：E1–E4 的断言全部通过，E5 的两次 `fetched_at` 不同。另外直接核对了源码中的 `exclude=True`（`deepseek_client.py` 第 444、448、450 行，`reply_plan.py` 第 34 行）、通用异常分支（4986–4993）和会话层消费点（`conversation_service.py` 第 1950–1953 行）。

| 发现 | Claude 复核 | 处理 | 修订位置 |
| --- | --- | --- | --- |
| SP-R1 / P2：每次尝试开头清空 `tool_parts` 会删掉已成功的查询事实 | 成立。原 §3.1 中“回执非空就不会重试”的推演有误 | 作废 D3-1，改为 D3-1′（只删同一查询后来成功时的失败分项），并新增两个控制测试 | §1.8 第 5 条、§2.1、§3.1、§3.3、§3.5、§3.6 |
| SP-R2 / P2：只比较 `model_dump()` 会漏掉排除字段 | 成立 | 不变合同显式纳入 `gap_units`、`referral_candidates`、`turn_plan` 和分项 `gap_unit`；对照工具必须能检出相应变异 | §2.2 第 5 条、§2.5、§3.2 |
| SP-R3 / P2：E2 会改变异常行为 | 成立（同日入住退房的输入：现行代码抛出 `IndexError`、主模型调用 0 次；按需计算后变成正常返回、调用 1 次） | 撤回 E2，保留“先算后丢”；把该输入设为固定控制；异常缺口记为 §1.8 第 6 条，另行处理 | §1.8 第 6 条、§2.2、§2.4、§2.5、§3.2 |
| SP-C1 / P3：`fetched_at` 让同一份源码的快照也不相等 | 成立 | 冻结两处证据时钟，只允许归一化 `fetched_at` | §2.5、§3.2 |

Claude 另外观察到一条现象，记为 §1.8 第 7 条，不处理：同一查询两次都成功时，分项会重复，但正文已经去重。

**待用户重新确认**：
- D3-1′
- E2 撤回
- §2.2 第 5 条与 §2.5 新增的对照要求

确认之后仍按 D1-b 和项目规则，等用户明确回复“开始”。

### 4.2 2026-10-20 修订确认

用户原话：“修订部分确认：D3-1′、E2 撤回、§2.2 第 5 条与 §2.5 新增对照要求都同意。”

至此全部决策已确认。实施前置条件不变：
- 工作区里 `deepseek_client.py` 的进行中改动已经提交；
- 用户明确回复“开始”；
- 开工时按 §1.1 重新核对行号和符号。

### 4.3 2026-10-20 按 Codex 复审修订

依据：Codex 复审结论（用户在对话中转达）。SP-R2、SP-R3、SP-C1 已在方案层闭合；SP-R1 还剩 1 项 P2，另有 1 处文字残留。

| 发现 | Claude 复核 | 处理 | 修订位置 |
| --- | --- | --- | --- |
| P2：原始参数不能准确标识周末查询 | 成立。`_weekend_argument_options`（4997–5020）只要入住日落在本周末的周五或周六，就展开成同一组两种住法；一次调用中任一住法失败，整次调用都按失败处理（4797–4845） | 查询身份改为“工具名 + 展开后的完整规范化参数组”，整组成功后才删除对应的失败提示；部分重叠的参数组不算同一查询。新增“周五参数失败 → 周六参数成功”复现用例（Codex 提到的普通同参数控制就是测试表第一行，不重复列） | §3.1 修复说明、测试表，§3.5 D3-1′ |
| 非阻断：§2.3 仍写“证据计划（E2）” | 成立 | 改为“整句证据计划（保留‘先算后丢’，E2 已撤回）” | §2.3 |

**待用户重新确认**：D3-1′ 的查询身份定义，以及新增的周末复现用例。确认后仍按 D1-b 和项目规则，等用户明确回复“开始”。

### 4.4 2026-10-20 复审修订确认

用户原话：“D3-1′ 的新查询身份和周末复现用例确认。”

至此全部决策已确认。实施前置条件不变：
- 工作区里 `deepseek_client.py` 的进行中改动已经提交；
- 用户明确回复“开始”；
- 开工时按 §1.1 重新核对行号和符号。

### 4.5 阶段 A 实施记录（2026-10-21）

依据：用户 2026-10-20 回复“开始”。1.70.8 已提交并部署（`v1.70.8` / `cdaf959`），D1-b 的前提满足。开工时按 §1.1 复核：`_respond_with_plan` 仍是第 4117 行起，S11 的相关行号与 §3.1 一致。

**测试先红**：在 `tests/unit/test_deepseek_client.py` 新增 4 个用例，共用两个本地替身（`ScriptedToolCompletionsStub` 按步骤脚本应答；`FlakyToolExecutorStub` 让指定序号的执行失败）。在修复前的代码上：

| 用例 | 修复前 | 修复后 |
| --- | --- | --- |
| `test_retry_drops_failure_of_a_query_that_later_succeeds` | 红：`['query_failed', 'grounded']` | 绿 |
| `test_retry_drops_weekend_failure_when_the_same_expanded_stays_succeed` | 红：`['query_failed', 'clarification', 'grounded', 'grounded']` | 绿：`['clarification', 'grounded', 'grounded']` |
| `test_retry_keeps_success_of_a_different_earlier_query` | 绿 | 绿 |
| `test_retry_keeps_failure_of_a_query_that_was_not_retried` | 绿 | 绿 |

每个用例都同时断言模型调用次数和工具调用参数及顺序。其中 `clarification` 是周末两种住法的说明分项，与本缺陷无关，修复后照常保留。

**修复**（`deepseek_client.py::_respond_with_plan` S11）：
- 新增 `failed_query_parts`：查询身份到失败分项列表的映射，跨重试共享。同一身份如果失败多次，每次的失败分项都记录。
- 查询身份 `query_key` = 工具名 + `_weekend_argument_options` 展开后每个住法参数的规范化 JSON 元组。
- 失败分项照旧追加，并登记到对应身份下。
- 同一身份整组成功后、重建房态回执之前，按对象身份删掉它登记过的失败分项，不按相等比较。
- 异常策略、`availability_fallback` 生命周期和预算共享都不变。

**验证**：
- §3.1 列出的 8 个相关测试文件：**373 passed**。
- Ruff（改动的两个文件）通过；Mypy 156 份源码无问题；`git diff --check` 通过。
- 未运行本地全量：没有改装配、依赖或构建配置，影响范围可以界定。

**尚未执行**：版本号、CHANGELOG、发布记录，以及提交、推送、all 门禁和部署。按 D3-5，这些都需要当次授权。

### 4.6 阶段 A 已发布（2026-10-21）

- 阶段 A 已作为 **1.70.9** 发布：候选 `rc-1.70.9-1` all 门禁通过（首轮 145/160，必过 144 全过，无退步）；正式标签 `v1.70.9` / `b4c6929`；CI success；备份后只替换了 API；独立核对 178 个文件一致；登录后诊断页确认版本无误。详见 `docs/releases/1.70.9.md`。
- 阶段 B 的基线就是 `v1.70.9`。开工仍需用户回复“开始”，并按 §1.1 重新核对行号。

### 4.7 阶段 B 实施记录（2026-10-21）

依据：用户 2026-10-21 回复“开始阶段 B”。基线是 `v1.70.9`（工作区源码与标签一致）。开工时按 §1.1 复核，`_respond_with_plan` 第 4117 行起，S11 行号与阶段 A 修复后一致。

**离线对照脚本**（会话临时目录，不入库）
- 装配：复用 `reply_regression._Runner._respond` 的确定性部分，包括 `_MemoryKnowledge` + 真实 `KnowledgeService`、`FakeHostexClient` + 真实 `HostexReadOnlyToolExecutor`，以及固定 `today`。
  - 主模型替身：读数据信封，按固定规则选证据（奇数项选第一条知识、偶数项不选），对 `other_items` 逐项作答，转交全部候选缺口。
  - 联网替身：固定应答。
  - 知识服务：记录型包装，内部委托真实服务。
  - 两处证据时钟（`deepseek_client` 与 `knowledge_evidence_policy` 的 `datetime.now`）冻结。
- 覆盖：
  - 161 个用例 × 13 种应答脚本 × 有计划/无计划，共 **4212 次运行**。用例为 160 个门禁场景加 1 个同日入住退房控制；同日控制另有一个替身知识服务版本。
  - 应答脚本：直接回答、先工具后回答、首次非法 JSON、工具失败、预算耗尽、SDK 异常、失败→SDK 异常→成功、同参数失败→非法 JSON→成功、两次非法 JSON、工具成功后非法 JSON、未授权工具、无工具执行器、工具执行被取消。
- 比较项：§2.2 的全部可观察量，包括每次主模型请求全文、联网与工具参数、知识服务调用、`model_dump()`、排除字段（`gap_units`、`referral_candidates`、`turn_plan`、分项 `gap_unit`）、异常类型、trace（去掉耗时）、阶段名序列和日志文本。
- 自身可信度：
  - 同一份基线代码跑两次，4212 次运行零差异。
  - 变异自检：清空 `gap_units`、清空 `referral_candidates`、清空分项 `gap_unit` 三类变异都能检出。
  - 控制样本：有效转交 287 次、被拒转交 717 次。
- 基线快照 SHA-256 前缀 `04a4024eb0d3e1a6`（临时目录，不入库）。

**分支命中（基线代码）**
- §1.5 的出口里，除第 4977 行外全部被对照命中。第 4977 行是工具轮次循环耗尽后的 `raise`：按预算，`model_calls` 或 `tool_result_rounds` 一定先在循环内抛出，实际走不到。
- 字符预算超限的两个出口不在对照里，由已有单测覆盖：带回执返回由 `test_cumulative_character_budget_stops_before_second_model_call` 覆盖，直接抛出由 `test_request_over_character_budget_stops_before_model_call` 覆盖。
- `BaseException` 的重新抛出由“工具执行被取消”脚本命中。
- §1.6 的 5 个模式分支点，两侧都有命中。

**§1.8 第 6 条更正**：用真实 `KnowledgeService` 装配时，同日入住退房的输入在 S4 知识检索就抛出 `ValueError`（“知识查询结束日期不得早于开始日期”），主模型调用 0 次，走不到 S10。Codex 审查 E4 的 `IndexError` 出现在不校验日期的测试替身上。两条路径都是主模型调用前就有未处理异常逃出 `respond`，结论不变：属于既有缺口，本 Spec 不处理。对照把两种装配都列为固定控制：真实装配抛 `ValueError`，替身装配抛 `IndexError`，拆分前后一致。

**逐步结果**：B1–B6 每一步之后，对照与基线都是 **4212/4212 零差异**；§3.1 的 8 个相关测试文件每步都是 **373 passed**；Ruff 与 Mypy（156 份源码）每步都通过。

| 步骤 | 内容 |
| --- | --- |
| B1 | 模块级 `_resolve_turn_dates`、`_main_system_prompt`；E1（3 处改读 `selection_dates[1]`） |
| B2 | `_TurnContext`、`_prepare_turn`、`_with_price_clarification`（8 个出口改用它） |
| B3 | `_TurnKnowledge`、`_load_turn_knowledge` |
| B4 | `_search_live_group`、`_answer_live_clauses` |
| B5 | `_MainRequest`、`_build_main_request`（保留“先算后丢”） |
| B6 | `_LoopState`、`_run_main_loop`、`_decision_without_tools`、`_run_tool_calls`、`_availability_receipt` |

**与 §2.3 的偏差**：
1. `_LoopState` 多了 `failed_query_parts`。它是阶段 A 引入的跨重试状态，与其他循环状态同生命周期。
2. `_run_tool_calls` 开头对 `self._tool_executor` 做了一次非空检查。原函数靠同一作用域里的前置判断让类型收窄，拆成方法后需要这次检查；调用方已在进入前拒绝无执行器的轮次，所以这个检查不会走到，行为不变。
3. 两个方法超过 150 行的软目标：`_build_main_request` 202 行（主要是条件规则与请求体），`_run_tool_calls` 174 行。再拆会引入新的跳转，本轮不再细分。
4. `_respond_with_plan` 现为 54 行，达到 60 行以内的目标。
5. `_TurnContext` 不含 `language`、`messages` 等调用入参，入参由各阶段方法显式接收，与 §2.3 的字段清单一致。

**未运行本地全量**：只改了 `deepseek_client.py`，唯一调用方是 `respond`，装配、依赖与构建配置不变，影响范围可以界定；发布时以 CI 为准。

**尚未执行**：按 D3-3 / D3-5，提交（B1–B6 各一个提交）、版本号、CHANGELOG、发布记录、all 门禁、推送与部署都需要当次授权。每一步的文件快照已保存在会话临时目录，可以按步骤重建 6 个提交。

### 4.8 1.70.10 门禁不通过与对照补齐（2026-10-21）

- **更正 §4.7**：§4.7 的合成计划只生成 4 种事项类型，没有覆盖 catalog_query、facility_fault、unclear、lost_item_report、unrelated、booking_request、chitchat 这 7 种。“4212/4212 零差异”只证明了这 4 种类型和无计划路径不变，原文没有写明这个范围，特此更正。
- **门禁**：候选 `rc-1.70.10-1` all 门禁不通过，退步场景是 SR-遗失（`lost_item_report`），结果为失败、通过、失败。未重跑，未发布。
- **补齐**：从三次门禁记录还原出 175 种真实规划（148 个场景，覆盖全部 11 种类型），加入对照。v1.70.9 worktree 与候选 **6487 次运行零差异**，已确认两边导入的是各自的源码。
- **结论**：现有证据支持 SR-遗失的失败来自模型输出波动（该场景在重构前已多次首轮失败），而不是重构。是否接受为发布例外，由用户裁决（见 `docs/releases/1.70.10.md`）。

### 4.9 阶段 B 已发布（2026-10-21）

- 阶段 B 已作为 **1.70.10** 发布：正式标签 `v1.70.10` / `aced310`；B1–B6 对应提交为 `681b838`..`9169db0`；CI success；备份后只替换了 API；独立核对 178 个文件一致；登录后诊断页确认版本无误。
- 候选 all 门禁未通过（SR-遗失），由用户 2026-10-21 接受为发布例外。依据是 §4.8 的真实规划对照（6487 次零差异），以及该场景在重构前已有的不稳定记录。
- 本 Spec 的两个阶段均已完成。SR-遗失的不稳定、§1.8 第 6 条（同日入住退房的异常逃逸）和第 7 条（重复成功分项）另行处理。

### 4.10 2026-10-21 审查后更正（Codex 会话总交接审查 SH-R1、SH-R2）

- **SH-R1（已复现）**：B6 改变了房态回执的赋值时机，不满足 §2.2 的行为不变合同。
  - **原逻辑**：先把初始 `AssistantDecision` 赋给 `availability_fallback`，再逐步应用证据计划与组合分项。中途抛出异常时，外层能看到这份半成品回执。
  - **B6 之后**：改为 `state.availability_fallback = self._availability_receipt(...)`，整份构造完才赋值。中途出错时，外层看到的仍是此前状态：没有回执就重试，已有完整回执就返回那一份。
  - **复现**：Codex 附录 A 在本地复现。首次 `_compose_items` 注入一次 `ValueError`：v1.70.9 返回空正文、0 个分项、模型调用 1 次；1.70.10 重试后返回房态、1 个分项、模型调用 2 次。不注入时两版一致。
  - **漏检原因**：§4.7 的离线对照没有在回执构造过程中注入故障。
  - **处理**：这是对 §2.2 合同的违反，已随 1.70.10 上线。接受为明确的行为变化，还是恢复原时机，等用户裁决；在裁决前，不改代码。
- **SH-R2（已复现）**：门禁规划摘要（`reply_regression.plan_summary`）不保留 `subject`、归一化 `question`、`target_room`、日期、`withdraws`。摘要相同的两个合法计划，产生的主模型请求可以不同（Codex 附录 B 在本地复现）。
  - §4.8 的“还原真实规划”应读作“按摘要重建计划变体”。
  - “6487 次零差异”只在重建计划、固定应答与装配范围内成立。
  - SR-遗失失败“来自模型波动”只是倾向性判断，不是证明。

### 4.11 SH-R1 裁决（2026-10-21）

用户 2026-10-21 裁决：“SH-R1 选 A：接受为明确行为变化，补 3 个回归用例锁定语义；文档修订和用例一起提交推送（不改生产代码、不发版）。”

§2.2 的行为不变合同对这一路径作出明确例外：回执构造中途失败时，不发布半成品回执（语义 A，见 `docs/reviews/2026-10-21_claude-to-codex-session-handoff-review-reply.md` §2）。

`tests/unit/test_deepseek_client.py` 新增 3 个用例：
- `test_receipt_fault_without_earlier_receipt_retries_instead_of_empty_reply`：此前没有回执时，首次回执构造失败会重试并恢复；
- `test_receipt_fault_keeps_earlier_complete_receipt`：已有完整回执时，后一次构造失败仍返回原来那份完整回执；
- `test_receipt_fault_every_time_ends_in_existing_unavailable_exit`：回执每次都构造失败时，走既有的不可用出口。

判别力：3 个用例在当前代码上通过；把同一测试文件放到 v1.70.9 源码上运行，3 个都失败（两个返回空回执，一个没有抛出不可用异常）。相关 8 个测试文件 376 passed，Ruff 通过。没有改生产代码。
