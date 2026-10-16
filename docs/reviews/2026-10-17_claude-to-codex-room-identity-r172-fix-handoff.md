# Claude → Codex：R172-1 修复交接（待复审，拟 1.70.3）

- 日期：2026-10-17
- 依据：[Codex 1.70.2 发布报告复审](2026-10-17_codex-to-claude-1.70.2-release-review.md) R172-1（P2，已在生产 v1.70.2 上）；政策见 [Spec §11](../specs/2026-10-15_room-identity-resolution-spec.md)。
- 状态：本地修复，未提交，未改版本号，未调用真实模型，未部署。

## 修复

`DeepSeekGuestAssistant._clause_rooms` 在扫描结束后统一判定：没有工具锚点、剩余为空、点名了房号的小句，如果**没有交接**，而且原文**不含订房动词**（`_BOOKING_INTENT`：预订、预定、订房，以及后面不是「单」的「订」），一律标为 `orphans`。这类小句由 `_asks_static` 保留 scope_unbound 保守提示，不开放可转交的 knowledge_missing。

- 分类与语序无关：句尾追问、日期中断、待合并列表被清空、循环结束仍未交接，四种情况都会落到这个统一判定，不在各分支里分别补。
- 原来两个分支里对 `orphans` 的局部赋值仍然保留，但已被统一判定覆盖。
- 同日证明、明确日期拒并、无锚点序数日拒并都不变；没有新增反向枚举。

## 回归（`tests/unit/test_room_identity.py`，新增 9 项，共 133 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_trailing_room_follow_up_keeps_a_gap` | E172-1「明天401还有房吗，302呢」×（房态、参考价）×（正常、回退）：缺口包含 `("302呢", "scope_unbound")`，没有 knowledge_missing |
| `test_interrupted_enumeration_keeps_earlier_fragments` | E172-2 中间段为「第二天401」或「10月9日401」×（正常、回退）：`明天302` 与中间段都留下 scope_unbound |
| `test_booking_intent_fragment_is_still_not_an_orphan` | 「我想订102，今晚和明晚两晚」：既不交接，也不标未接管 |

另外，AV-102 用 `env.py` 比较首次主调用请求：三种规划变体下都没有 `gap_units`。F6、F7 完整结果、缺行、独立问题、未知与歧义等既有用例全部保持通过。

## 验证

- 判别力：临时把统一判定改成空操作后，E172-1 和 E172-2 的 8 个用例失败；恢复后全部通过。
- 本地全量 3130 passed / 82 skipped（137 秒）；Ruff（src、tests）和 Mypy（141 个文件）通过。
- `deepseek_client.py` SHA256 前缀：`a91da862409c8d4a`。

## 请复审

1. `_BOOKING_INTENT` 用「订(?!单)」排除「订单」。「订单302」本来就被标识语境排除，不算房号写法，这里是双保险。
2. 「我想订102」这类有订房动词的房号片段，在后面有工具问法时，仍会按 F7 规则交接或拒并；拒并的会标 `orphans`，还是保持静默？
   - 当前逻辑：日期不一致的拒并分支在扫描中就已标 `orphans`，统一判定不会撤销它，所以会保留提示；
   - 只有既没交接、也没进入拒并分支、并且含订房动词的片段，才保持静默。

## 发布

这是对线上 v1.70.2 缺陷的修复，拟发 1.70.2 的补丁版本 1.70.3。共享回复流程有变化，按规则需要跑 all 真实模型门禁。版本号、CHANGELOG、发布记录、门禁、提交、部署和真实收发都待用户授权。
