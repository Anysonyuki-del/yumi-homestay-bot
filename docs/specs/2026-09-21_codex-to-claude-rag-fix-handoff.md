# Codex → Claude：数据库优化与 RAG 补修交接

日期：2026-09-21。交出方：Codex；接手方：Claude。

## 1. 接手结论

本轮已修复三个可复现的 RAG 证据来源缺陷：问题标题作证、否定免费支持肯定免费、客人问句中的数字作证。相关本地验证完成，公开留出集错误放行从 2 条降至 0 条。

**这不等于 A/C 阶段全部验收通过。** 上下文隔离仍有 2 条失败，公开留出集仍有保守漏判，PostgreSQL、真实模型与生产均未验收。当前不能直接以“测试全绿”或“具备发布条件”交付。

## 2. 工作区与依据

- 当前 HEAD：`4e348ce8253c2bcbb8b208e0e86c727674900eb2`，交接时已重新核对。
- 所有本阶段改动仍在同一工作区，未提交、未推送、未部署；包含你原有的数据库、检索、CI 和测试改动。
- 本轮未改数据库实现，没有迁移或依赖变更。不要重置或覆盖工作区，也不要把整个 `git diff` 都归为 Codex 的修改。
- 实施依据：[数据库优化 Spec](2026-09-21_database-optimization-spec.md) R4 §9.4.1。
- 历史交付与补修记录：[原交接文档](2026-09-21_database-optimization-handoff.md)。其中 §1～11 是原交付记录，§12 是本轮补修；本报告作为接手入口。
- 进度记录：`tasks/todo.md` 末尾“Codex 交接审查补修：C1 证据来源”。

## 3. 本轮实际改动

### 3.1 共享证据校验

文件：`src/homestay_bot/integrations/deepseek_client.py`。

| 符号 | 改动与约束 |
| --- | --- |
| `DeepSeekGuestAssistant._supporting_knowledge` | 不再把问题标题当作事实段落，只从答案识别事实；标题仍用于范围判断。客人问本店时，周边标题下的问答不能为本店设施作证。 |
| `_ANSWER_TOPIC_PATTERNS` | 补足入住退房、加床、距离的答案陈述式表达，避免移除标题证据后误伤原有正确回答；未扩展问题分类器。 |
| `_LOCAL_POLICY_PATTERN` 及答案分句逻辑 | 保留“自 9 月起暂停提供早餐，楼下有早餐店”等明确本店政策前句，不把周边商户后续描述拆出来当作本店事实。 |
| `DeepSeekGuestAssistant._has_affirmative_free_claim` | 新增有限的否定识别：“不免费”等不能作为肯定免费的支持证据。 |
| `DeepSeekGuestAssistant._has_unsupported_property_claims` | 免费与数字证据仅取支持条目的答案，排除标题和客人问句里的猜测。 |

未改真实工具结果的既有处理，也未引入向量库、embedding、外部服务或新的语义校验框架。

### 3.2 回归测试与报告

- `tests/unit/test_deepseek_client.py::test_property_evidence_requires_reviewed_answer_facts`：6 个错误回复场景，走实际 `respond` 和模型 stub。覆盖标题单独作证、周边范围、否定免费、标题免费、问句数字、标题数字。
- 同文件 `test_reviewed_answer_facts_remain_usable`：6 个正常对照，覆盖准确否定、已审核免费、已审核金额、入住陈述句、本店政策与周边混合答案、真正的周边问题。
- `tests/unit/test_knowledge_retrieval_eval.py::test_retrieval_meets_preregistered_targets`：只更新 strict xfail 原因，保留门槛和失败状态。
- Spec §9.4.1、原交接 §12、`tasks/todo.md`：补记本轮范围、证据和剩余缺口。

**评估样本、标签、基线均未修改。** 不能通过删除禁止事实、降低门槛或直接移除 xfail 来完成验收。

## 4. 验证证据

以下是本轮实际执行结果；不是对生产状态的推断。

| 检查 | 结果 |
| --- | --- |
| 修复前缺陷回归 | 6 个错误回复场景全部失败，证明原链路存在缺陷 |
| 最终受影响验证集 | **248 passed，1 xfailed**，1 个 Starlette 弃用警告 |
| 改动 Python 文件 Ruff | 通过 |
| mypy | 133 个源码文件通过 |
| `git diff --check` | 通过 |
| PostgreSQL | 本轮执行 0 项，未验收 |
| 真实模型、生产、外部消息 | 未执行、未验收 |

| 评估指标 | 校准集 40 条 | 公开留出回归集 40 条 |
| --- | --- | --- |
| Recall@3 | 1.000 | 0.935 |
| 证据门判断准确率 | 0.925 | 0.600 |
| 错误放行 | 0 | **2 → 0** |
| 模拟回复判断准确率 | 1.000 | 0.833 |
| 隔离通过率 | 1.000 | **0.950，未达标** |

留出集已公开并用于修复后的回归，不能继续称为独立泛化验证；本轮自编的 12 个回归场景也不是独立留出集。原报告的“1685 通过、24 跳过”等全量数字属于原交付证据，本轮未重跑，不得合并成当前全量结果。

复现命令：

```sh
RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python -m pytest -q --tb=short tests/unit/test_deepseek_client.py tests/unit/test_deepseek_complaint.py tests/unit/test_deepseek_tourism.py tests/unit/test_deepseek_faq_drafter.py tests/unit/test_faq_draft_job.py tests/unit/test_knowledge_retrieval_eval.py tests/unit/test_knowledge_service.py tests/unit/test_guest_reply_policy.py tests/unit/test_answer_policy.py tests/integration/test_knowledge_repository.py tests/integration/test_knowledge_routes.py
.venv/bin/python -m ruff check src/homestay_bot/integrations/deepseek_client.py tests/unit/test_deepseek_client.py tests/unit/test_knowledge_retrieval_eval.py
.venv/bin/python -m mypy
PYTHONPATH=src:tests RUN_LIVE_CONTRACT_TESTS=0 .venv/bin/python tests/unit/test_knowledge_retrieval_eval.py --report
git diff --check
```

仅文档交接未重跑业务测试；相关代码、配置、测试或环境发生变化后，再按影响范围刷新证据。不要运行 `--record-baseline` 覆盖原基线。

## 5. 剩余问题与已知上限

1. **上下文隔离未完成。** `hold-iso-35`、`hold-bound-39` 指定的禁止事实仍出现在检索上下文。`_build_context_envelope` 会把检索知识放进 `approved_reference_data`；最终回复被拦下不等于模型未见这些内容。需要先确定周边事实、历史静态价格在不同问题下的适用范围，再设计最小隔离修复，不能仅修改评估口径。
2. **检查仍是词面规则。** `_has_unsupported_property_claims` 按支持条目整体汇总答案，尚未建立“某个金额属于某项服务”的逐事实归属；数字、单位、否定、条件和跨主题蕴含的完整正确性没有保证。时间数字拆分规则保留原实现，不应把数字命中解释成金额或时间含义已核对。
3. **同义表达仍有保守漏判。** 现有别名不能覆盖所有表达或主题，证据门准确率不等于召回率。进一步调整先用校准集，再由非调参者提供新的独立留出集；不要围绕已公开失败样本补词后宣称泛化完成。
4. **PostgreSQL 独立门禁仍缺。** `tests/integration/test_retention_postgresql.py` 的 9 项测试未验收。SQLite 不能证明 VARCHAR 长度约束、行锁或锁竞争行为。按原决策保留“PG 未验收”；安装数据库、推分支跑 CI 均不在本次授权内。
5. **实时路由缺口未修。** 原交接记录的“今晚还有空房吗”识别问题涉及 `answer_policy.is_transaction_sensitive` 与 `DeepSeekGuestAssistant._should_force_availability`，不在本轮三项补修范围，接手后如处理需先复核并单独明确范围。

## 6. 建议 Claude 接手顺序与操作边界

1. 先读项目 `AGENTS.md`、Spec §9.4.1、本报告和当前差异；审查三处补修及其上限，保留已有工作。
2. 下一阶段优先处理剩余上下文隔离：先提交代码证据、最小文件范围、业务适用范围规则和验收案例，更新 Spec。不得把向量召回视为证据隔离的替代。
3. 按项目流程确认下一阶段 Spec 后，再获得明确“开始”实施；本报告是交接材料，不自动授权新一轮业务改动或外部操作。
4. PG 验收路径另行落实。若仍无隔离 PG，则明确交付缺口，不能以跳过代替通过。真实模型与生产验收同样独立安排。
5. C2 未获本轮授权。是否采用 PostgreSQL + pgvector，仍以 Spec §9.5 的收益证据、模型/预算/数据范围决策和 PG 验收为前置条件。

本次用户只要求写交接报告；没有新增提交、推送、部署、生产读写、真实服务调用或安装依赖的授权。不读取、摄入、修改或提交受保护的 `YuMi民宿AI项目总结.txt`。

## 7. 后续（Claude，2026-09-21）

复核本报告 §3.1 时发现 4 个新的错误放行（答案判据与否定窗口过宽），已按 Spec §9.4.2 修复，见原交接文档 §13。本报告 §4 中的「248 passed」等数字是修复前的证据；当前全量结果以 §13.2 为准。
