# Claude → Codex：第二轮修复 Spec v4 复审请求（V3-R1、V3-R2、C1、C2）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec v4 [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
  - 用户已决定 D-G9、D-G10 均取推荐项；
  - D-G7 选择先交 Codex 再审，三段确认留待复审之后；
  - 编码、门禁、提交、部署均未授权。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v3-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v3-review-handoff.md)（V3-R1、V3-R2、C1、C2）。
- 本轮没有修改业务代码，没有调用真实模型或外部服务，没有连接数据库或生产。

## 1. 对 V3 报告的复核

报告 §5 的探针在 main `80f9b2c` 上运行，退出 0，E-V3-1、E-V3-2、E-V3-3、E-V3-C1 输出与报告一致。四项全部同意，已写入 Spec §1.3。

另外用现有正向识别函数做了一次本地试算（写在 Spec §1.3）：
- `asks_room_price`、`asks_stay_availability`、`_should_force_property_catalog` 可以把粗粒度项的小句分别归到房价、房态、目录。
- 「有哪些房间可以看江景」同样命中目录识别，主题为空、属性只有 unknown。所以「目录命中且没有其他信号」仍会丢掉限定，这是 D-G9 选「无剩余」而不选「无信号」的原因。

## 2. v4 的逐项修订

| 缺口 | v4 方案（Spec 位置） | 用户决定 |
| --- | --- | --- |
| V3-R1 | 工具与澄清分项只覆盖它实际回答的那类事实。复用 §2.1(3) 的切句，取与该项区间相交的小句，每个小句按现有正向识别得出所需事实（可多类）：房价要该项房源、日期相符的参考价分项或房价日期澄清，参考价工具的明确失败说明也算回答；房态要该项房源、日期相符的房态分项；其余小句及带本店事实信号的小句走该项合法候选或未确认。全部小句、全部所需事实都有分项才算整项被工具覆盖，否则只对缺口小句补答案或未确认（§2.1(2)） | — |
| V3-R1 出口 | `availability_fallback` 不再拼整轮 `evidence_plan`，遵守同一契约；需模型回答的项在该出口按「模型漏答留空」处理（§2.1(2)） | — |
| V3-R1 后果 | 无日期的房态小句不再由模型自由正文追问，改为扩展现有房价日期澄清出口，追问入住与退房日期；实施时先用测试确认现行无日期房态的实际出口（§3.3 D-G10） | D-G10 推荐项 |
| V3-R2 | 删除「主题识别为空就由目录替代」。目录识别命中，且去掉目录词与纯形式词（代词、「有/都/哪些/介绍一下」，不收领域词）后小句没有剩余内容，才由目录分项回答；其余保留审核答案或未确认，目录照常列出。识别信号只用于保守拒绝（§2.1(2)、§3.3 D-G9） | D-G9 推荐项 |
| C1 | 上限更正：普通候选不因同主题互判冲突、不同对象不自动替换；D-G8 是保守否决，仍可能误拦两楼早餐厅、堂食与打包这类无关平时答案，F5 误拦在该子范围未消除；负例单列，SC-国庆早餐不单独证明全部期间场景（§2.3(3)、§3.1、§3.2） | 维持 D-G8 |
| C2 | 上限更正：同对象但归一化问法不同的冲突可能漏检，包括两条同时被选中时；模型选中不证明对象身份；同问法真冲突保留强验收（§2.3(3)、§3.1） | — |
| §4 Worker | 窗口二主验收用真实 `Worker.run_once`，按报告建议的最小构造：真实 PG 队列仓储，checkpoint 由队列会话提交，handler 用独立业务会话；业务提交后、handler 返回前暂停，取消并等待退出。手动标记只作额外仓储对照（§2.4(2)） | — |
| 复审问题 2 | 对象约束开关只用于计划成功的逐项回退，计划失败或无计划保留现行整句风险，不宣称全部事实路径已隔离（§2.1(2) 末条，未改） | — |

## 3. 请 Codex 重点复审

1. **D-G9 的形式词清单。** 清单只收无事实含义的词（代词、「有/都/哪些/哪种/什么/几种/介绍一下」、语气词，以及英文 what/which/do/you/have/any/show/list 等）。试算里「你们有哪些房型」「what rooms do you have」「房型介绍」无剩余，「有哪些房间可以看江景」剩「看江景」。漏收形式词只会让纯目录问法多一句未确认，是安全方向的失败。请看这个清单的边界是否足够窄，以及「哪种房间最便宜」剩「最便宜」后由房价识别接管是否合理。
2. **小句级覆盖与一次切句的耦合。** 房价、房态识别在小句上跑，切句沿用 §2.1(3) 的同一结果。一个小句同时问房价与房态（如「401住一晚多少钱还有房吗」不含逗号）时，按「可多类」要求两个分项都有。这一点是否足够，还是需要另外的约束。
3. **D-G10 扩展日期追问。** 现有出口 `price_clarification` 只在房价无日期时触发，文案提到参考价。扩展到房态时新增一句对应文案，覆盖条件按小句判定。这是 G1 失败关闭的直接后果，请看是否越出本轮范围。
4. **`availability_fallback` 下的模型回答项留空。** 该出口发生在主模型失败之后，需模型回答的项没有答案。沿用「模型漏答留空」是否可接受，还是应对这些项给出统一的未确认。

## 4. 工作区状态

- 本轮修改：Spec v4；新增本报告。
- 之前未提交、继续保留：
  - Claude 的 fix2 v1、v2、v3 交接报告；
  - Codex 的四份 2026-10-08 复审报告；
  - 上一轮 Claude 报告的补充；
  - `.aoci/baseline.json`、`aoci.code.txt`。
- `.impeccable/critique/` 不属于本工作。

## 5. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 06cb6e51a9a7357b9340970ab05477908a19ec82a8212290736219d1a3239792 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v3-review-handoff.md | 2dbdaa5ce84ba2361e59e2ea6bc7c15f0cf487b593d0f2c13f389e74e406ab83 |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/answer_policy.py | 01561d21304f2058132de2ba7603ca4ae20546d949417f929ebbbd2143a4e4fe |
| src/homestay_bot/worker.py | 95f77f6e307f92a51efdb1bc5299d01695041df83bf030abed3df5a66e287257 |
| tests/integration/test_guest_reply_postgresql.py | 33151cd8f979211b88f0218c85777f5166b8e14d16ba712abe952325a380b440 |
