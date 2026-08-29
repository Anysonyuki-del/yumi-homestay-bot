# YuMi 民宿 AI

YuMi 民宿 AI 是面向武汉小型民宿的半人工、半智能客服及运营管理系统。系统让 AI 处理重复咨询和运营协同，让员工保留对价格、退款、投诉、提前入住等经营决策的控制权。

## 当前能力

- 企业微信微信客服消息接入、回调验签、异步收发和失败回执处理
- DeepSeek 客服问答、温暖的快速响应、七天客户上下文和安全的回复长度控制
- 百居易房态、房源和订单查询，支持自然日期表达和房间号优先展示
- 房源、FAQ、SOP 知识管理，以及知识缺口和高频问题 FAQ 草稿提醒
- 客诉识别、人工复核、草稿编辑、员工接管和审计记录
- 客户 CRM、标签、客户合并、生命周期提醒和运营任务中心
- 房间清洁、待检查、可入住、维修等状态及员工任务管理
- 武汉旅游联网推荐、近期日期校验和无链接的客人侧回复
- SQLite 本地运行、Alembic 迁移、PostgreSQL 部署支持和健康检查

## 技术栈

- Python 3.12+
- FastAPI、SQLAlchemy 2、Alembic、Pydantic Settings
- 企业微信微信客服 API
- 百居易（Hostex）OpenAPI
- DeepSeek Chat Completions API
- SQLite（本地）或 PostgreSQL（生产）

## 版本与运行状态

应用发布版本唯一来源是 [`pyproject.toml`](pyproject.toml) 的 `project.version`，运行时由 `src/homestay_bot/version.py::get_app_version()` 读取安装包元数据。数据库结构以 `alembic current` 为准；实际生效的配置 revision、组件健康和任务异常在管理员“系统诊断”页面查看。

版本更新内容见 [CHANGELOG](CHANGELOG.md)，发布流程、工程约束和变更检查见 [YuMi 民宿 AI 开发经验与防回归手册](YuMi民宿AI开发经验与防回归手册.md)。尚未归并的新教训记录在 [tasks/lessons.md](tasks/lessons.md)，[tasks/todo.md](tasks/todo.md) 只保存当前工作单。

## 本地运行

### 1. 准备环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

编辑 `.env`，填写企业微信、DeepSeek、百居易和会话安全配置。真实密钥只保存在本地环境变量或部署平台的密钥管理服务中，不要提交到 Git。

### 2. 初始化数据库

```bash
alembic upgrade head
```

默认本地数据库为项目目录下的 `homestay.db`。使用 PostgreSQL 时，在 `.env` 中设置完整的 `DATABASE_URL`。

### 3. 启动服务

```bash
uvicorn homestay_bot.main:app --host 127.0.0.1 --port 8010
```

健康检查：

```bash
curl http://127.0.0.1:8010/health
```

企业微信回调需要一个 HTTPS 公网地址，并将 `/callbacks/wecom` 配置到企业微信后台。员工后台入口为 `/employee/login`。

## 配置说明

完整配置模板和字段说明见 [`.env.example`](.env.example)。启动必需配置由 `src/homestay_bot/config.py::BootstrapSettings` 定义，可热切换的外部服务配置由 `src/homestay_bot/config.py::RuntimeEnvironmentSettings` 定义；README 不维护第二份字段清单。

生产环境必须替换示例值，并限制 `.env`、数据库、私有上传目录和备份目录的访问权限。真实密钥只能保存在环境变量或部署平台的密钥管理服务中。

## 测试与质量检查

```bash
pytest -q
ruff check .
mypy src
python -m pip check
python -m compileall -q src migrations tests
git diff --check
```

真实 DeepSeek、百居易和企业微信契约测试默认跳过。只有在明确准备好测试凭证和测试环境后，才设置 `RUN_LIVE_CONTRACT_TESTS=1` 执行它们。

## 数据库迁移

创建迁移：

```bash
alembic revision --autogenerate -m "describe change"
```

执行升级或回滚：

```bash
alembic upgrade head
alembic downgrade -1
```

部署启动脚本会先读取应用同一套配置、在 SQLite 迁移前备份数据库和私有上传目录，再执行迁移并启动服务。

## 项目结构

```text
src/homestay_bot/
  integrations/  外部 API、DeepSeek、企业微信和旅游联网适配
  repositories/  数据访问和幂等写入
  routes/        HTTP、回调和员工后台路由
  services/      客服、CRM、任务、知识和运营业务逻辑
  domain/        数据模型、枚举和领域结构
  worker.py      异步任务和出站消息处理
migrations/      Alembic 数据库迁移
tests/           单元、集成和外部契约测试
deploy/          本地 LaunchAgent 和启动迁移脚本
```

## 安全边界

- 不在代码、README、测试输出或 Git 历史中保存真实 API 密钥、企业微信密钥、门锁密码、Wi-Fi 密码和客户隐私信息。
- 客诉、退款、价格、改期和提前入住等高风险事项由员工确认，AI 不直接做经营决策。
- 客人侧旅游回复不展示外部链接；联网来源只用于后台校验和摘要。
- 非文本企业微信消息仅保留受控元数据，媒体下载和房源资料导入必须经过类型校验和人工审核。

## 授权

本项目使用 YuMi 民宿专有许可证，详见 [LICENSE](LICENSE)。
