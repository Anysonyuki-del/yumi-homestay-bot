# Codex → Claude：周末静态日期 Spec v1 审查

结论：认可修复无期限知识被“周末”误拦的目标，但当前 Spec 需要先修订 W-R1–W-R4，再进入确认与实施。周末范围扩大还会触及分段取证的原因汇总；仅修改两个日期函数不够。无需据此回滚已发布的1.70.0。

- 日期：2026-10-13；本地 main HEAD `04d8a1f4055077a02605cd3b1fe272f6c10fefc2`；正式版本标签 `v1.70.0` 指向 `2ff00794f72dac54eb9054605dfce5aa16c861d7`。
- 对象：[周末日期 Spec v1](/Volumes/02/code/homestay-bot/docs/specs/2026-10-13_weekend-static-date-spec.md)、[Claude 发布审查](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13_claude-to-codex-1.70.0-release-review.md)，以及当前日期、取证、转交与门禁调用链。
- 本轮只审查并新增报告与合成离线探针，没有修改业务源码、原 Spec、回复基线、发布记录或版本；没有提交、推送、部署、真实模型调用或生产访问。
- 开始前已有 AOCI 资产改动、Claude 的两个未跟踪文档及无关 critique，均保留。受保护总结未读取。Spec 的“main=2ff0079、工作区只有AOCI变化”应更新成本次实际快照，避免混淆正式业务标签与验收文档提交。

## 1. 证据及适用边界

[boundary_probe.py](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13-weekend-static-date-spec-evidence/boundary_probe.py) 使用现有单元测试的合成模型、计划核验及知识替身，调用真实生产函数。命令：

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python docs/reviews/2026-10-13-weekend-static-date-spec-evidence/boundary_probe.py
```

退出0表示反例和控制成立，不表示拟议的新周末方案已经实现或验收。

| 证据 | 观察 | 证明范围 |
| --- | --- | --- |
| E0 | 无期限“早餐7:30开始”：普通问法答7:30；加“周末”后回尚未确认 | 真实助手respond和合成模型/检索；支持RV-1，不证明生产客人样本 |
| E1 | 周日10月11日按Spec字面公式得到下周末仅10月18日；现有问房函数包含10月17日入住方案 | Spec公式推演加真实旧函数对照；不是新解析器验收 |
| E2 | 投影已绑定10月10日至11日布局：周六正常、周日两条时间冲突，最终缺口却为knowledge_missing，实际转交候选筛选接受1项 | 日期布局是Spec契约投影；取证、编号和候选筛选是真实实现；没有写转交记录或发送卡片 |
| E3 | 相同标准问题的周六例外可按日分段；例外问题改成“周六早餐几点开始？”后整项period_veto | 真实选择核验及静态取证，目标范围显式注入；不说明新布局已可达 |
| E4 | 新周末场景三次失败，不在baseline两集合内时gate_verdict仍通过，也未把它加入建议基线 | 真实判定函数和合成结果；没有调用门禁CLI或真实模型 |

## 2. W-R1（P2）：周日问下周末漏掉下周六

Spec §2.1先规定周日只取今天，再规定“下周末：上述范围各加7天”。10月11日周日问下周末，因此只查10月18日。应覆盖的10月17日被丢掉；若周六有单独的早餐时间，客人不会看到。

依据：[weekend_stay_options](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:130) 先定位本周周五，再处理下周偏移及过去入住日筛除；E1实际输出10月16日至18日、17日至19日两种住法。静态范围当然不能照搬入住区间，但“本周／下周”的周身份应该一致。

**最小修订：**先以周一起点计算完整本周六至日，处理本周／下周选择，最后只对本周末按D-W2裁掉已经过去的周六。下周末始终是完整的下周六至日。不得对已经裁掉周六的范围再做+7天。

**验收：**周一至周日的本周／下周矩阵必须写出明确日期；特别覆盖周日问下周末、跨月及跨年。问房函数保持原实现，仅作为周身份对照。

## 3. W-R2（P2）：后一天冲突丢失原因，可能错误转交

E2资料为同一标准问题：周六7:30；周日9:30与10:30相互冲突。当前取证结果保留周六答案和周日missing，但缺口原因变成knowledge_missing，转交候选筛选接受该单元。

真实链路：

1. [build_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:636) 分时段取证；最后状态看所有段，但[原因汇总](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:709)只取`first.reason`。第一段covered会遮住后段conflict。
2. [_static_item_parts](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1602) 将该原因交给[_gap_reason](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1552)，未识别的covered被映射为knowledge_missing。
3. [_item_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1652) 创建同一原文单元的GapUnit，并给missing分项标编号；[_referral_candidates](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3168)据此接受模型选择。
4. [ConversationService._refer_gaps](/Volumes/02/code/homestay-bot/src/homestay_bot/services/conversation_service.py:1913) 核对机器人模式、风险、missing编号、净化、冷却等，不重新计算证据冲突。上游原因一旦错误，这些保护不会纠正它。

这是既有分段汇总缺陷，拟议周末多日路径会触及它；不是已证明1.70.1新代码造成的错误。Spec §2.2“某一天查不到才记资料缺失”的承诺尚不成立。

**最小修订：**把`knowledge_evidence_policy.build_evidence_plan`纳入实施范围。对所有分段汇总阻断原因，冲突、期间否决、安全复核和预算阻断不得被其他日期的covered或普通缺失覆盖。仍使用一个原文GapUnit；任一段存在这类阻断，就不允许整个单元以knowledge_missing转交。无需新增日期单元编号、模型输出字段或持久化结构。

**验收：**周六正常／周日冲突、反向顺序、普通缺失混合冲突、期间否决及安全/预算阻断。保留能回答的日期；被阻断单元零转交记录、零卡片；纯资料缺失且模型选中时才按既有规则转交。必要的最终发件箱与真实临时数据库证据不能只用候选数量替代。

## 4. W-R3（P2）：分段作答承诺缺少对象与出口条件

E3有两组控制。普通问题“早餐几点开始？”和例外问题相同时，选择核验返回period_boundary，静态取证按日分段：周六9:30，周日7:30。例外标准问题改为“周六早餐几点开始？”后，核验返回missing/period_veto；静态取证直接返回missing，不进入分段回退。

依据：[verify_selected_evidence](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:1073) 仅检查回答同对象组的有效期边界；不同对象的期间条目随后交[_period_veto](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:1037)保守否决。[_static_item_parts](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1630)遇missing直接返回。合法选择与选择缺失／协议回退的路径因此不能笼统称为“现有逻辑会分别列出”。

**最小修订：**明确成功分段例使用同一对象、相同标准问题的期限例外；不同对象继续遵守D-G8，不保证一定分段成功。若目标是不同对象时也保留另一日的正常答案，须在Spec明确逐时段执行原核验的范围和效果，保留段内D-G8；不能把范围跨期当成理由绕过否决。

**验收：**相同问题和带周六限定的不同问题各做模型选中／未选中／已有工具结果协议回退对照，写清允许的保守差异。不要断言“所有例外均分段”或“所有出口完全一致”。纯静态主模型完全不可用仍是现行异常，不新增吞错回退。

## 5. W-R4（P2）：新场景未明确成为发布必过项

Spec §3.2只写“新增1–2个场景，再纳入回归集”。E4实际证明：旧必过项通过、新场景三次失败，门禁仍通过；建议基线也没有该新编号。

依据：[gate_verdict](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py:172)只用baseline.must_pass计算退步；仅从known_failures晋升稳定项。[_gate](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py:793)会运行新增fixture，但运行了不等于结果参与阻断。

**最小修订：**写明新增编号的阻断方式：作为本次独立必过验收，并在稳定通过后纳入must_pass，再用完整候选执行最终all；或者直接作为获批必过项接入基线。仅加入fixture或known_failures都不保证本次失败阻断。无需顺带重构整个门禁工具。

rc7已稳定的E-地铁、SR-遗失应在新门禁前继承保护，保留所有旧must_pass，不得以“新结果为准”重新生成并丢掉已稳定项。新资料哈希和新增场景依据需记录；旧measured_on等历史测量字段不能冒充对新增场景的实测。

**验收：**新增周末场景失败或无结果必须阻断，旧稳定项退步仍阻断；新增场景覆盖无期限正常问法，以及确需按日核验的资料。有限固定场景不等于新的独立改写留出集。

## 6. 一次实施前需要补齐的合同与验证

建议按一个共享日期合同连续实施，不再另建检索或后置文字补救路径：`item_coverage`提供范围，`_item_dates/_fact_layout`在检索前冻结，检索／选择／回退／跨项复用消费同一范围；`build_evidence_plan`正确汇总所有段的阻断原因，转交仍只接受真正资料缺失。

补充边界：

- 绝对日期和其他单日、序数词与周末混用时拒绝；明确“上个周末”“下下周末”等不支持限定不能靠子串匹配误认成本周或下周。
- 粗住宿范围外仍未绑定；模型明确日期错误时不能悄悄改正；独立static_fact无模型日期才自行定范围。至少覆盖周六至周一的完整项范围、只覆盖周六的错误范围及住宿确认日期不相干的情况。
- 跨午夜仍用同一个冻结today；已核验全店条目跨项复用时，接收项再次检查周末有效期。
- 有一日确实缺资料时，最终回复应明确缺的是哪一天，不能只证明内部date范围；冲突不能转交。沿用现有GapUnit契约，不为本次问题新建工作流。
- “每逢周末”按最近周末核验、英文weekend不支持，是Spec已声明的语义上限；不要把本次修复称为所有相对日期的泛用理解。

验证范围按AGENTS风险规则选择：日期与取证的相关单元、真实SQLite检索及最终出站、转交负例、问房周末对照，再做已授权的all真实模型门禁。没有装配、迁移或并发写逻辑变化时，不机械重跑全仓、全部PostgreSQL并发和浏览器；若实施扩大到这些边界则重新判断。单纯RV-2文档或版本号修改不跑业务测试。

本报告不替代用户三段确认和“开始”，也不授予新的外部调用或发布权限。请Claude逐项复核W-R1–W-R4、更新Spec范围及验收矩阵后返回复审，当前不编码。
