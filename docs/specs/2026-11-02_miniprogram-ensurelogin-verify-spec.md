# 小程序 ensureLogin 服务端核验与退出残留清理 Spec

状态：用户 2026-11-02 回复「开始」，本地已实施并验证，未提交、未上传小程序。

## 一、现状证据

- `clients/wechat-miniprogram/utils/auth.js::ensureLogin`：只要本机有记录、本地时钟显示没过期，就直接返回「已登录」，**不向服务端核验**。
- `auth.js::invalidatedTokens`：失效 token 只记在本次运行的内存集合里。小程序重新启动后集合清空。如果之前清除本机记录失败，残留的旧记录会被 `ensureLogin` 当成有效会话，但服务端早已撤销，后续带它的请求都会是 401。
  - Codex 复审（`docs/reviews/2026-10-30_codex-to-claude-wechat-miniprogram-login-fix-review.md` §3 限制 1）用 Node 探针复现过。
- `auth.js::logout`：服务端返回 204 后，如果本机清除失败，token 会进失效集合；再点一次退出时 `readStoredSession` 读到 null，就直接报成功，**不会再试着删除本机残留**（同一复审 §3 限制 2）。
- `auth.js::restore`：已经会向服务端核验（`GET /api/miniprogram/me`），401 时作废本机记录。首页 `pages/index/index.js::onLoad` 打开时就调用它。
- 当前只有首页 `handleLogin` 调用 `ensureLogin`。两条限制按复审结论延期到「其他页面接入 `ensureLogin` 之前」处理（`tasks/todo.md` 小程序清单「下一阶段」）。
- 服务端会话存在 PostgreSQL（`miniprogram_sessions`），服务器重启不会让会话失效；失效只来自到期、退出撤销和过期清理。服务端不用改。

## 二、功能点

1. **`ensureLogin` 先核验再复用。** 本机有未过期记录时，先请求 `GET /api/miniprogram/me`：
   - 200：复用，并记为「本次运行已核验」。之后本次运行内再调用不重复请求。
   - 401：作废本机记录，重新走微信登录。
   - 网络失败、超时或其他状态码：报「暂时无法确认登录状态，请稍后重试」（可重试），**不另外签发新会话**，避免旧会话还有效时多出一条。
2. **`restore` 与 `ensureLogin` 共用同一个核验函数。** `restore` 核验成功也记为「本次运行已核验」。首页打开时已经恢复过，再点登录就不会重复请求。
3. **退出时清理残留。** 本机还留着已作废的记录时，再点退出不再请求服务端（服务端已经撤销），只重试删除本机记录；删除仍失败则如实报错。
4. **测试**（`tests/miniprogram/auth.test.js`，由 `tests/unit/test_miniprogram_client_js.py` 在 pytest 中调用）：
   - 修改「已有未过期会话时不重复登录」：改为先核验一次，本次运行内第二次调用不再请求。
   - 新增「重启后残留记录」：重新加载模块模拟小程序重启，残留记录经核验得到 401 后重新登录。
   - 新增「核验网络失败」：报可重试错误，不调用 `wx.login`。
   - 修改「退出残留」：第二次退出确实再删一次本机记录，并且残留被清掉。

不做：服务端改动；其他页面接入登录；通用的「带令牌请求 + 401 自动重新登录」封装（目前还没有第二个调用方）。

## 三、风险与决策

- **D1 核验频率：每次运行核验一次（推荐）。** 本地到期检查已经覆盖 24 小时到期；本次运行内服务端撤销会话的途径只有本机退出，退出后本机会作废记录。另一种做法是每次调用都核验，每个需要登录的操作都会多一次请求，收益很小。
- **D2 核验失败不自动重新登录。** 网络故障时重新登录也大概率失败，而且会留下重复会话。代价是网络恢复前需要登录的操作会提示重试。
- **上线方式：** 只改小程序代码，服务器不用部署，也不涉及 `REPLY_PATHS`。改动要你在开发者工具上传、发布体验版或正式版后才对客人生效。上传和发布由你操作。
- **验证：** 跑 Node 客户端测试和对应的 pytest 包装；不跑全量，因为没有改 `application.py`、依赖或构建配置。真机行为需要你上传后，在开发者工具或真机上点一次「重新打开 → 登录」确认。
- **回退：** 回退 `auth.js` 和测试文件，然后重新上传小程序。

## 四、实施记录（2026-11-02）

- `auth.js`：新增 `readRawSession`（不过滤失效 token）、`verifiedTokens`、`verifyStoredSession`。`restore` 直接返回核验结果；`ensureLogin` 核验后复用或重新登录，状态待确认时抛出 `verify_unknown`（可重试）；`logout` 遇到已作废的残留记录时只重试清除；登录成功的 token 记为已核验。
- **相对 Spec 的增补**：`pages/index/index.js::handleLogin` 捕获 `verify_unknown` 时显示待确认（「重新确认登录状态」按钮），不显示未登录。这是新增错误码的直接后果：不改的话，本机记录还在，页面却显示未登录。
- 验证：
  - `node --test tests/miniprogram/auth.test.js`：20 passed。
  - `tests/unit/test_miniprogram_client_js.py`：1 passed。
  - `git diff --check` 通过。
  - 判别力：把 `auth.js`、`index.js` 换回改动前版本后，新增和改写的 5 项测试全部失败。
- 未覆盖：开发者工具和真机上的实际表现，需要上传小程序后验证。
