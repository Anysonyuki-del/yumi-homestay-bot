# 前端审查 F01–F05 与币种统一修复 · Codex → Claude 实施交接

日期：2026-10-04。仓库：`/Volumes/02/obsidian codex/homestay-bot`。

**F01–F05、P3 公开函数迁移与 D4 币种口径统一已完成本地实现和相关验证，供 Claude 只读复审。** D3 真实 Hostex 统计和发布前 all 模型门禁尚未执行，详见 §6。未暂存、提交、推送或部署，未调用真实 DeepSeek、Hostex、企业微信，未写生产数据。当前 HEAD 仍为 `main` / `1ec9f95b17d0aca3a205588b20a306753b5c6226` / 1.67.0；本报告描述的是工作区候选实现，不是线上现状。

## 1. 当前授权与工作区来源

输入为 `docs/reviews/2026-10-04_claude-to-codex-frontend-audit-review-handoff.md`。初始要求分段确认、明确「开始」前不编码；随后用户明确：

> 我没有动过文件，按你的判断来做完，做完后写交接文件

当前指令将判断与完成委托给 Codex，已先更新 `docs/specs/2026-10-04_frontend-audit-fixes-spec.md` v2，再按 F01/F02 → F03 → F04/F05 实施。D1 选择按房间优先直订渠道，D2 采用服务层分组、保留业务安全契约的最小方案。这不包含发布或真实外部调用授权。

后续输入为 `docs/reviews/2026-10-04_claude-to-codex-frontend-audit-fixes-review-handoff.md`。按用户要求先更新 Spec v3、处理 P3；用户随后明确「做完吧」，再按 Spec v4 完成 D4 的既定本地方案。D3 外部统计已单独询问，当前未获得明确授权；不调用真实服务、不提交、推送、部署的限制保持。

两份可疑文档恢复前的内容精确对应旧提交 `74c904d`：

| 文件 | 恢复前 blob | 本轮处置 |
| --- | --- | --- |
| `docs/releases/1.67.0.md` | `27fb36dbb43bdccc95046f59983b906c879bfbef` | 仅工作区恢复到 HEAD，已提交的发布现场与登录验收证据恢复完整；现在与 HEAD 无差异 |
| `tasks/todo.md` | `6995098a97f76a4e85864e4bdc802b88ce0f70b4` | 先恢复 HEAD，再在顶部增加本次任务；历史发布记录保留 |

内容来源已确认，保存者及软件未知。不能据此声称用户、Claude、Codex 或 Obsidian 执行了覆盖。恢复前内容仍可由旧提交取回，索引为空。

既有两份审查报告和 `.impeccable/critique/` 保留，不自动纳入提交；受保护的 `YuMi民宿AI项目总结.txt` 未读取、修改或暂存。没有升级版本、修改 CHANGELOG 或生成新发布记录。

## 2. 最终实现与契约

### F01 · 参考价的房间、渠道和币种

- `integrations/hostex_client.py::HostexClient.list_reference_prices` 从全局优先直订改为逐房选择：本房有 booking_site 就选本房直订，否则选本房其它渠道。仍一次查询所选渠道，不新增参数或公共模型字段。
- `services/approval_page_service.py::ApprovalPageService._price_groups / get_detail` 用 `(channel_type, listing_id)` 映射已有房源字典，提供房间分组、空房缺价及未匹配价格。多房共用同一完整身份时不猜归属；未知行保留供人工核对。
- `integrations/hostex_client.py::reference_price_currency` 统一有效币种，仅 booking_site 缺字段或 null 时采用历史人民币兼容约定；审批分组与报价执行器共用。审批人民币金额显示 ¥，直订默认情况额外注明「直订默认人民币」，明确外币仍按原币种显示，未知 OTA 和空字符串不默认人民币。
- `templates/approvals/detail.html::reference_price_groups` 显示房间标题、编号、渠道、日期、币种和金额。无价房间有明确提示；不自动选房、填成交金额或作币种换算。`static/app.css::.price-list` 在 ≤520px 将资料与金额分行，修复长未知渠道编号把金额挤成竖排的问题。
- `integrations/deepseek_client.py::HostexReadOnlyToolExecutor._reference_prices` 同步用元组身份归房并固定单房单渠道，排除多归属、日期范围外和非人民币记录。新增回退渠道必须明确 `currency=CNY`，未知币种不进入客人报价；既有 booking_site 缺币种保留原人民币兼容约定，明确外币仍排除。
- `tools/reply_regression.py::FakeHostexClient.list_properties` 为既有合成人民币场景显式声明 CNY，使门禁替身继续经过正式币种保护，而不是放宽执行器。

保留逐项上游降级、拒绝入口与确认资格。没有把参考价视为房态、已收款或自动定价。详情仍重复读取两次房源字典，这是保留的既有开销，未声称已优化。

**必要修正 Claude §4：** `scripts/release/reply_gate.sh::REPLY_PATHS` 明确包含 Hostex、DeepSeek 客户端及回复回归工具，本次会命中门禁。旧「未命中，所以跳过」结论作废，Claude 实施复审 §1 已撤回。未来发布固定规划 `REPLY_GATE_SCOPE=all`；本轮没有真实模型调用授权，尚未执行该门禁。

### F02 · 全部客户 POST 拒绝与摘要草稿恢复

- `routes/customers.py::_raise_page_error` 的 POST 调用保留 `OperationRefused` 对象、状态和安全消息，设置业务落点后交给现有全局处理器；未知异常仍为通用 500。三处 GET 保留 HTTP 行为，避免缺失档案或合并页循环回跳。
- `_customer_return_path / _customer_redirect` 统一成功和拒绝的页签、来源地址；来源经 `safe_return_path` 限制为站内。对象不存在回客户列表，其余回 overview、memory、governance、service 或当前合并复核页。
- 10 个写入口全部覆盖：合并复核、手动合并、标签、备注、摘要、重算、删除摘要、记忆复核、清测试数据、交还会话。JSON 保留 403/404/409。重算冷却原有提示与跳转保留。
- `update_customer_summary / _render_customer_detail` 对 HTML 摘要冲突返回 409 的 memory 页：正文展示最新摘要，编辑区自动展开并原样保留提交草稿，新签发一次性 CSRF、读取最新版本。再次冲突仍拒绝，不自动强制覆盖。
- `templates/customers/detail.html::memory-edit` 区分草稿与最新内容，保留自动转义；textarea 增加首个哨兵换行，防止 HTML 解析吞掉草稿自身开头的空行。重新读取遇到权限拒绝或对象缺失时不渲染草稿、档案。

认证和 CSRF 消费位置、4000 字表单约束、仓储 CAS、事务及 JSON 契约保留。草稿仅存在于本次认证响应，未写 Cookie、日志、数据库或持久化草稿。无效/重放 CSRF 沿用现有 HTTP 409，不进入草稿恢复；不把它改成业务拒绝。

### F03 · 控件专用边界

`static/app.css::--control-border` 经典为 `#64748b`，暖色为 `#8d8172`，仅可见 input（原生 checkbox/radio 除外）、select、textarea 使用。`--line-strong`、卡片、表格、日历和按钮边界未加深。`DESIGN.md::colors / Colors` 同步记录角色。

真实同源浏览器检查两主题普通、焦点、aria-invalid 的内外表面边界 ≥3:1，以及焦点对比和禁用不可操作。经典白底约 4.76、页面底约 4.40；暖色卡片约 3.15、页面底约 3.61。证据范围是选定实际控件与相关主题回归，不是全站无障碍认证。

### F04/F05 · 手机信息与安全分派

- `templates/customers/index.html::mobile-card-list` 补住宿日期和完整标签；`templates/properties/index.html::mobile-card-list` 补房号、房型和存在时的下次入住。复用原投影和格式化，未加查询、分页、排序；缺值使用原提示。
- `static/app.css::.mobile-record-card` 允许长值换行，320px 与 1280px 的真实合成投影都完整可读，没有页面级横向溢出。
- `static/admin.js::selectionForms.guard / refresh` 在任务资格通过后、确认和忙态之前检查 assign 员工。空值拦截 click 和 requestSubmit，提示并聚焦员工，保留已选任务；补选清理提示，资格提示优先。
- 取消、归档及无 submitter 默认动作保持，不把员工 select 全局 required；镜像去重、重复提交锁和后端校验保留。

## 3. v2 验证证据（历史；v3 / v4 增量见 §6）

只运行相关验证，没有因共享文件或准备交接自动跑全仓。未改 `application.py`、依赖、构建或数据库结构；影响面可限定为审批参考资料、客户表单恢复、主题控件和列表/选择交互。

| 范围与命令 | 有效结果 |
| --- | --- |
| `pytest -q tests/unit/test_hostex_client.py tests/unit/test_approval_page_service.py tests/unit/test_approval_page_degradation.py tests/integration/test_approval_routes.py tests/integration/test_approval_review_actions.py tests/integration/test_customer_routes.py --tb=short` | 139 passed |
| `pytest -q tests/browser/test_admin_theme.py tests/browser/test_admin_batch_workbench.py --tb=short` | 当时 35 passed；后新增审批窄屏用例并修正金额竖排，下项补验覆盖最终变更。36 个不同用例有有效通过证据 |
| `pytest -q tests/browser/test_admin_batch_workbench.py -k 'lists_keep_operating or approval_price_groups' --tb=short` | 最终 3 passed；与前述列表用例重叠，不重复累加 |
| `pytest -q tests/browser/test_admin_interactions.py -k 'selection or select_all or unchecking or typed_confirm or confirm_text or default_action or bulk_action or cross_form or cancelling_the_send' --tb=short` | 11 passed |
| `pytest -q tests/unit/test_deepseek_client.py -k 'reference_price or price_question or tool_executor' --tb=short` | 8 passed，包含客人报价最终正文的已有证据用例 |
| `pytest -q tests/unit/test_live_price_scope.py tests/unit/test_reply_evidence_boundaries.py tests/unit/test_reply_regression.py -k 'price or tool_sources_cannot or fake_hostex_client' --tb=short`；随后对回复替身 `-k 'fake_hostex or grounded_price'` 补验 | 首次 20 passed、1 failed（旧替身未声明币种）；补齐合成 CNY 后相关 3 passed。去重后 22 个用例有有效通过证据，原失败已解决 |
| Ruff 对全部修改的 Python 源文件和测试；Mypy 对 5 个修改的 Python 源文件；`node --check src/homestay_bot/static/admin.js`；`git diff --check` | 均通过 |

以上合计 **216 个不同相关用例** 有有效通过证据，不把重复运行叠加。后续仅改文档，不再重复业务测试。保留既有 Starlette TestClient/httpx 弃用警告，未为此修改依赖。

缺陷反例在修复前失败：逐房回退、渠道身份与币种、审批分组；10 个 POST 的 HTML 恢复及各 JSON 状态分支；真实摘要版本冲突；经典边界不足；手机缺住宿日期；空员工的两种入口；长编号金额竖排。测试设置修正产生的失败不作为产品缺陷统计。

摘要专项采用真实 `SessionCustomerAdminService`、SQLite 摘要仓储及 `SessionAdminCsrfService`：验证两次并发冲突不覆盖最新内容、输入开头空行、原始空白/特殊字符保留且转义、令牌已消费、新令牌/版本人工重试成功、JSON 冲突保持。测试认证替代登录凭证，生产登录、目标 PostgreSQL 并发未由这些证据证明。

浏览器使用合成记录与临时 SQLite，真实路由、主题同源资源及正式会话门面；禁外网。分派测试只记录并阻断浏览器写请求，业务成功回跳另用本地真实路由验证。所有测试服务已随 fixture 关闭。

临时截图（合成数据，供本机复审；不提交图片）：

- `/tmp/yumi-frontend-audit-fixes/customers-320.png`、`customers-1280.png`
- `/tmp/yumi-frontend-audit-fixes/properties-320.png`、`properties-1280.png`
- `/tmp/yumi-frontend-audit-fixes/approval-320.png`

已人工查看手机客户、房源、审批截图，修复竖排金额后再确认。Ponytail 复杂度自审未发现需新增依赖、配置或抽象的理由；两个客户 helper 有正常页/恢复页、成功/拒绝的真实复用。未新增设计忽略；既有日历定位设计及已有忽略保持。

## 4. 请 Claude 重点复审

1. F01 完整渠道身份、多归属和币种保护：未知回退币种不进入客人资料；既有直订缺币种的人民币兼容是保留的历史边界，不是本轮真实 Hostex 币种验收。
2. F02 GET 与 POST 的异常差异、10 个 POST 落点、站内来源限制；摘要重新读取权限拒绝时不渲染草稿；CSRF 与版本保护没有被冲突恢复绕开。
3. F03 控件角色与装饰线隔离；F04 长标签完整展示；F05 捕获阶段顺序、取消/归档和默认动作兼容。
4. 复核最终工作区差异与 Spec，不用旧审查或旧版本号推断生产状态。源码实现与合成浏览器证据可以评审；此处没有新的生产、CI 或真实模型结果。

## 5. 未覆盖与后续门禁

本地实现与交接已完成。尚未执行真实模型回归、真实 Hostex 渠道/币种验收、生产登录页面验收或安卓/Windows 真机检查；没有真实消息收件或经营动作验收。

最后补验 `pytest -q tests/browser/test_admin_batch_workbench.py -k summary_conflict --tb=short` 覆盖草稿开头空行（同一用例，不累加）。

未来获准发布时，按最终变更性质维护版本、CHANGELOG 与发布记录，对最终差异做新鲜 Ponytail 审查，完成本次命中的真实模型门禁；按项目要求确认本地全量或候选 CI 的部署门禁、可恢复备份及源码/容器/数据库/页面独立验收。提交、推送、部署和真实外部调用分别取得当前授权。本报告不构成这些动作的授权。

## 6. Claude 实施复审的 v3 / v4 处理补录

- §1：已核实三个 REPLY_PATHS 文件均有改动；旧跳过结论作废。Spec §2.2 / §5.1 固定按 `REPLY_GATE_SCOPE=all` 规划最终发布门禁，本轮不执行。
- §4 P2：统一币种方案 D4 已按用户「做完吧」实施。`reference_price_currency` 是唯一判定位置，审批与客人侧共用；缺币种直订标「¥399（直订默认人民币）」，其它缺币种 / 空字符串仍未知，显式外币不成为人民币报价。不做汇率换算，不自动填建单金额。
- D3：一次只读 `GET /properties?offset=0&limit=100` 的统计范围、无重试 / 翻页、脱敏和样本覆盖边界已写入 Spec，仍待独立授权。未获取真实渠道样本，不能声称整个账号有直订或上游提供 currency；真实契约的发布前风险仍开放。
- §5 P3：`routes/page_errors.py::wants_html` 已公开，两个处理器和 `routes/customers.py::update_customer_summary` 三个调用点迁移；Accept 权重和 HTTP 契约保持。complaints / knowledge 的本模块私有函数及详情两次房源读取保留。

v3 的 27 个相关用例和两文件 Ruff / Mypy 已通过，见 Spec §8.3；v4 本轮 **65 passed**，命令与结果见 Spec §8.4：审批 / Hostex / 路由 55 项、客人报价相关 8 项、两主题 320px 审批浏览器 2 项。新增 10 项矩阵使同一资料同时经过实际审批服务和正式报价工具入口，覆盖直订 / OTA、缺字段 / null / 空字符串、显式 CNY / USD。修复前直订缺币种在审批页被错误标为未知的反例已失败，最终均通过。

v4 Ruff（3 份源码与 2 份测试）、Mypy（3 份源码）、差异检查通过；一个既有 Starlette 弃用警告保留。截图已人工查看：`/tmp/yumi-frontend-audit-fixes/approval-currency-classic-320.png`、`approval-currency-warm-320.png`。浏览器使用真实服务响应、临时 SQLite、正式模板 / CSS，全部出网被阻断；两主题金额说明布局使用显式主题选择，切换和存储仍由此前同源主题验证证明。

v2 的 216、v3 的 27 和 v4 的 65 各自保留其范围，存在重叠，不叠加为最终全量数量。本轮未改应用装配、依赖、构建或数据库，未重复全仓和未受影响的主题 / 客户流程测试。简化自审未新增缓存、配置、依赖或公共接口，唯一 helper 确有两个调用方。请 Claude 重点复核共享规则是否只用于缺币种直订、来源说明是否与原始字段一致，以及现有渠道 / 日期安全边界是否保留；不要把这些合成证据替代 D3 和真实模型 all 门禁。
