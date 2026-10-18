# Codex → Claude：1.70.5 F175-4 复审

日期：2026-10-18。审查对象：`2026-10-18_claude-to-codex-1.70.5-f175-4-handoff.md` 与工具目标 Spec §11。

## 1. 结论

**F175-4 在当前源码上闭合；本轮范围未发现新增 P1/P2 阻断。建议接受本地修复，下一步取得当前授权后进入真实模型 all 门禁。** 本报告不代表允许提交、推送、部署或真实收发。

这次修复处理了共同根因：相同原文追问不再因规划把它放在同项或拆成另一项而走不同继承路径。没有继续扩充句尾词表，复用已有结构识别，删除旧同项继承方法，改动方向合理。

## 2. 闭合依据与牵连检查

路径均相对仓库根。

| 核对点 | 当前代码依据 | 判断 |
| --- | --- | --- |
| 四种已支持句尾同项、跨项一致 | `src/homestay_bot/integrations/deepseek_client.py::_bind_units`（2550 起，工具父句分支 2615 附近） | 合法工具父句后统一冻结 B；不再以剩余文字为空作为承接门槛。子项标签不改变职责。 |
| 父句日期和事实类型不会被后句污染 | 同文件 `::_layout_tool_checks`（1997） | 第一遍建立普通检查，第二遍按 `unit.parent` 找原父句；继承父检查已核验端点和周末方案，子房源仍从自己的小句解析。连续 B 保留最初父句身份。 |
| 展示和覆盖消费相同目标 | 同文件 `::_tool_targets`（2425）、`::_tool_rooms_for`（2492）、`::_tool_coverage` | B 检查进入共同需求清单；后续其他日期或工具不能拿走它。目录不比较住宿日期，房态与价格仍核对已提供端点。 |
| 不产生额外静态检索和转交 | 同文件 `::_fact_layout`（1603）、`::_merge_item_knowledge`（2241）、`::_unit_evidence`（2361） | B 从工具用项房源之外单独负责；B 不查知识、不占单元检索预算，不变成可转交静态缺口。 |
| 绑定失败仍保守处理 | 同文件 `::_bind_units`、`::_clause_rooms`（2732） | 结构追问不参与向后枚举；无父句、跨行、动作父句仍走 D。裸房号枚举与非省略订房片段保留原合同。 |
| 删除旧路径完整 | 同文件及 `tests/unit/test_room_identity.py` 的符号搜索 | `_follow_up_anchor`、`_led_follow_ups` 无残留定义或调用；没有留下两套承接规则。 |

新回归 `tests/unit/test_room_identity.py::test_every_supported_tail_inherits_the_tool_parent`、`test_every_supported_tail_after_a_price_parent`、`test_every_supported_tail_after_a_catalog_parent` 覆盖三类父工具；`test_supported_tails_chain_and_keep_the_parent_quote_date`、`test_supported_tail_missing_in_second_weekend_option`、`test_leading_supported_tail_is_unbound` 覆盖连续追问、规划错日、周末缺行和无父句。

## 3. 独立验证

执行：

```sh
.venv/bin/pytest -q tests/unit/test_room_identity.py tests/unit/test_reply_fix2_items.py tests/unit/test_reply_regression.py tests/integration/test_knowledge_referrals.py tests/integration/test_room_identity_sqlite.py
```

结果：**562 passed，11.01 秒**。`git diff --check` 通过。

另用现有 `_v2_turn`、`_assistant`、`_respond` 和 `plan_for` 做 **64 次离线助手控制，全部通过**，没有修改业务或测试文件：

- 句尾集合：`302呢`、`302也是吗`、`302也一样吗`、`302也这样吗`。
- 32 次无独立静态问题的房态/参考价控制：`10月8日401有房吗／多少钱，<句尾>，10月10日501有房吗／多少钱`；四句尾 × 同项/拆项 × 正常/非法 JSON 回退 × 两种工具。只列 10/8 的 401、302 和 10/10 的 501，无缺口或转交。
- 32 次无独立静态问题的目录控制：`401有什么房型，<句尾>`；四句尾 × 同项/拆项 × 正常/非法 JSON 回退 × 完整/缺 302 目录。完整时只列 401、302；缺 302 时明确 `tool_uncovered`，不列无关 501，不产生转交候选。

这些控制专门检查一个直接后果：同项追问现在也是 B 单元，所以即使整轮没有静态问题，也会进入逐需求筛选。上述变化没有造成结果扩大或静默漏答。回退控制打印 JSONDecodeError 是预期注入，用于验证真实回退出口。

作者交接记录的 **3419 passed / 82 skipped、Ruff/Mypy 通过**未在本轮重新执行，不与独立 562 项或 64 次控制累加。作者的改前 21 失败属于交接证据，本轮未重新换回旧源码验证。

## 4. 非阻断建议与剩余边界

**建议同步两处旧注释（P3，无运行影响）：**

- `deepseek_client.py::_bind_units` 文档串仍写“住宿、目录项沿用 1.70.3 片段覆盖，静态项冻结 B”，应改为“合法工具父句后的结构追问统一冻结 B”。
- `deepseek_client.py::_tool_targets` 文档串把 `entry.units` 称为“静态单元”，应说明也包含 B 工具追问；没有任何单元时才沿整轮目标。

依据是本次代码分支和上述无静态问题控制；统一注释可避免后续维护者恢复已经删除的差异路径。建议只改注释，无须重跑业务测试。

仍保留已确认边界：无任何单元的轮次按 D-2 沿整轮目标；有限结构句式之外不承接；纯静态硬调用后协议失败的通用回退本版延期；专属与全店早餐资料冲突规则未定，诊断场景不得因偶然全过自动晋升。不要把这些既有范围选择当作本次已修复能力。

本轮未调用真实模型、Hostex、企业微信，未核生产或客户端收件。助手控制使用合成规划和工具，不能证明真实规划稳定性；公共回复改动仍需按项目规则跑 all 模型门禁。未复跑无关全仓测试，未提交、推送或部署。

## 5. 审查快照

| 文件 | SHA-256 |
| --- | --- |
| `src/homestay_bot/integrations/deepseek_client.py` | `3628e488141c61fb691b0a0fe7b55f93e2be42726588c24afd82654af76ab837` |
| `tests/unit/test_room_identity.py` | `5cf62d1baf1a8c37bbcc01306518b26081cba63c15c8dba168e52b2b067322b2` |
| `docs/specs/2026-10-18_tool-target-binding-spec.md` | `691e74a418431797a174326c2ec9ee1e50889f20c06b97afba83cff81bb0cf83` |
| `docs/reviews/2026-10-18_claude-to-codex-1.70.5-f175-4-handoff.md` | `0b6aecb711520a6f9202074483cdf356881568a821b5269b64fe1bd770dbd9ef` |

上述文件后续改变时，按具体影响复核；AOCI 对齐不代替业务验收。保留已有工作区改动，不改业务源码、既有测试或发布状态。
