# 结构化客户记忆自动晋升 Spec

制定日期：2026-09-11。**仅设计，未实施。**

## 一、现状：机制齐全，通道几乎关闭

治理基础设施早已完备，不需要新建：

| 能力 | 现状 |
| --- | --- |
| 状态机 | `CANDIDATE / ACTIVE / DISPUTED / STALE / SUPERSEDED / REJECTED` |
| 版本化 | `version` 字段 + `supersedes_id` 版本链 |
| 生命周期 | `review_at` 复审期、`expires_at` 有效期，按类别设定（偏好 365/730 天、待确认 30/60 天）|
| 审计 | `customer_memory_events` 状态变化时间线 |
| 自动晋升判定 | `_initial_memory_status()` 已存在，`reconcile_legacy_memories` 已在用 |
| 内容防线 | `is_dynamic_memory_text`（订单价格房态不得固化）、`is_instruction_like_memory`（指令注入）|

自动晋升的现行判据（四条同时成立才 ACTIVE）：

    stable_category（PREFERENCE 或 CONFIRMED_FACT）
    and grounded（原文可验证、摘录能在来源消息中对上）
    and can_auto_activate_subject(subject_key)
    and evidence is not MODEL_INFERENCE
    and confidence >= 0.8

**问题出在第三条。** `_CONTROLLED_SUBJECTS` 是写死的七个主题：

    pet_dog_name, pet_cat_name, floor_preference, quiet_preference,
    bed_preference, communication_preference, dietary_preference

模型生成的 `subject_key` 是自由文本，落在这七个之外的一律停在 CANDIDATE，无论证据
多硬、置信多高。2026-09-11 生产实测生成的四条候选（`arrival_time_preference`、
`service_preference_human`、`complaint_cleanliness`、`safety_gas_smell`）没有一条在
白名单内。

**第二个瓶颈**：那四条的 `evidence_type` 全部是 `MODEL_INFERENCE`，而它们分别来自
客人亲口说的「我明天下午三点左右到」「转人工」「房间卫生太差」「房间里有煤气味」。
`_grounded_evidence` 只在模型自报 `USER_EXPLICIT` 且来源消息为客人消息时才承认该
等级；提示词写的是「无法证明为客户明示或员工确认时 evidence_type 必须为
model_inference」，只说了何时降级，没说何时该报 `USER_EXPLICIT`，模型于是一律取
最保守值。两个瓶颈叠加，自动通道实际上从未打开过。

## 二、改动一：主题白名单改为类别判定

删除 `_CONTROLLED_SUBJECTS` 白名单，改由**类别 + 内容防线**把关。理由：白名单要穷举
模型可能生成的主题，与「机制在链路上、判据形态跟不上真实输出」是同一类问题，注定
追不上；而 `stable_category`、`grounded`、`evidence`、`confidence` 四条判据本身已经
相当严格。

保留 `_SUBJECT_QUERY_TERMS`：它服务于召回（按客人问题匹配主题），与晋升无关。

## 三、改动二：提示词说明何时可报 USER_EXPLICIT

在既有降级说明之外，补上正面条件：来源是客人消息且候选事实由该消息原文直接支持时，
`evidence_type` 应为 `user_explicit`。判定权仍在服务端——`_grounded_evidence` 会用
消息来源二次校验，模型报高了也会被降级，因此这一步不会削弱防线。

## 四、「重大事件」如何界定

用户要求「除重大事件外不需人工」。以下继续强制人工，且都由现有字段直接判定：

| 情形 | 判据 | 理由 |
| --- | --- | --- |
| 待确认事项 | `category == UNRESOLVED` | 本质是还没定的事，自动激活等于把未确认当事实 |
| 纠正既有记忆 | `is_correction` 为真，或同主题已有 ACTIVE 记忆 | 覆盖既有事实，风险高于新增 |
| 模型推断 | `evidence == MODEL_INFERENCE` | 无原文支撑 |
| 低置信 | `confidence < 0.8` | 维持现行阈值 |
| 服务历史 | `category == SERVICE_HISTORY` | 已在 stable_category 之外，维持现状 |

投诉与安全类内容天然落进 `UNRESOLVED`（生产实测 `complaint_cleanliness`、
`safety_gas_smell` 均被模型判为 UNRESOLVED），因此无需另设关键词规则。

## 五、边界（明确不做）

- 不改内容防线：动态业务数据与指令注入的拦截保持原样。
- 不改 `_grounded_evidence` 的服务端校验，模型不能自证证据等级。
- 不改复审期与有效期。
- 不做「自动激活后就不可撤销」：人工仍可随时标记失效或拒绝，只是不再是前置关卡。

## 六、验收标准

1. 客人明示的稳定偏好（任意主题）在高置信且原文可验证时自动 ACTIVE。
2. 上述五类「重大」情形仍为 CANDIDATE。
3. 同主题已有 ACTIVE 记忆时，新候选不自动覆盖。
4. 动态业务数据与指令样式内容仍被拦截，且不因放开白名单而进入。
5. 真实数据回归：用生产已有的四条候选跑一遍，确认哪些会自动激活、哪些仍需人工，
   结果与本 Spec 的判据表逐条吻合。
6. 自动激活必须写入 `customer_memory_events`，事后可回答「这条为什么变成 ACTIVE」。

## 七、待你确认

- **A**：置信度阈值维持 0.8，还是调整？
- **B**：`SERVICE_HISTORY`（服务记录，如「上次住过江景房」）要不要也纳入自动晋升？
  它是事实而非偏好，风险低，但会显著增加自动激活的数量。
- **C**：同主题已有 ACTIVE 记忆时，新候选目前的处理是「停在 CANDIDATE 等人工」。
  是否改为自动 SUPERSEDE（旧的转 SUPERSEDED、新的转 ACTIVE）？这会让偏好变化自动
  跟上，但也意味着模型可以在无人过问的情况下改写既有客户事实。
