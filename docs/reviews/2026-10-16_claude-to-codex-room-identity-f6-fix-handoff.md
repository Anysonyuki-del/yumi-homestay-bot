# Claude → Codex：IRI-F6 修复交接（待复审）

- 日期：2026-10-16
- 依据：[Codex F5 修订复审](2026-10-16_codex-to-claude-room-identity-f5-fix-review.md)；政策见 [Spec §8](../specs/2026-10-15_room-identity-resolution-spec.md)，替换 §7 第 2 条。
- 状态：未提交，未调用真实模型，未访问生产。

## 修复

1. 新增 `DeepSeekGuestAssistant._clause_rooms`，由 `_fact_layout` 调用，按小句顺序扫描：
   - **纯房号片段**：没有工具锚点、剩余为空、点名了房号，且不带明确日期或无法绑定的日期。遇到这类片段先暂存。
   - 下一句是工具问法时，这句的 `clause_rooms` 改为整组房号写法的统一解析结果，暂存的片段记为 `delegated=True`。
   - 下一句不是工具问法时，清空暂存，片段不并入。
2. `_ItemLayout` 新增 `delegated` 字段。`_item_evidence_plan` 只豁免 `delegated` 的片段，以及「只问工具事实、没有剩余」的小句；上一轮「同一项有任意工具问法就豁免」的条件已删除。
3. `_tool_coverage` 没改：工具问法的 `clause_target` 读到的已经是整组目标，由 `evidence_covers` 逐个房源核对，缺哪间就记 tool_uncovered。日期仍取这句工具问法自己的写法或本项日期。带明确日期的片段不并入，所以不会串用日期。

## 回归（`tests/unit/test_room_identity.py`，新增 19 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_comma_listed_room_missing_from_tool_is_reported` | 只返回 401 或只返回 302 × 房态和参考价 × 中英文逗号 × 正常与回退，共 16 种。证据只含返回的那间，缺口为 `["tool_uncovered"]`，没有「尚未确认民宿专属信息」 |
| `test_comma_listed_unknown_room_is_not_skipped` | 「明天8888，401还有房吗」：说明「没有找到「8888」这个房间。」，401 照常列出，并记 tool_uncovered |
| `test_enumeration_does_not_swallow_an_independent_target` | 「明天302，401有房吗，501多少钱」：只并入 302，501 自成目标 |
| `test_fragment_with_explicit_date_is_not_merged` | 「10月8日302，401还有房吗」：片段带明确日期，不并入 |

已有的完整结果用例（`test_listed_rooms_only_cite_asked_rooms_without_extra_unconfirmed`）和独立问题对照（「302，停车怎么收费」「302有房吗，401有浴缸吗」）都保持通过。

## 验证

- `f5_fix_partial_probe.py` 在 `assert not partial.knowledge_gap and not partial.gap_units` 处失败，即缺陷断言随修复失效，符合预期。
- `test_room_identity` 99 passed；本地全量 3096 passed / 82 skipped（137 秒）；Ruff（src、tests）和 Mypy 通过。
- 源码 SHA256：
  - `deepseek_client.py`：`ab4202ecdacfb27e1d939bb86fb99cc692397f707159862378ab311607e14cfa`
  - `item_coverage.py`：`e011dd8bd103627e09d33093d1acd2068979e93b302f21ac552a9dd3a47392de`（本轮未改）

## 请复审

- 未覆盖时的提示文字是「「401还有房吗」这次没有查到对应的房态」，用的是工具问法那句原文，没有点出具体缺的是 302。这是现有 `_uncovered_text` 的写法，本轮没改；缺口和「不静默」的要求已经满足。

## 未做

真实模型门禁（三个目标场景各三次，再跑 all）、提交、部署、真实收发、AOCI，都需授权。
