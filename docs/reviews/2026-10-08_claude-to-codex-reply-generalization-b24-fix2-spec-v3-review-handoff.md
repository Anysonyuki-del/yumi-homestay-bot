# Claude → Codex：第二轮修复 Spec v3 复审请求（V2-R1–R4）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec v3 [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
  - 用户已决定 D-G5 修订与 D-G8 均取推荐项；
  - 要求先交 Codex 再审（D-G7），三段确认留待复审之后；
  - 编码、门禁、提交、部署均未授权。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v2-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v2-review-handoff.md)（V2-R1–R4）。
- 本轮没有修改业务代码，没有调用真实模型或外部服务，没有连接数据库或生产。

## 1. 对 V2-R1–R4 的复核

用报告 §5 的两段探针在 main `80f9b2c` 上运行，均退出 0，输出与报告一致：
- 有效期例外让二楼 10:00 替换一楼答案；
- 包含 / 不包含套餐被判 conflict_fee；
- 同一组候选在选择路径得 grounded，在回退路径得 insufficient；二楼带有效期时，回退直接发出二楼 10:00；
- 真实单项检索「一楼早餐厅几点开放」同时得到一楼、二楼两条。

V2-R4 是源码与测试装配审查，我逐条核对了：投诉分支在 `_enter_complaint_mode` 后返回、不经过 `_stage_fast_ack`；PostgreSQL 用例缺审计仓储、业务任务服务与客户绑定。四项全部同意，已写入 Spec §1.2。

## 2. v3 的逐项修订

| 缺口 | v3 方案（Spec 位置） | 用户决定 |
| --- | --- | --- |
| V2-R1、V2-R2 | 同一对象只有一条判据：适用范围与房源一致，且专用归一化后的标准问题非空且完全相同。同主题、问法包含、有效期都不单独证明同一对象；撤回 v2 的包含入组与特殊时期例外（§2.3(2)） | D-G5 修订：只认问法完全相同 |
| V2-R1、V2-R3 | 新增一个共享函数，冲突检查与 D6 优先都只在同一对象内进行。选择核验与逐项回退（`build_evidence_plan` 新增「按对象约束」开关，只在计划成功的逐项回退中开启）共同调用；多个回答条目属于不同对象时各自检查、各自作答（§2.3(3)） | — |
| V2-R1 | 同主题但无法确认同一对象的特殊时期条目，在目标日期有效且与平时答案矛盾时回未确认：不替换答案，也不发出可能已被覆盖的平时答案。SC-国庆早餐（平时与国庆条目问法不同）预计改为未确认，写入基线预期变更；运营侧建议把特殊时期政策的标准问题写成与平时相同 | D-G8：回未确认 |
| V2-R4 | 装配注入真实审计仓储与业务任务服务、绑定合成客户、服务申请用已核验计划。员工接管用 `accept_handoff` 写原生接管审计，加隔离对照与变异检查。中断窗口按表拆为：窗口一平静/投诉；窗口二普通（`_stage_fast_ack` 后）；窗口二投诉（业务会话提交后、Worker 标记完成前）。窗口二走真实 `recover_stale` → `claim_next`，按作业类型、来源与去重键给出精确数量，投诉计数含客人 `complaint_ack` 出站（§2.4） | D-G6（上一轮）：做真实恢复验收 |
| 复审问题 1、2 | 工具覆盖在工具循环结束后按项判定：stay_query 需日期相符的房态、参考价或澄清分项；catalog_query 只在问法识别不出本店事实主题时，由目录分项替代未确认。小句覆盖取与区间相交的小句，空集不算覆盖（§2.1(2)(3)） | — |
| 验证规则 | 先跑相关测试；不改装配与依赖时部署门禁走 CI，不强制本地全量；真实模型全量门禁是另一项门禁，需当次授权（§3.2） | — |

## 3. 请 Codex 重点复审

1. **D-G8 的判定范围。** 「同主题、问法不同、在目标日期有效、所问属性矛盾」的特殊时期条目一律导致未确认。这是否会让同主题下与所问对象无关的期间条目（如国庆期间的「早餐打包」政策）误伤平时的「早餐时间」回答？现方案只看客人问到的属性（钟点或收费）是否矛盾。
2. **`build_evidence_plan` 的「按对象约束」开关只在逐项回退中开启**，计划失败的整句路径保持现状。这一边界是否可接受，还是应要求整句路径同样遵守。
3. **catalog_query 的目录替代判据**是「问法识别不出本店事实主题」，依赖现有主题识别。主题识别漏判时，事实问题可能被目录结果替代而漏答。这一残余风险是否可接受。
4. **窗口二投诉分支**用 Worker 队列会话与业务会话分离的真实装配，在业务会话提交后、标记完成前取消。测试里是否需要真实 `Worker.run_once`，还是用与 `application.handle_deferred_message` 等价的独立业务会话加手动标记即可。

## 4. 工作区状态

- 本轮修改：Spec v3；新增本报告。
- 之前未提交、继续保留：
  - Claude 的 fix2 v1、v2 交接报告；
  - Codex 的三份 2026-10-08 复审报告；
  - 上一轮 Claude 报告的补充；
  - `.aoci/baseline.json`、`aoci.code.txt`。
- `.impeccable/critique/` 不属于本工作。

## 5. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 17f7639638eb4bde59c2d39c3ff177ab49a85f5acb9a8dec150888fe1344d359 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-v2-review-handoff.md | c67f98288e457f4748f3dd2a4c6a31b4769e46155bc8165283e120defd97753b |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
