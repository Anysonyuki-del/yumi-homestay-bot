# 推送与本地分支、工作树清理｜Claude → Codex 交接

2026-11-19 · 基线 `main` = `origin/main` = `fb01660` · 本文未入库

## 结论

- **推送**：用户授权后推送 `main`，`7a3c1f6..fb01660`，含 `b1232e1` 与 `fb01660` 两个提交。没有部署。
- **工作树**：删除主仓库下 7 个，只保留主工作区和 Codex 自己的 `~/.codex/worktrees/e7a0`。
- **分支**：删除 28 个本地分支，本地只剩 `main`。
- **没动**：GitHub 远程分支和 13 个未跟踪文件都留着，等用户另行决定。

请 Codex 复核：
- 下面「已在 main」的判断是否成立；
- 有没有被误删、仍有价值的工作。

## 一、删除前的判断依据

### 两个未并入 main 的分支

| 分支 | 删前状态 | 判断依据 |
|---|---|---|
| `claude/gracious-borg-ce608c` | 比 main 多 1 个提交，落后 45 个；工作树里暂存 50 个文件 | **提交**：在 CI 里逐个列出 6 个 PostgreSQL 测试文件。main 的 `.github/workflows/ci.yml`「隔离 PostgreSQL 回归」一步改用通配符 `tests/integration/test_*_postgresql.py`，覆盖的 8 个文件包含这 6 个，所以这个提交已被取代。<br>**暂存内容**：1.71.0 小程序登录的早期版本，main 已经发布了 1.71.0（见 `docs/releases/1.71.0.md`）。逐个比对暂存文件：25 个与 main 相同，16 个已被 main 后续修改，另外 9 个是 `pages/index/*`、`pages/logs/*`、`utils/util.js`。**更正（Codex 复核）**：这 9 个文件主线曾采用，由 `d6ce9e2` 引入、`9afb646` 时仍在，后续改版 `2f6a370` 已删除；原写「main 从未采用」不对。 |
| `codex/guest-assistant-boundaries` | 比 main 多 1 个提交，落后 213 个 | 提交说明写的是「清理主工作区前的快照（内容已整合进 v1.42.0，仅本地保留，不推送）」。main 上的 `22dbd7c feat(reply): 住宿咨询助手回复边界重构 v1.42.0` 即对应的整合。 |

### 有未提交改动的工作树

| 工作树 | 未提交改动 | main 上的对应实现 |
|---|---|---|
| `.claude/worktrees/affectionate-hamilton-be1111` | `logging.py` 新增 `configure_application_logging`；`main.py` 改为调用它 | `src/homestay_bot/logging.py::configure_application_logging`、`_APPLICATION_HANDLER_NAME`（第 234–237 行） |
| `.claude/worktrees/zealous-clarke-fa51bc` | 请求头脱敏正则：防重复脱敏的负向断言，并一并遮住 `Bearer` 等方案词后的令牌 | `src/homestay_bot/logging.py` 第 17–20 行，正则一致 |
| `.worktrees/continue-verify-20260731` | `application.py` 里单个客户上下文维护失败时用保存点隔离；企业微信补拉失败时记录错误码；另有未跟踪测试 `tests/integration/test_context_maintenance.py` | main 的做法更彻底：每个客户用独立会话和事务，见 `src/homestay_bot/application.py` 第 4049–4067 行；失败日志「单客户上下文维护失败」。对应测试为 `tests/unit/test_application.py` 中「让第一个客户失败，验证第二个客户仍继续」那一例，断言 `maintained == [1, 2]`、`commits == [2]`。这份未跟踪的测试文件 main 里没有，但行为已有覆盖 |
| `.worktrees/yumi-admin-console` | 只有一个空的未跟踪 `.venv` | 无源码 |

另外两个工作树 `.worktrees/complaint-quality` 和 `.claude/worktrees/miniprogram-guest-guide` 是干净的，所在分支已并入 main。`gracious-borg` 的工作树见上一小节。

### 已并入 main 的 26 个分支

删前用 `git branch --merged main` 确认，全部是 main 的祖先。

## 二、执行与核对

- **工作树**：`git worktree remove --force` 删除 7 个，再 `git worktree prune`。
  - `--force` 是因为其中 4 个有未提交的源码改动，1 个只有空 `.venv`。删除依据见第一节。
  - **更正（Codex 复核）**：原写这些改动「不可从 Git 恢复」，过于绝对。已暂存的内容可能仍作为 Git 对象留存，但删除前没有记下对象号与路径的对应关系，找回不可靠；从未暂存的改动没有这项保证。
- **分支**：`git branch -D` 删除 28 个。删前记录了每个分支指向的完整提交号，见附录。
- **删后核对**：
  - `git worktree list` 只剩主工作区和 `~/.codex/worktrees/e7a0`（detached HEAD，干净，未动）；
  - `.claude/worktrees/` 与 `.worktrees/` 已为空；
  - `git branch` 只剩 `main`；
  - 主工作区未跟踪文件仍是原来的 13 个。
- 本轮没有改业务代码，没有跑测试。

## 三、请 Codex 复核的点

1. **gracious-borg 那 16 个「已被 main 后续修改」的文件**
   - 我只比对了文件哈希，没有逐一确认暂存版本里没有 main 遗漏的改动。
   - 如需核对，`f04891a` 的提交仍可读。
   - 依据是 1.71.0 已发布，且后续又有两轮登录修复（`7edf5c7` 等），我判断暂存版是更早的草稿。
   - **更正（Codex 复核）**：50 个暂存路径及其增改状态与 `git diff --name-status f04891a 9afb646` 逐项吻合；这些路径在 `9afb646` 的版本与 `fb01660` 对比，恰为 25 同、16 异、9 已删。这支持「暂存内容来自已整合进主线的版本」，但 16 个差异文件没有逐字节证明。暂存对象可能仍留存，找回不可靠，见第二节。
2. **`test_context_maintenance.py` 没有保留**
   - 它测的是「单客户维护失败回滚保存点、不影响其他客户」，针对的是旧的保存点写法。
   - main 已改用每客户独立事务，`tests/unit/test_application.py` 的对应用例覆盖了「一个失败、其余继续、失败者不提交」。
   - 那份文件是集成测试，用了真实 SQLite；main 的用例是单元替身。请判断这个差别是否值得补一个集成用例。
3. **GitHub 远程分支（19 个，不含 main）**
   - 18 个已并入 `origin/main`。
   - 只有 `origin/claude/gracious-borg-ce608c` 未并入，理由同第一节，内容已被取代。
   - 删远程分支是对外操作，用户尚未授权。

## 四、留给用户决定的事项

- **13 个未跟踪文件**：主要是 JEV 离线评测。去留按 `docs/reviews/2026-10-21_claude-to-codex-open-items.md` 的 O1 与 O10，等用户决定是结项入库还是丢弃。
- **远程分支清理**：见第三节第 3 条。

## 五、Codex 复核处理（2026-11-19，回复 [Codex 复核](2026-11-19_codex-to-claude-local-cleanup-review.md)）

Codex 接受本次清理，不需要恢复已删分支：26 个已合并分支的提交号都能从 `fb01660` 到达。

**两处更正已改在正文**：第一节 gracious-borg 一行、第二节、第三节第 1 条。Claude 本地复核了这两处：
- `git log --diff-filter=AD main` 显示，那 9 个文件由 `d6ce9e2` 引入、`2f6a370` 删除，`9afb646` 时仍在；
- `git diff --name-status f04891a 9afb646` 正好 50 行，与暂存清单数量一致。

**SQLite 集成回归（用户同意后已补，未提交）**
- **用例**：`tests/integration/test_context_maintenance.py::test_failed_customer_keeps_committed_write_and_rolls_back_the_rest`。
- **真实部分**：内存 SQLite、真实会话工厂和仓储，跑一轮 `application.py::_run_context_maintenance_loop`。
- **合成部分**：只把摘要服务换成合成步骤，仿照 `maintain_customer` 的事务形态：先写短摘要，提交后再写长摘要。
- **验收**：
  - 客户 1 写入 A，经 `before_external` 提交，再写入 B 并抛错；客户 2 写入 C 后正常结束。
  - 用新会话查询，结果为 `{1: ("A", ""), 2: ("C", "")}`，且本轮心跳照常记录。
- **验证**：
  - 新用例与 `test_application.py` 里两条维护循环单元测试共 3 passed；ruff、格式检查通过。mypy 按项目配置只查 `homestay_bot` 包，不覆盖测试。
  - **判别力**：对 `maintain_round` 做了三处变异，新用例都失败，源码已复原：
    - 提交边界改成只 flush，A 会丢失；
    - 去掉成功后的提交，C 会丢失；
    - 失败时也提交，B 会被保留。

**后续建议（未实施）**
- **企业微信补拉失败日志的格式**。
  - 旧补丁显式记录 `error_code=`；主线记录异常类型和经脱敏的异常栈，见 `application.py` 第 4545 行附近 `exc_info=error`，错误码在 `WeComApiError` 的异常文本里。
  - 只有运营侧依赖固定的 `error_code=` 格式时才需要改，目前没有发现这样的依赖。

**其他**：`.worktrees/` 目录里只剩一个 macOS 生成的 `.DS_Store`，没有残留的 checkout。

## 附录：删前分支提交号

```
chore/audit-cleanup-aoci-sync        cbc63979554c8abbeb3c4ed624241400dc0777ac
claude/affectionate-hamilton-be1111  80f9b2c309547c36c15cfd142d5dd30b98d87406
claude/codex-handoff-docs-e7043d     894fd5bcb630bb8f069ca2ab95eb3f7c58e53008
claude/gracious-borg-ce608c          f04891a5290112c53237874c8cde362d44ecd6c4
claude/zealous-clarke-fa51bc         80f9b2c309547c36c15cfd142d5dd30b98d87406
codex/admin-batch-workbench          8956d72157a7b05f50b61ccf5a2e23744eb38d55
codex/guest-assistant-boundaries     c0363fb9cfec9757abddab86bc1bb68671878c4a
design/admin-visual-system           14790719618ad147cf719e5547d93dacbb22989b
docs/1.73.2-deploy-record            6d2d635f1ba188900e169571417d9db43402a04a
feat/1.41.0-guest-data               96ba14285116300e051daff99176423d0af424f5
feat/hallmark-polish-aoci            6da66e266550d13829ea42fdbf504ae5efe601a5
feat/miniprogram-guest-guide         2f6a370bebf70d512867a11312e2dcbfd1fc869c
feat/miniprogram-texture             31969bb8a95c6d9ad60caf0d3b5cf6af98188342
feat/miniprogram-warm-ui             f554e607269e150b7a17c37e567d2ee33291e04e
feat/stage-timing-and-guest-data     5c0908dc9eebeb947960445f7e91ffdccaab0ad1
feat/wechat-miniprogram-login        17254e24cec235cdd0784b9c9004dad7b6ffc347
feature/c2-semantic-retrieval        5621e94edf2412279580efa43fdbb47bc3dfb189
feature/complaint-quality            98d8ad0a4f55dbb0f59078a0d0e40e46ccf59108
feature/yumi-admin-console           4b341713c29e614e7f0fb5c39dcfa44a4459314a
fix/admin-console-hardening-20260812 1d5a7901a42eb58855b763aae25bb21e5e31b239
fix/continue-verify-20260731         a971a0eefbbed9a41167eaa292179220adbc653f
fix/knowledge-evidence-attributes    1dd071e205bcbb256b5fc21c1adadfcd4d6ccd54
fix/logging-and-form-csrf-20260903   89c232bf98325974672ee5ac9699800c31880d94
release/1.37.0                       ae754ea74e78757041e6e66a521875e623979291
release/1.38.1                       faa2802da5c736689e38058a0f65df65d7ee76d6
release/1.73.2                       95610c59e4c6e3eaf0004e39365c820aa29050b2
release/guest-boundaries             22dbd7cc75817f8561cd6b025109167dee9ca2fa
test/holdout-v3                      9c69af9b0918c9158571367d90a07d93df747f2d
```

恢复某个分支：`git branch <名称> <提交号>`。提交对象在 `git gc` 清理不可达对象前都还在。
