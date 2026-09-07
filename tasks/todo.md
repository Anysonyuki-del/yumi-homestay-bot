# 当前任务：前端审查修复（v1.10.1 + v1.11.0）已发布，等待剩余登录后验收

上一张工作单（后台表单 CSRF 令牌迁移，方案 B）已全部完成并随 v1.4.0 上线，归档在提交 `9577dc6`。

## 目标

修复一轮前端审查发现的四项误操作风险和五项表达不一致，分两个版本发布：误操作风险与表达一致性风险等级不同、可分别交付，因此不合并成一次提交。

## 缺陷证据

四项 P1 均在代码中复核属实，不是观感问题：

- 任务列表的桌面表格与手机卡片各渲染一份 `name="task_ids"`，两份始终都在 DOM 里。`data-select-all` 用 `form.querySelectorAll` 无差别勾选两份，提交编号翻倍；取消可见那份时隐藏副本仍带着该任务进入归档或永久删除。`data-typed-confirm` 的条数同样按两套布局统计。
- 永久删除按钮借用归档表单提交，表单级 `data-confirm` 写的是「可在「已归档」中恢复」，且在专属确认之后弹出。
- `archive-filtered` 表单只提交 `status_filter / task_type / property_id`，路由签名也只接这三项，`service_date` 与 `assigned_employee_id` 被丢弃；`archive_matching` 表达不了 `overdue`。
- `tasks/detail.html` 的分派表单 `<option>` 无 `selected`，浏览器默认选中第一项。
- `.responsive-table` 手机端整体隐藏，而 `customers/detail.html` 的住宿记录与 `admin/operations.html` 的紧凑总览没有 `.mobile-card-list` 兄弟节点。

## 已实施

### v1.10.1 · 批量操作按用户看见的范围执行

- `admin.js` 新增 `syncMirroredSelection()`：按任务编号分组同名勾选框，只保留当前可见的那一份可提交，其余同步状态并 `disabled`。禁用控件不进入 `FormData`，脚本不可用时同样只提交可见的选择。全选、`indeterminate` 判定与条数统计一律只看 `:not(:disabled)`，条数再按 value 去重。
- 表单级 `data-confirm` 在 `event.submitter` 带 `data-typed-confirm` 时跳过。
- `archive-filtered` 表单补 `service_date` 与 `assigned_employee_id` 隐藏字段，路由补对应参数；`overdue` 生效时不渲染入口，服务端收到该条件直接 `OperationRefused`。按钮上方新增服务端渲染的归档范围说明。
- 分派表单按 `task.property_id` / `task.assigned_employee_id` 回填 `selected`，并加 `<option value="">` 占位项。
- 两处表格改用 `.table-scroll`，`app.css` 补注释说明 `.responsive-table` 的前提条件。

### v1.11.0 · 队列与附加筛选分层、房态时间轴

- 队列判定收敛到 `routes/tasks.py::_current_queue`（归档 > 逾期 > 状态 > 开放）与 `_extra_filters`，高亮、标题、清除入口都从同一个答案出发；队列元数据收进 `_TaskQueue` 数据类，管理员与员工两套称呼各自成立。
- 高级筛选的展开条件由 `filters.active` 改为 `extra_filters`；状态占位项由「全部开放状态」改为「不限状态」。
- `.room-timeline` 基础规则改为 `grid-auto-columns: minmax(76px, 1fr)`，此前只有 `max-width: 520px` 断点设了下限，桌面档漏在外面；跨度超过 7 天时房间卡片改整行；时间轴加 `tabindex="0"`。
- 新增 `ui.html::room_timeline` 宏，同步过期时空日子写「无记录」并在时间轴上方标注是上次同步记录；同步可信时「空闲」仍是事实陈述，不降级。
- 稳定房间展开后展示开放任务、下次入住退房与时间轴，所需数据本就在快照里。
- `operational_status` 的三套颜色映射收敛到 `ui.html::readiness_status_badge`；宏内用 `status | string` 取键，对枚举与裸字符串都成立。

## 验收门禁

- [x] 25 条新增测试逐条先在未修复代码上确认变红
- [x] 两个提交各自独立跑通全量测试，中间状态自洽（1391 / 1406）
- [x] `ruff` 与 `mypy` 120 文件通过
- [x] 拆分后的两个提交合起来与拆分前的工作区逐行一致（`diff` 结果 IDENTICAL）
- [x] 时间轴宽度用 Playwright 量真实渲染尺寸，不断言 CSS 字符串
- [x] 生产实测 11 项，见下

## 发布 v1.10.1 与 v1.11.0

- [x] 合并前确认 CI 不含部署步骤（唯一工作流 `validate`，权限 `contents: read`）
- [x] rebase 合并保留两个提交，两个版本号不被压成一个
- [x] 补 annotated 标签 `v1.10.1`、`v1.11.0` 并推送
- [x] 起飞前检查通过（`./run-deploy.command --check`）
- [x] 仅重建并替换 API，保持 PostgreSQL 运行
- [x] 独立复核公网版本与实际下发的 CSS
- [x] 登录后生产验收 11 项
- [ ] 剩余 3 项验收，需生产出现对应数据状态

### v1.11.0 生产发布 Review

- 本地源码：`main` = `1be527d561026126e62910a2339fbbbbd948937d`，工作区干净，`pyproject` 版本 `1.11.0`。
- GitHub：`refs/heads/main` 与 `refs/tags/v1.11.0`（标签对象 `7ddde54`）均解析到该提交；`refs/tags/v1.10.1`（标签对象 `ceaf9fd`）解析到 `5782e5a`。CI `34154876914`（main）、`34155060972`（v1.10.1）、`34155061084`（v1.11.0）与 `34154282590`（PR #1）全部成功。CI 装了 Playwright chromium，本轮新增的浏览器测试在 CI 里真跑了。
- 服务器源码：由 `31a8cf5` 快进到 `1be527d` 并断言 HEAD 相符；发布包 SHA-256 `901142117313319c…` 服务器端比对一致后才快进。三个既存未跟踪环境备份保持不变。
- 运行容器：新 API 容器 `a7f385a4`、新镜像 `f6d60e4a`，`status=running restarts=0`，运行用户 `uid=10001(app) gid=10001(app)`。容器包版本 `1.11.0`。
- 数据库：PostgreSQL 未重建也未重启，容器 `af11bb4a`、启动时间 `2026-08-10T16:43:53.783849747Z`、`restarts=0` 与部署前一致。`alembic current` 为 `0024_business_task_archive (head)`，本版本无迁移，启动时的 `alembic upgrade head` 是空操作。
- 健康与公网：本机与公网 `/health` 均为 `ok`，两侧 OpenAPI 均为 `1.11.0`；`/employee/login` 返回 200 且带 `no-store`、`frame-ancestors 'none'`、`DENY`、`no-referrer`、`nosniff`；未登录访问 `/employee/approvals` 返回 401。私有上传仍挂载 `/opt/yumi-data/private_uploads -> /app/data/private_uploads`，文件数 1。部署后日志异常计数为 0。
- 独立复核（不采信脚本自述）：部署前实测公网 OpenAPI 为 `1.10.0`、`app.css` 基础规则仍是 `minmax(0, 1fr)`、本轮新增类命中 0；部署后公网 `app.css` 基础规则为 `minmax(76px, 1fr)`、四组新增类全部命中、登录页引用 `app.css?v=1.11.0`。剥掉全部 `@media` 块后确认 `76px` 位于基础规则而非某个断点内——原缺陷正是「只有窄断点有下限」这一形态，必须排除重蹈。

### 本次未做生产备份

`.stage/remote.sh` 没有备份步骤（其中「三个既有未跟踪环境备份」只是列举服务器上已存在的 `.env.*`，不创建任何东西），v1.4.0 那次的 `/opt/yumi-backups/` 是人手做的。本次部署前未创建备份，与 `AGENTS.md` 「生产变更先做可恢复备份」不符，用户在事后知情的情况下决定不补做。

实际敞口：数据库全程未被触碰（容器 ID、启动时间、重启次数与部署前一致，无迁移执行），源码在本地、GitHub 与服务器上均有完整历史及 `v1.10.0` 标签。不可直接恢复的只有被覆盖的旧 API 镜像，可由 `v1.10.0` 标签重建。回滚成本是一次重建，不是数据丢失。

### 登录后生产验收（11 项通过）

全程只读：未提交任何表单，未执行归档、删除或分派。判据取自 DOM 与「表单实际会提交什么」。

- 勾选镜像：`?status_filter=pending_confirmation` 页 DOM 内 12 个勾选框、6 个镜像副本已被禁用；全选提交 6 条且无重复 ID；取消任务 `260` 后提交 5 条且该编号不在其中；全选框呈不确定态。
- 断点切换：桌面选中 `260, 525` → 390px 后仍为 `260, 525`，可见且启用的那份换成手机卡片，禁用数仍为 6。
- 归档范围：筛选状态、日期、房源、员工后，表单 `FormData` 实际包含 `service_date=2026-08-12` 与 `assigned_employee_id=1`；范围行与确认文案均列全四个条件。
- 逾期队列不渲染归档入口。
- 分派回填：任务 `260` 房间回填为《春和景明》（8 个选项，浏览器默认即选中它）；任务 `465` 缺房间时停在「请选择房间…」。
- 手机端：390px 下客户住宿记录与运营页紧凑总览均使用 `.table-scroll` 且内容可见可横向滚动。
- 队列层级：`?archived=true&status_filter=pending_confirmation` 仅「已归档」高亮，标题「已归档」，页面标题「已归档任务」，状态占位项「不限状态」；仅点队列时高级筛选保持收起且无摘要，加四个条件后摘要写明「又加了 4 项筛选」。
- 时间轴：`?days=14` 下 17 个日期格、最窄 `76px`、`0` 个标签溢出、整行模式生效、可横向滚动、全部时间轴 `tabindex="0"`。对照实验用同一份生产 CSS 仅把下限改回 `minmax(0, 1fr)`，17 个格子全部溢出、最窄 `28px`，而「退房 1」标签宽 `47.1px`。
- 徽标一致：房源列表桌面 7 个、手机 7 个，类名与文字逐项一致；表头已由「房态」改为「运营准备」。

### 剩余 3 项验收（受阻于生产数据，非代码问题）

- 永久删除的确认文案：生产当前没有任何已归档任务，删除按钮不渲染。
- 同步过期时的「无记录」标记：当前同步正常，实测 84 个「空闲」、0 个「无记录」，这正是同步可信分支的正确表现，过期分支观察不到。
- 稳定房间展开内容与维修/可入住色调：当前 7 间房今天都需处理，无稳定房间；也没有维修或可入住状态的房间。

三项均有测试覆盖，但测试不替代真实环境验收，因此记为未验而非通过。待生产出现对应状态后补验。

## 边界

- 不改权限判断、状态机、归档语义与保留期，不改任务生成规则。
- 不新增数据库迁移、依赖、动画、图表、字体依赖或组件库。
- 队列判定只决定高亮、标题与展开行为，不改列表筛选结果。
