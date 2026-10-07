# Claude → Codex：回复泛用化 Spec v3 与紧急豁免修复 Spec 复审请求

- 日期：2026-10-07；工作区 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 请求依据：用户看完 G1–G4 复审报告后要求「改出v3，准备提交给codex审核，都做」。
- 本轮交付：只修订文档，未改业务源码、场景预期或回归基线；不构成实施、提交、推送、部署或真实外部调用授权。

## 请审两份文档

1. [回复泛用化 Spec v3](../specs/2026-10-06_reply-generalization-spec.md)：回应 G1–G4，已把共同意图前移为 P1「轮次计划契约」，并写清工具开放、检索与证据选择、模型后校验、任务登记如何读同一份计划。复审重点见该文 §6。
2. [紧急分类「询问豁免」作用范围修复 Spec](../specs/2026-10-07_emergency-exemption-scope-spec.md)：G4 中「插座冒烟了还有燃气灶吗」漏判的根因，是 `EmergencyService._noncurrent_mention` 的第一项检查对整个分句生效，同句所有类别的命中都被放过（由 `22dbd7c`、v1.42.0 引入）。离线复核还发现「厨房着火了报警器在哪里」「燃气灶旁边着火了吗」「闻到燃气味燃气灶在哪」「报警器在哪里一直在响」同样被判为非紧急。建议改为按命中位置豁免，作为单独补丁先行。

## v3 相对 v2 的主要变化

- 新增 `TurnPlan`/`PlanItem`：子问题、意图类别、原话摘录（必须是本轮消息子串）、目标房间、日期、风险。仅本地填写到 `AssistantDecision.turn_plan`，主模型回传的 `intent` 不作授权。
- 工具开放取「旧规则 ∪ 计划」：计划只能增加开放，不能关闭旧规则要求强制调用的工具。
- 证据选择并入主调用（`answer_ids` 与 `related_ids`），不另加选择器调用；本地 `verify_selected_evidence` 核验编号与冲突，冲突范围为 answer ∪ related ∪ 同组合法候选。
- 新增 `resolve_service_request`，供 `_validate_decision` 与 `_record_task_suggestion` 共用；撤回和历史提及由计划否决词面误登记；计划缺失时沿用现行规则。
- 危险由计划 `risk` 补漏，只升不降；情绪词客诉由计划复核。
- 前置规划调用（V-a）与并入主调用（V-b）以实测选择（D10）。

## 证据绑定

文档引用的源码与夹具 SHA-256 与 G1–G4 报告记录一致（2026-10-07 复核），离线探针结果可用该报告内的脚本重跑。紧急豁免的复现在独立 Spec §1.2，只调用 `EmergencyService.classify`。

## 工作区归属

未提交内容包括：两份 Spec、两份 Codex 报告、本请求，以及 `.aoci/baseline.json`、`aoci.code.txt` 的索引更新（含多轮会话，不应整体归为某一轮业务改动）。`.impeccable/critique/` 为既有未跟踪目录。不要读取、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。
