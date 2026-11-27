# 1.77.0 知识库重构收尾、部署与 CI 修复｜Gemini → Claude 审查交接

2026-11-25 · 基线 `main` = `origin/main` = `9b0518a`（生产部署标签 `v1.77.0` = `8a0daa1`）· 本文未入库

## 一句话

你按终版 Spec 在本地完成的知识库页面重构，我独立验收（0 P1）后，按用户指令「全部做完吧」接手完成了全部收尾：补修两处验收 P3、完成 AOCI 全量对齐、升级 `1.77.0` 并补齐更新日志与发布记录、提交打标推送、通过干净副本部署上线并完成部署后独立核对，随后又定位修复了 `v1.77.0` 推送后在 Linux CI 上必现的一条无脚本浏览器测试超时。请审查**我追加的全部代码/测试/文档/AOCI 改动与发布核对结论**是否存在疏漏、边界回归或表述偏差。

- **基线提交**：`fa0ac38`（`v1.76.1`）。
- **本次提交链**（均已推送 `origin/main`）：
  1. `8a0daa1`（标签 `v1.77.0`，已部署生产）：你的知识库重构实现 + 我加的两处 P3 修复与回归测试 + 规格/评审/发布文档 + `pyproject.toml` / `CHANGELOG.md` + AOCI 对齐（31 files changed, +3701 / -228）。
  2. `ef71243`：将生产部署结果与独立核对事实补记入 `docs/releases/1.77.0.md`，并同步 AOCI 基线（3 files changed, +23 / -9）。
  3. `9b0518a`：修复 `tests/browser/test_knowledge_workbench.py` 在 Linux CI 上 `java_script_enabled=False` 提交按钮滚动超时（1 file changed, +4；仅测试文件，未改动生产代码，未重新部署）。
- **完整发布记录**：`docs/releases/1.77.0.md`。
- **工作区保留项**：`.impeccable/config.json`、`.impeccable/surfaces/`、`PRODUCT.md` 为用户既有未提交改动，全程未触碰、未混入任何提交。

## 审查规则

- **只读审查**，不要改代码、不要提交。审查意见写成报告交给用户转回，建议路径 `docs/reviews/2026-11-25_claude-to-gemini-1.77.0-delivery-review.md`。
- **不要碰生产**：不要登录、不要提交后台表单、不要调用真实 DeepSeek / Hostex / 企业微信。
- 每条发现请写明：文件路径 + 函数/符号/行号、复现条件、原因与严重程度（P1 会出错 / P2 体验或维护问题 / P3 建议）；拿不准的标成「疑问」。
- 你的原始实施和我的两处 P3 修复合并在 `8a0daa1` 里，下面第二节明确列出**仅属于我追加的改动**，请优先聚焦核查。

## 一、我验收确认无问题的你的实施部分

列出已核对项，避免重复盘点：

- Spec §0 四条硬边界全部满足：零表结构迁移（`domain/models.py` 与 `migrations/versions/` 零改动）；`REPLY_PATHS` 交集为空；保留 `.impeccable/` 与 `PRODUCT.md`；独立范围色令牌未借用主操作色，CSP 与安全头未放宽。
- `PrivateFileStorage.copy_image`、`KnowledgeAdminService` 英文不变式 `_validate_active_english`、多房同事务 `create_for_rooms`、快捷定界 `set_scope`、`SessionKnowledgeAdminService.copy_to_rooms` 四步状态机及提交失败不删文件策略，均与终版 Spec 及用户裁决（P1=B、配图上限 4 张）一致。
- 路由注册顺序（`GET /new` 在 `GET /{entry_id}` 前；`delete/copy/scope` 在 `/{action}` 前）与无 JS 降级逻辑核对通过。

## 二、我追加的改动清单（请重点审）

| # | 所属提交 | 改动位置 | 原因与改法 |
| --- | --- | --- | --- |
| 1 | `8a0daa1` | `src/homestay_bot/routes/knowledge.py::_render_detail`（L1991–2002、L2025）、`src/homestay_bot/templates/knowledge/detail.html`（原 L12 移除硬编码 `status_return`） | **验收 P3-1**：原模板把启停/快捷定界表单的 `return_to` 写死为 `/employee/knowledge/{id}#entry-status`，导致从房间目录或筛选结果进详情页切状态后，「返回知识列表」按钮退回默认 `/employee/knowledge`。改为后端计算 `status_return`：当 `safe_return != "/employee/knowledge"` 时拼装 `/employee/knowledge/{id}?return_to=<urlencoded>#entry-status`，但加了 `len(candidate_return) <= 200` 保护（匹配 `toggle_knowledge`/`set_knowledge_scope` 的 `Form(max_length=200)`），超限时回退为不内嵌列表路径，避免长中文搜索词触发 FastAPI 422。 |
| 2 | `8a0daa1` | `tests/integration/test_knowledge_workspace.py::test_detail_status_toggle_preserves_list_return_to_within_limit`（L629–667） | 覆盖 P3-1 两条路径：① 从 `?section=rooms&room=1` 进详情并停用后，重定向目标与详情页「返回知识列表」仍保持 `/employee/knowledge?section=rooms&room=1`；② 构造超长 `return_to`（编码后 >200 字符）时安全回退为 `/employee/knowledge/1#entry-status` 且提交返回 303 不报 422。 |
| 3 | `8a0daa1` | `src/homestay_bot/templates/knowledge/index.html`（L55） | **验收 P3-2**：顶部待审告警条文案由「逐条确认是全店通用还是店外周边后才会生效」补全为「逐条确认是全店通用、店外周边还是指定房间后才会生效」。 |
| 4 | `8a0daa1`、`ef71243` | `pyproject.toml`（`1.76.1` → `1.77.0`）、`CHANGELOG.md`、`docs/reviews/README.md`、`docs/releases/1.77.0.md` | 正式发版版本号、更新日志、审查目录登记及包含改动凭据、验证证据、生产库预检、部署备份与独立核对的发布变更记录。 |
| 5 | `8a0daa1`、`ef71243` | `aoci.code.txt`、`.aoci/baseline.json`（先经 `aoci scope acknowledge` 确认观测文件变更） | 补齐 13 条变更/新增受管文件条目（含此前滞后的知识库模板、`routes/knowledge.py`、版本与发布文档），部署补记后再对齐 `docs/releases/1.77.0.md`，两次 `aoci_update_entry` 均为 `aligned: true`、`finding_count: 0`。 |
| 6 | `9b0518a` | `tests/browser/test_knowledge_workbench.py::_Probe.__init__`（L171–179） | **CI 失败修复**：`8a0daa1`（`main` 与 `v1.77.0`）及 `ef71243` 三趟 GitHub Actions CI 均 100% 失败在 `test_new_page_without_javascript_shows_all_fields_and_server_rejects_mixups` 第 340 行 `page.get_by_role("button", name="保存知识").click()`（`Timeout 30000ms exceeded: element is not stable -> element was detached from the DOM, retrying`）。根因复现见下节，修法是在 `_Probe` 上下文增加 `reduced_motion="reduce"`。 |

### 第 6 项 CI 失败的复现与定位证据

- **现象**：macOS 本机 `h=900` 下该用例 `0.11s` 通过；GitHub Actions Ubuntu（无中文字体，fallback 行高更大）下 3 趟 CI 100% 稳定在「保存知识」按钮超时 30s。
- **本机复现**：固定 `java_script_enabled=False`，分别用 `viewport.height = 500 / 600 / 700 / 900` 运行同一流程：
  - `h=600 / 700 / 900`：`OK`（`0.11s–0.19s`，按钮在视口内或无需长距离滚动）；
  - `h=500`（按钮落到视口下方，强制触发 `scrollIntoView`）：`FAILED in 5.42s`，报错日志与 Linux CI 完全一致（`2 × waiting for element to be visible, enabled and stable -> element is not stable -> retrying click action`）。
- **根因**：`src/homestay_bot/static/app.css` 第 105 行全局开启 `html { scroll-behavior: smooth; }`，第 658 行在 `@media (prefers-reduced-motion: reduce)` 下回落为 `scroll-behavior: auto`。在无脚本模式下表单全展开高度增大，点击底部按钮触发平滑滚动时，Playwright 的连续帧稳定性检测与平滑滚动竞态导致超时。
- **验证**：保持 `h=500`、`java_script_enabled=False` 不变，仅加 `reduced_motion="reduce"` 后由 `FAILED (5.42s)` 变为 `OK (0.52s)`；完整跑 `tests/browser/test_knowledge_workbench.py` **7 passed (13.14s)**。

## 三、请你特别判断的 5 个点

1. **P3-1 `status_return` 的 200 字符截断边界**（`routes/knowledge.py::_render_detail` L1991–2002）
   - 当 `return_to` 为普通分区/房间路径（如 `/employee/knowledge?section=rooms&room=1`）时，编码后约 80 字符，完整保留返回路径；当 `return_to` 含超长中文搜索词（`urlencode` 后整串 > 200 字符）时，静默回退为 `/employee/knowledge/{id}#entry-status` 而不内嵌，保证提交不会 422。
   - 请核查：`safe_return` 二次 URL 编码进 `status_return` 后，经 `toggle_knowledge` / `set_knowledge_scope` 的 `safe_return_path(...)` 校验、303 重定向回到 `GET /employee/knowledge/{id}?return_to=...#entry-status`，再被 `knowledge_detail` 取出并传给 `safe_return_path` 时，是否存在双编码或嵌套越界问题？
2. **详情页其他写操作的一致性**（`detail.html`）
   - 本次只把 `status_return` 用于 `#entry-status` 里的启停与快捷定界表单；配图上传/排序/删除（`_images_anchor(entry_id)`）沿用你原来的行为（未内嵌原列表 `return_to`），跨房复制（`copy_knowledge`）本来已在服务端拼 `?return_to=`。请你判断是否可接受，或配图路由后续是否也应统一保留列表 `return_to`。
3. **CI 修复选择 `reduced_motion="reduce"` 是否合适**（`tests/browser/test_knowledge_workbench.py::_Probe.__init__`）
   - 我选择改测试上下文偏好，而不改生产 `app.css` 的 `scroll-behavior: smooth`。
   - 请你评估：这是否可能掩盖某个需要测动画/滚动的用例（当前 7 条用例都不测平滑滚动或过渡动画），以及在真实无脚本浏览器里点击底部提交按钮是否会受 `scroll-behavior: smooth` 影响（我判断真实用户是先手动滚到底部再点，不会触发程序 `scrollIntoView` + rAF 稳定性检测，风险仅在自动化测试层）。
4. **发布记录与 AOCI 条目准确性**（`docs/releases/1.77.0.md`、`CHANGELOG.md`、`aoci.code.txt`）
   - 请核对 `docs/releases/1.77.0.md` 的改动清单、验证数字、生产库预检数据、部署记录与未覆盖说明是否与实际提交和日志一致，有无夸张表述或遗漏脱敏项。
5. **生产部署与 `9b0518a` 的关系**
   - 生产部署的精确提交是 `8a0daa1`（`v1.77.0`），后续 `ef71243` 仅改文档与 AOCI，`9b0518a` 仅改浏览器测试文件，均不触及 `src/` 生产代码，因此未再次触发生产构建部署。请确认这个判断符合项目发布规范。

## 四、验证证据汇总

- **静态检查**（最终源码含 `1.77.0` 与两处 P3）：`ruff check .`、`mypy src`（152 个文件 0 问题）、`node --check src/homestay_bot/static/admin.js`、`git diff --check` 全部通过；`9b0518a` 后单独复跑 `ruff check tests/browser/test_knowledge_workbench.py` 通过。
- **定向与浏览器验证**
  - P3 修复后：`tests/integration/test_knowledge_workspace.py`、`test_knowledge_routes.py`、`tests/browser/test_knowledge_workbench.py` **78 passed**。
  - `9b0518a` 后：`tests/browser/test_knowledge_workbench.py` **7 passed (13.14s)**，且 `h=500` 无脚本专项探针 `OK (0.52s)`。
- **本地全量离线测试 + 隔离 PostgreSQL 16 专项**（`8a0daa1` 最终代码）
  - 非 PG 全量离线用例全部通过（全量运行中 3812 passed、16 skipped；首次运行因临时 PG 空库未先执行 `alembic upgrade head` 导致 83 条依赖建表的 PG 用例报错；16 项跳过为 15 项外部契约测试与 1 项本机无 Docker Compose 的配置用例）。
  - 临时 PostgreSQL 16 执行 `alembic upgrade head`（`0001_initial` → `0038_miniprogram_home`）后运行 `tests/integration/test_*_postgresql.py`：**92 passed (13.85s)**（含 `test_knowledge_delete_postgresql.py` 全部 11 条并发删除与跨房复制竞争用例）。
  - 合并覆盖最终源码全部 **3895** 条离线与 PG 用例（较交接时 3894 条新增 1 条 P3-1 回归）。
- **GitHub Actions CI 状态**
  - `8a0daa1`（run `45645014854` / `45645015524`）与 `ef71243`（run `45646230525`）：均仅失败于上述无脚本浏览器用例（其余 3812 条全过）。
  - `9b0518a`（run `45647746279`）：**全部通过（`conclusion: success`）**——静态检查、全新数据库迁移、无外联测试、隔离 PostgreSQL 迁移、隔离 PostgreSQL 回归与构建生产镜像全步绿灯。
- **生产上线前只读预检**：`knowledge_entries` 分布为 `global`（启用）3 条、`property`（启用）43 条、`unreviewed`（停用）37 条，「已启用、非 `unreviewed` 且英文为空」为 **0 条**。
- **生产备份、部署与独立核对**
  - 通过干净克隆副本运行 `./run-deploy.command`（保留主工作区未提交的 `.impeccable/` 与 `PRODUCT.md`），输出 `REPLY_GATE_SKIPPED` 与 `DEPLOY_OK`。
  - 备份：47 张表、447 个归档对象、1,078,690 字节，落盘 `pg_restore --list` 与 `--file=/dev/null` 复验通过；只重建 API 容器，PostgreSQL 未动（`restarts=0`）。
  - 独立核对：服务器源码 `HEAD=8a0daa1f5`（精确匹配 `v1.77.0`），API 容器 `running`、`restarts=0`、非 root、包版本 `1.77.0`、Alembic `0038_miniprogram_home (head)`、近 10 分钟错误日志 `0` 行；公网 `/health` 为 `{"status":"degraded"}`（同基线）、`/openapi.json` 版本 `1.77.0`、登录页安全头齐全且引用 `app.css?v=1.77.0`；未登录 `/employee/knowledge` 与 `/employee/knowledge/new` 返回 `401`；公网 `app.css?v=1.77.0` 含 `.kb-tabs` 及 `--scope-{global,room,public}-*`，`admin.js?v=1.77.0` 含 `data-kb-scope-form` 与 `data-kb-select-all`。

本地复跑命令（不碰外部服务）：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/integration/test_knowledge_workspace.py \
  tests/integration/test_knowledge_routes.py \
  tests/browser/test_knowledge_workbench.py
```

## 五、没有覆盖的地方

1. **生产登录后页面目视验收**：自动化浏览器未登录生产管理员会话，访问生产知识库跳转到登录页后按规则立即停止，未执行登录或任何写操作；需用户在已登录的本机浏览器中只读确认生产页面（预期顶部会显示 37 条待审「去集中定界」横幅）。
2. **跨浏览器与真机**：仅在 Chromium（1440 / 390 及 500px 复现视口）跑过，未在真实 iOS Safari、Android WebView、Windows WebView2 或读屏软件上实测。
3. **测试号真实收发与真实模型门禁**：未触及 `REPLY_PATHS` 与客人消息链路，按项目规则跳过。
4. **跨进程配图变更并发**：PostgreSQL 竞争用例覆盖「复制 vs 删除源条目」；「复制 vs 配图排序/删除」交错由 SQLite 事务快照比对用例覆盖语义。
