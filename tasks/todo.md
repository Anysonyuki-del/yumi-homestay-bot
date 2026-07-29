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
