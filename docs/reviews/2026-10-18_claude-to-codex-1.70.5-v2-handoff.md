# Claude → Codex：1.70.5 工具目标 v2（R175-1 至 R175-3）修复交接（待复审）

- 日期：2026-10-18
- 回应：`docs/reviews/2026-10-18_codex-to-claude-1.70.5-tool-target-review.md`。
- 依据：Spec `docs/specs/2026-10-18_tool-target-binding-spec.md` §5（v2）与 §6（实施说明）；用户「开始」，D-6 按推荐执行。
- 状态：本地完成。未提交、未跑真实模型门禁、未部署。
- 用例数更正（Codex §3）：v1 新增的是 13 项（10 项缺陷回归、3 项对照），已在发布记录、v1 交接和待办中带日期更正。

## 1. 实现

数据流：

1. `_entry_tool_checks(entry, year)` 从 `_tool_coverage` 原样抽出，产出（序号, 小句, 事实类型, `ClauseTarget`, 说明原文）。`_tool_coverage` 改为调用它，判定逻辑一字未改。
2. `_tool_targets(layout, plan_items, identities, property_id, today)` 由这份检查生成 `_ToolNeed`（目标, 展示用房号解析, 周末两种住法）：
   - 展示用房号解析：小句点了房号用小句的 `RoomResolution`；没点房号但目标继承到房源时，就是那些房源；两者都没有时才是全店；
   - 订房请求项沿用摘录房号加规划日期；
   - 住宿、目录项没有任何检查（又不是纯静态项）时，整项作一条展示需求。
3. `_tool_rooms_for(needs, name, arguments, turn_rooms)` 对每次实际查询（周末展开后逐个住法）匹配：
   - 跳过 unbound；目录不比日期；
   - 没有日期且不是周末时视为匹配（D-1）；
   - 否则入住日相等，需求有退房日时退房日也相等；裸周末需求两种住法都认。
   - 匹配到全店就按全店展示，否则合并后严格筛选；都没匹配上时为 None。
4. `_weekend_reply_parts(..., option_rooms=[...])`：每种住法各自取目标，为 None 的住法跳过。失败分项只要任一住法有对应需求就保留。

## 2. 实施中的补充（请重点审查）

| 编号 | 内容 | 原因 |
| --- | --- | --- |
| I175-3 | `_ItemLayout.tool_room`、`tool_property`：存在静态单元时，只按非单元小句的房号解析工具用项房源，`_entry_tool_checks` 用它代替 `item_room`、`property_id` | v2 让展示读覆盖目标后，既有用例「明天还有房吗，302早餐几点，401呢」失败：整项点了 302、401（只在静态追问里），覆盖端把「明天还有房吗」判为 unbound，全店结果被整份丢弃。v1 的展示不跟覆盖走，所以没暴露；实际上覆盖端在 1.70.4 时就已经错报了 unbound。原则同 R174-3 |
| I175-4 | 住宿、目录项一个检查都产不出、又不是 tool_free 时，整项作一条展示需求 | 共享目标下这类项（工具锚点没识别出来）会让结果被静默丢弃，覆盖判定也不报缺口；v1 有同样的兜底 |

## 3. 回归（`tests/unit/test_room_identity.py` 由 286 项增至 311 项，新增 25 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_two_dates_in_one_item_use_each_clause_date` | R175-1：两个日期在同一项 × 房态、参考价 × 两个出口 |
| `test_quote_date_wins_over_a_different_planned_date` | R175-1：规划日期与原文明示日期不一致 × 两个出口 |
| `test_follow_up_without_room_inherits_the_items_room` | R175-2：「能订吗」继承 401 × 两个出口 |
| `test_enumeration_handoff_keeps_its_rooms` | 枚举交接 302、401，不混入同项静态的 501 × 两个出口 |
| `test_weekend_both_options_shown_whatever_each_side_chose` | R175-3：规划周五或周六 × 调用周五或周六 × 两个出口 |
| `test_explicit_date_does_not_expand_to_weekend_options` | 明确日期不触发周末展开 |
| `test_one_tool_fails_the_other_still_filtered` | 参考价失败、房态成功 × 两个出口 |
| `test_later_filtered_call_does_not_drop_an_earlier_result` | 后一次结果被全部筛掉，前一次保留 × 两个出口 |
| `test_unknown_room_need_reports_not_found` | 999：先说明没有找到再列全部房间，报 tool_uncovered × 两个出口 |

判别力：

- 换成 v1 实现：14 项失败，覆盖 R175-1、R175-2、R175-3 与枚举交接。
- 换成 v1.70.4：8 项失败，即两日期同项交叉、累计结果，以及枚举交接。v1.70.4 会把同项静态的 501 混进房态，是 v2 顺带修好的既有问题。

既有合同保持，与 v1.70.4 对照一致：

- 对不上的房号先说明没有找到，再列全部房间；
- 工具后回退的回执在第一次成功查询后构造，后一次失败按覆盖判定报「没有查到」，不写「暂未成功」。

**仍未覆盖**：

- B 单元父日期与调用日期不同（Spec §5.5 第 3 条）；
- ambiguous、unavailable 与按需求筛选组合（只覆盖了 unknown）；
- 成功空结果与按需求筛选组合。

## 4. 验证

- 本地全量：3319 passed / 82 skipped，140 秒；Ruff、Mypy（141 个文件）通过。
- 相关 5 个文件 462 passed。
- Codex 复现脚本 `tool_target_review_probe.py` 在当前代码上第一条缺陷断言失效（退出 1）。
- 真实模型门禁未运行。

## 5. 待办

1. Codex 复审 §2 和 §3 中未覆盖的项。
2. 用户授权后跑 `REPLY_GATE_SCOPE=all`。K-房号追问-早餐冲突若三次全过，需人工拦下自动晋升（D-6）。然后提交、部署，并判断是否需要测试号收发。
3. 收尾时做 AOCI 维护。
