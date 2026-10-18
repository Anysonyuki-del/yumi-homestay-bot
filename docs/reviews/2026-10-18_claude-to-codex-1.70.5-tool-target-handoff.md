# Claude → Codex：1.70.5 工具结果按需求绑定日期与范围，实施交接（待复审）

- 日期：2026-10-18
- 依据：Codex 1.70.4 发布复审 RV174-1、RV174-2；Spec `docs/specs/2026-10-18_tool-target-binding-spec.md` v1 及 §4；用户「开始」，D-1 至 D-5 按推荐执行。
- 状态：本地实施完成，pyproject 已改为 1.70.5，CHANGELOG 与 `docs/releases/1.70.5.md` 已写。未提交、未跑真实模型门禁、未部署。

## 1. 实现

数据流：`_tool_targets` 在调用前列出需求（工具类型、住宿区间、房号写法） → 每次实际调用时由 `_tool_rooms_for(needs, name, arguments, …)` 按区间匹配同类需求 → 得到这次结果的 `RoomResolution` 或 None → 交给现有的 `_tool_reply_parts` 和 `_weekend_reply_parts` 筛行。`_tool_coverage` 不变，仍按单元或小句的 `ClauseTarget` 核对。

匹配规则：

- 区间相等，或需求没有区间（D-1），即视为匹配；目录不比日期。
- 匹配到全店需求时，按 `resolve_rooms(())`（status none）走全店或上下文语义。
- 都是点名时，合并这些需求的房号写法后解析。
- 一条都没匹配上时返回 None，这次结果不展示。

## 2. 对 Spec 的补充（请重点审查）

| 编号 | 内容 | 原因 |
| --- | --- | --- |
| I175-1 | 周末两种住法按模型原始调用的参数匹配，两种住法共用目标；Spec §2.2 原写「每种住法按自己的日期」 | 两种住法是 `_weekend_argument_options` 从同一次调用展开的。计划只绑定其中一种时，按各自日期匹配会丢掉另一种，两种住法并列展示就失去意义 |
| I175-2 | 回执条件从 `if tool_parts:` 改为 `if tool_parts or tool_needs is not None:` | 回归发现：模型查了计划外日期、结果被整份丢弃后，主模型又输出不合法时整轮不可用（`AssistantUnavailableError`）。这是本版筛选引入的新路径。改后静态答案保留，所问日期报 tool_uncovered |

## 3. 回归（`tests/unit/test_room_identity.py` 新增 13 项；2026-10-18 更正，原误写为 14 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_same_tool_different_dates_keep_their_own_rooms` | RV174-1 房态 × 两个出口 |
| `test_reference_prices_do_not_cross_dates` | RV174-1 参考价 × 两个出口 |
| `test_whole_store_need_is_not_narrowed_by_a_named_need` | RV174-2 × 两个出口 |
| `test_whole_store_on_one_date_does_not_open_another_date` | 全店只在 10/8，10/10 仍只展示点名 × 两个出口 |
| `test_call_on_an_unasked_date_is_not_shown` | 模型查了计划外日期：不展示、报 tool_uncovered，回退出口不失败（I175-2）× 两个出口 |
| `test_need_without_planned_dates_matches_the_call` | D-1 对照 × 两个出口 |
| `test_catalog_need_is_not_filtered_by_stay_dates` | 目录带日期的计划项仍展示全店目录 |

判别力：把 `deepseek_client.py` 换回 HEAD 后，前 10 项失败；D-1 两项与目录一项共 3 项是保持现状的对照。

**Spec §2.5 中没有新增专门用例的项**：

- B 类追问、订房请求的日期：由既有的 `test_tool_parent_makes_follow_up_a_tool_target_under_any_child_label` 间接覆盖，B 单元父日期与调用日期相同；没有构造「B 单元父日期与调用日期不同」的用例。
- 周末两种住法与静态单元同时出现；
- unknown、ambiguous、unavailable 与按需求筛选组合；
- 部分工具失败与按需求筛选组合。

## 4. 验证

- 本地全量：3294 passed / 82 skipped，137 秒；Ruff、Mypy 通过。
- Codex 复现脚本 `release_review_probe.py`：第一条缺陷断言失效（退出 1）。我把它的三组输入逐个打印，房态、参考价、全店三组在两个出口下都符合 Spec。
- 门禁：新增诊断场景 K-房号追问-早餐冲突，列入 known_failures（D-3）。真实模型门禁未运行。

## 5. 待办

1. Codex 复审，特别是 §2 和 §3 中没有覆盖的项。
2. 用户授权后跑 `REPLY_GATE_SCOPE=all`，再提交、部署，并判断是否需要测试号收发：改动影响多工具加静态问题的组合回复，离线覆盖较完整；如果做，建议用「明天还有房吗，2303停车怎么收费，101呢」这类问法。
3. 收尾时做 AOCI 维护。
