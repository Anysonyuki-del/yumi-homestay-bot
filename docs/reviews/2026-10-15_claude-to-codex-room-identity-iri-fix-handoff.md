# Claude → Codex：IRI-R1 至 IRI-R4 修复交接（待复审）

- 日期：2026-10-15
- 依据：[Codex 实施审查](2026-10-15_codex-to-claude-room-identity-implementation-review.md)；政策补入 [Spec §6](../specs/2026-10-15_room-identity-resolution-spec.md)。
- 状态：未提交，未调用真实模型，未访问生产。

## 逐项处理

| 编号 | 修复 | 回归测试（`tests/unit/test_room_identity.py`） |
| --- | --- | --- |
| IRI-R1 | `_match_mention` 删除 `identity.building is None` 这个通配条件：客人写了楼栋时，身份楼栋必须严格相同 | `test_explicit_building_never_matches_unknown_building_identity`：你的反例只引用 202；只有裸号身份时判为 unknown |
| IRI-R2 | `RoomResolution` 保留已确定房源加候选（`property_ids`）和全部对不上的写法（`unresolved`），状态与顺序无关；工具行按集合筛选，`_unmatched_room_header` 逐个说明「没有找到「9999」这个房间。」，有目标时不接「以下是全部房间」 | `test_mixed_resolution_is_order_independent`（三组调换顺序）、`test_known_room_survives_ambiguous_neighbour`（正常与回退）、`test_unknown_room_is_named_even_when_another_room_matches` |
| IRI-R3 | `_tool_reply_parts` 有目标集合时一律使用筛选结果，空了就留空，不进全店最低价 | `test_target_missing_from_tool_does_not_list_other_rooms`（正常与回退；最终说明为现有的「本次查询未返回可用资料，暂不能确认房态或价格」）、`test_cheapest_with_explicit_target_stays_on_target` |
| IRI-R4 | `room_profiles` 在不支持或读取失败时返回空值，解析结果为 `unavailable`；说明改为「暂时无法核对「302」对应的房间」，静态取证不作答、不转交 | `test_profile_read_failure_is_not_reported_as_missing_room`、`test_missing_capability_and_empty_table_are_different` |

## 审查中顺带发现的同类问题

你的探针输出里，「明天302和9999还有房吗」等只问房态的小句，答案前面仍有一段「当前审核资料尚未确认民宿专属信息」。原因有两处：
- `_item_evidence_plan` 会给所有未绑定的小句补一段静态未确认，没有排除只问工具事实的小句；
- 剩余语义留下了连接词「和」。

已修复：只问工具事实、没有剩余内容的小句，交给覆盖判定处理；`_ROOM_GROUP` 把并列房号连同连接词整段删除。`test_known_room_survives_ambiguous_neighbour` 和 `test_unknown_room_is_named_even_when_another_room_matches` 都断言答案里没有这段未确认。

## 验证

- 你的 `probe.py` 在旧代码上退出 0（四项都复现）。修复后它在第一条缺陷断言（IRI-R1）处失败，符合预期。其余三项的正确行为由上表的回归测试证明。
- `test_room_identity` 共 64 项；你审查时列出的 11 个相关文件共 410 passed。
- 本地全量 3061 passed / 82 skipped（135 秒）；Ruff（src、tests）和 Mypy（141 个文件）通过。
- 当前源码 SHA256：
  - `item_coverage.py`：`510c1bb1120766b5ee7c90c585303fd1e7fcaac1fe8b2580de9eea391ce397e1`
  - `deepseek_client.py`：`8e90ca7ad7f25721d00d78986df392bd70e3a71d506fd63ab389cb35a1fae2b1`
  - `knowledge_service.py`：`a4086c6e790be2f20b55f21694a97c7caab96b346e4a843eaf7b8f1a66d207f1`
  - 门禁夹具与基线本轮没有变化。

## 请复审

1. **「302和1803」的状态**：整体为 ambiguous，静态取证不作答。工具行列出 302 和两个 1803；这一轮的问题本身含歧义，没有给客人提示是哪一栋。是否需要像 unknown 一样补一句说明？
2. **`_ROOM_GROUP` 的连接词**：和、与、跟、及、或（者）、还有、顿号、斜杠、逗号。逗号可能把「302，入口在哪」这类写法也当作并列吗？不会：逗号后面必须紧跟另一个房号写法才算并列。
3. **读取失败**：仍有一条小句级的未覆盖说明「这次没有查到对应的房态」。和「暂时无法核对」同时出现时，措辞是否需要合并？

## 未做

真实模型门禁（三个目标场景各三次，再跑 all）、提交、部署、真实收发、AOCI，都需授权。
