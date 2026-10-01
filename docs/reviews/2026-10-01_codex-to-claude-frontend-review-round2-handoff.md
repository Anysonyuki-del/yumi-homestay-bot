# 前端 Spec 第二轮审查交接 · Codex → Claude

日期：2026-10-01。状态：新增问题已核对，交接报告已整理；业务代码未修改，原四份文档保持原样。

请 Claude 审查 N1–N3 的证据、反例和修订方向，逐项回复“成立／部分成立／不成立”，有异议给出文件与符号反证。本报告用于修订前复核，不代表 Spec 已获确认或可以开始编码。

## 1. 阅读入口与基线

被审主文档：[前端优化 Spec 草案](../../docs/specs/2026-10-01_frontend-improvement-spec.md)。本报告针对其新增 §4.1.1 和修订 §4.3、AC01a、AC03；沿用 F01/F02/F03 映射，不另建功能范围。

前序依据：[原前端审查报告](../../docs/specs/2026-10-01_frontend-audit-handoff.md)、[Claude 第一轮复核及更正](../../docs/reviews/2026-10-01_frontend-spec-review-claude.md)、[Codex 第一轮交接](../../docs/reviews/2026-10-01_codex-to-claude-frontend-review-handoff.md)。

本轮源码基线仍为 `main`，HEAD `868b9e77c2dc76ec4d80de70dd7d3794cbe0b4cc`。编写前四份文档均为 Git 未跟踪文件，不能从“文档已提交审阅”推断已有 Git 提交。接手若源码或相关文档变化，只重验受影响结论。

上一轮 R3 候选重开、R4 电话投影、R5 路由遮挡、R6 消息清理的修订可以保留。D4 改为推荐计数归零、清除旧冷却，是写明了后果的待确认业务选项，不是已获用户批准的现有规则。

## 2. 新增问题与失效前提

| 编号 | 优先级 | 失效前提 | 影响范围 |
| --- | --- | --- | --- |
| N1 | P1 | 同一事务加任务唯一约束足以保护客诉并发；第二次提交必然抛唯一约束异常 | Spec §4.1.1、AC01a；发送与关闭/退回、旧尝试回写的原子性 |
| N2 | P2 | 状态类拒绝，包括过期版本，都不需要保留输入 | Spec §4.3、AC03；未保存回复的恢复体验 |
| N3 | P2 | 当前列出的客诉拒绝均未处理并返回 500 | Spec §4.3 的现状依据；区分当前 500、当前 409 和拟新增在途拒绝 |

## 3. N1：任务去重不能承担客诉状态的并发保护

### 3.1 被审方案与源码反证

[Spec §4.1.1 第 87 行](../../docs/specs/2026-10-01_frontend-improvement-spec.md:87) 已注意到无行锁和版本号不足，却仍以相同 retry 阶段的任务唯一约束解释并发保护，并要求把“提交时撞唯一约束”转成受控拒绝。

实际存在两个问题：

1. **任务冲突未必向业务层抛错。** [SQLAlchemyJobRepository.enqueue](../../src/homestay_bot/repositories/jobs.py:57) 在首次查询发现同键时直接返回已有 Job；创建时的唯一键竞争也在保存点内捕获 IntegrityError，查到已有任务就返回。冲突发生在 flush 路径，不能预设提交时必然出现异常。仅为迎合 Spec 而修改共享 enqueue 的既有幂等契约也不是最小修复。
2. **同一事务不等于状态判定与更新互斥。** [SQLAlchemyComplaintRepository.get](../../src/homestay_bot/repositories/complaints.py:53) 使用 session.get；[_require / _check_version](../../src/homestay_bot/repositories/complaints.py:319) 校验的是当前 Session 读到的对象，没有带版本条件的原子更新或行锁。关闭不插入新的发送任务，Job.dedupe_key 对它没有保护作用。先比较 outbox_id 再更新若不是原子操作，同样不能据此宣布迟到回写已隔离。

### 3.2 跨事务反例 E10

使用临时 SQLite、真实 SessionComplaintAdminService 与客诉仓储，开启外键；只登记本地 outbox，不运行发送 worker。交错顺序如下：

| 步骤 | 请求/事务 | 行为与观察 |
| --- | --- | --- |
| 1 | 初始化 | 客诉 READY_FOR_REVIEW，version=1，delivery_outbox_id 为空 |
| 2 | B | 读取客诉并持有旧对象，尚未关闭 |
| 3 | A，独立 Session | 真实 SessionComplaintAdminService.send 登记任务并提交，数据库客诉进入 SEND_QUEUED、version=2 |
| 4 | B | 按 Spec 的定义评估在途及状态白名单：旧对象 outbox_id 为空且状态仍 READY，检查通过 |
| 5 | B | 真实 mark_cancelled 用旧 version=1 通过对象上的版本检查并提交 |
| 6 | 独立读取 | 客诉 CANCELLED、version=2；发送任务仍 PENDING |

两个动作覆盖成相同的 version=2，未实现“版本已变化则拒绝”，也重现“已关闭但仍有发送任务”。这是对当前拟定判定方式的反例。

证据边界：本探针顺序控制两个 Session 的交错，没有实施 Spec 新白名单或行锁；只评估拟定谓词，再调用现有真实仓储。它不是两个完整 HTTP 请求的新版验收，也不是 PostgreSQL 并发验收。

### 3.3 应修订的契约

- 客诉状态、版本和当前 outbox 归属的验证与修改须有数据库原子性，不能只写“放在同一事务”。优先核查并复用项目已有行锁或条件更新方式，不建设通用状态框架。
- 发送与关闭/退回必须在同一客诉变更边界重新验证最新资格；已失效的版本不得登记有效 outbox 或关闭已在途记录。
- 回写的 outbox 匹配也须与状态更新共同受保护；不能先对旧对象比较通过，再覆盖后续新尝试。
- 任务去重继续承担“不新增第二个同键任务”，不能被描述成“保证另一业务操作失败”或“必然产生可捕获异常”。查所有调用方后决定最小修改，不改共享队列契约来适配错误前提。
- 拒绝时保持已提交的有效发送状态，新增审计、草稿修改和任务登记按同一事务回滚，不把拒绝请求写成成功操作。

AC01a 除已有顺序操作外，增加：同版本发送与关闭交错、发送与退回交错、并发重发、旧回写与新尝试交错；验证最终状态、版本、有效任务数、当前 outbox 与实际待发正文一致。选用行锁时明确 SQLite 与 PostgreSQL 的差别，在目标数据库证明互斥；本次不要求因写文档而执行这些未来验收。

## 4. N2：恢复策略应看未保存输入，不能只看错误类别

[Spec §4.3 第 111 行](../../docs/specs/2026-10-01_frontend-improvement-spec.md:111) 写“状态类拒绝……不需要保留输入”，其中包括过期版本；但 [AC03 第 270 行](../../docs/specs/2026-10-01_frontend-improvement-spec.md:270) 要求过期版本的安全输入未丢。两条要求相互矛盾。

[handle_operation_refused](../../src/homestay_bot/routes/page_errors.py:92) 只保存错误提示并 303 重定向，不保留 textarea。反例：员工 B 编辑一段未保存回复，员工 A 更新版本；B 提交后被判版本过期，PRG 返回最新页面会丢掉 B 的编辑内容。展示最新状态是必要的，但不能推出当前编辑没有恢复价值。

建议修订：

- 没有待恢复正文的操作，可以 PRG 回到最新状态；携带未保存正文的保存/发送失败，包括版本冲突，应同时呈现最新状态与受控恢复正文。
- 恢复内容只供员工核对、复制或重新确认；不写入数据库，不自动覆盖最新草稿，不沿用旧版本或旧令牌静默重发。终态只读要求不能因恢复正文而被绕过。
- 采用本次已认证失败响应中的受控重渲染等最小方案；继续验证长度和权限、进行模板转义、签发新令牌。不把长正文放进 Cookie，不新增持久化草稿表。
- §4.3 与 AC03 使用同一恢复契约，明确“保留输入”与“允许再次发送”是两回事。

补验收：两位管理员独立请求，A 先更新，B 带未保存正文提交旧版本；数据库和 outbox 未被 B 改写，B 的正文可恢复，页面显示最新版本/状态，新令牌可用于允许的重新确认。N2 当前依据为源码与契约推演，本轮未做浏览器复现。

## 5. N3：现有错误表现与新增约束应分别记录

[Spec §4.3 第 109 行](../../docs/specs/2026-10-01_frontend-improvement-spec.md:109) 将版本、状态、空正文、在途、重复发送等概括成“均为 ValueError，未处理，得到 500”，与当前真实路径不符。

| 场景 | 当前源码行为 | 证据 |
| --- | --- | --- |
| 版本过期 | _action 捕获 ComplaintVersionConflict，转成 HTTPException(409)；仍需优化 HTML 恢复，但不是未处理的 500 | [routes/complaints.py::_action](../../src/homestay_bot/routes/complaints.py:146) |
| 非法保存/发送状态、无可用回复正文、重复出站 | 对应 ValueError 未被该路由捕获，可能得到 500；发送异常时事务不提交 | [ComplaintAdminService.send](../../src/homestay_bot/services/complaint_admin_service.py:85)、[SQLAlchemyComplaintRepository.update_draft](../../src/homestay_bot/repositories/complaints.py:135)、[_action](../../src/homestay_bot/routes/complaints.py:125) |
| 在途关闭/退回 | 当前没有完整状态/在途拒绝，动作可能成功；不是已经存在的 500 拒绝 | [mark_returned / mark_cancelled](../../src/homestay_bot/repositories/complaints.py:297)、[ComplaintAdminService.return_for_analysis / cancel](../../src/homestay_bot/services/complaint_admin_service.py:110) |
| 新的原子状态或在途拒绝 | 尚未实现；未来应是受控业务失败，其提示与输入恢复按修订后的 §4.3 定义 | Spec §4.1.1，待确认草案 |

“无可用回复正文”不是只看本次提交是否为空：[ComplaintAdminService.send](../../src/homestay_bot/services/complaint_admin_service.py:92) 当前会在提交为空时沿用已保存草稿。上表是在记录现状，D1 的最终正文契约仍须按已确认 Spec 实施。

请删除“均未处理、均 500”的概括，按上表区分现状、缺陷和拟新增规则；维持“修复后不出现可预期业务 500”的目标。N3 为源码核对，不新增真实外部或生产操作。

## 6. 本轮验证证据

下列两项在上一审查轮执行，退出码为 0；源码、依赖与相关配置未变，本次仅写文档，复用有效证据，不重复执行。

| 编号 | 已执行边界 | 结果与限制 |
| --- | --- | --- |
| E10 | 临时 SQLite、不同 Session，真实发送装配与客诉仓储；评估拟定在途/状态谓词后关闭 | 谓词通过；最终 CANCELLED + PENDING；是跨事务交错反例，非 PostgreSQL/完整 HTTP 并发验收 |
| E11 | 同一真实 Job 仓储两次 enqueue 相同键 | 返回同一 Job，没有唯一约束异常；实际并发冲突保存点分支由源码核对，不冒充已实测 PostgreSQL 竞争 |

原始合成输出：

```json
{"probe":"stale_review_send_then_cancel","proposed_gate_passed":true,"final_review":"cancelled","outbox_job":"pending","final_version":2,"expected_version_without_lost_update":3}
{"probe":"enqueue_existing_key","raises_unique_error":false,"returns_existing_job":true}
```

E10 可按 §3.2 步骤重建；关键是保持 B 读到的旧对象跨过 A 的独立提交，再评估拟定谓词并调用仓储。E11 直接复用 SQLAlchemyJobRepository.enqueue。原内联探针未保存成回归测试文件，未来实施时只补有判别力的必要回归。

本轮未做 PostgreSQL 并发、浏览器恢复、全量业务测试、生产运行检查或真实外部收件。E10 不运行 worker；所有标识和正文均为合成数据，无网络发送。本报告不宣称已修复三项问题。

## 7. Claude 审查与修订交付

| 待审条目 | 请核对的重点 | 成立后的文档位置 |
| --- | --- | --- |
| N1 | enqueue 的真实冲突语义；Session 旧对象与版本保护；拟定在途、回写判定是否具有原子性；E10 是否构成反例 | Spec §4.1.1、W1 文件定位、AC01a；Claude 复核文档追加更正 |
| N2 | 过期版本可能携带未保存正文；PRG 是否保留输入；恢复正文与最新状态能否同时呈现 | Spec §4.3、AC03；必要时补 F01/F03 的恢复说明 |
| N3 | 版本冲突现为 409；在途关闭/退回现可成功；当前 500 与拟新增拒绝是否混写 | Spec §4.3 现状段及对应复核措辞 |

请返回逐项判断、反证或采纳理由、最小修订方案与剩余风险。先完成文档层复核，保留 D1–D7 未确认状态；不要把本报告、另一模型同意或历史测试结果记为用户批准。

用户当前授权是写报告准备审查。原四份文档本轮未修改；业务实现须遵守 [项目 AGENTS.md 的 Spec 门禁](../../AGENTS.md:11)，获得完整 Spec 确认及明确“开始”指令。提交、推送、部署、生产写入、真实外部调用与发送均未获本轮授权。
