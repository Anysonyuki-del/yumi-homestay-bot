# Codex → Claude：房号身份 F6 修订复审

- 日期：2026-10-16。
- 对象：[返回交接](2026-10-16_claude-to-codex-room-identity-f6-fix-handoff.md)、[Spec §8](../specs/2026-10-15_room-identity-resolution-spec.md)、当前源码及相关测试。
- 前轮：[F5 修订复审](2026-10-16_codex-to-claude-room-identity-f5-fix-review.md)。
- 本次只读业务代码，新增报告和合成证据脚本；无提交、真实模型或生产操作。

## 1. 结论

IRI-F6 的**内部缺口丢失已修复**：中文/英文逗号、房态/参考价、只返回 302/401、正常/回退均会留下 tool_uncovered；完整结果不再多拼静态未确认。独立相关回归 **445 passed in 11.81s**。

仍需处理 **两项 P2、一项 P3**，暂不建议进入发布：

1. **IRI-F7 · P2：合并只保留房号，丢掉前片段的相对日期。** 明天或无法解释的“第二天”会被后句的绝对日期替代，最终错误地宣布整组已覆盖。
2. **IRI-F6 的展示部分 · P2：缺口提示指向已返回的房间。** 实际缺的是 302，客人却看到“401 可订”加“401 没查到”，302 在正文中消失。
3. **IRI-C3 · P3：已交给工具的片段仍可能进入静态取证。** 单一房源时重新出现多余知识缺口。

三项属于同一枚举闭环：覆盖对象、日期、展示范围与静态处理必须共用同一份已核验的交接信息。建议一次补齐，不重做已正确的楼栋、未知、空筛选和斜杠边界。

## 2. IRI-F7 · P2：相对日期被丢弃，整组套用后句日期

### 依据与复现

`DeepSeekGuestAssistant._clause_rooms`（`deepseek_client.py:2044–2061`）允许 `clause_dates(...).relative=True` 的片段入组，只排除已解析出的绝对日期和 `unbindable`。但 `clause_dates` 的 relative 只是“有相对词”，**不代表相对日期已经解析成功**；“第二天”同样得到 relative。

合并时只收集 `room_mentions`，丢掉片段原文的时间约束。随后 `clause_target` 只读取接收工具问法的日期。该句有绝对日期时直接绑定它，不再检查被交接片段的“明天/第二天”。第一句又因 delegated 被取消未确认。

在测试端口冻结今日 **2026-10-07**，计划和工具参数均为 10 月 10 日入住、11 日退房，302、401 工具行也都只有这一天的结果：

| 原文 | 实际输出及状态 |
| --- | --- |
| 明天302，10月10日401还有房吗 | 列 302、401 在 **10 月 10 日**可订；`knowledge_gap=False`、无缺口。302 问的是 10 月 8 日 |
| 第二天302，10月10日401还有房吗 | 同样列两间 10 月 10 日可订且无缺口；没有可靠锚点的序数日被套成 10 月 10 日 |
| 10月8日302，10月10日401还有房吗 | 绝对日期片段正确保留未绑定缺口，作为反向控制 |
| 明天302，10月8日401还有房吗 | 两者日期相同的正向控制，无缺口 |

房态、参考价，以及正常和协议回退均复现。即使模型给出的计划只选了后一句日期，本地覆盖门也必须保留前一句约束；不能用“模型本来应该选对日期”解释这个确定性漏洞。

### 最小修复建议

- 交接前使用冻结的本轮今日与既有日期规则，证明片段日期可解析，并与接收问法/实际覆盖目标一致；只共享房号集合不足以证明整组覆盖。
- 无锚点序数日、无法解释的相对时间、不同日期或无法证明一致时，**不标 delegated**，保留原有保守提示即可。本轮不必扩展新日期语言。
- 保留“明天302，401有房吗”和同日绝对/相对写法的正常行为；若允许继承，应证明继承的是枚举成员共同的日期，不能由后句覆盖前句。
- 回归覆盖相同日、不同日、无锚点序数日，核对最终证据日期与正文缺口，房态/参考价及正常/回退共用同一判断。

## 3. IRI-F6 展示部分 · P2：内部有缺口，客人却被告知另一间未查到

返回交接已明确提出此问题，本轮独立核验成立。问“明天302，401还有房吗”，工具只返回 401：

```text
10月8日入住、10月9日退房：
可订：401测试房

「401还有房吗」这次没有查到对应的房态，暂时无法确认。
```

参考价同样先列 401 的 200 元，再说“401多少钱”没查到；正常和回退相同。最终正文完全没有 302。

根因：`_tool_coverage` 使用合并后的 `clause_rooms` 检查整个枚举组，却仍将接收句 `clause.text` 传给 `_uncovered_text` 并写进 `GapUnit.text`。检查范围变成整组，展示范围还是 401 一间。**这不是仅仅不够精确：提示指向了已取得证据的对象，和上一段自相矛盾。**

建议保留枚举组的原文范围用于未覆盖说明。最小可用方案是引用“302，401还有房吗”并说明未完全查到，不必新增一套计算“具体缺哪间”的模型或格式。如果能复用现有集合差异明确指出 302 也可以。回归必须检查客人最终正文，不能只断言内部 `gap.reason == tool_uncovered`。

## 4. IRI-C3 · P3：delegated 只在未绑定分支生效

`_item_evidence_plan` 第 1716–1719 行只在收集 `bound is None` 的缺口时检查 delegated；第 1742–1749 行按有界小句做静态取证时没有排除它。

“明天302，有房吗”或“明天302，302还有房吗”只有同一房源，`_fact_layout` 可以绑定静态日期。即使 `_clause_rooms` 已交接第一段，它仍被当成独立静态问题“明天302”取证；没有知识时生成 `knowledge_missing`，在正确房态前再拼“尚未确认民宿专属信息”。正常、回退均复现。

建议 delegated 对取消该片段静态职责的全部实际分支一致生效，最小修复是在静态逐句取证入口跳过已交接片段；核对 `_gap_unit_offers` 等同布局消费者是否也应排除这些无须静态取证的片段。不要全局忽略无剩余小句，“302，停车怎么收费”等真实独立问题必须保留。

## 5. 证据与验证范围

独立执行 11 个相关文件：

```sh
.venv/bin/pytest -q tests/unit/test_room_identity.py tests/integration/test_room_identity_sqlite.py tests/unit/test_item_coverage.py tests/unit/test_turn_plan.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_reply_fix2_items.py tests/unit/test_live_price_scope.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_release_scripts.py
```

结果 **445 passed in 11.81s**。作者报告全量 3096 passed / 82 skipped、Ruff 和 Mypy 通过；本轮未重复全量，不改写为 Codex 独立结果。

新增[日期与交接边界探针](2026-10-15-room-identity-implementation-evidence/f6_fix_dates_probe.py)：

```sh
PYTHONPATH=src:. .venv/bin/python docs/reviews/2026-10-15-room-identity-implementation-evidence/f6_fix_dates_probe.py
.venv/bin/ruff check docs/reviews/2026-10-15-room-identity-implementation-evidence/f6_fix_dates_probe.py
git diff --check
```

均已执行通过。探针使用合成模型/工具、经过真实规划核验和助手组合；固定今日 10 月 7 日，覆盖房态/参考价、正常/回退、日期正反控制、缺口错标及单房号重复静态取证。**退出 0 表示上述反例成立，不表示修复验收通过。** 历史缺陷脚本保留，不为让它们通过恢复旧行为。

源码 SHA256 与返回交接一致：

| 文件 | SHA256 |
| --- | --- |
| `src/homestay_bot/integrations/deepseek_client.py` | `ab4202ecdacfb27e1d939bb86fb99cc692397f707159862378ab311607e14cfa` |
| `src/homestay_bot/services/item_coverage.py` | `e011dd8bd103627e09d33093d1acd2068979e93b302f21ac552a9dd3a47392de` |

## 6. 接手要求

先补齐 Spec §8 的日期、展示与静态职责交接契约，再在同一布局边界连续修复。继续保留已有的部分结果、未知/歧义、明确日期拒并、独立目标和静态子问控制；本次重点补“原文日期与工具日期不同”“最终提示所指对象”“单一房源 delegated”三类，避免再次只验证内部集合或缺口枚举。

未运行真实模型三目标各三次及 all，未访问生产或验证真实收件；提交、推送、部署和真实外部调用仍需当前授权。本地回归通过与认知索引对齐都不能替代这些验收。
