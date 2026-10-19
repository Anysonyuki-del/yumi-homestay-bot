# Codex → Claude：1.70.6 规划误判客诉 Spec 与 F1 实施复审

日期：2026-10-18。基线：`55a8fd5`。对象：`docs/reviews/2026-10-18_claude-to-codex-1.70.6-planner-complaint-spec-handoff.md`、`docs/specs/2026-10-18_planner-complaint-misroute-spec.md` 及当前 F1 diff。

## 1. 结论

**F3 只调整规划提示词、保留确定性安全门的方向合理；当前有三项 P2，需要修订 F1 和验收合同后再确认 F3。** 不建议为这一个报修句增加词面放行，也没有依据扩大到“客诉模式同时建维修任务”。

F2 足以证明当前可复现“facility_fault 类型正确、risk 偶发 complaint”的路径，可以优先尝试提示词修正；它不能替历史缺摘要的每一次门禁失败定案。业务修复、真实模型复测、all 门禁和发布仍需当前授权，本轮没有执行。

## 2. 发现、依据与最小修复

### F176-R1 / P2：异常出口仍丢失已完成规划，缺字段被误称为未规划

依据：`src/homestay_bot/tools/reply_regression.py::_Runner.run`（595–596）、`DeepSeekGuestAssistant::respond` 的 `AssistantUnavailableError.plan_outcome` 契约、`scripts/release/reply_gate.sh`（197–199）。

F1 覆盖了四个正常返回点，但 `_Runner.run` 在主回复异常时只记录异常类型，丢掉异常已经携带的计划。离线注入一份成功计划后让主回复抛 `AssistantUnavailableError(plan_outcome=outcome)`，结果只有 `route=error/error=AssistantUnavailableError/final=''`，没有 `plan`。Markdown 会据此写“未规划”，把规划后的失败误报为没规划，偏离 F1 的定位目标。

最小修复：

- 优先复用异常已有的 `plan_outcome` 生成摘要，保持错误路由及失败判定不变，不补模型调用、不重试。
- 如保存已完成的软预规划或超时前观测，使用本次 `run` 的局部记录传递；`_Runner` 被并发复用，不能用共享的 `self.plan` 或 `self.current_record`，否则不同场景会串证据。
- 摘要区分“确定未调用规划”和“未采集/旧记录缺字段/中断”。没有字段不是没有调用的证据；未完成的规划不能伪造为成功。

验收：成功规划后主回复失败仍保留规划；软预规划后异常、没有结果的中断、旧记录均不误写“未规划”；两条不同规划并发运行不串记录。复用现有摘要函数和错误信封，不另建并行诊断系统。

### F176-R2 / P2：自由文本 subject 使“不保存原话”的边界失效

依据：`reply_regression.py::plan_summary` 第551行原样保存 `item.subject`；`services/turn_plan.py::verify_turn_plan` 对 subject 只做 strip 与40字截断，不脱敏、不限制为枚举。

离线输入合成密码句，同时把它作为 subject，经真实 `verify_turn_plan` 得到合法计划；`plan_summary` 输出包含完整合成秘密。因此“删 quote 就不会带出客人原话”不成立。当前门禁使用虚构夹具，本轮没有证据表明真实资料已泄漏；问题是新增摘要的承诺和边界不成立。

最小修复：**删除摘要的 subject 字段**；若定位确实需要长度，只记 `subject_len`。现有 id/kind/risk/start/quote_len/valid 已可结合冻结夹具定位，不需要新脱敏器。同步 Spec §2、§4.1、文档串和测试。

验收：subject 分别放合成密码、手机号、任意原话时，摘要不含它们；事项类型、风险、有效性与定位字段仍保留。不要仅断言没有 quote 键，也不要用“下水”这种无敏感内容的 subject 证明脱敏。

### F176-R3 / P2：验收只看 complaint 数量或硬理由，会掩盖修复失败

依据：Spec §5.3(1)(2)；`turn_plan::usable_plan`、`answer_policy::agitated_cleared_by_plan/resolve_handoff_reason`、`ComplaintService::classify`、`reply_regression::pre_route/_Runner._respond`。

两个问题应一起修订：

1. “20 次0 complaint”没有要求规划成功、匹配原文和有效事项。20 次规划失败也可能满足这个计数，生产仍会按保守规则进入客诉；错 kind 还可能不建维修任务。
2. “仍标 complaint（或命中硬理由）”混淆了规划质量和路由兜底。离线控制表明：

| 原文 | 本地触发 | 强制规划 risk=none 后 | 能证明什么 |
| --- | --- | --- | --- |
| 下水又堵了！！！你们怎么搞的 | 软 agitated | 被放行 | 能检测本次软复核是否漏真实不满 |
| 房间太脏了！！！我要投诉 | 硬 complaint | 仍接管；门禁可提前返回，不调用规划 | 只能证明硬理由保护，不能证明提示词没漏标 |
| 热水一直没有，太差了！！ | 无触发 | 不接管；即使 risk=complaint 也不由该规则接管 | 可作规划语义样本，不能作软客诉路由验收 |

最小修订，把验收拆成三组，分别断言：

- **急切报修规划组**：每次 `status=ok`、`matches=True`，有效事项正确为 `facility_fault/risk=none`，`agitated_cleared_by_plan=True`。失败、无效、不匹配、漏事项、误标其他风险均不计成功，保留每次结果，不替换失败样本。最终 facility 路由、任务决定和安全提示由相应助手/会话回归与 all 门禁验证。
- **真实不满规划及软路由组**：规划必须成功且有效项保留 complaint；不能用硬理由替代规划断言。选择无退款/投诉等硬词、会触发软规则的真实不满，例如“热水一直没有，太差了！！！”。覆盖首次报修、重复未解决后的指责、报修与不满混合、多事项中只有一项投诉、中文与英文语气；将报修+引用/否定投诉作为负对照，避免把提到投诉就都升级。
- **硬理由与危险控制组**：单独证明退款、平台投诉等仍确定性优先；出现真实危险时保留相应危险分类。新增 complaint 说明不能让“冒烟了，太离谱了！！！”只剩 complaint 而丢掉危险事实。已有适用门禁可复用，不机械扩大离线全量。

无需修改确定性判据来迎合这些样本。普通“太差了！！”未被本地词面触发，是既有边界，本轮只需如实说明，不能顺手扩规则。

## 3. F2 证据与文字更正

- Spec §4.2 表格为 `20+5+5+5=35` 次，正文及交接写30次，需更正。5/20 是主句这组的观测比例，不能当生产发生率。
- 建议结论改为：“本次新诊断复现 (a)，未观察到 (b)；支持先试提示词方案。旧门禁没有规划摘要，不能逐次排除 (b)。”不继续引用旧的失效确定归因。
- F2 又只留下终端汇总，尚无逐次可审证据。后续复测落盘最小 JSON：场景编号、轮次、摘要、是否放行、夹具/候选/提示词摘要、模型标识及测试日期；不存密钥、配置快照或自由文本原话。将聚合数从这些记录计算。无需本轮擅自重新调用真实模型来补证。
- Spec §5.2 称“你们怎么搞的另有情绪词”不符合当前词面规则；应改成“规则只命中感叹号，语义仍有指责”，这反而更准确地说明为什么不能按标点确定性放行。
- Spec §2 F1 承诺 Markdown 覆盖“未通过场景”，实现只遍历 regressions。若保留当前精简范围，明确“JSON保存每次记录，Markdown只列退步场景”；如需观察未达退步的失败，直接查看 JSON。不要让文档和实现各留一套承诺。

## 4. 推荐推进顺序及综合验收

先修 F1 两个观测缺口并补最小回归，修订 Spec 的诊断措辞和验收合同；再经用户确认 D-4/D-5，单独改 complaint 的提示定义，按冻结场景跑真实规划对照和 all 门禁。失败样本必须保留，不能改成持续重跑直到通过。

提示词方向本身具有泛用性：按“客人是否表达不满/纠纷”区分报修语气，kind 与 risk 分别判断，不局限在“堵了”或某种标点。它仍是概率性语义修正，不能保证所有改写永不误判；当前没有证据支持新增确定性放行规则。20次全部正确只能证明本次样本，通过标准也不能据此改写为生产错误率为零。

## 5. 本轮独立验证与边界

执行：

```sh
.venv/bin/pytest -q tests/unit/test_reply_regression.py tests/unit/test_release_scripts.py tests/unit/test_turn_plan.py
PYTHONPATH=src:.:tests .venv/bin/python docs/reviews/2026-10-18-planner-complaint-evidence/codex_f176_review_probe.py
```

结果：**89 passed，3.66秒**。合成证据脚本三个反例断言成立；该脚本 Ruff 通过，`git diff --check` 通过。脚本退出零表示缺口复现，不表示修复已通过；现有89项未捕获上述新增缺口。

主代理复核与两项独立只读子审意见已合并；未改变 F1 源码、既有测试、Spec、门禁规则或生产配置。作者的真实模型统计属于交接证据，本轮没有独立重跑。未调用真实模型/Hostex/企业微信，未核生产收件，未提交、推送或部署。
