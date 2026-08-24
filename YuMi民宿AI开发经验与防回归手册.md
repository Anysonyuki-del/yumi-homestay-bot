---
title: YuMi 民宿 AI 开发经验与防回归手册
date: 2026-08-01
status: active
tags:
  - YuMi民宿
  - AI客服
  - 企业微信
  - 百居易
  - 防回归
aliases:
  - YuMi 开发经验
  - 民宿机器人防回归手册
---

# YuMi 民宿 AI 开发经验与防回归手册

> [!important] 使用方式
> 这是一份长期有效的工程约束，不是历史流水账。修改消息链路、模型提示词、百居易接口、人工接管、客户上下文、任务、凭证发送或后台界面前，必须先检查对应章节；完成后必须执行[提交和部署前强制检查清单](#提交和部署前强制检查清单)。

## 信息来源与可信边界

- 尚未归并的新经验：[项目 Lessons 收件箱](tasks/lessons.md)。
- 当前实施与验收证据：[项目 Todo 与 Review](tasks/todo.md)；完成记录由 Git 历史保留。
- 具体行为以各节列出的 `文件::符号` 和回归测试为准。
- 自动化测试通过不等于企业微信、DeepSeek 或百居易真实链路通过；外部契约被跳过时必须明确写“未验证”。
- 本文不记录 API Key、Secret、Token、门锁密码、二维码、完整手机号或客人聊天原文。

## 版本与发布边界

| 编号类型 | 唯一来源 | 展示位置 | 变更条件 |
|---|---|---|---|
| 应用发布版本 | `pyproject.toml::project.version` | 后台侧栏、系统诊断、FastAPI 元数据 | 按语义化版本随正式发布更新 |
| 数据库迁移版本 | `migrations/versions/` 与 Alembic head | Alembic 与部署验证 | 数据库结构变化时更新 |
| 运行配置 revision | 运行 registry；数据库记录只作标注回退 | 系统诊断、配置版本页 | 管理员激活或回滚配置时更新 |
| Git 构建标识 | 提交哈希与同号标签 | 部署记录 | 每次提交或发布变化 |

- 应用版本只从安装包元数据读取；源码未安装时显示 `development`，禁止回退成看似正式的旧编号。
- 修复兼容问题增加补丁号，例如 `1.0.1`；向后兼容的新功能增加次版本号，例如 `1.1.0`；破坏接口或数据契约才增加主版本号。
- 正式标签必须与 `pyproject.toml` 完全同号；不得用数据库迁移号或配置 revision 代替发布标签。

代码依据：

- `src/homestay_bot/version.py::get_app_version()`
- `src/homestay_bot/version.py::get_app_version_label()`
- `src/homestay_bot/application.py::application_lifespan()`
- `src/homestay_bot/web.py::base_template_context()`

## 十条不可违反的原则

1. **回复成功以客人实际收到为准。** `job=COMPLETED`、HTTP 200 或消息入库都不能单独证明最终回复已送达。
2. **同步入口和异步入口必须执行相同业务规则。** 修改 `ConversationService.handle_message()` 时必须同步检查 `process_recorded_message()`。
3. **先提交快速安抚，再执行耗时调用。** 安抚、最终回复必须使用不同阶段的幂等键。
4. **每个异步任务必须绑定来源消息边界。** 旧任务不得读取新消息后再回答旧问题。
5. **实时事实只能来自确定性数据源。** 房态和参考价以百居易结果为准，AI 只负责理解问题与组织语言。
6. **未知民宿专属事实不得编造。** 可以提供替代建议，并提醒员工补知识库，但不能把未知信息说成已确认。
7. **AI 不做经营决策。** 退款、赔偿、价格承诺、投诉责任、改期和提前入住由人工决定。
8. **敏感操作必须有确定性安全门。** 房间可入住、订单归属、凭证发送和客户合并不能只依赖模型判断。
9. **先有失败证据，再改代码。** Bug 修复必须完成 TDD 的红、绿、全量回归三个阶段。
10. **部署不是复制文件结束。** 必须备份、核对哈希、重启、检查健康状态，再用真实测试消息验收。

## 企业微信消息链路

### 快速安抚与最终回复

- `ConversationService._stage_fast_ack()` 负责快速安抚和最终任务入队；在调用 DeepSeek、百居易或联网搜索前，必须先提交安抚 outbox 与最终任务。
- 安抚只表达“已收到、正在处理、请稍等”，不能回答房态、价格或任务是否完成；正式模型上下文不得包含安抚消息。
- 同一来源消息的出站阶段必须显式区分 `ack` 和 `final`，不能依赖事务内序号生成幂等键。
- 最终任务重试必须在消息记录层保持幂等，不能重复发送同一回复。
- 安抚模型必须有短超时和固定温暖兜底，文本包含管家身份和等待语义，避免生成生硬或无效短句。
- 快速安抚必须按意图门控；房间介绍、普通 FAQ 等信息问题直接排最终回复，不发送泛化安抚，补给、维修等需要等待的服务请求才发送。

代码依据：

- `src/homestay_bot/services/conversation_service.py::ConversationService._stage_fast_ack()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._send_guest_reply()`
- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant.respond_ack()`
- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._fast_ack_fallback()`

### 异步时序和消息边界

- `ConversationService.process_recorded_message()` 必须检查来源消息之后是否出现了更新的客人消息；旧最终回复不得覆盖新问题。
- `_process_model_reply()` 构造上下文时必须传入 `through_external_message_id`，只读取当前来源消息之前的正式上下文。
- 企业微信消息时间统一以 UTC 入库；模型上下文按系统实际处理顺序恢复，不能混排外部时间和本地时间。
- 正式生成任务应使用独立 worker，避免单个慢模型请求阻塞后续客人的快速安抚。
- 验收时不仅检查“有回复”，还要检查回复是否对应最新一条客人问题。

代码依据：

- `src/homestay_bot/services/conversation_service.py::ConversationService.process_recorded_message()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._process_model_reply()`
- `src/homestay_bot/services/message_service.py::MessageService.build_context()`
- `src/homestay_bot/worker.py::Worker.run_once()`

### 轮询、限流和平台状态

- 客人消息可以较高频率补拉，但客服账号列表必须单独缓存；每轮都请求账号列表会触发企业微信 `45009` 限流。
- API access token 应复用缓存，不能每次发送或补拉都重新获取。
- 平台接受消息只表示 `PLATFORM_ACCEPTED`，不等于客人已读或已收到；系统没有可靠已读回执时不得虚构 `DELIVERED`。
- 发送超时或结果不明确时不得盲目重放；只有确定连接未建立的失败才允许有限重试。
- 日志只记录错误码、异常类型和退避时间，不记录 Token、客人正文或模型原始响应。

代码依据：

- `src/homestay_bot/integrations/wecom/api_client.py::WeComApiClient._get_access_token()`
- `src/homestay_bot/integrations/wecom/api_client.py::WeComApiClient.list_kf_accounts()`
- `src/homestay_bot/worker.py::WeComMessagePoller.run_once()`
- `src/homestay_bot/services/lifecycle_reminders.py::LifecycleReminderService.deliver()`

### 连续消息静默合并

- 普通文本先等待三秒静默窗口，再按数据库入库顺序合并；不能对客人拆开的每个片段分别安抚或分别调用模型。
- 原始消息仍须逐条保存，合并文本只作为本轮判断与生成边界；消息数和总字符数必须有硬上限。
- 紧急、客诉和人工接管规则既要逐条即时识别，也要在合并后复核，防止关键词被拆到两条消息中绕过安全门。
- 图片、语音、员工回复或更新客人消息出现后，旧静默任务必须失效；会话行锁要覆盖“检查活动到写出站任务”的完整窗口。
- 合并后的快速安抚与最终回复是两个合法阶段；最终安全文本若与已成功发送的安抚完全相同才抑制，包含新建议时仍须发送。

代码依据：

- `src/homestay_bot/services/conversation_service.py::ConversationService.process_debounced_message()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._enqueue_debounce()`
- `src/homestay_bot/services/message_service.py::MessageService.build_guest_batch()`
- `src/homestay_bot/repositories/conversations.py::SQLAlchemyConversationRepository.lock_activity()`

## 人工接管与客诉

### 风险识别顺序

处理顺序必须保持：

1. 保存并去重入站消息。
2. 识别员工消息，避免机器人回环。
3. 识别紧急事件。
4. 用确定性规则识别客诉、退款、赔偿、差评、举报和强烈情绪。
5. 再决定机器人回答、快速安抚或普通人工接管。

客诉识别必须发生在普通模型调用前。客人侧只发送固定安抚；DeepSeek 只能在后台整理脱敏事实和草稿，不能判断责任或承诺退款赔偿。

- 客诉分析输入必须脱敏且不携带客人身份；员工发送草稿前必须经过权限、CSRF、版本和审计校验。
- 模型返回的退款、平台升级和责任风险字段不得直接决定流程；非布尔或非文字值必须由本地规则重算或回退为“待核实”。

代码依据：

- `src/homestay_bot/services/conversation_service.py::ConversationService.handle_message()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._enter_complaint_mode()`
- `src/homestay_bot/services/complaint_service.py::ComplaintService.classify()`
- `src/homestay_bot/services/complaint_service.py::ComplaintService.guest_acknowledgement()`

### HUMAN_ACTIVE 不是全局禁答开关

- 人工正在处理一条投诉时，客人后来询问房态、旅游、WiFi 等独立低风险问题，机器人仍应自动回答。
- 当前消息本身是退款、投诉、价格承诺等高风险事项时，才继续交给人工。
- 同步入口 `handle_message()` 和后台入口 `process_recorded_message()` 必须以相同规则判断。
- 机器人回答低风险问题后，会话仍保持 `HUMAN_ACTIVE`，不能自动宣告客诉已经处理完成。

## DeepSeek 与回复质量

### 模型职责边界

- 第一阶段模型负责快速、温暖地确认收到消息。
- 正式模型负责理解意图、提取自然日期、决定查询知识库或只读工具，并整理旅客可读回复。
- 房态、价格、订单、退款、付款等实时或交易事实不得由模型猜测。
- 服务需求如补水、补纸巾、加被子应先保留任务建议，不能被“房源知识未确认”分支覆盖。
- 客人可见文本不得泄露“以员工确认为准”“工作人员确认”等内部流程；应表达已收到、正在安排、后续反馈，同时不虚构已完成。
- 客人可见出口必须共享确定性的承诺和高危策略；模型提示词只能引导口吻，不能单独保证不虚构已完成、到场时间或处理结果。
- 高危转人工文案保持理性、中立，不判断责任、不承诺结果；火灾、燃气等现实危险先给出撤离和报警动作，再说明联系值班管家。

代码依据：

- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant.respond()`
- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._validate_decision()`
- `src/homestay_bot/services/guest_reply_policy.py::prepare_guest_reply()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._record_task_suggestion()`

### 自然日期和近期信息

- 提示词必须明确传入武汉当前日期，不能假设模型自己知道当天日期。
- “今天入住明天退房”“某日住一晚”“后天退房”等可唯一推断的表达必须直接换算并查询，不能要求客人重复提供绝对日期。
- “房源列表”“有哪些房型”等短追问应沿用上一轮已确认的入住和退房日期。
- 武汉近期旅游信息优先展示未来 15 天；过期来源必须过滤。
- 演出需要完整具体日期；展览可接受覆盖当前窗口的展期，但不能凭空补年份。
- 客人侧不显示网址、Markdown 链接或裸链接；来源仅在后台用于事实核验。
- 天气回复不显示 Markdown 强调、`查询日期`、`参考来源` 等内部标签；客人侧使用自然时效表达和最多两个去重来源名称。
- 雨具提醒按语义去重，覆盖带伞、雨伞、晴雨伞、备伞和携带伞等常见表达；未指定城市的旅游搜索默认使用武汉，客人明确指定地点时保留原地点。

代码依据：

- `src/homestay_bot/integrations/deepseek_client.py::_wuhan_today()`
- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._should_force_availability()`
- `src/homestay_bot/integrations/deepseek_tourism.py::DeepSeekTourismSearcher.search()`
- `src/homestay_bot/integrations/deepseek_tourism.py::DeepSeekTourismSearcher._has_valid_recent_event_dates()`

### 模型失败恢复

- 系统固定失败文案和对应的未成功问题不得再次作为正常历史交给模型，否则模型会模仿故障话术。
- DeepSeek 结构化输出偶发空白时，重试必须缩短为只携带已脱敏的当前问题，不能原样重放长上下文。
- 房态工具已经成功，但第二轮 JSON 校验失败时，应保留已查询日期并返回不猜测房型的安全回执，同时提醒员工核实具体结果。
- 百居易已返回有效结果后，知识缺口逻辑不得覆盖工具事实。
- 回复长度应先由模型精简选优，目标约 1000 字；只有模型精简失败时才使用 1500 字绝对上限兜底。
- 外部模型错误日志只能包含轮次和异常类型。
- 生产模型请求和连接探针使用不同超时；排查 worker 差异时必须复用生产 transport、timeout 和 SDK，不以直接请求成功替代生产链验证。
- 旅游搜索保留模型深度思考；思考块和搜索证据不占客人回复预算，不能用关闭思考掩盖空正文问题。

### 企业微信安全拦截后的事实保留

- `kf/send_msg` 返回消息编号只表示平台受理；收到 `msg_send_fail.fail_type=13` 才能确认平台安全限制，不能靠猜测词句提前压缩所有正常回答。
- 第一次安全拦截后只允许一次无联网改写；输入为脱敏问题和已验证原答案，必须保留日期、地点、温度、降雨、票价、开放时间或路线等核心事实，不得新增事实。
- 改写结果继续经过纯文本清理、承诺过滤、民宿专属事实过滤和长度上限；来源标签、网址、Markdown、列表残片和内部流程不得进入客人正文。
- 模型改写不可用时才进入确定性分类兜底；第二次发送失败只通知员工，不得形成改写或重试循环。
- 真实验收以客人最终收到的正文为准，同时核对失败消息、改写任务、重试消息和最终平台状态的审计关联。
- 正常回答不能为了预防平台拦截而统一压缩成安全短答案；只有收到明确安全失败后才基于已验证事实改写。
- 旅游和天气回复同样经过民宿专属事实过滤，不能把搜索证据误当成本店设施或服务已确认。

代码依据：

- `src/homestay_bot/application.py::_handle_guest_delivery_failure()`
- `src/homestay_bot/services/delivery_rewrite_job.py::GuestDeliveryRewriteJobService.handle()`
- `src/homestay_bot/integrations/deepseek_delivery_rewriter.py::DeepSeekDeliveryRewriter.rewrite()`
- `tests/unit/test_delivery_rewrite_job.py`

- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._refine_reply()`
- `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant._availability_fallback()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._limit_assistant_reply()`

## 百居易接口

- 所有接口响应必须先检查统一信封的 `error_code`，不能仅依据 HTTP 200 判断成功。
- `/listings/calendar` 的 `restrictions` 是可选字段，可能缺失或为 `null`；进入领域模型前统一归一化为 `{}`。
- 日期、库存和价格解析必须使用结构化模型，不用字符串拼接猜字段。
- 客服查询只调用只读工具；创建或修改预订必须经过明确人工审批、幂等键和审计。
- 真实契约测试默认只读，不创建订单、不修改房态、不发送凭证。
- 百居易已经返回事实时，回复层只能组织语言，不能改写库存数量或日期。
- 房间介绍和房源名称必须读取 `/properties` 的 `title`；房态结果必须把 `property_id` 与 `property_title` 一起传给模型，客人侧不得显示内部编号代替房间名。
- 后台运营状态、房间主日历 `/availabilities` 和渠道库存 `/listings/calendar` 是不同口径；判断指定住宿晚能否新订时以主日历为准，并结合已接受订单区间解释冲突。

代码依据：

- `src/homestay_bot/integrations/hostex_client.py::HostexClient._request()`
- `src/homestay_bot/integrations/hostex_client.py::ListingCalendarDay.normalize_optional_restrictions()`
- `src/homestay_bot/integrations/hostex_client.py::HostexClient.list_availabilities()`
- `src/homestay_bot/integrations/deepseek_client.py::HostexReadOnlyToolExecutor.execute()`

## 知识库与高频 FAQ

- 已审核知识优先；普通问题没有知识时由大模型给出合理通用建议，不因低置信度直接转人工。
- 民宿专属事实缺失时必须说明尚未确认、给出可行替代建议，并提醒员工补充知识库。
- 高频问题按 72 小时窗口统计，第 3 次才生成候选；不限制客人，同一客人的重复提问可以累计。
- 动态房态、价格、订单、退款、投诉等问题不进入普通 FAQ 候选。
- 达到阈值后先由模型生成可编辑 FAQ 草稿，再通知管理员；草稿未经确认不得进入正式知识上下文。
- 提醒示例必须脱敏，最多保留 3 条，不展示客人身份。
- FAQ 统计、草稿或通知失败不能回滚客人已经收到的正常回复。
- 普通问题也要过滤模型主动夹带的未经审核房型、设施和公共空间宣传；删除列表项后重新编号并清理残留销售话术。
- 交易事实无法确认时通知员工核实，但在员工真正回复前不把整个会话永久切出机器人模式。

代码依据：

- `src/homestay_bot/services/faq_candidate_service.py::FrequentFaqService.track()`
- `src/homestay_bot/services/faq_candidate_context.py::FaqCandidateContextService.build_context()`
- `src/homestay_bot/services/faq_draft_job.py::FaqDraftJobService.handle()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._track_frequent_faq()`

## 客户 CRM、上下文与隐私

- 每个正式客户拥有独立档案和独立上下文，不能只用会话 ID 临时拼接身份。
- 原始对话保留 7 天；短摘要成功后才标记已摘要，长期摘要成功后才清理过期原文。
- 摘要失败必须保留原文，不能为了节省空间先删后补。
- 模型只接收完成当前任务所需的最小化个人信息；手机号、证件号、邮箱和订单号应脱敏。
- 客户合并必须锁定来源、目标和建议，使用固定锁顺序，在同一事务中迁移身份、会话、订单、任务、标签、备注和摘要。
- 管理员合并页面只展示脱敏预览并要求 CSRF 与二次确认；审计不得复制备注、摘要或聊天正文。
- 员工通知优先显示唯一有效订单的房间号；无法唯一匹配时显示客人名称，绝不显示复杂 UID。
- 自动入住备注是订单派生视图，必须与员工手写备注分栏保存、展示和搜索；不得覆盖或拼接员工备注。
- “最新入住”按业务状态选择：当前入住优先；实际退房首次观测日起保留三天，第四天才切换到未来订单；缺少真实观测时不得伪造退房日期。
- 列表页必须批量计算自动入住备注，禁止按客户逐条查询；客户合并后按目标客户全部订单重新择优。
- 企业微信员工通知优先显示自动入住备注，再回退员工备注和客人名称；任何路径不得显示平台 UID。

代码依据：

- `src/homestay_bot/services/context_retention.py::ContextRetentionService.maintain_customer()`
- `src/homestay_bot/repositories/context.py::SQLAlchemyContextRepository.save_long_summary_and_purge()`
- `src/homestay_bot/repositories/context.py::SQLAlchemyContextRepository.get_customer_room_number()`
- `src/homestay_bot/repositories/customers.py::SQLAlchemyCustomerRepository.merge_locked()`
- `src/homestay_bot/repositories/customers.py::SQLAlchemyCustomerRepository.latest_stay_notes()`
- `src/homestay_bot/repositories/customers.py::SQLAlchemyCustomerRepository.get_customer_notification_note()`
- `src/homestay_bot/services/conversation_service.py::ConversationService._notify_employee()`

## 房间状态、任务与凭证

- AI 只创建待确认任务，不能声称矿泉水、纸巾、加被子或维修已经完成。
- 当前任务执行员工完成检查清单和规定照片后，可以把房间从“待检查”改为“可入住”；不应错误限制为只有管理员。
- 普通员工只能查看分配给自己的任务和必要资料，不能因房间状态权限获得全部 CRM、订单金额或其他房间凭证。
- “可入住”必须经过确定性条件：订单、客户、房间、入住日期、任务状态和必要材料一致。
- 二维码、门锁密码、入住指南分段发送并分别记录状态；部分成功后重试不得重复发送已成功部分。
- 凭证发送失败、超时或会话窗口失效应转人工任务，不能让模型绕过安全门。

代码依据：

- `src/homestay_bot/routes/tasks.py::mark_room_ready()`
- `src/homestay_bot/services/room_readiness_service.py::RoomReadinessService.mark_ready()`
- `src/homestay_bot/services/credential_delivery.py::CredentialSafetyRules.invalid_reason()`
- `src/homestay_bot/services/credential_delivery.py::CredentialDeliveryService.evaluate()`
- `src/homestay_bot/services/credential_delivery.py::CredentialPartSender.handle()`

## 生命周期提醒

- 武汉本地计划时间统一转换为 UTC 入队。
- 幂等键必须包含订单、提醒类型和本地计划日期；订单改期要撤销旧计划。
- 发送前复核 48 小时会话窗口和窗口内消息数量上限。
- 企业微信异步失败码 4、5、6、10 转人工联系，不盲目重发。
- 天气查询失败时仍可发送不含链接的路线、停车和注意事项。
- 退房感谢不立即索要好评。

代码依据：

- `src/homestay_bot/services/lifecycle_reminders.py::LifecycleReminderService.schedule_for_order()`
- `src/homestay_bot/services/lifecycle_reminders.py::LifecycleReminderService.deliver()`
- `src/homestay_bot/services/lifecycle_reminders.py::LifecycleReminderService.handle_send_failure()`

## 变更前影响面检查

### 区分微信服务号与企业微信客服

- 涉及微信客户入口时，先区分公众号服务号自定义菜单、企业微信微信客服会话和项目后台网页。
- 服务号底部“转人工客服”由公众号菜单、公众号消息回调和客服消息能力组成，不等于给 CRM 页面加固定底栏按钮。
- 设计转接前必须确认公众号主体与认证状态、菜单事件类型、人工客服承载平台、企业微信客服账号和会话状态如何同步。
- 只有代码、配置和真实回调验收均具备时，文档和页面才能宣称服务号入口已经接通。

经验依据：

- `src/homestay_bot/routes/wecom_callback.py`
- `src/homestay_bot/integrations/wecom/api_client.py::WeComApiClient`

### 修改会话或人工接管

- [ ] 检查 `handle_message()` 同步路径。
- [ ] 检查 `process_recorded_message()` 异步路径。
- [ ] 检查员工消息不会触发机器人回环。
- [ ] 检查紧急、客诉、高风险、低风险四类消息。
- [ ] 检查模式是否按预期保持或切换。
- [ ] 检查快速安抚和最终回复的消息数量。

### 修改模型提示词或结构化输出

- [ ] 检查普通问答、房态工具、旅游搜索、服务任务和知识缺口。
- [ ] 检查空白、非法 JSON、超时和第二轮失败。
- [ ] 检查无链接、无内部术语、无虚构专属事实。
- [ ] 检查自然日期和武汉当前日期。
- [ ] 检查语义精简与 1500 字硬上限。
- [ ] 用真实长会话契约验证，不只跑单轮 mock。

### 修改企业微信或 worker

- [ ] 检查入站去重、outbox 去重和任务去重三层边界。
- [ ] 检查 `ack`、`final`、员工通知和生命周期提醒分别使用独立键。
- [ ] 检查任务崩溃恢复、超时不明和明确连接失败。
- [ ] 检查账号列表缓存，防止 `45009`。
- [ ] 检查日志和任务 payload 不含密钥或不必要原文。

### 修改百居易或房态逻辑

- [ ] 对照官方文档核实字段必填性、空值和统一错误信封。
- [ ] 先跑响应模型单元测试，再跑真实只读契约。
- [ ] 检查日期范围、时区、房源数量和库存语义。
- [ ] 确认测试不会创建订单或修改房态。
- [ ] 检查工具成功后的模型失败恢复。

### 修改客户、任务、房间或凭证

- [ ] 检查客户隔离和员工最小权限。
- [ ] 检查事务原子性、锁顺序、幂等重放和 CSRF。
- [ ] 检查摘要成功后才清理原文。
- [ ] 检查任务仍为待确认，没有虚构完成。
- [ ] 检查凭证发送安全条件和分段幂等。
- [ ] 检查审计记录不含敏感正文。

## 后台管理台安全边界

### 事务、日志与迁移

- 唯一键竞争使用事务保存点或原子 upsert；共享业务会话中不得直接 `rollback()` 丢弃同事务消息和出站任务。
- 日志脱敏安装在实际输出 handler，并覆盖格式化后的消息、字典消息和 `extra`；Uvicorn 访问日志保留格式化器依赖的参数协议，只清理 URL 中的敏感值。
- 非文本企业微信消息只保存受控媒体编号等最小元数据，媒体内容进入独立下载、类型校验和人工审核流程。
- 异步出站只有在平台返回真实消息编号后才标记成功；入队、失败和成功状态必须可区分。
- SQLite 删除列或新增唯一约束使用 Alembic batch 模式；离线 SQL 不能执行依赖现有数据的查询回填。
- 运行依赖和类型桩分别声明；升级服务后检查实际运行环境，不以开发环境安装结果代替。

### 未认证路径的容量与限速

- 任何"每主体上限"落到未认证路径都会退化成全局上限。匿名 CSRF nonce 必须绑定浏览器会话自身标识，并单独设置匿名子上限；不得让未认证访客与管理员共用同一个作用域或同一份容量。
- 登录页签发令牌与凭据提交必须使用各自独立的全局计数。共用一个桶时，成本最低的那类请求就能挤掉最关键的那类。
- 限速默认参数必须指向本类别常量。改动限速时要同时核对构造器默认值和每个调用点显式传入的值。
- 单进程内存限速只挡单机滥用。分布式来源打满某类别上限仍会影响该类别，公网部署前必须记为残留风险。

### 一次性令牌的两条独立性质

- "只能消费一次"与"只能被签发它的浏览器消费"是两条独立性质，必须各有独立测试。
- 用无效 Cookie 配真实 token 断言成功的测试，实际固化的是跨浏览器可复用的缺陷，不是并发安全。

### 响应头与角色校验

- 敏感响应头由中间件按路径前缀统一覆盖，不逐路由手工补。逐路由补的结果是渲染客人档案和客诉正文的页面被遗漏。
- 为流式响应补头必须用纯 ASGI 中间件；`BaseHTTPMiddleware` 会包装 `FileResponse`，影响私有附件下载。
- 路由取出 `role` 却不判断等于没有权限校验。角色边界必须在路由内显式断言，不得依赖"某类角色当前登不进来"这一外部前提。
- 日志脱敏正则要按凭据实际命名逐个核对，并同时补"不应脱敏"的反向测试，避免规则放宽后误伤业务幂等键。

### 高密度运营界面

- 后台视觉优化不能改变角色、CSRF、确认弹窗、脏表单提示或诊断脱敏等业务边界；先统一公共外壳，再逐页调整内容密度。
- 桌面端核心列表使用稳定列序的数据表，移动端转为信息卡片；仅审计等无法合理卡片化的宽表允许受控横向滚动。
- 页面只能有一个可见 `h1`；侧栏按“运营、客户与内容、系统管理”分组，避免把所有入口堆成无层级列表。
- 表单提交要立即显示“正在处理…”、`aria-busy` 并阻止重复提交；失败后仍由服务端错误状态恢复，不得只靠前端假成功。
- 移动抽屉必须管理 `inert`、`aria-hidden`、Escape 关闭和焦点返回；无 JavaScript 时保留可达的核心页面导航。
- 版本号放在侧栏次要位置，来源与系统诊断一致；不得为了展示版本在模板中硬编码第二份数字。
- 管理员新密码允许自定长度，但模板与服务端都必须拒绝空白并保留 128 字符输入上限；房间密码和 API 密钥使用各自独立规则。
- 后台列表同时实现稳定排序、页码、`limit + 1` 下一页判断和模板导航，不能只在服务层设置固定上限。
- 模板、CSS 和 JavaScript 同次发布时，静态资源 URL 使用应用版本作为缓存键；验收检查浏览器实际请求和计算样式，不只检查服务器文件。

### 系统诊断文案

- 诊断展示运行时实际生效值；回退数据库记录时明确标记来源和“未确认已生效”，不能把存储值冒充运行值。
- 空集合只表示探针成功且没有数据；探针失败必须显示“无法读取”，不能显示“无”。
- 首屏只展示异常、未配置、积压和失败等偏差；完整受控状态留在折叠报告或机器健康接口。
- 任务计数、错误码、配置 revision 和健康检查独立降级，单项失败不能清空其他已成功读取的数据。

代码依据：

- `src/homestay_bot/templates/layouts/admin.html`
- `src/homestay_bot/web.py::base_template_context()`
- `src/homestay_bot/static/admin.js`
- `src/homestay_bot/services/admin_diagnostics_service.py::AdminDiagnosticsService.snapshot()`
- `src/homestay_bot/routes/admin.py::admin_diagnostics()`
- `tests/unit/test_admin_assets.py`

## 提交和部署前强制检查清单

### 代码验证

- [ ] 先运行新增回归测试，确认修改前因目标行为缺失而失败。
- [ ] 实施最小修复后，目标测试转绿。
- [ ] 运行相关模块测试，覆盖相邻分支和失败路径。
- [ ] 运行全量 `pytest`，记录通过、跳过和警告数量。
- [ ] 运行 Ruff、mypy 和 `git diff --check`。
- [ ] 只格式化本次触碰的白名单文件；前后核对 `git status --short` 与 `git diff --name-only`，发现范围扩大立即停止。
- [ ] 检查所有跳过的真实契约，不能把跳过写成通过。

### 部署验证

- [ ] 记录旧 Git 提交，备份数据库、`.env` 和私有上传目录；备份路径和日志不能输出密钥。
- [ ] 只同步本次需要的文件，不覆盖用户其他未提交修改。
- [ ] 仅允许 tracked worktree 干净时执行 `git pull --ff-only`，保留服务器既有未跟踪环境备份。
- [ ] 通过 `docker compose up -d --build api` 重建 API，PostgreSQL 容器不得无故重建。
- [ ] 检查 `http://127.0.0.1:8000/health` 与公网 `/health` 都返回 HTTP 200。
- [ ] 核对应用版本、Git 提交、Alembic current/head 和运行配置 revision，不能只看一个编号。
- [ ] 检查数据库、worker、企业微信轮询、百居易同步、上下文维护和生命周期调度状态。
- [ ] 检查新日志没有异常，也没有泄露密钥、密码、二维码、完整手机号或客人正文。

### 企业微信真实验收

- [ ] 使用新的测试消息，不重放旧已处理任务，避免重复回复。
- [ ] 记录入站消息 ID、安抚消息 ID、最终消息 ID 和相应任务 ID。
- [ ] 确认客人实际收到，并核对语义对应最新问题。
- [ ] 房态问题确认实际调用百居易只读查询。
- [ ] 服务需求确认只生成待确认任务，不发送凭证。
- [ ] 客诉确认只发固定安抚并建立人工复核。
- [ ] 人工模式下再发低风险问题，确认仍有自动最终回复且模式不退出。
- [ ] 检查凭证投递和房间凭证数量没有因普通测试增加。

## 环境相关边界

以下边界需要在目标环境中单独验收，不能用本机单元测试替代：

- SQLite 与 PostgreSQL 的锁、迁移和并发语义不同；涉及这些行为的变更必须在实际目标数据库复核。
- DeepSeek、百居易和企业微信真实契约默认受显式开关保护；未开启的测试只能记为 skipped。
- 健康状态中的 `web_search=unknown` 表示尚无本轮搜索健康证据，不等于失败，也不等于通过。
- `wecom_contact_sync=not_configured` 只表示当前运行配置没有该能力，不能据此声称员工名称链路已真实验证。
- 企业微信没有可靠客户已读回执，生命周期消息只能记录平台受理或人工跟进状态。
- 使用单进程内存限速时，分布式来源仍可能打满全局类别上限；需要跨实例保障时必须使用共享计数存储。
- 公众号自定义菜单、公众号消息回调和企业微信客服转接是独立集成，不能用后台页面按钮替代真实链路验收。
- `fail_type=13` 只有平台级安全限制含义，无法从该编号断言唯一触发词；策略必须基于实际失败正文回放和最终客人收件验收。

## 维护规则

- 新教训先写入 [Lessons 收件箱](tasks/lessons.md)；确认长期有效并找到代码或测试依据后归并到本文，归并后从 Lessons 删除正文，由 Git 保留历史。
- 新经验必须有具体代码符号、测试或运行日志作为出处；没有证据的推断不得写成规则。
- 代码行为与本文冲突时，先核实当前正式 Spec；若本文过时，先更新本文，再实施代码变更。
- 同类规则只保留一个权威版本，避免多个说明相互矛盾。
- 行为文案变化后搜索旧措辞，依次检查路由说明、集中消息、模板、README、配置模板，以及项目实际存在的 CLI help、插件或 MCP 描述；不存在的表面不创建占位文档。
- `tasks/todo.md` 只保存一个当前工作单和本次 Review；完成后的过程与部署证据由 Git 历史保留，不在长期文档复制运行状态。
