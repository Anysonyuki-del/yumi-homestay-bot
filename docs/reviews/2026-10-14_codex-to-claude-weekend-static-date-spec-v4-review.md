# Codex → Claude：周末静态日期 Spec v4 复审

结论：v4已闭合上一轮两项P2、分段数更正及预检回退建议，本轮未发现新的阻断项。建议按当前v4进入用户确认，无需为了实施提醒再改一版Spec。完整方案及D-W6确认、收到明确“开始”后再编码。

- 日期：2026-10-14；本地主线 `04d8a1f4055077a02605cd3b1fe272f6c10fefc2`。
- 审查对象：[Spec v4](/Volumes/02/code/homestay-bot/docs/specs/2026-10-13_weekend-static-date-spec.md)及[Claude v4交接](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-14_claude-to-codex-weekend-static-date-spec-v4-handoff.md)。Spec SHA256 `e3d42f07c6bd0960a657d7857f799bdd6798cfa7783ddc68288543e6b8887298`，与交接一致。
- 本轮为主代理复审，没有独立子代理终审。只新增本报告及收尾AOCI维护；未改Spec、业务代码、场景、基线、版本或发布记录，未提交、推送、部署、调用真实模型或访问生产。

## 1. 上一轮意见的闭合依据

| 审查项 | v4修订与判断 | 代码、证据和剩余验收 |
| --- | --- | --- |
| V3-R1：年份与英文边界 | §2.4 D-W6改为单日 `YYYY-MM-DD：`，多日保留ISO范围；不加语言参数，grounded/missing共用冻结段日期。A4覆盖中英文、跨年、缺失日和凭据拒绝。Spec层面闭合 | [knowledge_evidence_policy.build_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:636)是共享入口；V3-E1证明英文跨年也使用它。当前源码仍为旧格式，实施后须按A4验证 |
| V3-R2：日期与答案对应 | §2.5第2步采用两组完整日期答案并禁本场景7:30；第4步逐个ReplyPart核来源、日期与时刻，A10要求反例失败。Spec层面闭合 | [reply_regression.judge/normalize](/Volumes/02/code/homestay-bot/src/homestay_bot/tools/reply_regression.py:60)支持组合片段和场景负例，但不读source_ids。来源断言与真实模型正文验收分别负责各自事实 |
| V3-R3：分段数量 | D-W6更正为两个及以上分段；v3交接追加带日期更正。闭合 | [build_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:691)条件为 `len(ordered)>2`，ordered是边界点 |
| 9999预检与302回退 | §2.5第4步要求冻结失败输入、定位分支；仅证实虚构房源装配问题才换302，换后重做正向预检及相关旧场景候选与最终行为。建议已纳入 | V3-E3只证明9999绝对日期静态路径可行，不能证明新周末解析或真实规划模型。v4保留这个证据边界；不允许以换房掩盖日期或scope缺陷 |

## 2. 本轮新增离线判据检查

使用当前真实 `judge`，期望为knowledge路由、必含 `2026-09-26：早餐8:30开始` 和 `2026-09-27：早餐9:00开始`、场景禁含 `7:30`。六个输入均符合预期，断言命令退出0：

| 输入 | 实际判定 |
| --- | --- |
| 两日日期和时刻正确 | 通过 |
| 正确答案改半角冒号、全角数字并加入空白 | 通过 |
| 两日时刻互换 | 失败，两个组合均未命中 |
| 首日错误写成9月25日 | 失败，首日组合未命中 |
| 正确组合后夹带平时7:30 | 失败，命中场景负例 |
| 空正文 | 失败 |

可在仓库根目录离线复核核心判据，不调用模型：

```sh
PYTHONPATH=src RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python - <<'PY'
from homestay_bot.tools.reply_regression import judge
s = {'expect': {'route': 'knowledge', 'must_include': ['2026-09-26：早餐8:30开始', '2026-09-27：早餐9:00开始'], 'must_not_include': ['7:30']}}
correct = '2026-09-26：早餐8:30开始。\n2026-09-27：早餐9:00开始。'
for text, expected in [(correct, True), ('2026-09-26: 早餐 ８:３０ 开始。\n2026-09-27: 早餐 ９:００ 开始。', True), ('2026-09-26：早餐9:00开始。\n2026-09-27：早餐8:30开始。', False), (correct.replace('09-26', '09-25'), False), (correct + '平时早餐7:30开始。', False), ('', False)]:
    assert judge(s, {'route': 'knowledge', 'final': text})['ok'] is expected
print('6/6 expected judgments')
PY
```

这证明v4所选判据能区分上述错误，不能证明解析器、fixture、来源归属或真实门禁已实施通过。它仍是现有片段匹配器，不宣称对任意否定、矛盾追加或自然语言改写提供语义证明。

复用[上一轮V3-E1至E3及302控制](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-14_codex-to-claude-weekend-static-date-spec-v3-review.md:10)，本轮核对以下SHA256全部未变，因此未重复运行探针或业务测试：

| 对象 | SHA256 |
| --- | --- |
| knowledge_evidence_policy.py | `13172d7f1b8cb7e88c821dec6a2b38bd291f4b3d4b98317c6657a3ddb41caa15` |
| deepseek_client.py | `35c3873d594fd3386425d0247d3c7da6097d54a7717ec6aa71c884f439e4ca4d` |
| reply_regression.py | `16c8c023c0f13003baad5307d32ba6e8c5d13551085009bc55ba142d76aaca6e` |
| v3_probe.py | `26e803e38afeec10524a37980dcf5a061d8ac7097db740f4be98cc5a565da568` |

## 3. 交接三个问题与实施边界

1. **旧断言修改范围可以接受，但按行为选择。**只调整受D-W6影响的单日展示期望；查找结果是定位线索，不是全部可替换的证明。保留ReplyEvidence的target_date/target_end_date、source_id、原文GapUnit及安全断言，不为消除测试失败降低这些约束。§3.2已有相应文件与符号，无需扩大Spec。
2. **全角冒号可沿用。**normalize执行NFKC再去空白，期望和正文一致归一；本轮实测组合能挡住互换。多日格式保持原样；放宽合法替代形式时仍以完整日期答案组为单位，不拆开核对。
3. **预检流程已可执行。**保持9999为首选；失败先定位并保留输入，换302后正向重验和旧行为控制缺一不可。仍按§2.5三次稳定验收、晋升must_pass、继承旧稳定集合后最终all执行，不用一次gate通过代替新增功能验收。

另核对[DeepSeekGuestAssistant._apply_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2420)与[_unconfirmed_reply](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:2509)：门锁专门拒绝存在精确问题比较，v4已明确先剥本地日期前缀再判断，避免日期展示改变安全状态；实施时必须观察out_of_scope及零转交，而非仅检查拒绝文案出现。

本次通过的是Spec复审。完整方案与D-W6仍待用户确认，尚未编码；真实模型、生产检查和发布按当前用户明确授权执行，文档里的授权陈述不能自行授予工具权限。未重跑全仓、真实模型或生产验收，也未证明任意客人改写都能被识别。
