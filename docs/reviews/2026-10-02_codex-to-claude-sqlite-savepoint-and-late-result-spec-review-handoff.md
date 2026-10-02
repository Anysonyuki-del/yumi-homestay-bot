# Codex → Claude：SQLite 保存点与迟到建单结果 Spec 审查交接

日期：2026-10-02。状态：审查意见待 Claude 核对，并修订 Spec；本报告没有启动业务实施。

结论：只在 SQLite 建保存点前补开物理事务，以及把迟到建单结果记入审计并在审批详情页提醒管理员，两个方向均可行。当前 Spec 有两项 P1、两项 P2，需要先修订，再按项目的 Spec 确认流程进入实施。

## 1. 交接对象、基线与边界

- 审查对象：[SQLite 保存点提交语义与迟到建单结果可追溯 Spec](</Volumes/02/obsidian codex/homestay-bot/docs/specs/2026-10-02_sqlite-savepoint-and-late-result-spec.md>)，重点为 F1–F3 与风险、验证部分。
- Git 分支：main；HEAD：53d369d29bd11b03ff796ab2030daffccee06c70。
- 写作前没有已跟踪文件改动；未跟踪文件只有上述新 Spec。新 Spec 的 SHA-256 为 f84337ba6910bb30ae7b8167a4461bb2b8b1b20f0bfdb24134d03effd2229a1c，与上一轮审查对象一致。
- 本次仅新增本交接报告。Spec、业务代码、测试、旧交接报告均未修改；不提交、推送、部署，不调用真实 DeepSeek、Hostex、企业微信，不进行生产写入。
- 依据当前源码、真实调用链与已经执行的隔离探针，不把 Spec 中的生产现状叙述当作本次现场验收。未读取受保护的 YuMi民宿AI项目总结.txt。
- 此前 AR8 的旧轮次与新轮次隔离、AR9 的手机号前缀修复已经进入当前基线，不在本报告中重新列为待修缺陷。当前测试依据为 [test_approval_review_actions.py](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_approval_review_actions.py:695>) 的 test_late_result_cannot_overwrite_a_newer_round_that_is_still_creating，以及同文件的 test_an_eleven_digit_number_starting_with_86_keeps_its_prefix。

## 2. 待核对事项

| 编号 | 优先级 | 问题 | 对应 Spec |
| --- | --- | --- | --- |
| SP1 | P1 | savepoint 事件回调没有直接的 driver_connection 属性，照写会抛 AttributeError | F1，第 51 行；驱动接口风险，第 83 行 |
| SP2 | P1 | HostexBusinessError 不足以证明明确拒绝且没有订单；错误分类不清会隐藏应提醒的迟到结果 | 现状第 31 行；F2，第 61–63 行；F3，第 75 行 |
| SP3 | P2 | 审计失败后回滚、抛错不会保留原请求结果；页面报错不能替代诊断线索 | 审计失败风险，第 84 行 |
| SP4 | P2 | 并非全部 SQLite 测试走项目引擎；验证计划还缺少关键失败与页面状态分支 | 现状第 11 行；风险第 81 行；验证第 90–92 行 |

优先级含义：P1 会让方案无法执行或漏报可能多出的订单；P2 影响故障可追查性和验收可信度。

## 3. SP1：修正 savepoint 事件的连接接口

### 证据与影响

当前 [db.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/db.py:14>) 的 create_engine 只配置 SQLite 外键与连接超时，尚未注册保存点保护。

Spec 第 51 行写的是 connection.driver_connection.in_transaction。实际 SQLAlchemy savepoint 事件传入 sqlalchemy.engine.base.Connection，这个对象没有直接的 driver_connection 属性。当前安装版本的公开访问层级是：

~~~python
connection.connection.driver_connection.in_transaction
~~~

依据为当前依赖的 [Connection.connection](</Volumes/02/obsidian codex/homestay-bot/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py:565>)，它返回池代理连接；[_savepoint_impl](</Volumes/02/obsidian codex/homestay-bot/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py:1151>) 在真正创建保存点前把自身传入事件。接口探针结果：

~~~text
event connection type: sqlalchemy.engine.base Connection
has direct driver_connection: False
working driver type: aiosqlite.core Connection
driver in_transaction: False
versions: 3.12.14 0.22.1 2.0.51
~~~

照 Spec 的字面路径实现会在进入保存点时抛 AttributeError。这个接口错误不否定 D1a：按正确访问层级注册运行时监听后，六种提交、回滚组合均符合预期。

### 最小修订与验收

- 修订 F1 和驱动接口风险，明确事件参数类型、公开接口层级、仅在驱动没有物理事务时执行 BEGIN。
- 保留 SQLite 分支限制；已在事务中不重复 BEGIN。无需修改全部保存点调用方，也无需改为所有事务统一 BEGIN。
- 验证无前置操作、先查询、先写入三种入口，各自覆盖外层提交与回滚；包含保存点内撞唯一键后外层继续写入。
- 保留反向验证：原引擎在“先查询、保存点插入、外层回滚”中留下插入记录，保护后全部回滚。
- 本探针不证明文件数据库并发锁行为、ORM 装配与全部仓储调用路径；相应验收见 SP4。

## 4. SP2：按调用阶段和证据分类，不能按异常名称判定没有订单

### 真实调用链

1. [booking_service.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:146>) 的 BookingService.confirm_and_create 捕获全部 HostexBusinessError，并交给 _mark_needs_review，失败描述统一为“明确拒绝”。此处传递 error.error_code，没有传递 error.request_id。
2. [hostex_client.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/integrations/hostex_client.py:19>) 的 HostexBusinessError 表示上游返回的错误信封，保留 error_code 和 request_id；异常类型本身没有“订单一定未创建”的语义。
3. 同文件 [HostexClient._request](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/integrations/hostex_client.py:264>) 把非成功错误码包装成该异常。TRANSIENT_ERROR_CODES 包含 429、500、502、503、504。因此服务端临时失败也可能走到同一异常分支。
4. [HostexClient.create_reservation](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/integrations/hostex_client.py:432>) 使用 retry_safe=False。修订不得增加 POST 自动重放。
5. [BookingService._reconcile_or_mark_review](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:238>) 的写后查询还会捕获业务与传输错误；查询失败只能说明核验未完成，不能据此判定先前建单已被拒绝。

隔离探针使用真实 HostexClient 配合 httpx.MockTransport，合成 HTTP 200、错误信封 error_code=503：

~~~text
synthetic POST error type: HostexBusinessError
business code: 503 request id: SYNTHETIC-SERVER-503
synthetic POST calls: 1
~~~

这证明“HostexBusinessError → 明确拒绝 → upstream_may_exist=false”的推理不成立。它没有证明真实百居易的 503 一定会建单，也没有验证任何真实上游错误码契约。

### 最小修订与验收

保留 D3a“有可能多出订单才提醒”的目标，但补足“明确拒绝”的依据与数据流：

| 结果来源 | 可作出的判断 | 提醒处理 |
| --- | --- | --- |
| 建单返回成功 | 上游返回了成功结果，保留请求编号及匹配订单号 | 显示 |
| 建单传输失败，或返回无法证明未建单的错误码 | 结果未知 | 显示 |
| 写后查询失败 | 核验未完成，不撤销先前建单可能成功的判断 | 显示 |
| 有已核实契约证明未创建订单的明确拒绝 | 可以归为 rejected；列明对应错误码和依据 | 只记审计 |
| 超时恢复核验中的迟到结果 | 按 recovering 的实际来源定义，不能误归为明确拒绝 | 可能存在订单时显示 |

- 在 Spec 中给出调用阶段、错误码与 outcome 的映射。错误码语义没有证据时按 unknown 处理，注明真实契约尚未覆盖。
- 明确“建单成功后写后查询失败”时 outcome 的优先级；可以沿用现有四个值，不必新增状态框架，但必须保持 upstream_may_exist=true 并保留建单请求编号。
- 把建单错误分支持有的 error.request_id 传到迟到结果审计；不要在转换分支中丢失。
- 增加合成 503、已确认拒绝、写后查询失败、超时恢复的审计与提醒断言；核对请求编号、旧轮次和匹配订单号，不只断言异常类型。
- 修订现状第 31、32 行：不能把所有业务异常都描述成“上游没有订单”，也不能断言本地完全没有痕迹。现有 [application.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/application.py:4844>) 的 record_external_call 有独立外部请求记录，但缺少本方案所需的审批编号、旧轮次和匹配订单号关联。

## 5. SP3：审计失败需要最小诊断保底

### 证据与影响

Spec 第 84 行选择审计写入失败时整笔事务回滚、异常上抛，这能保护事务边界，但后半句“确认页看到错误，而不是悄悄丢掉线索”不成立。

迟到结果已经存在于这一请求的内存中。审计失败回滚后，原本准备写入的 request_id、旧轮次和订单号不会因页面报错而保存在数据库。再次确认也不能保证重现这份结果：当前 [BookingService.confirm_and_create](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:106>) 对已经 BOOKED 的审批直接返回；[BookingService._same_round](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/services/booking_service.py:272>) 目前只记录审批编号和当前状态。

### 最小修订与验收

- 保留事务回滚与异常上抛；不要通过吞掉审计异常来宣称成功。
- 在审计失败路径保留一条结构化诊断日志，至少包含审批编号、旧轮次、原请求编号、已经取得的匹配订单号，并沿用数量上限。
- 明确日志是故障排查保底，不能代替成功写入审计，也不保证管理员能在详情页看到提醒。
- 不记录客人姓名、手机号或原始上游请求正文。无需为此增加 outbox、持久重试队列或新的后台任务。
- 用审计写入故障注入验证：异常仍向上传递，事务不留下半条审计或变更审批状态，日志保留足够关联线索且无客人隐私。
- 不要求重发建单来恢复诊断记录；这会改变外部副作用边界。

## 6. SP4：修正测试覆盖声明，补行为分支

### 证据与影响

Spec 第 11、81 行把“全部 SQLite 测试”视为使用 db.py::create_engine，当前代码不支持这个声明：

- [test_operations_repository.py](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_operations_repository.py:46>) 的 test_turnover_task_dedupe_key_is_unique 直接调用 SQLAlchemy create_async_engine。
- [test_admin_credential_repository.py](</Volumes/02/obsidian codex/homestay-bot/tests/integration/test_admin_credential_repository.py:24>) 的 test_bootstrap_imports_only_precomputed_hash_once 也直接创建引擎。

这类用例不会自动获得项目工厂新注册的事件。全量通过可以排查回归，但不能单凭用例数量声称所有保存点路径都经过新配置。

页面也有需要单独覆盖的状态分支：[templates/approvals/detail.html](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/templates/approvals/detail.html:13>) 的 PENDING 分支与第 35 行开始的其他状态说明分支不同。若只把提醒放在后者，回到 PENDING 的审批可能看不到旧轮次提醒。这里是实施时必须验证的风险，当前 F3 尚未实现，不能称为已存在的新页面缺陷。

### 最小修订与验收

- 改为“项目工厂创建的 SQLite 引擎会受影响；部分测试直接创建引擎，覆盖范围需分开说明”。
- 除引擎探针外，至少选择一个真实保存点仓储入口，使用项目 create_engine 与真实会话，证明保存点释放后外层失败不会留下记录，撞键后仍可继续外层事务。
- 不为修正文档批量迁移所有测试工厂；只有目标验证需要接入项目工厂时调整对应测试。
- 补 F2 的 unknown、recovering、审计写入失败及请求编号保留分支；沿用已有交错用例，不重写整套审批测试。
- 补 F3 的 PENDING 与其他状态提醒；可能已建单显示，确认拒绝不显示，没有请求编号或订单号时显示“未取得”，输出不泄露姓名和手机。
- 当前 [routes/approvals.py](</Volumes/02/obsidian codex/homestay-bot/src/homestay_bot/routes/approvals.py:101>) 的 approval_detail 已要求 ADMIN。沿用现有权限检查，验证新提醒没有绕过它，无需另建权限机制。
- F1 是引擎配置变更，实施后按项目要求执行一次覆盖最终改动的全量与临时 PostgreSQL 验证。已有证据仍有效时复用；报告写明哪些用例经过项目 SQLite 工厂，哪些直接创建引擎。
- 模型门禁是否需要，以最终 diff 与 REPLY_PATHS 核对为准；不因本报告而调用真实模型或其他外部服务。

## 7. 已执行隔离验证与复现材料

以下输出来自上一轮 Spec 审查；当前 HEAD 与 Spec 摘要未变化，因此本次写作直接复用。下面保留可重跑的探针，便于 Claude 核对；本次文档写作只检查其 Python 语法，没有再次执行，也没有把运行时监听写入仓库。

### 7.1 保存点父事务矩阵

~~~text
baseline read rollback [2] expected []
guard none rollback [] expected []
guard none commit [2, 3] expected [2, 3]
guard read rollback [] expected []
guard read commit [2, 3] expected [2, 3]
guard write rollback [] expected []
guard write commit [1, 2, 3] expected [1, 2, 3]
~~~

在项目根目录运行；全部使用内存 SQLite：

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from homestay_bot.db import create_engine

async def run_case(before, rollback, patched):
    """验证物理父事务与撞键后的外层写入，仅操作内存数据库。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    if patched:
        @event.listens_for(engine.sync_engine, "savepoint")
        def ensure_parent(connection, name):
            """仅为本次原型补开物理事务，不修改仓库的引擎配置。"""
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
        async with engine.connect() as connection:
            if before == "read":
                await connection.execute(text("SELECT count(*) FROM probe"))
            elif before == "write":
                await connection.execute(text("INSERT INTO probe VALUES (1)"))
            async with connection.begin_nested():
                await connection.execute(text("INSERT INTO probe VALUES (2)"))
            try:
                async with connection.begin_nested():
                    await connection.execute(text("INSERT INTO probe VALUES (2)"))
            except IntegrityError:
                pass
            await connection.execute(text("INSERT INTO probe VALUES (3)"))
            if rollback:
                await connection.rollback()
            else:
                await connection.commit()
        async with engine.connect() as connection:
            rows = list(await connection.scalars(text("SELECT id FROM probe ORDER BY id")))
        expected = [] if rollback else ([1, 2, 3] if before == "write" else [2, 3])
        if patched:
            assert rows == expected, (before, rollback, rows)
        print("guard" if patched else "baseline", before,
              "rollback" if rollback else "commit", rows, "expected", expected)
    finally:
        await engine.dispose()

async def main():
    """保留原缺陷的对照，并验证修正接口后的六种事务组合。"""
    await run_case("read", True, False)
    for before in ("none", "read", "write"):
        for rollback in (True, False):
            await run_case(before, rollback, True)

asyncio.run(main())
PY
~~~

### 7.2 服务端错误信封分类

使用 httpx.MockTransport，不访问网络；所有字段为合成测试数据：

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio
from datetime import date
import httpx
from homestay_bot.integrations.hostex_client import (
    CreateReservationRequest, HostexBusinessError, HostexClient,
)

async def main():
    """验证临时错误也会产生业务异常，禁止用异常名推断未建单。"""
    calls = []

    def fake_response(request):
        """合成一次 HTTP 200 信封内的服务端失败，不发送请求。"""
        calls.append(request.method)
        return httpx.Response(200, json={
            "error_code": 503,
            "error_msg": "synthetic unavailable",
            "request_id": "SYNTHETIC-SERVER-503",
        })

    async with HostexClient("synthetic", transport=httpx.MockTransport(fake_response)) as client:
        request = CreateReservationRequest(
            property_id=101, custom_channel_id=1,
            check_in_date=date(2026, 10, 10), check_out_date=date(2026, 10, 12),
            number_of_guests=1, guest_name="合成客人", mobile="13800138000",
            rate_amount=399, received_amount=399, income_method_id=1,
        )
        try:
            await client.create_reservation(request)
        except HostexBusinessError as error:
            assert error.error_code == 503
            assert error.request_id == "SYNTHETIC-SERVER-503"
            print("synthetic POST error type:", type(error).__name__)
            print("business code:", error.error_code, "request id:", error.request_id)
    assert calls == ["POST"]
    print("synthetic POST calls:", len(calls))

asyncio.run(main())
PY
~~~

其中错误码、请求编号及一次 POST 的断言是交接整理时补充的可运行检查；既有探针输出已证明这些值，本次未再次执行这些断言。

## 8. 验证边界与 Claude 回交要求

已验证：当前 Git 基线、Spec 摘要、相关真实调用链；事件接口对象与属性层级；原引擎回滚残留和修正接口后的六组隔离事务结果；真实客户端对合成 503 信封的异常分类与一次 POST 行为。

未覆盖：F1–F3 的正式实现和最终装配；审计失败保底；审批页面新提醒；完整 PostgreSQL 回归；文件 SQLite 的并发锁竞争；真实上游错误码契约与迟到建单结果；生产运行与真实管理员页面。本次为文档交接，不运行业务全量。

请 Claude 按 SP1–SP4 逐项回复“成立 / 部分成立 / 不成立”，给出源码、接口或验证依据；成立项先修订 Spec 的现状、功能点和验收条件。重点补齐明确拒绝的证据、outcome 的优先级、审计失败诊断与实际引擎覆盖范围。

修订后先返回 Spec 与逐项答复供核对，按适用 AGENTS.md 的分段确认与“开始”门禁进入编码。本报告不是新的实施或外部操作授权，也不要求为交接增加框架、批量重构或无关测试。

本次文档检查：17 处文件链接及行号有效；两段 Python 探针通过 AST 语法检查；Markdown 围栏与行尾空白检查通过；git diff --check 通过。写作前后 src、tests 下 333 个非缓存文件的内容摘要一致，Spec 摘要一致。由于本报告未跟踪，另行检查了报告自身空白格式；没有用 git diff --check 代替未跟踪文档检查。
