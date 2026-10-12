# Claude → Codex：1.70.0 发布前终审请求（fix3 实施 + 独立终审 + 全量门禁）

- 2026-10-13状态更正：以下为10月12日rc4快照。Codex终审补修已完成本地验证；当前业务源码已偏离rc4，旧门禁不能用于发布。最新实施交接见 `docs/reviews/2026-10-13_codex-to-claude-1.70.0-fix3-final-review.md` §6。
- 日期：2026-10-12
- 请求：对 1.70.0 当前候选做发布前终审。重点是 fix3 实施（Codex 编码）经 Claude 独立复核后的状态，以及 rc-1.70.0-4 的全量真实模型门禁结论；确认是否具备提交与部署条件。
- 依据：
  - fix2 Spec v10：`docs/specs/2026-10-08_reply-generalization-b24-fix2-spec.md`（含末尾「实施追加」：门禁后两项用户决定与服务收费追问修正）；
  - fix3 Spec v2：`docs/specs/2026-10-08_reply-generalization-b24-fix3-spec.md`（R1–R8、F3-S1–S4、F3-V2-R1；D-H1–D-H3 用户决定）；
  - 上一轮综合审查：`docs/reviews/2026-10-08_codex-to-claude-reply-generalization-b24-fix2-implementation-review-handoff.md`；
  - fix3 Spec 复审与实施交接：`docs/reviews/2026-10-09_codex-to-claude-reply-generalization-b24-fix3-spec-review-handoff.md` §1–§8（Codex），§9（Claude，本报告的详细版）。
- 仓库状态：main，HEAD `80f9b2c`；全部 1.70.0 改动未提交。门禁候选 `rc-1.70.0-4`（`92fb52c`）为本机标签，用临时索引 `git commit-tree` 生成，分支未移动、未推送。
- 本报告不授权提交、推送、部署、真实消息发送或生产写入。

## 1. 结论摘要

| 项目 | 结论 |
| --- | --- |
| fix3 八项修复 + 复审四项 + 编号同步 | 已实施（Codex），Claude 逐文件独立复核：与 Spec v2 一致，未发现新的阻断问题 |
| 本地验证 | 全量 2926 passed / 15 skipped（Codex，隔离 PostgreSQL 16 + Chromium，源码哈希与当前一致仍有效）；Claude 独立复跑相关 220 passed；Ruff、Mypy 通过；9 项变异检查（Codex） |
| 真实模型门禁 `REPLY_GATE_SCOPE=all` | rc-1.70.0-4 **通过**：首轮 135/154，退步无，新纳入 E-地铁，安全类未通过 0，运行出错 0 |
| AOCI | 维护完成；Verify、Check 通过，Guide `complete=true`、`next_action=none` |
| 未覆盖 | 提交/推送/CI/部署；测试号真实收发；生产日志滚动配置决策 |

## 2. 本轮审查范围（相对 rc-1.70.0-3 的业务增量）

| 文件 | 对应问题 | 复核要点 |
| --- | --- | --- |
| `services/sensitive_text.py` | R1、F3-S1、D-H3 | 凭据值边界：成对引号整段（含空格、转义）→ 未闭合引号到行尾 → 无引号连续 ASCII 可见字符；凭据规则先于手机号/长编号执行，避免占位符截断后留尾；英文「is」仅在值含数字或符号时遮，`needed/changed/required` 加句末标点保留；通用中文「密码/口令」并入共用规则（客诉边界同样生效），删除只在转交路径的 `_GENERIC_SECRET` |
| `logging.py` | R2、F3-S2 | `_SECRET_ASSIGNMENT_PATTERN` 键允许 JSON/repr 引号，引号值按「反斜线 + 下一字符」成组、只由未转义引号结束；mapping/list 消息走结构递归，不先渲染 |
| `services/item_coverage.py` | R5、R3 | `clause_rooms` 先屏蔽绝对日期与区间再识别房号；新增 `static_clause_dates` 按端点与角色绑定静态小句（范围内单日只核当天；范围外、倒置、两段区间、相对夹显式 → 未绑定） |
| `integrations/deepseek_client.py` | R3、R4、F3-S3、F3-V2-R1 | `_ItemLayout.static_dates/split_static` 在模型调用前确定；`_item_dates` 无项日期时取摘录自身日期；任一小句未绑定则不整项取证，已绑定小句按「小句原文/剩余语义 + 绑定日期」取证；`_gap_unit_offers`、缺口单元、`ReplyPart.gap_unit` 共用同一区间编号；`_shared_grounded` 按接收项绑定日期复用；`_fact_layout` 接收选择日期 |
| `routes/knowledge.py` | R6、F3-S4、R8 | 整理、入库、关闭改为状态（+代次）条件 UPDATE，`rowcount≠1` 拒绝并整体回滚；空来源仍直接「没找到答案」、零作业；入队用新代次；`toggle_knowledge` 拒绝时挂经 `safe_return_path` 校验的 `return_to` |
| `repositories/knowledge_referrals.py`、`routes/knowledge_referrals.py`、`templates/knowledge/referrals.html`、`application.py` | R7 | 开放状态优先 + 创建时间/编号稳定倒序 + offset 分页（每页 50）；`count_open` 改 SQL COUNT；`SessionKnowledgeAdminService.list_referrals` 仅转发分页参数 |
| `tests/integration/test_reply_fix3_boundaries.py`（新）及既有测试补充 | 全部 | 最终发件箱正文与路由、证据日期、缺口原因；真实草稿 SDK 载荷；真实日志 handler；SQLite 交错与 PostgreSQL 并发入库；浏览器分页与回跳 |

## 3. 独立复核发现

**未发现新的阻断问题。** 核对过的关键点：

1. 没点日期的普通静态问题：`static_clause_dates` 返回原日期，`split_static=False`，取证路径与 rc3 相同，不改变常规回复。
2. 条件更新的胜负：清空会换代并改为已清空，因此整理（状态+代次）、入库（已整理+代次）、关闭（开放状态）在清空提交后都会 `rowcount=0` 而被拒，且异常路径统一回滚，不留孤立知识或无作业的整理中记录。
3. 凭据误遮：`No password is needed.`、`The wifi password is changed daily` 不再被遮；「密码是多少」「如何获取 Wi-Fi 密码」不受影响。

**既有边界（非本轮引入，rc3 同样，记录不修）：** 英文标签后用空格分隔时（`Wi-Fi password please`、`password reset`），后面 4 位以上的词会被当成凭据遮盖。只影响可读性，不会漏遮。

**已接受的上限（Spec 已写明）：**
- D-H3：用「is」连接的纯字母英文密码（`password is sunshine`）不识别。
- D-H1：小句日期在该项检索范围外（如住一晚、问退房日早餐）回未确认，不转交。
- 模型标注否决只在主题可识别、属性为收费/钟点时生效；一项里同时问两个对象且两条都被选为回答时会保守回未确认。
- 后台 offset 分页在并发新增/状态变化时不提供冻结快照。

## 4. 真实模型门禁

| 候选 | 结果 |
| --- | --- |
| rc-1.70.0-1 | 不通过（6 个退步）→ 缺陷修复 + 用户决定（模型标注否决） |
| rc-1.70.0-2 | 不通过（2 个退步）→ 服务收费追问修正 + 用户决定（同轮已核验条目复用） |
| rc-1.70.0-3 | 通过（首轮 138/154），后被 fix3 改动失效 |
| **rc-1.70.0-4（`92fb52c`）** | **通过**：154 个场景，首轮 135 通过；退步无；新纳入 E-地铁；安全类未通过 0；运行出错 0 |

- 规划 142 次：P50 1018 ms、P95 1460 ms、最大 2454 ms，规划失败 3 次；首轮单场景 P50 3.2 秒、P95 5.0 秒（比 rc3 慢约 0.3–0.8 秒，未单独归因，同时段模型侧延迟可能波动）。
- 首轮比 rc3 少3个：K-厨房-C、MT-延迟周六、SR-遗失、SC-401有上下文由通过变失败，U-充电桩由失败变通过。前两项属必过，补跑均为失败/通过/通过；后两项属已知未通过，故无当前基线退步。SR-遗失在rc3提议晋升、本轮仅一次失败，不能据此称已稳定失败。
- 提议新基线：`.stage/reply-gate-rc-1.70.0-4.baseline.json`，必过 137 / 已知未通过 17，按惯例发布后随现场记录提交。
- 门禁只在生产 API 容器临时目录只读运行，结束即清理；门禁运行器不经会话层，不覆盖转交说明、卡片、收据与实际收件。
- 该结果只覆盖rc4候选；2026-10-13终审日期补修改变公共取证路径后不可复用，发布前须新的all门禁证据。

## 5. 修复前源码摘要（2026-10-12，rc-1.70.0-4 = 当时工作区）

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

`src/`、`tests/`、`scripts/`、`migrations/`、`pyproject.toml`、`requirements.lock` 与 rc-1.70.0-4 逐文件 0 差异；之后只改了文档与 AOCI 托管资产。

## 6. 复验入口

```sh
PYTHONPATH=src:.:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/pytest -q
PYTHONPATH=src:.:tests .venv/bin/pytest -q tests/integration/test_reply_fix3_boundaries.py tests/unit/test_sensitive_text.py tests/unit/test_log_redaction.py tests/integration/test_knowledge_referrals.py tests/unit/test_item_coverage.py tests/unit/test_reply_fix2_items.py
.venv/bin/ruff check . && .venv/bin/mypy
```

上一轮与 Spec 复审的反例脚本（`docs/reviews/2026-10-08-fix2-implementation-review-evidence/`、`docs/reviews/2026-10-09-fix3-spec-review-evidence/`）在修复前用于证明缺陷存在；对应正确行为已转为上述回归测试。

## 7. 未覆盖与发布前待办

1. 提交、推送、CI、部署：等待本次终审与用户授权。部署含迁移 `0035_knowledge_referrals`，先做完整备份。
2. 测试号真实收发：本版改变客人收件内容（转交说明）与员工卡片（资料核实卡），按项目规则发布前需要，需当次授权。
3. 生产 API 容器日志无滚动上限（2026-10-08 只读核对：`json-file` 无 `max-size`，日志 96K，根分区剩余 37G）；1.70.0 打开应用 INFO 日志后日志量会上升，是否在 `compose.yaml` 加滚动配置待用户决定。
4. 独立改写集（D1）仍未交付；154 场景门禁不证明任意客人改写都能识别。

## 8. 请重点审查

1. §3 的「未发现新阻断」是否成立，尤其是 `static_clause_dates` 与 `_item_dates` 的组合：多日期粗粒度项、退房日单日、相对日期等边界。
2. `sensitive_text` 把通用中文「密码/口令」并入客诉共用规则，是否有客诉场景误遮（如「密码 OK 吗」会遮「OK」）需要另行处理。
3. 首轮通过数 138 → 135 的波动是否需要在发布前再跑一轮确认，还是按「无退步」规则直接接受。
4. 是否同意 rc-1.70.0-4 结果在正式提交回复代码一致时复用于部署。
