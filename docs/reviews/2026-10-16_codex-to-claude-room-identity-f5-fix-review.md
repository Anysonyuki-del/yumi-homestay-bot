# Codex → Claude：房号身份 F5 修订复审

- 日期：2026-10-16。
- 对象：[返回交接](2026-10-16_claude-to-codex-room-identity-f5-fix-handoff.md)、[Spec §7](../specs/2026-10-15_room-identity-resolution-spec.md)、当前代码与相关回归。
- 前轮：[IRI 修复复审](2026-10-15_codex-to-claude-room-identity-iri-fix-review.md)。
- 范围：只读业务审查，新增本报告和合成证据脚本；未修改业务代码，未执行提交、真实模型或生产操作。

## 1. 结论

**IRI-F5 斜杠日期碰撞已闭合，IRI-C2 发布风险更正已闭合。IRI-C1 解决了完整工具结果下的多余提示，却新引入部分结果静默漏答，仍需修复。**

独立相关回归 **426 passed in 12.32s**。另用正常/回退、房态/参考价、五种连接符和完整/部分结果组成 40 次合成助手调用，复现下述 **1 项 P2（IRI-F6）**。这些调用不访问真实模型、Hostex 或企业微信。

建议保留日期边界修复，补齐“纯房号片段转交工具后，仍必须检查每个目标”的闭环，再进入已规定的真实模型验收；当前不作为可发布结论。

## 2. 已闭合依据

| 前轮项 | 当前文件与符号 | 独立核验 |
| --- | --- | --- |
| IRI-F5 | `services/item_coverage._ABSOLUTE_DATE`、`EXPLICIT_STAY_DATE_PATTERN`；`integrations/deepseek_client._CONCRETE_DATE_PATTERN` | 三处斜杠分支均增加完整数字边界。`302/401`、`2303/2203` 识别为房号，剩余为空；`10/08`、ISO 日期仍识别日期，`2/30` 仍无法绑定。固定今日 2026-10-07，周末加斜杠房号与加“和”均展开相同双住法 |
| IRI-C1 的完整结果路径 | `DeepSeekGuestAssistant._item_evidence_plan` | 工具返回 302、401 时，中文/英文逗号不再多拼静态未确认；斜杠、顿号和“和”控制也只引用所问房间，没有扩大到 9999。部分结果问题见下一节 |
| IRI-C2 | `docs/releases/1.70.2.md` 的“2026-10-16 复审修复”风险更正 | 已明确 `unavailable` 与成功空表 unknown 的区别、混合候选筛选及六种状态，并注明取代旧风险描述。旧文保留为历史，不再列为未修复项 |

426 项包含前轮楼栋严格匹配、混合目标、工具缺行、读取失败及新增日期/独立问题控制。本轮不将作者全量结果当作独立全量。

## 3. IRI-F6 · P2：逗号前的房间取消静态提示后，没有纳入工具覆盖

### 真实调用链

1. `item_coverage.split_clauses` 把“明天302，401还有房吗”切成“明天302”和“401还有房吗”。`DeepSeekGuestAssistant._fact_layout` 保留两段房号，并据整项得到多房源目标。
2. `deepseek_client.py:1715–1721` 的 `_item_evidence_plan` 新规则看到第一段只有房号、没有剩余内容，且同项存在工具问法，便取消该段的静态缺口。这是本轮新增的 IRI-C1 修复。
3. 但 `_tool_coverage` 第 1859–1860 行仍直接跳过 `not needs.tool_facts` 的小句。“明天302”自己没有“有房/多少钱”锚点，**没有被任何覆盖检查接手**。
4. 后半句只检查 401。工具若仅返回 401，最终 `knowledge_gap=False`、`gap_units=[]`，正文只回答 401；302 的缺失完全没有提示。

`_tool_reply_parts` 仍正确筛选已有目标，IRI-R3 的“不回退全部房间”没有退化。问题是消除多余静态提示时丢掉了原先对未覆盖目标的可见提醒。

### 可复现输入与结果

合成身份有 302、401、9999；工具只返回 401 的 10 月 8 日入住、10 月 9 日退房数据。参考价控制为 401 每晚 200 元。

| 问法 | 最终证据 | knowledge_gap / 缺口 | 最终行为 |
| --- | --- | --- | --- |
| 明天302和401还有房吗 | 401 | true / tool_uncovered | 回答 401，并说明所问房态尚未完全查到 |
| 明天302/401还有房吗 | 401 | true / tool_uncovered | 同上，日期修复有效 |
| 明天302、401还有房吗 | 401 | true / tool_uncovered | 同上 |
| 明天302，401还有房吗 | 401 | **false / 空** | 只说“可订：401测试房”，不再提 302 |
| 明天302,401还有房吗 | 401 | **false / 空** | 同上 |
| 明天302，401多少钱 | 401 | **false / 空** | 只列“401测试房：10月8日 200元”及参考价说明，302 静默漏答 |

中文/英文逗号 × 房态/参考价 × 正常 JSON/协议回退均成立；完整两房结果均正常。现有 `test_listed_rooms_only_cite_asked_rooms_without_extra_unconfirmed` 只喂完整结果，所以 426 项通过不能排除此反例。

### 最小修复建议

- 在现有小句布局/覆盖边界识别确定的枚举组，让“302，401还有房吗”中的纯房号片段与相邻工具问法建立关系；对这个组保留全部目标，调用现有 `clause_target` / `evidence_covers` 检查，而不是仅取消静态提示。
- **只有确定该片段已交由覆盖检查接手时，才允许删除它的静态缺口。** 无法证明枚举关系时保留未确认即可；不能只以“同项任意位置有工具问法”作完成条件。
- 不要全局删掉逗号切句，也不要把整项所有房间、所有工具类型和所有日期笼统合并。“302，401有房吗，501多少钱”中的 501 是独立目标；房态不能因有参考价而算已覆盖，日期不同也不能套用另一段的日期。
- 保留正常和回退出口共用布局与覆盖判定，不在两个出口各补提示。工具缺口继续不可转交管家，不恢复无条件的“民宿专属信息未确认”来掩盖缺行。

建议同次补齐以下少量有判别力的验收：完整两房不多提示；只返回 401 时提示 302 未覆盖；只返回 302 时提示 401 未覆盖；未知/歧义枚举成员不被静默跳过；房态和参考价均覆盖正常/回退；“302，入口在哪”“302有房吗，401有浴缸吗”仍保留独立问题；显式日期或独立目标不同的片段不得串用。无需扩展新房号语言、工具或全局切句规则。

## 4. 验证与证据

本轮独立运行：

```sh
.venv/bin/pytest -q tests/unit/test_room_identity.py tests/integration/test_room_identity_sqlite.py tests/unit/test_item_coverage.py tests/unit/test_turn_plan.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_reply_fix2_items.py tests/unit/test_live_price_scope.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_release_scripts.py
PYTHONPATH=src:. .venv/bin/python docs/reviews/2026-10-15-room-identity-implementation-evidence/f5_fix_partial_probe.py
.venv/bin/ruff check docs/reviews/2026-10-15-room-identity-implementation-evidence/f5_fix_partial_probe.py
git diff --check
```

- 相关测试 **426 passed in 12.32s**；Ruff 和差异空白检查通过。
- [补充探针](2026-10-15-room-identity-implementation-evidence/f5_fix_partial_probe.py) 20 个完整结果控制通过；20 个部分结果对照中，8 个逗号组合静默漏答，12 个其他连接符组合保留未覆盖提示。脚本退出 0 **表示反例成立，不表示修复通过**。
- 作者报告全量 3077 passed / 82 skipped、Ruff、Mypy 通过；本轮未重复全仓及真实模型，不将作者结果改写为独立证据。
- 原 `iri_fix_probe.py` 断言旧缺陷，修后失败属于预期，不能据此宣称所有连带行为正确；本次新增探针专门区分完整结果与部分结果。

源码摘要已核对，与返回交接一致：

| 文件 | SHA256 |
| --- | --- |
| `src/homestay_bot/services/item_coverage.py` | `e011dd8bd103627e09d33093d1acd2068979e93b302f21ac552a9dd3a47392de` |
| `src/homestay_bot/integrations/deepseek_client.py` | `99eba238101668e6d511135eaa36ccf549f35ad367dd78d86610d0dd9091d601` |
| `src/homestay_bot/services/knowledge_service.py` | `a4086c6e790be2f20b55f21694a97c7caab96b346e4a843eaf7b8f1a66d207f1`（沿用未改文件，验证见前轮） |

## 5. 接手边界

请围绕 IRI-F6 补齐 Spec §7 的覆盖承诺，再修复同一枚举闭环，不撤回已闭合的日期边界。检查最终回复的正文、证据及缺口，不能只断言“不再有未确认”。按最终影响重跑相关验证，真实模型三目标各三次及 all 门禁仍按 Spec 待授权执行。

未验证真实模型规划、实际 Hostex 数据、生产运行或客人收件。本报告不授权提交、推送、部署、真实外部调用或生产写入。
