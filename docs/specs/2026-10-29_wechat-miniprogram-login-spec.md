# 微信小程序基础登录 Spec

状态：已确认并本地实现，已提交到 `feat/wechat-miniprogram-login`（2026-10-30）；已按 Codex 审查（2026-10-30）修订，见文末「审查后决定与更正」。未推送、未部署，真实微信登录未验收。

## 目标与范围

让已接通 YuMi 后端的小程序支持微信登录、恢复登录和退出登录。用户已确认先完成这一条链路；入住、订单关联、转人工按钮另行设计。本次只进行本地实现和离线验证，提交、推送、部署、生产配置及真实微信登录验收需要当前单独授权。

## 第一段：现状证据

- `clients/wechat-miniprogram/app.js` 的 `App.onLaunch` 调用 `wx.login`，成功回调没有把 code 发给后端，当前不能形成 YuMi 登录状态。
- `src/homestay_bot/domain/enums.py::CustomerIdentityProvider` 尚无小程序身份渠道；`domain/models.py::CustomerIdentity` 已以 provider 和 external_id 联合唯一，适合增加独立小程序身份。
- `src/homestay_bot/repositories/customers.py::SQLAlchemyCustomerRepository.ensure_identity` 可复用建档逻辑，保存点内处理并发唯一键冲突；`_find_by_identity` 排除已被合并的来源客户。
- `src/homestay_bot/services/customer_service.py::CustomerService.ensure_for_message` 使用微信客服身份建档；`suggest_merge` 只产生待确认建议。小程序登录不能直接证明旧客户或订单归属。
- `src/homestay_bot/main.py::app` 尚未注册小程序认证路由；`routes/employee_auth.py::require_employee_session` 是员工权限入口，不应供客人登录复用。
- `src/homestay_bot/config.py::BootstrapSettings` 尚无小程序配置；`application.py::application_lifespan` 负责数据库和认证服务装配、资源关闭。
- `src/homestay_bot/middleware.py::NO_STORE_PATH_PREFIXES` 当前只保护 `/employee`；新认证接口需在同一响应保护边界加入禁止缓存。
- `src/homestay_bot/logging.py::_SENSITIVE_QUERY_KEYS` 尚无 `js_code`，通用敏感字段规则也未明确覆盖 `session_key`；新微信交换链路必须补齐脱敏，不能记录原始请求或响应。
- `compose.yaml::services.api.env_file` 已从 `.env` 注入环境，新增可选配置无需改变 Compose。`pyproject.toml::project.dependencies` 已有 httpx，无需增加网络依赖。

审查补充（Claude，2026-10-29）：

- `repositories/customers.py::SQLAlchemyCustomerRepository.merge_locked` 合并时把来源客户的 `CustomerIdentity` 行整体改挂到目标客户，身份行本身不变；`src` 中没有直接删除身份的代码，删除客户经外键级联删除身份。会话关联身份 id 并在每次请求时解析客户，即可在合并后自动指向当前客户。
- `migrations/versions/0003_customer_crm.py` 创建 `customer_identities`，其 provider 列为 `String(32)`，没有 CHECK 约束（2026-10-30 更正：原文误引 `0001_initial.py`，那里的 provider 属于 `external_requests`）；新增枚举值无需改约束。`appid:openid` 约 47 字符，未超过 `external_id` 的 128 上限。当前最新迁移为 `0035`。
- `CustomerIdentityProvider.WECHAT_UNIONID` 在 `src` 中只有枚举定义、没有写入方，老档案中不存在可匹配的 UnionID，本期保存它也无法关联任何客户。
- `Dockerfile` 和 `compose.yaml` 启动 uvicorn 时未显式传 `--forwarded-allow-ips`；此时 uvicorn 读取 `FORWARDED_ALLOW_IPS` 环境变量，未设置时只信任 127.0.0.1（2026-10-30 更正：原文写成「未设置」过于绝对，服务器环境变量未现场核实）。经反向代理进入容器时，`request.client.host` 很可能是代理地址（`routes/employee_auth.py::employee_login_submit` 也按此取 IP）。服务器的反代结构未现场核实，按 IP 限频不能当作可靠隔离。
- `logging.py::_QUERY_PARAM_IN_TEXT_PATTERN` 要求 `[?&]` 紧挨 `code`，因此 `&js_code=` 不会被脱敏；`secret=` 会被 `_SECRET_ASSIGNMENT_PATTERN` 遮住。
- `config.py::BootstrapSettings` 现有可选项如 `config_encryption_key` 带 `Field(min_length=...)` 约束，一旦取值无效就会导致整份设置加载失败。
- 微信官方《小程序登录凭证校验》（code2Session，页面最后编辑于 2026-07-04，2026-10-29 读取）：`GET https://api.weixin.qq.com/sns/jscode2session`，查询参数为 `appid`、`secret`、`js_code`、`grant_type=authorization_code`；返回 `openid`、`session_key`，小程序绑定开放平台时另有 `unionid`；失败时返回 `errcode`、`errmsg`，官方返回示例在成功时也带 `errcode: 0`。接口专属错误码：`-1` 系统繁忙，`40029` code 无效，`40226` 高风险用户被拦截，`45011` 调用过于频繁。文档未说明响应的 Content-Type。

## 第二段：已确认功能

1. 首页增加微信登录、登录状态和退出入口，保留已完成的后端连接测试。
2. 小程序取得临时 code 后交给后端，由后端向微信验证，创建或复用小程序客户身份。
3. 登录状态能在重新打开时恢复，并覆盖到期、失败重试及退出。
4. 小程序密钥只保存在服务端，不放入客户端、响应、日志或版本库。
5. 本期不自动合并旧客户，不读取订单、手机号或入住凭证。

## 第三段：待确认决策

### 已确认决策（2026-10-29）

- **D1 登录方式：按钮登录，代码按「按需登录」搭建。** `wx.login` 不弹窗、不需要用户同意，因此登录方式真正决定的是何时建客户档案。本期只有客人点「微信登录」才换 code、建档，避免每个打开者都在后台客户列表生成一条无业务意义的「小程序客户」。客户端只提供一个 `ensureLogin()` 入口，本期由登录按钮调用；将来一键入住等需要身份的功能直接调用它，届时去掉按钮即可，无需重写。会话固定 24 小时：转为按需静默登录后，到期重登对客人不可见。
- **D2 密钥存放：服务器环境变量。** AppSecret 只在泄露重置时才改，接入后台运行配置（`services/runtime_config_service.py`）需要改动已上线的配置快照、加密存储、表单、测试器与配置版本发布链路，工作量和风险都不相称。代码旁加 `ponytail:` 注释：换密钥需要改 `.env` 并重启；需要在后台管理或热更新时再迁入运行配置。

### 身份与权限

增加 `WECHAT_MINIPROGRAM` 身份渠道，以服务端配置的 AppID 与微信返回的 OpenID 组成有命名空间的 external_id。客户端不得提交可信身份、customer_id 或权限。UnionID 不作为本期自动合并依据。新档案使用通用名称“小程序客户”，不申请昵称、头像或手机号权限。登录与 `/me` 返回的显示名称固定为“小程序客户”，人工合并后也不显示合并目标的档案名称（2026-10-30 用户决定）。

认证会话绑定 CustomerIdentity，每次查询通过该身份解析当前客户。沿用后台人工合并后身份归属，不能在 token 中永久固定旧 customer_id。小程序凭证只用于小程序接口，不能满足员工后台鉴权；已有管理员 Cookie 也不能代替客人 token。

### 会话

使用不可预测的随机 token，数据库仅保存 SHA-256 摘要、身份外键、创建及到期时间；固定有效期 24 小时，不自动续期。客户端仅在本机存储 token，通过 Authorization Bearer 请求，重开时先向服务端核验。

退出需删除服务端当前会话，确认后清除本机状态；网络失败保留明确的失败反馈和重试入口，不能声称服务端已退出。过期或失效 token 返回 401，由客户端清除失效状态并展示重新登录入口。数据库中的过期会话在认证读取时拒绝，并在成功登录事务中有界清理，避免新增清理 worker。

会话数量上限：每个身份保留约 5 条未过期会话，新建第 6 条时在同一事务内删除最旧的一条。这是软上限：两次并发登录可能各自看到 4 条而共存 6 条，下一次登录即收敛；它只防止单个身份无限堆积，不作为安全边界。成功登录事务按顺序执行：删除本身份的过期会话 → 超过上限时删除最旧会话 → 插入新会话 → 删除全局过期会话（按到期时间最早的顺序，每次最多 100 条）。token 为 32 字节密码学随机数，用 URL 安全的 Base64 编码；数据库只存它的 SHA-256 十六进制摘要，并对摘要建唯一索引。会话指向 `customer_identities.id` 的外键使用 `ondelete=CASCADE`，删除客户时身份和会话一起删除。

### 配置与失败

新增可选 `MINIPROGRAM_APP_ID`、`MINIPROGRAM_APP_SECRET` 服务端环境配置（D2）；本期不新增后台配置 UI 或热更新机制。两项在 `BootstrapSettings` 中写成不带任何 `Field` 约束的 `str | None = None`，填错值也不会导致整份设置加载失败；是否可用在装配小程序认证服务时判断：只配一项、或 AppID 不符合 `wx` 加 16 位十六进制的格式，都视为未配置，仅小程序认证接口返回 503，其余系统不受影响。配置值不在聊天中索取，真实密钥配置留到上线授权阶段。

微信交换只调用官方 code2Session 契约（见第一段审查补充），使用固定官方 HTTPS 主机、5 秒总超时、拒绝重定向，不自动重试一次性 code；客户端重试时重新调用 `wx.login`。参数用 httpx 的 `params` 传入，不手工拼接 URL。响应正文上限 64 KB，不依赖 Content-Type，一律按 JSON 解析；解析失败或不是对象时视为上游异常。成功的判定条件是 `errcode` 缺失或等于 0，且 `openid` 为非空字符串（不超过 64 字符）。错误映射：

| 上游结果 | 接口返回 | 客户端提示方向 |
| --- | --- | --- |
| `40029` code 无效 | 401 `code_invalid` | 重新取 code 再登录 |
| `40226` 高风险用户被拦截 | 403 `login_blocked` | 当前微信账号暂不能登录，请联系管家；不提示重试 |
| `45011` 调用过于频繁、`-1` 系统繁忙 | 503 `upstream_busy` | 稍后重试 |
| 其他 errcode、超时、网络失败、非 2xx、正文无效 | 503 `upstream_unavailable` | 稍后重试 |

所有错误都返回安全错误码和中文提示，不透传微信响应、`errmsg` 或异常正文。日志只记录错误类别和 errcode 数字，不记录 URL、请求或响应正文。

限频：`routes/employee_auth.py::AdminLoginRateLimiter` 按 IP 计数，而第一段已说明当前部署下 IP 很可能都是代理地址，按 IP 限频会让一个人的刷量挡住所有客人。因此小程序登录不承诺按 IP 隔离，只设两道全局闸门，且不与员工登录共用额度：60 秒滑动窗口内最多 30 次登录请求（超出返回 429）；同时调用微信的请求最多 8 个（超出立即返回 429，不排队等待）。以上均为单进程内存状态，多实例统一限流不在本期范围，上线实例形态变化时须另行评估；代码旁用 `ponytail:` 注释写明这一上限。

输入边界：登录请求体最大 1 KB，code 为 1–128 个可见 ASCII 字符；`Authorization` 请求头最长 256 字符，必须是 `Bearer` 加 token，格式不符直接返回 401，且不查数据库。

### 客户端交互

客户端在 `utils/auth.js` 集中提供 `ensureLogin()`、恢复和退出，并统一定义后端基础地址；连接测试改用同一个地址常量。首页删除模板里的头像昵称区块（包括已被微信收回能力的 `getUserProfile`），以免 `hasUserInfo` 被误当作登录状态。

登录、恢复、退出均展示加载、成功及错误状态；重复点击不重复请求。使用操作代次或等价机制丢弃过期回调，避免退出后迟到的恢复请求把页面改回已登录。没有 token 时不发恢复请求；本机存储失败须反馈且清理本次新建会话，不能显示不可恢复的登录成功。

## 接口契约

| 接口 | 输入 | 成功结果 | 主要失败 |
| --- | --- | --- | --- |
| `POST /api/miniprogram/login` | 有界 JSON `{code}` | token、到期时间、最小显示名称 | 400 输入无效；401 code 无效；403 微信判定高风险被拦截；429 限频或在途已满；503 未配置或微信暂不可用 |
| `GET /api/miniprogram/me` | Bearer token | 到期时间、最小显示名称 | 401 无效或过期；503 暂不可用 |
| `POST /api/miniprogram/logout` | Bearer token | 204；重复退出不产生额外副作用 | 503 撤销未完成 |

响应不返回 OpenID、UnionID、session_key、内部客户编号或员工权限。认证响应与错误均禁止缓存；输入校验错误不得回显 code 或 token。

## 实施计划

按同一目标连续实施，不拆成独立发布，不自动提交或部署。完整 Spec 确认并收到明确“开始”后，在 `tasks/todo.md` 增加本任务跟踪。

| 文件与符号 | 职责和验证目标 |
| --- | --- |
| `src/homestay_bot/config.py::BootstrapSettings`、`.env.example` | 增加可选配置与无秘密占位；验证未配置时兼容现有启动 |
| `src/homestay_bot/domain/enums.py::CustomerIdentityProvider`、`domain/models.py::MiniProgramSession` | 独立身份、摘要会话、外键和到期索引；旧数据不回填、不合并 |
| `migrations/versions/0036_miniprogram_sessions.py::upgrade/downgrade` | 顺接当前 0035，创建会话表；降级仅删本期会话表，保留客户档案；上线前必须备份 |
| `src/homestay_bot/integrations/wechat_miniprogram.py::WeChatMiniProgramClient` | 单次 code 交换、输入输出校验、超时和安全错误；用 httpx MockTransport 验证官方契约及零自动重试 |
| `src/homestay_bot/repositories/miniprogram_sessions.py::SQLAlchemyMiniProgramSessionRepository` | 身份解析、token 摘要持久化、到期、撤销与有界清理；复用客户 ensure_identity |
| `src/homestay_bot/services/miniprogram_auth.py::MiniProgramAuthService` | 编排微信验证与短数据库事务；外调时不持数据库锁；提交成功后才返回 token |
| `src/homestay_bot/routes/miniprogram_auth.py::router` | 三个公开契约、Bearer 验证、独立限频、安全错误及输入长度限制 |
| `src/homestay_bot/application.py::application_lifespan/_clear_lifespan_state`、`main.py::app` | 装配、关闭客户端和注册路由，保持既有启动降级和取消清理行为 |
| `src/homestay_bot/middleware.py::NO_STORE_PATH_PREFIXES`、`logging.py::_SENSITIVE_QUERY_KEYS` | 加入 `/api/miniprogram` 前缀使认证响应不缓存；`_SENSITIVE_QUERY_KEYS` 加入 `js_code`；验证含合成 secret、js_code、Bearer 的异常 URL 文本脱敏 |
| `clients/wechat-miniprogram/utils/auth.js`、`app.js::App` | 集中提供 `ensureLogin()`、恢复、退出与后端基础地址；移除原先无消费的 wx.login；恢复时只验证已有 token |
| `clients/wechat-miniprogram/pages/index/index.js/.wxml/.wxss` | 登录、退出、恢复结果和在途保护；保留连接测试并改用统一地址；删除模板头像昵称区块 |
| `.gitignore` | 加入 `clients/wechat-miniprogram/project.private.config.json`（开发者工具本机私有配置，仓库公开） |

## 验收与交付边界

- 单元验证：上游成功（包括带 `errcode: 0` 和不带 errcode 两种）、四类错误码按表映射、缺 openid、非 JSON 正文、超长正文、超时及零自动重试；输入异常不回显；日志含合成 code/js_code/session_key/token/secret 时均脱敏；全局窗口和在途上限到达时返回 429；配置缺一项或格式无效时只有小程序接口返回 503，应用正常启动。
- 临时数据库与 HTTP 集成：同一微信身份复用客户、不同 AppID/OpenID 隔离；并发首次登录不重复建档；无效/到期 token 拒绝；退出后旧 token 不可用；管理员与客人凭证不能互换；人工合并后会话解析当前身份归属；事务失败无半成品会话。
- 迁移：临时 SQLite 升降级、PostgreSQL 离线 SQL；涉及唯一冲突的行为以隔离 PostgreSQL 专项补证，环境不可用则如实留下未覆盖项，不能连生产替代。
- 小程序行为验证：登录成功、失败和重新取 code；重开恢复；过期；退出成功和失败；重复操作、迟到回调、存储失败。使用 Node 的内置测试与 wx 替身验证状态，不新增依赖；开发者工具验证编译和可见反馈。
- 静态检查运行受影响 Python 的 Ruff、mypy 与小程序 JS 语法检查；因触及 application 装配，本地最终运行一次离线全量，关闭真实契约。
- 真实微信交换、服务器运行配置、生产迁移、部署和手机实际登录不包含在上述离线通过结论中；获准并完成后分别记录结果。
- 本次不修改模型回复链，不运行真实模型回复门禁，不调用 Hostex 或企业微信。

## 回退

本地实现可按本任务文件及差异回退，保留用户已有小程序模板和无关改动。后续部署若需回退，先停用小程序登录入口再回退代码；停用方式是清空 `MINIPROGRAM_APP_ID`/`MINIPROGRAM_APP_SECRET` 后重启应用，配置只在启动时读取，不重启不生效；本期新增会话可失效，客户及身份档案不自动删除。降级删除会话表会使现有小程序登录全部失效，恢复需要重新登录；数据恢复以获准变更前备份为依据。

## 审查后决定与更正（2026-10-30）

依据 Codex 审查（`docs/reviews/2026-10-30_codex-to-claude-wechat-miniprogram-login-review.md`）与用户 2026-10-30 的决定：

- 全局登录限频（30 次/60 秒、8 个在途）接受现状：一个人刷量可以暂时挡住所有客人登录，本期小程序未对外开放，影响可控。公开上线前须重新评估，届时先现场核实反代与 `FORWARDED_ALLOW_IPS`，再决定是否按 IP 计数。
- 显示名称固定为“小程序客户”，见第二段。
- 每个点过登录的微信身份都会在后台客户列表新增一条“小程序客户”，接受现状。
- 修复项：小程序本机存储读取或清除失败时不再误报已登录或已退出，失效 token 在本进程内不再被使用；登录路由的本地故障日志只记录异常类型与位置，不再输出可能带 OpenID 的 SQL 参数；日志脱敏按敏感字段模式遮住 `session_key` 及带前缀、大小写不同的变体（如 `wx_session_key`、`SessionKey`），裸函数名不受影响；补充 PostgreSQL 专项（并发首登、全局过期清理的 LIMIT 与回滚），需 `YUMI_TEST_POSTGRES_URL`，未设置时跳过；CI 的「隔离 PostgreSQL 回归」步骤已显式列入该文件（Codex 复审 F1，2026-10-30）。
- 登录后本机存储失败时客户端会尝试撤销新会话；撤销请求失败时只提示“未能确认撤销，将在 24 小时内自动失效”，不声称已撤销。
