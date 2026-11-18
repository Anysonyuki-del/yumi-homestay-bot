# YuMi 全仓审计修复 · Codex 交接 Claude 复审

**2026-11-18，待独立代码审查。** 请审查本地 `main` 工作区相对 `6f16d5e4706ee184673b22ec1c30095c05ca6eb7` 的候选改动，判断能否进入发版准备。最值得先看的三条路径是客人图片重复投递、迁移前备份能否恢复、已有 PostgreSQL 卷能否安全升级。

用户先要求全仓审计并修复确定的问题，随后对待复核的七项回复「按你的理解来修复」。本轮已完成本地实施与验证。包版本仍为 `1.73.2`，候选没有提交、标签或远端 CI 运行。本次交接请求 Claude 只读审查，发现写入审查意见，生产操作另行授权。[实施 Spec](../specs/2026-11-18_audit-repairs-spec.md) · [任务与验证摘要](../../tasks/todo.md)

## 一、如何取到同一份代码

在同一个本地工作区查看。只克隆远端或只检查 HEAD，会看到修复前的实现。交接时有 **33 个已跟踪文件有差异**，其中 2 个是 AOCI 索引与基线。另有两个必须单独打开的未跟踪输入：实施 Spec 和新的共享测试夹具 `tests/conftest.py`。工作区原有的 10 月 JEV 资料、离线评估工具与夹具、界面审查草稿不在候选范围。遵循根目录 AGENTS 的受保护文件规则。

下列命令从仓库根目录运行，只读。第一条摘要覆盖 31 个有改动的已跟踪文件；后两项摘要覆盖普通 `git diff` 看不到的输入。摘要绑定交接时工作区，同事后续编辑后应重新确认差异。

```sh
git rev-parse HEAD
git diff --no-ext-diff --no-textconv --no-renames --binary HEAD -- . ':(exclude)aoci.code.txt' ':(exclude).aoci/**' | shasum -a 256
shasum -a 256 docs/specs/2026-11-18_audit-repairs-spec.md tests/conftest.py
```

| 输入 | 交接时的 SHA-256 |
|---|---|
| 已跟踪工作区补丁，排除 AOCI 资产 | `a6c9e4b6a7080d5f2803a99e114082e497136293be036d54922488b551c2253f` |
| 实施 Spec | `ba2a70b28fe616d7fd9addd0fd3ef3e83fb67185d18053467bab67aa6f218ec1` |
| 共享测试夹具 | `43a5fd5f4ad460bf9995f2531ef42c319b6d970ba9e30ee3ba4c130ef14d9512` |

## 二、七项修复及审查判据

| 编号 | 修复前的证据 | 当前实现与复核入口 |
|---|---|---|
| 1 · SQLite 备份 | WAL 中有一行已提交，单拷主库后从副本查到零行。 | `deploy/start.sh` 的版本检查与备份分支分别检查 Alembic 命令退出码，建立 SQLite 读快照，用在线备份 API 复制，再备份私有附件。请检查备份失败是否阻止迁移、到达 head 后重启是否避免重复备份。恢复用例还验证备份保留迁移前结构。[启动脚本](../../deploy/start.sh) · [启动回归](../../tests/unit/test_launch_agent.py) |
| 2 · 迁移连接串 | 合成密码含 URL 编码的百分号时，ConfigParser 插值抛错，异常正文包含完整合成连接串。 | `migrations/env.py` 在 `set_main_option` 边界转义百分号。`alembic.ini` 移除本机默认口令，保留原主机、角色、库名。请核对在线和离线路径是否都将正确 URL 交给驱动。已验证该插值错误和成功迁移输出；其他异常日志的全面脱敏仍是独立问题。[迁移入口](../../migrations/env.py) · [离线 SQL 测试](../../tests/unit/test_migrations.py) |
| 3 · 图片结果未知 | 旧 `recover_stale` 会将已领取的 `wecom_send_image` 改回 pending。外部已受理、进程尚未提交时可能再发。 | `WeComApiClient.send_image`、`_build_guest_image_handler`、`SQLAlchemyJobRepository.enqueue_unknown_image_review/recover_stale` 和员工通知组成完整链路。逐个窗口的判据见下节。[企业微信客户端](../../src/homestay_bot/integrations/wecom/api_client.py) · [发送与回调装配](../../src/homestay_bot/application.py) · [队列仓储](../../src/homestay_bot/repositories/jobs.py) |
| 4 · CRM 失效订单 | 只有一张 `canceled` 订单的合成客户命中“在住”筛选，卡片也显示“在住”。 | 领域公开 `EXCLUDED_STAY_STATUSES`。`SQLAlchemyCustomerRepository.list_customers/customer_list_facts` 复用同一 SQL 条件。请核对取消两种拼写、拒绝、过期、删除、大小写及首尾空白；正常订单仍应保留。变更限于这两处列表语义。[领域规则](../../src/homestay_bot/domain/stay_status.py) · [CRM 仓储](../../src/homestay_bot/repositories/customers.py) · [筛选和卡片行为测试](../../tests/integration/test_customer_repository.py) |
| 5 · 缺少外部配置时启动 | 本机脚本仅需数据库与上传路径，却读取完整 `Settings`，导致配置修复页启动前就因缺外部凭据退出。 | `deploy/start.sh` 改读 `BootstrapSettings`，与 `application_lifespan` 的基础配置要求一致。脚本回归使用替代 Uvicorn 入口；另用真实 Uvicorn、临时库和基础配置确认回环地址的员工登录页返回 200，正文含表单。[配置模型](../../src/homestay_bot/config.py) · [启动回归](../../tests/unit/test_launch_agent.py) |
| 6 · Compose 数据库密码 | 旧配置在新卷初始化时使用仓库中的固定口令。 | `compose.yaml` 的 `postgres.environment.POSTGRES_PASSWORD` 拒绝缺失或空值。Postgres 接收原始密码，应用连接串使用 URL 编码。真实 Compose CLI 已验证三种输入。升级已有卷前须提供该角色**当前**密码；需要轮换时另排维护窗口，保留数据卷。[Compose](../../compose.yaml) · [旧卷升级说明](../../README.md) · [配置渲染测试](../../tests/unit/test_compose_security.py) |
| 7 · worker 空转 | 空库一次 `recover_stale` 实测执行 4 次 SELECT 和 3 次 UPDATE，原先两个 worker 分片每轮领取前都扫描。 | `application._run_worker_loop` 每个分片启动即扫描，成功提交后用单调时钟计时 30 秒。五分钟过期判定、正常任务领取和通用分片负责审批恢复的职责继续适用。请看恢复提交失败、慢任务、空闲及模型分片的测试边界。[worker 装配](../../src/homestay_bot/application.py) · [时序测试](../../tests/unit/test_runtime_consumers.py) |

### 图片链路优先审查

| 故障窗口 | 预期行为 |
|---|---|
| 上传素材时发生读超时，尚未调用图片发送接口 | 停止续链，走已知未发送的失败通知。不能把上传动作当作已向客人发图。 |
| 获取发送凭据时出现传输故障、发送端点明确未建连，或平台明确限流 | 进入既有有界重试。请同时检查另一个调用方 `CredentialPartSender` 的入住凭证人工复核策略。 |
| 发送请求已写出后读超时，或回执缺少有效的整数错误码、成功回执没有有效消息编号 | 停止自动重发与续链。员工收到“发送结果未知，请核实”的通知。显式业务拒绝走对应错误码路径。 |
| 进程在领取后中断，外部结果无法判定 | 实际从 RUNNING 转为 FAILED 的图片按作业 ID 去重登记核查；清载荷与登记同事务。旧载荷缺分组或会话编号时仍可定位作业。请重点看回滚、重复恢复、两事务竞争及员工通知本身的去重。 |
| 平台返回明确成功码和有效消息编号 | 保存平台受理记录，按链顺序继续。客人最终是否收到，仍需真实收件确认。 |

持久回归覆盖正常发送、超时、限流和回执解析，以及新旧载荷的重复恢复和回滚。本轮另用临时 PostgreSQL 让两个事务同时恢复一条中断图片，受影响行数为 0 和 1，最终仅有一条员工核查任务。这项竞争验证为一次性本地实验。部署假设为当前单进程、每分片一个 worker；多实例下旧 worker 恢复运行与接管者竞争，尚无本轮验收证据。[图片链集成测试](../../tests/integration/test_reply_images.py) · [HTTP 回执测试](../../tests/unit/test_wecom_api_client.py) · [入住凭证调用方](../../src/homestay_bot/services/credential_delivery.py)

## 三、同一份差异里的其他改动

第一轮审计已有五项修改，一并交审。① `.env.example` 注释可选主密钥和首次管理员字段，避免空串触发基础配置校验。② `.github/workflows/ci.yml` 的隔离 PostgreSQL 步骤由手列三个文件改为发现七组专项。③ `tests/unit/test_repository_guest_data_guard.py::_tracked_text_files` 使用 NUL 分隔 Git 原始路径，中文文件名纳入隐私扫描。④ `.dockerignore` 排除客户端签名目录、构建缓存和本机部署材料，后端 Dockerfile 的 COPY 输入均已核对。⑤ 删除无调用方的 `DebugAssistantPort` 及其孤立导入，管理员调试仍走 `AdminDebugService.preview`。

另修了审计中出现的 SQLite 测试线程与连接回收警告。`tests/conftest.py::sqlite_factory` 在用例所属事件循环结束前释放引擎；相关内存库用例改用该夹具，两处文件库用例在会话关闭时释放连接。请检查清理时机和事务断言是否仍覆盖原行为。夹具是未跟踪新文件，遗漏它会使依赖它的用例在干净检出中失败。[共享测试夹具](../../tests/conftest.py)

## 四、已有证据与空白

| 层级 | Codex 已验证 | Claude 应如何使用 |
|---|---|---|
| 本地源码与离线测试 | 最终工作区全量 pytest **3,725 通过、84 跳过、1 个警告**。剩余为 Starlette 测试客户端弃用提示。Ruff、严格 mypy、差异检查通过；SQLite 线程与连接回收警告在最终全量中未出现。 | 总数包含当前工作区能发现的测试。远端干净检出可能因既有未跟踪用例而计数不同。按变更行为判断覆盖，避免用通过总数替代边界验证。 |
| 隔离数据库 | PostgreSQL 16 七组专项 **78 通过**；临时库升级到 head，模型漂移检查通过。另以 SCRAM 和含特殊字符的合成密码在线升级；CRM 筛选在 SQLite 与 PostgreSQL 都验证过。 | 78 项是单独运行，不能与全量通过数简单相加。临时库使用合成数据，未连接生产库。 |
| 本地进程与构建 | 临时 Uvicorn 登录表单通过。Compose 已验证配置解析。AOCI 对实施差异完成 Verify、Check 和 Guide 对齐。 | 没有本候选的最终 Docker 镜像构建、容器运行或生产登录页面证据。 |
| GitHub 与发布 | 当前候选仍在工作区。未创建候选提交、标签、版本更新日志、正式发布记录或候选 CI 结果。 | 此报告供复审。待授权发版时建立 `CHANGELOG.md` 条目和独立的 `docs/releases/<版本>.md`。 |
| 服务器、容器、生产库、公网与外部收件 | 本轮均未检查或变更。真实 DeepSeek、Hostex、企业微信调用以及图片真实收件均未执行。 | 生产服务器源码、运行容器、库迁移状态、公网登录后页面和实际收件须分别取证。历史版本的部署记录不能证明该候选已上线。 |

## 五、Claude 的复核入口与回传格式

建议先读第 3 项图片链路，再读第 1、2、6 项启动和数据安全，最后看 CRM、worker 节拍和测试夹具。若需独立复跑，在仓库根目录用以下窄范围命令即可覆盖主要持久回归；Compose 测试需要可用的 Compose CLI。

```sh
env -u YUMI_TEST_POSTGRES_URL RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q \
  tests/unit/test_launch_agent.py tests/unit/test_compose_security.py \
  tests/unit/test_migrations.py::test_postgresql_offline_upgrade_sql_reaches_head \
  tests/unit/test_wecom_api_client.py tests/integration/test_reply_images.py \
  tests/unit/test_runtime_consumers.py \
  tests/integration/test_customer_repository.py::test_customer_list_excludes_invalid_stays_from_filters_and_cards
```

如要重验 PostgreSQL，仅使用独立、可清空的本机测试库。`tests/integration/test_retention_postgresql.py::pg_engine` 会清理测试表；请先核对 `validated_test_url` 的库名、角色和主机限制。这次文档交接复用了上述实施验证结果。

请按严重度回传实际发现。每项写明**触发条件、文件与符号、实际后果、证据、最小修复和验收方法**。结尾分别给出“可进入发版准备 / 需修复后复审”、剩余证据缺口和你实际执行的检查。若没有阻断项，请直接说明。

发版准备仍须处理 `scripts/release/reply_gate.sh::REPLY_PATHS` 命中的 `domain/stay_status.py`，在发布记录中写明门禁范围及理由。上线前需核对现有卷凭据、取得可恢复备份并验证归档内容；上线后需分别核对源码、运行容器、数据库、真实生产地址和适用的图片收发。当前审查授权到本地复核为止。[发布门禁](../../scripts/release/reply_gate.sh)
