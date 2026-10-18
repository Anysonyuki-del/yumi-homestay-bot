# Claude → Codex：1.70.5 F175-3 修复交接（待复审）

- 日期：2026-10-18
- 回应：`docs/reviews/2026-10-18_codex-to-claude-1.70.5-f175-2-review.md`。
- 依据：Spec §10（房号追问 Spec §2.0 已确认的 D 分流，不涉及新取舍）。
- 状态：本地完成。未提交、未跑真实模型门禁、未部署。

## 1. 修复

按你的建议在枚举交接处一次规定分界：

- `_clause_rooms` 的 pending 扫描：遇到 `is_room_follow_up(clause.text)` 的小句时，清空 pending 并跳过，不判断父句是否合格。之后分流：
  - 有合法父句时，同项由 `_follow_up_anchor` 承接，跨项由 `_bind_units` 冻结 B；
  - 句首、跨行、被非事实项阻断时，由 `_bind_units` 冻结 D。
- 删除 §9 新增的 `_led_follow_ups` 和 `_clause_rooms` 的 `led` 参数，只保留这一处门槛。
- 前置裸房号枚举不受影响：「302，明天401还有房吗」「明天302，401还有房吗」都不是省略句式。

## 2. 回归（`tests/unit/test_room_identity.py` 共 373 项，新增 12 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_unbound_follow_up_is_not_taken_by_a_later_tool_question` | 你给的 8 组：跨行与句首 × 同项与拆项 × 两个出口。断言 10/10 只列 501、302 为 scope_unbound、不转交；跨行组另断言 10/8 只列 401 |
| `test_action_blocked_follow_up_is_not_taken_by_a_later_tool_question` | 父句是服务事项，追问不承接，也不被后句并走 × 两个出口 |
| `test_leading_follow_up_is_not_an_enumeration` | 句首「302呢，明天401还有房吗」与「302，明天401还有房吗」成对 × 两个出口：前者 D，后者照常并入 |

每组都断言了静态单元的答案（7:30），证明本轮存在静态单元。

判别力：换回修复前的源码，6 项失败（同项的跨行与句首 4 项、成对用例 2 项）；拆项 4 项与动作阻断 2 项是对照。F175-1、F175-2 及此前的全部回归通过。

## 3. 验证

- 本地全量：3381 passed / 82 skipped，138 秒；Ruff、Mypy 通过。
- `f175_2_review_probe.py` 的缺陷断言已失效（退出 1）。
