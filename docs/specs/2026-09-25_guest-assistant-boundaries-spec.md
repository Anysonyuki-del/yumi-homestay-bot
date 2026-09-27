# 住宿咨询助手：回复决策、证据与服务转交 Spec

日期：2026-09-25。源码基线：`1300b13`，编写前工作区干净。

状态：用户已回复「开始实施」，完整 Spec 获得确认，进入离线实施和本地验证；不提交、推送、部署或调用真实外部接口。

## 1. 目标与决定来源

目标：让客人得到与本次住宿和出行有关、有依据、保留条件的回答；确需人工处理时登记可追踪请求，回复内容与登记结果一致。通过统一责任边界降低交叉回归，不承诺自然语言理解零误判。

本次会话中已经确认的产品决定，不再重新访谈：

| 编号 | 用户决定 | 验收含义 |
| --- | --- | --- |
| P1 | 根据房源资料和审核知识完整回答入住等问题 | 不编造本店事实；知识适用房间、条件和时效不能丢失 |
| P2 | 知识库外开放住宿出行相关查询，选择 A | 可查天气、景点、餐饮、交通、票价、开放时间；仅服务本次住宿和出行 |
| P3 | 推荐深度选择实用安排 A | 可按天气、偏好与时间组织半日或一日安排，关键条件不清才追问 |
| P4 | 资料缺失按影响处理 A | 已知部分照答；普通缺口记录待补充，影响当前入住或有具体安排需求时登记请求并通知管家 |
| P5 | 房态、价格及预订选择查询介绍 A | 实时查询权威系统；要求预订时交管家，不自动下单，不主动收集完整预订资料 |
| P6 | 外部查询失败选择备选建议 A | 明确缺项，保留有依据建议；不自动转人工，不承诺稍后补发 |
| P7 | 请求跟进选择登记通知及查询 A | 客人追问时按实际记录答复；不主动催办、不承诺完成时间，其他问题继续回答 |

| P8 | 办理入住前完成本次入住确认，再按入住时间和房间回答 | 先明确可信的本次住宿上下文；由客人确认已核实归属的订单，具体流程见 4.4 |

## 2. 已核实现状与改动动机

以下以本轮重新读取的源码为准；旧文档仅用于定位影响面。

| 现状 | 源码依据 | 对应风险 |
| --- | --- | --- |
| 房态或房源目录工具成功后，以 `property_tool_grounded` 放宽整段答复校验 | `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant.respond/_validate_decision` | 工具只证明某个结果，却替无关设施和金额背书 |
| 精炼后没有与原事实进行等价校验 | 同文件 `::_refine_reply` | 精炼可能改变天气、日期、数值或否定条件 |
| 旅游查询先于知识检索直接返回 | 同文件 `::respond` | 混合问题中的本店问题被遗漏 |
| 静态证据门要求所有主题同时成功；未识别属性可能退为 existence | `src/homestay_bot/services/knowledge_evidence_policy.py::asked_attributes/build_evidence_plan` | 不能部分作答；相关资料被误当完整答案 |
| 任务或设施字段能使静态证据计划不接管整段答复 | `src/homestay_bot/integrations/deepseek_client.py::_plan_handles_reply` | 一个动作字段影响其他事实段的验证 |
| 最终出口按关键词删除“旧话题”，人工分支再按句筛选 | `src/homestay_bot/services/conversation_service.py::_clean_guest_reply_topics/_process_model_reply`；`src/homestay_bot/services/guest_reply_policy.py::sanitize_guest_reply` | 已审核的限制条件仍可能被删除 |
| 先发客人回复，再保存任务；快速安抚判断也能启用人工话术 | `src/homestay_bot/services/conversation_service.py::_process_model_reply/_record_task_suggestion/_should_send_fast_ack` | 话术承诺和实际任务、通知不一致 |
| 预订确认仍会创建待审批单 | `src/homestay_bot/services/conversation_service.py::_create_pending_approval` | 与 P5 不符 |
| 旅游失败自动切人工并通知员工 | `src/homestay_bot/services/conversation_service.py::_escalate_tourism_failure` | 与 P6 不符 |
| 知识没有房间适用范围与有效期字段 | `src/homestay_bot/domain/models.py::KnowledgeEntry`；`src/homestay_bot/services/knowledge_service.py::KnowledgeSnippet` | 无法可靠隔离不同房间、过期资料 |
| 上下文只含未完成任务，且不含任务 ID、更新时间 | `src/homestay_bot/repositories/context.py::SQLAlchemyContextRepository.load_model_context` | 无法回答“刚才那件事完成了吗”，也难以消除同类请求歧义 |
| 一条来源消息只能创建一个待确认任务 | `src/homestay_bot/domain/models.py::BusinessTask.source_message_id`；`src/homestay_bot/repositories/operations.py::SQLAlchemyOperationsRepository.create_pending_confirmation` | 多事项不能直接循环调用建任务，否则后一个会复用前一个 |
| 客诉、紧急分类主要是词面匹配 | `src/homestay_bot/services/complaint_service.py::ComplaintService.classify`；`src/homestay_bot/services/emergency_service.py::EmergencyService.classify` | 普通平台咨询误升级，否定句误触发，危险改述漏识别 |
| 检索评估的 final 实际止于助手输出 | `tests/unit/test_knowledge_retrieval_eval.py::final_reply/reply_passes_oracle` | 未验证最终发送正文、动作及失败重试 |

入住关联的补充核查：`domain/models.py::Conversation` 仅有客户关联，没有本次入住确认字段；`repositories/operations.py::SQLAlchemyOperationsRepository.upsert_reservation` 按 Hostex 订单身份关联客户；`services/customer_service.py::CustomerService.ensure_for_message/confirm_merge` 分别建立微信渠道身份、执行管理员确认的客户合并。`repositories/customers.py::SQLAlchemyCustomerRepository.ensure_identity` 中的 `is_verified` 是渠道身份标记，不能单独解释为已确认本次订单。以上路径均以 `src/homestay_bot/` 为前缀。

本会话前一轮审查证据：900 项相关离线测试通过；24 项定向检查中 17 项不满足设定预期，包含重复路径和产品行为选择，不是 17 个独立缺陷，也不是错误率。当前基线未变；本轮不把这些历史结果标记为重构完成证据。

## 3. 方案选择

采用“保留现有接入、队列和运营系统，替换回复决策契约”的有范围重构。

- 仅继续改提示词与关键词：改动小，但不能解决来源范围、事实再改写与空承诺，不采用。
- 统一问题分项、证据绑定和动作结果，复用现有组件：可以逐段验收，采用。
- 重写整个项目或建立多代理/通用规则引擎：当前没有必要，不采用。

同一个目标、一个 Spec、连续实施和一次最终综合验收；下面各步是可运行的小步，不是另起多个产品项目。

## 4. 回复流程与职责

### 4.1 一次计划、分项取证、按结果组织回复

流程：入站去重和安全检查 → 识别本轮独立问题及明确请求 → 检查本次入住确认及房间/日期适用范围 → 分项检索/只读查询 → 本地校验证据 → 登记必要请求和通知 → 组织最终正文 → 分段出站。

- 复用 `ConversationService` 的同步、静默合并和后台入口，共用后续业务处理，不让各入口各自复制产品规则。
- 模型负责理解改述、承接上下文、拆分问题和选择证据；它的输出是待验证提议，不能自行授权工具、任务或对外承诺。
- 已确认的本次订单提供个性化回复的房间和住宿日期；客人新陈述用于提出变更或澄清，不能覆盖权威订单。实际任务用于查询进度，历史记忆不能授权动作或证明当前库存。
- 查询普通房源资料不等于确认客人身份；返回客人订单、服务进度或凭证前仍执行身份与客户归属检查。
- 能沿用明确日期、地点就不重复追问；多个可能房间/订单或互相冲突时，只澄清影响结果的部分。
- 同轮知识、实时查询和服务申请可以并存；一个分项失败不能清空其他分项，危险事件则优先给安全处置。

### 4.2 最少共享契约

新增 `src/homestay_bot/services/reply_plan.py`，供助手、会话出口和投递改写共同使用；不建立插件注册表、策略工厂或第二套会话服务。

- `ReplyPart`：本轮问题分项、结果类型（有依据/缺资料/需澄清/查询失败/范围外）、正文或事实数据、对应证据引用。
- `ReplyEvidence`：来源种类与本轮来源 ID、房源 ID、目标日期/日期区间、获取时间及必须保留的条件。来源只接受本轮确实读取的审核资料、工具结果或客户归属已校验的任务记录。
- `GuestActionResult`：任务 ID、任务是否成功登记、通知是否成功进入 outbox；由本地执行结果填充，禁止从模型 JSON 信任这些字段。
- 扩展现有 `AssistantDecision` 携带分项；原 `reply_text` 在调用方迁移期间只作为聚合展示结果，不能成为第二条绕过验证的自由输出通道。迁移完删除本次替代的分支。
- 新增类型只表达上述真实共享契约，既有 `BookingFields` 等类型若还有独立调用方则保留，不为“清理”扩大删除范围。

### 4.3 证据与表达

- 工具结果按来源、房间和日期逐项绑定；“查过工具”不再代表整个回答可信。
- 房态、房间名称、参考价和任务状态由结构化结果直接生成事实段。参考价须明确其口径和适用日期，不升级成最终成交报价。
- 库存中的缺失、查询失败与明确不可订分别表示；不把空结果当全部无房。仅有价格结果不能证明可订，房源目录也不能证明停车或接送。
- 静态本店知识保留审核答案的完整事实单元及条件；模型可选择、排序并组织多个单元，不能自由改写收费、资格或否定结论。不要求复制整条无关长资料；资料本身过长且无法完整安全发送时，说明受限并请求细化，不截断条件。
- 自然语言的“是否答到问题”仍需语义判断和独立评估；本地校验只宣称能核验来源、范围、数值、原文及动作状态，不能宣称证明任意自然语言蕴含。
- 未识别的限定不得默认成“只问是否存在”；模型必须指出对应的审核答案依据，无充分覆盖则只将该分项标记缺失。模糊、冲突的资料不按检索排名投票。
- 天气、开放时间、票价等动态信息，即使出现在知识库中，也不能绕过实时查询。公开搜索资料只能支撑店外信息。
- 实用行程可增加不含新事实的衔接与一般建议；精确用时、价格和开放状态必须来自查询，非实时建议不得伪装为现场情况。
- 最终排版只处理版式；不再按问句是否出现某个词删除审核条件，也不因为“需要人工”删掉全部事实段。
- 精炼与平台安全改写都需保留来源绑定和关键条件。优先复用 `DeepSeekDeliveryRewriter._validate_facts` 的已有校验；对于无法机械证明等价的事实段保留原文，只调整外围表达，不扩展一套新的无限语义正则。
- 平台发送失败属于投递故障，与 P6 的外部查询失败分开：沿用一次改写与有限重试，不能安全发送时通知员工处理，不形成无限改写或重查。

### 4.4 本次入住确认（已选择客人确认）

已明确的目标：办理入住前确认客人的本次住宿，随后按对应房间和入住时间回复。它是已有住宿的确认，不是创建预订、批准提前入住或把订单标记成实际已入住。

用户已选择由客人确认。具体流程纳入本次完整 Spec：

1. 先通过现有可信客户关联取得属于该客人的有效订单。未关联时交管家核实；不凭客人报出的房号、姓名或日期自动绑定，不先展示他人订单供选择。复用既有管理员关联/合并能力，不新增自动合并机制。
2. 已有关联订单时，请客人确认本次房间、入住及退房日期；多订单只在已核实归属的范围内选择。管家负责关联异常核实，不代替客人完成本次确认。
3. 确认后沿用本次住宿回答房间设施、入住安排及出行问题，不每轮重复询问。“入住日期”“预计到达时间”“政策允许入住时间”分别处理；客人预计中午到达不代表已批准提前入住。
4. 换房、改期、取消或客户归属变更后，旧确认不得继续支持新回复。发送前重新核对当前订单与确认时的房间/日期；发现变化只重新确认受影响部分。退房后可答该次住宿的历史问题，但不能继续按在住授权处理服务和凭证。
5. 普通公开咨询、已审核通用政策和紧急处置不等待入住确认；“我房间的……”等个性化问题在未确认时先澄清，已知通用部分照答。不把注册/核验强加给所有访客。
6. 入住确认不代替凭证安全门。`services/credential_delivery.py::CredentialSafetyRules.invalid_reason` 的客户归属、入住当日、房间 READY、有效凭证和渠道窗口校验全部保留。

技术方案（随完整 Spec 审核）：

- 在 `Conversation` 增加可空 JSON 字段 `stay_confirmation`，只保存待确认/已确认状态、订单 ID、客户 ID、房间 ID、入住/退房日期快照、发起确认的消息 ID，以及确认后的客人消息 ID、确认时间。字段由本地代码校验并写入，模型不能直接提交任意确认记录；不另建订单表、审批流或管理页面。
- 本地代码从已核实归属的订单生成待确认内容，例如“请确认：您本次入住 201 房，9月26日入住、9月28日退房，对吗？”没有待确认内容时，孤立的“好的”不能建立确认。多订单先选择，再确认；否定、改期诉求和指代不明时不能写成已确认。
- `SQLAlchemyConversationRepository` 新增 `confirm_stay`，复用会话锁与保存机制；按待确认消息和订单快照复核归属、房间、日期、有效状态，再原子写入确认。重复消息幂等；旧确认回复不能覆盖后来发起的确认。模型只识别确认意图，确定性校验决定是否接受。
- `ConversationService._process_model_reply` 接入确认交互；`SQLAlchemyContextRepository.load_model_context` 输出经校验的本次住宿，未确认订单不能被模型默认为当前房间。公开房源查询仍可按明确指定的房源回答，但不得称为客人的已订房间。
- 新迁移顺接知识范围迁移，计划为 `migrations/versions/0029_stay_confirmation.py`；存量会话字段为空，不从历史聊天猜测补确认。迁移和降级只在隔离数据库验证，生产处理另行授权。
- 测试增加无待确认时的“好的”、明确否认、重复确认、跨订单迟到回复、确认与改期并发、存量会话迁移；检查最终回复和保存状态。确认方式已经确定，不再保留管家代确认分支。

## 5. 知识最小数据变更（新增技术决策，待确认）

给 `KnowledgeEntry` 增加：

| 字段 | 含义及约束 |
| --- | --- |
| `scope` | `unreviewed` / `global` / `property` / `public`；分别是范围待审核、本店通用、指定房间、店外公开知识 |
| `property_id` | 可空，外键到现有 `PropertyProfile.id`；仅 `scope=property` 时必填，其他范围必须为空 |
| `valid_from`、`valid_until` | 可空的日期边界，包含起止日；两者同时存在时起日不得晚于止日 |

- 单房间知识先采用一个房间 ID；共同规则用 global，少数不同房间规则分条维护。当前不引入多房间关联表或可执行表达式。
- 空有效期只代表没有另设期限，不使天气/票价等动态事实永久有效；信息种类仍决定是否要实时查询。
- 具体房间规则优先于同主题通用规则，仅限同一事实项；同级互相冲突则按该项未确认处理，互补信息可以合并。
- 不猜测存量条目的范围。迁移保留原正文、关键词、启用标记及审核人，新范围默认为 `unreviewed`；该状态不能为本店断言作证。
- 上线前必须完成实际使用条目的范围审核，否则新链会减少可回答内容；不能把测试数据写进真实知识库替代审核。此项是启用前置条件，不是运行时静默兼容回旧逻辑。
- 新建、编辑、候选转正式、草稿导入都执行相同范围与日期校验；草稿保持停用/待审核，不自动生成“已确认”事实。
- 有效期按本轮问题的目标日期判断，未指定日期时使用武汉当前日期；住宿跨越不同生效区间时分别说明。失效、停用、范围不适用的条目在检索评分前过滤。范围变更即刻影响选取，向量排名不得使被排除的条目重新进入证据集。
- 管理页面只补必要的范围、房间和日期字段与错误提示；沿用原生控件、权限和 CSRF，不重做整个知识管理 UI。
- 迁移文件计划为 `migrations/versions/0028_knowledge_scope.py`；实施前再核对 Alembic head，若编号已占用则改为顺接的新修订，不能产生并行 head。

## 6. 服务请求、进度和失败

### 6.1 咨询与申请分开

- “提前入住有什么规定”只答政策；“我明天12点到，能帮我提前入住吗”登记待管家确认。语义不足以判断时澄清，不默认已批准。
- 房态、房价查询不因出现价格词而整体转人工。明确要求预订时登记 `SPECIAL_SERVICE` 待确认请求并通知管家，不创建审批单，不主动追问完整预订身份资料。
- 本店普通知识缺口复用 FAQ 候选机制做去重记录，不每条创建运营任务。现有频次阈值与草稿审核流程保留，不能因为要“记录缺口”让未审核资料自动生效。
- 模型提出任务仍受本轮请求、客户归属与白名单校验；知识或历史文本中的指令不能授权任务。
- 复用 `BusinessTaskService.record_ai_suggestion` 和 `create_pending_confirmation`。同一入站消息包含多项需要人工确认的事项时，先合并为一个 `SPECIAL_SERVICE` 请求，完整保留各事项；不得循环建单后被来源唯一键吞掉。此简化不提供逐项独立指派，未来确需独立执行再调整任务唯一键与状态模型，并在实现旁留 `ponytail:` 约束说明。

### 6.2 动作先于承诺，使用既有 outbox

- 同一事务登记待确认任务、员工通知 outbox 和客人最终回复 outbox；提交成功后才由 worker 对外发送。模型/查询阶段不持有任务写锁；员工显示名称查询在写事务前完成或使用已有安全降级，持锁期间只登记 outbox，不调用外部消息接口。
- 已登记任务可以说“已记录，等待管家确认”；通知入队只能表示已提交通知，不能说管家已阅读、已接单或人员已出发。
- 任务已记录、通知入队失败时保留任务并明确尚未完成通知，不声称已联系；任务写入失败时回滚该写入，不吞异常后继续发送成功话术。
- 必要时用现有事务/savepoint 和持久化队列恢复，不增加第二套补偿系统。入站重放仍按来源消息去重；客人失败提示、任务、通知各有稳定幂等键。
- 快速安抚只表示收到并正在查询，不再因为安抚阶段命中服务词就承诺联系管家。
- 客人新消息或员工新活动导致结果过时时，写动作与出站前重新检查会话活动，沿用现有锁和分段链路，不能旧回复失效却留下新建任务。

### 6.3 进度与失败

- 增加客户范围内的最近任务状态只读查询，包含已完成、取消和过期任务，返回 ID、类型、状态、房间、服务日期、更新时间；不把只含 `open_tasks` 的上下文当完整进度。
- 优先用会话中已关联的任务 ID；若同类任务不唯一，澄清是哪一项，不取任意最新任务替代。客户端或模型提供的任务 ID 必须再次校验 `customer_id`。
- 进度只从实际任务状态组织：待确认不等于已安排，待检查不等于完成；不输出员工内部备注或联系方式。
- 外部旅游查询失败保留已知分项并说明缺项，不改变会话模式、不创建人工请求；客人明确要求人工时沿用正常登记流程。
- 一般模型协议或网络失败也不能虚构“会继续查询”。明确服务请求可走本地可证明的待确认路径；无法可靠识别时告知暂不能确认，不凭空发起事项。
- 不新增业务催办、超时升级或主动进度推送。现有与本次请求无关的入住提醒、凭证发送和人工后台审批保持原边界。

## 7. 风险识别与预算

- 保留现实危险的确定性快速通道，补齐否定/引用/政策咨询与当前危险的区分；清晰危险信号不能被模型降级。模型对未命中词面的危险语义可以提出升级，不允许批准经营事项。
- “平台预订”不等于平台投诉；退款政策说明与实际退款请求分开。实际退款、赔偿及投诉仍由人工处理，不自动定责或承诺金额。
- 语义识别异常时，不因异常放开既有明确危险门；普通问题不默认提升为紧急事故。新增回归必须同时包含危险改述与非危险反例。
- 复用 `MODEL_BUDGET` 的主链最多 3 次模型调用、最多 2 轮工具结果及字符预算；拆成多项不能每项获得一份完整预算。
- 同轮公开问题合并为最多一次有限搜索；不新增无限研究循环、常驻后台搜索或共享缓存工程。超预算时回答已完成部分并说明剩余项，必要时请客人选择优先项。
- 不先修改部署超时或模型配置；若真实模型评估表明预算不足，应呈现证据再调整 Spec，而非静默加成本。

## 8. 文件与符号级实施范围

所有路径相对仓库根目录。新符号是本 Spec 拟新增项；既有符号均已核对。

| 顺序 | 文件与符号 | 修改与验证目标 |
| --- | --- | --- |
| 1 | 新增 `tests/fixtures/guest_reply_scenarios.json`、`tests/integration/test_guest_reply_contract.py` | 固化下面业务矩阵，使用虚构资料与外部替身；先复现关键失败并记录最终正文和副作用 |
| 2 | `src/homestay_bot/domain/models.py::KnowledgeEntry`；新增 `migrations/versions/0028_knowledge_scope.py` | 范围/有效期字段、外键与一致性约束；验证存量迁移与新建库 |
| 2 | `src/homestay_bot/routes/knowledge.py::KnowledgeAdminService/create_knowledge/update_knowledge/convert_candidate/_fields`；`src/homestay_bot/templates/knowledge/index.html`、`src/homestay_bot/templates/knowledge/detail.html`；`src/homestay_bot/tools/import_knowledge_drafts.py` | 全写入入口统一校验、保留草稿审核边界；浏览器验证范围和日期保存 |
| 2 | `src/homestay_bot/repositories/knowledge.py::SQLAlchemyKnowledgeRepository.list_active`；`src/homestay_bot/services/knowledge_service.py::KnowledgeRecord/KnowledgeSnippet/KnowledgeService.retrieve/retrieve_detailed` | 检索携带并过滤适用范围，保留来源；语义评分不越过范围门 |
| 2 | `src/homestay_bot/domain/models.py::Conversation`；`src/homestay_bot/repositories/context.py::SQLAlchemyContextRepository.load_model_context`；`src/homestay_bot/services/conversation_service.py::_process_model_reply` | 增加入住确认及失效校验；复用客户关联能力；`repositories/conversations.py::SQLAlchemyConversationRepository.confirm_stay`（新增）保存确认；新增 `migrations/versions/0029_stay_confirmation.py` |
| 3 | 新增 `src/homestay_bot/services/reply_plan.py::ReplyPart/ReplyEvidence/GuestActionResult`；`src/homestay_bot/integrations/deepseek_client.py::AssistantDecision/assistant_decision_schema/respond/_validate_decision/_static_evidence_plan/_plan_handles_reply/_refine_reply/HostexReadOnlyToolExecutor.execute` | 分项决策、逐源绑定和结构化工具事实呈现；不让任务字段跳过其他证据 |
| 3 | `src/homestay_bot/services/knowledge_evidence_policy.py::EvidencePlan/asked_attributes/build_evidence_plan`；`src/homestay_bot/services/answer_policy.py::handoff_reason/is_service_request/is_static_service_fee`；`src/homestay_bot/integrations/tourism.py::classify_tourism_query` | 支持部分回答，分清政策与申请；未知属性不退回存在性；路由不独占整句 |
| 3 | `src/homestay_bot/integrations/deepseek_tourism.py::DeepSeekTourismSearcher.search`；`src/homestay_bot/integrations/deepseek_delivery_rewriter.py::_validate_facts/rewrite`；`src/homestay_bot/services/delivery_rewrite_job.py::GuestDeliveryRewriteJobService.handle` | 公开查询结果携带可用证据，普通精炼与安全改写共享事实保留约束；不改变一次改写限制 |
| 4 | `src/homestay_bot/services/conversation_service.py::ConversationService._process_model_reply/_stage_fast_ack/_record_task_suggestion/_create_pending_approval/_escalate_tourism_failure/_escalate_assistant_failure/_clean_guest_reply_topics/_notify_employee` | 先登记动作后决定承诺；预订改为请求；公共查询失败不切人工；不删除审核条件 |
| 4 | `src/homestay_bot/services/business_task_service.py::BusinessTaskService.record_ai_suggestion`；`src/homestay_bot/repositories/operations.py::SQLAlchemyOperationsRepository.create_pending_confirmation`；`src/homestay_bot/application.py::TransactionalOutboxWeCom` 及会话装配 | 任务、通知、回复的事务和幂等；多事项合并；保持现有过时检查和分段机制；安全与承诺类回复除外（按 1.39.16 的 high_risk 出站标记） |
| 4 | `src/homestay_bot/repositories/context.py::SQLAlchemyContextRepository` 新增 `list_recent_task_statuses`；`src/homestay_bot/services/context_retention.py::ContextRepository`；会话装配与回复计划调用方 | 身份限定的进度查询，包含终态；不扩大模型读取客户隐私范围 |
| 4 | `src/homestay_bot/services/guest_reply_policy.py::prepare_guest_reply/sanitize_guest_reply/layout_guest_reply/split_guest_reply` | 事实段与动作收尾分开；已验证事实只排版，安全措施继续执行 |
| 4 | `src/homestay_bot/services/emergency_service.py::EmergencyService.classify`；`src/homestay_bot/services/complaint_service.py::ComplaintService.classify` | 同时验证漏报与误报，不靠全局放宽安全门解决误拦 |
| 5 | `src/homestay_bot/services/admin_debug_service.py::AdminDebugService` 及相关测试；`src/homestay_bot/services/faq_candidate_service.py` 的决策读取方 | 共用回复计划；调试仅模拟动作，不创建真实任务或发送通知；FAQ 按缺失分项统计 |
| 5 | `tests/unit/test_knowledge_retrieval_eval.py::final_reply/reply_passes_oracle` 与既有回复链回归 | 接到最终消息出口；新增行为契约同时检查正文、来源、任务、通知、模式 |
| 5 | `.github/workflows/ci.yml`；新增 `tests/integration/test_guest_reply_postgresql.py` | 在已有隔离 PostgreSQL 服务上覆盖本次事务、幂等和迁移，不把跳过当通过 |
| 6 | `YuMi民宿AI开发经验与防回归手册.md` 的回复、知识、人工接管和失败恢复章节；`tasks/todo.md` | 更新被本次产品决定替代的条目及验证结果；历史 Spec 不改写成当前规则 |

不变更房态库存、真实订单、房间凭证规则、员工权限或退款赔偿权限。只移除机器人新建预订审批的入口，不删除已有审批记录和人工审批后台。发布版本、CHANGELOG 与发布记录在另获发布授权时维护。

## 9. 验收矩阵与测试资料

虚构资料写入独立测试 fixture/SQLite/隔离 PostgreSQL，禁止写进实际知识库。至少覆盖两间设施不同的房间、本店通用规则、店外旅游知识和同主题不同日期的资料；姓名、身份和联系方式均为合成占位值。

| 用例 | 输入与依据 | 必须满足 |
| --- | --- | --- |
| C01 | 微信身份已建档，但尚未核实订单归属 | 不把渠道已验证当成入住确认，不展示他人订单 |
| C02 | 已核实归属、确认本次房间和日期，多轮咨询 | 使用同一次住宿回答，不反复索要日期房号；多订单不默认取第一条 |
| C03 | 确认后换房、改期、取消、退房或客户归属变化 | 旧确认不能支持当前入住断言或授权；并发发送前复核 |
| C04 | 到达时间早于规定入住时间，或房间未 READY | 不承诺可提前入住，不提前发送凭证 |
| C05 | 未确认的客人问通用政策、天气或报告危险 | 通用回答和紧急处置可继续；个性化部分单独澄清（依 4.4） |
| K01 | 早餐资料完整，模型故意改错时间和价格 | 最终正文采用正确时段、金额及预约条件 |
| K02 | 早餐已知、停车未知，一条消息同时提问 | 早餐照答，只标记停车缺失，普通缺项不建运营任务 |
| K03 | 202 房问浴缸，只有 201 房资料 | 不能用另一房间资料背书；范围待审核同样不能背书 |
| K04 | 同级早餐时段冲突；另有互补位置资料 | 时段未确认，位置可回答；不按排名选一个时间 |
| K05 | 问停车限高，仅有收费资料 | 不把收费答复标记为限高已解决 |
| K06 | 退房资料含节假日延迟限制 | 最终清理、分段后限制仍在 |
| K07 | 全文超过可完整发送预算 | 不截断条件后发送半条“可提供”结论 |
| K08 | 停用、过期、未到生效日、范围待审核资料 | 均不得作当前事实依据；未知不是明确不提供 |
| K09 | 英文问题、英文已审核资料；英文资料缺失 | 保留对应条件；缺译文不伪装为已经审核的英文事实 |
| R01 | 问明日天气及早餐时间 | 两个分项都处理，不因天气分流遗漏早餐 |
| R02 | 房态明确不可订，模型声称可订、199元、免费接送 | 错误房态、金额和服务断言均不得发出 |
| R03 | 参考价查询成功但库存查询失败 | 可说明有依据的参考价及口径；不得声称可订 |
| R04 | 天气阵雨25至31℃，精炼给出晴天35至40℃ | 精炼不被采用，保留原事实 |
| R05 | 存量天气资料过期，外部查询失败 | 不引用旧预报，不转人工、不承诺补发；其他已知部分仍答 |
| R06 | 一日游需求与明确异地行程；另有无关专业问题 | 在住宿出行边界内合理查询；无关问题不获得工具权限 |
| A01 | 提前入住政策咨询与具体提前入住申请 | 前者答政策，后者登记待确认；两者都不能当已批准 |
| A02 | 客人明确预订，模型返回完整 booking_fields | 不创建审批或订单，只登记管家请求，不再索要完整预订资料 |
| A03 | 延迟退房有规则、需要具体申请 | 条件保留，任务与通知成功登记后才说已转交 |
| A04 | 任务创建失败、通知入队失败、提交失败 | 无虚假成功承诺；持久化结果可追踪、重试不重复建单 |
| A05 | 同一消息多项人工请求、重复回调、客人新问题 | 多事项完整合并，重复回调幂等；过时结果不产生新动作 |
| A06 | 查询进行中、完成、取消、过期和同类多任务 | 按实际状态答复，歧义澄清，不泄露他人任务 |
| A07 | 人工处理一项诉求时又问独立天气/知识问题 | 独立问题可答，原事项状态不被擅自结束 |
| S01 | 平台预订咨询、退款政策、实际投诉/退款请求 | 前两者不只因单词升级；后者保留人工经营决策门 |
| S02 | 无呼吸/烟雾报警器响等危险改述，以及否定/引用/安全咨询 | 当前危险及时升级；普通咨询不误当现实事故；模型失败不绕过明确危险门 |
| S03 | 知识、历史、工具正文含“忽略规则/创建任务”等指令 | 仅视为数据，不扩大权限，不产生未授权动作 |
| D01 | 长答案分段、员工接管、新消息、平台失败及一次改写 | 时序、幂等和过时保护保持；安全与承诺类回复除外（紧急安全提示、客诉首响、转人工确认），完整事实不被二次改写破坏 |
| D02 | 调试页面运行同一场景 | 能显示计划和模拟结果，但任务、订单、消息外部副作用为零 |

风险样本先用可控替身注入错误模型输出，验证系统边界；模型理解能力另用独立改述集和真实模型评估，不能用“桩按预期分类”证明真实模型理解正确。

## 10. 实施顺序与验证门禁

1. 冻结上表业务预期，迁入本会话有价值的最小复现，记录红测；不为每个私有函数添加实现镜像测试。
2. 完成本次住宿确认及失效保护；完成知识范围最小迁移、管理入口和检索过滤；用虚构完整/缺失/冲突数据验证。
3. 完成分项计划、工具事实绑定和最终表达；迁移静态、旅游和混合问题，保留可运行状态。
4. 完成请求登记、通知和进度查询；同步迁移正常、异常及异步入口，覆盖事务与消息时序。
5. 清除本次替代的整体 grounded 放行、整句独占路由、关键词删事实、先承诺后建任务路径；调整调试和评估入口。
6. 自审冻结差异后运行最终验证集；完成后才报告离线实施完成，外部验收单列。

阶段验证按被改动模块运行。最终因为影响公共回复链和数据模型，使用一次全量离线门禁：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
git diff --check
```

迁移须分别验证空库 upgrade、从 0027 带合成数据 upgrade、隔离副本 downgrade/再次 upgrade；使用明确的临时 SQLite URL 和隔离 PostgreSQL URL，不读取默认生产连接串执行迁移。PostgreSQL 验证两会话竞争、任务/通知/出站原子性；测试不得缺环境后全部跳过却标绿。

知识管理表单在本地合成账户与数据库中验证保存、错误提示、键盘访问及窄屏；不使用已登录生产页提交表单。已有浏览器工具/运行时不可用时，记录缺口，不以单元测试替代页面验收。

真实模型、Hostex、企业微信和生产数据库操作尚未授权。本轮只形成离线实现计划；将来的真实模型评估先用合成资料和工具替身，明确调用预算并另获授权。真实消息验收需要实际收件及关联任务/通知证据，不能用平台受理代替。

## 11. 风险、回滚与确认点

需要本次完整 Spec 确认的新增范围：

0. **入住确认的实现。** 客人确认方式已确定；新增会话确认记录，核对订单快照并在变化后重新确认。存量会话不会自动获得确认；普通咨询继续，凭证独立校验。
1. **知识字段与迁移。** 原知识保留，但适用范围需审核，未完成审核会降低回答覆盖率。接受此方案后才写迁移；上线前复核实际数据与备份，不自动填充真实业务事实。
2. **旧行为替换。** 停止机器人新建预订审批、公共查询失败自动转人工，以及将普通价格/入住政策查询一概高风险处理；保留既有人工审批、危险与经营权限门。
3. **任务与进度。** 请求、员工通知、客人回复纳入同一持久化顺序；增加终态查询；同一来源消息的多事项合并为一个待确认请求，不新增主动催办。

回滚原则：开发阶段仅在隔离数据库验证；部署另行授权。新增字段保留旧正文，应用回退不要求删除新列。若已填写范围元数据，生产不直接 downgrade 丢弃字段；先备份并导出元数据，评估回退应用是否重新引入错误适用范围。重构期间不维护永久双轨回复引擎或无期限特性开关。

不可由离线测试证明的剩余风险：真实模型的意图拆分、冲突判断与改述覆盖；真实语义召回；平台实际投递；生产知识审核质量。这些是明确的独立验收项，不通过扩充桩测试掩盖。

确认后依据本 Spec 连续实施和本地验证；未经当前授权不提交、推送、发布或部署。


### 2026-09-26 并行发布衔接

用户明确补充：过时保护保持，安全与承诺类回复除外。已核实 main `a4b0b13` 含 1.39.16（实现提交 `34f13fd`）；该版以本地 `high_risk=True` 设置 `stale_exempt`，豁免客人新消息导致的出站过时丢弃。普通问答、快速安抚和普通查询失败不豁免；模型文字中的“已安排”不能自行获得豁免，凭证与订单归属校验仍保留。

本分支仍基于 `1300b13`，合并 main 时须逐项保留：安全出站标记、客诉即时员工通知、住宿房态意图统一识别、审核事实在投递改写中的保留、生产 outbox 的 conversation_id 接线。不能用整文件覆盖解决冲突；D01 补测安全提示收到新消息后仍发送，普通旧回复继续丢弃。未进行提交、发布或部署。

## 2026-09-26 连续实施补充（用户要求「做完」）

本次实施同时采用 P9–P14 补充 Spec 和 `docs/reviews/2026-09-26_codex-boundaries-rebase-handoff.md`；P12 按已确认新版。知识存量迁移改用交接第 0 节：启用条目按标题/类别分 public 或 global，停用条目待审核，不猜房间，提供只读复核清单。此决定替代原第 5 节全部存量待审核的默认值。整合固定 main `7ca055e`，迁移顺接 `0028_guest_plaintext`，知识与确认迁移顺延 0029/0030。

实施顺序：整合已发布行为与问价格式 → 知识迁移和回归夹具 → P9 交还、P10 危险分级、P11 语言 → P12 核对任务 → P13 通知 → 四项审查反例及联合验收。仅本地实现、合成数据与隔离数据库验证；真实模型调用及生产操作仍单独授权。
