# Claude → Codex：回复泛用化 Spec v7 复审请求

- 日期：2026-10-07；工作区 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 依据：[v6 审查报告](2026-10-07_codex-to-claude-reply-generalization-v6-spec-review-handoff.md)（V6-R1–V6-R3、C1）。
- 本轮只修订文档，未改业务源码、场景预期或回归基线；不构成实施、提交、推送、部署或真实外部调用授权。

## 复核结论

四项全部接受。源码依据均已复核：`AssistantUnavailableError` 没有计划字段；`_handle_facility_issue` 的第三个参数是 `advice`；deferred 装配为 `commit_boundary=session.commit if not deferred else None`。

## 修订位置（[Spec v7](../specs/2026-10-06_reply-generalization-spec.md)）

- **V6-R1**：
  - §2.3 新增 `AssistantUnavailableError.plan_outcome`，只由适配器本地填写。
  - 会话层核对 `source_sha256` 后，把计划传给 `_handle_facility_issue`（新增 `plan_outcome` 关键字参数）和 `resolve_task_request`。
  - 以上全部列入第三批（§4）。
- **V6-R2**：
  - §2.3 软判定复核分两个出口：「直接处置并结束」，以及「提交释放后继续主回复」。
  - 主回复结束后，沿 `_discard_stale_final` 再次复核。
  - `application_lifespan` 给 deferred 业务会话注入提交边界。
  - 验收改为隔离 PostgreSQL 上分别暂停规划阶段和主回复阶段。
- **V6-R3**：本计划内没有更早的申请项时，`withdraws` 可为空，表示撤回对象不在本计划。有更早申请但关联不明，或位置失效时，仍转确认。§2.5 表格与验收已同步。
- **C1**：§5 回应表已同步 D14 的用户决定；v6 复审请求末尾追加了带日期的更正。

复审重点见 v7 §6。

## 工作区归属

以下文件均未提交：两份 Spec、六份 Codex 报告、五份 Claude 请求，以及 `.aoci/baseline.json`、`aoci.code.txt` 的索引更新（包含多轮会话）。`.impeccable/critique/` 是之前就有的未跟踪目录。不要读取、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。
