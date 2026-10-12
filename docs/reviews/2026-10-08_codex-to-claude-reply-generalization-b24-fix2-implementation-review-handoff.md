# Codex → Claude：1.70.0 综合实施审查与修复建议

日期：2026-10-08。结论：**需修改，暂不具备本轮审查通过条件。确认 8 项问题：3 项 P1、5 项 P2。**

本报告合并审查泛用化 fix2 实施与知识占位符、INFO 日志、Bearer 脱敏修订。三个回复覆盖问题已进一步经过真实 `ConversationService`、来源完成入口和事务发件箱，核对最终客人正文及 `wecom_send_text/final` 路由。其余问题以真实日志输出、HTTP、临时 SQLite 和实际草稿 SDK 请求载荷复现。全部使用合成数据与本地替身；未调用真实模型或业务平台，未操作生产。

## 1. 范围、基线与证据归属

共同审查以下两份返回报告，第二份包含 §9 修订：

- [fix2 实施交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-reply-generalization-b24-fix2-implementation-handoff.md)。
- [占位符与日志交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_claude-to-codex-knowledge-placeholder-and-logging-handoff.md)。
- 实施合同：[fix2 Spec v10](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md)，以及实施交接 §7.1、§7.2 记载的后续决定。报告里的授权引用只作为作者实施背景，本次审查不据此执行外部动作。

仓库：`/Volumes/02/code/homestay-bot`。当前 HEAD：`80f9b2c309547c36c15cfd142d5dd30b98d87406`，main。候选 `rc-1.70.0-3`：`dca28e5a98bb0ced527e3411f7a5d3b7102f98c1`。独立逐文件读取该标签的 `src/`、`tests/`、`scripts/`、`migrations/`、`pyproject.toml`、`requirements.lock` 共 **399 个文件**，与审查时工作区字节比较 **0 差异**；目录额外文件仅两个 `.DS_Store`，不属于候选源码。

小修复报告 §8、§9.5 是不同时间的历史快照；共享的知识路由、草稿与测试后来继续加入 fix2 内容，不能要求其旧摘要与整合后的整文件一致。本次结论绑定下方当前摘要和 rc3。现有用户/Claude 改动全部保留；Codex 仅新增本报告、三个离线复现脚本和必要认知维护资产。

## 2. 问题总览

| 编号 | 优先级 | 已确认问题 | 归因与边界 |
| --- | --- | --- | --- |
| I70-R1 | P1 | 明确标签后的带标点密码通过净化、模型输入与启用入库 | 新闭环的确定性凭据防线不完整；客诉也复用该规则 |
| I70-R2 | P1 | JSON/repr 的带引号敏感键仍在实际日志和异常栈中泄漏 | 原有漏洞未闭合；不称为本次新引入的漏洞 |
| I70-R3 | P1 | 静态小句使用另一日期的审核知识 | Spec 明确守卫漏实现；旧助手代理也有问题，不能称纯新回归 |
| I70-R4 | P2 | 房价问法污染同句早餐静态取证，已有答案仍被判缺资料 | 本次覆盖/剩余处理未按自己的分解结果取证 |
| I70-R5 | P2 | ISO 日期年份被认作房号，正文同时说可订与无法确认 | 本次房号识别的格式遗漏 |
| I70-R6 | P2 | SQLite 清空提交后，旧代次审核仍新建启用知识 | 行锁不能代替跨方言的状态/代次条件更新；未证明 PG 失败 |
| I70-R7 | P2 | 最新 100 条列表隐藏旧待处理记录，后台数量与可达记录不一致 | 新后台列表截断，无分页 |
| I70-R8 | P2 | 启用占位符被拒后，HTML 跳到任务中心 | 新启用拒绝路径缺安全回跳信息；数据拦截本身正确 |

## 3. 逐项依据、影响与最小修复

### I70-R1：凭据字符集造成整条链路漏检

**依据。** [sensitive_text.py:64](/Volumes/02/code/homestay-bot/src/homestay_bot/services/sensitive_text.py:64) `_REDACTION_RULES` 与 [sensitive_text.py:85](/Volumes/02/code/homestay-bot/src/homestay_bot/services/sensitive_text.py:85) `_GENERIC_SECRET` 只接受连续 `[A-Za-z0-9_-]{4,}`。`contains_sensitive_text` 通过净化前后是否不同判断敏感，因而继承同一漏检。

合成反例 `Wi-Fi password: A!b2C#d4` 和 `门锁密码：1#2#3#4` 均得到 `False`，净化原样保留。真实链路继续如下：

1. [KnowledgeAdminService.start_referral_draft:662](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:662) 将含密码的管家消息冻结为合法来源。
2. [ReferralDraftJobService.handle:82](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_referral_service.py:82) 净化后调用真实 `DeepSeekFaqDrafter`；假 SDK 捕获的**实际请求**含 `staff_messages: ["Wi-Fi password: A!b2C#d4"]`。
3. [DeepSeekFaqDrafter.validate_staff_draft:207](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_faq_drafter.py:207) 接受中英密码草稿。
4. [KnowledgeAdminService.convert_referral:713](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:713) 最终字段检测也通过，实际创建启用知识，中英答案含完整合成密码。

这同时破坏“凭据不进入草稿模型”和“不沉淀到通用知识”两条边界。管理员勾选已审核不能代替本地安全门。这里未发现生产真实秘密，也未向真实供应商发送探针。

**建议。** 在现有共用规则里正确消费明确凭据标签后的标点值，检测与净化继续共用；凭据应整段遮盖，不能只遮住某个字母数字前缀而留下尾部。保留旧格式和不含凭据值的问句对照。只在共用边界修复，客诉、转交、草稿、最终入库一起受益。

**牵连检查。** 当前 `No password is needed for the gym` 被误判为敏感并改成 `No [credential removed] for the gym`。修复时同时约束“标签/赋值关系”和“值的边界”，避免扩大字符集后吞掉普通英文否定句。回归应断言实际 SDK 载荷无秘密、管理员直接提交被拒且无孤立启用知识；覆盖中英、标点、引号、否定句和无值提问。

### I70-R2：带引号的敏感键绕过统一日志脱敏

**依据。** [logging.py:36](/Volumes/02/code/homestay-bot/src/homestay_bot/logging.py:36) `_SECRET_ASSIGNMENT_PATTERN` 要求敏感键之后立即出现空白与 `=`/`:`。JSON/repr 在键与冒号之间还有引号：`{"Authorization": "Bearer SYNTHETIC_ONLY_TOKEN"}`、`{'api_key': 'SYNTHETIC_ONLY_KEY'}` 均不匹配。

以 `uvicorn.Config('homestay_bot.main:app').load()` 的生产启动顺序配置真实输出，执行两参数格式化日志及真实异常：

```text
WARNING homestay_bot.review: synthetic_headers={'Authorization': 'Bearer SYNTHETIC_ONLY_TOKEN'} status=401
RuntimeError: {'api_key': 'SYNTHETIC_ONLY_KEY'}
```

stderr 两个合成秘密均可见，异常栈正常保留。这不是“只测正则”的代理；已走 `SensitiveDataFilter` 与实际 handler。**一个 mapping 参数**会被 Python logging 特殊解包后进入字段脱敏，可正常遮盖；**多个格式参数中的 mapping**先变成 repr 文本，正是遗漏的兄弟路径。原始 JSON 字符串、异常字符串也同样受影响。

**建议。** 扩展共用文本规则支持可选的单/双引号键，仍整段消费 Bearer 值，保留其他文字、换行及重复过滤幂等性。复用 `redact_text`，保持 Uvicorn 五元访问参数协议。新增一组参数化回归覆盖原始 JSON、两参数 mapping、异常和调用栈；既有无引号、引号值、中文问号、URL 查询参数对照继续有效。

**归因。** HEAD 旧规则也存在此漏洞；§9 修订闭合了此前四种 Bearer 反例，但“统一入口”还没有覆盖常见序列化凭据文本。因此是本次日志安全目标尚未完成，不应写成新增漏洞或宣称生产已泄漏。

### I70-R3：静态小句的绝对日期守卫遗漏

**依据。** [DeepSeekGuestAssistant._item_evidence_plan:1670](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1670) 的 `unbound` 只检查房号；[deepseek_client.py:1705](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1705) 始终将 `item_dates` 传给静态取证。与 [Spec:241](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md:241) “小句点名的房号或绝对日期与项目标不同时，不使用该项候选”直接不符。

真实 SQLite 知识 6001 仅 **2026-10-10** 有效，审核答案为“早餐7:30开始”。合法粗粒度计划项为“10月10日有房吗，10月11日早餐几点开始”，项目标 10/10–11。真实检索按项目标取到 10/10 知识，随后静态小句没有复核 10/11。最终发件箱：

```text
早餐7:30开始。

10月10日入住、10月11日退房：
可订：401
```

早餐依据 `target_date=2026-10-10`，两项均 grounded、缺口为空，实际路由 `wecom_send_text/final`。直接按 10/11 检索得到 0 条。模型 JSON 失败后的 `availability_fallback` 同样复现；同日对照正确。

**建议。** 在静态候选使用前，复用 `clause_dates` 与现有范围绑定结果，同时核对房号、绝对日期及端点角色。先基于原始小句核对范围，再处理剩余文本；与项目标不符记 `scope_unbound`，保留已验证房态/价格，按合同回未确认，不能转交为“资料缺失”。正常出口、协议失败回退、日期澄清保持共用位置。

**牵连检查。** 限期、单个退房日、绝对日期区间、ISO 日期、静态项与住宿项、正常/回退出口，以及同轮复用的接收项有效期都应走同一守卫。无需扩大检索预算或借整轮池。旧助手源码配当前依赖的代理对照也发出错误日期答案，故准确归因为“本轮承诺的守卫漏实现”，没有完整旧 checkout 回归证据。

### I70-R4：取证仍使用整句，房价词污染早餐属性

**依据。** [deepseek_client.py:1701](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1701) 已持有 `needs.residue`，但调用 `_static_item_parts` 时仍传 `clause.text`；直到记录缺口才使用 residue。住宿“多少钱”进入静态收费筛选和 `asked_attributes`，变成了早餐收费要求。

工具提供 401 房间 300 元，合法审核知识提供早餐 7:30。提问：

```text
10月10日401房间住一晚多少钱和早餐几点开始
```

最终发件箱仍是“当前审核资料尚未确认早餐信息”加 300 元参考价；分项为 `missing:早餐费用`、`missing:早餐时间`、`grounded:search_reference_price`，缺口 `knowledge_missing`。**仅把“和”换成逗号**，就正确发送“早餐7:30开始”加 300 元。

**建议。** 静态取证以剥离已由工具负责的请求后的剩余语义为输入；原小句和区间仍负责日期、房号与缺口身份。调整范围守卫与语义取证的先后顺序，保留原始区间映射。修复应覆盖任意同句组合，而不是把“和”加入切分词来补这一个样本。

**牵连检查。** 保留价格、早餐时间两项，不误要求早餐收费；反向对照“早餐是否免费/是否含早餐”仍必须有收费/包含依据。错误 `knowledge_missing` 还可能在模型选中该缺口时触发无必要的管家核实，应断言已有资料的该子问不产生转交候选。

### I70-R5：ISO 日期年份混入房号

**依据。** [item_coverage.py:94](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:94) `_ROOM_NUMBER` 排除了单位，却没有排除日期区间；[clause_rooms:222](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:222) 直接返回全部匹配。

`2026-10-10 401房间住一晚有房吗` 的房号被识别为 `('2026', '401')`。同样的合法规划和 401 可订工具证据，最终发件箱同时包含“审核资料尚未确认”“可订：401”“这次没有查到对应的房态”，产生 `scope_unbound` 和 `tool_uncovered`。改为“10月10日401房间住一晚有房吗”则只发可订结果。

**建议。** 共享房号识别排除已有 `_ABSOLUTE_DATE` 命中区间内的数字；保留真正四位房号，避免把 2026 一律列为禁用房号。相同日期识别结果供房号提取、范围核对和剩余处理使用，防止三处各自猜测。

**牵连检查。** 保留 `room 401`、真正四位房号、220V、金额等既有正反样本；ISO 日期与中文日期应有相同事实与缺口判定。此例正说明增加单位词表仍不足以保证语义类型正确。

### I70-R6：SQLite 旧审核越过已提交清空

**依据。** [KnowledgeAdminService.convert_referral:699](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:699) 读取后检查 DRAFTED 与页面代次，随后等待范围校验、建知识，再于 [knowledge.py:724](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:724) 直接赋值 CONVERTED。`get(lock=True)` 使用 [knowledge_referrals.py:159](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/knowledge_referrals.py:159) 的 `FOR UPDATE`，SQLite 不提供该行锁。入库写回没有状态/代次条件。

使用项目 `create_engine`、临时 SQLite、真实 `clear_test_customer_data`：旧页面代次为 1；转换完成 `scope=property, property_id=401` 的真实房源读取后暂停；另一会话清空并提交；恢复旧转换：

```text
AFTER_CLEAR cleared 2 None
PROPERTY_SCOPE_STALE_CONVERT converted 2 401 True
```

清空已经换代并清掉问题，旧转换仍新建启用知识、把记录覆盖为已入库。这是确定性交错，不是靠睡眠碰运气。PostgreSQL 行锁路径没有被此反例证伪。“清空不删先前已入库知识”的规则不允许旧代次在清空后才创建知识。

**建议。** 在同一入库事务里用 `WHERE status=DRAFTED AND generation=页面代次` 的条件 UPDATE 原子取得转换资格，`rowcount=0` 拒绝并回滚。状态、知识编号、知识条目和审计仍同事务提交；保留 PG 行锁。沿用仓储 `finish_draft` 已有的条件写回思路，不新增独立锁服务。

**牵连检查。** 对 `start_referral_draft`、`close_referral` 同类读后写转换一并核查，保证终态不会被 SQLite 过期操作复活。本报告只动态确认入库这一个反例，不把其他路径的推演记作新增发现。最小回归应有清空先提交→旧入库拒绝，以及入库先提交→清空保留已有知识两个顺序；PG 并发两次入库仍只成功一次。

### I70-R7：截断列表造成待处理记录不可达

**依据。** [SQLAlchemyKnowledgeReferralRepository.list_recent:163](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/knowledge_referrals.py:163) 注释说“开放状态在前”，实际仅 `created_at DESC, id DESC LIMIT 100`。[referral_index:55](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge_referrals.py:55) 固定取 100，[referrals.html](/Volumes/02/code/homestay-bot/src/homestay_bot/templates/knowledge/referrals.html) 没有分页或筛选入口。

真实仓储放入 1 条最旧 PENDING 和 100 条较新 CLOSED，结果为：

```text
LIST 100 open_count 1 open_visible 0
```

后台显示一条待处理，默认列表却找不到该条，无法点开整理、关闭或入库。仅将开放状态排在前面不能处理超过 100 条开放记录。

**建议。** 复用知识后台已有的有界分页与稳定排序模式，使全部记录可达；开放优先或待处理筛选可以配合现有业务视图，先保证可达性。统计范围与页面语义一致，保持管理员鉴权与手机布局。

**验证。** 覆盖“1 条旧开放 + 100 条新终态”和“101 条开放”两种集合，浏览器能通过分页/筛选找到并打开最旧记录；分页每页保持有限查询量。不要将 LIMIT 改成无限量加载。

### I70-R8：启用拒绝的页面回跳丢失

**依据。** [KnowledgeAdminService.set_enabled:369](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:369) 正确新增占位符校验，抛 `KnowledgeFormError`。[toggle_knowledge:1492](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:1492) 没有设置异常回跳；[handle_operation_refused:142](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/page_errors.py:142) 取空 `return_to`，落到默认任务中心。

真实服务加临时 SQLite，通过实际知识路由、管理员会话、CSRF 与全局错误处理器请求旧占位符条目的启用。页面提交带 `return_to=/employee/knowledge?enabled=false#knowledge-entries`：

```text
ENABLE text/html 303 /employee/tasks
ENABLE application/json 422 问答中仍有「【待管理员确认】」……
```

数据库仍是停用，拦截正确；页面错误跳转是新拒绝路径遗漏。测试装配只对列表和认证使用现有夹具，启用校验与错误处理用真实实现。

**建议。** 启停路由捕获该领域拒绝，为异常设置经 `safe_return_path` 校验、以知识页为兜底的来源地址，再交已有全局处理器；保留筛选和锚点。API 仍返回 422，停用仍无占位符阻碍，CSRF 仍只消费一次。

## 4. 已认可的实现与仍需保留的边界

以下有源码/相关验证依据，不要求回退已经成立的改动：

- **占位符共享校验成立。** `validate_knowledge_content`/`ADMIN_CONFIRMATION_PLACEHOLDER` 由新增、编辑、候选转换、启用及资料核实入库复用；停用脏条目仍允许。四个 QA 字段的字面拦截是声明范围，不要求扩张成任意“待确认”语言识别。[knowledge_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py) `validate_knowledge_content`、[knowledge.py](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py) `_validate_content`。
- **此前四类 Bearer 与问号问题已闭合。** `redact_text` 不再把普通多行消息整体当 URL；访问日志继续保留五元协议。R2 是新的兄弟格式反例。[logging.py](/Volumes/02/code/homestay-bot/src/homestay_bot/logging.py) `redact_text`/`SensitiveDataFilter`。
- **卡片最小化与两阶段安排可接受。** 资料核实卡片固定客人行、净化问题、房间及对话链接，免取 CRM 名称与偏好；缺失/清空/错会话拒接入，关闭或入库后仍可交还。数据库取数失败在平台动作前传播，动作后的纯组卡/更新失败不会撤销动作。代价“卡片缺少客人备注”在交接中已披露。[application.py](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py) `_referral_card_for`/`_send_referral_card`、[human_session.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/human_session.py) `handle_card_action`。
- **补原生接管复核合理。** `_discard_stale_final` 取会话锁后检查原生人工状态，和最终入口判据一致；管家已原生接入时丢弃机器人的旧普通回复符合人工接待静默合同。[conversation_service.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py) `_discard_stale_final`。
- **顺序恢复与等待后清空判据有覆盖。** `_run_deferred_source` 统一查来源收据/清空；合并正常排出最终任务不写收据，业务终结才写。软规划和主模型等待后再读数据库当前来源；发送端在 `stale_exempt` 前复核来源清空。[application.py:920](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:920) `_run_deferred_source`、`_guest_reply_is_stale`；[conversations.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/conversations.py) `source_cleared`。
- **收据的并行上限应准确陈述。** 当前装配的模型队列单循环与 `claim_next` 路径支持顺序重放防护。`record_receipt` 先查已存在就直接返回，不能独立证明任意多实例并行“副作用 exactly once”；本轮没有复现当前单实例下的重复副作用，不以该推演新增阻断问题。后续若改变 worker 实例数，应单独验证并行入口。[application.py:5214](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py:5214) `application_lifespan`、[knowledge_referrals.py:72](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/knowledge_referrals.py:72) `record_receipt`。
- **草稿条件回写与耗尽落点成立。** `finish_draft` 按 DRAFTING+代次更新，第二次结果与旧代次不能覆盖；正常失败回调、超时耗尽及直接耗尽路径都有条件失败落点。R6 是管理员入库没有沿用同一条件写回边界。[knowledge_referrals.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/knowledge_referrals.py) `finish_draft`、[jobs.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py) `_fail_stale_referral_drafts`、[application.py](/Volumes/02/code/homestay-bot/src/homestay_bot/application.py) `_fail_exhausted_referral_draft`。
- **清空锁顺序与字段范围合理。** 客户→会话升序→消息/转交，与处理侧会话锁边界协调；清归一化问题和冻结编号、保留来源编号/收据、保留已有知识符合声明。[customers.py](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/customers.py) `clear_test_customer_data`。SQLite R6 需要另补条件更新，不能由 PG 锁结论代替。
- **同轮复用已有收紧边界。** `_shared_grounded` 只收本项核验通过的全店无条件知识，房间与附加条件条目不跨项，接收项继续核验有效期。模型标注否决仍依赖可识别主题和收费/时间属性，不能证明一般语义冲突。不同对象两条钟点被保守否决是交接已披露的取舍。[deepseek_client.py:1731](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1731) `_shared_grounded`、[knowledge_evidence_policy.py](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py) `verify_selected_evidence`。

应把实施交接 §7.1、§7.2 的两个后续决定及服务收费追问修正回填到当前 Spec/实施追加，明确与 v10 原文的差异，避免下一轮按过时合同重写正确代码。该文档同步不等于扩大业务功能。

## 5. 泛用性判断与一次修复的组织方式

“让模型做选择题”仍是合理方向：模型负责选意图、证据编号和缺口，本地验证事实范围及真实动作结果。本轮反例表明，**选择之后的确定性验证也会被词面耦合破坏**：ISO 年份被当房号、房价词变成早餐属性、一个粗项的日期替代子问日期。把样例加入提示词或不断增加关键词，不能修复这些共享输入边界。

建议将 R3/R4/R5 合成一个完整修复单元，复用现有 `Clause`、范围解析和 `ClauseNeeds`：

1. 原始小句与区间负责身份、房号和日期绑定；已知日期区间不参与房号提取。
2. 先核对范围，再用工具已经覆盖的操作锚点得到剩余语义；剩余语义用于静态属性与知识取证，原文仍用于排除词与范围证明。
3. 正常、回退和澄清走同一判定；只有真实资料缺失进入转交，范围未绑定不能转交。
4. 同一输入经过这几步后，断言最终客人正文、真实发件箱路由、逐项证据日期和缺口原因一起一致。

另外三个修复单元：R1/R2 各自在既有文本边界补全输入形态；R6 让管理员状态转移落实数据库条件；R7/R8 完成已有后台流程的可达性和失败回跳。它们可以统一规划与综合验收，不能用一句总括式“安全已修复”替代各自边界。

为避免反复往返，验证使用**成对变体**而不是固定文案快照：相同意义的“逗号/和”“中文日期/ISO 日期”应保留相同事实；不同意义的“同日/异日”“早餐时间/早餐收费”“密码提问/密码赋值”“清空先提交/入库先提交”应产生不同结果。已公开探针加入回归后不再当独立留出集。原 D1 改写集仍需单独交付，现有 154 场景门禁不证明任意客户改写都可识别。

## 6. 验证证据与可复核入口

三个脚本已留在 [本次证据目录](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08-fix2-implementation-review-evidence)。**脚本退出 0 表示当前反例成立，不表示实现通过。** 修复时应转成正确行为回归，不能修改实现去维持缺陷脚本通过。

```sh
cd '/Volumes/02/code/homestay-bot'
PYTHONPATH=src:.:tests:tests/integration PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-10-08-fix2-implementation-review-evidence/coverage_probe.py
PYTHONPATH=src:.:tests:tests/integration PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-10-08-fix2-implementation-review-evidence/referral_probe.py
PYTHONPATH=src:.:tests:tests/integration PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-10-08-fix2-implementation-review-evidence/logging_and_enable_probe.py
```

| 证据 | 结果与证明范围 |
| --- | --- |
| Codex 主代理：三个离线复现脚本 | 均退出 0；R1–R8 反例成立。coverage 脚本包含正常/回退、实际会话入口、事务出站及三个正对照；referral 用真实临时仓储、真实草稿生成器配假 SDK；日志脚本捕获实际 handler 与 HTTP 响应 |
| Codex：日志与知识路由相关验证 | `tests/unit/test_log_redaction.py` + `tests/integration/test_knowledge_routes.py`：49 passed、1 弃用警告、8.09s。本轮连续审查早先执行，源码与最终候选一致，结果仍有效 |
| Codex 主代理：卡片两阶段 | `tests/integration/test_card_action_two_phase.py`：13 passed、9.09s |
| Codex 并行审查：逐项覆盖 | `tests/unit/test_item_coverage.py` + `tests/unit/test_reply_fix2_items.py`：70 passed、1.02s |
| Codex 并行审查：草稿/入库/敏感与抑制 | `tests/unit/test_sensitive_text.py` + `tests/integration/test_knowledge_referrals.py`，`-k 'sensitive or draft or convert or closed_question'`：36 passed、20 deselected、6.95s |
| Codex：新增证据脚本静态检查 | 三个脚本 Ruff 通过；业务工作区 `git diff --check` 通过。仅整理导入和等价取消清理后复用实际反例结果 |
| Claude 返回记录，未独立重跑 | 全量 2855 passed / 15 skipped；隔离 PG 16、浏览器 7、迁移升降与 12 项变异；rc3 模型门禁首轮 138/154、不退步通过、16 已知未通过 |

相关用例全绿仍没有捕获这些输入/交错，已以独立反例证明缺口。因此不能用通过数量抵消发现，也不能将不同批次用例数简单相加为全量验收。

**后续最小验收。** 优先把八项反例与必要正对照加入对应既有测试。覆盖修复必须断言最终发件箱正文/路由及证据日期；安全修复断言实际模型载荷与真实日志输出；状态修复用目标方言的真实交错；后台修复用真实服务和浏览器验证可达/回跳。完成相关验证后，按最终改动重新判断全量与模型门禁：本候选包含装配改动，现有全量只能证明当前未修版本；主回复共享判定变化会使 rc3 回复门禁证据失效，后续获授权时按项目规则跑 all。CI、真实模型门禁和外部收件各自独立，不在此次审查中自动执行。

## 7. 当前摘要、未覆盖与交接边界

| 当前文件 | SHA-256 |
| --- | --- |
| src/homestay_bot/integrations/deepseek_client.py | 6933252064e770e7af972f27aae7ddc554059f7f9beb71a689f5e67a221ca0d9 |
| src/homestay_bot/services/item_coverage.py | 0c7e71eb1f777200ae47fce0e6a3a21fa1e4e1086c6ab9a9a9b6ce2cfcdf8880 |
| src/homestay_bot/services/sensitive_text.py | 10eeb4f2989583da649d6c422e050ea464014030b513ee6f4a78b6fd387a1d00 |
| src/homestay_bot/logging.py | a227341b7efeff8e4b92f7561da9dcf43bb7b9c87fa8f5b31394380f51745645 |
| src/homestay_bot/routes/knowledge.py | f31ccf0e31f75c52218dafc99b7e33ffd012e031a62037ed46332c5f3edbfb53 |
| src/homestay_bot/repositories/knowledge_referrals.py | 52973d367393a367076b791443640ba8d78238406762a98142248ed3f8f0e3b1 |
| src/homestay_bot/services/knowledge_referral_service.py | fad79f88f6654adab4a74c6da23f15ee20204b6669e5783f809bbac07cf27110 |
| src/homestay_bot/application.py | 996ccb19d3fbe5752f869fb89aa671fa40d859ab76d432bf39c41f3012b11672 |
| src/homestay_bot/services/human_session.py | 7e650d340f4a118b8668630d90aea2c4fbd2c12374e96a6c267dd1b5646708c4 |
| src/homestay_bot/repositories/customers.py | 5acd6bcebb49cd7b63edf9a8f22b579ce82c3a88b56b1679f6a7307baef48641 |

本次未独立重跑 PostgreSQL、浏览器全套、全量业务测试、真实模型门禁或迁移；未检查服务器、当前容器、生产库和真实收件。作者报告的“83 条启用知识无字面占位符”和“Docker 当前无日志滚动”是其现场核对记录，本报告没有重新获取生产证据。后者应在后续获授权发布时处理运维记录，不据此暗改 compose。INFO 日志已有输出证据，长期容量与滚动属于另一个现场事实。

最重要的剩余边界：草稿引用只证明出自输入，答案是否回答对应问题、是否只适用某客人仍靠管理员核对；模型标注冲突检查有已声明的主题/属性上限；完整泛用性需要独立改写集；事务发件箱登记不等于平台受理，更不等于客人实际收件。

Claude 接手时先按本报告当前字节核对八项反例，将上述共享根因与最小验收整理为一次修复计划/Spec 修订，再依项目确认与开始门禁实施。保持既有改动和保护文件边界。**本报告是审查与建议，不授权业务修改、提交、推送、部署、真实调用或生产写入。**
