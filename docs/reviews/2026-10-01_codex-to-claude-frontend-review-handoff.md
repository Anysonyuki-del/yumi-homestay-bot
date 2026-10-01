# 前端 Spec 复核交接报告 · Codex → Claude

日期：2026-10-01。用途：交接对 Claude 六条复核意见的核对结果，供下一轮修订原报告与 Spec。

结论：主要缺陷判断成立，但 R1 的修复范围不完整，R6 对已清理消息的描述不准确。R2 还需补输入恢复，R3 还需落实提醒与旧任务隔离，R4 应区分既有权限范围和数据投影边界。不能原样把六条意见全部转成实施要求。

本次已完成源码核对及 9 项本地隔离探针。业务代码、原报告、原 Spec、Claude 复核文档均未修改。本次授权是编写交接报告；业务选项尚未确认，未取得“开始”编码指令。

## 1. 接手基线与阅读入口

复核与交接基线：分支 `main`，HEAD `868b9e77c2dc76ec4d80de70dd7d3794cbe0b4cc`；[pyproject.toml 的 version](../../pyproject.toml:7) 为 `1.62.0`。这是本次调查快照，不代表生产部署状态。

接手先读以下三份文档，再读本报告中的差异：

| 文档 | 职责 |
| --- | --- |
| [原前端审查交接报告](../../docs/specs/2026-10-01_frontend-audit-handoff.md) | F01–F15 的问题证据与审查范围 |
| [前端优化 Spec 草案](../../docs/specs/2026-10-01_frontend-improvement-spec.md) | W1–W6、D1–D7 与验收场景；仍待确认 |
| [Claude → Codex 复核意见](../../docs/reviews/2026-10-01_frontend-spec-review-claude.md) | R1–R6 与补充建议；需要按本报告修正 |

本报告只保存本轮新增证据与修订要求，不替代原 Spec。接手时核对 Git 与相关源码；仅文档变化无需重跑业务验证，源码或装配变化才重验受影响证据。

## 2. R1–R6 核对结论

| 意见 | 判断 | 修订要求 |
| --- | --- | --- |
| R1 发送中可关闭/退回 | 缺陷成立，方案不完整 | 除 SEND_QUEUED 外，覆盖 DELIVERY_FAILED 但原任务仍自动重试的在途窗口；补手动重发与旧任务回写约束 |
| R2 非法操作成为 500 | 成立，恢复方案需补充 | 受控业务拒绝可复用现有错误处理；输入失败另定义草稿恢复，不能把 PRG 等同于保留输入 |
| R3 保留 CONVERTED 导致静默 | 成立 | 修订原 D4 推荐；重新开放时明确阈值、冷却、计数与 draft_generation，保留业务选项待确认 |
| R4 电话展示与标签 | 部分成立 | “脱敏电话”标签错误成立；可以推荐合并页显示电话，但原报告未误认完整号码，复用时需适配投影类型 |
| R5 删除路由遮挡 | 成立 | 删除路由顺序/路径须避开现有通配路由，验收真实 HTTP 路由匹配 |
| R6 消息游标定位 | 部分成立 | 可见消息可用现有游标；带 cleared_by 的目标会被查询排除，不能承诺一律显示占位 |

## 3. R1：客诉在途状态必须结合出站任务判断

### 已验证事实

1. [SQLAlchemyComplaintRepository.mark_returned / mark_cancelled](../../src/homestay_bot/repositories/complaints.py:297) 只校验版本，没有状态白名单；[ComplaintAdminService.cancel](../../src/homestay_bot/services/complaint_admin_service.py:122) 不撤回出站任务。
2. [_guest_reply_is_stale](../../src/homestay_bot/application.py:345) 会检查原生人工会话等条件，但不检查客诉复核状态。没有来源客人消息边界的客诉任务不会因“已关闭/已退回”在此被拦截。因此应写“客诉状态本身不阻止任务发送”，不要把它扩大成任何条件下都必然发送。
3. [_record_complaint_delivery](../../src/homestay_bot/application.py:889) 调用 [mark_delivery_failed / mark_delivery_sent](../../src/homestay_bot/repositories/complaints.py:202)；这两个方法仅在 SEND_QUEUED、DELIVERY_FAILED 写回，其余状态保持原样。本地已复现关闭后成功回写仍为 CANCELLED。
4. [_run_worker_loop 内 send_guest](../../src/homestay_bot/application.py:3049) 在发送异常后先回写 DELIVERY_FAILED，再把连接失败、明确限流包装为可安全重试异常。[SQLAlchemyJobRepository.mark_failed](../../src/homestay_bot/repositories/jobs.py:391) 在重试次数未耗尽时把原任务改回 PENDING。于是“客诉显示失败”和“任务已停止”并不等价。
5. [SessionComplaintAdminService.send](../../src/homestay_bot/application.py:2861) 对 DELIVERY_FAILED 使用 `retry-{review.version}` 阶段。本地使用真实 Session 服务和 worker，连接失败后立即手动重发，得到两条 PENDING 的 wecom_send_text 任务。
6. [_record_complaint_delivery](../../src/homestay_bot/application.py:889) 回写仅提取稳定客诉主键，不区分阶段。实施时需验证旧任务的结果不能错误确认或覆盖新尝试。

Claude 关于“退回后新草稿发不出去”的更正成立：[TransactionalOutboxWeCom._outbox_id / _enqueue_guest_text](../../src/homestay_bot/application.py:615) 在无重试阶段时沿用出站编号；退回、重新生成草稿后发送会遇到已有去重键。[ComplaintAdminService.send](../../src/homestay_bot/services/complaint_admin_service.py:85) 因返回空出站编号抛 ValueError。本地已复现。

### 对文档的修订要求

- 原 Spec W1 应把在途操作造成投递记录失真归入正确性修复，不能全部交给 D2 决定。
- Claude R1 “加状态白名单、不改出站与回写逻辑”的实现结论需改为待核查方案。只排除 SEND_QUEUED 会漏掉失败后的自动重试窗口。
- 最小安全方案优先复用已有 delivery_outbox_id、jobs 状态与现有事务边界。明确什么时候算在途、什么时候允许手动重试、旧尝试如何回写；不要新建并行出站系统。
- D2 仍保留真正的终态重开业务决策，至少包括 SENT 与 CANCELLED，不能只问 SENT。允许重开时必须定义新发送阶段与旧任务隔离；推荐终态先只读尚未得到用户确认。
- Claude 对“失败时也无人知道”的表述应收窄为“客诉投递回写被丢弃”。[SQLAlchemyJobRepository.mark_failed](../../src/homestay_bot/repositories/jobs.py:391) 仍记录任务错误，当前证据不足以证明全系统没有失败记录。

### 补充验收

- 在途关闭/退回被拒绝，状态、版本、草稿及任务数量不变；按钮限制与直接构造 POST 一致。
- 连接失败或 45009 后，原任务仍 PENDING/RUNNING 时的手动重发、关闭、退回符合已确认规则，不产生重复在途投递。
- 原任务终态失败后，允许的一次手动重试只登记一个新阶段；重复请求不新增任务。
- 用不同 Session 模拟请求与 worker 回写，验证旧尝试结果不覆盖新尝试，避免共享对象掩盖边界问题。
- 若修复会触及 application.py 装配，按项目 AGENTS.md 判断相关测试与提交前全量门禁；这不是本次文档交接需重跑全量的理由。

## 4. R2：可恢复提示与输入恢复是两个要求

### 已验证事实

[routes/complaints.py::_action](../../src/homestay_bot/routes/complaints.py:125) 消费 CSRF 后只捕获 ComplaintVersionConflict。状态拒绝、无可用正文、重复出站等 ValueError 没有受控页面处理，本地路由探针得到 HTTP 500。版本冲突虽已有 409，也需按 Spec 判断 HTML 恢复体验。

[handle_operation_refused](../../src/homestay_bot/routes/page_errors.py:92) 对 HTML 保存错误提示并 303 重定向，不保存提交的 textarea。原 Spec §4.3 已明确不能宣称该处理器自动保留输入。

### 对文档的修订要求

- 采纳 F01 补写真实 500 表现的意见。
- 已明确为用户可见的业务拒绝可以在服务/路由边界转成 OperationRefused；先查调用方，不把所有 ValueError 原文直接展示。
- 状态变化、过期版本拒绝与可修改的输入校验失败分别说明恢复方式。PRG 适用于返回最新状态；需要保留当前编辑正文时，采用本次已认证失败响应中的受控重渲染，或另明确足够的恢复方案。
- 错误响应签发新令牌，旧令牌保持失效；不把长正文塞进签名 Cookie，不为了恢复页面新增持久化草稿系统。
- 补验收：不再出现预期业务 500；提示可理解；需恢复的正文保留；修正后可再次提交；未知异常不泄露 SQL、路径或秘密。

## 5. R3：删除知识后的候选重开需明确提醒语义

### 已验证事实

[SQLAlchemyFaqCandidateRepository.get_or_create](../../src/homestay_bot/repositories/faq_candidates.py:59) 按 canonical_key 复用旧候选；[add_occurrence](../../src/homestay_bot/repositories/faq_candidates.py:105) 对非 OPEN 返回 False；[reopen_expired](../../src/homestay_bot/repositories/faq_candidates.py:416) 只重开 SNOOZED。本地构造“引用为空、状态仍 CONVERTED”，再次询问同一规范化问题时命中同一候选，不增加出现次数。

因此原 D4 推荐确实会使同一 canonical_key 继续静默。该结论不应扩大成所有语义近似但规范化结果不同的问题均永久消失。

### 对文档的修订要求

采用 Claude 的 A/B/C 选项及后果说明，建议 A 为“同事务解除引用并重新 OPEN”；仍由用户确认，不预填已选项。A 必须补齐：

| 字段/规则 | 必须说明的语义 |
| --- | --- |
| total_occurrences | 是否保留历史计数；推荐保留历史但不能用旧累计量触发立即提醒 |
| last_threshold_total | 新一轮统计从什么基线开始；可推荐设为重新开放时的累计量，仍需结合真实阈值验证 |
| last_reminded_total / last_reminded_at | 历史提醒数据如何保留，既有冷却是否继续生效 |
| notification_pending / draft_status / draft_payload | 清除旧待提醒与旧草稿，避免复用已删除知识对应的内容 |
| draft_generation | 保持单调推进并废止旧任务，不能泛称“清空草稿字段”后重置为零 |

依据：[FrequentFaqService._track_eligible](../../src/homestay_bot/services/faq_candidate_service.py:250) 同时要求最近窗口内至少三次出现、累计增量至少三次且没有通知在途；[_enqueue_trigger](../../src/homestay_bot/services/faq_candidate_service.py:186) 使用 last_reminded_at 计算冷却；[仓储 _clear_private_content](../../src/homestay_bot/repositories/faq_candidates.py:462) 已通过递增 draft_generation 使旧任务失效；[FaqDraftJobService.handle](../../src/homestay_bot/services/faq_draft_job.py:135) 校验状态与代次。

验收重点：删除本身不立即生成或发送提醒；下一轮达到约定阈值后可重新发现；重复入站不重复计数；旧代次任务不回填或提醒。现有字段可以承载候选状态变化，但本轮尚未实现，不把“无需迁移”扩展成已有数据无需核查。

## 6. R4：电话展示建议成立，投影适配不能省略

[CustomerAdminService._display_phone](../../src/homestay_bot/services/customer_admin_service.py:778) 明确返回完整号码，并记录 1.41.0 起的既有展示决策；[客户详情基础信息](../../src/homestay_bot/templates/customers/detail.html:15) 却写“脱敏电话”。因此改成“电话”应加入 F15，不必同时重命名 masked_phone。

同权限合并页内联两侧电话，可以作为 D5 推荐，不需要另制造一轮独立隐私审批。但这仍是待确认 Spec 选项，不等于已授权实现。

需要纠正两点：

- 原报告 F06 已明确完整号码事实。其限制是“不能把完整号码称为脱敏投影”和“确认合并页所需字段”，不能概括成原报告误判了号码内容或断言扩大管理员受众。
- [_safe_merge_customer](../../src/homestay_bot/repositories/customers.py:921) 返回字典，只查询 ID、姓名；_display_phone 使用 getattr。直接给字典加 phone 再传入该函数不会正确取值。实施时在现有仓储—服务映射处适配最小电话投影，复用已有解密能力，不加载整个客户对象，不复制另一套解密逻辑。

补验收：两侧电话对应正确客户；明文、存量密文、无号码三种情况可理解；普通权限直接访问仍受控；合并方向、档案返回链接与二次确认保留。若 D5 选完整电话，AC06 现有“断言真实脱敏”须改为断言已确认的展示契约与权限，不能一边选完整号码、一边要求遮罩。

## 7. R5：删除路由必须通过真实路由匹配验证

[toggle_knowledge](../../src/homestay_bot/routes/knowledge.py:952) 是 `POST /{entry_id}/{action}`，非 enable/disable 返回 404。隔离应用装配现有 router 后，在其后追加同前缀的 delete 探针，HTTP POST 先进入现有处理器，返回 404，新增处理器未执行。

采纳：删除路由在通配路由前注册，或选择不会冲突的路径；AC08 使用真实 HTTP 请求到真实路由表，不只直接调用删除函数。本探针只观察路由匹配，未执行删除。

原 Spec 的权限、CSRF、候选引用、数据库事务和提交后文件清理要求继续由该 Spec 承载，本报告不另复制删除契约。

## 8. R6：现有游标可复用，但不能保证清理后仍有目标占位

[customer_detail](../../src/homestay_bot/routes/customers.py:350) 已接收 before_message_id；[SQLAlchemyCustomerRepository.customer_messages](../../src/homestay_bot/repositories/customers.py:639) 查询 `Message.id < before_message_id`，取一页后转为正序。

因此目标存在、属于该客户且可见时，`before_message_id=root_id+1` 会让目标成为该页最新一条，可采用。

但同一查询排除了带 cleared_by 的行。隔离数据库里目标 120 带清理标记，以 before_message_id=121 查询只返回旧消息 119，目标不在页面中，隐藏计数为 1。正文为空但没有该标记的目标 121 则仍能被查询。这两种情况必须区分。

修订要求：

- 替换 Claude R6 的“一律照常显示占位”承诺；将游标定位限定为仍可查询的目标消息。
- 保留原 Spec §6.1 对已清理、无客户关联、已合并客户的受控降级。不能以隐藏总数推断某个目标消息的清理详情。
- 是否需要专门的目标状态提示，按实际页面能力确定；不要为了链接参数预先添加独立 API、全文搜索或滚动系统。
- 验收同时覆盖可见目标、仅正文为空、带 cleared_by、关联失效/合并后的路由处理；不能误跳到另一客户。

## 9. 已有验证证据与限制

以下是同一基线上的本地隔离复现，执行退出码为 0。表中“已复现”证明缺陷或边界存在，不表示修复验收通过。

| 探针 | 输入与真实边界 | 观察结果 |
| --- | --- | --- |
| E1 R1 自动重试窗口 | 临时 SQLite，真实 SessionComplaintAdminService、_run_worker_loop；企业微信 send_text 用无网络 ConnectError 替身 | review=delivery_failed，job=pending，attempts=1 |
| E2 R1 手动重发 | E1 后从独立 Session 手动发送 | 两条 wecom_send_text 均 PENDING，review=send_queued |
| E3 R1 关闭后回写 | E2 后关闭，再调用真实过时判定与成功回写函数 | blocked_before_send=false，回写后仍 cancelled |
| E4 R1 退回重发 | 真实服务发送→退回→mark_ready 新草稿→发送 | ValueError：客诉回复发送任务已存在或不可重试 |
| E5 R3 CONVERTED 静默 | 真实候选仓储，解除引用、保留 CONVERTED，再获取同一规范化问题并 add_occurrence | 复用同一候选，新增出现返回 False |
| E6 R6 清理标记 | 真实客户仓储，目标带 cleared_by，按 root+1 查询 | 目标被排除，仅返回旧消息，hidden_count=1 |
| E7 R6 空正文 | 真实客户仓储，目标 content=None、无 cleared_by | 目标仍存在于查询结果 |
| E8 R2 业务拒绝 | 现有客诉 router、测试管理员认证与真实 CSRF；服务替身抛既有业务 ValueError | HTTP 500 |
| E9 R5 路由遮挡 | 现有知识 router、测试管理员认证；追加无删除副作用的后置 delete 处理器 | HTTP 404，delete_handler_reached=false |

复现使用临时合成数据库，开启 SQLite 外键；没有真实客户、真实消息或外部网络发送。请求/worker 跨事务探针使用不同 Session，E8/E9 使用测试认证装配，不代表完整生产应用的端到端验收。

执行方式为项目 .venv 中的临时内联 Python，引用现有测试认证辅助与路由装配；未保存探针脚本、未新增回归测试文件。上表给出重建最小回归所需的输入、边界与判别结果，不伪称为已入库的九个自动测试。

未覆盖：本轮没有浏览器/生产运行验收、真实模型或企业微信收件验证，也未穷举并发交错、所有异常码和 PostgreSQL。45009 的自动重试来自源码确认，实际 worker 探针使用 ConnectError。F09、F11 不因这次六条反馈核对而成为已全面复核项。

本次仅新增交接文档，相关源码、依赖、测试与运行配置未变，因此复用上述证据，不重复跑业务测试。

## 10. Claude 下一轮建议交付

先修订文档，再进入用户确认；本次交接不授权业务编码、提交、推送、部署或真实外部操作。

| 原文位置 | 建议修改 |
| --- | --- |
| 原报告 F01；Spec §4.1、D2、AC01/AC03 | 补 500、在途重试、手动重发与回写归属；区分正确性修复和终态重开业务选择 |
| Spec §4.3 | 区分业务拒绝回跳和输入恢复；知识编辑、新建、候选转换分别说明恢复页面与上下文 |
| Spec §5.3、D4、AC08 | 调整候选关联推荐，补提醒阈值、冷却、旧代次任务约束 |
| 原报告 F06/F15；Spec §6.2、D5、AC06 | 修正电话标签；补合并页电话推荐与投影适配要求，同步展示契约的验收措辞 |
| Spec §5.2、AC08 | 增加删除路由冲突与真实 HTTP 匹配验收 |
| Spec §6.1、D5、AC05 | 采用现有消息游标，保留目标清理/关联失效的降级规则 |
| Claude 复核文档 §1、R1–R6、§5 | 收窄未覆盖结论，修订 R1 最小方案与 R6 占位承诺，区分源码判断和实际探针 |

可采纳的次要意见：删除原报告缺乏可复现计算依据的 13/20 评分；减少报告与 Spec 的重复；知识新建/候选转换失败在列表页恢复时，重建对应分页与表单上下文，不为此新增独立页面。

修订后交回：文档差异、更新后的工作包/决策表、尚未确认的业务选择及对应验收。仍保留 D1–D7 的待确认状态，不把推荐或另一模型赞同记成用户批准。实施前遵守 [项目 AGENTS.md 的渐进式 Spec 门禁](../../AGENTS.md:11)，取得完整 Spec 确认和明确“开始”指令。
