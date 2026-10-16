# Codex → Claude：R172-G1/G2 修复复审

- 日期：2026-10-17。
- 对象：`docs/reviews/2026-10-17_claude-to-codex-room-identity-r172g-fix-handoff.md`、房号身份 Spec §13、当前源码及相关调用方。
- 本地 HEAD：`9d150d6`；版本仍为 1.70.2，拟定 1.70.3 尚未发布。
- 边界：业务只读，新增本报告并维护认知资产；没有提交、推送、部署、真实模型或外发消息。

## 结论

**本轮本地复审通过：R172-G1、R172-G2 已闭合，未发现新的阻断项。可以准备后续发布验收，但本报告不构成发布授权或真实模型门禁通过证明。**

本次修复保留了上一轮正确的职责划分：纯房号片段只核对工具事实，不恢复动词豁免或静态资料缺口；日期按端点角色证明，目录按点名房源证明。没有增加模型调用、数据库结构或外部接口。

独立验证：**512 项相关测试通过，13.30 秒；补充 22 次助手调用及 2 项英文文案检查通过；Ruff、Mypy（141 个文件）、`git diff --check` 通过。**

## 逐项闭合依据

| 上轮意见 | 当前实现与调用链 | 复验结果 |
| --- | --- | --- |
| G1：完整区间只比入住日，丢失退房端点 | `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._fact_layout` 将规划入住、退房日期都传给 `_clause_rooms`；其内部 `fragment_dates_match` 在 2139–2150 行保留 `clause_dates` 的端点角色，完整区间两端都相同才记 target；只写退房日、无法绑定或无法解析成单日的相对日期均记 unbound | `tests/unit/test_room_identity.py::test_fragment_range_needs_both_endpoints`：区间结束不同、两端一致、仅退房端点 × 房态/参考价 × 正常/协议回退，共 12 项通过 |
| G2：目录任意一行替其他点名房间消除缺口 | `deepseek_client.py::_tool_coverage` 1919–1929 行：点名目标逐个核对目录证据的 `property_id`；空房源与 unbound 不覆盖；无点名目标仍只需有效目录行。此分支不比较住宿日期 | `test_catalog_named_room_needs_its_own_row`：302 漏行、9999 未知、全部返回、全店目录 × 两个出口，共 8 项通过；缺口正文能定位片段且无转交候选 |
| 目录缺口文字含糊 | `deepseek_client.py::_uncovered_text`：点名房号时引用片段并说未查到对应房源资料；全店目录沿用原说明；中英文均有对应分支 | 上述目录测试断言最终中文正文；额外直接核对英文点名和全店分支，均通过。英文整条助手链未单独验收 |
| 旧注释仍写“我想订102”不归工具目标 | `deepseek_client.py::_clause_rooms` 的函数说明已改为未交接房号片段都交给工具覆盖 | 说明与实际返回标记一致，没有恢复订房动词词表 |

正常与协议回退仍通过 `deepseek_client.py::_compose_items` 共用 `_tool_coverage`，没有另建仅修正常出口的分支。检索、缺口选项和取证仍通过 `_asks_static` 共用静态职责；`_referral_candidates` 仍只接受 knowledge_missing，因此本轮的 scope_unbound / tool_uncovered 不会变成可转交知识缺口。

## 相邻边界检查

补充验证复用既有测试端口，固定今日 2026-10-07，经过真实 `DeepSeekGuestAssistant.respond`；规划与工具结果均为本地合成，没有真实网络调用。

### 日期控制：16 次助手调用

以下四种输入各覆盖房态、参考价及正常、协议回退两种出口：

| 房号片段 | 规划与工具证据 | 结果 |
| --- | --- | --- |
| `10月8日至10日302` | 规划及工具都是 10 月 8 日至 10 日；价格行包含两个入住夜晚 | 片段无缺口，没有因补齐退房核对而误拦完整证据 |
| `10月8日至10日302` | 规划日期缺失，工具只查 10 月 8 日至 9 日 | 片段 scope_unbound，不能由工具自行选择的较短区间消除 |
| `第二天302` | 规划及工具为 10 月 8 日至 9 日 | 片段 scope_unbound，仍保留无锚点序数日保护 |
| `10月32日302` | 规划及工具为 10 月 8 日至 9 日 | 片段 scope_unbound，非法日期没有被当成未写日期而继承 |

每条完整原文均为该片段接 `，401还有房吗` 或 `，401多少钱`。断言对应片段的缺口原因，无 knowledge_missing、无 ReferralCandidate。这里只证明所列端点边界，没有扩张日期语法支持范围。

### 目录控制：6 次助手调用

以下三种组合分别经过正常与协议回退：

| 原文与身份 | 实际目录行 | 结果 |
| --- | --- | --- |
| `401有什么房型，302呢`；302 对应 2 栋与 3 栋两个候选房源，另有 401 | 401 与其中一个 302 候选 | 302呢 为 tool_uncovered，最终正文点出该片段，不能由一个候选消除歧义目标的缺口 |
| 同上 | 401 与两个 302 候选齐全 | 无缺口，逐房源核对没有误拦完整歧义候选集合 |
| `302有什么房型`；302、401 均存在 | 仅 302 | 无缺口，单房目录没有被错误扩大成必须查全店 |

这补足作者新增用例未直接包含的歧义房号部分/完整结果控制。目录无需附住宿日期；实现选择单独按目录房源编号比较，避免机械复用日期证据核对造成目录全被判缺失，符合本轮最小修复边界。

### 前轮行为保持

- `test_room_request_synonyms_have_no_fake_gap`：订/住/要三种表达、两个出口仍通过。
- `test_planner_room_hint_cannot_turn_follow_up_into_referral`：无 hint、401、302 三种合法规划提示仍不改变片段的非知识性质。
- 句尾追问、日期中断、同日枚举、部分工具结果、静态独立问题与逐项证据路径均在下列相关测试范围内通过。

以上结论分别依据 `tests/unit/test_room_identity.py` 对应行为测试及真实助手补充控制；不把有限同义词样本表述成任意自然语言泛化保证。

## 交接所列已知边界

1. **未知目录房号的文案**：接受当前“没有查到对应房源资料”。`_uncovered_text` 没有用未知身份推断目录中存在该房间，也没有借其他房间作答；无需为统一房态文案扩大 `_unmatched_room_header` 的调用范围。
2. **歧义目录房号必须覆盖全部候选**：接受，与当前房号身份合同一致；本轮额外验证了部分与完整候选集合两个方向。
3. **复杂或不支持的日期写法**：仍按现有解析器能力保守处理；不把本次修复扩大为通用日期解析。只写退房日一律 unbound 是 Spec §13 明确接受的保守边界，即使与某份规划端点相同也不单凭该端点推断整段住宿。

未发现新的持久化、事务或并发影响：当前修改发生在每轮冻结的本地布局与覆盖判断，没有新增共享可变状态或外部调用；此结论来自上述改动函数及调用链，不是生产负载测试。

## 独立验证范围

运行以下 11 个文件，结果为 512 passed：

```text
tests/unit/test_room_identity.py
tests/integration/test_room_identity_sqlite.py
tests/unit/test_item_coverage.py
tests/unit/test_turn_plan.py
tests/unit/test_deepseek_turn_plan.py
tests/unit/test_reply_fix2_items.py
tests/unit/test_live_price_scope.py
tests/unit/test_reply_regression.py
tests/integration/test_reply_fix3_boundaries.py
tests/unit/test_reply_evidence_boundaries.py
tests/unit/test_release_scripts.py
```

另运行 `ruff check src tests`、`mypy src`、`git diff --check`，全部通过。作者交接的全量 3163 passed / 82 skipped 是作者证据，未独立重跑；本轮影响可界定，未因复审或交接重复跑全仓。

## 身份绑定与后续边界

| 文件 | SHA-256 |
| --- | --- |
| `src/homestay_bot/integrations/deepseek_client.py` | `1be771bb96cc9840c5c7a62f8cd7dbde398844bfe96c259161098d73ce9d749b` |
| `tests/unit/test_room_identity.py` | `04406a1a520590922a7fa31fa9ea768bea537a2151152179983f3d933bf12668` |
| `docs/specs/2026-10-15_room-identity-resolution-spec.md` | `33892540ab9babbbf1fdc4801cd9bbcea2b2ce4d25898635b19cbe093b281e92` |
| 本轮 Claude 交接报告 | `00c8a1c72b7263f64c0ae1ba61e31850a56c74d26e2f389950974aa3ae10a651` |

源码和测试摘要与作者交接一致。共享回复链有变化，后续正式发布仍需当前授权及 all 真实模型门禁；版本升级须同步 CHANGELOG 和发布记录。没有本轮发布授权，不执行提交、推送或部署。源码、门禁、部署现场和客人实际收件证据继续分别记录。

本轮未改业务文件；没有生产、真实模型、外部消息或收件验收，也未处理无关 `.impeccable/critique/`。
