# 知识库页面整体重构 — 终版实施 Spec（Final）

- **版本**：Final（v2 审查闭合定稿，供实施方直接执行）
- **日期**：2026-11-25
- **代码基线**：`main` = `origin/main` = `fa0ac3872e5dd5a9bf1e7b52102a5e1ded4c0022`（`v1.76.1`）
- **决策依据**：
  - 用户阶段 1–2 拍板（U1–U7）；
  - 独立审查意见（`docs/reviews/2026-11-25_claude-to-gemini-knowledge-base-refactor-spec-review.md`）；
  - 用户审查后两项最终裁决：**P1-1 选 B（英文仅在生效时必填）**、**P2-1 配图上限 4 张**；
  - 全部 11 条审查意见（1 P1 + 5 P2 + 5 P3）已闭合。
- **执行门禁**：本 Spec 已定稿，实施方需在收到用户明确执行指令后编码；未获当前明确授权前，**不得**执行 `git commit`、`git push`、部署生产、修改数据库结构或调用真实外部服务。

---

## 0. 硬边界与禁止事项（实施全程不得违反）

1. **零数据库迁移**：不修改 `src/homestay_bot/domain/models.py`，不新增 Alembic 迁移。
2. **零 `REPLY_PATHS` 改动**：不修改 `scripts/release/reply_gate.sh` 中 `REPLY_PATHS` 列出的任何文件（包括 `services/knowledge_service.py`、`services/knowledge_evidence_policy.py`、`services/conversation_service.py`、`services/knowledge_embeddings.py`、`integrations/deepseek_*.py` 等），从而不触发真实模型回归门禁。
3. **生效数据契约不变**：所有满足 `is_enabled=True` 且 `scope != "unreviewed"` 的条目（即 `repositories/knowledge.py::list_active()` 可见集合），必须经统一服务层不变式保证**不含 `【待管理员确认】` 占位符且 `question_en`/`answer_en` 去空白后均非空**。
4. **安全与平台红线不变**：
   - 防串房（房间知识仅绑定单一 `property_id`）；
   - 权限边界（员工只读，仅管理员可写与访问 `/employee/knowledge/new`、候选与资料核实）；
   - 服务端一次性 `knowledge-write` CSRF（严禁降级为可重放令牌或 Cookie 信任）；
   - 遵守 `middleware.py::EMPLOYEE_CSP`（零内联 `<script>`、零 `on*` 属性、零外链资源）；
   - 遵守 `DESIGN.md` 与 `v1.76.0` 状态色守卫（深陶红 `--primary` / `--focus` / `--link` 仅用于可操作项，绝不用于非交互状态徽章）；
   - 遵守 `layouts/auth.html` 零 `admin.js` 隔离与单页唯一可见 `<h1>`；
   - 审计日志只记动作、条目/配图编号，绝不落问答正文、关键词或图片内容。
5. **不触碰无关工作区文件**：保留工作区既有未提交文件（`.impeccable/config.json`、`.impeccable/surfaces/`、`PRODUCT.md`）。

---

## 1. 现状证据（问题根因）

1. **全局与房间混排、筛选残缺**：
   - `routes/knowledge.py::KnowledgeAdminService.list_all()`（L237–L277）把 `global`、`property`、`public`、`unreviewed` 混在同一列表且只按 `id` 升序；
   - `room=shared`（L269–L271）查询 `property_id IS NULL`，把本店通用、店外公开、待审核三类揉在一起；无独立范围筛选、无分区/分房计数；
   - `templates/knowledge/index.html`（L50）与 `entry_state.html`（L12–L13）只显示 `title` 不显示 `room_number`；
   - 房源常驻资料卡（`services/knowledge_service.py::property_card_snippet()` L497–L522）未在房间知识页呈现。
2. **视觉区分失效**：
   - `templates/knowledge/entry_state.html`（L11–L16）中 `global`、`property`、`public` 和 `show_in_miniprogram` 全用同一种 `info` 蓝色徽章。
3. **默认 `unreviewed` 陷阱与未联动表单**：
   - `scope_fields.html`（L2）默认 `scope="unreviewed"`，但按钮写「新增并启用」，导致新增后被 `list_active()` 静默排除；`scope` 与 `property_id` 双下拉未联动。
4. **高频操作受限与导航错位**：
   - 13 字段平铺藏在 `<details id="knowledge-create">` 内；
   - 英文问答无论是否生效都强制必填；
   - 分类只能手打精确字面匹配（`routes/knowledge.py` L268）；
   - 详情页禁止启停（`detail.html` L40–L41）且 `_render_detail` 未读取 `pop_page_notice`；
   - 缺少多房间创建与跨房复制；`MAX_KNOWLEDGE_IMAGES = 3`（L72）；
   - `templates/knowledge/referrals.html`（L6–L10）把 `tab-nav` 放进 `block content` 而不是 `block page_actions`。

---

## 2. 目标功能与信息架构（F1–F4）

### F1. 顶层工作区切分、跨区搜索发现性与导航统一
- 统一 `knowledge/index.html` 与 `knowledge/referrals.html` 的页头 `block page_actions` 为 5 个平级页签：
  1. **全店通用**（`section=global`，默认视图）
  2. **按房间管理**（`section=rooms`）
  3. **店外周边**（`section=public`）
  4. **待审核候选**（`view=candidates`，仅管理员）
  5. **资料核实**（`/employee/knowledge-referrals`，仅管理员，显示 `referral_open_count`）
- **跨区搜索发现性**：
  - 带 `query`（及 `enabled`/`category`）筛选时，全店通用 / 按房间管理 / 店外周边 / 待审核范围各页签徽章显示**当前筛选条件下的命中数**；切换页签保留 `query`、`enabled`、`category`（切离 `rooms` 时清 `room`，页码归 1）。
  - 当前页签 0 命中而其他页签有命中时，空状态卡片直接给出可点击跳转提示（如「全店通用暂无匹配，**按房间管理**中有 3 条匹配 → 去查看」）。
- **存量 `unreviewed` 独立提醒条**：
  - 仅当存在 `unreviewed` 条目（总数 > 0 或筛选命中 > 0）时，列表顶部显示暖琥珀提示横幅，点击进入 `section=unreviewed` 集中定界；存量为 0 时界面不出现该概念。
- **锚点与旧链接兼容**：
  - 列表区保留 `id="knowledge-entries"` 容器锚点与 `category` 参数语义（兼容 `test_knowledge_referrals_page.py` L327）；
  - `GET /employee/knowledge` 视图解析优先级（确定性规则）：
    1. `view == "candidates"` 且为管理员 → 候选分区（普通员工忽略 `view=candidates` 退回知识分区）；
    2. 否则若显式传入 `section` → 校验枚举 `global | rooms | public | unreviewed`（非法值返回 422）；
    3. 否则若 `room` 为正整数字符串 → `section="rooms"` 且选中该房间；
    4. 否则若 `room == "shared"` → `section="global"`；
    5. 否则默认 `section="global"`。
  - `list_url()` 生成链接时，默认 `section="global"` 且无 `room` 时省略 `section` 参数（保证原有 `/employee/knowledge?page=1&candidate_page=2` 分页 URL 形式不变）。

### F2. 「按房间管理」双栏主从工作台
- `section=rooms` 采用主从布局（`≥ 1024px` 左侧 240px 房间目录 + 右侧列表面板；`< 1024px` 顶部紧凑房间切换栏，390px 零水平溢出）：
  - **房间目录**：首项「全部房间知识（总条数）」，其后按 `(PropertyProfile.room_number, PropertyProfile.id)` 列出全部房源，每项展示等宽房号微标、`title`、停用标识（若 `not is_active`）及当前筛选下知识条数胶囊（0 条灰显）。
  - **选中单房时的顶部只读「本房常驻房源资料卡」**：直接展示该房源的 `room_type`、`district`、`address_hint`、`parking_instructions`（与机器人 `property_card_snippet()` 同源；四项全空时显示轻量提示），附「去房源页编辑 →」链接（管理员可见）。
  - **上下文新建入口**：选中单房时主按钮写 `+ 为【房号 · 房名】新增知识`（跳转 `/employee/knowledge/new?scope=property&property_id=<id>&return_to=...`）；未选单房时写 `+ 新增房间知识`（跳转 `/employee/knowledge/new?scope=property&return_to=...`）。
- 全页面（目录、筛选/表单下拉、卡片徽章、详情标题）统一展示 **`房号 · 房名`**（`room_number` 为空或等于 `title` 时只显示 `title`）。

### F3. 高辨识度视觉体系（对齐 `DESIGN.md`）
- 严禁借用 `--primary` / `--focus` / `--link` 作为非交互状态色；在 `static/app.css` 新增独立语义令牌（保证正文对比度 ≥ 4.5:1）：
  - **全店通用（`global`）**：青绿系（如 `--scope-global-bg: #e7f3ee; --scope-global-fg: #1d5c46; --scope-global-bd: #b9ddd0`）；勾选小程序时紧邻独立「小程序指南」徽标；
  - **房间专属（`property`）**：深靛系（如 `--scope-room-bg: #ebeef8; --scope-room-fg: #2f3f7a; --scope-room-bd: #c6cfe9`），文案显示 **`房号 · 房名`**；
  - **店外周边（`public`）**：暖灰中性系（如 `--scope-public-bg: #f0ece6; --scope-public-fg: #544e46; --scope-public-bd: #d8d0c5`）；
  - **范围待审核（`unreviewed`）**：沿用暖琥珀 `warning` 徽章「范围待审核 · 暂不参与回答」；
  - **缺英文徽章**：当 `not entry.question_en.strip() or not entry.answer_en.strip()` 时显示暖琥珀 `warning` 徽章「缺英文」。
- 列表卡片信息增强：展示**配图数量角标**（`配图 N 张`，0 张不占视觉噪声）、有效期/附加条件词/排除词紧凑摘要，以及编辑、启停、快捷定界入口。

### F4. 独立新建页、分层表单与 5 项限制解除

1. **独立新建页 `GET /employee/knowledge/new`**（仅管理员，普通员工 403）：
   - 支持 URL 预设 `scope`（`global` / `property` / `public`，默认 `global`）、`property_id` 与 `return_to`；
   - 与详情编辑页共用分层表单宏；列表页不再嵌入 `<details id="knowledge-create">`；
   - 提交失败（422 HTML）原地重绘 `/employee/knowledge/new`，保留全部输入与多房间勾选并换发新 CSRF。
2. **可视化归属切换与多房间单事务批量创建**：
   - 顶部为三选一分段单选（`全店通用` / `指定房间` / `店外周边`），由 `admin.js`（`data-kb-scope-form`）联动显隐房间选择区与小程序勾选提示；无 JS 时字段均可见可提交。
   - 新建页选「指定房间」时列出**在用房源（`is_active=True`）**多选复选框 +「全选在用房 / 清空」按钮。
   - `POST /employee/knowledge` 同时兼容 `property_ids: list[str]`（新表单）与 `property_id: str`（旧单值调用方）。
3. **英文问答改为「仅生效时必填」**：
   - 留空即存空串 `""`；不以中文回填英文。
   - 新建页英文区默认折叠；编辑页若缺英文则默认展开；详情页启用按钮旁对缺英文条目显示「请先补全英文问答后再启用」。
   - 新建（单条或多房）留空英文时，**不打回表单，直接保存为停用草稿（`is_enabled=False`）** 并经 `set_page_notice` 明确提示「已保存为停用草稿，补全英文后才能启用」。
   - 编辑、启用、快捷定界、候选转换、资料核实入库均受同一服务层不变式约束（见 §3.2）。
4. **详情页直接启停、分类下拉补全与快捷定界**：
   - 详情页右侧状态区（`id="entry-status"`）直接提供「立即停用 / 立即启用」表单，携带 `return_to=/employee/knowledge/{entry_id}#entry-status`，移除原“请回列表执行”文案；
   - `_render_detail` 补读 `notice = pop_page_notice(request) or None`；
   - `toggle_knowledge` 仅在目标路径为 `/employee/knowledge` 且不含 `#` 时追加 `#knowledge-entries`；
   - 列表页分类筛选改为已有分类 `<select>` 下拉（含条数与当前查询值回显）；表单分类输入绑定 `<datalist>`；
   - 高级触发与有效期（关键词、`valid_from`、`valid_until`、`trigger_any`、`trigger_exclude`）收入 `<details>`，编辑页已填任一项时自动展开；
   - 新增 `POST /employee/knowledge/{entry_id}/scope` 快捷定界接口，仅允许将 `scope == "unreviewed"` 条目定界为 `global` 或 `public`，卡片确认弹窗按启用与小程序状态给出明确风险文案。
5. **配图上限 4 张 & 跨房间复制（含物理拷贝配图）**：
   - `MAX_KNOWLEDGE_IMAGES = 4`（保持 JPG/PNG、单张 ≤ 5MB）；
   - 详情页配图区文案照实写明：
     > 「每条最多 4 张（JPG 或 PNG，单张不超过 5MB）。这条知识作为固定回答发给客人时，文字之后按顺序发这些图；机器人每轮回复最多发 4 张图，有欢迎图或一次回答多个问题时会少发几张，排在前面的优先发送；模型自由作答时不发。」
   - 新增 `POST /employee/knowledge/{entry_id}/copy`：详情页右侧对 `property`（排除源房间自身）和 `global` 条目显示「复制到其他房间…」折叠面板（仅列在用房源，默认勾选「同时复制配图」当源条目有图时）。
   - 复制出的新条目统一设为 `scope="property"`、**`is_enabled=False`（初始停用）**、`show_in_miniprogram=False`，完整复制问答/分类/关键词/有效期/触发词及（可选）物理拷贝配图；若目标房已有相同归一化 `question_zh`，成功提示注明同名提醒。

---

## 3. 精确技术契约（实施直接对照）

### 3.1 路由清单与强制注册顺序（`src/homestay_bot/routes/knowledge.py`）

> **强制顺序要求**：静态路径与明确子动作路径必须注册在动态路径參數之前！

| 顺序 | Method & Path | 权限 | CSRF | 核心职责与返回 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `GET /employee/knowledge` | 员工 | — | 渲染双工作区列表或候选分区（带 `section`/`room`/`query`/`enabled`/`category`/`view`/`page`/`candidate_page`）。 |
| 2 | `GET /employee/knowledge/new` | 管理员 | 签发 | **必须在 `GET /{entry_id}` 之前注册**。渲染独立新建页，预设 `scope`/`property_id`/`return_to`。 |
| 3 | `GET /employee/knowledge/{entry_id}` | 员工 | 管理员签发 | 渲染双栏吸顶详情页；读取 `pop_page_notice(request)`。 |
| 4 | `POST /employee/knowledge` | 管理员 | 消费 | 新建单条或多房间条目；HTML 422 重绘 `knowledge/new.html`（非 HTML 仍返回 JSON 422）；成功 303 重定向并写 `set_page_notice`。 |
| 5 | `POST /employee/knowledge/candidates/{candidate_id}/convert` | 管理员 | 消费 | 转换候选为生效知识；经 `_validate_active_english` 校验英文非空。 |
| 6 | `POST /employee/knowledge/candidates/{candidate_id}/snooze` | 管理员 | 消费 | 保持现有 30 天关闭逻辑不变。 |
| 7 | `POST /employee/knowledge/{entry_id}/edit` | 管理员 | 消费 | 编辑条目；已启用且非待审条目清空英文时 422 原地重绘 `detail.html`。 |
| 8 | `POST /employee/knowledge/{entry_id}/delete` | 管理员 | 消费 | **必须在 `POST /{entry_id}/{action}` 之前**。永久删除（逻辑不变）。 |
| 9 | `POST /employee/knowledge/{entry_id}/copy` | 管理员 | 消费 | **必须在 `POST /{entry_id}/{action}` 之前**。跨房复制为停用草稿（含可选物理拷贝配图）；失败返回 422/409 并保留目标提示，成功 303 回详情页并带 `set_page_notice`。 |
| 10 | `POST /employee/knowledge/{entry_id}/scope` | 管理员 | 消费 | **必须在 `POST /{entry_id}/{action}` 之前**。快捷定界 `unreviewed -> global|public`；失败抛 `KnowledgeFormError`（挂安全 `return_to` 回跳提示），成功 303 回来源页并带 `set_page_notice`。 |
| 11 | `GET/POST /employee/knowledge/{entry_id}/images/...` | 员工读/管理员写 | 写消费 | 配图预览、上传（上限 4 张）、删除、排序（逻辑与现有一致）。 |
| 12 | `POST /employee/knowledge/{entry_id}/{action}` | 管理员 | 消费 | `action in {"enable", "disable"}`。启用走占位符 + `_validate_active_english`；仅目标路径为 `/employee/knowledge` 且无 `#` 时补 `#knowledge-entries`；成功写 `set_page_notice` 并 303 回跳。 |

### 3.2 统一服务层不变式与方法契约

1. **英文生效不变式（单一入口）**：
   ```python
   def _validate_active_english(
       *,
       is_enabled: bool,
       scope: str,
       question_en: str | None,
       answer_en: str | None,
   ) -> None:
       if is_enabled and scope != "unreviewed":
           if not (question_en or "").strip() or not (answer_en or "").strip():
               raise KnowledgeFormError("启用中的审核知识必须填写英文问题和英文答案，请补全后再启用")
   ```
   - 覆盖写路径：`create`、`create_for_rooms`、`update`、`set_enabled(..., enabled=True)`、`set_scope`、`convert_candidate`、`convert_referral`（`routes/knowledge_referrals.py::convert` 移除 `min_length=1`，统一走此校验返回可读 422 并保留表单输入）。
   - `create` / `create_for_rooms` 特殊规则：当 `scope != "unreviewed"` 且缺任一英文时，自动设置 `is_enabled=False` 保存为停用草稿，**不抛错**；仅当中英文均非空（或 `scope == "unreviewed"` 兼容旧调用）时 `is_enabled=True`。

2. **多房间批量创建校验矩阵（`create_for_rooms`）**：
   - `property_ids` 在路由层声明为 `Annotated[list[str], Form()] = []`（**严禁声明 `list[int]`**），自行解析以保证全部错误走 `KnowledgeFormError` 422 原地重绘：
     - 同时传非空 `property_id` 与非空 `property_ids` → 422；
     - 含空串或非正整数 → 422；
     - 重复 ID → 去重保序，不报错；
     - 任一房源不存在 → 422，指明缺失编号；
     - 任一房源 `is_active is False` → 422「不能为已停用房间新增知识」；
     - `scope != "property"` 但带了任意非空房间参数 → 422「全店通用 / 店外周边不能指定房间」（绝不静默丢弃）；
     - `scope == "property"` 但未选房间 → 422；
   - 事务语义：全部校验通过后，在**同一数据库事务**内逐条 `add(KnowledgeEntry)` + `flush()` + `_add_audit("knowledge.create")`，最后一次 `commit()`。
   - 回跳语义：建 1 间房回跳 `/employee/knowledge?section=rooms&room=<id>`（若安全 `return_to` 非单房冲突可保留）；建 N > 1 间房统一回跳 `/employee/knowledge?section=rooms`（即使原 `return_to` 指向单房）。

3. **快捷定界契约（`set_scope`）**：
   - 校验原条目存在且 `entry.scope == "unreviewed"`，否则抛 `KnowledgeFormError("仅范围待审核的条目可使用快捷定界")`；
   - 目标范围仅允许 `{"global", "public"}`，否则抛 `KnowledgeFormError`；
   - 复用 `_validate_content`（占位符拦截）与 `_validate_active_english`（当 `entry.is_enabled` 为真时拦截缺英文）；
   - 更新 `entry.scope = target_scope`、`entry.updated_by = employee_id`（保持 `show_in_miniprogram` 原值不清空）、记审计 `knowledge.set_scope`、提交。
   - 模板确认弹窗 `data-confirm`（针对 `entry.is_enabled == True`）：
     - 设为 `global` 且未勾小程序：「这条知识处于启用状态，设为全店通用后会立即参与机器人回答，确认继续吗？」
     - 设为 `global` 且已勾小程序：「这条知识处于启用状态且已勾选小程序展示，设为全店通用后会立即参与机器人回答并显示在小程序入住指南，确认继续吗？」
     - 设为 `public`：「这条知识处于启用状态，设为店外周边后会立即参与机器人回答，确认继续吗？」

4. **跨房复制配图四步安全状态机（`PrivateFileStorage.copy_image` + `SessionKnowledgeAdminService.copy_to_rooms`）**：
   - 常量：`MAX_KNOWLEDGE_IMAGES = 4`，`MAX_COPY_TARGET_ROOMS = 100`，`MAX_COPY_IMAGE_FILES = 36`。
   - **Step 1（短事务读源与预校验）**：读取源条目（必须 `scope in {"property", "global"}`）、源配图快照 `[(id, file_id, sort_order), ...]`；解析并去重目标房间（若源为 `property` 则拒绝或排除源房间自身），校验全部目标房存在且 `is_active=True`、数量 `1..100`；若勾选 `copy_images` 且源有图，校验 `len(target_rooms) * len(source_images) <= 36`（超限抛 `KnowledgeFormError` 提示分批或取消勾选复制配图）。
   - **Step 2（事务外物理拷贝）**：若 `copy_images` 且源有图，为每个目标房的每张源图调用 `storage.copy_image(src.file_id)`，记录 `copied_files: list[str]`；任何源图缺失或拷贝失败立即删除已拷出的全部 `copied_files` 并抛出可读 `KnowledgeFormError`。
   - **Step 3（单事务锁内复核并入库）**：开启新事务，执行 `SELECT ... FROM knowledge_entries WHERE id = :id FOR UPDATE` 锁住源条目，重新查询源配图列表并与 Step 1 快照逐项比对 `(id, file_id, sort_order)`；若源条目被删或配图快照不一致，回滚事务、在事务外删除全部 `copied_files`，抛 `KnowledgeFormError("源条目或配图已变化，请刷新后重试", status_code=409)`。复核一致则检测目标房同名 `question_zh` 计数，逐房插入 `KnowledgeEntry(scope="property", is_enabled=False, show_in_miniprogram=False, ...)`、`KnowledgeImage` 及审计 `knowledge.copy`，随后调用 `commit()`。
   - **Step 4（提交前/提交期清理边界）**：
     - `commit()` 之前任何异常：回滚并 `for fid in copied_files: storage.delete(fid)`；
     - **`commit()` 本身抛错时严禁删除 `copied_files`**（无法确定是否已落库，宁留孤儿文件绝不制造悬空引用），记录错误后上抛。

5. **聚合查询与计数方法（`KnowledgeAdminService` + `SessionKnowledgeAdminService` + `KnowledgeAdminServicePort`）**：
   - `list_all(..., section: str | None = None, ...)`：按 `section` 过滤 `scope`（`global -> 'global'`、`rooms -> 'property'`、`public -> 'public'`、`unreviewed -> 'unreviewed'`）；
   - `section_counts(*, query=None, enabled=None, category=None) -> dict[str, int]`：返回当前筛选条件下 `global`、`rooms`、`public`、`unreviewed` 四个分区命中数，以及全库 `unreviewed_total`；
   - `room_entry_counts(*, query=None, enabled=None, category=None) -> dict[int, int]`：返回各 `property_id` 下匹配条数；
   - `list_categories(*, section=None, room=None) -> list[tuple[str, int]]`：返回已有分类及计数（表单 `<datalist>` 使用全库去重分类）；
   - `image_counts_for_entries(entry_ids: Sequence[int]) -> dict[int, int]`：单次 `GROUP BY` 批量查本页条目的配图数，避免 N+1 查询。

---

## 4. 推荐实施顺序（Step 1–6）

1. **Step 1 · 私有存储扩展**：在 `src/homestay_bot/services/private_file_storage.py` 新增 `PrivateFileStorage.copy_image()` 及单元测试。
2. **Step 2 · 服务与路由重构**：更新 `src/homestay_bot/routes/knowledge.py` 与 `src/homestay_bot/routes/knowledge_referrals.py`（先落常量、`_validate_active_english`、查询聚合、`create_for_rooms`、`set_scope`、复制快照/入库、再落严格有序的路由处理器）。
3. **Step 3 · 生产装配透传**：更新 `src/homestay_bot/application.py::SessionKnowledgeAdminService`，实现 `copy_to_rooms()` 四步编排及新增查询/写方法透传。
4. **Step 4 · 模板与前端资产**：重写/新增 `templates/knowledge/{index,new,detail,scope_fields,entry_state,referrals,referral_detail}.html`，更新 `static/app.css`（`.kb-*` 布局与独立语义色令牌）与 `static/admin.js`（`data-kb-scope-form` 联动与全选/清空，遵循 `EMPLOYEE_CSP`）。
5. **Step 5 · 集成、安全与 PG 测试**：同步更新既有契约测试并补齐全部新增判别用例与 PG 竞争用例。
6. **Step 6 · 真实服务浏览器测试与全量收口**：新增 `tests/browser/test_knowledge_workbench.py`，完成静态检查、全量 `pytest` 与 `REPLY_PATHS` 零交集核对。

---

## 5. 测试改动清单与验收标准（Definition of Done）

### 5.1 既有测试同步更新清单（不放松任何安全断言）
1. `tests/integration/test_knowledge_routes.py::KnowledgeAdminStub`：补齐 `KnowledgeAdminServicePort` 新方法签名。
2. `tests/integration/test_knowledge_routes.py` L259：详情页改为断言管理员含启停表单、普通员工仍不含。
3. `tests/integration/test_knowledge_routes.py` L1011：适配 `scope` 控件为分段单选 `value="global" checked`。
4. `tests/integration/test_knowledge_routes.py` L1060–L1090：新建失败改为断言原地重绘独立新建页表单并保留已填输入与新 CSRF（另同步核查从 `GET /employee/knowledge` 取 CSRF 发起新建的旧用例，在空列表场景下改从 `GET /employee/knowledge/new` 取令牌，包括 `test_knowledge_image_admin.py` L387）。
5. `tests/integration/test_knowledge_routes.py` L1206–L1213：验证裸 `/employee/knowledge` 展示待审告警条 + `?section=unreviewed` 展示「范围待审核，暂不参与回答」徽章。
6. `tests/integration/test_knowledge_image_admin.py` L120–L123：配图上限断言改为「满 4 张隐藏上传表单、第 5 张上传被拒」。
7. `tests/integration/test_admin_hardening.py` L103：将 `/employee/knowledge/new` 加入 `no-store` 与安全响应头守卫清单。
8. `tests/browser/test_knowledge_referrals_page.py` L327：靠保留 `id="knowledge-entries"` 与 `category` 参数语义保持零回归通过。

### 5.2 新增判别测试清单
- **英文生效不变式（4 条必测）**：
  1. 启用中条目编辑清空英文 → 422 拒绝并保留输入；
  2. 新建（单条与多房）留空英文 → 成功保存为停用草稿（`is_enabled=False`）并提示补全英文；
  3. 停用条目补全英文后 → 可成功启用；
  4. 快捷定界一条「已启用但缺英文」或「含占位符」的 `unreviewed` 条目 → 被拒，保持 `unreviewed`。
- **多房间新建校验矩阵**：混传 `property_id`+`property_ids`、非数字 ID、重复 ID 去重、不存在房源、已停用房源、`scope != "property"` 带房源、整批事务原子回滚。
- **跨房复制配图安全**：复制结果均为停用 + `show_in_miniprogram=False` + 独立新 `file_id`；删源条目不破坏拷贝条目的物理图片；超 36 个文件被拒；源配图快照变化时锁内中止并清理新文件；`commit()` 异常不删除已拷贝文件；PG 专项在 `tests/integration/test_knowledge_delete_postgresql.py` 补「复制与并发删除源条目竞争」用例。
- **工作区与浏览器验收（`tests/browser/test_knowledge_workbench.py`，真实 FastAPI + 临时 SQLite + 生产同源 CSP）**：
  - 1440px 与 390px 双视口零水平溢出、零控制台报错、零 CSP 违规；
  - 顶层 5 页签切换、跨区搜索命中数与零命中跳转链接；
  - 「按房间管理」房间目录统计 + 只读常驻房源卡 + 统一 `房号 · 房名`；
  - 独立新建页三选一联动与无 JS 降级提交；
  - 详情页直接启停与 `notice` 显示、左侧未保存编辑点右侧启停弹出确认拦截、满 4 张隐藏上传。

### 5.3 最终验收门禁
- [ ] `ruff check .`、`mypy src`、`node --check src/homestay_bot/static/admin.js`、`git diff --check` 全过；
- [ ] 全部知识库集成测试、资产守卫测试（含状态色守卫）、浏览器测试通过；
- [ ] 本地全量离线 `pytest` 通过（因改动 `application.py` 装配层）；
- [ ] `git diff --name-only` 与 `scripts/release/reply_gate.sh` 的 `REPLY_PATHS` 交集为空，且无任何数据库模型或迁移变更；
- [ ] 本地启动确认存量库中无「已启用且英文为空」条目；
- [ ] 产出清晰的实施交接报告（注明未覆盖项如 PG 专项是否本地实跑），不擅自提交/推送/部署。

---

## 6. 审查意见闭合对照表（v1 → Final）

| 编号 | 级别 | 结论 | 终版落地位置 |
| :--- | :--- | :--- | :--- |
| P1-1 | P1 | 用户裁决 B | §0、§2 F3/F4.3、§3.2(1)、§5.2（英文仅生效时必填，新建留空存停用草稿，单一校验覆盖全部写路径） |
| P2-1 | P2 | 用户裁决 4 张 | §2 F4.5、§3.2(4)、§5.1(6)（上限 4 张，说明照实描述欢迎图与多问扣减，测试改为满 4 藏/第 5 拒） |
| P2-2 | P2 | 已闭合 | §3.2(4)（四步并发安全状态机，`commit()` 异常不删文件，单次复制文件总数硬上限 36） |
| P2-3 | P2 | 已闭合 | §2 F4.2、§3.2(2)（`create_for_rooms()` 单事务、`list[str]` 解析、7 项校验与单/多房回跳） |
| P2-4 | P2 | 已闭合 | §2 F1（筛选时页签显示命中数 + 零命中空态提供跨区直达链接） |
| P2-5 | P2 | 已闭合 | §3.2(3)（`set_scope()` 强制校验原态 `unreviewed`，复用占位符与英文不变式，不清零 `show_in_miniprogram`） |
| P3-1 | P3 | 已闭合 | §2 F4.4、§3.1(3)(12)（详情启停带 `#entry-status`，`_render_detail` 读 `pop_page_notice`，`admin.js` 保护未保存） |
| P3-2 | P3 | 已闭合 | §5.1–§5.2（补齐 `test_knowledge_referrals_page.py`、`test_admin_hardening.py`、空列表取 CSRF 兼容、4 条英文用例、PG 竞争用例） |
| P3-3 | P3 | 已闭合 | §2 F4.5、§3.2(4)（限定 `property`/`global` 复制，结果初始停用、`show_in_miniprogram=False`，同名提示） |
| P3-4 | P3 | 已闭合 | §3.2(3)（已启用 `unreviewed` 快捷定界按是否勾小程序区分确认弹窗文案） |
| P3-5 | P3 | 已复核 | 交接单第 123 行 `referral_detail.html` 摘要本身为完整 64 位 SHA-256，保留原文未改 |
