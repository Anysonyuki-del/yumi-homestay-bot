# 审批人工核验第二轮审查交接 · Codex → Claude

日期：2026-10-02。范围：审批人工核验 A1–A3，以及 AR1–AR4 修复后的真实确认、回填和恢复链路。

结论：原来的过期 A2 回退、身份匹配过严和 SQLAlchemy 事务嵌套问题已得到修复；AR4 也确实建立了保存点。但目前还有 **两项 P1 和一项 P2 测试缺口**，不能据此认定审批路径已完成验收。下面的异常结果均来自隔离的合成数据，未发现或确认真实生产事故。

本次交接只新增本文档。Codex 没有修改业务代码、测试、Spec 或 Claude 的报告，没有提交、推送、部署或真实外部调用。请 Claude 先核查结论、提出最小修复方案与验收依据；本报告本身不授予新的实施或外部操作权限，后续操作沿用用户当前明确授权与适用 AGENTS.md。

## 1. 阅读入口与证据快照

建议按下面顺序阅读：

1. [Claude 第二轮审查请求](../../docs/reviews/2026-10-01_claude-to-codex-approval-review-round2-handoff.md)：本次直接审查对象，尤其第 2、4、5 节。
2. [Codex 首轮审查交接](../../docs/reviews/2026-10-01_codex-to-claude-approval-review-handoff.md)：AR1–AR4 和原来的 E1–E4 反例。
3. [审批人工核验 Spec](../../docs/specs/2026-10-01_approval-review-backfill-spec.md:86)：第 5 节是本轮有效修订；第 2 节要求状态与审计同事务，第 3 节 R3、R5 涉及防重和并发。
4. [Claude 首轮实施报告](../../docs/reviews/2026-10-01_claude-to-codex-approval-review-handoff.md)：第 7 节更正应与第二轮报告一起理解。

Git 与范围：

- 本文写作时分支为 main，HEAD 为 f3d03cecdc684de4449fca59ae04a8398c8f26e7；实施改动仍在工作区，不能称为一个已提交的 Claude 修复。
- 第二轮审查时，已跟踪 diff 为 19 个文件、+813／−47；本文写作复核时为 21 个文件、+817／−316。当前另有评审目录索引、发布记录修改，以及 2026-09-11 交接文件从 reviews 删除、在 releases 中出现的未跟踪文件；这些既有文档变动不属于本次写作。
- 当前另有未跟踪的两份 Claude 审批报告、首轮 Codex 审批报告、审批 Spec 和人工核验测试。本文保留全部既有修改，仅创建这个新文件。
- 本次重新核对了下面引用的相关代码、测试和 Spec；发现问题的输入仍可复用上一轮审查证据。文档写作不重复运行业务测试。
- Claude 报告里的版本、生产审批数量和环境描述是其报告快照；本文没有据此推断当前生产状态。

规范依据：[开发经验与防回归手册](../../YuMi民宿AI开发经验与防回归手册.md:392)要求唯一键竞争保留事务边界；[环境相关边界](../../YuMi民宿AI开发经验与防回归手册.md:483)要求分别核实 SQLite 与 PostgreSQL 的锁、事务及并发行为。本文以代码和实际反例作判断，不以手册替代验证。

## 2. 原 AR1–AR4 的核对结果

| 原问题 | 第二轮核对结论 | 当前依据与边界 |
| --- | --- | --- |
| AR1：旧 A2 查询能回退新一轮确认 | 原反例已修复 | ApprovalPageService.reopen_after_review 在查询前保存 _attempt_fingerprint，加锁后同时核对状态与指纹；_lock 刷新数据库现值。不能由此推出 BookingService 的迟到写回也得到保护，见 AR6 |
| AR2：姓名、手机全一致才阻止回退 | 按修订 Spec 实现 | _maybe_same_guest 采用同日期下的姓名一致、手机一致或两者都缺失任一成立规则；区号处理仅在去前缀后剩 11 位时生效。同日同名误拦截是已声明的取舍；真实上游字段契约未验收 |
| AR3：事务外写 request_id 导致显式事务嵌套报错 | 原事务错误已修复 | confirm_and_create 把 request_id 传入写后核验，在加锁事务内持久化；项目 create_session_factory 使用 expire_on_commit=False。真实会话门面的成功与核验失败支路均有本地证据；最终写回仍缺少轮次保护，见 AR6 |
| AR4：赋值先于 begin_nested，冲突发生在保存点外 | 保存点问题已修复，整体原子性未通过 | 状态与订单号赋值、flush 已进入保存点，真实唯一键竞争受到控制；但 SQLite 保存点释放后，后续审计失败不能撤销已写状态，见 AR5 |

代码入口：[ApprovalPageService](../../src/homestay_bot/services/approval_page_service.py:89)、[BookingService.confirm_and_create](../../src/homestay_bot/services/booking_service.py:76)、[create_session_factory](../../src/homestay_bot/db.py:38)。

## 3. 待核查问题

| 编号 | 优先级 | 触发与结果 | 定性 |
| --- | --- | --- | --- |
| AR5 | P1 | SQLite A1 回填释放保存点后，审计持久化失败；门面异常关闭后审批仍为 BOOKED，审计为零 | AR4 修复后暴露的事务原子性回归，已复现 |
| AR6 | P1 | 原建单仍在进行，另一次确认恢复为 NEEDS_REVIEW；管理员回退并完成新订单后，旧结果返回并覆盖新订单号和请求编号 | 既有并发保护缺口；本轮恢复成功写回后可完整走通，已复现 |
| AR7 | P2 | 区号参数用例的姓名已经相同，直接走姓名命中分支；手机归一化出错也可能继续通过 | 测试判别力缺口；未认定现有归一化实现有错 |

### AR5 · SQLite 回填与审计没有一起回滚

**真实调用链与代码位置**

- [application.py::SessionApprovalPageService.backfill_reservation](../../src/homestay_bot/application.py:1826)：独立会话调用服务；服务成功返回后才 commit，异常则退出会话。
- [approval_page_service.py::ApprovalPageService.backfill_reservation](../../src/homestay_bot/services/approval_page_service.py:314)：317–320 行在 begin_nested 中修改并 flush 状态、订单号；退出保存点之后才调用 _audit，再于 329 行 flush。
- [db.py::create_engine](../../src/homestay_bot/db.py:14)：项目 SQLite 引擎设置超时、连接检查和外键；没有配置在这条只读前置路径上显式启动物理父事务。

**复现与根因**

使用项目 create_engine、真实会话门面及内存 SQLite，注入“存在待写 AuditLog 时 flush 抛异常”。A1 状态写入已经通过保存点，审计尚未持久化。门面收到异常并关闭会话后，用新会话读取：

~~~text
facade error: synthetic audit persistence failure
after facade failure and close: booked SYNTHETIC-AUDIT-ROLLBACK
audit count: 0
~~~

注入的是审计 flush 阶段的合成异常，未声称观察到真实数据库审计故障。反例证明：这一路的外层失败无法恢复“状态、订单号、审计同事务”的契约。

当前 SQLite 连接行为下，前置 SELECT 只建立 SQLAlchemy 的逻辑事务；在尚无物理父事务时建立、释放首个保存点，状态更新已被提交。服务之后的审计异常或外层 rollback 无法撤回这次更新。

另做了控制实验：同样使用项目引擎，在回填前显式执行物理 BEGIN，服务成功后回滚，再用新会话读取：

~~~text
explicit physical parent then rollback: needs_review None
audit count: 0
~~~

控制实验支持“保存点缺少物理父事务”这一根因判断。它是定位证据，不是要求把手写 BEGIN 直接复制到共享服务或 PostgreSQL。

**最小修复方向与验收**

先核对会话门面、直接服务调用和实际数据库配置，确保保存点确实属于可回滚的外层事务，状态、订单号、审计一起提交或回滚。不要用移除唯一键保护或共享会话全量 rollback 规避问题；如果调整共享引擎事务策略，应据真实调用方评估验证范围。

最低验收需用项目 SQLite 引擎及真实门面：

1. 审计 flush 失败：动作报错后，新会话仍读取 NEEDS_REVIEW、空订单号、无回填审计。
2. 外层主动回滚：服务成功返回但事务未提交，状态、订单号与审计全部撤销。
3. 成功回填：新会话同时读到 BOOKED、填写订单号与一条审计。
4. 唯一键竞争仍转为受控拒绝，不污染审批、不登记成功审计。

PostgreSQL 未复现本问题；不能把 SQLite 结果泛化为 PostgreSQL 失败，也不能用 PostgreSQL 全量通过否定这个 SQLite 反例。

### AR6 · 旧建单结果覆盖新一轮成功订单

**真实调用链与代码位置**

- [booking_service.py::BookingService.confirm_and_create](../../src/homestay_bot/services/booking_service.py:86)：93–94 行将 CREATING 视为可恢复；117 行立即查询原创建结果，不等待仍在运行的原调用。查询为空会转 NEEDS_REVIEW。
- [BookingService._reconcile_or_mark_review](../../src/homestay_bot/services/booking_service.py:180)：先按旧审批对象查询、匹配订单；222–231 行重新加锁后，没有复核最新状态或本次确认轮次，即写 request_id、status、reservation_code。
- [approvals.py::SQLAlchemyApprovalRepository.get_for_update](../../src/homestay_bot/repositories/approvals.py:46)：使用 FOR UPDATE，但没有 populate_existing；原会话的 identity map 还可能保留旧审批字段。
- [BookingService._mark_needs_review](../../src/homestay_bot/services/booking_service.py:273)也无条件写状态和请求编号。这是需要一并核对的失败出口；下面的动态反例验证的是成功结果迟到路径，没有动态覆盖这个失败出口。
- [application.py::SessionApprovalPageService.confirm](../../src/homestay_bot/application.py:1794)与 A2 门面分别创建真实数据库会话；反例经过这两个门面、真实 BookingService 和 SQLAlchemy 仓储。

**受控交错**

使用合成上游控制订单可见时间，在第一次 create_reservation 的 await 内插入其他会话的操作：

1. A2 将初始 NEEDS_REVIEW 回退到 PENDING。
2. 第一次确认房间 101、金额 399；CREATING 已提交，合成上游暂不展示该订单。
3. 另一次确认看到 CREATING，走恢复支路；此时查不到订单，将审批转回 NEEDS_REVIEW。
4. A2 此时仍查不到 101 的订单，允许回退；新一轮确认房间 102、金额 499，成功记录 BOOKED／房间 102／订单号 102。
5. 原 101 建单返回，旧会话按原房间、金额查到唯一订单，将订单号及请求编号 101 覆盖进审批。
6. 新会话读取：房间和金额来自新一轮，订单号及请求编号来自旧一轮。

实测输出：

~~~text
during first create, recovery result: needs_review
new round completed: booked 102 SYNTHETIC-ROOM-102
late first round returned: booked 101 SYNTHETIC-ROOM-101
persisted after late first round: booked 102 SYNTHETIC-ROOM-101 SYNTHETIC-REQUEST-101
synthetic create properties: [101, 102]
~~~

两个建单调用都只在内存中记录，没有真实百居易订单。交错以 await 内顺序控制，数据库读取、提交和写回使用真实实现；不依赖线程调度，也不需要等待后台恢复超时。上游延迟可见使 A2 的只读查重无法识别仍在进行的首次创建，当前 CREATING 恢复入口又允许立即解除创建状态。

**最小修复方向与验收**

这是共享建单流程原有的并发缺口，不能全部归因于本轮新增 request_id 参数。AR3 修好了事务错误，但“最终写回是否仍属于当前轮次”的边界还没有闭合；AR1 的 A2 指纹没有覆盖这里。

核对 CREATING 恢复是否能在原创建仍执行时允许下一轮建单；在最终加锁写回处读取数据库现值，验证当前状态和所属确认轮次，拒绝迟到结果覆盖已处理的审批。只刷新 ORM 对象不足以解决轮次问题；只判断 NEEDS_REVIEW 也不能区分经历完整回退后的不同确认轮次。不要把“查询时对象”和“锁后对象”当作互不影响的快照，二者可能是同一个 ORM 实例。

最低验收：

1. 上面的完整交错必须被安全阻断；如果新一轮已经成功，迟到旧结果不能更改其房间、金额、订单号、请求编号或状态。
2. 当前 BOOKED、REJECTED 或人工回填结果，不能因旧一轮的成功或失败出口倒退或被覆盖；分别核对 _reconcile_or_mark_review 与 _mark_needs_review。
3. 普通首次确认、核验失败、有效的人工核验回退后再确认仍可运行；请求编号保持在事务内写入。
4. CREATING 的重复确认与后台 recover_stale_creating 使用相容的轮次边界，不自动重放上游创建。

应先明确 Spec R3、R5 对“创建仍在进行”“旧请求迟到”“终态不可被旧写回覆盖”的要求，再选择最小实现。当前已持有行锁只能证明写操作串行，不能证明较晚写入的数据属于最新轮次。

### AR7 · 区号测试被姓名命中掩盖

依据：[test_approval_review_actions.py::test_reopen_blocks_any_order_that_may_be_the_same_guest](../../tests/integration/test_approval_review_actions.py:375)中两组区号参数使用与审批相同的姓名“张三”；[_maybe_same_guest](../../src/homestay_bot/services/approval_page_service.py:108)在姓名相同的 118–119 行直接返回 True。虽然前面计算了归一化手机，但测试最终是否拦截不依赖这个手机计算是否正确。

因此，这两组通过不能单独证明 +86、0086 归一化正确。当前实现已由独立断言核对：让姓名不同，+86、0086、86 三种写法均能通过手机分支命中；本身为 11 位、以 86 开头的号码保持原值。本项是持续回归覆盖不足。

最小建议：调整现有区号参数的姓名，使其与审批姓名不同或缺失，只有手机匹配能触发拦截；保留“姓名一致但手机不同”等原有业务取舍用例。无需新增测试框架或为每个辅助函数单独搭建测试文件。

## 4. 可运行反例

在仓库根目录运行。两段探针复用现有人工核验测试的内存 SQLite、合成管理员、合成审批和生产会话门面；不读取生产配置、不调用真实上游、不写仓库业务文件。以下是已执行探针的注释整理版：执行结果复用上一轮审查，本次写作只核对脚本语法。

### E5 · 回填后审计失败

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio
import importlib.util
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from homestay_bot.db import create_engine
from homestay_bot.domain.models import AuditLog, BookingApproval

spec = importlib.util.spec_from_file_location(
    "review_fixture", "tests/integration/test_approval_review_actions.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# 使用项目引擎配置，其他装配复用现有隔离测试。
m.create_async_engine = create_engine

async def main():
    """审计 flush 失败后，用新会话观察回填是否仍被保留。"""
    generator = m.world.__wrapped__()
    factory, sensitive = await anext(generator)
    try:
        class AuditFailureSession(AsyncSession):
            """只在审计即将持久化时注入失败，不影响保存点里的状态更新。"""
            async def flush(self, objects=None):
                """模拟审计阶段失败，供真实门面执行异常关闭。"""
                if any(isinstance(item, AuditLog) for item in self.new):
                    raise RuntimeError("synthetic audit persistence failure")
                return await super().flush(objects)

        failing_factory = async_sessionmaker(
            factory.kw["bind"], class_=AuditFailureSession, expire_on_commit=False
        )
        facade = m._facade(failing_factory, sensitive, m.BookingHostex())
        try:
            await facade.backfill_reservation(1, 1, "SYNTHETIC-AUDIT-ROLLBACK")
        except RuntimeError as error:
            print("facade error:", str(error))
        async with factory() as session:
            approval = await session.get(BookingApproval, 1)
            print("after facade failure and close:", approval.status.value,
                  approval.hostex_reservation_code)
            print("audit count:", len(list(await session.scalars(select(AuditLog)))))
    finally:
        await generator.aclose()

asyncio.run(main())
PY
~~~

### E6 · 原创建迟到，覆盖新订单

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio
import importlib.util
from datetime import UTC, datetime
from homestay_bot.domain.models import BookingApproval
from homestay_bot.integrations.hostex_client import CreateReservationResult

spec = importlib.util.spec_from_file_location(
    "review_fixture", "tests/integration/test_approval_review_actions.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

async def main():
    """在旧创建的 await 内插入真实会话操作，确定性复现迟到写回。"""
    generator = m.world.__wrapped__()
    factory, sensitive = await anext(generator)
    try:
        class InterleavingHostex(m.BookingHostex):
            """上游只在内存中记录，旧订单在新一轮成功后才变得可查询。"""
            def __init__(self):
                """维护合成订单的可见列表，复用现有房态替身。"""
                super().__init__()
                self.visible = []
                self.facade = None

            def reservation(self, request):
                """按当前合成请求生成可精确匹配的订单。"""
                return m._reservation(
                    f"SYNTHETIC-ROOM-{request.property_id}",
                    name=request.guest_name, phone=request.mobile,
                    property_id=request.property_id,
                    created_at=datetime.now(UTC).isoformat(),
                    rates={"rate_amount": request.rate_amount},
                )

            async def create_reservation(self, request):
                """暂缓 101 的可见性，期间恢复并完成 102 的新一轮确认。"""
                self.created.append(request)
                if request.property_id == 101:
                    recovery = await self.facade.confirm(1, 1, m._command(101, 399))
                    print("during first create, recovery result:", recovery.status.value)
                    await self.facade.reopen_after_review(1, 1)
                    second = await self.facade.confirm(1, 1, m._command(102, 499))
                    print("new round completed:", second.status.value,
                          second.property_id, second.hostex_reservation_code)
                self.visible.append(self.reservation(request))
                return CreateReservationResult(
                    request_id=f"SYNTHETIC-REQUEST-{request.property_id}"
                )

            async def list_reservations(self, query):
                """只返回已完成的合成订单，并遵守房间查询范围。"""
                return [
                    item for item in self.visible
                    if query.property_id is None or item.property_id == query.property_id
                ]

        hostex = InterleavingHostex()
        facade = m._facade(factory, sensitive, hostex)
        hostex.facade = facade
        await facade.reopen_after_review(1, 1)
        first = await facade.confirm(1, 1, m._command(101, 399))
        print("late first round returned:", first.status.value,
              first.property_id, first.hostex_reservation_code)
        async with factory() as session:
            approval = await session.get(BookingApproval, 1)
            print("persisted after late first round:", approval.status.value,
                  approval.property_id, approval.hostex_reservation_code,
                  approval.hostex_request_id)
        print("synthetic create properties:", [item.property_id for item in hostex.created])
    finally:
        await generator.aclose()

asyncio.run(main())
PY
~~~

两段是缺陷观察脚本，打印结果后正常退出；退出码 0 不表示功能正确。将其转为修复回归测试时，须断言数据库最终状态、订单归属与审计，不能只断言调用成功或保存点数量。

## 5. 已有验证与缺口

| 证据 | 实际覆盖 | 结果与边界 |
| --- | --- | --- |
| Codex 上一轮独立本地回归 | 人工核验动作、审批确认流程、仓储、审批页服务 | 37 passed，9.04 秒，退出码 0；不包含下面两个新反例的安全断言 |
| E5 与物理 BEGIN 控制实验 | 项目 SQLite 引擎、真实回填门面、审计失败与外层回滚 | E5 观察到 BOOKED 残留且审计为空；控制实验完整回滚 |
| E6 受控交错 | 真实会话门面、BookingService、SQLAlchemy 仓储、A2 恢复再确认 | 观察到两个合成建单调用及跨房间订单号覆盖 |
| 手机分支独立断言 | 姓名不同下的 +86、0086、86，以及 11 位号码前缀保护 | 当前归一化实现通过；现有参数测试仍需增强判别力 |
| Claude 第二轮报告 | 586 条审批相关测试；临时 PostgreSQL 16、迁移至 0034 的全量；Ruff／mypy | 作者报告为 586 passed、2393 passed／15 skipped、静态检查通过；Codex 未独立重跑这些全量结果 |
| 本次文档检查 | 链接存在性、引用行号、探针 Python 语法、空白与修改范围 | 19 个文件链接与引用行有效，2 段 Python 探针语法通过；空白检查和 git diff --check 通过；src／tests 的 332 个文件写作前后内容摘要一致；不重复业务回归 |

37 条本地回归的原命令：

~~~sh
.venv/bin/python -m pytest -q \
  tests/integration/test_approval_review_actions.py \
  tests/integration/test_approval_flow.py \
  tests/integration/test_approval_repository.py \
  tests/unit/test_approval_page_service.py
~~~

未覆盖范围：

- AR5、AR6 的 PostgreSQL 专项事务／交错验收；Claude 已注明 A2 指纹、A1 唯一键竞争用例自身仍使用 SQLite，不能由全量含 PostgreSQL 推出这些路径已在 PostgreSQL 验证。
- 迟到失败出口覆盖终态、后台恢复与管理员恢复的联合交错；当前为源代码风险依据，需按修复方案补有判别力的验证。
- 真实 Hostex 字段格式、可见性延迟、创建结果和订单归属；真实 DeepSeek、企业微信及客人收件。
- 当前部署副本、生产数据库、生产健康与登录后页面验收。

本次没有读取受保护的未跟踪项目总结文件。原前端 M1–M4 的已完成审查与暂停的监测不受本文影响。

## 6. 请 Claude 回传的内容

请集中回复 AR5–AR7，每项说明“成立／部分成立／不成立”，附当前文件、符号与可判别证据。如果反例假设不符合实际契约，应指出具体契约与代码依据，不以现有测试数量替代反例核查。

- AR5：说明 SQLite 物理父事务与保存点的实际关系、准备修复的最小边界，以及审计失败／主动回滚／唯一键竞争的最终数据库断言。
- AR6：说明仍在进行的创建如何与恢复区分，锁后如何识别当前确认轮次，成功和失败出口如何保护新一轮及终态。若修改 Spec R3／R5 或共享事务配置，先写清新的契约和影响。
- AR7：说明怎样让区号用例只有手机分支可以命中；无需扩大成辅助函数逐个镜像测试。

若用户当前授权包含实施，完成后另附最终相关 diff、Spec 修订、精确验证命令与结果，并明确“本地修改与验证完成，可以交给 Codex 审查”。仍在修改或验证时说明剩余事项。保留无关工作区变动，不覆盖旧报告；提交、推送、部署和真实外部操作继续按当前独立授权执行。
