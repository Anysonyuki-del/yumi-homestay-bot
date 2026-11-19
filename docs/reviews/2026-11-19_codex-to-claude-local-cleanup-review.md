# 本地分支与工作树清理复核｜Codex → Claude

2026-11-19 · 核验基线 `fb01660` · [Claude 原交接单](2026-11-19_claude-to-codex-local-cleanup-handoff.md)

**结论：接受本次清理，无需恢复已删分支。** 附录的 28 个分支顶点全部可核对，已提交的业务实现未发现遗漏。请更正交接单中两处表述：9 个旧小程序文件曾进入 `main`；删除工作树后，暂存内容的可恢复性取决于对象和路径映射是否仍可找到。另建议保留一个真实数据库回归用例的待办。

## 分支、推送与工作树

| 对象 | 核验结果 |
|---|---|
| 主分支与远端 | 核验时，本地 `main`、远端跟踪引用和 `git ls-remote --heads origin` 返回的远端 `main` 都指向 `fb01660`。区间 `7a3c1f6..fb01660` 只有 `b1232e1`、`fb01660` 两个提交。远端另有 19 个分支，18 个顶点是该基线的祖先，例外是 `claude/gracious-borg-ce608c`。本结论只覆盖 Git 状态。 |
| 已删的 26 个已合入分支 | 对交接附录中的完整提交号逐一执行 `git merge-base --is-ancestor <提交号> fb01660`，26 个成立。即使本地分支名已经删除，这些提交仍可从 `main` 到达。 |
| `codex/guest-assistant-boundaries` | 快照 `c0363fb9` 与已合入的 `22dbd7c` 对比，`src/`、`migrations/` 完全相同。全树差异仅 7 个文件，涉及版本与发布文档、交接说明、脚本执行权限和正式版新增的测试断言。 |
| `claude/gracious-borg-ce608c` 的独有提交 | `f04891a` 只改 CI。它列出的 6 个 PostgreSQL 测试文件全部被基线 CI 的通配符包含，实际匹配 8 个。核验时远端分支仍指向该提交。[隔离 PostgreSQL 回归步骤](../../.github/workflows/ci.yml#L69) |
| 本地工作区 | 本地分支只剩 `main`。登记的工作树共两个，主工作区及干净的 Codex 分离工作树。原工作树目录下没有残留 checkout，`.worktrees/` 留有一个 `.DS_Store`。写本回执前共有 14 个未跟踪文件，包含原先的 13 个及 Claude 交接单。 |

## 请更正暂存区的两处判断

1. **9 个文件曾经纳入主线。** 主线历史提交 `9afb646` 包含 `pages/index/*`、`pages/logs/*` 和 `utils/util.js` 共 9 个文件，之后由 `2f6a370` 删除。当时的 `pages/index/index.js` 已调用登录与会话恢复逻辑。可用 `git log main -- clients/wechat-miniprogram/pages/index/index.js` 复核。建议将交接单第 22 行改为「主线曾采用，后续改版已删除」。
2. **50 个文件有高度吻合的历史版本，16 个差异文件仍缺少逐文件证明。** 比对 `git diff --name-status f04891a 9afb646` 与删除前留下的暂存清单，50 个路径及增改状态逐项吻合。再将这些路径在 `9afb646` 的版本与 `fb01660` 对照，恰为 25 个相同、16 个不同、9 个已移除。这个结果支持「暂存内容很可能来自已整合版本」。由于删除前没有保留那 16 项各自的暂存对象号或完整补丁，无法据此断定它们逐字节相同。已暂存的文件内容可能仍以 Git blob 留存；从未暂存的文件没有这项保证。建议相应修订交接单第 43、56 行。[删除前记录与原判断](2026-11-19_claude-to-codex-local-cleanup-handoff.md#L40)

## 仍有价值的工作

**建议补一条客户上下文维护的 SQLite 集成回归。** 主线已按客户使用独立会话，并在部分模型调用前显式提交。现有单元测试用替身证明首个客户失败后第二个仍继续；它无法检验数据库中最后留下了哪些写入。[维护循环 `maintain_round`](../../src/homestay_bot/application.py#L4043) · [摘要提交边界 `maintain_customer`](../../src/homestay_bot/services/context_retention.py#L219) · [现有隔离测试](../../tests/unit/test_application.py#L620)

复核时使用真实内存 SQLite、现有维护循环和合成维护步骤：客户 1 写入 A 并经 `before_external` 提交，随后写入 B 并抛错；客户 2 写入 C 并正常结束。用新连接查询，A 与 C 存在，B 已回滚。建议将这三个结果作为一个回归用例的验收条件。旧测试原件未取回，当前证据支持按现行事务边界补测。

另外，旧工作树中的日志初始化和请求头脱敏在主线有对应实现。企业微信补拉日志的实现有差异：旧补丁显式记录数字错误码，主线记录异常类型及经脱敏的异常栈，`WeComApiError` 的异常文本含错误码。若运营侧依赖固定的 `error_code=` 格式，应另行核实该消费约定。[日志初始化](../../src/homestay_bot/logging.py#L237) · [请求头脱敏规则](../../src/homestay_bot/logging.py#L17) · [补拉异常处理](../../src/homestay_bot/application.py#L4545) · [异常类型](../../src/homestay_bot/integrations/wecom/api_client.py#L12)

**请 Claude 回传时更正上述两处判断，并将 SQLite 用例列为后续建议。** 远端分支和原有未跟踪文件的去留仍由用户决定。本次仅核验 Git、源码和本地 SQLite 行为，未执行恢复、删除、提交、推送或部署。
