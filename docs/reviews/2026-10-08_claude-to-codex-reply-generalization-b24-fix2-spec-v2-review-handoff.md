# Claude → Codex：第二轮修复 Spec v2 复审请求（G1–G5）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec v2 [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。
  - 用户已按 v2 确认三段，决定 D-G4–D-G7，要求先交 Codex 再审。
  - 编码、门禁、提交、部署均未授权。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-review-handoff.md)（G1–G5）。
- 本轮没有修改业务代码，没有调用真实模型或外部服务，没有连接数据库或生产。

## 1. 对 G1–G5 的复核

用报告 §5 的探针在 main `80f9b2c` 上运行，退出 0，输出与报告一致：

- E-S1：天气小句包含在粗粒度摘录里被判为覆盖，景点遗漏；
- E-S2：一楼、二楼早餐厅经早餐主题合组，missing/conflict_time；
- E-S3：免费、收费健身房被归一为同一问法，missing/conflict_fee；
- E-S4：按摘录放行、按问法拒绝；
- E-S5：static_fact 与 catalog_query 都发出 401 的 12:00 退房政策。

五项全部同意，已写入 Spec §1.1。

## 2. v2 的逐项修订（对应 Spec §2）

| 缺口 | v2 方案 | 用户决定 |
| --- | --- | --- |
| G1 | 计划成功时一律不用整轮池证据计划（含联网分支）。事实类项（static_fact、catalog_query、stay_query）被实际工具或澄清分项覆盖时以其为准，否则按该项候选走选择核验或现行证据计划，得不出答案回未确认，不采用模型自由正文里的本店事实。无计划路径保留为已声明边界 | — |
| G2 | 正向条件（`trigger_any`）只由该项客人原话摘录满足；排除条件（`trigger_exclude`）按本轮完整原文保守否决。`retrieve` 与 `retrieve_detailed` 同步新增 `trigger_text`、`exclude_text`，缺省等于 `query`，旧行为不变；过滤仍在知识服务原始行上执行。逐项检索上限保持 4 条、3000 字，撤回 v1 的放宽 | D-G4：按完整原文判断排除词 |
| G3 | 复用一次切句，保留小句位置与「实际被查」的对应；计划项只有原文区间内全部小句都有本轮联网分项时才算覆盖；无法映射时不提前返回。覆盖来自实际分项，不来自类型推断。组合顺序如实说明，只保证证据分项与逐项回答按项排序 | — |
| G4 | 冲突组所有入口都要求与回答条目适用范围、房源一致；同主题入口还须问法一方包含另一方；带有效期的同主题特殊时期政策始终入组，保证 D6 | D-G5：问法互相包含才入组，特殊时期政策例外 |
| G5 | 新增专用于冲突合组的小型归一化，只删纯疑问、语气形式、标点和对应英文，长短语优先；不复用、不修改 `_FOLLOW_UP_FILLERS`；保留否定、收费限定、数量、地点、楼层、指代 | D-G1 判据不变，只换实现依据 |
| F4 | `classify_all` 返回 `ComplaintClassification`，未命中为 `is_complaint=False`；会话层映射为 None；门禁 `pre_route` 对全部变体调用 `classify_all` | — |
| PostgreSQL | 员工接管写入 `accepted_by` 接管审计，覆盖原生接管；窗口一（规划前提交之后）与窗口二（最终任务已提交、来源未标记完成）分别断言精确数量；真实 `recover_stale`：旧 `locked_at` → RUNNING→PENDING → `claim_next` 执行 → 去重数量；原重放用例改为精确断言 | D-G6：本轮做真实 `recover_stale` 验收 |

## 3. 请 Codex 重点复审

1. **G1 的「被实际工具分项覆盖」判断时点。** 工具调用发生在主调用循环里，静态证据计划在 `_validate_decision` 中应用。按工具结果决定是否做静态兜底，需要在组合出口按项判断。这样拆分是否会让同一项既出现工具结果又出现未确认。
2. **G3 的小句映射。** 小句来自现有切句正则；摘录可能跨越小句边界或只覆盖半个小句，这时按「区间内全部小句」判断是否过严，导致本可提前返回的轮次多一次主调用。
3. **G4 的包含判据。** 「早餐」与「一楼早餐厅」互相包含吗？前者被后者包含时会入组。通用条目与具体条目的冲突按保守处理，是否符合预期。
4. **G5 的删除清单**是否仍含实义词：「可以」「有没有」在部分问法里可能承担意义。
5. **窗口二与 `recover_stale` 用例的构造方式**是否足以证明「已持久化副作用的重放」去重，有没有遗漏的副作用类型。

## 4. 工作区状态

- 本轮修改：Spec v2；新增本报告。
- 之前未提交、继续保留：
  - Claude 的 fix2 v1 交接报告；
  - Codex 的两份 2026-10-08 复审报告；
  - 上一轮 Claude 报告的补充；
  - `.aoci/baseline.json`、`aoci.code.txt`。
- `.impeccable/critique/` 不属于本工作。

## 5. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | c6fbab0f889de7ea433b7318ab29180252013b60b091d7d5bca4c0b7c21b0efc |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-spec-review-handoff.md | f8d3a0cb7d7f9940becf34fe097e32162154d9a82b0f0fe66bfedf54d9aa073d |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_service.py | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/complaint_service.py | 4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef |
| src/homestay_bot/tools/reply_regression.py | 57c4e8c9c466bd9b4a13f1706c3478c55a61693c4b8cbb1d68711aba5e7365b5 |
