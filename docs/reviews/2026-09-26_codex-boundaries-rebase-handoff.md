# Codex 边界重构并入 main 的交接说明

- 日期：2026-09-26
- 对象：`codex/guest-assistant-boundaries` 工作区（基线 `1300b13`）
- 对照：`origin/main` 当前已发布到 1.41.0（1.39.16 至 1.41.0 共 17 个提交）
- 方法：把工作区改动原样做成本地快照后，用 `git merge-tree --write-tree` 在内存里试合并，逐个核对冲突块和两边都改过、但 git 能自动合并的文件。没有真正合并，工作区没有被改动。
- 请在自己的分支上按 main 重新整理（rebase 或合并 main 后解决），完成后跑本文末尾的验收。

## 0. 存量知识迁移后的范围默认值（用户已决定）

**背景**

- 现在的写法：`0028_knowledge_scope` 把存量条目全部标为 `unreviewed`。
- `SQLAlchemyKnowledgeRepository.list_active` 和 `DeepSeekGuestAssistant._scope_knowledge` 都会把这些条目排除掉。
- 后果：上线后，在管理员逐条审核之前，所有靠知识库回答的问题都会变成「暂无法确认」。紧急情况后续答复（1.40.0）读取的「紧急处置」条目也拿不到。

**用户决定（2026-09-26）**：迁移时按规则给默认值，再出一份清单供管理员复核。原话是「后者」，指的是在两个方案里选了这一个。

**迁移规则**（写在改名后的 `0029_knowledge_scope` 的 `upgrade` 里，用 SQL 或迁移内的纯函数完成）：

1. 已启用（`is_enabled` 为真）且提问或类别讲周边、附近的条目，标为 `public`。
   - 判断时看 `question_zh`、`question_en` 和 `category`，词表与 `deepseek_client._EXTERNAL_SCOPE_PATTERN` 一致。
   - 把词表复制进迁移，迁移不要导入应用代码，否则以后应用改词表会改变历史迁移的结果。
   - 只看提问和类别，不看答案。答案里顺带提到「楼下有早餐店」的本店条目仍标 `global`，与 `_scope_knowledge` 按标题判断周边的口径一致。
2. 其余已启用条目标为 `global`。
   - 「紧急处置」类别一定落在这里，保证紧急后续答复可用。
3. 停用条目保持 `unreviewed`，重新启用时再由管理员设置范围。
4. 不推断 `property`：猜不出条目属于哪个房间，一律不标。`property_id`、`valid_from`、`valid_until` 保持为空。
5. `downgrade` 照原样删除这几列即可，不需要回写。

**复核清单**

- 提供一个只读工具，例如 `homestay_bot.tools.knowledge_scope_report`。它列出每个已启用条目的编号、类别、提问和迁移后的范围，并单独列出按规则标为 `public` 的条目。
- 清单只在服务器上生成、给管理员看，不提交进仓库。
- 管理员在知识管理页（分支已有的 `scope_fields.html`）逐条调整。

**测试**

- 在迁移测试里用合成数据覆盖四种条目：已启用的本店条目 → `global`；提问含「附近」的条目 → `public`；英文提问含 `nearby` 的条目 → `public`；停用条目 → `unreviewed`。
- 断言迁移后 `list_active` 能返回前三类条目。

## 1. 合并后会静默出错的地方（git 不报冲突，必须处理）

### 1.1 参考价结果格式不匹配：问价全部失败

- main 的 `HostexReadOnlyToolExecutor._reference_prices`（1.40.0）会被合并保留。它的输出：
  - 每个房间一行，字段有 `property_id`、`property_title`、`check_in_date`、`check_out_date`、`stay_available`、`nightly_reference_prices[{date, price}]` 和 `note`。
  - 渠道房源编号通过 `Property.channels` 换算成房间，不再原样出现。
- 分支的 `_tool_reply_parts` 在 `search_reference_price` 分支里读的是 `row["price"]`、`row["date"]`、`row["listing_id"]`。读不到就把这一行跳过，最后回退成「本次查询未返回可用资料，暂不能确认房态或价格」。
- 旧写法本身还会把「渠道 X 房源 <编号>」发给客人。1.40.0 修的正是这个问题。
- 要求：
  - 改成读按房间返回的格式。每个房间生成一段，写明房间名、每晚参考价和口径。
  - `stay_available` 为 false 时，不能写可订。
  - 参考价不能证明可订（符合 Spec R03）。
  - `ReplyEvidence.property_id` 直接取行里的 `property_id`。

### 1.2 回归门禁的知识全部被当成未审核

- `tools/reply_regression.py` 的内存知识条目 `_Entry` 没有 `scope` 字段。夹具里虽然写了 `"scope": "global"`，构造时会按字段名过滤掉。
- 走到 `_scope_knowledge` 和知识服务时，`getattr(item, "scope", "unreviewed")` 取到的是 `unreviewed`，条目被全部剔除。53 个知识场景会全部失败，门禁会拦住发版。
- 要求：给 `_Entry` 加上 `scope: str = "unreviewed"`，以及 `property_id`、`valid_from`、`valid_until`，默认值都是 None。夹具里已有的值就能生效。

### 1.3 `property_tool_grounded` 已删除，main 那侧的代码还在用

- `respond` 里「精炼后判断是否本店专属」那个冲突块，如果取 main 一侧，会引用分支里已经不存在的 `property_tool_grounded`，运行时报 `UnboundLocalError`。
- 这一块必须取分支自己的写法，再把计时加回去。见下面第 2 节第一张表。

## 2. 有文本冲突的地方：解决方向

总原则：结构以分支为准（分项回复、来源绑定、查询失败按分项降级），同时把下面这些 main 已发布的行为逐项移植进去，不能丢。

### `integrations/deepseek_client.py`（11 处）

| main 已发布的行为 | 出处 | 移植要求 |
|---|---|---|
| 分阶段计时：`stage_timing_sink`、`_record_stage` | 1.40.2 | 计时点包括：`tourism_search`、`knowledge`、`faq_context`、`main_call`、`refine`、`tool:<名字>`。成功和异常路径都要记。工具异常时用分支的降级（生成 `query_failed` 分项，继续往下），并在降级前记上耗时。 |
| 问价没给日期时先问日期：`_price_question_needs_dates` | 1.40.0 | 放在知识检索之前，直接回复「请问您计划哪天入住、住几晚？我帮您查一下参考价。」，不调用模型。 |
| 店外状态不推测：`_remove_unsourced_external_state` | 1.39.17 P14 | `_validate_decision` 里 main 的调用已经自动合入，保留即可。精炼后还要再过一遍。分支的旅游分项有联网证据，不走这道过滤。 |
| 参考价查询算作依据 | 1.40.0 | 分支用逐项来源绑定替代了这个布尔量，可以不再保留它。但要保证有参考价证据时，金额不被当成无依据删掉。 |
| 旅游搜索结果不再精炼 | 分支 | 采用分支做法。main 的旅游精炼和对应计时一起去掉。 |

### `services/answer_policy.py`（1 处）

- 保留 main 的「讲价转人工」：`_BARGAIN_PATTERN` 命中时返回 `"price"`。价格让步必须由人决定。分支 Spec 里的「普通价格查询不整体转人工」与此不冲突，main 也只对讲价转人工。

### `services/conversation_service.py`（3 处）

- 导入：`AbstractAsyncContextManager`、`nullcontext` 与 `field` 都要保留。
- `_EMERGENCY_CATEGORY_LABELS`：保留（紧急后续通知用）。
- 读上下文：用 main 的写法（先得到 `context_messages`，计 `context_ms`/`respond_ms`，再把 `stage_timing_sink=timing.add_stage` 传给 `respond`）。模型上下文里带上分支新增的 `stay_confirmation`、`confirmed_stay`。

### `services/guest_reply_policy.py`、`services/fact_policy.py`（5 处）

- 都是两边在同一位置各自新增，分支没有改这两处的逻辑。
- 取 main 的 `_drop_sentences`、`remove_unsourced_external_state_claims`、`unconfirmed_fallback`、`is_unsourced_external_state_claim`，以及模块说明里的编号。

### 迁移编号

- main 已占用 `0028_guest_plaintext`（下接 `0027_knowledge_embeddings`，已上生产）。
- 分支的两个迁移改为：
  - `0029_knowledge_scope`，`down_revision = "0028_guest_plaintext"`。
  - `0030_stay_confirmation`，`down_revision = "0029_knowledge_scope"`。
- 改名后，`test_knowledge_scope_migration.py` 和 PostgreSQL 测试里写死的修订号要同步改。
- 按 Spec 的要求，在隔离库上验证四种情况：空库升级、从 0028 带合成数据升级、降级、再升级。

### 测试（6 个文件有冲突）

- 冲突文件：`test_answer_policy`、`test_conversation_service`、`test_deepseek_client`、`test_emergency_service`、`test_fact_policy`、`test_tourism`。
- 两边新加的测试都保留。
- 删掉仍在断言旧行为的测试，例如「问价一律转人工」「机器人创建预订审批」。

### 格式化

- 分支对整批文件跑了格式化，冲突里有很大一部分是格式噪音。
- 建议格式化单独做成一个提交，或者等逻辑合完再统一格式化。不要和逻辑改动混在一起。

## 3. 分支自身的问题（与冲突无关，并入前请修）

以下结果是用分支代码实际运行得到的：

| 输入 | 分支结果 | main 结果 | 原因与建议 |
|---|---|---|---|
| 我想退款，退款规则是怎样的 | 不转人工 | refund | `is_policy_inquiry` 判断「是在提申请」的词里没有「我想退」「想退款」「要退」。建议补上这些说法，并加回归用例。 |
| 能便宜点吗，有什么优惠政策 | 不转人工 | price | 政策咨询判断在讲价判断之前就返回了。讲价判断要放到 `is_policy_inquiry` 之前。 |
| 我没有呼吸困难，就是有点累 | 医疗紧急 | — | 新加的 `(?:没有\|无\|停止)呼吸` 在句首先匹配上了，后面的否定排除就没起作用。建议匹配到「呼吸」后面紧跟「困难」时不算，或者把「呼吸困难」放在前面并做整体否定判断。误报方向是多报，但会误通知员工。 |
| 我无法呼吸 | 不识别 | 不识别 | 两边都有的老缺口，属于 Spec S02「危险改述」的范围，建议一并补上。 |

## 4. 已核对、两边兼容的部分

- **紧急情况后续答复**（1.40.0）：分支让模型也能判出紧急，但走的是同一个 `_escalate_emergency`，审计原因仍记为 `emergency:<类别>`。`_answer_emergency_follow_up` 照常工作。
- **1.41.0 客人数据政策**（日志和数据库可存明文、不再到期清除）：
  - 分支没有恢复任何清除逻辑，对 `context_retention.py` 只做了格式化。
  - `save_summary` 保留原文的改动在合并结果里还在。
  - 分支删除了机器人创建预订审批的路径，不影响 1.41.0 对人工审批资料的明文双写。
- **其他**：
  - 出站 `stale_exempt` 和会话编号补查不受影响。
  - 仓库客人数据守护：分支新增的测试只用了白名单里的虚构号码。
  - CI 新增的 PostgreSQL 测试文件没有问题。

## 5. 仍未完成

- **P9 至 P13**：见 `docs/specs/2026-09-25_guest-assistant-boundaries-p9-p14.md`，还没并入分支的 Spec，也还没实现。P14 已在 main 1.39.17 实现。
- **两版问价对比**：第 1.1 节修好后，用同一套部署前回归门禁和虚构场景比较，并人工复核回复。
  - 两版指分支的逐项来源绑定，和 main 1.40.0 的「参考价算依据加上带日期时强制查询」。
  - 场景：PR-201、PR-EN、PR-最便宜、PR-无日期、PR-只问价、PR-讲价、PR-无主渠道、MT-房态价格。
  - 择优保留，不保留两套。

## 6. 验收

1. 离线单测和集成测试全绿，看 pytest 自身的退出码（不要用 `| tail` 再接 `&&`）。
2. 隔离 PostgreSQL 上的迁移和并发测试通过，不能因为缺环境而全部跳过。
3. 部署前真实模型回归门禁（`scripts/release/reply_gate.sh`）不低于当前基线：必须通过 88 个，不能有新增失败。第 1.2 节修好之前，这一项必然失败。
4. 第 3 节四条输入都有回归用例。
5. 迁移按第 0 节的规则实现，四种合成条目的测试通过，复核清单工具能在隔离库上正常输出。

提交、推送、部署和生产数据库操作，仍需用户当面授权。

## 7. 2026-09-26 更正：P12 按新版实施

用户明确要求「统一为新版」。配套 P9–P14 Spec 的 P12 正文、文件范围及 I03/I05 已统一：核对信息明文保存在任务，不因到期或处理完成清除；消息原文及服务器日志按 1.41.0 保留规则处理。模型上下文与摘要输入仍用脱敏副本，员工通知不含核对原值；后台权限、管家核验后关联订单、P8 客人确认和真实客人数据不进 GitHub 的要求不变。不得再按旧条款新增核对信息加密、14 天清除或完成后清除。此次仅统一交接文档，不表示 P12 业务代码已实现。


## 8. 2026-09-27 Codex 接手实施结果

已按本交接完成本地文件级 main 衔接、参考价格式适配、回归夹具字段、知识默认范围与只读复核工具，及 P9–P13。迁移最终链为 `0028_guest_plaintext → 0029_knowledge_scope → 0030_stay_confirmation → 0031_guest_verification`。四项审查反例已固化测试。P12 用任务明文字段留存，模型与摘要使用占位，连续消息合并也隔离核对原文；提交资料不覆盖高风险接管审计。

全量本地测试含隔离 PostgreSQL：2168 通过、15 跳过（真实接口）、21 警告；最后房间展示修正后相关 169 项通过。Ruff/mypy 与本地移动端浏览器验证通过。PostgreSQL 空库、带数据升级/降级/再升级、请求事务及交还并发已执行。此处不代表生产或真实消息验收。真实 DeepSeek 门禁和两版问价实测仍待当前授权；未提交、推送、部署，尚无 Git merge/rebase 提交。


## 9. 2026-09-28 真实模型实测结果

用户明确回复「实测」后完成真实模型调用、合成知识/房源回归与两版问价对比。最终门禁通过：153 场景首轮100通过，原88个必过无退步，新增12项稳定通过；其余53项历史未通过未删除或掩盖。新版与固定main `7ca055e` 的8个问价场景均各跑三轮，价格夹具哈希相同。保留新版来源绑定实现；单房报价及英文价格改善，未恢复旧并行路径。

实测中修复知识属性误拒与跨主题/附加政策误冲突、单房与完整逐夜最低参考价筛选、公开搜索执行承诺、退款改述、门锁发放政策与实际密码拒绝、语言传递、模型JSON必填契约，并校准回归器与实际会话路由。最终本地2207 passed、27 skipped、11 warnings；静态检查通过。完整报告与失败清单：`.stage/guest-live-20260928-005640/report.md`，原始JSON及源码哈希同目录。未更新正式基线、提交、推送或部署；不是企业微信真实收件、实际订单核验或生产数据库验收。
