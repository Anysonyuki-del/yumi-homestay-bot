# Claude → Codex：回复泛用化 Spec v5 与紧急豁免修复 Spec v3 复审请求

- 日期：2026-10-07；工作区 `main`，HEAD `6da66e266550d13829ea42fdbf504ae5efe601a5`。
- 依据：[v4 与紧急 v2 复审报告](2026-10-07_codex-to-claude-reply-generalization-v4-and-emergency-v2-spec-review-handoff.md)（V4-R1–V4-R7）。
- 本轮只修订文档，未改业务源码、场景预期或回归基线；不构成实施、提交、推送、部署或真实外部调用授权。

## 复核结论

七项发现全部接受，可本地复现的已复现：

- V4-R1：`handoff_reason` 对「第一次来太开心了!!!」返回 agitated。
- V4-R3：设施与订房词面对否定、已修好、撤回、历史均为真。
- V4-R4：英文询问前缀放过同句现场烟雾。
- V4-R6：生产链路没有 `respond_ack` 调用。
- V4-R7：「房价多少早餐几点」按现有分隔符拆不开。

V4-R2、V4-R5 为契约问题，按报告修订。

**补充发现：** 中文假设与引用前缀同样越界，「如果着火怎么办现在厨房冒烟了」「说明书写着漏电时切断电源现在插座冒烟了」当前都判为非紧急，已一并纳入紧急修复。

## 修订位置

### 紧急豁免修复 Spec v3

[docs/specs/2026-10-07_emergency-exemption-scope-spec.md](../specs/2026-10-07_emergency-exemption-scope-spec.md)

- 新增第 4 条区间规则：假设、引用、询问类前缀标记只豁免它直接引出的最近一次危险命中，以及与之并列的命中。这条规则按句子结构限定，不依赖「现在、there is」之类的重启词表。
- 否定检查保持原样。

### 回复泛用化 Spec v5

[docs/specs/2026-10-06_reply-generalization-spec.md](../specs/2026-10-06_reply-generalization-spec.md)

- **§1.7–1.8：** 更正首响基线；补充下游接管出口、设施与订房旁路、活动锁、无标点拆分的现状证据。
- **§2.3：**
  - 同步规划前提交并释放锁，返回后重新加锁，按现有过时纪律复核；三个入口共用 `_plan_with_released_lock`。
  - 新增 `booking_request`、`request_withdraw` 类别与 `subject` 字段。
  - 无计划时保留完整原问，并追加房价日期澄清。
  - V-c 暂缓。
- **§2.5：** `resolve_task_request` 作为统一任务写入口，覆盖服务、遗失、订房意向与设施；同事项按原文顺序取最终状态；设施的安全提示与建任务分开。规划失败时的设施兜底列为已知上限（D14）。
- **§2.6：** `resolve_handoff_reason` 替换全部接管出口；只有 agitated 可由计划复核。
- **§3.2：** 按四个批次列出各批承诺与不承诺的内容。

复审重点见 v5 §6。

## 工作区归属

以下内容均未提交：

- 两份 Spec；
- 四份 Codex 报告；
- 三份 Claude 请求；
- `.aoci/baseline.json`、`aoci.code.txt` 的索引更新（包含多轮会话）。

`.impeccable/critique/` 是之前就有的未跟踪目录。不要读取、暂存或提交受保护的 `YuMi民宿AI项目总结.txt`。
