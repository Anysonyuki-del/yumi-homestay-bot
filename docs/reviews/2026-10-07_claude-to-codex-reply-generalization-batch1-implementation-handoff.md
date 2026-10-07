# Claude → Codex：回复泛用化第一批实施报告（v1.68.2，已上线）

- 日期：2026-10-07。作者：Claude。
- 用户指令原话：「开始，直接做完，全量模型也通过，发版部署上线也做，全部做完之后写报告写清楚你做了什么，依据是什么，我到时候发给codex审查」。
- 范围：只实施回复泛用化方案的**第一批**，即紧急豁免范围修复与门禁判定修正。第二至第四批（轮次计划、统一任务写入口、接管判定）按 Spec §3.2 每批单独确认、单独发布，本次没有动。
- 发布：提交 `2cfff79d9bc31405427c442ee29f23e2c6b35e8a`，注解标签 `v1.68.2`，已部署生产（仅替换 API）。发布记录见 [docs/releases/1.68.2.md](../releases/1.68.2.md)。
- 请求：独立复审实施是否忠实于两份已确认 Spec，判定逻辑有无新的越界或漏判，以及证据是否足以支撑结论。

## 1. 依据

| 依据 | 位置 | 状态 |
| --- | --- | --- |
| 紧急豁免范围修复 Spec v4 | [docs/specs/2026-10-07_emergency-exemption-scope-spec.md](../specs/2026-10-07_emergency-exemption-scope-spec.md) | 三段经用户确认；D1 疑问语气的现场描述判确定危险，D2「怎么赔」不豁免，D3 并入第一批 |
| 回复泛用化 Spec v7.1 | [docs/specs/2026-10-06_reply-generalization-spec.md](../specs/2026-10-06_reply-generalization-spec.md) | 三段经用户确认；§2.2 P0、§3.2 第一批、§3.3 D2、D7 |
| Codex 历轮审查 | 本目录 `2026-10-07_codex-to-claude-reply-generalization-*` 共 6 份，以及用户转述的 v7 结论 | 逐项回应记录在两份 Spec 的「复审回应」节 |

## 2. 做了什么

### 2.1 紧急分类（`src/homestay_bot/services/emergency_service.py`）

**符号变化**

- 新增 `EmergencyService._segments(text)`：把文本按句末标点（`。！？；\n.!?;`）切成句子，再按中文逗号与转折词（`，`、`but`、`但是`、`但现在`）切成分句，返回（句子、句末是否问号、分句起点、分句终点）。分句口径与旧版 `re.split(r"[，。！？；\n.!?;]|\bbut\b|但是|但现在")` 相同；新增的句子与问号信息只供需要看整句边界的豁免使用。
- `EmergencyService._noncurrent_mention`：签名由 `(clause, match)` 改为 `(sentence, is_question, clause_start, clause_end, start, end)`，规则全部改为按命中区间判断，详见下文。
- `EmergencyService.classify`：改为遍历 `_segments` 的结果，并在分句范围内匹配（`pattern.finditer(sentence, clause_start, clause_end)`）。类别顺序、`有点/不确定` 的可能危险判定、末尾的 `烟味|刚才摔…` 可能危险判定以及返回值，均与旧版一致。
- 新增模块级常量 `_CLAUSE_SEPARATOR`、`_INQUIRY_MARKER`、`_GAS_LIVE_SIGNAL`、`_ALARM_DEVICE`、`_LOCATION_QUESTION`、`_DEVICE_LIVE_SIGNAL`、`_SAFETY_RULE_SUFFIX`、`_HYPOTHETICAL_MARKER`、`_CONSEQUENCE_QUESTION`、`_QUOTE_MARKER`、`_QUOTED_TEXT`、`_MEANING_QUESTION`。这些常量只用来限定豁免区间，`_patterns` 没有改动。

**规则对照（Spec §2.2）**

| 豁免 | 旧行为 | 新行为 |
| --- | --- | --- |
| 开头整句短路「燃气灶…吗 / 报警器…在哪 / 怎么赔」 | 分句出现即豁免全部命中 | 删除 |
| 末尾「在哪里 / 位置在哪 / 安全规定 / safety policy」 | 无现场信号即豁免全部命中 | 删除，改由下面第 2、3 条按区间判断 |
| 否定（标记紧贴命中） | 不变 | 不变 |
| 1. 设备咨询 | — | 命中文本为「燃气 / 煤气 / 天然气」且后一字为「灶 / 炉」，句末为问号或分句含疑问标记（吗、么、哪、什么、还是、有没有、是不是、是否），且分句无燃气现场信号（味、漏、闻、泄、嘶） |
| 2. 报警器、灭火器位置 | — | 命中完全落在设备名区间内（`smoke alarm/detector`、`fire extinguisher`、`(烟雾)报警器`、`灭火器`），分句在问位置（在哪、哪里、位置、where），且无现场信号（响、叫、正在、going off、beeping、sounding） |
| 3. 安全规定 | — | 命中后直接接「安全规定 / 安全政策 / 规定 / 政策 / safety policy / safety rules / policy / rules」（中间只允许空白与「的」） |
| 4a. `what is/are` | 前缀出现即豁免 | 不再单独豁免 |
| 4b. 假设（如果、假如、假设、万一、what if、in case of） | 前缀出现即豁免 | 只豁免标记之后、到结束边界之前的命中。结束边界为第一个后果询问词（怎么办、该怎么、怎么处理、应该、要不要、会怎样、what should、what do、how do、how should），没有时取句末问号；都没有不豁免 |
| 4c. 引用（说明书、手册、政策、演练…写或说） | 前缀出现即豁免 | 标记之后有引号时只豁免引号内命中；没有引号时到释义询问词（是什么意思、什么意思、怎么理解、指什么）为止；都没有不豁免 |

### 2.2 门禁判定（`src/homestay_bot/tools/reply_regression.py`）

- `judge`：
  - 新增：`expected route == "knowledge"`、`observed == "unconfirmed"` 且 `expect.knowledge_gap is True` 时，路由视为符合。
  - `ok` 新增条件：规范化后正文非空。
  - docstring 写明了这两条的原因。
- `_Runner._respond`：
  - 设施分支调用 `prepare_facility_advice_reply(..., action_reply=_FACILITY_ACTION_PLACEHOLDER)`，占位文本为「<动作结果由会话服务决定>」，不再生成「我已提交管家人工处理」。
  - 场景声明 `expect.context.confirmed_stay` 时，以 `CustomerModelContext(confirmed_stay=...)` 作为 `customer_context` 传入 `respond`。没有声明或没有 `expect` 时传 `None`，行为不变。
- 新增模块常量 `_FACILITY_ACTION_PLACEHOLDER`。

### 2.3 场景预期与基线

- `tests/fixtures/guest_reply_scenarios.json`：
  - CH-谢谢、UN-代码：路由 `unrelated → knowledge`（D2，与既有 PI-内部、PI-提示词的先例写法一致）。
  - SR-毛巾、SR-垃圾、SR-遗失：路由 `handoff → knowledge`、`handoff: false`（D7：登记并通知，不转人工），禁用片段新增「尚未确认」「暂时无法确认」。
- `tests/fixtures/guest_reply_regression_baseline.json`：
  - 发布前：`expectation_changes` 追加以上 5 条及原因。
  - 发布后：采用门禁给出的新基线（must_pass 107 → 115、known_failures 47 → 39），并手工在 `ratchet_log` 追加 1.68.2 条目。门禁脚本生成的新基线不含这一项，与 1.39.17–1.41.0 的写法保持一致。

### 2.4 测试

| 文件 | 新增 | 改前代码上 |
| --- | --- | --- |
| `tests/unit/test_emergency_service.py` | `test_exemptions_do_not_cover_other_current_hazards`（14 例）、`test_recognizable_non_current_ranges_stay_exempt`（10 例）、`test_merged_batch_with_alarm_location_and_ringing_is_detected` | 18 个失败；其余对照例（如 `In case of fire, what should we do?`、`What is the fire safety policy?`）原本就通过，确认没有退步 |
| `tests/unit/test_conversation_service.py` | 同句混合危险转人工并通知员工；连发「烟雾报警器在哪」「一直在响」经 `process_debounced_message` 撤离；灶具类型咨询照常交给模型 | 3 个全部失败 |
| `tests/unit/test_reply_regression.py` | 知识缺口路由、空正文、设施收尾占位、住宿上下文传递 | 4 个全部失败 |

改前失败的验证方式：临时把对应源文件换回 `HEAD` 版本运行，再恢复；工作区最终内容未受影响。

### 2.5 版本与文档

`pyproject.toml` 1.68.1 → 1.68.2；更新 `CHANGELOG.md`、`docs/releases/1.68.2.md`（含部署补录）、`tasks/todo.md`。两份已确认 Spec 与全部审查交接一起入库。AOCI 认知条目已维护；本次确认了三个「仅观察」测试文件的变化（`aoci scope acknowledge --reviewed-by claude`），Verify、Check 与 Guide 均对齐。

## 3. 验证证据（分项，不互相替代）

| 项 | 结果 |
| --- | --- |
| 相关单测 | `test_emergency_service.py` 83 passed；`test_reply_regression.py` 20 passed；`test_conversation_service.py` 132 passed |
| 本地全量 | 2544 passed、61 skipped、13 warnings（`RUN_LIVE_CONTRACT_TESTS=0 RUN_DEEPSEEK_CONTRACT=0`）。跳过记录里显示旧仓库路径，来自搬迁前的字节码缓存；已核对 `homestay_bot.__file__` 指向当前仓库 |
| 静态检查 | `ruff check src tests` 通过；`mypy` 检查两份改动源码通过 |
| CI | [main](https://github.com/Anysonyuki-del/yumi-homestay-bot/actions/runs/37557433385)、[标签](https://github.com/Anysonyuki-del/yumi-homestay-bot/actions/runs/37557433506) 均 success；各 2544 passed、61 skipped、14 warnings，隔离 PostgreSQL 各 21 passed |
| 真实模型门禁（all） | 154 场景，首轮通过 114（1.68.0 为 107）；无退步；安全类无未通过；新纳入 8 个。必过 K-房型 首轮失败、两次重跑通过 |
| 备份 | 完整备份 `full-pre-v1.68.2-20261007T013052Z`（dump 380 对象）与部署脚本备份 `pre-v1.68.2-20261007T014009Z`（39 表、380 对象）均复验通过 |
| 部署后独立核对 | 服务器 HEAD 与标签一致；新 API 容器 running、重启 0；PostgreSQL 未重启；迁移版本仍为 0034；安装包与公网版本均为 1.68.2；改动文件在容器源目录与安装包中的哈希与标签一致；日志异常计数 0；健康接口仍为既有 degraded |
| 生产运行态 | 在生产 API 容器内调用已部署的 `classify`：4 句同句混合与现场询问判危险，4 句纯咨询判非紧急，与单测一致 |
| 测试号收发 | 未做。改动是确定性分类函数，已由单测与真实会话链路测试覆盖；用真实测试号发危险消息会触发真实撤离模板、管家接管与员工通知，打扰值班人员，且对验证分类函数没有额外判别力 |

## 4. 与 Spec 的差异与实施中的取舍（请重点核对）

1. **`Where is the fire extinguisher?` 改为非紧急**：Spec 第 2 条列入了 `fire extinguisher` 与英文 `where`，旧代码末尾的位置豁免只认中文，所以旧行为是判火警。门禁中 K-消防-EN 因此由已知失败转为 3 次全过。这是 Spec 规则的直接结果，不是额外扩展。
2. **对照用例中的 `In case of fire, what should we do?`**：英文逗号不在分句符里（与旧版一致），整句是一个分句，后果询问词 `what should` 构成结束边界，所以豁免。请确认英文逗号不切分的旧口径应当保留。
3. **SR 场景预期的写法**：D7 的原意是「登记并通知、不转人工」，门禁运行器不执行登记，无法直接断言。我改为允许模型路径，并禁止「尚未确认」「暂时无法确认」这类兜底，使当前错误回复如实保持未通过；登记与通知的正确性按 Spec §2.2 交给第三批的会话集成测试。
4. **`ratchet_log` 手工补录**：门禁生成的新基线不追加这一项，我按旧条目格式手工追加了 1.68.2 条目。
5. **`_QUOTE_MARKER` 的 `.*?`**：引用标记仍沿用旧的「说明书 / 手册 / 政策 / 演练 … 写 / 说 / 提 / 要求」词组，只是改为非贪婪匹配，并以标记结束位置作为豁免起点。

## 5. 已知未覆盖与后续

- **紧急分类仍有误报**（门禁中可见）：「Can I smoke on the balcony?」「到了请打我电话」仍判为紧急，原因是不属于设备、位置、规定、假设、引用任何一种可识别结构。按 Spec 留给第四批：语义补漏只升不降，咨询形态按 D3a、D3b 处理。
- **词面漏检**：「房间往外窜白烟了」「报警器在哪里一直在响」（无模式命中）仍判非紧急，留第四批。
- **保守误报**：没有结束边界的悬空假设（「如果着火」）与无引号且无释义询问词的引用，按设计判为危险。
- **门禁观察**：U-健身房这次走了设施分支，判定由模型的 `facility_issue` 决定，与本批改动无关；K-厨房-C 不再触发撤离，但变成「尚未确认」，留第二批证据门处理。
- **未验证**：PostgreSQL 并发（本批不涉及事务）、真实测试号收件、改写集与反例集（D1，待独立会话编写）。

## 6. 请 Codex 复审的重点

1. `_noncurrent_mention` 各条区间判断是否存在新的越界：同一句里另一处现场危险被放过。尤其是假设结束边界取「第一个后果询问词」、引用在有引号时只认引号内命中这两处。
2. `classify` 改为按句子与分句遍历后，类别优先级与可能危险判定是否与旧版完全一致。
3. `judge` 的知识缺口放行条件是否过宽：它只在 `expect.knowledge_gap is True` 且期望路由为 `knowledge` 时生效。
4. 场景预期与基线变更是否忠实于 D2、D7，有没有借预期调整掩盖真实缺陷。
5. 发布记录中的证据是否足以支撑「已上线、无退步」的结论。

## 7. 工作区归属

- 发布提交包含：两份源码、三份测试、两份夹具、版本与日志、两份 Spec、全部审查交接，以及 AOCI 资产。
- 后续文档提交包含：发布记录补录、新基线、Todo、本报告与 AOCI 资产。
- `.impeccable/critique/` 是之前就有的未跟踪目录，未纳入。
- 受保护的 `YuMi民宿AI项目总结.txt` 没有读取、暂存或提交。
