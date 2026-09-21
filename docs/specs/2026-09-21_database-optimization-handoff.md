# 数据库清理可靠性与 RAG 检索：实施交接

交接日期：2026-09-21。实施方：Claude。接手方：Codex。

对应 Spec：`2026-09-21_database-optimization-spec.md`（R4）。源码基线 `4e348ce`，版本 `1.36.3`。
全部改动在工作区，**未提交、未推送、未部署**。

本文严格区分「运行验证过」和「只写了代码、没跑过」。不要把后者当成验收。

**Codex 补修说明：**第 1～11 节保留 Claude 的历史交付记录；本轮最新修复、验证结果和剩余事项以第 12 节为准。第 9 节第 1 项及第 10 节第 2 项的补词建议已被共享证据来源修复替代；不能仅因错误放行归零而移除隔离测试的 xfail。

---

## 1. 一句话现状

A 阶段（清理可靠性，含 D1/D2）和 C0/C1（检索评估与最小修复）都已实现并通过本地 SQLite 验证。
**PostgreSQL 用例一条都没执行过**；留出集的召回达标，但安全项没有全部达标，以 strict xfail 记录。

## 2. 用户决策与授权边界

- PG 验收走路径（c）：本机没有 Docker 或 Postgres，不安装数据库，不为跑 CI 推分支，交付时标「PostgreSQL 未验收」。
- 英文：「英文先不管」。含义是只修证据门对英文知识的判断，**兜底文案仍然只有中文**，没改。
- 留出集：用户选「a」，由 Codex 独立编写（见第 7 节）。
- 用户要求一次跑完 A 和 C，中间不停。
- **未授权**：提交、推送、部署、生产读写、调用真实 DeepSeek/百居易/企业微信、安装数据库、执行 C2。
- 仓库在 GitHub 公开。密钥、本机绝对路径、客户数据不得写进任何文件。

## 3. 验证状态

### 3.1 最终验证集（差异冻结后跑一次）

| 项 | 结果 |
| --- | --- |
| 全量 `pytest` | 1685 通过，24 跳过，1 xfail，7 警告 |
| 跳过构成 | 15 项真实外部服务契约（按设计，需要授权）+ 9 项 PG 用例（没有测试库） |
| xfail | `test_retrieval_meets_preregistered_targets[holdout]`，见第 7.3 节 |
| 警告 | Starlette 与 Alembic 的弃用警告、SQLAlchemy 未归还连接警告、aiosqlite 线程异常警告。线程警告挂在哪个测试名下取决于垃圾回收时机，改动前的基线运行也有。本次新增和改动的 9 个测试文件单独以 `-W error`（线程异常与 SAWarning）运行：194 通过、9 跳过、1 xfail，不产生这类警告 |
| 变异验证 | A 阶段 12 处、C 阶段 10 处关键保护逐一删除，都有测试失败；其中 4 处第一次没抓到（批量删除的会话刷新、通用清理修改语句的资格条件、分类器复用别名、分类不作证），补强测试后才抓到 |
| `ruff check .` / `mypy` / `git diff --check` | 通过 |

### 3.2 A 阶段验收矩阵（Spec 第 6 节）

| 编号 | 状态 | 证据 |
| --- | --- | --- |
| T1 超长键 | ⚠️ 部分 | SQLite：40 个五位数编号只登记一条 job，键不超过 128；旧格式 254 字符由单测断言。**PG 上 22001 报错与新键实际写入均未执行** |
| T2 键契约 | ✅ | `test_task_page_service.py` 覆盖顺序、重复、来源隔离，并断言两个入口实际用的是这个键 |
| T3 多批 | ✅ | 归档 2/2/1、通用 2/2/1/0，每批各自登记一条清理 job |
| T4 临界值 | ✅ | `<` 类在恰好 cutoff 时保留、早一秒删除；运行中的 job、待处理 webhook、未归档任务都不删 |
| T5 回滚 | ✅ | 真实私有目录加生产清理 handler：回滚或登记失败后照片仍在，没有 job，也没有墓碑 |
| T6 批次独立 | ✅ | 单测，另有 PG 用例未执行 |
| T7 阶段隔离 | ✅ | 单测，两个阶段分别失败；日志不含 SQL 与参数 |
| T8 上限、取消、固定 now | ✅ | 单测，含批次跨越午夜 |
| T9 清理对恢复的锁 | ❌ **未执行** | `test_retention_postgresql.py` 中 2 条 |
| T10 旧格式 job 与重放 | ✅ | 旧键 job 照常处理，重复执行不报错 |
| T11 无权限批量删除 | ✅ | 普通员工被拒，仓储和队列都没被调用 |
| T12 批量删除对恢复的锁 | ⚠️ 部分 | SQLite 证明了会话里预加载的旧状态会被刷新；**3 条 PG 锁序用例未执行** |
| T13 整批原子 | ✅ | 混入不合格任务、删除集合不完整、重复编号 |
| T14 三入口墓碑 | ✅ | 单条、批量、自动三个入口；无附件的系统任务也写，人工任务不写 |
| T15 周转防重建 | ✅ | 三个入口删除后，用新会话调 `create_turnover`，返回 None |
| T16 墓碑时间 | ✅ | 取本轮删除时间，只前移不后退，离过期还差 1 分钟时仍能挡住重建 |
| T17 并发 upsert 与过期竞争 | ❌ **未执行** | PG 用例 3 条（其中 1 条同时覆盖 T6） |
| T18 凭证任务不受墓碑影响 | ✅ | 同键墓碑不挡凭证失败或凭证复核任务 |
| A4 线上基线 | ❌ 未采集 | 没有生产只读授权 |

### 3.3 C 阶段验收矩阵（Spec 第 9.6 节）

| 编号 | 状态 | 说明 |
| --- | --- | --- |
| K1 同义问法 | ✅ 校准集 / ⚠️ 留出集 | 留出集有成组漏判，见第 7.3 节 |
| K2 中英文 | ⚠️ 部分 | 英文问题能用英文知识作证；兜底文案仍只有中文（用户决定） |
| K3 长答案 | ✅ | 两个集合的证据完整都是 1.000；超预算时整条跳过并计数 |
| K4 增改停用与候选 | ✅ | `test_knowledge_repository.py`，每一步都用新会话检索 |
| K5 无答案与仅分类命中 | ✅ | 包括「分类写早餐、正文没讲早餐」的情况 |
| K6 多主题、周边商户、收费 | ⚠️ 部分 | 校准集全部通过；留出集 2 条周边措辞放行（真缺陷） |
| K7 实时、交易、注入 | ✅ | 知识不放行实时问题；「空房」识别缺口属于范围外，见第 8 节 |
| K8 预算与来源上限 | ✅ | 来源不超过 8 条，总字符不超过 12,000 |
| K9 FAQ 草稿共用检索 | ✅ | 相关测试通过；接口 `retrieve` 没变 |
| K10–K12（C2） | 未启动 | |
| 真实模型回答质量 | 未验证 | 只用了模型替身 |

## 4. 改动文件与符号

业务代码 7 个文件，都在 `src/homestay_bot/` 下：

| 文件 | 符号 | 阶段 |
| --- | --- | --- |
| `services/task_page_service.py` | `build_attachment_cleanup_dedupe_key`、`TaskPageService.purge_many` | A1 |
| `repositories/retention.py` | `purge`、`purge_archived_tasks`、`_candidate_ids`、`_execute_bounded`、`_require_positive_batch` | A2 |
| `application.py` | `_run_retention_loop`、`_run_retention_round`、`_run_retention_phase`、`RetentionPhaseResult`、`_database_error_code`、`RETENTION_MAX_BATCHES_PER_PHASE` | A2 |
| `repositories/operations.py` | `require_purgeable`、`purge_selected`、`purge_task`、`mark_purged_many`、`_purged_mark`（删除了 `_mark_purged`） | D1/D2 |
| `services/knowledge_service.py` | `PropertyTopic`、`PROPERTY_TOPICS`、`detect_property_topics`、`normalize_text`、`KnowledgeRetrieval`、`retrieve_detailed`、`_query_tokens` | C1 |
| `integrations/deepseek_client.py` | `_property_topic`、`_supporting_knowledge`、`_has_relevant_property_knowledge`、`_has_unsupported_property_claims`、`_validate_decision(knowledge_evidence=…)` | C1 |
| `services/answer_policy.py` | `is_property_specific` | C1 |

其他改动：`.github/workflows/ci.yml` 加了隔离 PG 服务和两个步骤；`tasks/todo.md` 末尾新增一节。
新增测试文件：`tests/integration/test_retention_postgresql.py`、`tests/unit/test_knowledge_retrieval_eval.py`、`tests/fixtures/` 下 3 个 JSON。
修改的测试文件：`test_application.py`、`test_task_page_service.py`、`test_retention_repository.py`、`test_operations_repository.py`、`test_knowledge_service.py`、`test_deepseek_client.py`、`test_knowledge_repository.py`。

`db.py`、`domain/models.py`、`compose.yaml` 和迁移都**没改**。

## 5. A 阶段实现要点

- **事务边界归调度方**：仓储一次只处理一批，从不提交。`_run_retention_phase` 每批一个短会话，一批失败只回滚这一批，之前的批次保留，本阶段就此结束，不影响另一阶段。`CancelledError` 原样抛出。
- **整轮固定一个 now**：两个阶段共用，不随耗时漂移。
- **批量与上限**：通用清理每类每批 500 条，归档清理每批 100 个父任务，每阶段每轮最多 20 批（`ponytail:` 注释写了天花板和升级条件）。达到上限且最后一批仍满时，日志记 `possibly_incomplete`。
- **失败日志不带 `exc_info`**：SQLAlchemy 的异常文本带 SQL 语句和参数（附件编号），项目的脱敏过滤器不处理这些。现在只记阶段、批次序号、异常类型和 SQLSTATE。
- **归档清理**：`FOR UPDATE SKIP LOCKED` 选出候选，先读 `(task_id, file_id)`，再执行带归档条件的 `DELETE … RETURNING id, dedupe_key`；墓碑、附件 job 和计数都只按实际删除的集合来。SKIP LOCKED 只保证清理不等管理员；**反过来，管理员恢复仍可能等清理这一批提交**，Spec 第 5.1 节写明了这一点。
- **人工批量删除**：`require_purgeable` 按编号升序 `FOR UPDATE`，并用 `populate_existing` 刷新会话里已有的对象。刻意**不用** SKIP LOCKED，否则会把用户勾选的整批静默删成一部分。`purge_selected` 用 `RETURNING` 核对删除集合，不一致就抛 `OperationRefused`，由外层回滚整批。
- **墓碑**：`mark_purged_many` 按方言选 PG 或 SQLite 的原生 `ON CONFLICT DO UPDATE`，时间用 `CASE` 保证只前移。`_purged_mark` 清理过期墓碑改成带到期条件的 DELETE，再以最新行版本读取，避免误删别的事务刚刷新的墓碑。
- **删除的参数**：`purge_archived_tasks(delete_file=…)` 和 `_run_retention_loop(storage)` 都删了。启动测试里的 `blocked_loop(*args, **kwargs)` 不受影响。

## 6. C 阶段实现要点

- **主题别名的两层**：`PropertyTopic.aliases` 同时用于专属问题分类、证据门和检索扩展，只收含义明确的说法；`recall_hints` 只帮检索排序。多义词（猫、狗、lift、dryer、smoke、「早上…吃」）只放在后者。已排查的误伤包括：猫咖、吹风机、烟雾报警器、朋友来接、快递包裹、取钥匙、公交车停在哪。
- 新增了「网络」和「入住退房时间」两个主题。原来表外的专属问题，即使知识库有答案也一律回「尚未确认」。
- **检索**：整条问答是最小证据单元，不截断；剩余预算放不下就整条跳过，`KnowledgeRetrieval.budget_skipped` 计数并记日志。问题里的虚词不参与评分。`retrieve()` 接口不变，回复主链路和 FAQ 草稿都还调它。
- **证据门**：问题里每个主题都要有支撑句；只看问答的问题和答案正文，分类和关键词不作证；答案按句判断，含周边措辞的句子不算本店证据（客人本来就在问周边时除外）。
- **回复断言检查**：只在专属事实**单靠知识**确认时启用，工具已确认时跳过，因为工具结果里的数字不在知识里。回复说「免费」而证据没有，或出现证据和问题里都没有的数字，就退回「尚未确认」。列表序号不算数字。这种情况**不生成 FAQ 候选**：候选资格仍按原来的「知识已覆盖」判断。
- **有天花板的地方都留了 `ponytail:` 注释**：别名是有限清单；不做语义蕴含；不区分具体房间。

## 7. 评估体系（接手前必读）

### 7.1 文件

| 文件 | 作者 | 说明 |
| --- | --- | --- |
| `tests/fixtures/knowledge_retrieval_cases.json` | Claude | 校准集 40 条，调参只用它 |
| `tests/fixtures/knowledge_retrieval_holdout.json` | Codex（gpt-5.6-sol） | 留出集 40 条，sha256 `1605a5e94dcf73a26b44a508bfddea61396afad55762d26d16572518dea3dc8b` |
| `tests/fixtures/knowledge_retrieval_baseline.json` | 脚本生成 | 在改动前的 C 范围源码上记录的逐条结果，不含耗时 |
| `tests/unit/test_knowledge_retrieval_eval.py` | Claude | 评估脚本、预设目标测试、直接问法不退化测试 |

Spec 原写两份用例放在同一个文件里。这里拆成两个文件，是为了让出题人的边界清楚。

Codex 出题时，用户配置的 `gpt-6-astra` 需要更新版本的 Codex CLI（本机是 0.152.1），所以那次调用临时指定了 `gpt-5.6-sol`，没有改用户配置。

### 7.2 运行与口径

```sh
PYTHONPATH=src:tests .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report
```

加 `--show-holdout` 才会列出留出集的逐条失败；`--record-baseline` 会重写基线文件，**只应在改动前的源码上运行**。

- 每个用例写进独立的内存 SQLite，走真实 `SQLAlchemyKnowledgeRepository`；mutations 逐条提交，然后用新会话检索。
- 「放行」按生产口径计算：`is_property_specific(问题)` 为真**且**证据门通过。不是专属问题时，回复里的本店自述会被逐句删除，等同于没放行。
- 边界分两项：`boundary_pass`（知识不放行实时问题，C1 负责，要断言）；`realtime_routed`（交易分类或房态强制规则能否识别，只报告，不是 C 的验收项）。
- 预设目标：Recall@3 ≥ 0.90；隔离、边界 100%；错误放行 0；不被支持的模拟回复一律拦下；直接词面问法不比基线差。

### 7.3 结果（留出集在 C1 冻结后只跑了一次）

| 指标 | 校准集 改前→改后 | 留出集 改前→改后 |
| --- | --- | --- |
| Recall@3 | 0.828 → 1.000 | 0.935 → 0.935 |
| 证据完整 | 0.833 → 1.000 | 0.828 → 1.000 |
| 证据门判断准确率 | 0.375 → 0.925 | 0.400 → 0.550 |
| 错误放行 | 1 → 0 | 2 → 2 |
| 模拟回复判定准确率 | 0.778 → 1.000 | 0.333 → 0.833 |
| 隔离 | 1.000 → 1.000 | 0.950 → 0.950 |
| 检索耗时 P50/P95 | 0.46 / 0.64 ms | 0.43 / 0.72 ms |

耗时是 40 条合成数据加内存 SQLite 的数字，不代表生产性能。

留出集未达标的原因：

- **真缺陷**：`hold-iso-31`、`hold-iso-35` 错误放行。周边措辞词表 `_EXTERNAL_SCOPE_PATTERN` 里没有「路口」「桥边」「商户」，周边早餐店的句子被当成了本店证据。
- **口径分歧，待用户确认**：`hold-iso-35`、`hold-bound-39` 隔离失败。留出集把已启用的周边商户信息和静态历史价格列入 `forbidden_facts`，评估的隔离口径是「停用、旧答案、候选草稿」。`hold-bound-39` 的证据门没有放行；`hold-iso-35` 另有上一条的错误放行。
- **保守漏判 16 条**，全部退回「尚未确认」：同义说法不在别名表里（车搁哪儿、甩洗、爬楼、抽两口、降温设备、Can I park、lift）；主题不在表里（晚到入住、儿童用品、空调、屋顶露台）。

⚠️ **留出集已经用过一次，而且失败条目已经公开。** 如果根据上面的失败去改别名或词表，这些条目就不再是独立检验。之后声称泛化能力，需要另出一套新的留出集，出题人不能是调参的人。

## 8. 范围外发现（未修）

- 「今晚还有空房吗」既不被 `is_transaction_sensitive` 识别，也不被 `_should_force_availability` 识别，因为词表里没有「空房」。这属于交易策略，C 阶段不改，需要单独立项。
- `TaskPageService.purge_many_file_ids` 在 `src` 里没有调用方，改动前就这样，没动。
- 自动清理只按父任务数设上限，单批附件行数和清理载荷没有严格上限（Spec 第 5.1 节已接受）；A4 应采集每个任务的附件数分布。

## 9. 待用户决策

1. 是否把「路口」「桥边」「商户」补进周边措辞词表（补了之后，留出集就不再独立检验这一项）。
2. 已启用但适用范围不对的知识，是否要禁止进入证据（决定第 7.3 节的隔离口径分歧）。
3. PG 验收路径：授权推送分支让 CI 跑，或者提供本机隔离的 PG。
4. 是否进入 C2。进入前要选定 embedding 服务商、模型、维度、预算，以及数据发送范围（Spec 第 9.5 节）。
5. 「空房」识别缺口是否单独立项。

## 10. 建议的下一步（按顺序）

1. **先跑 PG 用例**（需要第 9 节第 3 项的授权）。这些用例本身从没执行过，第一次运行可能暴露的是**测试代码**的问题，要和实现缺陷分开判断。连接串校验：只接受 `postgresql+asyncpg`、回环地址、库名 `yumi_test_retention`、角色 `yumi_test`，不接受查询参数。
2. 用户确认第 9 节第 1、2 项后，再改周边措辞或评估口径。改完后移除 xfail 标记，并在报告里注明留出集已不独立。
3. 提交前做一次独立审查，重点看：`purge_selected` 与 `restore_task` 的锁顺序；`_purged_mark` 条件删除在 READ COMMITTED 下的行为；`mark_purged_many` 在两种方言下的 upsert；`is_property_specific` 变宽后，旅游和故障类问题有没有被误判成专属问题。
4. 发布需要用户另行授权；发布时按项目规则更新 `CHANGELOG.md` 和版本号。

## 11. 复现命令

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
git diff --check
```

提供隔离 PG 之后：

```sh
.venv/bin/python -c 'import os; assert os.environ.get("YUMI_TEST_POSTGRES_URL"), "需要隔离 PostgreSQL 测试库"'
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q -rs tests/integration/test_retention_postgresql.py
```

PG 用例被跳过不能算通过；报告里要写实际执行的条数。

## 12. Codex 审查补修与当前状态（2026-09-21）

本轮用户已明确“开始”，范围为 Spec 9.4.1。保留原有数据库与检索改动，只修共享证据校验及直接相关回归；未提交、推送、部署或调用真实外部服务。

### 12.1 已修复

- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._supporting_knowledge`：问题标题仅帮助限定主题和范围，事实必须来自审核答案。周边标题不能给本店设施作证；保留明确的本店政策前句与入住时间等陈述句识别。
- 同文件 `_has_unsupported_property_claims`：免费和数字证据只取审核答案，不再从标题或客人问句提取。新增 `_has_affirmative_free_claim` 排除“不免费”等常见否定，避免用否定证据支持肯定免费。
- `tests/unit/test_deepseek_client.py`：新增 6 个拦截场景和 6 个正常回答对照，均走真实 `respond` 流程、模拟模型输出。修复前 6 个拦截场景全部失败；修复后全部通过。

这些仍是有限的词面检查，不证明任意自然语言蕴含；数字、单位、条件及跨主题事实的完整归属不在保证内。同一支持条目的答案仍整体用于费用和数字检查，不能据此声称逐句或逐事实隔离已经实现。

### 12.2 本轮实际验证

| 项目 | 结果 |
| --- | --- |
| 受影响的回复、投诉、旅游、FAQ、策略、知识检索及路由测试 | 248 通过，1 xfail，1 个 Starlette 弃用警告 |
| 三个改动 Python 文件的 Ruff | 通过 |
| mypy | 133 个源码文件通过 |
| 校准集（40 条） | Recall@3 1.000，证据门准确率 0.925，错误放行 0，模拟回复准确率 1.000，隔离 1.000 |
| 公开留出回归集（40 条） | Recall@3 0.935，证据门准确率 0.600，错误放行 **2 → 0**，模拟回复准确率 0.833，隔离 0.950 |

没有修改评估样本、标签、基线或验收门槛，仅更新 xfail 原因以对应现状。公开留出集已用于修复后的回归；本轮编写的测试也不是独立留出集，不能作为新增泛化能力的证明。

复现本轮最低充分验证集：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q --tb=short tests/unit/test_deepseek_client.py tests/unit/test_deepseek_complaint.py tests/unit/test_deepseek_tourism.py tests/unit/test_deepseek_faq_drafter.py tests/unit/test_faq_draft_job.py tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_knowledge_service.py tests/unit/test_guest_reply_policy.py tests/unit/test_answer_policy.py tests/integration/test_knowledge_repository.py tests/integration/test_knowledge_routes.py
.venv/bin/python -m ruff check src/homestay_bot/integrations/deepseek_client.py tests/unit/test_deepseek_client.py tests/unit/test_knowledge_retrieval_eval.py
.venv/bin/python -m mypy
PYTHONPATH=src:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report
git diff --check
```

### 12.3 尚未关闭的验收项

1. `hold-iso-35`、`hold-bound-39` 的禁止事实仍进入检索上下文，隔离检查失败。拦下最终回答不能替代上下文隔离；继续保留 strict xfail，后续按明确的适用范围规则处理，不能直接删除标签让测试通过。
2. 公开留出集仍有保守漏判；需要另一出题者提供的新独立留出集，才能评价进一步改动的泛化效果。
3. PostgreSQL 本轮执行 **0 项**；本机无隔离测试库，数据库锁、方言、超长键等生产行为仍未验收。数据库代码未在本轮改动，因此未重复全量 SQLite 测试；第 3 节全量数字是历史证据。
4. 真实模型、生产及外部消息均未验证。C2 未启动；“今晚还有空房吗”的实时路由缺口仍属于第 8 节的范围外事项。

## 13. Claude 复核 §12 与回归修复（2026-09-21，用户回复「开始修」）

依据 Spec §9.4.2。只改了 `src/homestay_bot/integrations/deepseek_client.py` 的 `_ANSWER_TOPIC_PATTERNS`、新增的 `_CLOCK_TIME`，以及 `_FREE_NEGATION_PATTERN`；§12 的另外两项修复（标题只限定范围、问句数字不作证）保留不变。

### 13.1 复核结论

§12 修对了两件事：客人问句里的数字不再作证（这是交接前实现的漏洞），标题带「附近」的问答不再为本店作证。但它为防误拒补的答案判据和否定窗口过宽，引入了 4 个错误放行。交接前的实现能拦下这 4 例；两套评估集都没有覆盖它们。

| 场景 | 被放行的错误回复 | 修复 |
| --- | --- | --- |
| 问入住时间，知识只有「猫狗入住」 | 「下午两点以后就可以入住」 | 入住或退房须和钟点表达（14:00、三点、中午、3 pm）在同一分句内相邻 |
| 英文问到地铁站多远，知识只有「300 米外的停车场」 | "about 300 meters from us" | 删除「数字+单位」判据，只认距离类用语（别名加「相距」）；`away` 也不算 |
| 问停车，知识写明收费 | 「不用预约也能免费停车」 | 否定只认紧贴在「免费」前的「不、非、不是、并非、not、n't」等 |
| 问加床，知识只有「加一床被子」 | 「可以加一床」 | 只认「折叠床」「加（一）张…床」「extra bed」「folding bed」「rollaway」 |

另外发现：中文距离问题（「离地铁站多远」等）会先被判为实时旅游问题，转去联网搜索，走不到知识证据门；只有英文问法会走证据门。

### 13.2 验证

| 项 | 结果 |
| --- | --- |
| 4 条回归测试 `test_topic_words_elsewhere_in_an_answer_do_not_prove_the_asked_fact` | 逐条换回 §12 的判据时都失败，失败信息是错误回复被原样发出；修复后通过 |
| §12 新增的 12 条测试 | 全部通过 |
| 全量 `pytest` | 1701 通过，24 跳过（15 项真实契约 + 9 项 PG），1 xfail，6 警告 |
| `ruff` / `mypy` / `git diff --check` | 通过 |
| 校准集 | 错误放行 0；证据门准确率 0.925 → 0.900（新增保守漏判 `cal-xl-09`，英文距离答案里没有距离用语，是 Spec §9.4.2 接受的代价） |
| 公开留出回归集 | 各项与 §12 相同：错误放行 0，证据门准确率 0.600，隔离 0.950 |

### 13.3 仍然开放

- 距离证据不核对目的地（`ponytail:` 已注明）：答案写「离地铁站 500 米」时，仍可能被当作问另一处距离的证据。这个缺口交接前就有；要处理，需另行确认「目的地一致」规则。
- §12.3 列出的其余事项不变：上下文隔离的两条分歧、同义漏判、PostgreSQL 未验收、真实模型与生产未验证、「空房」识别缺口。
- 这一轮的 4 条回归测试是看过代码后编写的，不是独立留出集。

## 14. 收尾补齐与 1.37.0 发布准备（2026-09-22，用户回复「全部做完」）

依据 Spec §7.2、§9.4.3、§9.5.0。

### 14.1 已完成

| 项 | 结果 |
| --- | --- |
| PostgreSQL 验收 | 分支 `release/1.37.0` 推送后，CI 在隔离的 PostgreSQL 16 上从零迁移到 head，`test_retention_postgresql.py` **18 通过、0 跳过**（9 条真连库：T1、T9、T12、T17），其余步骤与生产镜像构建全部通过 |
| A4 线上基线 | 只读采集，见 Spec §7.2：无清理积压，旧超长键缺陷从未触发，**生产启用的知识条目为 0** |
| 上下文隔离（剔除） | `deepseek_client.py::_scope_knowledge`；留出集第一版隔离 0.950 → 1.000，第一版的 xfail 已按其条件移除 |
| 距离核对目的地 | `_distance_destination/_mentions_place/_passage_states_topic`；`cal-xl-09` 靠目的地核对恢复放行 |
| 「空房」识别 | `answer_policy._TRANSACTION_PATTERN` 与 `_is_standalone_availability_query/_should_force_availability` |
| 英文兜底文案 | 专属事实未确认时，按回复语言给中文或英文；`PropertyTopic.english` |
| 变异验证 | 本轮 6 处新增保护逐一删除，都有测试失败 |
| 全量 `pytest` | 1707 通过，24 跳过（15 项真实契约 + 9 项 PG，PG 已在 CI 执行），0 xfail |

### 14.2 第二套留出集（Codex 编写，C2 前基线）

`tests/fixtures/knowledge_retrieval_holdout_v2.json`，sha256 `6df864144c0b15d0819ab3975fff63fd8a45091a4304dbf617ec56d4ca382dcd`，40 条（synonym 14、cross_language 8、long_answer 4、no_answer 6、isolation 5、multi_topic 2、boundary 1）。

在 1.37.0 代码上只看了汇总，逐条结果写在 `knowledge_retrieval_baseline_v2.json`，**调 C2 期间不查看**：

| 指标 | 结果 |
| --- | --- |
| Recall@3 | 0.900（目标 ≥0.90） |
| 多主题覆盖 | 0.500 |
| 证据完整 | 1.000 |
| 证据门判断准确率 | 0.525 |
| 错误放行 | **1**（目标 0，未达标） |
| 模拟回复判定准确率 | 0.833 |
| 隔离 | **0.975**（目标 1.000，未达标） |
| 实时问题被工具路由识别 | 0/1 |

这是 C1 第一次接受独立检验：召回刚好达标，安全项有 1 条错误放行、1 条隔离失败，具体用例留到 C2 冻结后一并查看。生产知识库目前为空，证据门不会放行任何本店事实，所以这条错误放行现在在生产上触发不了。
