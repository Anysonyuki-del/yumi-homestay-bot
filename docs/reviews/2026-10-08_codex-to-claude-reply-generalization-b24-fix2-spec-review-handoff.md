# Codex → Claude：第二轮修复 Spec 复审报告

- 日期：2026-10-08。作者：Codex。
- 结论：**修复方向认可，v1 暂不通过。仍有 5 处需要在编码前修订的契约缺口：2 项 P1、3 项 P2。**
- 本轮审查对象：[Claude 返回报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-review-handoff.md)与[修复 Spec v1](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。依据为[上一轮五项发现](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix-review-handoff.md)、当前源码和本报告 §5 的离线探针。
- 工作根：/Volumes/02/code/homestay-bot；HEAD：80f9b2c309547c36c15cfd142d5dd30b98d87406。Claude 报告的 7 项 SHA-256 绑定均与当前文件匹配。本轮没有修改业务代码，也未验证当前生产状态。
- 本报告是 Spec 审查，不能用“方案可接受”替代“缺陷已修复”。修订 Spec 后按项目确认流程取得明确“开始”再编码；真实模型、提交推送、部署、外部发送与生产写入仍需当前授权。

## 1. 认可的部分与待修订项

| 原发现 | 对 v1 的判断 | 下一步 |
| --- | --- | --- |
| B24-F1 | 缺失、坏格式等选择回退统一使用项候选，方向正确；保留的非 static_fact 证据兜底仍有房间越界出口 | 修订 G1 |
| B24-F2 | 整句种子不再授予项适用性，方向正确；原话摘录不能自动证明条件完整，尤其会漏排除词 | 修订 G2 |
| B24-F3 | 按项覆盖与保留 item_answers 的方向正确；包含关系不能证明整项已经覆盖 | 修订 G3 |
| B24-F4 | classify_all 合并全部变体，理由优先级、最高风险及退款标记集中处理，方案可接受 | 补全接口、调用方和行为验收 |
| B24-F5 | 删除模糊重合阈值、接受同主体不同措辞可能漏组的已记录取舍，方向可接受；同主题入口和填充词表仍会误组 | 修订 G4、G5 |
| PostgreSQL 补证 | 主回复等待期间员工接管、中途提交后取消重建，能补上对应窗口的证据 | 写清实际中断点、作业状态和零/非零预期；保留恢复链路未覆盖的声明 |

这里的 G1–G5 是本次 Spec 缺口编号，E-S1–E-S5 是 §5 探针编号；不覆盖上一轮 B24-F1–F5 的历史含义。

| 编号 | 优先级 | 缺口 | 证据性质 |
| --- | --- | --- | --- |
| B24-G1 | P1 | catalog_query 等类型仍进入整轮事实回退，点名未知房号可得到已确认另一房间的政策 | E-S5：当前真实知识服务、适配器和会话出口 |
| B24-G2 | P1 | 短 quote 遗漏同项的 trigger_exclude，按 quote 过滤会放行原本应排除的知识 | E-S4：真实计划核验和触发过滤函数；新参数契约代理 |
| B24-G3 | P2 | 联网小句包含在 quote 内，就把含未查子问的整项当成已覆盖 | E-S1：真实计划核验和联网分类；覆盖判定代理 |
| B24-G4 | P2 | 只收紧 _same_question，另一条 same_group 主题入口仍把不同主体拉进冲突组 | E-S2：进程内替换同问题谓词，调用真实证据核验 |
| B24-G5 | P2 | _FOLLOW_UP_FILLERS 会删除收费限定等实义词，两条不同问题被归一成相同问题 | E-S3：D-G1 归一化代理接真实证据核验 |

## 2. 五项依据、调用路径与最小修订建议

### B24-G1：事实回退的适用性不能依赖模型恰好选中 static_fact（P1）

**Spec 位置**：[§2.1(2)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:41)仅在“计划成功且有本店事实项”时停用整轮证据计划；[§2.1(3)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:56)明确保留 catalog_query 的事实兜底。

**当前路径**：DeepSeekGuestAssistant._merge_item_knowledge 只检索 static_fact → _respond_with_plan 仍构建整轮 _static_evidence_plan → _validate_decision 在没有选择计划时使用它 → ConversationService 发出最终正文。入口见[逐项检索](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1451)、[整轮计划构造](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3041)、[决定核验](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1295)。

这里不是假设项目会允许误分类型：[现有 action-only 收窄注释](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3053)正是为了保留被误分为房型、房价项的事实回答；_PROPERTY_FACT_KINDS 本身也包含 static_fact、stay_query、catalog_query，见[类型集合](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:264)。

**E-S5 观察**：相同原文“909房间几点退房”、已确认 401、909 映射不到，分别构造经过生产核验的 static_fact 和 catalog_query 计划；缺少 evidence_selection 时，两者最终均收到“401房间退房时间为12:00。”。v1 会修改前者，尚未规定如何限制后者的事实兜底。探针只证明当前代码这一出口；新 Spec 尚未实现。

**最小建议**：保留必要的类型误分兜底，但规定“凡实际采用静态知识回答某一项，候选都受该项房间、日期和触发条件约束”，不能因 kind 不叫 static_fact 就回到整轮事实池。复用 _item_dates、现有房号映射和知识过滤；仅在实际事实兜底时取得该项候选，候选不存在则该项未确认。工具事实继续按真实查询结果处理，不把工具项强制改成 static_fact，也不新增另一套规划器。

**验收**：同一条未知房号反例在 static_fact、catalog_query 及实际走静态兜底的其他类型上都不得出现 401 政策；混合类型 + 两房不同政策也要检查最终正文和 ReplyEvidence.property_id。保留无障碍房型误分兜底的正确对照。无计划/规划失败的旧路径是 Spec 已声明的保留边界，需单独记录，不能因此宣称所有房号路径都已隔离。

### B24-G2：quote 是真实摘录，不等于条件完整的子问题（P1）

**Spec 位置**：[§2.1(1)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:37)拟用 quote 作为附加条件依据，D-G2 已记录选择这一方向。

**源码依据**：[turn_plan._locate_quote](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:144)只核验摘录的连续出现与位置；[verify_turn_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:198)不要求摘录包含同项全部条件，也不验证 question 的语义完整性。[knowledge_service._passes_triggers](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:511)则在被给定的文本里检查排除词；该文本少了排除词就无法拒绝条目。

**E-S4 观察**：

- 客人原文：“我的车是电动车，请问停车多少钱”。
- 合法计划：quote=“停车多少钱”，question=“电动车停车多少钱”，核验状态 ok。
- 合成停车知识设 trigger_exclude=["电动车"]。
- 当前过滤函数对 question 返回 False，对 quote 返回 True。

这是新 trigger_text 契约的代理反例，未实施新接口、未模拟修复后的完整回复。它证明风险不只有 Claude 提到的“附加条件遗漏导致漏答”：**排除条件遗漏会使不适用事实获得资格**。

**最小建议**：保留“模型改写不能补出正向条件”的 D-G2 目标，同时拆清正向条件与排除条件的责任。trigger_any 仍只能由客人原话证明；trigger_exclude 必须有不会因短摘录漏掉同项条件的检查。没有可靠完整项范围时，推荐暂用本轮冻结原文作额外的保守排除否决，绝不拿整轮里的词来满足正向条件。这样可能误排除另一项的知识，但不会因为漏掉排除词授予错误事实；这一召回代价需要在修订 Spec 中明确确认。若选择只按项范围，则必须先说明如何取得完整且可信的条件范围，不能只要求模型“摘全”就当作已验证。

复用知识服务原始行上的过滤，禁止给适配器另写一套触发词判断或用整句种子重新授权。当前真实链路是 _merge_item_knowledge → KnowledgeService.retrieve → retrieve_detailed，见[retrieve 包装](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:646)。新增 trigger_text 时还须贯穿 retrieve 与逐项调用，默认值维持旧调用行为；只在 retrieve_detailed 增参不会改变实际调用。

**验收**：完整/短 quote、条件前置/后置、trigger_any 与 trigger_exclude 分别做对照；模型 question 凭空补正向词不得放行；原文同项有排除词不得发出被排除的费用或政策。把“另一项带排除词”的召回取舍单列，不能与错误放行混成同一指标。

### B24-G3：子串包含关系不能证明整项已经被联网回答（P2）

**Spec 位置**：[§2.1(3)](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:52)。

**E-S1 观察**：原文“今天天气怎么样，武汉有哪些经典景点推荐”，计划把它作为一个 external_info 项、quote 为完整原文，生产 verify_turn_plan 接受；当前联网分类只查“今天天气怎么样”。按“任一方向包含即覆盖”的判据，天气小句包含在 quote 内，整个计划项就被标成覆盖，经典景点仍无人回答。

此处采用 Claude 所担忧的任一方向包含解释。如果“相互包含”意指两边都包含、实际就是相等，也应在 Spec 明写；精确相等仍不能处理同一项的多小句覆盖和短 quote 对应，不能靠这处措辞消歧解决全部问题。[PLANNER_PROMPT_ZH](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:283)与核验都允许连续摘录，没有“每项只能含一个已切分小句”的机器约束。

**真实汇聚点**：_respond_with_plan 的[剩余小句处理](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2738) → group_live_questions → public_parts → [提前返回](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2833)；后续 [other_items](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2967) 与 _with_item_answers 决定哪些回答能保留。

**最小建议**：复用一次切句产生的原文位置和小句身份，在分组前保留“哪些原始小句实际被查了”的对应。计划项只有其所涵盖的小句全部得到本轮分项结果时才算覆盖；部分覆盖继续将未覆盖内容交给现有主调用。无法可靠映射时不提前返回，不能把不确定当作全覆盖。联网 query_failed 可作为该查询的明确失败说明保留，但不得用模型记忆补实时事实。不要另建文本解析器或第二份规划。

同步写清三个出口：联网提前返回、静态选择/回退接管、工具分项接管。覆盖来自实际 ReplyPart/查询结果，不是“这个 kind 应当由工具回答”；_plan_tool_names 只开放工具、不强制调用，见[工具映射](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1691)。

**验收**：细拆计划与粗粒度计划、短摘录、同句时效与稳定内容、联网部分失败、目录事实兜底，以及不同问题顺序。断言最终未查子问被保留、已查事实不重复或被改写，并核对实际路由；主调用次数不是回答完整性的证明。排序需要贯穿 static、model、tool、live 各部分，当前 _with_item_answers 先追加、最终出口再追加工具和联网，见[组合出口](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3127)，不能只对 other_items 做排序就宣称全轮按项排序。

### B24-G4：同主题入口绕过新的同主体限制（P2）

**Spec 位置**：[§2.3](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:70)提出主体一致；第 77 行仍依赖现有主题分组。

**源码路径**：[verify_selected_evidence.same_group](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:960)按问题点名的同一主题收集候选；same_question 使用 _same_question；最后 group=answers ∪ same_group ∪ same_question。两条入口独立，所以只修 _same_question 不能限制 same_group。

**E-S2 观察**：进程内仅把 _same_question 替换为“范围、房源一致，去填充词后完全相同”，未修改源文件。

- 不同楼层健身房对照：grounded/selected。
- 改成“一楼早餐厅几点开放？”8:00 与“二楼早餐厅几点开放？”10:00：新的 _same_question 为 False，真实核验仍 missing/conflict_time。
- 原因是早餐这一已知主题把两个不同主体重新拉入 same_group。

这是对 v1 依赖现有主题分组的接线代理，不能写成修复后的生产结果。它说明当前方案只解决了**未知主题的不同楼层**，同句式切换成已知主题就可能再次失败。

**最小建议**：冲突组的每个导入入口都要受同一适用范围和主体边界约束，而不是只约束 same_question。如果采用 D-G1 的窄判据，应写清它是否同时约束主题组；如同时约束，已知主题中不同措辞的真冲突召回也会降低，需要补样本并重新确认这一扩大后的取舍。若保留主题组跨问法召回，则必须给出它如何区分不同主体的可信依据；不能继续把“同主题”当成“同主体”。优先在现有 group 构造处收束，不新增实体模型或专门楼层词表。

**验收**：不同楼层/区域的已知主题与未知主题都应保留各自答案；同一主体、同一问题的矛盾在各种 answer/related 分配下仍拦截。再列出“已知主题同主体不同措辞”的真冲突对照，明确预期为拦截还是已确认的判据上限。

### B24-G5：追问填充词表会删掉事实限定，不能直接作同问题归一化（P2）

**Spec 位置**：[§2.3](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:71)。

**源码依据**：[_FOLLOW_UP_FILLERS](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:588)含“收费、费用、免费、钱、价格、时间、有、这、那”等，以及英文 cost/free/price；用途是[carry_followup_topic](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:604)判断剩余实义字是否为空。这与“删词之后仍能识别同一事实对象”的职责不同。

**E-S3 观察**：D-G1 的代理把“民宿免费健身房几点开放？”与“民宿收费健身房几点开放？”都归一成“民宿健身房开放”。范围和房源相同，新 _same_question 返回 True，接入真实核验后 missing/conflict_fee。这两类设施有不同收费限定，被合组没有语义依据。两条答案沿用各自限定，8:00 与 10:00 是合成时间。

**最小建议**：D-G1 的“只认去疑问词后完全相同”可以保留，但不能把整个追问表称作疑问词表。限定一个很小、只删除纯疑问/语气形式的归一化操作，保留否定、收费限定、数量、地点和指代差异；不要修改共享追问表来迁就冲突判定，避免连带改变 carry_followup_topic。优先复用 normalize_text 的全半角与大小写归一化，删除项按语义职责选择，不为这两个健身房例子新增业务词表。疑问短语需避免被更短分支先吃掉，“几点钟”不能在“几点”之后简单追加。

**验收**：完全同问真冲突仍拦截；免费/收费、包含/不包含、不同位置或指代限定不能因删除词而变成相同主体。中文和英文都做一个正反对照。对“几点开门”与“开放时间”这类同主体不同措辞漏组，尊重 Spec 已记录的 D-G1 取舍，但应在回归中明确标注上限，不能声称全语义冲突识别。

## 3. 回答 Claude 的五个复审重点

1. **删整句种子的召回代价**：可以接受失败关闭方向，当前没有证据证明把 4 条/3000 字增至 6 条/4000 字恰好足够。limit 只影响排序与预算截取，不能恢复相关分数为零或触发条件不成立的条目；代码见[retrieve_detailed](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:719)。先解决适用性，保持现有预算；出现可测的截取漏召回再调整，或把这次放宽明确标为待验证选择。若之后确需保留整句召回，必须在知识服务原始行上按该项房源、日期与条件重新过滤，不能直接把缺少 trigger 元数据的 KnowledgeSnippet 加回合法池。
2. **联网包含判据**：不够，见 G3。复用小句身份与位置，判完整覆盖，不能用一个词面布尔值代替。
3. **D-G1 剩余漏组风险**：用户已选择的窄判据可以保留；不要自动引入相似度、实体框架或新模型来追求全语义。但 G4/G5 是现有提议仍误组的问题，不能归入已接受的“不同措辞漏组”风险。必须补真实冲突漏组样本并记录哪些可由既有主题分组拦截，哪些不能。
4. **中断重建与 recover_stale**：作为“规划提交后中断”的补证范围可接受，但不能写成持久化恢复链路已通过。详见 §4。无需仅为 Spec 审查连接数据库。
5. **短 quote 漏条件**：需要同时写漏召回与错误放行，见 G2。模型原话摘录身份真实，只证明没有编造这段文字，不能证明条件完整。

F4 的方案认可：原因优先级由 ComplaintService 维护，所有硬理由取最高风险、退款标记取任一为真；只有纯 agitated 允许计划复核。classify_all 应保持 ComplaintClassification 返回契约，没有命中返回 is_complaint=False，再由 ConversationService._classify_complaint 映射为 None。代码依据为[ComplaintService.classify](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_service.py:33)及[会话共用入口](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1330)。

还须检查[reply_regression.pre_route](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py:480)：它目前只 classify 原始 question，不使用全部变体。正式门禁若声称覆盖此次分类合并，应复用 classify_all 与 policy_variants，或明确由会话集成测试提供这项证据；不能让门禁和生产判不同路由。

## 4. PostgreSQL 验收补证与验证边界

v1 §2.4 的第一条方向正确。复用[现有暂停主回复用例](/Volumes/02/code/homestay-bot/tests/integration/test_guest_reply_postgresql.py:309)，在第二事务确实取得活动锁、记录 SERVICER 消息并提交人工状态/原生接入事实；恢复后旧结果出站、业务任务与多余员工通知均应为零。只写 HUMAN_ACTIVE 并不一定覆盖原生接管路径；需要写明本用例证明哪一种接管事实，并保留另一种未覆盖边界。

中断重建用例至少应写出以下可观察状态：

- 来源作业真实持久化，已领取为 RUNNING；规划前 commit_boundary 提交之后暂停。这个位置由[ConversationService._plan_with_released_lock](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1364)确定，不是第一次函数调用结束。
- 取消并等待 task 收束、回滚未提交事务、关闭旧会话。新会话和新服务从同一来源载荷继续，不共用旧对象缓存。
- calm 与 complaint 两个分支分别列出哪些去重键应恰好 1、哪些恰好 0；正常分支要继续执行 final 阶段，才能对业务任务和最终客人出站作非空判断。不能只跑 debounce 后检查 BusinessTask=0 就证明登记幂等。
- 保留[原完整执行后再重放](/Volumes/02/code/homestay-bot/tests/integration/test_guest_reply_postgresql.py:369)的对照，但把 jobs>=1 改为分类型、分来源精确数量。前一个暂停点发生在副作用登记之前，单靠它不能证明已提交副作用的重放去重。

更有判别力的第二个窗口是 [_stage_fast_ack 已提交 final 作业](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1270)、来源 worker 尚未 mark_completed 之间：[Worker.run_once](/Volumes/02/code/homestay-bot/src/homestay_bot/worker.py:384)在 handler 返回后才标记完成。在该窗口取消，才有“来源仍 RUNNING、已存在持久化副作用”的重放前提。若本轮不补该窗口，就明确保留这个边界，不把第一窗口的证据扩大成所有副作用去重已通过。

如果需要最小真实 recover_stale 验收，可沿用同一个专用 PostgreSQL fixture：实际 enqueue/claim 并提交来源作业，构造固定旧 locked_at、attempts 低于上限；取消旧处理后用新会话调用 SQLAlchemyJobRepository.recover_stale(before=固定阈值)，提交并断言 RUNNING→PENDING、同一 job.id 和载荷保留、locked_at 清除；再由 claim_next 或真实 Worker 领取并完成，检查上述精确去重集合。源码位置为[recover_stale.retry_statement](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py:225)。不要靠睡眠等待超时，也不需要真实微信发送或生产库。v1 选择暂不覆盖这一链路可以保留，但必须保持相应验收未完成。

本轮实际验证：

| 项目 | 结果 | 证明边界 |
| --- | --- | --- |
| 返回报告 7 项文件摘要 | 全部匹配 | 对应本地 Spec、前报告与源码；不证明部署 |
| E-S1–E-S5 | 退出 0，9.16 s，输出见 §5 | E-S5 是当前最终正文；其余是具体方案前提的离线代理，源码未修改 |
| 既有 93 项相关 unit 回归 | 复用上一轮结果，未重跑；源码和三份测试摘要未变化 | 不覆盖新方案，不等于当前全量或生产验收 |
| 报告静态校验 | 37 个文件链接有效，17 项摘要匹配；新增报告与工作树 diff 空白检查通过 | 只证明报告绑定与格式，不是业务验收 |
| 新方案实现、PostgreSQL 补证、真实模型门禁、实际收件 | 本轮未执行 | 均不能标为通过 |

当前仅写报告，不跑业务全量。实施后的测试范围按最终行为与调用方确定；v1 §3.2 的“本地全量、CI”不应自动变成两个必过的重复部署门禁。主模型输入和知识检索改变后，全量真实模型门禁要求仍保留，需当前授权；D1 独立改写集应继续独立编写，公开反例只作回归。

## 5. 可独立运行的离线探针

在本报告绑定的工作根运行以下命令。只使用合成知识与现有测试替身；不连接数据库、真实模型、Hostex 或微信。E-S2/E-S3 只在进程内替换一个谓词，finally 恢复；E-S1/E-S4 只检验拟议判据，不是新实现验收。断言缺口出现，退出 0 表示反例成立，修复后应另写正确行为回归。

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/unit .venv/bin/python - <<'PY'
import asyncio
import json
import re
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.tourism import classify_tourism_query
from homestay_bot.services import knowledge_evidence_policy as policy
from homestay_bot.services.context_retention import CustomerModelContext
from homestay_bot.services.knowledge_service import KnowledgeService, KnowledgeSnippet, _passes_triggers, normalize_text
from tests.plan_helpers import plan_for
from tests.unit.test_knowledge_service import KnowledgeRow
from tests.unit.test_deepseek_turn_plan import _assistant, _decision
from tests.unit.test_conversation_plan_flows import _guest_turn
from tests.unit.test_conversation_service import AssistantStub

def emit(probe, **fields):
    """输出合成数据观察；正常退出表示反例成立，不表示新方案已实现。"""
    print(json.dumps({"probe": probe, **fields}, ensure_ascii=False))

def normalized_question(question):
    """仅在进程内模拟 Spec D-G1；新增长疑问词前置，排除排列顺序造成的干扰。"""
    fillers = re.compile(r"什么时候|何时|几点钟|" + policy._FOLLOW_UP_FILLERS.pattern, re.I)
    return fillers.sub("", normalize_text(question))

def proposed_same(first, second):
    """模拟范围、房源相同且去填充词后相等；不修改源码。"""
    return (
        first.scope == second.scope
        and first.property_id == second.property_id
        and bool(normalized_question(first.question))
        and normalized_question(first.question) == normalized_question(second.question)
    )

def pair(first_q, second_q):
    """构造两个范围相同、主体或限定不同的审核知识。"""
    return [
        KnowledgeSnippet(source_id=1, category="设施", question=first_q, answer=first_q.replace("几点开放？", "8:00开放。"), scope="global"),
        KnowledgeSnippet(source_id=2, category="设施", question=second_q, answer=second_q.replace("几点开放？", "10:00开放。"), scope="global"),
    ]

class Repo:
    """只返回合成已审核知识，点名的未知房号不映射。"""
    async def list_active(self):
        """给已确认401构造退房政策；不连接数据库。"""
        return [KnowledgeRow(id=401, category="政策", question_zh="401房间几点退房",
            answer_zh="401房间退房时间为12:00。", question_en="checkout",
            answer_en="12:00", scope="property", property_id=401)]

    async def find_property_by_room(self, room):
        """909始终未知；不推断住宿。"""
        return None

async def main():
    """逐项验证四类方案前提与一个当前真实回退出口。"""
    text = "今天天气怎么样，武汉有哪些经典景点推荐"
    plan = plan_for(text, ("external_info", text))
    assert plan.ok and plan.plan
    clauses = [c.strip() for c in re.split(r"[，,。；;！？!?\n]|另外|以及|同时", text) if c.strip()]
    live = [c for c in clauses if classify_tourism_query([{"role":"user", "content":c}]) == "live"]
    proposed_covered = any(plan.plan.valid_items[0].quote in c or c in plan.plan.valid_items[0].quote for c in live)
    assert proposed_covered and "武汉有哪些经典景点推荐" not in live
    emit("S1-coverage-proxy", verified_plan=plan.ok, live=live, proposed_covered=proposed_covered, omitted="武汉有哪些经典景点推荐")

    original = policy._same_question
    try:
        policy._same_question = proposed_same
        gyms = pair("民宿一楼健身房几点开放？", "民宿二楼健身房几点开放？")
        control = policy.verify_selected_evidence(gyms[0].question, [1], [2], gyms)
        breakfast = pair("民宿一楼早餐厅几点开放？", "民宿二楼早餐厅几点开放？")
        topic_result = policy.verify_selected_evidence(breakfast[0].question, [1], [2], breakfast)
        assert not proposed_same(*breakfast) and control.status == "grounded"
        assert topic_result.status == "missing" and topic_result.reason == "conflict_time"
        emit("S2-topic-bypass-proxy", exact_same=proposed_same(*breakfast), control=[control.status,control.reason], result=[topic_result.status,topic_result.reason])

        priced = pair("民宿免费健身房几点开放？", "民宿收费健身房几点开放？")
        priced_result = policy.verify_selected_evidence(priced[0].question, [1], [2], priced)
        assert proposed_same(*priced) and priced_result.status == "missing"
        emit("S3-filler-proxy", keys=[normalized_question(e.question) for e in priced], result=[priced_result.status,priced_result.reason])
    finally:
        policy._same_question = original

    text = "我的车是电动车，请问停车多少钱"
    plan = plan_for(text, ("static_fact", "停车多少钱", {"question":"电动车停车多少钱"}))
    assert plan.ok and plan.plan
    entry = KnowledgeRow(id=1, category="政策", question_zh="停车多少钱", answer_zh="停车每次20元",
        question_en="parking", answer_en="20 yuan", scope="global")
    entry.trigger_exclude = ["电动车"]
    item = plan.plan.valid_items[0]
    quote_allowed = _passes_triggers(entry, normalize_text(item.quote))
    question_allowed = _passes_triggers(entry, normalize_text(item.question))
    assert quote_allowed and not question_allowed
    emit("S4-trigger-proxy", verified_plan=plan.ok, quote=item.quote, question=item.question, quote_allowed=quote_allowed, question_allowed=question_allowed)

    text = "909房间几点退房"
    context = CustomerModelContext(confirmed_stay={"property_id":401,"check_in_date":"2026-10-08","check_out_date":"2026-10-09"})
    for kind in ("static_fact", "catalog_query"):
        plan = plan_for(text, (kind,text,{"target_room":"909"}))
        assert plan.ok
        assistant, calls = _assistant([_decision(evidence_selection=None)], KnowledgeService(Repo()))
        decision = await assistant.respond(guest_identifier="synthetic-guest",language=Language.ZH,
            messages=[{"role":"user","content":text}], turn_plan=plan, customer_context=context)
        _, wecom, _ = await _guest_turn(text, AssistantStub(decision=decision,plan=lambda _:plan))
        body = wecom.guest_messages[-1]
        assert "401房间退房时间为12:00。" in body
        emit("S5-current-fallback", kind=kind, main_calls=len(calls.requests), final=body)

asyncio.run(main())
PY
~~~

实际输出摘要：

- E-S1：verified_plan=True；live=["今天天气怎么样"]；proposed_covered=True；经典景点仍在未查内容中。
- E-S2：新的同问题判定 False；不同楼层健身房 grounded/selected；不同楼层早餐厅 missing/conflict_time。
- E-S3：两个键均为“民宿健身房开放”，核验 missing/conflict_fee。
- E-S4：计划 ok；quote_allowed=True，question_allowed=False。
- E-S5：static_fact 与 catalog_query 都最终发出 401 房间 12:00 退房政策。

## 6. 下一版交付与工作区归属

下一版先修订 G1–G5 的契约与验收表，保留 classify_all 的合并方向、已记录的 D-G1 窄判据取舍和独立验收边界。G2 的保守排除策略、G4 对主题组的限制如果改变已确认的召回取舍，应在 Spec 中单列确认，不能把本次审查建议当作用户已批准的新决策。之后再取得明确“开始”实施；本报告不授权编码或外部操作。

本轮新增文件仅为本报告。收尾会按项目规则维护报告对应的 AOCI 认知条目与托管资产；这些与业务源码改动区分。预先存在的 Claude fix2 报告、fix2 Spec、上一轮 Codex 报告、旧 Claude 报告补充、aoci.code.txt/.aoci/baseline.json 改动均保留，.impeccable/critique/ 不属于本任务。没有提交、推送或部署。

以下摘要绑定本轮判断；任何源码或 Spec 变化后，先重新核对受影响证据，不能机械引用本报告结论：

| 文件 | SHA-256 |
| --- | --- |
| docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-spec-review-handoff.md | c3159ed3df491a2fcd8ae7971c2b23f506e68900098fdbb8d8468f411ca162fb |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 2281283dc2e5eac07995209698e9555e6bd71286fe7a7d19652d8adf3eabdb27 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix-review-handoff.md | 9a36b61934186cf241031c02c1f40dd761e861eb04451e7f0dfbbb4d64850a5f |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_service.py | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/complaint_service.py | 4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef |
| src/homestay_bot/services/answer_policy.py | 01561d21304f2058132de2ba7603ca4ae20546d949417f929ebbbd2143a4e4fe |
| src/homestay_bot/services/turn_plan.py | 4f2f93caa43929ba50cc762811b0b26bd279cd3be6918841081e907f42d873d6 |
| src/homestay_bot/tools/reply_regression.py | 57c4e8c9c466bd9b4a13f1706c3478c55a61693c4b8cbb1d68711aba5e7365b5 |
| src/homestay_bot/repositories/jobs.py | dca84cc795e7a313678eddc66b7e93adc3837fb8380579efd6d1a47bf44a0c33 |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| tests/integration/test_guest_reply_postgresql.py | 33151cd8f979211b88f0218c85777f5166b8e14d16ba712abe952325a380b440 |
| tests/unit/test_turn_plan.py | 0397407966c04fb13cff99d852713eb7c3ef6ae0914768e762d26f5f609bb7c9 |
| tests/unit/test_deepseek_turn_plan.py | 0527ece822ceeb253d9f7632945b40b602e3e13818ead063466b830c3d46fb1c |
| tests/unit/test_conversation_plan_flows.py | 925413407f560abf24951f1cad334f05450c48cc2bc24e5eb8d7afbc1dc5dccb |
