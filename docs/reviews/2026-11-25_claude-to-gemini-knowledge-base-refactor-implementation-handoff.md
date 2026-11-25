# 知识库页面整体重构 · 实施交接报告（Claude → Gemini）

- 依据：`docs/specs/2026-11-25_knowledge-base-refactor-spec.md`（终版，SHA-256 `692e1bd7e9f7ed42d8d4f96a77f93685399857297705aecae22781673cfcca64`，实施前后核对一致）。
- 用户裁决：P1 选 B（英文只在「已启用且范围已审核」时必填，留空存 `""`）；P2-1 配图上限 4 张。
- 状态：本地实施完成，**未提交、未推送、未部署**，未调用任何真实外部服务。
- 请审范围：工作区相对 `fa0ac38` 的未提交改动（清单见 §1；`.impeccable/config.json`、`.impeccable/surfaces/`、`PRODUCT.md` 为用户既有改动，本次未触碰）。

## 0. 硬边界核对（Spec §0）

| 边界 | 结果 | 依据 |
| :--- | :--- | :--- |
| 零表结构迁移 | 满足 | 未改 `domain/models.py`，`migrations/versions/` 无新增 |
| 不改 `REPLY_PATHS` | 满足 | `git diff --name-only` 与未跟踪文件逐一比对 `scripts/release/reply_gate.sh::REPLY_PATHS`，交集为空 |
| 保留 `.impeccable/`、`PRODUCT.md` | 满足 | 三者修改时间早于 Spec 定稿，本次未写入 |
| 安全与 A+E 状态色守卫 | 满足 | 范围色用独立令牌 `--scope-{global,room,public}-*`，未借 `--primary/--focus/--link`；CSRF、`EMPLOYEE_CSP`（无内联脚本与 `on*`）、no-store、单 `<h1>` 均保持；资产守卫测试通过 |

## 1. 改动清单（按 Spec 步骤）

### Step 1 · 私有存储
- `services/private_file_storage.py::PrivateFileStorage.copy_image`：按源文件写出新 uuid 文件（`O_EXCL`、0600），源缺失抛 `LookupError`。单测 `tests/unit/test_private_file_storage.py`。

### Step 2 · 服务与路由（`routes/knowledge.py`）
- 常量：`MAX_KNOWLEDGE_IMAGES=4`、`MAX_COPY_TARGET_ROOMS=100`、`MAX_COPY_IMAGE_FILES=36`；分区映射 `_SECTIONS/_SECTION_SCOPES/_SECTION_LABELS`。
- 英文不变式 `_validate_active_english`（§3.2(1)），覆盖 `update`、`set_enabled(True)`、`set_scope`、`convert_candidate`、`convert_referral`；`create`/`create_for_rooms` 经 `_initially_enabled` 在缺英文时存为停用草稿而不报错。
- 聚合查询（§3.2(5)）：`list_all(section=)`、`section_counts`（一次 GROUP BY + `unreviewed_total`）、`room_entry_counts`、`list_categories`、`image_counts_for_entries`（单次 GROUP BY，避免 N+1）。
- `create_for_rooms`（§3.2(2)）：路由层 `property_ids: list[str] | None` 自行解析（`_parse_room_ids`/`_positive_id`），矩阵全部走 `KnowledgeFormError` 422；全部条目同一事务一次 `commit`，任何异常整批回滚。
- `set_scope`（§3.2(3)）：仅 `unreviewed → global|public`，复用占位符与英文校验，保留 `show_in_miniprogram`，审计 `knowledge.set_scope`。
- 跨房复制（§3.2(4)）：`copy_snapshot`（Step 1 校验）+ `prepare_copies`（Step 3 `FOR UPDATE` 锁源、逐项比对 `(id,file_id,sort_order)`，不一致抛 409；同名问题计数；新条目 `scope=property`、停用、不进小程序；审计 `knowledge.copy`；只 flush）。
- 新页面与路由：`GET /new`（管理员，预设 `scope/property_id/return_to`）、`POST /{id}/copy`、`POST /{id}/scope`；列表 `section` 参数非法值 422；详情 `_render_detail` 读取 `pop_page_notice`。
- 注册顺序已逐项核对与 §3.1 表一致（`GET /new` 在 `GET /{entry_id}` 前；`delete/copy/scope` 在 `/{action}` 前）。
- `toggle_knowledge`：仅当目标路径为 `/employee/knowledge` 且无 `#` 时补 `#knowledge-entries`，并写成功提示。
- `routes/knowledge_referrals.py`：英文字段 `Form("")`（移除 `min_length=1`），统一走服务层校验；页签复用 `section_tabs`。

### Step 3 · 生产装配（`application.py::SessionKnowledgeAdminService`）
- 新方法逐一透传；`copy_to_rooms` 按四步状态机编排：快照短事务 → 事务外 `copy_image`（失败删已拷文件并给可读提示）→ 新会话 `prepare_copies`（异常回滚并删文件）→ 独立 `commit`（**失败只记 warning 并上抛，不删文件**，宁留孤儿不造悬空引用）。`_discard_files` 单个删除失败只记日志，不掩盖原异常。

### Step 4 · 模板与前端
- 新增 `knowledge/nav.html`（`room_label`「房号 · 房名」、`knowledge_tabs`、`category_datalist`）、`knowledge/new.html`。
- 重写 `index.html`（页头 5 页签、待审告警条、分类下拉、房间目录 + 常驻房源卡双栏、跨区零命中跳转）、`detail.html`（`#entry-status` 直接启停、快捷定界、配图文案按 Spec 原文、`#entry-copy` 复制面板）、`scope_fields.html`（三选一分段单选、多房复选 + 全选/清空、英文与高级项 `<details>`）、`entry_state.html`（范围语义色徽章、缺英文、配图张数、快捷定界确认文案逐字按 §3.2(3)）。
- `app.css` 追加 `.kb-*` 与范围色令牌；`admin.js` 追加 `data-kb-scope-form` 显隐联动（隐藏区控件同时 disabled）与全选/清空（派发 `input` 以触发未保存提醒）。无 JS 时所有字段可见可提交，服务端校验兜底。

## 2. 验证证据

| 层 | 命令 / 文件 | 结果 |
| :--- | :--- | :--- |
| 静态 | `ruff check .`、`mypy src`（152 文件）、`node --check admin.js`、`git diff --check` | 全过 |
| 新增集成 | `tests/integration/test_knowledge_workspace.py`（真实 `SessionKnowledgeAdminService` + 临时 SQLite + 真实私有存储） | 33 通过 |
| 既有集成同步 | `test_knowledge_routes.py`、`test_knowledge_image_admin.py`、`test_admin_hardening.py`、`test_admin_assets.py`、`test_knowledge_referrals_page.py` | 通过（按 §5.1 更新断言，未放松安全断言） |
| PostgreSQL 专项 | `test_knowledge_delete_postgresql.py` 新增「复制与并发删除源条目」5 轮竞争；本地临时 PostgreSQL 16 实跑 | PG 用例 11 通过 |
| 浏览器 | `tests/browser/test_knowledge_workbench.py`（真实 uvicorn + `AdminNoStoreMiddleware` 生产 CSP + 临时 SQLite + 真实 PNG） | 7 通过 |
| 全量离线 | `pytest -q`（带 `YUMI_TEST_POSTGRES_URL`，PG 用例一并执行） | 见 §2.1 |

### 2.1 全量结果

最终源码上一次全量：**3894 passed, 16 skipped, 1 warning（174 秒）**，退出码 0。
- 16 项跳过均与本次无关：15 项为真实外部契约（DeepSeek 10、百居易 3、企业微信 2，需显式开关与凭据），1 项因本机无 Docker Compose。PostgreSQL 专项**没有**跳过。
- 1 条警告为 Starlette TestClient 依赖 httpx 的既有弃用提示。
- 全量之后唯一的源码变化为评审截图后补的一条 CSS 间距规则（详情页状态徽章与表单分组框之间留 16px），全量即在该修改之后运行。

### 2.2 判别力（变异检查，均已恢复并 `cmp` 字节一致）
M1 去掉 `update` 英文校验；M2 多房逐条 commit；M3 commit 失败删拷贝文件；M4 跳过锁内快照复核；M5 复制沿用源启用/小程序状态；M6 `set_scope` 放开任意源范围；M7 去掉 `.kb-tabs a, .kb-room-link { position: relative }`（390px 浏览器用例因水平溢出失败）。七项均被对应测试捕获。

### 2.3 数据核查
- 本机 `homestay.db` 停在迁移 `0001_initial` 且知识表为空，不存在「已启用且英文为空」条目。
- **生产库未查**（无当次授权）。上线前建议只读执行：`SELECT count(*) FROM knowledge_entries WHERE is_enabled AND scope <> 'unreviewed' AND (trim(coalesce(question_en,''))='' OR trim(coalesce(answer_en,''))='');` 非零时这些条目仍可回答，但一旦编辑保存会被新不变式拒绝，需先补英文。

## 3. 设计取舍与偏差（请重点审）

1. **复制目标含源房间时拒绝而非静默排除**：Spec §3.2(4) 允许「拒绝或排除」。选拒绝，与 §3.2(2)「绝不静默丢弃」的口径一致；页面本身不列源房间，正常操作不会触发。
2. **API/候选表单默认范围仍为 `unreviewed`**：新建页默认 `global`（Spec F4.1），但非 HTML 的 `POST /employee/knowledge` 未传 scope 时、以及候选转换表单的默认范围保持既有 `unreviewed`，避免旧调用方行为突变。
3. **`create` 对 `unreviewed` 保持启用**：按 §3.2(1)「`scope == "unreviewed"` 兼容旧调用」——待审条目即使启用也不参与回答，存为启用不改变客人可见行为。

## 4. 未覆盖

- 真机（iOS Safari / Android WebView / Windows WebView2）与读屏实测；浏览器测试只用 Chromium 模拟 1440/390 视口。
- 生产数据、生产页面登录后验收、部署副本态均未验证。
- 回复门禁：改动不触及 `REPLY_PATHS`，按项目规则不需运行；未做测试号收发（不影响客人消息链路）。
- 复制配图的跨进程并发只在 PostgreSQL 竞争用例覆盖「复制 vs 删除源」，未覆盖「复制 vs 配图排序/删除」的 PG 交错（SQLite 用例以快照比对覆盖其语义）。

## 5. 请审重点

1. `application.py::SessionKnowledgeAdminService.copy_to_rooms` 四步清理边界，尤其 commit 失败不删文件的取舍。
2. `_parse_room_ids` 矩阵与 Spec §3.2(2) 逐条对照。
3. 英文不变式覆盖面：是否还有绕过 `_validate_active_english` 的写路径。
4. 模板无 JS 降级：隐藏区控件在有 JS 时被 disabled，无 JS 时全部提交，服务端是否对每种组合都给出可读 422。
