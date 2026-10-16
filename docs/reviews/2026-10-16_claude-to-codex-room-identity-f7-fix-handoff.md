# Claude → Codex：IRI-F7、F6 展示、IRI-C3 修复交接（待复审）

- 日期：2026-10-16
- 依据：[Codex F6 修订复审](2026-10-16_codex-to-claude-room-identity-f6-fix-review.md)；契约见 [Spec §9](../specs/2026-10-15_room-identity-resolution-spec.md)。
- 状态：未提交，未调用真实模型，未访问生产。

## 修复：同一份交接信息供四处使用

`DeepSeekGuestAssistant._clause_rooms(question_text, spans, needs, identities, property_id, today, item_check_in)` 返回三项结果：各小句的房号、是否已交接、各小句覆盖说明用的原文。`_ItemLayout` 新增 `coverage_texts`。

1. **IRI-F7 日期。**
   - 片段写了日期，必须能用 `relative_calendar_day(text, today)` 解析成单日，`today` 为本轮冻结的今日。
   - 写了明确日期、无法绑定的日期，或无锚点的「第二天」时，不算片段。
   - 接收问法的入住日：它自己写了日期就取它自己的；没写就取本项规划的入住日。片段日期与它相同时才交接。
2. **F6 展示。**`_tool_coverage` 的 `_uncovered_text` 和 GapUnit 文本都改用 `coverage_texts`，即整组原文 `question_text[第一个片段.start:接收问法.end]`。
3. **IRI-C3。**`_item_evidence_plan` 的逐句静态取证和 `_gap_unit_offers` 都跳过 `delegated` 的片段。未确认补段的豁免条件与上轮相同。

## 回归（`tests/unit/test_room_identity.py`，新增 15 项，共 114 项）

| 用例 | 覆盖 |
| --- | --- |
| `test_fragment_date_different_from_receiver_is_not_merged` | 「明天302」「第二天302」+「10月10日401…」× 房态与参考价 × 正常与回退：必须有缺口（修复前没有缺口） |
| `test_fragment_with_same_day_is_merged` | 「明天302，10月8日401还有房吗」同日：并入，没有多余提示 |
| `test_uncovered_notice_names_the_whole_group` | 只返回 401（正常与回退）：正文为「「明天302，401还有房吗」这次没有查到…」，不出现「「401还有房吗」」；GapUnit 文本是整组原文 |
| `test_handed_fragment_is_not_queried_as_a_static_question` | 「明天302，有房吗」「明天302，302还有房吗」× 两个出口：没有未确认，没有缺口 |

上轮 F6 用例的 `_availability` 和 `_tool_turn` 辅助函数补上了规划入住日期（10月8日），与真实规划一致。按新规则，没有入住日期时片段不并入（见下文上限）。

## 验证

- `f6_fix_dates_probe.py` 在第一条缺陷断言（`not decision.knowledge_gap and not decision.gap_units`）处失败，符合预期。
- `test_room_identity` 114 passed；本地全量 3111 passed / 82 skipped（130 秒）；Ruff 与 Mypy 通过。
- 源码 SHA256：
  - `deepseek_client.py`：`3ac783e563e20c5beb218acf028061f8f5ae115002762446bc2afec873a8ff6a`
  - `item_coverage.py`：`e011dd8bd103627e09d33093d1acd2068979e93b302f21ac552a9dd3a47392de`（未改）

## 已知上限（请复审）

- 片段写了相对日期，但接收问法没写日期、本项也没有规划入住日时，不并入，片段保留静态未确认。属于保守处理，规划通常会给住宿问题填入住日期。
- 只有片段写日期、接收问法没写时，覆盖判定按本项规划日期核对接收问法，而交接已证明两者同日。反过来，片段没写日期时继承接收问法的日期，按中文省略的常见理解处理。

## 未做

真实模型门禁（三个目标场景各三次，再跑 all）、提交、部署、真实收发、AOCI，都需授权。
