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
- Create: `src/homestay_bot/repositories/credentials.py`
- Modify: `src/homestay_bot/integrations/wecom/api_client.py`
- Modify: `src/homestay_bot/services/room_readiness_service.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/worker.py`
- Modify: `src/homestay_bot/repositories/jobs.py`
- Test: `tests/unit/test_credential_delivery.py`
- Test: `tests/unit/test_wecom_api_client.py`
- Test: `tests/unit/test_worker.py`
- Test: `tests/integration/test_credential_delivery_repository.py`
- Test: `tests/integration/test_jobs.py`

- [x] **Step 1：先写失败测试**

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

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_credential_delivery.py tests/unit/test_wecom_api_client.py tests/unit/test_worker.py`

Expected: FAIL，提示凭证投递服务和图片发送接口不存在。

- [x] **Step 3：实现逐部件投递和安全门**

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

- [x] **Step 4：运行投递与 worker 回归**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_credential_delivery.py tests/unit/test_wecom_api_client.py tests/unit/test_worker.py tests/unit/test_room_readiness_service.py`

Expected: PASS。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/services/credential_delivery.py src/homestay_bot/integrations/wecom/api_client.py src/homestay_bot/services/room_readiness_service.py src/homestay_bot/application.py src/homestay_bot/worker.py tests/unit/test_credential_delivery.py tests/unit/test_wecom_api_client.py tests/unit/test_worker.py
git commit -m "feat: deliver room credentials safely"
```

**Review（Task 10）**

- 房间标记可入住后只在同一事务执行凭证安全评估，不在员工请求中直接发送；缺订单、客户、日期、房间、凭证、会话或 48 小时窗口时幂等创建管理员人工处理任务。
- 首次评估和 worker 实际发送前均复核：有效订单、当天入住且尚未退房、任务与订单房间一致、房态可入住、当前凭证有效且属于该房间、订单客户与会话客户一致、微信客服身份已验证、最近客人消息未超过 48 小时。
- 指南、密码和二维码分别建立唯一投递部件与后台任务；任务载荷只有内部 `part_id`，不保存客户身份、密码、指南、二维码编号或其他凭证明文。
- 文本只有获得企业微信 `msgid` 后才标记成功；二维码先上传临时素材再发送图片。发送结果不明确时部件和整体进入 `NEEDS_REVIEW` 并创建人工任务，不会自动重放。
- `credential_send_part` 已加入进程中断后的禁止恢复清单；即使外部发送后数据库提交中断，重启也不会盲目再次发送。
- 每次成功、阻止和待复核均写最小审计，只记录内部编号、部件类型、状态和错误类型。
- 验证：311 passed，10 skipped；Task 10 定向测试 58 passed；Ruff、mypy、`git diff --check` 均通过。

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
- Modify: `src/homestay_bot/repositories/customers.py`
- Modify: `src/homestay_bot/routes/health.py`
- Modify: `src/homestay_bot/worker.py`
- Modify: `.env.example`
- Modify: `src/homestay_bot/static/app.css`
- Test: `tests/integration/test_customer_routes.py`
- Test: `tests/integration/test_customer_repository.py`
- Test: `tests/integration/test_runtime_startup.py`
- Test: `tests/unit/test_customer_admin_service.py`
- Test: `tests/unit/test_wecom_contact_client.py`
- Test: `tests/unit/test_customer_tag_sync.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_health.py`
- Test: `tests/unit/test_worker.py`

- [x] **Step 1：先写失败测试**

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

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_customer_routes.py tests/unit/test_customer_admin_service.py tests/unit/test_wecom_contact_client.py tests/unit/test_customer_tag_sync.py`

Expected: FAIL，提示客户管理路由不存在。

- [x] **Step 3：实现管理员 CRM 页面**

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

- [x] **Step 4：运行 CRM 权限测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_customer_routes.py tests/unit/test_customer_admin_service.py tests/unit/test_wecom_contact_client.py tests/unit/test_customer_tag_sync.py tests/integration/test_task_routes.py`

Expected: PASS。

- [x] **Step 5：提交**

```bash
git add src/homestay_bot/integrations/wecom/contact_client.py src/homestay_bot/services/customer_tag_sync.py src/homestay_bot/routes/customers.py src/homestay_bot/services/customer_admin_service.py src/homestay_bot/templates/customers/index.html src/homestay_bot/templates/customers/detail.html src/homestay_bot/templates/customers/merge.html src/homestay_bot/main.py src/homestay_bot/application.py src/homestay_bot/config.py .env.example src/homestay_bot/static/app.css tests/integration/test_customer_routes.py tests/unit/test_customer_admin_service.py tests/unit/test_wecom_contact_client.py tests/unit/test_customer_tag_sync.py
git commit -m "feat: add mobile customer crm"
```

**Review（Task 11）**

- 管理员客户列表、详情、标签多选、备注、摘要更正/删除和合并确认/拒绝均已接入一次性 CSRF；普通员工入口和伪造写请求返回 403。
- CRM 页面只接收 `CustomerCard` 安全字段，手机号在内存解密后立即脱敏；审计只保存内部编号、版本和增删数量，不保存备注、摘要或客户聊天正文。
- 标签始终与本地 CRM 同事务提交；仅在配置可选 `WECOM_CONTACT_SECRET` 且客户存在已验证 `WECOM_CONTACT` 身份时登记异步同步任务。
- 企业微信标签同步先通过客户详情读取全部有效跟进员工，再按 `userid + external_userid` 客户关系调用 `externalcontact/mark_tag`；失败仅记录异常类型并由 worker 最多重试三次。
- 390px 手机尺寸实测客户详情与合并确认页无横向溢出，完整手机号和密文字段均未进入页面。
- 全量验证：`330 passed, 10 skipped`；跳过项仅为未显式开启的 DeepSeek、百居易和企业微信真实契约测试。Ruff、mypy 与 `git diff --check` 均通过。

### Task 12：入住生命周期提醒、发送窗口失败和人工联系任务

**Files:**
- Create: `src/homestay_bot/services/lifecycle_reminders.py`
- Create: `src/homestay_bot/repositories/lifecycle_reminders.py`
- Create: `migrations/versions/0007_lifecycle_reminders.py`
- Modify: `src/homestay_bot/domain/enums.py`
- Modify: `src/homestay_bot/domain/models.py`
- Modify: `src/homestay_bot/services/business_task_service.py`
- Modify: `src/homestay_bot/repositories/operations.py`
- Modify: `src/homestay_bot/repositories/jobs.py`
- Modify: `src/homestay_bot/application.py`
- Modify: `src/homestay_bot/worker.py`
- Modify: `src/homestay_bot/services/hostex_sync.py`
- Test: `tests/unit/test_lifecycle_reminders.py`
- Test: `tests/unit/test_worker.py`
- Test: `tests/unit/test_hostex_sync.py`
- Test: `tests/unit/test_application.py`
- Test: `tests/integration/test_lifecycle_reminder_repository.py`
- Test: `tests/integration/test_jobs.py`

- [x] **Step 1：先写失败测试**

```python
async def test_async_send_fail_creates_manual_contact_without_delivery():
    reminder = await service.accept_platform_message(
        reminder_id=1,
        external_message_id="msg-1",
    )
    assert reminder.status is ReminderStatus.PLATFORM_ACCEPTED
    await service.handle_send_failure("msg-1", fail_type=4)
    assert tasks.created[0].task_type is BusinessTaskType.MANUAL_CONTACT
    assert reminder.status is ReminderStatus.MANUAL_FOLLOWUP
```

同时覆盖：

- 入住前一天 18:00：天气、路线、停车和注意事项；
- 入住当天 10:00：预计到达时间与入住流程提示，不绕过房间可入住校验发送密码或二维码；
- 退房当天 09:00：退房时间与遗留物提醒；
- 退房当天 14:00：感谢入住，不立即索要好评；
- 所有时间按 `Asia/Shanghai` 换算 UTC 入队；
- 相同 `order_id + reminder_type + 本地计划日期` 幂等；
- 只选择订单客户最近有客人消息的微信客服会话，不跨客户发送；
- 本地预检最近客人消息 48 小时窗口，以及该窗口内机器人和人工已发条数；
- 平台受理后只标记 `PLATFORM_ACCEPTED`，不得标记客户已收到；
- `msg_send_fail` 的 4（超过 48 小时）、5（会话关闭）、6（超过 5 条）、10（客户拒收）转人工联系；
- 网络连接明确未建立时有限重试，超时或结果不明确时不盲目重放；
- 天气查询失败时仍发送不含链接的路线、停车和注意事项。

- [x] **Step 2：运行测试并确认失败**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_lifecycle_reminders.py tests/unit/test_worker.py tests/integration/test_lifecycle_reminder_repository.py`

Expected: FAIL，提示生命周期提醒服务、持久化状态和发送失败事件处理不存在。

- [x] **Step 3：实现持久化状态与确定性提醒调度**

```python
class LifecycleReminderService:
    async def schedule_for_order(self, order_id: int) -> list[Job]:
        order = await self._orders.require(order_id)
        return await self._reminders.ensure_schedule(order)

    async def deliver(self, reminder_id: int) -> None:
        reminder = await self._reminders.require_pending(reminder_id)
        context = await self._reminders.require_safe_send_context(reminder)
        if not context.within_48_hours or context.sent_count >= 5:
            await self._tasks.create_manual_contact(reminder)
            await self._reminders.mark_manual_followup(reminder)
            return
        try:
            message_id = await self._sender.send(reminder)
        except ConnectionError:
            raise RetrySafeJobError
        except TimeoutError:
            await self._tasks.create_manual_contact(reminder)
            await self._reminders.mark_manual_followup(reminder)
            return
        await self._reminders.mark_platform_accepted(reminder, message_id)

    async def handle_send_failure(
        self, external_message_id: str, fail_type: int
    ) -> None:
        reminder = await self._reminders.find_by_message_id(
            external_message_id
        )
        if reminder is None:
            return
        await self._tasks.create_manual_contact(reminder)
        await self._reminders.mark_manual_followup(reminder, fail_type)
```

新增 `LifecycleReminder` 保存计划时间、平台消息 ID 和
`SCHEDULED / PLATFORM_ACCEPTED / MANUAL_FOLLOWUP / CANCELLED` 状态。
系统没有企业微信“客户已读/已收到”回执，因此一期不建立虚假的
`DELIVERED` 状态。`WeComSyncJobHandler` 必须识别 `msg_send_fail`
事件并按 `fail_msgid` 回写准确提醒；普通消息仍走原有客户隔离流程。
其他配置类失败也生成管理员异常任务，但不向客人重放。

- [x] **Step 4：运行时区和失败回退测试**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_lifecycle_reminders.py tests/unit/test_worker.py tests/integration/test_lifecycle_reminder_repository.py tests/unit/test_deepseek_tourism.py`

Expected: PASS。

- [x] **Step 5：提交**

```bash
git add migrations/versions/0007_lifecycle_reminders.py src/homestay_bot/domain/enums.py src/homestay_bot/domain/models.py src/homestay_bot/repositories/lifecycle_reminders.py src/homestay_bot/repositories/operations.py src/homestay_bot/services/lifecycle_reminders.py src/homestay_bot/services/business_task_service.py src/homestay_bot/application.py src/homestay_bot/worker.py tests/unit/test_lifecycle_reminders.py tests/unit/test_worker.py tests/integration/test_lifecycle_reminder_repository.py tasks/todo.md
git commit -m "feat: schedule guest lifecycle reminders"
```

**Review（Task 12）**

- 四类提醒按武汉本地时间换算为 UTC 持久化入队；相同订单、类型和本地日期保持唯一，订单改期会撤销旧计划，取消后按相同日期恢复也能重新激活。
- 发送前只选择订单客户本人已验证的微信客服会话，并复核最近客人消息 48 小时窗口及其后的机器人/人工发送条数。
- 企业微信同步已识别 `msg_send_fail`，按平台消息编号把 4、5、6、10 四类异步失败转为幂等人工联系任务；平台同步受理只记录 `PLATFORM_ACCEPTED`，不虚构送达。
- 入住前天气复用现有武汉联网查询，明确当前日期、入住日期和房源区域；联网失败仍发送不含链接的路线、停车和注意事项。
- 订单取消、已关闭提醒遗留任务、超时和 worker 崩溃均禁止盲目重放；只有明确未建立连接的请求允许有限重试。
- 数据库迁移已通过全新升级、回退至 `0006_employee_roles`、再升级至 `0007_lifecycle_reminders` 的循环验证。
- 全量验证：`357 passed, 10 skipped`；Ruff 全仓通过；mypy 检查 71 个源文件通过；`git diff --check` 通过。

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

- [x] **Step 1：编写一期端到端失败测试**

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

- [x] **Step 2：运行端到端测试并确认暴露装配缺口**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_phase_one_flow.py tests/integration/test_runtime_startup.py`

Expected: FAIL，列出尚未注册的 handler、状态服务或健康项。

- [x] **Step 3：完成应用装配和健康检查**

`application_lifespan()` 只创建依赖并注册：

```python
app.state.customer_admin_service
app.state.task_page_service
app.state.property_admin_service
app.state.private_file_service
app.state.hostex_webhook_service
```

健康页增加 `hostex_webhook_sync`、`context_maintenance`、`lifecycle_scheduler`，每项以最近成功心跳判定，凭证和客户正文不得进入健康响应。

- [x] **Step 4：运行全部本地验证**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
git diff --check
```

Expected: 全部测试通过；只有未显式开启的真实契约测试被跳过；Ruff、mypy 和差异检查均通过。

- [x] **Step 5：运行本机可执行的真实契约与迁移演练**

显式开启并分别验证：

- 百居易订单查询、Webhook样例解析与对账；
- 企业微信文本、图片素材和发送窗口错误；
- DeepSeek客户摘要、任务提取和脱敏；
- SQLite完整升级/降级/升级；
- 临时PostgreSQL执行全部迁移和关键仓储测试；
- 备份恢复后订单、任务、摘要和审计数量一致。

Expected: 真实契约通过，日志无密钥、密码、二维码、完整手机号和客户聊天正文。

执行记录：百居易只读契约、企业微信客服账号发现、DeepSeek
10 项真实契约、SQLite 完整迁移循环和备份恢复计数已通过。
企业微信消息同步因缺少测试回调 Token 安全跳过；本机未安装
PostgreSQL 服务，按“先本地 SQLite 测试、云部署前再迁移
PostgreSQL”的已确认边界保留为云部署前验收项。

- [x] **Step 6：独立审查一期安全边界**

检查身份合并、跨客户查询、任务越权、路径穿越、CSRF、凭证明文、外部写入重放、Webhook伪造和摘要删除顺序。所有 Critical/Important 问题修复并重新执行 Step 4。

- [x] **Step 7：备份、迁移并部署本机测试环境**

先备份运行目录、数据库和私有文件，执行 `alembic upgrade head`，同步源码与模板，填入新环境变量，重启 LaunchAgent。验证：

- 健康检查 HTTP 200；
- 原有会话与知识不丢失；
- XuKuang 保持管理员；
- 普通员工权限最小化；
- 两个测试客户上下文隔离；
- 测试订单可生成唯一任务；
- 凭证只在安全条件全部满足后发送。

- [x] **Step 8：提交验收结果**

```bash
git add tests src deploy .env.example tasks/todo.md tasks/lessons.md
git commit -m "chore: verify yumi phase one delivery"
```

**Review（Task 13）**

- 新增一期订单到唯一保洁任务、员工执行、管理员验收、可入住和三段凭证发送的内存端到端测试；另验证两个客户的七天上下文相互隔离。
- 健康页已覆盖 worker、企业微信补拉、百居易对账、客户上下文维护和生命周期提醒心跳；运行响应不包含客户正文或凭证。
- 百居易只读查询、企业微信客服账号发现和消息补拉、DeepSeek 摘要与任务提取均已用当前真实配置验证；企业微信专用测试回调 Token 未配置，因此破坏性发送契约继续安全跳过。
- SQLite 已完成升级、降级、再升级和备份恢复计数核验；本机没有 PostgreSQL 服务，相关验证保留为未来云服务器交付前置项。
- 本机运行目录已备份到 `.backups/phase-one-20260731-074449`，数据库迁移至 `0007_lifecycle_reminders`，源码与迁移文件和当前分支一致；原有 2 个会话、94 条消息及管理员 `XuKuang` 均保留。
- LaunchAgent 运行于 `127.0.0.1:8010`，持续运行超过三个轮询周期后健康检查仍返回 HTTP 200；四个待发送任务均为尚未到执行时间的生命周期提醒。
- 企业微信兜底补拉实测返回 `45009` 频率限制后，已从每 5 秒调整为每 60 秒；实时 Webhook 不变，首个新周期成功且健康心跳已刷新。
- 提交前独立审查发现的未来心跳误判、事务提交前刷新心跳和生产装配覆盖不足均已修复，最终复审无 Critical/Important；全量验证为 `367 passed, 15 skipped`，Ruff、mypy（71 个源文件）及 `git diff --check` 全部通过。

### Task 14：管理员手动合并客户并继续一期真实验收

**Goal：** 允许管理员在没有手机号自动匹配建议时，安全选择两个客户档案，经脱敏预览和二次确认后原子合并，使百居易订单与正确的企业微信会话归属同一客户。

**现状依据：**

- `src/homestay_bot/repositories/customers.py::merge_locked()` 已迁移身份、会话、订单、任务和标签，但只能消费既有 `CustomerMergeSuggestion`，尚未处理双方备注、摘要和其他遗留建议。
- `src/homestay_bot/services/customer_admin_service.py::review_merge()` 只允许管理员审核现有建议，没有手动创建建议的服务边界。
- `src/homestay_bot/routes/customers.py::customer_merge_detail()` 已提供脱敏二次确认页和一次性 CSRF；客户详情页尚无选择目标客户的入口。
- 真实验收订单 `5-6BUAAN7FE` 属于测试订单客户 `7`，测试微信会话属于客户 `5`；两者没有可自动匹配的共同手机号。

**确认的功能规则：**

1. 只有启用中的 `ADMIN` 可以搜索目标客户、创建手动建议和确认合并。
2. 来源和目标必须是两个不同、存在且尚未合并的客户；目标列表只返回脱敏卡片。
3. 创建手动建议不迁移数据，必须继续使用现有合并对比页二次确认。
4. 确认时在一个事务内迁移身份、会话、订单、任务和标签；目标档案保留。
5. 目标电话优先，目标为空时才继承来源电话；目标显示名称保留。
6. 备注按“目标内容 + 来源档案补充”合并并限制为 2000 字；重复确认不得重复追加。
7. 摘要只有来源存在时迁移；双方都存在时保留目标并追加来源补充，待确认项去重且最多 20 项；原始消息不删除。
8. 其他涉及来源客户的待审核建议统一结束，防止继续操作已失效档案。
9. 任意权限、约束或提交失败必须整体回滚；已确认建议重复提交保持幂等。
10. 审计仅记录管理员、来源/目标客户编号和建议编号，不记录电话、备注、摘要或聊天正文。

**Files：**

- Modify: `src/homestay_bot/repositories/customers.py`
- Modify: `src/homestay_bot/services/customer_admin_service.py`
- Modify: `src/homestay_bot/routes/customers.py`
- Modify: `src/homestay_bot/templates/customers/detail.html`
- Modify: `src/homestay_bot/templates/customers/merge.html`
- Modify: `tests/unit/test_customer_admin_service.py`
- Modify: `tests/integration/test_customer_repository.py`
- Modify: `tests/integration/test_customer_routes.py`

- [x] **Step 1：为服务层手动建议编写失败测试**

新增 `CustomerAdminService.create_manual_merge(source_id, target_id, administrator)` 测试，验证普通员工被拒绝、自合并被拒绝、管理员只把编号交给仓储且返回建议编号。

Run:

```bash
../../.venv/bin/pytest -q tests/unit/test_customer_admin_service.py
```

Expected: FAIL，服务和仓储协议尚无 `create_manual_merge()`。

- [x] **Step 2：实现最小服务边界并通过单元测试**

仓储协议新增：

```python
async def create_manual_merge_suggestion(
    self,
    source_customer_id: int,
    target_customer_id: int,
    administrator_id: int,
) -> int:
    """创建待二次确认的管理员手动合并建议。"""
```

服务层复核管理员、拒绝相同编号并调用仓储，不读取或返回客户敏感字段。

- [x] **Step 3：为仓储原子合并规则编写失败测试**

在 `tests/integration/test_customer_repository.py` 覆盖：

- 手动建议创建前锁定管理员和两侧客户；
- 重复未决的同方向建议复用同一编号；
- 合并迁移身份、会话、订单、任务和标签；
- 备注、摘要、待确认项按已确认规则合并；
- 其他未决建议结束；
- 重复确认不重复追加；
- 事务异常后所有关系仍归来源客户。

Run:

```bash
../../.venv/bin/pytest -q tests/integration/test_customer_repository.py
```

Expected: FAIL，手动建议和补全的合并规则尚未实现。

- [x] **Step 4：实现仓储事务并通过集成测试**

`SQLAlchemyCustomerRepository.create_manual_merge_suggestion()` 使用 `with_for_update()` 复核管理员和两侧活动客户，写入 `reason="administrator_manual"` 的 `PENDING` 建议。`merge_locked()` 在现有关系迁移基础上合并备注和摘要、关闭其他未决建议，最后写最小审计并统一 `flush()`。

- [x] **Step 5：为管理员页面编写失败测试**

在 `tests/integration/test_customer_routes.py` 覆盖：

- 普通员工无法打开或提交手动合并；
- 管理员在客户详情页看到目标搜索表单；
- 搜索结果不包含来源客户和完整电话；
- POST 使用详情页一次性 CSRF 创建建议并 303 跳转到现有复核页；
- 伪造、重放令牌和自合并均返回稳定错误；
- 复核页显示迁移方向和关联数据计数，不展示密文。

- [x] **Step 6：实现非技术化搜索、预览和二次确认页面**

扩展 `CustomerAdminServicePort`、客户详情页上下文和路由：

```text
GET  /employee/customers/{source_id}?merge_query=...
POST /employee/customers/{source_id}/merge/manual
GET  /employee/customers/merge/{suggestion_id}
POST /employee/customers/merge/{suggestion_id}/confirm
```

详情页复用现有客户搜索服务输出 `CustomerCard`；POST 消耗 `customer_csrf` 后创建建议并跳转，确认继续复用 `customer_merge_csrf`。

- [x] **Step 7：运行安全专项与全量验证**

Run:

```bash
../../.venv/bin/pytest -q tests/unit/test_customer_admin_service.py tests/integration/test_customer_repository.py tests/integration/test_customer_routes.py tests/unit/test_employee_auth.py
../../.venv/bin/pytest -q
../../.venv/bin/ruff check src tests
../../.venv/bin/mypy src
git diff --check
```

Expected: 全部通过；真实契约测试只在未显式启用时跳过；页面、日志和审计无完整电话、聊天正文、备注或摘要正文。

验证结果（2026-08-01）：安全专项 `64 passed`；全量测试 `392 passed, 15 skipped`；Ruff、mypy（72 个源文件）与 `git diff --check` 全部通过。15 个跳过项均为未显式开启的 DeepSeek、Hostex、企业微信真实契约测试。

- [x] **Step 8：部署并继续真实业务验收**

备份本机运行数据库和源码，同步变更并重启 LaunchAgent。通过管理员页面将来源客户 `7` 合入目标客户 `5`，验证：

- 订单 `5-6BUAAN7FE` 与测试微信会话的 `customer_id` 相同；
- 保洁任务仍唯一且归属正确订单；
- 合并审计不含敏感正文；
- 机器人不会在房源凭证缺失或房间未标记可入住时发送密码或二维码。

真实验收结果（2026-08-01）：已备份运行目录至 `/private/tmp/HomestayBot-pre-manual-merge-20260801-0355.tar.gz`，同步已验证源码并重启 LaunchAgent。管理员页面流程创建建议 `1` 返回 `303`，复核页返回 `200` 且仅显示脱敏档案和关联计数；二次确认返回 `303`。订单和微信会话均已归客户 `5`，保洁任务保持唯一，审计仅包含员工编号、客户编号和建议编号；订单无凭证投递，房间凭证和运营状态均为空，未发送密码或二维码。健康端点复核为 HTTP `200`，数据库、worker、企业微信轮询、Hostex 同步、上下文维护和生命周期调度均为 `ok`。

#### Task 14 Review

- 实现复核：手动建议、脱敏预览、CSRF 二次确认、原子迁移、幂等重放、锁顺序、查询隐私和异常脱敏均已覆盖。
- 验证复核：安全专项 `64 passed`，全量 `392 passed, 15 skipped`，Ruff、mypy 和 `git diff --check` 通过；真实本机业务验收完成。
- 未覆盖项：15 个真实外部契约测试仍因未显式开启而跳过；云服务器部署和外部渠道真实消息回归不属于本次本地一期验收。

### Task 15：一期真实外部链路最终验收与证据回填

**Goal：** 在不增加业务功能和不触发凭证发送的前提下，验证百居易、DeepSeek、企业微信与本机消息处理链路，并明确记录仍受外部凭据限制的验收项。

**范围与停止条件：**

- 只使用现有测试客户和测试问题；不创建真实订单、不修改百居易数据、不修改房间可入住状态、不上传凭证照片。
- 不把本地健康检查、模拟 Webhook 或单元测试当作企业微信端到端通过。
- 任一外部链路失败、客户归属异常、敏感信息泄露或产生凭证投递时立即停止，不在验收过程中直接修复代码。
- 缺陷必须另行形成 Spec 后再实施；本任务只记录证据和验收结论。

**涉及文件与接口：**

- Read: `src/homestay_bot/application.py::_run_wecom_poll_loop()`、`handle_message()`、`_run_hostex_reconcile_loop()`
- Read: `src/homestay_bot/integrations/hostex_client.py::HostexClient`
- Read: `src/homestay_bot/integrations/deepseek_client.py::DeepSeekGuestAssistant.respond()`
- Test: `tests/contract/test_hostex_contract.py`
- Test: `tests/contract/test_deepseek_contract.py`
- Test: `tests/contract/test_wecom_contract.py`
- Verify: `/Users/rin/Library/Application Support/HomestayBot/homestay.db`、LaunchAgent 健康端点与运行日志

- [x] **Step 1：建立验收前只读基线**

记录当前 LaunchAgent PID、`/health` 响应、数据库 schema、测试客户/订单/会话/任务/发件箱/凭证数量和最近审计数量。不得读取或输出电话密文、模型密钥、聊天正文或房间凭证。

Run:

```bash
launchctl print gui/$(id -u)/com.rin.homestay-bot
curl --max-time 5 -sS http://127.0.0.1:8010/health
sqlite3 -readonly "$HOMESTAY_DB" 'PRAGMA user_version; SELECT version_num FROM alembic_version;'
```

Expected: LaunchAgent running；健康端点 HTTP 200；schema 为 `0007_lifecycle_reminders`；测试客户 5/7、订单 `5-6BUAAN7FE` 和会话归属均可核对。

执行结果（2026-08-02）：LaunchAgent PID `20227` running；健康端点 HTTP 200；schema `0007_lifecycle_reminders`；客户 7 已合并至 5，订单和会话均归客户 5；凭证投递和房间凭证数量均为 0。

- [x] **Step 2：执行百居易真实只读契约**

仅在当前 shell 临时设置 `RUN_LIVE_CONTRACT_TESTS=1`，运行房源、房态、参考价格、收益方式和近期订单对账测试；不写入 `.env`，不调用创建订单接口。

Run:

```bash
RUN_LIVE_CONTRACT_TESTS=1 ../../.venv/bin/pytest -q tests/contract/test_hostex_contract.py
```

Expected: 两项真实只读查询通过；Webhook 白名单测试不保存电话或门锁密码。

执行结果（2026-08-02）：`RUN_LIVE_CONTRACT_TESTS=1 pytest -q tests/contract/test_hostex_contract.py`，`3 passed`。

- [x] **Step 3：执行 DeepSeek 真实契约**

仅在当前 shell 临时设置 `RUN_DEEPSEEK_CONTRACT=1`，运行普通回答、房源缺口、旅游近期窗口、FAQ 草稿、摘要脱敏和待确认任务提取测试。不得把真实响应原文写入日志或任务记录。

Run:

```bash
RUN_DEEPSEEK_CONTRACT=1 ../../.venv/bin/pytest -q tests/contract/test_deepseek_contract.py
```

Expected: 真实模型返回结构化决策；未知房源事实包含 `【待管理员确认】`；回复不含链接；摘要不含手机号、密码和详细门牌。

执行结果（2026-08-02）：首次运行发现旅游搜索预算不足，`7 passed, 3 failed`；修复后最终运行 `RUN_DEEPSEEK_CONTRACT=1 pytest -q tests/contract/test_deepseek_contract.py` 为 `10 passed`。旅游联网预算调整为 3000 token，返回结果统一经过一次语义精简和旅客可读排版，精简失败仍保留已验证日期与来源并交由 1500 字硬上限兜底。全量测试 `392 passed, 15 skipped`，未发送企业微信测试消息。

- [x] **Step 4：完成企业微信客服账号与消息链路验收**

先运行只读客服账号发现；由于测试专用同步 Token 当前缺失，不得伪造参数。使用现有测试客户发送以下受控问题，并逐条等待系统处理后核对：

```text
当前房间预订状况
今天入住明天退房
武汉最近有什么好玩的？
可以帮我补两瓶矿泉水吗？
```

每条消息验证：入站消息归属正确客户；回复对应最新问题；普通问题由机器人回答；日期问题触发百居易房态查询；服务需求只生成待确认任务；旅游回复无链接；发件箱最终状态与实际发送结果一致。

Run:

```bash
../../.venv/bin/pytest -q tests/contract/test_wecom_contract.py::test_live_wecom_can_list_customer_service_accounts
```

Expected: 客服账号发现通过；真实消息回归结果逐条记录。`WECOM_TEST_SYNC_TOKEN` 或 `WECOM_TEST_OPEN_KFID` 缺失时，将消息补拉契约明确标记为 skipped，不得标记通过。

执行记录（2026-08-02）：客服账号只读契约 `1 passed`。第一条真实消息“当前房间预订状况”成功进入客户 5 的会话，机器人回复已由 `wecom_send_text` 任务一次发送完成；但回复错误落入民宿专属信息缺口兜底，未触发百居易房态查询。根因是 `DeepSeekGuestAssistant._should_force_availability()` 未识别“预订状况”，且未把“当前”换算为今天入住、明天退房。按停止条件，后续真实消息暂停；业务任务、凭证投递和房间凭证数量均未增加。

修复记录（2026-08-02）：同类真实只读调用已确认百居易工具能够触发；此前无回复的直接失败点是工具成功后 DeepSeek 最终 JSON 偶发不符合本地校验，重试后仍将回复判为不可用。新增仅针对“工具已成功执行”的安全房态回执，并补充无效结构化结果回归测试；尚未重新发送企业微信消息，Step 4 仍待真实端到端复验。

二次修复记录（2026-08-02）：部署后企业微信消息 ID 103 已收到且发件箱任务 73 完成，但回复仍是知识缺口兜底。定位为 `_validate_decision()` 在百居易工具成功后仍按空知识库覆盖模型房态结果；新增测试并让工具已验证结果跳过该覆盖逻辑。真实 DeepSeek + 百居易回放返回 `查询房态`、`knowledge_gap=false`，待企业微信再次回放确认。

房态场景验收（2026-08-02）：企业微信入站消息 ID 105、机器人回复 ID 106；`wecom_send_text` 任务 74 一次完成，员工待确认通知任务 75 完成。回复按 2026-08-01 入住、2026-08-02 退房返回百居易实时房态，不含链接且未落入知识缺口兜底。凭证投递和房间凭证数量均为 0。Step 4 的“当前房间预订状况”场景通过，其余三个受控问题仍待逐项验收。

相对日期场景验收（2026-08-02）：企业微信入站消息 ID 107、机器人回复 ID 108；`wecom_send_text` 任务 76 一次完成。系统自主把“今天入住明天退房”换算为 2026-08-01 至 2026-08-02，并返回百居易可用房间，没有再次追问绝对日期。凭证投递和房间凭证数量仍为 0。Step 4 剩余旅游和补矿泉水两个场景。

旅游场景验收（2026-08-02）：企业微信入站消息 ID 109、机器人回复 ID 110；`wecom_send_text` 任务 77 一次完成，`web_search=ok`。回复以 2026-08-01 至 08-16 为主要推荐窗口，半个月后的活动明确标注后续日期；包含查询日期和来源名称，不含网址或 Markdown 链接，长度 731 字。凭证投递和房间凭证数量仍为 0。Step 4 仅剩补矿泉水任务场景。

补矿泉水场景验收（2026-08-02）：企业微信入站消息 ID 111、机器人回复 ID 112；`wecom_send_text` 任务 78、员工通知任务 79 均一次完成。系统创建 `SUPPLIES / PENDING_CONFIRMATION` 任务 ID 12，回复明确说明需员工确认，没有直接承诺已完成，也未修改房态或创建凭证。至此 Step 4 四个受控问题全部通过。

- [x] **Step 5：执行安全与隔离核验**

对测试前后数量做差异比对，确认不同客户的消息、摘要、订单和任务未串线；确认没有新增凭证投递、房间凭证或二维码；审计不包含电话、聊天正文、摘要正文、密码和密钥；普通员工无管理员客户合并权限。

Run:

```bash
sqlite3 -readonly "$HOMESTAY_DB" 'SELECT COUNT(*) FROM credential_deliveries; SELECT COUNT(*) FROM room_credentials;'
../../.venv/bin/pytest -q tests/integration/test_customer_routes.py tests/integration/test_task_routes.py tests/integration/test_credential_delivery.py
```

Expected: 凭证投递和房间凭证数量不因本次验收增加；权限与安全测试通过。

执行结果（2026-08-02）：`test_customer_routes.py`、`test_task_routes.py`、`test_credential_delivery_repository.py` 共 `25 passed`；凭证投递、房间凭证和凭证分片数量均为 0；健康端点 HTTP 200，数据库、worker、企业微信轮询和配置均为 `ok`。

- [x] **Step 6：回填证据并提交验收记录**

只更新本任务清单的勾选状态和 Review；分别记录百居易、DeepSeek、企业微信客服账号发现、企业微信消息补拉和真实消息回归的通过/跳过/失败状态。若有失败，保留失败原因与下一步 Spec，不修改业务代码。

Run:

```bash
git diff --check
git add tasks/todo.md
git commit -m "chore: record phase one external acceptance"
```

Expected: 只提交验收记录；工作区干净；不合并 `main`，不部署云服务器。

执行结果（2026-08-02）：四个企业微信受控场景、百居易只读、DeepSeek 真实契约、客服账号发现和安全隔离证据均已回填；本地一期验收完成。云服务器部署和未配置的同步消息契约仍不属于本次本地验收范围。

## 修复 Review

- 根因证据：真实只读调用首轮成功收到 `search_availability` 工具调用并执行百居易，第二轮模型返回内容无法通过 `AssistantDecision` 校验；此前代码只重试并最终抛出 `AssistantUnavailableError`。
- 变更：`DeepSeekGuestAssistant` 在房态工具成功后保存日期；若后续结构化结果校验失败，返回包含入住/退房日期的安全查询回执并设置员工确认标记，不猜测房型或数量。
- 回归测试：新增无效工具后续 JSON 测试；全量 `395 passed, 15 skipped`；Ruff、mypy、`git diff --check` 通过。
- 真实验证：DeepSeek + 百居易只读调用返回结构化房态决定，未创建订单、未修改房态、未发送企业微信消息。

## 当前变更 Review

- 已新增无工具快速安抚模型请求，固定写入温暖民宿管家提示词；真实 DeepSeek 快速安抚返回自然中文，未包含员工、模型、数据库、接口或完成承诺。
- 已将正常消息拆为“安抚登记/提交”和“最终模型处理”两个阶段；安抚消息使用独立 `ack` 消息类型，不进入后续模型上下文；最终任务使用 `final:{source_message_id}` 幂等键。
- 已统一客人可见失败、任务和内部确认措辞，员工通知仍保留内部状态。
- 当前阶段自动化验证：全量 `397 passed, 15 skipped`；Ruff、mypy 通过。
- 待完成：本机部署、两阶段企业微信实测首条安抚延迟和最终回复顺序，随后再处理同会话连续消息的旧回复淘汰策略。

## 当前变更：温暖回复与快速安抚

### 实施计划

- [ ] 为客人可见回复增加统一温暖语气策略，覆盖正常回复、任务记录、房态安全回执、人工接管和模型失败兜底；内部“员工确认”等术语只保留在员工通知和任务状态中。
- [ ] 新增快速安抚阶段：入站消息短事务完成去重和记录后，先登记稳定幂等的安抚发送任务，再登记最终回复生成任务；安抚不调用联网、百居易或完整大模型，不承诺业务结果。
- [ ] 将最终回复生成移出企业微信同步消息处理事务，避免模型耗时阻塞安抚发送；保留最终结果、任务建议和员工通知的幂等关系。
- [ ] 防止安抚文本进入最终模型上下文，并处理同一会话多条消息的旧回复乱序。
- [ ] 按 TDD 增加语气、任务回复、安抚优先发送、上下文隔离、重复消息和失败回退测试。
- [ ] 完成全量测试、Ruff、mypy、真实 DeepSeek/百居易只读回放、本机部署和企业微信端到端延迟验收。

### 风险决策（待实现验证）

- 安抚消息采用静态模板优先，不复用完整 DeepSeek 链路；否则无法保证快速。
- 安抚消息使用独立消息类型或上下文过滤标记，不作为 assistant 正式答案交给最终模型。
- 同一会话新消息到达后，旧的最终回复必须丢弃或降级为员工内部记录，不能覆盖新问题。

### 实测缺陷修复计划

- [x] 增加安抚与最终回复使用独立发件箱去重键的失败回归测试。
- [x] 增加客人回复不得出现“跟员工确认”等自然语言变体的失败回归测试。
- [x] 修复最终阶段发件箱编号和统一温暖文案过滤，并保持员工内部通知不变。
- [x] 按来源消息截断模型上下文，并在生成前后淘汰同会话过期最终回复。
- [x] 将快速发送与耗时最终生成拆到独立 worker，避免连续消息互相阻塞。
- [x] 补齐最终任务重放幂等和暂时性失败重试策略。
- [x] 收紧客人文案过滤，保留退款事实及景区“工作人员”等非内部角色语义。
- [x] 运行相关测试、全量测试、Ruff、mypy 和差异检查。
- [x] 部署到本机运行目录并重启服务；备份保存于 `/private/tmp/HomestayBot-pre-deferred-fix-20260802.tar.gz`，健康检查 HTTP 200。
- [x] 完成部署后的企业微信真实消息复验，确认安抚和最终回复均实际送达。

### 实测缺陷修复 Review

- 根因：安抚和最终回复在不同事务中重新从序号 0 生成同一出站去重键；最终任务因此被已完成的安抚发送任务复用。最终阶段现使用显式 `delivery_phase="final"`，重复阶段在写消息前直接跳过。
- 上下文：最终模型按来源客人消息边界读取正式文本，安抚不占上下文；生成前后检查同会话是否出现更新客人问题，旧最终回复直接丢弃。
- 调度：普通 worker 排除最终生成任务，独立 worker 只领取 `wecom_process_message`，避免耗时模型阻塞下一条安抚。
- 文案：只替换民宿内部角色确认短语，保留退款核实对象和景区工作人员等必要语义。
- 验证：`406 passed, 15 skipped`；Ruff、mypy、`git diff --check` 通过。本机源码哈希与 worktree 一致，健康检查 HTTP 200。企业微信真实复验：入站消息 116，快速安抚 117，最终回复 118；发送任务 83/85 和最终任务 84 均为 `COMPLETED`。任务 14 为 `SUPPLIES/PENDING_CONFIRMATION`，凭证投递和房间凭证数量均为 0，客人可见三条消息均无“员工/工作人员”字样。

## 当前变更：员工通知显示房间号

- [x] 员工通知客服账号使用企业微信客服名称，不显示客服 UID。
- [x] 客户存在唯一有效订单时优先显示订单房间号。
- [x] 房间号无法唯一匹配时回退显示客人名称，不显示客人 UID。
- [x] 复用同一 `SQLAlchemyContextRepository` 注入客户上下文和房间号解析。
- [x] 完成房间号唯一/多订单、通知房间号优先和客人名称兜底测试。
- [x] 完成全量测试、Ruff、mypy 和差异检查。

## 当前变更 Review

- 房间号来源为客户未取消、未完成、未退房订单；查询最多读取两笔，只有恰好一笔时才展示，避免多订单误配。
- 员工通知展示顺序为“客服账号名称 + 房间号”，无房间号时使用企业微信客人昵称；两类名称接口失败都不会阻塞通知。
- 验证结果：全量 `410 passed, 15 skipped`；Ruff、mypy、`git diff --check` 通过。
# 当前任务：民宿客服系统继续完成和验证（2026-07-31）

- [x] 只读审计当前代码、任务记录、Git 状态和未完成项
- [x] 确认优先完成本机可执行验证，再推进企业微信真实端到端验收
- [x] 完成功能点 Spec 确认
- [x] 完成风险与决策 Spec 确认
- [x] 执行本机全量自动化验证并记录证据
- [x] 检查并验证 LaunchAgent 运行状态和健康端点
- [x] 验证企业微信端到端测试条件；缺少测试 Token 时明确记录外部阻塞
- [x] 根据验证结果修复必要问题并回归验证
- [x] 完成验收 Review 记录

## 实施计划：继续验证与必要运行时修复（已获 HARD-GATE 授权）

### Task A：基线与运行时诊断

- [x] 在隔离工作区建立基线并运行 `PYTHONPATH=src .venv/bin/pytest -q`
- [x] 重新运行 Ruff、mypy、`git diff --check`
- [x] 采集 LaunchAgent、8010 健康端点、监听端口和脱敏运行日志
- [x] 确认 15 个真实契约测试的跳过条件，不读取或输出密钥

### Task B：企业微信补拉错误可观测性

- [x] 为 `_run_wecom_poll_loop()` 增加错误码日志回归测试
- [x] 只记录 `WeComApiError.error_code` 和退避秒数，不记录 Token、正文或密钥
- [x] 运行企业微信 worker 单元测试和应用循环测试

### Task C：客户上下文维护隔离

- [x] 为 `_run_context_maintenance_loop()` 增加“单个客户维护失败不阻断其他客户、成功周期仍刷新心跳”的失败测试
- [x] 以最小改动隔离单客户异常，保证摘要失败不删除原始消息
- [x] 运行上下文维护、摘要和启动集成测试

### Task D：SQLite 锁竞争验证

- [x] 运行现有 SQLite 锁恢复测试和 FAQ 候选上下文测试
- [x] 检查事务边界和当前运行日志，确认是否需要代码修复
- [x] 无可稳定复现根因时不扩大改动，保留 PostgreSQL 云部署前置项

### Task E：全量回归与运行实例验证

- [x] 运行全量 pytest、Ruff、mypy 和 `git diff --check`
- [x] 验证 LaunchAgent、8010/health 和各项后台心跳
- [x] 验证日志不包含密钥、密码、二维码、完整手机号或客人正文

### Task F：真实外部契约闸门

- [x] 检查 DeepSeek、Hostex、企业微信真实契约测试条件
- [ ] 条件满足时只执行安全测试账号/测试对象的真实契约
- [ ] 条件缺失时记录外部阻塞，不标记为通过

### Task G：Review

- [x] 记录修改文件、测试证据、运行时告警和外部阻塞
- [x] 更新本节 Review，不声称未验证的真实链路已完成

## 新增功能：客诉冷静辅助模块

### 目标与边界

- 客诉、退款、赔偿、差评、举报、平台介入和强烈情绪触发人工模式。
- 客人只收到固定安抚：`我已收到您的诉求，正在火速通知管家，麻烦您稍作等待，我们的管家了解情况后一定会为您解决问题`。
- DeepSeek 只整理客诉事实、诉求、情绪和回复草稿，不判断责任、不承诺退款或赔偿。
- 员工通过企业微信卡片进入后台编辑页面，编辑后才能发送；卡片按钮不得直接发送。

### 实施计划

- [ ] Task 1：建立客诉记录状态和数据库迁移
  - 修改 `src/homestay_bot/domain/enums.py`，增加 `ComplaintReviewStatus`。
  - 修改 `src/homestay_bot/domain/models.py`，增加 `ComplaintReview`，保存来源会话、来源消息 ID、脱敏分析、草稿、版本号、状态和审计时间；不复制完整客人原文。
  - 新建迁移文件，增加来源消息唯一约束、版本字段和状态索引。
  - 先在 `tests/integration/test_complaint_repository.py` 覆盖创建、重复消息幂等、版本冲突、状态迁移和敏感字段边界。

- [ ] Task 2：实现确定性客诉识别和固定安抚
  - 在 `src/homestay_bot/services/complaint_service.py` 实现投诉/退款/赔偿/平台介入/强烈情绪规则，返回原因代码和风险等级。
  - 在 `ConversationService.handle_message()` 中于完整模型调用前识别客诉，切换 `HUMAN_ACTIVE`，发送固定安抚，并登记 `complaint_review` 任务。
  - 同一来源消息只发送一次安抚；客诉模式下不再生成 AI 最终回复。
  - 先在 `tests/unit/test_complaint_service.py` 和 `tests/unit/test_conversation_service.py` 写失败测试，再实现并验证员工先回复、延迟任务取消场景。

- [ ] Task 3：实现 DeepSeek 结构化客诉分析
  - 新建 `src/homestay_bot/integrations/deepseek_complaint.py`，使用严格 JSON schema 输出核心诉求、情绪等级、客人陈述、系统事实、待核实项、责任风险、退款赔偿标记和回复草稿。
  - 提示词明确禁止责任结论、金额承诺、平台规则推测、外部链接和客人身份泄露。
  - 分析失败最多按后台任务策略重试；失败不改变人工模式，不把原始模型响应写日志。
  - 先在 `tests/unit/test_deepseek_complaint.py` 覆盖投诉分析、退款赔偿禁承诺、脱敏和非法 JSON 安全回退。

- [ ] Task 4：接入客诉后台任务和人工通知
  - 在 `src/homestay_bot/services/complaint_review_job.py` 实现任务幂等、状态更新、版本检查和员工卡片内容生成。
  - 在 `src/homestay_bot/worker.py` 注册 `complaint_review_generate`，只重试可安全恢复的分析阶段。
  - 卡片只展示脱敏摘要、房间号优先身份、风险等级和编辑页面入口；不直接携带发送动作。
  - 先在 `tests/unit/test_complaint_review_job.py` 验证成功、失败重试、重复任务、无房间号回退和卡片不含手机号/原文。

- [ ] Task 5：实现企业微信卡片入口和后台编辑发送
  - 在 `src/homestay_bot/integrations/wecom/api_client.py` 增加企业微信应用卡片消息接口，按钮统一指向签名的后台编辑 URL。
  - 新建 `src/homestay_bot/routes/complaints.py` 和 `templates/complaints/edit.html`，实现管理员/值班员工权限、CSRF、版本冲突、编辑、发送、退回、人工处理和关闭。
  - 发送必须从编辑页执行，使用来源客诉 ID 的幂等键；发送成功后状态为 `SENT`，失败回到 `READY_FOR_REVIEW`。
  - 先在 `tests/integration/test_complaint_routes.py` 验证权限、CSRF、并发版本、发送幂等和退款赔偿人工确认。

- [ ] Task 6：补齐隐私、延迟任务和上下文质量修复
  - 延迟任务仅保存消息 ID 和必要结构化字段；完成/失败后清理 payload 中的敏感字段。
  - `process_recorded_message()` 在人工模式下直接丢弃旧最终回复。
  - 发送成功后才写入正式机器人上下文；发送失败不得形成幽灵回复。
  - 上下文维护按客户独立事务执行，单个客户失败不阻断其他客户并刷新心跳。
  - 员工通知名称查询增加缓存，避免在业务事务内重复调用企业微信接口。
  - 为每项修复增加回归测试，覆盖异常恢复和隐私边界。

- [ ] Task 7：明确并实现房间号映射
  - 优先使用 `PropertyProfile.room_number`；无映射时显示房源名称和百居易 ID，并标注房号待补充。
  - 迁移现有房源数据，不改变订单、房态和凭证数据。
  - 增加有映射、无映射、多有效订单和历史订单测试。

- [ ] Task 8：全量验证与本机验收
  - 运行全部单元/集成测试、Ruff、mypy、`git diff --check`。
  - 验证企业微信卡片只打开编辑页，编辑确认后只发送一次。
  - 验证客诉安抚、人工接管、DeepSeek 分析、退款赔偿禁承诺和任务状态。
  - 验证日志、任务 payload、消息历史不包含不必要的客人敏感原文。
  - 更新本节 Review，记录真实外部契约未配置项，不将跳过测试标记为通过。

## 本次继续执行 Review（2026-08-01）

- 客诉、审批幂等、名称缓存、真实房间号、outbox 送达状态和任务正文清理均已实现并合并到 `main`。
- 临时 SQLite 已完成 `upgrade head -> downgrade 0008_complaint_reviews -> upgrade head`，最终版本为 `0010_property_room_number`。
- 合并后主分支验证：`427 passed, 15 skipped`；Ruff、mypy（78 个源文件）和 `git diff --check` 通过。
- 本机运行目录已备份到 `~/Library/Application Support/HomestayBot/.backups/complaint-quality-20260801-104312`，源码已同步并重启 LaunchAgent。
- 本机健康检查 HTTP 200；数据库、worker、企业微信轮询、生命周期调度均为 `ok`；客诉后台入口未登录时返回 303 登录跳转。
- 企业微信真实卡片发送未主动触发，避免向员工产生测试通知；卡片接口已有受控单元测试。

## 测试消息缺陷修复（2026-08-01）

- [x] 核对主分支、遗留工作树和本机部署目录，确认不是未合并分支导致。
- [x] 增加补水任务回复不被房源知识缺口覆盖的回归测试。
- [x] 增加快速安抚统一管家话术和短超时回归测试。
- [x] 修复任务建议优先级、安抚话术校验和模型等待上限。
- [x] 完成全量测试、静态检查和本机部署复验。

### Review

- 根因：`DeepSeekGuestAssistant._validate_decision()` 在识别到服务任务后仍进入房源知识缺口兜底，覆盖了任务回复；`respond_ack()` 允许不含管家话术的模型短句直接发送。
- 修复：服务任务优先保留模型回复；快速安抚要求包含管家和等待语义，模型超时上限调整为 1.5 秒，失败使用统一温暖模板。
- 验证：`429 passed, 15 skipped`；Ruff、mypy（78 个源文件）和 `git diff --check` 通过；本机源码哈希已同步，LaunchAgent 重启后健康检查 HTTP 200。备份位于 `/Users/rin/Library/Application Support/HomestayBot/.backups/fast-ack-task-fix-20260801-1125`。

## 百居易接口规范化（2026-08-01）

- [x] 查询官方接口总览、鉴权、错误码、限流和房源/房态/渠道日历规范。
- [x] 增加 `restrictions: null` 的失败回归测试。
- [x] 将渠道日历可选限制字段归一化为空字典。
- [x] 全量测试、Ruff、mypy、差异检查和真实只读接口复验通过。

### Review

- 官方文档要求渠道日历每日对象必有 `date`、`price`、`inventory`，`restrictions` 为可选对象；本次修复严格按该规范处理缺失值和 `null`。
- 验证：全量 `429 passed, 15 skipped`；真实参考价格查询返回 38 个日历日，全部成功解析；本机健康检查 HTTP 200。

## 当前修复：人工接管期间低风险延迟回复（2026-08-01）

- [x] 核对最新企业微信消息、发送任务和最终处理任务，定位最终回复缺失位置。
- [x] 增加人工接管期间低风险延迟任务仍生成回复的失败测试，并确认因模型未调用而失败。
- [x] 最小修复后台延迟处理的人工作业门控，高风险消息继续由人工处理。
- [x] 运行相关测试、全量测试、Ruff、mypy 和差异检查。
- [x] 备份并部署本机运行目录，重启服务并验证健康状态。
- [ ] 记录 Review 和真实消息复验结果。

### Review

- 消息 140 在 21:11:32 入库，安抚消息 141 在 21:11:34 发出；最终任务 181 虽为 `COMPLETED`，但没有最终房态回复。
- 根因是同步入口已允许人工接管期间的低风险问题继续回答，但后台 `process_recorded_message()` 仍对所有 `HUMAN_ACTIVE` 会话直接返回。
- 修复后后台只丢弃当前消息本身属于退款、投诉、价格等高风险事项的任务；房态、旅游等独立低风险问题继续回答，且会话仍保持人工模式。
- TDD 证据：新增测试修改前按预期失败（模型调用 0 次），修改后相关 9 项测试通过。
- 全量验证：`431 passed, 15 skipped`；Ruff、mypy（78 个源文件）和 `git diff --check` 通过。
- 部署备份位于 `/Users/rin/Library/Application Support/HomestayBot/.backups/human-deferred-low-risk-20260801-211635`；部署后源码哈希一致，LaunchAgent 正常运行，健康检查 HTTP 200。
- 待用户重新发送同类消息，完成部署后的企业微信真实回复验收；不重放消息 140，避免重复回复。

## Obsidian 经验归档与防回归手册（2026-08-01）

- [x] 汇总 `tasks/lessons.md`、实施 Review、真实故障和未完成风险。
- [x] 创建 Obsidian 长期手册，按消息链路、人工接管、DeepSeek、百居易、知识库、CRM、任务和凭证分类。
- [x] 为每类代码结论补充实际文件路径与函数名。
- [x] 增加故障根因表、变更影响面清单、部署检查清单和企业微信真实验收清单。
- [x] 在 `tasks/lessons.md` 增加长期手册入口，确保后续会话能够发现并复查。
- [x] 验证 Markdown、双链、源码路径和敏感信息边界。

### Review

- 长期手册：`YuMi民宿AI开发经验与防回归手册.md`。
- Obsidian CLI 在当前终端不可用，采用库内文件直接写入；不影响 Obsidian 自动索引该 Markdown 笔记。
- 已确认当前目录是 Obsidian 库根目录；frontmatter 可由 YAML 解析，5 个标签有效，所有引用源码路径存在，项目双链目标存在，敏感值扫描无匹配，`git diff --check` 通过。

## 当前修复：安抚意图门控与百居易房间名称（2026-08-01）

- [x] 核对最新消息、安抚/最终出站记录和任务状态，确认不是企业微信投递失败。
- [x] 定位所有消息统一安抚的入口，以及房态工具没有返回房间名称的原因。
- [x] 先增加三条失败回归测试：信息问题跳过安抚、房态结果携带百居易房间名、房间介绍调用房源目录。
- [x] 实现按意图发送安抚，并扩展百居易只读房源名称查询。
- [x] 运行相关测试、全量测试、Ruff、mypy 和差异检查。
- [x] 备份部署并完成本机健康与百居易真实只读验证。
- [ ] 用新企业微信消息完成部署后的真实回复验收。

### Review

- 根因：`ConversationService._stage_fast_ack()` 原先对所有延迟消息调用 `respond_ack()`；`HostexReadOnlyToolExecutor` 原先只返回 `property_id`，工具定义也没有房源目录查询。
- 修复：服务/补给/维修等需要等待的请求才发送安抚；普通信息问题直接排最终任务。新增 `list_properties` 只读工具，房态结果增加 `property_title`，独立房间介绍强制查询房源目录。
- TDD：新增测试修改前按预期失败，修改后会话 37 项、DeepSeek 33 项通过；全量 `434 passed, 15 skipped`；Ruff、mypy 和差异检查通过。
- 本机部署备份：`/Users/rin/Library/Application Support/HomestayBot/.backups/ack-property-name-20260801-213401`；部署后健康检查 HTTP 200，百居易真实只读返回 7 个房源名称。
- 部署后企业微信消息 151“现在有几间房可用”未发送安抚，最终消息 152 已显示《春和景明》和“收藏家套房”等百居易房源名称；消息 155 的补被子请求发送安抚，符合服务请求门控。
- 仍待单独发送“介绍一下这间房”完成独立房间介绍场景的企业微信验收；当前数据库没有部署后新的该文本消息。

## 客诉后台任务失败诊断（2026-08-02）

- [x] 查询失败任务、重试次数、客诉记录状态和本机日志。
- [x] 确认客人固定安抚已送达，失败发生在后台 `complaint_review_generate`。
- [x] 对同一 DeepSeek 客诉契约做不输出原文的只读复现，记录 JSON 类型和 Pydantic 错误字段。
- [ ] 另行编写 Spec，决定布尔字符串兼容策略、日志字段和失败重试规则；本次只完成诊断，不修改生产代码。

### Review

- 任务 194：`complaint_review_generate`，重试 3 次后 `FAILED`，错误码为 `ComplaintDraftUnavailableError`；对应客诉记录仍是 `PENDING_ANALYSIS`，没有分析或草稿。
- 请求边界成功：DeepSeek 返回内容长度约 1000 字且为合法 JSON，失败不是网络连接或 JSON 顶层语法错误。
- 根因：`src/homestay_bot/integrations/deepseek_complaint.py::DeepSeekComplaintAnalyzer.generate()` 调用 `ComplaintDraft.model_validate_json()` 时，`refund_or_compensation` 和 `platform_escalation_risk` 返回为字符串，触发两个 `bool_parsing` 校验错误；该方法随后把底层异常统一包装为 `ComplaintDraftUnavailableError`，因此日志没有暴露字段级原因。
- 影响边界：客人固定客诉安抚正常发送；仅后台草稿、管理员卡片和编辑页数据没有生成。当前共有多个 `PENDING_ANALYSIS` 客诉记录，不能通过重放旧任务解决，需先完成兼容策略 Spec。

## 当前修复：客诉草稿布尔字段兼容（2026-08-02）

- [x] 将真实失败响应归纳为字段级回归场景。
- [x] 增加模型返回描述句、且本地有/无风险信号的失败回归测试，并确认修复前红灯。
- [x] 在模型解析边界用最新客人消息和本地原因确定性覆盖两个风险标记。
- [x] 增加责任风险错误类型的回归测试，并以“待核实”保留人工判断。
- [x] 运行客诉相关测试、全量测试、Ruff、mypy 和差异检查。
- [x] 备份并部署本机运行目录，验证源码哈希、LaunchAgent 和健康端点。
- [ ] 使用新的客诉测试消息验证后台草稿与管理员通知。

### Review

- 根因：DeepSeek 将两个布尔风险字段返回成说明句，真实复验中还把责任风险文字返回成布尔值；严格 Pydantic 校验因此让整个后台草稿失败。
- 修复：模型负责事实分析和回复草稿；退款、补偿及平台升级风险改由最新客人消息和本地客诉原因确定性计算。非文字责任风险统一回退为“待核实”，不替人工判断责任。
- TDD：两条布尔说明句测试和一条责任风险类型测试均在修改前按预期失败，修改后客诉分析与任务链 `9 passed`。
- 全量验证：`437 passed, 15 skipped`；Ruff、mypy（78 个源文件）和 `git diff --check` 通过。
- 真实 DeepSeek 隔离契约通过：两个风险字段类型均为 `bool`，责任风险为 `str`，草稿不含链接或退款金额承诺；测试未发送企业微信、未写数据库。
- 部署备份：`/Users/rin/Library/Application Support/HomestayBot/.backups/complaint-parser-20260802-B0sAU2`。部署后源码哈希一致，LaunchAgent 运行中，健康检查 HTTP 200，各后台核心状态为 `ok`。
- 未重放失败任务 194，避免重复管理员通知；仍需一条新的真实客诉消息完成企业微信端到端验收。
# 当前任务：全仓代码质量与风险审计（2026-08-02）

- [x] 建立测试、静态检查、依赖和迁移基线
- [x] 审计消息/模型/外部接口链路
- [x] 审计数据库、任务队列、并发和性能边界
- [x] 审计后台路由、权限、隐私和输入校验
- [x] 汇总按严重性排序的问题与优化方案
- [x] 经用户确认后按批次实施修复并回归验证

### 批次实施进度

- [x] 修复企业微信/百居易公网回调的认证前请求体限制与解析顺序。
- [x] 修复房源、任务页面未知异常回显；保留安全错误日志和追踪号。
- [x] 按用户确认保留 STAFF 客诉后台权限，不额外收窄值班客服角色。
- [x] 统一 DeepSeek 个人信息脱敏并过滤客诉凭证、地址、二维码和链接。
- [x] 将客户上下文维护改为每客户独立会话和事务，失败隔离并继续刷新心跳。
- [x] 将客诉发送状态拆为入队、投递失败和真实发送成功，并增加投递版本推进。
- [x] 用数据库保存点隔离客诉幂等唯一键竞争，避免回滚同事务其他写入。
- [x] 将日志脱敏过滤器安装到 handler，并覆盖子 logger 传播场景。
- [x] 为图片、语音、视频、文件和位置消息保存最小安全元数据，不把媒体正文交给模型。
- [ ] 健康接口详细状态是否需要进一步拆分为公开存活与内部运行状态，保留现有本地监控契约后续评估。
- [ ] DeepSeek、百居易、企业微信真实外部契约仍受凭据/测试对象条件限制，未将跳过项标记为通过。

#### Review（批次实施）

- 作用域修复后 outbox、客诉状态和应用上下文测试：`19 passed`。
- 客诉保存点回归：重复唯一键不会回滚同事务客户写入，仓储测试：`5 passed`。
- 路由加固代理验证：目标路由测试 `25 passed`，包含请求体超限和异常脱敏。
- 日志 handler 级脱敏测试：`4 passed`；媒体安全元数据与消息回归：`6 passed`。
- Alembic 临时 SQLite 回放：`upgrade head -> downgrade 0011 -> upgrade head`，最终 `0012_message_metadata`。

#### Review（仅审计，尚未修改生产代码）

- 基线：`437 passed, 15 skipped`；Ruff 全仓通过；mypy 78 个源文件通过；`pip check` 无依赖冲突；迁移链单头为 `0010_property_room_number`；`git diff --check` 通过。
- P1：企业微信回调和百居易 Webhook 均未在应用层限制请求体大小；企业微信外层 XML、百居易 JSON 都在认证校验前解析，公开端点存在内存/CPU 消耗风险。
- P2：房源和任务页面把未知异常原文作为 HTTP 409 返回；可能泄露 SQL、文件路径或第三方请求细节。
- P2（需确认角色边界）：客诉页面和服务层只校验登录，不校验角色；任意 `STAFF` 可读取完整客诉并编辑、发送、退回或关闭。
- P2：DeepSeek 预订关键词分支绕过姓名和手机号脱敏；客诉上下文只脱敏手机号、邮箱和长编号，可能把门锁密码、详细地址或入住凭证正文送入模型。
- P2：客户上下文维护把所有客户放在一个会话和一个异常边界内，单客户摘要失败会阻断后续客户并阻止心跳更新。
- P2：客诉发送状态在事务型 outbox 入队后立即标记 `SENT`，实际企业微信投递失败或尚未投递时后台状态仍显示已发送。
- P2：客诉幂等创建遇到并发 `IntegrityError` 时直接回滚整个会话，可能连带回滚同一事务已经写入的消息和安抚任务；应改用保存点或数据库原子 upsert。
- P3：日志脱敏过滤器安装在 logger 而不是统一 handler，子 logger 记录向上传播时可能绕过过滤；需要 handler 级过滤和回归测试。
- P3：公网健康接口返回各后台组件和配置状态；图片/语音/文件消息目前只保存空正文，无法直接用于入住指南导入。
- 外部契约测试未启用：DeepSeek、百居易、企业微信共 15 项跳过，不能据此声称真实联调已通过。

#### Review（2026-08-02 继续执行）

- 修复 `migrations/versions/0013_complaint_delivery_links.py`：新增唯一约束和降级删除均改用 `batch_alter_table`，解决 SQLite `ALTER` 约束不支持问题。
- 新增日志字典消息、`extra` 字段脱敏回归；新增 `MessageService` 到 SQLite 的 `message_metadata` 实际落库断言。
- 新增运行类型依赖 `types-defusedxml`；开发环境和本机运行环境均已安装 `defusedxml`。
- 最新全量验证：`460 passed, 15 skipped, 1 warning`；Ruff、mypy（78 个源文件）、`pip check`、`git diff --check` 全部通过。
- 临时 SQLite 已完成 `upgrade head -> downgrade 0012_message_metadata -> upgrade head`；本机数据库已从 `0010_property_room_number` 升级到 `0013_complaint_delivery_links`。
- 本机部署备份：`/Users/rin/Library/Application Support/HomestayBot/.backups/security-audit-20260802-070323`；LaunchAgent 重启后健康检查 HTTP 200，数据库、worker、企业微信补拉、百居易同步、上下文维护和生命周期调度均为 `ok`。
- 外部边界：DeepSeek、百居易和企业微信真实契约共 15 项仍因测试凭据/测试对象条件跳过；健康页 `web_search` 当前为 `unknown`、`wecom_contact_sync` 为 `not_configured`，未标记为通过。

## 当前修复：客诉投递与回调边界闭环（2026-08-02）

- [x] 复现并核对客诉重试来源、异步失败回写和回调 XML 解析路径。
- [x] 修复带重试阶段来源的客诉投递状态解析。
- [x] 核对客诉通知失败后的任务恢复与人工可重试状态；现有内部卡片任务保留有限重试，后台状态不因入队失败提前置为完成。
- [x] 增加人工草稿隐私脱敏和客诉投递回归测试。
- [x] 运行目标测试、全量测试、Ruff、mypy、依赖和差异检查。

### Review

- 根因：客诉重试来源包含 `:retry-*` 阶段后缀，旧解析只接受纯数字来源，导致重试结果无法回写客诉状态。
- 修复：回写边界只解析 `complaint:<id>` 的稳定主键部分，保留阶段后缀用于幂等键隔离。
- TDD：新增重试来源回归测试先失败，修复后目标客诉/回调测试 `46 passed`。
- 全量验证：`462 passed, 15 skipped, 1 warning`；Ruff、mypy、pip check 和 `git diff --check` 通过。
- 本机部署备份：`/Users/rin/Library/Application Support/HomestayBot/.backups/complaint-retry-20260802-190224`；运行源码哈希与工作区一致，LaunchAgent 运行中，健康检查 HTTP 200。

## 当前修复：企业微信异步投递失败（2026-08-02）

- [x] 核对最新测试消息的入库、出站任务和平台异步回执。
- [x] 确认根因：`kf/send_msg` 已受理但后续产生 `msg_send_fail`，失败类型为 `13`。
- [x] 普通机器人消息记录失败状态并从后续模型上下文排除。
- [x] 普通机器人消息最多自动重试一次，重试仍失败时通知员工人工跟进。
- [x] 部署运行时修复，并对本次失败消息执行一次安全重试。
- [x] 完成全量回归、静态检查和部署后健康检查。

### Review

- 原消息和一次重试均收到企业微信 `msg_send_fail` 类型 `13`；两条失败消息均已记录，人工通知任务已完成。
- 全量验证：`465 passed, 15 skipped, 1 warning`；Ruff、mypy、pip check 和 `git diff --check` 通过。
- 本机运行源码哈希与工作区一致，LaunchAgent 运行中，健康检查 HTTP 200。

## 当前修复：企业微信安全限制兜底回复（2026-08-02）

- [x] 核对 `msg_send_fail` 官方失败类型定义。
- [x] 为 `fail_type=13` 增加短文本安全兜底，避免重复发送被拦截正文。
- [x] 增加安全限制回归测试并通过全量验证。
- [x] 备份并部署运行副本，恢复旧消息的一次兜底回复。

### Review

- 官方文档确认 `fail_type=13` 为“安全限制”；发送接口成功返回不代表最终展示成功。
- 全量验证：`466 passed, 15 skipped, 1 warning`；Ruff、mypy（78 个源文件）通过。
- 运行副本健康检查：数据库、worker、企业微信轮询及配置均为 `ok`。
- 旧消息详细正文仍标记为失败；已补发短兜底“我已收到您的问题，正在为您核实相关信息，请稍等片刻。”，企业微信暂未产生新的失败事件。

# 当前任务：第二轮全仓代码质量优化（2026-08-03）

- [x] 复核工作树、既有审计结论和相关开发教训
- [x] 重跑测试、静态检查、依赖检查、编译检查和迁移头检查
- [x] 并行审计消息与模型、任务队列与数据库、权限与外部接口边界
- [x] 用户确认现状分析和优化功能点
- [x] 用户确认风险、实施边界和最终方案 A
- [x] 审查冻结候选补丁中的任务队列恢复范围、SQLite 领取竞态和 stale 最大重试边界
- [x] 审查冻结候选补丁中的失败任务正文清理
- [x] 完善异步外部发送不确定状态与平台幂等关联
- [x] 审查冻结候选补丁中的 SQLite 外键约束与部署启动迁移 preflight
- [x] 审查冻结候选补丁中的 Hostex 事件、生命周期、凭证和上下文摘要事务调整
- [x] 完善外部结果回写的条件更新与不确定状态
- [x] 审查冻结候选补丁中的百居易订单查询分页
- [x] 完善关键幂等写入竞争保护
- [x] 审查冻结候选补丁中的上下文摘要、客诉详情和后台列表批量边界
- [x] 建立任务、消息、审计、回调和附件的保留清理策略
- [x] 审查冻结候选补丁中的高频查询复合索引
- [x] 完成全量回归、迁移回放、静态检查和本机运行验收
- [x] 记录本轮 Review，明确通过项、跳过项和残余风险

### Review（2026-08-03）

- 通过：批次一独立 Spec 复审；worker 类型隔离、stale 上限、唯一键保存点、敏感载荷清理、外部发送不确定状态和取消释放均有回归证据。
- 通过：批次二 Hostex 分页、上下文批处理、客诉详情分页、任务/审批/知识/客户后台列表分页、Webhook/表单边界和分层健康接口。
- 通过：批次三外部调用前提交、Hostex 事件条件回写、SQLite 外键、保留清理循环、复合索引、启动迁移 preflight 和迁移链。
- 验证：`514 passed, 15 skipped`；Ruff 全仓通过；mypy 79 个源文件通过；pip check、compileall、`git diff --check` 通过；PostgreSQL 离线 SQL 到 `0014_query_indexes` 通过；SQLite upgrade/downgrade/upgrade 回放成功。
- 跳过：15 个真实 DeepSeek、百居易、企业微信契约测试因未显式开启而跳过；未执行真实 PostgreSQL 在线迁移和 LaunchAgent 重启；未做应用装配和 worker 注册的大范围结构拆分，以控制风险。
- 状态：未部署、未提交；当前工作树包含此前用户既有改动及本轮候选补丁，不能整体回滚或直接覆盖。

### 最终实施 Spec

- 本地 SQLite 仅支持单应用进程内的两个 worker；云部署使用 PostgreSQL，不为 SQLite 增加多进程分布式锁。
- 同一任务、会话、消息、百居易事件和业务任务的并发写入必须原子幂等；唯一键竞争不得破坏外层事务或终止 worker。
- worker 只能领取和恢复自己负责的任务；长任务不得被另一 worker 误恢复。重试达到上限必须终止，最终任务不得保留客人或机器人正文。
- 企业微信等外部副作用发生但本地结果无法确认时进入待人工复核状态，禁止盲目自动重放。
- 百居易订单按官方 `offset` / `limit` 契约完整分页；重复页、超过安全页数或响应异常必须显式失败，禁止静默漏单。
- 上下文摘要、客诉详情和后台列表必须有批次、字符或分页边界；处理顺序稳定，不丢消息、不串客户。
- 网络调用移出锁事务后，结果回写必须带状态或版本条件，不得覆盖员工的新操作。
- Webhook、上传和文本表单在服务端限制体积、深度、字段长度和数量；公开健康接口不暴露内部组件状态。
- 部署入口先备份和执行迁移，再启动应用；普通 Web 请求不执行迁移。SQLite 与 PostgreSQL 迁移都要验证，失败停止启动。
- 风险修复完成前不做大范围架构重写；最后只拆分直接影响可测试性的应用装配和 worker 注册代码。

### 执行批次与验收

#### 批次一：任务队列、幂等与隐私

- [x] 逐项证明冻结候选测试能覆盖 worker 互相恢复、SQLite 双领取、stale 重试上限和终态正文清理；不充分时先补失败测试。
- [x] 实现或修正按任务类型恢复、SQLite 原子领取/进程内串行边界、租约语义和取消安全释放。
- [x] 为 Job、Conversation、Message、HostexWebhookEvent、BusinessTask 的关键唯一键竞争增加并发测试和原子写入。
- [x] 为外部发送成功但本地提交失败增加 `NEEDS_REVIEW` 或等价确定性状态，验证不会自动重发。
- [x] 运行队列、消息、回调和业务仓储测试；完成 Spec 审查与代码质量审查后才进入批次二。

#### 批次二：百居易、上下文与公网边界

- [x] 复核百居易分页实现与官方 `offset` / `limit` 契约，覆盖 45 条、重复页和 100 页上限。
- [x] 复核上下文每批条数、单条字符、总字符和既有摘要上限，验证多批最终完整处理。
- [x] 为客诉详情及后台列表增加分页；为 Webhook JSON 深度、事件字段长度、上传体积和表单长度增加服务端边界。
- [x] 将 `/health` 收敛为公开整体状态，并为管理员保留鉴权后的详细诊断。
- [x] 运行 Hostex、上下文、路由和权限测试；完成批次二定向回归。

#### 批次三：事务、迁移与有限结构优化

- [x] 分别审查 Hostex 事件、生命周期、凭证投递和摘要流程，只把存在锁风险的外部调用移出事务，并加入条件回写测试。
- [x] 修复 Alembic 离线 SQL 生成，验证 SQLite 全链升级/允许降级；Docker 可用时验证临时 PostgreSQL 在线升级。
- [x] 统一部署迁移入口、SQLite 外键和必要复合索引；`ruff check .` 已覆盖迁移目录。
- [ ] 在行为测试保持不变的前提下，只提取应用装配和 worker handler 注册边界，不重写 ConversationService 或 DeepSeek 协议。
- [ ] 运行全量 pytest、Ruff、mypy、pip check、compileall、迁移回放和 `git diff --check`，再检查本机运行健康状态。

### Review（2026-08-03 部署收尾）

- [x] 主干提交 `a57afd2` 已包含本轮代码、迁移、测试和部署入口改动；历史功能分支提交已在主干祖先链中。
- [x] 本机运行副本已同步主干源码，数据库实际版本为 `0014_query_indexes (head)`，LaunchAgent `com.rin.homestay-bot` 正常运行。
- [x] 新鲜验证：`514 passed, 15 skipped`；Ruff、mypy（79 个源文件）、pip check、compileall 和 `git diff --check` 全部通过。
- [x] 运行目录已有部署前备份；两份房源资料因包含敏感信息，继续作为未跟踪文件保留，不纳入 Git。
- [ ] 未配置 Git 远程仓库，因此本次无法执行远程 push；当前交付为本地 `main` 主干合并及本机部署。

### 当前只读基线

- 全量测试：`466 passed, 15 skipped, 1 warning`；15 项为显式关闭的真实 DeepSeek、百居易和企业微信契约测试。
- `ruff check src tests`、mypy（78 个源文件）、`pip check`、Python 3.12 编译和 `git diff --check` 均通过。
- `ruff check .` 额外发现既有迁移 `0008_complaint_reviews.py` 两处 E501；不影响运行，但说明当前 Ruff 验收范围没有覆盖迁移目录。
- Alembic 单头为 `0013_complaint_delivery_links`；PostgreSQL 离线 SQL 生成在既有 `0003_customer_crm.py` 数据回填处失败，真实 PostgreSQL 在线迁移尚未验证。
- 本轮仍处于 Spec 确认阶段；并行审计任务曾越过 HARD-GATE 产生候选代码，现已冻结，不计为完成项。最终 Spec 确认后逐项审查，只有符合方案并通过 TDD 与回归的部分才保留。

## AKROS.ICU 可编辑数据库后台

- [x] 核对现有员工后台、权限边界、模板结构和数据库模型
- [x] 重新输出包含基础调试、精致 UI 和登录验证的现状分析 Spec，并等待确认
- [x] 输出功能点 Spec 并等待确认
- [x] 输出风险与决策 Spec 并等待确认
- [x] 确认后编写实现计划
- [ ] 按计划实现可编辑页面与权限保护
- [ ] 完成自动化测试、云端部署和公网验收

### 已确认实施边界

- 后台采用现有 FastAPI + Jinja 架构，不引入 Vue、React、Tailwind 或第二套部署服务。
- 仅一个独立管理员账号可登录；企业微信 OAuth 退出管理后台登录链路，但不影响微信客服渠道。
- 外部凭据保存为数据库加密不可变快照；候选配置测试通过后自动激活，保留上一有效版本回滚。
- 新请求使用新客户端快照，在途请求继续持有旧快照；旧客户端只在租约归零后关闭。
- 数据库地址、会话密钥、数据加密密钥和配置加密主密钥禁止网页修改。
- 后台只编辑业务对象，不提供任意 SQL、消息表、队列表、审计表或密文直接编辑。
- AI 调试不发送企业微信消息、不写正式会话、不创建任务，只允许百居易只读查询。
- UI 使用海军蓝、暖金色和浅灰白，系统中文字体；移动端优先，验收 375/768/1024/1440 四档。

### 批次 0：隔离工作区与基线

**范围：** 保留当前主工作区中用户未跟踪资料和本任务记录，在独立 worktree 开发。

- [x] 运行 `git status --short --branch`，确认只存在本任务的 `tasks/*.md` 改动和用户既有未跟踪资料；不得把敏感资料加入 Git。
- [x] 运行 `pytest -q`、`ruff check .`、`mypy src/homestay_bot`、`git diff --check`，保存修改前基线。
- [x] 使用 `using-git-worktrees` 建立 `yumi-admin-console` 功能 worktree；后续代码只在该 worktree修改。

### 批次 1：管理员凭证模型、迁移与密码服务

**文件：**

- 修改：`pyproject.toml`，增加 `argon2-cffi` 运行依赖。
- 修改：`src/homestay_bot/domain/models.py`，新增 `AdminCredential`、`RuntimeConfigVersion`、`RuntimeConfigState`。
- 修改：`src/homestay_bot/config.py::Settings`，增加不可网页修改的 `config_encryption_key`、`admin_bootstrap_username`、`admin_bootstrap_password_hash`。
- 新建：`migrations/versions/0015_admin_runtime_config.py`，`down_revision = "0014_query_indexes"`。
- 新建：`src/homestay_bot/repositories/admin_credentials.py`。
- 新建：`src/homestay_bot/services/admin_auth_service.py`。
- 新建测试：`tests/unit/test_admin_auth_service.py`、`tests/integration/test_admin_credential_repository.py`。
- 修改测试：`tests/unit/test_models.py`、`tests/unit/test_config.py`、`tests/unit/test_migrations.py`、`tests/integration/test_schema_indexes.py`。

**关键接口：**

```python
class AdminAuthService:
    async def authenticate(self, username: str, password: str, now: datetime) -> AdminSession
    async def change_password(self, admin_id: int, current: str, new: str) -> None
    async def reverify(self, admin_id: int, password: str) -> None
    async def revoke_other_sessions(self, admin_id: int) -> int
```

- [x] 先写失败测试：Argon2id 校验、错误凭据统一失败、5 次失败锁 15 分钟、成功清零、首次改密、会话版本递增。
- [x] 运行定向测试，确认因模型/服务不存在而失败。
- [x] 实现单例管理员凭证；`password_hash` 只存哈希，审计不记录用户名密码正文。
- [x] 实现首次 bootstrap：只导入环境中的用户名和预生成 Argon2 哈希，不读取或保存明文初始密码。
- [x] 实现 `0015` 的 PostgreSQL/SQLite 兼容迁移、索引、外键和单例约束。
- [x] 运行迁移 upgrade/downgrade/upgrade、模型、配置和认证服务测试。
- [x] 提交 `feat: add secure single-admin credentials`。

### 批次 2：独立登录、会话保护与账号安全页

**文件：**

- 修改：`src/homestay_bot/routes/employee_auth.py`。
- 修改：`src/homestay_bot/repositories/employees.py`，保留 Employee 外键身份并增加管理员会话复核适配。
- 修改：`src/homestay_bot/application.py::application_lifespan()`，装配管理员认证服务。
- 新建：`src/homestay_bot/templates/layouts/auth.html`。
- 新建：`src/homestay_bot/templates/auth/login.html`、`auth/change_password.html`、`account/detail.html`。
- 新建测试：`tests/integration/test_admin_auth_routes.py`。
- 修改测试：`tests/unit/test_employee_auth.py` 及现有 route 测试中的 OAuth 登录夹具。

**路由：**

```python
GET  /employee/login
POST /employee/login
POST /employee/logout
GET  /employee/account
POST /employee/account/password
POST /employee/account/revoke-sessions
```

- [x] 先写失败测试：GET 登录页、CSRF、站内 next、相同错误文案、锁定、首次改密、退出、8 小时闲置、会话版本失效。
- [x] 登录成功前清空旧 session，再写 `employee_id`、管理员角色、`admin_session_version`、`last_activity_at`。
- [x] 改造 `require_employee_session()`：每次复核唯一管理员启用状态、版本和闲置时间；未登录的 HTML 页面统一跳登录。
- [x] 删除管理后台企业微信 OAuth 跳转入口；保留企业微信客服 API 和员工业务身份模型。
- [x] 所有改密、退出和撤销会话动作使用 POST + CSRF；敏感操作错误不进入日志。
- [x] 运行认证路由和全部现有后台 route 测试。
- [x] 提交 `feat: replace admin OAuth with password login`。

### 批次 3：统一 UI Shell、总览与现有页面迁移

**文件：**

- 新建：`src/homestay_bot/web.py`，集中唯一 `Jinja2Templates` 和中文展示 filter。
- 新建：`src/homestay_bot/templates/layouts/admin.html`。
- 新建：`src/homestay_bot/templates/components/icons.html`、`components/ui.html`。
- 新建：`src/homestay_bot/static/admin.js`。
- 重构：`src/homestay_bot/static/app.css`，保留旧类兼容层。
- 新建：`src/homestay_bot/routes/admin.py`、`services/admin_dashboard_service.py`。
- 新建：`migrations/versions/0016_admin_dashboard_indexes.py`，为总览的入住/退房日期查询增加复合索引。
- 新建：`src/homestay_bot/templates/admin/dashboard.html`、`admin/diagnostics.html`。
- 修改：`src/homestay_bot/main.py`、`application.py::application_lifespan()`。
- 修改：`templates/tasks/*`、`properties/*`、`knowledge/*`、`customers/*`、`approvals/*`、`complaints/edit.html`，继承统一 shell。
- 新建：`templates/knowledge/detail.html`，为既有 FAQ 提供真实编辑入口。
- 新建测试：`tests/unit/test_admin_dashboard_service.py`、`tests/unit/test_template_helpers.py`、`tests/integration/test_admin_dashboard_routes.py`。

**关键接口：**

```python
class AdminDashboardService:
    async def get_snapshot(self, now: datetime) -> AdminDashboardSnapshot

@router.get("/employee/admin")
async def admin_dashboard(request: Request) -> HTMLResponse
```

- [x] 先写模板 smoke 和 dashboard 失败测试：统一导航、active 状态、健康降级仍可打开、页面不含密钥/UID/门锁密码/消息正文。
- [x] 实现全局 shell、移动抽屉、SVG 图标、状态组件、flash 区、提交禁用和未保存离页提示；无 JS 时核心表单仍能提交。
- [x] 实现今日入住/退房、房态、待办和组件健康的只读聚合；按 `Asia/Shanghai` 计算今日边界。
- [x] PostgreSQL 总览读取使用短只读一致性事务；入住/退房日期查询具备匹配索引，避免历史订单增长后扫描全表。
- [x] 逐页迁移现有模板，不改变原 POST URL、权限、CSRF 和业务服务调用。
- [x] FAQ 列表新增明确的编辑详情入口；客诉列表未实现前不放失效导航。
- [x] 验证 focus-visible、44px 点击区域、reduced-motion 和小屏无横向溢出。
- [x] 运行全部后台路由、dashboard 和模板测试。
- [x] 提交 `feat: add responsive YuMi admin console`。

### 批次 4：加密配置快照、版本仓储与设置页面

**文件：**

- 新建：`src/homestay_bot/domain/runtime_config.py`，定义完整不可变 `RuntimeConfigSnapshot` 和脱敏 view model。
- 新建：`src/homestay_bot/services/runtime_config_cipher.py`。
- 新建：`src/homestay_bot/repositories/runtime_config.py`。
- 新建：`src/homestay_bot/services/runtime_config_service.py`。
- 新建：`src/homestay_bot/routes/runtime_config.py`。
- 新建：`src/homestay_bot/templates/admin/settings.html`、`admin/config_versions.html`。
- 修改：`src/homestay_bot/main.py`、`application.py::application_lifespan()`。
- 新建测试：`tests/unit/test_runtime_config_cipher.py`、`tests/unit/test_runtime_config_service.py`、`tests/integration/test_runtime_config_repository.py`、`tests/integration/test_runtime_config_routes.py`。

**关键接口：**

```python
class RuntimeConfigService:
    async def create_and_test(self, command: UpdateRuntimeConfig, actor_id: int, admin_id: int, password: str, expected_session_version: int, expected_revision: int) -> ActivationResult
    async def rollback(self, actor_id: int, admin_id: int, password: str, expected_session_version: int, expected_revision: int, expected_previous_version_id: int) -> ActivationResult

class RuntimeConfigRepository:
    async def create_candidate(self, encrypted_payload: bytes, masked_summary: dict[str, object]) -> RuntimeConfigVersion
    async def activate(self, version_id: int, expected_revision: int) -> RuntimeConfigState
```

**设计复审补充（批次 4 执行前反向同步）：**

- 新建 `migrations/versions/0017_runtime_config_lifecycle.py`：为候选版本增加状态、脱敏测试结果、稳定失败码、激活时间、基线版本和基线修订号；测试失败候选必须可追踪但绝不激活。
- 拆分不可网页修改的 bootstrap 配置与可进入快照的外部业务 API 环境配置；外部 API 环境字段缺失时，登录和设置页仍能启动修复。`public_base_url` 属于部署/回调边界，禁止网页修改。
- `create_and_test` 在测试前一次读取基线快照与 revision，候选记录该基线；测试后只能用原 revision CAS 激活，并发变更必须标记 conflict。
- `rollback` 的 service、表单和 route 都必须携带页面读取的 `expected_revision`，禁止回滚时重新读取新 revision。
- 创建候选和回滚的二次认证都携带当前 `admin_session_version`；回滚额外绑定页面看到的 `expected_previous_version_id`，防止旧页面误操作新版本。
- 快照增加严格的 `schema_version` 信封、字段类型和长度验证，并覆盖安全 `repr`；页面及错误日志不得通过对象表示泄露秘密。
- 密文和解密 JSON 设固定大小上限，未知 schema/畸形信封映射为受控错误；`RuntimeConfigState` 迁移初始化单例，并约束 `revision >= 0` 且 active/previous 不得相同。
- 激活与回滚使用不同用途的服务端一次性 CSRF nonce；同一动作允许最多 8 个多标签页 nonce 独立消费。可选 `wecom_contact_secret` 使用明确“清除”动作，空白输入仍表示保留。
- 批次 4 只装配注入的 TesterPort/stub；真实外联探针留到批次 5，进程内客户端热切换留到批次 6。页面必须明确“版本已保存，当前进程将在安全热切换能力完成后使用”，不得宣称数据库指针更新等于当前进程立即生效。

- [x] 先写失败测试：用途隔离、错误主密钥不可解密、响应/日志不泄密、失败候选可追踪但不激活、测试期间并发激活导致乐观版本冲突、回滚指针正确。
- [x] 采用整份快照单密文而非逐字段写入；`RuntimeConfigState` 单例保存 active/previous/revision。
- [x] 页面空白密钥表示“保留旧值”，明确输入新值才替换；页面只返回是否配置和末尾掩码。
- [x] 二次密码验证成功后才能测试、激活或回滚；AuditLog 只存字段名、版本、错误码和结果。
- [x] 设置页所有 POST 使用按用途和管理员绑定的服务端原子 CSRF nonce，多标签页互不覆盖；响应 `Cache-Control: no-store`。
- [x] 首次无数据库版本时使用环境快照；首次成功激活后数据库版本优先。
- [x] 运行配置 service、repository、route、retention 和泄密边界测试。
- [x] 提交 `feat: add encrypted runtime configuration`。

**批次 4 Review：** `470f838`、`3fe8766`、`a9935cd`、`30dd45f`；全量 `677 passed, 15 skipped`，Ruff、mypy（93 个源码文件）、迁移回放、编译和 diff-check 通过；独立质量复审 APPROVED。回滚点为 `0f52bdd`。

### 批次 5：无副作用连接测试与外联地址防护

**文件：**

- 新建：`src/homestay_bot/services/runtime_config_tester.py`。
- 新建：`src/homestay_bot/services/outbound_url_policy.py`。
- 修改：`integrations/hostex_client.py::HostexClient`，补充只读 probe 和可测试关闭状态。
- 修改：`integrations/wecom/api_client.py::WeComApiClient`，补充鉴权/客服列表 probe。
- 新建测试：`tests/unit/test_runtime_config_tester.py`、`tests/unit/test_outbound_url_policy.py`。

**设计复审补充（批次 5 执行前反向同步）：**

- 自定义 DeepSeek 公网 HTTPS 地址必须在实际连接时把全部 DNS A/AAAA 校验为公网，并把请求固定到已校验字面 IP；保留原域名 Host 与 TLS SNI/证书校验，禁代理、禁自动重试、禁任何重定向，避免 DNS rebinding/TOCTOU。
- DeepSeek 同时执行 OpenAI 客服与 Anthropic 旅游兼容端点的最短探针；使用受控客户端、严格超时、单连接和响应体硬上限，成功失败都关闭临时客户端。
- 企业微信分别验证客服 Secret、Agent Secret + AgentId 的只读 `agent/get`，可选 Contact Secret 也使用只读权限探针；任何发送、转人工、标签或订单写操作均禁止。
- 测试结果保存 provider 级安全聚合状态和稳定错误码；UI 明确模型探针会产生两次极小调用和供应商日志，企业微信回调仅本地校验、未验证真实投递。

- [x] 先写失败测试：DeepSeek/百居易/企业微信三项成功、单项失败、超时、可信 IP 错误映射、所有临时客户端关闭、结果无响应正文。
- [x] DeepSeek 对 OpenAI/Anthropic 两个生产兼容端点各发送最短测试请求；百居易只调用 `list_properties()`；企业微信只使用 token、客服列表、`agent/get` 和 Contact 只读权限探针。
- [x] 企业微信回调 Token/AESKey 只做本地长度与加解密格式检测，UI 明确“未验证真实回调投递”。
- [x] 企业微信和百居易根地址固定；DeepSeek 自定义地址只允许公网 HTTPS，拒绝 localhost、私网、链路本地、元数据和越权重定向。
- [x] 运行所有集成客户端与候选测试器测试，不启用真实 contract 环境变量。
- [x] 提交 `feat: validate runtime integrations before activation`。

**批次 5 Review：** `d84e113`、`96b5361`、`4560fb2`；全量 `724 passed, 15 skipped`，批次定向 `100 passed`，Ruff、mypy（95 个源码文件）、compileall、pip check 和 diff-check 通过；独立 Spec 与质量/安全复审均 APPROVED。回滚点为 `30dd45f`。

### 批次 6：客户端租约、原子热切换与后台循环动态化

**文件：**

- 新建：`src/homestay_bot/services/runtime_clients.py`。
- 修改：`src/homestay_bot/application.py::application_lifespan()`、`handle_message()`、`_run_wecom_poll_loop()`、`_run_hostex_reconcile_loop()` 和各 handler factory。
- 修改：`src/homestay_bot/routes/wecom_callback.py::get_callback_service()`。
- 修改：`src/homestay_bot/routes/hostex_webhook.py` 的服务获取路径。
- 修改：需要固定客户端的审批、凭证、标签同步服务装配，使其在执行入口获取当前租约。
- 新建测试：`tests/unit/test_runtime_client_registry.py`。
- 修改测试：`tests/integration/test_runtime_startup.py`、callback/webhook/worker 相关测试。

**关键接口：**

```python
@dataclass(frozen=True)
class RuntimeClientBundle:
    revision: int
    snapshot: RuntimeConfigSnapshot
    hostex: HostexClient
    wecom: WeComApiClient
    assistant: DeepSeekGuestAssistant

class RuntimeClientRegistry:
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[RuntimeClientBundle]
    async def swap(self, candidate: RuntimeClientBundle) -> None
```

- [x] 先写并发失败测试：swap 时旧租约不中断、旧 bundle 在最后租约释放后关闭、失败 candidate 不替换 active、关闭应用释放全部 bundle。
- [x] 所有新消息、新 job 和每轮 poll/reconcile 在入口 acquire 一次，单次业务处理中不跨 revision。
- [x] Bundle/运行时标量覆盖企业微信客服与通讯录客户端、DeepSeek 客服/FAQ/旅游/客诉/摘要能力、回调和百居易 webhook 校验器、Agent ID、值班员工及轮询/同步间隔；`public_base_url` 继续使用只读环境配置。
- [x] 生产 AsyncOpenAI 与 AsyncAnthropic 必须复用批次 5 的受控公网 HTTPS transport/client 构造器；不能只保护候选探针后又让正式客服重新 DNS 解析。
- [x] 数据库激活成功后 swap；若 swap 失败则恢复数据库指针并标记 activation_failed，旧 bundle 继续服务。
- [x] `activation_failed` 只标记本次新建且已测试通过的候选版本；回滚目标是既有已验证版本，回滚热切换失败时只恢复指针并写稳定审计，不得破坏目标版本的可再次回滚资格。
- [x] Worker 间隔在下一轮读取新快照；回调校验与 webhook 服务同样按当前 revision 获取依赖。
- [x] 扩展启动测试：数据库 active 优先、损坏密文回退环境并健康降级、应用退出无客户端泄漏。
- [x] 健康检查从 registry 获取无秘密动态 metadata；Contact 配置、轮询/同步阈值和修复后的健康恢复不得永久捕获启动 bundle。
- [x] repair-only 启动也必须保留可安全创建首个 bundle/registry/workers 的激活协调器；首次成功配置应在当前进程生效，不能静默要求重启。
- [x] 资源关闭失败只能在该失败资源被确认处理后恢复健康；无关 bundle 的成功关闭不得掩盖仍未释放的资源。
- [x] 租约释放、数据库补偿和退役资源关闭必须抵抗重复取消：清理任务受跟踪且真正完成后才恢复原取消语义，禁止泄漏 refcount 或留下数据库/内存不一致。
- [x] shutdown 与首次 repair 激活共用协调锁和 closing 状态；关闭开始后不得再发布 registry、服务或后台任务。
- [x] shutdown 必须先关闭配置激活入口并等待完整的候选测试、数据库激活/补偿和运行时发布操作退出，再清理 app state、客户端和数据库 engine；不能只等待内层 runtime 启动锁。
- [x] registry 永久拒绝重新发布已接管或已关闭的 bundle；并发/重复 `close()` 必须等待同一个关闭完成结果。
- [x] `swap()` 发布成功后立即返回，退役清理由 registry 强跟踪任务完成；发布后的清理取消/失败不得被上层误判为候选未发布并补偿数据库。
- [x] bundle 通过自身不可逆 claimed 状态拒绝重复所有权；registry 不得永久强引用所有已成功关闭的历史 bundle，以免内存和秘密对象生命周期无界增长。
- [x] 运行 runtime、worker、callback、webhook、消息流和完整 phase-one 测试。
- [x] 提交 `feat: hot-swap integration clients safely`。

**批次 6 Review：** `2a59526`、`20c1a28`、`17ab243`、`5bda063`、`c70578a`；全量 `770 passed, 15 skipped`，批次定向 `136 passed`，Ruff、mypy（97 个源码文件）、compileall、pip check、diff-check 与敏感日志扫描通过；独立 Spec 与最终质量/并发复审均 APPROVED。回滚点为 `4560fb2`。

### 批次 7：AI 调试台、系统诊断与操作记录

**文件：**

- 新建：`src/homestay_bot/services/admin_debug_service.py`。
- 新建：`src/homestay_bot/services/admin_diagnostics_service.py`。
- 新建：`src/homestay_bot/routes/admin_debug.py`。
- 新建：`src/homestay_bot/templates/admin/debug.html`、`admin/audit.html`。
- 修改：`src/homestay_bot/templates/admin/diagnostics.html`、`main.py`、`application.py::application_lifespan()`。
- 新建测试：`tests/unit/test_admin_debug_service.py`、`tests/unit/test_admin_diagnostics_service.py`、`tests/integration/test_admin_debug_routes.py`。

**关键接口：**

```python
class AdminDebugService:
    async def preview(self, command: DebugPreviewCommand) -> DebugPreviewResult

class AdminDiagnosticsService:
    async def snapshot(self) -> DiagnosticsSnapshot
```

- [x] 先写失败测试：调试结果包含意图/FAQ/工具/房间/日期/最终回复，但不写会话、不发微信、不建任务、不执行百居易写操作。
- [x] 用只读测试仓储和 no-op 出站端口构造调试上下文；限制频率、输入长度和日期范围。
- [x] 诊断页只展示组件状态、心跳、数量、版本和脱敏错误码；不展示原始响应、Query、UID、消息正文或密钥。
- [x] 操作记录按时间倒序分页，只展示安全 details；配置审计展示字段名称和版本，不展示密文摘要之外的数据。
- [x] “复制诊断报告”由服务端返回已脱敏 view model，前端不得自行过滤原始对象。
- [x] 运行 debug、diagnostics、audit、health 和日志脱敏测试。
- [x] 提交 `feat: add safe admin debugging tools`。

Review（批次 7）：调试台、诊断页与操作记录已交付，操作记录模板实际命名为 `admin/audits.html`（计划写作 `admin/audit.html`），另新增 `admin/config_versions.html` 承载配置版本审计。提交 `33d54a0`（功能）、`f489528`（元数据加固）、`178c361`（失败边界加固：`AdminDebugSafeRoute` 兜底普通异常为 no-store 503 且只记异常类型，并补生产 bundle→真实 assistant→service→SQLite 纵向只读链路测试）。定向组 50 passed；全量禁 live 794 passed、15 skipped；ruff、mypy（101 files）、compileall、pip check、diff check 全部通过。已验证撤回加固后两项 503 测试转红，确认测试真实驱动实现。未推送、未合并 `main`、未部署。

### 批次 8：全量验证、UI 验收与云端部署

进展记录（批次 8，本地只读部分）：

- 全量禁 live：794 passed、15 skipped（skip 全部为 DeepSeek/百居易/企业微信 contract，需显式开启）。
- 静态门禁：ruff 全通过；mypy 101 files 无问题；compileall、pip check、diff check 均通过。
- 迁移往返（临时 SQLite）：`upgrade head` → `downgrade 0014_query_indexes`（本任务基线）→ 再次 `upgrade head`，三步均干净，终态回到单头。
- PostgreSQL 离线 SQL：683 行生成成功，终态单头为 `0017_runtime_config_lifecycle`；无 AUTOINCREMENT 等 SQLite 专用语法残留；本任务 5 张表（`admin_credentials`、`admin_csrf_nonces`、`admin_csrf_quota`、`runtime_config_versions`、`runtime_config_state`）均已建。
  注意：计划此处写的单头 `0015_admin_runtime_config` 已过时——批次 3 追加了 `0016_admin_dashboard_indexes`、批次 6 追加了 `0017_runtime_config_lifecycle`。链条线性无分叉，属计划陈旧而非缺陷。
- 模板与交互自动化：`tests/integration` + `tests/browser` 共 282 passed；Playwright `test_admin_interactions.py` 3 passed，覆盖抽屉开关与 ESC、焦点管理、未保存离页提示；`test_admin_assets.py` 4 passed。
- 真实浏览器多分辨率验收：以真实路由、模板、CSS 和 `admin.js` 装配只读 stub 服务（不连数据库、不接外部 API）在 127.0.0.1 起本地实例，用 Chromium 遍历 375×812 / 768×1024 / 1024×768 / 1440×900 四档 × 总览/诊断/操作记录/调试台四页，共 16 张截图，证据留在仓库外的 `/tmp/yumi_ui_acceptance/shots`（未提交，且仅含 stub 房源名与空数据，无真实客户信息）。
  - 横向溢出：16 组全部 `scrollWidth == clientWidth`，无横向滚动。
  - 键盘焦点：首个 Tab 均落在"跳到主要内容"skip-link，具备 `solid 3px` 可见焦点环。
  - 抽屉：375 与 768 两档 × 四页共 8 组，打开后 `aria-expanded=true`、移除 `inert`、焦点进入抽屉内；ESC 后 `aria-expanded=false`、恢复 `inert` 与 `aria-hidden=true`、焦点回到触发按钮。
  - 无 JS：`java_script_enabled=False` 下登录表单与提交按钮仍然存在可用。
  - 已知遗留（未修）：总览"查看任务/查看审批"、诊断"系统诊断"、操作记录"返回系统诊断/下一页"等裸文字链接高度仅 18–25px，低于计划的 44px 点击区域要求。这些是 `<a>` 而非 `.button`，未命中 `app.css` 的 `min-height: 44px` 规则。实测与最近可点目标间距 21–46px，留白充足不易误触，故判为轻微可访问性缺口而非阻塞项；修复面很小（给行内链接补命中区的若干 CSS 规则，无需改模板），是否修复待用户决定。
  - 说明：验收脚本首轮因先按 Tab 使 skip-link 获得焦点而遮挡抽屉触发按钮，已确认未聚焦时其 `bottom` 为 −19px 完全移出视口、汉堡按钮命中测试正常，属脚本顺序问题而非缺陷，已在脚本中于抽屉检查前清除焦点。
- [x] 使用 `requesting-code-review` 做 Spec 一致性和安全复审，修正后重复定向及全量验证。
- [x] 提交并推送功能分支，并合并到 `main`。
- [x] 云端先备份 PostgreSQL 和运行配置，再拉取主干、构建镜像、运行迁移、启动服务。
- [ ] 公网验收：未登录跳转、错误密码拒绝、首次改密、总览、业务编辑、候选配置失败保旧值、成功热切换、回滚、AI 调试无副作用、HTTPS 和健康检查。
- [ ] 在本节末尾添加 Review：提交号、测试数量、跳过项、云端容器状态、证书状态、残余风险和可回滚点。

### 批次 8 安全复审与修复（2026-08-12）

复审范围 `565b6a8..dc4bef1`，71 个文件、约 9750 行新增。发现四项问题并全部按 TDD 修复，提交 `cad4a95`，分支 `fix/admin-console-hardening-20260812` 已推送 `origin`，尚未合并 `main`。

#### 记录校正

- 批次 8 的"提交并推送功能分支…合并 `main`"此前未勾，但 `dc4bef1` 早已合并 `feature/yumi-admin-console` 并推送 `origin/main`；三个 worktree 分支（`yumi-admin-console`、`complaint-quality`、`continue-verify-20260731`）均已是 `main` 祖先，无遗留未合并分支。
- 本机运行副本仍是 8 月初版本（`~/Library/Application Support/HomestayBot/src` 停在 8 月 1 日），后台管理台连本机都尚未部署；LaunchAgent 在跑，`http://127.0.0.1:8010/health` 返回 `{"status":"ok"}`。
- 2026-08-12 复审时仓库内没有可验证的云端配置；2026-08-13 已通过现有 SSH 入口确认并升级京东云服务器，最新证据见下方“批次 8 云端部署”。

#### 修复一：匿名 CSRF nonce 作用域可被耗尽（高）

- 现象：`services/admin_csrf.py` 的 `max_active_per_scope=8`，作用域为 `(purpose, admin_id)`；登录令牌匿名签发，`admin_id=None`，全部访客共用一个作用域。8 次未认证 `GET /employee/login` 即可占满，真实管理员用干净浏览器打开登录页时 `_issue_csrf` 抛 `AdminCsrfCapacityError` 转 HTTP 429。TTL 15 分钟且 `_delete_expired` 只清过期记录，窗口内无法自愈，每 15 分钟重复即可无限延长。
- 根因：设计意图是"每个管理员 8 个多标签页"，匿名场景没有管理员可绑定，per-admin 上限退化为全局上限。
- 修复：`routes/employee_auth.py` 新增 `_scoped_purpose()`，已登录用途仍按 `admin_id` 隔离，匿名用途绑定会话内随机作用域标识（`CSRF_SCOPE_SESSION_KEY`）；消费时作用域缺失一律 409，令牌不能跨浏览器使用。另在 `services/admin_csrf.py` 与 `repositories/admin_csrf.py` 增加匿名独立子上限（默认 200/1000），防止反复更换会话耗尽管理员写操作所需的全局容量；子上限取 `min(子上限, 全局上限)`，避免小容量部署失去子池语义。

#### 修复二：登录限速两类共用全局桶（中）

- 现象：`AdminLoginRateLimiter.__init__` 的 `per_ip_limit` 默认写成 `LOGIN_RATE_GLOBAL`（60）而非 `LOGIN_RATE_PER_IP`（10）；生产以默认参数装配（`application.py:2504`），GET 登录页按 60/分钟/IP 放行。更根本的是登录页与凭据提交共用一个 60/分钟全局桶，单 IP 刷登录页即可挤掉其他来源的登录 POST。
- 修复：纠正默认值；把全局计数按类别拆分，页面浏览与凭据提交各自独立（页面 30/IP、120 全局；凭据 10/IP、60 全局）。GET 传 `category="page"`，POST 传 `category="login"`。
- 残留：限速仍是单进程内存态，分布式 IP 打满某一类别的全局上限仍会影响该类别；彻底解决需共享计数存储，本轮未做。

#### 修复三：后台缓存边界缺失（低）

- 现象：只有 `runtime_config`、`admin`、`admin_debug`、`properties`、`private_files` 设 `Cache-Control: no-store`，且无全局中间件；缺失的是 `employee_auth`（登录、账号、改密）、`tasks`、`customers`、`knowledge`、`complaints`、`approvals`。其中 `customers` 渲染客人档案、`complaints` 渲染客诉对话正文。
- 修复：新增 `src/homestay_bot/middleware.py` 的 `AdminNoStoreMiddleware`，对 `/employee` 前缀全部响应补 `no-store` 与 `nosniff`（已有更严格声明时不覆盖）。采用纯 ASGI 而非 `BaseHTTPMiddleware`，以免包装 `private_files` 的流式 `FileResponse`；`/static` 与公开 `/health` 不受影响。

#### 修复四：客诉路由缺角色校验（低）

- 现象：`routes/complaints.py` 详情页取了 `role` 却从不判断，`_action`（保存/发送/退回/关闭）直接以 `_` 丢弃。
- 可达性：当前不可达。唯一会话写入点是 `routes/employee_auth.py:456`，只认 `AdminCredential`；`_upsert_local_admin_employee` 强制 `role=ADMIN`，`_has_valid_existing_admin` 每次启动复核。OAuth 入口已彻底移除。
- 历史：`565b6a8` 之前即如此，非批次 1–7 引入。
- 修复：新增 `_require_admin()`，详情页与四个动作入口统一要求管理员，不再依赖"普通员工无法登录"这一外部前提。

#### 附带修复

- `logging.py` 的 `_SENSITIVE_KEY_PATTERN` 补 `aes[_-]?key`，覆盖企业微信 `EncodingAESKey`；未放宽到裸 `key`，新增测试锁死 `dedupe_key` 不被误伤。无已确认泄露路径，属纵深防御。
- `.env.example` 补齐后台登录必需的 `CONFIG_ENCRYPTION_KEY`、`ADMIN_BOOTSTRAP_USERNAME`、`ADMIN_BOOTSTRAP_PASSWORD_HASH`，并附生成命令；其中 Argon2 命令实测可产出通过 `validate_admin_password_hash()` 的哈希。缺这三项时服务不崩，但 `admin_auth_available=False`（登录不可用）且设置页降级只读。

#### 调整的既有测试

- `test_concurrent_login_posts_can_only_consume_same_nonce_once` 原用无效 Cookie 配真实 token 断言 `[303, 409]`，固化的正是"A 浏览器令牌 B 浏览器可用"。拆为两条：新增 `test_login_nonce_is_rejected_without_the_issuing_browser_session` 断言跨浏览器一律 `[409, 409]`；原测试改为复用签发会话 Cookie，继续断言 `[303, 409]` 保住"只能消费一次"。
- `test_anonymous_login_get_is_rate_limited_by_real_client_ip` 因 GET 改走页面类别，构造参数改为 `page_per_ip_limit` / `page_global_limit`；测试意图（伪造 XFF 不能绕过真实来源 IP）不变。

#### 复审确认无问题的部分

- SSRF 与 DNS 重绑定：全部 A/AAAA 要求公网、固定字面 IP 连接、保留 SNI 与证书校验、拒重定向、`Content-Length` 与流式双重限流、`trust_env=False` 挡代理。逐个验证 IPv4-mapped、NAT64、6to4、元数据地址、链路本地均正确拒绝。生产 `AsyncOpenAI` 与 `AsyncAnthropic` 确实复用 `build_public_https_client`；两个 SDK 均无查询串，不触发策略的 query 拒绝分支。
- Argon2 校验与锁定的 CAS 原子性、未知用户名的虚拟哈希时序对齐、统一错误文案。
- nonce 只存 SHA-256、单条 `DELETE RETURNING` 原子消费、用途与管理员绑定。
- 配置整份 Fernet 加密；`masked_view()` 只露末四位；`RuntimeConfigSnapshot.__repr__` 实测输出 `values=<redacted>`。
- 热切换的租约计数、退役强跟踪、bundle 关闭幂等、取消安全清理。
- 健康检查公私分层：`/health` 只回总体状态，`/employee/health` 需管理员。
- 模板无 `|safe`、`admin.js` 无 `innerHTML` / `eval`，autoescape 完整。
- 私有文件 `file_id` 正则加 resolved-parent 双重校验，读取前先过数据库授权。
- 候选探针全部只读（`chat.completions.create` / `messages.create` 最短请求、百居易 `list_properties`、企业微信 token/kf/`agent/get`/contact 只读权限）。
- 曾怀疑 `_safe_next` 的反斜杠开放重定向（确实放行 `/\evil.example`），实测不成立：Starlette 将其编码为 `/%5Cevil.example`，浏览器按路径处理，仍同源。

#### 验证证据

- 全量 `pytest`：**815 passed、15 skipped**（修复前基线 794 passed、15 skipped，净增 21 项）。15 项 skip 全为需显式开启的 DeepSeek / 百居易 / 企业微信真实契约，未计为通过。
- `ruff check .` 通过；`mypy src/homestay_bot` 102 源文件无问题（新增 `middleware.py`）；`compileall`、`pip check`、`git diff --check` 全部通过。
- 按手册第 9 条验证测试真实驱动实现：把 `_scoped_purpose` 的管理员分支短路为恒真后，作用域隔离测试立即转红，还原后转绿。
- 原始两条 PoC 复验：第 9 个浏览器可正常取得登录令牌；页面类别占满 120 次后管理员凭据提交仍放行。
- 未改动数据模型与迁移，无新 schema 变更，迁移链条保持单头 `0017_runtime_config_lifecycle`。

#### 下一步

1. ~~合并 `fix/admin-console-hardening-20260812` 到 `main`（待确认）~~ — 已完成，提交 `baa4903`。
2. ~~本机部署验证~~ — 已完成，见下方部署记录。
3. ~~云端部署~~ 已完成；公网验收仍待企业微信把固定公网 IP 加入两处可信 IP，并完成管理员登录后的写操作验收。
4. 遗留未修（用户已决定不管）：总览与诊断页裸文字链接命中区 18–25px，低于 44px 要求。

### 批次 8 本机部署（2026-08-12）

- 合并提交：`baa4903`，已推送 `origin/main`。
- 备份路径：`~/Library/Application Support/HomestayBot-backup-20260812-025712`（含旧数据库 960KB、`.env`、`data/private_uploads/`）。
- 迁移执行：`0014_query_indexes` → `0015_admin_runtime_config` → `0016_admin_dashboard_indexes` → `0017_runtime_config_lifecycle`，验证头正确。
- 代码同步：rsync 7.7MB，关键文件哈希与工作区 `baa4903` 完全一致。
- 配置补充：运行目录 `.env` 新增 `CONFIG_ENCRYPTION_KEY`、`ADMIN_BOOTSTRAP_USERNAME=admin`、`ADMIN_BOOTSTRAP_PASSWORD_HASH`（Argon2id）。首次登录凭据已在本机交付并完成强制修改；任务记录和 Git 中不得保存明文密码。
- LaunchAgent 修复：`start.sh` 改用 `python -m uvicorn` 绕过外部卷权限问题；`.env` 的 `ADMIN_BOOTSTRAP_PASSWORD_HASH` 改用单引号避免 shell 变量展开；删除运行目录的 `.venv`，直接复用工作区 venv（系统 Python 3.9.6 不满足 >=3.12 要求）。
- 验证结果：
  - 健康检查：`http://127.0.0.1:8010/health` 返回 `{"status":"ok"}`。
  - 后台登录页：`http://127.0.0.1:8010/employee/login` 可访问，标题 "管理员登录 · YuMi"。
  - 权限校验：`/employee/health` 正确返回 `{"detail":"管理员尚未登录"}`，未登录不可访问。
  - LaunchAgent：PID 87393，状态码 0，`KeepAlive` 生效。
- 残留：批次 8 的四项修复（匿名 CSRF 作用域隔离、限速分类、后台 no-store、客诉角色校验）均已部署生效，本机验证通过；云端部署与公网 11 项验收待进行。

### 批次 8 云端部署（2026-08-13）

- 服务器：京东云 Ubuntu 24.04，固定公网 IP `117.72.14.15`；域名 `akros.icu`，Nginx 配置检查通过，Let's Encrypt 证书有效至 2026-11-08。
- 部署前状态：代码停在 `b4eb9c2`，数据库停在 `0014_query_indexes`，旧 OAuth 登录仍启用；PostgreSQL 与 API 容器运行但健康检查为 degraded。
- 可恢复备份：`/opt/yumi-backups/20260813T110757Z`，包含已通过 `pg_restore -l` 校验的 PostgreSQL custom dump、权限为 600 的 `.env`、私有上传目录归档、部署前 Git HEAD 与 Compose 配置。
- 代码同步：云服务器访问 GitHub 超时，改用本机 `git bundle` 经 SSH 传输并执行 fast-forward；云端最终 HEAD 为 `051c27c`，与 `origin/main` 一致。
- 配置迁移：从本机安全复用已经强制修改后的管理员 Argon2 哈希及独立 `CONFIG_ENCRYPTION_KEY`，未生成、打印或提交明文密码；云端管理员首次登录仍要求强制改密。
- 数据库迁移：容器启动日志确认依次执行 `0014` → `0015` → `0016` → `0017_runtime_config_lifecycle`；迁移后 API 与 PostgreSQL 容器正常运行。
- 网络加固：新增提交 `051c27c`，把 API 端口从所有网卡收紧为 `127.0.0.1:8000`；新增回归测试，完整禁 live 测试为 816 passed、15 skipped。公网无法绕过 Nginx 直连 8000。
- 公网只读验收：`https://akros.icu/health` 返回 HTTP 200；后台登录页返回 HTTP 200、标题“管理员登录 · YuMi”、`Cache-Control: no-store`；未登录总览返回 303 到站内登录页；HTTPS 主机名与证书链正确。
- 外部连接测试：DeepSeek OpenAI/Anthropic 两项通过，百居易 properties 只读探针通过，企业微信回调 AES/签名本地自检通过。首次测试时企业微信 KF Secret 与 Agent Secret 均返回 `60020`；加入固定公网 IP `117.72.14.15` 后已恢复。
- 企业微信可信 IP 复验：KF 账号列表、AgentId、`sync_msg` 补拉全部通过；重启清除旧退避后跨过首个 60 秒周期，补拉错误日志为 0，完整 RuntimeConfigTester 三方结果全部成功，公网 `/health` 持续 HTTP 200。
- 剩余验收：发送一条真实企业微信客人消息，验证“企业微信 → 云端 → AI/数据库 → 自动回复”；随后使用管理员账号完成配置失败保旧值、成功热切换、回滚和 AI 调试无副作用的登录态验收。

### 云端真实消息与访问日志回归（2026-08-13）

- [x] 核对真实企业微信消息、机器人回复、出站任务及端到端耗时。
- [x] 使用百居易只读 `/properties` 与 `/availabilities` 复核回复中的库存事实。
- [x] 为 Uvicorn 结构化访问日志补充稳定复现测试，确认脱敏过滤器不会破坏五元参数。
- [x] 最小修复访问日志脱敏，同时保持敏感查询参数不进入日志。
- [x] 运行定向测试、全量测试、Ruff、mypy，并在云端复验异常 URL 不再触发 `Logging error`。

#### Review

- 真实企业微信消息“今天入住明天退房，还有空房吗”已完成接收、DeepSeek/百居易处理和企业微信发送；数据库出站任务一次完成，从收到到发送约 9 秒。
- 百居易只读复核返回 7 间房在 2026-08-13 与 2026-08-14 均不可用，客人回复中的满房结论有实时库存依据。
- Uvicorn 日志根因测试先稳定复现 `ValueError: not enough values to unpack`，修复后定向 9 项通过；禁 live 全量回归为 817 passed、15 skipped，Ruff、mypy、compileall、pip check 与 diff check 均通过。
- 修复已部署到云端提交 `204679c`；原异常 URL 返回 404，日志中的测试 token 已脱敏，近 3 分钟 `Logging error=0`，公网与本机健康检查均为 `ok`，API 仍仅监听 `127.0.0.1:8000`。

### 武汉旅游回复降级修复（2026-08-13）

- [x] 核对真实消息、机器人兜底回复、内部提醒和出站任务。
- [x] 使用 DeepSeek Anthropic 真实请求复现：搜索证据存在，但该轮间歇性没有最终正文。
- [x] 保留深度思考，重新定位搜索有证据但无正文的真实原因并补回归测试。
- [x] 对比直接客户端与生产受控传输，复现生产 5 秒读取超时并隔离候选/运行时超时。
- [x] 运行定向、全量与静态验证并部署云端。
- [x] 使用真实旅游搜索验证有正文、有来源、无链接，再请用户复测。

#### Review

- 真实请求确认深度思考可正常产出正文；撤销“关闭思考”的错误方向。再次复测后确认主要生产根因是受控 HTTPS 客户端统一使用 5 秒读取超时，而网页搜索通常需要约 20–30 秒。
- 请求继续保留深度思考，并增强“结束前必须输出最终正文”约束；仅在已有搜索证据且正文为空时有限重试一次，没有证据仍直接降级。
- 生产 OpenAI/Anthropic 客户端使用 45 秒有界超时；候选配置探针继续保持默认 5 秒，SSRF、固定公网 IP、禁重定向、响应体上限和零重试边界均不变。
- 定向旅游/客服/会话回归 91 passed；禁 live 全量 818 passed、15 skipped，Ruff、mypy、compileall、pip check 与 diff check 均通过。
- 修复已部署到云端提交 `2b57502`；部署后的真实搜索保持深度思考，返回 1224 字正文，包含来源标识且无链接，公网健康检查为 `ok`。
- 生产 5 秒超时修复已部署到 `d94ef9d`；使用 worker 同款受控 HTTPS 传输与 45 秒超时真实验证，返回 1009 字正文、包含来源标识且无链接，服务健康为 `ok`。

### 旅游问题分级与 10 秒补拉（2026-08-13）

#### 已确认 Spec

- 采用确定性本地路由，不增加额外模型分类调用。
- 稳定旅游问题（经典景点、美食、普通推荐）进入审核知识库与快速模型，不联网、不启用深度思考。
- 时效旅游问题（活动、演出、展览、天气、票价、营业/开放时间、实时交通、精确路线与距离）进入联网搜索并保留深度思考。
- 单独出现“最近/近期 + 玩什么”但没有活动、天气、票价、时间或路线语义时，按稳定推荐快速回答，不冒充实时活动信息。
- 房态、房价继续调用百居易只读工具并使用快速模型；客诉等既有独立流程不在本批改动。
- 企业微信补拉从 60 秒改为 10 秒；正常接收等待目标为 0–10 秒，普通问题总体目标为 5–15 秒。
- 没有审核知识时允许谨慎回答稳定常识并标记知识缺口；时效问题无搜索证据时继续安全降级，禁止编造。

#### 实施计划

- [x] 在 `tests/unit/test_tourism.py` 先写分类 RED：稳定推荐、模糊“最近玩啥”、时效活动/天气/票价/时间/路线、预订优先。
- [x] 在 `src/homestay_bot/integrations/tourism.py` 新增三态分类函数（非旅游/稳定旅游/实时旅游），保留 `is_tourism_query()` 兼容现有调用。
- [x] 在 `tests/unit/test_deepseek_client.py` 先写路由 RED：稳定旅游走知识库快速模型且 `thinking=disabled`，实时旅游走联网搜索且不关闭思考。
- [x] 修改 `src/homestay_bot/integrations/deepseek_client.py::respond()` 与系统提示词，仅实时旅游提前进入 `DeepSeekTourismSearcher`。
- [x] 运行旅游、DeepSeek、会话定向测试，确认 RED→GREEN（114 passed）。
- [x] 备份云端 `.env`，把 `WECOM_POLL_INTERVAL_SECONDS=60` 精确改为 `10`，重启 API 后跨过至少两个补拉周期并检查错误日志。
- [x] 运行禁 live 全量 pytest、Ruff、mypy、compileall、pip check 与 diff check（843 passed、15 skipped；其余全部通过）。
- [x] 提交、推送、部署，分别用稳定推荐和实时旅游做真实验收，检查内容、来源、链接、重复回复与端到端耗时。

#### 实施 Review

- 三态分类保持民宿预订优先；门票/演出票务预订走实时搜索，明确房间、房源、房型、酒店或住宿对象的预订不会被旅游规则抢走。
- 稳定旅游由快速模型处理并关闭深度思考；实时旅游继续使用联网搜索和深度思考。
- 独立代码复审两轮发现并修复票务预订与自然表达覆盖、住宿对象组合句误判，最终结论 APPROVED。
- 云端部署提交 `8dff796`；`.env` 已备份到权限为 600 的独立目录，容器实际读取 `WECOM_POLL_INTERVAL_SECONDS=10`，跨过多个周期无错误日志，内外网健康均为 `ok`。
- 生产受控客户端真实验收：稳定推荐约 3.03 秒、无来源和链接；实时活动约 23.34 秒、联网状态 `ok`、有来源且无链接。端到端企业微信接收耗时仍需用下一条真实客人消息测量。

### 房态住宿晚归一化与跨话题隔离（2026-08-13）

#### 已确认 Spec

- 百居易返回的日期范围可能同时包含入住日和退房日；可住判断采用酒店夜晚语义 `[入住日, 退房日)`，退房日绝不计入本次住宿晚。
- `HostexReadOnlyToolExecutor.execute("search_availability")` 在交给模型前只保留实际住宿晚，并为每个房源提供确定性的整段可住结果；任一住宿晚不可用即不可住。
- 当前问题同时包含房态意图与明确日期时，视为独立房态问题，只把当前客人问题交给模型，避免上一轮旅游回复污染；缺少日期的简短追问继续保留最多三条上下文，以继承上一轮住宿日期。
- 不改变百居易 API、不自动建单、不修改订单，不影响旅游、客诉和任务流程。
- 首次模型 JSON 校验失败的既有有限重试保留，本批不扩大模型重试策略。

#### 实施计划

- [x] 在 `tests/unit/test_deepseek_client.py` 写 RED：8 月 14 日入住、15 日退房时过滤 15 日，只按 14 日计算 `stay_available`。
- [x] 在 `src/homestay_bot/integrations/deepseek_client.py::HostexReadOnlyToolExecutor.execute()` 实现退房日排除和整段可住投影。
- [x] 在 `tests/unit/test_deepseek_client.py` 写 RED：独立房态问题不携带上一轮旅游回复；缺日期的追问仍保留相关历史。
- [x] 在 `DeepSeekGuestAssistant.respond()` 构造请求前裁剪独立房态问题上下文，并保持工具强制选择与日期换算行为不变。
- [x] 运行房态、DeepSeek、ConversationService 定向测试，确认 RED→GREEN（101 passed）。
- [x] 运行禁 live 全量 pytest、Ruff、mypy、compileall、pip check、diff check并完成独立代码复审（851 passed、15 skipped；复审 APPROVED）。
- [x] 合并并推送 `main`，部署云端；用百居易真实 8 月 14–15 日数据确认结果为 0 间，不附带旅游内容，检查健康和错误日志。

#### 实施 Review

- 工具层以 `[check_in, check_out)` 生成住宿晚并输出 `stay_available`；缺少任一住宿晚也视为不可住，模型提示明确禁止用退房日或参考价推断。
- 独立房态问题同时清空消息历史与客户摘要；带“那/改到/换到”等承接语气的追问保留历史并继续强制房态工具。
- 日期识别覆盖相对日期、`M月D日/号`、`M/D`、完整 ISO 日期与本周星期表达。
- 独立审查指出并修复两项上下文边界及原测试盲区，最终结论 APPROVED。
- 主干修复提交 `efb47f9` 已推送并部署；云端更新前备份位于 `/opt/yumi-backups/availability-20260813T1502Z`，包含 PostgreSQL、`.env` 和旧提交号。
- 生产百居易只读验收返回 7 个房源、整段可住 0 间，`days` 只含 8 月 14 日而不含退房日 15 日；真实 DeepSeek 模拟回复明确 7 间均满房，未夹带东湖、黄鹤楼或“两间可住”等矛盾内容，且未发送企业微信消息、未创建或修改订单。
- 部署后运行配置保持 revision 1 / `TEST_PASSED`，API 与 PostgreSQL 容器正常，内外网 `/health` 均为 `ok`，近 10 分钟错误筛查无 `ERROR`、Traceback、ValidationError 或补拉/同步失败。

### 批次 1-7 安全复审（2026-08-12）

复审范围 `565b6a8..dc4bef1`，31 个提交、118 文件、净增约 18,500 行。发现 7 个问题（6 medium、1 low），无高危漏洞。核心安全机制（认证、CSRF、权限、加密、SSRF、热切换、模板安全）验证通过。

#### 发现问题（仅记录，待后续批次修复）

**Medium — 响应头遗漏（6 项）**

1. **客户管理页面缺 no-store**（批次 3）
   - 文件：`src/homestay_bot/routes/customers.py`，行 169-207
   - 影响：客户档案列表（脱敏电话）、详情页（标签/备注/AI 摘要）、合并预览，3 个 GET 端点返回 HTMLResponse 但未设 `Cache-Control: no-store`
   - 风险：浏览器或代理可能缓存敏感客户信息

2. **客诉详情页缺 no-store**（批次 3）
   - 文件：`src/homestay_bot/routes/complaints.py`，行 67-89
   - 影响：客诉详情页包含客户对话正文和可编辑回复草稿
   - 风险：高敏感内容可能进入浏览器历史记录或代理缓存

3. **预订审批页面缺 no-store**（批次 3）
   - 文件：`src/homestay_bot/routes/approvals.py`，行 47-96
   - 影响：审批列表和详情页包含客户信息、预订金额和下单决策
   - 风险：敏感预订信息可能被缓存

4. **任务详情页缺 no-store**（批次 3）
   - 文件：`src/homestay_bot/routes/tasks.py`，行 158-222
   - 影响：任务列表和详情页包含房源信息、服务日期和现场照片
   - 风险：任务信息可能被缓存

5. **房源管理页面缺 no-store**（批次 4）
   - 文件：`src/homestay_bot/routes/properties.py`，行 127-170
   - 影响：房源列表和详情页包含凭证版本信息和配置数据
   - 风险：虽不回显明文密码，但版本信息和配置属于敏感运营信息

6. **知识管理页面缺 no-store**（批次 7）
   - 文件：`src/homestay_bot/routes/knowledge.py`，行 356-419
   - 影响：知识库列表（含 FAQ 候选草稿）和详情页
   - 风险：虽然知识内容公开给客户，但候选草稿和编辑状态属于内部运营信息

**Low — 防御深度不足（1 项）**

7. **知识库列表页未在路由层强制管理员角色**（批次 7）
   - 文件：`src/homestay_bot/routes/knowledge.py`，行 365-373
   - 现状：`knowledge_index` 使用 `require_employee_session` 而非 `_require_admin`，理论上普通员工可访问；当前代码按角色过滤候选列表（只有 ADMIN 时才加载），但路由本身未强制管理员角色
   - 风险：如果未来模板或代码变更，可能意外泄露候选给普通员工
   - 建议：路由层面强制只有管理员可访问 `GET /employee/knowledge`

#### 验证通过的核心机制

- **认证与会话**（批次 1-2）：Argon2 校验、锁定逻辑原子性、会话令牌熵与绑定、密码修改 CAS 更新正确
- **CSRF 防护**（批次 2）：nonce 原子消费、作用域隔离（批次 8 已修复匿名作用域问题）
- **权限校验**（批次 3-7）：所有写操作（complaints、customers、properties、tasks、knowledge、runtime_config、admin_debug）已显式检查 `EmployeeRole.ADMIN`
- **加密配置**（批次 4）：Fernet 覆盖完整、密钥派生隔离、`masked_view()` 无泄露、日志与 `__repr__` 脱敏
- **外联探测**（批次 5）：SSRF 防护完整（公网地址校验、DNS 重绑定、重定向拒绝）、候选探测只读无副作用
- **热切换**（批次 6）：租约计数隔离、激活原子性、退役跟踪、关闭幂等、取消安全
- **AI 调试与诊断**（批次 7）：调试工具无副作用、诊断页脱敏、操作记录完整
- **模板安全**：无 `|safe` / `|raw` / `innerHTML` / `eval`，autoescape 完整

#### 说明

- 响应头遗漏问题与批次 8 发现的一致（批次 8 已用中间件统一覆盖 `/employee` 前缀，这 6 项也同时修复）。本次复审单独记录以备将来回溯。
- 知识库授权问题当前不可达（只有管理员能登录），但建议在下一轮修复时一并加固。

### 客人回复零承诺与统一管家接管（2026-08-13）

#### 已确认 Spec

- 机器人不得向客人承诺任何尚未由人工确认的结果，包括“已安排”“马上安排师傅”“师傅会上门”“一定解决”“彻底解决”“保证”“马上送到”“会尽快处理好”等直接或变体表达。
- 凡退款、投诉、人工请求、媒体消息、模型/联网失败、维修、补给、保洁、特殊服务以及其他需要员工确认或形成待确认任务的场景，必须先温暖安抚，再明确表达：`我会立即联系管家来处理，请您稍等。`
- 可保留“长按童锁键”等低风险自助建议，但不得声称建议一定有效；紧急事件继续优先显示撤离、断电、拨打 119 等安全指令，随后使用同一管家联系表述，不得声称值班人员已经收到或一定会联系。
- 普通信息、房态和旅游等无需人工的问题不强行追加管家话术，但仍经过全局承诺检测，禁止模型夹带服务结果承诺。
- 企业微信内部员工通知、任务状态和真实人工发送内容不受客人侧措辞过滤影响。

#### 实施计划

- [x] 新建 `src/homestay_bot/services/guest_reply_policy.py`，集中定义中英文管家接管话术、禁用承诺模式、按句保留安全建议和最终兜底；所有函数与关键分支写中文注释。
- [x] 先在 `tests/unit/test_guest_reply_policy.py` 写 RED，覆盖洗衣机原始违规回复、全部违规句、普通信息误带承诺、固定管家话术只出现一次和中英文结果。
- [x] 修改 `src/homestay_bot/services/conversation_service.py::_process_model_reply()`，在发送前根据本地接管理由、模型接管理由、`staff_confirmation_required`、`task_suggestion` 和本地服务意图决定是否追加固定管家收尾；`_send_guest_reply()` 保留最终全局防线。
- [x] 修改 `ConversationService` 的快速安抚、普通人工接管、旅游/模型失败和紧急回复路径，确保所有需要人工的客人回复使用统一策略，同时保留紧急安全指令。
- [x] 修改 `src/homestay_bot/integrations/deepseek_client.py::respond_ack()` 提示与固定兜底，删除“一定会解决”等承诺；输出即使违规也由客人侧中央策略兜底。
- [x] 修改 `src/homestay_bot/services/complaint_service.py::guest_acknowledgement()` 和 `src/homestay_bot/services/emergency_service.py::safety_reply()`，移除结果承诺和“已通知”事实声明。
- [x] 扩充 `tests/unit/test_conversation_service.py`、`test_deepseek_client.py`、`test_complaint_service.py`、`test_emergency_service.py`，先观察原行为 RED，再验证所有客人可见路径 GREEN；断言员工内部通知仍正常产生。
- [x] 运行相关定向测试、禁 live 全量 pytest、Ruff、mypy、compileall、pip check、diff check，并进行独立代码复审。
- [ ] 合并推送主干，云端先备份再部署；重放“洗衣机显示锁/打不开怎么办”的无消息发送模拟，随后用新企业微信消息验收回复无承诺、包含固定管家收尾、员工通知正常、健康与错误日志正常。

#### 实施 Review

- 本地 RED 首先因中央策略模块缺失而收集失败；随后独立复审补充复现普通信息误判、六种中英文调度承诺绕过、安全指令误删和 `booking_confirmed` 漏接管，均先加入失败测试再修复。
- 本地 GREEN：客人回复策略及相关服务定向 `128 passed`；禁 live 全量 `878 passed, 15 skipped`；Ruff、mypy（103 个源码文件）、compileall、pip check 和 diff check 全部通过。
- 独立只读复审最终 `APPROVED`：普通信息不误触发、真实服务仍触发、承诺变体被过滤、燃气/安全区域提示保留、`booking_confirmed` 统一接管；员工通知仍走独立内部通道。
- 功能提交 `1fcc852` 已快进合并并推送 `origin/main`；云端升级前已备份旧提交、`.env` 和 PostgreSQL 到 `/opt/yumi-backups/20260814-000835`，数据库备份已验证非空。
- 云端 API 已重建并运行 `1fcc852`，PostgreSQL 保持健康，迁移为 `0017_runtime_config_lifecycle (head)`；本机和 `https://akros.icu/health` 均返回 `ok`，部署后近五分钟错误计数为 0。
- 云端无消息发送重放原洗衣机违规回复，结果为“您可以先长按童锁键三秒试试看；我会立即联系管家来处理，请您稍等。”；安全建议保留，师傅、上门、彻底解决、保证和一定等承诺均不存在。
- 仍待一条部署后的真实企业微信消息核对客人收到文本、员工通知和任务记录；完成后勾选部署验收总项。

### CRM 自动最新入住备注（2026-08-14）

#### 已确认 Spec

##### 数据模型与显示规则

- 新增独立只读的“最新入住备注”，不得覆盖或拼接现有 `Customer.note` 员工备注；员工备注继续允许管理员单独编辑和清空。
- 每笔本地 `StayOrder` 新增可空的“首次观察到已退房状态的武汉日期”；只在百居易订单状态首次进入 `checked_out/completed` 时写入，重复同步不得改写。
- CRM 派生备注格式固定为 `M.D-M.D房间名`，例如 `8.14-8.16春和景明`，月日不补前导零。
- 有效且当前正在入住的订单优先；异常重叠时按入住日期更晚、订单 ID 更大稳定选择。
- 已退房订单从退房观察日当天起保留至后续第 3 天；第 4 天起，如有未来有效订单则显示入住日期最近的一笔。
- 第 4 天起没有未来订单时，继续保留最近一次有效入住；取消、拒绝、过期或删除订单完全排除。
- 客户档案合并后，现有订单归属迁移完成即按目标客户全部订单重新计算；订单改期、取消和房源改名后也实时重新计算。

##### 同步、异常与权限

- 百居易 Webhook 与定时对账统一复用 `SQLAlchemyOperationsRepository.upsert_reservation()`：有效订单参与计算；`cancelled/canceled/declined/expired/deleted` 排除；`checked_out/completed` 首次写观察日期。
- 订单从已退房状态恢复为有效状态时清除观察日期；以后再次进入已退房状态可重新记录。
- 自动备注只查询本地已同步订单，不在 CRM 页面请求百居易；缺房源名称或本地仍为 `百居易房间 ID` 占位名时显示 `房间 #ID`。
- 入住或退房日期异常时排除该订单并记录稳定错误码，不得让 CRM 页面报错；无有效订单显示“暂无入住记录”。
- 客户列表增加“最新入住备注”列和移动端字段，详情页在员工备注上方显示同一只读值；本期不增加按自动备注搜索，也不向企业微信同步该备注。
- 所有选择和三天窗口均使用武汉自然日；只有管理员可以查看 CRM，派生备注不得包含手机号、订单编号、门锁、消息正文等敏感信息。
- 自动备注是实时派生值，不为每次页面读取新增审计；员工备注修改继续使用既有不含正文的审计。

##### 迁移、性能与验收

- 新增迁移 `0018`，为 `stay_orders` 增加可空日期字段 `checkout_observed_on`；迁移时已有 `checked_out/completed` 订单按计划退房日期回填，其他状态保持为空。
- 客户列表禁止逐客户查订单：先分页查询客户，再以一次批量查询取得本页客户的订单和房源名称，由同一纯计算器生成映射；详情页复用该计算器查询单个客户。
- 计算器的武汉日期可注入，验收覆盖：正在入住、重叠订单、退房观察日及第 1～3 天、第 4 天切未来、无未来保留最近、历史回填、未来取消回退、改期/取消、客户合并、无订单、缺房名和异常日期。
- 回归必须证明员工备注保存/清空/审计不变，客户列表没有 N+1；迁移执行 SQLite 升级→降级→再升级并检查 PostgreSQL 离线 SQL。
- 完成前执行定向与全量 pytest、Ruff、mypy、compileall、pip check 和 diff check；独立复审通过后合并推送，云端先备份数据库和旧提交，再迁移部署并验收列表、详情和健康状态。

#### 实施计划

- [x] 生成并审核精确实施计划；编码阶段严格执行 RED→GREEN，发现与 Spec 冲突先反向同步本节。
- [x] 实现数据库字段、迁移回填与百居易状态观察日期幂等更新。
- [x] 实现集中选择/格式化计算器及列表批量查询，避免 N+1。
- [x] 将只读自动备注接入 CRM 列表、移动卡片和详情页，不改变员工备注表单。
- [x] 完成迁移、仓储、服务、路由、模板及客户合并回归测试。
- [x] 完成全量验证、独立复审、主干合并、云端备份迁移和可执行的页面/数据验收。

##### 精确执行计划

**执行前隔离**

- [x] 使用 `using-git-worktrees` 创建 `fix/crm-latest-stay-note-20260814` 隔离 worktree，并确认主工作区两份未跟踪资料不会进入提交。
- [x] 在隔离 worktree 运行以下基线，预期全部通过且不访问真实外部接口：

```bash
env -u RUN_LIVE_CONTRACT_TESTS -u RUN_DEEPSEEK_CONTRACT \
  .venv/bin/pytest -q tests/integration/test_operations_repository.py \
  tests/integration/test_customer_repository.py \
  tests/unit/test_customer_admin_service.py \
  tests/integration/test_customer_routes.py tests/unit/test_migrations.py
```

**任务 1：集中订单状态与纯计算器**

文件：新建 `src/homestay_bot/domain/stay_status.py`、`src/homestay_bot/services/latest_stay_note.py` 和 `tests/unit/test_latest_stay_note.py`。

- [x] 先写 RED，定义以下不可变输入，并覆盖当前入住、重叠订单、退房第 0～3 天、第 4 天切未来、无未来保留最近、取消回退、无订单、异常日期、占位房名和格式化：

```python
@dataclass(frozen=True)
class LatestStayCandidate:
    order_id: int
    customer_id: int
    property_id: int
    property_title: str | None
    check_in_date: date
    check_out_date: date
    status: str
    checkout_observed_on: date | None

def select_latest_stay_note(
    candidates: Sequence[LatestStayCandidate], *, today: date
) -> LatestStayNoteResult:
    """按武汉日期和已确认优先级选择一条只读入住备注。"""
```

- [x] 运行 `.venv/bin/pytest -q tests/unit/test_latest_stay_note.py`，预期因模块缺失而 RED。
- [x] 最小实现集中状态函数；排除集合固定为 `cancelled/canceled/declined/expired/deleted`，已退房集合固定为 `checked_out/completed`。
- [x] 实现选择器：当前入住用 `check_in <= today < check_out`；保留窗口用 `today <= (checkout_observed_on or check_out_date) + 3 days`；窗口外未来订单按入住日和 ID 升序，历史订单按退房日、入住日和 ID 降序；无效日期只返回稳定 `invalid_stay_dates` 计数。
- [x] 空标题或 `百居易房间 <ID>` 显示 `房间 #<ID>`，其他标题原样使用；格式固定为 `M.D-M.D标题`。
- [x] 复跑单测至 GREEN；实现纳入统一功能提交。

**任务 2：迁移与退房观察日期**

文件：修改 `src/homestay_bot/domain/models.py::StayOrder`、`src/homestay_bot/repositories/operations.py::SQLAlchemyOperationsRepository`、`tests/unit/test_models.py`、`tests/unit/test_migrations.py`、`tests/integration/test_operations_repository.py`；新建 `migrations/versions/0018_stay_checkout_observation.py`。

- [x] 先写 RED：模型存在可空日期；历史完成订单回填计划退房日；其他状态为空；upsert 首次完成写武汉日期、重复同步不覆盖、恢复有效清空、再次完成重写。
- [x] 运行上述模型、迁移和仓储用例，确认因字段和迁移缺失而 RED。
- [x] 模型新增：

```python
checkout_observed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
```

- [x] `0018` 用 `batch_alter_table("stay_orders")` 添加列，并在线执行可跨 SQLite/PostgreSQL 的回填；降级删除列：

```sql
UPDATE stay_orders
SET checkout_observed_on = check_out_date
WHERE lower(trim(status)) IN ('checked_out', 'completed')
```

- [x] 仓储构造函数增加可注入 `local_date_provider: Callable[[], date]`，默认武汉今日；upsert 只在首次进入完成状态写日期，保持完成不改写，恢复其他有效状态清空，取消类状态不伪造退房日期。
- [x] 迁移测试检查 SQLite 升级→降到 `0017`→再升级，以及 PostgreSQL 离线 SQL；当前迁移头更新为 `0018_stay_checkout_observation`。
- [x] 复跑至 GREEN；实现纳入统一功能提交。

**任务 3：CRM 批量派生查询**

文件：修改 `src/homestay_bot/services/customer_admin_service.py`、`src/homestay_bot/repositories/customers.py`、`tests/unit/test_customer_admin_service.py` 和 `tests/integration/test_customer_repository.py`。

- [x] 先写 RED：`CustomerCard.latest_stay_note` 返回派生值或 `None`；列表无论 1 人还是 50 人只调用一次批量查询；详情复用相同逻辑；员工备注原值不变。
- [x] 仓储协议新增：

```python
async def latest_stay_notes(
    self, customer_ids: list[int], *, today: date
) -> dict[int, str | None]:
    """一次批量查询并返回客户编号到自动入住备注的映射。"""
```

- [x] 仓储一次查询显式选择 `StayOrder` 计算字段并左连接 `PropertyProfile.title`，以客户 ID 集合执行单次 `IN` 查询；空 ID 列表直接返回空映射。
- [x] `CustomerAdminService` 注入默认武汉今日的日期提供器；列表取得客户后只调用一次批量方法，详情传单个 ID；`CustomerCard` 增加 `latest_stay_note`。
- [x] 使用 SQLAlchemy 查询计数断言客户数量增长不会增加入住备注 SQL；加入客户合并后来源与目标全部订单重新计算的集成回归。
- [x] 复跑客户服务和仓储测试至 GREEN；实现纳入统一功能提交。

**任务 4：CRM 页面接入**

文件：修改 `src/homestay_bot/templates/customers/index.html`、`src/homestay_bot/templates/customers/detail.html` 和 `tests/integration/test_customer_routes.py`。

- [x] 先写 RED：桌面列表、移动卡片和详情均显示“最新入住备注”；无值显示“暂无入住记录”；自动值不出现在员工备注 textarea。
- [x] 列表模板使用 `customer.latest_stay_note or "暂无入住记录"`，不增加搜索或写操作；详情在员工备注表单上方增加只读面板。
- [x] 回归员工备注保存和清空仍为 303，审计仍为 `customer_note_updated` 且不含备注正文。
- [x] 复跑路由测试至 GREEN；实现纳入统一功能提交。

**任务 5：全量验证、复审与部署**

- [x] 运行定向测试：

```bash
.venv/bin/pytest -q tests/unit/test_latest_stay_note.py tests/unit/test_models.py \
  tests/unit/test_migrations.py tests/integration/test_operations_repository.py \
  tests/unit/test_customer_admin_service.py \
  tests/integration/test_customer_repository.py \
  tests/integration/test_customer_routes.py
```

- [x] 运行禁 live 全量与静态验证：

```bash
env -u RUN_LIVE_CONTRACT_TESTS -u RUN_DEEPSEEK_CONTRACT \
  -u RUN_HOSTEX_CONTRACT -u RUN_WECOM_CONTRACT .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m compileall -q src
.venv/bin/pip check
git diff --check
```

- [x] 独立只读复审状态转换、三天边界、历史回填、客户合并、批量查询次数、员工备注隔离和模板转义；发现 Important/Critical 时先补 RED 再修复。
- [x] 更新本节 Review，显式暂存目标文件并排除两份用户资料和 `.venv`；快进合并 `main` 并推送 GitHub。
- [x] 云端先备份旧提交、`.env` 和非空 PostgreSQL `pg_dump`，再同步代码并只重建 API；确认迁移头为 `0018_stay_checkout_observation`。
- [ ] 云端只读核对一名有多次入住的客户：列表和详情一致、员工备注未变化、自动值符合武汉今日规则；本机与公网 `/health` 为 `ok`、部署后无新增错误。
- [x] 保存备份路径和旧提交作为回滚证据，完成后清理隔离 worktree。

#### 实施 Review

- RED→GREEN：三个并行子任务分别从模块/字段/批量接口缺失开始；交叉审查发现未来入住却已 completed 的矛盾记录会误展示，补回归后要求 `check_in <= checkout_observed_on <= today`，并阻止同步层伪造观察日。
- 查询与合并：真实 50 客户、50 订单仅执行 1 条自动备注 SQL；真实客户合并后来源订单迁入目标，并按目标全部订单重新择优。
- 边界与页面：退房第 0～3 天保留、第 4 天切换；列表桌面/移动卡片/详情均展示，只读自动备注与员工备注完全隔离；恶意房名通过 Jinja 转义。
- 验证：定向 `92 passed`；禁 live 全量 `905 passed, 15 skipped`；Ruff、mypy（105 个源码文件）、compileall、pip check、diff check 全部通过。两轮独立复审最终均 APPROVED。
- 提交与部署：功能提交 `d9c62d4` 已快进合并并推送 `main`；云端已更新同一提交并只重建 API，PostgreSQL 容器保持运行。
- 回滚证据：备份目录 `/opt/yumi-backups/crm-latest-stay-20260814-013238`，包含旧提交 `89a17a6101395d2b2e858eae8276f16a88589f56`、权限收紧的 `.env` 和非空 `postgres.sql`（263340 bytes）。
- 云端验收：Alembic 为 `0018_stay_checkout_observation (head)`，API 新容器启动日志确认执行 0017→0018；本机与公网 `/health` 均为 `ok`。正式库当前没有同一客户两笔以上订单，因此无法虚构“多次入住客户”抽样；已对一笔关联订单只读计算出自动备注，并确认员工备注及会话均未变脏。多订单择优由真实 SQLite 合并集成测试覆盖。
- [x] 企业微信员工通知优先显示 CRM 自动入住备注；无自动备注时显示员工备注；两者都没有时显示客人名称，任何路径不得显示 UID。
- [x] 先补 ConversationService 通知格式与 SQLAlchemy 客户仓储三级回退的失败测试，确认 RED 后最小实现。
- [x] 复用 `SQLAlchemyCustomerRepository` 的最新入住备注计算，不复制日期/状态选择规则；应用消息入口复用同一仓储实例。
- [x] 更新用户纠正规则，完成定向/全量测试、Ruff、mypy、云端备份和部署。
- [ ] 请用户再发送一条会触发管家通知的真实企业微信消息，核对员工端展示自动入住备注且无 UID/房间号。

#### 员工通知 CRM 备注 Review

- RED→GREEN：最初 `ConversationService` 不接受 `customer_notification` 接口；补三级回退后，恶意换行展示名回归先失败再统一单行化；独立复审又复现长中文备注与消息组合为 2170 bytes，补完整正文 UTF-8 预算后转绿且不切断中文字符。
- 通知顺序：自动入住备注 → 员工备注 → 企业微信客人名称；客服账号使用实际名称，所有路径不显示 UID 或房间号。CRM 查询异常仅记录异常类型并回退客名。
- 装配边界：消息入口复用同一个 `SQLAlchemyCustomerRepository`，自动备注继续使用既有武汉日期、状态和退房三天选择器，没有复制业务规则。
- 验证：相关回归 `99 passed`；禁 live 全量 `909 passed, 15 skipped`；Ruff、mypy（105 个源码文件）、compileall、pip check、diff check全部通过。独立复审以中文和 emoji 极端值确认正文 2046 bytes、UTF-8 roundtrip 正常、四个字段完整，代码层面无 Critical/Important。
- 提交与部署：提交 `5c4177b` 已推送主干并部署；回滚备份 `/opt/yumi-backups/wecom-crm-note-20260814-021609` 含旧提交、权限收紧的 `.env` 和非空 PostgreSQL 导出（268944 bytes）。服务器迁移为 `0018 (head)`，本机及公网 `/health` 均为 `ok`，启动日志无新增错误。
- 云端只读核对：客户 62 的员工备注仍为空，测试入住为 `2026-08-14` 至 `2026-08-16`、房源 `《春和景明》`，自动备注数据没有改写员工备注。只差一条部署后的真实企业微信触发消息核对员工接收端最终排版。
- [x] 只读核对最新企业微信入站、消息、任务与日志，确认重复回复不是回调重复、worker 重试或消息重复入库。
- [x] 补 RED：快速安抚与最终安全文本完全一致时只能发送一次；最终包含新建议时仍发送。
- [x] 任务载荷只传安抚文本 SHA-256，延迟入口恢复摘要；最终出口比较安全策略处理后的实际文本。
- [x] 完成相关/全量验证和独立复审。
- [x] 提交推送并完成云端备份部署。
- [ ] 请用户再次发同类消息，核对只收到一次无新增内容的安抚；若模型生成有效排障建议，则允许收到第二条不同回复。

#### 重复回复根因与修复 Review

- 生产证据：客人消息 `33R5uBgv2s4A8q6AcrkUywhGX` 只入库一次；`wecom_process_message` 及两个发送任务各执行一次且无重试。02:24:01 的 `ack` 与 02:24:07 的 `text` 内容哈希完全相同，确认是快速安抚和最终回复两个业务出口重复，而非企业微信重复投递。
- 修复边界：快速安抚继续优先送达；只有最终文本经过承诺过滤后与该安抚的 SHA-256 完全一致才跳过发送，FAQ、任务和员工通知等后续副作用继续执行；包含新安全建议的最终回复不抑制。
- 投递状态：最终任务在调用模型前只读查询快速安抚 outbox；`PENDING/RUNNING` 使用长期低频重排，`FAILED/缺失` 清除摘要并发送最终兜底，只有 `COMPLETED` 才允许去重。任务只保存摘要和 outbox 编号，不复制安抚正文。
- 验证：相关 `146 passed`；禁 live 全量 `917 passed, 15 skipped`；Ruff、mypy（105 个源码文件）、compileall、pip check、diff check全部通过。独立复审最终 APPROVED，无 Critical/Important。
- 提交与部署：修复提交 `e7487ef` 已推送并部署；备份 `/opt/yumi-backups/duplicate-reply-20260814-024817` 包含旧提交 `fa0b1de`、权限收紧的 `.env` 和 PostgreSQL 导出（270594 bytes）。云端迁移保持 `0018 (head)`，本机与公网 `/health` 均为 `ok`，启动日志无新增错误。

### 连续客人消息三秒合并（2026-08-14）

#### 已确认 Spec

- 现状依据：`src/homestay_bot/services/conversation_service.py::handle_message()` 当前在普通消息入库后立即调用 `_stage_fast_ack()`；`src/homestay_bot/application.py::handle_deferred_message()` 随后逐条恢复消息并执行最终模型回复，所以连续片段会分别触发阶段任务。
- 普通客人文本先等待 3 秒静默；静默期内同一会话出现新文本时，旧任务只做过期退出，以最新一条消息自己的 3 秒截止时间为准。
- 静默结束后，按系统实际入库顺序合并同一连续片段，最多 10 条、合并正文最多 2000 字符；数据库仍逐条保存原始消息，不改写历史。
- 合并后的完整问题才执行民宿相关性和快速安抚判断：服务、补给、维修等请求只发一次安抚；房态、旅游、设施介绍等信息问题不发安抚，直接等待一次最终回复。
- 合并问题只调用一次正式模型、只产生一组业务副作用；模型上下文把本轮连续客人片段折叠为一条完整 user 消息，避免三条上下文上限遗漏开头。
- 单条即可识别的紧急事件、明确客诉、明确转人工、非文本消息和员工消息继续绕过静默窗口立即处理；若风险语义只有合并片段后才完整，静默结束后必须再次执行紧急、客诉和人工规则，禁止把合并出的高风险内容送入普通模型。
- 延续既有投递门控：快速安抚只有企业微信 outbox 完成后才允许抑制相同最终回复；发送失败仍由最终回复兜底。
- 并发线性化：所有新入站活动和静默任务消费都先对同一 `Conversation` 行执行数据库 `FOR UPDATE`；静默任务持锁完成“检查是否过期 → 合并 → 写 ACK/final outbox”，新消息若先取得锁则旧任务退出，静默任务若先取得锁则视为该三秒批次已正式关闭。
- 旧静默任务应被任何后续非机器人活动取消，包括客人图片/语音和员工回复，不能只检查后续客人文本。
- 确定性规则统一对原文、单空格归一化文本和去空白紧凑文本判断；该归一化只用于紧急、客诉、转人工、快速安抚和最终人工原因，不替换交给模型或员工通知的合并原文。
- 最终阶段在模型前快速检查任意后续非机器人活动；模型完成后、任何客人出站或业务副作用前必须取得同一会话行锁并再次检查。模型调用期间出现图片、语音或员工回复时，旧 final 任务应无出站、无业务副作用退出。

#### 精确执行计划

- [x] RED 1：在 `tests/unit/test_conversation_service.py` 证明普通服务请求入站后不立即安抚，只登记 `phase=debounce`、`available_at=now+3s` 的任务；旧来源检测到更新消息时不安抚、不调用模型。
- [x] GREEN 1：修改 `src/homestay_bot/services/conversation_service.py::ConversationJobPort` 和 `handle_message()`，新增三秒静默任务登记；普通文本的民宿相关性判断移到静默结束后，立即处理分支保持不变。
- [x] RED 2：在 `tests/unit/test_message_service.py` 与 `tests/integration/test_message_flow.py` 覆盖按入库顺序合并、间隔超过三秒断开、最多十条、最多 2000 字符及原始消息不变。
- [x] GREEN 2：在 `src/homestay_bot/services/message_service.py` 新增不可变 `GuestMessageBatch` 和 `build_guest_batch()`；复用 `MessageRepository.list_recent()`，不增加逐条数据库查询。
- [x] RED 3：覆盖静默结束后合并问题再判断安抚、信息问题零安抚、最终任务只登记一次、模型上下文把本轮片段折叠为一条且保留此前对话。
- [x] GREEN 3：新增 `ConversationService.process_debounced_message()`；扩展 `_stage_fast_ack()` 任务阶段和合并计数；`MessageService.build_context()` 仅在显式合并元数据存在时折叠本轮客人片段。
- [x] RED 4：在 `tests/unit/test_application.py` 覆盖 debounce/final 任务载荷往返和 handler 阶段分发，防止部署装配漏传合并计数或误把静默任务直接当最终任务。
- [x] GREEN 4：修改 `src/homestay_bot/application.py::_deferred_message_from_payload()` 与 `handle_deferred_message()`，同一 `wecom_process_message` worker 按 `phase` 分发，不新增后台循环和迁移。
- [x] 回归：验证单条紧急、客诉、明确人工、非文本仍立即，拆分后才完整的紧急/客诉在合并阶段进入固定安全流程；快速安抚 outbox PENDING/FAILED/COMPLETED 门控、最终正文去重和 worker 重试语义不回归。
- [x] 复审修复 RED：覆盖“补”+“矿泉水”跨行仍安抚、“提”+“前入住”最终转人工、后续图片/员工回复取消旧任务，以及新入站和静默消费都先取得同一会话锁。
- [x] 复审修复 GREEN：新增会话活动行锁和任意后续非机器人活动查询；集中确定性规则候选文本，贯穿合并安抚和最终人工判断。
- [x] 最终竞态 RED：覆盖 ACK 后出现图片/员工回复时 final 模型零调用，以及模型运行中员工回复后 final 无客人出站和副作用。
- [x] 最终竞态 GREEN：最终模型前查任意活动，模型后取得会话锁并复查；锁保持至 handler 事务提交。
- [x] 验收：运行相关 pytest、禁 live 全量 pytest、Ruff、mypy、compileall、pip check、diff check；更新本节 Review 和 `tasks/lessons.md`，独立复审后再决定合并、推送与云端部署。

#### 连续消息三秒合并 Review

- RED→GREEN：旧实现对第一条服务片段立即安抚，且没有 `process_debounced_message()`；新增测试先观察 4 项失败，再实现 `phase=debounce` 三秒任务、静默过期退出、合并后单次安抚和单个 final。消息服务的顺序、三秒断点、10 条/2000 字符和模型上下文折叠也均先因接口缺失失败再转绿。
- 合并边界：原始 `messages` 行逐条保留；只读批次按 `created_at` 与主键顺序计算，超过三秒即断开，超长时优先保留最新 2000 字符。本轮连续 user 片段在模型上下文中折叠成一条，上一轮问答仍保留。
- 并发与人工边界：入站和 debounce 对同一 Conversation 行加 `FOR UPDATE`；任何后续客人活动（含图片/语音）或员工回复使旧任务过期。final 在模型前快速检查，模型后锁行复查，故模型运行期间出现人工回复也不会再产生客人出站、FAQ、任务或员工通知副作用。
- 安全规则：单条紧急、客诉、明确人工和非文本继续立即处理；合并后再次用原文、空格归一化和去空白文本复核紧急、客诉、人工及快速安抚，覆盖跨消息拆开的“补/矿泉水”“提/前入住”“human/agent”。原始合并正文仍用于模型和审计展示。
- 出站与恢复：debounce 阶段安抚使用 `ack` outbox 阶段，最终使用 `final`，幂等键互不覆盖；已有 ACK PENDING/RUNNING 延迟、FAILED 兜底、COMPLETED 正文去重保持不变。
- 验证：相关回归 `161 passed`；最终禁 live 全量 `941 passed, 15 skipped`，仅有既有 Starlette 弃用警告；Ruff、mypy（105 个源码文件）、compileall、pip check、diff check 全部通过。独立复审两轮发现并验证修复竞态后最终 APPROVED，无 Critical/Important/Minor。
- 集成与部署：功能提交 `5d35e96` 已快进合并到 `main`、推送 GitHub 并部署云服务器；服务器只重建 API，PostgreSQL 容器保持运行。
- 回滚证据：备份目录 `/opt/yumi-backups/guest-message-debounce-20260820-213459`，包含旧提交、权限收紧的 `.env` 和非空 PostgreSQL 导出（370051 bytes）。
- 云端验收：服务器代码为 `5d35e96`，Alembic 保持 `0018_stay_checkout_observation (head)`；API 启动日志确认 application startup complete，本机 `127.0.0.1:8000/health` 与公网 `https://akros.icu/health` 均返回 `{"status":"ok"}`。
- 剩余真实验收：请用户在企业微信连续发送两到三条属于同一问题的文本，间隔均小于三秒；确认三秒静默结束后只产生一组回复和业务处理。

### 统一民宿管家回复口吻（2026-08-20）

#### 已确认 Spec

- 采用“模型提示词 + 本地确定性出站策略”的混合方案，不增加第二次模型调用；普通问答、天气、旅游、房态、FAQ 和服务请求统一以温暖、简洁、可靠的民宿管家口吻回复。
- 所有客人可见出口必须经过同一个本地回复策略；员工内部通知保持原样。发送正文、快速安抚去重摘要和最终回复摘要必须基于同一份最终正文，策略重复执行时结果不变。
- 天气类回答默认武汉：先自然说明“我帮您看了一下”，明确日期、天气、温度和降雨，再根据真实结果给一条简短实用提醒；不得编造天气、设施、距离、房态、价格或处理进度。
- 旅游与路线类先回答结论，再给关键提醒；保留客人指定日期、地点和来源。客人未指定地点时继续使用既有武汉默认规则。
- 房态与价格类先给结论；参考价必须明确为参考信息，不能包装成最终成交价。FAQ 使用熟悉房源的管家口吻，未知信息必须明确未确认。
- 普通服务请求只确认已收到并说明联系管家，不承诺完成结果、时点或人员已经出发；不得使用“马上安排好”“师傅正在赶来”等不可核实表达。
- 高危且需要转人工的场景使用理性、客观、中立口吻：优先确认已记录，不使用“抱歉、对不起、给您添麻烦”等道歉，不判断责任，不承诺结果、时间或人员到场。
- 高危转人工唯一允许的流程说明为“我会立即联系值班管家跟进处理”；不得写“第一时间派遣管家”，因为“派遣”会暗示人员已经调度或必然到场。
- 涉及火灾、燃气等现实安全风险时，安全指令必须排在确认与转人工之前。通用模板为“您的情况我已记录。我会立即联系值班管家跟进处理，请保持联系方式畅通。”；火灾/燃气模板为“请立即离开危险区域，并根据现场情况拨打 119。您的情况我已记录，我会立即联系值班管家跟进处理。”
- 风格重构不得改动日期、数字、温度、价格、房态、来源和安全步骤；不得重复开场白、堆叠语气词或使用“亲亲”等电商客服表达。

#### 精确执行计划

- [x] RED 1：扩展 `tests/unit/test_guest_reply_policy.py`，覆盖天气/旅游/房态/FAQ/普通服务的管家口吻、事实字段原样保留、中文和英文结果、重复执行幂等，以及客人可见承诺过滤。
- [x] RED 2：补高危转人工矩阵，覆盖道歉、责任判断、处理结果、完成时间、师傅或管家已经出发等表达均被移除；安全场景必须保留并优先输出撤离、报警等既有安全步骤。
- [x] GREEN 1：在 `src/homestay_bot/services/guest_reply_policy.py` 集中实现回复类型与统一最终正文准备函数；保留现有兼容入口，避免在各路由或服务中复制模板和正则。
- [x] RED 3：扩展 `tests/unit/test_deepseek_client.py` 与旅游/天气相关测试，证明普通系统提示和 `_refine_reply()` 都要求温暖简洁的民宿管家口吻，同时严格保留搜索事实、证据标签和客人指定条件。
- [x] GREEN 2：修改 `src/homestay_bot/integrations/deepseek_client.py` 的生成与精炼提示；继续复用当前一次模型调用和现有搜索结果，不新增二次润色调用。
- [x] RED 4：扩展 `tests/unit/test_conversation_service.py`，逐个覆盖快速安抚、最终模型、规则回复、搜索失败、人工接管和安全异常出口；断言员工内部通知不被改写，最终发送正文与去重摘要一致，连续消息三秒合并和 ACK 投递门控不回归。
- [x] GREEN 3：修改 `src/homestay_bot/services/conversation_service.py`，让所有客人可见出口统一调用最终正文策略；删除或收敛 `_warm_guest_reply()` 的重复职责，禁止同一正文被多次非幂等重构。
- [x] 回归验证：运行新增定向 pytest、对话/旅游/天气/安全策略相关套件、显式禁用 live contract 的全量 pytest、Ruff、mypy、compileall、pip check 和 diff check；记录修改前 RED 与修改后 GREEN 证据。
- [x] 质量审查：核对事实字段不变、所有客人出口覆盖、高危规则无道歉无承诺、安全步骤优先、无新增外部调用、日志与审计不包含客人敏感正文。
- [x] 集成与部署：用户确认实现并通过审查后再提交、推送；云端先备份代码、`.env` 和数据库，再只重建必要服务，验证迁移、容器日志、本机及公网健康检查。
- [ ] 真实验收：在企业微信分别询问“明天天气如何”、普通服务请求和一条高危需人工问题，核对武汉默认、亲和管家口吻、三秒合并、单次回复、高危中立模板与值班管家收尾。

#### 统一民宿管家回复口吻 Review

- RED→GREEN：本地策略入口最初不存在；天气事实重构、高危中立模板和安全优先测试先失败，再由统一 `prepare_guest_reply()` 转绿。模型提示的普通、旅游精炼和天气搜索三项先缺明确管家口吻与事实保护，再补齐中英文指令转绿。
- 出站边界：`ConversationService` 的普通出口、快速安抚、最终模型、客诉、人工接管、紧急事件和失败兜底均复用统一策略；最终发送正文与 SHA-256 去重摘要继续使用同一文本。员工通知、人工审核后发送和入住凭证等非机器人自动回复不被改写。
- 高危边界：客诉、退款、紧急事件和明确人工接管使用固定中立话术，不保留模型道歉、责任判断、到场或完成承诺；火灾等安全步骤排在确认与联系值班管家之前。普通模型或联网故障保留明确失败说明，再按原流程联系管家。
- 风格与事实：普通模型、旅游搜索和精炼提示统一为温暖、简洁、可靠的民宿管家口吻，不新增二次模型调用；天气本地策略只增加自然开场和有降雨依据时的带伞提醒，日期、地点、温度、降雨、价格、房态与来源不改写。
- 验证：相关套件 `181 passed`；禁 live 全量 `953 passed, 15 skipped`，仅有既有 Starlette 弃用警告；Ruff 全仓、mypy（105 个源码文件）、compileall、pip check、diff check全部通过。手工三场景探针确认天气、高危和火灾最终正文符合已确认模板。
- 集成与部署：功能提交 `6239048` 已推送主干；服务器直连 GitHub 两次 TLS 失败后，改用经 `git bundle verify` 校验的同一 Git 提交包执行 fast-forward，没有覆盖 `.env` 或未跟踪文件。回滚备份 `/opt/yumi-backups/guest-reply-tone-20260821-001935` 含旧提交、权限 `600` 的 `.env` 和非空 PostgreSQL 导出（371355 bytes）。
- 云端验收：服务器代码为 `6239048`，Alembic 保持 `0018_stay_checkout_observation (head)`；只重建 API，PostgreSQL 容器保持连续运行。API 日志确认 application startup complete，本机 `127.0.0.1:8000/health` 与公网 `https://akros.icu/health` 均返回 `{"status":"ok"}`。
