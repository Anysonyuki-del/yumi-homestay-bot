# 住宿咨询助手边界补充：P9–P14

日期：2026-09-25。源码基线：main `a4b0b13`，含 1.39.16。

本文是《住宿咨询助手：回复决策、证据与服务转交 Spec》（`docs/specs/2026-09-25_guest-assistant-boundaries-spec.md`）的补充条目，写法和编号接续原 Spec：决定编号接 P8，验收用例接第 9 章矩阵。由实施方并入原 Spec；已确认的 P1–P8 不重复。

## 1. 决定来源

| 编号 | 用户决定 | 验收含义 | 出处 |
| --- | --- | --- | --- |
| P9 | 人工模式可以退出。<br>- 后台可手动「交还机器人」；<br>- 低风险转人工在员工 30 分钟没有发言后自动交还；<br>- 客诉和紧急只能手动交还；<br>- 自动交还时通知值班员工 | 一张图片、一次超时、一句问价不会让会话永久停在人工；高风险事项不会被机器人自行接回 | 交叉评审 Q4、Q13 |
| P10 | 危险分两级。<br>- 确定危险：发完整的撤离或报警模板；<br>- 可能危险：给一句安全提醒、询问情况，并立即通知员工，不发撤离模板；<br>- 模型只能提升等级，不能降低 | 漏报有员工兜底，误报不吓到客人；确定危险的快速通道不被削弱 | Q5 |
| P11 | 会话语言。<br>- 由第一条有实际内容的消息决定；<br>- 连续 2 条都是另一种语言才切换；每条至少 3 个英文词或 4 个汉字才算，「ok」、表情、纯数字不计；<br>- 所有固定话术都有中英两版 | 中文客人回一句「ok」不会被切成英文；英文客人不会在固定话术里收到中文 | Q6、Q14 |
| P12 | 机器人可以在转交管家时索要核对信息，二选一：订单号，或预订手机号后 4 位 + 姓名。<br>- 管家确认前：只存在这次转交任务里，不写入客户资料，不给模型看；原定「14 天后清除」已于 2026-09-26 按用户决定取消，改为明文保存；<br>- 确认前不得据此展示或确认任何订单内容；<br>- 管家确认后，把订单关联到客户资料 | 能帮没关联订单的客人转交，又不把个人信息扩散到对话上下文和模型 | Q8、Q15 |
| P13 | 员工通知固定包含：中文原因、客人称呼、房间与入住日期（仅在已确认时写）、客人原话摘要、机器人已回复的内容、后台链接 | 管家看一眼就知道找谁、机器人答应了什么，不用翻记录 | Q18 |
| P14 | 会变化的店外状态只能来自本轮实时查询，不按季节或经验推测。会变化的状态指天气、气温、降雨、人流、排队、路况、营业或开放状态、活动安排 | 例如「这几天武汉早晚偏凉，带件薄外套」这类没查就说的句子不再出现；不会变化的常识照写，例如景点在哪个区、城市特色 | 2026-09-25 用户决定「店外信息推测收紧」；1.39.16 测试号验收发现 |

## 2. 已核实现状

以下路径均以 `src/homestay_bot/` 为前缀，依据 main `a4b0b13`。

| 条目 | 现状 | 依据 |
| --- | --- | --- |
| P9 | 全库只有一处把会话切到人工，没有任何切回路径，后台也只显示「人工接待 / 机器人接待」，没有操作入口 | `services/conversation_service.py::_switch_to_human`；`services/customer_admin_service.py`（`mode_label`） |
| P9 | 进入原因写在接管审计里，可按原因区分风险 | `repositories/operations.py::record_handoff`（`AuditLog.action="conversation_handoff"`、`details.reason`）；原因取值见 `conversation_service.py`：<br>- `emergency:*`、`complaint:*`；<br>- `manual_request_or_media`、`servicer_reply`；<br>- `tourism_failure:*`、`assistant_unavailable`；<br>- `answer_policy.py::handoff_reason` 判出的 `refund`、`complaint`、`early_check_in`、`agitated`、`price` |
| P9 | 已有按会话读取最新接管审计的函数，也有每小时一次的任务巡检 | `application.py::_latest_conversation_handoff_id`；`application.py::_run_task_lifecycle_loop` → `TaskLifecycleService.sweep` |
| P10 | 紧急判定是一张按类别排序的词表，命中即发该类撤离模板并切人工，没有中间等级 | `services/emergency_service.py::EmergencyService.classify/safety_reply`；`conversation_service.py::_escalate_emergency` |
| P10 | 真实模型测试：6 句真实危险只识别 1 句，6 句正常咨询误判 4 句 | 交叉评审 §4.5 |
| P11 | 只要消息里有英文字母、没有中文，会话就被切成英文并保存 | `conversation_service.py::_detect_language`，由 `handle_message` 调用 |
| P11 | 至少三处固定话术写死中文：<br>- 客诉首响写死 `Language.ZH`；<br>- 人工接待期间收到新高风险消息时回「我已收到您的诉求。」，再按会话语言追加收尾，英文会话得到中英混排；<br>- 与民宿无关的边界说明只有中文 | `services/complaint_service.py::guest_acknowledgement`；`conversation_service.py::handle_message`、`process_debounced_message` 的 HUMAN_ACTIVE 分支；`conversation_service.py::_send_unrelated_reply` |
| P12 | 转交任务没有存放核对信息的字段；已有的加密和清理做法在预订审批上 | `domain/models.py::BusinessTask`（无相关字段）；`domain/models.py::BookingApproval.guest_name_ciphertext/guest_mobile_ciphertext/pii_purged_at`；`services/sensitive_data.py::SensitiveDataCipher`；`repositories/retention.py::SQLAlchemyRetentionRepository.purge` |
| P12 | 客人消息原文全部入库，并进入模型上下文 | `services/message_service.py`；`repositories/context.py::SQLAlchemyContextRepository.load_model_context` |
| P12 | 真实模型测试中，模型在问路线、问距离时主动索要预订人姓名或订单号 | 交叉评审 §2 |
| P13 | 通知有原因、客服账号、客人备注或称呼、客人原话，上限 2048 字节。缺三项：房间与日期、机器人已回复的内容、后台链接。原因里还有内部代码，例如「YuMi 接管：refund」「紧急事件：gas」 | `conversation_service.py::_notify_employee`（`_EMPLOYEE_NOTIFICATION_MAX_BYTES`）；`_activate_human`、`_escalate_emergency` 传入的 reason |
| P14 | 规则文字写的是「店外信息只写有来源的内容或公认的常识」，没有区分会变化的状态；确定性层只约束民宿本身 | `services/fact_policy.py::FACT_SOURCE_RULE_ZH/FACT_SOURCE_RULE_EN`、`is_unsourced_homestay_claim` |
| P14 | 1.39.16 测试号验收：「明天天气怎么样？还有空房吗？」按住宿意图查了房态、没有联网，回复却写了「这几天武汉早晚偏凉，带件薄外套会舒服些」 | `docs/releases/1.39.16.md`「发布现场追加」 |

## 3. 方案

### 3.1 P9 人工模式交还

- **按原因分两类**，以最近一次接管审计的 `reason` 为准：
  - **仅手动交还**：`emergency:*`、`complaint:*`，以及 `refund`、`complaint`、`agitated`；
  - **可自动交还**：其余原因，包括 `manual_request_or_media`、`servicer_reply`、`tourism_failure:*`、`assistant_unavailable`、`price`、`early_check_in`。

  「平台」被误判成客诉的问题，由原 Spec 的 S01 修正客诉判定来解决，不靠把 `complaint:*` 划进可自动交还。
- **30 分钟的起算点**：取最近一次接管审计时间和最近一条员工消息时间中较晚的一个，员工每次发言都重新计时。
- **判定时机（用户决定：每小时巡检）**：并入现有每小时一次的任务巡检（`application.py::_run_task_lifecycle_loop` → `TaskLifecycleService.sweep`）。每轮找出空闲满 30 分钟、原因可自动交还的人工会话，逐个交还并通知员工，不另建循环。
  - 后台显示的接待模式始终与实际一致。
  - 巡检每小时一次，所以实际交还发生在空闲满 30 分钟之后的 30 到 90 分钟之间。这期间客人发来的消息仍按人工模式处理：原 Spec A07 保证独立问题照答，高风险事项继续交给人工。
  - 每轮处理数量沿用巡检已有的批量上限，数量满批时下一轮继续，避免一次锁住过多会话。
  - 交还前在同一事务里再核对一次：模式仍是人工、期间没有新的员工消息、没有新的接管，避免和员工同时操作时互相覆盖。
- **交还动作**：
  - 把 `Conversation.mode` 设回 `BOT_ACTIVE`；
  - 记录审计 `conversation_release`，写明原因（`auto_idle_30m` 或 `employee`）、员工编号、对应的接管审计编号；
  - 自动交还时通知值班员工，通知内容遵循 P13。
- **手动交还**：在后台客户详情的会话行加「交还机器人」按钮，沿用现有员工权限和 CSRF，不新建管理页面。仅手动类的会话同样可以手动交还。
- **与已有规则的关系**：
  - 交还后，1.39.16 的分段回复「新接管即停发」依据接管审计编号，仍然有效；
  - 原 Spec A07（人工期间独立问题照答）不变；
  - 交还不结束、不修改已有任务的状态。

### 3.2 P10 危险分级

- **确定危险**：沿用 `EmergencyService` 的快速通道和撤离模板，但判据要求「正在发生」。
  - 例子：「着火了」「好大的烟」「燃气味好重」「朋友晕倒了叫不醒」「喘不上气」「头破了在流血」「有人一直敲门还骂人，我很害怕」。
  - 否定、引用、设施咨询和政策咨询不算，例如「厨房是燃气灶吗」「烟雾报警器在哪」。
  - 快速通道保持确定性判定，不等模型。
- **可能危险**：提到危险对象，但看不出正在发生，或说法含糊。
  - 例子：「房间有点烟味」「刚才摔了一下」「头有点晕」。
  - 回复：一句安全提醒加一句询问，中英各一版。
  - 立即通知员工，原因写「可能的安全情况」。
  - 不发撤离模板，不切人工；员工回复后按现有 `servicer_reply` 进入人工。
  - 这类回复同样带 `stale_exempt`，不做出站过时判定。
- **模型只能提升等级**：模型可以把「无」或「可能危险」提升为更高一级，不能把确定危险降级；模型不可用时，确定性判定照常生效。
- **词表改动必须附语料**：同时维护正例（漏报句）和反例（误报句）两组，写进第 5 节的用例。原 Spec 第 7 章和 S02 的方向不变，本条补充中间一级。

### 3.3 P11 会话语言

- **什么算有实际内容**：至少 3 个英文词，或至少 4 个汉字。「ok」「好的」、表情、纯数字、链接、图片都不算。中英混写时，达到 4 个汉字就按中文计。
- **确定与切换**：
  - 第一条有实际内容的消息确定 `Conversation.language`；
  - 之后最近连续 2 条有实际内容的客人消息都是另一种语言，才切换；
  - 用最近的客人消息计算，不新增字段。
- **回复语言以会话语言为准**，不按单条消息切换。模型提示词同样使用会话语言。
- **固定话术补齐中英两版**：
  - 已知的有：客诉首响、人工接待期间的高风险确认、无关问题的边界说明；
  - 实施时逐一清点 `conversation_service.py`、`complaint_service.py`、`guest_reply_policy.py`、`emergency_service.py` 里的全部固定文本；
  - 加一条测试：英文会话下，每条固定话术都不含中文字符，中文会话反之。

### 3.4 P12 转交时的核对信息

- **什么时候可以问**：只在要转交管家，且需要管家核对订单时才问，例如改期、发票，或 P8 流程里找不到已关联订单。问路线、距离、设施这类普通咨询不得索要。
- **只问二选一**：订单号，或预订手机号后 4 位加姓名。不问完整手机号、身份证号、支付信息。
- **存放（2026-09-26 修订）**：用户决定「开放1和3」，服务器日志与数据库可以记录客人信息，数据库明文、不到期清除，见 `docs/specs/2026-09-26_guest-data-retention-spec.md`。据此：
  - 核对信息在 `BusinessTask` 上以明文字段存放，不新增加密、到期清除或处理完成清除逻辑；
  - 下面两条属于「发给模型前的脱敏」和「对客人的安全门」，**保留**：不进模型上下文、管家确认前不据此展示订单。
- **不进对话上下文**：
  - 核对信息保存到任务明文字段，消息原文可以入库；构造模型上下文及摘要输入时，才替换成占位说明，例如「[核对信息已转交管家]」，不为模型脱敏改写存储原文；
  - 模型及评估导出只看到脱敏内容；服务器日志允许记录客人信息，但密钥、令牌和门锁凭证等保护不变，真实客人数据不得进入仓库或 GitHub；
  - 后台任务详情仅向有权限的员工显示明文字段，不新增解密步骤。
- **员工通知**：只写「客人已提供核对信息，见后台任务」加链接，不在企业微信消息里写核对信息本身。
- **确认前不回答订单内容**：管家确认前，机器人不得据此展示或确认任何订单信息，包括房间、日期、金额、状态。
- **管家确认后**：
  - 管家用现有的订单关联或客户合并能力，把订单归到这位客人名下；核对信息保留在任务记录中，不因处理完成清除；
  - 之后按 P8 由客人确认本次住宿；
  - 不把后 4 位或姓名原样写进 `Customer`。

### 3.5 P13 员工通知字段

- **格式**：沿用 `_notify_employee` 的格式和 2048 字节上限。空间不够时按以下顺序保留，客人原话和机器人回复在末尾截断：
  1. 中文原因；
  2. 客人称呼（CRM 备注优先，已有）；
  3. 房间与入住日期（只在 P8 已确认时写）；
  4. 后台链接；
  5. 客人原话摘要；
  6. 机器人已回复内容的摘要。
- **中文原因**：内部代码经映射表转成中文，例如 `refund` 转成「退款或赔偿诉求」、`gas` 转成「燃气泄漏」；通知里不出现英文代码。1.39.16 的客诉即时通知已有一份映射，合并为一处。
- **机器人已回复的内容**：用刚登记的客人回复正文；还没回复的写「尚未回复客人」。
- **后台链接**：有任务时指向任务详情，没有任务时指向客户详情的会话；沿用现有的公开地址配置（`approval_base_url`）。

### 3.6 P14 店外状态不推测

- **规则文字**：在 `fact_policy.FACT_SOURCE_RULE_ZH/EN` 里补一句。

  > 会变化的店外状态（天气、气温、降雨、人流、排队、路况、营业或开放状态、活动安排）只能来自本轮的实时查询结果；没有查询结果就说暂时查不到，不按季节或经验推测。

  不会变化的常识可以写。所有引用该常量的提示词自动生效。
- **确定性层**：本轮没有实时查询结果时，删除「带时间指向，又断言店外会变化状态」的句子。
  - 时间指向：这几天、最近、今天、明天、今晚、这周、近期、现在等；
  - 会变化的状态：偏凉、偏热、下雨、降温、人多、排队、堵、开门、关门、营业等。

  判定放在 `fact_policy`，和民宿事实的逐句判定并列，所有回复出口共用。本轮有实时查询结果时不删，那些事实有来源。
- **与原 Spec 的关系**：原 Spec 4.3 已写「非实时建议不得伪装为现场情况」。本条把它落成规则文字和一条确定性检查。
- **实施方（用户决定）**：已在 1.39.17 实施，main `7778dec`。见 `fact_policy.is_unsourced_external_state_claim`、`guest_reply_policy.remove_unsourced_external_state_claims` 和 `docs/releases/1.39.17.md`。原 Spec 的重构合并 main 后沿用，不重复实现。
- **1.39.17 同时入库**：共用虚构资料 `tests/fixtures/guest_reply_scenarios.json`，以及部署前真实模型回归门禁（`scripts/release/reply_gate.sh`，基线 `tests/fixtures/guest_reply_regression_baseline.json`）。原 Spec 的重构上线同样要过这道门禁：已稳定通过的场景不能退步。

## 4. 文件与符号级范围（补充原 Spec 第 8 章）

| 条目 | 文件与符号 | 修改与验证目标 |
| --- | --- | --- |
| P9 | `services/task_lifecycle_service.py::TaskLifecycleService.sweep`（新增空闲会话交还）；`repositories/operations.py`（新增查询：空闲满 30 分钟、原因可自动交还的人工会话；新增 `record_release`）；员工通知复用 `conversation_service.py::_notify_employee` 的格式（P13）；`routes/customers.py`、`services/customer_admin_service.py`、客户详情模板（交还按钮） | 按原因和空闲时长交还；审计可追溯；权限与 CSRF 沿用 |
| P10 | `emergency_service.py::EmergencyClassification`（增加等级）/`classify`/`safety_reply`（可能危险的话术）；`conversation_service.py::_escalate_emergency`，新增可能危险分支 | 两级判定、成对语料、模型只能升级 |
| P11 | `conversation_service.py::_detect_language` 及调用方；`complaint_service.py::guest_acknowledgement`；上述三处固定话术 | 会话语言规则；固定话术双语；逐条枚举的双语测试 |
| P12 | `domain/models.py::BusinessTask` 及顺接实际 Alembic head 的新迁移；任务核对信息写入、模型上下文及摘要输入的占位替换；后台任务详情 | 明文保存，不因到期或处理完成清除；消息原文和服务器日志按新版保留；模型、员工通知及评估导出不含核对原值；后台权限及订单核验不变；迁移在隔离库验证 |
| P13 | `conversation_service.py::_notify_employee` 及全部调用方；与 1.39.16 的客诉原因映射合并 | 字段齐全、优先级截断、无英文代码 |
| P14 | `services/fact_policy.py`（规则常量，新增会变化店外状态的判定）；`guest_reply_policy.py::remove_ungrounded_property_claims` 或其上层调用处 | 无实时依据时不推测；有依据时不误删 |

## 5. 验收用例（接原 Spec 第 9 章）

| 用例 | 输入与依据 | 必须满足 |
| --- | --- | --- |
| H01 | 客人发图片进入人工，员工 30 分钟未发言，随后巡检运行，客人再问早餐 | 巡检交还并通知员工；之后机器人回答早餐 |
| H02 | 巡检时员工沉默 29 分钟与 31 分钟两种情况；巡检和员工发言同时发生 | 29 分钟不交还，31 分钟交还；员工每次发言重新计时；交还前复核，不覆盖员工的新操作 |
| H03 | 客诉或紧急进入人工，员工 2 小时未发言 | 不自动交还；员工在后台手动交还后恢复机器人 |
| H04 | 分段回复发送途中员工接管，随后又被交还 | 已停发的段落不恢复发送；新消息按机器人模式处理 |
| S04 | 交叉评审 §4.5 的 6 句真实危险 | 全部至少进入可能危险并通知员工；正在发生的进入确定危险 |
| S05 | 「厨房是燃气灶吗」「烟雾报警器在哪」「退房后东西被烧坏怎么赔」等咨询 | 不发撤离模板；按普通咨询或客诉路径处理 |
| S06 | 模型把确定危险判成普通咨询；模型不可用 | 确定危险照常发模板、切人工、通知员工 |
| L01 | 中文会话中客人回「ok」「👍」「123」 | 会话语言不变，回复仍为中文 |
| L02 | 中文会话中连续两条各至少 3 个英文词的英文消息 | 第二条之后切换为英文；只有一条时不切换 |
| L03 | 英文会话触发客诉、无关问题、人工期间高风险确认 | 固定话术全为英文，无中英混排 |
| I01 | 问路线、距离、设施 | 不索要任何身份或订单信息 |
| I02 | 要求改期，没有已关联订单 | 只索要二选一核对信息；不索要完整手机号或身份证 |
| I03 | 客人提供了核对信息 | 任务明文字段保存，消息原文可入库；模型上下文、摘要输入、员工通知及评估导出看不到原值；服务器日志允许保留客人信息；后台仅有权限员工可见，真实资料不得进入仓库或 GitHub |
| I04 | 管家确认前，客人追问「我订的是哪间」 | 不展示、不确认任何订单内容 |
| I05 | 超过原定 14 天、管家处理完成 | 核对信息仍保留，不触发个人信息到期或完成清除；订单关联仍经管家核实，之后按 P8 由客人确认住宿 |
| N01 | 各类转人工、客诉、紧急、自动交还的通知 | 六项字段按规则出现；无英文内部代码；超长时按优先级截断 |
| F01 | 「明天天气怎么样？还有空房吗？」，本轮只查房态 | 不出现「这几天偏凉」这类推测；说明天气暂时查不到 |
| F02 | 本轮有实时天气结果 | 有来源的天气事实保留 |
| F03 | 「黄鹤楼在哪个区」这类稳定常识 | 照常回答，不被当成推测删除 |

## 6. 与原 Spec 及 1.39.16 的衔接

- D01 写的是「过时保护保持」，需要补一句「安全与承诺类回复除外」：1.39.16 起，紧急提示、客诉首响、转人工确认带 `stale_exempt`，不做出站过时判定；P10 的可能危险提醒同样如此。
- 1.39.16 已经合入 main，改动涉及九个文件：
  - `application.py`、`conversation_service.py`、`answer_policy.py`、`tourism.py`、`fact_policy.py`；
  - `deepseek_client.py`、`deepseek_delivery_rewriter.py`、`delivery_rewrite_job.py`、`guest_reply_policy.py`。

  其中两点合并时要注意：
  - 住宿意图统一到 `answer_policy.asks_stay_availability`，联网分流、房态工具开放、交易判定都用它，原 Spec 3 阶段的路由调整请沿用；
  - 事实过滤新增依据参数 `grounded_in`（`fact_policy.is_supported_by`），原 Spec 4.3 的证据绑定可以直接把本轮证据文本传进来。
- 验收用例同样纳入 1.39.17 的共用虚构资料（`tests/fixtures/guest_reply_scenarios.json`），离线契约测试和真实模型回归共用一套。

### 6.1 2026-09-26 补充：main 已到 1.40.1（`68417df`）

原 Spec 末尾「并行发布衔接」只覆盖 1.39.16。main 自 `1300b13` 起共领先 10 个提交，合并时还要保留下面这些改动。

**两边都改了的 7 个源码文件**：`application.py`、`deepseek_client.py`、`deepseek_delivery_rewriter.py`、`answer_policy.py`、`conversation_service.py`、`emergency_service.py`、`guest_reply_policy.py`。不能整文件覆盖。

**main 独有、本分支还没见过的 8 个文件**：`tourism.py`、`repositories/operations.py`、`delivery_rewrite_job.py`、`fact_policy.py`、`tools/reply_regression.py`、`scripts/release/reply_gate.sh`、`tests/fixtures/guest_reply_scenarios.json`、`tests/fixtures/guest_reply_regression_baseline.json`。

需要保留的行为：

| 版本 | 行为 | 位置 |
| --- | --- | --- |
| 1.39.17 | 部署前真实模型回归门禁：改了回复链路就必须跑，已稳定通过的场景不能退步。本 Spec 的重构上线同样要过这道门禁 | `scripts/release/reply_gate.sh`、`tools/reply_regression.py`、基线文件（当前不退步 84 个） |
| 1.39.17 | 共用虚构资料已经入库（153 个场景、53 条知识，带 scope、property_id、valid_from、valid_until 字段）。原 Spec 第 8 章第 1 步「新增 `guest_reply_scenarios.json`」改为在这份文件上追加：已有场景的编号和期望不改，修改期望要写进基线文件的 `expectation_changes` | `tests/fixtures/guest_reply_scenarios.json` |
| 1.39.17 | P14：非联网回复不断言会变化的店外状态；回复被整段删空时用统一的中性说明 | `fact_policy.is_unsourced_external_state_claim`、`guest_reply_policy.remove_unsourced_external_state_claims/unconfirmed_fallback` |
| 1.40.0 | 紧急情况进行中（人工模式、最近一次接管原因是 `emergency:*`），求助类后续回固定处置答复，并再次通知员工，不调模型、不联网；独立问题照常回答（与 A07 一致） | `conversation_service._answer_emergency_follow_up`、`emergency_service.is_emergency_follow_up/emergency_follow_up_reply`、`ConversationAuditPort.latest_handoff_reason`、`EmergencyKnowledgePort` |
| 1.40.0 | 求助说法（「现在怎么办」）不触发联网规则 | `tourism.classify_tourism_query` |
| 1.40.0 | **问价对比版**：<br>- 参考价按房间换算，附是否可住，算作依据；<br>- 统一问房价判定 `asks_room_price`；<br>- 带日期问价强制查参考价，无日期先问日期；<br>- 单纯问价不转人工，讨价还价仍转人工 | `deepseek_client.HostexReadOnlyToolExecutor._reference_prices`、`respond`、`_price_question_needs_dates`；`answer_policy.asks_room_price/handoff_reason` |

**1.40.2 与 1.41.0（2026-09-26）**：
- 1.40.2 在 `respond` 和 `_process_model_reply` 加了分阶段耗时日志：`stage_timing_sink`、`_process_model_reply_body`、`_ReplyTiming`。重构这两个函数时，请在新版本的各返回点补回结果标记。
- 1.41.0 按用户决定「开放1和3」放开客人数据记录与保留：日志不再给手机号打码；作业载荷不再清空；长摘要后保留原文；审批与客户手机号改存明文、不再到期清除。
- 1.41.0 **占用了迁移编号 `0028_guest_plaintext`**。本分支规划的 `0028_knowledge_scope`、`0029_stay_confirmation` 请顺延为 `0029`、`0030`，`down_revision` 接到 `0028_guest_plaintext`，不能出现并行 head。

**关于问价的对比（用户决定「你做一个版本然后到时候和codex做的一起对比选优」）**：
- 本 Spec 的问价实现请按原 Spec 4.3 的逐项绑定来做，不需要沿用 1.40.0 的做法。
- 两版用同一套回归门禁和虚构场景对比，重点看这些场景：`PR-201`、`PR-EN`、`PR-最便宜`、`PR-无日期`、`PR-只问价`、`PR-讲价`、`PR-无主渠道`、`MT-房态价格`。另外人工复核回复，再择优保留。
- 生产实测：1.40.1 在测试号上已能按真实渠道对照表报出 7 间房的参考价。

## 7. 已确认的实施决定（2026-09-25）

1. **P14** 不在本 Spec 的实施范围内，由另一方在 1.39.17 实施，见 3.6。
2. **P9 用每小时巡检判定交还**，见 3.1。
3. **P12 的迁移编号**在 main 的 `0028_guest_plaintext` 及顺延后的 `0029_knowledge_scope`、`0030_stay_confirmation` 之后顺接，实施前按实际 Alembic head 核对。

### 2026-09-26 更正：P12 统一为新版

用户明确要求「统一为新版」。P12 以 1.41.0 客人数据记录与保留 Spec 为准：明文保存，不因到期或管家处理完成清除；入库原文与发给模型的脱敏副本分开。服务器日志允许记录客人信息；模型脱敏、员工通知不含核对原值、后台权限、订单核验和真实客人数据不进 GitHub 的边界保留。第 2 节的加密、清理描述仅是 `a4b0b13` 的历史现状，不是新实施要求。
