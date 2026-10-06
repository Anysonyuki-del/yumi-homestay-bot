# YuMi homestay-bot 项目 AGENTS.md

本文件只约束所在目录树，并在该范围内补充全局 `AGENTS.md`。这是 Codex 开发工作规则，不是民宿产品的客户记忆。

## 受保护内容与事实来源

- 不读取、摄入、暂存、修改或提交未跟踪的 `YuMi民宿AI项目总结.txt`。
- 描述代码现状时，每个结论必须标注文件路径和函数、类或其他符号；当前事实以代码、测试、Git、Alembic 和现场诊断为准，历史 Spec、Plan、Todo Review 和记忆只作线索。
- 任务涉及手册覆盖的业务、架构、发布或验收领域时，按需检索 `YuMi民宿AI开发经验与防回归手册` 的相关章节，不整本注入上下文。

## 渐进式 Spec 与实施门禁

- 纯机械、单点、低风险且不改变业务语义、接口、数据或运行配置的修改可以直接执行并验证。
- 涉及多个依赖步骤、业务规则、公共接口、数据结构、架构、生产行为或高失败代价的任务，必须先完成 Spec；没有 Spec 不写业务代码。
- Spec 分段确认：先给出现状证据，再确认功能点，最后确认风险与决策。完整 Spec 获得确认后，等待用户明确回复“开始”再编码。
- Spec 是实施依据；执行中发现偏差时，先更新并确认 Spec，再修改代码。
- 计划精确到文件路径、函数或符号和验证目标。中等及以上已确认任务使用 `tasks/todo.md` 跟踪；只读审查、纯记忆或简单修改不机械创建任务文档。

## 业务与外部操作边界

- 实时房态和价格只信 Hostex/百居易；未知的专属事实不得编造。
- AI 不替代人工决定退款、赔偿、价格或其他经营事项；敏感操作必须经过确定性安全门。
- 未经当前用户明确授权，不调用真实 DeepSeek、Hostex、企业微信，不创建或修改真实订单，不发送真实客人或员工消息，不进行生产写入。
- 历史授权、测试账号和记忆记录不构成本次外部操作授权。
- 客人可见回复保持亲和、简洁、像民宿管家，同时保留日期、数字、价格、房态、天气证据和安全步骤。危险事件优先给出撤离与紧急处置；退款、投诉等高风险场景不擅自道歉定责或承诺到达、完成时间。

## 验证与生产验收

- 源码态、部署副本态和真实运行态必须分别验证，不能用历史 Review 或本地测试推断当前生产健康。
- 测试、数据库、迁移、容器、应用健康、登录浏览器和真实外部消息分别提供独立证据；健康接口、OpenAPI 或包版本不能替代登录后的页面验收。
- 企业微信接口受理不等于回复成功；只有客人实际收件才算真实消息验收通过。
- 生产变更先做可恢复备份，并分别记录本地源码、GitHub、服务器源码、运行容器和数据库状态。
- 已登录时复用现有 Chrome 标签页做只读生产检查；未经授权不提交表单、不改变生产数据。
- 测试只写有判别力的（2026-09-29 用户要求）：断言行为、真实缺陷或计算得出的约束，不逐句复述提示词、样式或脚本源码；页面行为交给 `tests/browser`，回复措辞交给真实模型回归门禁。页面测试优先用真实服务加临时 SQLite，少用手写替身，替身与生产装配不一致会漏掉真实缺陷。
- 验证范围按全局 AGENTS.md「风险驱动验证」执行（2026-09-30 用户要求精简测试）：默认只跑与改动相关的测试，按行为、调用方和数据流判断范围，已有结果仍覆盖最终改动时直接复用；文档、注释、纯版本号不跑业务测试。本项目的补充：`application.py` 装配、依赖或构建配置有改动，或影响范围无法界定时，提交前本地跑一次全量（约 1 分钟）——测试替身绕过真实装配、「各层写对却点不通」的问题在这里出过多次。
- 部署测试门禁有两条路径，不要求双重必过：本地最终全量通过且证据仍有效时，获准后可直接部署；没跑本地全量时，部署前确认本次推送的 CI 已通过（部署脚本不检查 CI）。
- 真实模型回归门禁只在 `scripts/release/reply_gate.sh` 的 `REPLY_PATHS` 有改动时运行，新增回复模块须补进清单（`tests/unit/test_release_scripts.py` 会核对）。用 `REPLY_GATE_SCOPE` 控制范围：`all`（默认）、`tourism`（只影响联网回答时，自动圈出联网类场景与上次门禁里实际走过联网查询的场景）、逗号分隔的场景编号或类别、`skip`；非全量必须给 `REPLY_GATE_SCOPE_REASON` 并抄进发布记录。问题分类、知识检索、主模型提示或公共回复流程有改动时一律全量：主模型提示影响全部场景，按判断挑场景容易漏（1.60.0 加一条联网规则就让「一句多问」的玩法失效）。
- 测试号真实收发验收同样先判断必要性：改动影响客人消息收发或回复内容、且离线测试和门禁覆盖不到时才做，发布记录写明做或不做的理由。

## 项目记录

- 用户纠正后，仅把已确认、可复现且可泛化的项目教训写入 `tasks/lessons.md`；普通探索噪声和未经验证的假设不得写入。
- 默认不创建或修改 README、设计说明和额外文档；用户明确要求、发布版本维护正式更新日志和 `docs/releases/` 发布变更记录时除外。
- 全局 Git、Ponytail、中文注释、风险驱动验证和发布日志门禁继续生效。

<!-- aoci:begin -->
## AOCI 仓库认知

AOCI 为本仓库维护一个稳定、可版本化、可增量更新的仓库级认知层，供模型跨任务复用对系统的理解。

`aoci.txt` 是面向模型的结构化认知索引。它以每个受管理文件、数据库表或其他受管理对象一条独立 Entry 的方式，用符号标签与 F/R/A/S 语义表达对象的核心职责、重要关系、对外契约，以及理解或修改系统时必须知道的非显然约束和设计决策。

Header、目录段和全部 Entry 共同组成完整仓库索引，可以覆盖前端、后端、配置、数据库结构及其他受管理内容。受管理内容发生变化时，通常只需维护受影响的认知条目，不需要重新生成整个索引。

AOCI 提供系统架构、对象职责、重要关系、对外契约和关键约束的高密度视图。

### 工作原理

AOCI 采用“模型生成、模型读取”的认知闭环。

Header、Entry 和 Curation 语义的创作只按当前机器签发的 Plan 与实时 Guide 执行；由 Host 模型基于当前绑定证据独立完成。

Entry 的语义必须来自模型对真实证据的理解。不得仅依据路径、文件名、扩展名、AST、符号列表、依赖扫描、正则、固定模板或规则引擎推导、预填、拼接或改写索引语义。

对 Fresh Bootstrap，只按当前机器签发的 Plan 和实时 Guide 执行。当它们要求创作时，Host 模型创作 Root、Meta、Tag 和 F/R/A/S，提供 authoring-run 声明，并把它绑定到 Plan、Evidence 与完整 Candidate。不得要求 AOCI 填写 `origin=host_model`、制造 Receipt 或把程序生成的 Framework 当作语义。本文件不自行重建 Onboarding 流程。内部批次不是用户决策；只有遇到既有批准边界或真实的安全、漂移、CAS、Recovery 条件才停止。

### 最小使用入口

- `aoci_rules`：取得当前AOCI版本的会话运行合同。
- `aoci_overview`：建立或恢复本仓库的完整认知。
- `aoci_maintain`：受管理对象达到最终稳定状态后检查认知是否需要维护。
- `aoci_update_entry`：提交与当前证据和源码摘要绑定的完整语义更新批次。
- `aoci_report`：仅当当前布局和工具状态支持时，在证据不足、无法可靠生成语义时登记待办，不猜写。

其他MCP工具、CLI命令、参数和专项流程，以当前工具说明、Guide和 `--help` 返回内容为准，不在本文件中重复完整手册。

本区块只规定仓库接入、认知使用和收尾原则。`aoci_rules` 承载当前会话合同，Guide实时输出承载当前Plan的执行顺序与停点，工具Schema、Spec和Validator承载机器结构与判据；Prompt、Description、README和静态文档不能覆盖这些机器事实。

### 建立、生成和恢复认知

1. 每个新的 Agent Run 开始时，应先判断：

   - 本仓库是否已经存在可用的完整AOCI索引；
   - 当前上下文中是否已有与本仓库根、当前索引版本和当前AOCI服务相匹配，并且模型仍可可靠使用的完整仓库认知。

2. 仓库已经存在可用的完整索引，但当前Run没有可靠完整认知时，先调用 `aoci_rules`，再调用 `aoci_overview`。

   完整认知仍可靠时直接复用。局部不确定本身不要求机械重读系统全貌。

   本Run从已知Host上下文压缩恢复时（包括宿主注入的压缩摘要），必须把此前模型认知视为不可靠。压缩handoff不得保留或摘要正式Whole-Index，也不得保留或摘要任何Overview Header、Entry、Chunk、Challenge或Attestation正文；只能保留安全续接所需的receipt身份、未完成write或Recovery状态，以及立即重载指令。复制进handoff的Whole-Index语义或receipt不能证明恢复后模型的当前认知可靠。若当前上下文已无法可靠保留运行合同，先调用 `aoci_rules`。继续业务任务前，使用 `refresh_reasons=["context_compaction"]` 和新的 `refresh_event_id` 调用普通完整Whole-Index `aoci_overview`（不设置 `check_only` 或设为false）；不得使用 `check_only` 或认知probe。原样跟随每个 `next_cursor` 直到 `completed=true`，确认交付，并且只基于新交付正文提交一次Attestation。完成这次新的完整传输后，即使Attestation为partial或fail也消费该generation，并按既有合同继续source-bound任务，不再自动调用第二次Overview。

   AOCI可以针对 `context_compaction`、项目 `cognition_refresh_threshold` 下的机器 `semantic_threshold` 或主要 `phase_transition` 提供checkpoint与认知状态事实。只需要这些紧凑事实时使用 `check_only=true`；这些事实只向Agent提供建议，不替模型决定是否需要系统全貌。

   Agent显式调用普通 `aoci_overview`（未设置 `check_only` 或为false）时，只要能形成一致的CognitionSet，AOCI必须完整交付请求scope。不得因为已有receipt、阈值未达到或没有待处理刷新原因而抑制正文。正式认知Dirty或Stale时仍交付正文，但必须标记不可靠。存在未决恢复或无法形成一致snapshot时失败关闭，不返回混合正文。

   普通Overview返回 `continuation_required=true` 时，必须原样提交 `next_cursor` 并自动继续到 `completed=true`。不得询问用户、开始业务任务或给出阶段性系统结论。Host截断、缺块、重复、乱序、cursor失败、Index变化或`chunk_tokens`变化时停止本次认知链。Attestation完成前不得用Memory、源码、Spec、`aoci.txt`、历史会话、scope、search或Entry读取修补或补充Whole-Index认知。Challenge ordinal是正式Entry序列中的1-based位置；Header内容、注释、空行、Section/Overview/Chunk Marker、Receipt与Metadata均不计数，Chunk Receipt ordinal使用同一序列。Attestation必须原样回绑本次Challenge发布的当前`index_sha256`、`entry_sequence_sha256`与`entry_count`；旧Index、旧Entry序列、旧数量或旧Attestation均无效。完整链结束后只正式提交一次既有模型认知Attestation；同一响应只允许一次不改变语义答案的JSON Schema或字段格式修正。对象、Tag或F不匹配即失败且认知吸收不确定，不得语义重试或旁路补答。首次认知失败时还不得执行Root/Meta、Migration、全局布局或其他未重新绑定的系统级决策。上下文压缩刷新若传输完整、认知身份不变、治理对齐且没有Recovery或第三方冲突，即使Attestation为partial或fail也消耗该refresh generation，并继续原任务，不再自动重读Overview。`system_mastery_percent`只自评系统框架——架构、职责、强关系、稳定外部契约以及高熵安全和维护约束——不表示完整实现或运行实况知识；机器索引覆盖率必须分开。默认只向用户输出由本次真实覆盖率、Challenge、块数、Token和掌握度生成的规定成功或失败一句话。Host截断时提示用户把 `overview_delivery.chunk_tokens` 设置为更小的合法值后重新开始，不得自动修改。

   加法认知等级必须与严格证明字段分开解释。`delivery_verified`表示已加载Index且Host交付已确认，但完整认知验证仍未完成；应表达为“已加载且交付已验证”，不得描述为“没有认知”或“没有理解系统”。`cognition_verified`要求Attestation通过（Challenge至少80%的ordinal完全正确且对象身份至多失手一处），`cognition_governed`还要求治理对齐。通用完整读取失败句只用于真实交付故障。

   当Overview响应包含可选`cognition-state/v2`投影时，必须分别解释各维度。其Level止于`model_cognition_usable`；`strict_attestation_verified`、`governance_aligned`与`current_system_cognition_reliable`都是独立状态，绝不参与该Level。ordinal、对象身份、Tag或核心F不匹配可以导致严格Attestation失败，而模型认知仍然可用；不得仅凭这种不匹配就宣称模型没有理解系统。只有`current_system_cognition_reliable=true`允许无保留地声称当前完整系统认知可靠。投影缺失时继续使用上述Legacy解释。

   普通的只读审计、分析、检查、不修改代码或不提交、不push，不自动等于严格零写入，也不改变上述认知有效性判断。Codex Memory和历史Skill只能辅助恢复经验、用户偏好与调查方向，不能替代与当前仓库根、索引摘要、AOCI服务身份和认知范围匹配的当前认知收据；项目AGENTS和当前AOCI身份在AOCI状态上优先于历史Memory。

   只有用户明确禁止Ledger、元数据、`.aoci`运行资产及任何文件写入时，才按严格零写入处理。若必要的认知建立与该边界冲突，必须报告冲突并请求用户裁决或建议使用隔离副本，不得静默以Memory替代当前仓库认知。

3. 仓库没有可用的完整索引，或当前只有最小骨架、Header不完整、Entries未完成、必要Curation尚未裁决时，如果需要建立正式完整AOCI索引，先取得 `aoci_rules`，然后进入当前AOCI Guide。由Guide依据仓库真实状态决定下一阶段并完成必要安全步骤。

   `aoci_maintain` 不替代索引建立流程。

   不在本文件中自行重建或硬编码完整索引生成状态机。

4. 在长程任务中，模型负责保留当前认知收据并正确使用刷新门禁：

   - Host报告上下文压缩或模型已知系统全貌丢失时，执行上述强制 `context_compaction` 重载规则；AOCI不能自行推断Host事件；
   - 进入真正的主要阶段时声明 `phase_transition`，不得把函数、测试运行或小步骤当作阶段；
   - 在有用的稳定检查点通过 `check_only=true` 取得机器语义计数；
   - 除已知压缩的强制重载外，由Agent判断当前任务是否需要再次显式获取指定scope或完整Overview；
   - 在维护和对齐完成前，保留AOCI报告的Dirty或Stale可靠性状态。

### 任务收尾与认知维护

5. 纯只读问答、分析、版本核验，或没有产生受AOCI管理对象变化的任务，不需要调用维护工具。当前AOCI版本是任意`aoci_overview` check_only或`aoci_maintain`响应里的`cognition_receipt.mcp_service_version`；二进制路径是项目`.mcp.json`里的`command`，CLI不必在PATH上。

6. 发生受AOCI管理对象变化时，待其达到本次任务的最终稳定状态后，只调用一次 `aoci_maintain`。不要在每次中间修改后逐文件维护。

7. 若维护结果返回真实语义候选，Host 模型必须基于每个候选绑定的对象和必要证据，独立创作完整标签与F/R/A/S更新。通过 `aoci_update_entry` 一次提交当前机器签发批次的完整候选集合，同时原样保留每项 `source_sha256`、`candidate_id` 与对应domain批次身份。`max_entries`只限制单次请求和原子事务，不限制logical plan、Whole-Index或Managed Scope。`remaining`非零时，在当前批次成功Apply后重新调用Maintain并从新preimage继续；绝不能为满足transport上限缩减Index覆盖或自行截取返回批次。

   没有足够证据且当前布局支持 `aoci_report` 时，使用它而不猜测、套用模板或为消除待办而生成缺乏证据的认知。

8. 必须遵守工具返回的结构化状态和安全边界：

   - `repair_required`：只修复明确命中的候选，再重新提交当前机器签发的完整批次；
   - `stopped`：结束当前写入尝试并检查 `failed_step`、错误、正式写入证据与Recovery。auto模式下，已证明零写入则记录closure并重新Plan；完整Intent和可证明postimage则Resume；策略要求Rollback且preimage可证明则精确恢复后重新Plan。只有证据不足、第三方正式字节冲突、需要审批或外部动作，或命中其他真实安全边界时，才停止整个用户任务；
   - 冲突、审批、人工裁决、权限和安全信号不得忽略；
   - 已经对齐后不得重复维护或重复写入；`refresh_ready_for_overview` 是checkpoint事实，由Agent决定是否为下一阶段请求普通完整Overview。

   维护完成后如果又修改了任何受管理对象，之前的维护结果失效，应在新的最终稳定状态重新完成收尾。

9. 用户只限制业务文件范围，但没有明确禁止仓库托管资产时，AOCI托管资产可以在收尾阶段为保持认知一致而更新，并应在审计和提交中与业务文件区分。

   用户明确禁止修改 `aoci.txt`、`.aoci`、元数据或任何额外文件时，以用户限制为准，不得写入，并如实报告剩余不一致。

### 专项流程

初始化、完整索引生成、Header生成、Entries生成、数据库结构索引、Curation、人工评审和故障恢复，只按当前AOCI Guide或工具在对应阶段返回的指令、命令和安全停点执行。

不预加载、不猜测，也不自行重建这些专项流程。平台调用方式、请求格式、批次上限、审批规则、索引格式细节和恢复步骤由对应Guide、工具说明、模型Prompt和CLI帮助按需提供。
<!-- aoci:end -->
