# AOCI 索引交接与 Claude / SQLite 接入方案

日期：2026-10-06。本文交接已完成的代码索引，并提出后续接入方案；Claude 配置、SQLite 适配和数据库索引均未在本轮实施。

## 1. 接手时的状态

| 项目 | 已核实状态 | 证据入口 |
| --- | --- | --- |
| 可执行文件 | `/Users/rin/.local/bin/aoci`，0.1.0-rc17，commit `93d6ad5a87fd9624a51cae61b2134d33944501fb` | `aoci --version` |
| Codex MCP | 已配置，当前会话可以调用 AOCI 工具 | 项目 `.codex/config.toml` 的 `mcp_servers.aoci` |
| Claude Code MCP | 未配置；本机 Claude Code 2.1.259 | `claude mcp get aoci` 返回未找到服务；项目 `.mcp.json` 不存在 |
| 代码索引 | 本报告新增前，498 个受管源对象中 443 条已建立，55 项技术跳过；不是全仓文件总数 | `verify --json` 的 `governance.code_source_count/code_entry_count/code_drift` |
| 治理终态 | 报告新增前 Verify / Check / Guide 均通过，Guide 为 `complete/aligned`，无待执行目标 | `structure_valid=true`、`governance_aligned=true`、`ok=true`、`next_action=none` |
| 数据库索引 | 数据源列表为空，Database Volume 不存在，数据库 Entry 为 0 | `database source list --json`；Verify 的 `governance.database` |
| 本地数据库 | `.env` 的 DATABASE_URL 使用 `sqlite+aiosqlite`；`homestay.db` 只读目录查询得到 9 张非 SQLite 内部表 | `sqlite_schema`；没有查询业务记录 |
| 面板 | [本机 AOCI 面板](http://127.0.0.1:54792/)，本轮返回 HTTP 200 | 当前本机进程；重启机器后需重新启动 UI |

本地 SQLite 的 9 张表为 `alembic_version`、`audit_logs`、`booking_approvals`、`conversations`、`employees`、`external_requests`、`jobs`、`knowledge_entries`、`messages`。这只证明当前本地库的目录，不证明生产库状态或迁移设计的完整表数。

报告新增前的资产身份：

```text
Root   11480b594152b260db5255d53231a87f6df903788f5514a7d08b625d84e3dd7c
Meta   fbc304fa05182547e96f1f8404c7c30ad6c366bba9589523f1019c2c1da0427c
Code   e58fd2489a41d9aceb856e8623963b9e07ba269030c0155f6c99c8f5fab425d1
```

本报告也属于新文档对象，收尾维护后 Code 身份和条目数会变化。上述身份用于定位交接前版本，不作为下一会话的当前认知收据；接手 Agent 应重新取得运行合同和完整 Overview，原样跟随全部 cursor 并完成交付确认与一次 Attestation。本文不转录正式索引正文。

Git 边界：`aoci.txt`、`aoci.meta.txt`、`aoci.code.txt`、`AGENTS.md` 均未被 Git 忽略。初始化写入的宿主配置忽略项保持原样。保留现有 CSS、工作台模板、任务记录、主题测试及其他文档改动；没有提交、推送、部署或真实业务外调。

当前官方最新发布为 [rc18](https://github.com/aoci-spec/aoci-code/releases/tag/v0.1.0-rc18)，发布时间为 2026-10-05 16:43:45 UTC；本机仍使用 rc17。本轮没有升级二进制。后续若升级，应在没有进行中的写入批次时进行，完成发布校验并重启相关宿主；以实际 MCP 服务版本为准，不能只看磁盘文件版本。

## 2. Claude 会自动接入吗

**当前不会。** Codex 的 `.codex/config.toml` 只登记 Codex 服务。Claude Code 需要自己的 MCP 声明；读取 AOCI 规则也不会自动创建服务配置。

已核实项目 `.mcp.json` 和 `.claude/settings.json` 不存在，Claude 的用户配置中没有 AOCI 服务，`claude mcp get aoci` 退出码为 1。因此当前没有 Claude 连接成功的证据。

下一步可在项目目录执行以下注册命令。此段是待执行步骤，本轮未执行：

```bash
cd '/Volumes/02/obsidian codex/homestay-bot'
claude mcp add --scope project --transport stdio aoci -- \
  /Users/rin/.local/bin/aoci \
  --repo '/Volumes/02/obsidian codex/homestay-bot' mcp
claude mcp get aoci
```

等价的 `.mcp.json` 内容如下；存在其他 server 时只合并 `mcpServers.aoci`：

```json
{
  "mcpServers": {
    "aoci": {
      "type": "stdio",
      "command": "/Users/rin/.local/bin/aoci",
      "args": ["--repo", "/Volumes/02/obsidian codex/homestay-bot", "mcp"]
    }
  }
}
```

重开该项目的 Claude 会话；如出现项目信任或 MCP 批准提示，按宿主提示完成批准，再通过 `/mcp` 确认连接状态。注册落盘不等于连接成功，连接成功也不等于模型已经读取全部认知。依据：[Claude MCP 官方说明](https://code.claude.com/docs/en/mcp#project-scope)。

本机 2.1.259 还有规则加载缺口：原生读取 `AGENTS.md` 的功能要求 2.1.277 或更高版本。当前项目没有 `CLAUDE.md`，用户级 `~/.claude/CLAUDE.md` 只导入全局规则，不能据此认定项目的 AOCI 区块已加载。对当前版本，可在项目 `CLAUDE.md` 中加入以下导入，并通过下一会话的 `/context` 核实：

```markdown
@AGENTS.md
```

若以后升级 Claude，再按实际版本和 `/memory` 状态判断是否需要保留该导入。依据：[Claude 指令文件官方说明](https://code.claude.com/docs/en/memory#agentsmd)。本轮没有新增 `CLAUDE.md`、MCP 配置或 Hook。

## 3. SQLite 接入的事实与选择

rc17 CLI 的 `database source add --help` 只接受 `postgresql/mysql/opengauss`；AOCI 源码 `internal/dbevidence/types.go::Engine`、`config.go::supportedEngine`、`collector.go::driverNameForEngine/Collector.collect` 也只有这三种引擎。[rc18 数据库文档](https://github.com/aoci-spec/aoci-code/blob/v0.1.0-rc18/docs/database-evidence.md) 列出的支持范围相同。升级至 rc18 本身不能解决 SQLite 接入。

`internal/dbevidence/canonical.go::CanonicalTable` 同样检查支持的引擎，所以独立导出 SQLite JSON 后直接塞进 `.aoci` 或伪装成 PostgreSQL 不能建立可信的正式索引。

| 方案 | 得到什么 | 工作与限制 | 建议 |
| --- | --- | --- | --- |
| A. 在 AOCI 中增加原生 SQLite collector | 当前 SQLite 文件的真实表结构证据及正式 Database 索引 | 修改 AOCI 工具源码、引擎契约与验证；需要独立实施和验收 | 若目标是“实际 SQLite 的 AOCI 索引”，选此方案 |
| B. 隔离 PostgreSQL 按项目迁移建结构 | 项目迁移定义在 PostgreSQL 上的结构索引 | 可复用当前 AOCI；需要新建本地实例并运行迁移，不复制真实记录 | 适合先获得迁移设计的数据库认知；必须标注来源，不能声称已索引 SQLite |
| C. 只读导出 SQLite 结构到普通文档 | 可供 Agent 阅读的目录和 DDL，后续作为 Code 文档维护 | 不改 AOCI 引擎，落地成本较低；没有正式 Database Evidence/Binding 与数据库漂移闭环 | 临时参考资料，不满足正式数据库索引验收 |

建议：正式 SQLite 接入采用 A；如果需要立即得到迁移结构索引，可另外选择 B。C 只作为过渡资料。这里没有创建数据库、复制业务记录、运行迁移或开发适配器。

## 4. 原生 SQLite 方案的最小边界

### 4.1 接入位置和证据流

改动应在 AOCI 的独立源码工作区进行，保留本项目业务代码和当前可用 rc17。先完成实施 Spec，再按用户确认的边界编码。

| AOCI 路径与符号 | 拟调整职责 |
| --- | --- |
| `internal/dbevidence/types.go::Engine/SourceConfig/TableEvidence` | 增加 SQLite 引擎身份；明确逻辑数据库名、`main` namespace 和真实文件 URI 的区分；核实当前 Evidence 字段可表达的范围 |
| `internal/dbevidence/config.go::NormalizeSource/supportedEngine` | 校验 SQLite 配置；团队配置只保存环境变量引用，文件 URI 由本机环境提供 |
| `internal/dbevidence/collector.go::Collector.collect/catalogTransactionOptions/driverNameForEngine` | 引擎分流、只读打开、单连接一致性读取、超时和取消；SQLite 不照搬服务型数据库的 Repeatable Read 参数 |
| 新增 `internal/dbevidence/collector_sqlite.go` | 只从真实 catalog 采集结构事实，不生成标签或 F/R/A/S |
| `internal/dbevidence/canonical.go`、公开 Spec、CLI 帮助和相关验证 | 接受新增引擎，确定排序、哈希、漂移及兼容策略，保留既有三种引擎行为 |

原生采集 → Canonical Evidence / Snapshot → 显式接受精确 Snapshot Baseline → 当前 Guide 的 Database Bootstrap → Host 模型读取表证据并作者化全部签发候选 → Apply → Verify / Check / Guide。

继续复用 AOCI 的 Evidence、CAS、Binding、Recovery 和维护流程；不另建语义生成器，也不直接写 `aoci.database.txt`。当前 `go.mod` 没有 SQLite 驱动；实现 Spec 中应核查驱动能否满足纯 Go 发布、只读 URI、取消和供应链校验要求，再确认具体依赖。

### 4.2 只读与结构准确性

第一版限定单文件的 `main` namespace 和普通表，目标为当前本地库。以 URI `mode=ro` 打开，设置连接级只读限制，在一个读取事务里采集，使用固定 catalog 查询白名单；不开放通用 SQL、ATTACH 或扩展加载。不读取业务行，不更改 journal 模式，不做 checkpoint，不复制整个库。

对可变化的数据库不使用 `immutable=1`：该参数会跳过锁和变化检测。WAL 环境无法安全只读打开或超时锁冲突时，明确失败并保留旧证据，不静默换库。[SQLite URI 官方说明](https://www.sqlite.org/uri.html)。

采集入口为 `sqlite_schema` 和 `PRAGMA table_xinfo/index_list/index_xinfo/foreign_key_list`，保留名称、列顺序、声明类型、主键及外键顺序、唯一性和原生 DDL。不能只用 `table_info`，因为它遗漏生成列和隐藏列。[SQLite Schema 表](https://www.sqlite.org/schematab.html)、[SQLite PRAGMA](https://www.sqlite.org/pragma.html)。

SQLite 的类型亲和性、rowid 主键、无名约束、STRICT / WITHOUT ROWID 语义不能套用 PostgreSQL 规则。PRAGMA 不提供的 CHECK、生成表达式、部分或表达式索引信息，需要受支持的 DDL 解析；第一版不能准确表达的特性应返回明确的 unsupported finding，阻止不完整证据进入 Baseline。虚拟表、shadow 表、视图和附加数据库也采用显式不支持边界，不能静默删除后宣称完整覆盖。

### 4.3 验收与回退

验收至少覆盖当前 9 张表的目录一致性、复合键顺序、唯一及外键约束、默认表达式、特殊标识符、主键真实可空语义，以及超出第一版能力的 DDL 是否失败关闭。结构输出须稳定排序，相同结构得到相同摘要；增删表、增列或索引变化能被数据库 Verify 检出，接受前不移动 Baseline。

用临时 SQLite 验证写操作确实被拒绝、业务行哨兵不进入证据、超时/取消不留下半份正式资产；使用 WAL fixture 核实只读读取边界。现有三个引擎的相关契约也应保留。通过后才对本项目执行正式接入；连接成功或目录导出成功均不能替代表条目作者化与治理对齐。

适配二进制使用独立稳定路径并记录源码版本及校验值；切换 MCP 前结束已有写入批次并留存 AOCI 资产备份。回退时恢复原二进制与对应配置，遵守版本兼容和 Recovery，不让旧 rc17 强行读取尚不支持的 SQLite 配置。

## 5. PostgreSQL 过渡方案的边界

若选择 B，在本机独立目录、loopback 端口和专用库中创建实例。只在该隔离库执行项目 Alembic 迁移；不修改项目 `.env`，不复制 `homestay.db` 的记录，不连接生产实例。

项目依据为 `migrations/env.py::run_async_migrations/do_run_migrations`：它使用进程 `DATABASE_URL` 覆盖 Alembic 连接，基于 `Base.metadata` 执行迁移。迁移连接需要 SQLAlchemy 的异步 PostgreSQL URL；AOCI 的连接环境变量应使用其 PostgreSQL 驱动接受的 DSN，不能混用。

迁移完成后创建只读 catalog 账号，再按当时 CLI / Guide 执行 Source → Inspect → Snapshot → 精确 Baseline 接受 → Database Cognition 作者化与终检。将来源命名为迁移 fixture，并明确标注“迁移生成的 PostgreSQL 结构，不代表本地 SQLite 或生产库”。表数和迁移状态以该次真实采集为准。

## 6. 接手验证与下一步

以下命令可在本机复核身份、治理和数据源声明；Verify / Check / Guide 可能追加本地审计记录，但不修改正式索引或 Baseline：

```bash
cd '/Volumes/02/obsidian codex/homestay-bot'
/Users/rin/.local/bin/aoci --version
/Users/rin/.local/bin/aoci --repo "$PWD" verify --json
/Users/rin/.local/bin/aoci --repo "$PWD" check --json
/Users/rin/.local/bin/aoci --repo "$PWD" index agent guide --agent codex --json
/Users/rin/.local/bin/aoci --repo "$PWD" database source list --json
git check-ignore aoci.txt aoci.meta.txt aoci.code.txt AGENTS.md
```

最后一条退出码 1 且无输出表示这些认知资产未被忽略。

本轮证据链：Codex 配置及可用工具 → Codex 已接入；Claude 配置缺失与 CLI 未找到服务 → Claude 尚未接入；AOCI 引擎枚举与真实 SQLite 目录 → 当前无法建立该库的正式 Database 索引。后续实施按“先补 Claude 注册和规则加载，再确认 A/B 方案，再按批准 Spec 实施及验收”的顺序推进。

本轮只交付报告与方案；没有增加 Claude 配置、改变宿主批准、升级工具或建立数据库。需要实施时请明确选择原生 SQLite 方案或 PostgreSQL 过渡方案；历史授权和本文的操作示例不构成执行授权。
