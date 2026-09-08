# YuMi 民宿项目代码审查与修复方案交接报告

日期：2026-09-08。读者：接手本项目的 Claude、其他开发者及项目负责人。本文可独立阅读，不需要之前的聊天记录。

**当前状态：已完成本地审查并复现 9 项问题；修复方案是待确认草案，尚未实施。** 本次交付仅保存报告和离线复现材料，不代表授权修改业务代码、提交、推送、部署或调用真实外部服务。

## 1. 给 Claude 的接手入口

可以把下面这段话连同本报告交给 Claude Code。若使用 Claude 网页版，需要同时提供相关源码与本节链接的附件；只有报告时可以评审方案，不能声称已核实代码或运行测试。

> 请阅读 `docs/reviews/2026-09-08_code-review-handoff-report.md`，先读取当前项目适用的 AGENTS.md，再按本文的基线、证据和文件符号复核 F-01 至 F-09。区分已复现事实、适用条件、待确认设计和未验证事项。不要把复现脚本的成功退出解释为缺陷已经修复。先输出修复 Spec，精确到文件、函数、数据变更、依赖顺序和验收标准，并列出第 6 节的待决事项。当前只授权审查和方案；完整 Spec 确认且用户明确说“开始”后，才可实施对应范围。不要自行提交、推送、部署，或调用真实 DeepSeek、Hostex、企业微信。不要读取受保护的 `YuMi民宿AI项目总结.txt`。

附件：

- [离线复现脚本](2026-09-08-evidence/reproduce_findings.py)：使用项目源码、内存 SQLite、临时照片和假外部接口。
- [复现输出](2026-09-08-evidence/reproduction-output.txt)：本次从仓库内脚本重新运行得到的九条结果。
- [原审查全量测试输出](2026-09-08-evidence/pytest-output.txt)：保留原始测试结果及警告，未在本次文档整理时重跑全量测试。

## 2. 基线、业务背景与范围

### 2.1 代码基线

本机仓库路径为 `/Volumes/02/obsidian codex/homestay-bot`。下文路径都相对仓库根目录，换机器后仍可定位。

| 项目 | 已核对的值 |
| --- | --- |
| 项目版本 | `pyproject.toml::project.version` 为 `1.11.0` |
| 原审查源码基线 | 提交 `1be527d`，tree `4b982d7b7874a877b05622702bf08212c0ce1e33` |
| 文档整理时 HEAD | `40561edb037c805f2acc4666eb35cd7167072cc8` |
| 整理时 HEAD tree | `77db9a27ec7cb57767bf4e4624b0eaf1eb16af8d` |
| 两个已提交基线间差异 | `git diff --stat 1be527d HEAD` 仅有 `tasks/todo.md`；这只说明提交之间的差异，不包括工作区改动 |
| 本次新增 | 本报告及同目录证据附件；未改业务实现 |

**整理期间发生的工作区变化**：最初检查时工作区干净，随后出现另一组未提交业务改动，涉及 `application.py`、任务批量操作、提醒处置、运营工作台及相关测试。本次文档工作没有修改或撤销它们。不能继续使用“当前业务源码未变”的前提：下文行号均为原审查基线的定位提示，当前以符号为准。原全量测试只证明原审查源码；本次九项复现的重新执行也不构成对并行改动的完整审查。

接手时先执行以下只读命令，不能把本文基线直接视为届时最新状态：

```sh
git status --short
git rev-parse HEAD
git diff --stat 1be527d HEAD
git diff --stat
git diff --cached --stat
```

如果基线提交不在本地历史中，按下文文件与符号逐项复核；不能因此假定代码相同。任何相关代码变化都需要重新验证受影响的结论。

### 2.2 理解问题所需的业务模型

| 概念 | 含义和源码入口 |
| --- | --- |
| 订单 | Hostex/百居易提供订单事实，经 `services/hostex_sync.py::HostexSyncService` 落入 `domain/models.py::StayOrder` |
| 周转保洁任务 | 订单退房后清洁房间；`repositories/operations.py::SQLAlchemyOperationsRepository.create_turnover` 以房间和服务日去重，并关联生成它的订单 |
| 房间可入住 | `services/room_readiness_service.py::RoomReadinessService.mark_ready` 验证任务执行材料并改变房态，再触发凭证评估 |
| 入住凭证 | `services/credential_delivery.py` 的安全门、投递服务和分段发送器，发送入住指南、密码、二维码；每段有独立状态 |
| 后台发送 | `application.py::TransactionalOutboxWeCom` 入队，`application.py::_run_worker_loop` 实际执行；生成与发送有时间间隔 |
| 任务过期与归档 | 过期是业务判断，归档是展示与保留状态；`services/task_lifecycle_service.py::TaskLifecycleService._reason` 决定可自动失效的范围 |

关键业务边界以当前 [AGENTS.md](../../AGENTS.md) 和 [开发经验与防回归手册](../../YuMi民宿AI开发经验与防回归手册.md) 为准。接手时按需读手册“异步时序和消息边界”“HUMAN_ACTIVE 不是全局禁答开关”“百居易接口”“房间状态、任务与凭证”“事务、日志与迁移”。不要整本手册当成当前实现证据。

特别注意：退房订单 A 与当天入住订单 B 是两个业务对象；可入住不代表可以向 A 发送 B 的凭证。逾期任务也不等于无执行价值，不能统一清空。`HUMAN_ACTIVE` 不是禁止所有机器人回复的开关。

### 2.3 审查范围与限制

本报告汇总本轮对订单同步、房态、任务、凭证、消息队列、数据清理及工程验证的确定性发现。对认证和文件访问等边界的检查未形成新的已验证利用结论；这不能证明不存在漏洞，也不能代替完整安全认证。

没有执行真实 DeepSeek、Hostex、企业微信契约调用、生产数据库操作、生产浏览器验收、真实收件验收、容器运行检查或最新依赖漏洞库扫描。本地测试通过不能推导这些结果。

## 3. 可独立重现的证据

### 3.1 运行方法及结果含义

在仓库根目录、已安装本项目开发依赖的 Python 3.12+ 环境执行。本机使用现有 `.venv`；其他机器可替换解释器路径，但不要为复现启动真实应用或加载生产数据库。

```sh
RUN_LIVE_CONTRACT_TESTS=0 RUN_DEEPSEEK_CONTRACT=0 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
.venv/bin/python docs/reviews/2026-09-08-evidence/reproduce_findings.py
```

脚本使用 `sqlite+aiosqlite:///:memory:` 和 `TemporaryDirectory`，照片、客户、订单及员工均为虚构数据。它复用 `tests/unit/test_credential_delivery.py` 的 `valid_context`、`DeliveryRepositoryStub`、`JobQueueStub`，因此需要完整仓库及开发依赖。它不启动应用 lifespan，不调用真实外部客户端；环境变量本身并不是隔离保证，隔离由脚本内的数据库和接口替身实现。

**脚本断言的是“当前缺陷出现”。九行输出及退出码 0 表示九项复现成立，不是修复验收通过。** 修复后应在正式测试中断言正确行为；旧复现可能报断言错误，需要定位是哪项缺陷已经消失，不能为了让旧脚本全绿而撤销修复。

E-01 至 E-09 的观察日期均为 2026-09-08，类型为本地源码执行；来源是上述脚本和输出文件，复现命令相同。证据映射如下：

| 证据 | 脚本函数 / 输出前缀 | 观察到的行为 | 对应发现 |
| --- | --- | --- | --- |
| E-01 | `deletion_commit_failure` / `purge_commit_failure` | 提交失败后任务和附件记录恢复，照片已消失 | F-01 |
| E-02 | `turnover_and_reschedule_and_purge` / `turnover_credentials` | 可入住动作使用退房订单，触发 `not_checkin_day`，发送任务为 0 | F-02 |
| E-03 | `credential_failure_tracking` / `credential_async_failure` | `fail_type=4` 回执未改变凭证 SENT 状态，人工任务为 0 | F-03 |
| E-04 | `stale_final_send` / `queued_final` | 更新的人工消息和接管状态已入库，旧 final 仍调用假发送接口 | F-04 |
| E-05 | `reconciliation_gap` / `reconcile_window` | 漏掉长住订单，心跳更新，运营投影显示 vacant 且来源未过期 | F-05 |
| E-06 | `turnover_and_reschedule_and_purge` / `reschedule` | 改期后旧日和新日任务同时待分派 | F-06 |
| E-07 | `turnover_and_reschedule_and_purge` / `purge_reconcile` | 已完成归档任务删除后，同一订单同步又生成待分派任务 | F-07 |
| E-08 | `utc_credential_date` / `credential_clock` | 武汉 01:00、UTC 仍在昨日时，默认日期拒绝而业务日期接受 | F-08 |
| E-09 | `credential_failure_tracking` / `credential_stale_job` | stale job 已 failed，凭证部件仍 pending，人工任务为 0 | F-09 |

证据边界：E-01 注入提交故障，只实测单条删除，批量和保留期入口由源码确认同类顺序；E-02 隔离验证订单选择，未构建完整 A 退房、B 入住双订单链路；E-03 从当前 `application.py` 的 AST 提取真实嵌套回调并注入闭包依赖，未经过企业微信网络入口；E-05 的假 Hostex 按入店日期过滤，不证明真实接口组合过滤语义；E-08 模拟 UTC 环境，未检查生产时区；E-09 只复现凭证任务，不能扩大为所有不可重放任务已实测。SQLite 不提供生产 PostgreSQL 并发锁验证。

最后一次仓库内复现退出码为 0，输出九行；运行前后，`git ls-files` 在 `src`、`tests`、`migrations`、`pyproject.toml`、`requirements.lock` 范围内的 288 个已跟踪文件内容一致。将文件名排序后，依次连接 UTF-8 文件名、NUL、文件字节、NUL，得到的 SHA-256 为 `27f64fd2893d1e01b489cacfb1881dd75652441f222b2556c723e4e918551d80`。这是当次含未提交改动的工作区输入摘要，不是 Git tree，也不包含虚拟环境或未跟踪文件。以后工作区继续变化时应重新核对。

附件 SHA-256，可用于确认 Claude 收到的材料是否完整；这是本地一致性校验，不是签名：

| 附件 | SHA-256 |
| --- | --- |
| `reproduce_findings.py` | `e63656808ce264aedb81c66bc3fd16f98424f0b0f15ddcfa6819cd9087eb86a0` |
| `reproduction-output.txt` | `70d10c81da122cdf9ea2320e41228b21b9b70899505cc5d91af0f03f271dc1de` |
| `pytest-output.txt` | `96937f5c83485834f675d49b0782d5213f5f97005b214446312f4d85a9b8b231` |

### 3.2 原审查验证记录

以下结果来自本轮此前的源码审查，整理报告时只重跑仓库内复现脚本及附件检查。没有把旧全量测试冒充为新执行。

| 验证 | 结果 | 证据范围 |
| --- | --- | --- |
| 离线全量 pytest | **1406 passed，15 skipped，6 warnings，49.15 秒** | 原始日志见附件；包含现有测试，未覆盖本文全部缺陷 |
| Ruff：`src tests migrations` | 通过 | 本地静态检查 |
| Mypy | 133 个源码文件通过 | 本地类型检查 |
| `pip check` | 无破损依赖 | 依赖约束相容性，不等于无 CVE |
| 无网络 wheel 构建 | 生成 `homestay_bot-1.11.0-py3-none-any.whl` | Python 包可构建，不等于 Docker 或生产可用 |

六项警告包括 Starlette/httpx 弃用提示、Alembic `path_separator` 弃用提示及四个 aiosqlite 后台线程 `Event loop is closed` 警告。线程清理根因尚未调查，不将其算成已修复或新增业务缺陷。

原命令，可在需要重新验证且环境就绪时从仓库根目录执行：

```sh
RUN_LIVE_CONTRACT_TESTS=0 RUN_DEEPSEEK_CONTRACT=0 \
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src tests migrations
.venv/bin/mypy --cache-dir /tmp/yumi-audit-mypy-20260908
.venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
.venv/bin/python -m pip wheel --no-deps --no-build-isolation --no-index \
  --wheel-dir /tmp/yumi-audit-wheel-20260908 .
```

## 4. 九项发现与修复目标

优先级：P1 为数据丢失或关键入住链路故障，应优先修复；P2 为特定条件下的业务一致性与可靠性缺陷。下列状态均为“已在所述本地条件下验证”，不是生产事故发生率结论。编号与此前审查一致。

### F-01 · P1 · 删除照片早于数据库提交，回滚无法恢复文件

- **位置与调用路径**：`application.py::SessionTaskPageService.purge`（1574 行起）、`purge_many`（1607 行起）先读取附件、删除文件，再另开事务删除任务并提交；`repositories/retention.py::SQLAlchemyRetentionRepository.purge_archived_tasks`（103 行起）也先删文件再删记录。
- **证据与影响**：E-01 模拟提交失败，数据库回滚而照片永久缺失。当前注释称缺失文件记录“可以修复”，但没有备份就无法重建原照片。单条删除的故障已经复现，批量删除还可能形成部分文件删除。
- **建议修复**：任务校验、数据库删除、审计及文件清理任务入队放在同一事务；提交后由现有 job worker 幂等删文件，失败保留重试依据。单条、批量、保留期删除共用相同事务约束。
- **验收**：提交失败时记录与文件都保留；提交成功但文件删除失败时有持久待清理记录；重启后可恢复清理；文件不存在视为完成；删除与恢复的并发不能删掉恢复任务的照片。不能只把文件删除移到 `commit()` 后而没有持久失败补偿。

### F-02 · P1 · 退房保洁的来源订单被当作入住凭证接收订单

- **位置与调用路径**：`services/hostex_sync.py::HostexSyncService._sync_reservation`（121 行起）→ `repositories/operations.py::SQLAlchemyOperationsRepository.create_turnover`（90 行起），绑定退房订单及退房日；`services/room_readiness_service.py::RoomReadinessService.mark_ready`（85 行起）把 `task.order_id` 传给凭证评估；`services/credential_delivery.py::CredentialSafetyRules.invalid_reason`（160 行起）要求今天是入住日。
- **证据与影响**：E-02 证明系统自然产生的退房任务使用离店客人的订单，因日期不符拒绝自动发送。当天存在新入住客人时，也未在这条路径重新选择其订单。`tests/integration/test_phase_one_flow.py::test_phase_one_order_to_ready_and_credentials_flow` 将任务服务日改为入住日，未覆盖真实周转日期关系。
- **建议修复**：保留任务的退房来源关联；房间就绪后另行选择同房间、业务当天唯一有效的入住订单，再执行现有全部安全门。没有入住订单时正常结束；多单冲突、身份不明或来源不可信时交人工。
- **验收**：构造 A 当天退房、B 当天入住的完整双订单链路，只向 B 对应的已验证会话发送；A 永不收到 B 凭证；无 B 不制造异常；多个候选不猜选；重复点击不重复发送。禁止通过放宽入住日或客户归属校验消除报错。

### F-03 · P1 · 凭证异步发送失败没有回写投递状态

- **位置与调用路径**：`application.py::application_lifespan.handle_send_failure`（3212 行起）处理客诉、普通机器人消息及生命周期提醒，但没有按外部消息 ID 关联凭证部件；`repositories/credentials.py::SQLAlchemyCredentialDeliveryRepository.mark_part_sent`（201 行起）此前已记录 SENT。
- **证据与影响**：E-03 输入已知凭证消息的 `fail_type=4` 失败回执，部件和整组仍为 SENT，人工任务为 0。平台接口受理状态可能掩盖后续失败。
- **建议修复**：回调按外部消息 ID 锁定部件，幂等更新部件与整组状态，并建立去重的人工处理任务；复用现有失败状态和人工任务能力，先检查枚举是否足够，不先加状态体系。
- **验收**：任一部件失败不能继续显示整组成功；重复回执不重复建任务；成功部件不重发；未知 ID 不误改其他消息；回执与发送完成记录并发时不能把失败覆盖回成功。是否暂停同组尚未发送部件见第 6 节。

### F-04 · P2 · 入队时有效的旧 AI final 在实际发送前没有再次校验

- **位置与调用路径**：`application.py::TransactionalOutboxWeCom` 保存来源消息关联 → `application.py::_run_worker_loop.send_guest`（2254 行起）直接发送。生成阶段已有 `services/conversation_service.py::ConversationService._discard_stale_final`（941 行起），但未覆盖排队期间的新消息。
- **证据与影响**：E-04 在更新人工消息和 HUMAN_ACTIVE 已入库后处理旧 final，仍调用发送接口，可能打断人工回复或回答过时问题。
- **建议修复**：实际 final 出站前复用 `repositories/conversations.py::SQLAlchemyConversationRepository.has_newer_conversation_activity`（143 行起），围绕来源消息及现有会话顺序机制判定过时；重试、改写链路保留同一来源关联。过时出站记可审计的跳过原因，不记作已发送。
- **验收**：排队后新增客人消息或人工回复均能拦截旧 final；当前有效低风险问题仍可在 HUMAN_ACTIVE 下回答；不误拦审批后的客诉、安全处置和其他独立消息类型。外部请求已发起后无法撤回，不能承诺消除所有并发时间窗。

### F-05 · P2 · 对账仅按近期入住日过滤，漏掉仍在住订单却更新成功心跳

- **位置与调用路径**：`application.py::_run_hostex_reconcile_loop`（2577 行起）使用昨天至未来 15 天 → `services/hostex_sync.py::HostexSyncService.reconcile`（109 行起）只设入住日期过滤 → `services/admin_operations_service.py::AdminOperationsService._occupancy_status`（453 行起）与 `_source_is_stale`（510 行起）生成运营投影。
- **证据与影响**：E-05 中三天前入住、两天后离店的订单因漏 Webhook 未落库；对账也未捞到，但心跳成功，房间显示 vacant。这个结论是运营工作台投影错误，不表示 Hostex 的实时可售房态接口已被证明错误。
- **建议修复**：覆盖与运营窗口重叠的订单，包括更早入住的长住单；对仍影响本地状态的已有订单按 ID 复核改期和取消。只有必要查询及所有分页成功才更新可信心跳。先复用 `integrations/hostex_client.py::ReservationQuery` 的入住/退房过滤能力，但必须核实组合语义和分页，不能盲目扩大天数冒充完整对账。
- **验收**：漏 Webhook 的长住单可补回；改到窗口外或取消的已知单能纠正；部分查询失败不能刷新为可信；无完整数据时不得把“未查到”当作确定空置。真实 Hostex 组合查询仍需另行授权的契约验证。

### F-06 · P2 · 订单改期后旧保洁任务仍可执行

- **位置与调用路径**：`services/hostex_sync.py::HostexSyncService._sync_reservation` 为新退房日建任务；`services/task_lifecycle_service.py::TaskLifecycleService._reason`（76 行起）处理取消及窗口过期，缺少订单房间/服务日期变化导致旧计划失效的判断；候选数据也需检查。
- **证据与影响**：E-06 将退房日从 8 月 2 日改为 8 月 4 日，两个任务都处于待分派状态，可能出现错误日期的清扫。
- **建议修复**：在现有生命周期入口核对来源订单的当前房间和退房日。仅对确定被替代、无人分派、无清单和照片的系统任务自动失效；已分派或存在执行证据的任务保留并转人工处理。
- **验收**：纯计划旧任务失效且新任务唯一；重复 Webhook 幂等；已执行任务证据保留；换房与改期均覆盖；普通逾期但仍有价值的工作不能批量关闭。

### F-07 · P2 · 删除任务同时删除去重依据，同步后旧任务复活

- **位置与调用路径**：`repositories/operations.py::SQLAlchemyOperationsRepository.purge_task`（630 行起）删除任务 → `services/hostex_sync.py::HostexSyncService._sync_reservation` 再次进入 `SQLAlchemyOperationsRepository.create_turnover`，只按现存任务行查重。
- **证据与影响**：E-07 删除已完成且归档的任务后，同一订单同步重新生成待分派任务，历史工作回流到运营待办。
- **建议修复**：删除业务任务时在同一事务保留最少的“该来源任务已删除”标记，同步创建前检查。标记不保存正文、照片或客户隐私，不可永久封禁某个房间和日期。
- **验收**：同一来源重放不复活；真正不同的新订单仍可产生需要的任务；单条、批量和自动清理行为一致。当前存量任务按房间/日期去重，标记候选按订单/房间/日期识别，二者存在语义差异，必须先解决第 6 节的身份决策再做迁移。

### F-08 · P2 · 凭证安全门默认系统日期与武汉业务日不一致

- **位置与调用路径**：`services/credential_delivery.py::CredentialSafetyRules.__init__` 默认使用 `date.today`；`application.py::SessionTaskPageService.mark_ready` 及 `application_lifespan.build_credential_part_handler` 构造服务时未注入业务日期。可复用 `services/stay_date_range.py::wuhan_today`。
- **证据与影响**：E-08 模拟武汉 8 月 2 日 01:00、UTC 仍为 8 月 1 日，默认安全门错误拒绝当天入住。实际影响取决于部署时区，本次没有验证生产是否命中。
- **建议修复**：评估与分段发送的共享日期默认统一使用 `wuhan_today`，保留测试时钟注入。检查相关对账日期计算是否也应统一，但不要顺带改变所有存储时间语义；消息时间仍按现有 UTC 约定。
- **验收**：武汉午夜前后和 UTC 日期不同的时刻，评估与每段发送一致；昨日、明日及已离店订单仍被拒绝，不放松原有安全门。

### F-09 · P2 · 凭证 worker 超时恢复只终止 Job，没有业务补偿

- **位置与调用路径**：`repositories/jobs.py::SQLAlchemyJobRepository.recover_stale`（171 行起）将 `credential_send_part` 等不可重放任务置 FAILED 并清空 payload；现有补偿未同步处理凭证部件及人工任务。
- **证据与影响**：E-09 中 Job 已 FAILED、部件仍 PENDING、人工任务为 0。系统不再自动处理，却没有可接手的业务待办。
- **建议修复**：在清空载荷前取得凭证关联，在同一事务更新部件/整组为失败或结果待核实，并建立去重人工任务。不可因为状态未知就自动重发密码或二维码。
- **验收**：模拟 worker 在外部调用前后宕机，恢复后均有明确业务状态和人工待办；重复恢复不重复创建；成功部件不重发；事务失败可重试恢复；不得删除唯一关联信息后再尝试补偿。

## 5. 修复单元、文件计划与验证

以下为建议范围，**不是已批准的最终 Spec**。围绕共同事务和验收边界组织为三个单元，推荐 A → B → C；每个单元经确认后连续实施，不把独立的历史数据处置混入代码修复。

### A. 删除一致性与防复活：F-01、F-07

| 文件 | 计划触及的符号或新增内容 | 验证目标 |
| --- | --- | --- |
| `src/homestay_bot/application.py` | `SessionTaskPageService.purge/purge_many`，现有 worker handler 注册与保留期清理组装 | 三种删除入口均在提交后执行可恢复文件清理 |
| `src/homestay_bot/services/task_page_service.py` | `TaskPageService.purge/purge_many` 及附件查询边界 | 删除校验与实际操作在同一事务，保留权限检查 |
| `src/homestay_bot/repositories/operations.py` | `purge_task/purge_selected/create_turnover` | 原子删除及来源标记，防同源重建 |
| `src/homestay_bot/repositories/retention.py` | `purge_archived_tasks` 与 Job 清理条件 | 自动清理同样可靠，未完成文件清理记录不得提前丢失 |
| `src/homestay_bot/repositories/jobs.py` | 复用入队、失败恢复和幂等能力；检查新清理类型 | 不引入第二套队列；失败可观测、可恢复 |
| `src/homestay_bot/domain/models.py`、`migrations/versions/` | 待确认的最小删除来源标记及一条新迁移，编号在实施时按当前 Alembic head 确定 | 不修改历史迁移；无正文/照片/不必要隐私；验证升级和回退边界 |
| `tests/integration/test_operations_repository.py`、`test_retention_repository.py`、`test_jobs.py`、`test_task_routes.py` | 对应删除、去重、事务和入口测试 | 回滚保照片、失败可恢复、重复同步不复活；PostgreSQL 并发另验 |

不建立通用“跨资源事务框架”。复用已有 job 表承载文件清理；仅增加防复活确实需要的数据。若现有审计记录已能可靠表达删除来源且生命周期足够，应优先复用，确认不能满足后才新建标记。

### B. 订单、房态与凭证闭环：F-02、F-03、F-05、F-06、F-08、F-09

| 文件 | 计划触及的符号 | 验证目标 |
| --- | --- | --- |
| `src/homestay_bot/services/hostex_sync.py` | `reconcile/_sync_reservation` | 订单窗口覆盖、分页失败处理、改期后的旧计划复核 |
| `src/homestay_bot/integrations/hostex_client.py` | `ReservationQuery` 及已有订单查询方法；仅必要时调整 | 过滤条件与外部契约匹配，不新增订单写操作 |
| `src/homestay_bot/services/task_lifecycle_service.py`、`repositories/operations.py` | `_reason`、生命周期候选查询、入住订单候选查询 | 自动失效限定无执行证据任务；唯一入住对象可确定 |
| `src/homestay_bot/services/room_readiness_service.py` | `RoomReadinessService.mark_ready` | 区分保洁来源订单与入住接收订单 |
| `src/homestay_bot/services/credential_delivery.py`、`repositories/credentials.py` | `CredentialSafetyRules`、`CredentialDeliveryService`、`CredentialPartSender`、部件状态方法 | 统一业务日期；失败与不确定结果一致落库 |
| `src/homestay_bot/application.py` | 对账循环、`handle_send_failure`、凭证 handler 组装 | 心跳可信，异步失败路由到凭证域 |
| `src/homestay_bot/repositories/jobs.py` | `recover_stale` 与现有补偿入口 | 丢弃载荷前完成凭证状态和人工任务补偿 |
| `src/homestay_bot/services/admin_operations_service.py` | `_occupancy_status/_source_is_stale`；仅在需要表达同步覆盖不足时调整 | 来源不完整不能被显示为确定空置 |
| `tests/unit/test_hostex_sync.py`、`test_hostex_client.py`、`test_task_lifecycle_service.py`、`test_room_readiness_service.py`、`test_credential_delivery.py`、`test_admin_operations_service.py` | 最小回归场景 | 覆盖日期、候选冲突、失败回执、数据来源可信度 |
| `tests/integration/test_phase_one_flow.py`、`test_credential_delivery_repository.py`、`test_jobs.py` | 修正真实 A/B 周转场景，补事务与恢复测试 | 从退房保洁到 B 凭证的离线完整链路闭环 |

B 内部顺序：订单覆盖和改期一致性 → 明确入住订单选择 → 统一日期 → 异步失败和 stale 恢复。更改共享入口前追踪所有调用方；不另造凭证发送通道，也不绕过已有确定性安全门。

### C. 排队 final 的时效校验：F-04

| 文件 | 计划触及的符号 | 验证目标 |
| --- | --- | --- |
| `src/homestay_bot/application.py` | `TransactionalOutboxWeCom`、`_run_worker_loop.send_guest`，有关重试载荷 | 实际出站前保留并校验来源消息 |
| `src/homestay_bot/repositories/conversations.py` | 复用 `has_newer_conversation_activity` 与现有锁方法 | 同一排序口径判断过时，不另建消息版本系统 |
| `src/homestay_bot/services/conversation_service.py` | 对照 `_discard_stale_final`，必要时最小共享 | 生成与发送判定相容，HUMAN_ACTIVE 独立低风险回复保留 |
| `tests/unit/test_application.py`、`tests/unit/test_runtime_consumers.py`、`tests/integration/test_jobs.py` | `_run_worker_loop` 相关用例及任务状态断言 | 新客人/人工活动拦旧 final，重试仍拦，当前有效消息正常发送 |

C 的剩余竞态需要在 Spec 明确：本地最后校验只能覆盖已经入库的活动；网络请求发起后的撤回、外部事件尚未同步的情况不在保证范围。不要用长期持有数据库锁覆盖无限外部等待。

### 综合验收与历史数据

实施前把 F-01 至 F-09 的“验收”转成最小且有回归价值的正确行为断言；优先修改已有测试，不为每个私有函数铺一套镜像测试。源代码差异冻结后，运行一次必要的离线全量 pytest、Ruff、Mypy、依赖检查和 wheel 构建；修改了迁移、锁或事务时，另用隔离 PostgreSQL 验证事务竞争和迁移，SQLite 结果不能替代。

历史数据先做只读盘点：缺照片但保留附件记录的任务、改期旧任务、疑似删除后重建任务、SENT 但收到了失败回执的部件、FAILED Job 对应的 PENDING 凭证。不要猜测缺失照片内容；只能从已验证备份恢复。不要根据不完整历史推导删除标记，不自动补发历史凭证。数据修复范围、备份、回滚及真实外部操作需要独立方案与当前授权。

生产验收仍需分别证明服务器源码、运行容器、数据库、应用健康、登录后的相关页面及真实消息收件。企业微信接口受理不等于客人收到。本文不包含这些已完成证明。

## 6. 最终 Spec 仍需明确的决策

1. **删除来源身份**：现有保洁去重键只有房间和服务日。一个房间同日多个来源订单、订单取消后恢复、同单改期再改回时，哪些任务应复用、哪些应新建、哪些应抑制？建议保护明确删除的同源工作，允许真正新增工作；不能只选一个更长的唯一键就声称业务问题已解决。标记保留期也要与外部可重放历史及隐私保留策略相容。
2. **凭证目标与数据可信度**：建议无当天入住订单时不发且不报错，多个候选或身份/来源不可靠时转人工。需明确何种同步证据足以选择唯一订单，以及房间可以标记 READY、但凭证暂缓的产品表现。不能把同步缺口解释成“没有入住订单”。
3. **分段失败后的剩余发送**：建议已成功部件不重放；一段失败或结果未知时整组进入人工处理，尚未开始的同组发送暂停。需确认这项业务取舍及现有状态能否表达，不能擅自把全部失败自动重试。
4. **Hostex 查询契约**：本地模型存在入住/退房日期字段，但组合条件、分页、取消单返回范围和按 ID 查询能力需依据当前客户端和已授权契约证据确认。未验证前不能承诺对账覆盖完整。
5. **实施范围与授权**：确认 A/B/C 是否全部纳入，以及是否包含历史数据修复。先确认现状、功能与风险决策，形成完整 Spec；按项目规则等待明确“开始”再编码。提交、推送、部署及真实服务调用仍需各自授权。

交接完成标准：Claude 能从本文定位九项代码路径、运行证据脚本、解释复现限制，并以第 5 节为基础完成待确认 Spec。本文交付不表示任何业务缺陷已经修好。
