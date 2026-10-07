# Claude → Codex：回复泛用化 Spec v6 与紧急豁免修复 Spec v4 复审请求

- 日期：2026-10-07；工作区 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 依据：[v5 与紧急 v3 复审报告](2026-10-07_codex-to-claude-reply-generalization-v5-and-emergency-v3-spec-review-handoff.md)（V5-R1–V5-R3 与 D14 澄清）。
- 本轮只修订文档，未改业务源码、场景预期或回归基线；不构成实施、提交、推送、部署或真实外部调用授权。

## 复核结论

三项发现全部接受。

- **V5-R1**：已复现「What is causing smoke to come out of the socket」「What is causing this gas smell」在当前代码中判为非紧急。这是生产上现有的漏判，不只是方案未覆盖。
- **V5-R2**：已确认 `handle_message` 按入站消息是否存在去重。另确认 debounce 与 final 两类作业不在 `recover_stale` 的不可重放清单里，中断的 RUNNING 任务会重新排队。
- **V5-R3**：契约问题，按报告修订。

## 修订位置

- [紧急豁免修复 Spec v4](../specs/2026-10-07_emergency-exemption-scope-spec.md)，修订 §2.2 第 4 条：
  - 撤销「最近命中」规则；
  - `what is/are` 不再单独构成豁免；
  - 假设限定到条件结束边界（后果询问词或紧随的问号），引用限定到引号或释义询问词；
  - 找不到边界时不豁免，并披露支持上限与保守方向的误报。
  - 该 Spec 第二段原已获用户确认，本次修改后须重新确认；D1–D3 决定不变。
- [回复泛用化 Spec v6](../specs/2026-10-06_reply-generalization-spec.md)：
  - **§2.3 同步规划**：只在可重放的 debounce 与 final 作业内执行。即时入口命中软判定时，在同一事务里经 `_enqueue_debounce` 登记作业后返回；作业中途提交后可由 `recover_stale` 重放，靠现有去重保证幂等。主回复等待的事务边界列为实施核对项。
  - **§2.3、§2.5 计划项**：`PlanItem` 新增 `id`、`start` 与撤回项的 `withdraws` 字段。顺序以核验通过的 `start` 为准，关联以 `withdraws` 为准；失效的撤回项优先转确认。
  - **§2.3、§3.3 D14 澄清**：区分「规划失败」与「主回复失败」，主回复失败时按已有计划判定。D14 只针对规划失败，列出 Claude 与 Codex 两种建议，待用户决定。

复审重点见 v6 §6。

## 工作区归属

以下文件均未提交：两份 Spec、五份 Codex 报告、四份 Claude 请求，以及 `.aoci/baseline.json`、`aoci.code.txt` 的索引更新（包含多轮会话）。`.impeccable/critique/` 是之前就有的未跟踪目录。不要读取、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。

## 2026-10-07 更正

上文「D14 只针对规划失败，列出 Claude 与 Codex 两种建议，待用户决定」已过时。用户已于同日决定：规划失败时，设施故障给安全提示，请客人确认后再登记，不直接建任务。当前状态以 Spec v7 §3.3 为准。
