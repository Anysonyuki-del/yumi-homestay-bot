# Codex → Claude：fix2 Spec v7 深度复审与修订建议

- 日期：2026-10-08。
- 审查对象：完整读取 [v7 交接报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v7-review-handoff.md:1) 与 [fix2 Spec v7](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:1)，并沿当前源码核对调用链。
- 基线：main，HEAD `80f9b2c309547c36c15cfd142d5dd30b98d87406`。fix2 v7 尚未实现；工作区另有知识占位符和日志任务的未提交代码。
- 结论：**保留 v7 的收敛方向，但暂不进入实施。仍有 2 项 P1、3 项 P2，应一次补齐共享契约再确认。**
- 本次只写审查报告；没有修改业务代码，没有提交、推送、部署、真实模型调用、平台发送或生产数据库操作。

## 1. 上一轮八项闭合情况

这里的“闭合”是方案层面认可，不是代码验收通过。

| v6 项 | v7 评估 | 依据与剩余条件 |
| --- | --- | --- |
| R1 卡片与模型隐私 | 部分闭合 | worker 和按钮更新都采用最小转交内容，方向正确；拟复用的净化函数漏识别英文凭据，见 V7-R2 |
| R2 人工消息事务耦合 | 方案闭合 | D-G18 取消 SERVICER 入库钩子。主回复上下文不纳入人工消息的既有行为继续保留 |
| R3 逐夜价格 | 方案闭合 | Spec 175–176 行区分请求范围和实际夜价；不能用另一房源的夜价补齐。实施需同时验正常与回退出口 |
| R4 生成代次 | 部分闭合 | 每次整理递增代次解决已完成任务去重；仍需生成状态条件与输入边界，见 V7-R4、R5 |
| R5 新旧草稿校验 | 方向认可 | 独立人工来源入口保留旧校验正确；引用子串只证明引用存在，不能证明答案事实，见 C1 |
| R6 入库原子性及误关 | 方案闭合 | 取消批量“已覆盖”，单事务、单记录采用、知识删除 SET NULL 都正确；必须补入库状态资格，见 V7-R4 |
| R7 恢复幂等 | 未闭合 | 新标记仅跳过转交阶段，不能保证后续整轮动作及回复不重算，见 V7-R1 |
| R8 清空派生内容 | 部分闭合 | 转交清空、递增代次正确；旧来源任务在清空后首次执行和旧卡片点击仍需守卫，见 V7-R1、R3 |

认可并保留的边界：D-G8 的期间候选保守否决、同对象问法完全相同的识别上限、无计划及 availability_fallback 不转交、管理员决定知识范围、人工回答对应可能有误且须审核。本轮不要求重建规划器、实体识别框架或第二套检索器。

## 2. 需要一次补齐的五项

### V7-R1 [P1] “转交已决定”应结束已提交的最终处理，不能只跳过转交阶段

**Spec 依据**：[§2.6(2)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:335) 认为看到决定行后跳过转交，其余回复由原发件箱键去重，客人仍收到首次正文。

**源码依据**：

- [TransactionalOutboxWeCom._outbox_id](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:615) 的序号由该实例全部出站动作共享，类型不隔离计数。
- [_enqueue_guest_text](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:711) 只拒绝同一个键，不能判断这是已完成来源的另一种执行顺序。
- [ConversationService._process_model_reply_body](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1754) 先登记任务及通知，再生成客人回复；风险、住宿确认、设施分支还在转交插入位置之前。
- [_record_task_suggestion](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1877) 由重新得到的计划决定是否通知；[_send_prepared_guest_reply](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:2200) 在发件箱拒绝重复前已把本次重算正文设为通知使用的最后回复。
- [process_recorded_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1027) 当前没有最终阶段完成标记检查。

**反例与影响**：同一来源第一次为“任务通知 → 客人回复”，重放计划不再登记任务，只发客人回复，guest 序号从 2 变成 1。E7-1 使用真实 outbox 和内存 SQLite，客人出站任务变为 **2 条、不同正文**。这不是 v7 全链实现复现，而是直接证伪它所依赖的“其余动作重算仍会按原键去重”前提。资料核实卡片独立去重只移除了这一种序号变化，混合问题的任务通知仍共享计数。

此外，[clear_test_customer_data](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/customers.py:1521) 保留消息行、抹正文并写 cleared_by。若旧最终任务在清空前尚未执行转交阶段，就没有决定行；按 v7“没有标记从头计算”，旧载荷仍可能首次新建转交。仅清理已有转交记录挡不住这一窗口。

**最小修订**：

1. 复用拟新增的决定表，把它明确为本来源**最终业务阶段已经完成提交**的收据；与全部转交、最终客人 outbox 及其他本轮业务副作用同事务完成，不新增正文副本。没有缺口或接受列表为空也要记完成，否则第一次没有缺口、重放时新增缺口仍可绕过。优先放在 [handle_message 的最终业务事务提交处](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:4436) 统一收尾，覆盖最终分支的早返回；首响、debounce 与规划释放锁的中间提交不能写最终完成。
2. 恢复入口在重新调用助手、取当前知识、判风险、登记任务之前读取收据：存在就结束该来源的最终处理，让原有已登记出站继续执行；不要只跳过转交阶段再重新组装其余行为。
3. 来源消息已清空时，即使没有完成收据，旧最终任务也应结束。实际客人发送处同样核对来源清空状态，防止已排队的核实说明在清空后发送。当前 [_guest_reply_is_stale](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:345) 检查人工接管及新活动，没有这个来源清空守卫；新增判断不应被 stale_exempt 或后续分段绕过。复用现有 cleared_by/消息记录，不另建客户版本系统。
4. 收据不是“卡片已经送达”。原卡片和客人 outbox 自己维护投递状态；不得用新模型结果重建已提交的回复。
5. 收据在来源身份仍保留期间不得被出站作业的 30 天保留期牵连删除：[SQLAlchemyRetentionRepository.purge](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/retention.py:72) 当前只清理列明的历史对象，新表的保留契约需明确。

**验收**：真实 Worker 取消、recover_stale、claim_next；第二次助手改变任务计划、风险、缺口选择及答案，也不得再被调用或新增任何副作用。首次提交前中断仍可续接。补“清空后旧任务首次执行”和“清空后已有客人 outbox 待发”两个窗口。断言最终正文、通知与任务精确数量，不能只数 referral-card。

### V7-R2 [P1] 直接复用客诉净化规则仍会把常见英文凭据送入新模型路径

**Spec 依据**：[§2.6(0)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:318) 叠加客诉净化与 redact_sensitive_guest_text；[§2.6(3)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:355) 根据是否含敏感内容决定是否把人工消息交给模型；新草稿和管理员提交同时含英文问答。

**源码依据**：[SQLAlchemyComplaintMessageContext._REDACTION_RULES](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_review_job.py:54) 的密码、验证码规则为中文标签，只有 QR code 单独涵盖英文；[redact_sensitive_guest_text](/Volumes/02/code/homestay-bot/src/homestay_bot/services/guest_reply_policy.py:315) 使用的 [_SENSITIVE_GUEST_PATTERNS](/Volumes/02/code/homestay-bot/src/homestay_bot/services/guest_reply_policy.py:82) 覆盖联系方式、证件、订单和地址，不补英文密码或验证码。

**实测**：E7-4 顺序调用 Spec 指定的两个真实函数。合成的 `Door lock password: Ab12_X`、`Verification code: Q9r8` 均原样保留；对应中文格式被过滤。因此“净化前后没有变化”会把这两条凭据判断为可发送，风险不只是翻译错误。

**最小修订**：

- 在转交与客诉共用的边界函数中补这两类已证明的英文凭据标签及其大小写、常见分隔形式；检测与净化复用同一模式，不能各写一套产生差异。
- 新转交路径的输入、输出和管理员提交都走同一检测。不得把“全部自由文本已调用净化”误写成“全部字段已安全”。
- 保持有限格式识别的声明；不用任意英文单词或全部数字作为密码。保留房号、正常金额、公共地点和“如何获取密码”等无凭据提问的对照。
- 本次必要增加的英文凭据防护应明确记录为客诉共享函数的行为增强；其余已有格式保持结果一致，不能同时宣称“客诉行为完全不变”。

**验收**：捕获新草稿模型的实际请求信封；英文凭据消息不得进入。对英文答案、类别、关键词和最终提交做相同负例，入库失败不能留下知识或改变采用状态。无需为此扩展无关日志任务。

### V7-R3 [P2] 卡片重建属于显示失败，不能让已执行的人工动作失去本地记录

**Spec 依据**：[§2.6(2)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:344) 把 referral 查库与最小卡片重建放到 handle_card_action → _card_for，人工动作沿用当前逻辑。

**源码依据**：[HumanSessionService.handle_card_action](/Volumes/02/code/homestay-bot/src/homestay_bot/services/human_session.py:102) 先执行 _accept/_release；109 行重建卡片在 110 行 try **外面**，只有 update_template_card 的异常被隔离。[accept_handoff](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/operations.py:1707) 只 flush 模式和审计。当前 card_for 契约只收 Conversation，task_id 仅传给模板构建；新分支需要明确怎样获得 task_id。

**实测**：E7-2 使用真实 HumanSessionService、真实审计仓储与内存 SQLite，只有平台是替身。平台 transfer 成功之后，card_for 抛异常，异常外传；调用方回滚后，本地模式仍为 BOT、接管审计为 0，而平台替身已 HUMAN。新 referral 查库、已清空记录和任务编号解析增加了触发这条既有失败边界的机会。

**最小修订**：

1. referral 的 task_id 解析、记录存在性、conversation_id 对应关系与状态资格应在外部人工动作前核实。编号前缀只是用途路由，不替代关联校验。
2. 把卡片重建和模板更新一起放进同一个显示失败隔离范围；单纯内容构建失败不能向上触发人工动作重做。真正的数据库事务错误不能吞掉后照常提交：在动作前读取所需最小字段，必要时按现有保存点模式隔离显示阶段。
3. 清空、删除、终态卡片的规则要分 accept/release：旧待接入卡片不能在清空后重新接入；**已接入会话不能因转交已入库/关闭而失去交还入口**，交还仍按当前人工会话和员工权限处理，显示用不含旧正文的固定关闭卡片。
4. 若修改 card_for 参数契约，一次更新生产 lambda 和现有测试调用方，保留普通 handoff 卡片对照。不增加另一套人工会话服务。

**验收**：卡片重建抛错、缺失 referral、task_id 与 key 会话不符、清空后旧 accept、关闭/入库后现有 release；同时断言平台替身状态、本地模式、审计与结束任务。普通卡片已有接入/交还用例可复用。

### V7-R4 [P2] 代次隔离还应限制本代次的生成状态和入库资格

**Spec 依据**：[§2.6(3)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:356) 规定三种回写都只匹配“开放＋当前代次”；[入库规则](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:372) 同样只列开放状态与页面代次。新表把待处理、整理中、已整理、没找到答案、草稿失败混在一个状态枚举中，却没有定义“开放”的精确集合。

**源码依据与实测**：[SQLAlchemyFaqCandidateRepository.mark_draft_ready](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/faq_candidates.py:221) 的 OPEN 是候选生命周期状态，不是草稿的生成状态。E7-3 对同一候选、同一代次连续写两次，第二次成功覆盖 READY 草稿。这个模式证明换代/关闭保护，不能直接证明“一个生成代次只产生一个不可再变结果”。

现有 [FaqDraftJobService.handle](/Volumes/02/code/homestay-bot/src/homestay_bot/services/faq_draft_job.py:148) 还有 READY/FAILED 的 needs_draft 判断；v7 只引用写回 CAS，遗漏了这部分重放终止约束。异步任务结果已提交、队列尚未完成时重放是本轮已经要求覆盖的实际窗口。

**最小修订**：

- 不照搬旧候选的 OPEN：新记录的完成回写必须匹配 **status=整理中 AND generation=期望值**；成功、没答案、重试耗尽分别转到明确终态，迟到的同代次结果不再可写。
- Worker 读取到本代次已整理/没答案/失败时结束，不再次调用模型。重试只在仍整理中时继续，明确最多 3 次的计数归属，避免服务内重试与 Worker 重试叠加。
- 入库必须在锁内确认 **status=已整理＋代次一致＋完整草稿存在**，不能从整理中、没答案或失败状态入库。新整理要清掉旧 payload 或明确禁止把旧稿随新代次重新采用。
- 新整理、关闭、采用、清空统一用现有记录锁和确定性条件；无需新增 worker 所有权框架，也不为此改旧 FAQ 的并发语义。

**验收**：同代次第一次成功后，第二次成功/失败/没答案回写都拒绝；新代次已生成但旧页面仍提交时拒绝；整理中入库拒绝；草稿已提交后 Worker 中断重放不再调模型。PostgreSQL 对照两次入库及回写与采用交错。

### V7-R5 [P2] “转交后消息”和“一次整理使用的消息”必须有明确上下界

**Spec 依据**：[§2.6(3)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:353) 下界是来源客人消息时间，上界是转交创建后 48 小时；任务执行再动态取最新 5 条。与 303、512 行“转交后的管家消息”不完全一致。

**合同反例**：客人 08:00 发问，管家 08:05 处理另一件事，08:10 才创建转交。现有条件会收入 08:05 的话，虽然发生在转交前。E7-5 为文字合同代理，尚未实现新 SQL。管理员点整理后再补拉一条旧时间消息，任务首次执行与重试的“最新 5 条”也可能不同；页面只携带 generation 无法说明草稿究竟依据哪一批消息。

**最小修订**：

1. 明确转交后窗口：保留来源本地消息编号条件，同时按管家 UTC sent_at 限定从 referral.created_at 开始到 +48 小时。平台时间按秒、本地创建时间可能更精细，须统一比较精度；同秒先后及跨时钟误差无法凭本地入库编号证明，边界来源提示管理员核对。若业务刻意允许转交前已有回答，应单独标为可供管理员选择的已有来源，不能静默称为转交后回答。
2. 管理员触发整理的事务里冻结本代次可用来源消息编号，最多 5 条；保存于现有草稿代次记录/任务的最小元数据，不复制人工原文。Worker 按这些编号取、净化并复核尚未清空，重试不自动换一批。
3. 把“最新”定义为发送时间和稳定编号的排序，晚到的旧消息不能只凭较大本地主键挤掉真正较新的回答。页面展示本代次实际采用的来源与截取提示；新到消息需要再次点整理、递增代次。
4. 输入长度复用现有模型预算，数量上限不等于字数上限；被截取/过滤掉的消息不能作为 source_quotes 的支持来源。

**验收**：转交前消息、来源之后但转交之前、窗口内发送且晚补拉、+48 小时之外、管理员触发后新增消息、六条以上与超长单条。固定代次的来源一致，清空后旧任务不再取正文。

## 3. 交接报告五个重点的答复与附加修订

1. **恢复标记位置**：独立表避免 clear_test_customer_data 重写 metadata，方向成立；尚未实现的表无法证明“没有路径删除它”。保留期和恢复消费位置应按 V7-R1 定义，不能用当前代码没引用新表证明未来安全。
2. **task_id 前缀**：当前 [WeComCallbackService.verify_and_enqueue](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/wecom_callback.py:120) 原样搬运 TaskId；前缀与现有 handoff- 不冲突。但当前 _card_for 没收到 task_id，且异常隔离不足。按 V7-R3 一次补用途路由、关联校验与失败边界。
3. **C1：引用子串并不证明草稿事实出自原话。** E7-C1：原话“401没有江景；只对这位客人免收停车费”，引用“401没有江景”，答案却写“401有江景，所有客人停车免费”，引用校验照样通过；空列表用 all 也会通过。最小做法是 found_answer=true 时要求非空、非空白引用，绑定本代次已发送的来源编号；页面把中英答案、实际引用、仅本客人的限定并列，入库明确确认两种语言及适用范围。中文也要人工审核。无需再加一次模型裁判，但把“证明事实出自原话”改为“证明引用片段来自本次输入”；语义、否定、金额与泛化正确性仍由管理员确认。
4. **问题标签截断**：30 字引用没有业务一致性问题；split_guest_reply 按 UTF-8 限额切正文，跨段引号属于显示问题。截断后再包引号、处理标签中的引号/控制字符、固定收尾，做一个长回复分段对照即可，不需要为了成对引号改全局切分器。[build_handoff_card](/Volumes/02/code/homestay-bot/src/homestay_bot/services/handoff_card.py:85) 输出普通文本 JSON，卡片与客人文字的转义方式不能混用。
5. **两阶段与门禁**：覆盖契约先完成相关验证，再接转交闭环，共用最终全量真实模型门禁与发布合理。必须仍等 Spec 确认及明确“开始”；候选门禁、提交和发布授权分别处理。

**C2 文档机械同步**：Spec 414 行仍写“新增两张表”，实际 383–385 行列三张；537 行还写部署 1.69.2，与 477 行新增功能建议 1.70.0 不一致；515–516 行旧 D-G14 文案误挂在 D-G20 后。修订时同步，不再保留容易误认成有效决策的旧表述。

**C3 当前知识校验已有变化**：独立占位符任务已在工作区给 KnowledgeAdminService.create/update/convert_candidate 加 [_validate_content](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:231)，并抽出 [validate_knowledge_content](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:415)。因此 Spec 148 行、交接 44 行“不拦截”的描述只属于写报告时旧基线，不能作为当前实现事实。新 referral 入库复用此校验及常量，再叠加自身凭据规则；不要再复制占位符常量或建第二套校验。本次核对了这部分共享差异，**没有把独立 INFO/Bearer 日志任务当作已完整审过**。

## 4. 收敛实施与综合验收建议

建议只修订三个实际共享位置，其他 v7 设计保留：

- **来源最终处理入口**：完成收据＋来源清空检查，一次结束整轮已提交处理，复用已持久化 outbox；不用冻结完整模型输出，也不重建出站序号映射。
- **转交草稿记录**：代次＋精确状态＋本代次来源编号；完成写回和入库共用资格。模型只整理待审草稿，引用存在与事实确认分别标示。
- **人工卡片边界**：purpose/task_id 路由、同会话关联、最小内容和显示失败隔离；共享净化补已证英文凭据格式。

综合验收围绕三个端到端问题组织，避免逐个字段堆无判别力测试：

| 问题 | 主验收 | 必须观察 |
| --- | --- | --- |
| 同一来源能否因重放/清空再产生不同结果 | 真实 Worker＋真实仓储，中断与 recover_stale；变化助手为禁止再次调用的对照 | 所有本轮副作用、最终正文、队列载荷精确一致；清空后无旧正文发送 |
| 草稿是否一直属于同一代次与来源 | 真实记录、暂停草稿模型替身、重放/换代/采用/关闭/清空交错 | 请求信封不含凭据，来源编号一致，迟到写回被拒，生成中不能入库 |
| 人工动作能否独立于资料显示失败完成 | 真实 HumanSessionService＋平台替身；普通与 referral 卡片 | 外部状态、本地模式、审计、结束任务；关闭后可交还，清空后不能旧接入 |

逐夜价格、逐项范围和混合问题继续使用 v7 已列出的正常/availability_fallback 对照；不用因本次报告再跑已覆盖、未变的旧探针。独立改写集必须在实现冻结后检验“换措辞、粗细拆分、问句顺序”对最终事实及路由的影响；历史反例修复集不能证明泛用性已达标。

## 5. 本轮验证与可复核证据

本轮仅运行一份小型离线探针：现有 outbox、人工会话、FAQ 写回采用真实类及内存 SQLite；平台替身；新 source_quotes 和时间窗口采用文字合同代理。退出 0 的含义是**反例得到确认**，不是 v7 已修复。

输出：

```text
E7-1 existing outbox: changed prefix -> guest jobs 2
E7-2 card rebuild failure: fake platform HUMAN, local BOT, audits 0
E7-3 existing generation CAS: same generation overwritten
E7-4 combined sanitizers unchanged EN/EN/ZH/ZH [True, True, False, False]
E7-C1 contract proxy: contrary answer passes quote-presence; empty quotes pass all
E7-5 contract proxy: pre-referral staff message included
```

复核入口，在仓库根目录运行（仅合成数据与内存库）：

```sh
PYTHONPATH=src .venv/bin/python - <<'PY'
import asyncio
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, func
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import Base, Conversation, Job, AuditLog, KnowledgeCandidate
from homestay_bot.domain.enums import ConversationMode
from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.services.handoff_card import HandoffCard
from homestay_bot.services.human_session import HumanSessionService
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.services.complaint_review_job import SQLAlchemyComplaintMessageContext
from homestay_bot.services.guest_reply_policy import redact_sensitive_guest_text

async def main():
    """仅以合成输入和内存 SQLite 复核 v7 所借用的现有边界，不实现新方案。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with factory() as s:
        conv = Conversation(open_kfid="probe-kf", external_userid="probe-guest")
        s.add(conv)
        await s.commit()
        cid = conv.id

        # 第一次走任务通知再发回复；重放的助手计划不登记任务，序号发生变化。
        first = TransactionalOutboxWeCom(s, source_message_id="synthetic-source",
                                      delivery_phase="final", conversation_id=cid)
        await first.send_handoff_card(agent_id=1, employee_userids=["probe-duty"],
                                      customer_id=None,
                                      card=HandoffCard(cid, "待确认任务", "", "/synthetic"))
        await first.send_text("probe-kf", "probe-guest", "第一次正文与核实说明")
        await s.commit()
        replay = TransactionalOutboxWeCom(s, source_message_id="synthetic-source",
                                       delivery_phase="final", conversation_id=cid)
        await replay.send_text("probe-kf", "probe-guest", "重算后的不同正文")
        await s.commit()
        bodies = list((await s.scalars(select(Job.payload).where(
            Job.job_type == "wecom_send_text").order_by(Job.id))).all())
        assert [b["content"] for b in bodies] == ["第一次正文与核实说明", "重算后的不同正文"]
        print("E7-1 existing outbox: changed prefix -> guest jobs", len(bodies))

        # 实际人工服务和审计仓储；平台替身接入成功后，重建卡片失败导致异常外抛。
        class FakeApi:
            def __init__(self):
                self.state = 1
                self.transfers = 0
            async def get_service_state(self, *_):
                return self.state, "probe-duty" if self.state == 3 else ""
            async def transfer_service_state(self, *_):
                self.state = 3
                self.transfers += 1
                return ""
            async def update_template_card(self, **_):
                raise AssertionError("不应运行到模板更新")
        async def broken_card(_):
            raise RuntimeError("synthetic referral lookup failure")
        api = FakeApi()
        svc = HumanSessionService(s, api, agent_id=1, duty_userids=["probe-duty"],
                                  card_for=broken_card)
        try:
            await svc.handle_card_action({"userid":"probe-duty", "agent_id":1,
                "key":f"accept:{cid}", "task_id":"referral-1", "response_code":"synthetic-code"})
        except RuntimeError:
            await s.rollback()
        else:
            raise AssertionError("现有 card_for 异常应外抛")
        audits = await s.scalar(select(func.count(AuditLog.id)))
        mode = await s.scalar(select(Conversation.mode).where(Conversation.id == cid))
        assert api.state == 3 and api.transfers == 1
        assert audits == 0 and mode == ConversationMode.BOT_ACTIVE
        print("E7-2 card rebuild failure: fake platform HUMAN, local BOT, audits", audits)

        # 同状态、同代次连续写回：真实 FAQ CAS 允许第二次覆盖，是复用模式的能力边界。
        candidate = KnowledgeCandidate(canonical_key="synthetic-cas", canonical_question="测试问题",
                                       category="测试", draft_generation=1)
        s.add(candidate)
        await s.commit()
        repo = SQLAlchemyFaqCandidateRepository(s)
        one = await repo.mark_draft_ready(candidate.id, {"answer":"first"}, expected_generation=1)
        await s.commit()
        two = await repo.mark_draft_ready(candidate.id, {"answer":"late"}, expected_generation=1)
        await s.commit()
        assert one is not None and two is not None and two.draft_payload["answer"] == "late"
        print("E7-3 existing generation CAS: same generation overwritten")

    # 按 Spec 指定的现有两个净化函数叠加。合成凭据不来自客户或环境。
    samples = ["Door lock password: Ab12_X", "Verification code: Q9r8",
               "房门密码：Ab12_X", "验证码：Q9r8"]
    unchanged = []
    for item in samples:
        out = redact_sensitive_guest_text(SQLAlchemyComplaintMessageContext._sanitize(item))
        unchanged.append(out == item)
    assert unchanged == [True, True, False, False]
    print("E7-4 combined sanitizers unchanged EN/EN/ZH/ZH", unchanged)

    # 以下仅为 v7 文字合同代理：引用存在不证明答案支持，空集合还会真空通过。
    source = ["401没有江景；只对这位客人免收停车费。"]
    quotes = ["401没有江景"]
    fabricated_answer = "401有江景，所有客人停车免费。"
    assert all(any(q in m for m in source) for q in quotes)
    assert all(any(q in m for m in source) for q in [])
    print("E7-C1 contract proxy: contrary answer passes quote-presence; empty quotes pass all")

    sent = datetime(2026, 10, 8, 0, 5, tzinfo=UTC)
    source_at = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    referred_at = datetime(2026, 10, 8, 0, 10, tzinfo=UTC)
    current_range = 2 > 1 and source_at <= sent <= referred_at + timedelta(hours=48)
    assert current_range and sent < referred_at
    print("E7-5 contract proxy: pre-referral staff message included")
    await engine.dispose()

asyncio.run(main())
PY
```

未运行业务 pytest、全量测试或真实模型门禁：v7 本轮只有文档；探针已足以判别上述设计前提。未验证新 v7 代码、PostgreSQL 竞争、迁移、后台浏览器、平台受理或客人收件，均不能称为通过。

文档静态核对：30 个本地链接均存在且行号有效；15 项当前文件摘要匹配；内嵌探针 AST 与已执行探针一致；新报告的 `git diff --check --no-index /dev/null <报告>` 没有空白错误（退出 1 表示新增文件有差异）。

## 6. 证据绑定及工作区归属

| 对象 | 当前 SHA-256 |
| --- | --- |
| Spec v7 | 78dee567d735b626b4ac7251ca5ba5fdee3bf82f6741b82a70f4b0a572f02602 |
| Claude v7 交接 | 64f1aee43340a2a7b05c0dbcda3fea02b0724353b9cb5a54dff5858bc47c8a5f |
| application.py | fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac |
| conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| human_session.py | b0f19102a007f0c4e262b730967eecb2a953f6feb2fc0f76570e1f32fc954c60 |
| complaint_review_job.py | 5bb8db52e6a21b323eb964fad4f883e1014a055ee8b054b799466c15b3e6c0b1 |
| guest_reply_policy.py | d032c5fb4cff586aa3072cd2b895ec211e4fe903f7a45cfe3d355c5928d3d702 |
| repositories/faq_candidates.py | f449f40c5198523b06aa8f3ed7c305f7b62c2d15f5bcb4cb94dffcc99cea99e2 |
| services/faq_draft_job.py | 426b781481586ba9bba1227041d8523c34727f2a3aa10b31a19ce67e4699df79 |
| repositories/jobs.py | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
| worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| deepseek_faq_drafter.py（已有独立改动） | e299cd8c9d01147c343e1186bc242ba21092fb898fff4d869145c11c4f55d8e6 |
| routes/knowledge.py（已有独立改动） | a831260ea4d34037b7e800d2905c1b30c7ce84b042d27f5afcdc6a0a4248dc3c |
| services/knowledge_service.py（已有独立改动） | f1960df6a34cde842899273c2c715cbfb0625c24fdc242c63196563a5d111260 |
| repositories/customers.py | 6748b2cabe6838d85666015d44b449e975c55e520c1cc98a956d2a16bd896b21 |

除知识占位符任务触及的 drafter/knowledge 两文件外，Claude v7 交接所列共享源码摘要仍匹配。占位符任务还修改了 knowledge_service；本文以当前差异重新核对，没有沿用旧文件摘要冒充当前。

本次业务交付只有此报告。既有 Claude/Codex 报告、Spec、占位符与日志代码和测试、.impeccable/critique 均保留；AOCI 托管资产按项目规则在报告稳定后维护。不得据本报告实施业务、提交、推送、部署或进行任何真实外部发送。
