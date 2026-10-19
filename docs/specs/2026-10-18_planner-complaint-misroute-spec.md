# 设施报修被规划复核误判为客诉：定位与修复 Spec（拟 1.70.6）

- 日期：2026-10-18
- 状态：已实施（§9）。用户 2026-10-18「开始」执行 D-1 至 D-3；2026-10-19「开始，全部做完」执行 D-4 至 D-6 并授权发布。§9.2 的核验归一是实施中新增的，未经 Codex 事前审查。
- 依据：Codex 1.70.4 至 1.70.5 总交接复审 §2.4 的第 1 条优先项（「先为门禁保留最小脱敏规划结构及失败原因……再用 F-堵了的成功、失败规划做固定输入回放……先定位再改」）；用户 2026-10-18「提交推送，然后开始」。

## 1. 第一段：现状证据

### 1.1 现象

门禁场景 F-堵了「卫生间下水堵了！！！」的期望是走设施报修（route facility、handoff true，标签 complaint_false_positive）：建维修任务、给安全提示。

| 来源 | 结果 |
| --- | --- |
| rc-1.70.4-3 all | `[False, False, True]`，判退步 |
| 对照：v1.70.3、rc-1.70.4-4 各 6 次 | 5/6、5/6（只留终端输出） |
| rc-1.70.5-1 第 1 次 all | `[False, True, False]`，判退步 |
| rc-1.70.5-1 第 2 次 all | 首轮通过 |

所有失败记录的形态都一样：route 为 complaint，回复是「您的情况我已记录。我会立即联系管家来处理，请您稍等。」，stages 只有 plan 一个阶段（耗时 610、851 毫秒；成功的那次 plan 为 1213 毫秒）。

### 1.2 代码路径（生产与门禁一致）

- 生产 `services/conversation_service.py`（约 1060 至 1072 行）：
  - `_classify_complaint` 命中情绪词（reason 为 agitated，「！！！」触发）后，调用 `_plan_with_released_lock` 做规划复核；
  - `answer_policy.agitated_cleared_by_plan` 只有在**规划成功、对得上本轮正文、且没有任何事项的 risk 为 complaint** 时才返回真；
  - 否则进入 `_enter_complaint_mode`：给客人发固定的已记录回复，并以客诉身份通知员工。
- 门禁 `tools/reply_regression.py::_Runner._respond`：`pre_route` 给出软客诉后，同样调用 `plan_turn` 和 `agitated_cleared_by_plan`，不放行时在约 640 行直接返回 `{**routed, "stages": stages}`，不再进入 `assistant.respond`。
- 规划提示词 `services/turn_plan.py`（约 288 行）对 complaint 的定义是「客人在投诉、表达不满或要求处理纠纷；咨询、假设、引用、否定和开心激动都不是危险或投诉」。没有专门说明「急切的报修语气」该怎么标。

### 1.3 影响（推断，未在生产实测）

客人急切地报修（带感叹号）时，如果规划复核判为客诉，就不会进入设施流程：不建维修任务，不给安全提示，只回一句已记录，员工收到的是客诉通知而不是维修任务。员工仍会被通知，所以不是无人处理，但处理路径和客人得到的信息都不对。生产里的发生率没有数据。

### 1.4 已知与未知

- **已知**：失败停在规划复核这一步，与 1.70.4、1.70.5 的取证和工具改动无关（Codex 已用调用链和 AST 对照确认）。
- **未知**：到底是哪种原因。
  - **(a)** 规划成功，但把这句报修的 risk 标成了 complaint；
  - **(b)** 规划失败或对不上正文（例如摘录不合法），按保守规则进入客诉。

  门禁在提前返回的路径上不保存规划状态和事项，所以现有记录无法区分。两种原因的修法完全不同：(a) 要改提示词或判据，(b) 要看规划为什么失败。

## 2. 第二段：功能点（初稿，先定位再修）

### F1 门禁记录保存规划摘要（只改门禁工具，不影响生产回复）

`tools/reply_regression.py`：只要本轮调用过 `plan_turn`（包括客诉、无关这些提前返回的路径），记录里都加上 `plan` 字段，内容为：

- status、reason（规划失败的原因码）；
- 每个事项的 kind、risk、subject、start 和 quote 长度。

不存 quote 原文，避免以后接入真实语料时带出客人原话；当前夹具都是虚构的，长度和位置已足够定位。摘要文件（`.md`）对未通过的场景附上这一字段。

### F2 定位诊断（需要授权调用真实模型）

用门禁同样的方式（候选源码、虚构资料、容器只读运行），只对「卫生间下水堵了！！！」调用 `plan_turn`，重复约 20 次，统计 (status, reason, 各事项 kind 与 risk) 的分布。对照组：同一句去掉感叹号，以及另外两三句急切报修，例如「空调坏了！！快来人」「房间停电了！！！」。

只调用规划、不调用主回复，单次成本约为一次门禁场景的三分之一。不写库、不发消息。

### F3 修复（按 F2 的结论二选一，届时补第三段再确认）

- **若是 (a)**：在规划提示词里补一句——设施故障报修、需要帮助时的急切语气、感叹号，本身不是投诉；只有明确表达不满、指责或要求赔偿处理才填 complaint。属于主模型提示变更，按规则要跑 all 门禁。不加词面特判。
- **若是 (b)**：按失败原因码修规划核验或请求，不放宽「规划失败按客诉保守处理」的安全规则。
- 无论哪种，都不改「硬理由（退款、平台投诉、议价）不受规划影响」的规则。

## 3. 第三段：风险与决策（待 F2 之后补全）

- **D-1**：是否授权 F2 的真实模型诊断（约 20 次加对照，只调用规划，虚构文本，容器只读）。推荐授权，否则只能猜原因。
- **D-2**：F1 是否单独先做。推荐先做：它只改门禁工具，以后所有门禁失败都能看到规划摘要，也为 F2 和后续波动定位提供数据。
- **D-3**：版本号 1.70.6，F1 与 F3 同版发布；如果 F2 的结论是不需要改生产代码，F1 可以只提交、不部署：`reply_regression.py` 虽然打进镜像，但不在生产回复链路上，只供门禁在容器临时目录里用候选源码运行。

## 4. F1 实现与 F2 结论（2026-10-18）

### 4.1 F1 已实现（本地，未提交）

- `tools/reply_regression.py`：新增 `plan_summary(outcome, text)`，返回 status、reason、matches，以及各事项的 id、kind、risk、subject、start、quote_len、valid，不存摘录原文。`_Runner._respond` 在四处调用过规划后的返回点都写入 `plan`：客诉或无关提前返回、计划判为当前危险、模型升级紧急、正常结束。
- `scripts/release/reply_gate.sh` 摘要：每个退步场景追加一行「失败记录的规划：状态(原因)：类型/风险…」。用系统自带的 Python 3.9 对 1.70.5 第 1 次门禁的真实结果文件跑过，旧记录没有规划时显示「未规划」。
- 测试：`tests/unit/test_reply_regression.py::test_plan_summary_records_status_and_risk_without_quote_text`。相关 50 项通过；`ruff check .`（与 CI 同口径）、mypy 通过。

### 4.2 F2 诊断结论（容器内只读，当前源码，虚构文本，只调用规划）

| 输入 | 次数 | 规划结果 |
| --- | --- | --- |
| 卫生间下水堵了！！！ | 20 | 15 次 `facility_fault/none`（放行）；**5 次 `facility_fault/complaint`（25%，进入客诉）** |
| 卫生间下水堵了 | 5 | 全部 `facility_fault/none` |
| 空调坏了！！快来人 | 5 | 全部 `facility_fault/none` |
| 房间停电了！！！ | 5 | 全部 `facility_fault/none` |

- **结论：原因是 (a)**。30 次规划全部成功、全部对得上正文；事项类型每次都正确识别为 facility_fault，只是风险标签约四分之一被标成 complaint。原因 (b)（规划失败）在这次样本里没有出现。
- 对照组样本小（各 5 次），只能说明不是每句急切报修都会出错，不能证明它们一定不会。
- 脚本：`docs/reviews/2026-10-18-planner-complaint-evidence/planner_complaint_diag.py`。结果只留在当次终端输出里，已抄入本节。

## 5. 第三段：F3 改法、验收与风险（待 Codex 审查）

### 5.1 推荐改法：只改规划提示词里 complaint 的定义

在 `services/turn_plan.py` 规划提示词的 risk 说明里补一句，意思是：

> 报修设施故障、请求帮助时的急切语气、感叹号、催促（如「快来人」「赶紧」）本身不是投诉；只有明确表达不满、指责、要求赔偿退款或处理纠纷时才填 complaint。

- 不加词面特判，不改 `agitated_cleared_by_plan`、`resolve_handoff_reason` 等确定性判据；硬理由（退款、平台投诉、议价）仍不受规划影响。
- 规划失败仍按客诉保守处理（原因 (b) 的安全规则不动）。
- 这是规划提示词变更，影响所有场景的规划，按规则跑 all 门禁。

### 5.2 不推荐的备选

- **确定性放行**：情绪词只由标点触发、且事项都是 facility_fault 时，忽略规划的 complaint。问题是会吞掉「下水又堵了！！！你们怎么搞的」这类真实不满（虽然它另有情绪词，但边界难以穷举），而且把语义判断交给词面，违背「不加词面特判」的原则。
- **客诉模式里照样建维修任务**：是产品流程变化，范围更大，不在本版。

### 5.3 验收

1. **F2 复测（真实模型，需授权，同 F2 方式）**：「卫生间下水堵了！！！」20 次，目标 0 次 complaint；另加两三句急切报修各 10 次。
2. **真实投诉对照（同样跑规划）**：例如「下水又堵了！！！你们怎么搞的」「房间太脏了！！！我要投诉」「热水一直没有，太差了！！」各 10 次，必须仍标 complaint（或命中硬理由），不能被新说明放过。
3. **all 门禁**：不退步；客诉类场景（含 complaint_false_positive 标签的、真实客诉的）逐一核对；新摘要会显示退步场景的规划。
4. **离线**：既有规划核验、客诉分流测试保持通过；提示词文本本身不写逐句复述的测试（项目规则：提示词措辞交给真实模型门禁）。

### 5.4 风险

- 提示词改动可能让模型在别的场景里少标 complaint。验收 2 和 all 门禁专门盯这一点。
- 25% 的误判率是 20 次样本的估计，复测 20 次全部为 0 也只能说明明显下降，不能证明降为零。

### 5.5 待确认

- **D-4**：按 §5.1 只改提示词。推荐。
- **D-5**：验收 1、2 的真实模型复测，以及 all 门禁，届时一并授权。

## 6. Codex 复审修订（2026-10-18，`docs/reviews/2026-10-18_codex-to-claude-1.70.6-planner-complaint-spec-review.md`）

本节取代 §2 F1 中「附上未通过场景」「不存 quote 就不会带出原话」的表述、§4.2 的次数与结论措辞、§5.2 第一条的理由，以及 §5.3 整节验收。

### 6.1 F1 修订（已实现，本地）

- **F176-R1**：`_Runner.run` 为每次运行建一个局部 `trace`。`_respond` 做完软预规划就写入摘要；主回复抛异常时，优先用异常自带的 `plan_outcome`（`AssistantUnavailableError` 的既有契约），其次用 `trace` 里的软预规划，两者都没有就不写 `plan` 字段。路由与失败判定不变，不补调用、不重试。`trace` 是局部变量，不放在实例属性上，并发运行不串记录。
- **摘要措辞**：硬分流在规划前返回时显式写 `plan: None`，摘要显示「未调用规划」；记录里没有 `plan` 字段（旧记录、中断、没有观测）显示「未采集」，不能当成没调用规划。
- **F176-R2**：删除摘要里的 subject，改为 `subject_len`。摘录原文与对象名都不存。
- **范围说明**：JSON 保存每一次记录的规划摘要；Markdown 只列**退步**场景的失败记录。没有达到退步的失败直接查 JSON。
- 回归（`tests/unit/test_reply_regression.py`）：
  - 对象名放合成密码、手机号、任意原话时，摘要都不含它们；
  - 异常自带计划、软预规划后异常、没有观测三种情况，加上两条不同规划并发运行不串；
  - 摘要区分「未采集」「未调用规划」与规划摘要。

  相关 98 项通过；`ruff check .`、mypy 通过；Codex 复现脚本的第一条缺陷断言已失效。

### 6.2 F2 更正

- 次数更正：诊断共 **35 次**（20+5+5+5），不是 30 次。全部规划成功、全部对得上正文。
- 结论改为：**本次新诊断复现了 (a)，没有观察到 (b)，支持先试提示词方案**。旧门禁没有规划摘要，不能逐次排除 (b)。5/20 只是主句这一组的观测比例，不是生产发生率。
- 证据形态：F2 只留下终端汇总。之后的复测要落盘最小 JSON：场景编号、轮次、规划摘要、是否放行，以及夹具、候选源码、提示词的摘要哈希、模型标识和测试日期；不存密钥、配置快照或自由文本原话。聚合数从这些记录算出。

### 6.3 §5.2 理由更正

「下水又堵了！！！你们怎么搞的」按当前词面规则只命中了感叹号，并不另有情绪词；它的指责是语义上的。这恰好说明不能按标点做确定性放行：标点触发无法区分急切报修与真实不满，只能交给规划判断。

### 6.4 验收合同（取代 §5.3）

失败样本一律保留，不替换、不「重跑到通过」。每组断言都基于逐次记录，不只看计数。

1. **急切报修规划组**（真实规划，落盘逐次 JSON）
   - 句子：「卫生间下水堵了！！！」20 次；另取「空调坏了！！快来人」「房间停电了！！！」「热水器不出热水了！！！」「门锁打不开！！！急」各 10 次。
   - 每一次都必须：`status=ok`、`matches=True`、有效事项为 `facility_fault`、`risk=none`、`agitated_cleared_by_plan=True`。规划失败、事项无效、不匹配、漏事项、误标其他风险，都算不通过。
   - 最终的设施路由、任务登记与安全提示，由既有会话、助手回归和 all 门禁验证。
2. **真实不满规划与软路由组**（真实规划，只选会触发软规则、不含退款或投诉等硬词的句子）
   - 句子：
     - 首次报修就带指责：「下水又堵了！！！你们怎么搞的」；
     - 重复未解决：「说了三次热水还是没有！！！」；
     - 报修与不满混合：「空调坏了！！！住得太糟心了」；
     - 多事项里只有一项是不满：「毛巾送两条，空调又坏了！！！什么破房间」；
     - 英文：「The AC is broken AGAIN!!! This is unacceptable」。
   - 每一次都必须：规划成功、有效，且表达不满的那一项保留 `risk=complaint`（`agitated_cleared_by_plan=False`）。不能用硬理由兜底来代替这条规划断言。
   - **负对照**：报修里引用或否定投诉，例如「下水堵了！！！我不是要投诉，就是急」，应为 `risk=none`，防止「提到投诉就升级」。
3. **硬理由与危险控制组**
   - 退款、平台投诉、议价仍确定性优先，不经规划；复用既有离线回归。
   - 真实危险不被 complaint 掩盖：「冒烟了，太离谱了！！！」等，必须保留危险分类（本地紧急分类或规划的 `current_hazard`）。复用既有门禁场景，新增一条规划对照。
4. **all 门禁**：不退步；客诉相关场景逐一核对，新摘要显示退步场景的规划。
5. **离线**：既有规划核验、客诉分流测试保持通过；不为提示词措辞写逐句复述的测试。

### 6.5 已知边界

- 「热水一直没有，太差了！！」这类不触发本地词面规则的不满，不会走软复核。这是既有边界，本版不扩规则，只如实说明。
- 提示词修正是概率性的语义修正。上述样本全部通过，只能证明这些样本，不能写成「生产误判率为零」。

### 6.6 待确认

- **D-4**：按 §5.1 只改规划提示词中 complaint 的定义。推荐。
- **D-5**：授权 §6.4 第 1 至 3 组的真实规划复测（约 70 次规划，只调用规划，虚构文本，容器内只读，逐次落盘），以及 all 门禁。

### 6.7 验收句子的本地分流核对（2026-10-18，离线，`ComplaintService.classify_all` 与 `EmergencyService.classify`）

逐句核对 §6.4 的样本会走哪条本地分流，确保每组真的测到规划：

- 「空调坏了！！快来人」只有两个感叹号，**不触发**软规则，换成「空调坏了！！！快来人」。其余急切报修句都只触发 agitated。
- §6.4 第 2 组五句都只触发 agitated（软规则），不含硬理由，可以测规划。
- **负对照更换**：「下水堵了！！！我不是要投诉，就是急」里的「投诉」直接触发**硬** complaint，不经过规划，测不到提示词，改用「下水堵了！！！别误会，就是着急」（只触发 agitated）。否定式的「投诉」也被硬规则当作客诉，这是既有边界，本版不改，如实记录。
- **危险控制更换**：「冒烟了，太离谱了！！！」「插座有焦味！！！」会被本地紧急分类直接判为 fire，排在客诉判断之前，规划的标签影响不到它们。这类由既有紧急回归覆盖。规划层的对照改用「插座一直滋滋响还发烫！！！太离谱了」：本地不判紧急，只触发 agitated。要求故障项的 risk 为 `possible_hazard` 或 `current_hazard:electric`，不能只剩 `complaint`。每个事项只有一个风险标签，新说明不能让「投诉」挤掉危险。

## 7. 危险优先于软客诉的顺序修订（2026-10-19，Codex `docs/reviews/2026-10-19_codex-to-claude-1.70.6-planner-complaint-spec-fix-review.md` F176-F1）

### 7.1 现状证据

- 规划可以把一句话拆成多个有效事项：故障项标危险，另一项（如「太离谱了」）标 complaint。
- `services/conversation_service.py::process_debounced_message`（约 1060 至 1072 行）：软复核时只要 `agitated_cleared_by_plan` 为假，就立即调用 `_enter_complaint_mode` 并返回。
- 计划里的危险由 `_apply_plan_risk`（约 1388 行）消费，但它只在后续主回复的成功出口（约 1734 行）和失败出口（约 1685 行）被调用，会被上面的提前返回绕过。
- Codex 用固定计划经真实会话编排做了对照（`docs/reviews/2026-10-18-planner-complaint-evidence/codex_f176_fix_review_probe.py`）：故障项为 `possible_hazard` 或 `current_hazard:electric` 时，**只要另有一个有效的 complaint 项**，客人就只收到客诉确认，没有避险提醒，也没有电气危险处置，最终作业为 0。这是既有遗漏，不是 F1、F3 引入的。
- 门禁 `tools/reply_regression.py::_Runner._respond`（约 686 行）的软客诉提前返回，顺序与生产相同。
- 同一函数后面，人工接待模式下的高风险确认（约 1075 至 1094 行）在已有规划时也排在危险消费之前，实施时一并核对是否同类。

### 7.2 推荐修复（生产控制流，超出原 D-4，需确认）

在软预规划完成、并且现有的过时、清空、原生接管等复核都已通过之后，凡是**基于这份规划准备提前返回**的分支，都先用同一份已核验计划调用 `_apply_plan_risk`：

- **当前危险**：完整紧急处置并接管，本轮结束，不再进入客诉或主回复。
- **可能危险**：先发避险提醒并通知员工，然后照常进入客诉。不能因为 `_apply_plan_risk` 返回假就忽略投诉。
- **规划放行、没有投诉**：维持原有的危险消费点（主回复的成功或失败出口），提前分支不消费，避免重复提醒。
- 只消费 `usable_plan` 核验通过（来源摘要一致）的计划；规划失败或对不上正文时照旧不处置，按既有规则进入客诉。
- 投诉不能降低危险等级；不增加规划调用；硬理由（退款、平台投诉、议价）的顺序不变。
- 员工通知：危险与客诉各自照常通知，不合并、不丢弃。
- 门禁 `_Runner._respond` 同步同一顺序：当前危险走紧急路由与固定安全回复；可能危险在客诉回复前附上避险提醒。

**文件与符号**：

- `services/conversation_service.py`：`process_debounced_message` 的软客诉分支，以及人工接待高风险确认分支（核对后决定是否同改）；
- `tools/reply_regression.py`：`_Runner._respond`；
- 测试：`tests/unit/` 下的会话服务与门禁回归，按 7.3 补。

### 7.3 验收（固定计划、完整路由，离线）

用通过真实 `verify_turn_plan` 的固定计划，走真实 `process_debounced_message`，需要时接着走 `process_recorded_message`。对以下组合 × 主回复成功与失败两个出口，断言客人实际收到的回复、会话模式、员工通知和作业：

| 故障项 risk | 另有 complaint 项 | 期望 |
| --- | --- | --- |
| possible_hazard | 无 | 避险提醒（原有路径，不重复） |
| possible_hazard | 有 | 先避险提醒，再客诉确认与客诉通知 |
| current_hazard:electric | 无 | 电气危险处置并接管（原有路径） |
| current_hazard:electric | 有 | 电气危险处置并接管；不再发客诉确认 |
| none | 有 | 只进客诉（原有行为不变） |

另加：规划失败与规划对不上正文时，不消费危险、照旧进入客诉；门禁 `_Runner` 的同样组合。Codex 的 `codex_f176_fix_review_probe.py` 4 次控制改为正确行为断言。

### 7.4 真实规划复测样本（取代 §6.4 的次数，D-5 以此为准）

| 组 | 句子 | 次数 | 每次必须 |
| --- | --- | --- | --- |
| 急切报修 | 卫生间下水堵了！！！ | 20 | `ok`、`matches`、有效 `facility_fault/none`、放行 |
| 急切报修 | 空调坏了！！！快来人 / 房间停电了！！！ / 热水器不出热水了！！！ / 门锁打不开！！！急 | 各 10，共 40 | 同上 |
| 真实不满 | 下水又堵了！！！你们怎么搞的 / 说了三次热水还是没有！！！ / 空调坏了！！！住得太糟心了 / 毛巾送两条，空调又坏了！！！什么破房间 / The AC is broken AGAIN!!! This is unacceptable | 各 5，共 25 | `ok`、`matches`、表达不满的有效项 `risk=complaint`、不放行 |
| 负对照 | 下水堵了！！！别误会，就是着急 | 5 | 有效 `facility_fault/none`、放行 |
| 危险对照 | 插座一直滋滋响还发烫！！！太离谱了 | 10 | 故障项为 `possible_hazard` 或 `current_hazard:electric`；不能只剩 complaint |

**合计 100 次规划**，只调用规划，虚构文本，容器内只读，逐次落盘最小 JSON（§6.2）。全量门禁单独计。

### 7.5 待确认

- **D-6**：本版同时修复危险优先顺序（§7.2、§7.3）。推荐：这关系到已识别的危险能否得到处置；修复只复用 `_apply_plan_risk`，范围清楚。如果不做，本版只能宣称「样本中更准确地保留危险标签」，并把这个顺序缺口列为未修。
- **D-4**：规划提示词按 §5.1 修改 complaint 的定义（不变）。
- **D-5**：授权 §7.4 的 100 次真实规划复测，以及全量门禁。
- 否定式「投诉」被硬规则当作客诉：另立事项（已登记在 `tasks/todo.md`），本版不改。

## 8. 危险优先方案修订（2026-10-19，Codex `docs/reviews/2026-10-19_codex-to-claude-1.70.6-hazard-order-spec-review.md` H176-R1、R2）

本节取代 §7.2 的「先调用 `_apply_plan_risk` 再进客诉」「规划放行、没有投诉就维持原消费点」两条表述，以及 §7.3 的验收矩阵；§7.4 的样本表不变。

### 8.1 现状证据（补充）

- **出站顺序**：`application.py::TransactionalOutboxWeCom._enqueue_guest_text` 把每次客人发送登记为独立任务。只有同一 `send_text_chain` 的后续段，才会在前段成功后排入。连接失败、45009 限流会转成 `RetrySafeJobError`，`worker.py::Worker.run_once` 调用 `SQLAlchemyJobRepository.mark_failed` 推迟这一条；`claim_next` 只按到期时间和编号取任务，独立任务之间没有先后依赖。Codex 用临时 SQLite、真实 outbox 和真实 Worker 复现了：先排的避险提醒重试时，后排的客诉确认先被受理。
- **人工高风险分支**：「插座一直滋滋响还发烫！！！可以便宜点吗」可以拆成故障项（`possible_hazard` 或 `current_hazard:electric`）加议价项（`static_fact/none`）。没有 complaint，`agitated_cleared_by_plan` 为真，但 `resolve_handoff_reason` 仍返回硬理由 `price`。会话处于 HUMAN_ACTIVE 时，`process_debounced_message`（约 1084 行起）回通用高风险确认后直接返回：主回复 0 次、最终作业 0 个，危险没有处置。
- 现有发送路径：
  - 客诉确认：`_enter_complaint_mode` → `_send_guest_reply`；
  - 避险提醒：`_answer_possible_danger` → `_send_prepared_guest_reply(stale_exempt=True)`，再通知员工「可能的安全情况」；
  - 人工高风险确认：`_send_guest_reply`，再通知员工。

### 8.2 修订方案

**判定与发送分开**。从 `_apply_plan_risk` 抽出不带副作用的 `_plan_hazard(message, plan)`，返回当前危险类别、「可能危险」或无。判定逻辑与现在逐字相同：词面已判紧急或可能危险的，不重复处置。`_apply_plan_risk` 改为调用它，原有两个出口的行为不变。

**适用分支**：凡是在软预规划完成后、**不会再进入主回复**就返回的分支，都先用同一份 `usable_plan` 核验通过的计划判定危险：

1. 软客诉分支（`agitated_cleared_by_plan` 为假，进入 `_enter_complaint_mode` 前）；
2. 人工接待高风险确认分支（HUMAN_ACTIVE 且 `resolve_handoff_reason` 非空，已有计划时）。

**处理**：

- **当前危险**：走 `_escalate_emergency` 完整处置并接管，本轮结束，不再发客诉确认或人工确认。员工通知按紧急接管的既有合同。
- **可能危险**：把固定避险正文与该分支原有的确认（客诉确认，或人工高风险确认）**组合成一次客人回复**，超长时复用已有的分段接力链。员工通知分别保留：「可能的安全情况」一条，加上客诉或人工确认原有的那一条。客诉建档、接管状态、分析任务都照旧。
- **无危险**：两个分支行为不变。

**仍会进入主回复的放行分支**：保留原有的危险消费点（主回复成功与失败两个出口的 `_apply_plan_risk`），提前分支不消费，避免重复提醒。

**不变的约束**：

- 只消费 `usable_plan` 核验通过的计划；规划失败、对不上正文、`_plan_with_released_lock` 返回 None（规划期间清空、原生接入、有新活动等）时，照现有规则处理，不产生新副作用。
- 沿用 `_plan_with_released_lock`：提交后规划，重新取活动锁并复核模式、清空、原生接入、更新活动。核验通过后到登记出站与接管状态之间，保持现有的锁与事务边界，不为首条回复额外提前提交。
- 组合回复沿用安全正文的过时豁免（`stale_exempt`）；清空与原生接管优先的既有发送门不放开。
- 投诉不降低危险等级；硬理由的识别与无计划时的分流不变，也不为它们额外规划；不增加规划调用。
- `process_recorded_message`（约 1123 行起）的人工高风险提前返回：本版不改。实施时只核对调用方，确认在没有过时或原生接入变化时能否走到；如确需修改，必须先复用 `_discard_stale_final` 的锁内检查。

**门禁**：`tools/reply_regression.py::_Runner._respond` 只同步软客诉提前返回这一处：
- 当前危险时走紧急路由与固定安全回复；
- 可能危险时，在客诉回复前组合避险正文。

主回复异常仍记为 `route=error`、空正文，不另造一套生产回退。

**文件与符号**：

- `services/conversation_service.py`：`_plan_hazard`（新增）、`_apply_plan_risk`（改为调用前者）、`process_debounced_message` 的两个分支，以及 `_enter_complaint_mode` 和人工确认的发送（接受可选的安全前缀）；
- `tools/reply_regression.py`：`_Runner._respond`；
- 测试：会话服务、出站与 Worker、门禁回归。

### 8.3 验收（取代 §7.3）

用真实 `verify_turn_plan` 核验过的固定计划，经真实即时入口、合并入口，必要时再走最终入口：

| 初始模式 | 计划 | 期望 | 主回复 |
| --- | --- | --- | --- |
| BOT_ACTIVE | 故障 `possible_hazard` + complaint 项 | 一次组合回复（避险正文在前、客诉确认在后）；两条员工通知；客诉建档与分析任务 | 0 次 |
| BOT_ACTIVE | 故障 `current_hazard:electric` + complaint 项 | 电气危险处置并接管；无客诉确认 | 0 次 |
| BOT_ACTIVE | 只有 complaint 项 | 只进客诉（不变） | 0 次 |
| BOT_ACTIVE | 故障 `possible_hazard`，无 complaint | 原路径：最终链路避险提醒一次，主回复成功与失败两个出口各验 | 1 次 |
| BOT_ACTIVE | 故障 `current_hazard:electric`，无 complaint | 原路径：最终链路电气处置，主回复成功与失败两个出口各验 | 1 次 |
| HUMAN_ACTIVE | 故障 `possible_hazard` + 议价项（硬理由 price） | 一次组合回复（避险正文 + 人工高风险确认）；两条员工通知 | 0 次 |
| HUMAN_ACTIVE | 故障 `current_hazard:electric` + 议价项 | 电气危险处置；无通用确认 | 0 次 |
| HUMAN_ACTIVE | 只有议价项 | 通用高风险确认（不变） | 0 次 |

另需验证：

- **出站顺序**：临时 SQLite、真实 outbox、真实 Worker，首条发送抛可重试错误时，不会单独先发出客诉确认（组合后只有一条，或分段接力在首段成功后才排后段）。
- **规划期间状态变化**：清空、原生接入、有新活动时，以上分支都不产生新的发送、通知或接管。
- **规划失败、对不上正文**：不消费危险，按既有规则走客诉或人工确认。
- **门禁**：`_Runner` 软客诉提前返回的三种组合（可能危险 + complaint、当前危险 + complaint、只有 complaint）。
- Codex 的 `codex_f176_fix_review_probe.py`、`hazard_order_spec_probe.py` 改为正确行为断言。

### 8.4 待确认

- **D-6（修订）**：本版修复 §8.2 两个分支的危险优先顺序与组合回复。推荐。
- **D-4**：规划提示词只改 complaint 的定义（不变）。
- **D-5**：授权 §7.4 的 100 次真实规划复测，以及全量门禁（不变）。

### 8.5 实施注记（Codex `docs/reviews/2026-10-19_codex-to-claude-1.70.6-hazard-order-spec-v2-review.md` C8-1、C8-2）

- **C8-1 去重条件分别保留**：`_plan_hazard` 照搬 `_apply_plan_risk` 现有的两种去重条件，不能合并成一个统一的提前返回：
  - 当前危险，只在**词面已判紧急**时才不处置；词面只判「可能危险」而计划是当前危险时，**仍升级为完整处置**（例如「插座好像漏电了！！！太离谱了」）；
  - 可能危险，在词面已判「可能危险」或「紧急」时不重复提醒。

  类别的选取顺序照搬，不另定多危险排序。补一个「词面可能危险 + 计划当前危险 → 升级」的判别控制。
- **组合范围的如实限定**：`process_debounced_message` 开头对词面可能危险的提醒，在规划之前就已单独登记，`_plan_with_released_lock` 会提交它。本版组合的只是**由规划补出的可能危险提醒**与提前返回的确认，不重构所有危险发送的全局顺序，也不取消或重复早期提醒。
- **C8-2 消息类型与发送标记**：完整的避险正文包括追问「现在具体是什么情况？」。经 `_send_guest_reply` 的高危策略处理时，追问会被删掉。做法是：
  1. 先按现有策略处理原有确认语；
  2. 再把本地固定的安全前缀拼在前面；
  3. 最后一次调用 `_send_prepared_guest_reply`。

  具体要求：
  - 客诉组合沿用 `message_type="complaint_ack"`，人工确认组合沿用 `message_type="text"`，不新增类型（类型会影响 `_handle_guest_delivery_failure` 的失败改写与重试）。
  - 两种组合都显式 `stale_exempt=True`；原生接管、来源清空仍在 `_guest_reply_is_stale` 内优先于豁免。
  - 验收时查看真实 outbox 的最终 payload，中英文各一次，核对安全前缀、确认语、类型与豁免；连接失败和 45009 的 Worker 重试控制按原类型保留。

## 9. 实施说明（2026-10-19，用户「开始，全部做完」，D-4、D-5、D-6 都按推荐执行）

### 9.1 已实现

- **D-6 危险优先**（§8）：
  - `services/conversation_service.py`：新增无副作用的 `_plan_hazard`（静态方法，判定与原 `_apply_plan_risk` 逐字相同，两种去重条件分别保留）、`_possible_danger_text`、`_early_plan_hazard`、`_send_combined_safety_reply`；
  - `_apply_plan_risk`、`_answer_possible_danger` 改为调用前两者，行为不变；
  - `_enter_complaint_mode` 新增 `safety_prefix`；`process_debounced_message` 的软客诉分支和人工高风险确认分支先调用 `_early_plan_hazard`。
  - 门禁 `tools/reply_regression.py::_Runner._respond` 的软客诉提前返回同序。
- **D-4 规划提示词**（`services/turn_plan.py::PLANNER_PROMPT_ZH`），最终措辞：
  > 报修设施故障、请求帮助时的急切语气、感叹号和催促（如「快来人」「赶紧」）本身不算投诉，也不算表达不满；明确的指责、抱怨或要求赔偿退款才算。
- **核验归一**（`services/turn_plan.py::verify_turn_plan`，实施中新增，见 9.2）：事项类型恰好是 `complaint` 时归为 `unclear`，并保留风险；其他未知类型仍判为结构失败。

### 9.2 实施中的发现、失效结论与决定（请重点审查）

- **第一版措辞**多了一句：「同一句里既报修又表达不满时，只给表达不满的那一项填 complaint，故障项仍按危险情况单独填写」。100 次复测为 92/100：
  - 7 次 `failed:schema`，模型另拆一项并把**类型**写成 `complaint`（不在 `PLAN_KINDS` 里），整份计划失败；
  - 1 次「房间停电了！！！」仍被标为 complaint。

  当时 HEAD 对照 40 次 0 失败，我据此判定是第一版措辞引入的回归。
- **该结论已作废**：之后同一句子在 HEAD 上又跑了 80 次，失败 7 次。HEAD 本来就会把 `complaint` 写进类型，发生率在波动；之前的「HEAD 0 次」是样本偶然。后续只以同一时段的 A/B 为准。
- **同一时段 A/B**（`docs/reviews/2026-10-18-planner-complaint-evidence/ab_same_window_results.txt`，脚本 `planner_ab.py`）：

| 句子 | HEAD（两轮） | 最终措辞、无核验归一（两轮） | 最终措辞 + 核验归一（一轮，HEAD 同时段对照） |
| --- | --- | --- | --- |
| 卫生间下水堵了！！！ 标 complaint | 11/30、8/30 | 0/30、0/30 | 0/30（HEAD 10/30） |
| 插座一直滋滋响还发烫！！！太离谱了 结构失败 | 0/20、0/20 | 3/20、6/20 | 1/20（HEAD 0/20） |
| 三句投诉与危险句合计结构失败 | 5/120 | 18/120 | 1/60（HEAD 6/60） |

- **结论**：新措辞消除了报修误判，但会让模型更常把 `complaint` 写进类型。规划失败时，同句已识别的危险项随之丢失，退回保守客诉，这正是本版要防的情况。
  - 中间试过一版措辞，写了「complaint 只是 risk 的取值，不是事项类型」，失败反而增多（「下水又堵了」20 次里 6 次），已放弃。
  - 采用**结构性补充**：核验时把这个确定的误写（类型等于 `complaint`）归为 `unclear` 并保留风险，危险项与投诉信号都能保住。其他未知类型照旧判失败，没有放宽整体结构校验。
- **残余**：最终措辞加核验归一时，危险句仍有 1/20 次结构失败，原因未能复现（之后同一句 30 次 0 失败），如实保留。

### 9.3 验证

- **真实规划复测**（§7.4 的 100 次，最终代码）：**100/100 通过**（`retest_final.jsonl`，逐次记录，提示词哈希前缀 `0ab9e27034768cec`）。第一版措辞的 92/100 逐次记录另存为 `retest_run1_split_prompt.jsonl`，失败样本保留。
- **离线**：
  - `tests/unit/test_conversation_plan_flows.py`：§8.3 提前返回各行（机器人与人工模式 × 可能危险、当前危险、无危险）、英文组合回复、计划对不上正文、C8-1 升级控制，共 9 项。换回 HEAD 的 `conversation_service.py` 时 6 项失败，另外 3 项是对照。
  - `tests/integration/test_hazard_combined_reply.py`：真实 outbox 的最终内容（中、英文，complaint_ack 与 text 类型）、过时豁免、首条重试时没有独立确认任务，共 4 项。
  - `tests/unit/test_reply_regression.py`：门禁软客诉三种组合，共 3 项。
  - `tests/unit/test_turn_plan.py`：核验归一与其他未知类型，共 1 项。
- **本地全量**（除浏览器测试）3324 passed / 82 skipped；Ruff（整个仓库）、Mypy 通过。浏览器测试因本机 Playwright 浏览器缓存缺失无法启动（环境问题，本次未改后台页面），以 CI 为准。
- Codex 的 `codex_f176_fix_review_probe.py`、`hazard_order_spec_probe.py` 断言的是修复前的缺陷，已由上面的正向回归取代，原脚本不改。

## 10. 核验归一收窄（2026-10-19，Codex `docs/reviews/2026-10-19_codex-to-claude-1.70.6-release-review.md` RV176-1，拟 1.70.7）

### 10.1 现状证据

- `services/turn_plan.py::verify_turn_plan`（约 199 至 204 行）：只要 `kind == "complaint"`，就无条件归为 `unclear`。
- 同文件 `_verify_risk`（约 129 至 140 行）：风险缺失或为 null 时按 `none` 处理，显式 `none` 也合法。
- 因此「下水又堵了！！！你们怎么搞的」若第二项写成 `kind=complaint`、风险为 `none`、缺失或 null：
  - 计划从旧版的 `failed:schema` 变成有效的无投诉计划；
  - `answer_policy.py::agitated_cleared_by_plan` 只看风险集合，于是放行，会话按普通维修处理，员工收不到客诉通知。
- Codex 已在真实核验和会话编排上用合成端口复现（见复审 §2 的表）。这是 1.70.6 新增放宽造成的可达回归；没有证据表明线上已经发生，A/B 记录里被接受的 `unclear` 项风险都是 complaint。
- 现有用例 `tests/unit/test_turn_plan.py::test_complaint_written_as_kind_is_kept_as_unclear_item` 只覆盖风险已写成 complaint 的情况。

### 10.2 修改（Codex 推荐的方案 1）

- `verify_turn_plan`：只有原始风险**明确等于字符串 `complaint`** 时，才把 `kind=complaint` 归为 `unclear`。其他组合保留原类型，照旧因不在 `PLAN_KINDS` 判为 `schema` 失败，退回保守客诉。
- 不扩大未知类型白名单，不改 `_verify_risk`、危险流程、提示词或调用方。

### 10.3 验收（离线）

- `test_turn_plan.py`：
  - 风险为 `none`、缺失、null 三个参数化变体，都判为 `schema` 失败；
  - 保留现有成功控制（危险项加 kind、risk 都为 complaint 的项）和其他未知类型失败的控制。
- `test_conversation_plan_flows.py`：「下水又堵了！！！你们怎么搞的」加上述三种计划，走真实会话编排：
  - 机器人模式下都进入客诉：转为 HUMAN_ACTIVE，员工收到客诉通知，没有最终回复任务，没有普通维修任务；
  - 人工模式下都保留高风险确认：不放行到普通回复，不新增普通任务；
  - 正向控制：危险项加 kind、risk 都为 complaint 的项，两种模式结果与上面相同。
- 这些用例须在当前 1.70.6 实现上失败。
- 相关单测、Ruff（整个仓库）、Mypy；不跑无关全量。

### 10.4 发布

- 版本 1.70.7：CHANGELOG、`docs/releases/1.70.7.md`。
- 真实门禁 all：`turn_plan.py` 在回复链路清单里，规划核验影响所有场景，按项目规则全量。
- 不做真实规划复测：只收紧一种原本被接受的输出，A/B 中被接受的样本都满足新条件。
- 不做测试号收发：触发需要模型给出特定误写，测试号无法稳定复现；离线会话用例和门禁覆盖。
- 部署和现场核对与 1.70.6 相同。

### 10.5 待确认

- R-1：按 10.2 至 10.4 执行，并授权 1.70.7 的门禁、提交推送、部署与现场核对。
- 2026-10-19 授权更新：Codex 明确说明本次仅做本地修复与验证后，用户回复「开始做完吧」，据此执行 §10.2、§10.3；R-1 的真实模型及发布部分仍待当前明确授权。

### 10.6 Codex 本地实施与自审（2026-10-19）

- `src/homestay_bot/services/turn_plan.py::verify_turn_plan`：归一条件收紧为 `kind == "complaint" and entry.get("risk") == "complaint"`；其他组合保留未知类型并返回 `failed/schema`。未改 `_verify_risk`、提示词、危险流程或调用方。
- `tests/unit/test_turn_plan.py::test_complaint_kind_without_explicit_complaint_risk_fails_schema`：原始风险为 `none`、缺失、null 时均核验失败且无可用计划；现有合法投诉归一与未知类型控制保留。
- `tests/unit/test_conversation_plan_flows.py::test_miswritten_complaint_kind_never_reaches_normal_repair`：六类输入 × 机器人/人工两种模式，共 12 项，经 `handle_message → process_debounced_message`；错误实现排出最终任务时继续跑 `process_recorded_message`。三种错误风险均不调用普通主回复、不排最终任务、不登记维修；固定确认与客诉通知保留。
- 正向控制包括 `kind/risk` 均为 `complaint` 的普通投诉，以及独立 possible/current 危险项。§10.3 的「结果相同」仅指不进入普通回复/维修；危险结果继续以已确认的 §8 为准：possible 给完整避险及客诉确认，current 给完整紧急处置并优先升级，不强行转成客诉确认。

判别力证据（业务代码修改前）：

```sh
PYTHONPATH=src:.:tests .venv/bin/pytest -q tests/unit/test_turn_plan.py tests/unit/test_conversation_plan_flows.py -k 'complaint_kind_without_explicit or miswritten_complaint_kind'
```

结果：**9 failed / 6 passed / 81 deselected**。3 项核验反例原先误返回 `ok`，6 项会话反例原先调用普通主回复；6 项合法投诉/危险控制原本通过。修复后这 15 项全部包含在以下 154 项通过结果中，不重复相加。

最终相关验证：

```sh
PYTHONPATH=src:.:tests .venv/bin/pytest -q tests/unit/test_turn_plan.py tests/unit/test_conversation_plan_flows.py tests/unit/test_reply_regression.py tests/unit/test_release_scripts.py tests/integration/test_hazard_combined_reply.py --tb=short
.venv/bin/ruff check .
.venv/bin/mypy
git diff --check
```

结果：**154 passed**；全仓 Ruff、Mypy（155 份源码）及差异空白检查通过。覆盖核验边界、即时/合并/最终会话链、门禁路由、组合危险出站与发布路径清单；没有装配、依赖、持久化或页面改动，因此不扩大到无关全量、数据库竞争或浏览器测试。

Ponytail 主代理自审：核对 `DeepSeekGuestAssistant.plan_turn`、`agitated_cleared_by_plan/resolve_handoff_reason`、会话早退与最终入口，以及合法风险、未知类型、原始风险非字符串的拒绝边界，未发现本次范围内新的阻断问题。测试走真实核验与会话编排，模型、任务和发送端口为合成替身，不证明真实模型发生率或实际收件。

本轮仅完成本地修复；版本仍为 1.70.6，未创建 1.70.7 发布记录，未执行真实模型门禁、提交、推送、部署或生产核对。§10.4 在发布获准后执行；旧候选门禁不能代替本次修复后的 all 门禁。否定式投诉、残余结构失败、`process_recorded_message` 既有早退与词面早期提醒维持另立事项。AOCI 收尾以本轮最终工具回执为准。

### 10.7 发布授权与执行（2026-10-19）

用户在本地修复与验证完成、明确说明发布未执行后回复「全部做完」，授权完成 §10.4 的 all 真实模型门禁、版本与更新日志、提交推送、CI、可恢复备份、仅 API 部署和只读现场核对。无需测试号收发的理由保持 §10.4；本轮不发送真实客人或员工消息。发布逐项结果见 `docs/releases/1.70.7.md`，未通过门禁不得放行。


## 11. 1.70.7 all 门禁三项补修（2026-10-19）

状态：用户选择「补充 Spec，修复三项后再发布」，本节作为继续实施与发布依据。保持 §10.7 的授权和失败关闭条件，不发送测试号或真实客人、员工消息。

### 11.1 现状证据与结论边界

首个实际 all（rc-1.70.7-1）中，T-吸烟、W-天气玩法、W-推荐各 1/3，通过门槛未满足。原始结果 `.stage/reply-gate-rc-1.70.7-1.json` SHA256 为 `59c14bf473331061329ef9ccba6bfded67e1f4abb06bd640de2c36b891c9edff`。三项规划合法，未进入 RV176-1 分支；这只能排除该条件对这些已观测失败的直接作用，不能据此称为无须修复的波动。18 次有界新旧对照及逐次答复见发布记录，不覆盖 all 失败。

追加 6 次合成场景结构诊断保存在 `.stage/1.70.7-structural-diagnosis.json`：保存脱敏后的合成问题、候选、实时结果及原始模型响应，不保存密钥、运行配置或生产原文。它证明：

- 吸烟问法在两次诊断中均拿到 9021 并回答成功。当前 `knowledge_service.PROPERTY_TOPICS` 的吸烟别名不含「菸」，`normalize_text` 的 NFKC 不会转换该字；`DeepSeekGuestAssistant._static_item_parts` 缺选择时的确定性计划依赖主题识别。模型把规范问法改成「烟」或选中编号时可答，不改写且没选中时会失去这条后备路径。不能断言首次 all 的两次失败均由此产生，原记录没有主模型原始响应。
- 天气＋玩法：一次原始回答已有博物馆、江汉路，但与无依据的「住处附近……方便」用分号连在同一句，`guest_reply_policy._drop_sentences` 删除整句；另一次模型本身没有给任何具体去处。故有生成遗漏和过滤误删两条路径。
- 普通推荐：原 all 出现只追问偏好或以没有资料为由拒绝推荐；现行 `_LIVE_RESULTS_RULE_ZH` 及主提示虽允许常识推荐，未明确要求先答再问，也未要求独立建议按完整句与本店事实隔离。`tourism.classify_tourism_query` 还不能识别「有啥玩的」这个普通推荐表达。

### 11.2 最小修复与保留边界

1. **静态取证**：仅补充吸烟主题的「烟／菸」常见异体别名，复用现有检索和证据计划。不要全局替换原文，不改变 quote/start、触发条件、房号日期或危险分类。证明无 evidence_selection 且规范问法保留「菸」时仍由审核来源回答；无审核资料、其他主题、危险冒烟的控制仍保守处理。
2. **普通推荐生成**：复用现有主调用，不增加调用、工具、固定景点兜底或平行规划。普通景点／美食推荐先给少量具体去处和适合理由，再按需问偏好；「你们推荐」是请求建议，不能把称呼误当成本店设施证据。缺出游日期不阻止普通推荐；具体活动、开放、票价、天气和交通仍只用实时来源。天气混问必须同时给普通玩法，实时结果只约束已查询部分。中英文约束一致。
3. **过滤误删**：保留严格事实过滤和句末边界，不拆逗号或分号去猜条件、否定、指代关系。要求生成时把可独立成立的建议写成完整句、不同事实分开；条件与结论必须同句，不能为分句丢失条件。无依据本店位置和服务仍删除。此项以生成契约降低误删，**不是确定性语义分句修复**；模型不遵守时仍可能保守少答，必须在门禁证据中如实报告。
4. **分类**：普通「有啥玩／有啥好玩／有什么玩」与已有「玩啥／玩什么」统一为 stable；有活动、票务、明确单日、住宿和求助信号时沿用既有优先级。补别名只识别请求，不赋予事实来源。

本节不修改危险优先、人工接管、数据模型、数据库、生产配置、知识内容或门禁判据；不晋升早餐冲突诊断。相比新增通用回退、二次模型校验或放宽事实过滤，本方案改动更小且保留安全边界。只有真实门禁通过才能证明这批回归在当前候选上达到发布要求，不宣称任意改写均已解决。

### 11.3 文件、验证与发布

- `src/homestay_bot/services/knowledge_service.py::PROPERTY_TOPICS`；`tests/unit/test_reply_fix2_items.py`：无选择的异体问法最终正文、来源与无资料控制。
- `src/homestay_bot/integrations/tourism.py::classify_tourism_query`；`tests/unit/test_tourism.py`：稳定问法与活动、日期、住宿优先级。
- `src/homestay_bot/integrations/deepseek_client.py` 主提示和 `_LIVE_RESULTS_RULE_ZH/EN`：先答后问、混问完整性及独立事实分句。措辞效果交真实模型门禁，不写复述提示词的单元测试；复用天气混问、数字事实补回、过滤与危险相关回归。
- 相关离线测试、Ruff、Mypy、差异检查；本轮不改装配、依赖、构建、持久化，部署前以本次推送 CI 通过满足离线全量路径。
- 修后新候选重新执行 `REPLY_GATE_SCOPE=all`；不复用 rc1、不修改资料及必过判据、不重跑至绿。新失败保留逐次证据，按根因决定后续，不静默放行。
- 自审最终差异，更新 CHANGELOG 和发布记录；门禁成功后正式提交、标签、推送、当前 CI、可恢复备份、仅 API 部署，以及源码／容器／迁移／日志／公网／登录诊断的独立核对。发布全过程沿用 §10.7 授权。


### 11.4 本地实施与自审

实现遵循 §11.2：三处运行文件仅修改主题别名、稳定旅游别名与主回复约束；没有新工具、调用、分句过滤或事实兜底。异体取证新增 6 项（3 问法×有无资料），改前 3 failed / 3 passed，失败均为有审核资料仍未确认。修后首次来源断言把字符串编号误写成整数，修正测试类型后全部通过。

相关验证：`test_reply_fix2_items.py`、`test_tourism.py`、`test_deepseek_client.py`、`test_guest_reply_policy.py`、`test_knowledge_service.py`、`test_emergency_service.py` 共 **477 passed**；追加共享调用边界 `test_answer_policy.py`、`test_knowledge_evidence_gate.py`、`test_reply_evidence_boundaries.py`、`test_release_scripts.py` 共 **137 passed**。两批无重叠，共 614 项。全仓 Ruff、Mypy（155 份源码）、`git diff --check` 通过；未重跑原 RV176-1 不受影响的 154 项，保留其既有证据。曾误用不存在的 `test_knowledge_evidence_policy.py` 路径，命令未执行用例，不计通过数。

主代理按 Ponytail 做最终差异自审：追踪主题识别在取证、事实过滤、政策/任务判别、欢迎卡片去重中的调用，以及旅游分类在搜索和首响中的调用；别名不改原文和危险规则，活动/明确日期/住宿优先级保留。没有新增本次范围内阻断项。旅游措辞效果尚未用真实模型证明；严格过滤仍可能保守少答，须由新候选 all 把关。此为主代理自审，不是独立 Claude 审查。


### 11.5 rc2 阻断与提示作用域收窄

rc2 的 all：160 场景首轮 140 通过，原三项各首轮通过；E-冰箱 1/3、K-访客 0/3，仍不允许发布。UN-代码仅为建议晋升，失败门禁不采纳新基线。四次有界追加诊断（两项各两次，`.stage/1.70.7-extra-diagnosis.json`）均能选对 9035／9033 并通过；不将诊断通过替代门禁，也不把原 all 失败确定归因于模型波动或这次提示词。

当前全局主提示新增了旅游生成指令，虽然只期望影响推荐，却会改变纯静态问答的模型输入。为减少本轮非目标影响，保留原全局一句普通推荐规则，将新增的先答后问及完整分句规则提为 `_STABLE_RECOMMENDATION_RULE`，只在现有小句分类至少一项为 stable 时追加。天气混问的 live 约束仍仅在有实时结果时使用。这样 E-冰箱与K-访客不接受新增旅游指令；不新增静态主题白名单、检索放宽或二次调用。此为 §11.2 同一方案的作用域收窄，不是新业务取舍。补充请求级作用域测试，再跑受影响助手测试及静态检查；因源码输入改变，须用新候选再跑 all，rc2 原结果继续保留。若还失败，继续停在发布门禁前，不用重复同候选碰运气。


收窄后 `test_deepseek_client.py` **150 passed**（含新增4项请求作用域控制），全仓 Ruff、差异检查通过；Mypy 155 份源码在同一运行改动上通过。首轮新测试误用禁止联网的 TourismStub 测天气混问而失败，改为复用既有 WeatherStub 装配后通过，不是生产链故障。其余相关验证输入未变，复用 §11.4，不相加重复用例。主代理再次核对最终差异：新增指令只按既有小句分类追加，未改变编号选择、候选、事实过滤或外部调用；AOCI 在最终发布证据稳定后收尾。


rc3（`f0db6d6`）all 通过：160 首轮143通过；144必过无退步，K-厨房-C 按既有规则补足三次为 false/true/true。原三项及 rc2 的冰箱、访客均首轮通过，无新晋升；早餐冲突仍诊断。进入 §10.7 授权的正式发布；正式业务输入须与 rc3 零差异才能复用本门禁。三轮和逐次结果见发布记录，不撤销失败历史或宣称模型泛化问题已全部解决。
