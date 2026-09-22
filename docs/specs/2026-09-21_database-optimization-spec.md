# YuMi 数据库可靠性与 RAG 检索优化 Spec

日期：2026-09-21  
状态：R4，保留清理可靠性修复，新增独立 RAG 优化阶段，待用户确认并明确“开始”；本文件不表示已授权生产操作。  
源码基线：`4e348ce8253c2bcbb8b208e0e86c727674900eb2`，`pyproject.toml` 版本 `1.36.3`。执行前重新核对，漂移时以当前代码重建受影响证据。  
技术栈：Python、SQLAlchemy AsyncSession、asyncpg、Alembic、PostgreSQL 16；SQLite 保留本地兼容用途。

### R4 决策摘要

- PostgreSQL 验收采用路径（c）：允许交付实现及可执行测试，但标注“PostgreSQL 未验收”；不安装全局数据库、不推分支触发 CI、不访问生产试跑。未补齐 PG 证据前不通过发布验收。
- 固定自动清理使用 `FOR UPDATE SKIP LOCKED`、带资格条件的 `DELETE ... RETURNING id`，据实际删除集合登记附件清理。
- 删除无效的 `delete_file` 与清理循环 `storage` 参数；测试角色固定为 `yumi_test`。
- 根据用户本轮要求，人工批量删除竞态与删除墓碑覆盖缺口从 R2 的“仅记录”提升为 A2 的正式修复项，详见第 5.4、5.5 节；与附件事务保障一起实施和验收。
- 新增 C 阶段：先做无外联检索评估与最小修复（C0/C1），pgvector 混合检索（C2）需测量证明必要、配置和迁移方案确认后再实施；不以向量库替换业务数据库。
- A 与 C 分阶段交付，验收独立，避免把清理修复与模型/向量依赖绑在一起。保留用户确认和“开始”门禁；R4 文档更新本身不表示开始实现。

## 1. 目标与方案决策

在现有 PostgreSQL 上修复历史清理可靠性问题，限制清理事务规模，并提升已审核民宿知识的检索质量。数据库升级、硬件扩容与知识检索分别测量和验收：A 处理清理正确性，B 为条件式数据库调优，C 为 RAG 检索评估与改造。

选择“先修正确性，再用测量决定性能改动”。其余方案：

| 方案 | 判断 |
| --- | --- |
| 直接升级数据库大版本或服务器 | 无运行态瓶颈证据，不能保证解决已发现的问题，本轮不执行 |
| 引入 Redis、独立向量库、分库分表 | 增加状态同步与运维成本，本轮不执行 |
| 保留 PostgreSQL，修复清理、建立基线 | 本轮采用；不需要数据库结构迁移或新增生产依赖 |

PostgreSQL 16 仍处支持期；保持其小版本维护，但先核对服务器实际版本、扩展和备份恢复能力。`postgres:16-alpine` 是镜像标签，不能证明正在运行的补丁版本，也不能代替镜像内容记录。

参考：[PostgreSQL 版本政策](https://www.postgresql.org/support/versioning/)、[pg_stat_statements](https://www.postgresql.org/docs/16/pgstatstatements.html)、[pgvector](https://github.com/pgvector/pgvector)。执行升级时重新核对官方信息。

## 2. 当前证据与边界

以下路径均相对仓库根目录。此处为源码证据，不代表线上已发生故障。

| 编号 | 代码事实 | 依据 |
| --- | --- | --- |
| E1 | 部署配置选择 PostgreSQL 16；引擎启用连接健康检查，未显式设置连接池容量 | `compose.yaml::services.postgres`；`src/homestay_bot/db.py::create_engine` |
| E2 | 消息、订单、任务领取等已有复合索引 | `migrations/versions/0014_query_indexes.py::upgrade`、`0016_admin_dashboard_indexes.py::upgrade`；`domain/models.py::Job` |
| E3 | 队列使用行锁和 SKIP LOCKED，处理任务前提交领取状态 | `repositories/jobs.py::SQLAlchemyJobRepository.claim_next`；`worker.py::Worker.run_once`；`application.py::_run_worker_loop` |
| E4 | 通用清理没有批量上限；归档清理一次加载全部到期任务及附件 | `repositories/retention.py::SQLAlchemyRetentionRepository.purge/purge_archived_tasks` |
| E5 | 自动清理与人工批量删除都把任务 ID 列表拼入去重键，字段上限 128 字符 | `repositories/retention.py::purge_archived_tasks`；`services/task_page_service.py::TaskPageService.purge_many`；`domain/models.py::Job.dedupe_key` |
| E6 | 所有通用清理和归档清理在同一事务最后统一提交 | `application.py::_run_retention_loop` |
| E7 | 附件清理已有提交后执行的持久化任务，不应再实现一套 | `application.py::_build_attachment_cleanup_handler`；`services/task_page_service.py::_schedule_attachment_cleanup` |
| E8 | 待处理事项计数先生成完整列表再求长度 | `repositories/admin_operations.py::SQLAlchemyAdminOperationsRepository.count_attention` |
| E9 | 知识检索全量读取启用条目并在 Python 评分；答案截取前 1,200 字；另有主题证据校验 | `repositories/knowledge.py::list_active`；`services/knowledge_service.py::retrieve`；`integrations/deepseek_client.py::_has_relevant_property_knowledge` |
| E10 | 现有清理集成测试使用 SQLite；CI 的现有迁移步骤也是 SQLite | `tests/integration/test_retention_repository.py`；`.github/workflows/ci.yml` |
| E11 | 人工批量删除未在资格读取时锁行，DELETE 只按 ID；恢复归档会加行锁 | `repositories/operations.py::require_purgeable/purge_selected/restore_task` |
| E12 | 单条删除写墓碑，人工批量与自动清理未写；当前只有周转创建读取墓碑 | `repositories/operations.py::purge_task/_mark_purged/_purged_mark/create_turnover`；`repositories/retention.py::purge_archived_tasks` |

已完成的最小构造检查：自动清理键包含 40 个五位数 ID 时为 254 字符，超过模型字段的 128 字符。人工批量入口有相同结构风险。尚未在 PostgreSQL 上复现实际写入错误，实施时补齐。

影响推导：自动清理前缀长 14 字符，n 个五位数 ID 对应 `13 + 6n` 字符，20 个即为 133 字符。当到期批次达到此长度且包含附件时，PG 将拒绝清理 job 写入；当前代码异常向外传播，使该轮其他清理一起回滚。符合条件的记录会保留到下一天，错误可能持续重复。这是源码推导，不能据此宣称生产已有积压。

异常分层：asyncpg 的 `StringDataRightTruncationError` 属于驱动 `DataError`，SQLSTATE 为 `22001`；当前本地 SQLAlchemy asyncpg 适配器将其按 `PostgresError` 转成通用适配器 Error，应用层可能看到 `DBAPIError`，不能要求日志 `error_type` 必须等于 `DataError`。`enqueue` 仅特殊处理 `IntegrityError`，不处理此长度异常；保存点仍会回滚自己的写入，外层清理因异常未处理而退出并回滚。不得把“不在捕获范围”误写成“保存点完全不生效”。

尚未取得：生产版本、Alembic revision、实际索引、数据库大小、增长率、慢 SQL、连接池等待、锁等待、CPU/内存/磁盘压力、当前备份可恢复性。不得把这些缺口写成已确认故障，也不得用历史部署记录补作当前验收。

## 3. 实施范围与授权

### A：本轮实施包

1. 自动归档清理与人工批量删除使用有界、稳定的附件清理去重键。
2. 自动历史清理有批量上限、分批提交、可恢复执行；不改变保留期限与资格条件。
3. 最小 SQLite 回归，编写真实 PostgreSQL 回归与既有 CI 的隔离 PostgreSQL 步骤；按路径（c）交付时 PG 执行证据保留为未验收。
4. 完成本文件规定的基线采集准备；仅在目标环境已明确授权时执行采集。
5. 人工批量永久删除在同一事务内锁定、校验并删除整批任务，解决与恢复归档的竞态。
6. 单条、人工批量和自动归档清理统一登记删除墓碑，使已有周转保洁防重建规则覆盖三个删除入口；不扩大其他来源的抑制规则。

### B：取得测量证据后另行确认

优化 `count_attention`、增加或替换索引、调整连接池、启用 pg_stat_statements、调整数据库参数、升级小版本或扩容。A 的代码交付不得以 B 未获授权或无线上指标为由无限等待；分开报告状态。知识库优化统一移至第 9 节 C 阶段，避免两处执行边界矛盾。

### C：独立 RAG 优化阶段

用户确认 R4 并要求开始后，默认先交付 A，再执行 C0/C1 的合成评估、现有检索与证据校验修复；A 的 PG 环境缺口不阻塞 C0/C1 离线验证。C2 是有明确准入条件的设计建议，不包含在笼统“开始”的默认范围内。

### 共同不做

不改客户记忆、订单、实时房态价格或人工业务决策；不改变手动批量删除的全有或全无语义；不改数据库字段长度来掩盖无界去重键；不做全库 JSON→JSONB 转换、分区、读写分离或任务队列替换。C 只修改知识检索、相关证据判断及必要的局部提示词约束，不改无关回复风格、投诉或交易策略。

用户确认本 Spec 并明确要求“开始”后，Claude 才实施 A。当前编写 Spec 的请求不授权提交、推送、部署、生产写入、真实外部消息或模型调用。测试数据全部合成，禁止读取受保护的未跟踪项目总结文件。

## 4. A1：统一附件清理批次键

### 文件与落点

- 修改 `src/homestay_bot/services/task_page_service.py`：在现有 `ATTACHMENT_CLEANUP_JOB_TYPE` 附近增加一个小型模块函数，供 `TaskPageService.purge_many` 与自动清理共用。
- 修改 `src/homestay_bot/repositories/retention.py::purge_archived_tasks`：调用同一函数。该模块已依赖上述服务模块的常量，不再创建工具包或通用哈希框架。
- 不修改 `SQLAlchemyJobRepository.enqueue` 的全局键语义；不改 `Job.dedupe_key` 字段。

### 契约

建议函数名 `build_attachment_cleanup_dedupe_key(task_ids, *, source)`；`source` 仅接受内部固定值 `retention` 或 `manual`。

算法固定为：任务整数 ID 去重、数值排序，以英文逗号连接，UTF-8 编码，使用标准库 SHA-256 完整十六进制摘要，加固定来源前缀。示例格式：`task-retention-batch:<64位摘要>`、`task-manual-batch:<64位摘要>`。

要求：

- 同一来源、同一 ID 集合产生相同键，不受输入顺序和重复 ID 影响。
- 不同来源隔离；大批量 ID 也不超过 128 字符。
- 只在本次数据库操作成功确认的任务集合上登记清理任务；无附件时不创建空清理任务。
- 单条删除现有 `task-purge:<id>` 保持原状；不重写历史 pending/running/completed 清理任务的键或载荷。
- 旧版本未提交成功的超长键不会成为要迁移的记录。已提交的旧格式任务继续由既有 worker 处理。
- 数据库删除、附件行处理与清理任务登记仍在同一事务；事务失败则三者一起回滚。
- 不因改为摘要而扩大人工删除权限、跳过 `require_purgeable`、改变去重唯一约束或吞掉数据库错误。

## 5. A2：有界清理、删除并发与墓碑一致性

### 5.1 仓储只处理一批，不自行 commit

保留现有两个方法和返回结构，可增加仅供内部调用和测试使用的正整数 `batch_size` 参数，不增加管理台配置项：

| 方法 | 默认批量 | 单次返回 |
| --- | --- | --- |
| `purge` | 每类 500 条 | 保留现有五项计数字典 |
| `purge_archived_tasks` | 100 个父任务 | 实际删除父任务数 |

`purge` 的五类为审批 PII 更新、终态 jobs 删除、external_requests 删除、非 pending webhook 事件删除、审计日志删除。每一类先按原资格条件和主键顺序选取至多 `batch_size` 个 ID，再更新/删除这些 ID；修改语句仍包含原资格条件，不能只凭旧 ID 快照误操作状态已变化的行。

不得简单给 SQL DELETE 拼接 LIMIT；使用 SQLAlchemy 兼容 PostgreSQL/SQLite 的候选 ID 查询与有界 DML。负数或零批量应明确拒绝，不能意外变成无上限。选择 ID、执行修改、提交必须使用同一批事务。

`purge_archived_tasks` 使用原归档到期条件，按任务 ID 排序选取一批，固定使用 SQLAlchemy `.with_for_update(skip_locked=True)`，在 PostgreSQL 选择阶段锁住父任务到本批事务结束。SQLite 保留兼容执行，但不作为行锁证据。返回不足一批仅作为本阶段停止继续取批的信号，不能据此宣布全库没有积压；并发状态变化与被跳过的锁定行都可能使本轮处理数减少。

锁语义必须准确：管理员先锁住某行时，清理跳过该行；清理先锁住某行时，管理员恢复可能等待本批提交。SKIP LOCKED 不是“管理员永不阻塞”的保证，也不跳过所有表级锁。使用短批事务限制锁持有时间，不以取消锁来换取表面无等待。

随后读取本批附件的 `(task_id, private_file_id)` 映射，在同一事务中执行带原资格条件和候选 ID 条件的 `DELETE ... RETURNING id, dedupe_key`。原条件至少包含 `archived_at IS NOT NULL` 与 `archived_at < cutoff`，cutoff 使用本轮固定 now。收集实际返回的任务 ID 和业务 dedupe_key，仅给这些任务登记第 5.5 节的墓碑及其附件清理 job，附件清理摘要键也只使用实际删除的 ID 集合。空返回不登记任务或墓碑。附件映射必须在删除前读取，以免级联删除后丢失文件编号；禁止在删除之前操作磁盘。计数取返回 ID 的数量，不取候选数量。

针对 PostgreSQL 使用两个会话验证两个锁顺序：管理员恢复先成功时清理不删除；清理先持锁时恢复只能等待后观察提交结果，不能报告已恢复一个已删除任务。另覆盖管理员持锁未提交时清理跳过，并能继续处理另一条未锁定合格任务。

批量上限限制的是父任务数量，不代表附件行数和 payload 字节数有严格上限。本轮不为此引入分片协议；基线记录单批附件数量和最大清理载荷规模，发现现有业务允许极端附件扇出时报告证据并补充设计，不宣称已解决所有内存上限问题。

保持所有原保留期与比较符：jobs 30 天、外部请求/webhook 90 天、审计 365 天、已完成预订 PII 30 天、拒绝/冲突审批 PII 90 天、归档任务 180 天。重点区分代码现有 `<` 与 `<=`，不得用“满多少天”的自然语言偷偷改变边界。

### 5.2 调度拥有事务边界

修改 `application.py::_run_retention_loop`，保持启动后执行和每日 86,400 秒间隔，不另建调度器。

一轮开始固定一个 UTC `now`，整个轮次复用，避免循环过程中保留期边界漂移。分成两个独立阶段：

1. 通用清理：每批创建短会话，调用 `purge`，提交成功后累加计数；如果全部计数都小于该类批量上限则结束阶段，否则继续下一批。
2. 归档任务清理：每批创建短会话，调用 `purge_archived_tasks`，提交成功后累加计数；不足一批则结束阶段，否则继续。

两个阶段各最多执行 20 批，使用代码常量，不新增环境变量。达到上限且末批仍满时记录 `possibly_incomplete`，下一次每日调度继续；这只是限制单轮工作量，不能声称已精确证明仍有积压。该保守上限应留 `ponytail:` 中文注释：稳定出现截断或超出清理窗口时，再依据统计调整频率/批次，不在本轮搭建自适应调度。

旧故障若曾阻断清理，修复后积压可能需要多天消化：当前上限下每类通用记录最多 10,000 条/轮、归档父任务最多 2,000 个/轮，这是理论容量而非实际吞吐承诺。首轮触达上限属于设计内行为，不能仅据此判失败；连续多轮积压增长或 PII 清理延迟应记录为容量/保留期风险，再决定是否调整上限，不临时绕过安全边界执行无界删除。

一个阶段数据库异常：回滚当前批，记录阶段和异常类型，结束该阶段；另一个阶段使用新会话仍可执行。此前已提交批次保留，不伪装全轮原子性，也不撤销有效进度。`CancelledError` 必须继续抛出，保证停机响应。

只统计成功提交的批次。日志记录轮次耗时、阶段耗时、各表数量、批次数、失败阶段和是否触达上限；不记录 SQL 参数、密文、任务正文、附件编号列表或连接串。

确定移除 `purge_archived_tasks` 中从未使用的 `delete_file` 参数，以及 `_run_retention_loop` 中随之无用的 `storage` 参数；同步仓储调用、应用启动调用和 `test_application.py` 的 stub。全仓检索调用方确认没有遗漏；`test_runtime_startup.py` 的通用 `*args, **kwargs` stub 不必机械修改。仅清理因此成为孤儿的导入，不能留下“传入回调却从未被调用”的虚假测试保障。不改附件 worker 的存储接口。

### 5.3 明确接受的行为变化

自动清理从“整轮一起成功或回滚”变为“每批独立成功或回滚”。不同历史表之间没有本轮要求的全轮原子性；附件所属任务删除与清理任务登记的原子性不可拆开。人工批量删除依旧保持单次操作事务，不套用自动分批提交。

旧 `tests/unit/test_application.py::test_retention_loop_purges_and_commits_daily` 将整轮一个 commit 写成断言，须按新事务契约更新；不能为保住该断言而保留无界大事务。

### 5.4 人工批量永久删除与恢复归档的竞态（原 D1）

修改 `repositories/operations.py::require_purgeable/purge_selected`，沿用 `TaskPageService.purge_many` 和 `application.py::SessionTaskPageService.purge_many` 的单事务调用链，不新建删除服务或把锁放进进程内存。

明确流程：

1. 管理员身份校验仍在业务服务入口，早于删除；选中 ID 去重并按数值排序。
2. `require_purgeable` 按 ID 排序、在 PostgreSQL 使用普通 `FOR UPDATE` 锁住所选父任务，锁持有到外层提交或回滚。**人工批量入口不用 SKIP LOCKED**，否则会把用户明确选择的一整批静默删成一部分。
3. 在锁取得后重新读取当前归档状态并验证完整集合。查询应刷新会话中可能已有的 ORM 实例（例如 `populate_existing=True`），不能锁住新行版本后仍依赖 identity map 的旧 `archived_at`。空集合、缺失任务或任一未归档任务，拒绝整批，维持既有异常类型与路由错误处理。
4. 仅在锁定和校验成功之后读取附件编号。服务随后调用 `purge_selected` 时仍是同一个 session、同一个事务，不允许中途 commit。仓储直接调用也必须执行锁定校验，不只依靠路由预检。
5. DELETE 同时带选中 ID 与 `archived_at IS NOT NULL`，使用 `RETURNING id, dedupe_key`。返回 ID 集合必须等于归一化选择集合；若不相等，抛出异常并由外层回滚整批，禁止把部分删除当作成功返回。附件 job、墓碑和审计计数仅对应最终成功集合。
6. 审计、墓碑、父任务/附件行删除、附件清理 job 登记同事务提交。任一步失败全部回滚；不改变人工批量操作的全有或全无语义。

锁顺序统一按任务 ID 升序，避免两个重叠批次逆序取锁；单条删除与恢复继续使用自身行锁。不要扩展为全表锁、全局互斥或覆盖外部调用的长事务。清理与人工删除竞争时，自动清理仍按第 5.1 节跳过人工已锁的任务。

并发结果必须明确：恢复先提交，则批量删除读取到未归档状态，整批拒绝；删除先锁定并提交，则恢复等待后得到任务不存在，不能返回成功恢复。已有“同一事务多次调用 require_purgeable”的少量重复查询可保留，避免为省一次查询改动所有接口；只消除本次直接产生的重复或孤儿代码。

修改上述函数时同步修正“校验与删除之间已删除磁盘文件”等失效注释，明确物理文件只由提交后的 worker 删除。无需重写无关路由、列表或权限机制。

### 5.5 三种删除入口的墓碑一致性（原 D2）

复用现有 `PurgedTaskMark` 表及 180 天保留语义，不新增表或迁移。墓碑保存的是 **BusinessTask 原有业务 dedupe_key**，不是第 4 节附件清理 job 的摘要键；两者职责不能混用。

| 删除入口 | 本轮要求 |
| --- | --- |
| `SQLAlchemyOperationsRepository.purge_task` | 保留单条锁定/鉴权/审计行为，复用下面统一墓碑写入方法 |
| `SQLAlchemyOperationsRepository.purge_selected` | 仅为实际成功删除集合中的非空业务 dedupe_key 登记墓碑；失败整批回滚 |
| `SQLAlchemyRetentionRepository.purge_archived_tasks` | 根据 RETURNING 的实际结果登记墓碑；与当前批删除、附件 job 一起提交 |

在现有 `repositories/operations.py::SQLAlchemyOperationsRepository` 内提供一个窄用途的共享批量方法，例如 `mark_purged_many(dedupe_keys, *, now)`，供三处直接复用；不创建通用仓储层。现有私有 `_mark_purged` 可改成单键委托，若没有其他调用方也可由统一方法直接替代。自动清理仓储可通过同一 AsyncSession 的 operations 仓储调用它，不能另开事务。检查并避免循环导入。

写入契约：

- 忽略 None/空业务键，对非空键去重排序；输入为空直接返回。
- 新墓碑时间使用实际删除所在轮次/操作的 UTC now。自动清理使用本轮固定 now，**不能使用 180 天前的 archived_at**，否则刚写入即过期。
- 同一键已有墓碑则刷新，保持唯一约束。优先使用 SQLAlchemy 已安装的 PostgreSQL/SQLite 方言原生 `ON CONFLICT DO UPDATE`，不添加依赖；更新时间不得倒退为早于已有 `purged_at` 的值。
- 不得通过外层 session.rollback 吞掉唯一键冲突并丢弃同事务业务写入。墓碑写入失败必须使本次删除事务失败，不能删除成功却静默漏记。
- 含附件、无附件的系统任务都要登记；人工创建且没有 dedupe_key 的任务不生成墓碑。
- 原 `_purged_mark` 保留严格 `< cutoff` 的过期判断。清除过期墓碑时应带回到期谓词，避免先读取到过期值、随后误删被另一事务刷新的墓碑；使用 fresh 查询/锁或等价条件化删除确认结果，不能基于旧对象无条件删除。

**防重建范围固定为现有业务语义：**`create_turnover` 当前读取墓碑，本轮保证通过单条、批量、自动三种入口删除的周转任务，提交后相同房间/服务日再次同步均不重建，过期后允许重建；不同房间或服务日不受影响。复用已有 `test_purged_turnover_is_not_recreated_by_the_next_sync` 与 `test_purge_mark_expires_and_stops_blocking` 扩展入口覆盖。

`create_manual_contact_for_reminder`、`create_credential_failure_review`、`create_credential_review` 当前没有墓碑抑制逻辑。本轮对其已删除业务键一致写入墓碑，但不自动改变这些创建入口的返回类型、调用方流程或是否生成必要人工待办的规则。验收报告不能写“所有系统任务永不重建”。若需要扩展读取墓碑的来源，应另行确认经营/安全语义；本轮不以全局过滤吞掉凭证失败或入住复核任务。

尚未有墓碑的历史删除记录不能从空表反推出完整来源，不做猜测性回填。此次修复从后续成功删除事务开始生效。墓碑不永久保存任务正文、照片或客户身份。

## 6. A3：PostgreSQL 验收与最小回归

### 修改范围

| 文件 | 内容 |
| --- | --- |
| `tests/integration/test_retention_repository.py` | 保留现有边界覆盖，补充多批、资格保护、事务回滚 |
| `tests/integration/test_operations_repository.py` | 三种删除入口的墓碑生命周期、整批原子性、直接仓储调用资格保护 |
| `tests/unit/test_task_page_service.py` | 两批量入口共享键契约；人工权限与事务行为不变 |
| `tests/unit/test_application.py` | 独立提交、异常隔离、批次上限、固定 now、每日间隔 |
| `tests/integration/test_retention_postgresql.py`（新增） | PostgreSQL 字段约束、真实提交/回滚、自动/人工锁竞态、墓碑并发写入的最小集成验证 |
| `.github/workflows/ci.yml` | 添加独立测试 PostgreSQL 服务、迁移步骤和新测试入口；保留既有 SQLite 验证 |

使用已安装 asyncpg/SQLAlchemy/pytest，不增加测试容器框架。PostgreSQL 测试库必须隔离，使用专用测试角色与库；禁止自动回退到应用的 `DATABASE_URL` 或加载生产 `.env`。

固定专用环境变量 `YUMI_TEST_POSTGRES_URL`：仅接受 `postgresql+asyncpg`、主机字面值 `127.0.0.1`/`localhost`/`::1`、数据库名 `yumi_test_retention`、角色名 `yumi_test`；URL 查询参数不得覆盖实际连接目标，不符合条件时在连接之前拒绝。CI 提供合成测试凭据与独立 PostgreSQL 16 服务，通过 Alembic 将该测试库迁移至当前 head。测试凭据不复用生产角色；不得自动连接非测试数据库。清理测试数据只允许在通过上述校验的测试库内进行。

缺少此变量时，普通 SQLite 测试运行可明确 skip PostgreSQL 文件；正式验收命令必须先验证变量存在，并报告 PostgreSQL 用例实际执行数量，不能把 skip 计为通过。Docker/PostgreSQL 不可用时，继续完成代码与 SQLite 验证，交接标注“PostgreSQL 未验收”，不扩大为本机全局数据库安装或生产试跑。

本次核对 `command -v docker/postgres/psql` 均未发现可用命令；这只证明当前 PATH 中不可用，不宣称已枚举全机所有安装。当前 CI 同时订阅 push 和 pull_request，但没有外部操作授权。路径（c）允许完成 PG 测试代码与 CI 配置并交付审核，不允许把未实际执行的 T1/T9/T12/T17 或 CI 标成通过。以后用户另行授权推送测试分支或提供隔离 PG 环境后补验；PG 核心用例未通过前，A3 标记“测试准备完成、PG 验收未完成”，不得宣布 A 全部验收完成或通过发布门禁。

### 验收矩阵

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| T1 | 40 个五位数任务 ID 且带附件 | 旧路径可复现超长键；修复后 PG 写入成功，键不超过 128 |
| T2 | 集合顺序变化/重复 ID/不同来源 | 同集合相同键，来源隔离；验证实际自动和人工入口使用此键 |
| T3 | 合格记录超过两批 | 单次不超父行限额；循环无漏处理/重复登记，使用小测试批量即可 |
| T4 | 各保留期临界值、pending/running job、未归档任务、近期记录 | 原有 `<`/`<=` 与状态保护不变；不增加自动删除资格 |
| T5 | 删除后登记清理任务失败或事务回滚 | 任务及附件行恢复，无已提交清理 job，无磁盘删除调用 |
| T6 | 首批已提交、第二批失败 | 首批保持，第二批回滚；下轮可续跑，计数只包含提交批 |
| T7 | 通用清理失败或归档清理失败 | 不污染另一阶段的新会话；原异常可定位 |
| T8 | 达到批次上限、取消任务、时间跨越边界 | 有截断提示；取消及时传播；一轮固定 now；仍按日调度 |
| T9 | 两个 PG 会话按不同顺序清理/恢复归档 | 管理员先持锁时清理跳过且继续其他任务；已恢复任务不删除；清理先持锁时恢复等待并正确处理删除结果 |
| T10 | 已提交旧格式清理 job、文件重复删除 | 旧任务可继续处理；重复执行复用已有幂等 worker 行为 |
| T11 | 无授权人工批量删除 | 仍被拒绝；不因键生成或批量逻辑改变鉴权/资格判定 |
| T12 | PG 人工批量删除与恢复归档交错 | 恢复先成功则整批拒绝；删除先锁则恢复等待后正确报不存在；含会话预加载旧归档状态的情况 |
| T13 | 批量中混入缺失/未归档任务，或返回删除集合不完整 | 删除、墓碑、附件 job 和成功审计全回滚；重复 ID 不重复计数 |
| T14 | 单条/人工批量/自动清理删除带业务键任务 | 三入口都写原业务键墓碑；无附件也写；无键人工任务不写；失败不留下新墓碑 |
| T15 | 三入口删除周转任务后重新调用 create_turnover | 保留期内相同来源不重建；不同房间/服务日正常创建；提交后用新会话验证 |
| T16 | 自动清理旧任务、墓碑临界值与刷新 | 时间取删除轮次 now；严格过期边界不变；过期可重建；刷新时间不倒退 |
| T17 | PG 同键墓碑并发 upsert/过期清理与刷新竞争 | 唯一行、时间不倒退；过期清理不能误删已刷新的有效标记，失败不回滚其他已提交批次 |
| T18 | 凭证失败与生命周期人工任务创建回归 | 不因引入统一写墓碑而吞掉必要人工待办，不改变现有创建接口与审批流程 |

复用已有测试并补足缺口，不为每个步骤复制一套测试框架。回滚文件测试必须用实际 spy/临时文件或可观察 worker 执行；现有仅定义空列表但没有接入调用链的断言，不能证明磁盘未删除。

建议本地命令，使用项目实际 Python 环境：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_retention_repository.py tests/integration/test_operations_repository.py tests/unit/test_task_page_service.py tests/unit/test_application.py tests/integration/test_jobs.py tests/integration/test_task_routes.py
.venv/bin/python -m ruff check src/homestay_bot/repositories/retention.py src/homestay_bot/repositories/operations.py src/homestay_bot/services/task_page_service.py src/homestay_bot/application.py tests/integration/test_retention_repository.py tests/integration/test_operations_repository.py tests/integration/test_retention_postgresql.py tests/unit/test_task_page_service.py tests/unit/test_application.py
.venv/bin/python -m mypy
git diff --check
```

独立 PostgreSQL 环境准备完毕后执行，不能使用真实业务连接串：

```sh
.venv/bin/python -c 'import os; assert os.environ.get("YUMI_TEST_POSTGRES_URL"), "需要隔离 PostgreSQL 测试库"'
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/integration/test_retention_postgresql.py
```

迁移与 CI 配置按现有仓库方式实现，不在 shell 回显连接串。必要时对 `test_runtime_startup.py` 做受影响启动回归；仅当实际变更影响 startup 签名或任务注册时扩展，不把所有无关测试当作每次必跑项。最终差异冻结后执行一次充分验证集。

## 7. A4：数据库基线采集约定

本轮准备采集方法与结果表；访问生产、安装扩展、配置重启不由本 Spec 自动授权。不新建监控平台。没有线上权限时填“未采集”，照常交付 A1～A3。

获得线上只读诊断授权后，优先核查已有日志中“历史记录清理失败”的时间、频率及脱敏异常类型，结合当前到期记录的聚合数量判断是否存在积压。不能只匹配 `DataError`：按异常包装层级检查 `DBAPIError`，可用时核对安全的 SQLSTATE `22001`，不输出可能夹带正文/参数的整段异常。历史日志不存在或已轮转不能证明故障从未发生；没有授权则该项标注“未核查”。先确认积压基线，再解释修复后多日分批消化的进度。

### 7.1 最小采集项

| 类别 | 数据来源 | 用途 |
| --- | --- | --- |
| 版本/迁移/索引 | server_version、alembic_version、pg_indexes | 对齐源码和数据库；索引存在不等于有效使用 |
| 容量/增长 | pg_database_size、pg_total_relation_size、pg_stat_user_tables；至少两个时点 | 判断增长，不把估计行数当精确 COUNT |
| SQL 成本 | 已启用的 pg_stat_statements，SQL ID、调用量、总/平均耗时、行数 | 找总成本最高的查询；不导出原始 SQL/客户参数 |
| 事务/锁 | pg_stat_activity/pg_locks 的聚合计数和最长持续时间 | 区分等待连接、等待锁和查询计算 |
| 应用 | 请求 P95、连接池等待、后台任务等待、清理耗时与数量 | 区分数据库耗时、排队与外部模型/接口延迟 |
| 资源/恢复 | CPU、内存、swap、磁盘延迟/余量、最近恢复演练 | 判断是否需要硬件或恢复能力投入 |

观察窗口至少包含一个实际营业高峰；增长趋势建议连续 7 天。累计统计必须记录起止时间和 stats_reset，比较差值；pg_stat_statements 平均时间不能冒充 P95。没有池等待仪表时明确不可得，不从连接数推算精确等待时间。

下面 SQL 仅含元数据/聚合，适合有授权后的短时只读会话；各项之间不保持跨小时事务：

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
SELECT current_setting('server_version') AS server_version,
       pg_database_size(current_database()) AS database_bytes;
SELECT version_num FROM alembic_version;
SELECT relname, n_live_tup, n_dead_tup, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY n_dead_tup DESC;
SELECT relname, pg_total_relation_size(relid) AS total_bytes
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY total_bytes DESC;
SELECT state, wait_event_type, count(*) AS connections,
       max(clock_timestamp() - xact_start) AS oldest_transaction
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
GROUP BY state, wait_event_type;
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('pg_stat_statements', 'vector');
COMMIT;
```

权限不足或超时记为未取得，不自动提权、重复重压查询。指标输出不包含客户内容、账号、连接串、令牌或 SQL 参数。检查 pg_stat_statements 后才决定是否可采集，不存在时不能悄悄 CREATE EXTENSION 或重启。

### 7.2 采集结果（2026-09-22，用户「全部做完」授权只读诊断）

经 SSH 在生产主机执行，数据库全部走 `BEGIN READ ONLY` 且 `statement_timeout = 5s`；日志只计数、只取首行，没有输出客户内容、调用栈或 SQL 参数。

| 类别 | 结果 |
| --- | --- |
| 版本、迁移、扩展 | PostgreSQL 16.14（镜像 `postgres:16-alpine`），`alembic_version = 0026_settle_retry_latch`，只装了 `plpgsql`；没有 `pg_stat_statements`，也没有 `vector` |
| 容量 | 库 13.7 MB；最大的表 `external_requests` 4,597 行、0.85 MB |
| 资源 | 内存 3.9 GB，可用 2.9 GB，无 swap；磁盘剩 39 GB；负载 0.1；数据库容器占 76 MB，API 容器占 145 MB |
| 连接 | 5 个空闲连接，无等待、无长事务 |
| 清理积压 | 已到期但未清的：jobs 17 条（最老的 30.8 天，刚过 30 天期限），其余各类都是 0。说明每日清理在正常运行 |
| 超长键风险 | 归档任务 0 条，任务最大编号 739（三位数），旧超长键缺陷在生产上从未触发 |
| 附件 | 只有 1 个任务带附件，每任务最多 1 张 |
| 知识库 | **启用的知识条目 0 条**，生产上检索和语义检索当前都没有可用数据 |
| 日志 | API 容器自 2026-09-15 起的日志里没有「历史记录清理失败」。日志不是 `INFO`/`WARNING` 文本格式，因此「清理完成」也计数为 0，不能据此判断是否运行；是否运行以上面的 jobs 年龄为准 |

pg_stat_statements 没有安装，SQL 成本、P95、连接池等待都不可得；按 §7.1 约定不自行安装。B 阶段各项没有进入条件：没有可测的数据库瓶颈，资源余量充足。

## 8. B 阶段触发条件与禁止自动实施项

| 候选项 | 进入下一阶段的证据 | 最小方案与验收 |
| --- | --- | --- |
| 待处理事项聚合计数 | 列表物化导致可测的数据库/应用耗时或内存成本 | 共享谓词的 COUNT，确保总览和列表一致；禁止两份独立业务规则 |
| 队列/清理索引 | 执行计划与代表性负载显示扫描或排序是主要成本 | 比较已有索引与候选索引；只加有收益的索引，并计入写入成本 |
| 连接池设置 | 持续池等待，已排除长事务且数据库有余量 | 明确每进程池上限×进程数；先消除占用再增加容量，不盲调 max_connections |
| 数据库参数/硬件 | 优化查询后仍有持续 CPU/IO/内存压力 | 依据实际主机共享负载配置，禁止套用未知内存百分比模板 |
| 小版本维护 | 运行版本缺修复、备份与兼容检查就绪 | 独立维护窗口，记录运行镜像/版本，验证恢复与登录业务页面 |
| 大版本升级 | 需要具体新功能、实测收益或临近支持终止 | 单独迁移方案，不能只修改镜像标签后挂载旧数据目录 |

查询优化前后使用同一数据规模与并发条件，至少核对结果等价、延迟分布、总成本与写入影响。不承诺尚未测得的“提升百分比”。生产禁止用 EXPLAIN ANALYZE 执行清理 DELETE/UPDATE；优先在隔离数据集测量，生产普通 EXPLAIN 也应受超时限制。

知识库按第 9 节独立验收。数据库变快不等于 RAG 准确，该项不属于 A 的完成条件。

## 9. C 阶段：RAG 检索质量优化

### 9.1 架构与事实来源

RAG 是“检索审核知识→给模型提供证据→验证回复”的流程，向量数据库只是可选组件。保留 PostgreSQL 存储客户、订单、任务与知识原文；语义检索如有需要使用同库 pgvector，不新增独立向量数据库。

现状追踪：`KnowledgeAdminService.create/update/set_enabled` 提交知识变更；`SQLAlchemyKnowledgeRepository.list_active` 读取全部启用记录；`KnowledgeService.retrieve` 按中文二元组/英文词元重合评分；`DeepSeekGuestAssistant.respond` 将 KnowledgeSnippet 放入上下文；`_has_relevant_property_knowledge` 和 `_validate_decision` 再做主题与安全处理。当前没有已接入的 embedding 生成链路，不能假设聊天模型 API 自动提供向量。

仅启用且经既有管理流程审核的 `KnowledgeEntry` 是正式知识；FAQ 候选、客人原话、模型草稿不得作为同等证据。知识可以说明静态设施和政策，不能代替 Hostex 的实时房态、价格和订单状态，也不能授权退款、赔偿或其他经营决定。

C1 不新增数据库表、扩展、供应商或后台索引任务。只有 C2 才设计向量字段/表、embedding 调用和数据库迁移，且须满足第 9.5 节准入条件。

### 9.2 C0：固定评估集与基线

在 `tests/fixtures/knowledge_retrieval_cases.json` 新建合成问答数据，配套 `tests/unit/test_knowledge_retrieval_eval.py`；不采集生产客人正文，不调用真实模型。80 个用例建议分布：20 同义改写、20 中英文问法、10 长答案/后段事实、10 无答案或仅共享泛词、10 停用/候选/错误适用范围、5 多主题、5 交易/实时信息或指令注入边界。一个用例可有多个标签，但仅归入一个统计主类。

每条至少保存：case_id、split（calibration/holdout）、语言、问题、合成知识条目、期望来源 ID 集合、允许的关键事实、禁止断言、是否应拒绝专属事实或转实时工具。为 mutation 场景增加明确的修改/停用步骤。40 条用于调整，40 条用于留出评估；调整时不能读取留出结果反复拟合阈值后仍称其为独立测试。

先在改动前输出一次基线，再用同一数据比较 C1；只记录用例 ID、来源 ID、判定、耗时，不复制真实资料。分别度量：

- 可回答问题的 Recall@3（至少一个正确来源）、多主题问题的全部所需来源覆盖率；必要证据是否实际出现在传给模型的文本中。
- 无答案/错误范围的错误放行率。检索出相似候选与最终允许断言分开计数，不把两者混成“准确率”。
- 直接词面问法不得退化；修改/停用/候选隔离、指令注入和交易边界用例必须全部通过。
- 固定合成规模下的检索 P50/P95，分别说明是否包含数据库读取，不把 mock 的耗时当生产性能。

初始验收目标：留出集中可回答问题 Recall@3 ≥90%，直接词面问法不低于原基线；已覆盖主题的证据片段完整；安全边界与停用隔离用例 100% 通过。这些是拟定目标，不是当前性能承诺。目标不达标时记录具体失败类型，不调低门槛冒充完成。

使用模型 stub 可验证装配、输入证据和确定性安全门，不能证明真实模型最终回答准确率。真实 embedding/模型对比需额外授权、预算与独立结果记录；未执行时标记“真实模型未验收”。

### 9.3 C1：先修现有检索，不引入向量依赖

**同义表达与中英文主题。** 在现有 `knowledge_service.py` 内复用 NFKC/casefold，集中维护一小份经测试的主题别名。检索和专属事实主题判断共享这一来源，不在两个模块各写一份规则。优先覆盖已有主题与评估集确认的漏检，例如停车/泊车/停车位/parking/car park，以及“开车过去车放哪”这类有明确语境的表达；不能把单独的“车”“car”都归成民宿停车。保留原始问题，不用额外模型改写问题，不把同义识别变成设施存在的证据。

别名用于增强词面召回；category/keywords 可以帮助召回，但单独命中分类或标签不能证明答案正文包含相应事实。适量排除纯英文停用词造成的假相关，并用无答案反例约束；不引入大型 NLP 依赖或自动全局扩写。校准集决定具体权重/阈值，确定后以常量和测试固定，不新增管理台调参界面。

**完整知识单元与预算。** C1 采用一个审核问答作为最小证据单元，优先取消当前无条件 `question[:300]`、`answer[:1200]`，在现有总字符预算 12,000 和最多 8 个来源内放入完整问题与答案。按相关性依次选择，剩余预算不足时跳过该单元；全部无法容纳时返回无充分证据，不能截掉尾部的否定、收费、时间或适用条件。统计“因预算跳过”与“没有匹配”两种原因，内部诊断不得把前者误报成已证明知识库没有该知识。

这样可覆盖“相关事实在第 1,200 字之后”的问答，且不立即建设自动分块管线。长条目应由管理员在后续内容维护中拆成语义完整问答；本轮不自动重写已审核正文。以后引入长文档时才设计分块、父文档与条件关联，不能把重叠窗口当成保留业务语义的充分保证。

同一来源不重复占槽；稳定排序以相关度为主，分数相同的 ID 次序只作可复现排序，不表示新 ID 自动优先于正确适用范围。引用保留 `source_id`，不能在摘要中创造原文没有的数字、日期或承诺。

**读取与生效。** C1 保留现有每次读取启用知识的路径，不先加 Redis/TTL 缓存或在数据库简单截取前 N 条。在修改/停用事务提交后开始的新请求必须读取新状态；已在途的请求可能仍持有此前快照，不能宣称已撤回所有在途上下文。已有数据规模未测量，不把全量读取直接描述成当前性能故障。确实出现瓶颈再进入 C2 或单独证明缓存失效设计。

### 9.4 C1：召回与证据安全门一起验收

修改 `DeepSeekGuestAssistant._property_topic/_has_relevant_property_knowledge` 及必要的 `services/answer_policy.py::is_property_specific` 调用边界，修复“同义问法召回后又因固定中文词被拒绝”的不一致。追踪所有调用方，复用共享主题识别，但保持交易、设施故障、人工服务请求的既有优先级。

确定性约束：

1. 证据来自本次实际传入模型的已启用审核问答；`source_id` 必须与该集合一致。不接受模型自己声称“有来源”或仅有高相似度作为事实确认。
2. 主题一致只是必要条件。最终允许引用的设施、费用、时间、否定与例外必须由对应问答支持，不能因“附近有早餐店”就宣称“民宿含早餐”，或因“停车需收费”回答“免费停车”。关键词/分类不参与证明设施存在。
3. 多主题问题不能因第一个主题有证据，就把整句话视为全部已证实。C1 复用当前安全回复路径，在无法确认全部专属主题覆盖时保守标记未确认，不趁此改造复杂部分回答协议。
4. 没有充分证据仍走既有未确认/知识缺口流程；不删除专属事实门，不把所有检索结果直接判为 grounded，不用额外 LLM 裁判掩盖规则缺口。
5. 正式条目内容仍是参考数据，不能执行其中夹带的“忽略系统规则”、工具调用、退款或消息发送指令。知识检索不得扩大工具权限。

当前确定性规则不能证明任意自然语言的完整语义蕴含；C1 必须报告已覆盖主题和失败问法，不承诺消灭所有幻觉。测试至少包含负面/收费/外部商户/错房间对照，并检查真实 `respond`→上下文→`_validate_decision` 链路。模型 stub 故意返回不被证据支持的已知类型断言时，安全门须拦截或退回保守路径，不能只验证正常回复样例。对现有规则无法安全实现的泛化场景保守处理并列入下一步评估，不绕过安全门换取 Recall 分数。

#### 9.4.1 交接审查后的 C1 补修（本轮“开始”已授权）

2026-09-21 独立核对发现三个可复现缺陷，本轮在现有 C1 范围修复：问题标题不能单独证明答案事实；“不免费”等否定不能给“免费”背书；客人问句中的猜测数字不能作为已审核金额依据。修复位置限定在 `DeepSeekGuestAssistant._supporting_knowledge/_has_unsupported_property_claims` 及直接所需的私有辅助逻辑，并通过 `respond` 链路的模型 stub 测试验证错误回复被替换、真实证据仍可使用。问题可用于主题和适用范围识别，但只有答案正文提供事实；周边主题的问答不得绕过本店证据边界。

不放宽评估标签来消除失败，不改真实工具结果的既有处理、不扩大 C2 或生产操作。已公开的留出集只作为回归集；新增自编案例也不宣称独立留出。完整的独立泛化验证、PG 与真实模型验收分别保留状态，不以这三项修复全部完成替代。

#### 9.4.2 9.4.1 补修引入的放行回归（2026-09-21，用户已回复「开始修」）

复核 9.4.1 时发现，为避免误拒而补的答案判据和否定窗口过宽，造成 4 个新的错误放行；交接前的实现能拦下这 4 例：

| 场景 | 被放行的错误回复 | 原因 |
| --- | --- | --- |
| 问入住时间，知识只有写着「猫狗入住」的宠物条目 | 「下午两点以后就可以入住」 | 答案中只要出现「入住」就算入住退房时间证据 |
| 问离江汉路多远，知识只有「步行 3 分钟的公共停车场」 | 「步行 3 分钟就到江汉路」 | 答案中任意「数字+分钟/米」都算距离证据 |
| 问停车，知识写明收费 | 「不用预约也能免费停车」 | 否定窗口往前 12 字，把「不用」误当作否定了「免费」 |
| 问加床，知识只有「加一床被子」 | 「可以加一床」 | 被子量词「床」被当作加床 |

修复限定在 `deepseek_client.py` 的 `_ANSWER_TOPIC_PATTERNS` 与 `_has_affirmative_free_claim`，只向保守方向收窄：

1. 入住退房：答案里的入住或退房必须与时间表达同在一个分句内相邻（如「三点后入住」「退房时间为 12:00」「after 3 PM」），单独的「入住」不算。
2. 距离：删除「数字+单位」判据；答案须出现距离类用语（沿用主题别名，另加「相距」「away」）。代价是更多保守漏判。
3. 加床：只认「折叠床」「加（一）张…床」「extra bed」「rollaway」，排除被子、床单的量词用法。
4. 免费否定：只认紧贴在前的否定（「不」「非」「不是」「并非」「not」「n't」「no longer」）。
5. 上述 4 例写成回归测试：先确认它们在当前实现上失败，修复后通过；9.4.1 的正常对照保持通过。

已知天花板（交接前就有，本轮不处理）：距离证据不核对目的地，答案写「离地铁站 500 米」时，仍可能被当作「离江汉路多远」的证据。要处理需另行确认「目的地一致」规则。

#### 9.4.3 收尾补齐（2026-09-22，用户回复「全部做完」）

用户确认的口径与范围：

1. **上下文隔离（剔除）。** 问本店事实且没有问周边时，问题标题在讲附近、周边的问答不交给模型；问实时价格、房态等交易问题时，标题或分类讲房价的静态条目不交给模型。问周边的问题照常保留周边问答。剔除在检索之后、构建模型上下文和证据门之前统一完成，评估脚本按同一函数计算「交给模型的证据」。
2. **距离核对目的地。** 能从问题里取出目的地时（「离江汉路步行街多远」「How far is it to the metro station?」），答案分句必须提到该目的地，并且有距离用语或「数字+距离单位」，才算距离证据；取不出目的地时，沿用只认距离用语的规则。
3. **「空房」等说法识别为实时房态。** `answer_policy` 的交易分类，以及 `deepseek_client` 的独立房态与强制房态判定，补上「空房、余房、剩房、满房、订满」。
4. **英文兜底文案。** 专属事实未确认时，按回复语言给出中文或英文兜底，中文文案不变。
5. 真实模型验收：用户决定跳过，交付继续标「未验收」。

### 9.5 C2：满足条件后采用 PostgreSQL + pgvector 混合检索

#### 9.5.0 实施决策（2026-09-22，用户确认）

- **服务商与模型**：硅基流动 `BAAI/bge-m3`，走 OpenAI 兼容的 embeddings 接口，复用已安装的 `openai` 客户端，1024 维。
- **凭据与配置**：服务商地址、模型、key 和开关都进现有「接口设置」运行时配置（加密保存、有版本），不写进文件或普通配置，也不进日志。
- **存储与检索（替代下文的 pgvector）**：向量存在现有 PostgreSQL 的一张普通表里（`JSON` 列，SQLite 同样可用），检索时读出当前启用、内容与模型版本都匹配的向量，在 Python 里做精确余弦相似度。不换生产数据库镜像，不装扩展，迁移只是普通建表。
  ponytail：知识条目涨到数千条，或检索 P95 超过预算时，再改用 pgvector。
- **默认关闭**：代码上线后语义检索处于关闭状态。用户在「接口设置」填好 key 后，先补齐向量、跑评估，达到本节「C2 验收门槛」再开启。
- **现状**：生产上启用的知识条目为 0（§7.2）。在知识库有内容之前，C2 对线上回答没有实际作用。


#### 9.5.1 实现要点（2026-09-22，用户确认「现在就做，默认关闭」）

- **配置**：`RuntimeConfigSnapshot` 新增 `embedding_enabled`（默认 false）、`embedding_base_url`（默认 `https://api.siliconflow.cn/v1`）、`embedding_model`（默认 `BAAI/bge-m3`）、`embedding_api_key`（默认空）。生产上已存的旧版本快照没有这 4 个字段，读取时按默认值补齐，**不改变任何现有客户端的行为**。只有开关打开时，才要求填写 key，并在连通测试里做一次极小的 embeddings 调用。
- **向量表**：新增 `knowledge_embeddings`，每条知识的每种语言一行，记录 `entry_id`、`language`、`model`、`content_hash`（参与向量化的规范化正文加模型名的 SHA-256）、`dimensions`、`vector`（JSON）。迁移 `0027` 只建普通表，SQLite 与 PostgreSQL 通用。
- **向量生成**：由新增的每小时知识向量维护循环按需补齐：读取启用条目与已有向量，对缺失或内容哈希不符的条目调用 embeddings；调用在数据库事务之外完成，落库前重新核对内容哈希，旧结果晚到不会覆盖新正文。不新建队列（与 §9.5「复用 jobs」的差异：对账式补齐天然幂等，失败下一小时自动重试）。停用条目的向量保留，重新启用且内容与模型一致时直接复用。
- **检索**：关键词排序照旧；语义检索只读内容哈希与当前正文、模型都匹配的向量，查询先用 `redact_sensitive_guest_text` 脱敏再向量化，超时 3 秒。两路按倒数排名融合（RRF），再按 C1 的整条问答与预算规则选入证据。语义检索出错、超时或没有可用向量时退回纯关键词，只记录异常类型。
- **证据门不变**：相似度只影响召回，不作为本店事实的证据（§9.4 第 1 条）。
- **事先登记的验收门槛**：在第二套留出集上，与 C2 前基线（§14.2：Recall@3 0.900）相比，Recall@3 要提升；错误放行不增加；隔离不下降；含查询向量化在内的检索 P95 不超过 1.5 秒。融合常量与相似度下限只用校准集确定，确定后冻结，再对第二套留出集跑一次。
- **可观测性**：清理轮次触达批次上限时，汇总日志改为 WARNING 级别。生产上 INFO 日志不输出，否则 `possibly_incomplete` 看不到。

#### 9.5.2 审查补修（Codex，2026-09-22，用户明确「开始修复」）

范围：修评估真实性、向量响应边界、C1 已知错误放行与隔离；不调用真实服务、不读写生产、不部署。

- `tests/unit/test_knowledge_retrieval_eval.py`：质量调参保留缓存，增加独立延迟模式绕过查询缓存；知识向量仍可缓存。复用生产 HTTPS transport、SDK 零重试和 3 秒查询超时；报告查询调用、缓存、成功/失败/无向量与回退，缓存耗时不得用于真实 P95 验收。关闭客户端。原样本、标签和历史基线保持不变。
- `services/knowledge_embeddings.py::OpenAICompatibleEmbeddingClient.embed`：校验索引恰好覆盖输入、向量有限且维度一致、非零；bge-m3 固定 1024 维。异常整批拒绝，调用方沿用回退和不落库。
- `integrations/deepseek_client.py::_supporting_knowledge`：问免费/费用时，需要同主题审核答案明确涉及费用；开放时间、洗衣液位置不能证明洗衣收费政策。
- `services/answer_policy.py::is_transaction_sensitive` 与 `_scope_knowledge`：英文房间 how much 问法进入交易边界，使历史房价不进入模型上下文；不把一般物品价格扩大为房价工具请求。
- `tests/unit/test_deepseek_client.py`、`test_knowledge_embeddings.py` 及评估测试：先红测后修复，真实 respond 验证上下文/兜底；公开第二套失败并纳入安全回归。第二套自此不是独立留出集。
- 验收分开：C2 的召回改善/不退化只算效果比较；开启前已知安全样本必须错误放行 0、隔离 100%，另由非调参者提供新独立留出集。缓存或 mock 不证明真实延迟、成本及生产表现。
- 本轮不为已公开失败逐个扩充同义词，不改变正式校准/留出标签。新增针对评估与安全缺口的回归；新校准集和独立留出集、真实向量调参及登录生产验收保留为后续外部证据门禁。

本节的验收要求优先于 §9.5.1「不退化即可开启」的解读。

#### 9.5.3 收费证据与英文问价的校正（Claude，2026-09-22，用户回复「全部做完，生产也做」）

复核 §9.5.2 时发现两处过度收紧，均为保守方向，不影响安全，但会误拒常见的正常回答：

- **收费证据**：§9.5.2 要求主题与费用写在同一句，「门口有 2 个车位。每天 20 元。」这类常见写法因此退回未确认；英文「20 yuan per day」也不被认作费用。改为：同一条审核问答里，有一句本店范围内讲到该主题，另有一句本店范围内的费用说明即可；费用句若点名了**别的**主题（例如问洗衣时的「停车每天 20 元」），不能作证。费用字眼补上 `yuan`、`rmb`、`¥`。周边商户的价格仍不作证。
- **英文问房价**：§9.5.2 的「how much … room」也会把「How much space is in the room?」「room service」算成房价。收窄为「how much is/are/does/for/…」这类问价结构，并排除 room service。
- 其余 §9.5.2 的修复（问句数字不作证、周边标题整条排除、向量响应校验、评估延迟模式）保持不变。

**开启语义检索的门槛**（沿用 §9.5.2 与 Codex 交接的 G1～G4）：已知安全样本错误放行 0、隔离 100%；在新的独立留出集（第三套）上，关键词 + 语义的 Recall@3 高于同一版本的纯关键词，且 ≥ 0.90；含失败回退的真实 P95 ≤ 1.5 秒。第一、二套留出集只作回归。真实向量调参与延迟测试需要用户提供 key；开启需要用户在后台输入管理员密码。

**触发与前置决策。** C1 后仍有成组的同义/跨语言漏检或可测的全量计算瓶颈时，提交失败案例和候选方案，再由用户确认 C2。进入前必须选定 embedding provider、模型、维度、最大输入、延迟/费用预算、超时与数据发送范围；这些在本项目尚未定型，本 Spec 不替用户默认供应商或允许真实调用。向量模型不必与聊天模型相同，不假设 DeepSeek 聊天接口能返回 embedding。

使用现有配置机制表达 provider、model、API 地址；凭据通过环境变量或已有安全凭据机制读取，不硬编码或写入日志/普通配置文件。优先复用已安装客户端；未经必要性证明不增加多供应商抽象层。pgvector 是数据库扩展，需独立构建/部署支持，不能仅通过 pip 安装就宣称已接入。

**数据与失效。** `KnowledgeEntry` 继续是审核状态和原文的唯一真相。派生向量记录关联 entry_id、语言、参与 embedding 的规范化内容哈希、模型标识/版本和维度；具体表、迁移文件编号在 C2 决策完成后根据届时 Alembic head 确定，不能现在预占迁移号。固定模型与维度后再定义数据库向量列，禁止混用不同模型空间。

- 管理员修改原文仍先提交业务记录；向量更新失败不得回滚已成功保存的知识，也不得继续把旧向量当成新正文的有效索引。
- 复用已有持久化 jobs 做异步向量生成，不建第二套队列。外部 embedding 调用前释放数据库事务/连接；完成后用内容哈希与模型版本比较再落库，旧任务晚到不得覆盖新版本。
- 新增/修改到向量就绪之间由关键词路径提供服务；查询时关联当前条目校验 is_enabled、语言和内容版本，仅检索已就绪且匹配当前原文的向量。
- 停用立即从新请求候选中排除；恢复启用时只允许复用内容和模型版本都一致的向量。失败可有限重试并记录错误码，不静默扩大延迟。
- 不批量向外发送客人聊天、客户档案、密码或门锁凭证；索引输入仅限明确允许的审核知识。查询 embedding 也属于外部发送，须走既有脱敏边界和对应授权。

**查询路径。** 相同启用/版本/适用范围过滤后分别取得关键词与语义候选，按来源去重并融合排序，再按 C1 的完整证据与预算规则提供给模型。可采用简单的排名融合，不能直接相加量纲不同的原始词面分数与向量距离。具体候选数、融合常量和最低语义相关阈值由固定校准集确定，冻结后再跑留出集。

小规模先使用 pgvector 精确搜索；只有代表性数据下延迟超标才增加近似索引。向量搜索超时、索引不完整或服务不可用时回退关键词检索，不能退回“取最新几条知识”或让模型自由编造。回退次数可观测，但不记录原始问题/密钥。

**C2 验收门槛。** 必须提供相同评估集下的召回改进、无答案误放行、过滤正确性、延迟和调用成本；语义搜索增加的收益应对应实际失败案例，不能仅报告索引建成。使用真实目标 PostgreSQL+pgvector 验证增改停用、旧任务晚到、embedding 失败、版本切换与回退。当前路径（c）的“PG 未验收”不允许跨过该门槛发布 C2。

向量回滚只关闭语义检索并保留关键词路径与 KnowledgeEntry；不得通过删库或删除正式知识来回退。数据库扩展/表迁移、embedding 模型变更及重建索引均有独立实施记录，不与 A 的清理上线捆绑。

### 9.6 文件范围与 RAG 验收矩阵

| 文件 | C0/C1 改动职责 |
| --- | --- |
| `src/homestay_bot/services/knowledge_service.py` | 共享主题/别名、最小词面评分修正、完整问答预算与来源去重 |
| `src/homestay_bot/integrations/deepseek_client.py` | 主题证据门、实际上下文与必要局部提示词保持一致 |
| `src/homestay_bot/services/answer_policy.py` | 仅当同义/英文专属问题分类阻断真实链路时定点复用识别规则；不扩大交易权限 |
| `tests/fixtures/knowledge_retrieval_cases.json`、`tests/unit/test_knowledge_retrieval_eval.py`（新增） | 合成校准/留出集与可重复评估 |
| `tests/unit/test_knowledge_service.py`、`tests/unit/test_deepseek_client.py` | 检索与安全门联动回归 |
| `tests/integration/test_knowledge_repository.py`、`tests/integration/test_knowledge_routes.py` | 新增/修改/停用/候选隔离，SQLite 测试开启外键 |

`KnowledgeAdminService`、`SessionKnowledgeRepository` 和 FAQ 生成调用方先读后测，C1 不因覆盖测试就强行修改其业务实现。不增加 C2 配置、表或未使用接口作为“以后备用”。C2 需要的 config、模型、仓储、知识写入、job handler、compose 与迁移文件，在准入决策时补充准确符号和验证目标后才实施。

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| K1 | 停车/泊车/开车停放等合成同义问法 | 正确来源进入 Top 3，相关主题证据门一致；自行车维修等无关反例不误放行 |
| K2 | 中英文问法与答复语言 | 两种语言命中同一正确来源；不因中文主题硬编码拒绝英文证据 |
| K3 | 有效事实/限制条件在第 1,200 字之后 | 预算允许时完整送入；超预算明确缺证，不截断成反向含义 |
| K4 | 新增、修改、停用与未审核候选 | 提交后新请求读新状态；旧答案和停用/候选内容不进入正式上下文 |
| K5 | 无答案、只有类别/标签匹配、纯泛词重合 | 不因候选存在就确认专属事实；必要时保守拒答/知识缺口 |
| K6 | 多主题、外部商户、收费/否定、房间范围不同 | 不以部分证据概括全部；不把附近服务说成本店提供，不改变限制 |
| K7 | 房态、价格、退款或知识中夹带指令 | 保留实时工具/人工决策边界；知识不能授予工具权限或覆盖系统约束 |
| K8 | 校准与留出集、预算/来源去重 | 指标按定义独立统计，直接命中不退化；来源≤8、总字符≤12,000 |
| K9 | FAQ 草稿与正式客人回复共用检索 | 未审核事实不提升为证据，现有 FAQ 审核流程与调用契约不退化 |
| K10 | C2 旧 embedding 任务晚到、停用/重新启用、模型切换 | 只使用匹配当前内容/模型的向量；失败时仍可走关键词路径 |
| K11 | C2 语义超时、缺索引、无有效向量 | 有界失败与回退，不阻断基本问答、不越过证据门 |
| K12 | C2 真 PG 扩展、实模质量/成本 | 提供实际运行证据；mock、SQLite 或索引完成不能替代实测 |

C0/C1 本地验证命令（无外联）：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_knowledge_service.py tests/unit/test_deepseek_client.py tests/unit/test_deepseek_faq_drafter.py tests/unit/test_faq_draft_job.py tests/integration/test_knowledge_repository.py tests/integration/test_knowledge_routes.py
.venv/bin/python -m ruff check src/homestay_bot/services/knowledge_service.py src/homestay_bot/integrations/deepseek_client.py src/homestay_bot/services/answer_policy.py tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_knowledge_service.py tests/unit/test_deepseek_client.py tests/integration/test_knowledge_repository.py tests/integration/test_knowledge_routes.py
.venv/bin/python -m mypy
git diff --check
```

若修改 `answer_policy.py`，补跑其直接调用方的相关安全/交易分类回归；若涉及其他文件，按受影响风险增补而非机械全库重跑。交付时 K1～K9 与 K10～K12 分列；C2 未进入实施时写“未启动”，不能冒充失败或通过。

## 10. 文件范围与交付清单

A 默认修改 4 个业务文件：`repositories/retention.py`、`repositories/operations.py`、`services/task_page_service.py`、`application.py`；测试和 CI 按第 6 节。C0/C1 的独立范围见第 9.6 节。`db.py`、`domain/models.py`、`compose.yaml`、迁移在 A 与 C0/C1 中不修改；C2 另行确认。

不新建通用清理框架、配置中心、索引管理器或监控服务。所有新增/修改函数及关键逻辑写清楚中文职责与约束注释。正式实施后按项目规则在 `tasks/todo.md` 跟踪；当前 Spec 编写不创建实施任务记录。

执行步骤：

- [ ] 核对 HEAD、现有改动和本文件证据；若关键前提失效，列出偏差，修订后再扩大实现。
- [ ] 在现有测试中固定两个超长键入口的失败条件；完成共享有界键与回归。
- [ ] 修复人工批量锁定与整批校验，统一三种入口的墓碑写入，验证周转同步防重建与墓碑过期语义。
- [ ] 实施有界仓储、独立批次事务和异常隔离；验证保留期限与附件原子性。
- [ ] 补齐真实 PostgreSQL 与 CI 验证；保留 SQLite 支持。
- [ ] 自审范围、并发和失败恢复；冻结后跑最低充分验证集。
- [ ] 交付基线采集状态，明确区分代码完成、PG 验收、线上采集、部署四种状态。
- [ ] 独立执行 C0 基线与 C1 最小检索修复，交付 K1～K9、校准/留出对比和真实模型证据缺口；据结果判断是否申请进入 C2，不自动安装 pgvector 或调用 embedding。

交付报告至少包括：实际文件/符号变化、测试命令与通过/失败/跳过数、PostgreSQL 版本与迁移结果、未验证风险、是否触达批次上限、B 阶段是否具备进入条件。未授权生产时不得给出“线上已优化”结论。

本轮不强制升级应用版本；若用户随后要求发布或版本升级，按项目规则更新 CHANGELOG。提交/推送前运行适用的最终 Ponytail 审查；任何提交、推送和部署仍需当前明确授权。

### 10.1 上轮问题与本轮验收映射

R2 的“仅记录、不实施”决定已由用户本轮要求替代。D1/D2 纳入 A2，实现前仍遵守用户确认与“开始”门禁。此处只记录范围变更，不表示源码问题已修复。

| 编号 | 正式修复落点 | 验收 |
| --- | --- | --- |
| D1 | 第 5.4 节；`require_purgeable/purge_selected` 的锁、fresh 状态与带资格条件删除 | T12、T13，结合 T5、T11 |
| D2 | 第 5.5 节；统一墓碑写入，三个删除入口复用，沿用周转来源的读取规则 | T14～T18，结合 T5、T6 |

R4 已将 RAG 纳入独立 C 阶段；仍不改变额外同步来源的业务抑制规则，也不授权生产发布。交付报告必须分别写明 D1 的 PG 并发证据和 D2 的三入口墓碑/周转重放证据，不能只以新增函数或 SQLite 通过标记全部完成。

## 11. 交给 Claude 的开场指令

> 先读取项目 AGENTS.md 和本 Spec R4，核对源码基线，并简要报告范围和偏差。用户确认本 Spec 并明确要求开始后，先完成 A1～A3 的实现与可运行的本地验证，包含第 5.4、5.5 节原 D1/D2 的修复；再独立完成 C0/C1 的检索评估与最小修复。PG 采用路径（c）：完成测试与 CI 配置，环境仍不可用时标注“PostgreSQL 未验收”，不得宣称 A 全部通过；不为跑 CI 擅自推送或安装数据库。A4 在有目标环境授权时采集，否则交付未采集项。B 和 C2 仅按准入条件提出建议，未确认不得实施。保留现有业务资格、权限、幂等和附件事务保障，不调用真实外部服务，不提交、推送或部署。最终分别交付 T1～T18、K1～K9 的结果，并把 K10～K12、真实模型及生产验收的未启动/未验证状态写清楚。
