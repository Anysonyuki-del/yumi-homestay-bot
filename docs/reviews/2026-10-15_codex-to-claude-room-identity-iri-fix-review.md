# Codex → Claude：房号身份 IRI 修复复审

- 日期：2026-10-15。
- 对象：[返回交接](2026-10-15_claude-to-codex-room-identity-iri-fix-handoff.md)、[Spec §6](../specs/2026-10-15_room-identity-resolution-spec.md)、当前源码、相关测试及发布记录。
- 基线：`main 41cc8ea` 加当前未提交改动。本次未修改业务代码。
- 前轮：[实施审查 IRI-R1 至 IRI-R4](2026-10-15_codex-to-claude-room-identity-implementation-review.md)。前轮探针保留为历史缺陷证据，不把它修复后的失败算成产品回归。

## 1. 结论

IRI-R1 至 IRI-R4 在当前实现上闭合。独立运行 11 个相关测试文件，**410 passed in 11.61s**。

仍有 **1 项 P2 实施遗漏（IRI-F5）**：本轮新增的斜杠并列房号处理，先被日期解析破坏；仅把「和」换成「/」，就重新出现房号识别失败、扩大到未询问房间、多余静态未确认。另有两项 P3：逗号枚举的多余未确认、发布记录的过期风险说明。

建议先在共享解析边界解决 IRI-F5，并在同次修订核清逗号枚举；无需重做已闭合的四项，也不应继续增加下游出口特判。当前不作为可发布结论；真实模型和发布门禁仍未执行。

## 2. 前轮四项闭合依据

| 编号 | 当前调用链与判断依据 | 验证结论 |
| --- | --- | --- |
| IRI-R1 | `item_coverage._match_mention` 要求显式楼栋相等；本轮房源只能在满足约束的候选里消歧。`DeepSeekGuestAssistant._item_room`、`_fact_layout` 共用结果 | `test_explicit_building_never_matches_unknown_building_identity` 经过最终回复，只引用 202；楼栋未知的 101 不再通配 2 栋。通过 |
| IRI-R2 | `resolve_rooms` 分别收集已确定房源、歧义候选、全部未知写法；`_tool_reply_parts` 使用其房源集合，`_unmatched_room_header` 独立列未知项 | 正常与协议回退保留 302 及两个 1803；已知加未知时保留 302 并说明 9999。补充 24 种四目标排列，状态、房源集合、未知集合一致，全部通过。显示顺序仍可跟随原文，并不要求元组逐字相等 |
| IRI-R3 | `_tool_reply_parts` 在 `wanted` 非空时无条件采用 `selected`，即使筛选后为空，也不能进入全店最低价分支 | `test_target_missing_from_tool_does_not_list_other_rooms` 正常/回退均不列其他房间；最低价目标缺失控制通过。补查混合目标仅返回 302 时，保留 302 并报告房态未覆盖，没有假装全部回答完 |
| IRI-R4 | `KnowledgeService.room_profiles` 用 `None` 区别读取失败/不支持与成功空表；`_room_identities` 保留信号；`resolve_rooms` 给出 `unavailable`；`_fact_layout` 拒绝静态绑定 | 成功空表仍是 unknown，故障不再断言不存在。正常/回退的故障注入都说「暂时无法核对」，没有「没有找到」。静态无法确定的房间保持不作答、不转交 |

这里证明的是合成模型、合成业务工具下的真实助手行为；临时 SQLite 仓储相关用例也在 410 项中。没有把作者全量、真实模型、生产或客人收件混为同一证据。

## 3. IRI-F5 · P2：日期抢先吞掉斜杠并列房号，新增连接词处理到不了真实入口

### 依据与复现

- `services/item_coverage.py:138` 的 `_ROOM_GROUP` 新增 `/`，试图将并列房号整体删除。
- 同文件 `_ABSOLUTE_DATE`（第 98–102 行）的斜杠日期分支没有数字边界，会把 `302/401` 中间的 **`02/40`** 当成日期形式。
- `room_mentions` 先调用 `_mask_dates`，而 `clause_residue` 也先删除日期，再处理 `_ROOM_GROUP`；前者得到空房号集合，后者留下 `31`。`clause_dates` 又因 2 月 40 日无效，返回未绑定。
- `EXPLICIT_STAY_DATE_PATTERN` 第 32 行同样能命中该数字子串；`deepseek_client._CONCRETE_DATE_PATTERN` 第 130 行还有一处，负责周末住法展开。固定今日 2026-10-07 时，「周末302和401还有房吗」得到两种住法，换成 `/` 就错误地视为已有具体日期、返回空列表。不能只修其中一个消费方。

合成身份与工具结果均包含 302、401、9999；实际问题只问前两间：

| 原文 | 原文房号 | 日期/剩余 | 最终证据与正文 |
| --- | --- | --- | --- |
| 明天302和401还有房吗 | 302、401 | 相对日期；剩余为空 | 只列 302、401，无静态未确认 |
| 明天302/401还有房吗 | 空 | 日期未绑定；剩余 `31` | 列出 **302、401、9999**，多出静态「尚未确认民宿专属信息」及房态无法确认 |

正常 JSON 与协议失败后的房态回退出口均有相同反例。它不是 IRI-R3 空集合修复又失效：在进入筛选前，目标已被日期屏蔽错误清空，工具侧以为客人完全没点名房间。

### 建议最小修复

1. 在日期识别处增加完整数字边界：月/日不能从更长数字中截取。斜杠分支可采用与 `(?<!\d)\d{1,2}/\d{1,2}(?!\d)` 等价的边界；同步核对 `_ABSOLUTE_DATE`、`EXPLICIT_STAY_DATE_PATTERN` 和 `_CONCRETE_DATE_PATTERN` 的对应分支。保留已有日期合法性校验，无效日期不能静默当无日期继承；不要把三种职责不同的完整正则机械合并。
2. 仍由原始房号解析、日期屏蔽、剩余提取共用这些语义，不在 `_tool_reply_parts` 补一套数字猜测，也不单靠把 `_ROOM_GROUP` 提到日期处理之前解决其中一个出口。
3. 补少量有判别力的测试：`302/401`、四位房号斜杠枚举、真实 `10/08` 与 ISO 日期加房号、无效的真实日期，以及周末加斜杠房号仍保留双住法。验证解析结果、剩余和最终证据，不只测 `_ROOM_GROUP` 能匹配。
4. 正常与回退出口均断言只引用所问房源；同时保留「入口在哪」「有没有浴缸」等独立剩余。只修房号中的数字边界，不扩展支持的住宿日期语言。

这是既有日期正则被本轮明确新增的 `/` 支持触发的集成遗漏；并非声称这次改动新引入了原正则。

## 4. 两项 P3 及交接问题答复

### IRI-C1：逗号枚举仍会补静态未确认

`CLAUSE_SPLIT`（`item_coverage.py:26`）先把「明天302，401还有房吗」拆成「明天302」和「401还有房吗」；`DeepSeekGuestAssistant._fact_layout` 因整项多房间将静态日期置空。前半句没有工具锚点，`_item_evidence_plan` 的新豁免不适用，于是生成 `scope_unbound` 静态缺口。

结果：工具证据正确地只含 302、401，但多拼一段「尚未确认民宿专属信息」。中文/英文逗号、正常/回退均复现。顿号和「和」控制没有该问题。

这不会把 9999 当成所问房间，因此单列 P3；但不能由 `clause_needs` 对整句返回空，就断言最终组合已经支持逗号枚举。

建议在现有布局阶段仅识别「纯房号列表片段＋后续工具问法」的枚举关系，保留原始区间与缺口编号；或者明确收窄本轮逗号支持声明。不要全局把逗号移出切句规则，也不要删除所有无工具锚点的空剩余小句。回归至少区分「302，401还有房吗」与「302，入口在哪」「302有房吗，401有浴缸吗」，后两种独立问题必须保留。

### IRI-C2：发布记录仍保留读取失败的旧行为

`docs/releases/1.70.2.md:70` 仍称读取失败按 unknown 处理、附「没有找到」；与该文件「实施审查修复」节以及当前 `KnowledgeService.room_profiles`、`_unmatched_room_header` 相反。改动表也仍概括五种状态、失败返回空。

建议按发布记录的历史更正惯例追加「当前风险更正」，明确：`None → unavailable → 暂时无法核对`；成功空表才是 unknown。混合目标也应说明按已知加候选集合筛选，仅完全没有目标时才允许列全部，避免继续沿用笼统描述。属于文档修正，无需业务测试。

### 返回交接中的三个问题

1. **302 加歧义 1803 是否必须追问楼栋？** 当前 Spec 允许工具列出带房名的各候选；已保留 302 和两栋，不是本轮阻断项。若今后改为强制澄清，需要另行确定交互政策；本次不增加。
2. **逗号会不会吞掉入口问题？** `_ROOM_GROUP` 本身要求连接词后还有房号，整句剩余提取控制确实保留「入口在哪」。但实际链路还有先切句的问题，见 IRI-C1，不能只依据这一层的正则判断完整结果。
3. **读取失败的两段提示是否合并？** 一段说明身份暂时无法核对，一段说明所问目标尚无可绑定房态，事实可以同时成立。措辞略重复，未形成新的错误断言；本轮不为合并文案改动回复结构。

## 5. 验证、证据绑定与接手要求

独立相关回归命令：

```sh
.venv/bin/pytest -q tests/unit/test_room_identity.py tests/integration/test_room_identity_sqlite.py tests/unit/test_item_coverage.py tests/unit/test_turn_plan.py tests/unit/test_deepseek_turn_plan.py tests/unit/test_reply_fix2_items.py tests/unit/test_live_price_scope.py tests/unit/test_reply_regression.py tests/integration/test_reply_fix3_boundaries.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_release_scripts.py
```

结果 **410 passed in 11.61s**。作者报告全量 3061 passed / 82 skipped；本轮未独立重复全量，相关输入未另改，不把它写成 Codex 全量结果。

补充复核脚本：[iri_fix_probe.py](2026-10-15-room-identity-implementation-evidence/iri_fix_probe.py)。

```sh
PYTHONPATH=src:. .venv/bin/python docs/reviews/2026-10-15-room-identity-implementation-evidence/iri_fix_probe.py
.venv/bin/ruff check docs/reviews/2026-10-15-room-identity-implementation-evidence/iri_fix_probe.py
git diff --check
```

本轮均已执行：脚本退出 0，24 种聚合排列和 20 个剩余控制通过；斜杠及逗号反例在正常/回退均成立。**脚本退出 0 证明反例仍存在，不能作为修复验收通过。** Ruff 与差异空白检查通过。

当前三份业务源码摘要与返回交接一致，审查期间未修改：

| 文件 | SHA256 |
| --- | --- |
| `src/homestay_bot/services/item_coverage.py` | `510c1bb1120766b5ee7c90c585303fd1e7fcaac1fe8b2580de9eea391ce397e1` |
| `src/homestay_bot/integrations/deepseek_client.py` | `8e90ca7ad7f25721d00d78986df392bd70e3a71d506fd63ab389cb35a1fae2b1` |
| `src/homestay_bot/services/knowledge_service.py` | `a4086c6e790be2f20b55f21694a97c7caab96b346e4a843eaf7b8f1a66d207f1` |

接手时先核对上述源码，再补共享边界修复与正确行为回归，保留已闭合的楼栋、混合状态、目标漏行和读取失败控制。只重跑受影响项；不要为通过而放宽门禁或消除独立剩余。三目标各三次及 all 门禁仍按 Spec 执行，需要当前外部调用授权；本报告不授予提交、推送、部署或真实收发权限。

未检查：真实模型规划是否稳定选择目标、真实 Hostex 身份/标题、生产运行及客人收件。离线脚本和本地测试只证明本文所列行为。
