# Codex → Claude：fix2 Spec v6 深度复审与修改建议

- 日期：2026-10-08；作者：Codex。
- 审查对象：[Claude v6 交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v6-review-handoff.md)、[fix2 Spec v6](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
- 基线：工作根目录 `/Volumes/02/code/homestay-bot`，HEAD `80f9b2c309547c36c15cfd142d5dd30b98d87406`；Claude 交接中 10 项文件摘要均与本轮读取时一致。
- 范围：只读业务代码与相关手册，允许新增本审查报告及 AOCI 收尾。未修改业务代码；未提交、推送、部署；未调用真实模型、Hostex、企业微信；未连接已有或生产数据库。
- 证据级别：现有代码事实、离线反例、对拟实施合同的推演分别标明。v6 尚未实施，以下不是“新版测试失败”或生产故障证明。

## 1. 结论与上一轮闭合

**v6 方向可保留，但不建议直接按当前文字进入实施。** 前轮八项已在方案上得到实质回应；本轮沿接管事务、真实卡片 worker、草稿校验、入库和清空重放检查后，收敛出 **2 项 P1、6 项 P2**。应一次补齐共同合同，再沿现有流程连续实施，避免每遇到一个词就加一次特殊处理。

| 上轮问题 | v6 评价 | 本轮剩余约束 |
| --- | --- | --- |
| V5-R1 通配范围被整段消费 | 只删固定操作锚点，方向成立 | 锚点测试须带真实语境，见 §3 第 1 点；不重新扩成领域词表 |
| V5-R2 日期都按入住日比较 | 保留入住、退房角色，方向成立 | 请求区间不能证明逐夜价格完整，见 R3 |
| V5-R3 普通员工无后台身份 | 改用现有 SERVICER 消息，管理员审核，成立 | 新整理失败不能撤销已接收的人工消息，见 R2 |
| V5-R4 整项缺口无法逐个核实 | 调用前候选单元、调用后按缺口原因接受，成立 | 稳定单元和本次最终选择都要跨恢复保持，见 R7 |
| V5-R5 不同房间共用 FAQ 候选 | 独立转交表、房源维度、显式范围，成立 | 批量“已覆盖”还须核对实际知识适用性，见 R6 |
| V5-R6 新旧草稿共享版本 | 独立任务、页面版本与状态检查，成立 | 手动重新生成和迟到写回须同一代次合同，见 R4、R5 |
| V5-R7 隐私保护不足 | 最小输入和编号审计，方向成立 | 最终卡片仍会回填原文，凭据规则也不能缩成纯数字，见 R1 |
| V5-R8 卡片改变客人出站序号 | 独立发件箱阶段、来源加单元唯一键，成立 | 唯一键不能冻结重放时重新选择出的单元集合，见 R7 |

保留现有决定：D-G8 的字面同对象判据及其误拦上限、无计划/回退出口不自动转交、同会话多条转交由模型判断回答对应且管理员审核、不新增员工登录。本轮不要求重新决定这些已声明取舍。

## 2. 必须补齐的合同

### V6-R1 · P1：隐私保护必须覆盖实际发出和更新的卡片，以及全部自由文本字段

**依据。** Spec §2.6(1)(3)(4)(5) 只补“门锁密码、验证码、口令后跟数字”，§2.6(2) 复用现有卡片发送。实际路径是：

1. [TransactionalOutboxWeCom.send_handoff_card](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:793) 登记卡片载荷。
2. [worker 闭包 send_handoff_card](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4775) 在发送前调用 `ContextRetentionService.maintain_customer`；没有接手要点时，读取最近三条 `MessageOrigin.GUEST` 的 **原始 Message.content**，替换卡片 `handover`。入队时脱敏并不能约束这个最终内容。
3. 点击按钮后，[HumanSessionService.handle_card_action](/Volumes/02/code/homestay-bot/src/homestay_bot/services/human_session.py:109) 再经 [_handoff_card_for](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:3717) 读取当前要点与偏好，更新卡片。仅修首次发送仍会遗漏更新出口。
4. [redact_sensitive_guest_text / contains_sensitive_guest_text](/Volumes/02/code/homestay-bot/src/homestay_bot/services/guest_reply_policy.py:315) 不覆盖字母数字密码和二维码；已有 [SQLAlchemyComplaintMessageContext._REDACTION_RULES / _sanitize](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_review_job.py:43) 已覆盖字母数字密码、验证码、二维码、入住指南和链接。

**离线证据。** E6-4 执行从当前源码抽出的真实 worker 函数体：初始卡片原问不含合成手机号，最终 `quote_area.quote_text` 却含该号码。E6-3 证明通用脱敏与敏感检测对合成字母数字凭据不变，现有客诉凭据脱敏会改变它。纯数字补充仍不能处理这个反例。

**影响。** 新资料核实会带上与缺口无关的聊天、偏好或凭据，还会触发现有摘要调用；违反 v6“只发送绑定原问与管家消息、不发整段会话”的承诺。Spec 的模型标准问题、类别、双语问题、关键词与最终人工编辑字段也不能只检查 `answer`。

**最小修改建议。**

- 在现有卡片队列/handler 中区分“资料核实”用途，以转交编号恢复已确定的最小内容；这类卡片跳过整段上下文维护、原文兜底和偏好回填。继续复用卡片组装、接入按钮、值班身份检查与原生接管服务。回调更新同样保留该用途与最小内容。
- 复用既有凭据规则的语义；如需共用，迁出实际有两个调用方的文本净化 helper，保留原客诉行为，不另抄一套越来越大的词表。对原问、标准问题、类别、所有草稿文本及最终人工编辑字段采用一致检查。凭据消息只留来源编号、后台人工查看。
- 过滤后追加的“问题标签”仍是用户输入：净化为有长度上限的一行纯文本，去链接、控制字符和凭据，并明确引为问题。不能直接拼接原始多行文本；主提示里的“不能拼模型文字”不是这条出口的校验。
- 后台经管理员鉴权查看管家原文，和模型/卡片最小输入是不同合同，可保留现有设计。

**验收。** 捕获真实卡片 worker 的最终模板与按钮更新模板；同时覆盖摘要存在、为空、失败，以及数字和字母数字凭据、二维码、指南、类别/关键词与标签换行。断言原文和整段上下文未重新混入；保留正常房号、金额和公共地点。

### V6-R2 · P1：自动沉淀失败不能回滚人工回复与原生接管记录

**依据。** [ConversationService.handle_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:793) 在活动锁内保存消息，SERVICER 分支记原生接入与人工模式，随后返回；[application 内 handle_message 装配](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4387) 到服务返回后才统一 `session.commit`。Spec §2.6(3) 将回答追加和草稿入队放在该同一事务内，没有像客人转交创建 §2.6(2) 那样规定失败隔离。同步 [WeComSyncJobHandler.sync_from_saved_cursor](/Volumes/02/code/homestay-bot/src/homestay_bot/worker.py:229) 还要整页成功后才保存游标。

**推演。** 新记录追加、约束校验或任务入队异常若直接上抛，会撤销本地人工消息和接管记录；平台上管家消息已经发出。错误反复出现时还能挡住该页后续同步。新增知识功能不应扩大人工收发故障面。

**最小修改建议。** 在现有人工落库与接管处理之后，仅将新增“挂接转交＋排草稿”放入保存点。新增整理失败只回滚这部分，保留核心消息与接管事实；日志只记转交/来源编号和错误类型。后台已有“重新生成”入口应能从已保存的受限消息范围恢复失败挂接，页面如实显示未整理，不谎称已排队。不用增加定时重扫系统；也不捕获取消或核心接管异常来伪造成功。

**验收。** 注入新增挂接/入队失败，走真实装配：人工消息与接管记录各保留一次、会话保持人工；新增半成品不留存；同步能够继续。另用核心消息落库失败对照，确认没有吞掉原有错误。

### V6-R3 · P2：参考价的请求端点不能作为实际逐夜覆盖证明

**依据。** Spec §2.1(2) 的日期角色比较以及“按来源测试固定”，没有明确普通问价所需夜晚的完整性；只有最低价明确要求完整逐夜。真实工具 [_reference_prices](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:681) 只汇总上游实际返回的夜价，只要有一晚就输出整行，行的入住/退房字段仍为整个请求区间。[_tool_reply_parts](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2451) 接受非空夜价，随后用这些请求端点生成证据范围。

**离线证据。** E6-1：问 10 月 10 日入住、12 日退房，两晚价格，只提供 10 日的夜价；真实证据仍为 10–12 日，正文只有 10 日价格。端点都相等仍缺 11 日事实。

**最小修改建议。** 区分“查询范围”与“实际夜价集合”：按所问区间的住宿夜晚核对同房有效夜价，复用 `_stay_nights` 和现有最低价完整性逻辑。有价夜晚照常输出，只对缺价夜晚说明未确认；价格缺口保持不可转交。不能因为范围匹配就删掉整句价格锚点、判整个问价已覆盖。房态仍按其实际区间契约判断。

**验收。** 两晚只回一晚、全部回齐、不同房间补夜价、区间外夜价、零价、明确失败；正常 JSON 与 `availability_fallback` 最终正文一致，既不抹掉已有价格，也不漏掉缺价夜晚。

### V6-R4 · P2：回答版本、手动重新生成、迟到结果必须共享明确的代次合同

**依据。** Spec §2.6(3)(4) 的任务去重键只含转交编号与回答版本，但 §2.6(5) 提供“重新生成”。[SQLAlchemyJobRepository.enqueue](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py:57) 对存在的键直接返回旧任务，不区分完成或失败。E6-5 对完成后再入队同键证明：仍是同一个 COMPLETED 作业，任务数为 1。页面的“草稿版本”并没有被写入任务载荷，不能自动解决这个问题。

**影响。** 管家未发新消息时，重新生成可能一直返回旧已完成/失败作业；两个不同生成请求的迟到结果还可能同回答版本竞争。只在外调前判断“记录未结束”，也无法阻止外调期间管理员关闭、入库或标覆盖后再写回。

**最小修改建议。**

- 定义一个单调处理代次，复用现有版本字段即可：新回答或管理员重新生成均递增；载荷、任务键、页面及草稿来源绑定此代次。说明版本提升是生成轮次变化，不一定是原回答正文变化。
- 新代次产生时清除旧稿可入库资格；新稿实际关联的代次必须与页面和当前记录一致。
- 成功、失败、找不到答案三个写回出口都采用**同一条条件更新**，同时匹配开放状态与当前代次；复用 [SQLAlchemyFaqCandidateRepository.mark_draft_ready 的状态＋generation CAS 模式](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/faq_candidates.py:218)，不要复制成“先读状态再无条件写”。
- “最多 5 条”须明确是保留最新五条、还是模型输入窗口。不要采用最早五条满后永不更新：五条寒暄之后的第六条真实答案会永久丢失。保留最新受限输入并显示发生截断，由管理员核对原会话；不为这点建立回答检索系统。
- 48 小时与“转交后”按可信消息发生时间定义，统一 UTC，并处理平台秒级时间与数据库时间精度差；不能把迟到补拉的旧员工消息当成本轮回答。

**验收。** 同样回答手动重新生成确实产生新任务；旧任务晚于新代次/关闭/入库/覆盖时，三种结果均不写回；五条寒暄后的答案仍可进入整理；延迟补拉旧消息和窗口边界不误挂接。

### V6-R5 · P2：新草稿输出须与现有 FAQ 安全校验对齐

**依据。** Spec §2.6(4) 让模型输出实际管家事实并直接复用 `_validate_safety`；现有 [DeepSeekFaqDrafter._validate_safety](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_faq_drafter.py:57) 不只是拒绝链接：凡识别为本店专属事实，必须有 `verification_items`，且中文答案必须包含“【待管理员确认】”。旧 `generate` 提示也有此要求。E6-2 证明“401 房间可以看江景”的普通双语草稿会被拒。

**影响。** 若新提示仅按 v6 字段生成事实草稿，正确答案可能三次都因该合同失败；若为了放行全局削弱旧 FAQ 校验，又会影响原高频候选安全。人工原话是整理来源，仍不是已经审核的经营知识。

**最小修改建议。** 首选保留现有安全校验，新生成合同明确保留“真实管家事实＋待确认标记＋核验项”，同时增加 `found_answer`、`guest_only`；管理员审核后的入库字段按现有知识表单合同处理。没找到答案不要伪造一份专属 FaqDraft 再交校验。若选择支持不带占位的人工来源草稿，须限定新入口、保留共同链接/凭据保护并明示新合同，不能改弱旧入口。不需要新 drafter 框架。

**验收。** 有真实事实、无事实、仅客人个人安排、带链接或凭据各有判别用例；旧高频 FAQ 校验保持不变。区分模型整理可用和管理员已确认两种状态。

### V6-R6 · P2：“同问题＋同房源”不充分证明其他转交已被知识覆盖

**依据。** Spec §2.6(5) 允许管理员选具体房间、全店和有效期，却按转交原来的归一化问题＋房源批量关闭其他记录。真实 [KnowledgeService.retrieve_detailed](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:699) 会按审核范围、房源、所问日期与触发条件过滤；[_validate_scope](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:207) 只验证范围结构与房间存在，不能证明对每个来源问题适用。

**推演。** 例如两条同房源问题分别问今日和下月，管理员只收录下月有效的答案，今日记录也会被关闭为“已覆盖”。房源原本为空却人工改选 401 时，其他同样为空但未确认房源的记录也不因此变成 401。问题文字同一并不能修补这些适用范围。

**最小修改建议。** 首版只结束实际采用的记录；其他记录只有能按它自己的原问、目标房源与日期证明新增条目适用时才可标覆盖。缺少这些证据则留待审核，避免为批量关闭新增匹配系统。确实保留批量操作时，条件更新须复核开放状态和范围，防止覆盖已变更的记录。

同时明确两个实现约束：

- Spec 当前写的是复用 `KnowledgeAdminService.create` **字段**，不能误解为在外层事务直接调用该方法：它在 [create](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:228) 内部提交。按现有 [convert_candidate](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:357) 的模式，同一事务创建条目、转换记录、写审计后一次提交；故障注入应证明全体回滚。
- 保留历史转交时，知识删除要解除新引用（例如 `SET NULL`），历史状态不因此重开；不能只描述“记录不变”而留下阻止知识删除的外键。双数据库迁移验证这个约束。

**验收。** 同问题同房但有效期不同、未定房源人工选房、触发条件不符、真正适用的对照；转换中途失败无孤立启用知识；删除知识不被新外键拦住；历史标记不被迟到任务唤醒。

### V6-R7 · P2：恢复必须沿用整次已提交结果，不能只在重新选中的单元里查旧记录

**依据。** Spec §2.6(1) 说计划已冻结，§2.6(2) 只在“本次被接受单元”的循环中检查已有转交。当前 [_stage_fast_ack](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1250) 仅在传入计划时附带 `turn_plan`；普通最终回复可在 [DeepSeekGuestAssistant.respond](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2571) 中才生成计划。“当前所有最终任务已经冻结计划”不是代码事实，需写出新增冻结位置。

**推演。** 同来源有江景、健身房两个稳定单元：首次模型选择江景，转交/卡片/客人回复已提交，Worker 标完成前中断。重放时即使计划完全不变，模型也可能改选健身房，或知识/冷却/缺口原因已变。来源＋单元唯一键只能阻止江景重复，不能阻止新增健身房卡片；已入队客人正文却仍是首次的结果。独立出站阶段解决序号污染，不解决业务决定变化。

**最小修改建议。** 在现有最终作业/事务边界保存来源绑定的计划与本次最终处理结果（包括接受的转交集合、实际入队状态和完成标记）。**业务事务已提交后**恢复直接认领这个已持久化结果，不再重新规划、选择缺口或计算冷却；尚未提交才可重新计算。复用现有作业载荷与出站记录，不新增恢复框架。冻结计划的写入必须在主调用前，不持活动锁跨模型调用；完成标记和全部业务出站同事务。

**验收。** 真实 Worker 中断窗口保留；第二次助手故意返回不同 `unit_id`、返回空选择、或使原问题已获知识/员工接管。已提交窗口下不再次调用模型、不多出记录或卡片，客人正文与首次结果对应。再以提交前中断作对照，确保没有提前“完成”而漏出站。

### V6-R8 · P2：新正文副本与排队任务必须进入已有测试数据清空边界

**依据。** [SQLAlchemyCustomerRepository.clear_test_customer_data](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/customers.py:1474) 清除客诉、摘要和记忆，将消息正文抹掉但保留消息行与外部编号，会话也保留。Spec §2.6(6) 的“会话删除级联”在这条路径不会触发。新增转交保存原问、标准问题与双语草稿，已经是新的聊天派生正文副本。

**推演。** 用户点清空后，资料核实页面仍能显示旧问题或稿件；在途生成拿着已经读取的文本晚回，还会重新写入；排队卡片或最终回复重放也可能再次使用旧载荷。仅清 Message.content 不能完成新功能加入后的清空语义。

**最小修改建议。** 将该客户相关转交的正文和草稿纳入同一清空事务，保留最小编号/终态以防重放，递增/失效生成代次；在模型前、回写和新资料核实卡片发送前复核记录/来源的清空状态。不能删除原消息编号，也不能让清空产生 30 天“不入库”抑制。已由管理员独立审核入库的知识沿用现有知识生命周期，不随聊天清空自动删除。

**验收。** 真实临时数据库清空后页面无派生正文；模型在途晚回、卡片已排队、旧最终任务重放都不能恢复旧内容；旧来源消息不重入业务；新来源的新正常问题仍可创建转交。

## 3. 对 Claude 六个重点问题的直接答复

1. **操作锚点清单可保留。** 小而明确的操作词清单比新解析框架成本低。若当前正则能直接用固定捕获组返回“消耗的操作部分”，可复用捕获；不要为了消除清单而重写所有识别器。测试应验证“带必要上下文的锚点正例＋保留限定的反例”，不能要求“怎么算”“介绍”等裸词独立触发：这些识别依赖房费/房源语境。未识别的操作说法仍是已声明保守天花板。
2. **房态与参考价不能只共用端点比较。** 可以共用房源和日期角色绑定，但参考价必须另核实际夜价集合，见 R3。
3. **调用前候选、调用后接受的推理有条件成立。** 前后须使用同一切句、原文区间和编号；不能调用后重新切一份残余字符串来找回编号。尚未覆盖的价格/房态/目录原因须先保持不可转交，再对非动态事实单元作实际“资料缺失”核验。主模型选中不是缺口证明，也不是接受证明。将这两个阶段作为一条本地合同测试即可，不再建立第二规划器。
4. **多转交挂接同一员工消息可以保留。** 这是 v6 已明确的模型对应上限；管理员同时看原话与问题/房源，草稿来源不混成自动审核事实即可。无需退回“只有一条才处理”。但需补 R2 的失败隔离和 R4 的代次、最新输入窗口。
5. **固定文案后追加可保留，但原文标签不是固定文案。** 必须满足 R1 的最小输入、一行纯文本和最终出口保护；不得用“来自本地”把客人输入视为可信。
6. **两阶段、一次发布合理，1.70.0 合理。** 阶段一完成回复覆盖；阶段二接入资料核实闭环，统一做最终验收。版本建议不等于升级或发布授权。真实模型门禁在明确授权后按公共回复/提示变化跑 all；本次审查无需运行。application 装配变化触发项目要求的提交前本地全量。

另更正 Claude 交接 §2 第 7 点：`list_recent` 不过滤来源，不代表主回复会使用员工消息；[MessageService.build_context](/Volumes/02/code/homestay-bot/src/homestay_bot/services/message_service.py:147) 只纳入 GUEST 与成功 BOT，SERVICER 不进入主回复上下文。摘要流程可能处理员工消息，应分别说明。即使供应商相同，新的自动草稿仍须独立执行最小输入和凭据保护，不能借旧用途证明新用途安全。

## 4. 一次修订、连续实施的建议

先将上述问题压成五条可验收的不变量，再在同一 Spec 内更新文件/符号计划，不逐条加兼容分支：

| 不变量 | 最小复用位置 | 本轮发现 |
| --- | --- | --- |
| 只有实际覆盖的事实可消除缺口 | 现有覆盖共用位置、实际工具行、`_stay_nights` | R3、锚点/候选单元 |
| 已接收的人工事实不依赖沉淀成功 | SERVICER 原分支之后的现有保存点 | R2 |
| 模型/卡片只有当前用途所需安全文本 | 现有凭据净化、卡片 handler 与回调用途 | R1 |
| 一个生成代次只允许一个当前状态结果 | 既有任务去重和条件 UPDATE；既有草稿 schema | R4、R5 |
| 已提交、已关闭、已清空结果不能被迟到工作改变 | 最终作业事务、知识转换、清空边界 | R6、R7、R8 |

文件计划除 v6 原列文件外，至少显式纳入：`services/human_session.py::handle_card_action` / `application.py::_handoff_card_for` 的卡片更新边界、`repositories/customers.py::clear_test_customer_data` 的新正文清空、`repositories/faq_candidates.py::mark_draft_ready` 的复用模式（不因此修改旧候选）。新任务还须加入 worker 重试与 `recover_stale` 的各自清单，不能只登记 handler。

实施验证按行为合并：覆盖最终正文一组；卡片实际输出一组；人工事务故障一组；生成代次/终态/清空交错一组；知识转换与真实适用性一组；恢复窗口一组。不同数据库承担不同证明：临时 SQLite 验真实服务与回滚，隔离 PostgreSQL 验行锁/条件更新竞争与外键，浏览器验新表单。已有结果未受影响则复用，不以测试总数替代这些合同。

三段 Spec 确认与用户明确“开始”之前不写业务代码。本报告未改变该门禁。

## 5. 本轮验证与复现

### 5.1 已执行与边界

- 完整读取 v6 Spec、交接及上一轮报告；沿最终回复、SERVICER、实际卡片发送/更新、草稿、知识转换/检索、队列和测试清空检查调用链。
- E6-1–E6-5 合并离线探针最终退出 **0**，输出见下。第一次仅因探针将 `date` 对象与字符串比较而退出 1；改为 `isoformat()` 后运行，业务代码未改。
- 探针使用合成文本、内存 SQLite、真实 ORM/队列及当前函数。卡片探针从源码 AST 抽出真实 worker 函数体，注入数据库与收件替身，证明该函数体的最终模板；没有经过完整 FastAPI/企业微信调用，也没有证明按钮线上行为。
- R2、R6–R8 是绑定真实流程的方案推演；R4 的重复键由真实仓储证明，生成竞争与 48 小时仍待实施验收。R5 证明现有校验，不证明新入口已实现或失败。
- 本次产出是文档；不重复跑业务 pytest、浏览器、全量或付费门禁。没有真实模型、PostgreSQL 并发、生产运行或客人收件验收。
- 相关手册只读取最小模型输入与共享事务原则：[手册](/Volumes/02/code/homestay-bot/YuMi民宿AI开发经验与防回归手册.md:282)、[事务原则](/Volumes/02/code/homestay-bot/YuMi民宿AI开发经验与防回归手册.md:394)。手册其他历史留存描述未作为当前实现依据。

### 5.2 观察输出

```json
{"probe":"E6-1","evidence_range":["2026-10-10","2026-10-12"],"actual_price_nights":["2026-10-10"],"missing_night":"2026-10-11"}
{"probe":"E6-2","human_fact_rejected":true}
{"probe":"E6-3","generic_guard_detects":false,"generic_redactor_changes":false,"existing_credential_redactor_changes":true}
{"probe":"E6-4","redacted_original":true,"final_quote_contains_synthetic_raw_phone":true}
{"probe":"E6-5","same_job":true,"status":"completed","job_count":1}
```

### 5.3 可复现命令

在 `/Volumes/02/code/homestay-bot` 执行。断言的是当前边界反例；修复后应改成正确行为断言，不应为了让旧反例退出 0 而恢复缺陷。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import ast
import asyncio
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import Language, MessageOrigin
from homestay_bot.domain.models import Base, Conversation, Job, Message
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.integrations.deepseek_faq_drafter import (
    DeepSeekFaqDrafter, FaqDraft, FaqDraftUnavailableError,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.services.complaint_review_job import SQLAlchemyComplaintMessageContext
from homestay_bot.services.guest_reply_policy import (
    contains_sensitive_guest_text, redact_sensitive_guest_text,
)
from homestay_bot.services.handoff_card import HandoffCard, build_handoff_card

# 本脚本断言现有代码的边界反例，不表示 v6 修复通过。
parts = DeepSeekGuestAssistant._tool_reply_parts(
    "search_reference_price",
    {"check_in_date": "2026-10-10", "check_out_date": "2026-10-12"},
    [{"property_id": 1, "property_title": "401房",
      "check_in_date": "2026-10-10", "check_out_date": "2026-10-12",
      "nightly_reference_prices": [{"date": "2026-10-10", "price": 300.0}]}],
    Language.ZH, "offline-probe", "401房10月10日入住12日退房，两晚多少钱",
)
evidence = parts[0].evidence[0]
assert evidence.target_date.isoformat() == "2026-10-10"
assert evidence.target_end_date.isoformat() == "2026-10-12"
assert "10月11日" not in parts[0].text
print(json.dumps({"probe": "E6-1", "evidence_range": [
    evidence.target_date.isoformat(), evidence.target_end_date.isoformat()],
    "actual_price_nights": ["2026-10-10"], "missing_night": "2026-10-11"},
    ensure_ascii=False))

draft = FaqDraft(
    category="房间设施", question_zh="401房间能看江景吗",
    answer_zh="401房间可以看江景。",
    question_en="Does room 401 have a river view?",
    answer_en="Room 401 has a river view.",
)
try:
    DeepSeekFaqDrafter._validate_safety(
        draft, canonical_question=draft.question_zh, category=draft.category)
except FaqDraftUnavailableError:
    print(json.dumps({"probe": "E6-2", "human_fact_rejected": True}))
else:
    raise AssertionError("预期现有专属事实校验拒绝普通事实草稿")

sample = "门锁密码：Ab12_X，验证码：Q9r8，二维码：ABCD1234"
assert not contains_sensitive_guest_text(sample)
assert redact_sensitive_guest_text(sample) == sample
assert SQLAlchemyComplaintMessageContext._sanitize(sample) != sample
print(json.dumps({"probe": "E6-3", "generic_guard_detects": False,
    "generic_redactor_changes": False, "existing_credential_redactor_changes": True}))

async def main():
    """只使用内存 SQLite 与收件替身，调用真实队列和真实卡片 worker 函数体。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)
        async with factory() as session:
            conversation = Conversation(open_kfid="offline-kf", external_userid="offline-guest")
            session.add(conversation)
            await session.flush()
            # 合成测试文本，不是客户数据；入队时原问已脱敏。
            session.add(Message(conversation_id=conversation.id, external_message_id="offline-source",
                origin=MessageOrigin.GUEST, message_type="text",
                content="合成原文手机13800000000，另问401能否看江景", sent_at=datetime.now(UTC)))
            await session.commit()
            captured = []
            async def send_internal_template_card(**kwargs):
                """截获实际卡片，禁止任何网络发送。"""
                captured.append(kwargs["template_card"])
            tree = ast.parse(Path("src/homestay_bot/application.py").read_text())
            worker = next(node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_handoff_card"
                and node.lineno > 4000)
            namespace = {"Any": Any, "HandoffCard": HandoffCard, "session": session,
                "select": select, "Message": Message, "MessageOrigin": MessageOrigin,
                "replace": replace, "build_handoff_card": build_handoff_card,
                "bundle": SimpleNamespace(wecom=SimpleNamespace(
                    send_internal_template_card=send_internal_template_card))}
            exec(compile(ast.Module(body=[worker], type_ignores=[]),
                "actual-card-worker", "exec"), namespace)
            card = HandoffCard(conversation_id=conversation.id, reason="资料待核实：401看江景",
                guest="合成客户", link="https://example.invalid/employee/",
                original="401能否看江景")
            await namespace["send_handoff_card"]({"card": asdict(card), "customer_id": None,
                "agent_id": 1, "employee_userids": ["offline-staff"], "task_id": "offline-card"})
            quote = captured[0]["quote_area"]["quote_text"]
            assert "13800000000" in quote
            assert "13800000000" not in captured[0]["sub_title_text"]
            print(json.dumps({"probe": "E6-4", "redacted_original": True,
                "final_quote_contains_synthetic_raw_phone": True}))

            jobs = SQLAlchemyJobRepository(session)
            first = await jobs.enqueue("knowledge_referral_draft",
                {"referral_id": 1, "answer_version": 1}, dedupe_key="offline-referral:1:v1")
            await jobs.mark_completed(first)
            await session.commit()
            repeat = await jobs.enqueue("knowledge_referral_draft",
                {"referral_id": 1, "answer_version": 1}, dedupe_key="offline-referral:1:v1")
            assert repeat.id == first.id
            count = await session.scalar(select(func.count()).select_from(Job))
            assert count == 1
            print(json.dumps({"probe": "E6-5", "same_job": True,
                "status": repeat.status.value, "job_count": count}))
    finally:
        await engine.dispose()

asyncio.run(main())

PY
```

## 6. 证据绑定与工作区归属


| 文件 | SHA-256 |
| --- | --- |
| [docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md) | 3a14b521ef8bab68e630d901ed978dc0fc50d871d52090a622caeda899837324 |
| [docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v6-review-handoff.md](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v6-review-handoff.md) | a00f166c1c9203132b20400a59b15d989c78d067b3d5410f8b22b3e0bae92ef8 |
| [docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v5-review-handoff.md](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v5-review-handoff.md) | 39df156185580c5dc86b49032bd8bf396e35ae8ca8d9041ca0d73c4dba2e91e8 |
| [src/homestay_bot/services/conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py) | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| [src/homestay_bot/application.py](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py) | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| [src/homestay_bot/integrations/deepseek_client.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py) | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| [src/homestay_bot/integrations/deepseek_faq_drafter.py](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_faq_drafter.py) | db3cf06d7f448829ae5285607a87255fc5b42dc4e0ebced8ecdeab05fb193526 |
| [src/homestay_bot/services/complaint_review_job.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_review_job.py) | 5bb8db52e6a21b323eb964fad4f883e1014a055ee8b054b799466c15b3e6c0b1 |
| [src/homestay_bot/services/guest_reply_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/guest_reply_policy.py) | d032c5fb4cff586aa3072cd2b895ec211e4fe903f7a45cfe3d355c5928d3d702 |
| [src/homestay_bot/services/message_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/message_service.py) | 68f186b8a8983aaf512232c036df9707cd474958ccb58fbd3b9a25f84dcd6443 |
| [src/homestay_bot/services/human_session.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/human_session.py) | b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60 |
| [src/homestay_bot/services/handoff_card.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/handoff_card.py) | 6b47dd1a6037cefb431040713ae3827f9571e899243db9ff0267e851fb60859b |
| [src/homestay_bot/routes/knowledge.py](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py) | d03f246a224fdcd90db2bb8dd883617b8ce7c91c25092cf8c7e5ed28ee499fe1 |
| [src/homestay_bot/services/knowledge_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py) | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| [src/homestay_bot/repositories/jobs.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py) | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
| [src/homestay_bot/repositories/customers.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/customers.py) | 6748b2cabe6838d85666015d44b449e975c55e520c1cc98a956d2a16bd896b21 |
| [src/homestay_bot/worker.py](/Volumes/02/code/homestay-bot/src/homestay_bot/worker.py) | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |

本轮新增仅此审查报告；AOCI 在报告稳定后维护对应认知资产。原有未提交 Spec、Claude/Codex 历轮报告、上一轮 Claude 报告补充、`.aoci/baseline.json` 与 `aoci.code.txt` 继续保留；`.impeccable/critique/` 不属于本次工作。没有业务代码 diff。

Claude 接手时请一次回应 R1–R8：认可/不认可、对应 Spec 条款、最小修改位置与有判别力的验收。若不同意，请提供同一真实链路的反向证据；不以新增总测试数或旧门禁替代。完成共同合同后再按现有三段确认推进，不先编码、外发或部署。
