# 微信小程序基础登录：实现交接与审查请求

日期：2026-10-30。交接方：Claude。审查方：Codex。

**本地实现已完成并提交到分支 `feat/wechat-miniprogram-login`，提交号 `d6ce9e2e4baa62392d88799faf6734ac9da7b332`，基线为 `d1abcf5`（main）。没有推送，没有部署，没有执行生产迁移，也没有调用真实微信、DeepSeek、Hostex 或企业微信。** 请审查这次提交的设计和代码，不要把「离线测试通过」当成真实微信登录或线上可用的证据。

本交接不授予修复、推送、部署、生产写入或真实外部调用权限。审查发现实质问题时，请按严重程度列出，并标明「代码缺陷」「设计缺口」「待业务决定」「证据不足」。

## 1. 依据与决策链

| 环节 | 内容 | 出处 |
| --- | --- | --- |
| 上一轮交接 | Codex 完成连接测试按钮与登录 Spec 草案，请 Claude 审查 | `docs/reviews/2026-10-29_codex-to-claude-wechat-miniprogram-handoff.md`（本次一并入库） |
| Claude 审查 | 交接属实、连接按钮无缺陷；Spec 有 5 处设计缺口（IP 限频失效、微信契约、`js_code` 日志、可选配置拖垮启动、数值未定），另有 2 项待业务决定 | 本会话审查结论，已写入 Spec 第一段「审查补充」 |
| 用户决定 D1 | 「按钮登录，代码按按需搭」：本期客人点按钮才登录、建档；客户端只留 `ensureLogin()` 一个入口 | 用户 2026-10-29 在选项中选择；Spec「已确认决策」 |
| 用户决定 D2 | 「服务器环境变量」：AppSecret 只放 `.env`，不接后台运行配置 | 同上 |
| Spec 确认与开工 | 用户回复「确认，开始」 | 2026-10-29；Spec 状态行 |
| 提交 | 用户回复「提交到新分支」 | 2026-10-30 |
| 微信接口契约 | `GET https://api.weixin.qq.com/sns/jscode2session`，参数 `appid`、`secret`、`js_code`、`grant_type=authorization_code`；返回 `openid`、`session_key`、可选 `unionid`，失败返回 `errcode`、`errmsg`；官方示例成功时也带 `errcode: 0`；专属错误码 `-1`、`40029`、`40226`、`45011`；未说明 Content-Type | 微信官方《小程序登录凭证校验》，页面最后编辑于 2026-07-04，2026-10-29 读取 |

Spec 全文：`docs/specs/2026-10-29_wechat-miniprogram-login-spec.md`（本次一并入库，含审查补充和已确认决策）。

## 2. 改了什么（按文件与符号）

行号以提交 `d6ce9e2` 为准。

### 2.1 新增后端模块

**`src/homestay_bot/integrations/wechat_miniprogram.py`**

| 符号 | 行为 | 为什么这样做 |
| --- | --- | --- |
| `load_miniprogram_credentials`（:67） | AppID 必须匹配 `wx` 加 16 位小写十六进制，AppSecret 必须是 1–128 个可见 ASCII 字符；缺一项或格式不符返回 `None` | Spec「配置与失败」：配置填错只停用小程序接口，不能让整个系统启动失败 |
| `MiniProgramCredentials`（:60） | `app_secret` 设为 `repr=False` | 防止凭据对象被打印进日志或异常 |
| `WeChatMiniProgramClient.exchange_code`（:101） | 外层 `asyncio.timeout(5)` 限制总时长；`httpx.HTTPError` 或超时统一转成 `UPSTREAM_UNAVAILABLE`，并用 `from None` 切断异常链，日志只记录异常类型 | httpx 的超时按阶段计算，慢速上游可能拖住在途名额；httpx 异常文本可能带含 secret、js_code 的完整 URL |
| `_fetch`（:113） | 每次调用新建 `httpx.AsyncClient`；`follow_redirects=False`；非 2xx 视为异常；正文按流读取，超过 64 KB 立即放弃；参数用 `params=` 传入 | 登录频率很低，按次建连接就不用在应用生命周期里管理关闭；拒绝重定向和限制正文大小是边界防护 |
| `_parse`（:144） | 不看 Content-Type，一律按 JSON 解析；不是对象就视为异常；`errcode` 缺失或等于 0 视为成功（布尔值和字符串都不算合法错误码）；`openid` 必须匹配 `[A-Za-z0-9_-]{1,64}` | 官方未说明 Content-Type；官方示例成功时也带 `errcode: 0`；openid 会拼进 `external_id`，所以只接受安全字符 |
| `_ERRCODE_FAILURES`（:41） | `40029`→`CODE_INVALID`，`40226`→`LOGIN_BLOCKED`，`45011` 和 `-1`→`UPSTREAM_BUSY`，其他→`UPSTREAM_UNAVAILABLE` | Spec 错误映射表 |

**`src/homestay_bot/services/miniprogram_auth.py`**

| 符号 | 行为 | 为什么这样做 |
| --- | --- | --- |
| 常量（:16–23） | 会话 24 小时；每身份最多 5 条会话；每次过期清理最多 100 条；窗口 60 秒内最多 30 次；同时最多 8 个；新档案名称「小程序客户」 | Spec「会话」「限频」中确认的数值 |
| `MiniProgramLoginGate`（:58） | 全局滑动窗口加在途计数；`try_acquire` 中间没有 `await`，在单个事件循环内是原子的；带 `ponytail:` 注释说明为什么不按 IP 限频，以及什么时候需要升级 | `Dockerfile` 和 `compose.yaml` 启动 uvicorn 时没有设置 `--forwarded-allow-ips`，经反代进入时后端看到的很可能都是代理 IP，按 IP 计数会让一个人刷量挡住所有客人 |
| `hash_token`（:98） | SHA-256 十六进制摘要 | token 是 32 字节随机数，熵足够，不需要慢哈希 |
| `MiniProgramAuthService.login`（:120） | 先占用闸门名额，在没有数据库事务的情况下调用微信，然后归还名额；之后在一个事务（`session.begin()`）里依次执行 `ensure_identity`、读取身份编号、`create`；事务提交后才返回 token | Spec：调用外部服务期间不持有数据库连接或锁；中途失败要整笔回滚，不留只建了一半的档案 |
| `profile`（:157）、`logout`（:169） | 按摘要解析会话、撤销会话；`logout` 提交失败时把异常抛给调用方 | 退出没成功就不能声称已退出 |

**`src/homestay_bot/repositories/miniprogram_sessions.py`**

| 符号 | 行为 | 为什么这样做 |
| --- | --- | --- |
| `create`（:32） | 按固定顺序执行：删除本身份的过期会话 → 超过上限时按编号删除最早的会话 → 插入新会话 → 全局过期清理（子查询按到期时间和编号排序，`LIMIT 100`） | Spec「会话数量上限」中规定的顺序 |
| `resolve`（:90） | 关联会话、身份和客户，条件为摘要匹配、未过期、`provider == WECHAT_MINIPROGRAM`、客户未被合并 | 会话挂在身份上，每次请求再查出当前客户；`provider` 条件是防御性的，即使身份行被误改也不会越权 |
| `_as_utc`（:10） | SQLite 读回的时间不带时区时补上 UTC | 本表只写入 UTC；PostgreSQL 的 `timestamptz` 不需要这一步 |

**`src/homestay_bot/routes/miniprogram_auth.py`**

| 符号 | 行为 | 为什么这样做 |
| --- | --- | --- |
| `miniprogram_login`（:120） | 未配置返回 503 `not_configured`；请求体超过 1 KB、不是 JSON、`code` 不是 1–128 个可见 ASCII 字符，都返回 400，且不回显内容、不调用微信；名额满返回 429；微信错误按表映射为 401、403 或 503；其他异常记录类型和调用栈后返回 503 | 不用 FastAPI 的请求体模型：它校验失败时返回的 422 会回显输入的 code |
| `miniprogram_me`（:161） | Bearer token 必须恰好是 43 个 URL 安全字符，否则直接 401，不查数据库 | 先挡掉格式错误的请求，减少无效查询 |
| `miniprogram_logout`（:186） | token 格式正确就返回 204，不论会话是否还存在；撤销失败返回 503 `revoke_failed` | 重复退出没有额外副作用 |
| `_read_login_body`（:93） | 声明的 Content-Length 超过上限时立即拒绝；流式读取超过上限也立即放弃 | `routes/hostex_webhook.py::_read_limited_body` 的错误文案是 Hostex 专用的，不能直接复用 |

### 2.2 修改的已有文件

| 文件与符号 | 改前 | 改后 | 依据 |
| --- | --- | --- | --- |
| `config.py::BootstrapSettings`（:35–36） | 没有小程序配置 | 新增 `miniprogram_app_id`、`miniprogram_app_secret`，都是不带 `Field` 约束的 `str \| None = None`，旁边写了 `ponytail:` 注释 | 同一个类里的 `config_encryption_key` 带有 `Field(min_length=...)`，一旦取值无效整份设置就加载失败 |
| `domain/enums.py::CustomerIdentityProvider`（:37） | 没有小程序渠道 | 新增 `WECHAT_MINIPROGRAM = "wechat_miniprogram"` | `0001_initial` 的 provider 列是 `String(32)`，没有 CHECK 约束，不需要改表 |
| `domain/models.py::MiniProgramSession`（:240） | — | 新模型，字段为 `token_hash`（唯一约束 `uq_miniprogram_sessions_token_hash`）、`identity_id`（外键指向 `customer_identities.id`，`ondelete=CASCADE`，建索引）、`expires_at`（建索引）、`created_at`；只用 `Base`，不带 `TimestampMixin` | 写法与 `AdminCsrfNonce` 一致；会话不会被更新，不需要 `updated_at` |
| `migrations/versions/0036_miniprogram_sessions.py` | — | 新建表和两个索引；降级只删除这张表 | Spec「回退」：降级只让小程序登录失效，客户和身份档案保留 |
| `application.py::application_lifespan`（:4420–4430） | — | 紧接在 `admin_login_rate_limiter` 之后装配服务；凭据无效时只记录一条警告，不装配服务；`_clear_lifespan_state` 中加入 `miniprogram_auth_service`（:4324） | 小程序登录只依赖数据库和可选凭据，不随运行配置激活；没有需要在关闭阶段回收的资源 |
| `main.py`（:87） | — | 注册 `miniprogram_auth_router` | — |
| `middleware.py::NO_STORE_PATH_PREFIXES`（:8） | `("/employee",)` | 加入 `"/api/miniprogram"`，并同步更新两处中间件说明 | 登录响应里带 token |
| `logging.py::_SENSITIVE_QUERY_KEYS` | 有 `code`、`state`、`token` 等，但没有 `js_code` | 加入 `js_code` | `_QUERY_PARAM_IN_TEXT_PATTERN` 要求 `?` 或 `&` 紧挨着键名，所以 `&js_code=` 原来不会被脱敏；`secret=` 已经能被 `_SECRET_ASSIGNMENT_PATTERN` 遮住 |
| `.gitignore` | — | 忽略 `clients/wechat-miniprogram/project.private.config.json` | 这是开发者工具的本机私有配置，而本仓库公开 |

**故意没改的地方**：`session_key` 没有加入日志脱敏规则。代码从不保存、不记录、不返回 `session_key`，测试也断言它不会出现在响应里。如果把 `session_key` 加进 `_SECRET_ASSIGNMENT_PATTERN`，会误伤 `drop_legacy_session_key` 这类已有符号的日志文本。这与 Spec 验收里「日志含 session_key 也要脱敏」的字面写法不一致，请审查这个取舍是否可以接受。

### 2.3 小程序端（`clients/wechat-miniprogram/`）

这个工程是用户在开发者工具里创建的，此前从未入库。本次整个目录入库，包括模板自带的 `pages/logs`、`utils/util.js`、`app.json`、`app.wxss`、`sitemap.json` 和 `project.config.json`；后者含 AppID，AppID 不属于秘密。

| 文件与符号 | 改动 | 依据 |
| --- | --- | --- |
| `utils/auth.js`（新增） | `API_BASE_URL` 统一定义后端地址。`ensureLogin()`：本机已有未过期会话就直接返回，否则执行 `wx.login`、`POST /login`、`setStorageSync`；本机存储失败时用刚拿到的 token 调用 `/logout` 撤销，再报错。`restore()`：本机没有 token 不发请求；收到 401 清除本机；网络失败返回 `unknown` 并保留本机会话。`logout()`：只有收到 204 或 401 才清除本机，否则报错。导出的函数都不向页面返回 token | Spec「客户端交互」与 D1 |
| `app.js::App.onLaunch` | 删除模板里没有实际作用的 `wx.login` | Spec 实施计划 |
| `pages/index/index.js` | 新增 `restoreLogin`、`handleLogin`、`handleLogout`；`authBusy` 防止重复点击，`authGeneration` 用来丢弃过期的回调；连接测试改用 `API_BASE_URL`；删除模板的头像昵称、`getUserProfile`、`bindViewTap` | Spec「客户端交互」 |
| `pages/index/index.wxml/.wxss` | 新增登录面板（checking、logged_out、logged_in、unknown 四种状态）；连接测试按钮从 `primary` 改为普通按钮；删除头像昵称相关样式 | 同上 |

### 2.4 测试与任务记录

| 文件 | 内容 |
| --- | --- |
| `tests/integration/test_miniprogram_auth.py`（29 项） | 用真实服务、真实仓储和临时 SQLite（项目自己的 `create_engine`，开启外键与保存点修正），只把微信替换成 `httpx.MockTransport`，通过 ASGI 发 HTTP 请求。覆盖：<br>• 只返回最小字段、禁止缓存、数据库只存摘要、请求参数符合官方契约<br>• 同一 AppID 下复用客户，AppID 和 OpenID 各自隔离；并发首次登录只建一份档案<br>• 恢复和退出、重复退出、24 小时到期且不续期<br>• 每身份上限、有界过期清理<br>• 管理员调用 `merge_locked` 合并客户后，token 指向合并后的客户<br>• 签发阶段失败时整笔回滚<br>• 6 类无效输入不回显、不调用微信；5 个错误码的映射；5 种畸形 Authorization<br>• 客人 token 与员工会话不能互换；窗口额度和在途上限；未配置时只停用这组接口 |
| `tests/unit/test_wechat_miniprogram_client.py`（29 项） | 凭据加载、`errcode` 缺失或为 0、错误码映射且只请求一次、10 种畸形或重定向响应、超大正文、网络异常不链接原异常且日志里没有 secret 和 code、总超时、含 code2Session URL 的异常文本脱敏 |
| `tests/unit/test_migrations.py`（+1 项） | SQLite 上升级到最新 → 降级到 0035 → 再升级；降级后 `customer_identities` 仍在 |
| `tests/miniprogram/auth.test.js`（12 项）+ `tests/unit/test_miniprogram_client_js.py` | Node 内置测试配合 `wx` 替身，由 pytest 包装调用；本机没有 Node 时跳过 |
| `tasks/todo.md` | 本任务的记录 |

## 3. 验证证据

| 验证 | 结果 | 能证明什么 |
| --- | --- | --- |
| 新增和修改的测试 | 集成 29、单元 29、迁移 1、Node 12，全部通过 | 离线行为符合 Spec |
| 变异核对 | 8 处故意改坏全部被测试发现：去掉 `js_code` 脱敏；去掉到期条件；关闭每身份上限；`openid` 存在时忽略 `errcode`；把 `from None` 改成 `from error`；把建档和签发拆成两个事务；去掉页面的代次检查；去掉登录按钮的忙碌检查 | 这些测试确实能抓到对应缺陷，不是只复述实现 |
| Ruff、Mypy | `ruff check src tests migrations` 通过；`mypy src` 146 个文件无问题 | 静态检查 |
| 本地全量（因为改了 `application.py` 装配，按项目规则执行） | 不含 `tests/browser`：3522 passed、82 skipped | 离线全量没有回归 |
| `tests/browser` | 6 failed、107 errors，全部是 `BrowserType.launch: Executable doesn't exist`（本机缺 Playwright Chromium） | 只是环境问题；本次没有改后台页面，但浏览器测试这一层确实没跑 |
| 小程序 JS 语法 | `node --check` 检查 `auth.js`、`index.js`、`app.js` 通过 | 只是语法 |
| PostgreSQL 离线迁移 SQL | 已有测试 `test_postgresql_offline_upgrade_sql_reaches_head` 包含在全量中并通过 | 0036 能生成 PostgreSQL DDL；没有在真实 PostgreSQL 上执行 |

以上结果都来自本地离线运行，提交后没有改代码。没有 CI 结果，因为分支未推送。

## 4. 风险与需要重点审查的地方

按我判断的严重程度排序。

1. **全局限频可能被恶意耗尽（设计取舍，待业务决定）。** `MiniProgramLoginGate` 只按全局计数，任何人每分钟发 30 个格式合法的假 code，就能让所有真实客人在这一分钟内收到 429。这些假 code 会先打到微信，收到 40029 后才返回。这是 Spec 为了避开「IP 都是代理地址」而做的取舍。缓解办法有两种：在部署层确认真实客户端 IP（配置反代转发头和 uvicorn 的 `--forwarded-allow-ips`）后加上按 IP 计数，或者在反代层限速。请评估当前规模（7 间房）下这个风险能否接受。
2. **人工合并后，token 持有人会看到合并目标客户的名称（待业务决定）。** `/me` 返回 `customer.display_name`。管理员如果把小程序档案合并错了人，客人就会在小程序里看到别人的姓名。这一期 Spec 只要求返回「最小显示名称」，没有专门讨论这一点。
3. **后台客户列表会出现「小程序客户」（待业务决定）。** `SQLAlchemyCustomerRepository.list_customers` 只排除已合并的客户，不按身份渠道筛选，所以每个点过登录的人都会生成一条名为「小程序客户」的档案。不会触发模型调用：`_run_context_maintenance_loop` 只处理有消息的客户（`list_customer_ids_with_messages`）。
4. **PostgreSQL 上的并发首次登录和全局清理子查询没有实测（证据不足）。** 并发首登依赖 `ensure_identity` 的保存点捕获唯一冲突，只在 SQLite 上验证过。`DELETE ... WHERE id IN (SELECT ... ORDER BY ... LIMIT)` 在 PostgreSQL 上语法合法，但没有在 PostgreSQL 上执行过。CI 里有 PostgreSQL 服务（`YUMI_TEST_POSTGRES_URL`），本次没有补 PostgreSQL 专项测试。
5. **每身份会话上限是软上限（已知、接受）。** 两个请求并发时可能都看到 4 条会话，结果共存 6 条，下一次登录时会收敛回 5 条。`SQLAlchemyMiniProgramSessionRepository.create` 的说明里已写明。
6. **意外的数据库异常可能把 openid 带进日志（低）。** 路由在兜底分支用 `exc_info` 记录异常，SQLAlchemy 的异常文本可能包含参数，也就是 `external_id`（`AppID:OpenID`）和 `token_hash`。`token_hash` 不能反推出 token；OpenID 属于假名化的用户标识，现有脱敏规则不会遮住它。正常的唯一冲突在 `ensure_identity` 内部就被处理了，不会走到这条路径。
7. **CI 里 Node 测试可能被静默跳过（低）。** `test_miniprogram_client_js.py` 在找不到 `node` 时会跳过。CI 用的 `ubuntu-24.04` 通常预装了 Node，但工作流没有显式安装，没有逐项核实过。
8. **小程序 logs 页已经无法从界面进入（低）。** 删除头像区块后，`bindViewTap` 也一起删掉了，它是原来唯一跳转到 `pages/logs/logs` 的入口。logs 页是模板示例，已不在登录 Spec 的范围内。
9. **AppID 格式只接受小写十六进制（低）。** 当前真实 AppID 符合这个格式。如果将来遇到大写写法，会被视为未配置并停用，表现是接口返回 503，而不会发出错误的请求。
10. **入库文档含本机路径（低）。** 本次一并入库的 Codex 交接报告第 3 行写有工作根目录的绝对路径。仓库公开，但 `docs/reviews` 里已有同类先例。
11. **`session_key` 脱敏的取舍**：见 2.2 末尾。

## 5. 没有覆盖、没有执行的

- 开发者工具编译、模拟器和真机上的界面与交互。
- 真实微信 code2Session 交换：需要真实 AppSecret 和用户授权；离线 MockTransport 的结果不能证明真实契约。
- 服务器 `.env` 配置、生产迁移 0036、部署和真机登录验收。上线前必须先做可恢复备份。
- PostgreSQL 实库上的并发和清理语句（见风险 4）。
- `tests/browser`（本机缺 Playwright Chromium）。
- AOCI：`aoci_maintain` 返回 `blocked`。原因是有 9 个已有的观察文件待审核、56 个漂移目标，其中大部分与本任务无关，例如 JEV 测试、历史发布记录和审查文档。没有执行 `scope acknowledge`，正式索引没有改动。
- 正式版本号、CHANGELOG 和 `docs/releases/`：本次不是发版，没有升级版本号。

## 6. 回退

- **本地或分支层面**：不合并 `feat/wechat-miniprogram-login`，或者执行 `git revert d6ce9e2`。工作区里的 JEV 等未跟踪文件不在这个提交里，不受影响。
- **将来部署后**：先从服务器 `.env` 删掉两个小程序配置项，接口就退化为 503 `not_configured`，其余系统不受影响；再回退代码。迁移降级到 `0035_knowledge_referrals` 只删除 `miniprogram_sessions`，所有小程序登录失效，客户和身份档案保留。

## 7. 复查命令（只读，在仓库根目录执行）

```bash
git switch feat/wechat-miniprogram-login
git show --stat d6ce9e2
git diff d1abcf5 d6ce9e2 -- src migrations
.venv/bin/python -m pytest -q tests/integration/test_miniprogram_auth.py tests/unit/test_wechat_miniprogram_client.py tests/unit/test_miniprogram_client_js.py tests/unit/test_migrations.py
node --test tests/miniprogram/auth.test.js
.venv/bin/ruff check src tests migrations
.venv/bin/mypy src
```

不要读取 `.env`、`project.private.config.json`、公众平台会话或受保护的项目总结文件。

## 8. 请 Codex 优先回答的问题

1. 第 4 节风险 1 到 3：是否要在上线前处理，还是先记录为待业务决定的事项？
2. 第 2.2 节关于 `session_key` 不加入脱敏规则的取舍是否成立？
3. `MiniProgramAuthService.login` 的事务边界、闸门名额的占用和释放、`_read_login_body` 和 `_bearer_token` 的边界校验，有没有遗漏的失败路径？
4. 0036 迁移和 `MiniProgramSession` 模型是否一致（约束名、索引名、外键级联）？
5. 是否需要在上线前补 PostgreSQL 专项测试（并发首次登录、全局清理语句）？

## 9. 更正与审查后修复（2026-10-30 追加）

本节依据 Codex 审查 `docs/reviews/2026-10-30_codex-to-claude-wechat-miniprogram-login-review.md` 追加，上文原文保留不改，以本节为准。

**事实更正**

- 第 2.2 节表格（`CustomerIdentityProvider` 行）：`customer_identities` 表及其 `provider String(32)` 列由 `migrations/versions/0003_customer_crm.py` 创建；`0001_initial` 里的 provider 属于 `external_requests`，引错了。结论（无 CHECK 约束、无需改表）不变。
- 第 2.1 节 `MiniProgramLoginGate` 行与第 4 节风险 1：uvicorn 未显式传 `--forwarded-allow-ips` 时读取 `FORWARDED_ALLOW_IPS` 环境变量，默认只信任 127.0.0.1。原文写成「没有设置」过于绝对；服务器上该变量未现场核实。
- 第 2.4 节：集成测试是「真实服务与仓储 + 测试自建的应用」，不是生产装配；`application_lifespan` 的装配只由全量测试间接覆盖。测试里「与生产相同装配」的说明已改。
- 第 2.2 节「`session_key` 不加入脱敏规则」的取舍不成立，已改为按字段名精确脱敏（见下）。
- 第 4 节风险 5：「每身份最多 5 条」是软上限，Spec 原文「最多 5 条」已同步改为软上限措辞。
- 第 6 节回退：清空服务器 `.env` 的两个配置项后必须重启应用才生效，配置只在启动时读取。
- 第 7 节标题写「只读」不准确：`git switch` 会改动工作区所在分支。
- 第 2.3 节登录后本机存储失败的撤销：客户端会尝试撤销，但撤销请求本身可能失败，原文没说明此时提示什么；现已改为未确认撤销时如实提示「将在 24 小时内自动失效」。
- `.env.example` 原先漏了两个占位，现已补上 `MINIPROGRAM_APP_ID=`、`MINIPROGRAM_APP_SECRET=`。
- Spec 状态行与 `tasks/todo.md` 的「待授权：提交」已过时，均已更新。

**审查后修复（未提交，待用户确认）**

- R1 `clients/wechat-miniprogram/utils/auth.js`：读取本机存储失败时 `restore` 返回「暂时无法确认」，`logout` 直接报错且不发请求；清除失败时把该 token 记入进程内失效集合，`readStoredSession` 不再返回它，`ensureLogin` 会重新登录。
- R2 `src/homestay_bot/routes/miniprogram_auth.py::_log_local_failure`：本地故障日志只记录阶段、异常类型、原始驱动异常类型与最内层代码位置，不再用 `exc_info`，SQL 参数中的 `AppID:OpenID` 不会进日志。
- R3 `src/homestay_bot/logging.py`：`_SENSITIVE_KEY_PATTERN` 与 `_SECRET_ASSIGNMENT_PATTERN` 加入 `session[_-]?key`，只匹配字段名，`drop_legacy_session_key(` 之类函数名不受影响。
- R4 `tests/integration/test_miniprogram_auth_postgresql.py`：真实 PostgreSQL 上验证并发首登（A 持事务时 B 确实阻塞在身份唯一键，A 提交后只有 1 个客户、1 个身份、2 条会话且两个 token 都能解析）与全局过期清理（LIMIT 生效、事务失败时整体回滚）。需 `YUMI_TEST_POSTGRES_URL`，未设置时跳过。
- 用户决定：`/me` 与登录响应的显示名称固定为「小程序客户」；全局限频风险接受，公开上线前重新评估；后台客户列表出现「小程序客户」接受现状。
