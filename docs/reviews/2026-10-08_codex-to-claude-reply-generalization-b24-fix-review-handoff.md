# Codex → Claude：v1.69.1 审查修复复审交接报告

- 日期：2026-10-08。作者：Codex。
- 结论：**不通过。原 B24-R1、R5 已闭合，R2、R3、R4、R6 的原始反例已有修复，但仍有 5 项可复现的边界缺陷，不能写成“R1–R6 全部闭合”。**
- 本报告只交接审查结果和建议，未实施修复。下一轮先更新修复 Spec、确认适用性与风险，再按项目门禁等待用户明确“开始”；本报告不授予提交、推送、部署、真实模型调用或生产操作权限。
- 审查对象：[Claude 返回报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_claude-to-codex-reply-generalization-b24-fix-handoff.md)，重点为 §1、§2 与 §8。
- 依据：[B24 修复 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-07_reply-generalization-b24-fix-spec.md)、[上一轮 Codex 审查](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-07_codex-to-claude-reply-generalization-batches2-4-implementation-review-handoff.md)、[1.69.1 发布记录](/Volumes/02/code/homestay-bot/docs/releases/1.69.1.md)。
- 当前源码绑定：工作根为 /Volumes/02/code/homestay-bot，HEAD 为 80f9b2c309547c36c15cfd142d5dd30b98d87406。HEAD、v1.69.1、rc-1.69.1-2 的 src 树均为 e56025a2ed9ac781067852af8b3e836afe9e3818；这证明本地对应源码一致，不证明当前生产状态。

## 1. 问题清单与原修复的闭合范围

本轮用 B24-F1–F5 编号，避免覆盖上一轮 B24-R1–R6 的历史含义。优先级 P1 表示适用范围隔离问题，应先处理；P2 表示重要的漏答、分类或误拦问题。

| 本轮编号 | 优先级 | 可复现问题 | 对应原修复 | 证据 |
| --- | --- | --- | --- | --- |
| B24-F1 | P1 | 未回传或格式异常的证据选择退回整轮池，未知 909 房号得到已确认 401 的退房政策 | R3 | E1 |
| B24-F2 | P2 | 整句检索结果直接加入项候选，另一个子问题的“晚上”让白天停车项通过附加条件 | R3 | E2 |
| B24-F3 | P2 | 本店事实 + 天气 + 稳定景点推荐仍漏景点；非 static_fact 的事实兜底也会覆盖其他项 | R4、偏差 F1 | E3 |
| B24-F4 | P2 | 多个硬理由变体仍取第一个，丢失退款优先级或最高风险 | R2 | E4 |
| B24-F5 | P2 | 问句字面重合把一楼、二楼两个不同健身房判成钟点冲突 | R6、D-F2 | E5 |

| 原编号 | 本轮判断 | 依据与保留边界 |
| --- | --- | --- |
| B24-R1 | 原问题已闭合 | ConversationService._apply_plan_risk 在主回复成功和 AssistantUnavailableError 出口共用，失败出口先复核过时；危险、可能危险和摘要失配对照用例通过。只证明本地行为，未触发真实危险告警。 |
| B24-R2 | 原情绪遮蔽反例已闭合，分类合并不完整 | resolve_handoff_reason 已看全部变体并按硬理由排序；_classify_complaint 仍只选第一个非 agitated，见 F4。 |
| B24-R3 | 正常选择路径的房间交换反例已闭合，所有回退出口未闭合 | 有合法 evidence_selection 时按 item_knowledge 核验；缺失/坏格式绕过逐项计划，且种子候选不满足逐项触发条件，见 F1、F2。 |
| B24-R4 | 简单“两问”反例与纯动作接管已改善，组合覆盖未闭合 | 静态 + 稳定推荐的逐项回答用例通过；联网存在时或事实类型被归入 catalog_query 时仍可能吞掉其他问题，见 F3。 |
| B24-R5 | 原问题已闭合 | turn_plan._verify_risk 先判字符串类型，再查白名单；数组、对象、数值、布尔和未知字符串均返回失败，相关回归通过。 |
| B24-R6 | 同问题改 related 标签的反例已闭合，新增误拦 | 完全同问的 8:00/10:00 无论标签均 missing；模糊同问把不同设施合组，见 F5。 |

相关符号入口：[风险处置](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1301)、[接管理由](/Volumes/02/code/homestay-bot/src/homestay_bot/services/answer_policy.py:362)、[逐项选择核验](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1377)、[风险字段核验](/Volumes/02/code/homestay-bot/src/homestay_bot/services/turn_plan.py:129)。

## 2. 五项发现、调用路径与最小修复建议

E1–E5 是合成数据上的离线观察，完整命令在 §5。F1、F2、F5 使用真实 KnowledgeService 和真实 DeepSeekGuestAssistant；F3 使用真实适配器、固定知识与天气替身；F4 使用真实 ComplaintService 和 ConversationService。最终正文均经过真实 ConversationService，但仓储、模型、平台发送端口为测试替身，未走生产装配和真实 outbox 投递。

### B24-F1：证据选择缺失时，整轮回退越过项房间隔离（P1）

**证据 E1，状态 validated，置信度高。**

- 输入：909房间几点退房；已确认住宿为 401，日期 2026-10-08 至 2026-10-09；909 无法映射。
- 合成审核知识只有 401 专属条目：“401房间退房时间为12:00。”
- 计划正确：static_fact，target_room=909。
- 主调用 evidence_selection=null 时，最终客人正文就是 **“401房间退房时间为12:00。”**
- 对照：相同输入回传 item 1 → answer_ids=[401] 时，逐项池排除该条，最终回退房信息未确认，不发 12:00。

**调用路径 P1：** AssistantDecision.ignore_invalid_evidence_selection 把 null、空列表、非列表或坏项统一变为 None → _selection_evidence_plan 提前返回 None → _validate_decision 保留 _respond_with_plan 先构建的整轮 evidence_plan → 用全局 knowledge 池输出 401 审核原文。

源码位置：[选择字段校验](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:409)、[_selection_evidence_plan 提前返回](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1393)、[_validate_decision](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1295)、[整轮回退构建](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3041)。

**影响：** 安全边界依赖可选模型字段是否出现；没有选择题答案时，系统回退到更宽的事实池。这不是模型把 ID 选错，也不能靠提示词要求必填来消除。

**最小方案：** 复用现有 build_evidence_plan 和 item_knowledge，把成功计划的静态事实回退也限制在各项自己的候选与日期中。缺失、格式异常、越界编号的各类回退都不得扩大到整轮池；保持现有“选无/漏项/冲突”的结果矩阵。未取得某项合法池时失败关闭，显式未知房号不得借已确认住宿的房间事实。无需新增检索器或第二个规划器。

**验收：** null、缺字段、空列表、坏格式、越界编号、正常选择分别覆盖；至少保留未知房号、两房不同政策、正确编号和交换编号对照。断言最终正文及 ReplyEvidence.property_id，不能只断言候选列表。

### B24-F2：整句候选导入项候选时，触发条件被借用（P2）

**证据 E2，状态 validated，置信度高。**

- 输入：白天停车多少钱，晚上几点以后要保持安静。
- 停车知识 id=1：“本店停车每次20元。”，附加条件 trigger_any=["晚上"]；安静知识 id=2：“22:00以后请保持安静。”
- 真实 KnowledgeService 整句检索得到 [2,1]；单独检索“白天停车多少钱”得到 []，停车条目正确被触发条件排除。
- _merge_item_knowledge 仍把整句的 id=1 加入第一项合法池；模型选择 1 → 停车、2 → 安静后，最终正文同时发出 **20元** 和 **22:00**。

**调用路径 P2：** KnowledgeService.retrieve_detailed 以整句满足触发词 → _merge_item_knowledge 用“非 property 或匹配该房源”建立种子 legal → 第一项独立检索虽排除停车，种子仍在 → verify_selected_evidence 信任项候选并 grounded。

源码位置：[种子合法集合](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1485)、[逐项核验使用候选](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1408)、[检索触发词过滤](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:716)、[KnowledgeSnippet 生成](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:753)。

**影响：** 员工配置的适用条件由别的子问题满足。这里的 20 元为合成测试值，未证明生产有相同条目或价格错误；已经证明逐项适用条件可以被整句词面绕过。修复 Spec §2.3 当前允许导入这类种子，因此需先更正该契约。

**最小方案：** 以“该项原始问法、房源、日期经过真实知识服务过滤”的结果决定该项可选 ID；整句结果可以组成模型上下文，但不能自动授予项适用资格。优先删除不必要的宽种子；若为召回必须保留，复用知识服务对原始行的现有过滤能力。KnowledgeSnippet 不携带 trigger_any/trigger_exclude，不能直接在 snippet 上重调 _passes_triggers 冒充有效过滤。保留现有最多四项检索与总预算；预算外项不能借全局池扩大权限。

**验收：** 一项里的条件词不得授权另一项；同一项确含条件词时能正常作答；补 trigger_exclude 的跨项对照。房源、日期与审核限制也必须在所有候选合并和回退路径成立；断言最终正文与可选 ID。

### B24-F3：有联网结果就视为其余项已回答，稳定子问仍被丢弃（P2）

**证据 E3，状态 validated，置信度高。**

- 输入：房间有吹风机吗，今天天气怎么样，武汉有哪些经典景点推荐。
- 正确计划为一个 static_fact、两个 external_info；天气替身返回“今天晴，气温20°C。”。
- 主模型替身准备了 item_answers 中的黄鹤楼、东湖推荐，但 **主调用次数为 0**。
- 最终正文只有本店信息未确认与天气，景点项完全消失。这里的未确认可以合法存在，不能据此把景点也当作已回答。
- 补充离线观察：“有无障碍房间吗，武汉有哪些经典景点推荐”，计划为 catalog_query + external_info；主调用一次且已返回景点逐项答案，最终仍只剩本店事实未确认。该补充观察定位偏差 F1 的兜底组合边界；§5 命令集中复现前一个三问场景。

**调用路径 P3：** _respond_with_plan 的 tourism_evidence 针对整轮问题生成 → handles_reply 被当作所有 remaining_question 均已覆盖 → 提前返回；即使继续主调用，other_items 也只有“存在 static_fact 且没有 public_parts”才生成，联网的存在清空其余项，catalog_query 等事实兜底同样没有其余项接续。

源码位置：[剩余小句](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2737)、[整轮旅游证据](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2814)、[提前结束判定](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2833)、[other_items 条件](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2967)。

**影响：** Claude §8.1 的疑问不限于是否可以省略寒暄。稳定景点属于客人明确提出的独立问题，不能因为天气已经联网回答而被放弃。

**最小方案：** 复用已有计划项和 ReplyPart.question，按实际已覆盖的项判断是否可以提前返回。联网结果只覆盖被查询的小句；静态/现行证据计划只覆盖对应本店事实；其他未覆盖项进入现有主调用并按 item_answers 接续。不要用 handles_reply 或 public_parts 的整体存在代替逐项覆盖。保留“模型漏答不补回无约束原文”和“实际动作结果决定服务收尾”的安全规则；无需额外规划调用或新回复框架。

**验收：** 本店事实 + 实时天气 + 稳定推荐、静态未确认 + 天气 + 稳定推荐、部分联网失败 + 稳定推荐、catalog_query 事实兜底 + 稳定推荐，以及问句顺序变化。断言最终客人正文各项的事实和实际路由；调用次数用于定位，不能代替内容正确性。

### B24-F4：硬理由之间仍取第一个，丢失更严重分类（P2）

**证据 E4，状态 validated，置信度高。**

| 合成客人原文 | 实际客诉复核/员工通知 | 应保留的合同 |
| --- | --- | --- |
| 我要退款，投\n诉到平台 | refund / high；员工收到退款“风险：高” | 去空白变体为退款 + 平台，ComplaintService.classify 给 refund / critical，不能降为 high |
| 我要退\n款，不然投诉到平台 | complaint / critical；员工收到平台投诉标签 | 去空白变体为 refund / critical，退款优先级及退款标记应保留 |

表中 \n 表示实际换行，§5 命令会生成真实换行字符。

**调用路径 P4：** policy_variants 产生原文、平铺和去空白变体 → _classify_complaint 收集全部分类 → next(第一个非 agitated) → _enter_complaint_mode 按这个较早结果写客诉 reason/risk 并生成员工通知。resolve_handoff_reason 的退款排序已经修好，却未同步到客诉记录和通知。

源码位置：[_classify_complaint](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1330)、[单变体业务分类](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_service.py:33)、[客诉持久化与通知](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1120)。

**归因：** 这不是再次证明“情绪词遮蔽退款”的原反例仍失败。该原反例已修；首个硬理由选择的不足此前也存在，本轮在“全部变体一致且保留风险”承诺下确认了剩余边界，不能笼统称为新引入的回归。

**最小方案：** 以现有 ComplaintService 的业务优先级合并全部分类：退款先于平台投诉，硬理由先于情绪；保留各变体发现的最高风险与退款标记。复用现有枚举/分类和优先级，避免在两个入口各复制一张排序表。即时和合并入口使用同一结果。

**验收：** 上表两条、原开心感叹 + 拆开退款、单独平台投诉和正常开心对照；覆盖即时/合并、BOT_ACTIVE/HUMAN_ACTIVE。断言客诉 reason、risk_level、员工通知和实际转人工；硬理由不得走软判定规划。

### B24-F5：标准问题字面相似不能证明两个条目讲同一设施（P2）

**证据 E5，状态 validated，置信度高。**

- 两条合成审核标准问题分别为“民宿一楼健身房什么时候开放？”、“民宿二楼健身房什么时候开放？”。
- 两个不同设施分别在 8:00、10:00 开放，范围都为 global；客人问一楼，选择 answer=[1]、related=[2]。
- 相邻两字 Jaccard 重合度为 **0.7142857**，高于 0.6；当前核验 missing / conflict_time，最终客人只收到未确认，8:00 被误删。
- 局部旧版本对照：从 2e2f33b 提取旧 verify_selected_evidence 函数，配当前公共辅助函数，同输入为 grounded / selected。此对照只证明该函数规则引入了误拦，**未运行旧版完整回复流程**。

**调用路径 P5：** _same_question 只计算标准问句二元组重合 → verify_selected_evidence 把二楼候选加入一楼冲突组 → 不同开放钟点被当成同一对象矛盾 → 选证据为 missing → 最终未确认。

源码位置：[_bigrams](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:557)、[_same_question](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:873)、[同问题冲突组](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:975)。

**影响：** D-F2 用两组对照校准只证明完全同问真冲突能拦、明显异问能放，未证明主体不同而句式相同的情况。代码 ponytail 注释只记录措辞差异造成的漏组，本轮已证实另一侧的误组。

**最小方案：** 先约束同一主体及适用范围，再允许模糊问句信号参与合组；优先复用已有 scope、房源、标准问题和可验证的对象信息。对象不一致或仅因共享句式相似时，不据此制造钟点冲突。保留同一标准问题的真实矛盾检查，不能恢复靠 related 标签排除冲突。不要只调高阈值直到这个例子通过；下一轮 Spec 应明确“主体不同”的限制与无结构化主体时的判定上限，再确认 D-F2 修订。

**验收：** 同主体 8:00/10:00 的全部标签分配仍 missing；不同楼层、不同区域或不同设施的不同钟点可正常作答；保留安静时段/客厅、顺带提及其他收费和有效期政策对照。不能用两组锚点声称泛用校准已完成。

## 3. 泛用性方案与 Claude §8 的答复

“让模型做选择题”仍是正确方向：计划种类、知识 ID 和动作类型由有限契约约束。**但选择题能成立的前提，是本地给出的选项确实适用于该项，而且所有回退和组合出口遵守同一边界。** F1、F2 不需要新增同义词，F3 也不需要再要求模型“别漏答”：本轮主调用甚至没有执行。F4 是确定性分类合并，F5 是把相似度当作主体身份。

| Claude 请求复审项 | 答复与下一步 |
| --- | --- |
| §8.1 三者组合能否忽略寒暄 | 不能据此接受目前实现：E3 丢的是稳定景点独立子问。先修逐项覆盖；是否省略纯寒暄可另按产品表达规则处理。 |
| §8.2 全动作判定会否漏掉费用子问 | 收窄偏差 F1 有候选门禁退步证据，可保留已有保护；但若规划把“送毛巾 + 多少钱”压成单动作，漏项风险仍取决于计划完整性。本轮未对该规划误分构造生产复现，不能写成已确认新 bug；应加入独立组合验收。 |
| §8.3 整句触发词是否足够 | 不够，E2 已复现项间借条件。先修候选适用性。 |
| §8.4 阈值与样本是否足够 | 不够，E5 已复现不同主体误冲突；先补主体边界和正反例，再校准近似上限。 |
| §8.5 三条真实收发与日志 | 可作为那三个具体场景的历史收件证据，不能覆盖 E1–E5。INFO 缺口影响 D5，应在依赖线上失败率/耗时监控的验收前补齐；本报告不扩展为日志实现。 |

建议把 F1/F2/F3 作为同一个逐项事实与覆盖契约修复单元：先确保每项候选合法，再让选择与现行回退共用该候选，最后按实际覆盖接续其余项。F4、F5 各在已有共享函数修复，不引入新的规划器、平行检索器、通用解析框架或针对这些句子的词表。

D1 独立改写集仍未完成。公开的 E1–E5 与旧 E1–E6 后续只能作为回归。陌生改写验收至少区分意图识别、逐项候选合法性、最终回答完整性和实际路由；加入标点/空白变化、主体/房间变化、三项组合与字段缺失。当前证据不能宣称达到陌生改写 95% 通过率，也不能把首轮门禁通过数当成该指标。

## 4. 验收证据缺口与本轮验证范围

### 4.1 PostgreSQL 验收仍有缺口

- [test_main_reply_wait_after_soft_review_holds_no_lock](/Volumes/02/code/homestay-bot/tests/integration/test_guest_reply_postgresql.py:309) 确实暂停主回复、让另一事务锁会话并写入新客人消息，再断言旧结果不登记出站/任务。它补了“主回复等待 + 新客人活动”这部分。Claude 报告去掉提交后锁超时的变异结果有判别力；本轮只核对测试代码，没有独立重跑该 PostgreSQL 结果。
- 同一暂停主回复边界下的**员工接管**未在这个新增用例中验证，不能用其他阶段的接管用例自动代替修复 Spec §2.7 的组合要求。
- [test_replayed_debounce_job_does_not_duplicate_side_effects](/Volumes/02/code/homestay-bot/tests/integration/test_guest_reply_postgresql.py:369) 先完整 process_debounced_message 并 commit，再重跑完整作业。它证明完成后的重放幂等，**没有在首次中途提交之后中断**，没有 recover_stale，也未重新领取一个真实 RUNNING 作业。测试注释中的 recover_stale 不能当作执行证据。
- 最小补证：在真实第一次提交之后、尚未登记后续副作用的边界暂停并取消；回滚未提交事务，重建会话/服务续接同一来源作业。分别断言最终作业、客诉复核、分析作业、业务任务和客人/员工出站的具体去重键及数量。若还声称 recover_stale 已覆盖，必须真实构造并恢复相应作业状态，而非仅直接重调服务。
- 隔离 PostgreSQL 专项与员工接管并发属于对应风险验证；不要求因此额外跑不相关测试或生产数据库。

### 4.2 已有验证能证明什么

| 证据 | 本轮核验 | 边界 |
| --- | --- | --- |
| 三个相关 unit 文件 | 93 passed，6.31 s；命令见下 | 证明已有计划、适配器和会话回归；不能消除新反例 |
| E1–E5 离线探针 | 审查时复现；写交接报告时合并成 §5 命令再次执行，退出 0 | 退出 0 表示缺陷已复现；修复验收应改断言为正确业务契约 |
| Claude 12 项 SHA-256 绑定 | 与当前工作树全部匹配 | 证明报告对应本地源码，不证明生产安装包 |
| rc-1.69.1-2 已保存门禁产物 | verdict passed、无退步，首轮 137/154；安全已知失败为空 | .stage/reply-gate-rc-1.69.1-2.json 属历史真实模型结果，当前未再外调；不包含本轮五项新验收 |
| 作者本地全量、CI、PostgreSQL、部署 | 引用返回报告与发布记录 | 未独立重跑全量/PG、未查询当前 CI 或服务器 |
| 三条测试号实收 | 作者报告含用户收件确认 | 历史样本只证静态+稳定、开心复核、毛巾登记；没有本轮手机侧复验 |

相关回归命令：

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/unit .venv/bin/pytest -q -p no:cacheprovider \
  tests/unit/test_turn_plan.py \
  tests/unit/test_deepseek_turn_plan.py \
  tests/unit/test_conversation_plan_flows.py
~~~

上述 93 项结果来自本轮审查，写文档期间源码与测试输入未变，按风险验证规则复用，不因交接重复跑。没有在本轮调用真实 DeepSeek、Hostex、企业微信，没有生产读写、消息发送、任务取消、发布或部署。

关于应用 INFO 日志：[configure_logging_redaction](/Volumes/02/code/homestay-bot/src/homestay_bot/logging.py:177) 只安装脱敏过滤器，不设置 INFO 级别；这是源码依据。Claude 所述容器只见访问日志仍是作者的现场观察，本轮未读生产日志，不能把根日志器初判当作完整运行根因。日志修复应独立确认 Spec，并核验真实处理器、级别、传播与脱敏，避免只开级别却没有输出或泄露内容。

## 5. 可独立运行的离线复现命令

在本仓库根目录、已有 .venv 和当前测试依赖下执行。合成问句、知识及账号无真实客户数据；命令不连接数据库、不调用外部模型或平台。F3 的知识/天气为固定替身；其他项的知识适用性使用真实 KnowledgeService。该脚本**断言缺陷现状**：修复后旧缺陷断言失败是预期，正式回归应使用 §2 的正确契约。

~~~sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:tests/unit .venv/bin/python - <<'PY'
import asyncio
import ast
import json
import subprocess
from homestay_bot.domain.enums import Language
from homestay_bot.services import knowledge_evidence_policy as policy
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.context_retention import CustomerModelContext
from homestay_bot.services.knowledge_service import KnowledgeService, KnowledgeSnippet
from tests.plan_helpers import plan_for
from tests.unit.test_knowledge_service import KnowledgeRow
from tests.unit.test_deepseek_turn_plan import _assistant, _decision, _Knowledge, HAIRDRYER
from tests.unit.test_conversation_plan_flows import _guest_turn
from tests.unit.test_conversation_service import (
    AssistantStub, ComplaintReviewStub, build_service, incoming,
)

class Repo:
    """只返回合成审核知识，不连接数据库或外部服务。"""
    def __init__(self, rows):
        """保存测试行；所有显式房号均映射失败。"""
        self.rows = rows

    async def list_active(self):
        """模拟已启用知识的仓储输出，保留真实知识服务的适用性过滤。"""
        return self.rows

    async def find_property_by_room(self, room):
        """构造未知房号，不把客人的自报房号认定为住宿。"""
        return None

def row(number, question, answer, **fields):
    """构造双语字段完整的合成知识行。"""
    return KnowledgeRow(
        id=number, category="设施", question_zh=question, answer_zh=answer,
        question_en=question, answer_en=answer, **fields,
    )

async def final(text, plan, assistant, context=None):
    """经过真实适配器，再交真实会话服务处理最终客人正文；模型和平台用替身。"""
    assert plan.ok
    decision = await assistant.respond(
        guest_identifier="synthetic-guest", language=Language.ZH,
        messages=[{"role": "user", "content": text}],
        turn_plan=plan, customer_context=context,
    )
    _, wecom, _ = await _guest_turn(
        text, AssistantStub(decision=decision, plan=lambda _: plan)
    )
    return decision, wecom.guest_messages[-1]

async def main():
    """断言当前五个缺陷确实出现；正常退出表示复现成功，不表示修复通过。"""
    text = "909房间几点退房"
    plan = plan_for(text, ("static_fact", text, {"target_room": "909"}))
    context = CustomerModelContext(confirmed_stay={
        "property_id": 401, "check_in_date": "2026-10-08", "check_out_date": "2026-10-09",
    })
    knowledge = KnowledgeService(Repo([
        row(401, "401房间几点退房", "401房间退房时间为12:00。",
            scope="property", property_id=401),
    ]))
    bad = _assistant([_decision(evidence_selection=None)], knowledge)[0]
    _, absent = await final(text, plan, bad, context)
    selected = [{"item_id": 1, "answer_ids": [401], "related_ids": []}]
    control = _assistant([_decision(evidence_selection=selected)], knowledge)[0]
    _, bounded = await final(text, plan, control, context)
    assert "401房间退房时间为12:00。" in absent and "12:00" not in bounded
    print(json.dumps({"evidence": "E1", "absent_selection": absent,
                      "selection_control": bounded}, ensure_ascii=False))

    text = "白天停车多少钱，晚上几点以后要保持安静"
    plan = plan_for(text, ("static_fact", "白天停车多少钱"),
                    ("static_fact", "晚上几点以后要保持安静"))
    parking = row(1, "本店停车多少钱", "本店停车每次20元。", keywords=["停车"])
    parking.trigger_any = ["晚上"]
    knowledge = KnowledgeService(Repo([
        parking, row(2, "晚上几点以后要保持安静", "22:00以后请保持安静。"),
    ]))
    whole = await knowledge.retrieve(Language.ZH, text)
    item = await knowledge.retrieve(Language.ZH, "白天停车多少钱")
    selected = [{"item_id": 1, "answer_ids": [1], "related_ids": []},
                {"item_id": 2, "answer_ids": [2], "related_ids": []}]
    assistant = _assistant([_decision(evidence_selection=selected)], knowledge)[0]
    _, body = await final(text, plan, assistant)
    assert 1 in [s.source_id for s in whole] and 1 not in [s.source_id for s in item]
    assert "20元" in body
    print(json.dumps({"evidence": "E2", "whole_ids": [s.source_id for s in whole],
                      "item_ids": [s.source_id for s in item], "final": body},
                     ensure_ascii=False))

    class Weather:
        """返回合成天气，不联网。"""
        async def search(self, **kwargs):
            """只提供本轮天气项的结果，不回答稳定景点项。"""
            return "今天晴，气温20°C。"

    text = "房间有吹风机吗，今天天气怎么样，武汉有哪些经典景点推荐"
    plan = plan_for(text, ("static_fact", "房间有吹风机吗"),
                    ("external_info", "今天天气怎么样"),
                    ("external_info", "武汉有哪些经典景点推荐"))
    assistant, calls = _assistant([_decision(
        reply_text="武汉推荐黄鹤楼、东湖。",
        item_answers=[{"item_id": 3, "text": "武汉推荐黄鹤楼、东湖。"}],
    )], _Knowledge([HAIRDRYER]))
    assistant._tourism_searcher = Weather()
    _, body = await final(text, plan, assistant)
    assert not calls.requests and "20°C" in body and "黄鹤楼" not in body
    print(json.dumps({"evidence": "E3", "main_calls": len(calls.requests),
                      "final": body}, ensure_ascii=False))

    for text, expected_bad in [
        ("我要退款，投\n诉到平台", ("refund", "high")),
        ("我要退\n款，不然投诉到平台", ("complaint", "critical")),
    ]:
        reviews = ComplaintReviewStub()
        service, _, _, wecom = build_service(
            complaint_service=ComplaintService(), complaint_reviews=reviews,
        )
        await service.handle_message(incoming(content=text))
        observed = reviews.calls[0][2:]
        assert observed == expected_bad
        print(json.dumps({"evidence": "E4", "text": text, "review": observed,
                          "staff": wecom.internal_messages}, ensure_ascii=False))

    first = row(1, "民宿一楼健身房什么时候开放？", "一楼健身房8:00开放。")
    second = row(2, "民宿二楼健身房什么时候开放？", "二楼健身房10:00开放。")
    snippets = [KnowledgeSnippet(source_id=r.id, category=r.category,
                                question=r.question_zh, answer=r.answer_zh, scope="global")
                for r in (first, second)]
    text = first.question_zh
    plan = plan_for(text, ("static_fact", text))
    knowledge = KnowledgeService(Repo([first, second]))
    selected = [{"item_id": 1, "answer_ids": [1], "related_ids": [2]}]
    assistant = _assistant([_decision(evidence_selection=selected)], knowledge)[0]
    _, body = await final(text, plan, assistant)
    current = policy.verify_selected_evidence(text, [1], [2], snippets)
    old_source = subprocess.run([
        "git", "show", "2e2f33b:src/homestay_bot/services/knowledge_evidence_policy.py",
    ], check=True, capture_output=True, text=True).stdout
    old_function = next(n for n in ast.parse(old_source).body
                        if isinstance(n, ast.FunctionDef) and n.name == "verify_selected_evidence")
    namespace = dict(vars(policy))
    # 只对比旧核验函数，公用辅助函数取当前版本；不能据此声称跑过旧版完整流程。
    exec(compile(ast.Module(body=[old_function], type_ignores=[]),
                 "<old verifier only>", "exec"), namespace)
    old = namespace["verify_selected_evidence"](text, [1], [2], snippets)
    left, right = policy._bigrams(first.question_zh), policy._bigrams(second.question_zh)
    overlap = len(left & right) / len(left | right)
    assert current.status == "missing" and old.status == "grounded" and "8:00" not in body
    print(json.dumps({"evidence": "E5", "overlap": overlap,
                      "current": [current.status, current.reason],
                      "old_function_only": [old.status, old.reason], "final": body},
                     ensure_ascii=False))

asyncio.run(main())
PY
~~~

对应观察摘要：E1 的 null 选择发出 401 的 12:00，对照不发；E2 整句 [2,1]、单项 [] 仍发 20 元；E3 主调用 0 次且无景点；E4 两次复核分别为 refund/high、complaint/critical；E5 重合 0.7142857、当前 missing/conflict_time、旧核验函数 grounded/selected。任何输出变化都应先重新判断绑定与前提，不得为让“复现成功”强行恢复缺陷。

## 6. 工作区归属、源码摘要与下一轮交付

本轮开始时已有以下工作区状态，均保留：.aoci/baseline.json、aoci.code.txt 与 Claude 返回报告已有修改；.impeccable/critique/ 为未跟踪目录。Codex 本轮新增本报告，并按项目规则维护它对应的 AOCI 托管条目/基线；不把已有改动归为本轮业务修复。未读取受保护的 YuMi民宿AI项目总结.txt。

以下摘要于写本报告时重新读取；行号只对应这些字节，接手前先核对目标内容。报告内不复制真实生产消息或凭据。

| 文件 | SHA-256 |
| --- | --- |
| docs/reviews/2026-10-07_claude-to-codex-reply-generalization-b24-fix-handoff.md | d902cb6c9545a7b41834c26924d5416bde410de9dfb4afe8e50d76a7c5bfe117 |
| docs/specs/2026-10-07_reply-generalization-b24-fix-spec.md | 29a1441bee4e3c9e26bd13a9e7aaf6953d7b716b8dde1f62d0b501114c023c07 |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_service.py | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/answer_policy.py | 01561d21304f2058132de2ba7603ca4ae20546d949417f929ebbbd2143a4e4fe |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/complaint_service.py | 4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef |
| src/homestay_bot/services/turn_plan.py | 4f2f93caa43929ba50cc762811b0b26bd279cd3be6918841081e907f42d873d6 |
| tests/unit/test_turn_plan.py | 0397407966c04fb13cff99d852713eb7c3ef6ae0914768e762d26f5f609bb7c9 |
| tests/unit/test_deepseek_turn_plan.py | 0527ece822ceeb253d9f7632945b40b602e3e13818ead063466b830c3d46fb1c |
| tests/unit/test_conversation_plan_flows.py | 925413407f560abf24951f1cad334f05450c48cc2bc24e5eb8d7afbc1dc5dccb |
| tests/integration/test_guest_reply_postgresql.py | 33151cd8f979211b88f0218c85777f5166b8e14d16ba712abe952325a380b440 |

下一轮 Claude 需交付：

1. 对 B24-F1–F5 逐项独立复核；不同意时给相同输入、源码路径、最终正文/路由的反证，不能仅引用已有全绿用例。
2. 更新修复 Spec，明确每项候选适用性、所有格式回退、未覆盖项组合、硬理由合并与不同主体冲突边界；保留此前已闭合的危险失败处置和字段类型校验。
3. 仅在获得本轮完整 Spec 确认与“开始”后编码；在已有相关测试中补最小、有判别力的回归和 §4.1 必要的隔离 PostgreSQL 补证。
4. 修复报告逐项列出改前失败、改后正确行为、文件/符号、仍未覆盖的边界及输入绑定。改变公共回复/主模型提示时，真实模型全量门禁仍需当次明确授权；未跑不能写通过。
5. 保留 D1 独立改写集、D5 生产可观测性及真实收件的独立证据边界。本报告与 AOCI 对齐不代表业务修复、发布或生产验收通过。

禁止下一步直接整文件回退、把本轮反例补成词表、靠提高阈值掩盖主体错配、吞异常或放宽候选适用性以追求通过数。生产任务 968 的取消留在管理员授权边界，本轮不操作。
