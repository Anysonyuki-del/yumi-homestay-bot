# Claude → Codex：1.70.7 核验归一收窄（RV176-1）实施交接

- 日期：2026-10-19
- 用户决定：2026-10-19「交接给codex做」。
- 回应：`docs/reviews/2026-10-19_codex-to-claude-1.70.6-release-review.md` RV176-1（P2）。
- Spec：`docs/specs/2026-10-18_planner-complaint-misroute-spec.md` §10，已写好，未提交。

## 授权状态

**尚未授权。** §10.5 的 R-1 用户没有回复「开始」，只决定交给你做。

以下事项都需要你向用户取得当前明确授权，1.70.6 的「全部做完」不延续：

- 编码前确认 Spec §10；
- 真实模型门禁；
- 提交推送；
- 部署；
- 生产只读核对。

## 1. 现状

- 生产与 main：v1.70.6（`60de4da`），之后只有文档与 AOCI 提交，main 当前为 `8bc1113`。Alembic 无变化。
- 工作区未提交的内容：
  - Spec §10（我写的）；
  - 本交接；
  - 你的复审报告；
  - `.aoci/baseline.json`、`aoci.code.txt`（你复审时留下的 AOCI 资产变更）。
- 未跟踪、保持不提交：`.impeccable/critique/`。另外，不读取未跟踪的 `YuMi民宿AI项目总结.txt`。

### RV176-1 我已复核属实

- `services/turn_plan.py::verify_turn_plan`（约 199 至 204 行）无条件执行 `kind == "complaint"` → `unclear`。
- `_verify_risk` 把 None 当作 `none`。

所以只要模型写出 `kind=complaint` 且风险为 `none`、缺失或 null，计划就会从 `failed:schema` 变成有效的无投诉计划。

## 2. 要做的（Spec §10.2、§10.3）

1. `verify_turn_plan`：
   - 归一条件改为 `kind == "complaint" and entry.get("risk") == "complaint"`；
   - 其他组合保留原类型，因不在 `PLAN_KINDS` 照旧判 schema 失败；
   - 同步改那段中文注释，写明收窄的原因；
   - 不改 `_verify_risk`、提示词、危险流程或调用方。
2. `tests/unit/test_turn_plan.py`：
   - 新增三项参数化用例，风险分别为 `none`、缺失、null，都判 schema 失败；
   - 保留现有 `test_complaint_written_as_kind_is_kept_as_unclear_item`，它是成功控制，也覆盖未知类型失败。
3. `tests/unit/test_conversation_plan_flows.py`：
   - 用「下水又堵了！！！你们怎么搞的」加上述三种计划走真实会话编排；
   - 机器人模式：进入客诉，即 HUMAN_ACTIVE、有客诉通知、无最终回复任务、无普通维修任务；
   - 人工模式：保留高风险确认，不新增普通任务；
   - 正向控制：kind 和 risk 都为 complaint 的项。
   - 可参考该文件已有的固定计划替身写法。你复审时用的合成端口探针，也可以直接改成回归测试。
4. 以上新用例须在当前 1.70.6 的 `turn_plan.py` 上失败，作为判别力证据记录下来。

## 3. 验证与发布（Spec §10.4）

### 3.1 本地

- 跑相关单测；Ruff 按 CI 口径对**整个仓库**跑 `ruff check .`，再跑 Mypy。1.70.5 时 CI 只因证据脚本 Ruff 失败而挂过。
- 本机 Playwright 浏览器缓存缺失，浏览器测试起不来；本次不改页面，以 CI 为准。

### 3.2 版本与文档

- 版本号改为 1.70.7（`pyproject.toml`）。
- 写 CHANGELOG 条目，新建 `docs/releases/1.70.7.md`，按项目发布记录五项要求写。

### 3.3 门禁

- 范围用 all：`turn_plan.py` 在 `REPLY_PATHS` 里，规划核验影响全部场景。
- 先建一个候选提交并打 rc 标签。
- `REPLY_GATE_SCOPE=all bash scripts/release/reply_gate.sh <rc标签>`，需要 `DEPLOY_HOST`、`DEPLOY_KEY` 环境变量（取自 `run-deploy.command` 的配置）。不要在输出或文档里打印主机和密钥。
- 摘要文件里，每个退步场景都附有失败记录的规划（1.70.6 F1 加的）。
- 门禁会给出建议基线。晋升哪些场景要人工审阅：K-房号追问-早餐冲突 保持诊断身份，不晋升。

### 3.4 提交推送

- 正式提交与标签 v1.70.7，推送后查 CI（push 和 tag 两次运行）。

### 3.5 部署

- 主目录有未提交的 AOCI 资产，而部署脚本要求在 main 分支上，所以：
  1. 用 `git worktree add --force <临时目录> main` 建独立工作树；
  2. 执行 `DEPLOY_REPO=<该目录> bash run-deploy.command --skip-reply-gate "<复用 rc 门禁的理由>"`；
  3. 跳过理由的前提，是正式提交与 rc 在 src、tests、scripts、migrations、pyproject、requirements.lock 下零差异。

### 3.6 现场只读核对

- 服务器 HEAD；
- 本地 tag、服务器源码、运行容器三处应用文件摘要比对，排序用 `LC_ALL=C`，1.70.6 时是 178 个；
- 部署后日志中 ERROR、Traceback、Exception 计数；
- 后台诊断页：用户登录，只读；
- 不做测试号收发：误写无法稳定触发。理由写进发布记录。

### 3.7 收尾

- 用 `aoci_maintain` → `aoci_update_entry` → `aoci verify/check/index agent guide` 维护 AOCI，然后单独提交。
- 不要和其他提交并行进行。

## 4. 不在本次范围

- 否定式投诉（`tasks/todo.md` 另立事项）；
- 危险句 1/20 的残余结构失败；
- `process_recorded_message` 早退；
- 词面早期提醒。

都按你复审 §5 的顺序另行处理。
