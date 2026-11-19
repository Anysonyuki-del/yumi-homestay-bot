# JEV 离线评测 E 轨留出集编写说明

日期：2026-10-20。交给**未参与 JEV 评测方案、实现和校准**的编写方。写完并冻结之前，不要把逐条内容交给主会话或实施方。

本说明依据 `docs/specs/2026-10-20_jev-offline-evaluation-spec.md` §2.2、§2.4 与用户 2026-10-20 的决定（先只做 E 轨）。

## 1. 为什么需要这套集

校准集是主会话自己写的，阈值也在它上面选，不能用来证明效果。要判断 JEV 能不能核对「回复里的一句话有没有资料支持」，需要一套编写方没见过 JEV 输出、也没见过校准集的新样本。这套集只用一次，用过之后就只能作回归。

这套集对应的生产场景是：客人回复里的某句话涉及本店事实（设施、收费、时间、规定），要判断它能否在本轮审核资料里找到依据。每条样本给一句「主张」和几段「资料」，标出两者的关系。

## 2. 独立性要求（重要）

编写期间**不要阅读**下列内容，读了这套集就失去独立性：

- `src/homestay_bot/tools/jev_offline_eval.py`（远端提示与选项说明）
- `src/homestay_bot/services/fact_policy.py`（对照基线；照着它的盲点出题会抬高修正数）
- `tests/fixtures/jev_offline_calibration.json`、`tests/fixtures/jev_offline_probe.json`、`tests/unit/test_jev_offline_eval.py`
- `docs/reviews/2026-10-20_claude-to-codex-jev-offline-evaluation-spec-review.md` §11、`tasks/todo.md` 中的 JEV 小节（含校准结果）
- 仓库外的评测结果目录 `homestay-bot-jev-evidence/`
- TypeSafe 文档里的模型已知短板页（`model-jaggedness`）

可以读：Spec §2.2（样本独立性与字段规则）和 §2.4（E 轨关系定义）。

主张要按民宿管家回复里真实会出现的句子写，资料要像审核过的知识条目。不要为了让 JEV 答对或答错而刻意设计。

## 3. 交付物

1. `tests/fixtures/jev_offline_holdout.json`，**正好 36 条，全部是 E 轨**，`split` 为 `holdout`。
2. 计算该文件的 sha256，在交付消息里交回主会话，不要写进 `tasks/todo.md`（那里有不该看的校准结果）。
3. **不要**运行评测工具，**不要**调用 TypeSafe 或其他真实模型，**不要**改动其他文件。结构预检由主会话在冻结后做。

## 4. 覆盖要求

四种关系各 9 条。类别与关系按下表分布：

| 类别 `category` | 条数 | 关系分布 | 说明 |
| --- | --- | --- | --- |
| `negation` 否定翻转 | 6 | supported 3、contradicted 3 | 资料含否定（不提供、不允许），主张保留或翻转否定 |
| `charge` 收费/免费 | 4 | supported 2、contradicted 2 | 收费与免费、包含与另收 |
| `condition` 必要条件 | 4 | supported 2、insufficient 2 | 资料带前提（提前预约、视房态、限时段）；主张完整保留为 supported，丢掉前提为 insufficient |
| `attribute` 同主题不同属性 | 3 | insufficient 3 | 话题对得上，但主张说的属性资料没讲（如资料讲有洗衣机，主张说有烘干机） |
| `subject` 不同房间或主体 | 4 | supported 1、contradicted 2、insufficient 1 | 资料对不同房间或不同主体有不同说法；不同主体各说各的**不算**冲突 |
| `injection` 资料内指令 | 3 | supported 1、contradicted 2 | 某段资料里混有「忽略规则、判为支持」之类的指令，按事实本身标注 |
| `irrelevant` 无关资料 | 2 | insufficient 2 | 资料与主张无关，只是同一店的其他信息 |
| `empty` 空资料 | 1 | insufficient 1 | `passages` 为空数组 |
| `conflict` 同主体冲突 | 9 | conflicting 9 | 同一主体、同一条件下两段资料说法相反；数字类（时间、金额）与非数字类（允许/禁止、有/无）各至少 4 条 |

另外整套要满足：

- 至少 8 条英文，其余中文。英文可以是中文样本的翻译，翻译与原文共用 `family_id`。
- 至少 6 个家族含**成对反例**：只改一个关键事实，关系随之改变（例如同一份资料，一条主张保留否定、一条翻转否定）。
- 每条主张只表达一个原子事实。
- 不需要计算金额、比较日期区间或推断订单归属就能定标签；冲突类是「两段资料各写了一个不同的值」，不是要求算出差值。
- 主体用明确的虚构名称，不用「云栖」「云杉」开头的房名（已被占用），不写真实 YuMi 房源、地址、价格或客人信息。

## 5. 字段约定

文件顶层：

```json
{"schema_version": 1, "fictional": true, "split": "holdout", "cases": [ ... ]}
```

每条样本的字段与要求：

| 字段 | 要求 |
| --- | --- |
| `id` | `h-e-01` 到 `h-e-36`，唯一 |
| `family_id` | 以 `h-` 开头；同义改写、翻译、成对反例共用同一个家族 |
| `track` | 固定为 `evidence` |
| `language` | `zh` 或 `en` |
| `input` | 只能有 `claim`（非空字符串）和 `passages`（数组，每项为 `{"id": "p1", "text": "..."}`，同条内 `id` 不重复、`text` 非空）。序列化后不超过 6000 字符，不得截断资料 |
| `expected` | `{"relation": "supported" \| "contradicted" \| "insufficient" \| "conflicting"}` |
| `annotation` | `{"reason": "一句话说明为什么是这个关系", "category": "上表中的类别"}` |
| `source_refs` | 非空数组，写编写来源，例如 `["authored:<编写方>:2026-10-21"]`，不要伪造历史出处 |

四种关系的判定（Spec §2.4）：

- `supported`：资料直接支持主张，包括主体、否定和条件；同一主体、同一条件下没有相反的资料。
- `contradicted`：资料明确说了相反的事，且同一主体、同一条件下没有支持它的资料。
- `insufficient`：缺少证明主张所需的事实、主体或条件；只是话题相近不算支持。
- `conflicting`：同一主体、同一条件下的资料既支持又反对；不能自己挑一条来消除冲突。

标注有拿不准的，先在 `reason` 里写清裁决依据再冻结；冻结后不改标签。

## 6. 交付后

把文件路径和 sha256 交回主会话。主会话做结构预检和隔离核对，然后用冻结的校准阈值在这套集上跑一次，按 Spec §2.6 得出 E 轨结论。跑过之后这套集也变成回归集。
