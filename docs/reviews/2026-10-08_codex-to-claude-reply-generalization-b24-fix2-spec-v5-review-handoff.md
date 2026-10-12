# B24 fix2 Spec v5 深度复审与修改建议

- 审查方：Codex；日期：2026-10-08。
- 工作区：`/Volumes/02/code/homestay-bot`；HEAD：`80f9b2c309547c36c15cfd142d5dd30b98d87406`。
- 对象：[Claude v5 返回稿](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v5-review-handoff.md)、[fix2 Spec v5](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)、新增流程的真实调用链。
- 本轮只审查并写报告，没有实施业务代码，没有连接现有数据库或调用真实模型、Hostex、企业微信，没有提交、推送或部署。

## 1. 结论与本轮范围

**暂不通过。确认 2 项 P1、6 项 P2 的实施契约缺口，建议一次补齐后再确认 Spec。** 这些是“按当前契约直接实施会留下什么问题”的审查结论；v5 尚未实现，不能描述成新版已在线上发生故障。

v5 已把上一轮的粗粒度房号/日期继承问题纳入实际证据行核对，并保持合法工具结果、不借整轮知识池，方向成立。但新“未知剩余”检查仍可能删掉未知限定；新转管家流程增加了身份、持久化来源、知识范围、草稿生命周期与恢复重放边界，不能只按新增字段和页面来实施。

| 编号 | 优先级 | 需要补齐的根边界 |
| --- | --- | --- |
| V5-R1 | P2 | 路由正则的完整命中不能作为已回答内容删除 |
| V5-R2 | P2 | 入住、退房日期不能一律与入住日比较 |
| V5-R3 | P2 | STAFF 登录、CSRF 主体及对象权限缺少可运行入口 |
| V5-R4 | P2 | 转交必须绑定本地具体缺口，而不只是 item_id 和模型标准问法 |
| V5-R5 | P1 | 候选和答案来源没有确定性房源范围，可能跨房间归纳或启用 |
| V5-R6 | P2 | 人工核实与旧高频草稿共用候选时的来源和生命周期未定义 |
| V5-R7 | P1 | 现有 FAQ 脱敏不足以承接原问、人工答案和模型产物的新用途 |
| V5-R8 | P2 | 24 小时去重改变出站调用序号，会破坏提交后重放幂等 |

P1 针对可进入长期知识/外部模型的信任边界；P2 针对会漏答、误报缺口、使主要流程不可用或产生重复出站的可复现契约问题。下文把当前源码事实、离线观察和新流程推演分开。

## 2. 逐项发现、依据与最小修订

### V5-R1 [P2] 删除完整识别片段会吞掉尚未取证的限定

**依据。** Spec §2.1(2)，第 135 行要求去掉命中的房价、房态、目录“识别片段”后判断剩余。当前 [answer_policy.py::_STAY_INTENT_PATTERN](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:12) 中有 `有[^，。？?！!\s]{0,4}的房`；[deepseek_client.py::_should_force_property_catalog](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2272) 有 `介绍.*房`、`房间.*(?:介绍|详情|名称)`。这些正则用于开放查询，通配部分并没有获得事实证据。

**离线 E-V5-1。** 用当前真实正则代理“删除完整命中片段”：

- “有江景的房吗”删除“有江景的房”，剩余为空。
- “有朝南的房吗”删除“有朝南的房”，剩余为空。
- “介绍能看江景的房”被目录正则完整命中，剩余也为空。

这不是新版最终回复复现；它证明当前被指定复用的命中区间并不满足 v5 的删除前提。普通房态或名称列表不能回答江景、朝向限定。“没有剩余”可能是假结果，之后也没有缺口可供转交。

**建议。** 保留现有正则作查询信号，但剩余检查只消费真正由证据覆盖的操作/形式锚点；通配区间、属性和限制条件必须保留。不能拿完整路由命中区间当事实覆盖凭证。复用同一内部覆盖结果供正常 JSON、工具失败回退、目录与日期澄清使用，不给每个出口补“江景、朝南”词表。

**验收。** 中英文纯房态/纯目录正例不多发缺口；带任意未知修饰词的问法必须保留该修饰词的答案或未确认。分别走成功模型与 `availability_fallback`，断言最终正文，不只断言检测到房态。

### V5-R2 [P2] 日期端点角色被丢弃，正确住宿区间也会被判未覆盖

**依据。** Spec 第 130 行把所有绝对日期按数值与证据行的“入住日”比较。当前 [deepseek_client.py::_EXPLICIT_STAY_DATE_PATTERN](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:98) 识别日期文本；[_tool_reply_parts](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2477) 的证据同时保留 `target_date` 与 `target_end_date`。现有证据并非只有一个日期。

**离线 E-V5-2。** 合成问题“10月10日入住10月12日退房有房吗”，证据区间为 10 日至 12 日。真实提取规则得出 10 日、12 日；按 v5 的单端点比较得到 `[true,false]`。正确退房日会被当成另一个未查入住日。Spec D-G11 也承认“10月3日到5日”剩“到5日”，不能把日期区间尾部当新固定知识问题。

**建议。** 单个明确入住日与 `target_date` 比；明确退房日与 `target_end_date` 比；成对区间必须保留顺序与端点角色。复用已有合法住宿区间和原文位置，不建通用日期解析器。确实无法绑定的范围做针对日期的澄清/未确认，不能转 FAQ。静态候选也要应用同一日期边界，不能只校验第 133 行的房号。

**验收。** 正确入住/退房一对、倒置一对、同句两个不同入住日、跨月跨年、简写区间、相对日期无法证明的对照；正常出口与回退出口一致。不能用“任意日期等于任一端点”放行倒置或另一段住宿。

### V5-R3 [P2] “STAFF 登录后可填、复用现有 CSRF”目前没有真实支撑

**依据。**

- [employee_auth.py::require_employee_session](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/employee_auth.py:397) 要求 `admin_id/admin_session_version`，随后调用 `get_active_admin`；[employees.py::get_active_admin](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/employees.py:47) 只允许活动 ADMIN。
- [employee_login_submit](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/employee_auth.py:555) 建立的也是唯一管理员会话。
- [admin_form_csrf.py::csrf_subject](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/admin_form_csrf.py:66) 只读管理员主体。员工不能简单借用匿名主体或管理员编号。
- [customers.py::_current_admin](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/customers.py:149) 拒绝 STAFF；卡片上的“对话记录”链接也不能直接声称员工可用。
- [test_knowledge_routes.py::build_client](/Volumes/02/code/homestay-bot/tests/integration/test_knowledge_routes.py:190) 用测试登录入口及认证装配模拟 STAFF；其只读用例不能证明生产员工登录可达。

**影响。** Spec 第 253 行的新表单即使写对角色分支，也可能在登录/令牌阶段就被拒绝。为了让它通过而共享管理员密码、信任 Cookie 角色或放开 CRM，会引入新的授权问题。

**建议。** 在 Spec 中明确 STAFF 身份入口、会话失效、每请求活动员工复核、表单主体和核实记录的对象权限。保留管理员现有登录；若保留 STAFF 填写目标，优先复用 [WeComApiClient.get_employee_userid](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/wecom/api_client.py:500) 与本地 `get_active_by_wecom_userid` 能力，补齐受绑定的登录回调与员工会话，而非新增密码系统。OAuth 适配器已存在，不代表完整登录已存在；实际平台配置仍需后续独立验证。

建议 STAFF 仅访问其有权处理的核实记录，管理员可审核；具体采用通知收件人还是当前值班名单须写成明确规则。STAFF 页面提供最小核实上下文，不开放全部客户档案；对话链接按角色提供。员工核实权限与知识发布权限保持分别验证。

**验收。** 用真实认证/CSRF 装配的临时服务证明 STAFF 能进入并提交；未登录、停用员工、伪造角色、跨记录、跨主体令牌均拒绝。不要只覆盖测试登录替身。新增身份与服务注入会影响装配：Spec 第 330 行“预计不改 application.py”应重新评估。

### V5-R4 [P2] item_id 无法标识具体缺口，资格检查会漏转或错转

**依据。** Spec 第 241 行只定义 `{item_id,canonical_question,category}`，第 242 行却允许“该小句剩余内容”转交。粗粒度项可能同时有已覆盖价格、未覆盖静态限定，以及工具失败。当前 [FrequentFaqService._is_eligible](/Volumes/02/code/homestay-bot/src/homestay_bot/services/faq_candidate_service.py:153) 按整轮 intent/question 排除；[_EXCLUDED_PATTERN](/Volumes/02/code/homestay-bot/src/homestay_bot/services/faq_candidate_service.py:12) 含“多少钱、入住、价格、房态”等。

**推演。** “401多少钱，而且能看江景吗”作为一个项时，对整项沿用排除会拒绝江景缺口；改为只检查模型生成的“能看江景吗”，又失去原文和具体未确认结果的绑定。一个 item 含两个静态缺口时，也不知道标准问题对应哪一个。未确认还可能来自日期不明、范围不匹配、冲突、预算跳过或工具失败，不一定是资料库缺事实。

**建议。** 让模型从适配器本地已形成的缺口列表中选择稳定编号；每个编号绑定原文区间、所问范围和缺失原因。可以仅是现有内部覆盖结构的编号，不需要第二个规划器，也不增加对外 ReplyPart 协议。模型标准问法只用于整理，不决定“哪个缺口存在”或房源归属。

资格检查针对绑定的静态缺口原文执行；整轮硬风险仍优先。日期/交易/外部查询失败、范围歧义及已知冲突分别保留其澄清或人工复核路径，不能仅因为最终状态为 missing 就进入普通知识补充。同一源消息可有多个缺口，见 R8 的独立去重边界。

**验收。** 粗粒度与细拆计划的价格＋江景结果一致；一项两个缺口分别关联；模型改写掉排除词、引用不存在的缺口、把已回答内容标缺口均拒绝。工具后主模型失败时没有合法语义选择，就保留未确认，不额外调用模型补 referral。

### V5-R5 [P1] 只按标准问题复用候选，会把不同房间答案放进同一个归纳槽

**依据。** Spec 第 252 行转交记录没有房源、事实区间等来源范围；第 255 行让草稿生成器建议 global/property。当前 [faq_candidates.py::_canonical_key/get_or_create](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/faq_candidates.py:24) 只按规范化问题做全局唯一键；[models.py::KnowledgeCandidate](/Volumes/02/code/homestay-bot/src/homestay_bot/domain/models.py:654) 只有一个 `draft_payload` 和一个 `knowledge_entry_id`。

**离线 E-V5-4。** 当前真实仓储中，两次“房间能看江景吗”，即使 category 不同，也返回同一个候选编号。当前接口没有 property 输入，无法确定性地区分 401 与 402。

**推演。** 401 的“能看见”、402 的“看不见”可能写进同一候选草稿；后填答案可能替换先填来源。用户问房源限定时，不能让模型从规范化问法或一条人工答案推定“全店都这样”。管理员一键转换会把这个错误范围带入长期回复。

已有 [KnowledgeAdminService._validate_scope](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:207) 会校验范围结构/房源存在性；它不能证明 scope 与人工事实来源相符。默认 unreviewed 是有效保护，但若新页面把模型建议的 global 预填并一键提交，这道人工范围确认会被实质绕过。

**建议。** 转交保存本地已核验的 property_id/范围及所问日期；候选身份至少区分标准问题与明确房源范围。保留旧候选键兼容，迁移时不猜旧记录属于哪个房间。新草稿必须绑定具体 referral/人工答案版本，不能只绑定候选编号；一个房间的答案默认不推广全店。

范围、有效期可有模型建议，但发布必须由管理员明确确认；人工原话没有给期限时，不从客人住宿日期制造知识有效期。未知房源的答案仍待范围审核；跨房间合并需要明确人工选择，不能靠 category 或同一句标准问题自动决定。

**验收。** 两房同问相反答案、未确定房号、全店事实与单房事实、先后及并发填答、旧候选迁移、模型建议扩大范围的负例；分别检查候选、草稿来源和正式知识 property_id。不能只检查后台能看到一段正确答案。

### V5-R6 [P2] 新人工来源和旧高频草稿会争用同一候选，未填也可能走旧生成

**依据。**

- [FrequentFaqService._enqueue_trigger](/Volumes/02/code/homestay-bot/src/homestay_bot/services/faq_candidate_service.py:186) 会改变草稿代次并入队旧 `faq_draft_generate`。
- [FaqDraftJobService.handle/_generate_draft](/Volumes/02/code/homestay-bot/src/homestay_bot/services/faq_draft_job.py:135) 当前来源是 approved_knowledge 与 examples，不含人工回答；没有“待填写提醒”分支。现有提醒是在草稿处理后通知管理员，不是催管家填写。
- 现有 generation＋OPEN 的回写 CAS 是有效保护；[test_candidate_closed_during_model_call_discards_old_result](/Volumes/02/code/homestay-bot/tests/unit/test_faq_draft_job.py:368) 已覆盖关闭期间丢弃旧结果，不能说项目完全没有代次保护。
- [faq_candidates.py::convert/snooze/_clear_private_content](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/faq_candidates.py:372) 清草稿、示例并提升代次；删除知识还会重开候选。新增转交记录不在这些现有清理/失效链路内。
- [KnowledgeAdminService.convert_candidate](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:357) 目前只查 OPEN，[_require_candidate](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:509) 没有行锁；提交接口不携带草稿代次。

**推演。** 待填写 referral 复用到已有候选时，已排队的旧任务仍可能生成一个非人工来源草稿；人工任务与高频刷新轮流提高代次，互相废止或覆盖。管理员看见旧草稿后，另一个员工/任务换了来源，原页面仍能转换。候选被关闭或转换后，旧“待填写”页的首次提交也不是自动安全的。

**建议。** 在现有 job/service 中区分旧高频来源与人工核实来源，人工分支只读取不可变的 referral＋答案版本。接管该候选轮次时废止旧代次；旧任务不能生成或覆盖人工轮次。复用既有 expected_generation 校验，增加来源身份核验，而非另建通用工作流引擎。

补一张生命周期表：填写、个人专属标记、生成失败、候选关闭/转换、知识删除/停用/过期、重开各自怎样处理 referral 与在途任务。尊重已关闭/已入库候选，不能默默复用不可写草稿；需要重新归纳时应明确开启新轮次。不要把“现有提醒机制”当已实现的待填提醒：可先保留首次通知和后台待填列表；如确需定期催填，明确触发与去重，催填不调用草稿模型。

管理员转换在同一事务锁定并核对候选状态、预览的代次、来源答案版本，再建知识、转换候选并更新相应 referral；拒绝过期预览。其他来源记录不能一律标“已入库”，只有实际被采用的答案才有这项事实。

**验收。** 旧任务先入队→新 referral 待填→旧任务恢复；人工回答两次竞争；关闭/转换/删除时模型迟到；预览后换代再提交；生成失败重试；勾选只对客人成立不生成草稿、不出现在可一键入库集合。PG 验证真正的并发竞争与回滚，SQLite 只证明本地行为。

### V5-R7 [P1] “已脱敏”不能只复用当前 FAQ 四类标识过滤

**依据。** [FrequentFaqService.redact_example](/Volumes/02/code/homestay-bot/src/homestay_bot/services/faq_candidate_service.py:141) 过滤邮箱、手机、身份证和带标签订单号，截到 500 字；category 当前直接持久化。它不保护门锁密码、验证码、精确地址、链接或任意凭据。当前 [DeepSeekFaqDrafter._validate_safety](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_faq_drafter.py:56) 拒链接/缺专属占位，不是完整隐私过滤器。

**离线 E-V5-3。** 合成“房门密码是 2468；验证码：1357；地址：测试路12号”经真实 `redact_example` 完全保留。这个结果不表示所有这样的问题都会通过新资格检查；它证明该脱敏函数不足以兑现新的输入/存储边界。

**影响。** Spec 新增原问卡片、STAFF 页面、人工原话展示和“原问＋人工答案”外发草稿模型；数据用途和接收者都扩大。模型不建议入库发生在调用之后，不能补救已外发；管理员审核发生在存储/展示之后，也不能替代隐私最小化。人工输入同样可能夹带密码、个人安排或把一次性处理当固定政策。

**建议。** 在持久化/卡片展示/模型调用边界明确最小字段，原问、canonical、category、人工答案、模型所有文本字段分别经过适用校验。优先复用 [redact_sensitive_guest_text](/Volumes/02/code/homestay-bot/src/homestay_bot/services/guest_reply_policy.py:315) 和 [SQLAlchemyComplaintMessageContext._REDACTION_RULES](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_review_job.py:40) 已有能力，并说明已知覆盖范围；不把正则脱敏说成任意个人信息识别。

门锁、入住凭证、验证码等专属安全内容禁止进入普通 FAQ 生成/发布链；管家“只对客人成立”是本地硬约束，不能由模型分类改回可入库。只发送绑定缺口与必要核实答案，不发送整个会话。输出再校验，审计仅记编号/动作/版本，不复制正文。

**验收。** 合成敏感字段同时放在原问、标准问题、category、人工答案和模型输出中，分别断言持久化、员工卡片与假的模型实际请求载荷；个人专属勾选后断言模型调用 0。保留房号、正常金额/公共地点等反向用例，避免把业务证据一并删掉。

### V5-R8 [P2] 业务冷却和来源重放必须分别幂等，不能只“沿用现有去重键”

**依据。** Spec 第 248–249 行要求 24 小时只转一次、提交后重放不重复出站。当前 [TransactionalOutboxWeCom._outbox_id](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:615) 的键依赖 `source_message_id:kind:sequence`；sequence 是同一 sender 上所有动作的递增序号。首次先登记卡片再发客人回复，恢复时因 referral 冷却跳过卡片，会改变客人出站序号。

**离线 E-V5-5。** 真实内存 SQLite＋真实 outbox，首次 card→guest 提交，恢复时同一来源/阶段只走 guest，得到 **员工卡片 1、客人出站 2**。这是新控制流与当前 adapter 的组合代理，不是 v5 全链实现验收。当前 [process_recorded_message](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1027) 会复跑最终阶段；[has_newer_conversation_activity](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/conversations.py:427) 排除 BOT，所以不能用已有机器人回复当然阻止恢复。

另一个现有约束： [KnowledgeCandidateOccurrence.source_message_id](/Volumes/02/code/homestay-bot/src/homestay_bot/domain/models.py:701) 全表唯一。[add_occurrence](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/faq_candidates.py:105) 的真实 E-V5-4 显示：同一消息两个不同候选只记第一条，第二条返回 false。新增逐缺口转交不能把这个“每条消息一次”的契约直接当“每个缺口一次”。

**建议。**

1. 来源重放按来源消息＋本地缺口身份幂等；24h 冷却按会话＋标准事实＋确定范围幂等。新来源重复问与同一来源重放分别处理，重放复用首次持久结果，不把它改成新的“已在核实”出站。
2. 新卡片用固定 referral 身份/用途的独立阶段命名空间，复用现有事务 outbox，不能让“这次是否通知”影响原客人回复键。无需重写全局 outbox。
3. 新增逐缺口出现键，明确 legacy 单消息统计兼容；数据库唯一约束保证两个缺口均可记录，重放各自仍一次。
4. 24h 检查与创建原子化（可复用会话活动锁＋短事务）；失败入队不消耗成功冷却。区分通知已入队、失败、员工已填写/关闭，不能对失败或已结束事项一律说“已在请管家核实”。
5. 转交写入安排在过时/原生接管复核之后、最终文案组合之前。转交局部失败时保存点回滚该项，正常已知答案和客人中性回复仍可入队；外层事务失败则 referral、卡片、回复均回滚。现有 [_track_frequent_faq](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1951) 位于客人回复之后，不能原样承担“依通知结果决定文案”的新流程。

**验收。** 必须增加最终回复作业的“业务事务提交后、Worker 标记完成前”窗口；取消退出→第三会话确认→真实 recover_stale→重领执行，检查每个缺口、通知、客人回复的精确键和数量。现有 Spec §2.4 的 debounce/fast_ack 窗口尚未生成 gap_referrals，不能只在那里增加 referral=1 断言。另测两个缺口、混合服务登记、通知失败后恢复、24h 过界、并发同问、员工提前接管/新消息到达。

## 3. 对 Claude 六个重点问题的答复

| 返回稿问题 | 本轮结论 |
| --- | --- |
| “明天401有房，402呢”继承 | 402 必须用 402 证据，不能继承 401；日期只有在原文关系可证明时继承。v5 的显式房号约束方向正确，仍需 R2 的区间角色合同。 |
| 剩余形式词校准 | 接受漏识别时保守未确认的能力上限，但先修 R1 的通配吞限定。日期端点、静态条件不是可随便删除的形式词，不能为正例通过扩删。 |
| 本地未确认＋模型 canonical；脱敏 | 不足。需 R4 的具体缺口选择与 R7 的输入/输出最小化；“模型认为适合沉淀”不是发布授权或隐私判断。 |
| 同事务和 PG 计数是否足够 | 同事务是基础；还需 R8 的稳定操作身份、24h 原子门及最终作业恢复窗口，以及 R6 的答案/发布竞争。 |
| STAFF 可填但不能发布是否一致 | 角色职责一致，现有生产认证/CSRF 不支持这条路径。需 R3 的完整入口与对象权限，不能用知识页测试替身证明。 |
| 拆两步实施、共用发布是否可行 | 可在同一确认后的 Spec 中先完成共享覆盖，再完成核实闭环；每步应可运行、可验证。不能把新字段先上线而表单、身份或来源未接好，也不能沿用“无需装配变更”的旧验证假设。 |

## 4. 一次修订应冻结的最小合同

这是对已提出功能的补齐，不建议再加规划器、领域别名库或通用日期解析系统。

1. **事实覆盖**：事实种类＋原文限定＋房源＋日期区间共同绑定实际证据；查询信号只能证明有请求，不能消费未知限定。保留已有正确结果，仅为剩余给答案/澄清。
2. **选择题式转交**：模型只能选择本地已确认存在的静态缺口编号并整理标准问法；本地决定可否转、范围与实际通知结果。不要让模型填空生成一个无法绑定原文的新问题。
3. **知识来源**：核实记录绑定原文、确定范围和人工答案版本；候选按事实范围隔离；草稿只格式化该版本；范围/期限最终由管理员确认。
4. **生命周期**：旧高频来源与人工来源分别守代次，关闭/转换/重开失效一致；个人专属和凭证禁止进入普通入库链；发布检查预览版本与采用来源。
5. **出站与权限**：来源重放与业务冷却分别去重；新通知使用稳定身份；STAFF 身份与对象权限真实可达，管理员独立发布；guest 文案由实际持久结果生成。

以下建议可能改变已选择业务体验，须写进 Spec 的决策段，不能在编码时默默处理：STAFF 登录方式与核实记录可见范围、非 OPEN 候选的新核实如何开轮次、是否真的需要定期催填、管理员一键入库前怎样确认范围。其余是兑现当前承诺所需的工程约束。

已接受的能力上限继续保留：D-G8 字面同一对象判据的同义漏检/异主体误拦；无计划只用旧整轮路径；工具后模型失败且没有合法转交选择时只保留未确认。不能把这些上限当作本轮新缺陷再增加框架。

## 5. 证据、验证与未覆盖

### 5.1 本轮实际执行

- 完整读取 v5 返回稿与 Spec；追踪 coverage→本地缺口→会话文案/任务→事务 outbox→FAQ 候选→草稿→转换→权限/CSRF/装配，并检索手册知识/隐私/消息边界。
- 下面离线探针退出 0，输出 E-V5-1–5；其中 E-V5-1/2 是明确标注的契约代理，E-V5-3/4 使用当前真实函数/仓储，E-V5-5 使用真实 outbox 的新控制流代理。**退出 0 表示缺口被观察到，不表示修复通过。**
- HEAD 与 SHA-256 重新核对；相关业务源码与上一轮相同。上一轮 93 项相关回归没有重跑，仍只证明当时三个指定测试文件，不能证明本次新增核实闭环。
- 本轮不运行全仓 pytest、真实模型门禁或 PG 测试。没有业务修改；现有纯离线源码/内存探针足以判定这些契约问题，不能用更多无关绿测替代。
- 报告的本地链接、Python 语法和空白检查单独执行；AOCI 收尾另记工具结果。

### 5.2 尚需实施后补的证明

- v5 实际最终客人正文、日期/范围与所有出口一致性。
- 真实临时服务的员工认证、CSRF、核实页和管理员过期预览拒绝；手机提交失败保留输入、重复提交反馈、无脚本提交。
- PostgreSQL 迁移、填写/转换竞争、失败回滚与真实 Worker 恢复；不能用 SQLite 替代行锁证据。
- 公共回复和主模型变化后的 `REPLY_GATE_SCOPE=all`，真实调用须当次授权；新草稿模式的事实保真另外覆盖。
- 平台配置、员工卡片实际可点、人工接入/回复与客人实际收件。通知入队不表示员工收到或客人已获得核实答案。

### 5.3 可重复的离线命令

所有文本与数据库都是合成数据。运行只创建内存 SQLite，不读取应用配置或现有数据库，不发送外部请求。脚本未另写到业务目录。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
import ast
import asyncio
import inspect
import json
import re
import textwrap
from datetime import UTC, datetime

from sqlalchemy import func, select
from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import Base, Job, KnowledgeCandidateOccurrence
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant, _EXPLICIT_STAY_DATE_PATTERN
from homestay_bot.repositories.faq_candidates import SQLAlchemyFaqCandidateRepository
from homestay_bot.services.answer_policy import _STAY_INTENT_PATTERN
from homestay_bot.services.faq_candidate_service import FrequentFaqService

# 仅代理 v5 的“删除完整命中片段”，不声称是尚未实现的覆盖函数。
for q in ["有江景的房吗", "有朝南的房吗"]:
    hit = _STAY_INTENT_PATTERN.search(q)
    remainder = (q[:hit.start()] + q[hit.end():]).strip("吗?？")
    assert remainder == ""
    print(json.dumps({"case": "E-V5-1", "question": q, "removed": hit.group(), "remainder": remainder}, ensure_ascii=False))
tree = ast.parse(textwrap.dedent(inspect.getsource(DeepSeekGuestAssistant._should_force_property_catalog)))
call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "search")
catalog_pattern = ast.literal_eval(call.args[0])
q = "介绍能看江景的房"
hit = re.search(catalog_pattern, q, re.I)
assert hit.group() == q
print(json.dumps({"case": "E-V5-1-catalog", "removed": hit.group(), "remainder": ""}, ensure_ascii=False))

# 代理 v5 将全部绝对日期与入住日比较；真实日期提取规则和合成证据范围。
q = "10月10日入住10月12日退房有房吗"
dates = _EXPLICIT_STAY_DATE_PATTERN.findall(q)
assert dates == ["10月10日", "10月12日"]
coverage = [tuple(map(int, re.findall(r"\d+", value))) == (10, 10) for value in dates]
assert coverage == [True, False]
print(json.dumps({"case": "E-V5-2", "absolute_dates": dates, "evidence": ["2026-10-10", "2026-10-12"], "compare_only_check_in": coverage}, ensure_ascii=False))

# 使用合成文本，检验当前 FAQ 脱敏的具体覆盖范围。
raw = "房门密码是 2468；验证码：1357；地址：测试路12号"
redacted = FrequentFaqService.redact_example(raw)
assert redacted == raw
print(json.dumps({"case": "E-V5-3", "credential_and_address_preserved": redacted == raw}, ensure_ascii=False))

async def main():
    # 新建内存 SQLite；不读取应用配置或现有数据库，不调用外部 API。
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            repo = SQLAlchemyFaqCandidateRepository(session)
            a = await repo.get_or_create(canonical_question="房间能看江景吗", category="设施")
            b = await repo.get_or_create(canonical_question="房间能看江景吗", category="房源")
            assert a.id == b.id
            c = await repo.get_or_create(canonical_question="健身房几点开放", category="设施")
            one = await repo.add_occurrence(a.id, source_message_id="synthetic-two-gaps", occurred_at=datetime.now(UTC), example="合成缺口甲")
            two = await repo.add_occurrence(c.id, source_message_id="synthetic-two-gaps", occurred_at=datetime.now(UTC), example="合成缺口乙")
            assert (one, two) == (True, False)
            await session.commit()
            count = await session.scalar(select(func.count()).select_from(KnowledgeCandidateOccurrence))
            print(json.dumps({"case": "E-V5-4", "same_canonical_same_id": a.id == b.id, "two_gaps_added": [one, two], "occurrences": count}))
        # 代理“提交后恢复，24h 去重使员工通知被跳过”的调用序列。
        async with factory() as session:
            first = TransactionalOutboxWeCom(session, source_message_id="synthetic-referral", delivery_phase="final", conversation_id=999)
            await first.send_internal_card(agent_id=1, employee_userids=["synthetic-staff"], title="资料待核实", description="合成问题", url="https://example.invalid/answer/1")
            await first.send_text("synthetic-kf", "synthetic-guest", "已请管家核实。")
            await session.commit()
        async with factory() as session:
            replay = TransactionalOutboxWeCom(session, source_message_id="synthetic-referral", delivery_phase="final", conversation_id=999)
            await replay.send_text("synthetic-kf", "synthetic-guest", "已在请管家核实。")
            await session.commit()
        async with factory() as session:
            kinds = list((await session.scalars(select(Job.job_type).order_by(Job.id))).all())
            assert kinds.count("wecom_send_internal_card") == 1
            assert kinds.count("wecom_send_text") == 2
            print(json.dumps({"case": "E-V5-5", "job_types": kinds, "staff_cards": 1, "guest_outboxes": 2}))
    finally:
        await engine.dispose()

asyncio.run(main())
PY

```

### 5.4 本轮证据摘要绑定

| 文件 | SHA-256 |
| --- | --- |
| `docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md` | `744af02d90c28748d8e5039388187584cae9377c90c19eb1aa7861ff758af147` |
| `docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-v5-review-handoff.md` | `690d43fae2205dd3cb03e9485fbedc0c690fc278f320e23bc164e6c5c0b5f68a` |
| `docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v4-review-handoff.md` | `7c9a91bc24210c3a4dcefdc558fd8ec2663269322014634133605c5a6028d752` |
| `src/homestay_bot/integrations/deepseek_client.py` | `430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd` |
| `src/homestay_bot/services/answer_policy.py` | `01561d21304f2058132de2ba7603ca4ae20546d949417f929ebbbd2143a4e4fe` |
| `src/homestay_bot/services/conversation_service.py` | `1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878` |
| `src/homestay_bot/services/faq_candidate_service.py` | `b348f1a4b58919090615fe06479fe9a43c4cb0d5d0fa446725d47a03847f8517` |
| `src/homestay_bot/services/faq_draft_job.py` | `426b781481586ba9bba1227041d8523c34727f2a3aa10b31a19ce67e4699df79` |
| `src/homestay_bot/repositories/faq_candidates.py` | `f449f40c5198523b06aa8f3ed7c305f7b62c2d15f5bcb4cb94dffcc99cea99e2` |
| `src/homestay_bot/routes/employee_auth.py` | `d396d410afb4b5fe15c146dda6bb633052dc7be42ef63591034df8f745583f2e` |
| `src/homestay_bot/routes/admin_form_csrf.py` | `1b428787ae3c007c2a35734ba6cf710abdf4a474bfe14917eb0718e3c87fe855` |
| `src/homestay_bot/routes/knowledge.py` | `d03f246a224fdcd90db2bb8dd883617b8ce7c91c25092cf8c7e5ed28ee499fe1` |
| `src/homestay_bot/integrations/deepseek_faq_drafter.py` | `db3cf06d7f448829ae5285607a87255fc5b42dc4e0ebced8ecdeab05fb193526` |
| `src/homestay_bot/services/complaint_review_job.py` | `5bb8db52e6a21b323eb964fad4f883e1014a055ee8b054b799466c15b3e6c0b1` |
| `src/homestay_bot/application.py` | `fbcd22df35cadfac887cff8048549cd16b0b81b0dce2f61b0d2adaee65bdcdac` |
| `src/homestay_bot/domain/models.py` | `f906c9297f9fa71ff2c25dfdd1afad2ba32b08eaa8f5e3198894595322f1e27b` |
| `tests/integration/test_guest_reply_postgresql.py` | `33151cd8f979211b88f0218c85777f5166b8e14d16ba712abe952325a380b440` |

摘要只绑定本地审查输入，不证明生产/容器状态；被审文件变化后应只重核受影响结论。

## 6. 工作区归属与交接动作

本轮新增本报告；AOCI 托管索引/基线可为此更新，与业务代码区分。进入本轮时已有 Claude/历轮审查文档、Spec、`.impeccable/critique/` 和 AOCI 改动，均保留，不能把整工作区归为本轮产物。

Claude 接手应按 V5-R1–R8 一次完善共享覆盖、缺口身份、身份入口、范围/来源、生命周期和幂等合同，给出相应代码接线符号及有判别力的验收矩阵。既有已接受上限保留；若反驳某项，提供能阻止该反例的真实分支或更明确的契约，不能只复跑上一轮探针。

本报告不修改 Spec，不授权业务编码、真实模型/平台调用、提交、推送、迁移现有数据库或部署。完成 Spec 确认后仍按项目规则等待本次明确“开始”。
