# 当前任务：综合审查风险修复 Spec（HARD-GATE）

- [x] 完成综合代码审查并保留代码、迁移和测试证据
- [x] 第一段确认：修复目标、范围边界和四阶段发布顺序
- [x] 第二段确认：功能规则、资源预算和精确文件/函数计划
- [x] 第三段确认：迁移、回滚、验证和生产验收门禁
- [x] 用户明确回复“开始”后，才进入红测与业务代码实现
- [x] 阶段 0 红测：Compose 持久挂载与私有目录写入探针
- [x] 阶段 0 实现：单一容器目录、宿主机挂载与启动失败保护
- [x] 阶段 0 本地最小验证与差异自审
- [x] 阶段 0 提交、推送、生产文件迁移与双重重建验收
- [x] 阶段 1 红测：模型调用/字符预算、相关知识检索和工具前置校验
- [x] 阶段 1 实现：统一预算、确定性召回和共享住宿日期验证
- [x] 阶段 1 本地最小验证与差异自审
- [x] 阶段 1 更新 v1.3.5 版本号与正式更新日志
- [x] 阶段 1 提交、标记 v1.3.5 并推送 GitHub
- [x] 阶段 1 完成生产备份、API 单容器部署和运行态验收
- [x] 阶段 2A 红测：用途隔离、双读双写、页面视图和下单核验
- [x] 阶段 2A 实现：0022 密文列、敏感数据服务和有界回填能力
- [x] 阶段 2A 验证：SQLite 往返迁移、PostgreSQL 离线 SQL 和聚焦回归
- [x] 阶段 2A 本地 Review；不提前实施 2B 删列或保留期清理
- [x] 阶段 2A 发布：升级 v1.3.6、补充正式更新日志并验证发布包
- [x] 阶段 2A 提交、标记 v1.3.6 并推送 GitHub
- [x] 阶段 2A 生产备份、迁移、API 单容器部署和运行态验收
- [x] 阶段 2B 进入门禁：2A 健康、密文完整、备份可列出且隔离恢复成功
- [x] 阶段 2B 红测：删列安全门、无明文读取和审批 PII 保留期
- [x] 阶段 2B 实现：0023 不可逆迁移、密文唯一读取和既有清理循环接入
- [x] 阶段 2B 验证：迁移失败回滚、聚焦回归、静态检查和本地 Review
- [x] 阶段 2B 发布：升级 v1.3.7、补充正式更新日志并验证发布包
- [x] 阶段 2B 提交、标记 v1.3.7 并推送 GitHub
- [x] 阶段 2B 完成生产备份、不可逆迁移、API 部署和运行态验收
- [x] 阶段 3 入口复核：构建、依赖、响应头、CI 与生产上传目录权限基线
- [x] 阶段 3 红测：镜像 digest、哈希锁、可信主机、非 root、构建上下文和后台响应头
- [x] 阶段 3 实现：锁文件、Dockerfile、`.dockerignore`、后台响应头和最小 CI
- [ ] 阶段 3 最终验证：锁文件重建、镜像/容器、迁移、静态检查和无外联测试
- [ ] 阶段 3 本地 Review；版本、GitHub 推送和生产部署等待单独授权
- [x] 阶段 3 发布：升级 v1.3.8、维护正式更新日志并验证发布包
- [ ] 阶段 3 提交、标记 v1.3.8 并推送 GitHub
- [ ] 阶段 3 生产备份、上传目录权限迁移、API 部署和运行态验收

### 阶段 2A 本地 Review

- 新审批临时双写旧明文和用途隔离密文；下单、写后核验和员工审批页面均优先从密文读取，只有密文不存在的旧记录才回退旧明文，损坏密文不会静默降级。
- 审批模板只接收不可变 `ApprovalPageView`，不再接触含密文字段的 SQLAlchemy 实体；显式回填服务每批最多 100 条、按主键续跑且不自动启动循环。
- `0022_approval_pii` 只增加四个 nullable 列，SQLite 升级/降级/再升级和 PostgreSQL 离线 SQL 均已验证；未实施 2B 明文删列、PII 清理或保留期策略。
- 红测先证明服务缺失；最终聚焦回归为 `31 passed`，运行时装配回归为 `14 passed`；Ruff、mypy（7 个受影响源文件）和 `git diff --check` 通过。未调用真实模型，未升级版本、推送或部署。

### 阶段 2A 发布 Review

- 正式功能提交与标签均为 `5fa0db51b3833f2d5c69467e4cf10e68e92a8731` / `v1.3.6`，已推送 GitHub；标准 wheel 元数据版本为 `1.3.6`。
- 发布前生产审批总数、姓名、手机号和特殊需求非空数再次确认为 `0|0|0|0`；新镜像构建完成、迁移前再次确认为零，因此未执行无意义的历史回填。
- 生产备份位于 `/opt/yumi-backups/v1.3.6-20260831T025557`，包含旧提交、权限 600 的 `.env`、Compose、完整源码 bundle、上传目录归档、PostgreSQL custom dump和旧 API 镜像编号；bundle 与数据库归档均已验证，旧镜像标记为 `rollback-v1.3.5`。
- 服务器源码以验证过的 Git bundle 快进到功能提交；三个既存未跟踪环境备份保持不变。PostgreSQL 从 `0021_task_lifecycle` 事务升级到 `0022_approval_pii`，四个新列全部存在，迁移后审批密文计数仍为 `0|0|0|0`。
- 只重建 API，PostgreSQL 未重启；运行镜像为 `sha256:a0bd56b2c58fcbb15d29c28aa2103efcd3432f7881f6368262203352de074678`，API 容器包和公网 OpenAPI 均为 `1.3.6`，重启次数为 0，近期 `ERROR`、`Traceback`、`Exception` 标记为 0。
- 本机和公网 `/health` 均为 `ok`，公网 `/employee/login` 返回 200，未登录访问 `/employee/approvals` 返回 401；私有上传仍挂载 `/opt/yumi-data/private_uploads -> /app/data/private_uploads`，文件数为 0。
- 本次发布没有主动调用 DeepSeek、百居易或企业微信，没有发送真实消息；受保护的未跟踪项目总结文件保持未读、未改、未暂存。

### 阶段 2B 本地 Review

- 进入前复核生产 v1.3.6 健康、API 重启次数为 0、审批密文门禁为 `0|0|0|0`；将 `/opt/yumi-backups/v1.3.6-20260831T025557/postgres.dump` 恢复到隔离数据库，确认 revision 为 `0021_task_lifecycle`、35 张表可读、审批数为 0，并在验收后删除隔离数据库。
- 新增不可逆迁移 `0023_approval_pii_final`：未清理审批缺少姓名或手机号密文时先失败，结构保持 0022；门禁通过后删除 `guest_name`、`guest_mobile`、`special_requests`。普通 downgrade 明确拒绝，回滚只能恢复升级前数据库备份并切回旧镜像。
- `ApprovalSensitiveData` 已删除旧明文回退、回填协议与批处理服务；新审批只写用途隔离密文。未清理记录缺少必需密文立即失败，已按策略清理的记录在后台显示“已清理”，下单状态机拒绝重新使用。
- 既有每日 `SQLAlchemyRetentionRepository.purge()` 增加审批 PII 清理：`BOOKED` 在退房满 30 天清理，`REJECTED`、`CONFLICT` 在终态更新时间满 90 天清理；`PENDING`、`CREATING`、`NEEDS_REVIEW` 永不自动清理，审批主记录和审计字段保留。
- 红测最初为 `5 failed`，分别证明兼容回退、保留期和 0023 均尚不存在；最终综合门禁为 `80 passed`，Ruff、mypy（117 个源文件）、PostgreSQL 离线 SQL、SQLite 成功/失败迁移、Alembic 单一 head 和 `git diff --check` 通过。
- 本阶段目前只完成本地编码与验证，没有升级版本、提交、推送或部署；生产仍保持 v1.3.6 / `0022_approval_pii`。没有调用 DeepSeek、百居易或企业微信，受保护项目总结文件保持未读、未改、未暂存。

### 阶段 2B 发布 Review

- 发布前生产仍为 v1.3.6 / `0022_approval_pii`，API 健康且重启次数为 0；审批记录和未清理密文缺口均为 0，满足不可逆迁移入口门禁。
- 版本暴露回归为 `7 passed`，标准 wheel 元数据为 `1.3.7` 且 `git diff --check` 通过；业务源码自最终 `80 passed` 门禁后没有变化，因此版本号、更新日志和任务记录没有重复触发业务回归。
- 正式功能提交与标签均指向 `e9ae7d8f1bdb204b6a254c25c2b9d682666d43a3` / `v1.3.7`，已推送 GitHub；服务器使用 SHA-256 一致且 `git bundle verify` 通过的 bundle 快进，三个既存未跟踪环境备份保持不变。
- 生产备份位于 `/opt/yumi-backups/v1.3.7-20260830T193253Z`，包含 v1.3.6 源码、配置、私有上传、PostgreSQL custom dump 和旧 API 镜像；数据库转储已恢复到隔离数据库，确认 `0022_approval_pii`、35 张表和 0 条审批后删除隔离库，旧镜像标记为 `rollback-v1.3.6`。
- 停止旧 API 后再次确认审批密文门禁为 `0|0|0|0|0`；PostgreSQL 事务升级到 `0023_approval_pii_final`，三个旧明文列数量为 0，四个密文/清理字段完整，迁移后审批计数仍为零。
- 生产 API 运行镜像为 `sha256:4e5b77a07ecb9bab915fcc79709a2fe01eb02d1cc8ee79de2890346b5d3e59b3`，容器包与公网 OpenAPI 均为 `1.3.7`，重启次数为 0，近期 `ERROR`、`Traceback`、`Exception` 标记为 0；PostgreSQL 容器没有重建。
- 本机和公网 `/health` 均为 `ok`，公网 `/employee/login` 返回 200，未登录访问 `/employee/approvals` 返回 401；私有上传仍挂载 `/opt/yumi-data/private_uploads -> /app/data/private_uploads`，文件数为 0。
- 本次发布没有主动调用 DeepSeek、百居易或企业微信，没有发送真实消息；受保护的未跟踪项目总结文件保持未读、未改、未暂存。

### 阶段 3 本地 Review（待镜像门禁）

- 红测首次为 `13 failed, 12 deselected`，分别证明基础镜像未固定 digest、缺少哈希锁和构建上下文排除、存在 `PIP_TRUSTED_HOST`、容器以 root 运行、缺少 CI，且后台未返回防嵌套与来源保护头。
- `requirements.lock` 由 Python 3.12 和 pip-tools 生成，精确固定生产及构建依赖与制品哈希；连续两次生成的 SHA-256 均为 `9abddc58bb9c9f1e7209aaa7c3d369649faee26f07f2f75aed259a60396e3ede`。全新虚拟环境按哈希安装、项目无依赖重解析安装、`pip check` 和导入均通过。
- SCA 首次发现原约束会锁入存在已知公告的 `cryptography==45.0.7`；用户确认后将源约束升级为 `>=50.0.1,<51`，锁定 `50.0.1`，重新扫描结果为 `No known vulnerabilities found`，加密相关聚焦回归为 `41 passed`。
- Dockerfile 固定 Python 3.12 slim digest，通过 TLS 索引和哈希锁安装，并声明固定非 root UID/GID `10001:10001`；`.dockerignore` 排除密钥、版本库、虚拟环境、运行数据、备份、测试与受保护总结。最小 CI 采用只读仓库权限、完整 Action SHA、关闭真实外联测试，并覆盖静态检查、迁移、全量测试和镜像构建。
- `/employee` 应用响应新增 `frame-ancestors 'none'`、`DENY` 和 `no-referrer`，公开健康检查与静态资源保持不变；供应链与响应头聚焦回归为 `26 passed`。
- 冻结差异后的本地门禁：Ruff 全仓通过，mypy `130` 个源码文件通过，CI YAML 有效；全新 SQLite 数据库迁移到 `0023_approval_pii_final (head)`；使用锁定依赖环境的无外联全量回归为 `1229 passed, 15 skipped`。两项 warning 均为既存 Starlette/httpx 与 Alembic 配置弃用提示。
- 本机没有 Docker、Podman 或 Colima，因此尚未执行 Docker build、容器内用户与镜像内 `pip check`；阶段 3 最终验证和本地 Review 暂不勾选。生产上传目录当前为 `root:root`、权限 `0700`，部署前必须先备份并调整为 UID/GID `10001:10001`，否则非 root 启动写入探针会按设计拒绝启动。
- 已升级为 v1.3.8 并维护正式更新日志；标准 wheel 元数据为 `1.3.8`，SHA-256 为 `db9320f53dbaf06cb0b9fda2bd91e0f409eda572e338f90bdd0dbb9bf5ed5855`。当前尚未提交、推送或部署，也未调用 DeepSeek、百居易或企业微信；受保护的未跟踪项目总结保持未读、未改、未暂存。

## 第一段：推荐修复边界

- 阶段 0（最高优先级）：先抢救并持久化私有上传文件。部署前从真实运行容器导出并校验既有文件，再为 `data/private_uploads` 增加持久卷和备份/恢复检查；这一阶段不混入模型、数据库或前端改动。
- 阶段 1（高优先级）：给所有 DeepSeek 生产调用增加显式输出上限、单消息调用次数和上下文字符预算；把知识注入改为有相关性排序且受总量限制的检索；所有日期和房源工具参数必须在调用百居易前完成本地校验。
- 阶段 2（高优先级、独立迁移）：将预订审批的姓名、手机号和特殊需求迁移为密文，并增加终态记录的保留期清理；采用可验证的分阶段回填，禁止在未备份、未核对数量时直接删除明文字段。
- 阶段 3（中优先级）：锁定依赖与基础镜像、移除不安全的可信镜像配置、补充构建上下文排除规则与非 root 运行；在应用层补防嵌套响应头，并与生产 Nginx 的真实响应联合验收。
- 四个阶段分别提交、验证、发布和回滚，不合并成一次大版本；受保护的未跟踪 `YuMi民宿AI项目总结.txt` 始终保持未读、未改、未提交。

## 当前状态

- 本轮只写入任务计划，没有修改业务代码、数据库、Compose、依赖或生产环境。
- 用户以“继续”确认第一段；当前等待第二段确认。

## 第二段：待确认的功能规则与精确修改面

### 私有上传持久化

- `compose.yaml` 为 API 固定容器目录 `/app/data/private_uploads`，宿主机目录使用 `${PRIVATE_UPLOAD_HOST_DIR:-./data/private_uploads}`；生产 `.env` 指向 `/opt/yumi-data/private_uploads`，避免把生产路径硬编码进本地开发配置。
- `BootstrapSettings.private_upload_dir` 保留非容器默认值；Compose 显式覆盖 `PRIVATE_UPLOAD_DIR=/app/data/private_uploads`，容器内只有一个目录真相。
- `PrivateFileStorage.verify_writable()` 在应用生命周期启动时执行一次真实创建/删除探针；不可写时拒绝启动，不让系统在数据库可写但附件必丢的状态下继续运行。
- 部署前从旧容器导出当前目录，核对文件数量和 SHA-256；迁移后重建 API 两次并证明文件仍存在。`deploy/start.sh` 继续负责本机 SQLite 路径，生产容器备份流程单独包含宿主机上传目录。

### 模型资源预算

- 新增唯一的 `ModelBudget` 规则：单个主请求最多 48,000 字符，主链累计最多 120,000 字符，主链最多 3 次模型调用、最多 2 轮工具结果回填；必要精炼最多额外 1 次，投递拒绝后的安全改写最多 1 次且不能递归。
- 主回复 `max_tokens=1800`，精炼和投递改写各 `max_tokens=900`；这是在原建议基础上按用户允许放宽 50% 后的硬上限，不随所选 OpenAI 兼容模型自动扩大。
- 历史对话最多保留最近 6 条、每条 1,000 字符且合计不超过 6,000；当前问题最多 2,000；客户治理上下文最多 6,000；FAQ 候选最多 20 条且合计 4,000。
- 工具结果单次最多 24,000 字符；超过时按结构裁剪，不截断成无效 JSON。任何请求超出单次或累计预算时停止继续调用，优先返回已有确定性房态回执，否则转人工。
- `src/homestay_bot/services/model_budget.py` 保存统一常量与计数器；`DeepSeekGuestAssistant.respond()`、`_refine_reply()` 和 `DeepSeekDeliveryRewriter.rewrite()` 必须共用该规则，不在各模块复制第二套数字。

### 相关性知识检索

- `KnowledgeService.build_context()` 收敛为 `retrieve(language, query, limit=8, char_budget=12_000)`；每条问题最多 300 字符、答案最多 1,200 字符。
- 首期使用确定性的中英文词元、中文二元组、关键词、分类和问题字段混合评分；分数降序、同分按较新主键排序。无相关命中时返回空，不用旧的前 100 条兜底。
- `DeepSeekGuestAssistant.respond()` 只用当前问题检索；`FaqDraftJobService._build_approved_knowledge()` 用候选标准问题分别检索中英文，避免 FAQ 草稿链继续注入全部知识。
- `SQLAlchemyKnowledgeRepository.list_active()` 继续作为唯一审核知识来源；首期不增加向量数据库、Embedding 调用或第二套索引，待数据规模和召回证据确实需要时再升级。

### 百居易工具边界

- 新增共享 `validate_stay_date_range()`：先解析严格 ISO 日期，再检查入住日不早于武汉当天、提前不超过 365 天、退房晚于入住、住宿不超过 30 天。
- `HostexReadOnlyToolExecutor.execute()` 必须在任何 `list_properties()`、`list_availabilities()` 或 `list_reference_prices()` 调用前完成校验；非法参数对应百居易调用次数必须为 0。
- `AdminDebugService._validate_context()` 复用同一验证器，删除重复日期规则；工具 JSON schema 同步声明边界，但本地验证仍是最终安全门。

### 审批隐私数据

- `BookingApproval` 增加 `guest_name_ciphertext`、`guest_mobile_ciphertext`、`special_requests_ciphertext` 和 `pii_purged_at`；三个密文用途分别为 `approval_guest_name`、`approval_guest_mobile`、`approval_special_requests`。
- 新增 `ApprovalSensitiveData`，统一负责从 `BookingRequest` 加密和按需解密；`ApprovalService.create_pending()`、`BookingService._build_create_request()`、`_reconcile_or_mark_review()`、`ApprovalPageService.get_detail()` 和 `list_pending()` 只通过该服务接触明文。
- 审批模板改用不含密文字段的只读视图模型，不直接把 SQLAlchemy 实体交给 Jinja。
- 真实历史数据要求两次发布：`0022` 增加密文字段并提供临时双读/双写和有界回填；核对全部成功后，`0023` 删除明文字段及兼容分支。兼容层只服务真实旧数据，不能永久保留。
- 保留策略：`BOOKED` 在退房 30 天后清空审批 PII；`REJECTED`、`CONFLICT` 在终态 90 天后清空；`PENDING`、`CREATING`、`NEEDS_REVIEW` 永不自动清空。保留审批状态、日期、金额、百居易编号和审计证据。

### 构建与浏览器安全

- `requirements.lock` 使用精确版本和哈希；`Dockerfile` 使用固定 digest、TLS 校验索引、锁文件安装、非 root 用户，并删除 `PIP_TRUSTED_HOST`。
- 新增 `.dockerignore` 排除 `.env`、`.git`、虚拟环境、数据、备份、worktree 和受保护项目总结文件；不改变运行时业务行为。
- `AdminNoStoreMiddleware.__call__()` 只为 `/employee` 增加 `Content-Security-Policy: frame-ancestors 'none'`、`X-Frame-Options: DENY` 和 `Referrer-Policy: no-referrer`；本阶段不扩展为全站完整 CSP。
- 新增最小 CI，覆盖 Ruff、Mypy、无真实外联的 Pytest、全新 Alembic 升级和 Docker 构建；仍按风险指纹只运行一次最终综合门禁。

### 预定修改文件

- 阶段 0：`compose.yaml`、`src/homestay_bot/config.py`、`src/homestay_bot/services/private_file_storage.py`、`src/homestay_bot/application.py`、`tests/unit/test_private_file_storage.py`、`tests/unit/test_compose_security.py`。
- 阶段 1：`src/homestay_bot/services/model_budget.py`、`services/knowledge_service.py`、`services/stay_date_range.py`、`services/faq_draft_job.py`、`integrations/deepseek_client.py`、`integrations/deepseek_delivery_rewriter.py`、`repositories/knowledge.py`、`services/admin_debug_service.py` 及对应聚焦测试。
- 阶段 2：`domain/models.py`、`services/approval_sensitive_data.py`、`approval_service.py`、`booking_service.py`、`approval_page_service.py`、`repositories/retention.py`、`application.py`、审批模板、`0022`/`0023` 迁移和审批/保留测试。
- 阶段 3：`pyproject.toml`、`Dockerfile`、`.dockerignore`、`requirements.lock`、`.github/workflows/ci.yml`、`src/homestay_bot/middleware.py`、`tests/unit/test_compose_security.py`、`tests/integration/test_admin_hardening.py`。
- 阶段 3 实施中经 SCA 发现 `cryptography>=44,<46` 只能锁入存在当前安全公告的 `45.0.7`；用户确认将 `pyproject.toml` 纳入范围，升级到已通过无漏洞扫描和加密聚焦回归的 `>=50.0.1,<51`，再重建哈希锁。

## 第三段：待确认的迁移、回滚与验收门禁

### 阶段 0：私有上传持久化

- 红测先证明当前 Compose 没有 API 上传挂载、只读目录会在启动探针失败；实现后运行存储、应用启动和 Compose 聚焦测试，以及 `docker compose config`。
- 生产修改前备份 `.env`、Compose、源码 bundle、PostgreSQL custom dump和旧容器私有上传目录；记录文件数量、总字节数和逐文件 SHA-256，输出只包含随机文件编号与校验结果，不输出图片内容。
- 如果旧容器目录不存在但数据库仍有附件/凭证引用，立即停止部署并报告既有缺失，禁止创建空目录冒充迁移成功。
- 切换后连续重建 API 两次；既有文件数量、校验和必须一致，并通过已授权入口读取代表性任务附件与凭证图片。失败时恢复旧 API/Compose，保留新宿主机副本供排查，不删除任何文件；本阶段没有数据库迁移。

### 阶段 1：模型预算、知识检索与工具校验

- 红测覆盖：主链最坏调用次数不超过 3；所有生产调用都有 `max_tokens`；单请求/累计字符超限确定性降级；工具结果保持有效 JSON；超过 100 条知识时能找出较新的相关项并排除无关项；非法日期触发百居易调用 0 次。
- 实现冻结后运行 DeepSeek 客户端、投递改写、知识服务、FAQ 草稿、管理员调试聚焦测试，随后 Ruff、Mypy、`pip check` 和一次全量无外联回归。确定性证据充分时不调用真实模型。
- 只有供应商协议兼容仍有证据缺口时，最多执行一次真实 DeepSeek 验收：输入小于 4,000 字符、无工具、`max_tokens<=256`，记录调用次数和 token 数，不记录问题或回复正文。
- 本阶段无迁移；回滚只需恢复上一版本代码/镜像。生产验收检查版本、健康、近期日志、预算数值日志和只读知识检索；企业微信客人实际收件仍是单独验收，不由健康检查替代。

### 阶段 2A：审批密文列与历史回填

- `0022` 只增加 nullable 密文列和 `pii_purged_at`，不删除旧列；先验证 SQLite 升级/降级/再升级和 PostgreSQL 离线 SQL。
- 红测覆盖用途隔离、密文不含明文、错误用途不能解密、审批下单与核验仍使用原值、模板不接触 ORM 密文字段、旧记录双读和新记录临时双写。
- 生产先做数据库备份；回填按每批 100 条短事务执行，支持幂等续跑。完成门禁为：审批总数=三种必需密文完整数，逐条解密比对成功数=总数，失败数=0；日志只记录计数和记录编号，不记录明文/密文。
- 任一回填失败时停止并保留 `0022` 与旧列，不进入 2B；旧版代码仍可回滚运行。
- 2026-08-31 发布前调研时，生产 `booking_approvals` 总数、姓名非空数、手机号非空数和特殊需求非空数均为 0；2A 仍保留双读/双写和每批最多 100 条的幂等回填能力，以覆盖调研后新增记录与旧版回滚，但不新增无事实依据的自动回填循环。正式发布前必须重新计数，再决定是否实际执行回填。

### 阶段 2B：删除明文与启用保留期

- 进入条件：2A 已稳定运行、回填门禁全部通过、最新数据库备份可列出且完成一次隔离恢复演练。
- `0023` 删除三列明文和临时双读/双写；保留期只清空符合条件的密文，不删除审批主记录。红测证明待处理/创建中/需复核永不清空，已预订按退房 30 天、拒绝/冲突按终态 90 天清空。
- 由于删列回滚不能依赖普通 Alembic downgrade，2B 的唯一生产回滚是恢复升级前 PostgreSQL 备份并切回旧镜像；迁移失败必须由事务回滚，禁止带着半迁移结构启动 API。
- 验收检查数据库不存在旧列、全部未清理记录可解密、后台审批列表/详情可读、下单状态机聚焦测试通过；生产不创建真实百居易订单。

### 阶段 3：供应链、非 root 与响应头

- 红测覆盖基础镜像 digest、锁文件哈希、无 `PIP_TRUSTED_HOST`、非 root `USER`、`.dockerignore` 敏感排除，以及后台防嵌套/Referrer 响应头。
- 非 root 发布前先验证宿主机上传目录 UID/GID 和读写权限；失败则不启动新镜像。回滚恢复旧镜像，上传目录和数据不回退、不删除。
- 最终验证为锁文件重建、Docker build、容器用户检查、`pip check`、全新 Alembic 升级、Ruff、Mypy、一次全量无外联 Pytest；依赖和构建指纹未变化时不重复运行。
- 生产验收分别核对源码提交、服务器部署副本、运行镜像、应用版本、数据库 revision、上传目录读写、本机/公网健康、Nginx 后真实响应头、容器重启次数和近期错误日志。

### 统一发布规则

- 每个阶段独立版本、正式更新日志、提交、标签、备份目录和 Review 证据；前一阶段未验收不得开始后一阶段。
- 默认不推送、不部署，除非实施阶段用户再次明确授权对应外部动作；本次“开始”只授权在完整 Spec 确认后进入本地编码与验证。
- 全程不读取、暂存或提交未跟踪的 `YuMi民宿AI项目总结.txt`；不主动发送企业微信消息，不执行真实百居易写操作。

## 阶段 0 本地 Review

- 红测先得到私有写入探针缺失、Compose 持久挂载缺失共 `3 failed, 5 passed`，生命周期接入红测单独为 `1 failed`；实现后相关存储、Compose 与完整运行时启动文件为 `21 passed`。
- `PrivateFileStorage.verify_writable()` 使用随机探针完成创建、写入、`fsync`、关闭和删除；应用在数据库、外部客户端和 worker 启动前执行一次，失败会直接阻断启动。
- Compose 把容器内目录固定为 `/app/data/private_uploads`，宿主机通过 `${PRIVATE_UPLOAD_HOST_DIR:-./data/private_uploads}` 持久挂载；项目现有 `.gitignore` 已排除默认目录。
- Ruff 受影响文件通过；Mypy 两个受影响源码文件通过；`git diff --check` 通过。Docker CLI 在本机不存在，`docker compose config` 留到服务器部署前执行。
- 版本已升级为 `1.3.4` 并补充正式更新日志；版本测试 `5 passed`，标准 wheel 构建成功且元数据为 `1.3.4`。
- 本地冻结时尚未提交、推送或修改生产；后续生产证据单独记录在下方，避免用本地结果替代运行态验收。

## 阶段 0 生产 Review

- 功能提交 `255ba9c` 和正式标签 `v1.3.4` 已推送 GitHub，并通过本机与服务器双重验证的完整 Git bundle 快进到生产；服务器原有 3 个未跟踪配置备份保持不变。
- 生产预检确认旧容器 `data/private_uploads` 存在但文件数为 0，数据库 `task_attachments` 和 `room_credentials` 引用数也均为 0，不存在需要人工恢复的既有缺失文件。
- 备份位于 `/opt/yumi-backups/v1.3.4-20260830T130258Z`，包含旧提交、权限 600 的 `.env`、Compose、源码 bundle、部署 bundle、旧上传目录清单和 PostgreSQL custom dump；主机缺少 `pg_restore` 后改由 PostgreSQL 容器验证同一归档，未重复或覆盖备份。
- 旧运行容器的镜像内容层已不可直接引用，`docker commit` 也因缺少 content digest 失败；在现网未切换时从旧提交 `f3b663a` 独立重建并保留 `rollback-v1.3.3` 镜像，补齐即时回滚能力后才继续部署。
- 生产 `.env` 已设置 `PRIVATE_UPLOAD_HOST_DIR=/opt/yumi-data/private_uploads` 且权限保持 600；Compose 解析确认 bind mount 来源为该目录、目标为 `/app/data/private_uploads`，宿主机目录权限为 700。
- 使用非业务哨兵连续强制重建 API 两次，容器编号依次变更为 `8e1ee68c...` 和 `d58159dc...`；两次健康后哨兵都可在容器挂载内读取，随后已安全删除，最终上传文件、数据库引用和遗留写入探针均为 0。
- PostgreSQL 容器始终为 `af11bb4a...` 且重启次数为 0；API 重启次数为 0，Alembic current/head 均为 `0021_task_lifecycle`。
- 容器包版本和公网 OpenAPI 均为 `1.3.4`，本机与公网 `/health` 均为 `ok`，部署后 15 分钟日志中 `ERROR`、`Traceback`、`Exception` 计数为 0。
- 本次发布未调用 DeepSeek、百居易或企业微信真实接口，没有发送真实消息；由于生产原本没有附件或凭证文件，授权页面读取验收不适用，改由目录清单一致性和跨两次容器重建哨兵证明持久化。

## 阶段 1 本地 Review

- 红测先得到 7 项失败，分别证明相关知识无法检索、主回复和投递改写缺少输出上限、持续工具请求超过三次、非法日期会在本地校验前触达百居易。
- 新增唯一 `ModelBudget`，主请求上限 48,000 字符、主链累计 120,000 字符、最多 3 次主模型调用和 2 轮工具结果；主回复 `max_tokens=1800`，精炼和投递改写均为 900。
- 历史消息、当前问题、客户上下文、FAQ 候选和工具结果均按确认预算裁剪；超大工具结果保持有效 JSON，累计预算触发时优先返回已有房态安全回执，否则停止调用。
- 审核知识改为按当前问题进行确定性中英文混合召回，最多 8 条和 12,000 字符；超过 100 条时仍能命中后置相关知识，无相关命中返回空。FAQ 草稿按候选标准问题召回，不再注入前 100 条全量知识。
- 房态和参考价查询在任何百居易调用前复用严格住宿日期验证；管理员调试入口使用同一规则，非法日期的百居易调用次数为 0。
- 聚焦回归 `157 passed`；最终无外联全量回归 `1211 passed, 15 skipped`，跳过项仅为显式关闭的真实 DeepSeek、百居易和企业微信契约测试。Ruff、116 个源码文件 Mypy、`pip check` 和 `git diff --check` 均通过。
- 本阶段本地实现和验收没有调用真实 DeepSeek、百居易或企业微信，没有产生模型 token 费用；受保护的未跟踪项目总结文件保持未读、未改、未提交。正式发布证据记录在下方。

## 阶段 1 生产 Review

- 功能提交 `5e4d610` 和正式标签 `v1.3.5` 已推送 GitHub；服务器从阶段 0 部署记录提交 `afaeea0` 快进到同一功能提交，既有 3 个未跟踪配置备份文件保持不变。
- 生产备份位于 `/opt/yumi-backups/v1.3.5-20260830T151245Z`，包含旧提交、权限 600 的 `.env`、Compose、完整源码 bundle、上传目录归档和 PostgreSQL custom dump；源码 bundle 与数据库归档均已验证并生成 SHA-256 清单。
- 切换前把原运行镜像 `sha256:5526daf...` 保留为 `rollback-v1.3.4`；新 API 镜像为 `sha256:394a33c...`，只强制重建 API 容器。
- PostgreSQL 容器继续为 `af11bb4a...` 且重启次数为 0；新 API 容器为 `92c6fffb...`、重启次数为 0，Alembic 仍为 `0021_task_lifecycle (head)`。
- 容器包版本和公网 OpenAPI 均为 `1.3.5`，本机与公网 `/health` 均为 `ok`；容器内预算值为主请求 48,000 字符、累计 120,000 字符、3 次主调用、2 轮工具结果和 1,800/900/900 输出 token 上限。
- 私有上传挂载仍为 `/opt/yumi-data/private_uploads -> /app/data/private_uploads`，最终文件数为 0；部署后近 10 分钟日志中 `ERROR`、`Traceback` 和 `Exception` 计数为 0。
- 本次部署验收未主动调用 DeepSeek、百居易或企业微信，没有发送真实消息；公网后台登录页不展示版本号，运行版本以容器包和公网 OpenAPI 双重验证。

# 当前任务：v1.3.3 综合代码审查

- [x] 建立应用入口、信任边界、高价值资产和外部输入清单
- [x] 运行 Python/SAST、密钥、危险 API 与依赖配置扫描
- [x] 审查鉴权、CSRF、IDOR、上传、SSRF、日志脱敏和外部发送边界
- [x] 审查事务、并发、任务幂等、错误处理和后台调度一致性
- [x] 对候选问题做可达性与测试证据人工复核，排除误报
- [x] 按严重度输出发现、代码依据、影响和修复建议

## 审查边界

- 以当前 `main` 提交为对象，只读审查 `src/`、`migrations/`、`deploy/`、`compose.yaml`、`Dockerfile`、`pyproject.toml` 和相关测试。
- 不读取、暂存或提交未跟踪的 `YuMi民宿AI项目总结.txt`；不修改业务代码、数据库、生产环境或外部服务。
- 自动扫描结果必须回到具体调用链人工验证；没有可达性或影响证据的命中不列为正式问题。

## Review

- 审查基线：`main` / `767447d70213bc623303458fadb84b14658bc461`，业务代码、数据库、生产和外部服务均未修改。
- 高风险发现：Compose 未持久化私有上传目录；主模型链路缺失完整 token/调用预算；审核知识无相关性检索且单轮最大可达 1,063,121 字符；模型工具日期在本地边界校验前已传给百居易；预订审批姓名、手机号和特殊需求明文且无保留期。
- 中风险发现：构建依赖无锁文件/哈希且使用受信任的第三方镜像；应用中间件未自行设置防嵌套响应头，需与 Nginx 现场配置联合确认。
- 已验证：Ruff 通过；Mypy 114 个源文件通过；`pip check` 通过；全新 SQLite 从基线升级至 `0021_task_lifecycle (head)` 通过；Pytest `1199 passed, 15 skipped`。跳过项仅为未显式开启的真实 DeepSeek、百居易和企业微信契约测试。

# 当前任务：后台筛选链路全面审查与修复准备

- [x] 枚举所有后台筛选、搜索、页签和分页入口
- [x] 复现任务中心组合筛选失效并建立参数证据链
- [x] 对照模板字段、URL、路由、服务查询和分页状态审查全部页面
- [x] 按严重度列出真实缺陷、测试缺口和精确修改文件
- [x] 分段确认功能规则、风险边界和最小验证方案
- [x] HARD-GATE：用户已明确授权完成编码、推送和生产部署
- [x] 增加浏览器原生查询字符串红测并保留 6 项失败证据
- [x] 实现共享空值规范化、规范 URL 和房态范围修复
- [x] 完成聚焦后端、浏览器、静态检查和发布包验证
- [x] 更新 v1.3.3 版本号与正式更新日志
- [x] 提交、标记 v1.3.3 并推送 GitHub
- [x] 完成生产备份、部署和真实后台验收

## 审查边界

- 现状审查阶段只读取代码、测试和真实后台；用户完成三段确认后才进入红测和实现。
- 审查目标是现有筛选状态的端到端一致性，不顺带重做视觉样式、导航结构或业务状态机。
- 后续修复优先复用现有 GET 查询参数和仓储查询，不引入前端框架、第二套筛选状态或客户端假过滤。
- 受保护的未跟踪文件 `YuMi民宿AI项目总结.txt` 保持未读、未改、未提交。

## 待确认的功能规则

- 浏览器原生 GET 表单提交的空字符串统一视为“未启用该筛选”，后端不得因此返回 422；非法非空值仍保持 422，不能静默放宽校验。
- 任务、客户和知识库筛选使用同一空值规范化规则；前端只从最终 URL 恢复选中状态，不增加客户端假筛选或第二份状态。
- 三个筛选表单在支持 JavaScript 时删除空参数，生成简洁、可复制、可刷新和可翻页的 URL；无脚本、旧书签或手工带空参数访问时由服务端容错。
- 房间近期运营把 `days` 按浏览器实际传入的字符串白名单解析，再显式转换为服务所需整数；只允许 3、7、14，其他值继续返回 422。
- 点击“筛选”始终回到第一页；翻页继续只保留非空有效筛选；“重置”和现有快捷页签语义不变。
- 不修改仓储 SQL、任务/客户/知识业务规则、数据库结构、视觉布局或外部接口。

## 预定修改文件

- `src/homestay_bot/routes/query_params.py`：新增可复用的空查询值规范化函数。
- `src/homestay_bot/routes/tasks.py`、`customers.py`、`knowledge.py`：在保留强类型校验的前提下接收空筛选值。
- `src/homestay_bot/routes/admin.py`：修正 3/7/14 天字符串到整数的边界转换。
- `src/homestay_bot/templates/tasks/index.html`、`customers/index.html`、`knowledge/index.html`：显式标记需要规范化的 GET 筛选表单。
- `src/homestay_bot/static/admin.js`：在表单数据生成阶段删除空筛选参数，不接管服务端筛选。
- `tests/integration/test_task_routes.py`、`test_customer_routes.py`、`test_knowledge_routes.py`、`test_admin_dashboard_routes.py`：增加真实查询字符串红测。
- `tests/browser/test_admin_interactions.py`：验证组合筛选生成规范 URL，并保留非空参数。

## 待确认的风险与验收门禁

- 强类型风险：空值规范化必须发生在 FastAPI/Pydantic 的枚举、日期和整数解析之前；用真实路由请求红测证明，不用只测辅助函数代替。
- 校验放宽风险：只把空值或纯空白视为未筛选；未知枚举、非法日期、零/负数编号和非 3/7/14 的房态范围继续返回 422。
- JavaScript 兼容风险：`formdata` 清理只做渐进增强；服务端容错是无脚本、旧浏览器和旧书签的最终保障，不让功能依赖脚本。
- 状态一致性风险：模板仍只按服务端解析后的筛选对象回显；不读取浏览器缓存值作为真相，不改变员工忽略管理员专属执行员工筛选的权限边界。
- 交互回归风险：清理逻辑只绑定显式标记的三个 GET 筛选表单，不影响 POST、CSRF、二次确认、未保存提醒或客户合并搜索。
- 最小红绿门禁：先证明任务空日期、客户空住宿状态、知识空启用状态、房态 3/7/14 和前端空参数清理在当前版本失败；实现后只运行对应路由、现有有效组合筛选、浏览器交互、JS 语法、Ruff 和 `git diff --check`。
- 不运行与改动无关的全量业务、数据库迁移、外部接口或真实模型测试；若聚焦测试暴露共享依赖问题，再按证据升级验证范围。
- 用户在三段方案后明确授权完成版本更新、GitHub 推送和生产部署；发布仍须经过备份与真实生产地址验收。

## Review

- 生产只读探测确认任务中心原生 GET 表单会提交空的日期、枚举和整数参数，FastAPI 在进入 `task_index` 前返回 422；只保留有效非空参数时，既有任务筛选链路可正常工作。
- 客户管理和知识库复用相同的空选项模式，生产分别以空 `stay_status`、空 `enabled` 稳定复现 422；客户合并搜索、客户/房源页签、审批/客诉/审计分页未发现同类空值问题。
- 房间近期运营存在另一独立缺陷：模板发送字符串 `days=3/7/14`，路由使用整数 `Literal[3, 7, 14]`，生产三个范围链接均返回 422。
- 现有路由测试直接构造完整有效参数，只证明参数到达后能够转发和保持分页；没有覆盖浏览器原生表单生成的空参数。房态测试只访问默认 URL，也没有点击任何范围链接。
- 红测阶段 6 项真实路由请求全部以 422 失败；最小实现后，相关后端与既有筛选用例 `13 passed`，浏览器交互和后台静态资源 `9 passed`。
- Ruff、Mypy（5 个受影响源码文件）、JavaScript 语法和 `git diff --check` 均通过；版本单测 `5 passed`，标准 wheel 元数据为 `1.3.3`。
- 没有修改仓储 SQL、业务服务、数据库结构或外部接口；开发验收未调用 DeepSeek、Hostex 或企业微信。受保护的未跟踪项目总结文件保持未读、未改、未提交。
- 发布提交 `f3b663a` 和正式标签 `v1.3.3` 已推送 GitHub；服务器以 fast-forward 更新到同一提交和标签，既有 3 个未跟踪配置备份文件保持原样。
- 生产备份位于 `/opt/yumi-backups/v1.3.3-20260830T105047Z`，包含权限 `600` 的 `.env`、Compose、源码 bundle、校验和以及经 `pg_restore -l` 验证的 PostgreSQL custom dump。
- 只重建 API 容器；PostgreSQL 容器 ID 继续为 `af11bb4a411fae8cf94df0ec6b1963eeb0dc08e8ad417475189a39fa5a39db7f`，API 和数据库重启次数均为 0，Alembic 仍为 `0021_task_lifecycle (head)`。
- API 容器包、公网 OpenAPI 均为 `1.3.3`，本机与公网 `/health` 均为 `ok`，部署后近期日志中 `ERROR`、`Traceback` 和 `Exception` 为 0。
- 真实生产域名上，任务空字段、客户空住宿状态、知识空启用状态以及房态 3/7/14 天链接均从部署前 422 变为通过参数校验后的 303 登录保护；公网 `admin.js?v=1.3.3` 已包含空筛选清理，运行镜像中 3 个筛选模板均已标记。
- 生产验收未主动调用 DeepSeek、Hostex 或企业微信，也未发送真实消息；生产启动后仅由既有运行循环按正式配置继续工作。

# 当前任务：后台克制动效与交互反馈

- [x] 分段确认现状、功能范围、风险边界和最小验收门禁
- [x] HARD-GATE：用户明确回复“开始”
- [x] 增加抽屉中断、提交反馈与 reduced-motion 红测
- [x] 完善移动抽屉与遮罩的可中断开关动画
- [x] 增加页面、提示、提交、折叠面板、按钮和页签微动效
- [x] 运行聚焦测试、静态检查和 375px/1440px 浏览器验收
- [x] 在 Review 小节记录范围与最终证据
- [x] 发布 `v1.3.2` 并完成生产备份、API 单容器部署和真实页面验收

## 实施边界

- 仅修改 `static/app.css`、`static/admin.js` 及对应资产和浏览器测试，不改模板、Python 业务代码、路由、数据库或外部接口。
- 只动画 `opacity` 与 `transform`；不使用 `transition: all`、布局动画、装饰循环、第三方动画库或逐卡交错效果。
- 保留原生 `details`、表单提交语义、抽屉 `inert`/ARIA 和焦点管理；`prefers-reduced-motion` 下关闭所有非必要动画。
- 初始编码阶段只允许本地验证；用户随后另行授权推送 GitHub，并明确要求直接发布 `v1.3.2` 到生产。

## Review

- 抽屉与遮罩改为可中断的 `transform` / `opacity` 动画；关闭期间保留焦点和可访问状态，结束后再恢复，快速“关闭后立即重开”不会被旧回调反向关闭。
- 页面、状态提示、提交按钮、折叠面板、按钮和页签均增加 160–180ms 的克制反馈；唯一持续动画是提交等待指示，不使用 `transition: all`、布局动画或第三方动画库。
- `prefers-reduced-motion: reduce` 下关闭所有非必要动画并保留静态终态；原生 `details`、无脚本导航、表单提交语义及抽屉 `inert` / ARIA / 焦点管理保持有效。
- 红测阶段 5 项动效契约全部失败，实现后转绿；最终聚焦门禁为 `22 passed`，覆盖静态资源、真实 Chromium 交互和后台路由兼容，375px / 1440px 均无横向溢出。
- Ruff、`node --check` 和 `git diff --check` 均通过；未运行无关全量后端测试，未调用 DeepSeek、Hostex 或企业微信，外部模型 token 消耗为 0。
- `8634f25` 与正式标签 `v1.3.2` 已推送 GitHub；版本测试 `5 passed`，标准 wheel 构建成功且包内元数据为 `1.3.2`，此前冻结的动效门禁仍为 `22 passed`。
- 生产备份位于 `/opt/yumi-backups/v1.3.2-20260830T025758Z`，包含旧提交、权限 `600` 的 `.env`、Compose、源码 bundle 和经 `pg_restore -l` 验证的 PostgreSQL custom dump。
- 服务器源码以 fast-forward 更新到 `8634f25`；GitHub 标签拉取遇到 TLS 中断后，使用本地和服务器双重验证的 Git bundle 同步同一 `v1.3.2` 标签，没有覆盖 3 个既有未跟踪文件。
- 只重建 API 容器；PostgreSQL 容器 ID 保持 `af11bb4a411fae8cf94df0ec6b1963eeb0dc08e8ad417475189a39fa5a39db7f`，已连续运行两周且重启次数为 0。API 重启次数为 0，Alembic current/head 均为 `0021_task_lifecycle`。
- 本机与公网 `/health` 均为 `ok`，公网 OpenAPI 版本为 `1.3.2`，受保护健康与诊断入口均返回预期 `401`，部署后近期日志中 `ERROR`、`Traceback` 和 `Exception` 均为 0。
- 已刷新真实登录后台 `/employee/tasks`：侧栏显示 `v1.3.2`，浏览器实际请求 `app.css?v=1.3.2` 与 `admin.js?v=1.3.2`；页面进入动画和按钮过渡已生效，1920px 无横向溢出，控制台无错误。
- 部署验收没有主动调用 DeepSeek、Hostex 或企业微信，也没有发送真实消息；生产启动后仅由既有运行循环按正式配置继续工作。受保护的未跟踪文件 `YuMi民宿AI项目总结.txt` 未读取、未修改、未暂存。

# 当前任务：任务生命周期治理与房间近期运营看板 v1.3.1

- [x] 完成生产积压、重复计数、任务状态机和七日房态现状审查
- [x] 分段确认过期治理、房间工作区和近期运营时间轴方案
- [x] HARD-GATE：用户明确回复“开始”
- [x] 先增加红测：唯一事项、自动失效安全边界、逾期保留和房间时间轴
- [x] 新增 0021 迁移、任务失效元数据和确定性生命周期服务
- [x] 将订单取消/改期与每小时兜底巡检接入同一任务治理规则
- [x] 修正待关注唯一事项计数，升级任务队列筛选和房间聚合
- [x] 将七日矩阵升级为响应式房间近期运营看板，并保留紧凑总览
- [x] 增加巡检心跳、积压和失败诊断，不调用模型或外部发送接口
- [x] 更新 v1.3.1 版本与正式更新日志
- [x] 运行最小充分验证、生产备份、迁移、部署与真实页面验收

## 实施边界

- 自动失效只处理订单已取消或提醒窗口已确定结束、且尚未开始执行的任务；已分派、执行中、待检查、有附件或检查清单的任务不得自动关闭。
- `EXPIRED` 表示业务价值已经消失，不能冒充 `COMPLETED`；管理员主动取消继续使用 `CANCELLED`。
- 房态拆分为 Hostex/百居易订单事实形成的入住状态，以及本地任务证据形成的运营准备状态；同步过旧时显示待确认。
- 不引入前端框架、图表库、第二套任务实现或模型判断；不调用真实 DeepSeek、Hostex、企业微信，不发送真实消息。
- 未跟踪的 `YuMi民宿AI项目总结.txt` 保持未读、未改、未提交。

## Review

- 红测先后证明新终态尚不存在、提醒与派生任务被重复计数；实现后生命周期、唯一事项、房间聚合和来源过旧用例均转绿。
- `0021_task_lifecycle` 已通过真实 SQLite 升级、逐级降级、再次升级及 PostgreSQL 离线 SQL；当前唯一迁移头为 0021。
- 自动失效严格限制在未分派、未开始且无清单/附件的任务；订单同步立即复核，每小时再以 100 条批次兜底，健康页可识别巡检停滞。
- 浏览器验收覆盖 1440px 与 375px；移动端页面和房间时间轴均无横向溢出，3/7/14 天范围可恢复到 URL，14 天视图正确显示 17 个日期格。
- 最终全量回归为 `1186 passed, 15 skipped`，唯一失败是旧 Hostex 测试桩缺少新增构造参数；修正后受影响运行时组 `12 passed`。Ruff、mypy（113 个源文件）、compileall、pip check、wheel 构建、版本元数据和 diff 检查均通过。
- 开发和离线验收未调用 DeepSeek、Hostex 或企业微信真实接口，外部模型 token 消耗为 0，未发送真实消息；生产启动后仅由既有运行循环按正式配置继续同步和补拉。
- 生产迁移首次暴露 Alembic revision 超过 PostgreSQL `varchar(32)` 的兼容缺陷；事务完整回滚且旧版服务未中断。补充迁移编号长度红测后以 `4055ce3`、`v1.3.1` 修复发布，`v1.3.0` 未进入运行态。
- 发布前备份位于 `/opt/yumi-backups/v1.3.0-20260829T185339Z`，源码 bundle 与 PostgreSQL 自定义归档均已验证；GitHub TLS 中断时使用已验证 bundle 做 `ff-only` 传输。
- 生产源码和 API 均为 `4055ce3` / `v1.3.1`，Alembic 为 `0021_task_lifecycle (head)`；本机与公网健康均为 `ok`，API 重启计数为 0，PostgreSQL 容器 ID 保持不变且重启计数为 0，近六小时日志无 WARNING、ERROR 或 Traceback。
- 已登录真实后台显示 `v1.3.1` 和“房间近期运营”看板；截至 2026-08-30 10:07（Asia/Shanghai），333 条历史任务已自动失效，且不存在仍满足自动失效条件的开放候选，已有负责人、维修和补给任务均继续保留。

# 当前任务：民宿运营后台工作流升级 v1.2.0

- [x] 完成前端页面、后端能力和角色工作流的现状审查
- [x] 确认导航、今日运营、房态、任务、审批、客诉、房源和客户方案
- [x] 确认无数据库迁移、无新外部调用、保留既有业务状态机的实施边界
- [x] HARD-GATE：用户明确回复“开始”
- [x] 先增加运营聚合、任务筛选、房源摘要、客户投影和安全交互红测
- [x] 实现今日工作台、待处理中心、订单与房态页面及客诉列表
- [x] 实现任务调度筛选和审批状态专属动作
- [x] 重构房源列表、房源详情、客户列表和客户详情
- [x] 完成知识筛选、公共前端交互和无障碍修复
- [x] 升级版本号并维护更新日志
- [x] 运行聚焦测试、静态检查、浏览器验收和一次最终全量回归
- [x] 在 Review 小节记录红测、范围、资源调用和最终证据

## 已确认实施边界

- 复用现有 Jinja、原生 JavaScript、CSS、SQLAlchemy 和 PostgreSQL，不引入前端框架或第二套业务实现。
- 新页面只读取本地同步数据；开发和验收不调用 DeepSeek、Hostex 或企业微信真实接口。
- 不新增数据库字段或迁移，不修改订单、任务、房态、凭证投递和消息发送状态机。
- 客户电话继续脱敏，客户列表不展示消息正文，门锁密码和入住指南永不回显。
- 不建设完整站内聊天、大型拖拽日历、价格编辑器或收益分析。
- 当前未跟踪的 `YuMi民宿AI项目总结.txt` 保持未读、未改。

## Review

- 红测证据：审批状态与提交保护首次为 `3 failed`，客诉列表首次返回 `404`，运营工作台路由首次返回 `404`；房源、客户和任务的组合聚焦测试首次为 `10 failed, 91 passed`，证明模板、路由和服务投影尚未接通。
- 工作流结果：后台新增“待我关注”和“房态与运营”，总览、房源、任务、审批、投诉、客户和知识库均提供明确下一步；房源和客户详情改为 URL 页签，筛选条件随分页保留。
- 数据与安全边界：只增加本地只读聚合查询和安全页面投影；没有数据库迁移，没有修改既有状态机，不加载客户消息正文，不回显门锁密码或入住指南。
- 发布元数据：项目版本升级为 `1.2.0`，`CHANGELOG.md` 已记录新增、改进及安全边界；以本 Review 记录的验收结果作为后续提交、推送与生产部署基线。
- 资源证据：开发与验收未调用 DeepSeek、Hostex 或企业微信真实接口，外部模型 token 消耗为 0；未发送真实消息。
- 验收证据：聚焦浏览器与资源测试 `10 passed`；最终全量回归 `1176 passed, 15 skipped`；Ruff、mypy（111 个源文件）、compileall 和 `git diff --check` 均通过。唯一警告为既存 Starlette/httpx 弃用提示。
- 生产页面验收发现 v1.2.0 的 656 条历史人工事项被逐条展开；v1.2.1 先归并凭证与提醒，实际页面复验又发现 332 条待确认业务任务仍逐条展开。新增第二个红测后将业务任务也按房源归并，真实总数与单条直达能力保持不变，并升级为 v1.2.2。
- 未跟踪的 `YuMi民宿AI项目总结.txt` 保持未读、未改、未纳入任务范围。

# 当前任务：升级客户记忆机制（原生链路 v2）

- [x] 现状分析：确认近期原文、分层摘要、实时订单任务和 FAQ 复核链路
- [x] 功能确认：结构化客户记忆、证据分级、冲突治理、限量召回和物理删除
- [x] 风险确认：不接入 Codex 全局记忆，不新增 Mastra/Node 旁路，不缓存动态业务事实
- [x] HARD-GATE：用户确认方案并要求开始改动
- [x] 新增客户记忆数据模型、索引和 0019 数据库迁移
- [x] 扩展既有 DeepSeek 摘要调用，同次返回结构化记忆候选
- [x] 实现证据晋级、纠正覆盖、冲突隔离、过期失效和待确认项稳定合并
- [x] 在会话回复前按当前客户、当前问题和字符预算召回有效记忆
- [x] 在既有客户详情页提供记忆复核，并让摘要删除同时物理删除结构化记忆
- [x] 覆盖客户合并迁移、隐私删除和自动维护边界
- [x] 运行迁移升降级、定向测试、全量测试和静态检查

## 已确认实现边界

- 复用 `ContextRetentionService`、`DeepSeekContextSummarizer` 和 PostgreSQL，不建立第二套模型调用或独立记忆服务。
- 客户记忆严格按 `customer_id` 隔离；跨客户经验继续走已有 FAQ 候选和管理员审核链路。
- 手机号、身份证、详细地址、门锁/验证码/二维码，以及价格、房态、付款退款和当前订单状态不得进入结构化记忆。
- 实时订单、任务与 Hostex 查询始终高于历史记忆；模型推断只能成为待复核候选。
- 召回只读取有效且未到复核期的记忆，并按当前问题相关性及固定数量/字符预算裁剪。
- 修改限于客户记忆及必要依赖点；订单、房态、审批、凭证和任务状态机保持不变。

## Review

- 新增 `customer_memory_items`，以客户、主题、证据、状态、复核期和有效期治理结构化记忆；0019 迁移已通过真实 SQLite 升级、逐级降级和再次升级测试。
- DeepSeek 分层摘要仍只调用一次，并在同一 JSON 中返回记忆候选；本地会校验消息来源、敏感字段、动态业务事实和候选边界，推断不会自动晋级。
- 客户明示或员工确认的高置信稳定事实可成为有效记忆；明确纠正会覆盖旧值，同主题冲突会全部停止召回，到复核期后自动失效。
- 最近三条原文继续保留且不进入短摘要，但会独立标记“已观察”；单条“我的狗叫查理”在维护任务运行后即可供新会话召回，无需等待更多聊天消息。
- 摘要器会回传实际进入模型预算的消息编号，只有这些消息能被标记或清除；因输入预算省略的原文会留到下一轮，避免错误清理。
- 会话按当前 `customer_id` 和当前问题召回最多 8 条、2400 字符的有效记忆；本轮陈述、实时订单、任务与工具结果始终优先。
- 既有客户详情页可批准、拒绝或标记失效；删除摘要会同时物理删除结构化记忆，客户合并会迁移记忆并隔离合并冲突。
- 验收结果：`1137 passed, 15 skipped`；Ruff、mypy（108 个源文件）、compileall、pip check、Alembic head 和 diff 检查均通过。
- 本机与生产环境均已发布 `v1.0.4`；生产提交为 `fe9cf8a`，API 容器包版本为 `1.0.4`，PostgreSQL 已从 `0018` 升级到 `0019_customer_memory_items`，公网后台实际页面显示 `v1.0.4`。
- `/health` 启动后曾返回 200，随后因既存企业微信定时补拉 `WeComApiError` 变为 503 `degraded`；数据库、迁移和应用进程正常，该外部依赖故障不由本次记忆升级引入。
- 部署前备份位于 `/Users/rin/Library/Application Support/HomestayBot/.backups/pre-memory-v2-20260827-000814`。
- 生产部署前 PostgreSQL 自定义备份位于 `/opt/yumi-backups/v1.0.4-20260826T190756Z`，已通过容器内 `pg_restore -l` 校验。
- 部署时修复两个既存启动缺陷：LaunchAgent 改用安装目录自包含虚拟环境，SQLite 驱动 `aiosqlite` 改为正式运行依赖；对应防回归测试已补齐。
- 与本次任务无关的未跟踪 `YuMi民宿AI项目总结.txt` 保持未读、未改。

# 当前任务：客户记忆可信治理与时间线升级

- [x] 完成现状、漏洞与代码证据审查
- [x] 确认目标功能、行为边界、迁移和关键技术决策
- [x] HARD-GATE：用户明确回复“开始编码”
- [x] 先增加可复现红测：证据伪造、System 注入、证据降级、过期候选残留
- [x] 新增 0020 迁移、记忆事件时间线和单活跃值约束
- [x] 实现集中的脱敏、引用校验、证据单调性和受控主题策略
- [x] 实现摘要乐观并发、历史记忆本地重验证和全状态清理
- [x] 将动态客户数据移出 System prompt，建立上下文预算与副作用授权门
- [x] 升级管理后台的来源证据、当前值对比和版本冲突处理
- [x] 运行聚焦测试、Ruff、mypy 和一次最终全量回归
- [x] 在 Review 小节记录红测、迁移、资源用量与最终验收证据

## 实施边界

- 复用现有 Python、SQLAlchemy、PostgreSQL 和 OpenAI 兼容模型接口，不新增 Mastra、Node、向量库或第二记忆服务。
- 只改客户记忆及必要的上下文、审核和副作用出口；不重构订单、FAQ、凭证和任务状态机。
- 开发期间不调用真实 DeepSeek；最终最多运行一次有硬预算的摘要契约测试。
- 不部署、不升级版本号、不修改发布日志；未跟踪 `YuMi民宿AI项目总结.txt` 保持不动。

## Review

- 红测证据：`tests/integration/test_context_repository.py` 的 3 个聚焦用例首次运行均失败，分别证明无关客人消息可被误当强证据、重复模型推断会覆盖客人明示证据、过期 1000 天的候选仍不退出待审池。
- 红测证据：`tests/unit/test_deepseek_client.py` 的 3 个信任边界用例首次运行均失败，证明动态客户数据尚未使用结构化 user envelope，且模型可在本轮无服务或预订确认意图时保留任务建议和 `booking_confirmed`。
- 数据层升级：新增 `0020_memory_trust_timeline`，补齐来源摘录、来源哈希、核验时间、内容清理时间、行版本、记忆事件时间线及“每客户每主题最多一条有效值”的数据库约束；迁移测试覆盖旧数据重分类和升降级。
- 证据治理：只有可逐字回查且值与受控主题一致的客户/员工原文才能形成强证据；模型推断不会覆盖强证据，明确纠正才替换当前值，其他同主题冲突统一隔离为争议状态。
- 生命周期与并发：候选、争议和有效记忆均有退出路径，终态正文 90 天后脱敏、事件 365 天后删除；摘要写入和后台审核均使用期望版本，冲突时保留原消息并要求刷新。
- 上下文边界：实时订单、开放任务、相关记忆、近期情节和按需历史情节共享硬字符预算；动态数据使用结构化 user envelope，不再进入 System prompt；任务和预订副作用同时受当前问题意图与业务规则门控。
- 后台复核：详情页显示来源摘录、来源时间、状态原因和同主题当前有效值；历史待确认项改为只读，终态记忆不能被错误重新批准。
- 资源证据：开发和最终验收均未调用 DeepSeek，模型 token 消耗为 0；真实外部契约测试保持显式开关关闭。
- 验收证据：聚焦回归 `220 passed`；最终全量回归 `1159 passed, 15 skipped`；Ruff、mypy（109 个源文件）、Alembic 单一 head 和 `git diff --check` 通过。唯一警告为既存 Starlette/httpx 弃用提示。
- 发布边界：本次没有部署、没有修改版本号或发布日志；未跟踪 `YuMi民宿AI项目总结.txt` 保持未改。

# 当前任务：发布并部署 v1.1.0

- [x] 确认本地 Git、生产容器、数据库迁移和公网健康基线
- [x] HARD-GATE：用户明确要求直接发布
- [x] 将应用版本升级为 `1.1.0` 并建立正式发布日志
- [x] 运行发布级静态检查、构建检查和全量测试
- [x] 提交发布代码、创建 `v1.1.0` 标签并推送 GitHub
- [x] 创建并验证生产源码、配置和 PostgreSQL 备份
- [x] 仅重建 API，保持 PostgreSQL 容器连续运行
- [x] 验收生产 HEAD、版本、迁移、健康、重启次数、日志和后台页面

## 发布边界

- 发布对象为当前已验收的客户记忆可信治理与时间线升级，不附带无关功能。
- 生产数据库从 `0019_customer_memory_items` 升级到 `0020_memory_trust_timeline`。
- 不运行真实 DeepSeek、Hostex 或企业微信契约测试，不发送真实企业微信消息。
- 服务器既存 `.env` 备份和本地未跟踪 `YuMi民宿AI项目总结.txt` 均保持不动。

## Review

- 发布提交和 `v1.1.0` 标签均为 `618c1b109f4f629fa8dedde372e2079b56b39394`，已推送 GitHub。
- wheel 构建产物为 `homestay_bot-1.1.0-py3-none-any.whl`，元数据版本为 `1.1.0`。
- 发布门禁：Ruff、mypy（109 个源文件）、compileall、pip check、Alembic 单一 head、diff 检查和全量回归通过；全量结果为 `1159 passed, 15 skipped`。
- 生产备份位于 `/opt/yumi-backups/v1.1.0-20260829T060436Z`，包含 `.env`、Compose、旧源码 bundle 和 PostgreSQL custom dump；dump 已通过 `pg_restore -l`，敏感文件权限为 `600`。
- 生产源码以已验证 Git bundle fast-forward 到发布提交；tracked worktree 干净，3 个既存 `.env` 备份保持未跟踪且未改。
- 只重建 API；PostgreSQL 容器 ID 仍为 `af11bb4a411f`，启动时间仍为 `2026-08-10T16:43:53Z`，未发生重启。
- 生产容器版本与公网 OpenAPI 版本均为 `1.1.0`；Alembic current/head 均为 `0020_memory_trust_timeline`。
- 本地及公网 `/health` 均返回 `ok`；API restart count 为 `0`，部署后十分钟日志异常标记为 `0`。
- 实际公网后台登录页已通过浏览器渲染，标题为“管理员登录 · YuMi”；管理员诊断地址未登录返回预期 `401`。
- 当前浏览器没有管理员登录态，未读取登录后侧栏版本；该展示与容器/OpenAPI 共用 `pyproject.toml` 的安装包版本源。
- 真实 DeepSeek、Hostex、企业微信契约测试保持关闭，本次未发送真实企业微信消息。
- 本地未跟踪 `YuMi民宿AI项目总结.txt` 保持未改且未进入发布提交。
