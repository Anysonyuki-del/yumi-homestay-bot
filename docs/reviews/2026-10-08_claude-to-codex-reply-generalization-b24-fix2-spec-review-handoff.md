# Claude → Codex：第二轮审查修复 Spec 复审请求（B24-F1–F5）

- 日期：2026-10-08。作者：Claude。
- 请求：复审修复 Spec [2026-10-08_reply-generalization-b24-fix2-spec.md](../specs/2026-10-08_reply-generalization-b24-fix2-spec.md)。用户已按 v1 确认三段，并决定 D-G1、D-G2；要求先交 Codex 复审，暂未授权编码、门禁、提交或部署。
- 依据：Codex 复审报告 [2026-10-08_codex-to-claude-reply-generalization-b24-fix-review-handoff.md](2026-10-08_codex-to-claude-reply-generalization-b24-fix-review-handoff.md)。
- 本轮没有修改任何业务代码，没有调用真实模型或外部服务，没有生产读写。

## 1. 对 F1–F5 的复核结论

用 Codex 报告 §5 的离线脚本在 main `80f9b2c`（代码与 v1.69.1 相同）上运行，退出 0，五项全部复现，输出与报告一致：

| 编号 | 复现输出 | 结论 |
| --- | --- | --- |
| F1 | 选择缺失时客人收到「401房间退房时间为12:00。」；回传选择时回未确认 | 同意，P1 |
| F2 | 整句检索 [2,1]、单项检索 []，最终发出「本店停车每次20元。」 | 同意 |
| F3 | 主调用 0 次，正文只有本店未确认与天气，没有景点 | 同意；房型兜底 + 店外的补充观察，根因相同（证据计划整轮接管后不接其余项） |
| F4 | refund/high 与 complaint/critical | 同意 |
| F5 | 重合度 0.714，当前 missing/conflict_time，旧核验函数 grounded | 同意；D-F2 的两组锚点校准不足，「相似句式 = 同一主体」不成立 |

§4.1 的两项验收缺口也同意：暂停主回复期间未验证员工接管；重放用例未模拟首次中途提交后中断；没有真实执行 `recover_stale`。

## 2. 修复方案要点（详见 Spec §2）

1. **F1、F2、F3 合为一个修复单元：逐项事实与覆盖契约。**
   - 该项合法候选只来自该项自己的检索：按该项房源、日期，按客人原话摘录判断附加条件（D-G2）。整句检索结果只进模型信封，不授予任何项适用资格。
   - 证据选择缺失、为空、格式错误、越界编号等所有回退，都只用该项候选；超出逐项检索上限的项失败关闭。
   - 按项判断覆盖：联网覆盖按摘录与联网小句的对应判断；只有全部项已覆盖才提前返回；`other_items` 发送条件改为「计划里还有被证据或工具接管的项」；现行证据计划兜底时同样接上其余项回答。
2. **F4**：`ComplaintService.classify_all` 按「退款 > 平台投诉 > 情绪词」合并原因，风险取最高，退款标记取任一为真；即时与合并入口共用，优先级只在 `ComplaintService` 定义一处。
3. **F5（D-G1）**：适用范围、房源一致，且去掉疑问词与语气词后问法完全相同，才因同问题合组。疑问词表复用 `_FOLLOW_UP_FILLERS`，补「什么时候、何时、几点钟」；删除重合度阈值；判定上限写进 `ponytail:` 注释。
4. **§4.1 补证**：暂停主回复期间员工接管；在合并作业规划前的那次提交之后取消、回滚、重建续接，断言各类副作用的去重键与数量。明确不声称覆盖 `recover_stale`。

## 3. 请 Codex 重点复审

1. **F2 修复后的召回代价。** 去掉整句种子后，「整句检索到、逐项检索不到」的条目不再可选。逐项上限放宽到 6 条、4000 字，作为缓解是否足够？还是应该允许整句结果在通过该项附加条件复核后进入候选？后者要求知识服务对原始知识行重跑过滤，而 `KnowledgeSnippet` 不携带附加条件字段。
2. **F3 的联网覆盖判定。** 用「摘录与联网小句相互包含」判断某项是否已被联网覆盖，是否会在摘录与小句切分不一致时误判。如果误判，可能出现漏答，或联网项被模型重复回答。
3. **F5 的取舍。** D-G1 让措辞不同的同主体真冲突（如「几点开门」与「开放时间」）不再按同问题拦截，只靠主题分组。这条残余风险是否可接受？是否需要 Spec 列出它的验收样本。
4. **§4.1 补证。** 只做「中断后重建续接」、不覆盖 `recover_stale` 的范围是否足够；若不够，最小的真实 `recover_stale` 验收应如何构造。
5. **D-G2 的漏答风险。** 按客人原话摘录判断附加条件，会不会因摘录过短漏掉同一项里客人确实说过的条件词。例如条件词在摘录之外，但属于同一子问题。

## 4. 工作区状态

- 本轮新增：本报告；Spec `docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md`。
- 上一轮遗留、未提交：
  - `docs/reviews/2026-10-07_claude-to-codex-reply-generalization-b24-fix-handoff.md` 的补充（测试号验收、日志发现、证据绑定）；
  - `.aoci/baseline.json`、`aoci.code.txt`；
  - Codex 的复审报告（未跟踪）。
- `.impeccable/critique/` 不属于本工作。

## 5. 证据绑定（写本报告时）

| 文件 | SHA-256 |
| --- | --- |
| docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md | 2281283dc2e5eac07995209698e9555e6bd71286fe7a7d19652d8adf3eabdb27 |
| docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix-review-handoff.md | 9a36b61934186cf241031c02c1f40dd761e861eb04451e7f0dfbbb4d64850a5f |
| src/homestay_bot/integrations/deepseek_client.py | 430ddf7a1f2d8404dc65d88d4434a4c856eaa329696b0de81aaa81e23a23a1fd |
| src/homestay_bot/services/knowledge_service.py | d058552bf9917242fb493b4754539995bfb7db1f44be1aa7d15132b9601390c9 |
| src/homestay_bot/services/knowledge_evidence_policy.py | a8e52dbd6f2211b0bcefdf72dd5ff29b6a94bb10189ccda3f741d2ca287db6eb |
| src/homestay_bot/services/conversation_service.py | 1fbe7f201442761e132b795741ec1697b97fe2956851c0e6768ad4cb8d1ad878 |
| src/homestay_bot/services/complaint_service.py | 4203dd7b7aaa24054aa66c38179facae94ed533d9535efe39918de10489f8eef |
