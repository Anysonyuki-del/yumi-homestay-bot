# 第四套独立留出集（holdout_v4）编写说明

日期：2026-09-23。给**未参与本轮方案与实现**的会话。写完冻结前，不把逐条内容交给实施方。

## 1. 为什么需要这套集

前三套评估集都已经用于修复，只能作回归。本轮刚改完知识证据门（问题主题 + 所问属性的核对），需要一套没被用于调参的新集，才能说明泛化效果。

## 2. 独立性要求（重要）

编写期间**不要阅读**下列内容，读了这套集就失去独立性：

- `src/homestay_bot/services/knowledge_evidence_policy.py`
- `docs/specs/2026-09-22_knowledge-evidence-gate-spec.md` 的 §4（主题与属性规则）与 §11（实施记录）
- `tests/unit/test_knowledge_evidence_gate.py`

可以读：`tests/fixtures/knowledge_retrieval_holdout_v3.json`（**只看字段结构，不要照抄题目**）、`tests/unit/test_knowledge_retrieval_eval.py` 里加载与校验用例的部分。

题目要按真实客人会怎么问来写，不要照着现有实现的词表去凑。

## 3. 交付物

1. `tests/fixtures/knowledge_retrieval_holdout_v4.json`，**正好 40 条**，`split` 字段为 `holdout_v4`。
2. 在 `tests/unit/test_knowledge_retrieval_eval.py` 的 `CASE_FILES` 里登记 `holdout_v4`，并确认 `--only holdout_v4` 能跑出报告（脚本对每个划分有条数断言，注意对齐）。
3. 记录文件的 sha256，写进 `tasks/todo.md`。
4. **不要**跑 `--semantic`（需要真实调用授权），**不要**改动其他评估集与基线文件。

## 4. 覆盖要求

40 条按下面分布，每条自带一个小知识库（3 条左右），合成数据，不写真实民宿事实、真实地址或客人信息：

| 类别 | 条数 | 说明 |
| --- | --- | --- |
| 属性错配 | 8 | 主题对得上、问的那个属性答案没讲。例如问能不能做某种特殊餐食，知识只写了送餐时间；问某设施收费，知识只写了开放时间。这是本轮重点 |
| 同义换说法 | 8 | 口语、别称、绕着说，不出现知识条目里的原词 |
| 中英文 | 6 | 英文问法；其中 2 条的知识只有中文答案，英文字段留空 |
| 无答案 | 6 | 知识库里确实没有答案，且要放语义相近的干扰条目 |
| 隔离 | 4 | 停用条目、旧版本答案、候选草稿、周边商户信息不得进入证据 |
| 多主题 | 4 | 一句话问两件事；其中 2 条只有一个主题有证据 |
| 边界 | 4 | 实时房价、房态、退款等必须走实时查询，不能由静态知识或模型回答 |

另外整套里至少包含：

- 6 条带 `mutations`（新建、修改、停用、启用各至少一次），验证停用立即生效、旧答案不作证。
- 8 条带 `stub_reply`：其中 4 条 `stub_reply_supported` 为 false（模型说了知识里没有的事实，例如错误的时间、金额、能力），4 条为 true。
- 2 条长答案（答案超过 1000 字符，且尾部带条件或例外），检验条件不被截断。

## 5. 字段约定

照 `holdout_v3` 的结构写。逐条需要给出：

- `case_id`（`h4-xxx`）、`group`（synonym / cross_language / long_answer / no_answer / isolation / multi_topic / boundary）、`language`、`question`、`tags`
- `knowledge`：每条含 `id`、`category`、`question_zh`、`answer_zh`、`question_en`、`answer_en`、`keywords`、`is_enabled`
- `expected_source_ids`（可回答时的正确来源）、`required_source_ids`（多主题时全部必需来源）、`key_facts`（必须原文出现在证据里的关键事实）、`forbidden_facts`（绝不能进入证据的内容）
- `expect_grounded`：客人是否应当得到本店事实答案
- `property_specific`：是否属于本店专属问题
- `realtime`：是否必须走实时查询
- `stub_reply` / `stub_reply_supported`：可选
- `notes`：一句话写明这条要考什么

## 6. 交付后

把文件和 sha256 交回主会话。之后由主会话在冻结版本上跑一次纯关键词评估，比较安全项与回答率；门槛见 Spec §9。跑过之后这套集也变成回归集。
