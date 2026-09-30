# v1.55–v1.61 审查问题修复 Spec · Codex → Claude

日期：2026-09-30。状态：**已按 §8 修订实施（Claude，2026-09-30）。** 原草案正文保留作审查出处；实施与原草案不一致之处以 §8 为准。

本轮用户要求：「审查claude最近的改动」「1.55开始审查吧」「写修复spec准备交接给claude」。本文件是本批修复的唯一 Spec 与交接入口；当前授权覆盖审查和文档编写，不包含实现、提交、推送、部署或真实接口操作。

## 1. 接手基线与确认顺序

- 审查范围包含 v1.55.0，Git 范围为 `af1f595^..c23d822`；编写前重新核对 HEAD 为 `c23d822`，`pyproject.toml` 版本为 1.61.0，工作区无已有未提交改动。这是本次交接的源码快照，不代表当前生产健康。
- 接手先核对 Git、相关源码与本文件；若源码已变化，重验受影响结论，不照抄旧行号或历史测试数字。
- 按项目 `AGENTS.md` 依次确认：§2 现状证据 → §3 功能范围 → §4 风险与决策。完整 Spec 确认后，只有收到用户明确的「开始」或同等明确实施指令才编码。已经取得的本轮确认无需重复索要。
- 实施获准后才在 `tasks/todo.md` 记录三个工作包的进展；当前不改实现、测试、版本号、更新日志或发布记录。
- 不读取、摄入、暂存或提交未跟踪的 `YuMi民宿AI项目总结.txt`；只使用合成样本，不在文档、测试或日志中记录真实客户信息、凭据与服务器地址。

设计依据按需核对：`YuMi民宿AI开发经验与防回归手册.md` 的「异步时序和消息边界」「轮询、限流和平台状态」「HUMAN_ACTIVE 不是全局禁答开关」「模型职责边界」；历史意图见 `docs/releases/1.57.0.md`、`1.58.0.md`、`1.58.3.md`、`1.60.0.md`、`1.60.1.md`、`1.61.0.md`。历史文档不替代当前代码。

## 2. 已核实现状与复现证据

下表的完整路径相对于仓库根目录；省略公共前缀的 `services/`、`repositories/`、`integrations/`、`worker.py`、`application.py` 均指 `src/homestay_bot/` 下对应文件。行号是 `c23d822` 的定位提示，符号是接手后的查找依据。

| 编号 | 级别 | 当前代码与根因 | 离线反例和影响 |
| --- | --- | --- | --- |
| H1 | P1 | `src/homestay_bot/services/conversation_service.py::ConversationService.handle_message` 员工分支（796 行）把处理时刻传给 `accept_handoff`；`repositories/operations.py::accepted_at` 读取该审计的 `created_at`；`services/human_session.py::on_servicer_ended`（207 行）以此过滤结束事件 | 员工在 t1 发言、t2 结束，t3 才补拉，且 t1 < t2 < t3。使用真实服务、仓储与新事务复现后，平台桩为 state=4，本地仍为 `human_active`、`native_session_active=True`，结束语发送数为 0。客人随后被原生人工判据静默；高风险接管不能依靠空闲巡检恢复。`worker.py::sync_page` 已保留消息和事件发生时间，错误在消费端时间含义 |
| H2 | P1 | `src/homestay_bot/repositories/operations.py::release_conversation`（1588 行）用 `kf-end:<handoff.id>` 去重，却只把 `conversation_id` 放进载荷；`services/human_session.py::end_native_session`（168 行）只检查平台是否 state=3；`application.py::build_handoff_handlers` 的结束处理器只传会话编号 | 接入 A → 交还并登记 A 的任务 → 任务延迟/重试 → 再接入 B → 执行 A 的旧任务。通过真实卡片接入及独立事务复现，平台由 3 变 4，本地仍 `human_active/native=True`。随后若消费平台结束事件，还可能进一步把 B 误交还。任务去重不等于绑定当前接管 |
| R1 | P2 | `src/homestay_bot/integrations/deepseek_client.py::_compose_with_live_results`（1195 行）的遗漏检查只针对部分带单位数字；正常分支不保留 `query_failed`；异常分支按整段删除含数字文本 | 经真实 `respond()` 入口与固定搜索/模型桩复现：查到「黄鹤楼今天开放时间为08:30～18:00」，模型只答东湖玩法，最终没有开放时间；天气查询失败时，最终仅剩玩法，没有失败说明。另一个反例中模型把玩法与错误温度写在同一段，数字回退把玩法一起删除 |
| R2 | P2 | 同文件 `_compose_with_live_results`（1187–1200 行）把全部搜索正文数字合成集合，用包含关系放行，丢失区间顺序、单位、次数及日期/实体绑定 | 搜索原文「明天武汉阴有阵雨，15～21℃」，模型改成「21～15℃」，经过 `respond()` 后仍原样发出，并附「查到的最新预报」时效说明。现有单测只覆盖新增不同数字，没有覆盖相同数字被重排 |
| M1 | P2 | `src/homestay_bot/repositories/context.py::handover_brief`（438 行）另查 `ACTIVE` 偏好，没有检查 `review_at/expires_at`；已有 `_recall_memories`（839–841 行）具备期限和核验过滤 | 通过 `save_short_summary` 正常保存已核验明示事实；在到期前执行维护，在读取前跨过复核期限。状态合法仍为 ACTIVE，简报返回「客人对海鲜过敏」，同一客户的 `load_model_context().memories` 却为空。卡片更新、交还通知直接读简报；首次卡片维护失败后的回退也不能保证先治理 |

H1/H2 用临时 SQLite、真实服务/仓储和现有企业微信状态桩复现；R1/R2 用现有 `ChatClientStub` 与固定旅游搜索桩，未调用模型；M1 从正常保存路径生成合成记忆，未把人工构造的未核验 ACTIVE 当作可达缺陷。

2026-09-30 审查中的限定测试三组分别为 320、219、37 项通过，存在重叠，不能相加当成唯一用例总数。第一组有 1 条 Alembic 配置弃用警告。现有测试通过仍缺上述反例覆盖；本次写 Spec 未重跑测试。未做全量、隔离 PostgreSQL、真实模型、生产页面或企业微信实际收件验收。

2026-09-30 更正发布流程结论：`docs/releases/1.61.0.md` 的验证记录为本地全量 `2277 passed / 27 skipped`，满足用户确认的 `AGENTS.md` 本地全量验证路径，无须额外等待 CI；撤回“1.61.0 未等 CI 就部署不合规”的判断。本地最终全量通过且证据仍有效时可直接部署；未跑本地全量时才必须等待本次推送 CI 全量通过。测试门禁合规不替代生产运行态或真实收件验收，也不授予后续部署权限。

## 3. 功能范围与推荐方案

三个工作包分别验收，H1/H2 在同一人工会话边界修复，R1/R2 在同一分项事实边界修复。M1 只复用已有有效记忆读取。统一在本 Spec 下交接，不建设跨模块的通用状态机或校验框架。

### 3.1 工作包 H：事件时间与接管批次

**H1 必须达成：** 同步/补拉中正常的「员工发言后结束」能够交还；本次接入前的旧结束事件仍不能交还新接入。

推荐在现有接管审计 `details` 中记录平台接入发生时间，不新增数据表：

1. 员工消息补记接入时，使用 `IncomingMessage.sent_at` 作为发生时间，统一 UTC；按钮接入没有对应员工消息时，以本次平台接入成功的本地时刻记录。
2. 保留审计 `created_at` 的写入时间及现有空闲巡检口径。不能只把 `accept_handoff(now=...)` 换成旧消息时间，顺带改变审计/空闲交还行为。
3. `accepted_at()` 优先读取明确记录的发生时间；旧审计未带字段时兼容读取原 `created_at`。旧结束事件的秒级比较仍保留。
4. 未知/损坏的时间不得推成「当前时间」再错误过滤正常事件；保守处理并留下不含正文的诊断。具体字段解析沿用项目日期处理方式。
5. 复核旧员工消息与最新接管/交还的顺序，不能让早于新接管的旧消息生成一条覆盖新接管的审计。保持高风险原因以及普通 HUMAN_ACTIVE 的既有行为。

**H2 必须达成：** 一次交还只允许结束它对应的原生接待；旧任务执行、重试或重复消费均不能操作新接管。

1. 新结束任务载荷带对应 `handoff_id`，继续使用原审计编号去重，不增加第二套编号。
2. 结束处理器在调用平台前复核：会话仍为该次交还对应的状态，最新接管编号与任务一致，没有后续重新接入。发现新接入则跳过，不发结束语，也不修改新接管审计。
3. `HumanSessionService._accept`、结束任务与交还入口必须检查同一会话并发边界。仅给结束任务加锁，而接入 API 调用仍发生在取得锁之前，会留下“复核后又接入”的窗口。复用现有会话行锁，在必要的有限时长平台调用与本地状态写入之间串行；不把摘要或主模型调用放进锁。
4. 同批次重复结束维持无重复外部副作用；平台已非人工接待时不重复结束。连接尚未建立的失败沿用有限重试；超时/结果未知不盲目重放外部状态变更。
5. 卡片 `_release` 的“本地已交还但平台仍挂着”补偿也要走同一批次复核，不能保留无条件 `end_native_session(conversation_id)` 旁路。
6. 旧载荷兼容见 §4 D2；不能只给新任务加字段而让存量任务继续无条件结束。

修改定位：

- `src/homestay_bot/services/conversation_service.py::handle_message`：只改员工补记接入的时间/批次边界。
- `src/homestay_bot/repositories/operations.py::accept_handoff/accepted_at/release_conversation`：保存发生时间、读取兼容、登记带批次任务及复核。
- `src/homestay_bot/services/human_session.py::_accept/_release/end_native_session/on_servicer_ended`：统一平台状态变更的串行与批次判定。
- `src/homestay_bot/application.py::build_handoff_handlers`：迁移结束任务调用方；按实际需要处理旧载荷诊断。
- `tests/integration/test_human_session.py`：真实仓储和事务复现；`tests/unit/test_conversation_service.py`、`tests/unit/test_application.py`：更新真实契约受影响的调用方。
- `worker.py` 的消息游标实现不是本包重写对象；只有旧任务兼容确实需要时，提出具体差异并更新 Spec 后再触及它。

### 3.2 工作包 R：实时分项完整性与事实校验

目标是保留“先查询，再由主模型自然整合”的既定体验；同时每项成功结果或失败状态都能追溯。不能继续用“数字相交”推断问题已答完。

推荐复用已有 `AssistantDecision.reply_parts` 和 `ReplyPart`，仅在当前混合联网分支接收模型的分项候选：

1. 输入仍带完整问题和本地 `live_search_results`；要求模型按本地查询分组返回候选分项，其他问题独立成项。实时项的 `question` 对应本地查询组，其他问题项对应本地 `remaining_question`。拒绝未知项、重复项及把多组事实混成一个无法校验的候选。
2. 模型分项只是候选。候选只提供 `question/text`；`status/evidence` 由本地查询重建后才构造 `ReplyPart`。当前 `assistant_decision_schema()` 没有向模型声明 `reply_parts`，现有 `ReplyPart.status` 又是本地必填字段；必须明确处理模型输出到内部模型的映射，不能直接对缺少 status 的候选做 `AssistantDecision.model_validate_json` 而让整轮失败。保留 `_validate_decision()` 对一般路径清空模型 `reply_parts` 的保护，不全局开放模型事实通道；其他问题候选仍走既有事实、安全与承诺过滤。
3. 对每个成功查询项，先验证候选是否保持完整事实，再允许采用改写。优先复用 `integrations/deepseek_delivery_rewriter.py::DeepSeekDeliveryRewriter._validate_facts` 的纯函数校验能力；它已有数字、日期、天气、条件与实体绑定检查，但须用本批反例验证适用边界，不能仅凭函数名认定覆盖充分。不能把夹杂其他推荐的整条正文拿来与单项原文比较，也不能为通过新用例放宽原安全改写契约。
4. 数字校验必须保护完整区间、单位、日期与地点归属，以及营业时间、无数字的闭馆/活动取消信息；不是只补一个温度正则。无法证明等价、遗漏候选或协议无效时，该项回退本次查询原文。
5. `query_failed` 必须保留该问题的本地失败提示，不能改标 grounded；一组失败不得删掉另一组成功回答或模型对其他问题的有效回答。
6. 回退按分项执行，不能按整段删除含数字正文。最终沿用 `compose_reply_parts/prepare_planned_reply` 和客人文本策略；不重复追加原文、温度、失败提示或时效说明。时效说明最多一次，只为实际成功的查询背书。
7. 明确保留客人要求的目标日期、地点及其他问题；无日期天气维持当前已确认天数口径。Hostex 房态/价格工具分项、审核知识固定答案、服务动作与高风险回复不改合同。
8. 不增加额外主模型轮次、查询重试、依赖或通用语义判定框架。现有校验若保守拒绝合法转述，先回退原文；反复误拒且有独立样本时才另评估能力扩展。

修改定位：

- `src/homestay_bot/integrations/deepseek_client.py::respond/_compose_with_live_results/_validate_decision/_build_context_envelope/assistant_decision_schema` 及 `_LIVE_RESULTS_RULE_ZH/_EN`：只在混合联网请求声明分项候选、做边界映射和局部校验/回退；其余请求保留原必填字段与合同。删掉本次被替代的数字集合与整段删除逻辑。
- `src/homestay_bot/services/reply_plan.py::ReplyPart/compose_reply_parts/prepare_planned_reply`：优先原样复用，不为本包重定义模型。
- `src/homestay_bot/integrations/deepseek_delivery_rewriter.py::_validate_facts`：先核对复用边界；若不需要修改就保持原文件。确需抽出双方共用的纯函数时，只迁移这两个真实调用方，并保留原校验回归。
- `tests/unit/test_deepseek_client.py`：通过 `respond()` 注入合成候选/查询结果，断言最终行为与分项状态。
- `tests/unit/test_deepseek_delivery_rewriter.py`：仅在该校验或其依赖变化时运行。
- `tests/fixtures/guest_reply_scenarios.json`：实施时补入混合开放时间/无数字状态场景，使用虚构资料；确定性搜索失败与温度重排优先留在离线替身用例，不依赖真实搜索碰巧出错。

### 3.3 工作包 M：简报复用有效记忆

`src/homestay_bot/repositories/context.py::handover_brief` 已经为订单调用 `load_model_context(customer_id)`，推荐在同次调用中同时取 `memories`，抽取最多三条 `statement` 为偏好，删除另查 ACTIVE 的 SQL。

- 统一沿用 `_recall_memories` 的核验、期限、敏感内容和动态事实过滤，不另建一个员工偏好过滤器。
- `query` 为空，避免用某条新问题的相关性错误隐藏员工应看到的有效偏好；保持现有最多三条、无偏好为空、多个当前订单不猜房间的合同。
- 本次不修改记忆晋升、存量迁移、保留期限、客户详情页签或复核操作。读取不触发模型、写库或额外治理任务。
- 在 `tests/integration/test_context_repository.py` 经正常保存路径建立已核验合成记忆，并覆盖维护之间跨期限的读取。

## 4. 风险与待确认决策

本节是草案决策，不表示用户已接受。

| 编号 | 推荐决策 | 取舍与必须保留的边界 |
| --- | --- | --- |
| D1 | H1 在接管审计 details 分开记录发生时间，保留 created_at/空闲计时 | 比直接替换 now 多一个内部字段，但避免顺手改变空闲交还；不新增表或配置。旧审计保持已有读法，不倒推精确平台时间 |
| D2 | 新结束任务绑定 handoff_id；旧任务只能从其自身原去重编号等可证实来源恢复批次 | 不能把最新接管编号填给旧任务。若执行边界无法可靠取得原编号，推荐阻止该旧任务自动结束、以现有任务失败机制留下可诊断结果；后续在获得授权后盘点并逐项安排补偿。该策略防误结束，但可能保留旧平台人工状态，发布前必须明确旧任务数量和处理方案，不能悄悄标记成功 |
| D3 | R1/R2 采用现有 ReplyPart 候选＋分项校验和原文回退 | 可以因保守回退使表达略少融合，但有效问题不丢、错误事实不出站。维持当前单段文本再扩充词表无法证明每项完整；全面退回“实时原文＋其他正文”虽简单，却改变已选择的整合体验，本草案不默认采用 |
| D4 | M1 直接复用 load_model_context 的有效 memories | 排序随既有召回规则，不再另按 updated_at 排员工偏好；保留三条上限。若必须保留旧排序，先说明产品理由再确认，不能为了排序复制治理规则 |
| D5 | 版本/发布在实施验收后另行确定 | 当前不升级版本、不指定部署日程。本地最终全量通过且证据仍有效时可直接部署，无须额外等待 CI；未跑本地全量时才必须等待本次推送 CI 全量通过。分别获得真实模型、企业微信验收与生产变更的当前授权 |

人工边界特别注意：手册的一般 HUMAN_ACTIVE 规则与原生人工接待静默是两种状态；当前 `native_session_active()` 及 v1.58.0 已确认的原生平台约束保留。不得为本次修复把所有 HUMAN_ACTIVE 变成全局禁答，也不得恢复原生人工接待期间的机器人发送。

并发验证风险：SQLite 只能证明事务恢复与时间/批次判定，不能证明 PostgreSQL 行锁。H 包若调整平台调用和行锁顺序，必须做隔离 PostgreSQL 并发验收；不能用同一 session 中两次顺序调用代替竞争。

## 5. 验收矩阵

新测试只覆盖真实缺陷和关键反向边界，不逐句断言提示词、脚本或样式。

| 工作包 | 合成输入/操作 | 必须断言的行为 | 测试位置 |
| --- | --- | --- | --- |
| H1 | t1 员工发言，t2 结束，t3 才消费；每个处理入口重建 session，随后处理新客人消息 | 正常结束后 BOT_ACTIVE、native=False；新客人重新进入正常回复入口；结束提示不重复 | `tests/integration/test_human_session.py`；必要时复用真实 `WeComSyncJobHandler.sync_page` 串起同页消息和事件 |
| H1 反向 | 本次接入前旧结束事件、缺少事件时间、按钮接入及旧格式审计 | 新接入不被旧事件交还；兼容行为有确定断言；空闲计时和高风险原因保留 | 同文件及 `tests/unit/test_conversation_service.py` |
| H2 | A 接入/交还后任务延迟，B 重新接入，再执行 A 任务；跨事务 | 平台仍接待 B；B 的模式、接管审计及消息不被旧任务修改，不发“已交还” | `tests/integration/test_human_session.py` |
| H2 反向/失败 | 同批次重复任务、平台已结束、旧载荷缺编号、连接未建立与结果未知 | 重复不产生外部副作用；未知批次不误结束；失败状态可诊断且无无界重试 | 同文件及 `tests/unit/test_application.py` 的处理器装配 |
| H2 并发 | 隔离 PostgreSQL 两个 session，用事件屏障控制重新接入与结束的交错 | 无“批次复核之后 B 接入又被 A 结束”的窗口；无死锁；不依赖任意 sleep | `tests/integration/test_guest_reply_postgresql.py` 复用 `test_retention_postgresql.py::pg_engine` 与已有 `test_release_and_staff_message_serialize_on_conversation` 的装配；只用隔离本地库 |
| R1 | 玩法＋开放时间 08:30～18:00；遗漏候选；成功天气＋失败活动；无数字闭馆说明 | 所问各项保留；缺项回退原文；失败状态/提示保留；一项失败不覆盖其他答案 | `tests/unit/test_deepseek_client.py`，通过 `respond()` |
| R2 | 15～21℃ 改成 21～15℃；两天数字交换；时间/单位变化；同段玩法＋错误温度 | 错误候选不进入最终正文；原查询事实恢复，玩法保留；正确等价候选无重复 | 同文件；若共用校验改变，加 `tests/unit/test_deepseek_delivery_rewriter.py` |
| R 反向 | 中文/英文；纯联网；联网＋知识固定答案；联网＋Hostex；全部查询失败；模型协议不完整 | 日期/地点及高风险安全边界保留；来源由本地构造；全失败不声称“查到”；不增加模型调用次数 | 复用 `test_deepseek_client.py` 与 `tests/integration/test_guest_reply_contract.py` 的现有装配 |
| M1 | 正常保存已核验偏好，前一维护尚未到期，读取跨 review_at；再覆盖 expires_at | brief.preferences 与有效召回一致排除过期项，不要求先执行维护 | `tests/integration/test_context_repository.py` |
| M 反向 | 有效偏好、其他客户数据、无偏好、多个当前订单 | 有效偏好保留且最多三条；客户隔离；无数据为空；不猜房间；不改变记忆记录 | 同文件 |

## 6. 实施顺序与验证成本

以下是获得实施授权后的执行顺序，不是本轮已执行清单。

- [ ] 重新核对基线、调用方全集和 §4 决策，完成分段确认；把失效证据明确标记，不继续引用。
- [ ] 先在 H 包添加两个稳定红测，再完成发生时间、接管批次和平台调用串行；验收新旧载荷及并发边界。
- [ ] 在 R 包保留最小缺项/事实变形红测，再实现分项候选和校验；迁移调用方后删除本次替代的数字集合与整段删文逻辑。
- [ ] 在 M 包添加真实保存路径的期限红测，复用现有有效召回；不扩大为客户记忆重做。
- [ ] 合并差异自审与 Ponytail 简化检查；冻结源码后跑一次最低成本充分的最终验证集。
- [ ] 向用户交付：五项对应修复证据、验证/跳过项、旧任务兼容和风险；另列外部验收尚未完成，不自行发布。

受影响离线文件可一次运行，去掉本批实际上未改动且证据仍有效的文件，并说明依据：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q \
  tests/integration/test_human_session.py \
  tests/unit/test_conversation_service.py \
  tests/unit/test_application.py \
  tests/unit/test_deepseek_client.py \
  tests/unit/test_deepseek_delivery_rewriter.py \
  tests/integration/test_guest_reply_contract.py \
  tests/integration/test_context_repository.py
```

预期：本批最小红测在修改前稳定失败、修改后通过，原相关行为不退步；结果数字以实施时真实输出为准，不预填通过数量。仅因文档、交接或任务记录变化，不重跑业务测试。

本批计划触及 `application.py`、`conversation_service.py` 与仓储共用模块，按项目规则在获得提交授权后的提交前，需要一次最终本地全量：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q --tb=short
```

隔离 PostgreSQL 证据单独记录；先阅读 `tests/integration/test_retention_postgresql.py::pg_engine` 的本地库限制并核对连接目标，再运行 `tests/integration/test_guest_reply_postgresql.py`，无有效隔离库时如实记录未验证，不代换成生产连接。R 包修改主模型提示/公共回复流程，真实模型回归必须 `REPLY_GATE_SCOPE=all`，不能用 tourism 范围代替；本文件不授予真实模型调用权限。补入新场景前核对 `scripts/release/reply_gate.sh::REPLY_PATHS`，不得降低必过基线或放宽禁令来伪造修复。源码、测试、依赖、配置、环境或验收目标变化后，仅重跑受影响证据。

若后来获准发布：提交/推送各自前按当前规则做 fresh Ponytail review；写 `CHANGELOG.md` 与对应 `docs/releases/<实际版本>.md`；本地最终全量通过且证据仍有效时可直接部署，无须额外等待 CI，未跑本地全量时才必须等待本次推送 CI 全量通过；按已有流程做可恢复备份与生产操作。真实企业微信验收须确认客人最终收件正文、时间及对应失败/任务关联，平台受理和健康接口不替代收件。

## 7. 可复制给 Claude 的接手 Prompt

```text
请先阅读 docs/specs/2026-09-30_claude-review-fixes-spec.md，并核对项目 AGENTS.md、Git 基线和相关真实调用链。目标是修复 v1.55–v1.61 审查确认的 H1/H2/R1/R2/M1，优先人工会话状态问题，再完成实时分项与有效偏好读取。

当前只交接 Spec 草案：按现状证据、功能点、风险/决策三段与我确认，完整 Spec 确认后等我明确说“开始”再实施；如果我在当前对话已明确确认相应内容，不重复索要。采用已有接管审计编号、ReplyPart、事实校验及记忆召回，避免逐样本补词表或新建通用框架。实施时保留最小红测，覆盖跨事务、旧任务与并发边界；按 Spec 做风险驱动验证。

不得读取受保护的 YuMi民宿AI项目总结.txt，不改无关工作区，不调用真实 DeepSeek/Hostex/企业微信，不发送消息或写生产数据，不自行提交、推送、升级版本或部署。遇到会改变本 Spec 范围或重要决策的事实，先更新并确认 Spec。
```

本轮交接交付物只有此文档；未发送到其他聊天或外部应用，未实施五项修复。

## 8. 2026-09-30 修订与实施记录（Claude）

用户审阅 Claude 对本草案的审查意见后选择「1，直接全部做完」：按下列简化方案修订并实施五项，完成后按项目流程发布。

### 8.1 问题核实

五项均在 `c23d822` 源码中逐条核实成立：H1 `conversation_service.py` 员工分支用处理时刻补记接入；H2 `operations.py::release_conversation` 的结束任务只带会话编号；R1、R2 见 `_compose_with_live_results`；M1 `context.py::handover_brief` 另查 ACTIVE、不看复核与到期。生产上 `wecom_kf_session_end` 任务共 4 个，均为 COMPLETED，没有排队中的旧格式任务。

### 8.2 与草案不同的决策

| 草案 | 修订 | 理由 |
| --- | --- | --- |
| D1：发生时间另存进 details，保留 created_at | 员工发言补记接入时，直接以消息 `sent_at` 作为接入时间 | 对空闲计时的影响只有同步延迟那几秒；少一个字段，也少一套读取兼容 |
| H2 第 3 条：接入、结束、交还共用行锁串行，外加 PostgreSQL 并发验收 | 结束任务带 `handoff_id`，执行前核对最新接管编号，不一致就跳过；不加锁，也不做 PG 并发测试 | 单进程、单值班管家，任务正常几秒内执行完，窗口只在失败重试时出现；锁与并发验收的成本和风险与收益不成比例 |
| D2：旧载荷阻止自动结束，并盘点补偿 | 旧载荷（无 `handoff_id`）按原方式执行 | 生产排队中的旧任务为 0 |
| D3：模型按查询组返回候选 `reply_parts`，逐项用改写器校验 | 维持方案二的单段输出，新增纯函数模块 `services/live_fact_check.py` 做确定性核对 | 不改模型输出协议；核对覆盖草案的全部反例：区间顺序、营业时间、失败说明、按句删除 |
| D5 / §2：「即使本地全量通过也须等 CI」 | 沿用 AGENTS.md 第 35 行现行规则 | 该规则由用户 2026-09-30 制定；1.61.0 本地全量已通过，合规 |

### 8.3 实施内容

- **H1** `conversation_service.py::handle_message`：`accept_handoff(now=message.sent_at)`。
- **H2** `operations.py::release_conversation`：载荷写入 `handoff_id`；新增 `latest_handoff_id`。`human_session.py::end_native_session(handoff_id=…)`：编号不一致就跳过，不调平台、不发结束语。`application.py`：结束任务处理器透传编号。卡片按钮对当前会话的直接交还不带编号，保持原有行为。
- **M1** `context.py::handover_brief`：偏好改取 `load_model_context(customer_id).memories` 的前三条 `statement`，删除另查 ACTIVE 的 SQL。
- **R1/R2** `services/live_fact_check.py::check_integrated_reply`：抽取温度、百分比、价格、时间及区间（保留先后顺序）；写错关键事实的句子删除；各组主要事实（区间原样出现，或两端都出现）缺失的补回原文；只核对查询结果里出现过的单位，避免误伤知识库里的时间和价格。`deepseek_client.py::_compose_with_live_results` 改用它：查询失败的组由系统补固定说明；时效说明只写一次。删除原来的数字集合判断与整段删除。`scripts/release/reply_gate.sh` 的 `REPLY_PATHS` 加入新模块。

### 8.4 测试

- H1：`test_conversation_service.py::test_servicer_reply_marks_native_session_accepted` 断言接入时间等于消息发送时间。
- H2：`test_human_session.py::test_delayed_end_task_of_old_handoff_does_not_end_a_new_handoff`。
- M1：`test_context_repository.py::test_handover_brief_preferences_skip_memories_past_review_or_expiry`。
- R：`tests/unit/test_live_fact_check.py`（区间写反、漏写营业时间、正常转述不误判），以及 `test_deepseek_client.py::test_mixed_question_keeps_failed_notice_and_restores_omitted_facts`（经 `respond()`）。
- 未做：隔离 PostgreSQL 并发验收（见 8.2）。结果数字见对应发布记录。

