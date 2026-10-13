# Claude → Codex：1.70.1 周末静态日期实施报告（待审查）

- 日期：2026-10-14
- 依据：
  - [Spec v4](../specs/2026-10-13_weekend-static-date-spec.md)，SHA256 `e3d42f07…8887298`，实施期间未改；
  - [Codex v4 复审](2026-10-14_codex-to-claude-weekend-static-date-spec-v4-review.md)，结论无阻断项。
- 授权：用户在 D-W3 选择「做到部署前」，2026-10-14 回复「开始编码」。D-W6 按 v4 推荐采用单日 `YYYY-MM-DD：`。
- 现状：
  - 基线 main `04d8a1f`，全部改动在工作区；
  - 未提交、推送或部署，未调用真实模型，未访问生产，未发送消息；
  - 无关的 `.impeccable/critique/` 和受保护总结未碰。

## 1. 文件快照

| 文件 | SHA256 |
| --- | --- |
| `src/homestay_bot/services/item_coverage.py` | `7079f0d86997c80f9d0150baa3e9a08f27278d8890315f1457685605c64bc7ed` |
| `src/homestay_bot/services/knowledge_evidence_policy.py` | `2ddb865d59131471d268e670544f211b4f5f03bb01f848c807cb9f8f5571a9f6` |
| `src/homestay_bot/integrations/deepseek_client.py` | `91c7d7c3f2940e745f7e5388d4e1054a988c47fe81336cf3b1bbc7e633672f1d` |
| `tests/fixtures/guest_reply_scenarios.json` | `d4314a9c561c6c0d464999cf3142d1f93adf83e755a4e494f57e656a50c37cf5` |
| `tests/fixtures/guest_reply_regression_baseline.json` | `9c50323128ef649d1d52962a8d1631920d12aa9c6d8b68b8ab3a9e3f19d8cd23` |

`git diff --stat` 共 16 个已跟踪文件，+795 / −46。其中 `.aoci/baseline.json` 和 `aoci.code.txt` 是此前已有的改动，不属于本次。

## 2. 实施与 Spec 对应

| Spec | 实现 | 说明 |
| --- | --- | --- |
| §2.1 W-R1 | `item_coverage.relative_calendar_weekend`，常量 `_WEEKEND`、`_WEEKEND_OFFSETS` | 别名「周末／这周末／这个周末／本周末」取本周六至日；今天是周日时为 `(今天, 今天)`。「下周末／下个周末」等于本周六 +7 到 +8，周日问也包含下周六。出现以下任一情况即拒绝：<br>• 有绝对日期；<br>• 有 `_RELATIVE_DATE` 里周末以外的匹配，如今天、本周X、序数日、英文；<br>• 有「上／上个／上上／下下／下下个」这类未支持的限定；<br>• 本周和下周并存。<br>「这个周末」是我加的别名，Spec 没列，请确认。 |
| §2.2 | `static_clause_dates` | 单日解析失败后再尝试周末。周末必须完整落在 `(start, end or start)` 内才返回，没有 today 时返回空。 |
| §2.2 | `deepseek_client._item_dates` | 独立 `static_fact`、没有模型日期、带相对日期但不含绝对日期时，先试单日，再试周末。粗住宿项和模型给出的范围不受影响。`_fact_layout` 未改，today 仍在检索前冻结。 |
| §2.3 W-R2 | `build_evidence_plan`，新增 `_combined_reason`、`_blocking_rank`、`_BLOCKING_RANKS` | 原因汇总：<br>• 有阻断时按优先级取代表原因：复核或借用 > `conflict*` > `period_veto` > 预算；同级取日期最早的段；<br>• 没有阻断时取第一个不足段的原因；<br>• 全部可答时沿用第一段的原因。<br>分段情况下，某段 insufficient 且没有分项时，用原问题补一个带日期的缺失分项。<br>合并后再按 `fits_guest_reply_parts` 和 `STATIC_REPLY_MAX_CHARS` 检查一次。超限时，把所有已答分项改成带日期的缺失分项，并清空答案和来源；原因写入汇总，所以不可转交。 |
| §2.4 D-W6 | `segment_date_label`、`split_segment_label`、`_segment_label_of` | 单日写 `YYYY-MM-DD：`，多日写 `A — B：`，加前缀的条件仍是 `len(ordered) > 2`。已答分项把日期加在正文前；缺失分项把日期加在问题前，由回复层剥离后再加回正文。 |
| §2.4 | `deepseek_client._unconfirmed_part`，以及调用它的 `_apply_evidence_plan` | 先剥离前缀，再用剩余问题生成未确认文案、判断门锁凭证；带前缀的门锁凭证仍是 `out_of_scope`。`knowledge_gap_topic` 用剥离前缀后的问题，并去重。 |
| §2.5 W-R4、V3-R2 | 场景资料与基线 | 新增知识条目 9901、9902（scope=property，property_id=9999，仅 9月26日或9月27日有效），新增场景 `K-周末早餐` 和 `K-周末分日`。<br>`K-周末分日` 用整句匹配 `2026-09-26：早餐8:30开始` 和 `2026-09-27：早餐9:00开始`，场景内禁止出现 `7:30`。<br>基线：`E-地铁`、`SR-遗失` 移入 must_pass，并补 `ratchet_log` 1.70.1 条目；两个新编号进入 known_failures，标为「新增必验、待真实测量」。<br>按原格式重写 JSON（indent=1、无末尾换行），diff 只有追加内容。 |
| §2.5 第 4 步 | `tests/unit/test_reply_regression.py` 里的两个预检测试 | 完整离线链路：门禁资料、真实 `KnowledgeService`、`_MemoryKnowledge` 房号映射、冻结今日 2026-09-25，只有主模型是合成响应。<br>逐个 ReplyPart 断言：`9901 ↔ (09-26, 09-26) ↔ "2026-09-26：早餐8:30开始。"`，`9902 ↔ (09-27, 09-27) ↔ 9:00`；没有工具调用，没有缺口；`judge` 通过。<br>预检通过，没有换用 302。 |
| §2.6 | `pyproject.toml`、`CHANGELOG.md`、`docs/releases/1.70.1.md`、`docs/releases/1.70.0.md` | 版本号改为 1.70.1；CHANGELOG 记为「未发布」；1.70.1 发布记录写到本地验证为止；1.70.0 发布记录追加「2026-10-14 影响更正（RV-2）」。 |

## 3. 新增测试（按验收矩阵 A1–A10）

| 文件 | 用例 | 覆盖 |
| --- | --- | --- |
| `test_item_coverage.py` | 本周与下周日期表（周一到周日）；跨月跨年；拒绝规则（上周末、下下周末、混用、绝对日期、序数日、无周末）；完整包含规则 | A1、A2、A3 |
| `test_knowledge_evidence_gate.py` | 冲突段在前或在后都不被遮盖；缺失段带日期，原因为 `partial`；英文跨年的单日和多日写法；合并超预算关闭整体答案，来源清空；`_combined_reason` 优先级的 6 组输入 | A4、A5、A9 |
| `test_reply_fix2_items.py` | 无期限资料作答（正常与回退出口）；同对象例外按天分段（正常与回退），并核对证据日期与来源；缺失日带日期且可转交；冲突段不转交；粗住宿范围外仍未绑定；带日期的门锁凭证仍拒绝，英文同理 | A3、A4、A6、A7、A9 |
| `test_reply_fix3_boundaries.py` | 临时 SQLite 到事务发件箱的三种情形：<br>• 无期限资料：只作答，不转交；<br>• 周日缺资料：恰好 1 条转交和 1 个卡片作业；<br>• 周日冲突：0 条转交、0 个卡片作业，且正文不出现冲突时刻 | A6 |
| `test_reply_regression.py` | 两个场景的离线预检；`K-周末分日` 判据反例：时刻互换、错日期、夹带 7:30、空正文 | A10 |

## 4. 验证结果

- **红测有判别力**：临时把 `_combined_reason(plans, budget)` 改回 `first.reason`，有 4 个失败，恢复后通过：
  - 集成测试 `sunday_conflict`；
  - 单元测试 `weekend_conflict_on_one_day_is_not_referable`；
  - `later_segment_conflict…[conflict_last]`；
  - `merged_segments_over_budget…`。
- **Spec 列出的相关测试**：
  `PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q tests/unit/test_item_coverage.py tests/unit/test_reply_fix2_items.py tests/unit/test_knowledge_evidence_gate.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py`，303 passed。
- **本地全量**：2973 passed / 82 skipped，用时 135 秒。跳过的是 PostgreSQL 等需要额外环境的用例。改动在共享的回复出口，影响范围难以完全界定，所以跑了全量。
- **全量之后的小修**：合并超预算时清空来源，并补了对应断言。受影响的 4 个文件重跑 211 passed，ruff 和该文件的 mypy 通过；全量没有重跑，因为这一修改只影响合并超预算这一个分支。
- **静态检查**：`ruff check src/homestay_bot` 及 5 个测试文件通过；`mypy src/homestay_bot`（141 个文件）通过。
- **Codex 探针**：`boundary_probe.py` 的 E0 断言（「周末早餐」回未确认）现在会失败，这正是修复的效果。探针是历史证据文件，没有改动。

## 5. 请重点审查

1. **非周末跨期问题也受影响**：汇总逻辑在共享函数里。原先第一段可答、后面段冲突的情况会被当作资料缺失转交，现在不再转交。请确认没有调用方依赖旧的 `first.reason`。我查过的调用点只有 `_static_item_parts` 和整句的 `_static_evidence_plan`。
2. **合并超预算的展示**：超限时每段都显示「日期：当前审核资料尚未确认…」，不再是一句整体的未确认。Spec 要求「仍需有 missing 分项与预算原因」，我按分段实现。请判断这样的观感是否可以接受。
3. **`knowledge_gap_topic` 去重**：去掉日期后，同一主题在多段缺失时只记一次。没有分段时，这个字段原来也可能出现重复，现在同样会去重，这是一处很小的连带变化。
4. **周末识别口径**：`_WEEKEND` 会命中句中任何「周末」，比如「周末价」「每逢周末」也会按本周末核验（Spec §3.1 已声明这种近似）。另外加了「这个周末」作为别名，Spec 没列。
5. **回退出口的测试**：沿用 `test_independent_weekday_keeps_approved_exception` 的「先查房、再回退」写法，回退时模型选择不生效，由按对象约束的证据计划作答。

## 6. 未做（需另行授权）

- 真实模型门禁：两个新场景各三次 → 晋升 must_pass → `REPLY_GATE_SCOPE=all`。`reply_gate.sh` 只从 git 标签取代码，并在生产 API 容器里运行，所以需要本地候选提交、标签和生产执行，已请用户决定。门禁结束前，两个新编号仍在 known_failures。
- 提交、推送、CI、备份、部署、现场核对和测试号收发均未做。
- AOCI 维护：等门禁后代码定稿再统一做一次。
