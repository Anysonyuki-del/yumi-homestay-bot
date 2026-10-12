# Codex → Claude：fix2 Spec v8 深度复审交接

- 日期：2026-10-08。审查基线：main `80f9b2c309547c36c15cfd142d5dd30b98d87406`，含现有未提交工作区。
- 已审查：Claude [v8 交接报告](2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v8-review-handoff.md)和 [fix2 Spec v8](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)，并对照 [Codex v7 报告](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v7-review-handoff.md)与当前真实调用链。
- 结论：**仍需补齐 1 项 P1、2 项 P2 工程边界；建议把三处一起补进本 Spec，再进入确认流程。** 保留已确认业务决策与已声明的识别天花板，无需新增表、通用恢复框架或重做知识检索方案。
- 本轮只写审查报告和临时离线探针；未实现业务方案，未提交、推送、部署，未调用真实模型、Hostex、企业微信，未连接既有业务数据库或生产。

## 1. 上一轮意见闭合情况

| v7 意见 | v8 复核结论 |
| --- | --- |
| V7-R1 最终处理重放 | final 作业的收据方案成立；但同一来源可以在 debounce 阶段直接完成客诉，仍漏在收据边界之外。见 V8-R1。 |
| V7-R2 英文凭据 | 合同已闭合：补英文标签、共用检测与净化，并承认客诉输入行为增强。是否误伤需实施后的正反行为样本证明，当前不能当成实现通过。 |
| V7-R3 卡片失败隔离 | referral 分支明确动作前取数，方向成立；普通卡片仍调用带数据库读取的现有 `card_for`，合同未说明如何同时满足“显示不读库”与“普通行为不变”。见 V8-R3。 |
| V7-R4 同代次覆盖 | 成功回写与采用资格已闭合：整理中 + 代次 CAS、已整理 + 代次采用。新增由 Worker 统一重试后，终态失败尤其 stale 恢复的落点仍缺。见 V8-R2。 |
| V7-R5 来源窗口 | 合同已闭合：转交创建时间起算、秒级对齐、点击冻结编号、截掉的输入不得引用。 |
| C1 引用与答案 | 已如实区分引用存在与语义正确，补非空引用和双语/范围人工审核。保留管理员审核天花板。 |
| C2、C3 | 三张表、1.70.0、D-G14 文本与占位符复用已更正。独立 INFO/Bearer 任务仍不属于本报告的完整审查范围。 |

## 2. 需要修订的三处

### V8-R1（P1）：收据按 phase=final 写入，漏掉 debounce 已经完成的客诉终结

**依据**

- Spec §2.6(2) 第 357 行明确只有 `deferred_phase` 为最终回复时写收据，第 358 行也只终止最终处理；§2.4 第 299 行却已列出“投诉在合并阶段业务提交、队列未完成”的真实中断窗口。
- [application.py](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:869) 的 `_dispatch_deferred_message` 将 debounce 直接交给 `process_debounced_message`，不经过 `record_incoming`。
- [conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:975) 的 `process_debounced_message` 会重新规划情绪词；规划失败时调用 `_enter_complaint_mode` 后直接返回。该函数在 [1120 行](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1120) 已登记固定客诉回复、员工通知、客诉记录和分析作业；不会再生成 final 作业。
- 同函数在 [1025 行](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1025) 允许下一次规划成功、去除情绪判定后进入 `_stage_fast_ack`，创建 `final:{msgid}`。
- 本轮 E8-1 使用真实会话服务、真实仓储和真实事务发件箱：同一正文“第一次来太开心了!!!”，第一次规划返回失败并提交，客诉记录 1、final 作业 0；同一来源重放时返回经 `verify_turn_plan` 核验的正常计划，客诉记录仍 1，却新增 final 作业 1。人工模式没有 `accepted_by` 的原生接入，现有原生接管护栏不会拦这条路径。

**后果**

这是已提交处理被重放改换路线，而不只是转交编号变化。首次已经完成客诉处理，再次会进入正常主回复；目前探针证明多出 final 作业，未执行该作业或证明实际第二次收件。v8 的 final-only 收据不会改变此反例，因为第一次根本没有 final 作业可写收据。§2.4 中“恢复后 final 作业 0”的要求因此不能只用两次相同规划结果来证明。

**最小修订**

1. 继续复用同一张来源完成收据，按“本来源业务已终结”判断，而不是仅按作业名称判断。
2. 后台 debounce 直接完成客诉、紧急、固定边界回复等终结分支，也与这些副作用在同一业务事务写收据；只登记后续 final 的中间阶段不写。
3. debounce 与 final 恢复入口都在重新规划或做副作用前查同一收据。不要在 `_stage_fast_ack` 的中间提交上把正常 final 提前挡掉。
4. 在服务返回结果或现有编排边界明确区分“本轮已终结”和“已排后续 final”；不需要新增状态机框架。
5. 把“首次提交前什么都没落库”改成“最终业务副作用与完成收据尚未提交”；入站消息、快速回执或规划前提交可能已经存在。

**验收**

复用 §2.4 的真实 Worker 窗口二：第一次规划失败并完成客诉；取消点放在业务提交后、来源完成前；真实 recover → claim 后让助手若被调用就返回正常计划。恢复必须不再调用规划器、final 作业仍 0，客诉记录、通知、分析作业、客诉回复各仍 1。另保留正常 debounce 排 final 的对照，证明收据没有把正常最终回复挡掉。无需为此增加外部调用。

### V8-R2（P2）：第三次领取后中断，stale 恢复不会执行草稿失败写回

**依据**

- Spec §2.6(3) 第 396–397 行要求重试耗尽转“草稿失败”，由 Worker 最多重试三次；第 388 行不允许从“整理中”重新点击整理。因此没有终态写回会使该记录一直无法继续整理。
- [worker.py](/Volumes/02/code/homestay-bot/src/homestay_bot/worker.py:386) 的 `Worker.run_once` 只把 `job.payload` 传给 handler；普通异常在 `mark_failed` 后调用既有 `on_job_failed`。取消不会进入 `except Exception`。
- [jobs.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py:209) 的 `recover_stale` 对达到三次的 RUNNING 作业直接改 FAILED、清空 payload；它不会调用 Worker 的失败回调，也不再执行 handler。
- [application.py](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:3102) 的 `_run_worker_loop` 在创建 Worker 前执行这次恢复。现有 [3281 行](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:3281) 的失败回调只处理投递补偿，不能当作新草稿状态已获补偿的证据。
- E8-2 使用真实 Worker、真实 Job 仓储：合成草稿任务从 attempts=2 被第三次领取，checkpoint 后取消；恢复得到 FAILED、payload={}、失败回调次数 0。

**后果与证据边界**

如果只按 v8 写 handler 内的失败 CAS 与普通异常回调，第三次中断这一条路径没有代码能把“整理中”改成“草稿失败”。新转交表尚未实现，E8-2 证明的是现有队列路径与回调缺口，不能表述为新版转交记录已经卡死的运行复现。

**最小修订**

- 普通异常耗尽：在现有 `on_job_failed` 装配里增加新草稿类型的终态分支，只有 job 已 FAILED 才以 payload 中的编号和代次做“整理中 + 当前代次”条件更新；前两次失败仍保留整理中。
- stale 达到上限：复用 `recover_stale` 已有“清载荷前补偿”的位置，在清空新草稿作业 payload 前，同事务完成同一失败条件更新。不要新增巡检服务、另造重试计数或笼统允许整理中强制重开。
- 已整理、已入库、已关闭、已清空和已换代必须都不被该失败补偿改动。数据库失败正常传播并回滚，不能吞掉。
- 明确 SQL/事务异常的回滚归属，再进入队列失败登记，避免在已经失败的 Session 上尝试补偿。

**验收**

真实 Worker 普通异常 1、2 次仍可重试，第 3 次进入失败；第三次领取后取消再 recover 也进入失败。每条路径都检查转交状态、代次与 Job 状态；第三次业务结果已经成功提交后再取消时，恢复不得把“已整理”改成失败；关闭、清空、换代各一个条件更新对照即可。

### V8-R3（P2）：普通卡片的数据库取数仍在人工动作之后，显示隔离合同只落实了 referral

**依据**

- Spec §2.6(2) 第 374 行只明确 referral 在动作前读最小字段；第 376 行要求显示阶段不读数据库，第 377 行又要求普通卡片行为不变。
- [human_session.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/human_session.py:102) 的 `handle_card_action` 先 `_accept/_release`，再调用 `card_for`。
- 生产装配 [application.py](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4769) 的 lambda 仍接入 `_handoff_card_for`；该函数在 [3728 行](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:3728) 读取住宿、接手简报、最新接管原因及客户身份。仅新增 `task_id` 参数或把这整个回调搬进 broad except，不会消除显示阶段的数据库事务错误。
- v7 E7-2 的源码绑定未变化，其“平台替身已经接入、本地模式与审计回滚”证据继续有效；本轮不重复跑它。当前 ordinary 生产 loader 的读库职责由上述源码再次确认。

**最小修订**

在同一个公共按钮入口明确两阶段：动作前完成普通卡片与 referral 各自的数据库取数，复用已有 `HandoffCard` 作为快照；动作后只做纯组卡与平台显示更新。普通卡片保持现有字段和接入/交还语义，referral 继续最小文本。取数的真实数据库错误在外部接入前传播；纯组卡和显示失败才隔离，不用“吞数据库错误”来保人工状态。已有普通 loader 一起迁移，不能只写 referral 分支。

**验收**

普通与 referral 各覆盖：动作前数据库取数失败 → 平台接入调用 0；纯组卡或 update 失败 → 人工动作与本地审计仍提交一次。更新生产 lambda 和全部测试调用方，保留普通接入、交还、他人已接入三个行为对照。

## 3. Claude 五个重点问题的答复

1. **入口与即时路径**：当前生产后台经 `_dispatch_deferred_message` 调用两个已记录入口；即时 `handle_message` 在活动锁内先 `record_incoming`，已提交来源重放会提前返回，这部分自查成立。漏的是 debounce 本身可以作为终结业务阶段，见 R1；不是另一个未发现的即时入口。
2. **首次提交正文即最终正文**：接受此幂等语义。当前平台拦截后的事实等价改写走独立 `GuestDeliveryRewriteJobService.handle`，不是重跑最终模型阶段；收据检查不应加到独立出站补偿/改写入口。此次未发现依赖重跑整个来源来纠正正文的明确契约。
3. **英文凭据**：接受有限格式方案，但仅列标签不能证明正则足够窄。实施时补“Wi-Fi password?”、“How do I get the Wi-Fi password?”等无值提问，与冒号/等号/空格凭据、中英文、大小写的正反对照；同一组样本走共用函数和实际客诉输入，证明普通问法保留、凭据值不出现在卡片/模型输入。无需新建第二套隐私规则。
4. **同秒窗口**：接受计入并提示。平台只有秒级事实，不能可靠断言同秒内先后；排除同秒也会丢掉真正后发的回复。保持这一天花板，按固定时间样本验证边界即可。
5. **冻结编号与清空**：合同一致。清空把记录终态化并递增代次，读取只用冻结编号并复核正文，迟到回写又按整理中 + 原代次 CAS；实际实现需把这些检查接到同一路径。不要为了重试复制旧正文到 Job payload。

两处机械补齐可随以上修订完成，不新增业务取舍：

- §2.6(7) 的收据字段尚未列出外键，而 §2.6(2) 声称随会话删除级联。明确来源编号引用 `messages.external_message_id`，`ON DELETE CASCADE`；当前 [models.py](/Volumes/02/code/homestay-bot/src/homestay_bot/domain/models.py:478) 的消息已有唯一外部编号并随会话删除级联，无需再加第二个会话字段。
- 五条不变量第 4 条同步改成“整理中 + 当前代次”。采用前“已核对中英答案与适用范围”要由服务端验证，不能只禁用前端按钮；验收含未勾选的直接 POST。

## 4. 验证范围、结果与复核入口

- v8 两份文档均已读并互相对照，复核上一轮五项与三个补充项；真实代码重点追踪后台入口、业务提交、普通/资料卡片、Worker 异常及 stale 恢复、清空与消息来源。
- E8-1、E8-2 在新建内存 SQLite 上运行，助手/平台无真实请求。最终退出 0，输出如下。退出 0 表示反例断言成立，**不表示 v8 修复通过**。
- 初次探针有构造参数错误；修正为真实规划 JSON 和明确日期提供者。恢复探针读取状态使用列投影，避免将 SQLite 无时区时间载入 ORM 后触发本地同步比较问题；恢复函数本身未被替换。
- 未运行全量 pytest、PostgreSQL、浏览器、真实模型门禁或真实收发。没有实现变动，业务全量不适用于本次文档审查。实施后仍按 Spec 与 AGENTS 判断相应门禁。
- 文档静态核查覆盖空白、链接/行号、引用文件摘要与嵌入探针的一致性。

```text
E8-1 debounce source replay: planner-failed complaints= 1 new-final-jobs= 0
E8-1 debounce source replay: planner-ok complaints= 1 new-final-jobs= 1
E8-2 third-attempt cancellation: failed payload= {} failure-callbacks= 0
```

复核方式：取下面 Python 代码保存到临时文件，在仓库根目录执行 `PYTHONPATH=src .venv/bin/python /tmp/codex-b24-fix2-v8-probe.py`。不读取应用配置，不打开既有数据库；创建的引擎仅为 `sqlite+aiosqlite:///:memory:`。

```python
import asyncio
import json
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, func
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.domain.models import Base, Job, ComplaintReview
from homestay_bot.domain.enums import MessageOrigin, Language, JobStatus
from homestay_bot.repositories.conversations import SQLAlchemyConversationRepository, SQLAlchemyMessageRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.turn_plan import failed_plan, verify_turn_plan
from homestay_bot.worker import Worker

async def debounce_probe(factory):
    """真实会话服务重放同一来源，助手用失败与成功两个本地返回作对照。"""
    text = "第一次来太开心了!!!"
    message = IncomingMessage(msgid="synthetic-v8-debounce", open_kfid="probe-kf",
        external_userid="probe-guest", origin=MessageOrigin.GUEST, msgtype="text",
        content=text, sent_at=datetime.now(UTC))
    async with factory() as session:
        conv = await SQLAlchemyConversationRepository(session).get_or_create(message)
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(conv.id, message)
        await session.commit()
    class Assistant:
        def __init__(self, fail): self.fail = fail
        async def plan_turn(self, *, text, language):
            if self.fail: return failed_plan(text, "synthetic-timeout")
            outcome = verify_turn_plan(json.dumps({"items":[{"id":1, "kind":"chitchat", "quote":text,
                "start":0, "question":text, "subject":"开心", "risk":"none"} ]}, ensure_ascii=False), text=text, today_provider=lambda: datetime.now(UTC).date())
            assert outcome.ok, outcome.reason
            return outcome
        async def respond(self, **kwargs):
            raise AssertionError("合并阶段不调用主回复")
    for fail in (True, False):
        async with factory() as session:
            service = ConversationService(
                conversations=SQLAlchemyConversationRepository(session),
                messages=MessageService(SQLAlchemyMessageRepository(session)),
                assistant=Assistant(fail), emergency_service=EmergencyService(),
                wecom=TransactionalOutboxWeCom(session, source_message_id=message.msgid,
                    source_guest_message_id=message.msgid, delivery_phase="debounce"),
                agent_id=1, duty_employee_userids=["probe-duty"],
                audit_events=SQLAlchemyOperationsRepository(session),
                jobs=SQLAlchemyJobRepository(session), complaint_service=ComplaintService(),
                complaint_reviews=SQLAlchemyComplaintRepository(session),
                commit_boundary=session.commit)
            await service.process_debounced_message(message)
            await session.commit()
        async with factory() as check:
            final = await check.scalar(select(func.count()).select_from(Job).where(
                Job.dedupe_key == "final:" + message.msgid))
            complaints = await check.scalar(select(func.count()).select_from(ComplaintReview))
            if fail: assert final == 0 and complaints == 1
            else: assert final == 1 and complaints == 1
            print("E8-1 debounce source replay:", "planner-failed" if fail else "planner-ok",
                  "complaints=", complaints, "new-final-jobs=", final)

async def stale_probe(factory):
    """用真实 Worker 在第三次领取后取消，再用真实恢复仓储证明没有终态回调。"""
    async with factory() as session:
        repo = SQLAlchemyJobRepository(session, included_job_types={"knowledge_referral_draft"})
        job = await repo.enqueue("knowledge_referral_draft",
            {"referral_id":99, "generation":3}, dedupe_key="synthetic-v8-draft")
        job.attempts = 2
        await session.commit()
        key = job.id
    called = []
    async def cancelled(_):
        raise asyncio.CancelledError()
    async def failure(*args):
        called.append(args)
    async with factory() as session:
        worker = Worker(repository=SQLAlchemyJobRepository(session,
            included_job_types={"knowledge_referral_draft"}),
            handlers={"knowledge_referral_draft":cancelled},
            checkpoint=session.commit, on_job_failed=failure)
        try: await worker.run_once()
        except asyncio.CancelledError: pass
        else: raise AssertionError("取消必须传播")
    async with factory() as session:
        state, attempts = (await session.execute(select(Job.status, Job.attempts).where(Job.id == key))).one()
        assert state is JobStatus.RUNNING and attempts == 3
        await SQLAlchemyJobRepository(session).recover_stale(
            before=datetime.now(UTC)+timedelta(seconds=1))
        await session.commit()
    async with factory() as session:
        job = await session.get(Job, key)
        assert job.status is JobStatus.FAILED and job.payload == {} and not called
        print("E8-2 third-attempt cancellation:", job.status.value,
            "payload=", job.payload, "failure-callbacks=", len(called))

async def main():
    """仅创建内存 SQLite，无真实模型、平台或持久业务库。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    try:
        await debounce_probe(factory)
        await stale_probe(factory)
    finally:
        await engine.dispose()

asyncio.run(main())
```

## 5. 工作区归属与下一步

- 本轮新增：本审查报告；临时探针仅在 /tmp。收尾按项目规则维护对应 AOCI 条目和基线，与业务改动分别归因。
- Claude 的 Spec、历轮交接、占位符/INFO/Bearer 代码与测试、其他已有文档及 .impeccable 产物均保留，未回退或修改。
- 建议 Claude 一次补齐 R1–R3 和两处机械合同，并将恢复验收按“终结在哪个阶段、失败由谁收尾、显示取数在哪个阶段”组织。不要重新扩大到新的知识检索框架、隐私框架或消息重试系统。
- Spec 修订、代码实现、本地验证、真实门禁、发布、客人实际收件仍是不同证据与授权阶段。本报告不是“开始”授权。

## 6. 审查证据绑定

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 125e87a9c8a4364fb7737b57ba661cf08b2e3512b85c2eecdbde080e3924c18e |
| docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v8-review-handoff.md | fd856cf83a0ad3393b29f7269a84196f23bcca480e18dbb74aecd26a9636ba83 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v7-review-handoff.md | 748db08c7c4090cfa43f3d64c488836901992c9002f6316e00380e0e3a81cc21 |
| src/homestay_bot/application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/human_session.py | b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60 |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| src/homestay_bot/repositories/jobs.py | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
| src/homestay_bot/repositories/conversations.py | e5c6dee2b19df73b561a539506e5c3c58583f1df01c46793a46467c8a0cbfef4 |
| src/homestay_bot/services/message_service.py | 68f186b8a8983aaf512232c036df9707cd474958ccb58fbd3b9a25f84dcd6443 |
| src/homestay_bot/services/turn_plan.py | 4f2f93caa43929ba50cc762811b0b26bd279cd3be6918841081e907f42d873d6 |
| src/homestay_bot/services/complaint_service.py | 4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef |
| src/homestay_bot/services/complaint_review_job.py | 5bb8db52e6a21b323eb964fad4f883e1014a055ee8b054b799466c15b3e6c0b1 |
| src/homestay_bot/repositories/customers.py | 6748b2cabe6838d85666015d44b449e975c55e520c1cc98a956d2a16bd896b21 |
