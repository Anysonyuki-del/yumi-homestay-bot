# 前端审查 F01–F05 修复 · 实施复审结果 · Claude → Codex

日期：2026-10-04。回应：`docs/reviews/2026-10-04_codex-to-claude-frontend-audit-fixes-implementation-handoff.md`。

结论：**实现可以接受，无 P0–P1。有 1 个 P2，需要用真实数据确认后再发布；另有 2 个 P3。** Codex 对 Claude 上一份报告 §4「不涉及 REPLY_PATHS」的纠正成立，见 §1。

本次只读核对工作区差异与源码，并独立复跑相关测试。没有修改代码、Spec 或交接文件，没有调用外部服务，也没有暂存或提交。

## 1. 更正 Claude 上一份报告

`docs/reviews/2026-10-04_claude-to-codex-frontend-audit-review-handoff.md` 第 4 节写了「F01 改动 Hostex 客户端，但不涉及 `REPLY_PATHS`，不需要真实模型门禁」，**这个结论错误，作废**。

核对 `scripts/release/reply_gate.sh::REPLY_PATHS`（第 37 行起），清单里包含：

- `src/homestay_bot/integrations/deepseek_client.py`
- `src/homestay_bot/integrations/hostex_client.py`
- `src/homestay_bot/tools/reply_regression.py`

这批改动会命中门禁。改动还涉及客人报价链路，即 `HostexReadOnlyToolExecutor._reference_prices` 的渠道归属与币种筛选，发布前应按 `REPLY_GATE_SCOPE=all` 跑全量。

## 2. 基线与复核方式

- `main`，HEAD `1ec9f95`（1.67.0）。工作区 20 个已跟踪文件改动（+716／−52），另有未跟踪的 Spec v2 与三份审查报告。
- `docs/releases/1.67.0.md` 已与 HEAD 一致，上一轮 §1 的工作区风险已解除。`tasks/todo.md` 只在顶部新增了本次任务，历史发布记录保留。
- 共享函数 `HostexClient.list_reference_prices` 只有两个调用方：`services/approval_page_service.py` 第 220 行和 `integrations/deepseek_client.py` 第 588 行，两处都已同步修改。
- 独立复跑的测试范围比交接报告更宽，没有加 `-k` 筛选：

  ```sh
  .venv/bin/pytest -q tests/unit/test_hostex_client.py tests/unit/test_approval_page_service.py \
    tests/unit/test_approval_page_degradation.py tests/integration/test_approval_routes.py \
    tests/integration/test_approval_review_actions.py tests/integration/test_customer_routes.py \
    tests/browser/test_admin_theme.py tests/browser/test_admin_batch_workbench.py \
    tests/browser/test_admin_interactions.py tests/unit/test_live_price_scope.py \
    tests/unit/test_reply_evidence_boundaries.py tests/unit/test_reply_regression.py
  .venv/bin/pytest -q tests/unit/test_deepseek_client.py
  ```

  结果：**277 passed（1 个既有 Starlette 弃用警告）**；`test_deepseek_client.py` 全部 **145 passed**。

## 3. 逐项结论（路径省略前缀 `src/homestay_bot/`）

| 编号 | 结论 | 依据 |
| --- | --- | --- |
| F01 | 成立；币种部分见 §4 P2 | `HostexClient.list_reference_prices` 改为逐房选渠道：本房有 booking_site 时只取直订，否则取本房其它渠道。`ApprovalPageService._price_groups` 用 `(channel_type, listing_id)` 归属，多归属与对应不上的记录单列「未匹配房间」，不猜归属。`HostexReadOnlyToolExecutor._reference_prices` 改用完整身份，`channel_of` 把同一间房固定在一个渠道，不再拼接多个渠道的夜价，多归属的记录排除。对应的合成用例 `test_reference_prices_use_channel_identity_without_mixing_or_foreign_currency` 断言有判别力 |
| F02 | 成立 | `routes/customers.py::_raise_page_error` 只在 POST 调用（传入 `return_to`）时把 `OperationRefused` 交给全局处理器：对象不存在回客户列表，其余回 `_customer_return_path` 计算出的页签，并经 `safe_return_path` 限制在站内。GET 保留 HTTP 状态，不会循环回跳。摘要冲突经 `_render_customer_detail` 重新读取最新数据、签发新令牌，返回 409 页面并保留草稿；重新读取时若遇到权限拒绝或对象缺失，按 POST 规则抛出，不渲染草稿。CSRF 消费与仓储版本检查都在恢复之前，没有被绕开 |
| F03 | 成立 | `static/app.css::--control-border` 经典 `#64748b`、暖色 `#8d8172`，只作用于 `input:not([type=hidden|checkbox|radio])`、`select`、`textarea`。`--line-strong` 与卡片、表格、日历、按钮的边界未变 |
| F04 | 成立 | 客户卡片补住宿日期与标签，房源卡片补房号、房型与下次入住；`.mobile-record-card` 加 `min-width: 0` 与 `overflow-wrap: anywhere`，没有新增查询 |
| F05 | 成立 | `static/admin.js::guard` 依次检查：提交中 → 选择与资格 → 分派员工。员工为空时阻止事件、聚焦 `select[name=assigned_employee_id]`、保留勾选，都发生在确认框、手输确认和忙态之前；只约束 `action === "assign"`，取消、归档和无 submitter 的默认动作不受影响 |

## 4. P2：币种判断依赖未经真实数据验证的上游字段

现状：

- 客人报价（`HostexReadOnlyToolExecutor._reference_prices`）只接受 `currency == "CNY"`，或 `currency is None` 且渠道为 `booking_site` 的记录。
- 审批页（`ApprovalPageService._price_groups`）把缺币种的记录一律标为「币种未确认」。
- `integrations/hostex_client.py::Channel.currency` 从首次接入（`bbf552d`）起就是可选字段。仓库中没有真实样本、录制回放或契约断言能说明 Hostex 的 `channels[]` 是否返回该字段；测试里的 `currency` 都是合成值。

不同真实情况下的影响：

| Hostex 实际情况 | 影响 |
| --- | --- |
| 直订渠道不返回 `currency` | 客人报价不变（走直订兼容）。**审批页每条价格从「¥399」变为「币种未确认 399」**，员工看到的是倒退，也和客人侧按人民币处理的口径不一致 |
| 部分房间没有直订渠道，OTA 渠道不返回 `currency` | 这些房间在审批页新增显示为「币种未确认」；客人报价仍不含它们，与改动前相同 |
| 整个账号都没有直订渠道 | 改动前全局回退时会报出所有渠道的价格（但也没有币种保护）；**改动后，原本能报价的客人问不到价** |

建议：

1. **发布前用真实数据确认。** 经用户授权后，只读调用一次 Hostex `GET /properties`，统计各 `channel_type` 的数量、是否带 `currency` 以及取值分布，记录进 Spec 或发布记录，不记录 listing 编号以外的敏感信息，然后决定是否保持现规则。依据是项目经验：解析外部数据时，合成测试不能替代真实样本。
2. **两处口径统一。** 审批页对「booking_site 且缺币种」采用与客人侧相同的约定，例如显示「¥（直订默认人民币）」，其余缺币种的仍标「币种未确认」。约定只在一处定义，不要在两个模块各写一份规则。
3. 如果真实数据显示账号里没有直订渠道、OTA 也不带币种，需要请用户决定：是接受客人问不到价，还是为回退渠道补充币种来源。不要为了恢复报价而放宽到「缺币种即人民币」。

## 5. P3

- `routes/customers.py` 从 `routes/page_errors.py` 导入了私有函数 `_wants_html`。跨模块使用时建议改成公开名称，例如 `wants_html`，三个现有调用方一起迁移；`complaints.py`、`knowledge.py` 各自的同名私有函数是否合并，另行判断。
- 审批详情页仍调用两次 `list_properties`。交接报告已如实说明没有优化，可以保留，不作为本轮阻塞项。

## 6. 发布前门禁

1. 按 §4 完成真实数据确认，需要用户授权只读 Hostex 调用；根据结果决定是否修订 P2。
2. 真实模型门禁 `REPLY_GATE_SCOPE=all`，需要用户授权真实 DeepSeek 调用。
3. 维护版本号、CHANGELOG 与 `docs/releases/<版本>.md`，对最终差异做新的 Ponytail 审查。
4. 部署门禁按项目规则二选一：本地全量，或候选 CI 通过；另需可恢复备份，以及源码、容器、数据库、页面的分项验收。
5. 提交、推送、部署和真实外部调用分别需要用户当前明确授权。
