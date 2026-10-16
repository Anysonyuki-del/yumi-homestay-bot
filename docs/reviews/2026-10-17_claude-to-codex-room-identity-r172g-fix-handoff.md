# Claude → Codex：R172-G1、R172-G2 修复交接（待复审，拟 1.70.3）

- 日期：2026-10-17
- 依据：[Codex R172-F 修复复审](2026-10-17_codex-to-claude-room-identity-r172f-fix-review.md)；契约见 [Spec §13](../specs/2026-10-15_room-identity-resolution-spec.md)。
- 状态：本地修复，未提交，未改版本号，未调用真实模型，未部署。

## 修复

1. **G1**：`_clause_rooms` 新增内部函数 `fragment_dates_match`，取代扫描后判定里用 `own_day` 只比较入住日的做法。
   - 用 `clause_dates` 保留端点角色：完整区间要求入住、退房两端都与规划一致；只有入住日时比较入住日；只有退房日、无法绑定、相对日期解析不出单日时返回假，片段记 `"unbound"`。
   - `_clause_rooms` 新增参数 `item_check_out`，由 `_fact_layout` 传入本项规划的退房日。
   - 合并阶段的 `own_day` 不变：带明确日期的片段本来就不合并。
2. **G2**：`_tool_coverage` 的目录分支在 `target.property_ids` 非空时，要求每个房源都有同一 `property_id` 的目录证据行。
   - 空房源（对不上的写法）或未绑定的目标都不算覆盖。
   - 没点名房间时，仍沿用「有任一目录行即覆盖」。
   - 没有复用 `evidence_covers`，避免套用住宿日期比较。
   - `_uncovered_text("property", …)`：小句点名了房号时改为「「302呢」这次没有查到对应的房源资料」，英文同理；没点名时仍是原来的全店目录说明。
3. 注释：`_clause_rooms` 的说明删除「我想订102 两者都不是」的旧说法。

## 回归（`tests/unit/test_room_identity.py`，新增 20 项，共 166 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_fragment_range_needs_both_endpoints` | 「10月8日至10日302」（退房日不同）、「10月8日至9日302」（两端一致）、「10月9日退房302」（只有退房日）×（房态、参考价）×（正常、回退）：只有两端一致的那种没有缺口，其余都是 `scope_unbound`，不是 knowledge_missing |
| `test_catalog_named_room_needs_its_own_row` | 「401有什么房型，302呢」只返回 401、「…，9999呢」只返回 401、302 也返回、全店「有什么房型」×（正常、回退）：前两种的缺口为 tool_uncovered，正文含「「302呢」/「9999呢」这次没有查到对应的房源资料」；后两种没有缺口；都没有转交候选 |

你给的离线反例重跑结果：
- 「10月8日至10日302」两个出口都是 `scope_unbound`，正文有说明；
- 目录 302 和 9999 两个出口都是 `tool_uncovered`，并指出是哪一句；
- 全部返回、全店目录两种情况都没有缺口。

R172-F1、F2 的订/住/要和三种 hint 用例全部保持通过。

## 验证

- 本地全量 3163 passed / 82 skipped（143 秒）；Ruff（src、tests）和 Mypy（141 个文件）通过。
- 源码 SHA256：
  - `deepseek_client.py`：`1be771bb96cc9840c5c7a62f8cd7dbde398844bfe96c259161098d73ce9d749b`
  - `test_room_identity.py`：`04406a1a520590922a7fa31fa9ea768bea537a2151152179983f3d933bf12668`

## 已知边界（请复审）

- 目录里对不上的房号（9999），只说「这次没有查到对应的房源资料」，不像房态那样说「没有找到这个房间」。这是因为 `_unmatched_room_header` 只用于房态和房价，本轮没有扩到目录。
- 歧义房号在目录里要求每个候选都有目录行才算覆盖，和房态同口径。

## 发布

拟 1.70.3，需要 all 真实模型门禁；版本号、CHANGELOG、发布记录、提交、部署和真实收发都待用户授权。
