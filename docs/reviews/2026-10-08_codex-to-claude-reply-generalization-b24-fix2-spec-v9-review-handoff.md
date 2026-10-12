# Codex → Claude：fix2 Spec v9 审查报告

- 日期：2026-10-08。审查者：Codex。
- 审查对象：[Claude v9 交接报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v9-review-handoff.md)与[当前 Spec v9](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)，两份均完整审阅。
- 基线：`main`，HEAD `80f9b2c309547c36c15cfd142d5dd30b98d87406`；当前工作区尚未实施 fix2。
- 范围：只读追踪源码、核对前轮闭合、运行内存合成数据库探针，撰写本报告。没有修改业务代码或 Spec，没有调用真实模型、Hostex、企业微信，没有连接现有数据库或生产，也没有提交、推送或部署。

## 1. 结论与前轮闭合

**v9 的总体方向可保留，但仍需补齐 1 项 P1、1 项 P2。** 都是既定清空与人工事务边界的实现契约缺口，不需要增加业务功能或重新选择 D-G1–D-G20。

| v8 发现 | v9 审查结论 | 依据 |
| --- | --- | --- |
| V8-R1：合并阶段直接终结没有收据 | 设计已闭合；实施验证仍待执行 | Spec §2.6(2) 明确两种终结、两入口读取，以及“排出最终作业”不写。结果由 `process_debounced_message` → `_dispatch_deferred_message` → 业务提交处传递，能覆盖前轮客诉重放反例 |
| V8-R2：超时耗尽无人回写草稿失败 | 设计已闭合；实施验证仍待执行 | Spec §2.6(3) 分别绑定 `on_job_failed` 与 `recover_stale` 清载荷前的同事务条件更新；保留状态和代次校验。当前 `Worker.run_once` 在 `mark_failed` 后、checkpoint 前调用失败回调，可与队列终态一起提交 |
| V8-R3：普通卡片仍在动作后读库 | 修订方向正确，调用链尚未完全闭合 | Spec §2.6(2) 已统一先取快照、后纯显示；但生产取数内部仍吞 CRM 数据库异常，见 V9-R2 |
| 收据外键、状态不变量、入库勾选 | 设计已闭合 | Spec §2.6(5)(7) 与不变量分别明确外键级联、状态＋代次、服务端勾选校验 |

这里的“设计已闭合”不表示功能已实现，也不表示 PostgreSQL 或真实收件验收通过。

## 2. V9-R1（P1）：模型等待期间清空，旧结果仍可能创建新的转交

**缺口：入口检查与清理已有记录，不能拦住清空之后才返回的主模型或软规划结果。**

### 依据与可执行反例

1. Spec [§2.6(2)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:368) 把创建转交放在模型返回后的过时与原生接管复核之后；[第 382 行](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:382)只明确“恢复入口”和“客人发送处”检查来源清空。[§2.6(6)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:445)清掉已存在转交，覆盖尚未执行的旧最终作业与在途草稿，没有绑定正在等待的主回复／规划结果。
2. 当前 [`ConversationService._process_model_reply_body`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1572)等待助手；返回后在第 1632 行调用 [`_discard_stale_final`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1829)。该守卫只取得会话活动锁、检查是否有更新活动。
3. [`SQLAlchemyMessageRepository.has_newer_conversation_activity`](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/conversations.py:427)检查后续非机器人消息的本地主键；[`clear_test_customer_data`](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/customers.py:1521)保留来源行和编号，置空正文并标记 `cleared_by`，随后把会话恢复为 BOT_ACTIVE。清空不增加客人／员工活动。
4. 同样，[`_plan_with_released_lock`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1348)等待规划后，只检查模式变化、原生接管与新活动；原先就是 BOT_ACTIVE 时，清空后模式仍相同。

本轮 E9-clear 用真实 `clear_test_customer_data` 提交清空，再以模型等待前冻结的 `IncomingMessage` 调用真实 `_discard_stale_final`：

```text
E9-clear cleared=True buffered_text=True stale_discarded=False mode=bot_active
E9-clear source_check_stops=True
```

所以存在明确的顺序：旧来源进入模型 → 管理员完成清空 → 旧模型返回 → 当前过时守卫放行 → 按 Spec 新建转交和卡片任务。清空时没有这条转交可清理；卡片发送只检查新记录是否待处理，也会放行。客人发送处拦截只能拦客人回复，不能撤销重新落库的旧正文或员工卡片。

这证明当前守卫不足，以及 v9 契约存在允许该顺序的缺口；**没有声称未实施的 v9 已经实际写入转交，也没有把 SQLite 当作 PostgreSQL 并发证据。**

### 最小修订与牵连控制

- 把“来源已清空则终结本轮”的判据绑定到**每次模型／规划等待结束后的统一复核处**：主回复成功及两个异常出口复用 `_discard_stale_final`；软规划返回处采用同一判据，同时保留其现有模式／原生接管校验。不要只在“创建转交”前补一处，这会留下风险、任务、通知等兄弟出口。
- 来源判断读数据库当前列值：正文为空且 metadata 有 `cleared_by`；不能看旧 `IncomingMessage`，也不能直接信任会话 identity map 内缓存的旧 Message。可复用发送处所需的来源查询，避免建立第二套失效状态或新增代次。
- 复核通过后仍持有现有会话活动锁至业务提交；清空事务也取得同一批会话锁，**按会话编号升序、在更新 Message 和转交之前取得**。这样处理先提交时，清空能清掉新记录；清空先提交时，处理能看见清空标记。
- 不在“已经更新 Message 后”再追加会话锁：处理侧是先会话、后消息／转交，反向顺序可能引入死锁。保留现有客户锁、测试标签核验、消息编号去重和清空原子性；核对实际 SQL 与清空／接管调用方的锁顺序。无需取消所有队列作业或新增巡检服务。

### 最小验收

用暂停助手／规划器替身和真实业务仓储，至少覆盖：

- 主回复等待中先清空提交，再恢复成功结果；以及主回复失败出口、软规划返回出口。本轮新建转交、员工卡片、业务任务／客诉派生和客人最终出站均为 0，清空页面不恢复旧正文；清空前已存在且按规则保留的任务不计入新增数。
- PostgreSQL 两种锁顺序：处理先锁先提交，随后清空；清空先锁先提交，随后处理。最终均无旧派生正文，且不发生锁顺序循环。新来源在清空后仍正常处理。
- 只移除“模型返回后的来源清空检查”，反例必须失败；避免仅验证“清空后旧作业首次执行”的现有入口检查。

## 3. V9-R2（P2）：卡片取数内部吞 SQL 异常，前移仍会调用平台接入

**缺口：将 `card_for` 移到人工动作前，不能保证其内部数据库异常会传播。**

### 依据与本轮验证

- Spec [第 395 行](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:395)要求普通卡片复用 `_handoff_card_for`，取数的数据库错误在平台接入前抛出。
- [`application._handoff_card_for`](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:3717)第 3740 行调用共用 [`resolve_notification_identity`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:174)，注入同一个业务 Session 的 `SQLAlchemyCustomerRepository`。
- 身份函数第 199 行读取 CRM 备注，却在第 202 行用 `except Exception` 吞掉所有错误并退回普通姓名。[仓储 `get_customer_notification_note`](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/customers.py:618)包含实际 SQL 读取。
- [`HumanSessionService._accept`](/Volumes/02/code/homestay-bot/src/homestay_bot/services/human_session.py:127)先调用平台接入，第 152 行才写本地接管。数据库事务损坏时，会留下平台已接入而本地后续失败的风险；移动外层调用位置并未消除这个顺序。

E9-card 用真实生产卡片取数与真实 `handle_card_action`，只把顺序按 v9 前移，并在 CRM 备注方法边界注入 `OperationalError`：

```text
E9 strict=False database_error=True: stopped=False transfers=1
E9 strict=True database_error=True: stopped=True transfers=0
E9 strict=True database_error=False: stopped=False transfers=1
```

第二、三行是在**进程内**为真实身份函数增加 SQL 异常重抛的对照，业务文件没有修改。探针证明 SQL 异常被吞、平台替身仍被调用；不证明真实 PostgreSQL 事务已被打坏，也不涉及真实企业微信。

### 最小修订与验收

- 在共用 `resolve_notification_identity` 的 CRM 备注 try 内，先 `except SQLAlchemyError: raise`，再保留非数据库错误的现有兜底；同步修改“查询失败不阻塞”的注释，明确仅允许可安全降级的非 SQL 故障。
- 不在身份 helper 内 rollback 共享业务 Session；不要靠 `Session.is_active` 猜事务健康，也不增加 strict 参数或复制专用卡片取名函数。
- 已核对三处生产调用：普通卡片 `application._handoff_card_for`、自动交还通知 `application._notify_conversation_release`、任务／接管通知 `ConversationService._notify_employee`。三者的 CRM 查询均使用事务仓储，SQL 失败应交给外层回滚；企业微信名称读取的独立兜底 `load_notification_names` 保留。
- Spec 文件计划应明确包含此符号；“取数失败”验收在**嵌套 CRM 数据库读取处**注入故障，不能只替换整个 `card_for` 为抛错桩，否则无法捕获本缺陷。
- 复用普通卡片接入／交还行为测试；SQL 读取失败时平台调用 0；非 SQL 名称故障仍安全兜底；动作后的纯组卡或更新失败不撤销已完成动作。无需扩大为全仓测试来代替这些断言。

## 4. Claude 提出的重点与泛用性判断

1. **更新活动导致旧来源放弃，也写收据：接受。** 当前活动判据按来源后的非机器人 Message.id 判定，清空保留消息行；没有将旧来源重新激活的正常续接路径。更新活动可能来自员工，因此表述宜为“已有更新活动，旧来源不再处理”，不必承诺一定由新客人作业接手。这是说明澄清。
2. **合并返回两种结果：接受。** `application._dispatch_deferred_message` 是唯一生产调用方；全部直接调用的测试与各早返回分支应一起适配，快速回执排出最终任务时不得提前写终结收据。
3. **卡片快照前移的原因显示差异：接受。** `latest_handoff_reason` 与 `accept_handoff` 的原因沿用关系支持交接报告的判断。没有历史审计时显示兜底人工请求说明，属于展示差异；不影响权限、平台状态或交还规则。取数前移的 SQL 异常问题另见 V9-R2。
4. **仍是“选择题”的正确方向。** v9 以供应的单元编号、候选、事实范围和本地门禁约束模型选择；没有为本轮反例追加客人措辞词表。剩余两项是事务与来源失效边界，不能靠提示词解决。
5. **保留已确认天花板。** 问法归一化可能漏同义冲突、D-G8 的特殊时期保守误拦、凭据识别的格式上限、草稿引用不能证明语义，都已明示并有人工复核；本轮不据此要求实体系统、向量去重或新状态机。

为减少继续往返，建议把两项修订放进同一份 Spec：共用“等待后来源仍有效”和“显示取数的 SQL 错误必须传播”两个已有边界，再用上面的反例及正常对照验证。**不需要重新讨论通知形式、草稿条数或入库权限。**

## 5. 验证、证据绑定与工作区归属

- 本轮两个离线脚本各退出 0：E9-clear 调用真实清空和模型返回守卫；E9-card 调用真实卡片取数和人工动作，以平台替身计数并作进程内最小对照。退出 0 证明反例／对照成立，**不表示 v9 实施通过**。完整脚本见附录。
- 文档静态核查通过：15 个文件链接及行号有效、附录两份脚本与实际执行文件一致且可解析、8 项证据摘要一致、无行尾空白；`git diff --check` 通过。此项只验证报告与证据绑定，不验证业务实现。
- 前轮 E8 证据对应的源码摘要与本轮一致，复用其合并终结和第三次取消反例；未重复运行无变化的相关测试或全量。Claude 声明重跑的结果不冒记为本轮 Codex 执行结果。
- 未验证：v9 转交表与迁移、真实 Worker 的新版收据／草稿恢复、PostgreSQL 竞争及故障事务、后台浏览器和真实外部收件。它们是实施后的验收范围。
- 本轮只新增本报告；临时脚本在 `/tmp`，不入业务代码。AOCI 收尾资产与报告分开记账。独立占位符、INFO 日志、Bearer 脱敏代码与测试以及已有全部报告保持原状；本报告没有完整审查该独立任务。
- 不得据本报告执行编码、提交、推送、部署、真实模型门禁或生产写入；修订后先按项目 Spec 确认流程办理。

| 文件 | SHA-256（本轮核对） |
| --- | --- |
| Spec v9 | `d62ff0612939db9e094a8b39d50d1de03fed0aeae616730b37ba0f4465a64936` |
| Claude v9 交接报告 | `875556cb59f9ff7e0154228f4dc241750a42b51dd706f22f30c9c812b74671cd` |
| Codex v8 报告 | `fc6b49d36ffb483234add086844396b923a0a95f693626b4176e40fc406a48fb` |
| `application.py` | `fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac` |
| `services/conversation_service.py` | `1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878` |
| `services/human_session.py` | `b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60` |
| `repositories/conversations.py` | `e5c6dee2b19df73b561a539506e5c3c58583f1df01c46793a46467c8a0cbfef4` |
| `repositories/customers.py` | `6748b2cabe6838d85666015d44b449e975c55e520c1cc98a956d2a16bd896b21` |

## 附录：最小复核脚本

以下脚本只访问各自新建的内存 SQLite，不加载应用生命周期或现有配置，不产生真实外部调用。复制到指定 `/tmp` 文件后，在本仓库运行：

```sh
PYTHONPATH=src .venv/bin/python /tmp/codex-b24-fix2-v9-clear-probe.py
PYTHONPATH=src .venv/bin/python /tmp/codex-b24-fix2-v9-probe.py
```

### A. E9-clear：来源清空与模型返回守卫

```python
"""用真实清空与过时检查验证在途来源失效；仅使用内存合成数据库。"""
import asyncio
from datetime import UTC, datetime
from sqlalchemy import select
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import EmployeeRole, MessageOrigin
from homestay_bot.domain.models import Base, Customer, CustomerTag, CustomerTagLink, Employee, Conversation, Message
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository, TEST_ACCOUNT_TAG
from homestay_bot.repositories.conversations import SQLAlchemyConversationRepository, SQLAlchemyMessageRepository
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage, MessageService

async def main():
    """冻结模型输入后实际清空，再调用当前模型返回守卫；不伪造转交实现。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory() as session:
            customer = Customer(display_name="合成测试客户")
            tag = CustomerTag(name=TEST_ACCOUNT_TAG)
            employee = Employee(name="合成管理员", wecom_userid="synthetic-admin", role=EmployeeRole.ADMIN)
            session.add_all([customer, tag, employee])
            await session.flush()
            session.add(CustomerTagLink(customer_id=customer.id, tag_id=tag.id))
            conversation = Conversation(open_kfid="synthetic-kf", external_userid="synthetic-guest", customer_id=customer.id)
            session.add(conversation)
            await session.flush()
            incoming = IncomingMessage(msgid="synthetic-source", open_kfid=conversation.open_kfid,
                external_userid=conversation.external_userid, origin=MessageOrigin.GUEST,
                msgtype="text", content="这个房间能否借用小型投影设备？", sent_at=datetime.now(UTC))
            await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(conversation.id, incoming)
            customer_id, employee_id, conversation_id = customer.id, employee.id, conversation.id
            await session.commit()
        # 相当于模型已持有 incoming，清空在等待期间先完成提交。
        async with factory() as session:
            await SQLAlchemyCustomerRepository(session).clear_test_customer_data(customer_id, employee_id)
            await session.commit()
        async with factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            service = ConversationService(conversations=SQLAlchemyConversationRepository(session),
                messages=MessageService(SQLAlchemyMessageRepository(session)), assistant=object(),
                emergency_service=EmergencyService(), wecom=object(), agent_id=17, duty_employee_userids=[])
            discarded = await service._discard_stale_final(conversation, incoming)
            source = await session.scalar(select(Message).where(Message.external_message_id == incoming.msgid))
            cleared = source.content is None and bool(source.message_metadata.get("cleared_by"))
            assert cleared and incoming.content and discarded is False
            print(f"E9-clear cleared={cleared} buffered_text={bool(incoming.content)} stale_discarded={discarded} mode={conversation.mode.value}")
            # 最小合同对照：在同一模型返回检查点增加新鲜来源清空判定。
            stopped_with_source_check = discarded or cleared
            assert stopped_with_source_check
            print(f"E9-clear source_check_stops={stopped_with_source_check}")
    finally:
        await engine.dispose()

asyncio.run(main())
```

### B. E9-card：嵌套 CRM SQL 故障与接入前取数

```python
"""离线核对 v9 取数前移合同；数据库与平台只使用合成数据。"""
import asyncio
import inspect
from unittest.mock import patch
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from homestay_bot import application
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import Base, Conversation, Customer
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.services.conversation_service import resolve_notification_identity
from homestay_bot.services.human_session import HumanSessionService

class Platform:
    """只计数接入调用；不会访问企业微信。"""
    def __init__(self):
        self.transfers = 0

    async def get_service_state(self, *_args):
        """提供可以接入的合成平台状态。"""
        return 1, ""

    async def transfer_service_state(self, *_args):
        """记录一次合成接入，不产生真实外部操作。"""
        self.transfers += 1
        return ""

def strict_identity_proxy():
    """仅在进程里为真实函数加 SQL 异常重抛，用作最小修订对照。"""
    source = inspect.getsource(resolve_notification_identity)
    assert source.count("except Exception as error:") == 1
    source = source.replace(
        "except Exception as error:",
        "except SQLAlchemyError:\n            raise\n        except Exception as error:",
        1,
    )
    namespace = dict(resolve_notification_identity.__globals__, SQLAlchemyError=SQLAlchemyError)
    exec(source, namespace)
    return namespace["resolve_notification_identity"]

async def run_case(strict, database_error):
    """先走真实生产卡片取数，再走真实授权按钮动作，模拟 v9 的前移顺序。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory() as session:
            customer = Customer(display_name="合成客户")
            session.add(customer)
            await session.flush()
            conversation = Conversation(open_kfid="synthetic-kf", external_userid="synthetic-guest",
                                        customer_id=customer.id)
            session.add(conversation)
            await session.commit()
            api = Platform()
            async def failing_note(_repository, _customer_id):
                """在实际 CRM 备注读取边界注入故障，不伪造事务回滚结果。"""
                if database_error:
                    raise OperationalError("synthetic select", {}, RuntimeError("synthetic DB fault"))
                raise ValueError("synthetic non-SQL lookup fault")
            resolver = strict_identity_proxy() if strict else resolve_notification_identity
            stopped = False
            with patch.object(SQLAlchemyCustomerRepository, "get_customer_notification_note", failing_note), \
                 patch.object(application, "resolve_notification_identity", resolver):
                try:
                    card = await application._handoff_card_for(
                        session, conversation, identity_resolver=None,
                        public_base_url="https://example.invalid",
                    )
                    assert card.conversation_id == conversation.id
                    await HumanSessionService(
                        session, api, agent_id=17, duty_userids=("synthetic-duty",)
                    ).handle_card_action({
                        "userid": "synthetic-duty", "agent_id": "17",
                        "key": f"accept:{conversation.id}",
                    })
                    await session.commit()
                except SQLAlchemyError:
                    stopped = True
                    await session.rollback()
            result = stopped, api.transfers
            print(f"E9 strict={strict} database_error={database_error}: stopped={stopped} transfers={api.transfers}")
            return result
    finally:
        await engine.dispose()

async def main():
    """原实现暴露合同缺口；对照证明 SQL 重抛不破坏普通名称兜底。"""
    assert await run_case(False, True) == (False, 1)
    assert await run_case(True, True) == (True, 0)
    assert await run_case(True, False) == (False, 1)

asyncio.run(main())

```
