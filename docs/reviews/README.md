# 审查报告与复现证据

这里存放对本项目的外部审查报告，以及每份报告对应的可复现证据。

## 为什么入库

这些报告此前一直是未跟踪文件，只存在于某一台机器上。后果有两个：换会话接手时
无从查证某条结论的出处；而且它们曾被 `git add -A` 顺手扫进一次无关提交，导致
CI 在 `ruff check .` 上挂掉。纳入版本库后，出处稳定，门禁也稳定。

## 目录

| 文件 | 内容 |
| --- | --- |
| `2026-09-08_code-review-handoff-report.md` | 代码审查交接报告，九项发现 F-01～F-09，已在 v1.13.0 全部修复 |
| `2026-09-08_frontend-operations-optimization-report.md` | 前端运营精简报告，三阶段，已在 v1.14.0 落地 |
| `2026-09-08_deployed-usability-audit-report.md` | 已部署版本的可用性审计，UX 编号发现，**尚未逐项处理** |
| `2026-09-08-evidence/` | 上述报告的复现脚本与运行输出 |
| `2026-09-09_historical-data-audit.md` | 生产历史数据只读盘点 |
| `2026-09-09_room-status-clarity-review.md` | 房态与本地流程的设计审查和简化建议 |
| `2026-09-11_handoff-to-codex.md` | v1.26.0～v1.35.0 交接给 Codex |
| `2026-09-25_reply-rules-cross-review.md` | 回复规则审查与交叉评审 |
| `2026-09-26_codex-boundaries-rebase-handoff.md` | Codex 边界重构并入 main 的交接 |
| `2026-09-28_codex-to-claude-guest-assistant-handoff.md` | 民宿助手回复边界交接（Codex → Claude） |
| `2026-09-30_test-validation-rules-analysis-handoff.md` | 测试流程精简分析，规则已于 2026-09-30 改写 |
| `2026-10-01_frontend-spec-review-claude.md` | 前端审查 Spec 的复核意见与两轮更正（Claude → Codex） |
| `2026-10-01_codex-to-claude-frontend-review-handoff.md` | 对复核意见的第一轮核对，探针 E1–E9（Codex → Claude） |
| `2026-10-01_codex-to-claude-frontend-review-round2-handoff.md` | 第二轮核对 N1–N3，探针 E10–E11（Codex → Claude） |
| `2026-10-01_claude-to-codex-frontend-implementation-handoff.md` | 前端补齐实施交接与审查请求，含 M1–M4 更正（Claude → Codex） |
| `2026-10-01_codex-to-claude-frontend-implementation-review-handoff.md` | 实施审查 M1–M4，已在 v1.63.0 修复后发布（Codex → Claude） |
| `2026-10-01_claude-to-codex-approval-review-handoff.md` | 审批人工核验回填与遗留小项实施交接（Claude → Codex），含 AR1–AR4 更正 |
| `2026-10-01_codex-to-claude-approval-review-handoff.md` | 审批回填实施审查 AR1–AR4（Codex → Claude） |
| `2026-10-01_claude-to-codex-approval-review-round2-handoff.md` | AR1–AR4 修复后的第二轮审查请求，含 AR5–AR7 更正（Claude → Codex） |
| `2026-10-02_codex-to-claude-approval-review-round2-handoff.md` | 第二轮审查 AR5–AR7（Codex → Claude） |
| `2026-10-02_claude-to-codex-approval-review-round3-handoff.md` | AR5–AR7 修复后的第三轮审查请求，含 AR8–AR9 补测（Claude → Codex） |
| `2026-10-02_codex-to-claude-approval-review-round3-handoff.md` | 第三轮审查：无新 P1，AR8–AR9 测试判别力缺口（Codex → Claude） |

## 这些文件的性质

**报告是某一时刻的观察，不是当前事实。** 引用其中任何一条结论前，先回到代码、
测试、Git 历史或现场诊断确认它是否还成立——已修复的发现不会自己从报告里消失。

`2026-09-08-evidence/` 下的脚本不参与 CI：`mypy` 只检查 `packages = ["homestay_bot"]`，
`pytest` 按 `test_*.py` 收集因而收不到它们，但 `ruff check .` 覆盖整个仓库，
所以这些脚本仍需保持 lint 通过。
