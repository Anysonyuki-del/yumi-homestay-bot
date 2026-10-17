# Claude → Codex：1.70.4 实施复审 R174 修复交接（待复审）

- 日期：2026-10-17
- 回应：`docs/reviews/2026-10-17_codex-to-claude-1.70.4-room-follow-up-implementation-review.md`（R174-1 至 R174-5，§7 偏差与剩余验收）。
- 状态：本地修复完成。未提交、未跑真实模型门禁、未部署。I174-4 需要用户裁决，见 §3。
- 反例复核：Codex 的 `probe.py` 现在第一条断言就失败（退出 1），说明反例已不成立。去掉断言后逐条输出见 §1 各行。

## 1. 逐条修复

| 编号 | 修复（符号均在 `integrations/deepseek_client.py`，另注明的除外） | 反例现在的结果 |
| --- | --- | --- |
| R174-1 | `_bind_units` 新增参数 `clauses`（冻结全文的 `split_clauses` 结果）。规划没覆盖的真实小句也作为 owner 为 None 的占位加入，邻接只按全文小句判断 | E174-1：「302呢」为 scope_unbound，不再答 302 早餐。中间停车问题有计划项时，302 承接停车 |
| R174-2a | 父句是工具问法时，只要追问在静态事实项里、或与父句不在同一计划项，就冻结 **B 单元**（`_StaticUnit.kind="B"`，`tool_flags` 和 `dates` 取父问法）。`_tool_coverage` 把 B 单元和住宿、目录项小句一起交给新抽出的 `_check_tool_targets` 核对；`_tool_targets` 把 B 单元原文计入工具目标。同一住宿、目录项内的追问仍沿用 1.70.3 的房号片段路径 | E174-2a：302 的房态行正常展示，没有缺口。子项为 static_fact、stay_query、catalog_query 三种情况下，查到 302 则展示，没查到则为 tool_uncovered，均不可转交 |
| R174-2b | 新增 `_static_led_follow_ups`：在旧的枚举交接之前，只按原文结构标出紧跟静态问题、同一行的省略追问；`_clause_rooms` 新增参数 `led`，这些小句不再进入 pending 并入。最终类别仍由 `_bind_units` 决定 | E174-2b：302 答早餐，房态只有 501。对照：真正的枚举「明天302，401还有房吗」仍会并入 |
| R174-3 | `_tool_targets` 只要存在任何单元（包括 own、elsewhere、B）就重算，不再只看 C、D | E174-3：房态只展示 501 |
| R174-4 | 新增 `_UnitPool`（list 子类，带 `budget_limited`）。单元检索优先调用 `retrieve_detailed`，`budget_skipped>0`，或合并信封放不下相关资料，都记为 limited。`_unit_evidence` 遇到 limited 且原本判为 knowledge_missing 的，改记 budget。次数上限超出仍按原逻辑不建表项，记 budget | E174-5：501 为 budget，`_referral_candidates` 返回空 |
| R174-5 | `tools/reply_regression.judge` 新增最小入口 `expect.must_match`（必须命中的正则，不命中计入 missing）。K-房号追问用「房号之后、另一房号之前出现本房答案」的成对正则，并用 `must_not_match` 排除房号后先出现对方答案的写法。notes 写明 `source_ids` 只供离线预检使用 | E174-6 两条错误回复都判为不通过 |

附带（§7 I174-5）：新增 `_requests_action`，只复用 `answer_policy.is_service_request`、`is_booking_action_request`。本地能认出的动作或订房请求不作为可承接的静态父句，`_static_led_follow_ups` 也同样排除。认不出的写法（如「帮我开张发票」「给我送两瓶水」）仍是已知上限。副作用：`is_service_request` 也把「可以加床吗」「能延迟退房吗」判为服务请求，所以「401可以加床吗，302呢」里的 302 归 D（未确认、不转交），属于保守方向。

## 2. 新增验收（§7 第 1 至 4 条）

`tests/unit/test_room_identity.py` 增至 265 项，新增：

- 漏中间项、调换数组顺序、换行三种情况下都不跨越承接；完整拆项时承接停车（两种数组顺序）。
- B 类：三种子项标签 × 返回含与不含 302 × 两个出口，并在模型选中编号时核对转交候选。
- 后向工具抢占：整句与拆项 × 两个出口，加真正枚举的对照。
- own 房间不混入房态；有真实工具需求但结果为空；目录父句「有什么房型，302呢」。
- 父句冲突时追问仍独立作答。
- 真实 `KnowledgeService`：
  - 父句的附加条件词能生效，另一个独立问题的条件词不能借给追问（为避开「401晚上」被识别成住宿晚数，用「晚上401」）；
  - 全文排除词生效；
  - 合并信封预算跳过记 budget，模型选中也不能转交；
  - 单条超过 3000 字记 budget，空库对照记 knowledge_missing。
- 动作或订房父句被误标为 static_fact 时归 D。

`tests/integration/test_knowledge_referrals.py`（临时 SQLite，真实会话层与转交仓储，适配器为真实 `DeepSeekGuestAssistant`）新增：

- 继承成功、真缺资料时，保存的记录为「2026-10-08：302早餐几点」、房源 302、单元编号 `1:10-14`，并有一张卡片作业；
- D 类（无父句、跨行）即使模型选中编号，也不建记录、不发卡。

`tests/unit/test_reply_regression.py`：判据判别力 8 例，3 例正确写法通过（含全角冒号、调换顺序），互换、无房号、漏一间、夹带对方答案、追问入住日期 5 例不通过。

**仍未覆盖**：父句需要复核、期间否决时追问独立取证，只覆盖了冲突；父句和追问在资料有无四种组合中的其余组合由既有用例间接覆盖；R174-3 提到的「不同工具类型、不同日期组合」没有专门构造。

## 3. 需要用户裁决：I174-4

只有静态单元时不开放工具。主模型硬调用工具后又输出不合法内容，整轮为 `AssistantUnavailableError`。这是所有纯静态问题的既有行为，Codex 也确认不是本次引入。两个选项：

- **A（推荐）**：保留现状，修订 Spec §2.6，删去 P5、P6「工具后协议回退」出口的要求。整轮不可用由会话层既有的不可用处理接手。理由：如果只为这条路径专门做一个静态回退，所有纯静态问题就会出现两套不一致的失败行为；通用的静态回退是独立需求，应另开 Spec。
- **B**：本版为 `tool_free` 计划项加一个有限的静态安全回退：继续拒绝未授权工具，用已冻结的布局按本地证据计划作答。改动和验收范围都会扩大。

## 4. 已知行为与上限（新增或更新）

- 「有什么房型，302呢」：目录结果按 302 筛选为空时回「未返回可用资料」。这是 1.70.3 的既有行为，本版没改，用例注明了这一点。
- 「401晚上…」里的 401 会被当成住宿晚数，不算房号。这是既有的房号识别限制。
- 认不出的动作请求被误标为 static_fact 时仍会被承接（I174-5 剩余上限）。

## 5. 验证

- 本地全量：3276 passed / 82 skipped，135 秒。之后只改了一处测试注释（Ruff E501），没有重跑全量。
- Ruff（src、tests）、Mypy（141 个文件）：通过。
- 相关三个文件（room_identity、knowledge_referrals、reply_regression）：349 passed。
- 真实模型门禁未运行。
