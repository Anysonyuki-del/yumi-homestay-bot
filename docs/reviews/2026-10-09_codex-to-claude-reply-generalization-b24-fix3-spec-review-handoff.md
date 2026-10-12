# fix3 v1 Spec 综合审查交接报告

- 日期：2026-10-09。
- 审查对象：[fix3 v1 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix3-spec.md)，119 行，SHA-256 `15f567c4d86988cb1bcde0f1a3eba451f86a8ccf5433eeafa368b32e173d2951`。
- 对照：[上一轮 fix2 实施与占位符／日志综合审查](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-implementation-review-handoff.md)，以及 [fix2 Spec](/Volumes/02/code/homestay-bot/docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md) §2.1(2)、§2.6。
- 当前 HEAD：`80f9b2c309547c36c15cfd142d5dd30b98d87406`；审查业务源码仍为上一轮 `rc-1.70.0-3` 候选，不是已实施的 fix3。当前复核的关键源码摘要见 §6。
- 操作边界：审查源码与方案、运行合成离线探针、保存本报告及证据脚本。不修改业务实现；未调用真实模型、Hostex、企业微信或生产，未提交、推送、部署。

## 1. 结论

**需修订后再进入实施：2 项 P1、2 项 P2。** Claude 选择共享根因位置的方向合理，但当前文字还不能保证闭合上一轮八项问题。主要遗漏是：凭据值的完整边界、日期范围与实际所问日期的区别，以及 CAS 改写时必须保留的空来源终态。

| 编号 | 级别 | Spec 位置 | 问题与影响 |
| --- | --- | --- | --- |
| F3-S1 | P1 | §2.1，29–36 行 | 引号包围且含空格的密码只遮到第一个空格，客诉模型边界仍收到秘密尾部 |
| F3-S2 | P1 | §2.2，41 行 | 只修敏感键引号、沿用现有值模式，JSON/repr 中转义引号后的凭据尾部仍输出 |
| F3-S3 | P2 | §2.3，47–51 行、§3.1，79 行 | 区间外守卫放过住宿区间内另一日，静态取证仍回答未被问到的日期，且将所问日期误归资料缺失 |
| F3-S4 | P2 | §2.4，59 行 | 整理入口写成一律置整理中，遗漏无可用管家消息时直接 NO_ANSWER、不入队的既有契约 |

前两项涉及确定性脱敏边界，应先补清楚再编码。后两项属于方案契约遗漏，不能将本报告中的代理观察写成“fix3 已实施失败”或“生产已发生”。

## 2. 逐项依据与最小修复建议

### F3-S1：凭据值应先处理引号边界，不能一律以空格结束

**依据。** Spec 29 行把值定义为“连续非空白 ASCII 可见字符”，同时要求含引号且整段遮盖。输入 `Wi-Fi password: "A1! SYNTHETIC_VALUE_TAIL"` 完全处于明确标签、冒号、ASCII 值的场景，但连续非空白匹配只能消费 `"A1!`。

本轮进程内代理仅为明确冒号写法增加该模式，再调用真实 [SQLAlchemyComplaintMessageContext._sanitize](/Volumes/02/code/homestay-bot/src/homestay_bot/services/complaint_review_job.py:67)。结果为：

```text
[credential removed] SYNTHETIC_VALUE_TAIL"
```

同一检查中，不带空格的 `A!b2C#d4` 已被遮盖。真实调用链是 `list_context → _sanitize → redact_credentials`，不会先拒绝整条消息；因此即便 [contains_sensitive_text](/Volumes/02/code/homestay-bot/src/homestay_bot/services/sensitive_text.py:111) 能令管理员提交拒绝，也不能证明客诉模型输入完整脱敏。此处证明真实净化调用方输出残留，没有发起真实模型请求。

**最小修订。** 继续在 `sensitive_text` 共用规则修复，但值分两种：有明确配对引号时消费完整引号值（允许内部空格及转义）；无引号时采用当前拟定的连续可见字符边界。对明确标签后开启但未闭合的引号，规定保守遮盖到该行结束。无需新隐私识别服务或通用语法解析器。

**必要验收。** 双引号／单引号、内部空格、内部转义引号、未闭合引号，以及现有无空格标点密码；断言净化后的完整载荷没有任何秘密尾部。保留无值提问、`is needed/changed` 对照。D-H3 已记录的纯字母 `is` 上限不在此重新开决策；该上限是漏识别风险，应把 78 行“安全方向”改为准确的风险说明。

### F3-S2：日志值模式必须识别转义，不能只放宽键

**依据。** 当前 [_SECRET_ASSIGNMENT_PATTERN](/Volumes/02/code/homestay-bot/src/homestay_bot/logging.py:36) 的引号值分支使用 `"[^\"]*"` 与 `'[^']*'`，不能区分转义引号和结束引号。Spec 41 行只要求键两边允许引号，并“继续”现有值处理，原反例的键问题闭合后仍有值尾部泄漏。

本轮仅在进程内给当前键模式增加可选引号，原样保留值部分；通过真实 `logging.StreamHandler + SensitiveDataFilter` 输出 JSON 和两参数 repr 日志：

```text
{"password": [REDACTED]SYNTHETIC_JSON_SUFFIX"}
fields={'password': [REDACTED]"SYNTHETIC_REPR_SUFFIX'} status=401
```

简单的带引号 `Authorization: Bearer …` 对照已脱敏，重复过滤保持幂等；因此残留定位在值结束边界，不是格式化链没运行。见 [log_value_probe.py](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-09-fix3-spec-review-evidence/log_value_probe.py)。

**最小修订。** 在现有 `redact_text` 入口修订引号值匹配：反斜线与下一字符作为一组消费，只由未转义的同类引号结束。未加引号的值保持已有字段分隔规则。引号内的方案词与空格由整个值一起遮盖，不为 JSON/repr 各造一条日志管线，也不把普通文本整体当 URL 解析。

**必要验收。** 双引号 JSON、单引号 repr、转义引号、反斜线、Bearer 带空格、同一行多个字段；实际 handler 与异常输出均不得留下秘密尾部。保留 Uvicorn 五元组、换行和栈、重复过滤幂等对照。安全边界测试的秘密应有可独立检查的尾部，不能只断言完整原秘密字符串不再出现。

### F3-S3：在入住区间内不等于绑定了静态子问的日期

**依据。** Spec 48 行只拦“入住日到退房前一天之外”的日期；79 行又承诺“入住次日早餐”回未确认。二者在两晚住宿时不一致。

本轮用真实 SQLite 知识仓储与 `KnowledgeService`，仅存 10/10 有效的早餐资料，运行本地核验过的粗粒度 `stay_query`：计划日期为 10/10 入住、10/12 退房，原文为“10月10日住2晚有房吗，10月11日早餐几点开始”。10/11 在拟定区间内，48 行守卫不会触发；正常与 `availability_fallback` 的真实适配器、会话和事务发件箱均输出：

```text
2026-10-10 — 2026-10-10：早餐7:30开始。
当前审核资料尚未确认早餐信息。……
10月10日入住、10月12日退房：可订：401
```

早餐证据的 `target_date` 为 10/10；10/11 小句缺口为 `knowledge_missing`。直接按 10/11 检索返回零条。证据计划确实按有效期分段，**没有证明它把 10/10 的答案冒充成 10/11**；已证问题是回复混入没问的 10/10 事实、所问日期未绑定，而 Spec 的新守卫不处理此输入，正常出口还可能把范围问题转交为资料缺失。

源码链：[_item_dates](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1885) 返回整项住宿区间；`_merge_item_knowledge` 以该区间检索；[KnowledgeService.retrieve_detailed](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_service.py:739) 保留相交条目；[_item_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1704) 把整项日期传给静态小句；[build_evidence_plan](/Volumes/02/code/homestay-bot/src/homestay_bot/services/knowledge_evidence_policy.py:636) 分段生成各日期的回答。

**最小修订。** 先写清静态小句的日期绑定判据，按已有 `clause_dates` 的端点和角色核对，不能只做区间包含检查。沿 D-H1 的保守决定，不能证明与该项静态目标一致的小句记 `scope_unbound`，不转交，不增加逐小句检索。绑定成功时，静态取证日期应是已绑定的实际所问日期，而非机械传整个住宿区间。没有显式日期的小句保留已有跨期分段行为。

同时在本修复内约束 `static_fact` 分支：目前 1686–1690 行只在**所有**小句未绑定时跳过整项，否则仍拿 `item.question` 作答。不能先标部分小句未绑定，再让包含这些小句的整项问题重新借用候选。无法安全保留其余静态内容时可以保守拒绝该项，不应新增第二套分项规划。

**必要验收。** 同日／区间外／区间内另一日三组；每组覆盖正常与协议失败回退，断言最终正文、静态证据日期及缺口原因。增加一项跨两个静态小句且其中一个范围未绑定的对照，确保不能绕回整项取证。保留真正跨期住宿问政策的原分段正例。

### F3-S4：CAS 应保留无来源直接结束的状态转移

**依据。** [KnowledgeAdminService.start_referral_draft](/Volumes/02/code/homestay-bot/src/homestay_bot/routes/knowledge.py:649) 当前在冻结列表为空时直接写 `NO_ANSWER`，仅非空时入队；本轮真实内存 SQLite 控制检查为 `no_answer draft_jobs 0`。Spec 59 行却一律写“置整理中”，验证计划也只有清空／入库竞争，没有空来源分支。

若按该文字改 CAS，同时保留当前 `if frozen: enqueue`，空来源会成为无作业的 DRAFTING，且整理中又不可重新整理。后台 [recover_stale](/Volumes/02/code/homestay-bot/src/homestay_bot/repositories/jobs.py:312) 只从真实超时作业反查补偿，不能救没有作业的记录。此项是确定的契约冲突及实施风险，尚未模拟一个完整新 CAS 实现。

**最小修订。** CAS 的目标状态明确为 `DRAFTING if frozen else NO_ANSWER`；两者都递增代次、清旧稿并保存冻结编号。只有取得资格且 frozen 非空才按**新代次**入队；状态、作业、审计同事务，失败全部回滚。条件更新后须保证对象或入队变量使用新代次，不能沿用读到的旧值。

**必要验收。** 没有管家消息、全部消息因敏感被排除，均立即 NO_ANSWER、零模型调用、零草稿作业；有普通消息时恰好入队一次且任务代次等于数据库新代次。并发开始／关闭／清空应有明确胜者，旧任务不得回写。无需新锁服务或额外恢复状态。

## 3. 可以保留的方案与连带约束

| 原问题 | 本轮方案判断 | 实施时保留的边界 |
| --- | --- | --- |
| I70-R1 | 检测与净化共用 `sensitive_text` 正确，补 F3-S1 | 客诉是净化后继续输入；管理员是检测后拒绝，二者不能互相替代验收 |
| I70-R2 | 共用 `redact_text` 正确，补 F3-S2 | 不破坏访问日志五元组、栈及幂等 |
| I70-R3 | 共用范围守卫正确，补 F3-S3 | 取证、回退、共享已核验候选复用都须受同一日期判据约束；保留候选和模型可见预算 |
| I70-R4 | 以 `needs.residue` 静态取证正确 | 原小句仍核范围、编缺口编号；排除词用整轮；免费／包含等静态费用仍需证据 |
| I70-R5 | 排除日期跨度后识别房号合理 | 复用已识别日期跨度，不全局删四位数；房号、剩余、范围核对口径一致 |
| I70-R6 | CAS 放在资格取得与同事务写入处正确，补 F3-S4 | 0 行拒绝必须回滚整笔，保留 PostgreSQL 行锁；不可在认领后单独提前提交 |
| I70-R7 | 开放在前加原生有界分页足够 | 当前 `count_open` 读取所有开放 ID 再计数；实现“每页查询量有限”时宜原位改 SQL COUNT，口径不变，无需缓存服务 |
| I70-R8 | `safe_return_path` 后设置异常回跳正确 | 知识列表兜底，保留筛选与锚点；HTML 提示、API 422、CSRF 一次消费保持 |

继续保留上一轮已核验的卡片最小内容与两阶段、原生接管复核、来源完成收据与等待后清空复核、草稿条件写回。上述肯定是方案判断和现有源码边界，不是新实现验收。fix2 后续决定回填有必要，不应把三项既定业务选择重新包装成待决，也不应凭回填宣称已完成独立改写集验收。

## 4. 复现与验证范围

本轮只运行两份最小证据脚本，均退出 0；**退出 0 表示代理遗漏／当前控制现状得到证实，不表示 fix3 修复通过**：

```sh
PYTHONPATH=src:.:tests:tests/integration PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-10-09-fix3-spec-review-evidence/boundary_probe.py
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-10-09-fix3-spec-review-evidence/log_value_probe.py
```

- `boundary_probe.py`：明确冒号的连续 ASCII 值代理；区间内另一日的真实检索、适配器、会话和最终入队；空来源真实管理员服务对照。
- `log_value_probe.py`：只放宽敏感键引号的进程代理，实际 handler 输出，含正常 Bearer 与幂等对照。
- 两者仅用合成秘密、临时 SQLite、合成模型／工具结果；最终入队不是平台受理或客人实收证据。
- 源码未变的旧套件结果只保留为上一轮证据，没有重复跑全量、PostgreSQL、浏览器或真实模型门禁。两名子代理因额度限制未完成，本报告结论由主代理独立核验，不计作双重审查。
- 本轮新增脚本做局部 Ruff 检查；报告做文档空白及链接检查。未验证 fix3 的 HTTP、目标 PostgreSQL 竞争、生产日志、真实模型或真实收件。

后续最小闭合顺序：先修 Spec 的值边界和日期判据，再一次实施共享函数及 CAS／页面改动，把上一轮八项反例与本轮四项边界合并进已有行为测试。权限／事务相关用例单独在隔离目标数据库验证，页面路径用已有真实服务浏览器框架。只有相关输入改变才重跑受影响验证，不用无关用例数量证明这些漏洞被修好。

R3–R5 会改变公共回复，新的真实模型 `all` 门禁有必要；旧 rc3 门禁不能覆盖新实现。本文未执行外部调用，Spec 的作者授权记录不自动成为 Codex 当前外部操作授权。完整 Spec 修订确认与用户明确“开始”仍是编码门禁；本报告仅完成审查。

## 5. 交接要求

Claude 请在同一份 fix3 Spec 中处理 F3-S1–S4，逐项说明采纳的规则、具体符号和必要行为断言，并纠正 78 行风险措辞。优先复用现有解析、脱敏、条件更新和分页能力；不新增通用凭据识别框架、逐小句检索服务、锁服务或分页缓存。

本轮新增文件只有本报告与两份证据脚本；仓库已有业务、Spec 和交接文件属于原工作区。AOCI 托管资产按项目收尾规则另行维护，不将其当业务修复或发布证据。禁止因本次审查而提交、推送、部署、发送外部消息或整文件回退已有改动。

## 6. 本轮关键源码摘要

以下摘要已从本轮工作区重新取得，可用于判断后续改动是否让审查依据失效：

| 文件 | SHA-256 |
| --- | --- |
| `src/homestay_bot/services/sensitive_text.py` | `10eeb4f2989583da649d6c422e050ea464014030b513ee6f4a78b6fd387a1d00` |
| `src/homestay_bot/logging.py` | `a227341b7efeff8e4b92f7561da9dcf43bb7b9c87fa8f5b31394380f51745645` |
| `src/homestay_bot/integrations/deepseek_client.py` | `6933252064e770e7af972f27aae7ddc554059f7f9beb71a689f5e67a221ca0d9` |
| `src/homestay_bot/routes/knowledge.py` | `f31ccf0e31f75c52218dafc99b7e33ffd012e031a62037ed46332c5f3edbfb53` |
| `src/homestay_bot/services/knowledge_evidence_policy.py` | `13172d7f1b8cb7e88c821dec6a2b38bd291f4b3d4b98317c6657a3ddb41caa15` |

## 7. 2026-10-09 v2 复审追加

### 7.1 当前结论与四项闭合

复审对象为 fix3 **v2**，139 行，SHA-256 `5a5b8edc2af3031cb74a3be2314c9f130d4033f546c0c8b70afed90d6e0046b1`。重新读取实际 Spec，并核对 §6 的凭据、日志、适配器与知识后台四个源码摘要全部不变。现有 Git dirty 状态属于之前候选，不能因此断言已实施 fix3。

**最新结论：F3-S1–S4 的主要修订已闭合；尚需补一项 P2 的编号衔接契约（F3-V2-R1）。** 只需同步已有布局与一个行为验收，不需要新的业务决定或扩展架构。本次仍不开始编码，不调用外部服务。

| 原意见 | v2 对应位置 | 方案复审结论 |
| --- | --- | --- |
| F3-S1 | 39–42、47–51、108 行 | 配对引号优先、转义与内部空格、未闭合到行尾、完整模型输入尾部断言已补；方案闭合，等待实现验收 |
| F3-S2 | 55–57、109 行 | 日志值按真正结束引号匹配，保留五元组、栈与幂等；方案闭合 |
| F3-S3 | 62–71、99、107 行 | 区间内明确单日按该日对现有候选取证，范围外失败关闭，部分未绑定不绕回整项；主要修订闭合，编号衔接见下 |
| F3-S4 | 79、110 行 | 空来源 NO_ANSWER、不入队；非空才用新代次入队且事务回滚完整；方案闭合 |

98 行已准确披露纯字母 `is` 写法漏识别，87 行已承诺 SQL COUNT。D-H1 可与 v2 的区间内单日规则共存：现有候选已按住宿范围检索，局部以 d 核验有效期无需新检索；范围外仍未绑定、不转交。没有据此要求重新选择 D-H1–H3。

“四项都复现成立”须按 §4 的证据类型理解：S1/S2 是 v1 规则代理，S3 是现有路径与拟定守卫对照，S4 是现有正确分支加契约退化推演；它们不是四项已实施的 v2 缺陷，也没有生产复现证据。

### 7.2 F3-V2-R1（P2）：静态项拆成小句后，调用前后的缺口编号必须同步

**Spec 位置：70 行及 107 行。** 新方案规定 `static_fact` 中有一个小句未绑定时，其余小句独立取证，复用 stay_query 小句路径。该路径给资料缺口使用 `unit_id_for(item.id, clause.start, clause.end)`，而当前 [_gap_unit_offers](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:1553) 给所有 `static_fact` 只提供整项编号。调用前 `_respond_with_plan` 将该列表放入模型信封；模型只能选择列出的编号。

真正接收选择的 [_referral_candidates](/Volumes/02/code/homestay-bot/src/homestay_bot/integrations/deepseek_client.py:3137) 使用精确 `unit_id` 匹配，随后 `ConversationService._refer_gaps` 还要求该编号出现在最终 missing 分项的 `gap_unit` 中。因此只迁移取证分支、未同步 offers 与最终分项，会让本来合法的资料缺失转交被静默丢弃。

**离线契约对照。** 原文“10月10日健身房几点开门，10月11日早餐几点开始”，一个本地核验通过的静态项携带 10/10–10/11 的一晚范围。按 v2，第二句范围外不转交；第一句范围内，若无健身房资料应允许模型选择后转交。本轮真实编号生成与真实候选过滤结果：

```text
模型收到的整项编号：1:0-26
按 v2 小句路径生成的健身房缺口编号：1:0-13
按提供给模型的编号选择：候选数 0
正向控制，选择与小句一致的编号：候选数 1
```

见 [offer_id_probe.py](/Volumes/02/code/homestay-bot/docs/reviews/2026-10-09-fix3-spec-review-evidence/offer_id_probe.py)。该脚本只代理 v2 拟定的小句缺口，并实际调用当前 offers 与过滤函数，**没有证明完整 v2 已实施或真实模型已失败**。v2 没有列出 `_gap_unit_offers` 的同步修改，也没有覆盖此合法转交，不能用“未绑定句不得转交”断言代替。

**最小修订。** 在 §2.3 增补一个实现契约：是否按小句取证及其原文区间，在模型调用前已有的 `fact_layout` 中统一确定；`_gap_unit_offers`、取证后的 `GapUnit` 与最终 missing `ReplyPart.gap_unit` 必须消费同一布局及编号。保持 `unit_id_for` 和精确选择校验，不把整项选择自动扩散给所有小句，不新增编号兼容层、二次模型调用或新的规划器。

**必要验收。** 在 107 行现有“静态两句其一未绑定”用例中增加合法缺口的闭合断言：主模型从真实信封选择第一句已提供的编号，第一句无资料时建立且仅建立一条资料核实记录、一张卡片，最终正文带该句转交说明；第二句保持范围未绑定、零转交。未知编号仍拒绝，有资料的第一句零转交。正常协议出口走此正向链，协议失败回退继续没有模型转交选择。用真实会话、仓储与事务发件箱证明结果，不能只断言 `_referral_candidates` 返回值。

### 7.3 v2 复审验证与交接边界

```sh
PYTHONPATH=src:.:tests PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/reviews/2026-10-09-fix3-spec-review-evidence/offer_id_probe.py
```

本轮只新增上述一个契约探针，退出 0 表示编号失配代理与正向控制成立。凭据／日志的旧探针及旧业务套件未重跑，因为源码和对应输入未变；对新增脚本做局部 Ruff、对报告做链接和空白检查。没有实现验收、目标 PostgreSQL、浏览器、真实模型或生产证据。

请只补 F3-V2-R1 的共享编号与闭合验证要求，再保持等待用户明确“开始”的状态。本次修改本报告、新增证据脚本；Spec 本文未改，AOCI 托管资产按最终稳定状态维护。


## 8. 2026-10-09 Codex 本地实施交接

### 8.1 当前结论、授权与归属

**fix3 v2 的 R1–R8、F3-S1–S4 及 F3-V2-R1 已在本地落地并验证。** 依据用户当前明确指令“你来根据 spec 编写代码”。前文 §1–§7 是历史只读审查，不代表现在仍未实施；其源码摘要也不再绑定本次实现，当前摘要见 §8.6。

当前主工作区为 `main`，HEAD 仍为 `80f9b2c309547c36c15cfd142d5dd30b98d87406`。保留 Claude 已有的 1.70.0 候选改动，在实施前字节备份上逐文件比较本次增量；不把相对 HEAD 的所有改动归给 Codex。版本仍为 1.70.0，没有新增依赖、迁移或新的规划/检索/锁服务。

本轮只执行本地实现与验证，没有真实模型、Hostex、企业微信、生产操作、Git 提交、推送或部署。D-H1–H3 原业务决定保持；文档里作者记录的历史外部授权没有被用作本轮 Codex 外部调用授权。

### 8.2 根因修复及实现依据

| 编号 | 根因及当前实现 | 主要文件、符号与行为证据 |
| --- | --- | --- |
| R1 / F3-S1 | 旧规则只认字母数字前缀；先处理配对/未闭合引号、转义与空格，再处理 ASCII 可见值。凭据先于手机号/长编号替换，避免占位符截断完整秘密。英文 is 按既定消歧契约；中文短显式值也遮盖。 | `services/sensitive_text.py::_REDACTION_RULES`、`_redact_english_credential`、`redact_credentials`；`test_sensitive_text.py::test_credential_values_are_removed_through_the_shared_complaint_boundary`；真实草稿 SDK 请求无秘密尾部、管理员提交被拒且无知识行见 `test_knowledge_referrals.py` 末两项。 |
| R2 / F3-S2 | 敏感键支持 JSON/repr 引号，值按转义组合消费到真正结束引号；结构化 mapping/list 消息保留递归处理，避免先渲染破坏结构。 | `logging.py::_SECRET_ASSIGNMENT_PATTERN`、`SensitiveDataFilter.filter`；`test_log_redaction.py::test_actual_handler_redacts_quoted_keys_and_escaped_values` 经真实 handler 核验正文、参数、异常和栈；原五元组与幂等用例保留。 |
| R3 / F3-S3 | 检索住宿范围不等于实际问到的单日。共用布局给每个静态小句绑定日期，范围内单日只核该日；任一小句日期/房号未绑定则禁止整项取证，范围外仍未确认、不转交。无计划项日期时先用自身摘录日期。 | `item_coverage.py::static_clause_dates`；`DeepSeekGuestAssistant._fact_layout`、`_item_dates`、`_item_evidence_plan`、`_shared_grounded`；`test_reply_fix2_items.py::test_static_clause_uses_its_own_day`、`test_each_static_item_retrieves_its_own_absolute_day` 与最终发件箱用例。 |
| R4 | 房价整句污染早餐属性。工具锚点移除后的 `needs.residue` 用于静态取证，原小句仍负责范围和编号，排除词保持整轮输入。 | `DeepSeekGuestAssistant._item_evidence_plan`；`test_tool_price_words_do_not_change_breakfast_attributes` 覆盖“和”/逗号、正常/协议回退；最终出站保留 8:30。 |
| R5 | ISO 年份被误当四位房号。复用已有绝对日期与区间模式，先用空格屏蔽日期再识别房号，避免数字拼接。 | `item_coverage.py::clause_rooms`；`test_item_coverage.py::test_date_spans_are_not_room_numbers` 及 R4 的 ISO 最终出站对照；真四位房号、英文房号、电压与金额正反例保留。 |
| R6 / F3-S4 | SQLite 读后写没有行锁。整理、入库、关闭用状态/代次条件更新争取资格；成功后的记录、知识、作业、审计同事务，失败回滚。无可用来源保持 NO_ANSWER、零作业；非空按新代次入队。 | `KnowledgeAdminService.start_referral_draft`、`convert_referral`、`close_referral`；真实 SQLite 清空先提交/入库先提交、开始/关闭/清空有胜者、入队失败回滚；真实 PostgreSQL 两次入库只有一次成功。 |
| R7 | 时间截断隐藏最旧开放记录。开放状态优先，再按时间/编号稳定排序，每页 50+1；待处理数改为 SQL COUNT，偏移穿过现有会话适配器。 | `SQLAlchemyKnowledgeReferralRepository.list_recent/count_open`；`KnowledgeAdminService.list_referrals`；`SessionKnowledgeAdminService.list_referrals`；`referral_index`、`templates/knowledge/referrals.html`；浏览器从第一页翻到最旧记录并打开，覆盖 100 新终态与 101 开放。 |
| R8 | 启用拒绝未指定回跳，全局错误页回到任务页。先校验来源，再把筛选/锚点挂到 `KnowledgeFormError.return_to`。 | `routes/knowledge.py::toggle_knowledge`；浏览器 HTML 303 回原知识筛选页并显示错误，API 422，数据库仍停用，重复 nonce 返回 409。 |
| F3-V2-R1 | 调用前整项选项和调用后小句缺口失配。`_ItemLayout` 在模型前确定绑定与拆分，选项、GapUnit、ReplyPart.gap_unit 共用同一原文区间编号；未绑定小句不列选项。 | `_gap_unit_offers`、`_item_evidence_plan`；`test_reply_fix3_boundaries.py::test_offered_clause_id_reaches_final_outbox` 从真实信封选编号，经实际助手、会话、仓储、出站与收据：缺资料只建一记录/一卡片，已知或未知编号零转交，范围外第二句零转交。 |

修改 `application.py` 仅为分页参数转发，是 R7 的必要装配修改，已补入 Spec 并触发项目要求的本地全量。fix2 Spec §4 同步模型标注否决、同轮通用无条件条目复用和服务收费追问的既有实施决定；没有另造检索通道。

### 8.3 验证结果与有效范围

| 验证 | 实际结果 | 证明范围 |
| --- | --- | --- |
| 修改前 R3–R5 最小红测 | 8 failed / 36 deselected | 原实现无法通过新增日期/剩余语义/ISO 行为断言。 |
| 一次本地全量 | **2926 passed / 15 skipped / 11 warnings，159.03 秒，exit 0** | 隔离 PostgreSQL 16.15、Chromium、本地真实装配；`RUN_LIVE_CONTRACT_TESTS=0`，15 项真实外部契约跳过。 |
| 全量静态检查 | Ruff 通过；Mypy 155 个源码文件无问题 | 本轮业务实现；最后 R1 微调另对该文件执行 Mypy，通过。 |
| R1 最后补修 | 104 passed（凭据、日志、转交相关）；最终正反断言 3 passed / 48 deselected，0.28 秒 | 全量后发现的 `No password is needed.` 误遮已补；needed/changed/required 带句末标点保留。冒号/等号的引号值完整遮盖；is的引号值去掉引号后仍须含数字/符号，纯字母仍有D-H3漏识别上限。数量重叠，不累加。 |
| 最终出站补证 | `test_reply_fix3_boundaries.py` 19 passed；最后 6 个新增断言定向 6 passed / 13 deselected，5.22 秒 | R3 日期范围内另一日/范围外、R4/R5 的 ISO 房价加早餐，正常/协议回退均经过真实会话和事务发件箱；最终正文不混 7:30，正常早餐保留 8:30，保持 BOT_ACTIVE，零误转交。其他 13 项含 F3 编号链和 CAS 竞争。 |
| 变异检查 | **9/9 被对应测试捕获** | 逐项移除 R1–R8 或 F3-V2-R1 修复，对应行为测试均失败；每次恢复完整原始字节并断言一致。不是多次全仓，也不代表真实模型或生产验收。 |

本地全量使用专用合成测试角色、独立临时 PostgreSQL 库和回环端口，从空库迁移到 `0035` 后运行；没有连接生产库，完成后临时服务已关闭。本轮未修改迁移，因此未重复旧版迁移往返验收。新 PostgreSQL 用例 `test_two_real_conversions_create_exactly_one_entry` 调两个真实服务请求与独立会话，实际一成功、一拒绝、一条启用知识，不用“锁住一行”代理最终业务结果。

11 条警告涉及 TestClient/httpx 弃用、SQLite 连接清理、配置路由线程事件循环和 Alembic `path_separator` 提示。没有运行未改动旧基线作对照，不把它们武断称为已证明的历史警告；本轮没有因此扩展修改无关代码。

复验入口（PostgreSQL 全量须事先提供符合项目安全校验的独立临时测试库环境，不能用生产连接）：

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q
PYTHONPATH=src:.:tests .venv/bin/pytest -q tests/integration/test_reply_fix3_boundaries.py
PYTHONPATH=src:.:tests .venv/bin/pytest -q tests/unit/test_sensitive_text.py tests/unit/test_log_redaction.py tests/integration/test_knowledge_referrals.py
.venv/bin/ruff check .
.venv/bin/mypy
git diff --check
```

已有全量仍覆盖全部最终业务代码，之后仅 R1 行为变化已定向复验、最终出站补证只增加测试；不再机械重跑全量，也不把未运行的新全量总数写成通过。

### 8.4 独立审查、自审与泛用性边界

独立代理已读完本次八项业务增量与主要调用链，确认 CAS 状态/代次、失败回滚、空来源、新代次入队、分页 COUNT、适配器偏移与启用回跳契约，并发现 R1 英文状态句末标点误遮。该问题已修及补正反例。代理随后触及额度上限，**没有完成最终独立终审**；主代理重新核对实际增量、调用点与简化检查，没有以代理未返回的结论充当审查通过。

这些改动将日期、房号、静态剩余语义与缺口编号集中在已有布局和共享函数，覆盖“和”/逗号、ISO/中文日期、正常/协议回退等会改变词面的路径。并不据此声称任意客户改写都已解决：规划失败、旧联网早返回、主题/属性词面识别仍有原有上限。测试中的模型、房态和知识候选均为合成数据；范围内静态单日核验现有候选，不按小句再检索。

D-H3 的纯字母英文 is 密码仍有漏识别；needed/changed/required 加句末标点仍按说明保留，同拼写真实密码也有歧义，已写明中文注释、ponytail 上限及 Spec。没有声称新增通用隐私识别。后台 offset 分页在同时新增/状态变化时不提供冻结快照，这是现有页码模式边界，本轮只保证给定结果集排序稳定与可达。

### 8.5 交接、未覆盖与禁止事项

1. 当前代码改变公共回复，**rc-1.70.0-3 的模型门禁不再覆盖**。需要当前授权后运行 `REPLY_GATE_SCOPE=all`；不能沿用旧 138/154、旧源码一致或历史生产核对。
2. 当前没有生产登录页面、真实平台受理、客人/员工实际收件证据；本地 outbox 只证明入队正文与路由，不能冒充实际送达。
3. 本次新增回归为 `tests/integration/test_reply_fix3_boundaries.py`；其余在已有凭据、日志、逐项覆盖、资料核实、PostgreSQL 与浏览器测试中补充。Spec、CHANGELOG、发布草稿和本报告已经同步。AOCI 由主代理在这些对象稳定后完成维护，具体成功/阻断以工具回执为准。
4. Claude 接手应只审本次增量和以上共享边界，保留原候选及历史报告；不要整文件回退，不把这些文档当提交、推送、部署、真实模型、生产或消息发送授权。

### 8.6 最终业务源码摘要

以下绑定本次稳定实现；后续改动相关源码后，只重跑受影响验证并更新相应证据。

| 文件 | SHA-256 |
| --- | --- |
| `src/homestay_bot/services/sensitive_text.py` | `8c0292309c2a832ed839b1d40c58b5e3c5b93d6ff7d57a3acc4dc2e6be8e90be` |
| `src/homestay_bot/logging.py` | `1962c03d6d41308c991e4de97cb9485b33c196accffef443c13fefa1a36cc2d6` |
| `src/homestay_bot/services/item_coverage.py` | `14ed22ab17e91126ce7a9d29b206f22aec8bd486b997d2f7496133d3ac05f763` |
| `src/homestay_bot/integrations/deepseek_client.py` | `7a29e310315d55c1eb1b456fc957becf2e26d0527e45c2ef4dd7a4a7f20ca642` |
| `src/homestay_bot/routes/knowledge.py` | `c02a158699da513b12aabbf454f52fb7fa7a6bf661e3ef89a2cbb98c2bc34e32` |
| `src/homestay_bot/repositories/knowledge_referrals.py` | `50447e61af478be4a98f53992535d81f3b03ec4ab4b9de342c21ce2bc963939c` |
| `src/homestay_bot/routes/knowledge_referrals.py` | `576b218ceb39695bdf802cf7f4af15ee95b18d3f0faa7bcc6bf21d299643e4ac` |
| `src/homestay_bot/application.py` | `1246bb1528ec6abad13a82011e18380fe44132b5ecc54fc502a041be4faa5e0e` |
| `src/homestay_bot/templates/knowledge/referrals.html` | `cbf9df69d90e29e544f3189530cfcd48ebdbad165da1e7d6f52fd4b342dda13a` |

## 9. 2026-10-12 Claude 独立终审与真实模型门禁

### 9.1 独立终审

- 源码核对：§8.6 的 9 个业务文件 SHA-256 与当前工作区逐一一致，§8.3 本地全量 2926 passed / 15 skipped 仍覆盖当前源码，未重复全量。
- 逐文件审阅相对 `rc-1.70.0-3` 的全部业务增量（`sensitive_text.py`、`logging.py`、`item_coverage.py`、`deepseek_client.py`、`routes/knowledge.py`、`repositories/knowledge_referrals.py`、`routes/knowledge_referrals.py`、`application.py`、`referrals.html`），对照 fix3 Spec v2：
  - R1/F3-S1：凭据规则先于手机号/长编号；成对引号整段、未闭合到行尾、无引号连续可见字符；英文 is 按 D-H3 与 needed/changed/required 句末标点消歧；通用中文密码并入共用规则，客诉边界同样遮盖。
  - R2/F3-S2：键可带引号，引号值按转义成组消费；字典/列表消息走结构递归，不先渲染。
  - R3/F3-S3、R4、R5、F3-V2-R1：`static_clause_dates` 按端点绑定；没点日期的小句仍用原日期，行为不变；任一小句未绑定则不再整项取证；剩余语义取证；日期区间先排除再认房号；选项、缺口与分项共用 `_ItemLayout`。
  - R6/F3-S4：三处状态转移均为状态（+代次）条件 UPDATE，`rowcount≠1` 拒绝并整体回滚；空来源仍直接没找到答案、零作业；入队用新代次。
  - R7/R8：开放优先稳定排序 + offset 分页、SQL COUNT；启用拒绝挂经 `safe_return_path` 校验的 `return_to`。
- 结论：**未发现新的阻断问题。** 记录一项既有边界（非本轮引入，rc3 同样）：英文标签后用空格分隔（`Wi-Fi password please`、`password reset`）时，后面 4 位以上的词会被当凭据遮盖，只影响可读性、不漏遮。
- 独立复跑：fix3 边界、凭据、日志、转交、逐项覆盖与发布脚本相关测试 **220 passed**；Ruff 通过。

### 9.2 真实模型门禁（`REPLY_GATE_SCOPE=all`，用户 D-H2 授权）

- 候选 `rc-1.70.0-4`（`92fb52c`，本机标签，用临时索引 `git commit-tree` 生成，分支未移动、未推送）；`src/`、`tests/`、`scripts/`、`migrations/`、`pyproject.toml`、`requirements.lock` 与工作区逐文件 0 差异。
- **通过**：154 个场景，首轮 135 通过；退步无；新纳入不退步集合 E-地铁；安全类未通过 0；运行出错 0。
- 规划 142 次：P50 1018 ms、P95 1460 ms、最大 2454 ms，规划失败 3 次；首轮单场景 P50 3.2 秒、P95 5.0 秒（比 rc3 慢约 0.3–0.8 秒，同一时段模型侧延迟也可能波动，未单独归因）。
- 首轮通过数比rc3（138）少3：K-厨房-C、MT-延迟周六、SR-遗失、SC-401有上下文由通过变失败，U-充电桩恢复通过；前两项属必过，补跑均失败/通过/通过，后两项属已知未通过，故无当前基线退步。SR-遗失本轮仅一次失败，不据此称稳定失败。提议新基线 `.stage/reply-gate-rc-1.70.0-4.baseline.json`：必过137 / 已知未通过17；仅绑定修复前rc4，终审日期补修后须重新验证。

### 9.3 仍未覆盖

- 提交、推送、CI、部署未执行（等待审查与授权）。
- 测试号真实收发（改动客人收件内容与员工卡片）需当次授权。
- 生产日志滚动配置待用户决定。
