# 小程序住客自助服务（房间介绍 + 入住指南）Spec

状态：待用户审阅。用户回复「开始」前不写业务代码。

范围来源（用户 2026-11-03 选择）：
- 小程序给已订房住客自助服务；第一版不绑定订单。
- 不在小程序内与 AI 对话，「问管家」转企业微信客服。
- 内容复用知识库，逐条勾选；界面用 TDesign 组件库。
- 加房间介绍，每间房单独维护：照片轮播、简介、床型/可住人数/面积、设施清单。
- 知识条目放进「入住指南」。
- 默认进房间页；展示名称不填用房间标题；房间按后台排序号排列；只做中文。

## 一、现状证据

### 小程序

- `clients/wechat-miniprogram/app.json`：只有模板页 `pages/index/index`、`pages/logs/logs`，没有 tabBar。
- `pages/index/index.wxml`、`pages/index/index.js`：首页是登录面板、「测试后端连接」和模板 motto。
- `clients/wechat-miniprogram/utils/auth.js`：
  - 提供 `API_BASE_URL`、`ensureLogin`、`restore`、`logout`。
  - 内部 `request` 未导出。
- 没有 `package.json`，没有任何 npm 依赖。
- `tests/unit/test_miniprogram_client_js.py::test_miniprogram_auth_client_behaviour` 只运行 `tests/miniprogram/auth.test.js`。

### 后端 · 小程序接口

- `src/homestay_bot/routes/miniprogram_auth.py`：`router = APIRouter(prefix="/api/miniprogram")`，只有登录、`/me`、退出三个接口。
- `src/homestay_bot/middleware.py::NO_STORE_PATH_PREFIXES`：含 `/api/miniprogram`。`AdminNoStoreMiddleware` 对该前缀下所有响应强制 `cache-control: no-store`，会覆盖路由自己设置的缓存头。

### 后端 · 知识库

- `src/homestay_bot/domain/models.py::KnowledgeEntry`：
  - `scope` 取值 `unreviewed`/`global`/`property`/`public`。后台标签见 `templates/knowledge/scope_fields.html`：范围待审核、本店通用、指定房间、店外公开知识。
  - 另有 `is_enabled`、`valid_from`、`valid_until`、`category`、`question_zh`、`answer_zh` 等字段。
  - **没有**「是否在小程序展示」字段。
- `src/homestay_bot/repositories/knowledge.py::list_active`：返回已启用且范围不是 `unreviewed` 的条目，按 id 排序。
- `src/homestay_bot/services/knowledge_service.py`：约第 742–751 行按 scope 和有效期过滤，日期取 `Asia/Shanghai`。

### 后端 · 房间与图片

- `src/homestay_bot/domain/models.py::PropertyProfile`：
  - 现有字段：`title`、`room_number`、`room_type`、`district`、`address_hint`、`parking_instructions`、`is_active`、`welcome_image_file_id`。
  - **没有**简介、床型、人数、面积、设施或多张照片。
- `src/homestay_bot/integrations/hostex_client.py::Property`：百居易只给 `id`、`title`、`channels`、`address`，没有介绍和照片。
- `src/homestay_bot/services/property_admin_service.py::PropertyAdminService`：`update_profile`、`set_welcome_image`、`require_admin`、`_add_audit` 是房间后台的现有写入口，只允许管理员。
- `src/homestay_bot/routes/properties.py`：
  - 房间详情和表单，`_issue_csrf`/`_consume_csrf` 按房间签发 CSRF 令牌。
  - `upload_welcome_image`、`view_welcome_image` 是现有上传和预览模式。
- `src/homestay_bot/application.py`：
  - `replace_welcome_image`（约第 2498 行）：先 `self._storage.save_image(...)` 落盘，再在事务里换 file_id；旧文件用 `ATTACHMENT_CLEANUP_JOB_TYPE` 任务清理；事务失败删新文件。
  - `welcome_image_for` 用 `open_for_read` 读取。
- `src/homestay_bot/services/private_file_storage.py::save_image`：
  - 限制大小，核对 MIME 与真实签名。
  - 随机命名，权限 0600。
  - **原样保存字节，不去除 EXIF**。
- 大小和格式限制来自 `src/homestay_bot/routes/knowledge.py`：`KNOWLEDGE_IMAGE_MAX_BYTES = 2 MB`，`KNOWLEDGE_IMAGE_TYPES` 为 PNG 和 JPEG。
- `src/homestay_bot/routes/private_files.py`：私有文件只给已登录员工（`/employee/private-files/{file_id}`）。现在没有任何公开图片出口。
- `pyproject.toml` 没有 Pillow 等图像库。

### 部署与版本

- `compose.yaml` 只有 `api` 和 `postgres`，反向代理在仓库外。
- 已知公网能到达 `/health` 和 `/api/miniprogram/*`；其他路径前缀是否被代理未核实。
- 迁移头是 `0036_miniprogram_sessions`；当前版本 `1.71.0`。

## 二、功能点

### F1 入住指南：知识条目展示开关与接口

- **数据：** `KnowledgeEntry` 增加 `show_in_miniprogram`，布尔型，默认 false，`server_default` 为 false。
- **后台：**
  - 知识编辑表单加勾选框「在小程序入住指南展示」。
  - 列表卡片在已勾选时显示徽标「小程序」。
  - 沿用现有编辑权限和 CSRF。
- **接口 `GET /api/miniprogram/guide`：** 不需要登录。
  - 筛选条件：
    - `is_enabled`；
    - `scope == "global"`（只要本店通用）；
    - `show_in_miniprogram`；
    - 当天（`Asia/Shanghai`）落在有效期内。
  - 按分类分组。分类顺序取各分类最小条目 id，组内按 id 排序。
  - 最多 200 条，超出部分截断不报错。
  - 只返回 `category`、`question`、`answer`（取中文），不返回 id、关键词、触发词、更新人。
  - 返回结构：`{"sections": [{"category": "...", "items": [{"question": "...", "answer": "..."}]}]}`。

### F2 房间介绍：数据与后台

- **新表 `room_showcases`**，与 `property_profiles` 一对一，主键即 `property_id`。
  - `is_visible`：布尔，默认 false。
  - `display_name`：可空，最长 64。
  - `intro`：可空，最长 2000。
  - `bed_type`：可空，最长 64。
  - `max_guests`：可空，1–20。
  - `area_sqm`：可空，1–1000 的整数。
  - `facilities`：JSON 字符串列表，最多 30 项，每项最长 20 字；后台一行一项，空行去掉，重复项去重。
  - `sort_order`：可空整数，0–9999。
  - `updated_by`，以及创建、更新时间。
- **新表 `room_showcase_photos`**：
  - 字段：`id`、`property_id`（外键）、`file_id`（唯一）、`sort_order`（整数）、`created_at`。
  - 每间房最多 9 张。
- **后台房间详情页新增「小程序展示」区块，仅管理员：**
  - **保存文字资料：** 一个表单。
  - **上传照片：**
    - 一次一张，沿用 2 MB、JPG/PNG 限制。
    - 新照片排在最后。
    - 超过 9 张拒绝并提示。
  - **删除照片：**
    - 删除前确认。
    - 文件通过 `ATTACHMENT_CLEANUP_JOB_TYPE` 任务清理。
  - **调整顺序：** 每张照片一个排序号输入，统一保存。
  - **审计：** 每个写操作都用 `_add_audit` 记一条，记录不含正文内容。
  - **服务层：** 写操作放在 `PropertyAdminService` 新方法中，先 `require_admin`，再锁定房间行；装配沿用 `application.py` 现有房间页服务。

### F3 房间介绍：公开接口

- **房间可见条件：** `room_showcases.is_visible` 并且 `property_profiles.is_active`。两者任一不满足，就像不存在一样返回 404。
- **`GET /api/miniprogram/rooms`：**
  - 列表排序：`sort_order` 升序，空值排最后，再按 `property_id`。
  - 每项字段：`id`（property_id）、`name`（`display_name`，为空时用 `title`）、`bed_type`、`max_guests`、`area_sqm`、`cover_url`（排序第一张照片，没有照片为 null）。
- **`GET /api/miniprogram/rooms/{id}`：**
  - 在列表字段基础上加 `intro`、`facilities`、`photos`（按 `sort_order`、id 排序的 URL 列表）。
- **`GET /api/miniprogram/rooms/{id}/photos/{photo_id}`：**
  - 只有照片属于该房间、且房间可见时才返回文件，否则返回 404。
  - 永远不按 `file_id` 或任意路径读取文件。
- **不返回的字段：** 房号、地址、停车说明、欢迎图、凭证，以及任何订单或房态数据。

### F4 照片去元数据

- 照片对外公开，手机拍摄的 JPEG 常带 GPS 坐标，所以上传时去掉元数据，再交给 `save_image`。
- **JPEG：** 删除 APP1–APP15 段（EXIF、XMP 等），保留 APP0（JFIF）、APP2 中的 `ICC_PROFILE` 和图像数据。
- **PNG：** 删除 `eXIf`、`tEXt`、`zTXt`、`iTXt`、`tIME` 块。
- 用标准库按段或块解析，不新增依赖。
- 解析失败视为无效图片，拒绝上传，不原样保存。

### F5 小程序页面

- **`app.json`：**
  - 页面：`pages/rooms/index`（首个，默认进入）、`pages/rooms/detail`、`pages/guide/index`。
  - 纯文字 tabBar：「房间」「入住指南」。
  - 移除模板页 `pages/index`、`pages/logs` 及其文件。
  - 导航栏标题改为民宿名，由用户提供，Spec 中暂写「YuMi 民宿」。
- **房间列表：**
  - 每张卡片：封面图、名称，以及床型、可住人数、面积三个标签。
  - 点卡片进入详情页。
- **房间详情：**
  - 照片轮播（TDesign `t-swiper`），点击可预览大图（`wx.previewImage`）。
  - 名称、三项基本信息、简介、设施标签。
  - 底部固定按钮「预订或咨询请问管家」。
- **入住指南：** 分类标题加 TDesign 折叠面板，点开看答案。
- **问管家：**
  - 两个 tab 页右下角都有按钮，调用 `wx.openCustomerServiceChat`。
  - 企业 ID 与客服链接写成常量，放在 `utils/service-chat.js`，值由用户提供。
  - 调用失败时提示「暂时无法打开客服，请稍后再试」。
- **状态处理：**
  - 加载中显示骨架屏。
  - 失败显示「加载失败」和「重试」按钮，不展示服务器原文。
  - 空列表显示「内容整理中，有问题请问管家」。
  - 下拉刷新重新请求。
  - 页面卸载后的过期响应不再写页面，沿用首页代次做法。
- **请求封装：**
  - 新增 `utils/content.js`：`fetchRooms`、`fetchRoom`、`fetchGuide`。
  - 10 秒超时；非 200 或结构不符一律按加载失败处理。
  - 复用 `utils/auth.js` 的 `API_BASE_URL`。
- **登录代码：** `utils/auth.js` 及其测试保留不动，供将来订单绑定使用。
- **TDesign 引入：**
  - 在 `clients/wechat-miniprogram/package.json` 写入 `tdesign-miniprogram`，固定精确版本，提交 lock 文件。
  - `.gitignore` 忽略 `node_modules`、`miniprogram_npm`。
  - 用户在开发者工具「构建 npm」。

### F6 测试（有判别力的才写）

- **后端集成测试（临时 SQLite，真实装配）：**
  - **入住指南：** 范围、启用、有效期边界、开关逐项各一条反例；200 条截断；返回字段白名单。
  - **房间：**
    - 可见条件的两个反例：未勾选展示、房间停用。
    - 名称回退；排序空值排最后。
    - 详情页不含地址、停车说明、房号。
  - **照片：**
    - 照片属于另一房间时 404；房间不可见时 404。
    - 不能通过路径或任意 file_id 读到私有文件。
  - **后台：**
    - 非管理员拒绝；CSRF 失败拒绝。
    - 第 10 张照片拒绝；字段越界拒绝。
    - 删除照片会登记清理任务。
- **单元测试：**
  - JPEG 带 EXIF GPS、PNG 带 tEXt，去除后元数据不存在、图像数据字节不变。
  - 截断或伪造的文件被拒绝。
- **迁移：** 在 `tests/unit/test_migrations.py` 的升降级用例中覆盖 0037。
- **PostgreSQL：** 本次没有并发写入和锁语义的新增风险，不新增 PostgreSQL 专项。照片上限依赖锁定房间行后计数，在 F2 里注明，并用 SQLite 测试串行情形。
- **小程序 Node 测试：**
  - 新增 `tests/miniprogram/content.test.js`，覆盖：请求失败和结构不符时的报错；列表页加载、空状态、失败重试；页面卸载后的过期响应被丢弃；客服按钮失败时的提示。
  - `test_miniprogram_client_js.py` 改为运行 `tests/miniprogram/` 下全部测试。
- **不写的测试：** 不复述样式和 wxml 结构的测试。

## 三、风险与决策

- **D1 缓存：** 推荐方案是照片接口不缓存，以免被中间层或客户端缓存。
  - 公开内容接口仍在 `/api/miniprogram` 下，沿用 no-store。
  - 照片接口在 `AdminNoStoreMiddleware` 中按路径精确豁免，改为 `cache-control: public, max-age=86400`。理由：照片文件不可变，替换会产生新 id；每次重新下载 2 MB 级照片，在手机上代价明显。
  - 豁免只针对 `/api/miniprogram/rooms/{id}/photos/{photo_id}` 这一种路径形态，用测试锁定，登录接口仍是 no-store。
  - 备选：不豁免，代价是流量。
- **D2 访问频率：** 公开只读接口第一版不加限流。
  - 这些接口都是有上限的小查询：入住指南最多 200 条，照片每间房最多 9 张。
  - 留下 `ponytail:` 注释：公开推广或日志出现异常频率时，再加按 IP 或全局限流。
- **D3 去元数据不引入 Pillow**，按 F4 用标准库剥离。
  - 天花板：不压缩、不转码，2 MB 上限照旧。
  - 需要缩略图或压缩时，再评估引入图像库。
- **D4 名称回退：** `display_name` 为空时显示房间标题，用户已确认。后台在展示区块提示「留空将显示房间标题：xxx」。
- **D5 移除首页登录面板和模板页：** 用户已确认。
  - `auth.js` 保留。
  - `tests/miniprogram/auth.test.js` 中 4 项首页测试随首页一起删除（重复点击登录、退出失败、迟到结果丢弃、核验待确认），登录模块的其余测试保留。
  - 「迟到结果丢弃」的代次做法会在 F5 内容页测试里重新覆盖。
- **需要用户提供或操作（我无法代办）：**
  - 微信客服后台绑定该小程序。
  - 提供企业 ID 和客服链接（都是公开值）。
  - 确认小程序主体与企业微信主体的要求，以微信后台实际提示为准。
  - 提供导航栏显示的民宿名。
  - 在开发者工具「构建 npm」，然后上传并发布。
  - 在后台填写房间内容、勾选知识条目。上线前逐条核对措辞：有些条目是为机器人配合关键词写的。
  - 小程序类目和隐私指引按微信审核要求填写。
- **安全边界：**
  - 公开接口只读，绝不返回地址、停车说明、凭证、订单、房态和价格。
  - 照片只能按「房间可见 → 照片属于该房间」的数据库关系读取。
  - 不调用真实百居易、DeepSeek、企业微信。
- **验证与发布：**
  - 改动 `application.py` 装配，提交前本地跑一次全量。
  - 不涉及 `REPLY_PATHS`，不跑真实模型门禁。
  - 版本 1.72.0：同步 CHANGELOG 与 `docs/releases/1.72.0.md`。
  - 部署：先备份，再迁移到 0037，只替换 API。
  - 部署后只读核对：三个公开接口返回空列表或已有内容；登录后的后台页面能打开「小程序展示」区块。
  - 小程序由用户上传后，在开发者工具或真机上走一遍「房间 → 详情 → 指南 → 问管家」。
  - 提交、推送、部署都需单独授权。
- **回退：**
  - 小程序端回退文件后重新上传。
  - 后端回退代码，并把迁移降级到 0036。降级会删除两张新表和知识开关列，已填写的房间介绍随之丢失，需以部署前备份为准。
  - 新照片文件由清理任务或手工清理。
- **实施顺序（每步都能独立运行和验证）：**
  1. F1 入住指南端到端：迁移字段、后台勾选、接口、指南页。
  2. F2、F3 文字部分：房间表、后台表单、两个房间接口、列表页和详情页（先不放照片）。
  3. F2、F3、F4 照片：上传、去元数据、删除和排序、公开读取、轮播和封面。
  4. 收尾：tabBar 和客服按钮、移除模板页、全量测试、版本与发布记录。
- **不做：** 订单绑定、小程序内 AI 对话、价格房态、英文、按房型介绍、缩略图、内容审核流。

## 四、实施记录（2026-11-03，用户「开始」）

本地实现完成，未提交、未部署、未调用真实微信或企业微信。

- **与 Spec 的偏差（实施中确认）：**
  - F2 服务层：房间介绍的写入放在新模块 `services/room_showcase_admin.py::RoomShowcaseAdminService`，没有并入 `PropertyAdminService`。原因：介绍内容会公开给任何人，与地址、凭证等运营资料分开更不易误放字段。权限仍复用 `PropertyAdminService.require_admin`，装配在 `application.py::SessionPropertyAdminService` 的 `*_showcase*` 方法里。
  - F2 照片删除：每个删除按钮通过 `form=` 属性关联到单独的隐藏表单。原因：`admin.js` 只对带 `data-confirm` 的表单弹出确认，嵌在排序表单里的删除按钮不会弹出。
  - F4 JPEG：除删掉元数据段外，EOI 之后的厂商附加数据（如动态照片视频）也截掉，否则这些数据会原样公开。
  - F6：CSRF 失败沿用项目现有的 409，不是 400 或 403。`test_miniprogram_client_js.py` 逐个列出 `tests/miniprogram/*.test.js` 传给 `node --test`，因为较旧的 Node 不支持目录参数。
  - 照片接口失败时（404、503）显式返回 `no-store`，只有成功的照片响应可缓存。
- **验证：**
  - 相关 pytest 共 91 passed，覆盖房间介绍、公开内容、知识路由、去元数据、迁移和 Node 包装。Node 行为测试 25 项（auth 16、content 9）。
  - 判别力：去掉指南勾选过滤、去掉到期过滤、列表不看房源停用、照片不看展示开关、照片上限差一、照片归属不校验、缓存豁免失效，这 7 处变异都会让测试失败。
  - Ruff 与 Mypy 通过。
  - 本地全量结果是 3505 passed、84 skipped。`tests/browser` 有 6 failed、107 errors，原因是本机缺 Playwright Chromium，无法启动浏览器，与本次改动无关。
- **未覆盖：**
  - 后台「小程序展示」页签、知识勾选框和照片删除确认，都没有经过浏览器测试，需由 CI 的 `tests/browser` 或登录后人工检查。
  - 开发者工具编译、TDesign「构建 npm」和真机界面没有验证。
  - `wx.openCustomerServiceChat` 的失败路径没有验证，因为客服常量还空着。
