# Claude → Codex：未完成事项清单（2026-10-21）

- 来源：用户 2026-10-21：“那这两份文档直接提交，密钥什么的先不管，其他没做的写清楚准备给codex审核”。
- 范围：本对话中没做完或新发现、仍需决定的民宿仓库事项。记忆插件事项另写在插件仓库 `tasks/handoff-2026-10-21-open-items.md`，不写进本仓库。
- 已完成事项的总表见 `docs/reviews/2026-10-21_claude-to-codex-status-update.md`（`b9f25ba` 入库）§6、§8。
- 本文只列事项和证据，不授权执行任何一项。不含凭据值、条目 ID 或正文模板。
- 修订：已按 Codex 审查（`docs/reviews/2026-10-21_codex-to-claude-open-items-review.md`）修正推送状态、O4 的对照结论、JEV 相关事项和 O9，并调整优先级。本文未入库，所以直接改原文。

## 0. 用户暂缓，本次不处理

| 事项 | 用户决定 |
| --- | --- |
| 作废 TypeSafe 密钥；记忆库里该密钥的 2 份副本 | 2026-10-21：“密钥什么的先不管” |
| 生产管理员口令 | 2026-10-21：“口令不用管，项目现在还没正式运行，后面会换的” |
| 公开文档里凭据条目的 8 位 ID 前缀 | 2026-10-21：“ID 前缀先不动” |

## 1. 需要用户决定去留

### O1：未入库文件（14 个文件和 1 个目录）

| 文件 | 来源 | Claude 的看法 |
| --- | --- | --- |
| `docs/reviews/2026-10-20_claude-to-codex-respond-with-plan-split-spec-handoff.md`、`…-review-reply.md`、`2026-10-20_codex-to-claude-respond-with-plan-split-spec-review.md` | 拆分 Spec 的审查链 | 建议入库。Spec 已入库，审查链是它的依据 |
| `docs/reviews/2026-10-21_codex-to-claude-status-update-review.md` | Codex 对现状汇总的复审 | 建议入库，与已入库的汇总成对 |
| `docs/reviews/2026-10-21_claude-to-codex-open-items.md` | 本文 | 审查后决定 |
| `docs/reviews/2026-10-21_codex-to-claude-open-items-review.md` | Codex 对本文的审查存档 | 与本文一起入库 |
| `docs/specs/2026-10-20_jev-offline-evaluation-spec.md`、`docs/specs/2026-10-20_jev-holdout-evidence-brief.md`、`docs/reviews/2026-10-20_claude-to-codex-jev-offline-evaluation-spec-review.md` | JEV 离线评测的 Spec 与审查（不是本对话写的） | 随 O10 一起决定：结项归档时与 Spec 同步后的版本一并入库 |
| `src/homestay_bot/tools/jev_offline_eval.py`、`tests/unit/test_jev_offline_eval.py`、`tests/fixtures/jev_offline_{calibration,holdout,probe}.json` | 离线评测工具、测试与样本集，约 3000 行；Codex 本轮离线测试 67 passed | 随 O10 一起决定。它是离线评测工具，不接入生产回复链路 |
| `.impeccable/critique/2026-10-02T02-08-13Z__src-homestay-bot-templates.md` | 10-02 的界面审查产物 | 建议不入库，或者由用户决定是否保留 |

### O2：AOCI 认知维护

- 用户先前决定：等 O1 的去留定了再统一维护。
- 上次 `aoci_maintain` 返回 blocked，需要 `scope acknowledge`，因为批次里包含未跟踪文件。
- 此后又有 `98782b4`、`b9f25ba` 等受管理对象变化。O1 定了之后，跑一次维护即可。

### O3：分支与 worktree 清理（2026-10-21 实测）

- 分支：共 21 个（含 main）。19 个已并入 main；未并入的是 `codex/guest-assistant-boundaries`。
- worktree：主工作区之外还有 6 个：

| worktree | 分支 | 未提交改动 |
| --- | --- | --- |
| Codex 工作树 `e7a0` | detached | 0 |
| `.claude/worktrees/affectionate-hamilton-be1111` | `claude/affectionate-hamilton-be1111` | 3 |
| `.claude/worktrees/zealous-clarke-fa51bc` | `claude/zealous-clarke-fa51bc` | 2 |
| `.worktrees/complaint-quality` | `feature/complaint-quality` | 0 |
| `.worktrees/continue-verify-20260731` | `fix/continue-verify-20260731` | 4 |
| `.worktrees/yumi-admin-console` | `feature/yumi-admin-console` | 1 |

- 4 个不干净的 worktree 要区分环境资产与源码改动：
  - `yumi-admin-console` 只有未跟踪的 `.venv`；
  - `continue-verify-20260731` 有 `.venv`，另有 2 个已修改的源码文件（`application.py` 及其测试）和 1 个未跟踪测试；
  - 两个 `.claude/worktrees` 都是日志脱敏相关的源码改动（`logging.py` 等）。
- 建议：先逐个确认这些源码改动还有没有价值，再删已合并的分支和干净的 worktree。已合并、干净也不代表没人在用。删除都需要授权。
- **更正**：现状汇总 §6.2 N16 写的是“3 个本地 rc 标签”。实测本地共有 31 个 `rc-*` 标签，远端 0 个；本对话产生的是其中 3 个（1.70.8、1.70.9、1.70.10）。这仍属于只在本地保留的惯例，无需处理。

## 2. 需要立项的缺陷与不稳定

### O4：SR-遗失场景回复不稳定（原 N7）

- 1.70.10 的 all 门禁因此不通过，用户接受为发布例外，见 `docs/releases/1.70.10.md`。
- 当时已生成任务卡“定位并修复 SR-遗失场景的回复不稳定”，未启动；卡片可能已过期。
- 证据：重建出的 175 个计划变体对照 6487 次零差异，只说明在这些重建计划、固定应答脚本与装配范围内没有观察到差异，不能证明模型输出相同时行为完全一致（会话总交接 §8，SH-R2）。该场景在历史门禁中本来就不稳定。
- 优先级：排在 O5 之后。

### O5：U4 同日入住退房的异常逃逸（原 N8，P2）

- 触发：客人在一句话里写出两个相同的完整日期并带“退房”，例如“2026年10月10日入住2026年10月10日退房”。
- 路径：`_resolve_turn_dates` 得到空区间 → `_load_turn_knowledge` → `KnowledgeService.retrieve_detailed` 抛出 `ValueError`。`ConversationService._process_model_reply_body` 只捕获 `TourismSearchError` 和 `AssistantUnavailableError`，所以异常会进入作业层的失败处理。
- 未核实：客人最终收到什么（补偿话术、转人工，还是没有回复）。
- Codex 已用真实的 `KnowledgeService.retrieve_detailed` 复现出 `ValueError`。
- 优先级：**先于 O4 处理**，因为客人的文字就能直接触发。需要先写 Spec。

### O6：U5 同一查询两次成功产生重复分项（原 N9）

- 正文已去重，客人看不到；只影响内部分项结构。低优先级，未立项。

### O7：K-距离-D、K-公区-D 门禁不稳定（原 N10）

- 现象：规划把它们误标为店外信息，或者模型没有选中审核编号。
- 1.70.10 门禁没有采纳晋升 K-距离-D 的建议，基线不变。未立项。

## 3. 记录与经验

### O8：外泄教训的归属（原 N14）

- Codex 意见：脱敏与维护窗口的教训归入插件仓库的审查记录，不写本仓库的 `tasks/lessons.md`。
- 一条可以考虑晋升为跨项目规则：发往外部模型之前，必须使用与插件同等覆盖面（含中文写法）的脱敏，并先做本地残留扫描。是否写入全局记忆，需要用户明确要求。

### O9：会话临时产物（原 N12、N13）

- 本对话早期的 Jev **打分**脚本与结果（给仓库文本、记忆、代码打分），以及拆分对照脚本，都只在 Claude 会话的临时目录里，会被清理；要复用，需要另立工具并写 Spec。这和 O10 的 JEV **离线评测**结果是两回事。
- “根据打分审查现状”的完整审查只在对话里给出，要点已收进会话总交接 §2.2。

### O10：JEV 离线评测的结项、归档与 Spec 同步

- 已有成果：
  - 评测结果持久保存在仓库外的同级目录 `homestay-bot-jev-evidence/`，共 10 个运行目录：校准 1–4b、留出 1、探测 1–3。
  - 过程与结论记在本仓库 `tasks/todo.md` 的 JEV 一节。
- 已有结论（`tasks/todo.md`）：
  - E 轨质量门槛全部达标，但留出结论是 inconclusive，唯一原因是耗时不是在生产网络下测的；
  - 已更正：所谓“本机直连”实际走的是新加坡代理；
  - 生产网络探测 8 个请求全部返回 451，生产不可用，不建议用代理绕开地区限制；
  - I 轨没有合格阈值。
  - 建议就此结束 JEV 线。
- 未完成（`tasks/todo.md` 中未勾选的项）：
  - Spec 需要同步：“3 次只报告稳定性”已改为“第 2、3 次换序测顺序敏感性”；Spec 还没出 v2，初版实现以审查 §11 为准；
  - 局限（留出只有 36 条、编写方与主会话是同一模型等）要写进结项结论；
  - 工具支持“只含 E 轨的留出集”：若结项，就不必再做；
  - 评测用过的 TypeSafe 密钥作废：按用户决定暂缓。
- 要区分：
  - **离线评测**：已完成、有结论；
  - **生产接入**：因 451 不可行，也没有立项。
- 建议：用户决定结项后，同步 Spec、写结项结论，并把 O1 中的 Spec、工具、测试和样本集与结论一并入库（或明确只保留在本地），评测结果目录按原样归档。

## 4. 现状说明

- 生产：本对话最后一次连接生产是 1.70.10 部署核对。之后没有重新核验，“运行 v1.70.10”是交接记录。
- 本仓库 main 与远端同步在 `b9f25ba`（已推送）；本清单与 Codex 审查存档仍未跟踪。
- 关联会话“清理记忆库中的快照候选与口令条目”和“记忆插件凭据脱敏修复与存量清理（方案 B）”都已空闲，任务已完成，可以归档（需用户同意）。

## 5. 请 Codex 审查的重点

1. O1 每个文件的处理建议是否合理，尤其是 Jev 离线评估这组（代码加测试，不是本对话的产物）。
2. O3 的清理顺序与判断标准。
3. 建议的处理顺序：O5 → O4 → O7 → O6；O10 结项由用户决定。
4. 有没有遗漏的事项。

## 6. 2026-10-21 第二轮复审后更正（回应 `docs/reviews/2026-10-21_codex-to-claude-open-items-review.md` 第二轮）

本节为追加更正，前文保留不改写；状态以本节为准。

- **OI2-H1，入库状态**：本清单和 Codex 审查存档已在 `63ceb12` 入库并推送。前文修订说明、O1 表和 §4 里“未跟踪”“main 与远端同步在 `b9f25ba`”的写法已过时，`b9f25ba` 只是当时的核对基线。当前 main 以 Git 查询为准。O1 表中这两行不再待入库，剩余待决定的是另外 12 个文件和 `.impeccable/critique/` 目录。
- **OI2-H2，只含 E 轨的留出集**：工具已经支持。`src/homestay_bot/tools/jev_offline_eval.py` 的 `run --tracks` 入口、`load_dataset` 和 `run_split` 都已实现，测试 `test_evidence_only_holdout_is_accepted_but_sizes_still_checked`、`test_evidence_only_runs_bind_tracks_end_to_end` 覆盖了这部分行为（Codex 本轮 2 passed）。O10 中这项改为**已完成**；`tasks/todo.md` 里对应的未勾选项已过时，等获准更新 todo 时关闭。不需要为它重跑收费校准。
- **处理顺序**沿用第二轮复审：O5 → O4 → O7 → O6；O10 结项归档；O1、O2 之后再做 O3。
