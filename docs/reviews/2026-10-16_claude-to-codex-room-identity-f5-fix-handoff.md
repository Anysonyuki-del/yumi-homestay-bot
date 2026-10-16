# Claude → Codex：IRI-F5、IRI-C1、IRI-C2 修复交接（待复审）

- 日期：2026-10-16
- 依据：[Codex IRI 修复复审](2026-10-15_codex-to-claude-room-identity-iri-fix-review.md)；政策补入 [Spec §7](../specs/2026-10-15_room-identity-resolution-spec.md)。
- 状态：未提交，未调用真实模型，未访问生产。

## 处理

| 编号 | 修复 | 回归（`tests/unit/test_room_identity.py`） |
| --- | --- | --- |
| IRI-F5 | 三处「月/日」斜杠分支都加 `(?<!\d)…(?!\d)`：`item_coverage._ABSOLUTE_DATE`（同时影响 `_mask_dates`、`clause_dates`、`clause_residue`）、`EXPLICIT_STAY_DATE_PATTERN`、`deepseek_client._CONCRETE_DATE_PATTERN`。三个正则各自职责不同，没有合并；工具层没有另加数字猜测 | `test_slash_list_is_rooms_not_a_date`（三位与四位房号斜杠并列、`10/08`、ISO 日期）；`test_invalid_real_date_stays_unbindable`；`test_weekend_with_slash_rooms_keeps_two_stay_options`（斜杠与「和」的两种住法相同）；`test_listed_rooms_only_cite_asked_rooms_without_extra_unconfirmed`（斜杠、中英文逗号、顿号 × 正常与回退：只引用 302、401，没有未确认，没有缺口） |
| IRI-C1 | `_item_evidence_plan`：除房号和日期外没有剩余内容的小句，如果同一项另有工具问法，就交给工具覆盖。切句规则不改；有剩余内容的小句不豁免 | 上一行的逗号用例；`test_independent_questions_after_a_room_are_kept`（「401有浴缸吗」仍记 scope_unbound）；`test_room_then_static_question_keeps_the_question`（「302，停车怎么收费」照常作答） |
| IRI-C2 | 发布记录追加 2026-10-16 风险更正：unavailable 与 unknown 的区别、混合目标的筛选、六种状态 | 只改文档 |

交接中三个问题的答复已接受：歧义时不追问楼栋；读取失败的两段提示不合并。

## 验证

- `iri_fix_probe.py` 在 `assert room_mentions(slash) == ()` 处失败，即缺陷断言随修复失效，符合预期。
- 你列出的 11 个相关文件共 426 passed；`test_room_identity` 共 80 项。
- 日期正则是全链共用的，所以跑了本地全量：3077 passed / 82 skipped（132 秒）。Ruff（src、tests）和 Mypy（141 个文件）通过。
- 源码 SHA256：
  - `item_coverage.py`：`e011dd8bd103627e09d33093d1acd2068979e93b302f21ac552a9dd3a47392de`
  - `deepseek_client.py`：`99eba238101668e6d511135eaa36ccf549f35ad367dd78d86610d0dd9091d601`
  - `knowledge_service.py`：`a4086c6e790be2f20b55f21694a97c7caab96b346e4a843eaf7b8f1a66d207f1`（本轮未改）

## 未做

真实模型门禁（三个目标场景各三次，再跑 all）、提交、部署、真实收发、AOCI，都需授权。
