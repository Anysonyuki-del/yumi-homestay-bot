# 当前任务：收敛文档事实归属与系统诊断输出

- [x] 第一段：审查文档职责、重复事实、易过期状态和诊断信息层级
- [x] 第二段：确认 README、长期手册、Lessons、Todo 的唯一职责
- [x] 第三段：确认 diagnostics 的有效状态、失败语义与展示层级
- [x] HARD-GATE：用户确认完整 Spec 后再编码
- [x] 实施最小范围修复并同步全部用户可见文案入口
- [x] 运行文档契约、诊断定向测试和全量验证

## 现状依据

- `README.md` 手写当前版本和配置字段，同时声明版本唯一来源为 `pyproject.toml::project.version`；长期手册 frontmatter 也维护第二份版本。
- `tasks/lessons.md` 与长期手册重复保存规则；长期手册的维护规则同时要求“双写”和“单一权威”，两者冲突。
- `tasks/todo.md` 同时承载多个当前任务、完整 Spec、实施过程和历史部署证据，无法可靠表达当前状态。
- `AdminDiagnosticsService.snapshot()` 在探针失败时把任务计数与错误码降为空集合，模板会把读取失败显示成“无”。
- 诊断首屏重复展示全部组件、任务和复制报告，没有突出异常差异。

## 文档唯一归属

- `README.md` 只负责项目定位、最短启动路径和权威入口，不保存当前版本、迁移号、运行 revision 或配置字段复本。
- `YuMi民宿AI开发经验与防回归手册.md` 只保留长期工程契约、代码符号入口和变更检查清单，不保存讨论过程、批次说明或运行状态。
- `tasks/lessons.md` 只保存尚未归并的新教训；归并后删除正文，由 Git 保留历史。
- `tasks/todo.md` 只保留唯一当前工作单和本次 Review；完成记录由 Git 历史保存。
- 带日期的 `docs/superpowers/specs` 与 `docs/superpowers/plans` 保持原文，不作为当前产品说明。
- `tests/unit/test_version.py` 只验证版本格式和运行时读取契约，不硬编码当前发布号。

## Diagnostics 行为

- 健康、任务计数、错误码和配置 revision 独立探测；单项失败不清空其他成功数据。
- 空集合表示读取成功且没有数据，`None` 表示无法读取；页面和完整报告不得混淆两者。
- registry 可读时展示实际生效 revision；否则回退数据库记录并标记“未确认已生效”。
- 首屏只展示异常、未配置、积压和失败；完整脱敏报告折叠保留。
- `/employee/health` 的机器 JSON、字段名和状态码保持不变。

## 文件计划

- `src/homestay_bot/services/admin_diagnostics_service.py`
- `src/homestay_bot/routes/admin.py::admin_diagnostics()`
- `src/homestay_bot/templates/admin/diagnostics.html`
- `src/homestay_bot/static/app.css`
- `tests/unit/test_admin_diagnostics_service.py`
- `tests/integration/test_admin_dashboard_routes.py`
- `README.md`
- `YuMi民宿AI开发经验与防回归手册.md`
- `tasks/lessons.md`
- `tasks/todo.md`
- `tests/unit/test_version.py`
- `pyproject.toml`

## 验收要求

- 文档链接和 `文件::符号` 可解析，长期文档不包含当前发布号、部署状态或多个当前任务。
- 诊断定向测试覆盖部分探针失败、数据库 revision 回退、真假空值、差异展示和敏感信息缺席。
- 运行完整测试、Ruff、mypy、compileall、依赖、JavaScript、模板静态资源和 diff 检查。
- 敏感未跟踪文件不得读取、暂存或提交。

## Review

- 长期文档已经按唯一事实归属收敛：README 只保留入口，手册只保留稳定契约，Lessons 清空已归并内容，Todo 只保留当前工作单。
- Diagnostics 的健康、任务计数、错误码与配置 revision 已改为独立探测；读取失败、成功但为空、数据库回退三种状态不再混淆。
- 管理员诊断页首屏只显示异常差异，完整脱敏报告折叠保留；机器健康接口未改动。
- 本地验收：`1124 passed, 15 skipped`；Ruff、mypy（108 个源文件）、compileall、pip check、JavaScript 语法和 diff 检查均通过。
- 构建验收：wheel 文件名和 METADATA 均确认版本为 `1.0.3`。
- 发布：功能提交 `1f5d995` 与 `v1.0.3` 标签已推送并部署；部署前备份位于 `/opt/yumi-backups/v1.0.3-20260824T195148Z`，数据库备份已通过 `pg_restore` 目录校验。
- 云端验收：仓库和容器版本均为 `1.0.3`，本机与公网健康状态均为 `ok`，Alembic 为 `0018_stay_checkout_observation (head)`，诊断模板已更新且未登录访问返回 401。
- 运行状态：PostgreSQL 保持 healthy，API 重启次数和近 10 分钟异常关键词计数均为 0；三个既有未跟踪环境备份文件保持不变。
