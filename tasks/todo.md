# 当前任务：后台表单 CSRF 令牌迁移至服务端（方案 B）

上一张工作单（v1.3.14 运营台风险优先信息架构）已全部完成并归档在提交 `fdce14a`。

## 目标

消除五个后台家族把 CSRF/确认令牌累积写入签名会话 Cookie 导致的无界增长。令牌改由既有服务端 nonce 设施持有，会话不再存放任何令牌。

## 缺陷证据

`SessionMiddleware`（starlette 1.3.1）把整个 session 序列化进签名 Cookie。五处签发点按实体 ID 写入、且仅在 POST 消费成功时删除，浏览详情页而不提交即永久驻留。实测 `Set-Cookie` 长度：

- `task_csrf` 40 条 → 2648 字节
- `task_csrf` 70 条 → 4408 字节，超出浏览器 4096 上限
- 任务 40 条 + 审批 40 条 → 5024 字节

溢出后浏览器整体丢弃 Cookie，表现为随机掉登录与「表单令牌无效或已使用」。生产当前 86 项逾期任务、366 项需人工关注，一次登录内刷约 68 个任务详情页即可触发。

## 已确认 Spec

### 一、现状证据

- 服务端设施齐备且已上线：`services/admin_csrf.py::AdminCsrfService`、`repositories/admin_csrf.py::SQLAlchemyAdminCsrfRepository`、`domain/models.py::AdminCsrfNonce`（`purpose` 为 `String(64)`）与 `AdminCsrfQuota`、`application.py::SessionAdminCsrfService`。本次不新增表、不新增迁移、不新增依赖。
- 已用服务端 nonce 且本次不动：`employee_auth.py`、`knowledge.py`、`admin_debug.py`、`runtime_config.py`。
- 待迁移五处：`tasks.py`（`task_csrf`，1 签发 6 消费）、`customers.py`（`customer_csrf` 与 `customer_merge_csrf`，2 签发 7 消费）、`complaints.py`（`complaint_csrf`，1 签发 4 消费）、`properties.py`（`property_csrf`，1 签发 2 消费）、`approvals.py`（`approval_nonces`，1 签发 1 消费）。
- `admin_credentials` 带 `CheckConstraint("id = 1")` 单例约束，`admin_id` 恒为 1，因此服务端作用域 `(purpose, admin_id)` 等价于全系统单一作用域，而 `max_active_per_scope` 默认 8 生效。固定 purpose 方案会让每家族全系统只能同时存在 8 个未提交表单，第 9 个详情页在 GET 阶段即 429。
- 模板取的是 context 的 `csrf_token`（`components/ui.html::confirm` 宏同键），审批取 `confirmation_nonce`；键名不变则模板零改动。
- `admin_csrf_service` 与 `employee_access_verifier` 在 `application.py` 同一个 `if admin_auth_available:` 块内发布，管理员引导失败时 `require_employee_session` 已先 503，迁移不引入新的失效模式。
- `tests/admin_auth_helpers.py::configure_admin_auth` 已注入 `MemoryAdminCsrfService`，五个测试文件均调用它，测试装配不需改。
- 现无任何测试锁定跨实体重放会被拒绝。

### 二、功能点（方案 B：per-entity purpose + 作用域淘汰）

- 新增 `routes/admin_form_csrf.py`：`AdminCsrfServicePort`、`get_csrf_service`、`csrf_subject`、`issue_form_csrf`、`consume_form_csrf`、`drop_legacy_session_key`。
- purpose 取 `task-write:{task_id}`、`property-write:{property_id}`、`complaint-write:{review_id}`、`customer-write:{customer_id}`、`customer-merge:{suggestion_id}`、`approval-confirm:{approval_id}`。
- `AdminCsrfService.issue` 与 `AdminCsrfRepository.reserve_and_create` 增 `evict_oldest_in_scope`；作用域满时按 `(expires_at, id)` 删最旧的 `scope_count - max_active_per_scope + 1` 条、按实删数回冲配额后再插入。登录、knowledge、admin_debug、runtime_config 一律不传，默认 `False`，行为不变。
- `AdminCsrfService.issue` 增 `ttl` 覆盖；`SessionAdminCsrfService.issue` 透传两个新参数。
- 五个家族删除各自的 `_issue_csrf` / `_consume_csrf` / 内联 nonce 字典，改调共享助手。`_consume_csrf` 由同步改异步，全部调用点补 `await`（调用方均已是 `async def`）。
- 签发路径执行 `request.session.pop("<旧键>", None)`，主动收缩既有 Cookie。
- context 键名与模板一律不变。

### 三、风险与决策

- 已实测排除：`reserve_and_create` 在 200/500/1000/2000 行时为 3.52/1.84/1.82/1.76 ms，不需要新增 `(purpose, admin_id)` 索引；compose 与 Dockerfile 均为单进程 uvicorn，`SessionAdminCsrfService` 的 `asyncio.Lock` 足以串行化淘汰的读删插序列。
- R1 已实证：`reserve_and_create` 先查全局配额、后查匿名子池，`max_active_anonymous` 只设上限不预留额度。管理员表单占满 1000 后，登录令牌签发失败、登录页 429。**决策：本次一并修**，`admin_id is not None` 时全局天花板取 `max_active - min(max_active_anonymous, max_active // 5)`，默认值下管理员上限 800、登录恒留 200 槽位；`max_active // 5` 用于避免小容量用例被压成 0。
- D1 已决策：**运营四家族 TTL 取 8 小时**（对齐 `SESSION_IDLE_TIMEOUT`，保持今天行为、不引入回归），**审批保持 15 分钟**，超时强制刷新重读当前状态后再确认下单。
- 部署瞬间已打开页面上的旧令牌一次性 409，刷新重试，失败关闭，不做兼容层。
- `purge_expired` 仍是零调用死接口、retention 循环不含 nonce 表，本次不接入，另记。

## 任务清单

- [x] 红测：跨实体重放被拒（五家族各一条）
- [x] 红测：同一详情页连开 9 次不 429 且第 9 次正常渲染
- [x] 红测：连开 60 个任务详情页后会话 Cookie 长度不增长
- [x] 红测：遗留会话键在首次签发后被清除
- [x] 红测：仓储级淘汰双态（`evict_oldest_in_scope` 开关）
- [x] 红测：管理员作用域占满后登录令牌仍可签发（R1）
- [x] 实现：`services/admin_csrf.py` 与 `repositories/admin_csrf.py` 增淘汰模式、TTL 覆盖与登录预留额度
- [x] 实现：`application.py::SessionAdminCsrfService.issue` 透传新参数
- [x] 实现：新增 `routes/admin_form_csrf.py` 共享助手
- [x] 实现：`tests/admin_auth_helpers.py::MemoryAdminCsrfService` 对齐新签名与淘汰语义
- [x] 迁移 `tasks.py`（1 签发 6 消费）
- [x] 迁移 `properties.py`（1 签发 2 消费）
- [x] 迁移 `complaints.py`（1 签发 1 消费助手，覆盖 4 个 POST）
- [x] 迁移 `customers.py`（2 签发 2 消费助手，覆盖 7 个 POST）
- [x] 迁移 `approvals.py`（保留 per-entity 绑定与 `confirmation_nonce` 键名）
- [x] 复跑既有 8 条容量测试，确认登录预留额度未破坏 `max_active=100` 小容量用例
- [x] 差异自审：逐文件复核签发/消费配对，确认无 POST 遗漏令牌校验
- [x] 本地全量门禁

## 验收门禁

- 本地：全量 pytest（基线 1309 + browser 7）、Ruff、mypy 117 文件、`pip check`、`compileall`、`git diff --check`
- 部署：无数据库迁移，仅替换 API 容器；先做可恢复备份
- 生产：登录后逐家族实测一次写操作（任务流转、房源资料、客诉保存、客户标签）；连开 60 个任务详情页后读取实际 Cookie 长度；桌面与 375px 各一遍
- 审批确认涉及真实订单，生产验收需用户单独授权，否则只验收到令牌签发与表单渲染为止
- 回滚：无 schema 变更，回退镜像即可；已签发 nonce 随 TTL 自然过期

## 实施 Review

- 五个家族共 20 个 POST 端点（tasks 6、properties 2、complaints 4、customers 7、approvals 1）经 AST 逐个核对，全部仍消费一次性令牌，无遗漏；与 Spec 的计数一致。
- 会话不再承载任何业务表单令牌：六个旧键的写入点在 `src/` 中已归零，仅 `knowledge.py` 保留其自有的有界列表（八项，本次未改其存储方式）。
- 实测迁移效果：登录后会话 Cookie 227 字节，连续浏览 60 个任务详情页后仍为 227 字节，完全不随浏览增长；迁移前同一操作约 4000 字节并越过浏览器 4096 上限。
- 跨实体绑定首次获得测试锁定，五个家族各一条：任务、房源、客诉、客户详情，以及客户详情令牌不可用于合并确认。审批的跨单重放单独锁定，因为它会创建真实订单。
- 门禁：全量 `1323 passed, 15 skipped` 与 browser `7 passed`（基线 1309 + 7，本次净增 14 条）；Ruff 通过，mypy 118 个源文件通过，`pip check` 无破损，`compileall` 与 `git diff --check` 通过。

## 超出原定边界的一处改动

第二段曾写明不动 `knowledge.py` 的现有行为。实施中把测试桩 `MemoryAdminCsrfService` 补齐为带作用域容量后，`test_knowledge_csrf_token_collection_is_bounded` 失败，随后以真实 `AdminCsrfService` 复现确认这是线上缺陷而非测试假象：

`_MAX_CSRF_TOKENS = 8` 与 `max_active_per_scope = 8` 构成 off-by-one 死锁——`knowledge.py::_issue_csrf` 先签发再裁剪，因此必须签发第九个才会触发裁剪，而第九个在作用域检查处即被拒绝。裁剪分支在生产中从未执行，知识页第九次打开返回 429。旧测试桩没有任何容量约束，长期掩盖了它。

修复为在该处传入 `evict_oldest_in_scope=True`（一个关键字参数），既有测试断言未改动即通过。同时把 `AdminCsrfServicePort` 的导入从 `employee_auth` 改为新的 `admin_form_csrf`，否则 mypy 因旧 Protocol 缺少新参数而报错。

## 待用户决定

- 是否保留上述 `knowledge.py` 修复。它超出第二段边界，但修的正是本次迁移要消除的失败模式，且证据已复现。
- `purge_expired` 仍是零调用死接口、retention 日清理仍不含 nonce 表，按原计划本次未接入。
