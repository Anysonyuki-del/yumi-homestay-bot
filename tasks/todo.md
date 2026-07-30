# 当前任务：高频 FAQ 归纳提醒

## 轻量 Spec

### 现状依据

- 客人、机器人和客服消息统一保存在 `src/homestay_bot/domain/models.py` 的 `Message`，当前 `src/homestay_bot/repositories/conversations.py` 的 `SQLAlchemyMessageRepository.list_recent()` 只支持单会话近期上下文，不支持跨会话高频统计。
- `src/homestay_bot/services/conversation_service.py` 的 `ConversationService.handle_message()` 只会对单次 `knowledge_gap` 立即提醒，没有三天聚类、提醒冷却、草稿或处理状态。
- `src/homestay_bot/integrations/deepseek_client.py` 的 `DeepSeekGuestAssistant.respond()` 已使用同一次结构化响应返回意图和知识缺口，可在不增加客人等待调用次数的前提下返回候选标准问题和已有候选编号。
- `src/homestay_bot/application.py` 的 `TransactionalOutboxWeCom.send_internal_text()` 已支持事务内登记员工通知，真实发送由 worker 重试，不需要同步等待企业微信。
- `src/homestay_bot/routes/knowledge.py` 的 `KnowledgeAdminService.create()` 会创建并启用人工审核知识，`_require_admin()` 和 `_consume_csrf()` 已提供管理员权限与表单安全边界。

### 功能规则

- 统计滚动 72 小时内的问题；同一语义主题出现 3 次即达到首次提醒门槛，不限制是否来自同一客人。
- 只统计知识库尚未覆盖或答案不足、适合形成固定 FAQ 的问题。
- 房态、价格、订单、退款、预订、实时旅游和紧急问题不得进入候选。
- 相同语义的不同问法合并；现有未关闭候选最多向 DeepSeek 提供 50 条编号和标准问题，模型返回已有编号或新标准问题，不提供客人身份和示例正文。
- 首次达到 3 次后创建 FAQ 草稿后台任务；客人回复不等待草稿生成。
- 每新增 3 次且距离上次提醒超过 24 小时再次提醒；有新增脱敏示例时更新草稿，没有新示例时复用原草稿。
- 管理员关闭候选后暂停统计和提醒 30 天；关闭期内不记录次数，期满后从零重新累计，达到门槛可重新打开并生成新草稿。
- 已转为正式知识的问题不再参与候选统计。

### 数据与隐私

- 新增待归纳候选，保存标准问题、建议分类、状态、累计次数、最近提醒时间、草稿状态、正式知识编号和关闭截止时间。
- 新增候选出现记录，只保存候选编号、来源消息去重编号和 UTC 出现时间，用于滚动 72 小时统计；不复制客人正文。
- 每个候选最多保存 3 条参考示例；入库前遮盖手机号、订单号、身份证号和邮箱，不保存客人 ID。
- 归纳或关闭后删除示例与未采用草稿正文；超过 72 小时的出现明细定期清理，只保留累计次数。
- 所有归纳、关闭和重新打开操作写入 `AuditLog`，审计只保存候选编号、知识编号和动作。

### DeepSeek FAQ 草稿

- 达到门槛后使用独立后台任务调用 DeepSeek，不增加客人回复链路的模型调用。
- 输入只包含标准问题、最多 3 条脱敏示例和相关已审核知识。
- 输出必须是结构化草稿：分类、中文问题、中文参考答案、英文问题、英文参考答案、建议关键词和待管理员核实事项。
- 未经审核的民宿专属事实必须写为 `【待管理员确认】`，不得参考其他民宿推测。
- 草稿不进入机器人知识上下文，不自动回复客人，也不自动启用。
- 草稿生成失败时按后台任务规则重试；达到重试上限后仍提醒管理员，并标记“FAQ 草稿生成失败”，附脱敏示例供人工归纳。

### 管理员流程

- 只向数据库中启用的管理员企业微信账号发送提醒；普通员工不接收提醒，也看不到候选区域。
- 企业微信提醒包含最近 72 小时次数、标准问题、精简草稿、最多 3 条脱敏示例、待核实事项和管理页面入口。
- 知识管理页新增仅管理员可见的候选区域，展示完整草稿并支持修改。
- 管理员提交后通过现有知识创建入口生成并启用 `KnowledgeEntry`，候选标记为已转知识并关联知识编号。
- 管理员可选择“暂不收录”，候选进入 30 天关闭期。
- 没有启用管理员时保留待提醒状态；后续出现新问题或管理员恢复后再次尝试。

### 容错与性能

- 候选聚类字段无效时跳过本次统计，客人回复保持正常。
- 候选统计使用数据库保存点隔离；统计失败不得回滚客人回复或触发人工接管。
- 来源消息编号必须唯一计数，任务重试不得增加次数。
- 管理员通知通过现有事务型发件箱发送并重试。
- 所有时间计算使用 UTC；回复主链路只增加本地数据库读取和写入，不增加外部模型调用。

### 验收标准

- 72 小时内第 3 次相似问题才创建草稿任务；窗口外记录不计，同一客人重复提问可以累计。
- 相似问法归入同一候选，不同问题互不影响；排除的动态或高风险问题永不进入候选。
- FAQ 草稿包含中英文问答、关键词和待核实事项；未知民宿事实不被模型编造。
- 首次提醒、每新增 3 次且满 24 小时再提醒、关闭 30 天和重新打开行为均有自动化测试。
- 只通知启用管理员；提醒与管理页不显示客人身份，示例完成脱敏且最多 3 条。
- 归纳后创建正式知识并删除示例；关闭后删除示例和草稿。
- 重复消息、模型异常、数据库异常和企业微信发送失败不影响客人正常回复。
- 通过数据库迁移、单元测试、集成测试、Ruff、mypy、真实 DeepSeek 草稿契约和本机端到端验收后才可交付。

## Spec 确认记录

- [x] 现状分析已确认
- [x] 高频判定、范围、语义合并与提醒节奏已确认
- [x] 管理员权限、脱敏示例与 30 天关闭策略已确认
- [x] DeepSeek 先生成 FAQ 参考草稿再提醒管理员已确认
- [x] 用户审核本文件中的最终书面 Spec
- [x] 编写并确认实施计划
- [x] 按 TDD 实施、验证并部署

## 实施计划

### Task 1：候选数据模型与迁移

- [x] 先在 `tests/integration/test_faq_candidate_repository.py` 编写失败测试，覆盖候选创建、来源消息幂等、72 小时计数、最多 3 条示例、30 天关闭和过期重开。
- [x] 在 `src/homestay_bot/domain/enums.py` 增加 `KnowledgeCandidateStatus`，在 `src/homestay_bot/domain/models.py` 增加 `KnowledgeCandidate` 与 `KnowledgeCandidateOccurrence`。
- [x] 新建 `migrations/versions/0002_frequent_faq_candidates.py`，包含外键、唯一约束和候选状态、出现时间索引。
- [x] 新建 `src/homestay_bot/repositories/faq_candidates.py`，实现 `list_context()`、`get_or_create()`、`add_occurrence()`、`count_since()`、`mark_draft_*()`、`snooze()`、`convert()` 和过期明细清理。
- [x] 运行仓储测试并提交 `feat: persist frequent faq candidates`。

### Task 2：主回复内的语义候选归类

- [x] 先在 `tests/unit/test_deepseek_client.py` 编写失败测试，验证现有候选编号、标准问题、分类随同一次结构化客服响应返回，且动态、高风险或已有知识覆盖的问题被清空。
- [x] 在 `src/homestay_bot/integrations/deepseek_client.py` 扩展 `AssistantDecision` 与 JSON Schema，新增 `faq_candidate`、`faq_candidate_id`、`faq_canonical_question` 和 `faq_category`。
- [x] 新建 `src/homestay_bot/services/faq_candidate_context.py`，最多向模型提供 50 条未关闭候选编号与标准问题，不提供示例或客人身份。
- [x] 在 `src/homestay_bot/application.py` 注入短会话候选上下文，保持客人回复链路只有现有一次 DeepSeek 调用。
- [x] 运行 DeepSeek 客服单元测试并提交 `feat: classify faq candidates in guest response`。

### Task 3：72 小时高频统计与隐私处理

- [x] 先在 `tests/unit/test_faq_candidate_service.py` 编写失败测试，覆盖三次门槛、同客人累计、不同主题隔离、每新增三次且满 24 小时、关闭期不计数、消息幂等和统计异常隔离。
- [x] 新建 `src/homestay_bot/services/faq_candidate_service.py`，实现 `record()`、固定 FAQ 排除规则、UTC 窗口计算和草稿任务去重键。
- [x] 在同一服务实现手机号、订单号、身份证号和邮箱脱敏；只保留最近 3 条不同示例和示例版本。
- [x] 在 `tests/unit/test_conversation_service.py` 先验证客人回复完成后才记录候选，记录失败不回滚回复且不触发人工接管。
- [x] 在 `src/homestay_bot/services/conversation_service.py` 注入可选候选服务，并在有效 `AssistantDecision` 后执行隔离记录。
- [x] 运行服务与会话测试并提交 `feat: detect frequent faq gaps`。

### Task 4：DeepSeek FAQ 草稿后台任务

- [x] 先在 `tests/unit/test_deepseek_faq_drafter.py` 编写失败测试，验证中英文问答、关键词、待核实事项、`【待管理员确认】`、无链接和不携带客人身份。
- [x] 新建 `src/homestay_bot/integrations/deepseek_faq_drafter.py`，实现 `FaqDraft` 与 `DeepSeekFaqDrafter.generate()` 的严格 JSON 校验和安全回退。
- [x] 先在 `tests/unit/test_faq_draft_job.py` 编写失败测试，验证成功草稿、三次失败后的人工兜底通知、无新示例时复用原草稿，以及草稿不进入机器人知识上下文。
- [x] 新建 `src/homestay_bot/services/faq_draft_job.py`，实现候选草稿状态更新、失败次数、管理员收件人查询和通知正文生成。
- [x] 在 `src/homestay_bot/worker.py` 将 `faq_draft_generate` 标记为可安全恢复任务，在 `src/homestay_bot/application.py` 注册 handler 和事务型管理员通知。
- [x] 运行草稿与 worker 测试并提交 `feat: generate reviewable faq drafts`。

### Task 5：管理员候选管理与正式知识转换

- [x] 先扩展 `tests/integration/test_knowledge_routes.py`，验证普通员工不可见、管理员可见完整草稿、预填编辑、转换为启用知识、关闭 30 天、CSRF 和审计不复制正文。
- [x] 在 `src/homestay_bot/repositories/employees.py` 增加 `list_active_admin_userids()`，只返回启用管理员。
- [x] 扩展 `src/homestay_bot/routes/knowledge.py` 的管理端口与服务，增加 `list_candidates()`、`convert_candidate()` 和 `snooze_candidate()`，转换操作与候选清理保持同一事务。
- [x] 扩展 `src/homestay_bot/templates/knowledge/index.html`，复用现有 `src/homestay_bot/static/app.css`，只向管理员展示候选草稿、脱敏示例、待核实事项及转换/关闭表单。
- [x] 在 `src/homestay_bot/application.py` 扩展 `SessionKnowledgeAdminService` 装配新管理方法。
- [x] 运行知识管理集成测试并提交 `feat: review frequent faq drafts`。

### Task 6：全链路验证与本机部署

- [x] 运行迁移升级与降级测试、全部 `pytest`、Ruff 和 mypy。
- [x] 扩展 `tests/contract/test_deepseek_contract.py`，显式启用真实 DeepSeek FAQ 草稿契约，确认未知专属事实使用待确认占位。
- [x] 在临时数据库回放三条相似问法，验证只创建一个候选、一个草稿任务和一条管理员通知，且客人回复不受影响。
- [x] 独立代码审查 Critical/Important 问题并修复后重跑全部验证。
- [x] 备份本机运行代码和数据库，执行 Alembic 迁移，部署源码并重启 LaunchAgent。
- [x] 验证健康检查 HTTP 200、管理员收件人、候选表与受保护管理入口、会话保持 `BOT_ACTIVE`，在 Review 中记录证据。

## Review

- 全量自动化验证：196 passed、10 skipped；Ruff 全仓通过；mypy 检查 46 个源文件通过。
- 真实 DeepSeek FAQ 草稿契约通过：未知民宿专属事实保留 `【待管理员确认】`、非空核实项且不含链接。
- 数据库迁移完成升级、降级至 `0001_initial`、再升级至 `0002_frequent_faq_candidates` 的完整循环。
- 临时数据库三条相似问法回放结果：1 个候选、1 个草稿任务、1 条管理员通知，通知显示最近 72 小时 3 次。
- 四轮独立代码审查最终结果：Critical 0、Important 0；已覆盖并发计数、冷却游标、飞行中关闭、原子重开、隐私脱敏和通知完整性。
- 本机部署前备份位于 `/Users/rin/Library/Application Support/HomestayBot/.backups/frequent-faq-20260730-092251`。
- 本机数据库已迁移至 `0002_frequent_faq_candidates`，`XuKuang` 已设为启用管理员；两个原有会话均保持 `BOT_ACTIVE`。
- LaunchAgent `com.rin.homestay-bot` 运行中，健康检查 HTTP 200；数据库、worker、企业微信轮询和配置均为 `ok`。

# 当前任务：武汉旅游联网推荐

- [x] 分析模型、提示词与会话状态
- [x] 确认旅游信息范围与回复形式
- [x] 确认意图门控、失败升级与隐私边界
- [x] 编写并确认设计 Spec
- [x] 同步官方 `web_search` 请求边界和首次健康状态
- [x] 编写详细实施计划
- [x] 用户确认执行方式后实施
- [x] 运行单元、集成、静态检查和真实 Fenno 能力测试
- [x] 部署到本地运行目录并恢复测试会话
- [ ] 完成企业微信端到端验收
- [x] 修复“今天入住明天退房”的相对日期理解
- [x] 真实 Fenno 验证相对日期直接触发房态查询
- [ ] 部署并完成相对日期企业微信验收
- [x] 确认大模型兜底与知识缺口提醒设计
- [x] 编写并确认大模型兜底设计 Spec
- [x] 编写大模型兜底详细实施计划
- [x] 实施普通问题兜底、知识缺口提醒和交易待确认
- [x] 真实 Fenno 验证三类兜底行为
- [ ] 部署并完成大模型兜底企业微信验收
- [x] 分析 DeepSeek V4 Flash 与现有 Responses API 的兼容差异
- [x] 确认彻底弃用 Fenno 和 DeepSeek 双接口方案
- [x] 分段确认 DeepSeek 迁移设计
- [x] 编写并审核 DeepSeek 迁移 Spec
- [x] 编写 DeepSeek 迁移实施计划
- [x] 实现 DeepSeek Chat Completions 与 Anthropic Web Search 适配
- [x] 通过真实 DeepSeek 契约并切换本机配置
- [ ] 完成 DeepSeek 企业微信端到端验收
- [x] 修复失败文案污染模型上下文并保留安全诊断日志
- [x] 限制 DeepSeek 有效上下文长度，规避多轮结构化输出空白响应
- [x] DeepSeek 空白响应重试时降级为只携带当前问题
- [x] 缓存企业微信客服账号列表，解除五秒消息补拉导致的 `45009` 限流
- [x] 让“房源列表”等简短追问沿用上一轮入住退房日期查询百居易
- [x] 过滤普通回答中未经审核的民宿专属宣传
- [x] 初版将 DeepSeek 回复限制为 1000 字（现由语义精简与 1500 字硬上限替代）

## 当前变更：DeepSeek 语义精简与 1500 字硬上限

- [x] 在 `tests/unit/test_deepseek_client.py` 添加普通问答和旅游回复超长时触发一次语义精简的失败测试
- [x] 在 `src/homestay_bot/integrations/deepseek_client.py` 实现目标 1000 字的单次精简，保持事实、日期、来源和风险提示且禁止新增事实与链接
- [x] 在 `tests/unit/test_deepseek_client.py` 验证精简失败时保留原回复给发送层兜底
- [x] 在 `tests/unit/test_conversation_service.py` 将绝对上限改为 1500 字，并验证 1500 字不变、1501 字截断
- [x] 运行全量测试、Ruff、mypy 和真实 DeepSeek 精简契约
- [x] 部署本机运行服务并验证健康状态

## 当前修复：近期旅游信息与回复格式

- [x] 在 `tests/unit/test_deepseek_tourism.py` 验证当前日期及未来 15 天窗口传给 DeepSeek
- [x] 在 `tests/unit/test_deepseek_tourism.py` 验证过期来源过滤、演出年份和窗口证据校验
- [x] 在 `src/homestay_bot/integrations/deepseek_tourism.py` 实现武汉当前日期、15 天优先窗口和过期证据拒绝
- [x] 在 `tests/unit/test_deepseek_client.py` 验证删除宣传后的连续编号和无关房型推销清理
- [x] 在 `src/homestay_bot/integrations/deepseek_client.py` 实现安全行过滤后的编号重排
- [x] 运行全量测试、Ruff、mypy 和真实 DeepSeek 旅游契约
- [x] 部署本机服务并完成消息回放与健康检查

## 当前优化：企业微信回复速度（方案 A）

- [x] 在 `tests/unit/test_deepseek_tourism.py` 添加成功缓存命中、10 分钟过期、失败不缓存、日期与语言隔离测试，并先验证失败
- [x] 在 `src/homestay_bot/integrations/deepseek_tourism.py` 实现有界内存缓存，缓存键只包含规范化问题、语言和查询日期
- [x] 调整旅游搜索提示词为首轮选优 3 项、约 700 至 900 字，并降低输出令牌预算，同时保留 2 次搜索、完整年份和 15 天校验
- [x] 在 `tests/unit/test_deepseek_client.py` 添加旅游回答不再触发第二次精简请求的失败测试，并先验证失败
- [x] 在 `src/homestay_bot/integrations/deepseek_client.py` 让已校验旅游回答直接返回，普通长回复继续使用语义精简
- [x] 在 `tests/unit/test_config.py` 固化企业微信 5 秒轮询配置边界
- [x] 运行相关单元测试、全量测试、Ruff、mypy 和真实 DeepSeek 契约
- [x] 将本机 `WECOM_POLL_INTERVAL_SECONDS` 调整为 5，部署并验证健康状态、会话状态与延迟日志

## Review

- 已按 TDD 完成旅游意图门控、Fenno 联网、来源提取、失败升级和健康状态。
- 设计以一次应用层 Responses 请求为上限；模型托管搜索内部动作不由应用计数。
- 全量测试结果：117 passed、4 skipped；Ruff 通过；mypy 检查 39 个源文件通过。
- 真实 Fenno 契约测试：1 passed；Fenno 来源位于 `web_search_call.action.sources`，已兼容读取。
- 按用户反馈移除全部客人可见链接；真实 Fenno 无链接契约测试 1 passed。
- 相对日期真实契约测试：Fenno 将“今天入住明天退房”转换为 2026-07-29 至 2026-07-30，并直接调用房态工具。
- 本地健康检查：HTTP 200；`database`、`worker_heartbeat`、`wecom_polling`、`configuration` 均为 `ok`，首次联网前 `web_search` 为 `unknown`。
- 已将唯一测试会话 ID 1 恢复为 `BOT_ACTIVE`；待企业微信端到端消息验收。
- 大模型兜底全量测试：128 passed、7 skipped；Ruff 通过；mypy 检查 40 个源文件通过。
- 关键安全回归：44 passed；知识缺口和交易待确认均只提醒员工，不切换机器人会话。
- 真实 Fenno 兜底契约：3 passed；普通问题、停车信息缺口和退款金额三类行为均符合设计。
- DeepSeek 迁移后全量测试：120 passed、8 skipped；Ruff 通过；mypy 检查 41 个源文件通过。
- 真实 DeepSeek 双接口契约：6 passed；普通问答、专属知识缺口、退款待确认、相对日期房态和武汉旅游联网均通过。
- 企业微信首次 DeepSeek 验收暴露固定失败文案污染上下文；已按 TDD 移除失败轮次并增加不记录客人正文的异常类型日志。
- 企业微信第二次验收复现 DeepSeek 多轮空白响应；对照确认 5 条有效消息正常、7 条有效消息失败，现限制最近 5 条。
- 修复后全量测试：121 passed、8 skipped；Ruff 和 mypy 通过；真实 DeepSeek 契约 6 passed；历史会话实况重放正常返回。
- 企业微信第三次链路验收成功，但语义审计发现模型主动虚构民宿房型和公共空间；已增加确定性过滤。
- 专属宣传过滤后全量测试：122 passed、8 skipped；Ruff、mypy 通过；真实 DeepSeek 普通问答契约 1 passed。
- DeepSeek 回复长度限制采用发送前统一截断：1000 字保持不变，超长回复取前 999 字并追加省略号。
- 长度限制后全量测试：124 passed、8 skipped；Ruff 和 mypy 通过；数据库记录与企业微信实际发送文本一致。
- 已按用户纠正将机械截断升级为 DeepSeek 单次语义精简：目标 1000 字，1500 字为绝对上限。
- 精简会保留日期、来源和风险提示；新增链接、丢失旅游证据标签或重新引入民宿专属臆测时拒绝精简结果。
- 语义精简后全量测试：129 passed、9 skipped；Ruff 和 mypy 通过；真实 DeepSeek 完整契约 7 passed。
- 本机部署后健康检查 HTTP 200；数据库、worker、企业微信轮询和配置均为正常，会话保持 `BOT_ACTIVE`。
- 近期旅游修复把武汉当前日期和未来 15 天窗口直接交给 DeepSeek；明确过期来源被过滤，窗口外活动必须标注“半个月后”。
- 演出与展览日期会补全省略年份；展览月度展期覆盖窗口时允许通过，证据不满足时拒绝发送。
- 回复安全过滤后会重新连续编号并删除无关房型推销。
- 修复后全量测试：135 passed、10 skipped；Ruff、mypy 通过；真实 DeepSeek 完整契约 8 passed。
- 本机部署健康检查 HTTP 200，会话保持 `BOT_ACTIVE`；历史普通回复回放得到连续 `1、2、3、4`，房型推销、客厅和庭院臆测均已清除。
- 回复速度优化后全量测试：144 passed、9 skipped；Ruff、mypy 通过；真实 DeepSeek 契约 7 passed。
- 真实旅游搜索首次耗时 15.39 秒，同题缓存命中低于 0.001 秒；回复 1069 字，保留日期与来源且低于 1500 字硬上限。
- 本机轮询已从 15 秒改为 5 秒；部署文件哈希一致，健康检查 HTTP 200，两个会话均保持 `BOT_ACTIVE`。
- DeepSeek 空白响应修复后全量测试：196 passed、10 skipped；Ruff、mypy 和差异检查均通过。
- 使用故障会话的“今天入住明天 / 房态回复 / 房源列表”上下文真实连续重放 3 次均成功；其中 2 次首轮空白，降级重试后正常生成回复。
- 企业微信补拉限流修复后全量测试：197 passed、10 skipped；客服账号列表每 5 分钟刷新，消息仍保持 5 秒检查频率。
- 简短房源追问修复后全量测试：199 passed、10 skipped；真实 DeepSeek 与百居易重放返回房源结果且没有再次索要日期。

# 当前任务：YuMi 民宿 AI 运营系统一期

## 一期 Spec

### 目标与边界

- 在现有模块化单体上扩展客户 CRM、七天上下文、百居易订单同步、入住生命周期、业务任务中心和企业微信员工手机页。
- 一期客服渠道只完善当前微信客服；员工通过企业微信提醒进入手机管理页。
- 公众号和小程序后续通过微信客服入口接入，一期不开发独立消息协议。
- 一期不开发博主管理、遗留物寄送、竞品分析、运营报表和 AI 调价。
- 本地测试继续使用 SQLite；正式云部署前迁移 PostgreSQL，并使用独立文件存储保存图片和二维码。

### 现状依据

- `src/homestay_bot/repositories/conversations.py` 的 `SQLAlchemyConversationRepository.get_or_create()` 已按微信客服账号和客户 ID 隔离会话，但没有正式客户主档。
- `src/homestay_bot/services/message_service.py` 的 `MessageService.build_context()` 当前只读取最近消息，没有七天原文、短期摘要和长期摘要机制。
- `src/homestay_bot/services/conversation_service.py` 的 `ConversationService.handle_message()` 已具备 AI 回复、敏感交易提醒和人工模式边界，可扩展任务识别与 CRM 上下文。
- `src/homestay_bot/integrations/hostex_client.py` 的 `HostexClient` 已支持房源、房型、房态、价格、订单查询和创建订单，但尚未接入百居易 Webhook 与定时订单对账。
- `src/homestay_bot/worker.py` 的 `Worker` 和 `src/homestay_bot/application.py` 的事务型发件箱已支持后台任务与安全发送重试，但现有任务是系统任务，不是保洁、维修等业务任务。
- `src/homestay_bot/routes/employee_auth.py` 已提供企业微信员工登录，`src/homestay_bot/routes/approvals.py` 已有移动 Web 审批入口，可复用其身份与权限边界。

### 总体架构

- 微信客服消息进入客户与会话模块，再由 AI 客服结合安全规则、审核知识、百居易查询、客户摘要和相关未完成任务生成回复或待确认任务。
- 百居易 Webhook 快速验签入队，后台更新订单、入住生命周期和业务任务；定时对账补回丢失事件。
- 企业微信只负责员工通知和手机管理页入口，CRM、订单、任务、权限与审计统一保存在 YuMi 系统。
- 所有外部事件使用稳定外部编号去重；耗时 AI、同步和发送工作均由后台队列处理。

### 客户 CRM

- 每个首次咨询者立即建立正式客户档案，不同客户不得共享上下文。
- 客户档案包含内部客户编号、微信客服身份、昵称、标签、员工备注、首次与最近咨询时间、入住次数、最近订单、当前入住状态、长期脱敏摘要和合并状态。
- YuMi 系统是标签主档；已关联企业微信“客户联系”的客户可同步标签，同步失败不影响内部 CRM。
- 支持一个客户多个标签。
- 系统仅使用已核验手机号、百居易订单、UnionID 等可靠身份提出重复档案合并建议；姓名或对话相似不能作为自动合并依据。
- 管理员确认后才执行合并；合并前订单、消息、任务和敏感资料保持隔离，合并操作写入审计。

### 七天上下文与摘要

- AI 上下文由长期脱敏摘要、最近七天有效对话、当前订单与入住状态、审核知识和相关未完成任务组成。
- 为保证 DeepSeek 稳定，最近一轮问答和当前问题使用原文；七天内较早消息先生成短期摘要，不把七天全部原文直接传给模型。
- 超过七天的原始聊天在成功并入长期摘要后删除；摘要生成失败时保留原文并等待下次处理。
- 摘要可保存房型偏好、常用语言、已确认需求、服务偏好、完成入住次数和未解决事项。
- 摘要禁止保存门锁密码、二维码、完整手机号、身份证、完整地址、支付资料、失效验证码和 AI 未确认推测。
- 管理员可以更正、重新生成或删除摘要。

### 百居易订单同步

- 百居易订单创建、订单更新和房态变化通过 Webhook 实时同步；接收端校验 Secret Token、持久化事件并在三秒内返回。
- Webhook 业务处理异步执行，定时查询百居易进行补漏对账。
- 订单保存百居易订单编号、客户、房间与房型、入住退房日期、入住人数、订单状态、渠道、特殊要求和同步时间。
- 价格、退款、赔偿、取消和改期由管理员决定，AI 不得修改。
- 重复 Webhook 或对账结果不得重复创建订单、任务或客户。

### 房间状态

```text
未开始 → 清洁中 → 待检查 → 可入住 → 已入住
                       ↓
                     退回清洁

任意阶段 → 维修中
维修完成 → 待检查
```

- 当前房间任务的执行员工在完成任务、检查清单和规定照片后，可以把房间从“待检查”改为“可入住”。
- 其他普通员工不能修改该房间为“可入住”；管理员可以修改或撤回，并可退回重做。
- 每次状态变更记录员工、时间、任务、订单和房间。

### 业务任务中心

- 一期任务类型包括保洁、维修、补耗材、特殊服务、提前入住和延迟退房。
- 任务状态为“待确认 → 待分派 → 待接收 → 进行中 → 待检查 → 已完成”，并支持已取消、异常和退回重做。
- 百居易退房与新入住自动生成基础保洁任务。
- AI 从聊天识别的服务需求只生成待确认任务，管理员确认并指定执行人后才进入执行流程。
- 员工手动创建的任务同样需要管理员确认。
- 管理员可查看、确认、分派、取消、退回和检查全部任务。
- 普通员工只能查看分配给自己的任务，进行接收、开始、上传照片、填写结果或异常、提交待检查和符合条件时标记可入住。

### AI 客服与人工接管

- AI 可处理房源、地址、交通、停车、WiFi、设施、入住退房流程、已确认订单基础状态、武汉旅游、天气路线和服务需求提取。
- 价格、优惠、退款、赔偿、投诉、取消、改期、提前入住、延迟退房、订单争议、强烈情绪和安全风险必须提醒 YuMi 接管。
- AI 不得承诺任务完成、提前入住成功或房间可入住。
- 与住宿、武汉出行、客户订单和民宿服务无关的问题，礼貌说明服务范围。
- YuMi 发出人工消息后会话锁定为人工模式；管理员可恢复 AI。人工模式下 AI 可生成内部建议，但不得直接回复客户。

### 主动提醒

- 一期包含入住前一天的天气、路线、停车和注意事项，入住当天的预计到达与入住资料，退房前的退房与遗留物提醒，退房后的感谢，以及任务异常提醒。
- 微信客服发送窗口允许时自动发送；超过 48 小时、超过条数限制、客户拒收或发送失败时停止盲目重试，记录原因并生成 YuMi 人工联系任务。
- 未成功发送不得标记为客户已收到。

### 入住资料安全自动发送

- 只有订单有效、客户与订单准确关联、当天入住、房间匹配、当前执行员工完成清单与照片并标记可入住、凭证属于本房间本次入住且尚未成功发送时，系统才自动发送二维码、入住指南和门锁密码。
- AI 没有修改安全条件或直接触发发送的权限。
- 任一条件不满足时不得发送凭证，只生成管理员异常任务并显示缺失条件。
- 发送结果不明确时不得盲目重复，必须先核对发送记录或交由管理员处理。
- 凭证加密保存；当前任务执行员工仅在必要时间内查看对应房间资料，其他普通员工不可查看。
- 订单结束后停止展示凭证；查看、修改和发送均写入审计；凭证不得进入大模型上下文。

### 员工权限

- 一期仅设管理员和普通员工两类角色。
- 管理员管理客户、订单、标签、档案合并、入住资料、全部任务、会话接管和审计。
- 普通员工仅查看分配给自己的任务和完成任务所需的最少信息。
- 客户电话默认脱敏；订单金额、完整地址、CRM 管理和全部门锁凭证仅管理员可见。
- 当前任务需要时，执行员工可在受控时间内查看对应房间必要资料，并记录访问日志。

### 可靠性与隐私

- 外部回调先验签入队再异步处理；所有外部事件幂等去重。
- AI 失败不得影响客户、订单和任务落库。
- 安全只读操作可有限重试；写入结果不明确时转人工核对。
- 图片和二维码使用独立文件存储；建立健康检查、失败告警、每日备份和恢复演练。
- AI 只接收回答所需的脱敏信息，禁止传入门锁密码、二维码、身份证、完整地址、支付资料和不必要的客户身份。
- 支持管理员查询、更正和删除客户资料；所有关键操作写入审计。

### 验收标准

- [ ] 不同客户的对话、摘要、订单和任务绝不串线。
- [ ] 最近七天上下文可延续，超过七天只使用成功生成的脱敏摘要。
- [ ] 摘要失败时不删除原消息，敏感字段永不进入摘要和模型请求。
- [ ] 重复客户仅生成建议，管理员确认后才合并。
- [ ] 百居易 Webhook 实时同步，丢失事件可由定时对账补回。
- [ ] 重复事件不重复创建订单、客户和任务。
- [ ] AI 识别服务需求只生成待确认任务。
- [ ] 普通员工只能访问自己的任务和必要资料。
- [ ] 当前执行员工完成清单与照片后可以标记可入住，其他员工不能操作。
- [ ] 安全条件不完整时绝不发送二维码或密码。
- [ ] 条件全部满足后只发送一次，失败或不明确结果不会盲目重复。
- [ ] 价格、退款、投诉、改期和提前入住正确转 YuMi。
- [ ] 超出微信客服发送窗口后生成员工任务，不误记为已送达。
- [ ] 客户、订单、任务、凭证和权限操作均有审计。
- [ ] 自动化测试、真实企业微信、真实百居易、备份恢复和权限测试通过后才部署。

## Spec 确认记录

- [x] 一期范围与模块化单体架构已确认
- [x] 客户 CRM、人工确认合并与标签主档已确认
- [x] 七天原文、短期摘要与长期脱敏摘要已确认
- [x] 百居易 Webhook 与定时对账已确认
- [x] 房间状态与当前执行员工标记可入住已确认
- [x] 业务任务中心类型、状态与分派方式已确认
- [x] AI 客服、人工接管与主动提醒边界已确认
- [x] 入住资料安全自动发送条件已确认
- [x] 管理员与普通员工最小权限已确认
- [x] 可靠性、隐私与验收标准已确认
- [x] 用户审核本文件中的最终书面 Spec
- [x] 编写并确认实施计划
- [ ] 按 TDD 实施、验证并部署

## YuMi Phase One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development`
> (recommended) or `executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有民宿机器人上交付一期客户 CRM、七天上下文、百居易订单同步、业务任务中心、员工手机页、房间可入住流转、入住凭证安全发送和生命周期提醒。

**Architecture:** 继续使用 FastAPI、SQLAlchemy 和持久化任务队列组成模块化单体。外部回调只验签入队，CRM、订单、任务、摘要和凭证规则由独立服务处理；所有外部写入使用发件箱、幂等键和人工复核边界。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy Async、Alembic、SQLite（本地）、PostgreSQL（正式环境）、Jinja2、DeepSeek、企业微信、百居易 OpenAPI、cryptography。

**执行约束：** 每项严格按“失败测试 → 最小实现 → 验证 → 提交”执行；所有新增或修改的函数和关键业务分支必须写清楚中文注释。若实现发现与本计划不一致，先更新本计划并重新取得确认。

---

### Task 1：客户主档、身份、标签与合并建议数据模型

**Files:**
- Create: `migrations/versions/0003_customer_crm.py`
- Create: `src/homestay_bot/services/sensitive_data.py`
- Modify: `src/homestay_bot/domain/enums.py`
- Modify: `src/homestay_bot/domain/models.py`
- Modify: `src/homestay_bot/config.py`
- Modify: `.env.example`
- Test: `tests/integration/test_customer_repository.py`
- Test: `tests/unit/test_sensitive_data.py`
- Test: `tests/unit/test_models.py`
- Test: `tests/unit/test_config.py`

- [x] **Step 1：先写失败测试**

```python
async def test_customer_identity_is_unique_and_conversation_links_customer():
    customer = Customer(display_name="微信客户")
    identity = CustomerIdentity(
        customer=customer,
        provider=CustomerIdentityProvider.WECOM_KF,
        external_id="wm-1",
        is_verified=True,
    )
    session.add_all([customer, identity])
    await session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        open_kfid="wk-1",
        external_userid="wm-1",
    )
    session.add(conversation)
    await session.commit()
    assert conversation.customer_id == customer.id
```

同时覆盖标签多选、相同 `provider + external_id` 唯一、合并建议状态和会话客户外键。
增加凭证明文不出现在密文、相同手机号产生相同 HMAC 指纹、错误密钥不能解密的测试。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_customer_repository.py tests/unit/test_models.py`

Expected: FAIL，提示 `Customer`、`CustomerIdentityProvider` 等类型不存在。

- [x] **Step 3：实现最小数据模型和迁移**

在 `domain/enums.py` 增加：

```python
class CustomerIdentityProvider(StrEnum):
    WECOM_KF = "wecom_kf"
    WECOM_CONTACT = "wecom_contact"
    WECHAT_UNIONID = "wechat_unionid"
    HOSTEX = "hostex"


class CustomerMergeStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
```

在 `domain/models.py` 增加 `Customer`、`CustomerIdentity`、`CustomerTag`、`CustomerTagLink`、`CustomerMergeSuggestion`，并给 `Conversation` 增加可空 `customer_id`。`CustomerTag` 增加可空 `wecom_tag_id`，`CustomerTagLink` 增加 `sync_pending` 和脱敏后的 `last_sync_error_code`，用于后续可重试的企业微信标签同步。迁移为已有会话创建客户与微信客服身份。

增加必填 `DATA_ENCRYPTION_KEY`，与 Session Secret 分离。`SensitiveDataCipher.encrypt()` 使用 Fernet，`fingerprint()` 使用带独立上下文前缀的 HMAC-SHA256；手机号只保存密文和指纹，不保存明文。

- [x] **Step 4：验证迁移升级、降级和测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_customer_repository.py tests/unit/test_sensitive_data.py tests/unit/test_models.py tests/unit/test_config.py tests/unit/test_db.py`

Expected: PASS；`alembic upgrade head → downgrade 0002_frequent_faq_candidates → upgrade head` 成功。

- [x] **Step 5：提交**

```bash
git add migrations/versions/0003_customer_crm.py src/homestay_bot/services/sensitive_data.py src/homestay_bot/domain/enums.py src/homestay_bot/domain/models.py src/homestay_bot/config.py .env.example tests/integration/test_customer_repository.py tests/unit/test_sensitive_data.py tests/unit/test_models.py tests/unit/test_config.py
git commit -m "feat: add customer crm schema"
```

### Task 2：首次咨询建档、可靠身份匹配与管理员确认合并

**Files:**
- Create: `src/homestay_bot/repositories/customers.py`
- Create: `src/homestay_bot/services/customer_service.py`
- Modify: `src/homestay_bot/repositories/conversations.py`
- Modify: `src/homestay_bot/services/conversation_service.py`
- Test: `tests/unit/test_customer_service.py`
- Test: `tests/integration/test_customer_repository.py`
- Test: `tests/unit/test_conversation_service.py`

- [x] **Step 1：先写失败测试**

```python
async def test_first_message_creates_customer_and_verified_wecom_identity():
    customer = await service.ensure_for_message(incoming_message)
    assert customer.id is not None
    assert repository.identities == [
        ("wecom_kf", incoming_message.external_userid, customer.id)
    ]


async def test_phone_match_only_creates_merge_suggestion():
    suggestion = await service.suggest_merge(
        source_customer_id=2,
        verified_phone="13800000000",
    )
    assert suggestion.status is CustomerMergeStatus.PENDING
    assert repository.customers_were_merged is False
```

同时覆盖重复消息不重复建档、姓名相同不触发合并、管理员接受后迁移现阶段已存在的身份/会话/标签并写审计。订单和业务任务表在 Task 4 创建，因此其关联迁移测试也在 Task 4 同步补齐，避免依赖尚不存在的数据模型。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_customer_service.py tests/integration/test_customer_repository.py tests/unit/test_conversation_service.py`

Expected: FAIL，提示客户服务和仓储不存在。

- [x] **Step 3：实现客户服务边界**

```python
class CustomerService:
    async def ensure_for_message(self, message: IncomingMessage) -> Customer:
        return await self._customers.ensure_identity(
            provider=CustomerIdentityProvider.WECOM_KF,
            external_id=message.external_userid,
            display_name="微信客户",
        )

    async def suggest_merge(
        self, source_customer_id: int, verified_phone: str
    ) -> CustomerMergeSuggestion | None:
        fingerprint = self._cipher.fingerprint(verified_phone)
        return await self._customers.suggest_unique_phone_match(
            source_customer_id, fingerprint
        )

    async def confirm_merge(
        self, suggestion_id: int, administrator_id: int
    ) -> Customer:
        return await self._customers.merge_locked(
            suggestion_id, administrator_id
        )
```

`ConversationService.handle_message()` 在记录首次消息前确保客户存在；合并必须锁定两个客户行，在同一事务中迁移身份、会话和标签，标记来源客户已合并，并写 `AuditLog`。HMAC 指纹只用于精确匹配，姓名和 AI 相似度不得触发合并。Task 4 创建订单与业务任务模型后，必须扩展同一 `merge_locked()` 事务迁移它们的客户外键。

- [x] **Step 4：运行客户服务测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_customer_service.py tests/integration/test_customer_repository.py tests/unit/test_conversation_service.py`

Expected: PASS。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/repositories/customers.py src/homestay_bot/services/customer_service.py src/homestay_bot/repositories/conversations.py src/homestay_bot/services/conversation_service.py tests/unit/test_customer_service.py tests/integration/test_customer_repository.py tests/unit/test_conversation_service.py
git commit -m "feat: create customer profiles from conversations"
```

### Task 3：七天上下文、短期摘要、长期摘要与安全清理

**Files:**
- Create: `migrations/versions/0004_customer_context.py`
- Create: `src/homestay_bot/integrations/deepseek_context_summarizer.py`
- Create: `src/homestay_bot/services/context_retention.py`
- Create: `src/homestay_bot/repositories/context.py`
- Modify: `src/homestay_bot/domain/models.py`
- Modify: `src/homestay_bot/services/message_service.py`
- Modify: `src/homestay_bot/services/conversation_service.py`
- Modify: `src/homestay_bot/integrations/deepseek_client.py`
- Modify: `src/homestay_bot/application.py`
- Test: `tests/unit/test_context_retention.py`
- Test: `tests/integration/test_message_flow.py`
- Test: `tests/unit/test_conversation_service.py`
- Test: `tests/unit/test_deepseek_client.py`

- [x] **Step 1：先写失败测试**

```python
async def test_messages_are_purged_only_after_long_summary_succeeds():
    await service.maintain_customer(customer_id=1, now=NOW)
    assert summarizer.long_calls == 1
    assert old_message.content is None
    assert old_message.purged_at == NOW


async def test_summary_failure_keeps_original_message():
    summarizer.raise_error = True
    await service.maintain_customer(customer_id=1, now=NOW)
    assert old_message.content == "七天前的原文"
    assert old_message.purged_at is None
```

同时覆盖不同客户摘要隔离、最近一轮原文保留、七天内较早消息进入短期摘要、敏感字段拒绝、失败重试幂等。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_context_retention.py tests/integration/test_message_flow.py tests/unit/test_deepseek_client.py`

Expected: FAIL，提示上下文摘要服务和字段不存在。

- [x] **Step 3：实现摘要模型与维护服务**

通过 `0004_customer_context.py` 新增 `CustomerContextSummary(short_summary, long_summary, short_cutoff_at, long_cutoff_at, version)`；给 `Message` 增加 `short_summarized_at` 和 `purged_at`，清理时保留消息 ID 但把正文置空以维持去重。

```python
class ContextRetentionService:
    async def maintain_customer(
        self, customer_id: int, now: datetime
    ) -> None:
        await self._update_short_summary(customer_id, now)
        await self._roll_expired_messages_into_long_summary(customer_id, now)

    async def build_model_context(
        self, customer_id: int, conversation_id: int, now: datetime
    ) -> CustomerModelContext:
        return await self._repository.load_model_context(
            customer_id=customer_id,
            conversation_id=conversation_id,
            raw_since=now - timedelta(days=7),
            raw_limit=3,
        )
```

`DeepSeekContextSummarizer` 只接收脱敏文本，结构化返回 `summary` 和 `unresolved_items`；本地再次检查手机号、身份证、地址、密码和二维码特征。`ConversationService` 在调用助手前按当前 `customer_id` 读取 `CustomerModelContext`；`DeepSeekGuestAssistant.respond()` 增加可选 `customer_context` 参数，把客户摘要写入系统提示，最近原文仍保持最多三条。订单和任务尚未建表，由 Task 6 在模型与任务状态机接通时扩展同一上下文，避免本任务依赖未来模型。

- [x] **Step 4：注册每小时维护任务并验证**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_context_retention.py tests/integration/test_message_flow.py tests/unit/test_conversation_service.py tests/unit/test_deepseek_client.py tests/unit/test_application.py`

Expected: PASS；两个客户并行维护不串数据。

- [x] **Step 5：提交**

```bash
git add migrations/versions/0004_customer_context.py src/homestay_bot/domain/models.py src/homestay_bot/integrations/deepseek_context_summarizer.py src/homestay_bot/services/context_retention.py src/homestay_bot/repositories/context.py src/homestay_bot/services/message_service.py src/homestay_bot/services/conversation_service.py src/homestay_bot/integrations/deepseek_client.py src/homestay_bot/application.py tests/unit/test_context_retention.py tests/integration/test_message_flow.py tests/unit/test_conversation_service.py tests/unit/test_deepseek_client.py
git commit -m "feat: retain seven day customer context"
```

### Task 4：订单、房间、业务任务、附件和凭证数据模型

**Files:**
- Create: `migrations/versions/0005_operations.py`
- Create: `src/homestay_bot/repositories/operations.py`
- Modify: `src/homestay_bot/domain/enums.py`
- Modify: `src/homestay_bot/domain/models.py`
- Modify: `src/homestay_bot/repositories/customers.py`
- Modify: `src/homestay_bot/services/customer_service.py`
- Test: `tests/integration/test_operations_repository.py`
- Test: `tests/integration/test_customer_repository.py`
- Test: `tests/unit/test_models.py`

- [x] **Step 1：先写失败测试**

```python
async def test_turnover_task_dedupe_key_is_unique():
    first = await repository.create_turnover(
        property_id=101, service_date=date(2026, 8, 1)
    )
    second = await repository.create_turnover(
        property_id=101, service_date=date(2026, 8, 1)
    )
    assert first.id == second.id
```

同时覆盖订单代码唯一、任务来源消息唯一、附件归属、房间状态唯一、凭证投递部件唯一和 Webhook 事件幂等；并验证管理员确认客户合并时，已有订单和业务任务的 `customer_id` 在同一事务迁移到目标客户。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_operations_repository.py tests/unit/test_models.py`

Expected: FAIL，提示运营模型不存在。

- [x] **Step 3：创建模型和迁移**

新增枚举 `BusinessTaskType`、`BusinessTaskStatus`、`RoomOperationalStatus`、`CredentialDeliveryStatus`；`BusinessTaskType` 除六种客人服务任务外包含仅由系统创建的 `MANUAL_CONTACT`。新增模型：

```python
HostexWebhookEvent
StayOrder
PropertyProfile
RoomOperationalState
BusinessTask
TaskAttachment
RoomCredential
CredentialDelivery
CredentialDeliveryPart
```

`BusinessTask.dedupe_key`、`StayOrder.hostex_reservation_code`、`HostexWebhookEvent.event_key` 和投递部件组合键必须唯一。AI 创建的 `PENDING_CONFIRMATION` 任务允许 `property_id` 与 `service_date` 暂时为空；进入待分派、已分派、执行中或待检查前必须补齐，数据库检查约束与任务服务共同守卫。周转保洁任务始终要求房间和服务日期。凭证字段只保存密文和私有文件引用。

创建最小 `SQLAlchemyOperationsRepository`，只实现本任务可验证的数据入口：幂等 `create_turnover()`、订单/房态/凭证投递记录的基础读写。Task 5 在同一仓储上继续增加百居易订单 upsert、Webhook 事件和对账查询，禁止测试绕过生产仓储直接伪造幂等行为。

扩展 `SQLAlchemyCustomerRepository.merge_locked()`：锁定合并建议和两个客户后，除 Task 2 已处理的身份、会话和标签外，把 `StayOrder.customer_id` 与 `BusinessTask.customer_id` 一并迁移到目标客户；任何唯一冲突或写入失败都回滚整个合并事务。

- [x] **Step 4：验证迁移和约束**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_operations_repository.py tests/unit/test_models.py tests/unit/test_db.py`

Expected: PASS；迁移升级、降级、再升级成功。

- [x] **Step 5：提交**

```bash
git add migrations/versions/0005_operations.py src/homestay_bot/domain/enums.py src/homestay_bot/domain/models.py src/homestay_bot/repositories/operations.py src/homestay_bot/repositories/customers.py src/homestay_bot/services/customer_service.py tests/integration/test_operations_repository.py tests/integration/test_customer_repository.py tests/unit/test_models.py
git commit -m "feat: add operations data model"
```

### Task 5：百居易 Webhook、订单同步和定时对账

**Files:**
- Create: `src/homestay_bot/routes/hostex_webhook.py`
- Create: `src/homestay_bot/services/hostex_sync.py`
- Modify: `src/homestay_bot/repositories/operations.py`
- Modify: `src/homestay_bot/config.py`
- Modify: `.env.example`
- Modify: `src/homestay_bot/main.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/worker.py`
- Test: `tests/unit/test_hostex_webhook.py`
- Test: `tests/unit/test_hostex_sync.py`
- Test: `tests/integration/test_operations_repository.py`

- [x] **Step 1：先写失败测试**

```python
async def test_hostex_webhook_verifies_secret_and_enqueues_once(client):
    response = client.post(
        "/webhooks/hostex",
        headers={"Hostex-Webhook-Secret-Token": "valid"},
        json={"event": "reservation_updated", "reservation_code": "R-1"},
    )
    assert response.status_code == 202
    assert queue.calls == [("hostex_event", {"event_key": ANY})]
```

同时覆盖错误 Secret 返回401、未知字段忽略、重复事件幂等、订单更新、订单取消、Webhook 遗漏后对账补回。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_hostex_webhook.py tests/unit/test_hostex_sync.py tests/integration/test_operations_repository.py`

Expected: FAIL，提示路由和同步服务不存在。

- [x] **Step 3：实现快速入队和订单 upsert**

```python
class HostexSyncService:
    async def handle_event(self, event_key: str) -> None:
        event = await self._events.require_pending(event_key)
        matches = await self._hostex.list_reservations(
            ReservationQuery(reservation_code=event.reservation_code)
        )
        if len(matches) != 1:
            raise HostexSyncConflict(
                event_key=event.event_key,
                reservation_count=len(matches),
            )
        reservation = matches[0]
        order = await self._orders.upsert_from_hostex(reservation)
        await self._events.mark_completed(event)

    async def reconcile(
        self, start_date: date, end_date: date
    ) -> ReconcileResult:
        reservations = await self._hostex.list_reservations(
            ReservationQuery(
                start_check_in_date=start_date,
                end_check_in_date=end_date,
            )
        )
        return await self._orders.reconcile(reservations)
```

Webhook 使用 `secrets.compare_digest()` 验签，规范化 JSON 后计算缺省事件键，事务内保存事件并入队，不能调用 AI。同步服务按 `reservation_code` upsert `StayOrder` 并关联可靠客户身份；查到零条或多条订单时抛出 `HostexSyncConflict`，保留事件待人工复核，不猜测订单。周转任务在 Task 6 接入，避免本任务依赖尚未实现的任务服务。

- [x] **Step 4：注册 handler 与定时对账**

增加 `HOSTEX_WEBHOOK_SECRET_TOKEN` 和 `HOSTEX_RECONCILE_INTERVAL_SECONDS=900`；worker 注册 `hostex_event`，应用启动独立对账循环。运行：

`PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_hostex_webhook.py tests/unit/test_hostex_sync.py tests/unit/test_application.py`

Expected: PASS，Webhook 请求路径不执行慢操作。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/routes/hostex_webhook.py src/homestay_bot/services/hostex_sync.py src/homestay_bot/repositories/operations.py src/homestay_bot/config.py .env.example src/homestay_bot/main.py src/homestay_bot/application.py src/homestay_bot/worker.py tests/unit/test_hostex_webhook.py tests/unit/test_hostex_sync.py tests/integration/test_operations_repository.py
git commit -m "feat: sync hostex reservations and availability"
```

### Task 6：业务任务状态机、自动周转任务和 AI 待确认任务

**Files:**
- Create: `src/homestay_bot/services/business_task_service.py`
- Modify: `src/homestay_bot/services/answer_policy.py`
- Modify: `src/homestay_bot/integrations/deepseek_client.py`
- Modify: `src/homestay_bot/services/conversation_service.py`
- Modify: `src/homestay_bot/repositories/context.py`
- Modify: `src/homestay_bot/services/hostex_sync.py`
- Modify: `src/homestay_bot/repositories/operations.py`
- Test: `tests/unit/test_business_task_service.py`
- Test: `tests/unit/test_answer_policy.py`
- Test: `tests/unit/test_deepseek_client.py`
- Test: `tests/unit/test_conversation_service.py`
- Test: `tests/unit/test_context_retention.py`
- Test: `tests/unit/test_hostex_sync.py`

- [x] **Step 1：先写失败测试**

```python
async def test_ai_suggestion_creates_pending_confirmation_task():
    task = await service.record_ai_suggestion(
        customer_id=1,
        source_message_id="msg-1",
        task_type=BusinessTaskType.SUPPLIES,
        description="补两瓶矿泉水",
    )
    assert task.status is BusinessTaskStatus.PENDING_CONFIRMATION
    assert task.assigned_employee_id is None
```

同时覆盖不支持类型拒绝、重复消息幂等、订单同步后生成唯一周转保洁任务、非法状态跳转、提前入住不被 AI 自动批准、民宿无关问题礼貌拒答，以及价格、退款、投诉和明显激动情绪触发 YuMi 接管通知。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_business_task_service.py tests/unit/test_answer_policy.py tests/unit/test_deepseek_client.py tests/unit/test_conversation_service.py tests/unit/test_hostex_sync.py`

Expected: FAIL，提示业务任务服务和结构化建议字段不存在。

- [x] **Step 3：实现任务状态机和同轮 AI 提取**

```python
class BusinessTaskService:
    async def create_turnover(
        self, *, property_id: int, service_date: date, order_id: int
    ) -> BusinessTask:
        return await self._tasks.create_turnover(
            property_id=property_id,
            service_date=service_date,
            order_id=order_id,
        )

    async def record_ai_suggestion(
        self, *, customer_id: int, source_message_id: str,
        task_type: BusinessTaskType, description: str,
        property_id: int | None = None, service_date: date | None = None
    ) -> BusinessTask:
        return await self._tasks.create_pending_confirmation(
            customer_id=customer_id,
            source_message_id=source_message_id,
            task_type=task_type,
            description=description,
            property_id=property_id,
            service_date=service_date,
        )

    async def transition(
        self, task_id: int, actor: Employee, target: BusinessTaskStatus
    ) -> BusinessTask:
        task = await self._tasks.require_for_update(task_id)
        self._state_rules.require_allowed(task, actor, target)
        return await self._tasks.save_status(task, target)
```

扩展 `AssistantDecision`，同一次 DeepSeek 响应返回可空 `task_suggestion`；本地只允许六种一期任务类型并清除敏感字段。AI 未能可靠确定房间或日期时仍可创建 `PENDING_CONFIRMATION`，但确认或分派前必须由管理员补齐；任何可执行状态不得缺少房间和服务日期。`AnswerPolicy` 在本地执行确定性边界：民宿无关问题礼貌拒答，价格、退款、投诉、提前入住和激烈情绪只提供安抚与流程说明，不替 YuMi 作决定，并创建带会话摘要的接管通知。`ConversationService` 在客人回复成功后记录待确认任务，失败不得回滚回复；任务待确认时通知管理员，分派后只通知执行员工。扩展 `ContextRepository.load_model_context()`，把当前客户的有效订单摘要和未完成任务摘要加入 Task 3 已建立的 `CustomerModelContext`，不得包含金额、完整手机号或入住凭证明文。把 `HostexSyncService` 的订单 upsert 成功事件接入 `create_turnover()`，以房源和服务日期幂等创建周转任务。每次状态变化和接管动作均写不含聊天正文的审计事件。

- [x] **Step 4：运行任务与会话测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_business_task_service.py tests/unit/test_answer_policy.py tests/unit/test_deepseek_client.py tests/unit/test_conversation_service.py tests/unit/test_hostex_sync.py`

Expected: PASS。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/services/business_task_service.py src/homestay_bot/services/answer_policy.py src/homestay_bot/integrations/deepseek_client.py src/homestay_bot/services/conversation_service.py src/homestay_bot/services/hostex_sync.py src/homestay_bot/repositories/operations.py src/homestay_bot/repositories/context.py tests/unit/test_business_task_service.py tests/unit/test_answer_policy.py tests/unit/test_context_retention.py tests/unit/test_deepseek_client.py tests/unit/test_conversation_service.py tests/unit/test_hostex_sync.py
git commit -m "feat: manage operational tasks"
```

**Review（Task 6）**

- 已提交 `7d57553 feat: manage operational tasks`。
- AI 任务建议只进入待确认状态；缺少房间或服务日期时不能进入执行流程。
- 价格、退款、投诉、提前入住和激烈情绪由本地规则触发 YuMi 接管，并写安全审计。
- 百居易有效订单按退房日幂等创建周转保洁任务；取消订单不新建任务。
- 客户模型上下文只包含脱敏摘要、有效订单摘要和未完成任务摘要。
- 验证：251 passed，10 skipped；Ruff、mypy、`git diff --check` 均通过；迁移升级、降级、再升级通过。

### Task 7：两级员工权限与移动任务页

**Files:**
- Create: `migrations/versions/0006_employee_roles.py`
- Create: `src/homestay_bot/routes/tasks.py`
- Create: `src/homestay_bot/services/task_page_service.py`
- Create: `src/homestay_bot/templates/tasks/index.html`
- Create: `src/homestay_bot/templates/tasks/detail.html`
- Modify: `src/homestay_bot/domain/enums.py`
- Modify: `src/homestay_bot/routes/employee_auth.py`
- Modify: `src/homestay_bot/main.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/static/app.css`
- Test: `tests/integration/test_task_routes.py`
- Test: `tests/unit/test_task_page_service.py`
- Test: `tests/integration/test_approval_routes.py`
- Test: `tests/integration/test_knowledge_routes.py`

- [x] **Step 1：先写失败测试**

```python
def test_staff_only_sees_assigned_tasks(client, staff_session):
    response = client.get("/employee/tasks")
    assert response.status_code == 200
    assert "自己的任务" in response.text
    assert "其他员工任务" not in response.text
```

同时覆盖管理员查看全部、员工不能分派/取消/看CRM、管理员仍可审批和管理知识、CSRF、越权任务ID返回403。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_task_routes.py tests/unit/test_task_page_service.py tests/integration/test_approval_routes.py tests/integration/test_knowledge_routes.py`

Expected: FAIL，提示任务路由不存在。

- [x] **Step 3：实现两级权限和页面服务**

`EmployeeRole` 收敛为 `ADMIN` 与 `STAFF`；迁移把非管理员角色统一为 `STAFF`，预订审批、知识管理、客户合并和任务分派限定管理员。

```python
class TaskPageService:
    async def list_for(self, employee: Employee) -> list[BusinessTask]:
        if employee.role is EmployeeRole.ADMIN:
            return await self._tasks.list_all_open()
        return await self._tasks.list_assigned_open(employee.id)

    async def detail_for(
        self, task_id: int, employee: Employee
    ) -> TaskDetail:
        task = await self._tasks.require_visible_to(task_id, employee)
        return TaskDetail.from_task(task, reveal_sensitive=False)

    async def transition(
        self, task_id: int, employee: Employee, target: str
    ) -> BusinessTask:
        return await self._task_service.transition(
            task_id, employee, BusinessTaskStatus(target)
        )
```

Jinja 页面使用移动优先的大按钮、状态徽标和明确确认文案；普通员工详情不渲染客户电话、订单金额、完整地址或无关凭证。

- [x] **Step 4：运行权限回归**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_task_routes.py tests/unit/test_task_page_service.py tests/integration/test_approval_routes.py tests/integration/test_knowledge_routes.py tests/unit/test_application.py`

Expected: PASS；越权请求均为403且无敏感正文。

- [x] **Step 5：提交**

```bash
git add migrations/versions/0006_employee_roles.py src/homestay_bot/routes/tasks.py src/homestay_bot/services/task_page_service.py src/homestay_bot/templates/tasks/index.html src/homestay_bot/templates/tasks/detail.html src/homestay_bot/domain/enums.py src/homestay_bot/routes/employee_auth.py src/homestay_bot/main.py src/homestay_bot/application.py src/homestay_bot/static/app.css tests/integration/test_task_routes.py tests/unit/test_task_page_service.py tests/integration/test_approval_routes.py tests/integration/test_knowledge_routes.py
git commit -m "feat: add employee mobile task center"
```

**Review（Task 7）**

- 已提交 `81e0308 feat: add employee mobile task center`。
- 员工角色已收敛为 `ADMIN` 与 `STAFF`；历史非管理员角色可迁移、降级和再次升级。
- 管理员可查看全部任务、分派和取消；员工只可查看并推进自己的任务。
- 分派严格经过待确认、待分派、已分派状态，全部写入不含任务正文的审计。
- 任务页使用一次性 CSRF，越权任务编号返回 403，手机号和详细地址会脱敏。
- 预订审批仅管理员可见和确认；知识管理与客户合并继续保持管理员写权限。
- 验证：266 passed，10 skipped；Ruff、mypy、`git diff --check` 均通过。

### Task 8：私有附件、检查清单与执行员工标记可入住

**Files:**
- Create: `src/homestay_bot/services/private_file_storage.py`
- Create: `src/homestay_bot/services/room_readiness_service.py`
- Create: `src/homestay_bot/routes/private_files.py`
- Modify: `src/homestay_bot/config.py`
- Modify: `.env.example`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/repositories/operations.py`
- Modify: `src/homestay_bot/services/task_page_service.py`
- Modify: `src/homestay_bot/routes/tasks.py`
- Modify: `src/homestay_bot/templates/tasks/detail.html`
- Modify: `src/homestay_bot/main.py`
- Test: `tests/unit/test_private_file_storage.py`
- Test: `tests/unit/test_room_readiness_service.py`
- Test: `tests/unit/test_task_page_service.py`
- Test: `tests/unit/test_application.py`
- Test: `tests/integration/test_task_routes.py`
- Test: `tests/integration/test_operations_repository.py`

- [x] **Step 1：先写失败测试**

```python
async def test_assigned_employee_can_mark_ready_after_checklist_and_photo():
    task.assigned_employee_id = employee.id
    task.status = BusinessTaskStatus.PENDING_INSPECTION
    task.checklist = {"clean": True, "supplies": True, "damage": True}
    task.attachments = [photo]
    state = await service.mark_ready(task.id, employee)
    assert state.status is RoomOperationalStatus.READY
```

同时覆盖非执行员工403、缺照片/清单拒绝、非待检查状态拒绝、管理员撤回、文件路径穿越和超大/伪图片拒绝。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_private_file_storage.py tests/unit/test_room_readiness_service.py tests/integration/test_task_routes.py`

Expected: FAIL，提示文件存储和可入住服务不存在。

- [x] **Step 3：实现私有文件和状态守卫**

```python
class PrivateFileStorage:
    async def save_image(
        self, stream: BinaryIO, content_type: str, size_limit: int
    ) -> StoredPrivateFile:
        validated = await self._validator.read_image(stream, size_limit)
        return await self._files.save_random_name(
            validated, content_type=content_type
        )


class RoomReadinessService:
    async def mark_ready(
        self, task_id: int, actor: Employee
    ) -> RoomOperationalState:
        task = await self._tasks.require_for_update(task_id)
        self._rules.require_ready_evidence(task, actor)
        return await self._rooms.mark_ready(task.property_id, actor.id)

    async def revoke_ready(
        self, property_id: int, administrator: Employee
    ) -> RoomOperationalState:
        self._rules.require_administrator(administrator)
        return await self._rooms.mark_pending_inspection(
            property_id, administrator.id
        )
```

文件使用随机 UUID 名称保存到 `PRIVATE_UPLOAD_DIR`，下载必须经过员工会话和任务归属检查。`SQLAlchemyOperationsRepository` 负责附件归属查询、任务/房态行锁、清单和安全审计；`SessionTaskPageService` 在短事务中组合私有存储、任务页面服务与可入住服务，上传文件落盘失败或附件落库失败时不得留下可访问的孤儿记录。`mark_ready()` 在同一事务中锁定任务与房间状态，验证执行人、待检查状态、完整清单和至少一张有效照片并写审计。

- [x] **Step 4：运行安全与路由测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_private_file_storage.py tests/unit/test_room_readiness_service.py tests/integration/test_task_routes.py`

Expected: PASS。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/services/private_file_storage.py src/homestay_bot/services/room_readiness_service.py src/homestay_bot/routes/private_files.py src/homestay_bot/config.py .env.example src/homestay_bot/routes/tasks.py src/homestay_bot/templates/tasks/detail.html src/homestay_bot/main.py tests/unit/test_private_file_storage.py tests/unit/test_room_readiness_service.py tests/integration/test_task_routes.py
git commit -m "feat: verify room readiness with evidence"
```

**Review（Task 8）**

- 私有照片只接受经过文件签名核验的 PNG、JPEG 和 WebP，使用随机 UUID 文件名及 0600 权限保存；下载复用员工会话和任务归属授权，并禁止浏览器缓存。
- 检查清单与照片只允许任务执行员工在已分派、执行中或待检查状态提交；仓储在行锁后再次校验执行人和任务状态，避免并发越权。
- 可入住操作要求任务处于待检查、三项清单全部完成且至少存在一张已验证照片；维修中或已入住的房间不能被覆盖为可入住。
- 只有管理员可以把当前可入住房间撤回待检查；重复设置相同房态保持幂等。
- 附件落库失败会删除已写入文件；审计只记录内部编号、状态和完成数量，不记录任务正文、文件编号或图片内容。
- 验证：283 passed，10 skipped；Task 8 定向测试 44 passed；Ruff、mypy、`git diff --check` 均通过。

### Task 9：房源配置、加密凭证和管理员管理页

**Files:**
- Create: `src/homestay_bot/services/property_admin_service.py`
- Create: `src/homestay_bot/routes/properties.py`
- Create: `src/homestay_bot/templates/properties/index.html`
- Create: `src/homestay_bot/templates/properties/detail.html`
- Modify: `src/homestay_bot/config.py`
- Modify: `.env.example`
- Modify: `src/homestay_bot/main.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/services/sensitive_data.py`
- Modify: `tests/unit/test_sensitive_data.py`
- Test: `tests/integration/test_property_routes.py`

- [x] **Step 1：先写失败测试**

```python
def test_room_password_is_encrypted_at_rest(cipher):
    encrypted = cipher.encrypt("839201")
    assert b"839201" not in encrypted
    assert cipher.decrypt(encrypted) == "839201"
```

同时覆盖非管理员不可配置、页面不回显完整密码、二维码私有保存、凭证版本与房间绑定、审计不复制明文。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_sensitive_data.py tests/integration/test_property_routes.py`

Expected: FAIL，提示加密和房源管理服务不存在。

- [x] **Step 3：实现独立数据密钥和房源配置**

复用 Task 1 已配置的 `DATA_ENCRYPTION_KEY` 和 `SensitiveDataCipher`，凭证与手机号采用相同密钥管理边界但使用不同用途上下文。

```python
class SensitiveDataCipher:
    def encrypt(self, value: str, *, purpose: str | None = None) -> bytes:
        cipher = self._fernet if purpose is None else self._purpose_cipher(purpose)
        return cipher.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes, *, purpose: str | None = None) -> str:
        cipher = self._fernet if purpose is None else self._purpose_cipher(purpose)
        return cipher.decrypt(value).decode("utf-8")


class PropertyAdminService:
    async def update_profile(
        self, property_id: int, administrator: Employee, fields: PropertyFields
    ) -> PropertyProfile:
        self.require_admin(administrator)
        property_profile = await self._require_property_for_update(property_id)
        # 校验并保存允许管理员维护的公开运营字段。
        return property_profile

    async def replace_credentials(
        self, property_id: int, administrator: Employee,
        password: str, guide: str, qr_file_id: str
    ) -> RoomCredential:
        self.require_admin(administrator)
        await self._require_property_for_update(property_id)
        return RoomCredential(
            property_id=property_id,
            password_ciphertext=self._cipher.encrypt(
                password, purpose="room_password"
            ),
            guide_ciphertext=self._cipher.encrypt(
                guide, purpose="checkin_guide"
            ),
            qr_file_id=qr_file_id,
        )
```

管理员页配置百居易房间映射、区域、地址提示、停车说明、入住指南、密码和二维码；日志只记录版本号和房间号。

- [x] **Step 4：运行加密、权限和日志测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_sensitive_data.py tests/integration/test_property_routes.py tests/unit/test_log_redaction.py`

Expected: PASS，数据库和日志中均无凭证明文。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/services/sensitive_data.py src/homestay_bot/services/property_admin_service.py src/homestay_bot/routes/properties.py src/homestay_bot/templates/properties/index.html src/homestay_bot/templates/properties/detail.html src/homestay_bot/config.py .env.example src/homestay_bot/main.py src/homestay_bot/application.py tests/unit/test_sensitive_data.py tests/integration/test_property_routes.py
git commit -m "feat: manage encrypted room credentials"
```

**Review（Task 9）**

- 管理员可维护百居易房间编号对应的房源名称、房型、区域、地址提示、停车说明和启用状态；普通员工无法进入房源管理页或读取二维码。
- 门锁密码与入住指南分别使用从 `DATA_ENCRYPTION_KEY` 派生的用途专属子密钥加密；既有未指定用途的手机号加密格式保持兼容，不同用途之间无法互相解密。
- 每次替换凭证都会锁定房源、创建递增版本并停用旧版本；管理页只显示当前版本，不解密或回显密码与入住指南。
- 二维码复用 Task 8 的私有目录、真实图片校验、随机文件名和大小限制；凭证事务失败时自动删除新文件，旧版本文件保留供后续幂等投递核对。
- 房源与凭证审计只记录房源编号、版本和启用状态，不记录密码、指南、二维码文件编号或其他正文。
- 现有配置已经包含独立数据密钥、私有目录和上传大小上限，因此无需新增环境变量或数据库迁移。
- 验证：292 passed，10 skipped；Task 9 定向测试 20 passed；Ruff、mypy、`git diff --check` 均通过。

### Task 10：可入住后的幂等凭证发送

**Files:**
- Create: `src/homestay_bot/services/credential_delivery.py`
- Modify: `src/homestay_bot/integrations/wecom/api_client.py`
- Modify: `src/homestay_bot/services/room_readiness_service.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/worker.py`
- Test: `tests/unit/test_credential_delivery.py`
- Test: `tests/unit/test_wecom_api_client.py`
- Test: `tests/unit/test_worker.py`

- [ ] **Step 1：先写失败测试**

```python
async def test_ready_room_enqueues_each_credential_part_once():
    result = await service.evaluate(order_id=7)
    assert result.status is CredentialDeliveryStatus.PENDING
    assert jobs.dedupe_keys == [
        "credential:7:guide:v3",
        "credential:7:password:v3",
        "credential:7:qr:v3",
    ]
```

同时覆盖订单/客户/日期/房间不匹配不发送、非当天入住不发送、重复可入住事件不重复、文本成功图片不明确时不重放图片、48小时窗口失败转人工任务。

- [ ] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_credential_delivery.py tests/unit/test_wecom_api_client.py tests/unit/test_worker.py`

Expected: FAIL，提示凭证投递服务和图片发送接口不存在。

- [ ] **Step 3：实现逐部件投递和安全门**

```python
class CredentialDeliveryService:
    async def evaluate(self, order_id: int) -> CredentialDelivery:
        order = await self._orders.require_for_update(order_id)
        self._rules.require_all_delivery_conditions(order)
        return await self._deliveries.ensure_parts(order)

    async def mark_sent(
        self, part_id: int, external_message_id: str
    ) -> None:
        await self._deliveries.mark_sent(part_id, external_message_id)

    async def mark_uncertain(self, part_id: int, error_code: str) -> None:
        await self._deliveries.mark_needs_review(part_id, error_code)
```

扩展企业微信客户端的临时素材上传和图片发送。指南、密码和二维码各自拥有唯一投递部件与幂等键；外部结果不明确时状态为 `NEEDS_REVIEW`，不自动重放。房间标记可入住后只调用 `evaluate()`，不在请求事务中直接发送。

- [ ] **Step 4：运行投递与 worker 回归**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_credential_delivery.py tests/unit/test_wecom_api_client.py tests/unit/test_worker.py tests/unit/test_room_readiness_service.py`

Expected: PASS。

- [ ] **Step 5：提交**

```bash
git add src/homestay_bot/services/credential_delivery.py src/homestay_bot/integrations/wecom/api_client.py src/homestay_bot/services/room_readiness_service.py src/homestay_bot/application.py src/homestay_bot/worker.py tests/unit/test_credential_delivery.py tests/unit/test_wecom_api_client.py tests/unit/test_worker.py
git commit -m "feat: deliver room credentials safely"
```

### Task 11：CRM管理员手机页、标签和合并审批

**Files:**
- Create: `src/homestay_bot/integrations/wecom/contact_client.py`
- Create: `src/homestay_bot/services/customer_tag_sync.py`
- Create: `src/homestay_bot/routes/customers.py`
- Create: `src/homestay_bot/services/customer_admin_service.py`
- Create: `src/homestay_bot/templates/customers/index.html`
- Create: `src/homestay_bot/templates/customers/detail.html`
- Create: `src/homestay_bot/templates/customers/merge.html`
- Modify: `src/homestay_bot/main.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/config.py`
- Modify: `.env.example`
- Modify: `src/homestay_bot/static/app.css`
- Test: `tests/integration/test_customer_routes.py`
- Test: `tests/unit/test_customer_admin_service.py`
- Test: `tests/unit/test_wecom_contact_client.py`
- Test: `tests/unit/test_customer_tag_sync.py`

- [ ] **Step 1：先写失败测试**

```python
def test_admin_can_confirm_merge_but_staff_cannot(client):
    staff_response = client.post("/employee/customers/merge/1/confirm")
    assert staff_response.status_code == 403
    admin_response = admin_client.post(
        "/employee/customers/merge/1/confirm",
        data={"csrf_token": csrf_token},
    )
    assert admin_response.status_code == 303
```

同时覆盖客户列表、标签多选、备注、手机号脱敏、摘要更正/删除、拒绝合并、合并审计无正文。标签始终先在本地成功保存；仅当客户存在已验证的 `WECOM_CONTACT` 身份且配置了客户联系 Secret 时同步企业微信标签；缺少身份或配置时跳过，接口失败时标记待重试且不回滚本地标签。

- [ ] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_customer_routes.py tests/unit/test_customer_admin_service.py tests/unit/test_wecom_contact_client.py tests/unit/test_customer_tag_sync.py`

Expected: FAIL，提示客户管理路由不存在。

- [ ] **Step 3：实现管理员 CRM 页面**

```python
class CustomerAdminService:
    async def list_customers(self, query: str | None) -> list[CustomerCard]:
        return await self._customers.search_cards(query)

    async def get_detail(self, customer_id: int) -> CustomerDetail:
        return await self._customers.require_admin_detail(customer_id)

    async def set_tags(
        self, customer_id: int, tag_ids: list[int], administrator_id: int
    ) -> None:
        self._permissions.require_administrator(administrator_id)
        await self._customers.replace_tags(customer_id, tag_ids)
        await self._tag_sync.enqueue_if_linked(customer_id)

    async def update_note(
        self, customer_id: int, note: str, administrator_id: int
    ) -> None:
        self._permissions.require_administrator(administrator_id)
        await self._customers.update_note(customer_id, note.strip())
```

全部路由要求管理员、CSRF 和活动员工复核。详情页只显示脱敏电话，完整敏感资料不通过普通 HTML 渲染。

`CustomerTagSyncService` 仅对已验证的企业微信客户联系身份和配置了 `wecom_tag_id` 的标签调用官方 `/cgi-bin/externalcontact/mark_tag`。增加可选 `WECOM_CONTACT_SECRET`：未配置时健康项显示 `not_configured`，不影响本地 CRM；同步失败只记录错误码并保持 `sync_pending=True`，由 worker 幂等重试，日志不得写外部联系人 ID 或标签正文。

- [ ] **Step 4：运行 CRM 权限测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_customer_routes.py tests/unit/test_customer_admin_service.py tests/unit/test_wecom_contact_client.py tests/unit/test_customer_tag_sync.py tests/integration/test_task_routes.py`

Expected: PASS。

- [ ] **Step 5：提交**

```bash
git add src/homestay_bot/integrations/wecom/contact_client.py src/homestay_bot/services/customer_tag_sync.py src/homestay_bot/routes/customers.py src/homestay_bot/services/customer_admin_service.py src/homestay_bot/templates/customers/index.html src/homestay_bot/templates/customers/detail.html src/homestay_bot/templates/customers/merge.html src/homestay_bot/main.py src/homestay_bot/application.py src/homestay_bot/config.py .env.example src/homestay_bot/static/app.css tests/integration/test_customer_routes.py tests/unit/test_customer_admin_service.py tests/unit/test_wecom_contact_client.py tests/unit/test_customer_tag_sync.py
git commit -m "feat: add mobile customer crm"
```

### Task 12：入住生命周期提醒、发送窗口失败和人工联系任务

**Files:**
- Create: `src/homestay_bot/services/lifecycle_reminders.py`
- Modify: `src/homestay_bot/services/business_task_service.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/worker.py`
- Test: `tests/unit/test_lifecycle_reminders.py`
- Test: `tests/unit/test_worker.py`

- [ ] **Step 1：先写失败测试**

```python
async def test_expired_wecom_window_creates_manual_contact_task():
    wecom.raise_error_code = 95004
    await service.send(reminder)
    assert tasks.created[0].task_type is BusinessTaskType.MANUAL_CONTACT
    assert reminder.delivered_at is None
```

同时覆盖入住前一天、入住当天、退房前、退房后提醒，武汉时区日期边界，已发送幂等，客户拒收和超过5条不误记送达。

- [ ] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_lifecycle_reminders.py tests/unit/test_worker.py`

Expected: FAIL，提示生命周期提醒服务不存在。

- [ ] **Step 3：实现确定性提醒调度**

```python
class LifecycleReminderService:
    async def schedule_for_order(self, order_id: int) -> list[Job]:
        order = await self._orders.require(order_id)
        return await self._reminders.ensure_schedule(order)

    async def deliver(self, reminder_id: int) -> None:
        reminder = await self._reminders.require_pending(reminder_id)
        try:
            await self._sender.send(reminder)
        except WeComApiError as error:
            if error.error_code not in EXPIRED_SEND_ERROR_CODES:
                raise
            await self._tasks.create_manual_contact(reminder)
            await self._reminders.mark_manual_followup(reminder)
```

提醒幂等键使用 `order_id + reminder_type + scheduled_date`。入住前天气由现有实时搜索能力生成，但不得包含链接；天气失败时仍发送路线、停车和注意事项。`EXPIRED_SEND_ERROR_CODES` 只包含已由企业微信契约测试确认的窗口过期、拒收和条数超限错误码；这些错误创建 YuMi 人工联系任务并终止自动重试，其他网络或服务错误继续走有上限的退避重试。

- [ ] **Step 4：运行时区和失败回退测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_lifecycle_reminders.py tests/unit/test_worker.py tests/unit/test_deepseek_tourism.py`

Expected: PASS。

- [ ] **Step 5：提交**

```bash
git add src/homestay_bot/services/lifecycle_reminders.py src/homestay_bot/services/business_task_service.py src/homestay_bot/application.py src/homestay_bot/worker.py tests/unit/test_lifecycle_reminders.py tests/unit/test_worker.py
git commit -m "feat: schedule guest lifecycle reminders"
```

### Task 13：全链路装配、健康状态、真实契约与本机部署

**Files:**
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/routes/health.py`
- Modify: `tests/integration/test_runtime_startup.py`
- Create: `tests/integration/test_phase_one_flow.py`
- Modify: `tests/contract/test_hostex_contract.py`
- Modify: `tests/contract/test_wecom_contract.py`
- Modify: `tests/contract/test_deepseek_contract.py`
- Modify: `deploy/com.rin.homestay-bot.plist`
- Modify: `.env.example`

- [ ] **Step 1：编写一期端到端失败测试**

```python
async def test_phase_one_order_to_ready_and_credentials_flow():
    await receive_guest_message("今天入住明天退房")
    await receive_hostex_reservation_webhook("R-1")
    task = await require_single_turnover_task("R-1")
    await assign_and_complete_with_photo(task)
    await mark_room_ready(task)
    assert await credential_parts("R-1") == {
        "guide": "sent",
        "password": "sent",
        "qr": "sent",
    }
```

另写两个客户上下文隔离、七天摘要、Webhook补漏、越权员工、发送窗口过期和重复事件测试。

- [ ] **Step 2：运行端到端测试并确认暴露装配缺口**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_phase_one_flow.py tests/integration/test_runtime_startup.py`

Expected: FAIL，列出尚未注册的 handler、状态服务或健康项。

- [ ] **Step 3：完成应用装配和健康检查**

`application_lifespan()` 只创建依赖并注册：

```python
app.state.customer_admin_service
app.state.task_page_service
app.state.property_admin_service
app.state.private_file_service
app.state.hostex_webhook_service
```

健康页增加 `hostex_webhook_sync`、`context_maintenance`、`lifecycle_scheduler`，每项以最近成功心跳判定，凭证和客户正文不得进入健康响应。

- [ ] **Step 4：运行全部本地验证**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
git diff --check
```

Expected: 全部测试通过；只有未显式开启的真实契约测试被跳过；Ruff、mypy 和差异检查均通过。

- [ ] **Step 5：运行真实契约与迁移演练**

显式开启并分别验证：

- 百居易订单查询、Webhook样例解析与对账；
- 企业微信文本、图片素材和发送窗口错误；
- DeepSeek客户摘要、任务提取和脱敏；
- SQLite完整升级/降级/升级；
- 临时PostgreSQL执行全部迁移和关键仓储测试；
- 备份恢复后订单、任务、摘要和审计数量一致。

Expected: 真实契约通过，日志无密钥、密码、二维码、完整手机号和客户聊天正文。

- [ ] **Step 6：独立审查一期安全边界**

检查身份合并、跨客户查询、任务越权、路径穿越、CSRF、凭证明文、外部写入重放、Webhook伪造和摘要删除顺序。所有 Critical/Important 问题修复并重新执行 Step 4。

- [ ] **Step 7：备份、迁移并部署本机测试环境**

先备份运行目录、数据库和私有文件，执行 `alembic upgrade head`，同步源码与模板，填入新环境变量，重启 LaunchAgent。验证：

- 健康检查 HTTP 200；
- 原有会话与知识不丢失；
- XuKuang 保持管理员；
- 普通员工权限最小化；
- 两个测试客户上下文隔离；
- 测试订单可生成唯一任务；
- 凭证只在安全条件全部满足后发送。

- [ ] **Step 8：提交验收结果**

```bash
git add tests src deploy .env.example tasks/todo.md tasks/lessons.md
git commit -m "chore: verify yumi phase one delivery"
```
