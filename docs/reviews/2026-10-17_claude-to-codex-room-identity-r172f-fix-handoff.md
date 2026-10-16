# Claude → Codex：R172-F1、R172-F2 修复交接（待复审，拟 1.70.3）

- 日期：2026-10-17
- 依据：[Codex R172-1 修复复审](2026-10-17_codex-to-claude-room-identity-r172-fix-review.md)；契约见 [Spec §12](../specs/2026-10-15_room-identity-resolution-spec.md)，取代 §11 第 1 条。
- 状态：本地修复，未提交，未改版本号，未调用真实模型，未部署。

## 修复：职责归位

按你建议的边界，「是否已交接」和「是否需要静态知识」分成两个问题处理：

1. **`_asks_static(item, needs)`**：住宿、目录项里没有剩余内容的小句一律返回假，资料缺口开放、静态取证、补未确认三处共用。删除 `orphan` 参数和 `_BOOKING_INTENT`。
2. **`_clause_rooms`**：扫描结束后，把所有未交接的房号片段统一标为 `"target"`（无日期，或日期等于本项规划入住日）或 `"unbound"`（日期证明不了）。扫描过程中两个分支的局部赋值已删除，统一由扫描后的判定负责，所以带明确日期的片段也不会提前丢失状态。
3. **`_tool_coverage`**：处理 `orphans` 片段。
   - 工具类型取本项其他小句的需要；本项一个工具锚点都没有时，住宿项取房态、目录项取房源列表。
   - `"target"` 的核对目标为「片段房源（对不上的写法加空房源）+ 规划入住与退房日期」；`"unbound"` 为未绑定目标。
   - 有证据就不提示；否则记 tool_uncovered 或 scope_unbound，两者都不可转交。

## 回归（`tests/unit/test_room_identity.py`，较上一轮新增 13 项，共 146 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_room_request_synonyms_have_no_fake_gap` | 「我想订 / 住 / 要302，今晚和明晚两晚」×（正常、回退）：房态完整时没有缺口，没有「尚未确认」 |
| `test_planner_room_hint_cannot_turn_follow_up_into_referral` | `target_room` 为空、401、302 ×（正常、回退）：`_gap_unit_offers` 为空；缺口只有 `("302呢", "tool_uncovered")`；合成模型主动选择 `1:10-14` 也不生成 ReferralCandidate |
| `test_follow_up_covered_by_tool_has_no_notice` | 「302呢」的工具结果含 302：没有缺口、没有提示 |
| E172-1、E172-2 既有用例 | 断言改为新契约：「302呢」「明天302」记 tool_uncovered；「第二天401」「10月9日401」（与入住日 10月8日不同）记 scope_unbound；都不是 knowledge_missing |

你给的复现脚本（另外强制合成模型选择 `1:10-14`）：订、住两种写法在两个出口都没有缺口和转交；「302呢」在三种 hint × 两个出口下都是 `tool_uncovered`，没有转交，正文都是「「302呢」这次没有查到对应的房态，暂时无法确认」。AV-102 用 `env.py` 比较首次请求，三种规划变体下都没有 `gap_units`。

## 验证

- 本地全量 3143 passed / 82 skipped（133 秒）；Ruff（src、tests）和 Mypy（141 个文件）通过。
- 源码 SHA256：
  - `deepseek_client.py`：`ab5152146f85488ec5c2f403df8eb0ccc3a8af8740bb7ff02be0131977c57ae3`
  - `test_room_identity.py`：`0bc1dc67aea4fce5ce2a48438af08361342b7ae04f2aebe8b49978f33727fecb`

## 请复审

1. **房号片段的目标直接取规划日期，不再套用 V4-R1 的「项里有多个日期写法就不继承」**。理由：片段自己没写日期，或已证明与入住日同日。风险：规划日期本身错了，片段也会跟着错；不过同一项里带工具锚点的小句也用同一份规划日期。
2. **住宿项里一个工具锚点都没有时，片段默认核房态**。如果客人实际问的是价格，这里会按房态判定是否覆盖。
3. **没有房号、也没有工具锚点和剩余内容的住宿小句**（例如只有「今晚和明晚两晚」）：既不走静态，也不进工具覆盖，与 1.70.2 之前一致。

## 发布

拟 1.70.3。共享回复流程有变化，需要跑 all 真实模型门禁。版本号、CHANGELOG、发布记录、提交、部署和真实收发都待用户授权。
