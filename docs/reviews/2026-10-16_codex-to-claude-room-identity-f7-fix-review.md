# Codex → Claude：房号身份 F7 修订复审

- 日期：2026-10-16。
- 对象：[返回交接](2026-10-16_claude-to-codex-room-identity-f7-fix-handoff.md)、[Spec §9](../specs/2026-10-15_room-identity-resolution-spec.md)、当前源码及相关测试。
- 前轮：[F6 修订复审](2026-10-16_codex-to-claude-room-identity-f6-fix-review.md)。
- 本次只读业务代码，新增本报告；无提交、真实模型调用或生产操作。

## 1. 结论

**本轮限定的三项修复通过复审，未发现新的阻断项。** IRI-F7 日期交接、IRI-F6 提示范围、IRI-C3 重复静态取证均在真实助手正常与协议回退出口闭合。

独立运行 11 个相关文件：**460 passed in 12.85s**。另核验 24 个最终回复组合、6 个布局边界和4个混合目标/独立子问最终回复控制，均通过。

这允许结束本轮代码修订，进入 Spec 规定的门禁准备；**不等于可以发布，也不等于真实模型门禁或客人收件通过**。无需为冻结或报告重复运行仍有效的本地结果。

## 2. 三项闭合依据

| 项目 | 当前文件与符号 | 独立行为证据 |
| --- | --- | --- |
| IRI-F7 日期 | `src/homestay_bot/integrations/deepseek_client.py`：`_clause_rooms.own_day`、`_clause_rooms`；`_fact_layout` 传入冻结今日与本项入住日 | 相对日期先用 `relative_calendar_day` 解释；序数日无法解释则拒绝交接，明确日期仍拒绝。只有片段日期与接收方入住日一致才交接；不同日保留前片段缺口，不再宣布整组已覆盖 |
| IRI-F6 展示 | 同文件：`_ItemLayout.coverage_texts`、`_clause_rooms`、`_tool_coverage` | 覆盖说明从首片段起到接收问法末尾，正文和 GapUnit 都引用整组。“明天302，401还有房吗”只返回401时，未查到提示包含302，不再指称单独的401未查到；房态、参考价、正常/回退均核对 |
| IRI-C3 静态职责 | 同文件：`_item_evidence_plan` 的未绑定补段与逐句静态取证、`_gap_unit_offers` | 三处均排除 delegated；“明天302，有房吗”“明天302，302还有房吗”只保留302工具事实，没有额外 knowledge_missing，也不再开放该片段的静态缺口 |

缺口编号仍使用接收问法的原始区间，只替换说明范围；没有重切原文、生成另一路房号映射或修改真实业务工具契约。只返回部分房间时仍由共享的 `clause_target` / `evidence_covers` 判断，没有用删除缺口来消除提示。

## 3. 补充边界核验

下列24次最终回复调用复用历史证据脚本 `f6_fix_dates_probe.py` 的 `answer` 合成端口，仅调用该函数、不执行其检查旧缺陷的 `main`。固定今日2026-10-07，工具每晚价格日期与查询参数一致，避免价格行日期不匹配造成“碰巧有缺口”的假阳性。

- 房态/参考价 × 正常/回退 × “明天302”“第二天302”“10月8日302”加“10月10日401…”：12次都保留**前片段自身**的 scope_unbound，未把其日期算成10月10日。
- 同日正向控制“明天302，10月8日401…”：4次均无缺口。
- 部分结果“明天302，401…”只返回401：4次都保留缺口，GapUnit 是整组原文，正文不出现单独的“「401…」未查到”。
- 单房源两种写法“明天302，有房吗”“明天302，302还有房吗” × 正常/回退：4次都无缺口。

另外独立检查 `_clause_rooms` 的六个布局输入，今日同上、规划入住日除最后一例外为10月8日：

| 输入 | 预期与实际 |
| --- | --- |
| 明天302，401，501有房吗 | 两个片段交接，最终目标302、401、501 |
| 明天302，后天401，501有房吗 | 日期不能证明一致，两个片段都不交接 |
| 10月8日302，401，501有房吗 | 明确日期的302保留；只将401并入501 |
| 明天302，401有房吗，501多少钱 | 302只并入401房态组；501价格仍独立 |
| 明天302，入口在哪，401有房吗 | 独立静态问题打断枚举，不将302越过它并入401 |
| 明天302，401有房吗，且规划入住日为空 | 无法证明接收日期，302不交接 |

最终回复再核验4次：未知“8888，401”在两个出口都说明8888未找到并保留工具缺口；“明天302，401有房吗，401有浴缸吗”在两个出口都保留浴缸静态子问，未因枚举交接吞掉它。

## 4. 验证与源码绑定

独立相关回归命令：

```sh
.venv/bin/pytest -q tests/unit/test_room_identity.py tests/integration/test_room_identity_sqlite.py tests/unit/test_item_coverage.py tests/unit/test_turn_plan.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_reply_fix2_items.py tests/unit/test_live_price_scope.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_release_scripts.py
git diff --check
```

结果460项通过，差异空白检查通过。作者报告全量3111 passed / 82 skipped、Ruff与Mypy通过；本轮没有重复全量或将作者结果算作独立验证。历史负向探针修后失败仅表示其旧缺陷断言失效，本轮结论依据正确行为断言和上述组合核验。

已核对源码SHA256，与返回交接一致：

| 文件 | SHA256 |
| --- | --- |
| `src/homestay_bot/integrations/deepseek_client.py` | `3ac783e563e20c5beb218acf028061f8f5ae115002762446bc2afec873a8ff6a` |
| `src/homestay_bot/services/item_coverage.py` | `e011dd8bd103627e09d33093d1acd2068979e93b302f21ac552a9dd3a47392de`（本轮未改） |

## 5. 已知边界与后续

- **接受当前保守上限：**片段写了相对日期、接收句没写且规划入住日为空时不交接，保留未确认。原文没有可靠目标就不猜；这一边界已入Spec §9和交接，不是本轮新缺陷。真实规划能否稳定填入日期，仍需真实模型门禁证明。
- 房号标题兜底、裸号碰撞、未知/歧义静态问题不转交的既定取舍不在本轮扩大；测试不能证明真实Hostex标题或生产数据质量。
- 按Spec §2.4，在获当前授权后执行三个目标场景各三次，全部通过再晋升 must_pass，然后跑all并要求无退步；未达到完成条件不能发布，不放宽基线替代修复。
- 提交、推送、部署、真实外部调用和生产收发仍需当前授权。平台受理与客人实际收件分开记录；本地回归及AOCI对齐不能替代外部验收。

本轮未修改业务代码，未检查生产或真实收件。结论仅为当前源码和已列本地行为的复审通过。
