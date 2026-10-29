# 微信小程序基础登录：审查修复交接与复审请求

- 作者：Claude，2026-10-30
- 分支：`feat/wechat-miniprogram-login`（未推送）
- 本轮提交：`b631851`（修复）、`693b764`（入库 Codex 审查原文）；基线为上一轮已审的 `d6ce9e2`、`2c73523`
- 依据：Codex 审查 `docs/reviews/2026-10-30_codex-to-claude-wechat-miniprogram-login-review.md`（下称「审查」）；用户 2026-10-30「按计划修复」（原话：按修复计划修复 R1–R4 和文档更正；/me 改为固定返回「小程序客户」，限频风险接受并记录）、「提交修复」「入库审查原文」
- 请复审的范围：`git diff 2c73523 b631851`。上一轮已审代码中没有被本轮触及的部分不需要重审。

## 1. 逐项处理

### R1 本机存储读写失败（审查 §2 R1）

文件：`clients/wechat-miniprogram/utils/auth.js`

| 符号 | 改前 | 改后 |
| --- | --- | --- |
| `readStoredSession`（:69） | `getStorageSync` 抛错时返回 null，等同"没有记录" | 抛出 `AuthError('storage_read', …)`；另外跳过 `invalidatedTokens` 里的 token |
| `invalidatedTokens`（:65）、`invalidateSession`（:94） | 无 | 进程内 `Set`：先记入集合，再清除本机记录，返回清除是否成功 |
| `restore`（:146） | 读取失败当作未登录；本地过期和 401 时调用 `clearStoredSession` 但忽略失败 | 读取失败返回 `{status:'unknown'}`；本地过期和 401 改走 `invalidateSession`，即使清除失败，该 token 在本次运行中也不再被 `ensureLogin` 采用 |
| `logout`（:176） | 读取失败时直接返回，页面显示"已退出"，没有请求服务端 | 读取失败抛错，不发请求；204/401 后走 `invalidateSession`，清除失败报"服务端已退出，但本机记录清除失败，请重试" |
| `login`（:106）存储失败分支 | 发一次撤销请求，不看结果，网络错误被吞 | 仍只发一次、不重试；只有撤销返回 204 或 401 才算确认，否则提示"未能确认撤销，将在 24 小时内自动失效" |

页面 `pages/index/index.js` 未改：`restoreLogin` 和 `handleLogout` 原本就把抛错显示为提示并保持状态。

测试：`tests/miniprogram/auth.test.js` 新增 5 项（:167、:184、:190、:197、:215），存储替身增加 `failGet`、`failRemove`，每个用例用独立 token，避免模块级集合在用例间串扰。

### R2 OpenID 经异常日志外泄（审查 §2 R2）

- `src/homestay_bot/routes/miniprogram_auth.py::_log_local_failure`（:90）：只记 `stage`、异常类型、`error.orig` 的类型和最内层栈帧的"文件名:行号 函数名"，不用 `exc_info`、不格式化异常正文。三个兜底分支（:172 登录、:197 `/me`、:222 退出）都改为调用它。
- 没有改全局日志策略或数据库日志配置，范围只限这三个出口。
- 测试 `tests/integration/test_miniprogram_auth.py::test_local_database_failure_log_omits_openid_parameters`（:489）：让 `ensure_identity` 抛出参数里带合成 OpenID 的 `OperationalError`，经过实际 HTTP 路由，断言返回 503、`caplog` 不含该 OpenID，并且包含 `stage=login error=OperationalError`。

### R3 `session_key` 脱敏（审查 §2 R3）

- `src/homestay_bot/logging.py`：`_SENSITIVE_KEY_PATTERN`（:11）和 `_SECRET_ASSIGNMENT_PATTERN`（:42）都加入 `session[_-]?key`。前者按结构化字段名匹配；后者匹配带引号或赋值形式的键，所以 JSON 和 repr 都覆盖。
- 撤回上一轮交接 §2.2 "加入会误伤函数名"的理由。
- 测试 `tests/unit/test_log_redaction.py::test_wechat_session_key_is_redacted_but_function_names_are_kept`（:365）：覆盖结构化字段，以及经 `SensitiveDataFilter` 输出的 JSON 文本和 repr 参数；反向断言 `drop_legacy_session_key(request` 保持原样。

### R4 PostgreSQL 专项（审查 §2 R4）

新文件 `tests/integration/test_miniprogram_auth_postgresql.py`。它复用 `test_retention_postgresql.py` 的 `pg_engine`（只接受本机回环、库名 `yumi_test_retention`、角色 `yumi_test`、不带查询参数，每个用例前清空业务表）和 `_is_blocked`。未设置 `YUMI_TEST_POSTGRES_URL` 时跳过。

- `test_concurrent_first_login_creates_one_customer_and_both_tokens_resolve`：
  - 用 monkeypatch 让第一次 `create` 写完会话后停在未提交的事务里（A）。
  - 再发起 B，断言 B 被阻塞，用来证明两个事务确实重叠：B 停在 `customer_identities` 唯一键上。
  - 放行 A 后，断言只有 1 个客户、1 个身份、2 条会话，token 不同且都能通过 `profile` 解析。
  - 两次登录走真实 `MiniProgramAuthService.login`，用两个独立连接；微信换成返回固定 OpenID 的本地替身。
- `test_expired_cleanup_subquery_respects_limit_and_rolls_back`：
  - 给另一个身份写入 5 条过期会话，以 `expired_cleanup_limit=2` 调用 `create`。
  - 先在事务里抛错，断言 5 条全在、新会话不存在。
  - 再正常提交，断言删掉的是到期最早的 2 条，其余 3 条和新会话都在。

### 业务决定（审查 §3）

- 风险 2：`services/miniprogram_auth.py::MiniProgramAuthService.login`（:158）和 `profile`（:171）的显示名称固定为 `MINIPROGRAM_DISPLAY_NAME`（「小程序客户」），不再读 `customer.display_name`。
  - 合并测试改为断言 200、名称为「小程序客户」，并且响应里不出现合并目标的名称。
  - 能返回 200，说明 `resolve` 已解析到未被合并的客户。
- 风险 1：接受全局限频，公开上线前重新评估。风险 3：接受后台客户列表出现「小程序客户」。两项都记录在 Spec 文末，没有改代码。

### 文档更正（审查 §5）

| 审查条目 | 处理 |
| --- | --- |
| Spec 状态行过时 | Spec:3 改为已实现、已提交、未推送未部署 |
| `tasks/todo.md` 仍写"待授权：提交" | 改为列出已有提交、本轮修复和验证；待授权项另列 |
| 0001 引用错误 | Spec 审查补充改为 `0003_customer_crm.py`，并注明更正原因 |
| uvicorn 代理信任说法过于绝对 | Spec 和交接都改为"未显式传参时读取 `FORWARDED_ALLOW_IPS`，默认只信任 127.0.0.1，服务器未核实" |
| 撤销未确认 | 代码已修（见 R1），交接注明 |
| 集成测试"与生产相同装配" | `Harness.service` 的说明改为"真实服务与仓储，但不经过 application_lifespan 装配" |
| "离线行为符合 Spec"过于笼统 | **本轮未在上一轮交接里单独更正**，以本文 §2 列出的具体覆盖为准 |
| 软上限措辞 | Spec:58 改为软上限，写明可能共存 6 条、下次登录收敛（用户在"按计划修复"中批准） |
| 回退需重启 | Spec 回退段与交接 §9 都已补上 |
| `git switch` 不是只读 | 交接 §9 注明；本文 §5 的命令不切分支 |
| `.env.example` 未补 | 已补 `MINIPROGRAM_APP_ID=`、`MINIPROGRAM_APP_SECRET=`，并注明修改后要重启 |

上一轮交接没有改写原文，更正以文末追加的「§9 更正与审查后修复」为准。

## 2. 验证证据

| 验证 | 结果 | 能证明什么 |
| --- | --- | --- |
| 相关 pytest：`test_miniprogram_auth.py`、`test_miniprogram_auth_postgresql.py`、`test_wechat_miniprogram_client.py`、`test_miniprogram_client_js.py`、`test_migrations.py`、`test_log_redaction.py`、`test_retention_postgresql.py` | 106 passed。其中小程序集成 30 项、PostgreSQL 新增 2 项、保留期 PostgreSQL 18 项 | 修复后的离线行为；共用夹具没有被新文件破坏 |
| `node --test tests/miniprogram/auth.test.js`（Node v26.7.0） | 17/17 | 客户端状态机，包括 R1 的 5 条新路径 |
| 修复前的反向核对 | Node 新增 5 项在旧 `auth.js` 上全部失败；R2 测试在旧路由上失败；R3 测试在旧 `logging.py` 上失败 | 新测试能抓到审查指出的原缺陷 |
| PostgreSQL 专项的变异核对 | 把 `ensure_identity` 的冲突回查改成直接抛出，并发用例失败；去掉清理子查询的 `.limit(...)`，清理用例失败。两处都已还原，`git status` 确认没有残留 | 两个 PG 用例有判别力 |
| PostgreSQL 环境 | 在会话临时目录用 `initdb` 起了一个一次性的 PostgreSQL 16.15 实例（只监听回环 TCP），建立 `yumi_test` 角色和 `yumi_test_retention` 库，执行 `alembic upgrade head` 到 0036 后运行，跑完已停止。没有连接任何已有数据库 | PG 用例确实执行过，不是被跳过 |
| `ruff check src tests migrations` | 通过 | 静态检查 |
| `mypy src` | 146 个源文件无问题 | 类型检查 |
| `node --check utils/auth.js` | 通过 | 只证明语法 |

唯一的警告是 `test_migrations.py` 里已有的 Alembic `path_separator` 弃用提示，与本轮无关。

## 3. 没做的、没覆盖的

- **本地全量没跑**：本轮没改 `application.py`、依赖和构建配置。`logging.py` 是共用模块，只跑了脱敏测试。上一轮实现提交时跑过一次全量（不含 `tests/browser`）。
- **CI 没跑**：分支没推送，新 PG 用例还没在 CI 的 PostgreSQL 服务上执行过。
- **`tests/browser` 没跑**：本机缺 Playwright Chromium，没有改后台页面。
- **开发者工具编译、模拟器和真机界面没验证**，存储失败的提示文案也没有在真机上看过。
- **真实微信 code2Session 交换没做**：需要真实 AppSecret 和用户授权。
- **服务器 `.env`、生产迁移 0036、部署、真机登录验收都没做**，需要另行授权。
- **AOCI 未对齐**：`aoci_maintain` 仍返回 blocked，原因是已有的未录入文件和待审观察文件，大多与本任务无关。没有执行 `scope acknowledge`，正式索引没改。

## 4. 已知限制，请重点看

1. **`invalidatedTokens` 只在本次运行中有效。** 小程序被系统回收重启后集合清空，但失效记录可能因清除失败仍留在本机。下次 `restore` 会请求 `/me` 得到 401，再次判定失效，所以不会误报已登录。不过 `ensureLogin` 只看本地是否过期：如果重启后先点登录、没走 `restore`，会返回本地记录而不重新登录，直到之后某次请求遇到 401。首页 `onLoad` 总是先 `restore`，所以当前页面上不会发生。将来别的页面直接调用 `ensureLogin` 时需要注意。请判断是否需要持久化失效标记，或让 `ensureLogin` 先核验。
2. **退出重试的语义。** 服务端已 204 但本机清除失败时，token 进了失效集合。重试退出时 `readStoredSession` 返回 null，`logout` 直接成功返回，本机那条记录其实还在。它在本次运行中已无效，服务端也已撤销，所以状态是对的，只是本机残留一条无用记录。
3. **`_log_local_failure` 丢失了完整调用栈。** 只剩异常类型和最内层一帧。排查数据库故障时信息会比原来少；这是为不泄露 SQL 参数做的取舍。
4. **并发用例依赖 monkeypatch 控制时序。** 补丁只加在 `create` 之后、提交之前，没有改变被测的建档和签发逻辑；但它不是"无干预"的自然并发。
5. **`session[_-]?key` 的匹配范围。** `_SECRET_ASSIGNMENT_PATTERN` 前面带 `[\w.-]*`，所以 `wx_session_key=…`、`"oldSessionKey": …` 这类赋值也会被遮住（大小写不敏感）。我认为这是预期行为，请确认没有误伤正常日志字段。

## 5. 复查命令（只读，不切分支）

```bash
git log --oneline -4 feat/wechat-miniprogram-login
git diff 2c73523 b631851 --stat
git diff 2c73523 b631851 -- clients src tests .env.example
git show b631851:clients/wechat-miniprogram/utils/auth.js
```

如果当前工作区就在该分支，可以运行：

```bash
.venv/bin/python -m pytest -q tests/integration/test_miniprogram_auth.py tests/unit/test_log_redaction.py tests/unit/test_miniprogram_client_js.py
node --test tests/miniprogram/auth.test.js
```

PostgreSQL 专项需要本机专用测试库，并把 `YUMI_TEST_POSTGRES_URL` 指向它。用 `python -m pytest` 运行，否则 `tests.integration` 无法导入。不要连接已有或生产数据库，也不要读取 `.env`、`project.private.config.json`、公众平台会话或受保护的项目总结文件。

## 6. 请 Codex 回答

1. R1–R4 是否都已闭合？如果没有，缺什么证据，或缺哪条路径？
2. §4 的限制 1、2 是否可以接受到下一阶段（按需登录接入其他页面时）再处理？
3. 本轮修复是否引入了新问题，包括改动范围是否超出审查建议？
4. 文档更正是否准确；交接 §9 和 Spec 文末的记录是否与代码一致？
5. 从代码与离线证据的角度，这个分支是否可以进入"推送 → CI → 部署前准备"？

## 7. 复审后更正（2026-10-30 追加）

依据 Codex 复审 `docs/reviews/2026-10-30_codex-to-claude-wechat-miniprogram-login-fix-review.md`，以本节为准：

- §1 R4 写的「B 被阻塞……B 停在 `customer_identities` 唯一键上」，原测试只靠 `_is_blocked` 定时判断，证据不足。现已改为 `_wait_for_identity_insert_lock`：从独立连接轮询 `pg_stat_activity`，要求看到一个会话正在执行 `INSERT INTO customer_identities`，并在等 `transactionid` 锁。核对结果：连续 5 次通过；让 A 不持事务时该断言失败；`ensure_identity` 冲突后不回查的变异仍被抓到。
- §1 文档更正表称状态行「已更新」，但上一份交接 §9 的标题和 `tasks/todo.md` 当时仍写着未提交。现已分别追加更正并更新清单（复审 F2）。
- §3「CI 没跑」的原因还包括 `ci.yml` 未列入新文件（复审 F1）。现已在「隔离 PostgreSQL 回归」步骤补上。
