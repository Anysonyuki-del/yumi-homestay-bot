# SQLite 保存点提交语义与迟到建单结果可追溯 Spec

日期：2026-10-02。状态：用户在 Codex 复审后回复「开始」；按本轮 SP5、SP6 更正完成本地实施与验证，未提交、推送或部署。

来源：`docs/releases/1.64.0.md`「风险与未覆盖」第 2、4 条；用户「23开始」。

## 1. 第一段：现状证据（已确认）

### 第 2 项：SQLite 下保存点提前提交

- 使用场景：生产用 PostgreSQL，不受影响。本地运行（`README.md`：默认 `homestay.db`）与经项目 `db.py::create_engine` 建引擎的测试受影响；另有 49 个测试文件直接调用 `create_async_engine`，不会获得引擎上注册的事件（Codex SP4 更正）。环境：aiosqlite 0.22.1、SQLAlchemy 2.0.51、Python 3.12。
- 机理：Python sqlite3 驱动的旧式事务模式只在 INSERT、UPDATE、DELETE 前隐式发 BEGIN，普通 SELECT 不开启物理事务。外层事务如果到建保存点时只做过查询，`SAVEPOINT` 就成了最外层事务，`RELEASE` 当场提交，外层随后回滚也撤不回。
- 本地复现，使用项目的 `create_engine` 与 `create_session_factory`：
  - 外层先 SELECT，保存点内插入一行，外层失败回滚：这一行**仍在库里**；
  - 外层先 INSERT，再走同样的流程：全部回滚，与 PostgreSQL 一致。
- 受影响的保存点共 13 处：
  - `repositories/credentials.py:136`、`jobs.py:86`、`conversations.py:75`、`conversations.py:355`、`complaints.py:137`、`operations.py:118`、`operations.py:1804`、`lifecycle_reminders.py:103`、`customers.py:99`；
  - `application.py` 中把 `session.begin_nested` 作为工厂传入的 3 处（4385、4409、4618），以及 `services/complaint_review_job.py` 的同类注入。

  多数用于「插入撞唯一键就回滚这一步、外层继续」的去重。是否在只读之后进入，按调用路径而定。
- 已有的个别绕行：1.64.0 的 A1 回填为此去掉了保存点，改为单次 flush（`approval_page_service.py::backfill_reservation` 旁有注释）。
- 原型验证（未改仓库）：在引擎上监听 SQLAlchemy 的 `savepoint` 事件。驱动尚未开启物理事务时（通过公开接口 `driver_connection.in_transaction` 判断），先补发一句 `BEGIN`。验证了四种组合：
  - 先查询后外层失败、先写入后外层失败：都全部回滚；
  - 先查询后正常提交、没有前置操作就正常提交：两次保存点（一次成功、一次撞键回滚）加后续写入，结果都与 PostgreSQL 一致。

### 第 3 项：迟到的旧一轮建单结果被丢弃时无从追查

- `services/booking_service.py::_same_round`：加锁后发现审批已不是本轮的「创建中」时，丢弃结果，只写一条 warning 日志。日志只有审批编号和当前状态，不含上游请求编号和匹配到的订单号。
- 丢弃发生在两个出口：
  - `_reconcile_or_mark_review`：建单返回成功（手里有 `request_id`），或者建单时网络出错、结果未知。此时可能已经算出唯一匹配的订单号 `matches`。
  - `_mark_needs_review`：建单返回 `HostexBusinessError`，或者写后查询失败、结果未知。
- 更正（Codex SP2）：`HostexBusinessError` 不等于「明确拒绝、没有订单」。`integrations/hostex_client.py::HostexClient._request` 把信封里所有非成功错误码都包成这个异常，包括 `TRANSIENT_ERROR_CODES`（429、500、502、503、504）这类服务端临时失败。项目没有任何已核实的契约能证明哪个错误码一定未建单。建单本身 `retry_safe=False`，最多发一次，不会重放。
- 「上游其实已建单」是唯一会造成实际损失的情况：百居易多出一单。本地只有 `record_external_call` 的外部调用记录，与审批编号、轮次和订单号没有关联，实际上无从追查。
- 范围外的相关发现：本轮之内（非迟到）的同一分支，把所有 `HostexBusinessError` 都写成「百居易明确拒绝创建请求」，临时错误时这句话不准确，见 D4。
- 现有的可见渠道：
  - 审计表 `AuditLog`：`actor_employee_id` 可为空，`details` 是 JSON。但审计页 `templates/admin/audits.html` 只显示时间、动作名和目标类型，不显示目标编号和详情；审批详情页也不读审计。只写审计，管理员实际看不到。
  - 审批详情页 `templates/approvals/detail.html` 是管理员处理这张审批的地方。
- 后台的 `recover_stale_creating` 只把超时的「创建中」转为需复核，不涉及丢弃。

## 2. 用户决定（2026-10-02）

- 第一次「按推荐写」：D1a、D2a、D3a。
- Codex 审查后「按推荐的来写spec」：D3 重新确认为 a（实际效果为所有丢弃都提醒），新增的 D4 选 a。
- Codex 复审后用户「开始」：采纳 SP5 的中性错误说明与 SP6 的新增提醒区隐私边界；不改变其他功能范围。

| 编号 | 问题 | 选项 | 推荐 |
| --- | --- | --- | --- |
| D1 | 第 2 项的修法 | a. 只在建保存点时补 BEGIN，其余 SQLite 行为不变<br>b. 按 SQLAlchemy 官方方案，所有事务一开始就发 BEGIN | a：只影响保存点路径。b 会让只读事务也持有读锁，本地并发写更容易「database is locked」，这个风险与本项无关 |
| D2 | 第 3 项记在哪里、怎么让人看到 | a. 写审计，并在审批详情页给管理员显示提醒：「上一轮建单结果迟到，百居易可能多出订单（请求编号 / 订单号），请到后台核对」<br>b. 只写审计<br>c. 在 a 的基础上，再发企业微信通知员工 | a：管理员处理审批时一定会看到。b 实际看不到。c 增加外部发送，需另行授权，范围也更大 |
| D3（按 SP2 重新确认：a） | 哪些丢弃需要提醒 | a. 只提醒「上游可能已建单」；但目前没有任何错误码被证实「一定未建单」，按 SP2，所有丢弃都属于「可能已建单」<br>b. 把非临时错误码（429、5xx 以外）当作明确拒绝，只记审计不提醒，这条判断未经契约验证 | a，实际效果是所有丢弃都提醒：迟到要求旧一轮卡住 5 分钟以上，极少发生，多提醒一次的代价只是人工核对一次；漏提醒的代价是多出一笔订单 |
| D4（本轮 SP5 更正） | 本轮之内错误信封不能证明订单未创建 | 所有 HostexBusinessError 均写「百居易返回错误，订单是否已创建待核验」，保留错误码，不推断明确拒绝 | 没有已核实的未建单契约；使用中性说明，A2 回退前的反查防重仍然有效 |

## 3. 第二段：功能点

### F1 SQLite 保存点前补开事务（D1a）

- 位置：`db.py::create_engine`，只在 SQLite 分支注册，与现有的外键开关并列。PostgreSQL 引擎不注册，行为不变。
- 做法：监听引擎的 `savepoint` 事件。事件参数是 `sqlalchemy.engine.Connection`，它没有直接的 `driver_connection`（Codex SP1 更正）；按公开接口层级 `connection.connection.driver_connection.in_transaction` 判断。驱动尚未开启物理事务时，先发一句 `BEGIN`，再建保存点；已经在事务里的不做任何事。
- 效果：保存点总是嵌在真实事务里。`RELEASE` 不再提交；外层提交、回滚的结果与 PostgreSQL 一致。13 处保存点调用方都不用改。
- 顺带：`approval_page_service.py::backfill_reservation` 旁「不用保存点」的注释，原因改为「单次 flush 更简单，且不依赖驱动的保存点语义」，代码不动。

### F2 迟到结果写日志与审计（D2a、D3、Codex SP2、SP3）

- 位置：`services/booking_service.py`。两个写回出口加锁后判定不属于本轮时：
  1. 先写一条结构化 warning 日志（扩充现有日志），作为审计失败时的诊断保底（SP3）；
  2. 再在同一加锁事务里写一条审计：动作 `booking_approval_late_result_discarded`，目标为这张审批，操作人为空（系统动作）。
- 日志与审计记同一组字段，不记客人姓名、手机号或上游请求正文：
  - `create_result`：建单阶段的结果，按调用阶段定，不按异常名推断：
    - `success`：建单返回成功；
    - `business_error`：建单返回错误信封，同时记 `error_code`；
    - `transport_error`：网络或协议错误；
    - `not_attempted`：超时恢复核验，本次请求没有建单。
  - `verify_result`：写后查询的结果，取 `matched`、`unmatched`、`failed` 或 `skipped`。两个阶段分开记，「建单成功后写后查询失败」时 `create_result` 仍是 `success`，请求编号保留（SP2 的优先级问题）。
  - `request_id`：建单成功的请求编号，或错误信封里的请求编号（SP2：业务错误分支现在会丢掉它，改为带进审计）。只进日志和审计，不改本轮之内写 `hostex_request_id` 的现有行为。
  - `reservation_codes`：写后查询匹配到的订单号，最多 20 个。
  - `round_approved_at`：被丢弃那一轮的确认时间。
  - `current_status`：丢弃时审批的现状态。
- 审计写入失败：事务回滚、异常上抛，不吞错；诊断线索已在第 1 步的日志里。
- 写入经由审批仓储新增的方法（`ApprovalRepository` 协议与 SQLAlchemy 实现），服务层不直接碰会话。按 D3a，不再设 `upstream_may_exist` 字段，所有丢弃都提醒。
- D4：`confirm_and_create` 的全部 `HostexBusinessError` 失败说明统一为「百居易返回错误，订单是否已创建待核验」，保留错误码，不再按是否为临时错误来推断一定未建单。

### F3 审批详情页提醒（D2a、D3a）

- 位置：`ApprovalPageService.get_detail` 读出这张审批的丢弃审计，按时间倒序，最多 10 条。`templates/approvals/detail.html` 把警示框放在各状态分支之外（第 13 行的状态判断之前），待审批、需复核、已预订等任何状态都能看到（Codex SP4：回到待审批后的页面走另一个分支）。
- 文案大意：「有一轮建单结果在审批状态改变后才返回，已被丢弃。百居易可能多出一笔订单，请到百居易后台核对，多余的订单需人工取消。」逐条列出时间、请求编号、匹配到的订单号；都没有时写「未取得」。
- 详情页本来只给管理员看（`routes/approvals.py::approval_detail` 校验 ADMIN），提醒随之只给管理员，沿用现有权限检查。按 D3a，所有丢弃都显示提醒；失败的建单同时显示错误码。

## 4. 第三段：风险与验证

| 风险 | 说明与处理 |
| --- | --- |
| F1 改变 SQLite 保存点路径 | 本地运行与经项目工厂建引擎的测试受影响；直接建引擎的测试不受影响，报告时分开说明，不为此批量迁移测试。改的是引擎配置，所以跑带临时 PostgreSQL 隔离库的本地全量。若有测试依赖了提前提交的旧行为而失败，逐条判断：是测试按错误语义写的就改测试，是业务代码借了这个行为就单独报告，不顺手改 |
| F1 的锁占用 | 只是让含保存点的事务从 BEGIN 起就是真实事务。BEGIN 是延迟型，首次读写前不加锁，对本地并发的影响限于这些事务本身。不采用 D1b，不含保存点的只读事务不受影响 |
| F1 依赖驱动接口 | `Connection.connection` 与 `driver_connection` 是 SQLAlchemy 公开接口，`in_transaction` 是 aiosqlite 与标准库 sqlite3 的公开属性。属性缺失时直接报错，不静默跳过 |
| F2 审计写入失败 | 与丢弃判定同处一个加锁事务，失败时整个事务回滚、异常上抛。丢弃路径本来就不改审批任何字段，回滚不会留下半截状态。页面报错本身不保存线索（Codex SP3 更正），线索靠事先写好的结构化日志；日志不能替代审计，管理员此时在详情页看不到提醒 |
| D3a 提醒偏多 | 没有已核实的「未建单」错误码，所有丢弃都提醒。ponytail：拿到百居易错误码契约后，再把被证实未建单的错误码改为只记审计 |
| F3 提醒不能消除 | 不做「已核对」按钮，提醒随审计在 365 天后被保留期清理。ponytail：如果提醒多到干扰，再加「已核对」标记 |
| F3 查询成本 | 审计表在目标列上没有索引，详情页每次打开都要按动作与目标过滤扫描一遍。审批详情访问频率低，可以接受。ponytail：审计表变大、详情页变慢时，再给 `(target_type, target_id)` 加索引 |
| 未覆盖 | 真实百居易的迟到场景无法在生产复现；生产当前没有审批单 |

验证计划：
- F1：
  - 引擎级回归：无前置操作、先查询、先写入三种入口，各自覆盖外层提交与回滚；每组都含一次撞唯一键回滚的保存点，之后外层继续写入。
  - 反向验证：去掉监听后，「先查询、保存点插入、外层回滚」应留下记录而失败。
  - 至少一个真实保存点仓储入口，用项目 `create_engine` 与真实会话，证明外层失败后不留记录、撞键后外层能继续（SP4）。
- F2：在现有 AR6、AR8 交错用例上补断言；补「建单返回 503 后迟到」「写后查询失败」「超时恢复」的丢弃用例；补审计写入故障注入：异常上抛、不留半条审计、审批状态不变、日志含请求编号与订单号且不含姓名、手机。PostgreSQL 组复用这些用例。
- D4：本轮之内建单返回 503 与 400 时，均使用中性失败说明，保留错误码，不重放建单。
- F3：路由与浏览器检查覆盖待审批与需复核两种状态都显示提醒；没有请求编号和订单号时显示「未取得」；新增警示区、日志和审计不含姓名、手机，原有预订资料区照常向管理员展示客人信息。
- 带临时 PostgreSQL 16 隔离库的本地全量；Ruff、mypy、`git diff --check`。报告写明哪些用例经过项目 SQLite 工厂、哪些直接建引擎。
- 真实模型门禁：以最终 diff 与 `REPLY_PATHS` 核对为准，预计不需要。

## 5. Codex Spec 审查答复（2026-10-02）

依据 `docs/reviews/2026-10-02_codex-to-claude-sqlite-savepoint-and-late-result-spec-review-handoff.md`。

| 编号 | 判断 | 依据 | 修订 |
| --- | --- | --- | --- |
| SP1 | 成立 | 原型实际用的是 `conn.connection.dbapi_connection` 下的私有属性，Spec 把路径简写成了 `connection.driver_connection`，这个属性在事件参数上不存在 | F1 写明事件参数类型与公开接口层级 |
| SP2 | 成立 | `hostex_client.py::HostexClient._request` 把所有非成功错误码（含 `TRANSIENT_ERROR_CODES`）包成 `HostexBusinessError`；`booking_service.py::confirm_and_create` 的该分支没有传 `error.request_id` | 现状更正；F2 按调用阶段分别记建单与核验结果，带上错误信封的请求编号；D3 重新确认为 a；新增 D4，选 a |
| SP3 | 成立 | 审计回滚后，请求编号、轮次、订单号只在内存里；现有日志只有审批编号与状态 | 修法比报告更简单：丢弃时总是先写含这些字段的结构化日志，再写审计；不单独为失败路径加分支 |
| SP4 | 成立 | 49 个测试文件直接调用 `create_async_engine`；`detail.html` 中待审批（第 13、19 行）与其他状态（第 35 行起）是不同分支 | 现状与风险改为「经项目工厂的引擎」；补真实仓储入口验证；提醒放在状态分支之外 |

## 6. 本轮实施计划（用户「开始」，2026-10-02）

1. db.py::create_engine：SQLite 保存点事件检查真实驱动事务并按需 BEGIN；tests/unit/test_db.py 验证六种事务组合、真实任务仓储的撞键继续与外层回滚。
2. booking_service.py::confirm_and_create、_reconcile_or_mark_review、_mark_needs_review：传递建单与核验阶段证据，在丢弃前记录诊断与审计；repositories/approvals.py::SQLAlchemyApprovalRepository 增加同事务审计方法。保留当前轮次和原有成功请求编号的持久化语义。
3. approval_page_service.py::get_detail 查询同审批最近 10 条丢弃审计；templates/approvals/detail.html 在状态分支之外展示提醒，不展示客人隐私。回填注释改为单次 flush 的简单事务边界，业务逻辑不变。
4. 复用并扩充审批交错用例、增加阶段失败与审计失败用例；PostgreSQL 复用真实仓储用例；浏览器通过真实会话门面、临时 SQLite 与真实路由核对提醒、权限和资料区边界。
5. F1 修改公共引擎配置，最终运行一次带临时 PostgreSQL 16 的全量、相关 Ruff、mypy 与 diff 检查；真实模型和外部消息不在本次授权范围内。

## 7. 本地实施与验证记录（2026-10-02）

基线：main，HEAD 53d369d29bd11b03ff796ab2030daffccee06c70；没有新增提交。开始前业务工作区干净，已有本 Spec 与上一份 Codex 交接报告未跟踪；保留原交接报告不改写。

实施结果：
- db.py::create_engine 增加仅 SQLite 的保存点监听，使用公开驱动连接层级；首次保存点前按需 BEGIN，已处于物理事务时不重复开启。
- BookingService.confirm_and_create、_reconcile_or_mark_review、_mark_needs_review 传递建单与核验阶段；_record_late_result 先写含审批编号与旧轮次的诊断日志，再通过 SQLAlchemyApprovalRepository.record_late_result 在同一加锁事务内刷新审计。保留旧轮次、新状态、请求编号、最多 20 个匹配订单号；错误信封采用中性说明，成功请求编号的原有回填语义不变。
- ApprovalPageService.get_detail 仅查询当前审批的指定审计动作，按时间、编号倒序限 10 条，给模板只提供提醒必需字段。approvals/detail.html 在状态分支之外显示警示；沿用 approval_detail 的 ADMIN 校验。原预订资料区继续展示已授权管理员需要的姓名、手机。
- 不增加依赖、数据迁移、索引或通知任务；不添加已核对按钮或审计重试队列。审计失败会回滚并抛错，日志仅作为诊断保底。

验证证据：
| 范围 | 命令 / 入口 | 结果 |
| --- | --- | --- |
| 保存点缺陷判别力 | pytest tests/unit/test_db.py | 修复前 3 项回滚残留失败、7 passed；修复后 10 passed |
| 迟到审计与中性说明判别力 | test_approval_review_actions.py 中旧轮次、阶段保留、审计失败与错误码用例 | 修复前 9 项失败；实际缺少审计、缺少诊断保底以及误称明确拒绝均被捕获 |
| 相关审批回归 | pytest tests/integration/test_approval_review_actions.py tests/integration/test_approval_flow.py tests/integration/test_approval_repository.py | 54 passed；随后在 AR8 中补了旧轮次时间和正常新结果不重复登记的断言，已包含在最终全量 |
| 页面与公共引擎目标回归 | pytest tests/unit/test_db.py tests/browser/test_approval_late_results.py tests/unit/test_approval_page_service.py tests/unit/test_approval_page_degradation.py tests/integration/test_approval_routes.py | 38 passed；真实服务门面和路由、临时 SQLite，Chromium 390px 验证两种状态、倒序上限、空标识、权限、隐私与无横向溢出 |
| 最终全量与 PostgreSQL | RUN_LIVE_CONTRACT_TESTS=0 YUMI_TEST_POSTGRES_URL=<本次临时库> .venv/bin/python -m pytest -q --tb=short | 2440 passed、15 skipped、10 warnings，87.22 秒；临时 PostgreSQL 16 独立角色与专用库，先迁移到 0034_wecom_sync_cursors；审批交错、阶段保留和审计回滚用例在 PostgreSQL 上实际执行，运行后停止集群 |
| 静态检查 | Ruff 检查 src 与本次受影响测试；mypy src/homestay_bot；git diff --check | 通过；mypy 检查 134 个源文件。另行检查未跟踪的新测试与 Spec 的行尾空白 |

引擎覆盖边界：test_db.py、test_approval_review_actions.py 的 SQLite world，以及新增 browser/test_approval_late_results.py 使用项目 create_engine；PostgreSQL 组复用真实仓储行为用例。另有 49 个测试文件直接调用 create_async_engine，未批量迁移；全量通过不代表这些 SQLite 引擎获得了新监听。

验证过程中修正了新增浏览器夹具与 Playwright 同步事件循环的冲突，临时数据库初始化和清理使用独立线程；已有参考数据降级会话替身补充空审计查询，实际审计查询仍由真实服务回归覆盖。未为这些测试适配增加业务兼容分支。

警告与未覆盖：10 条警告包括 Starlette、Alembic 弃用提示，以及 FAQ / 知识导入相关测试阶段出现的 SQLite 连接回收、线程关闭警告；未宣称已修复。15 个跳过项全部为真实 DeepSeek、Hostex、企业微信契约，未执行。浏览器检查使用真实路由返回的 HTML 与项目样式，没有验证生产站点或真实上游。未验证文件 SQLite 的完整并发压力与真实迟到建单契约。

核对最终 diff 后，没有 REPLY_PATHS 清单内文件改动，未运行真实模型门禁。没有调用真实外部服务、发消息或写生产数据；版本仍为 1.64.0，不包含提交、推送、部署或版本发布。
