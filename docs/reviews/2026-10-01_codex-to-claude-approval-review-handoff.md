# 审批人工核验回填实施审查交接 · Codex → Claude

日期：2026-10-01。结论：改动清单与工作区基本一致；A2 防重复建单存在两处 P1，重新确认依赖的既有建单路径另有一处 P1，A1 保存点描述需要更正。本文件交给 Claude 核查；此次只新增交接文档，没有修复业务代码。

请 Claude 对 AR1–AR4 分别回复“成立／部分成立／不成立”，给出文件、符号、调用链或可运行反例，以及最小修订范围和必要验收。AR3 必须单独标明为既有依赖问题，不能归因于本轮新增代码。本文是审查交接，不代表开始实现、提交、推送、部署或调用真实外部接口的授权。

## 1. 阅读入口、基线与证据边界

- 被审文件：[Claude 审批回填与遗留小项实施交接报告](../../docs/reviews/2026-10-01_claude-to-codex-approval-review-handoff.md)，重点为 §2、§4、§5。
- 功能依据：[审批人工核验回填 Spec](../../docs/specs/2026-10-01_approval-review-backfill-spec.md) 的 A2、R3、R5、R6，以及 A1 的 R2。
- 规则依据：[AGENTS.md](../../AGENTS.md) 的真实调用链、最小必要验证、真实装配与外部操作边界；[开发经验与防回归手册](../../YuMi民宿AI开发经验与防回归手册.md) 的“百居易”“后台管理台安全边界／事务、日志与迁移”相关条目。

编写前核对：分支 `main`，HEAD `f3d03cecdc684de4449fca59ae04a8398c8f26e7`。18 个已跟踪文件存在工作区修改，合计 +748／−43；另有 Claude 本轮实施交接、审批回填 Spec、`test_approval_review_actions.py` 三个未跟踪文件。审查对象是 HEAD 加该工作区实现，不是一份新的 Git 提交。新增本文后，未跟踪文档再增加一份。

源码核读与上一轮审查涉及的行为一致，故本文复用上一轮有效测试和探针结果。本文是日期化快照；接手时先比对相关输入，改变了哪些输入，就只重新核验受影响结论。没有检查部署副本、运行态、生产审批数量或生产健康；Claude 报告中的生产描述不作为本次现场证据。

## 2. 发现与优先级

| 编号 | 优先级 | 结论 | 性质 |
| --- | --- | --- | --- |
| AR1 | P1 | 迟到的 A2 反查能回退新一轮建单后再次需复核的审批 | 本轮新增回退路径的状态竞争缺陷 |
| AR2 | P1 | 同名同日期已有订单的手机号带区号或缺失时，A2 仍允许回退 | 实现符合 Spec 的字面匹配条件，但不满足 R3 的安全目标；Spec 也需修订 |
| AR3 | P1 | 上游建单成功后，真实会话装配报事务重复开启，本地停在创建中 | 既有下单路径缺陷；本轮功能依赖该路径，操作闭环未获证明 |
| AR4 | P2 | A1 更新在建立保存点前已 flush，报告的保存点描述不准确 | 文档与事务边界更正；当前独立会话异常退出已验证不会错误提交 |

A1“不再向百居易验证订单号”在 Spec 中是明确决定，本文没有将这一决定重新列为缺陷。客诉草稿串入旧诉求不在本轮审查范围内。

## 3. AR1：只看最终状态，识别不了审批已经重新建过一次单

### 3.1 代码与契约依据

- [ApprovalPageService.reopen_after_review](../../src/homestay_bot/services/approval_page_service.py:294)：先读取原审批，在外部反查期间不持有行锁；查询使用读取时的 `property_id` 与日期。
- [回退前状态复核](../../src/homestay_bot/services/approval_page_service.py:354)：`_lock` 使用 `populate_existing=True` 刷新对象，但只要求当前状态为 `NEEDS_REVIEW`，随后清空确认字段并改为 `PENDING`。
- [BookingService.confirm_and_create](../../src/homestay_bot/services/booking_service.py:87)：PENDING 可以重新确认；新一轮确认写入新的房间、金额和 `approved_at`，创建或写后核验不确定时可再次转为 NEEDS_REVIEW。
- Spec R3 要求防重复建单，R5 要求后到的操作按最新处理结果拒绝。Claude 报告 §5.1 假定另一轮处理后一直是 PENDING；这不能覆盖再次回到 NEEDS_REVIEW 的情况。

### 3.2 已执行反例 E1

使用真实 `ApprovalPageService`、真实 `BookingService`、SQLAlchemy 审批仓储与权限校验、合成资料和临时 SQLite；外部接口全部是替身。交错在替身的 await 边界控制，顺序如下：

| 步骤 | 操作 | 结果 |
| --- | --- | --- |
| 1 | A 对需复核的审批发起 A2，反查原房间 101 | 外部查询尚未返回 |
| 2 | B 对同一审批完成 A2 | 改为 PENDING 并提交 |
| 3 | B 重新确认，改选房间 102 | 建单替身记录写调用后模拟响应丢失，后续查询也失败；真实状态机提交 NEEDS_REVIEW，保留新确认字段 |
| 4 | A 原房间 101 的旧查询返回空集合 | 复核只看到 NEEDS_REVIEW，仍成功回退；新房间、金额等被清空 |
| 5 | 再次确认房间 102 | 又调用一次建单替身 |

上一轮实际输出：

```text
stale A2 lookup property: 101
new booking result: needs_review property: 102
stale A2 accepted: pending property: None
new booking result: needs_review property: 102
synthetic create calls: [102, 102]
audit actions: ['booking_approval_reopened', 'booking_approval_reopened']
```

该反例证明旧反查可以回退另一轮创建的不确定结果，并允许再次发起建单。它不证明生产已发生重复订单，也不以 SQLite 证明 PostgreSQL 的行锁行为。此交错中的各次写事务先后结束，即使末尾加行锁，也不能仅靠状态值识别处理轮次。

### 3.3 待核查的最小方向与验收

反查结果应绑定读取时的审批尝试或可可靠辨别变化的版本。加锁后除校验状态，还要确认反查针对的尝试没有变化；变化即拒绝旧请求。先核查已有字段能否可靠承担该判断，再决定是否需要新增版本字段；不要未经核查就增加迁移或跨外部调用持锁。

验收必须包含完整的 `NEEDS_REVIEW → PENDING → CREATING → NEEDS_REVIEW` 交错，断言旧 A2 不再成功、不清空新确认字段、不再登记第二条回退审计，并保持新一轮的需复核状态。只有“期间被改为 REJECTED”的测试不足以覆盖此缺陷。

## 4. AR2：字段不完全相同，不等于确认没有已有订单

### 4.1 代码与契约依据

- [reopen_after_review 的 matches](../../src/homestay_bot/services/approval_page_service.py:339)：日期、姓名、手机要求同时完全一致；不匹配的候选统一视为可回退。
- [Reservation](../../src/homestay_bot/integrations/hostex_client.py:111)：`guest_name`、`guest_phone` 允许为空，没有在该模型中归一化区号或姓名格式。因此字段缺失属于边界模型可接受输入。
- Spec A2 字面上确实写了“日期、姓名、手机都一致”；但 R3 要求疑似订单阻止回退，用户决定记录中“结果查不清”应保持需复核。实现与功能表一致，功能表与安全目标之间仍有缺口。

### 4.2 已执行反例 E2

同一临时审批、同一客人姓名、同一入住和退房日期，返回一笔已有订单。只改变上游手机号表示：

```text
country prefix existing same-name same-date order -> pending
missing phone existing same-name same-date order -> pending
```

第一种是在合成审批手机号前加 `+86`；第二种返回 `guest_phone=None`。两种均成功回退。反例没有确认真实百居易一定采用这些表示；它证明当前代码对这些模型允许的输入没有守住不确定结果边界。

### 4.3 待核查的最小方向与验收

先区分“确定不是同一笔”与“存在疑似但无法排除”。能够确定等价的号码、姓名表示可在最小匹配位置归一化；同日期且部分身份信息相符、另一字段缺失或无法可靠核对时，应按疑似或不确定处理。不要把缺失字段直接当作不存在订单，也不要用金额、创建时间完全相等缩小疑似范围。

“姓名或手机任一一致即阻止”是可讨论的保守规则，不是本文已经确认的产品决定；它可能阻止同名不同人的独立订单。请 Claude 说明误拒绝与重复建单风险的取舍，并同步修订 A2、R3 和提示文案，再按项目 Spec 门禁推进实现。

验收至少包含：等价区号表示、姓名表示差异、同日期部分身份信息缺失、明显无关的另一客人。前三者具体判定按确认后的规则断言；明显无关订单不能无条件阻断所有回退。`property_id=None` 时按日期查询全店是保守范围，本文未发现其本身放宽防重边界，但未单独验证真实全店查询契约。

## 5. AR3：重新确认的既有成功建单路径报事务冲突

### 5.1 真实装配与根因依据

调用链：`SessionApprovalPageService.confirm → ApprovalPageService.confirm → BookingService.confirm_and_create → _reconcile_or_mark_review → SQLAlchemyApprovalRepository.transaction`。

- [SessionApprovalPageService._service](../../src/homestay_bot/application.py:1766)：将真实 SQLAlchemy 仓储、权限校验和 BookingService 绑定到同一个会话。
- [SessionApprovalPageService.confirm](../../src/homestay_bot/application.py:1800)：服务成功返回后才执行最终 commit。
- [BookingService.confirm_and_create](../../src/homestay_bot/services/booking_service.py:119)：创建前的显式事务已经提交 CREATING；上游成功后，在新显式事务之外对已持久化对象赋 `hostex_request_id`，触发会话自动开启事务。
- [_reconcile_or_mark_review](../../src/homestay_bot/services/booking_service.py:212) 又要求打开仓储事务；[SQLAlchemyApprovalRepository.transaction](../../src/homestay_bot/repositories/approvals.py:21) 直接 `session.begin()`，没有接管已自动开启的事务。

审查时 `git diff -- src/homestay_bot/services/booking_service.py src/homestay_bot/repositories/approvals.py` 为空，上述两个文件相对 HEAD 没有工作区修改。因此 AR3 是既有问题，不是 Claude 本轮新增回填代码引入的回归。

### 5.2 已执行反例 E4

使用真实 `SessionApprovalPageService` 门面及其 `_service` 装配，registry 仅替换外部客户端。先真实执行 A2，再重新确认；合成房态满足所有住宿晚，建单替身返回成功，写后查询返回唯一、时间与金额匹配的订单。

```text
real session facade confirm result: InvalidRequestError A transaction is already begun on this Session.
persisted approval after successful synthetic create: creating code: None
synthetic upstream creation succeeded: True
```

该反例绕过 HTTP 层，但经过生产使用的会话门面、服务和仓储装配。未验证实际 HTTP 页面错误呈现，也没有创建真实订单。

现有 `test_approval_flow.py` 使用内存仓储模拟事务；本轮回填测试的 BookingStub 不允许建单。它们通过不能证明 SQLAlchemy 会话下“回退后再确认”的成功路径通过。

### 5.3 待核查的最小方向与验收

先明确创建后的请求编号保存、写后核验与最终状态写入的事务所有权。保留创建前提交 CREATING 的防重复边界，不得通过自动重放 `create_reservation`、吞异常或无条件提交共享会话解决事务异常。若要处理此既有依赖问题，应在 Spec 中明确纳入范围，不把它记为本轮新增缺陷。

必要验收使用真实 Session 门面和 SQLAlchemy 仓储：A2 成功后再次确认，替身建单成功且返回唯一匹配订单，最终 BOOKED、订单号与请求编号持久化、建单恰好一次；同时保留创建结果不确定时转需复核、已 BOOKED 不重复创建的相关回归。不要继续只用内存 transaction 替身验证此边界。

## 6. AR4：A1 回滚安全成立，保存点的解释不成立

### 6.1 代码与已执行反例 E3

[ApprovalPageService.backfill_reservation](../../src/homestay_bot/services/approval_page_service.py:277) 先赋 BOOKED 和订单号，再进入 `begin_nested()`。当前 SQLAlchemy 2.0.51 在开启嵌套事务前 flush 已有脏对象，唯一键异常可发生在真正创建 SAVEPOINT 之前。

探针在查重返回空之后，让另一个独立会话登记相同订单号，再继续 A1；监听 `after_begin` 统计真正开始的嵌套事务。

```text
A1 collision controlled refusal; session active: False started savepoints: 0
A1 persisted state: needs_review code: None audits: 0
```

当前 [SessionApprovalPageService.backfill_reservation](../../src/homestay_bot/application.py:1826) 遇异常不会走到 commit，会话关闭回滚。因此已验证当前独立会话调用没有错误持久化 BOOKED、订单号或审计；没有发现当前用户数据损坏。

### 6.2 文档更正与修订边界

报告 §2.1 与 Spec §4 中“保存点内 flush”的描述需更正；报告 §5.3 对异常位置和对象状态的推断也应按实际会话验证，不能由 `begin_nested()` 的代码结构推断外层会话仍可继续使用。准确描述是：查重竞争异常被转成受控拒绝，更新未提交，安全性目前依赖异常退出独立会话的整体回滚。

若仍保留“真实保存点内更新”的实现目标，应核查把对象修改放入嵌套事务之后的最小调整，并重新验证该竞争。本文不要求为当前独立会话增加恢复、重试或新事务抽象；但也不能把此方法目前的事务行为推广为共享业务会话中的可安全复用保证。

## 7. 对 Claude 报告 §5 的逐项回应

| 原审查点 | 判断 | 依据与限制 |
| --- | --- | --- |
| 1. A2 先查后锁 | 部分成立 | `_lock` 会刷新并锁定当前记录，已变为其他状态时拒绝；状态重新回到 NEEDS_REVIEW 时仍放行，见 AR1 |
| 2. A2 匹配条件 | 部分成立 | 完全相同的日期、姓名、手机会被拒绝；表示差异与信息缺失反例仍放行，见 AR2。全店查询本身偏保守，真实契约未覆盖 |
| 3. A1 唯一约束与异常对象 | 部分成立 | 当前独立会话异常退出不会提交；保存点未真正建立，见 AR4 |
| 4. 重置字段 | 当前合法状态路径成立 | [ApprovalPageService._reset_to_pending](../../src/homestay_bot/services/approval_page_service.py:406) 清空本轮确认元数据；`BookingService.confirm_and_create` 会重新赋审批人与时间。完整重新建单闭环仍受 AR3 影响 |
| 5. admin.js 占位符 | 已检查范围成立 | [fillConfirmPlaceholders](../../src/homestay_bot/static/admin.js:145) 与静态模板中的占位符用途一致；A1 确认框浏览器用例通过。没有穷举所有动态知识标题或用户正文中的同名文本 |
| 6. multipart 保留来源 | 成立 | [properties.py::_with_source](../../src/homestay_bot/routes/properties.py:178) 读取缓存表单；真实路由下凭证与欢迎图上传均返回带来源的详情地址。房态原有 return_to 分支在 [set_room_operational_status](../../src/homestay_bot/routes/properties.py:371) 仍优先保留 |
| 7. image_counts 范围与权限 | 成立 | [KnowledgeAdminService.image_counts](../../src/homestay_bot/routes/knowledge.py:419) 按条目聚合；[_render_index](../../src/homestay_bot/routes/knowledge.py:791) 只对管理员查询当前页前 50 项；普通员工无删除入口，配图数量测试通过 |

另有一处描述需澄清：[HostexClient.list_reservations](../../src/homestay_bot/integrations/hostex_client.py:399) 会自动读取完整分页，`ReservationQuery.limit` 是单页大小；A2 的 `len(candidates) >= 100` 是新增的保守拒绝阈值，不能解释为客户端只返回前 100 条导致结果截断。本文没有据此要求删除该阈值或放宽安全策略。

## 8. 已验证、作者证据与未覆盖范围

### 8.1 Codex 上一轮已执行且仍适用的验证

在仓库根目录执行，所有 Hostex 调用均为替身。

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_approval_review_actions.py \
  tests/integration/test_approval_routes.py \
  tests/integration/test_approval_flow.py \
  tests/unit/test_approval_page_service.py \
  tests/unit/test_approval_page_degradation.py \
  tests/integration/test_property_routes.py \
  tests/browser/test_admin_interactions.py::test_backfill_confirm_repeats_the_entered_reservation_code
```

结果：66 passed，1 条既有 Starlette/httpx 弃用警告，退出码 0。

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_knowledge_image_admin.py \
  tests/integration/test_knowledge_routes.py \
  tests/integration/test_approval_review_actions.py
```

结果：57 passed，1 条同类弃用警告，退出码 0。第二组包含已经跑过的 13 项审批回填测试，统计去重后为 **110 项独立用例通过**，不是 123 项独立用例。交接时不需要因写文档再次执行这两组。

上轮还执行了 E1–E4 反例（其中 E3 是 A1 查重竞争探针）及两次 multipart 来源探针。凭证与欢迎图上传各返回 303；详情地址保留 `active=inactive` 的列表来源，欢迎图地址同时保留 `#welcome-image` 锚点。认证与服务均为本地测试装配，没有写生产。

`git diff --check` 在审查与本文编写时通过。本文编写只检查相关代码、文档引用、内嵌 Python 语法及变更范围，不增加业务测试结果。

### 8.2 Claude 提供的证据

Claude 报告记载全量 2382 passed、15 skipped，临时 PostgreSQL 16、迁移至 0034，Ruff 与 mypy 通过。本文没有独立重跑该全量与 PostgreSQL 专项，也没有重新核查临时库已删除的现场。应标为作者提交的证据，不能转记为本轮 Codex 执行结果。

知识删除／上传的 PostgreSQL 并发用例已阅读；本次没有重跑，也没有据其五次并发结果宣称每种锁顺序都已稳定覆盖。A2 的 PostgreSQL 行锁交错与 A1 的 PostgreSQL 唯一键竞争仍没有本轮独立执行证据。

### 8.3 证据覆盖与未覆盖

- 已验证：上述离线测试、A2 旧反查与新建单交错、身份字段两个反例、真实 Session 门面的成功建单事务冲突、A1 独立会话回滚、两个 multipart 来源回跳。
- 未覆盖：真实百居易字段表示与外部订单契约、真实建单、真实 DeepSeek／企业微信、实际消息收件、生产页面与部署运行态；没有真实外部调用授权。
- E1 的重复调用是合成建单替身的记录；E4 的“上游成功”是替身返回成功。二者不代表真实订单或生产事故。
- 真实模型门禁未运行：本轮已核对的工作区改动没有触及 `scripts/release/reply_gate.sh::REPLY_PATHS`；该事实不替代数据库与审批调用链验证。

## 9. 可运行反例：E1–E4

以下单段命令按上一轮实际执行的探针整理，使用本轮新增测试文件中的合成 world。该整理版在本文编写时只做语法检查，没有重新执行；上一轮对应输出分别记录在 AR1–AR4。它是当前缺陷快照的复现入口，不是修复后的验收测试；代码改变后，原反例应被拒绝或得到正确结果，并将相关断言迁入已有测试。

在仓库根目录运行。只创建临时 SQLite 和合成数据，不读取 `.env`，registry 不连接真实客户端。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio
import importlib.util
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from homestay_bot.application import SessionApprovalPageService
from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import AuditLog, BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import (
    AvailabilityDay, CreateReservationResult, HostexTransportError, PropertyAvailability,
)
from homestay_bot.repositories.approvals import (
    SQLAlchemyApprovalRepository, SQLAlchemyPermissionChecker,
)
from homestay_bot.services.approval_page_service import ApprovalActionRefused, ApprovalPageService
from homestay_bot.services.booking_service import BookingService

spec = importlib.util.spec_from_file_location(
    "review_fixture", "tests/integration/test_approval_review_actions.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@asynccontextmanager
async def isolated_world():
    """每个反例使用独立合成 world，退出时释放临时数据库。"""
    generator = fixture.world.__wrapped__()
    try:
        yield await anext(generator)
    finally:
        await generator.aclose()


def page_service(session, sensitive, hostex):
    """复用真实页面服务，建单只在明确验证 BookingService 的地方装配。"""
    return ApprovalPageService(
        session=session, sensitive_data=sensitive,
        hostex=hostex, booking=fixture.BookingStub(),
    )


def command(property_id, amount):
    """构造合成管理员的明确收款确认命令。"""
    return ConfirmBookingCommand(
        property_id=property_id, final_rate_amount=amount, received_amount=amount,
        income_method_id=1, payment_confirmed=True,
    )


class SyntheticHostex:
    """纯内存上游：可模拟写入响应丢失，绝不发送真实请求。"""

    def __init__(self, uncertain=False):
        self.uncertain = uncertain
        self.created = []

    async def list_availabilities(self, ids, start, end):
        """提供全部住宿晚可用的确定性房态。"""
        days = []
        while start < end:
            days.append(AvailabilityDay(date=start, available=True, remarks=""))
            start += timedelta(days=1)
        return [PropertyAvailability(property_id=ids[0], days=days)]

    async def create_reservation(self, request):
        """记录合成写调用，并按反例需要返回成功或模拟响应丢失。"""
        self.created.append(request)
        if self.uncertain:
            raise HostexTransportError("synthetic order created but acknowledgement lost")
        return CreateReservationResult(request_id="SYNTHETIC-CREATE-OK")

    async def list_reservations(self, query):
        """成功场景返回唯一匹配订单；不确定场景模拟写后查询失败。"""
        if self.uncertain:
            raise HostexTransportError("synthetic read failure after creation")
        if not self.created:
            return []
        request = self.created[-1]
        return [fixture._reservation(
            "SYNTHETIC-BOOKED", name=request.guest_name, phone=request.mobile,
            property_id=request.property_id, check_in_date=request.check_in_date,
            check_out_date=request.check_out_date,
            created_at=datetime.now(UTC).isoformat(),
            rates={"rate_amount": request.rate_amount},
        )]


async def e1_stale_lookup():
    """真实状态机走完另一轮建单后，旧 A2 仍可回退并允许再次建单。"""
    async with isolated_world() as (factory, sensitive):
        hostex = SyntheticHostex(uncertain=True)

        async def book():
            """使用真实 SQLAlchemy 仓储和权限校验执行合成确认。"""
            async with factory() as session:
                service = BookingService(
                    SQLAlchemyApprovalRepository(session),
                    SQLAlchemyPermissionChecker(session), hostex, sensitive,
                )
                result = await service.confirm_and_create(1, 1, command(102, 499))
                print("E1 new booking:", result.status.value, result.property_id)

        class DelayedLookup(fixture.HostexStub):
            async def list_reservations(self, query):
                """控制另一个回退与重新建单完成后，才返回旧房间的查询结果。"""
                print("E1 stale lookup property:", query.property_id)
                async with factory() as other:
                    await page_service(other, sensitive, fixture.HostexStub()).reopen_after_review(1, 1)
                    await other.commit()
                await book()
                return []

        async with factory() as session:
            result = await page_service(session, sensitive, DelayedLookup()).reopen_after_review(1, 1)
            await session.commit()
            print("E1 stale A2 accepted:", result.status.value, result.property_id)
        await book()
        print("E1 create calls:", [item.property_id for item in hostex.created])


async def e2_identity_uncertainty():
    """手机号带区号或缺失的同名同日期订单，当前实现仍允许回退。"""
    for variant in ("country-prefix", "missing-phone"):
        async with isolated_world() as (factory, sensitive):
            async with factory() as session:
                approval = await session.get(BookingApproval, 1)
                guest = sensitive.read(approval)
                phone = "+86" + guest.guest_mobile if variant == "country-prefix" else None
                hostex = fixture.HostexStub([
                    fixture._reservation("SYNTHETIC-EXISTS", name=guest.guest_name, phone=phone)
                ])
                result = await page_service(session, sensitive, hostex).reopen_after_review(1, 1)
                await session.commit()
                print("E2", variant, "->", result.status.value)


async def e3_unique_collision():
    """在查重和写入之间登记相同订单号，观察保存点及独立会话回滚。"""
    async with isolated_world() as (factory, sensitive):
        class RaceSession(AsyncSession):
            raced = False

            async def scalar(self, statement, *args, **kwargs):
                """只在 A1 查重返回后插入竞争记录，其余数据库操作保持真实。"""
                result = await super().scalar(statement, *args, **kwargs)
                if not self.raced and "WHERE booking_approvals.hostex_reservation_code =" in str(statement):
                    self.raced = True
                    async with factory() as other:
                        approval = await other.get(BookingApproval, 2)
                        approval.status = ApprovalStatus.BOOKED
                        approval.hostex_reservation_code = "SYNTHETIC-RACE"
                        await other.commit()
                return result

        race_factory = async_sessionmaker(
            factory.kw["bind"], class_=RaceSession, expire_on_commit=False,
        )
        async with race_factory() as session:
            started = []
            event.listen(
                session.sync_session, "after_begin",
                lambda _session, transaction, _connection: started.append(transaction.nested),
            )
            try:
                await page_service(session, sensitive, fixture.HostexStub()).backfill_reservation(
                    1, 1, "SYNTHETIC-RACE"
                )
            except ApprovalActionRefused:
                print("E3 active:", session.is_active, "savepoints:", started.count(True))
        async with factory() as session:
            approval = await session.get(BookingApproval, 1)
            audits = list(await session.scalars(select(AuditLog)))
            print("E3 persisted:", approval.status.value, approval.hostex_reservation_code, len(audits))


async def e4_real_session_facade():
    """真实 Session 门面执行回退后建单，验证成功响应后的事务错误。"""
    async with isolated_world() as (factory, sensitive):
        hostex = SyntheticHostex()

        class Registry:
            @asynccontextmanager
            async def acquire(self):
                """仅提供合成外部客户端，保留真实门面的服务和会话装配。"""
                yield SimpleNamespace(hostex=hostex)

        service = SessionApprovalPageService(
            factory=factory, registry=Registry(), sensitive_data=sensitive,
        )
        await service.reopen_after_review(1, 1)
        try:
            await service.confirm(1, 1, command(101, 399))
        except Exception as error:
            print("E4 confirm:", type(error).__name__, str(error))
        async with factory() as session:
            approval = await session.get(BookingApproval, 1)
            print("E4 persisted:", approval.status.value, approval.hostex_reservation_code)
        print("E4 synthetic successful create calls:", len(hostex.created))


async def main():
    """依次执行独立反例，隔离数据并保证每个 world 释放。"""
    await e1_stale_lookup()
    await e2_identity_uncertainty()
    await e3_unique_collision()
    await e4_real_session_facade()


asyncio.run(main())
PY
```

## 10. 接手时的核查与交付

1. 核对 HEAD、工作区和本文引用的符号；保留现有改动。先审查 AR1–AR4，不将本文自动解释成实施授权。
2. 对 AR1、AR2 给出最小安全规则与对应 Spec 修订；AR3 单独确认既有事务根因及是否纳入本轮修复范围；AR4 更正实际保存点边界。
3. 对每项回复“成立／部分成立／不成立”，附当前代码依据和复现结果。若不同意某项，用当前真实调用链或反例说明；不能只引用旧的全量通过数字。
4. 按已确认的变更影响选择最小必要验证。已有结果仍覆盖最终输入时复用；事务所有权变化需真实会话与仓储验证，并按实际生产数据库风险补必要竞争用例，不机械重复全量。若最终修改触及装配或无法界定影响，单独判断项目规定的提交前全量条件。
5. 新交接记录最终 diff、受影响 Spec、验证命令与结果、未覆盖项，并明确“本地修复与验证完成，可以交 Codex 审查”或仍在实施。提交、推送、部署和真实外部结果仍需分别取得当前授权。

本文没有修改 Claude 的实施报告、Spec、README 索引、任务记录或业务源码，也没有把报告发送到其他线程或外部服务。
