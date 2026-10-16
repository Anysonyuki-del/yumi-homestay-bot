# Codex → Claude：R172-F1/F2 修复复审

- 日期：2026-10-17。
- 对象：`2026-10-17_claude-to-codex-room-identity-r172f-fix-handoff.md`、房号身份 Spec §12、当前业务差异及相关调用链。
- 基线：HEAD `9d150d6`；本地版本仍为 1.70.2。拟定 1.70.3 未发布。
- 本轮业务只读；仅新增审查报告及维护认知资产。未提交、推送、部署、调用真实模型或外发消息。

## 结论

**需修改：上一轮 R172-F1、R172-F2 已闭合；另外确认 2 项 P2，应一起补齐目标覆盖边界后再进入发布门禁。**

本轮职责划分是正确方向：`DeepSeekGuestAssistant._asks_static` 不再因 orphan 开放静态取证，`_tool_coverage` 接手实际工具覆盖。但“片段有目标”还不能等同于“整段住宿日期匹配”，目录的“有任一行”也不能等同于“点名房间已覆盖”。继续保留共享布局，无须恢复动词词表、增加模型调用或建立第二套解析器。

独立运行 11 个相关测试文件：**492 passed，12.26 秒**。Ruff（src、tests）、Mypy（141 个文件）、`git diff --check` 通过。作者报告的全量 3143 passed / 82 skipped 未独立重跑。

## 上轮闭合

| 项目 | 当前证据与结论 |
| --- | --- |
| R172-F1：订/住/要同义表达假知识缺口 | `deepseek_client.py::_asks_static` 删除 orphan 特例；`test_room_request_synonyms_have_no_fake_gap` 覆盖三种表达、正常及协议回退，完整房态时无假缺口 |
| R172-F2：规划 target_room 改变可转交性质 | `_gap_unit_offers`、`_item_evidence_plan` 共用 `_asks_static`；`test_planner_room_hint_cannot_turn_follow_up_into_referral` 覆盖空/401/302 提示和两个出口，主动选择旧单元编号也无候选 |
| 原 R172-1：句尾追问与日期中断漏答 | `test_trailing_room_follow_up_keeps_a_gap`、`test_interrupted_enumeration_keeps_earlier_fragments` 通过；对应片段为 tool_uncovered / scope_unbound |
| 实际覆盖时不多提示 | `test_follow_up_covered_by_tool_has_no_notice` 通过，工具含 302 时无额外缺口 |
| F7 与静态取证相邻路径 | 房号、逐项覆盖、日期、证据、正常与回退相关测试通过；不表示所有日期组合均已覆盖，见下文 |

## R172-G1 / P2：完整区间只比较入住日，错误日期的房态可以消除缺口

### 依据与反例

`src/homestay_bot/services/item_coverage.py::clause_dates` 已能把 `10月8日至10日302` 解析为入住 2026-10-08、退房 2026-10-10；`clause_needs` 的剩余为空，因而进入房号片段处理。

但 `src/homestay_bot/integrations/deepseek_client.py`：

1. `_clause_rooms::own_day`（2106–2112 行）把已解析的完整区间缩成 `named.check_in`，丢弃退房端点。
2. `_clause_rooms`（2152–2155 行）仅比较这个入住日与规划入住日；相等便记为 `target`。
3. `_tool_coverage`（1886–1893 行）为该 target 重新填入规划的两个日期，原文的退房日不再参与核对。

固定今日 2026-10-07；原文：

```text
10月8日至10日302，401还有房吗
```

合法 `stay_query` 计划给出 10 月 8 日入住、10 月 9 日退房；合成工具只提供 302、401 在 **10 月 8 日至 9 日**的房态。这里故意让规划与原文不一致，验证本地边界能否拦住，不假定模型永远准确。

正常与协议回退均得到 `orphans=('target', None)`、`gap_units=[]`，正文只有：

```text
10月8日入住、10月9日退房：
可订：302房间、401房间
```

302 所问截至 10 日的区间没有证据，却被视为已经覆盖，没有任何对应未覆盖说明。工具来源标签确实写了 9 日，但不能据此把缺少的 9 日入住夜晚当成已经回答。

**改前对照**：在独立进程中加载 Git HEAD 的完整 `deepseek_client.py` 模块，复用相同测试端口、原文、合法计划和工具行。HEAD 返回 `('10月8日至10日302', 'scope_unbound')`；当前版本无缺口。因此该保护丢失是本轮相对正式版本的回归。对照不修改磁盘，不等于完整旧版本环境验收。

### 建议修复

- 不把“入住日相同”称为整个日期目标已证明。保留 `clause_dates` 已识别的端点角色，完整区间需逐一核对入住、退房；仅退房日期也不能丢失其角色。
- 最小保守方案：片段带完整区间时，只有两端与规划一致才允许继承；不一致或缺少必要端点，保留不可转交的 scope_unbound。若选择直接按片段原文区间核验证据，也须两端都参与，不能只补一个判断后仍覆盖原文。
- 没写日期的单一住宿片段可沿用本次已接受的规划继承；无锚点序数日、不同日拒并继续保留。
- 修订 Spec §12 的“日期等于入住日”文字，明确单日、完整区间、退房端点的证明条件。不要扩展日期语法或增加工具反向委派。

**最低验收**：两个出口 × 房态/参考价；完整区间同入住日但不同退房日不能被覆盖；两端相同可覆盖；仅退房端点、规划日期缺失仍保守。保留本轮订/住/要及 hint 三种控制，避免修日期时恢复假静态缺口。

## R172-G2 / P2：目录沿用“任一行即覆盖”，点名房号片段仍会静默漏答

### 依据与反例

`deepseek_client.py::_tool_coverage` 新增对 catalog_query 房号片段的处理，已计算 `target.property_ids`；但在 1918–1919 行，目录分支仍只判断：

```python
covered = any(row.source_kind == "property" for row in evidence)
```

它既不检查该行是否属于片段房源，也不检查片段是否 unresolved / unbound。

身份库含 302、401，目录工具仅返回 `{'id': 401, 'title': '401房间'}`：

| 原文 | 当前两个出口 |
| --- | --- |
| `401有什么房型，302呢` | `房源：401房间。`，无 GapUnit，无 302 未覆盖提示 |
| `401有什么房型，9999呢` | 同上；9999 对不上身份库，仍无提示 |

`_tool_reply_parts` 的筛选没有拿 401 冒充 302；遗漏发生在覆盖判定：“401 有目录证据”被用来取消“302/9999 尚未回答”的提示。

**归因说明**：相同输入在 Git HEAD 也静默漏答。这不是本轮新引入的目录条件，但本轮明确把 catalog_query 的 orphan 纳入工具覆盖，并承诺“按实际证据核对”，仍未关闭这条路径。作为当前修复范围内的遗漏报告，不宣称是新增回归。

### 建议修复

- 目录有明确房源目标时按房源编号逐一核对有效目录证据；未知/无法核对的目标不能由其他房间的行覆盖。
- 无点名目标的全店目录继续采用现有目录语义，避免把本次修复扩大成全店目录数量校验。
- 可复用 `item_coverage.py::evidence_covers` 的目标逐房核对能力，但目录目标不要直接带上住宿日期：目录行没有入住退房字段，机械复用会误判全部不覆盖。把房源身份与目录需要的范围明确传入现有判定即可。
- 缺口仍为 tool_uncovered / scope_unbound，不恢复 knowledge_missing。目录提示应能让客人知道哪个片段没有答案；当前 `_uncovered_text('property', ...)` 为泛化“这次没有查到房源目录”，与已返回 401 并列会含糊，可在本次目标分支复用片段标签。

**最低验收**：302 存在但漏行、9999 未知、歧义目标只返回一个候选、全部点名目标返回、无点名全店目录；均覆盖正常与协议回退。不要仅断言内部有缺口，还要断言最终正文能定位未覆盖的房号片段，且不生成转交候选。

## 交接中三个复审问题

1. **片段直接取规划日期**：不能无条件接受。对于无日期片段可保留当前约定；对于已写日期片段，G1 证明只比入住日不足。完整区间不能降成单日再套回规划区间。
2. **没有工具锚点时默认核房态**：作为 stay_query 的保守提示可以接受；它不能证明价格已覆盖，也不能授权模型报价格。当前代码没有凭该默认生成价格证据。本轮未发现该默认本身产生新的事实误放行，不为此增加意图分类器。
3. **只有日期、没有房号/工具锚点/剩余的小句跳过**：本轮不把它单列为问题；这类片段没有独立事实目标，保留既有行为。但不能把“没有独立目标”推广到已经点名房间或写了日期端点的片段。

另有一处随修同步的注释：`_clause_rooms` 2097–2099 行仍写“我想订102”两者都不是，实际已标为 target；应按新职责更新，避免下一轮按旧注释恢复动词豁免。无须为该注释单独增加测试。

## 避免反复的统一收尾

按一张覆盖矩阵一起完成 G1/G2：**事实类别 × 点名身份 × 日期端点 × 实际返回范围 × 正常/回退**。证据证明的是哪个房间、哪段日期、哪类事实，覆盖判定就只取消那个目标的提示。

先补明 Spec §12 的日期与目录目标规则，再修改 `_clause_rooms` / `_tool_coverage` 的现有共享路径。保留 `_asks_static` 的职责修正，删除过时注释；不要增加动词词表、规划器、模型调用或数据库结构。

本地只重跑受影响回归。修复影响公共回复流程，后续发布前按 AGENTS 要求执行 all 真实模型门禁；当前用户仅要求复审，本轮不调用。门禁不等于生产收件验收，也不替代这里的确定性边界用例。

## 可复跑的最小离线反例

在仓库根运行以下脚本。依赖已有测试助手，冻结日期由 `tests.plan_helpers::PLAN_TODAY` 提供；没有真实模型、网络工具、数据库或外部消息。

```bash
PYTHONPATH=src:. .venv/bin/python - <<'PY'
import asyncio
from tests.plan_helpers import plan_for
from tests.unit.test_reply_fix2_items import (
    _assistant, _Knowledge, _Tools, _tool_message, _decision, _respond,
)

async def main():
    """经过真实助手的正常及协议回退出口，打印缺口和最终正文。"""
    stay = {'check_in_date': '2026-10-08', 'check_out_date': '2026-10-09'}
    cases = [
        ('10月8日至10日302，401还有房吗', 'stay_query',
         'search_availability', stay,
         [{'property_id': n, 'property_title': f'{n}房间',
           'stay_available': True, **stay} for n in (302, 401)]),
        ('401有什么房型，302呢', 'catalog_query', 'list_properties', {},
         [{'id': 401, 'title': '401房间'}]),
        ('401有什么房型，9999呢', 'catalog_query', 'list_properties', {},
         [{'id': 401, 'title': '401房间'}]),
    ]
    for text, kind, tool, fields, rows in cases:
        for fallback in (False, True):
            assistant, _ = _assistant(
                [_tool_message(tool, fields), 'invalid' if fallback else _decision()],
                _Knowledge([], {'302': 302, '401': 401}), _Tools({tool: rows}),
            )
            result = await _respond(
                assistant, text, plan_for(text, (kind, text, fields)),
            )
            print(text, fallback, [(g.text, g.reason) for g in result.gap_units])
            print(result.reply_text)

asyncio.run(main())
PY
```

当前六次调用均缺口为空；脚本用于观察反例，正常退出不表示修复通过。完整绝对结束日期写法 `10月8日至10月10日302` 在当前 residue 中残留“至”，走另一条保守路径，不构成 G1 反例；本报告采用解析器已支持且剩余为空的 `10月8日至10日302`，避免混淆。

## 验证与身份绑定

独立 492 项覆盖：`test_room_identity`、`test_room_identity_sqlite`、`test_item_coverage`、`test_turn_plan`、`test_deepseek_turn_plan`、`test_reply_fix2_items`、`test_live_price_scope`、`test_reply_regression`、`test_reply_fix3_boundaries`、`test_reply_evidence_boundaries`、`test_release_scripts`。补充六次反例经过真实助手，模型与工具均为合成；额外 Git HEAD 进程对照确认日期回归和目录历史遗漏的区别。

| 文件 | SHA-256 |
| --- | --- |
| `src/homestay_bot/integrations/deepseek_client.py` | `ab5152146f85488ec5c2f403df8eb0ccc3a8af8740bb7ff02be0131977c57ae3` |
| `tests/unit/test_room_identity.py` | `0bc1dc67aea4fce5ce2a48438af08361342b7ae04f2aebe8b49978f33727fecb` |
| `docs/specs/2026-10-15_room-identity-resolution-spec.md` | `bbd1a088fae40b842f2db625b3018c58fda86ec22d59b00ea67ef04c4e1da862` |
| 本轮 Claude 交接报告 | `e202ac1044cd52ba391153a9ce89b42401380d3985eda96405a090a86d0e6f85` |

源码、测试摘要与作者交接一致。没有独立重跑作者全量、真实模型门禁、生产或收件验收；本地结果不能证明这些边界。业务文件和原有工作区改动保持原状，`.impeccable/critique/` 未处理。
