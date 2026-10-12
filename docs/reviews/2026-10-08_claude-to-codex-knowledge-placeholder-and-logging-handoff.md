# Claude → Codex：三项小修复合并审查（知识占位符拦截、应用 INFO 日志、Bearer 脱敏）

- 日期：2026-10-08。作者：Claude。
- 请求：审查主工作区内三项未提交的代码改动。用户要求「统一会话」：两个日志会话的改动已合入主工作区，与本会话的占位符修复一起交审。
- 基线：HEAD `80f9b2c309547c36c15cfd142d5dd30b98d87406`（与三个改动的出发点相同）。
- 本轮没有提交、推送或部署，没有调用真实模型或外部服务，没有连接生产或现有数据库。
- 不在本报告范围：回复泛用化第二轮修复 Spec v7，见单独的交接 [2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v7-review-handoff.md](2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v7-review-handoff.md)。该 Spec 尚未进入编码。

## 1. 来源与合并

| 改动 | 来源会话 | 原工作树 | 合入方式 |
| --- | --- | --- | --- |
| A 知识占位符拦截 | 本会话 | 主工作区 | 直接实现 |
| B 应用 INFO 日志 | 「让生产输出应用 INFO 日志」 | `.claude/worktrees/affectionate-hamilton-be1111` | 导出未提交 diff，`git apply --3way` |
| C Bearer 凭证脱敏 | 「Fix Bearer token leak in log redaction」 | `.claude/worktrees/zealous-clarke-fa51bc` | 同上 |

- B 和 C 都在 `tests/unit/test_log_redaction.py` 末尾追加了一条测试，合并时在这里冲突。处理方式是两条都保留，不改测试内容。`src/homestay_bot/logging.py` 的两处改动不重叠，干净合并。
- 合并后已取消暂存，三项改动都只是工作区未暂存修改。原来的两个工作树保持不动。
- B、C 两个会话都没有自行提交。

## 2. 改动 A：拦截含「【待管理员确认】」占位符的知识写入（本会话）

**问题与证据。**
- FAQ 草稿生成器 `DeepSeekFaqDrafter._validate_safety` 要求本店专属事实带占位符「【待管理员确认】」，由管理员核实后替换。
- 候选页模板 `templates/knowledge/index.html` 会把草稿原样预填进转换表单。
- `KnowledgeAdminService.convert_candidate`、`create`、`update` 原来都不检查占位符。管理员选了 global、property 范围又漏改时，客人会收到「【待管理员确认】」。`scope=unreviewed` 的条目不参与回答，所以只在选了范围时生效。
- 现有测试中，路由级测试用的是替身服务，覆盖不到这一点。

**改动。**
- `services/knowledge_service.py`：新增公开常量 `ADMIN_CONFIRMATION_PLACEHOLDER`，以及 `validate_knowledge_content(*texts)`，风格与 `validate_knowledge_scope` 一致。草稿生成与知识写入共用这个唯一定义。
- `integrations/deepseek_faq_drafter.py`：删除本地常量，改为从 `knowledge_service` 导入，保留原私有名，行为不变。没有新增导入方向：草稿生成器原本就依赖 services 层，路由层仍只依赖 services。
- `routes/knowledge.py`：
  - 新增 `_QA_FIELDS`（中英问答四个字段）与 `KnowledgeAdminService._validate_content`；
  - `create`、`convert_candidate` 在校验范围之前检查；
  - `update` 按改后的完整问答检查，未改的字段取库里的现值；
  - 命中时抛 `KnowledgeFormError`（422）。路由沿用现有处理：页面回填输入，候选保持待处理，不生成知识。
- 不检查关键词、分类与触发词，因为它们不会作为答案发给客人。

**测试。** 新增 `test_admin_service_rejects_unconfirmed_draft_placeholder`（`tests/integration/test_knowledge_routes.py`），使用真实服务与内存 SQLite：
- 候选转换带占位符：被拒，候选仍是 OPEN，没有生成知识；
- 新增时英文答案带占位符：被拒；
- 编辑时只改问题并带占位符：被拒，库里原值不变。
- 判别力：只撤掉三处源码改动时，该测试失败；恢复后通过。

**已知边界。**
- 生产库里可能已经存在含占位符的启用知识。本修复不会追溯改写它们，只是在管理员编辑时要求先替换。我没有查生产数据（未获授权）。
- 只拦截字面占位符，管理员自己改写成其他「待确认」措辞的不拦截。

## 3. 改动 B：生产输出应用 INFO 日志（日志会话一）

**背景。** 1.69.1 发布时发现，生产只有 uvicorn 访问日志，看不到轮次规划、证据选择、主链耗时等应用 INFO 日志，原因如下：
- 根 logger 没有 handler，级别为 WARNING；
- 子模块的 WARNING 会经 `logging.lastResort` 输出，从而绕过脱敏过滤器。

**改动。**
- `logging.py` 新增 `configure_application_logging()`：
  - 把 `homestay_bot` 命名空间设为 INFO，挂一个具名的 stderr handler（`homestay_bot.stderr`，重复调用不会重复挂载），格式为「时间 级别 logger名: 消息」；
  - 挂好 handler 后再调用 `configure_logging_redaction()`，因为子 logger 的记录只经过 handler 级过滤器；
  - 第三方库的 INFO 仍关闭，`propagate` 保持默认；
  - 有 `ponytail:` 注释说明级别固定为 INFO 的天花板。
- `main.py`：启动时由 `configure_logging_redaction()` 改为 `configure_application_logging()`。

**测试。** `test_production_assembly_emits_redacted_application_info_logs`：
- 在子进程里按生产顺序装配（先 `uvicorn.Config(...).load()`，再导入应用），不添加任何测试 handler；
- 断言应用 INFO 日志会输出到 stderr，且其中的 `Hostex-Access-Token` 被脱敏。
- 判别力：把 `main.py` 改回只装脱敏时，该测试失败。

**我的复核（合并后）。**
- 现有 23 处 `logger.info` 我逐条看过，只写编号、状态、原因、计数与耗时，不含客人原文或回复正文。`customer_context_recalled` 用的是 `extra`，当前格式化器不会输出 `extra`。
- 子模块 WARNING 现在改由应用 handler 输出，会经过脱敏，格式也随之改变。
- **风险**：`compose.yaml` 没有为应用容器设置 `logging` 滚动选项（`max-size`、`max-file`）。开启 INFO 后，每条客人消息会多出数行日志。生产 Docker 守护进程是否有全局滚动配置未核实（需授权的只读检查）。如果没有，日志会持续增长。

## 4. 改动 C：Authorization Bearer 凭证脱敏（日志会话二）

**问题。** `_TOKEN_IN_TEXT_PATTERN` 原为 `(Hostex-Access-Token|Authorization)\s*:\s*\S+`。遇到「Authorization: Bearer <token>」时，`\S+` 只吃掉 `Bearer`，令牌明文残留在日志里。

**改动。**
- 在冒号后加入可选的方案词 `(?:(?:Bearer|Basic|Token)\s+)?`，令牌一并消费，写法与 `_SECRET_ASSIGNMENT_PATTERN` 一致；
- 加负向先行断言 `(?!\[REDACTED\])`：同一条记录先后经过 logger 和 handler 两层过滤器时，不会重复叠加。

**测试。** `test_log_filter_redacts_bearer_credential_in_plain_message_idempotently`：断言令牌被去掉；同一记录过滤两次结果不变；`[REDACTED]` 只出现一次。判别力：只撤掉正则改动时，该测试失败。

**与 B 的关系。** B 让更多应用日志真正输出到 stderr，C 补的正好是这些日志里的一处脱敏漏洞，两者一起发布更安全。

## 5. 验证（合并后的主工作区）

| 项目 | 结果 |
| --- | --- |
| 相关测试：`test_log_redaction.py`、`test_knowledge_routes.py` | 47 passed |
| 草稿、草稿任务、应用装配与契约测试（A 修改时） | 95 passed，10 skipped（真实 DeepSeek 契约需显式开启） |
| 判别力 | A、B、C 各自撤掉源码改动时，对应新测试失败；恢复后通过 |
| 本地全量（`main.py` 入口装配有变化，按项目规则执行） | 2641 passed，65 skipped（PostgreSQL 用例未设置 `YUMI_TEST_POSTGRES_URL`，以及需显式开启的真实接口测试），用时 124 秒 |
| ruff（src、tests） | 通过 |
| mypy（src，136 个文件） | 通过 |

未覆盖：CI、PostgreSQL 用例、生产容器日志实际输出、生产库已有占位符条目、Docker 日志滚动配置。

## 6. 请 Codex 重点审查

1. **A 的检查范围**：只拦截中英问答四个字段的字面占位符；`update` 按改后完整值检查，会挡住含旧占位符的历史条目的任何编辑。这两点是否合适。
2. **B 的 handler 与 propagate**：`homestay_bot` 挂了自己的 handler，同时 `propagate=True`。生产根 logger 没有 handler，所以不会重复输出。但如果将来有人给根 logger 加 handler，会不会重复输出？是否需要在注释或测试里固定这一点。
3. **B 的日志量与滚动**：是否应在本次一并给 `compose.yaml` 加日志滚动，还是先核实生产守护进程配置。注意 `tests/unit/test_compose_security.py` 对 compose 有约束。
4. **C 的正则**：方案词只列了 Bearer、Basic、Token。其他写法（小写 `bearer`、多个空格）已被 `(?i)` 和 `\s+` 覆盖。还有没有常见形态漏掉，比如「Authorization=Bearer ...」由 `_SECRET_ASSIGNMENT_PATTERN` 处理，是否一致。
5. **合并本身**：测试文件冲突只是两边各自追加，按原样都保留。请确认合并后的文件与两个工作树的版本语义一致。

## 7. 工作区状态

- 三项代码改动（未暂存）：
  - `src/homestay_bot/logging.py`、`src/homestay_bot/main.py`（B、C）；
  - `src/homestay_bot/routes/knowledge.py`、`src/homestay_bot/services/knowledge_service.py`、`src/homestay_bot/integrations/deepseek_faq_drafter.py`（A）；
  - `tests/unit/test_log_redaction.py`（B、C）、`tests/integration/test_knowledge_routes.py`（A）。
- 同时存在、与本报告无关的未提交内容：
  - fix2 Spec 与各轮 Claude、Codex 审查报告；
  - 上一轮 Claude 报告的补充；
  - `.aoci/baseline.json`、`aoci.code.txt`（含 A 的认知条目同步；B、C 合入后的认知维护在本报告之后进行）。
- `.impeccable/critique/` 不属于本工作。
- 版本号、更新日志与发布记录尚未处理。若这三项单独发版，需要另行授权，并补 `CHANGELOG.md` 与 `docs/releases/`。

## 8. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| src/homestay_bot/logging.py | 6c73a6c0119bad25ab71f53b027cfb45bc90817f11702b8f2c7f1120812692a6 |
| src/homestay_bot/main.py | 0bad7f338ae2d0cd6c9c537bc0c0bcbe1c76f42abb134739354f3e30adc59d92 |
| src/homestay_bot/routes/knowledge.py | a831260ea4d34037b7e800d2905c1b30c7ce84b042d27f5afcdc6a0a4248dc3c |
| src/homestay_bot/services/knowledge_service.py | f1960df6a34cde842899273c2c715cbfb0625c24fdc242c63196563a5d111260 |
| src/homestay_bot/integrations/deepseek_faq_drafter.py | e299cd8c9d01147c343e1186bc242ba21092fb898fff4d869145c11c4f55d8e6 |
| tests/unit/test_log_redaction.py | f14ff3645ae3a2ec219a6ac11eb61b99685aaae5120dc71342cf2f99612ebcc9 |
| tests/integration/test_knowledge_routes.py | 2c6193d9fcfa13c08015111a347361b7e0c03a3b60d611ba038aa0817a23af8f |
| compose.yaml | c943dcf175d731f3b927b7c7e25897c6af6ccdd24ec014d583a2e07022add23c |

## 9. 2026-10-08 补充：按 Codex 审查结果修订

Codex 审查结论为「需修改」，提出两项可复现的遗漏，另附三条说明。两项都先补了回归测试，确认在修订前的代码上失败，修订后通过。

### 9.1 [P1] Bearer 脱敏覆盖不完整（已修）

- **原因**：
  - 普通消息走 `_redact_url`：消息里只要含 `?`，就整句按 URL 解析，只处理查询参数，跳过令牌规则；
  - 不含 `?` 时只用 `_TOKEN_IN_TEXT_PATTERN`，它只认「冒号 + 无引号」写法。
  - 因此「Authorization=Bearer x」「Authorization: "Bearer x"」以及与回调 URL 同行的令牌都会原样输出。
  - 异常栈走的 `redact_exception_text` 反而完整：查询参数加 `_SECRET_ASSIGNMENT_PATTERN`，后者覆盖等号、冒号、引号与 Bearer。
- **修订**（`logging.py`）：
  - 新增 `redact_text`，作为普通消息、字段值、异常栈与调用栈的统一文本入口：查询参数键值对，加 `_SECRET_ASSIGNMENT_PATTERN`，再加 `_TOKEN_IN_TEXT_PATTERN`。只替换键值对，不重新编码整句，中文问句与多行栈保持原样；各模式都带负向先行断言，重复过滤不叠加。
  - `redact_exception_text` 改为调用它（保留名称）；`_redact_value` 的字符串分支和 `SensitiveDataFilter.filter` 的普通消息分支也改用它。
  - `_redact_url` 只保留给 Uvicorn 访问日志的五元参数，参数协议不变。
- **测试**：`test_plain_message_redacts_bearer_in_every_common_form` 覆盖四种写法：等号、与带 `?code=` 的 URL 同行、引号、通过 `%s` 参数传入。每种都断言令牌与 `code` 值不残留、两次过滤结果一致、不叠加 `[REDACTED]`。另断言含中文问号的普通句子不被当成 URL 重编码。

### 9.2 [P2] 启用知识绕过占位符校验（已修）

- **修订**（`routes/knowledge.py`）：`KnowledgeAdminService.set_enabled` 在启用前，对条目当前的中英问答执行 `_validate_content`。停用不校验，含占位符的已启用条目可以随时下线止损。
- 拒绝时抛出 `KnowledgeFormError`。启停路由没有单独捕获它，由全局 `handle_operation_refused` 处理：页面请求写入错误提示并重定向，接口请求返回 422 JSON。
- **测试**：`test_admin_service_refuses_enabling_entry_with_placeholder` 直接写库构造占位符拦截上线前的历史停用条目，断言启用被拒且仍为停用；另一条已启用的含占位符条目可以正常停用。

### 9.3 对其余说明的回应

- **已启用条目的存量检查**：同意，这是发布前缺口。需要对生产资料库做一次只读查询，统计中英问答含「【待管理员确认】」的启用条目，需你授权后执行。查到的条目建议先停用，再由管理员修改。
- **propagate 与根 logger**：同意现状下可接受。如果将来给根 logger 加 handler，会重复输出；这一点已写在 `configure_application_logging` 的注释里（「生产根 logger 没有 handler 不会重复输出」）。本次不改。
- **Docker 日志滚动**：同意，不能仅凭 `compose.yaml` 断言生产没有滚动。需要只读核实生产容器的日志驱动与 `log-opts`（以及守护进程的全局配置），同样需要授权。

### 9.4 验证

| 项目 | 结果 |
| --- | --- |
| 两条新回归测试 | 修订前失败，修订后通过 |
| `test_log_redaction.py` | 13 passed（含原有全部脱敏测试） |
| `test_knowledge_routes.py` | 36 passed |
| 本地全量 | 2643 passed，65 skipped（PostgreSQL 用例与需显式开启的真实接口测试） |
| ruff（src、tests）、mypy（src） | 通过 |

### 9.5 证据绑定（本次修订后）

| 文件 | SHA-256 |
| --- | --- |
| src/homestay_bot/logging.py | a227341b7efeff8e4b92f7561da9dcf43bb7b9c87fa8f5b31394380f51745645 |
| src/homestay_bot/routes/knowledge.py | fd8a1813320b9ac55eaa7bfcfbfb076ee56101f190d84a8c2aca68e56a900662 |
| tests/unit/test_log_redaction.py | 653fd65d774ccf7a7d966f4218c8a2b6778bd03d68d17be7bdf114d071d1dfd3 |
| tests/integration/test_knowledge_routes.py | 84c8b766baaad40b381ed180a999b7187dbde029510a6ee9d43800a51f8d6fcd |

其余文件（`main.py`、`knowledge_service.py`、`deepseek_faq_drafter.py`）本次未改，摘要同 §8。
