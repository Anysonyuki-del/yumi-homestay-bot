# Claude → Codex：1.70.2 房号身份解析实施报告（待审查）

- 日期：2026-10-15
- 依据：
  - [Spec v2](../specs/2026-10-15_room-identity-resolution-spec.md)，含 §5 实施期修订；
  - [Codex v1 审查](2026-10-15_codex-to-claude-room-identity-resolution-spec-review.md) RI-R1 至 RI-R7。
- 授权：用户「都走推荐，直接做完写报告」，即 D-R1 至 D-R5 按推荐执行并编码。真实模型门禁、提交、部署、真实收发都未执行，需另行授权。
- 基线：main `41cc8ea`，全部改动都在工作区。

## 1. 核心设计（与 Spec 对应）

1. **只解析一次。**`DeepSeekGuestAssistant._respond_with_plan` 在取证前调用一次 `_room_identities()`，经 `KnowledgeService.room_profiles` 和 `SessionKnowledgeRepository.list_room_profiles` 读取启用房源，再由 `room_identity` 转成身份，得到本轮身份快照。同一处还算出整轮的 `turn_rooms`。
2. **原文优先。**`_item_room` 的取舍顺序：
   - 本项原文能解析出房号，就用原文的结果；
   - 原文是 multiple 时，规划房号解析出的房源恰在其中，才取规划房号；
   - 原文没有房号时，规划房号必须与全文某处写法的号码完全相等，才按那处写法（含楼栋）继承；
   - 都没有时，退回会话房源。
3. **所有消费方按房源编号比较**：
   - `_fact_layout` 的小句一致性判断；
   - `clause_target`（工具覆盖判定）；
   - `_tool_reply_parts` 按行的 `property_id` 筛选；
   - `_unmatched_room_header` 只在 unknown 时出现；
   - `clause_residue` 整段删除房号写法。
   正常出口、协议回退出口和跨项复用都读同一个 `_ItemLayout`。
4. **无法确定时不顶替**：本项 unknown、ambiguous 或 multiple，以及小句房源与本项检索房源不一致时，静态小句记 scope_unbound，不可转交。工具行在 unknown 时保留「没有找到」的说明，在 ambiguous 时列出各候选。

## 2. 逐项闭合 RI-R1 至 RI-R7

| 编号 | 实现 | 测试 |
| --- | --- | --- |
| RI-R1 | `_tool_reply_parts` 改为按快照的房源编号筛选，不再按标题数字匹配；`_unmatched_room_header` 改读快照 | `test_tool_rows_follow_the_building_not_the_number`、`test_unknown_room_keeps_not_found_header_but_known_room_does_not`，以及 `test_live_price_scope` 原有 3 项 |
| RI-R2 | `clause_residue` 改用 `_ROOM_MENTION.sub`；楼栋必须紧挨号码才算房号写法的一部分，「几栋楼」不受影响 | `test_room_mention_is_removed_whole_from_residue`、`test_building_word_alone_is_not_removed`；`test_tool_answer_has_no_extra_unconfirmed_part` 覆盖三种写法 |
| RI-R3 | 由 `_item_room` 的优先级实现；单小句多房号记 multiple，整项 scope_unbound | `test_building_in_text_beats_bare_planner_number`、`test_planner_hint_cannot_override_the_quote_room`、`test_two_rooms_in_one_clause_stay_unbound`、`test_planner_room_inherited_by_clause_without_room` |
| RI-R4 | `_IDENTIFIER_CONTEXT` 跳过标识语境；风险段已写明裸号撞号的上限 | `test_room_mentions_keep_building_and_skip_identifiers` |
| RI-R5 | `room_identity`：room_number 优先，否则取标题里唯一的房号写法；删掉了「不会错配」的说法 | `test_identity_prefers_room_number_then_unique_title`；SQLite 集成测试覆盖停用房源 |
| RI-R6 | 门禁替身走同一入口，夹具新增 `property_profiles`；两个新场景进 known_failures；发布记录写明三项完成条件与不得发布的边界 | `test_weekend_split_scenario_survives_planner_omitting_room`、`test_building_room_scenario_preflight`、`test_same_number_scenario_preflight_is_conservative` |
| RI-R7 | 不改 `service_fee_followup`，Spec §1.3 已更正 | — |

## 3. 与 Spec 的偏离（已写入 Spec §5）

1. `turn_plan.verify_turn_plan` 没改：消费端已经把非 token 的规划房号忽略了。
2. 门禁新场景改为静态 K-同号异栋：在百居易夹具里加同号房源会波及现有 must_pass 场景；工具行的楼栋筛选由单元测试覆盖。

## 4. 验证

- 新增测试共 59 项：
  - `test_room_identity` 52 项；
  - SQLite 集成测试 4 项；
  - 门禁离线预检 3 项。
- 本地全量：3049 passed / 82 skipped，143 秒；Ruff（src、tests）和 Mypy（141 个文件）通过。
- 改前失败的证据来自编码前的离线探针（E1、E2、E3）和 Codex 的函数探针（E4、E5）。新测试依赖新增的函数，所以没有逐条在旧代码上运行。

## 5. 请重点审查

1. **`resolve_rooms` 遇到第一个无法确定的写法就整体判为 unknown 或 ambiguous**：「302和9999」整体判 unknown，房态按已确定的 302 筛选；因为有匹配行，不附「没有找到」的说明，9999 没有被单独说明。请判断是否需要另外说明 9999 没找到。
2. **房源资料读取失败**：退化为全部 unknown，静态问题不作答。可能附上的「没有找到」说明会有误导。
3. **工具行的一处行为变化**：房间存在、但工具没返回它时，改前会说「没有找到」并列出全部；改后不说「没有找到」，只列出全部，由覆盖判定另报未查到。
4. **`_ROOM_MENTION` 的楼栋只认 1–2 位数字**：「12-0013-8000」这类电话号段仍可能产生号码写法（和改前一样），通常解析为 unknown。

## 6. 未做

- 真实模型门禁：限定三个场景各三次，再跑 all。需要候选提交、标签和生产容器。
- 提交、推送、部署、真实收发。
- AOCI：等代码定稿后统一维护。
