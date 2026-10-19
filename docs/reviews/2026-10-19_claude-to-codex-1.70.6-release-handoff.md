# Claude → Codex：1.70.6 实施与发布报告（待审）

- 日期：2026-10-19
- 授权：用户 2026-10-19「开始，全部做完 所有都给你授权，做完之后写报告」（D-4、D-5、D-6、真实复测、门禁、提交推送、部署、验收）。
- 基线：main `55a8fd5`，生产 v1.70.5。正式提交 `60de4da` / `v1.70.6`，已推送并部署。
- 依据：Spec `docs/specs/2026-10-18_planner-complaint-misroute-spec.md`（§1 至 §9）；你的四轮复审（F176-R1 至 R3、F176-F1、H176-R1、R2、C8-1、C8-2，最后一轮无 P1、P2）。
- 发布记录：`docs/releases/1.70.6.md`。

## 1. 做了什么

| 项 | 文件与符号 | 事前经你审查 |
| --- | --- | --- |
| F1 门禁规划摘要 | `tools/reply_regression.py::plan_summary`、`_Runner.run`、`_Runner._respond`；`scripts/release/reply_gate.sh` 摘要 | 是（F176-R1 至 R3） |
| D-4 规划提示词 | `services/turn_plan.py::PLANNER_PROMPT_ZH`：急切报修的语气、感叹号和催促不算投诉 | 方向是；**最终措辞与 §5 原稿不同**，见 2.1 |
| **核验归一（新增）** | `services/turn_plan.py::verify_turn_plan`：事项类型恰好是 `complaint` 时归为 `unclear`，保留风险 | **否**，实施中新增，见 2.2 |
| D-6 危险优先 | `services/conversation_service.py::_plan_hazard`、`_possible_danger_text`、`_early_plan_hazard`、`_send_combined_safety_reply`；`_apply_plan_risk`、`_answer_possible_danger`、`_enter_complaint_mode(safety_prefix=)`、`process_debounced_message` 的软客诉与人工高风险确认分支；门禁 `_Runner._respond` 同序 | 是（§8，含 C8-1、C8-2） |
| D-5 复测 | 证据目录 `docs/reviews/2026-10-18-planner-complaint-evidence/` | 是（§7.4 的 100 次） |

## 2. 请重点审查

### 2.1 失效结论

第一版措辞（多一句「既报修又不满时只给不满那项填 complaint」）复测 92/100，7 次 `failed:schema`。当时 HEAD 对照 40 次 0 失败，我据此判定是新措辞造成的回归。**该结论已作废**：同一句子在 HEAD 上再跑 80 次，失败 7 次，「HEAD 0 次」是样本偶然。之后只用同一时段的 A/B（`ab_same_window_results.txt`）。拆分句已删除，最终措辞是一句中性的定义补充（Spec §9.1）。

### 2.2 核验归一（未经事前审查）

A/B 显示：新措辞把报修误判从 HEAD 的 11/30、8/30 降到 0/30，但模型更常把 `complaint` 写进事项**类型**。这会让整份计划结构失败，同一句里已识别的危险项随之丢失，退回保守客诉。合计结构失败为 18/120，HEAD 是 5/120。

- 我加了一个结构性补充：核验时只把类型等于 `complaint` 的这一种确定误写归为 `unclear`，并保留其风险。其他未知类型照旧判失败，整体结构校验没有放宽。
- 加上之后，同一时段为 1/60，HEAD 为 6/60。100 次复测 100/100。
- 请判断：
  - 这个归一是否会掩盖本应失败的计划？例如模型的意图其实是「这一项是投诉」，归为 `unclear` 后风险仍为 complaint，软客诉判断照样不放行。我认为这是安全方向，但这是我的判断。
  - 归一应该放在核验层，还是解析层？

### 2.3 残余

最终代码在 A/B 中，危险句「插座一直滋滋响还发烫！！！太离谱了」有 1/20 次结构失败，原因未能复现（之后 30 次 0 失败）。规划失败时仍按保守客诉处理，危险由词面判断兜底（这一句词面命中电气危险）。

## 3. 验证（各项独立）

- 离线新增 17 项。危险优先的会话用例换回 HEAD 时 6 项失败，另外 3 项是对照。
- 本地全量（除浏览器测试）3324 passed / 82 skipped；Ruff（整个仓库）与 Mypy 通过。
- CI：push 与 tag 两次都通过，3442 passed（含浏览器测试），隔离 PostgreSQL 回归 25 passed。
- 真实模型门禁 `rc-1.70.6-1` 第 1 次 all 通过：
  - 160 个场景，首轮通过 144，退步无；
  - F-堵了 首轮通过；
  - 新纳入不退步集合 T-吸烟，基线已随部署记录晋升；
  - 早餐冲突诊断场景未通过，未被晋升。
- 部署：三处 178 个文件一致，日志 0 错误，诊断页正常（降级原因不变，失败任务 2 个与此前相同）。
- 测试号：「卫生间下水堵了！！！」由规划放行（ok，1 项），走设施流程，建维修任务，发出转人工卡片，没有客诉复核。另有一条不带感叹号的对照，同样走设施流程。用户 2026-10-19 确认客户端已收到。

## 4. 没做的

- 否定式「我不是要投诉」命中硬客诉规则：另立事项（`tasks/todo.md`），本版不改。
- `process_recorded_message` 的人工高风险早退（Spec §8.2）、词面早期提醒不与后续确认组合（§8.5）：都维持原样。
- 本机浏览器测试没跑（Playwright 浏览器缓存缺失，未下载），以 CI 为准。
- 生产误判率没有统计；提示词修正是概率性的。
- 你此前的探针 `codex_f176_fix_review_probe.py`、`hazard_order_spec_probe.py` 断言的是修复前的缺陷，已由正向回归取代，原脚本未改。
