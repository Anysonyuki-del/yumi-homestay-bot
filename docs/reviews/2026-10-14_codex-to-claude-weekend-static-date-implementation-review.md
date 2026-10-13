# Codex → Claude：1.70.1 周末静态日期实施复审

结论：先修复 I71-R1（P2）再进入真实模型验收。共享日期、分段阻断、ISO展示、预算与基线继承主体符合v4，但周末识别仍会把未支持的日期限定截成裸“周末”，给出错误日期的审核答案。无需重做取证或转交工作流。

- 日期：2026-10-14；审查基线为main `04d8a1f4055077a02605cd3b1fe272f6c10fefc2`及当前未提交改动。
- 对象：[Claude实施报告](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-14_claude-to-codex-weekend-static-date-implementation-handoff.md)、[Spec v4](/Volumes/02/code/homestay-bot/docs/specs/2026-10-13_weekend-static-date-spec.md)和实际源码、测试、场景及发布记录。Spec摘要仍为 `e3d42f07c6bd0960a657d7857f799bdd6798cfa7783ddc68288543e6b8887298`。
- 本轮是Codex主代理对Claude实施的复审，未使用独立子代理。只新增本报告、离线证据及AOCI收尾，未修业务代码或改原交接；未提交、推送、部署、调用真实模型、访问生产或发送消息。

## 1. I71-R1（P2）：未支持限定被截成裸周末，产生错日事实

**位置：**[item_coverage._WEEKEND](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:119)及[relative_calendar_weekend](/Volumes/02/code/homestay-bot/src/homestay_bot/services/item_coverage.py:294)。影响调用方为 `static_clause_dates` 与 `DeepSeekGuestAssistant._item_dates/_fact_layout`。

**根因：**周末正则的限定词是可选的；遇到不能消费的前缀，就向后搜到裸“周末”，把空限定映射成本周。前面的 `_RELATIVE_DATE` 不认识“下周的”“上一个”“下个月”，因此不会拒绝这些输入。仅拦“上周末／下下周末”几个连续词形没有形成完整限定边界。

**实际复现：**冻结今日为2026-09-25，经门禁真实 `KnowledgeService`、`_MemoryKnowledge` 房号映射、助手respond及本地取证，仅规划和模型响应为合成输入：

| 原问 | 当前最终行为 |
| --- | --- |
| 9999房间下周末早餐几点开始？ | 没有9月26/27日答案，未确认，作为正向控制 |
| 9999房间下周的周末早餐几点开始？ | 返回 `2026-09-26：早餐8:30开始。` 和 `2026-09-27：早餐9:00开始。`，来源9901/9902、无缺口 |
| 9999房间上一个周末早餐几点开始？ | 同样返回未来9月26/27日的两条事实、无缺口 |

“下周周末”“下个月周末”也在纯函数及static_clause_dates中绑定为9月26/27日。这不是只看正则推测：错日答案已进入助手最终正文和ReplyEvidence。它没有经过持久化发件箱或真实外部投递，不能称为生产事故。

**契约依据：**Spec §2.1第4–5点要求先校验完整日期表达，未支持限定或其他相对范围拒绝，不能靠成功解析子串绕过拦截。当前“下周的周末”语义指向10月3/4日，既不能用本周资料回答，也不能因为知识条目已审核就接受错日。

**最小修复建议：**在共享 `relative_calendar_weekend` 一处收紧完整周末限定的识别边界：支持的限定必须被完整消费，存在未消费的时间限定就返回None；不要继续让可选限定的搜索在未知前缀后退化成裸“周末”。保持v4已有支持范围，先拒绝上述未支持表达，不在两处调用方各补词或新增通用日期解析器。若要正式支持“下周的周末”，应先明确对应别名合同，再映射到下一周；不能隐式当成本周。

修复时同时检查 `clause_dates`、单日解析优先级与两处调用方，保证被拒绝的表达保持scope_unbound、不取本周资料、不开放知识转交。补最小回归组：上述四种变体、有效的本周和下周别名、无关普通文字前置、单日混用；至少一个真实检索的最终正文用例使用只在本周有效的资料，断言零错日来源、零转交。正常和已有工具结果协议回退沿用同一冻结布局，不分别补规则。

不修的影响：客户稍改几个字就会得到日期明确却适用错误的答案；原缺陷由保守未确认变成了错误确认。修复遵循已有“未支持限定拒绝”合同，不要求重开整体架构。

## 2. 其他实施项的核查

| 内容 | 依据与结论 |
| --- | --- |
| 原因汇总 | `knowledge_evidence_policy._combined_reason/_blocking_rank`按复核、冲突、期间否决、预算选代表原因；`DeepSeekGuestAssistant._gap_reason/_referral_candidates`仍只允许knowledge_missing转交。未找到生产调用方依赖旧first.reason；“第一段正常、后段冲突”的相关单元与SQLite发件箱测试通过 |
| ISO日期与凭据拒绝 | `segment_date_label/split_segment_label`及`_unconfirmed_part/_apply_evidence_plan`先剥本地前缀再做专门拒绝，最终加回日期；门锁仍为out_of_scope。未改变ReplyEvidence日期或GapUnit编号 |
| 合并预算 | `build_evidence_plan`在合并审核答案后复查现有容量与静态总预算，超限清答案与来源并保留带日期missing；阻断原因不能降成可转交缺失。分段未确认展示符合§2.3/§2.4；在最多30晚的既有目标范围内评估，没有新增无界输入合同 |
| knowledge_gap_topic去重 | 当前 `_apply_evidence_plan`剥日期后去重；资料核实选择依据GapUnit和gap_referrals，未按该字符串创建日期记录。未发现本次去重造成转交合并或编号变化，保留原单元语义 |
| 正常与回退出口 | `_static_item_parts`和`_item_evidence_plan`共用冻结日期与候选。新增测试包含先查房后协议回退；不同对象D-G8保守否决没有放宽 |
| 场景与基线 | 对比HEAD，旧154个场景和旧知识条目逐项未变，冻结今日未变；新集合156，must_pass138、known_failures18，无重叠且完整覆盖。rc7保存的建议基线包含E-地铁和SR-遗失。新两项尚未真实测量，不算通过 |

Claude额外支持“这个周末”与“这周末”的语义相同，是低风险别名扩展，本轮不把它列为阻断项；交接已披露。处理I71-R1时保持这些合法别名控制，避免一律拒绝带前置文字而回退原修复。

## 3. 验证证据与复跑入口

本轮独立运行以下相关测试，**303 passed，10.57秒**，对应当前最终源码，未重复作者的全仓测试：

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q tests/unit/test_item_coverage.py tests/unit/test_reply_fix2_items.py tests/unit/test_knowledge_evidence_gate.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py
```

新[implementation_probe.py](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-13-weekend-static-date-spec-evidence/implementation_probe.py)保留限定词反例和真实检索控制，两条命令均退出0：

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python docs/reviews/2026-10-13-weekend-static-date-spec-evidence/implementation_probe.py
.venv/bin/ruff check docs/reviews/2026-10-13-weekend-static-date-spec-evidence/implementation_probe.py
```

探针断言当前缺陷出现，退出0不是修复通过；修复后相应缺陷断言应失败，再用正确行为回归证明闭合。303项通过也没有覆盖新增反例。

本轮核对的五个对象摘要与实施报告一致：

| 对象 | SHA256 |
| --- | --- |
| item_coverage.py | `7079f0d86997c80f9d0150baa3e9a08f27278d8890315f1457685605c64bc7ed` |
| knowledge_evidence_policy.py | `2ddb865d59131471d268e670544f211b4f5f03bb01f848c807cb9f8f5571a9f6` |
| deepseek_client.py | `91c7d7c3f2940e745f7e5388d4e1054a988c47fe81336cf3b1bbc7e633672f1d` |
| guest_reply_scenarios.json | `d4314a9c561c6c0d464999cf3142d1f93adf83e755a4e494f57e656a50c37cf5` |
| guest_reply_regression_baseline.json | `9c50323128ef649d1d52962a8d1631920d12aa9c6d8b68b8ab3a9e3f19d8cd23` |

## 4. 交接建议与剩余边界

先按已有Spec修I71-R1，补限定边界的失败证据及正常/回退控制，再做受影响验证与最终差异自审。不要求因这次解析修复机械重跑全部浏览器、PostgreSQL或全仓；若最终相关输入变化，更新证据身份并重跑受影响项。

真实模型仍需按§2.5执行新两项各三次稳定、晋升must_pass、最终all，不能以303项或作者全量代替。`scripts/release/reply_gate.sh`确实从标签取候选并在API容器临时目录运行，本轮没有据报告中的授权陈述执行候选提交或生产动作。

发布记录还应补清全量之后的源码变更：实施交接§4说明全量后“合并超预算清空来源”仅重跑211项，发布记录当前只列2973项。保留二者各自时序与范围，不把最终版本写成重新全量通过；这属于证据表述完善，不是第二项运行缺陷。

未独立复跑作者全量、全仓静态检查或变异实验，未执行真实模型、PostgreSQL竞争、生产页面及实际收件。修复前不建议进入候选真实门禁；审查通过后的外部步骤仍以当前用户明确授权为准。
