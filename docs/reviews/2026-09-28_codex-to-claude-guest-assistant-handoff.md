# Codex → Claude：民宿助手回复边界交接

日期：2026-09-28。本文供 Claude 接手当前工作区使用；记录**当前可核实的工作树和本地验证**，不代表代码已提交或生产已验收。

## 1. 当前状态与结论

- 工作目录：项目主工作区；分支 `codex/guest-assistant-boundaries`，HEAD 仍是 `1300b13`。本地 `main` 和 `origin/main` 当前都是 `7ca055e`（1.41.0）。
- 已在**工作文件层面**将 main 的发布行为与本分支回复边界实现做三方整合，解决冲突及静默不兼容；**没有执行 Git merge/rebase，也没有形成整合提交**。当前有大量已修改和未跟踪文件，不能把 `git diff --stat` 当成完整变更清单，更不能把 main 导入的内容全记为 Codex 原创。
- P1–P8 和 P9–P13 的本地实现、离线测试、隔离 PostgreSQL、合成页面验收以及真实 DeepSeek＋虚构房源/知识的回归已做。P14 是 main 已发布的店外状态约束，在本工作树中保留并与新回复路径衔接。
- 本轮真实模型门禁按“原必过不退步”规则通过，**153 个场景并非全部通过**。还有 53 个历史未通过项，其中有真实回复缺口。尚未提交、推送、部署、写生产业务数据或完成企业微信客人实际收件验收。

## 2. 决策依据

| 决策 | 来源与可核对依据 |
| --- | --- |
| 民宿房源、入住、天气、玩乐等问题：本店事实取已核实房源、知识及实时工具；知识库外的明日天气等查询有限回答，不编造未知事实 | 用户在本任务中的要求；`docs/specs/2026-09-25_guest-assistant-boundaries-spec.md` 的回复流程、证据和验收矩阵 |
| 入住前先由客人确认本次住宿，再按确认的房间和入住时间回答；订单关联或状态变化后不能继续使用旧确认 | 用户明确选择“客人确认”；原 Spec §4.4，`tests/integration/test_stay_confirmation.py` |
| 客人申请服务时，任务登记成功之后才能说已转交或已安排；价格让步、退款等经营决定交人工 | 原 Spec §6；main 的讲价接管规则；`tests/integration/test_guest_reply_contract.py` 与 `tests/unit/test_answer_policy.py` |
| P9 人工交还、P10 危险分级、P11 会话语言、P12 核对资料、P13 通知字段 | `docs/specs/2026-09-25_guest-assistant-boundaries-p9-p14.md` §1–5；用户要求继续完成 |
| “过时保护保持，安全与承诺类回复除外” | 补充 Spec §6；main 的 1.39.16 `stale_exempt` 发布行为。普通答复仍受过时保护；模型自己声称“已安排”不能获得豁免 |
| P12 改用新版留存口径：任务内明文保存，不按 14 天清除；模型上下文与摘要脱敏，通知不带核对原值 | 用户“统一为新版”；补充 Spec §3.4/§6.1；`docs/specs/2026-09-26_guest-data-retention-spec.md` 与 main 1.41.0 |
| 知识迁移时，已启用的本店条目默认 `global`、标题或类别明确周边者默认 `public`，停用者 `unreviewed`，不猜测所属房源 | 用户在 `docs/reviews/2026-09-26_codex-boundaries-rebase-handoff.md` §0 已选择的方案；隔离库合成资料验证 |

历史 Spec 用于说明意图；**代码现状以本工作树中的符号、测试和迁移为准**。旧交接说明 §5 写的“P9–P13 尚未实施”是 2026-09-26 的快照，已被其 §8–9 和本文更新。

## 3. 当前工作树改了什么

下表按行为归组。表中 `services/`、`repositories/`、`integrations/`、`routes/`、`templates/`、`domain/` 和 `tools/` 均省略公共前缀 `src/homestay_bot/`；涉及 main 1.39.16–1.41.0 的内容属于**导入并适配**，不是在本分支新设计一套平行实现。

| 行为 / 改动 | 主要代码与符号 | 为什么改 |
| --- | --- | --- |
| 每个回答分项绑定知识、房源、价格或公开查询证据，再组合客人可见回复；未知、冲突和查询失败按分项处理 | `src/homestay_bot/services/reply_plan.py::ReplyEvidence/ReplyPart/prepare_planned_reply`；`services/conversation_service.py::_process_model_reply_body/_send_guest_reply_parts`；`services/guest_reply_policy.py::prepare_guest_reply` | 原有多条回复规则会互相覆盖，容易把其他房间事实、未经执行的动作或无来源状态写进最终回复；对应原 Spec §4、§9 |
| 知识检索加范围、有效期、问句属性覆盖和冲突判断；管理页可编辑范围及日期 | `services/knowledge_service.py::retrieve_detailed`；`services/knowledge_evidence_policy.py::build_evidence_plan`；`repositories/knowledge.py`；`routes/knowledge.py`；`templates/knowledge/{index,detail,scope_fields}.html` | 本店知识要可回答，同时防跨房源、过期条目和主题相似但属性不同的误答；实测又暴露了正常问法被误拒的缺口 |
| 适配 main 的 Hostex 房间级参考价结果，按所问房间给出每晚参考价；最低参考价只比较可住且夜晚报价完整的返回房源 | `integrations/deepseek_client.py::_tool_reply_parts/respond`；`tests/unit/test_live_price_scope.py` | main 已改变价格工具结构，旧分支读旧字段会静默失败；虚构价格的两版三轮实测显示单房与英文问价改善。参考价不能证明可订或最终成交价 |
| 模型决策字段、模型与本地校验、店外实时状态及公开搜索结果约束统一；模型收到会话语言要求；实际门锁密码走本地拒绝及核验指引 | `integrations/deepseek_client.py::assistant_decision_schema/_validate_decision/respond`；`services/guest_reply_policy.py`；`services/answer_policy.py` | 真实模型实测暴露 JSON 必填不一致、英文问价、公开搜索为本店行动背书、敏感请求误答等问题；同时保留 main P14 和“讲价转人工” |
| 先核实归属，再向客人展示可确认的本次住宿；确认快照随订单变化失效，回复按确认的房间/日期取证 | `repositories/conversations.py::prepare_stay_confirmation/confirm_stay/get_confirmed_stay`；`services/conversation_service.py`；`migrations/versions/0030_stay_confirmation.py` | 用户明确的“客人确认”边界；避免根据姓名、后四位或旧订单直接透露房间和日期 |
| 服务申请先登记任务再产生承诺；高风险接管、危险二级提醒、语言切换及员工通知内容集中在实际会话出口 | `services/conversation_service.py::_record_task_suggestion/_escalate_emergency`；`services/emergency_service.py::EmergencyService.classify`；`repositories/conversations.py::detect_language`；`services/answer_policy.py::handoff_reason` | 原 Spec §6 与补充 Spec P10/P11/P13；通知含中文原因、客人称呼、已确认房间/日期、原话、已回复正文和后台链接，并受字节限制 |
| 人工接待可在后台手动交还；低风险接管空闲满 30 分钟后由既有巡检尝试自动交还，高风险只手动；并发时复核最新接管和员工消息 | `repositories/operations.py::list_idle_human_conversations/release_conversation`；`services/task_lifecycle_service.py::sweep`；`services/customer_admin_service.py::release_conversation`；`routes/customers.py`；`templates/customers/detail.html` | 补充 Spec P9；避免暂时转人工后永久停在人工，也避免巡检覆盖员工的新接管 |
| 转交时可收订单号，或姓名＋手机号后四位；明文留在 `BusinessTask` 核对字段，模型及摘要只看替代文本，员工通知只给后台链接；管家确认前不把资料视为订单归属 | `services/guest_verification.py::GuestVerificationService.handle/_save_details`；`services/message_service.py::model_message_content`；`domain/models.py::BusinessTask`；`migrations/versions/0031_guest_verification.py`；`templates/tasks/detail.html` | 补充 Spec P12 的新版留存决定；与 main 1.41.0 的明文数据政策一致，同时保留模型与客人答复边界 |
| 知识与 guest-data 迁移链顺接 main，并提供只读复核清单 | `migrations/versions/0028_guest_plaintext.py`（main 导入）→ `0029_knowledge_scope.py` → `0030_stay_confirmation.py` → `0031_guest_verification.py`；`tools/knowledge_scope_report.py::scope_report` | main 已占用 0028，必须顺延；存量资料按已决定默认范围迁移，管理员上线前仍须逐项复核实际知识 |
| 用虚构房源/知识覆盖静态、实时、混合、安全及业务流程，并建立不退步的真实模型门禁 | `tools/reply_regression.py::_gate`；`tests/fixtures/guest_reply_scenarios.json`、`guest_reply_regression_baseline.json`；`tests/integration/test_guest_reply_contract.py` 等 | 复现了改一处、其他问法退化的问题；门禁同时检查正文与实际路由，但不能取代真实消息投递和人工语义判断 |

其余改动中还有 main 导入的分阶段计时、紧急后续回复、数据留存与发布记录，以及相应测试、CI 和格式差异。审阅合并时请按 `main 7ca055e`、当前 HEAD 和当前工作树做三方核对，**不要把整个工作树视为一笔纯 Codex 提交**。

## 4. 已做的验证与证据边界

| 层次 | 已核对结果 | 不能由此证明 |
| --- | --- | --- |
| 最新本地离线测试 | `RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q --tb=short`：**2207 passed、27 skipped、11 warnings**；Ruff、mypy（130 个源文件，最后客户端修改再做单文件检查）及 `git diff --check` 通过。记录见 `tasks/todo.md` 顶部及本机临时日志 | 27 跳过含外部契约和本轮未重启的隔离 PostgreSQL；不代表生产运行 |
| 隔离 PostgreSQL 16.15 | 此前在临时本地库验证空库至 0031、合成数据 `0028→0031→0028→0031`、默认范围/正文保留及 3 项事务并发集成测试；含 PG 的当轮全量为 **2168 passed、15 skipped、21 warnings**。其后实测修复未改持久化逻辑 | 没有迁移生产库，也没有复核真实知识条目 |
| 合成浏览器 | Chromium 390px 检查知识范围/日期编辑与回显、客户详情手动交还；记录在本机临时日志 | 不是已登录生产页面验收 |
| 真实 DeepSeek＋虚构资料 | `.stage/guest-live-20260928-005640/report.md`：153 场景首轮 100 通过；原 88 个必过无退步，新增 12 个三轮稳定通过；53 个历史未通过仍保留。源码哈希与实测快照一致，未出现最终调用错误 | `.stage` 是本机忽略目录；Hostex 是替身、知识是内存仓储，回归器近似会话路由；不经过真实 outbox/企业微信和订单核验 |
| 固定两版问价比较 | main `7ca055e` 与当前工作树各 8 场景×3 轮，价格夹具 SHA256 同为 `89a725b9f2b3a2dd7eef246dfd11a7a74ac9525c806d67d577fce9703bcc11be`。新版 6/8 场景稳定自动通过，main 4/8；`PR-201`、`PR-EN` 从 0/3 到 3/3，保留新版来源绑定路径 | `PR-无日期` 两版都正确追问日期却被旧判定要求调用价格工具；`PR-讲价` 新版正确转人工却被旧判定要求知识路由。原失败记录未改成通过 |

53 个历史失败中，`PI-折扣` 仍值得优先处理：未答应五折，但没有进入期望的人工路由，回复中的“帮您核对”缺乏已执行动作证据；另有若干知识召回、误判危险和路由缺口。详见本机 `.stage/guest-live-20260928-005640/report.md` 的完整清单；**门禁通过不等于这些问题已经修复**。回归器正式基线没有用本轮“提议基线”覆盖。

## 5. Claude 接手时建议先做

1. 先确认 Git 现状并保护当前未提交/未跟踪文件及用户其他改动，尤其不得读取或暂存受保护的未跟踪 `YuMi民宿AI项目总结.txt`。阅读原 Spec、P9–P14 补充、旧交接 §0–4、本文及当前代码；旧交接 §5 的未完成状态已过时。
2. 对照 `main 7ca055e` 真正整理 Git 祖先关系与当前工作文件，逐项复核 price 结构、P14、`stale_exempt` 例外、分阶段计时、P12 明文留存及迁移 0028–0031；这一步尚未被文件级整合替代。
3. 在隔离数据下优先复现并修 `PI-折扣` 等真实缺口，明确区分旧判定误报；新增有回归价值的最小用例，再跑受影响测试和真实模型门禁。不要靠降低基线、放宽禁用片段或补假房源事实让数字变好。
4. 上线前由管理员用 `src/homestay_bot/tools/knowledge_scope_report.py` 的只读入口复核**实际**知识范围、适用房源和有效期；真实房态/价格只能信 Hostex，不能把虚构测试资料导入真实知识库。
5. 如后续获得新的明确授权，再分别安排提交/推送、部署与生产迁移、真实 Hostex/企业微信测试账号验收。生产要分别记录源码、部署副本、容器、数据库、登录页面和**客人实际收件正文/时间**；接口受理或健康检查不能替代收件证据。

当前这份交接仅写入文档，没有进行外部发送或生产操作。
