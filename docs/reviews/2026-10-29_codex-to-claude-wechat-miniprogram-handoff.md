# 微信小程序接入交接：已完成、未完成与登录方案待审

日期：2026-10-29。工作根目录：`/Volumes/02/code/homestay-bot`。

**当前交付只有基础工程、连接测试按钮和登录 Spec 草案。微信登录尚未实现，一键入住与转人工按钮也未实现。请审查现有连接测试代码和登录设计，不要把计划中的接口、数据表或测试当成已完成代码。**

本轮用户要求“做了什么什么没做都写好，我要给 Claude 审查”。本交接不授予修复、提交、推送、部署、生产写入或真实外部调用权限；审查发现实质设计偏差时，先提出修订，再按项目 AGENTS 的 Spec 门禁推进。

## 1. 当前状态与确认边界

| 事项 | 状态 | 依据 |
| --- | --- | --- |
| 微信小程序申请、开发者工具安装 | 用户已完成 | 前轮用户操作和现场开发者工具观察 |
| 原生 JavaScript 基础工程 | 已创建并在开发者工具打开 | `clients/wechat-miniprogram/project.config.json` 的 `compileType`、`appid`；`project.private.config.json` 的 `projectname` |
| request 合法域名 | 用户截图已确认保存 `https://akros.icu` | 用户提供的服务器域名配置截图；本轮未重新读取公众平台 |
| 小程序到 YuMi 公网连接 | 前轮模拟器和手机均已验证收到响应 | 模拟器按钮结果；用户对手机同样显示 HTTP 503 的确认 |
| 连接测试按钮 | 已实现 | `pages/index/index.js::testConnection`、`index.wxml` 的 `bindtap`、`index.wxss` 的 `.connection-test/.connection-result` |
| 微信登录功能范围 | 已确认 | 用户对登录、恢复、退出、服务端验证及身份隔离范围回复“可以” |
| 登录风险与决策 | 待确认 | Spec 第三段已给出；其后用户要求先整理给 Claude 审查 |
| 登录编码 | 未开始 | 本轮源码复核见第 4 节；完整 Spec 后的明确开始门禁尚未满足 |

此前对话中的“开始”不能自动解释为批准后来新增的完整登录方案。现在应先审查草案，再确认风险决策及开始编码。依据：`AGENTS.md` 的“渐进式 Spec 与实施门禁”。

## 2. 已经做了什么

### 2.1 工程与域名接通

- 用户在开发者工具创建工程：`clients/wechat-miniprogram`，名称 `yumi-miniprogram`。本轮核对 `project.config.json` 的 AppID 为 `wx946f83d5f3269cbc`；AppID 是工程标识，不是 AppSecret。
- 曾出现工程目录选在父级的问题，已由用户重新创建到上述目录。后续开发以该目录为准，不在 `/Volumes/02/code` 父目录操作整个工程。
- 用户在公众平台保存了 request 合法域名 `https://akros.icu`。开发者工具最初持有旧域名信息，前轮通过项目配置刷新域名信息，再用菜单“工具 → 编译”后，测试请求成功到达服务器。
- 当前 `project.private.config.json::setting.urlCheck` 为 `true`。没有通过关闭合法域名校验完成测试。

### 2.2 本次实际增加的业务外代码

小程序工程由用户创建；下表列出 Codex 在模板基础上实际修改的三个文件，不能把整个未跟踪目录都认定为 Codex 新写的功能。

| 文件 / 符号 | 已实现内容 | 保留或限制 |
| --- | --- | --- |
| `clients/wechat-miniprogram/pages/index/index.js::Page.data/testConnection` | 增加测试状态、10 秒 GET `/health`、请求期间禁重复提交、成功/失败文案、完成后恢复重试 | 固定地址为 `https://akros.icu/health`；不发送客户资料、不调用业务接口 |
| `clients/wechat-miniprogram/pages/index/index.wxml` | 绑定“测试后端连接”按钮，绑定 loading/disabled 和结果文本 | 保留模板头像、昵称和 Hello World；结果文本使用 `user-select` |
| `clients/wechat-miniprogram/pages/index/index.wxss::.connection-test/.connection-result` | 增加测试区域间距和结果换行样式 | 没有做正式小程序产品视觉设计 |

`testConnection` 同时核对 HTTP 状态与 JSON `status`，不是收到响应就显示服务正常：

| 响应 / 故障 | 当前展示 |
| --- | --- |
| HTTP 200 且 `status=ok` | 连接成功，后端服务正常 |
| HTTP 503 且 `status=degraded` | 已连到 YuMi 后端，服务当前处于降级状态 |
| 其他 HTTP / 内容组合 | 服务器已响应，但健康检查结果异常 |
| timeout | 连接超时，请检查网络后重试 |
| 其他请求失败 | 请求失败，请检查网络和 request 合法域名后重试 |

没有添加后端 ping 接口；直接复用 `src/homestay_bot/routes/health.py::health/_health_status_code`。HTTP 503 的实际原因本轮没有诊断，不能沿用历史发布记录把它确定归因给某一组件。

### 2.3 登录调查与方案

已沿以下真实链路读取源码并形成设计依据；下面描述的是现状，不是已经实施的登录能力。

| 已核对位置 | 结论 / 设计用途 |
| --- | --- |
| `clients/wechat-miniprogram/app.js::App.onLaunch` | 模板调用 wx.login 后未消费 code；后续需替换这个空登录调用，避免与新入口重复 |
| `src/homestay_bot/domain/enums.py::CustomerIdentityProvider`、`domain/models.py::CustomerIdentity` | 已有渠道身份及唯一约束，尚无独立小程序身份渠道 |
| `src/homestay_bot/repositories/customers.py::SQLAlchemyCustomerRepository.ensure_identity/_find_by_identity` | 可复用保存点建档与并发唯一冲突处理，不能另建平行客户模型 |
| `src/homestay_bot/services/customer_service.py::CustomerService.ensure_for_message/suggest_merge/confirm_merge` | 微信客服建档与后台人工合并是既有能力，不能据小程序登录直接认定旧订单归属 |
| `src/homestay_bot/routes/employee_auth.py::require_employee_session` | 员工认证权限要保持独立；客人不能复用员工 Cookie 作为小程序权限 |
| `src/homestay_bot/config.py::BootstrapSettings`、`application.py::application_lifespan` | 确认可选配置与客户端装配、关闭位置 |
| `src/homestay_bot/middleware.py::NO_STORE_PATH_PREFIXES`、`logging.py::_SENSITIVE_QUERY_KEYS` | 新认证 API 需纳入禁止缓存；code、js_code、session_key 与 token 的日志泄漏路径需覆盖 |
| `compose.yaml::services.api.env_file`、`pyproject.toml::project.dependencies` | 已有环境注入和 httpx；暂不需要改 Compose 或加网络依赖 |

产物：[微信小程序基础登录 Spec](../specs/2026-10-29_wechat-miniprogram-login-spec.md)。该文档含接口草案、精确文件/符号计划、验证目标和回退边界；第三段仍标为待确认。

## 3. 验证了什么，证据能证明到哪里

| 验证 | 结果 | 证据性质与上限 |
| --- | --- | --- |
| 前轮 `node --check` 检查首页 JS | 通过 | 语法检查；没有证明微信运行环境或后端业务 |
| 前轮 Node vm + Page/wx.request 替身 | 通过：8 次请求场景，并检查重复点击、完成后重试 | 覆盖正常、降级、4 种异常响应、超时、网络失败；当时是内联探针，未保存为仓库测试文件或独立结果附件，本轮未重新执行 |
| 前轮开发者工具菜单编译及真实点击 | 收到 HTTP 503，页面明确显示已连通但降级 | 模拟器真实网络响应；不是登录验证 |
| 前轮用户手机预览 | 用户确认同样的 503 文案 | 真机连通性确认；没有验证微信 code 交换、客户绑定或业务操作 |
| 前轮公网只读检查 | `/health` 返回 503、`{"status":"degraded"}`；员工登录页返回 200 | 前轮网络观察，本轮未重新访问；登录页 200 不证明已登录后台可用 |
| 本轮文件 / Git 复核 | 与交接描述一致；见第 7 节摘要 | 证明当前本地源码和未提交状态；不证明服务器部署副本 |
| 文档检查 | Spec 新文件已做空白检查，本轮交接也做相同检查 | 仅文档变更，无需业务测试 |

已有证据仍覆盖前轮连接按钮；本轮仅写交接，不因交接重新跑全仓或真实外部测试。没有持久化的测试输出应由 Claude 明确视为会话记录，不能伪称可下载的完整测试报告。

公众平台浏览读取曾受到工具安全拦截，域名状态来自用户截图，没有绕过拦截。交接不含公众平台登录 URL 中的 token、Cookie、AppSecret 或任何会话凭证。

## 4. 明确没做什么

### 4.1 登录链路全部尚未实现

- 首页没有真正的微信登录、退出、恢复会话功能。`index.js::Page.data.hasUserInfo` 只由模板头像昵称逻辑维护，不能当认证状态。
- `app.js::App.onLaunch` 的 wx.login 成功回调仍是模板注释，未把 code 发到 YuMi。
- `main.py::app.include_router` 未注册小程序认证路由；`POST /api/miniprogram/login`、`GET /api/miniprogram/me`、`POST /api/miniprogram/logout` 都只是 Spec 中的拟议接口。
- `CustomerIdentityProvider` 未新增 `WECHAT_MINIPROGRAM`，`domain/models.py` 未新增 `MiniProgramSession`，`0036_miniprogram_sessions.py` 尚未创建。
- `integrations/wechat_miniprogram.py`、`repositories/miniprogram_sessions.py`、`services/miniprogram_auth.py`、`routes/miniprogram_auth.py`、客户端 `utils/auth.js` 均尚未创建。
- `BootstrapSettings` 和 `.env.example` 未增加 `MINIPROGRAM_APP_ID/MINIPROGRAM_APP_SECRET`；没有取得、保存或配置真实 AppSecret。
- 没有新增 token 摘要存储、24 小时到期、退出撤销、独立限频、上游请求或相应脱敏代码；这些都是方案。
- 没有写登录测试、执行迁移、修改数据库或验证真实微信登录。

注意：源码搜索会命中 `src/homestay_bot/services/customer_admin_service.py::_NON_TEXT_LABELS["miniprogram"]`。该项只是把聊天中的小程序类型消息显示为“[小程序]”，不是小程序身份、登录接口或本次实现。

### 4.2 产品功能与外部操作

- 一键入住、订单核验/关联、入住凭证、微信转人工按钮尚未设计落地，也没有接入原有 HumanSessionService。
- 没有开通或接入云开发/云托管，没有新增服务器或更换后端地址。
- 没有修改 YuMi 后端业务代码、回复提示词或现有员工认证；没有调用真实 DeepSeek、Hostex、企业微信，没有创建订单或发送真实客人/员工消息。
- 没有执行 git add、commit、push、打版本、部署、服务器重启、生产数据库迁移或生产数据写入。
- 前轮手机预览由用户操作；Codex 未代替用户执行正式上传、审核提交或小程序发布。
- 未修改 `tasks/todo.md`：当前仍处设计待确认阶段，尚未进入已批准的中等规模实施。也没有写入人工长期记忆。

## 5. 请 Claude 优先审查什么

本交接提出审查方向，不预设草案已正确，也不将以下问题当作已修复漏洞。

1. **现有连接按钮**：追踪 `index.wxml → testConnection → wx.request → health`；核对 503 与异常内容的区分、重复点击与失败恢复。当前按钮只是基础排障入口，是否保留在最终产品另议。
2. **身份与合并**：独立 AppID/OpenID 命名空间、复用 ensure_identity 是否足够；会话绑定 identity 后在人工合并、身份移动或删除时的精确行为；禁止自动绑定旧订单。
3. **会话设计**：随机 token 的摘要存储、24 小时固定到期、退出幂等、员工/客人隔离；是否需要明确每身份会话上限；有界清理的批量数和事务顺序仍需在实施前固定。
4. **失败与隐私**：退出网络失败、本机存储失败后撤销也失败、迟到响应、code 已消费而本地事务失败等情况，前端状态和残留会话如何诚实处理。输入长度、超时、响应体上限、限频和在途上限的具体数值应在完整 Spec 确认前补齐。
5. **微信外部契约**：直接官方微信登录文档未在前轮工具中完整取得。实现前应以可访问的微信官方资料核对 code2Session 请求/响应与错误契约；腾讯其他“小程序/超级应用”教程不能替代微信接口定义，离线 MockTransport 通过也不能证明真实契约已验收。
6. **既有系统兼容**：可选小程序配置无效时不能连带让 BootstrapSettings 失败、使既有系统只剩健康页；新增资源须覆盖启动异常、取消、关闭和 state 清理；新增迁移不得改写历史迁移。
7. **最小方案与验证范围**：是否有不必要的模块、配置或抽象；复用现有能力而不共享员工权限；验证应针对身份隔离、并发建档、到期撤销及实际装配，不用静态结构测试代替行为验收。

请按严重程度列问题，给出文件/符号、触发条件、实际影响和最小修订建议。分别标明“现有代码缺陷”“设计缺口”“待业务决定”“证据不足”。仅方案无阻断不等于实现、部署或真机登录验收完成。

## 6. 下一步与回退

预期顺序：Claude 审查 → 修订并确认完整 Spec → 用户明确“开始” → 增加 tasks/todo 跟踪 → 本地实现与相关验证 → 单独申请真实配置、部署和登录验收。

当前连接按钮回退仅涉及三个首页文件中的新增区域/状态/方法；整个小程序目录是用户创建的未跟踪工程，不能用删除目录作为回退。登录还没有实现或迁移，不存在登录数据回滚操作。登录实施后的会话表降级和客户身份保留取舍见 Spec“回退”，待审查。

## 7. 本地基线、差异与复查命令

本轮记录时 HEAD：`d1abcf5737d27f69550bfe42e54dd2bec9a56df2`，提交标题：`docs: 入库未完成事项分析与 Codex 审查（含修订）`。这不是本任务提交，也不证明 GitHub 或服务器与该提交一致。

`git diff --stat` 为空；**小程序整个目录、登录 Spec 和本交接都未跟踪**，因此不能仅凭默认 git diff 审查本次成果。工作区还包含 JEV 工具/夹具/测试、历史交接与设计审查等无关未跟踪文件，不能整目录暂存、删除或回退。受保护的 `YuMi民宿AI项目总结.txt` 未读取或处理。

以下摘要绑定本轮审查输入，不代表模板初始版本摘要：

| 文件 | SHA-256 |
| --- | --- |
| `clients/wechat-miniprogram/app.js` | `71458d1bbfe841529359fa50aeb5405d23a174e903e07d3c3eddf420f8c91fb8` |
| `clients/wechat-miniprogram/pages/index/index.js` | `a503463dd809093cbeda47ad593d5eedc4490d994090b9efe41b4eae3c2317d1` |
| `clients/wechat-miniprogram/pages/index/index.wxml` | `fe23860e7545fc9fe69783fb551b17d9992f4438df51b315229df2d40251740e` |
| `clients/wechat-miniprogram/pages/index/index.wxss` | `d07be24b89197b4c988a025528636cf9f474b049e0f6975a9f4ec7c31a15ec88` |
| `clients/wechat-miniprogram/project.config.json` | `b6fe7d837affef0c94e2fa00ef3162bbb3523af7b6ec4ff1708394fe7cbf418a` |
| `docs/specs/2026-10-29_wechat-miniprogram-login-spec.md` | `9a4ec2245eaf9a81e1a2b3932c1bd4450f56a6026004328546265fa441eb2a77` |

Claude 可从仓库根执行以下只读复查。不要递归读取 `.env`、私有运行数据、公众平台会话信息或受保护总结。

```bash
git rev-parse HEAD
git status --short
git diff --stat
sed -n '1,120p' clients/wechat-miniprogram/pages/index/index.js
cat clients/wechat-miniprogram/app.js
cat docs/specs/2026-10-29_wechat-miniprogram-login-spec.md
rg -n 'WECHAT_MINIPROGRAM|MINIPROGRAM_APP|api/miniprogram|MiniProgramSession' src/homestay_bot migrations .env.example
shasum -a 256 clients/wechat-miniprogram/pages/index/index.js docs/specs/2026-10-29_wechat-miniprogram-login-spec.md
```

最后一条 rg 在当前基线无匹配是预期结果；不能据此单独推断不存在形态不同的实现，应结合本交接定位的装配、身份枚举与 App 回调核查。

## 8. AOCI 状态与证据链

前轮完整 Overview 已加载并完成交付及严格 Challenge 验证，但正式索引仍为 Dirty/Stale，不能称当前完整系统认知可靠。收尾 Maintain 被既有 `observed_evidence_review_required` 阻挡：4 项观察文件待审核，包含无关测试。没有进行批量 acknowledge，没有自动改 Scope 或修订与本任务无关的索引内容；正式索引未改。AOCI 不一致不等于业务实现失败，也不能以旧索引替代当前源码审查。

### 证据 → 结论 → 调用路径

| 证据 | 可得结论 | 路径 / 下一步 |
| --- | --- | --- |
| E1：本轮三个首页文件源码及上述摘要 | 连接按钮已实现；只调用公开健康接口 | `index.wxml::bindtap → index.js::testConnection → wx.request → routes/health.py::health` |
| E2：前轮模拟器真实响应、用户手机结果确认 | 当时网络接通且服务降级；不能推出业务或登录成功 | 需要真实登录验收时另行授权并单独记录 |
| E3：本轮 `app.js::onLaunch`、身份枚举、主路由核查 | 当前没有完成 YuMi 小程序登录链路 | 审查 Spec 的身份、会话、装配与失败边界 |
| E4：Spec 文件与确认对话 | 功能范围已确认，风险决策和开始实施尚未确认 | 审查后修订、确认，再编码 |
| E5：本轮 Git 状态与 HEAD | 本地成果未提交；默认 diff 不包含未跟踪成果 | 逐文件读实际内容；不触碰无关工作区内容 |

交接只陈述这些证据实际覆盖的事实，没有宣称登录通过、服务全面健康、已上线或正式小程序已发布。
