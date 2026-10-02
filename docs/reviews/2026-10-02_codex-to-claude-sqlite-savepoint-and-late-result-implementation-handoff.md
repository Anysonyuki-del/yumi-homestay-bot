# Codex → Claude：SQLite 保存点与迟到建单结果实施审查交接

日期：2026-10-02。状态：本地实施及最终验证完成，准备交给 Claude 独立审查；尚未提交、推送、部署或发布。

本轮修复了 SQLite 保存点释放后不能随外层回滚的问题，并让被丢弃的旧轮次建单结果留下可追溯日志、审计和管理员页面提醒。全部建单错误信封使用中性说明，避免在未核实上游契约时断言订单没有创建。请按最终工作区审查实现和验收证据，不把测试通过直接当作生产验收通过。

## 1. 实施依据与本轮边界

- 用户在 Spec 复审后回复「开始」，授权本地编码与验证；本次要求是准备实施交接报告。
- 当前实施依据：[Spec：用户决定、F1–F3、D4](</Volumes/02/obsidian codex/homestay-bot/docs/specs/2026-10-02_sqlite-savepoint-and-late-result-spec.md:40>)；[Spec §6–7：文件计划与验证记录](</Volumes/02/obsidian codex/homestay-bot/docs/specs/2026-10-02_sqlite-savepoint-and-late-result-spec.md:123>)。
- 前序依据：[Codex Spec 审查交接：SP1–SP4](</Volumes/02/obsidian codex/homestay-bot/docs/reviews/2026-10-02_codex-to-claude-sqlite-savepoint-and-late-result-spec-review-handoff.md:1>)。本对话后续复审的 SP5、SP6 已写入当前 Spec 的 D4、隐私边界和验收条件。原审查报告保留历史原文，不覆盖。
- 适用规则：[AGENTS.md](</Volumes/02/obsidian codex/homestay-bot/AGENTS.md:1>)；手册[事务、日志与迁移](</Volumes/02/obsidian codex/homestay-bot/YuMi民宿AI开发经验与防回归手册.md:392>)和[环境相关边界](</Volumes/02/obsidian codex/homestay-bot/YuMi民宿AI开发经验与防回归手册.md:483>)。验证按相关风险选取，已有有效结果直接复用。
- 本轮包括 F1 保存点父事务、F2 迟到诊断与审计、F3 详情页提醒、D4 错误说明，以及保证这些行为可验证的测试适配。没有新增依赖、迁移、索引、通知任务、审计重试队列或「已核对」按钮。

| 决策 / 审查项 | 最终落实 | 关键依据 |
| --- | --- | --- |
| D1a / SP1 | 仅 SQLite 保存点前按需 BEGIN；按公开连接层级取得真实驱动连接 | create_engine 内 _ensure_sqlite_parent_transaction |
| D2a / SP3 | 先记含原请求证据的 warning，再在当前事务刷新审计；失败上抛并回滚 | BookingService._record_late_result；SQLAlchemyApprovalRepository.record_late_result |
| D3a / SP2 | 当前没有经核实的「一定未建单」错误码，所有迟到丢弃都登记并提醒 | 不设 upstream_may_exist，不按 400 / 503 分流提醒 |
| SP2：阶段与请求编号 | 建单阶段、核验阶段分别记录；成功后查询失败仍保留 success 与原请求编号；错误信封编号进入迟到诊断 | _reconcile_or_mark_review；_mark_needs_review |
| SP4：实际引擎与页面分支 | 新回归经过项目 SQLite 工厂和真实仓储；提醒置于各状态分支外 | test_db.py；test_approval_late_results.py；detail.html |
| D4 / SP5 | 所有 HostexBusinessError 使用「百居易返回错误，订单是否已创建待核验」 | confirm_and_create；400 / 503 行为用例 |
| SP6 | 隐私限制针对新增警示区、日志与审计；原有管理员预订资料区继续展示已授权信息 | get_detail 的字段投影；浏览器警示区与资料区分别断言 |

## 2. Git 基线与完整审查范围

工作目录：/Volumes/02/obsidian codex/homestay-bot。分支 main，HEAD：53d369d29bd11b03ff796ab2030daffccee06c70。本轮没有新增提交，也没有暂存文件；要审查的是 HEAD 之上的工作区改动。

写报告前，Git 记录 10 个已跟踪文件修改，共 402 行增加、28 行删除；另有 3 个未跟踪文件。统计不包含未跟踪文件及本报告，不能用普通 git diff 代替完整范围。

| 已跟踪修改文件 | 符号 / 内容 |
| --- | --- |
| [db.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/db.py:14>) | create_engine；新增 _ensure_sqlite_parent_transaction |
| [repositories/approvals.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/repositories/approvals.py:68>) | SQLAlchemyApprovalRepository.record_late_result |
| [services/booking_service.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:94>) | ApprovalRepository.record_late_result；confirm_and_create；_reconcile_or_mark_review；_same_round；_record_late_result；_mark_needs_review |
| [services/approval_page_service.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/approval_page_service.py:174>) | get_detail 的迟到审计查询与投影；backfill_reservation 仅调整事务注释 |
| [approvals/detail.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/approvals/detail.html:13>) | late_results 提醒，位于各状态分支之外 |
| [tests/unit/test_db.py](</Volumes/02/obsidian codex/homestay-bot/tests/unit/test_db.py:38>) | 保存点六种事务组合；真实 Job 仓储冲突、提交与回滚 |
| [tests/integration/test_approval_review_actions.py](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:624>) | 迟到交错、阶段保留、审计失败、中性错误说明与 AR8 证据断言 |
| [tests/integration/test_approval_review_postgresql.py](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_postgresql.py:1>) | 复用新增审批行为用例，在专用 PostgreSQL 上执行 |
| [tests/unit/test_approval_page_degradation.py](</Volumes/02/obsidian codex/homestay-bot/tests/unit/test_approval_page_degradation.py:30>) | _Session.scalars 提供空审计结果，保留参考数据降级测试的原职责 |
| [tasks/todo.md](</Volumes/02/obsidian codex/homestay-bot/tasks/todo.md:1>) | 当前实施清单与最终验证摘要 |

| 原有未跟踪文件 | 审查方式 |
| --- | --- |
| [当前 Spec](</Volumes/02/obsidian codex/homestay-bot/docs/specs/2026-10-02_sqlite-savepoint-and-late-result-spec.md:1>) | 阅读 F1–F3、D4、§6–7，核对设计与实现 |
| [前序 Spec 审查交接](</Volumes/02/obsidian codex/homestay-bot/docs/reviews/2026-10-02_codex-to-claude-sqlite-savepoint-and-late-result-spec-review-handoff.md:1>) | 历史问题依据；其中旧代码行号和未实施结论不代表当前状态 |
| [新增浏览器回归](</Volumes/02/obsidian codex/homestay-bot/tests/browser/test_approval_late_results.py:1>) | 必须读完整文件；普通 git diff 不显示其正文 |

本次文档交接只新增本报告。请勿将这些工作区修改描述成一个已经提交的 Codex commit，也勿把已在 HEAD 中的前序审批修复算作本轮新增实现。

## 3. 关键调用链与事务边界

### F1：项目 SQLite 引擎 → 保存点 → 外层提交 / 回滚

[create_engine](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/db.py:14>) 在 SQLite 分支注册 savepoint 事件。回调使用 connection.connection.driver_connection，并检查 in_transaction；只有驱动还没有物理事务时才执行 BEGIN，再由 SQLAlchemy 建立保存点。

修复前，外层没有写入或仅做查询时，保存点可能成为最外层物理事务，RELEASE 已经提交内部插入，外层 rollback 无法撤销。修复后，保存点属于物理父事务：内部唯一键冲突只撤销内层，外层仍可继续；成功内层也随外层一起提交或回滚。已有物理事务时不重复 BEGIN。

该监听沿用项目工厂覆盖保存点调用方，无需逐个改仓储。PostgreSQL 分支不注册；不使用保存点的 SQLite 只读路径保持原事务策略。含保存点事务的真实锁持有期会相应恢复，不能宣称完全不影响 SQLite 并发。

真实仓储回归经过 [SQLAlchemyJobRepository.enqueue 用例](</Volumes/02/obsidian codex/homestay-bot/tests/unit/test_db.py:68>)，实际撞数据库唯一键；测试只将一次查重预读模拟为未命中，未替换保存点、唯一约束或事务提交。

### F2：真实审批门面 → 建单 / 核验 → 加锁复读 → 迟到证据

入口装配仍由 [SessionApprovalPageService._service](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/application.py:1766>) 组装同一会话的审批仓储、权限和 BookingService，application.py 未改动。

[BookingService.confirm_and_create](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:94>) 捕获本轮 approved_at，按实际调用阶段传递 create_result。写后查询在事务外完成；写回前通过 [SQLAlchemyApprovalRepository.get_for_update](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/repositories/approvals.py:46>) 加锁并使用 populate_existing 取得数据库现值。轮次判断仍要求状态为 CREATING 且 UTC 确认时间等于捕获值。

[_reconcile_or_mark_review](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:217>) 与 [_mark_needs_review](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:372>) 两个出口一旦发现不属于当前轮次，调用同一 [_record_late_result](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:303>) 后返回当前审批，不能把旧房间、金额、请求编号、订单号或失败说明写回新轮次。

审计契约：

| 字段 | 含义与边界 |
| --- | --- |
| action / target | booking_approval_late_result_discarded；booking_approval；审批编号字符串 |
| actor_employee_id | None，系统事件 |
| create_result | success / business_error / transport_error / not_attempted；恢复核验不发建单 POST |
| verify_result | matched / unmatched / failed / skipped；与建单阶段分别保存 |
| request_id | 原建单成功或错误信封的请求编号；未取得为 None |
| reservation_codes | 已匹配的订单号列表，最多 20 个；没有匹配为 [] |
| round_approved_at | 被丢弃旧轮次的确认时间，不从加锁后的新对象重新取值 |
| current_status | 丢弃时数据库现状态 |
| error_code | 有错误码时才加入；不据此推断未建单 |

日志在审计前输出审批编号与同一组最小证据，格式化消息本身保留字段，不依赖 formatter 输出 extra。没有加入客人姓名、手机或上游原始请求正文。

[record_late_result](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/repositories/approvals.py:68>) 只 add + flush，不单独 commit；提交与回滚由既有 transaction 负责。审计失败传播异常，事务撤销审计写入；管理员此时看不到新提醒，日志是排障保底，不能替代持久审计。

正常当前轮次保留原有请求编号持久化语义：成功建单的编号写入 hostex_request_id；错误信封编号只进入迟到日志 / 审计，不因本轮修复扩展该字段语义。D4 只改未经契约证明的拒绝说明，仍保留错误码，建单不自动重放。

### F3：管理员详情路由 → 定向审计查询 → 提醒投影与模板

[approval_detail](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/routes/approvals.py:102>) 的 ADMIN 校验沿用原实现。[ApprovalPageService.get_detail](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/approval_page_service.py:174>) 按 action、target_type、target_id 三个条件过滤，仅取当前审批的迟到审计，按 created_at、id 倒序最多 10 条。

模板只收到 created_at、request_id、reservation_codes、error_code，没有透传整份 details。[late_results 警示区](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/approvals/detail.html:13>) 放在状态分支外，用现有样式和 role=alert；缺少编号显示「未取得」。原 [预订资料区](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/approvals/detail.html:6>) 继续向管理员显示客人资料。

数据边界是“新增提醒、日志和审计不夹带客人资料”，并非“整个审批详情页不能包含姓名或手机”。浏览器夹具故意在历史审计 JSON 中塞入合成敏感字段，验证模板投影不透传它。

## 4. 已完成验证与判别力

以下结果来自本轮实施结束时的本地验证，本次写报告没有重跑业务测试。源码、测试和验收目标未再改变，最终全量记录覆盖了最后新增的 AR8 审计断言。

| 验证范围 | 结果 | 能证明的事实 |
| --- | --- | --- |
| test_db.py 保存点回归 | 修复前 3 failed、7 passed；修复后 10 passed | 无前置操作 / 查询 / 写入 × 外层提交 / 回滚；内层撞键后继续；真实 Job 仓储外层回滚不残留 |
| 新增迟到行为与 D4 用例 | 实现前目标行为 9 项失败 | 缺失审计、审计故障未上抛、原拒绝说明均能被用例捕获 |
| SQLite 审批相关 3 文件回归 | 54 passed，8.58 秒 | 真实会话门面、旧轮次交错、阶段保留、审计回滚、中性说明及原有审批行为 |
| DB / 页面 / 路由 / 浏览器相关 5 文件回归 | 38 passed、1 warning，5.83 秒 | 两种页面状态、最近 10 条、目标与动作过滤、缺失编号、普通员工 403、390px 宽度及新增警示隐私边界 |
| 最终本地全量，带专用 PostgreSQL 16 | 2440 passed、15 skipped、10 warnings，87.22 秒 | 审批交错、阶段保留和审计回滚复用用例在 PostgreSQL 实际执行；最终源码与测试通过 |
| Ruff | 通过 | src 与本轮受影响测试的静态规则 |
| mypy | 134 个源文件通过 | src/homestay_bot 类型检查 |
| diff / 未跟踪文本检查 | 通过 | 已跟踪 diff 合法；新测试、Spec 和本报告另查行尾空白 |

54 项目标回归完成后，AR8 用例补充了旧确认时间、当前 CREATING 状态、原请求编号及审计数量断言；这些最终断言由最后的 2440 项全量覆盖。不能把较早的 54 项结果描述为独立验证了最后这些断言。

重点行为证据：

- [旧轮次不能覆盖已完成新轮次](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:624>)：成功、400、503 三条迟到路径，检查新状态 / 房间 / 金额 / 请求编号 / 订单号不被覆盖，并核对审计动作、目标、阶段、原编号和隐私。
- [建单与核验阶段分别保留](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:689>)：网络错误、成功后查询失败、超时恢复；成功后失败仍记 success 与原请求编号，恢复不发建单 POST。
- [实际 flush 后故障回滚](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:747>)：审计先真实落库，再注入异常；新轮次仍为 CREATING，数据库无该审计，日志保留原请求与订单线索且不含合成姓名、手机。
- [400 / 503 中性说明](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:795>)：保留错误码、正常 hostex_request_id 原语义、仅一次建单。
- [新轮次仍创建中时的 AR8](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:853>)：不同确认时间仍能识别旧结果；旧结果只记一条审计，新轮次自身成功不能被当成迟到再记一条。
- [浏览器真实响应检查](</Volumes/02/obsidian codex/homestay-bot/tests/browser/test_approval_late_results.py:92>)、[普通员工访问限制](</Volumes/02/obsidian codex/homestay-bot/tests/browser/test_approval_late_results.py:133>)：临时文件 SQLite、真实会话门面与路由；只替换测试认证和上游，未增加业务兼容分支。

PostgreSQL 验证使用本次新建的本机隔离集群、独立角色与专用数据库，先 Alembic upgrade head 到 0034_wecom_sync_cursors，再执行全量；运行后已停止临时集群。没有连接生产数据库。此处不记录连接串或凭据。

供必要重现时使用的命令如下；请按风险复用有效证据，不因接手审查重复运行全量：

~~~sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q \
  tests/integration/test_approval_review_actions.py \
  tests/integration/test_approval_flow.py \
  tests/integration/test_approval_repository.py --tb=short

RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q \
  tests/unit/test_db.py tests/browser/test_approval_late_results.py \
  tests/unit/test_approval_page_service.py \
  tests/unit/test_approval_page_degradation.py \
  tests/integration/test_approval_routes.py --tb=short

# 全量包含 PostgreSQL 的前提：先建立并迁移符合测试夹具安全约束的本机专用库。
RUN_LIVE_CONTRACT_TESTS=0 \
YUMI_TEST_POSTGRES_URL='<本机专用测试库连接串占位，不可原样运行>' \
.venv/bin/python -m pytest -q --tb=short

.venv/bin/ruff check src tests/unit/test_db.py \
  tests/unit/test_approval_page_degradation.py \
  tests/integration/test_approval_review_actions.py \
  tests/integration/test_approval_review_postgresql.py \
  tests/browser/test_approval_late_results.py
.venv/bin/mypy src/homestay_bot
git diff --check
~~~

引擎覆盖：test_db.py、审批 actions 的 SQLite world、新浏览器夹具使用项目 create_engine。部分其他 SQLite 测试直接 create_async_engine，未批量迁移；全量通过不能证明这些引擎安装了新监听。PostgreSQL 用例使用专用 PostgreSQL 引擎验证其自身事务与锁语义。

## 5. 未覆盖范围与已知限制

1. 15 个 skipped 全部是真实外部契约：DeepSeek 10、Hostex 3、企业微信 2。没有调用真实模型、上游或外部消息接口，也没有写生产数据。
2. 浏览器检查是 TestClient 经真实 ASGI 路由取得 HTML，再用 Chromium set_content 与项目样式检查静态提醒；阻止额外资源请求。它不证明浏览器真实 HTTP 导航、完整 JS 交互、生产登录页面或真实上游迟到事件已验收。
3. PostgreSQL 组覆盖受控交错和故障注入，不是长时间并发压力测试；文件 SQLite 的完整写锁压力也未覆盖。
4. 10 条警告包括 Starlette / Alembic 弃用提示，及 FAQ、知识导入测试阶段出现的 SQLite 连接回收、线程关闭警告。本轮未修复，未做完整基线归因，不宣称全部是历史警告。
5. 审计查询没有新索引，现有实现旁已留下 ponytail 限制与升级条件；实际出现查询性能问题再评估。没有「已核对」按钮；沿用 [SQLAlchemyRetentionRepository.AUDIT_RETENTION_DAYS](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/repositories/retention.py:42>) 的 365 天保留阈值，实际清理由既有清理流程执行，本轮未验证生产清理调度。
6. 审计失败时只有先写出的诊断日志保底，页面不会凭日志生成提醒；本轮不引入自动重试或通知补偿。
7. Spec §4 的“生产当前没有审批单”是前序历史陈述，本轮没有检查生产数据，不能作为当前事实或免验收依据。本报告以实际验证边界为准。
8. 最终 diff 未触及 [reply_gate.sh::REPLY_PATHS](</Volumes/02/obsidian codex/homestay-bot/scripts/release/reply_gate.sh:37>)，未运行真实模型回复门禁。版本仍为 1.64.0；本地通过不构成生产健康或发布授权。

## 6. 请 Claude 重点复核的事项

以下是审查重点，不是已经确认的新缺陷：

| 优先级 | 复核问题 | 证据入口 |
| --- | --- | --- |
| P1 | SQLite savepoint 回调是否正确取得实际驱动事务；成功 RELEASE 后外层回滚是否撤销；撞键回滚后外层能否继续，未给普通只读路径扩锁 | create_engine；两个新增 DB 行为用例 |
| P1 | 加锁复读、捕获旧轮次时间、当前对象刷新是否仍成立；两个丢弃出口能否覆盖新轮次状态或业务字段 | get_for_update；_same_round；AR6 / AR8 交错用例 |
| P1 | 审计实际写入后失败是否整体回滚、异常传播；日志格式化输出是否确实保留原请求 / 轮次 / 订单线索且无客人资料 | _record_late_result；record_late_result；故障注入用例 |
| P2 | success 后核验失败、错误信封、网络失败、恢复核验四种建单来源是否分别保留；400 / 503 是否仍被误判为未建单，是否引入重放 | confirm_and_create；_reconcile_or_mark_review；_mark_needs_review |
| P2 | action / target 查询、排序上限、字段白名单、模板转义及 ADMIN 限制是否完整；PENDING 与非 PENDING 都能看到提醒 | get_detail；approval_detail；detail.html；新浏览器完整文件 |
| P2 | 本地回归是否确实经过项目装配与引擎；专用 PostgreSQL 结果、跳过 / 警告及浏览器范围是否被准确描述 | SessionApprovalPageService._service；world；pg_engine；Spec §7 |
| P3 | 是否出现本轮引入的多余抽象、状态、依赖、回退分支；保留期与索引限制是否需要与 Spec 进一步对齐 | 完整 diff、新测试与当前 Spec |

请沿适用 AGENTS.md 只读审查：读取完整相关 diff、未跟踪的新测试与 Spec，追踪真实调用链，复用本报告仍覆盖最终内容的验证证据。只有发现覆盖缺口或可疑行为时，运行最小本地回归；真实外部调用、生产写入、提交、推送、部署不在本次审查授权内。不得读取受保护的项目总结文本。

请回交以下内容：

- 按 P1 / P2 / P3 排序的可行动发现：具体触发条件、实际后果、绝对文件路径与函数 / 模板符号、代码或验证依据；没有发现也明确说明。
- 对 F1、F2、F3、D4 及 SP1–SP6 的落实判断；区分真实实现缺陷、Spec 表述问题和已有范围外问题。
- 已验证、仅静态核对、未覆盖范围，以及是否建议进入下一步。审查结论不替代用户的提交 / 部署授权。

## 7. 交接快照与文档校验

五个业务实现文件的 SHA-256 与最终验证后的记录一致，可用于核对 Claude 接手时是否又发生变化：

~~~text
src/homestay_bot/db.py
4b1af8100173fac02d841dbb5fb0f669688344ddeb60d9b80aa057b2e2da9909
src/homestay_bot/repositories/approvals.py
539bd87e3d873091d01af97c3d1e8dc3edaa9575ea7057109b9f7bd2d3f7b898
src/homestay_bot/services/booking_service.py
33049d8cdfd3a9995f2eb1eb5460b606c8a8e8a0326ac6283cd8ef747e9d536c
src/homestay_bot/services/approval_page_service.py
b7b515d925d532e3e2b9cc8eca6fdd1375feab10b27f61725cfad5d77ba99132
src/homestay_bot/templates/approvals/detail.html
f8aac6ec23a1035918350324b3049b26e0216fdbc4975434883fd56091e75140
~~~

写报告前后核对源码、测试、Spec、todo 与旧报告的内容摘要，仅本报告新增。已检查报告绝对路径引用、行号、代码围栏与行尾空白，并运行 git diff --check；本次没有重新执行业务测试。
