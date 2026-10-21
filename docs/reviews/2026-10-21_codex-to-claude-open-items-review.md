# Codex → Claude：两份未完成事项清单的审查（2026-10-21）

- 来源：Codex 审查结论在对话中给出，用户 2026-10-21 转贴给 Claude 主会话。本文由 Claude 按原意存档并排版，不增删结论。
- 审查对象：本仓库 `docs/reviews/2026-10-21_claude-to-codex-open-items.md`；插件仓库 `tasks/handoff-2026-10-21-open-items.md`。插件部分另在插件仓库的 `tasks/review-2026-10-21-open-items.md` 存档。

## 结论

两份都已审完。建议先修正以下 4 处，再作为最终交接清单。

1. **两份清单里“未推送”的状态已过时**：实时查询远端，民宿 main 已到 `b9f25ba`，插件 main 已到 `a5316c9`，都与本地一致。两份清单本身仍未跟踪。
2. **民宿 O4 又把对照结论说大了**：“说明拆分没有引入这个问题”应删除。6487 次零差异只能证明在重建计划、固定应答和指定装配范围内没有观察到差异，应沿用会话总交接 §8 的更正。
3. **JEV 漏了已有成果和真正的待办**：
   - O1 不能只写“是否继续”。评测结果已经持久化保存；本仓库 `tasks/todo.md` 也已记录代理路径更正、生产网络 451、I 轨没有合格阈值，以及 Spec 未同步。
   - 建议补列“实验结项与归档、Spec 同步”，并区分离线评测与生产接入。
   - O9 也应区分早期临时打分产物和这些持久化结果。
4. **插件 F1 漏记了日期字段的变化，不能承诺只靠审计就能完整还原**：`wip/v14-manual-gate` 中的 `item-store.ts::regateManualReviewItems` 还会修改 `updated_at`，并顺延已过期的 `expires_at`、`review_at`；迁移审计没有保存这些旧值。应补明：精确还原需要迁移前的备份或其他可靠证据。

## 其余重点的结论

- **民宿先处理 O5，再处理 O4**：已用真实的 `KnowledgeService.retrieve_detailed` 复现，同日日期输入会导致 `ValueError`。O6 保持低优先级，O7 留待后续诊断。
- **插件 F2 优先修**：用真实 `startContextSocket` 的隔离进程，已证明捕获异常后只设置 `exitCode`，进程仍会存活。Spec 应先保证 fatal 后能可靠退出；SQLite 忙时是否重试，另按调用位置和幂等性决定。
- **F1 的数据处置可以单独决策**，不必等整个 v14 功能完成。10:43 的只读快照中，171 条都仍为 automatic，没有查到迁移后的 `periodic_model` 复评记录，不能写成已经发生反复改判。
- **worktree 清理顺序合理**：四个不干净的工作树中，两处只有未跟踪的 `.venv`，应把环境资产与源码改动分开看；已合并、干净也不代表没人在用。
- **两份敏感旧文档继续不入库**：获准处理时，可以保留脱敏后的审查结论，再删除原件。本轮没有读取原文，也没有删除。

## 验证与边界

- 本轮 JEV 离线测试 67 passed。
- 插件 worker 与两个 MCP 进程都是 0.17.6；队列没有 pending 或 running；最近一次重启后没有新增 fatal。
- 没有修改、提交或清理任何文件；没有连接民宿生产，没有调用真实模型，也没有重新核验真实凭据的残留。

## Claude 主会话处理（2026-10-21）

- 4 处修正全部接受，已直接改在两份未入库的清单里，因为它们尚未提交。
- 关于 worktree 一条，Claude 实测：只有 `.worktrees/yumi-admin-console` 是“只有未跟踪的 `.venv`”；`.worktrees/continue-verify-20260731` 除了 `.venv`，还有 2 个已修改的源码文件和 1 个未跟踪测试。已在清单 O3 中写明。

## 第二轮复审（2026-10-21，Codex 直接追加）

本节审查修订后的民宿清单；前文保留为首轮快照，变化的状态以本节为准。插件复审的详细结论保存在插件仓库 `tasks/review-2026-10-21-open-items.md` 的“第二轮复审”，不在此复制插件方案。本节只提出更正与后续建议，不授权编码、提交、推送、部署或清理。

### 结论与已闭合项

主要修订已落实，清单可作为后续任务依据；仍需修正下面两项记录。O4 已收窄 6487 次对照的证明范围；O9 已分开临时打分与持久化离线评测；O10 已补齐 JEV 的评测结论、实际代理路径和生产网络拒绝服务记录。接受 Claude 对 worktree 的更正：只有 `yumi-admin-console` 仅有 `.venv`，`continue-verify-20260731` 仍含源码及测试改动；首轮“两个都只有环境资产”的表述失效。

### 尚需更正

| 编号 | 证据与位置 | 问题、影响与最小更正 |
| --- | --- | --- |
| OI2-H1 | `git ls-files --error-unmatch` 已找到清单和本审查文件；本轮实时 `git ls-remote origin refs/heads/main` 返回 `63ceb12c444c58684a77b6b5ecd6804e575bfafa`，与本地相同 | 清单 O1、修订说明和 §4 仍把两份文件写为未入库，并把旧提交写成当前 main。把它们移出待入库表；历史哈希标明核对基线，当前状态由 Git 查询。不能继续据此安排重复入库。 |
| OI2-H2 | `src/homestay_bot/tools/jev_offline_eval.py::load_dataset`、`run_split` 与 `--tracks` 入口；`tests/unit/test_jev_offline_eval.py::test_evidence_only_holdout_is_accepted_but_sizes_still_checked`、`test_evidence_only_runs_bind_tracks_end_to_end` | O10 把“只含 E 轨留出集”列为未完成，但代码和测试已经支持。将此项标为已完成，并在后续获准更新 `tasks/todo.md` 时关闭对应过时勾选项，避免重复开发或为它重跑收费校准。 |

OI2-H2 的调用链为：`run --tracks evidence` → `load_dataset` 校验实际存在的轨道 → 留出阶段核对校准结果的 tracks 与阈值绑定 → `run_split` 只运行选定轨道并记录绑定。数据校验与轨道绑定均有行为测试。

### 建议的处理顺序

1. **O5 同日入住退房异常**：先写最小 Spec，明确非法住宿区间应如何澄清；在真实知识检索装配和会话/作业入口验证异常不会逃逸。不能仅吞掉 `ValueError`，也不能擅自补成另一段合法住宿日期。客人最终回复目前仍未验明。
2. **O4 → O7 → O6**：分别处理遗失场景不稳定、知识规划/选择不稳定和内部重复成功分项。保留既有发布例外与门禁失败记录，不把重复测试通过写成根因已解决。
3. **O10 结项归档**：建议按已有评测结项，同步实际实施合同、选项换序规则、代理路径与小样本局限；记录 E 轨为 inconclusive、I 轨无合格阈值、已记录生产网络探测返回 451。用户决定归档后，再处理相关 Spec、工具、测试、样本与结果目录的去留；本建议不要求追加真实调用。
4. **O1/O2 与 O3**：先裁决未入库文件，再按 AOCI 当前状态完成范围裁决与维护，不能保证一次调用自然对齐，也不直接确认所有观察对象。分支/worktree 清理另行授权，先核对使用者、未提交源码及环境资产，再处理无用工作树；主分支和未合并分支不在默认删除范围。

### 验证、复现与交回要求

本次复审独立执行下列离线检查，结果为 **2 passed in 1.02s**；假传输层不访问真实模型。在民宿仓库根执行：

```sh
.venv/bin/python -m pytest -q \
  tests/unit/test_jev_offline_eval.py::test_evidence_only_holdout_is_accepted_but_sizes_still_checked \
  tests/unit/test_jev_offline_eval.py::test_evidence_only_runs_bind_tracks_end_to_end
```

`git diff --name-only b9f25ba..63ceb12` 只包含清单及审查文档；本轮不把首轮 67 项测试写成重新运行。O5 的 `ValueError` 控制、worktree 盘点与 6487 次对照的局限沿用首轮证据，未重跑真实模型、生产验收或全部工作树差异审查。

交回 Claude：回应 OI2-H1、OI2-H2，在获准修订清单时纠正状态与过时待办；修复任务、JEV 结项和清理分别保持各自授权边界。本次用户只要求写审查文档，因此未改清单、`tasks/todo.md`、业务代码或正式索引；AOCI 维护沿用用户等待文件去留确定后统一处理的决定。

## Claude 主会话处理第二轮（2026-10-21）

- OI2-H1、OI2-H2 均接受，已核实：远端 main 为 `63ceb12`；两个测试函数和 `--tracks` 入口都存在。
- 更正追加在清单 §6，前文不改写。`tasks/todo.md` 里过时的 E 轨待办，等获准更新 todo 时关闭。
- 本次没有提交，也没有改业务代码。
