# 语义检索 C2 调参与开启：交接（Claude → Codex）

> **2026-09-22 Codex 补修说明：**第 1～9 节保留 Claude 原交付记录。本轮已修评估缓存/运行条件和两项已知安全失败，最新状态见第 10 节。第二套留出集已揭示并转为回归；第 5 节原“第二套只跑一次”的后续安排不再适用。真实 C2 调参、延迟和开启仍未执行。

交接日期：2026-09-22。本文是当前的**接手入口**；此前的交接文档只作为历史记录：

- `2026-09-21_database-optimization-handoff.md`（§1–15：A 阶段、C1、1.37.0 与 1.38.0 的完整过程）
- `2026-09-21_codex-to-claude-rag-fix-handoff.md`（Codex 对 C1 的补修及其后续）

对应 Spec：`2026-09-21_database-optimization-spec.md`，重点是 §9.5.0（实施决策）、§9.5.1（实现要点与**事先登记的验收门槛**）。

本文严格区分「运行验证过」和「只写了代码、没跑过」，不要把后者当成验收。

---

## 1. 一句话现状

1.37.0（清理可靠性与知识证据门）和 1.38.0（可选语义检索，默认关闭）都已上线并核对完毕。**唯一还没做完的是 C2 的调参、验收与开启**：卡在需要用户提供硅基流动的 key。

## 2. 授权边界

- 本文不构成任何新的授权。按 `AGENTS.md`，接手后先核对状态、报告偏差，等用户明确回复「开始」再改代码。
- 需要用户**当前明确授权**才能做的：提交、推送、部署；读写生产；调用真实 DeepSeek、百居易、企业微信；用真实 key 调用硅基流动（评估会把**合成**知识与合成问题发给服务商）。
- key 只能由用户放进本机文件，**不要让用户把 key 贴进对话**，也不要在命令、日志、文档里打印它。
- 仓库在 GitHub 公开，不得写入密钥、本机绝对路径、服务器地址、客户数据。
- 不读取、不提交未跟踪的 `YuMi民宿AI项目总结.txt`。

## 3. 当前状态（2026-09-22 核对）

| 项 | 状态 |
| --- | --- |
| 本地仓库 | `main` = `origin/main` = `90ce112`，工作区干净；最新标签 `v1.38.0`（`5621e94`） |
| 生产 | 包版本 1.38.0，迁移 `0027_knowledge_embeddings`，`knowledge_embeddings` 为空表 |
| 生产配置 | 两个已存配置版本（含激活版本）都能被新代码读出，`embedding_enabled=False`（容器内只读解密核对） |
| 生产知识库 | 启用的知识条目 **0 条**，开启语义检索后也没有向量可补 |
| key 文件 | `~/.config/yumi/siliconflow.env` **尚未提供** |
| 语义检索常量 | `SEMANTIC_MIN_SIMILARITY = 0.5`（**未校准的初值**）、`SEMANTIC_TOP_K = 8`、查询超时 3 秒、RRF `k = 60` |

## 4. 已验证 / 未验证

| 项 | 状态 | 证据 |
| --- | --- | --- |
| A 阶段清理可靠性 | ✅ | SQLite 全量 + CI 隔离 PostgreSQL 16（18 通过、0 跳过，含锁序与并发用例） |
| 1.37.0 线上清理 | ✅ | 上线后第一轮清掉 17 条到期 job（661 → 644） |
| C1 证据门 | ✅ 本地 / ⚠️ 独立检验 | 校准集与第一套留出集全部达标；**第二套留出集**：错误放行 1、隔离 0.975，未达标（见 §6） |
| C2 代码 | ✅ | 新增 29 条测试，全量 1737 通过；7 处关键保护的变异验证都能被测试抓到 |
| C2 迁移 | ✅ | CI 在 SQLite 与 PostgreSQL 上都执行了 `0027`；生产已迁移 |
| C2 真实向量效果 | ❌ **未验证** | 没有 key；只用假向量客户端冒烟过评估流程 |
| C2 开启后的生产行为 | ❌ **未验证** | 一直处于关闭状态 |
| 登录后的后台页面 | ❌ 未验证 | 需要管理员登录，agent 不代输密码；「接口设置」的新分组只经过路由测试 |
| 真实 DeepSeek 回复质量 | ❌ 未验证 | 用户决定跳过 |

## 5. 下一步：C2 调参、验收与开启

### 5.1 前置条件

用户在本机执行（key 由用户自己填写）：

```bash
mkdir -p ~/.config/yumi && printf 'YUMI_EMBEDDING_API_KEY=%s\n' '<用户自己的 key>' > ~/.config/yumi/siliconflow.env && chmod 600 ~/.config/yumi/siliconflow.env
```

评估脚本只从环境变量 `YUMI_EMBEDDING_API_KEY` 或这个文件读取 key，不打印。向量结果缓存在系统临时目录的 `yumi-embedding-eval-cache.json`（只存摘要作键，不存正文），同一文本不会重复计费。每跑一套评估集，大约向量化 400 段文本。

### 5.2 调参：只用校准集

```bash
PYTHONPATH=src:tests .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report --semantic --only calibration --min-similarity 0.5
```

- 在校准集上比较几个 `--min-similarity` 取值（例如 0.40～0.70），看 Recall@3、错误放行、隔离三项；关键词模式的对照是 Recall@3 1.000、证据门准确率 0.925、错误放行 0。
- 选定后，把值写进 `src/homestay_bot/services/knowledge_embeddings.py::SEMANTIC_MIN_SIMILARITY`，并在 `ponytail:` 注释里注明依据。**选定后冻结代码**（记录源码哈希）。
- 调参期间**不得**对第二套留出集运行 `--semantic`、`--show-holdout`，也不查看 `knowledge_retrieval_baseline_v2.json` 的逐条内容。

### 5.3 验收：第二套留出集只跑一次

```bash
PYTHONPATH=src:tests .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report --semantic --only holdout_v2
```

对照 Spec §9.5.1 事先登记的门槛（C2 前基线见旧交接文档 §14.2）：

| 指标 | C2 前（1.37.0，关键词） | 门槛 |
| --- | --- | --- |
| Recall@3 | 0.900 | **必须提升** |
| 错误放行 | 1 | 不增加 |
| 隔离 | 0.975 | 不下降 |
| 检索 P95（含查询向量化） | 0.99 ms（纯关键词、不含外部调用；出自当时的报告输出，基线文件不存耗时） | ≤ 1.5 秒 |

跑完后可以用 `--show-holdout` 查看逐条失败，这时第二套留出集就不再独立。报告要**同时**写明：C2 的结果，以及 C1 在第二套留出集上那 1 条错误放行、1 条隔离失败的具体原因。之后若要据此修改规则，需要第三套独立留出集才能再说明泛化效果。

### 5.4 发布与开启

1. 如果改了 `SEMANTIC_MIN_SIMILARITY`，需要发一个补丁版本：升版本号、更新 `CHANGELOG.md`、推分支跑 CI（含隔离 PostgreSQL），合并 main、打标签、用 `run-deploy.command` 部署（脚本会先备份并复验）。**每一步都要用户当前授权。**
2. 验收达标后，由用户在后台「接口设置 → 语义检索（可选）」里填 key、勾选开启、输入管理员密码保存。保存前会做一次极小的向量连通测试，不通过不会激活。
3. 开启后，每小时一次的维护循环会为启用知识补齐向量。生产知识库目前为 0 条，**要等知识库录入内容后才有实际效果**。开启后可以用只读方式核对：`knowledge_embeddings` 行数，以及日志里是否出现「知识向量补齐」或「语义检索不可用」。

## 6. 仍然开放的问题

1. **C1 在第二套留出集上未达标**：错误放行 1 条、隔离失败 1 条。为了让它能作为 C2 的盲测集，具体用例还没看过。生产知识库为空，证据门不会放行任何本店事实，所以这条错误放行目前触发不了。
2. **C2 只改进召回**：相似度不作为事实证据（Spec §9.4 第 1 条）。说法不在主题别名表里的本店问题，仍会退回「尚未确认」。
3. 距离证据只有在能从问题里取出目的地时才核对；取不出时只认距离用语。中文距离问题会先走联网旅游搜索，不经过知识证据门。
4. 生产应用没有配置根日志器，WARNING 以下的日志都不输出。需要在线上可见的信号，必须用 WARNING 或以上级别。
5. 生产上没有 `pg_stat_statements`，SQL 成本与 P95 不可得。B 阶段（数据库调优）没有进入条件。

## 7. 改动时必须守住的约束

- **旧配置兼容**：`RuntimeConfigSnapshot.from_dict` 只允许缺少 `OPTIONAL_EMBEDDING_FIELDS` 里的 4 个字段；其他字段缺失或出现未知字段仍要拒绝。以后再给配置加字段，也必须让生产已存版本能读出。
- **开启才测试**：`RuntimeConfigTester` 只在 `embedding_enabled` 时做向量探针，否则 key 没填时任何配置都保存不了。
- **向量只用于召回**：`SemanticRanker` 只使用内容哈希与当前正文、当前模型都一致的向量；查询发出前必须经过 `redact_sensitive_guest_text`；任何失败都退回纯关键词，日志只记异常类型。
- **晚到保护**：`save_current_vectors` 落库前重新核对正文哈希，向量化期间被改的结果直接丢弃。
- **不要重跑** `--record-baseline`：第一版基线是在改动前的源码上记录的。`--record-baseline-v2` 也已经在 1.37.0 上记录过，C2 验收要和它比。

## 8. 复现命令

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
git diff --check
PYTHONPATH=src:tests .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report
```

最后一条只跑关键词模式，不联网，第二套留出集只输出汇总。

## 9. 相关文件

| 用途 | 位置 |
| --- | --- |
| 语义检索核心 | `src/homestay_bot/services/knowledge_embeddings.py` |
| 检索融合 | `src/homestay_bot/services/knowledge_service.py::KnowledgeService.with_semantic/_fuse` |
| 证据门 | `src/homestay_bot/integrations/deepseek_client.py::_scope_knowledge/_supporting_knowledge/_has_unsupported_property_claims` |
| 配置 | `src/homestay_bot/domain/runtime_config.py`、`services/runtime_config_service.py`、`services/runtime_config_tester.py`、`routes/runtime_config.py`、`templates/admin/settings.html` |
| 客户端接线与维护循环 | `src/homestay_bot/services/runtime_clients.py::build_runtime_client_bundle`、`src/homestay_bot/application.py::_run_knowledge_embedding_loop` |
| 评估 | `tests/unit/test_knowledge_retrieval_eval.py`；用例与基线在 `tests/fixtures/` |
| 第二套留出集 | `tests/fixtures/knowledge_retrieval_holdout_v2.json`，sha256 `6df864144c0b15d0819ab3975fff63fd8a45091a4304dbf617ec56d4ca382dcd`，Codex 编写 |
| 部署 | `run-deploy.command`（先 `--check`）；远端步骤见本地 `.stage/remote.sh` |

## 10. Codex 审查补修（2026-09-22）

用户明确“开始修复”，依据现有 Spec §9.5.1 的本轮补修条款。没有提交、推送、部署、访问生产或调用真实 embedding/聊天服务；原语义阈值和默认关闭状态保持不变。

### 已修复及根因

- `DeepSeekGuestAssistant._supporting_knowledge`：`h2-iso-01` 不是停用过滤失效，而是仍启用的洗衣区开放时间、洗衣液位置被当成了洗衣收费证据。费用问题现在要求对应主题的答案分句讲费用；多主题按问题分句限定对象，不能把停车收费要求套到早餐政策上。`respond` 回归检查安全兜底，且保留真实收费答案和原多主题正例。
- `answer_policy.is_transaction_sensitive`：`h2-boundary-01` 的英文 “How much is one room tonight?” 未识别为交易问题，导致 `_scope_knowledge` 未剔除历史价。补齐房间对象的英文问价规则，真实 `respond` 回归验证历史价不进入上下文。
- `OpenAICompatibleEmbeddingClient.embed`：响应索引必须恰好覆盖输入；维度一致、数值有限、非零；已选 bge-m3 校验 1024 维。重复/偏移索引、维度错误、NaN/Infinity、零向量整批拒绝，合法乱序恢复原顺序。调用方沿用失败回退/不落库逻辑。
- `test_knowledge_retrieval_eval.py`：质量调参可用缓存；新增 `--latency` 绕过查询缓存，知识向量仍可复用。改为生产 HTTPS transport、SDK 零重试和 3 秒查询截止；所有划分共用事件循环并在退出时关闭 SDK。
- 评估新增 `query_requests`（无缓存客户端调用尝试数，不等于服务商受理数）、`query_cache_hits`、缓存命中率、语义成功/无向量/无候选/超时/错误计数、超时率及无缓存成功 P95。总体 P95 保留失败回退耗时；不能仅用成功 P95 隐藏超时。mock 指标不是生产性能证据。

### 回归结果及剩余门禁

| 划分 | Recall@3 | 错误放行 | 隔离 | 证据门准确率 |
| --- | --- | --- | --- | --- |
| 校准集 | 1.000 | 0 | 1.000 | 0.925 |
| 第一套公开回归集 | 0.935 | 0 | 1.000 | 0.600 |
| 第二套公开回归集 | 0.900 | **1 → 0** | **0.975 → 1.000** | 0.550 |

第二套实时路由指标从 0 变为 1，多主题覆盖仍为 0.500，模拟回复判断准确率仍为 0.833。没有改原始用例、标签或基线；第二套纳入同一安全验收断言，不再作为独立泛化证据。有限词面规则仍不能证明任意语义、金额归属或条件蕴含。

红测：7 个坏向量批次、收费主题和英文房价各 1 项均按预期失败；评估缓存测试在旧实现因缺少计数失败。

最终验证：`RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q --tb=short` 得到 **1751 passed / 24 skipped / 12 warnings**。跳过是 15 项真实契约和 9 项本机 PG；警告包含 Starlette/Alembic 弃用与 SQLite 连接清理/线程警告，不能表述为零警告。改动 Python 文件 Ruff 通过，mypy 134 个源码文件通过。完整记录见 `tasks/todo.md`。

针对全量连接清理警告，额外将 `PytestUnhandledThreadExceptionWarning` 与 `sqlalchemy.exc.SAWarning` 视为错误运行 `test_knowledge_embeddings.py`、`test_deepseek_client.py`、`test_knowledge_retrieval_eval.py`、`test_answer_policy.py`：**149 passed，无警告**。本轮范围未复现资源清理异常，但这不代表整个项目的警告已修复。

接手顺序：先审查本轮差异；新增校准案例应与已公开回归分开，补充同义、多主题、错范围案例，冻结后由非调参者提供新独立留出集。效果比较可用“召回提升、不退化”，但开启门槛还要求已知安全项全部通过、新独立验收和真实延迟证据。

需要当前授权才能执行的真实评估命令（本轮未执行）：

```sh
# 质量调参：缓存查询，不可据此声称真实 P95 达标。
PYTHONPATH=src:tests .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report --semantic --only calibration --min-similarity 0.5
# 延迟验收：每次查询都走真实客户端；固定知识向量可以缓存。
PYTHONPATH=src:tests .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report --semantic --latency --only calibration
```

第二套公开回归可以重复执行，不要再称作盲测。没有新 key 也能完成本轮代码验证；真实向量、成本/延迟、新独立留出、后台登录及生产开启仍是未完成的外部门禁。生产知识数量、部署状态和先前 CI PostgreSQL 结果仍是原交接的历史记录，本轮没有重新核验。
