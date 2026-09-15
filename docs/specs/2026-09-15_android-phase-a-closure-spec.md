# YuMi Android 阶段 A 收尾与业务联调 Spec

日期：2026-09-15。状态：待确认，仅审查和设计，未修改实现或部署。

依据：当前工作区源码、2026-09-14 阶段 A 第二版交接、主 Spec R2、2026-09-13 实测修复 Spec。交接中的历史授权和执行建议不自动转化为当前授权。

## 1. 审查结论

下一阶段是“修复已发现的实现缺口，再完成 Android 端到端验收”，不是直接进入正式发布。原生错误覆盖层、委托 WebViewClient 和 margin 安全区可以保留，不需要推倒重写。

本次已逐项读取 Kotlin 下载/Activity/委托类、Rust 导航、测试身份配置和网页差异。报告中的真机数据作为历史证据保留，未重新连接设备。尝试按报告执行离线 Rust 测试时，当前 shell 返回 `cargo: command not found`，测试没有启动；不认定工具链已丢失，也不引用 28 项作为本次通过数。JVM、浏览器、APK 构建和生产验收本次均未执行。

### 1.1 报告可以保留的结论

- `MainActivity.onWebViewCreate` 确已使用 margin；报告提供了一个 Android 16 手势导航设备的 viewport 验算。此证据不自动覆盖 Android 10、三键导航、横屏或放大字体。
- `.topbar` 的透明背景与 blur 已在源码中改为实色；线上部署未完成，不能描述为生产修复完成。
- `SafeWebViewClient` 真实存在且显式拒绝表单重发；图片保存、外链确认和原生错误覆盖层已接线。是否正确覆盖全部生命周期和失败分支，仍需以下修复与验证。
- Windows 不具备本次运行证据，继续保持未验证。

### 1.2 必须纠正或补齐的发现

下列路径相对仓库根。优先级 P1 表示进入端到端验收前应修复，P2 表示验收前应明确的缺口；不等同于已发生生产事故。

| 编号 | 优先级 | 证据与问题 | 后果 |
| --- | --- | --- | --- |
| C01 | P1 | `MainActivity.startImageDownload/finishImageSave` 每次开线程，共用单个 `pendingTemp`，无忙碌门；Activity 销毁没有对应清理 | 两次下载交错时，文件选择回调可能取到另一张图片；临时文件遗留或已销毁 Activity 继续弹选择器 |
| C02 | P1 | `ImageSaver.CreateImageDocument.parseResult` 不检查 resultCode；`commit` 失败仅删缓存文件，不处理本次新建的目标 URI | 取消结果若携 URI 仍可能被处理；失败后留下空白/部分目标文件 |
| C03 | P1 | `ImageSaver.streamToTemp` 仅判断非空，`download` 接受任意 2xx 与声明 MIME，未核对期望长度或图片内容 | 206/截断内容或错误标为图片的正文可能被判为 Ready；具体网络截断行为仍需故障 fixture 验证 |
| C04 | P1 | Rust `WORKSTATION_URL` 随 test-backend 改变，但 `values/strings.xml::workstation_url` 始终是生产入口，Activity 重试/保存使用后者 | 测试来源分叉，原生重试会尝试导航生产地址并被 Rust 策略阻止，测试图片不提供保存；诊断包也可能因返回逻辑尝试该地址 |
| C05 | P1 | `identity.gradle` 只给 debug 加 `.test`；报告使用的 diagnostics 构建命令未声明 debug，诊断配置也未改身份 | 诊断/test-backend 的 release 构建没有统一隔离保障，不能仅凭 debug applicationId 宣称 A14 完成 |
| C06 | P1 | `MainActivity.installBackHandling` 对 canGoBack 直接 goBack；只有根页面退出确认，提示未提及未保存内容 | `onFormResubmission` 只拒绝网络重发，不能证明历史是安全 GET 或表单不会丢失，不满足 R2 返回保护 |
| C07 | P2 | `ui.html::_calendar_segments` 新增预扫描，用 `peak.rows` 统一各段高度，报告却写成真实行数 | 不均匀分段仍有空轨道，与实测修复 Spec 相冲突；增加了重复扫描且未解决完整问题 |
| C08 | P2 | `should_prompt_external` 只记最后 URL 和五秒窗口，没有弹窗在途状态 | 不同 URL 连续跳转或长时间不确认时可重复请求弹窗，去重单测不证明 UI 不堆叠 |
| C09 | P2 | Kotlin `DiagnosticsGate` 只按运行时 URL 门控，MainActivity 总会安排注入轮询 | 正式包仍含诊断路径与执行入口；单向注入不等同远程特权漏洞，但未达到诊断构建隔离目标 |

另外，`ImageSaver.sameOrigin` 不拒绝 URL userInfo，而 Rust 导航拒绝，跨语言策略存在差异；应补一致性测试，不将其直接描述为已证明的跨域 Cookie 泄漏。

## 2. 本阶段范围与不变项

保持 Android + Windows 总目标、管理员登录、手动分发、现有线上网页。当前只收尾 Android；不增加自动更新、账号体系、通用下载队列、后台服务或新原生权限。仅修复上述缺口及其直接测试。

原生错误覆盖层正式替代 R2 的“本地错误 HTML”实现要求：不新增本地协议放行，保留原生重试仅 GET 入口的行为。已有无调用错误页辅助代码只在确认调用全集后删除，不扩大清理范围。

不更改现有 margin 算法来迎合更多设备的猜测，先补设备矩阵；只有实际复现双重 IME 避让时，才修正相应责任方。

## 3. B1：单次图片保存和失败正确性

文件：`MainActivity.kt`、`ImageSaver.kt`、`SecurityBoundaryTest.kt`（均在 Android 可维护区），以及 `clients/yumi/tests/webview_fixture.py`。

### 3.1 事务边界

采用一个在途保存流程，覆盖确认、下载、选择位置和写入直到结束；忙碌时第二次长按只提示“正在保存，请完成或取消后再试”，不启动第二个线程/选择器。无需创建任务队列或 ViewModel 框架。

为一次操作保存一个 ID 和其资源归属：临时文件、目标 URI、Activity 生命周期。异步回调必须匹配当前操作；旧回调只清理自己创建的资源，不覆盖当前引用。

Activity 销毁后停止或使该操作失效，不再弹窗、写入新目标或 Toast；后台线程在结束时清理自身缓存。系统回收后不自动重启保存。文档选择器回传但已无有效操作时不写入，提示用户重新保存；仅对能确认是本次创建的 URI 执行删除，不猜测路径。

### 3.2 结果与输出

- `parseResult` 只有 `Activity.RESULT_OK` 且存在 URI 才返回成功；取消即使含 data 也不提交。
- 系统文档创建得到的新 URI 记录为本操作资源；失败时用文档 provider 支持的删除接口尽力清理。清理失败提示“未保存完整，所选位置可能留有不完整文件”，不能说完全未写入。既有文件不删、不覆盖。
- 拒绝空正文、非预期的部分响应；首版普通无 Range GET 要求完整 200 文件响应。存在 Content-Length 时核对收到的字节数；无长度时依赖流结束及图片格式验证，不把 socket EOF 一概当作完整图片证明。
- 复用平台图片解码/元数据能力验证实际内容与允许 MIME，不按声明类型或扩展名单独通过；不编写完整图片格式解析器。测试需要覆盖误标 image/png 的 HTML、零字节、截断及合法图片。平台无法验证的格式明确拒绝，不静默保存。
- 下载和解码应有资源上限。实施前读取真实上传服务的最大文件限制并复用；若存在多个类型，覆盖允许的最大附件，不能随意设更小阈值使合法附件无法保存。流式计数和有界解码，不分配整张超大位图。连接/读取超时和重定向上限保留。
- 初始地址及每次重定向都拒绝 userInfo、非构建允许来源、非法 scheme；Cookie 仅来自平台并仅发往允许来源，不写日志。

验收：同时触发两张不同 fixture 图片，最终每个 URI 与用户本次选择一致且无串单；取消/空间不足/provider 失败/Activity 销毁都无假成功；HTTP 和文件故障有明确请求计数及临时目录检查。

## 4. B2：统一构建身份、来源和诊断

文件：`src-tauri/build.rs`、`src/lib.rs`、`tauri*.conf.json`、Android `identity.gradle`、`app/build.gradle.kts`、`values/strings.xml` 及必要的构建配置生成脚本。

将“环境”与 debug/release 编译优化分开，建立三种互斥模式：

| 模式 | 运行身份 | 入口与允许来源 | 诊断 |
| --- | --- | --- | --- |
| production | icu.akros.yumi | 固定正式 HTTPS | 不含测量实现、页面和轮询 |
| isolated-test | icu.akros.yumi.test | 单一编译期测试入口；不允许正式来源 | 可调试，但不自动启动本地诊断页 |
| diagnostics | icu.akros.yumi.test | 打包本地测量页；不导航任何业务服务器 | 仅此模式包含原生测量代码 |

两个非生产模式共用测试身份即可，不增加第三套长期应用。两模式相互覆盖使用稳定测试签名；测试身份始终与生产隔离。release 优化并不将测试模式变成生产；不同模式明确显示名称后缀。

来源仅有一份构建输入，生成或注入 Rust 和 Android 的常量/资源，不能由运行时页面提供。测试模式缺入口构建失败；production 加 test-backend/diagnostics 的发布组合必须被构建检查拒绝或明确转为测试身份，不能保留生产 ID。明确失败规则并写入构建脚本，禁止人工记忆命令参数作为唯一防线。

原生重试、图片来源验证和 Rust 导航全部使用同一构建值。诊断模式的返回/错误恢复只退出或回诊断入口，不能调用生产工作台。跨语言测试覆盖实际测试构建资源，不能只比较生产 strings.xml。

诊断实现拆至条件 source set：production 提供同接口空实现，diagnostics 才包含采集与注入；MainActivity 不再在生产安排测量轮询。不为两个类抽象插件系统。编译结果检查实际资源与可执行代码；不能仅因字符串未检出就证明代码不存在。

验收构建至少包含 production-release、isolated-test-debug、isolated-test-release、diagnostics-release；核对 applicationId/namespace/Activity/JNI、来源、cleartext、debuggable、备份规则及 APK 签名。生产和测试包可并存且 Cookie 隔离。此处不授权卸载用户已装应用。

## 5. B3：返回、外链和错误恢复

文件：`MainActivity.installBackHandling/confirmExit`、`SafeWebViewClient`、`lib.rs::confirm_and_open_external/should_prompt_external`。

- 保留键盘优先收起；系统选择器走自己的生命周期。
- 无法证明历史目标是安全 GET 时，使用 R2 已允许的最小方案：原生确认离开并提示未保存内容会丢失，确定后 GET 当前构建工作台。不要仅凭 canGoBack 调 goBack，不为首版构建通用历史请求追踪系统。
- 根页面退出同样提醒未保存内容；网页已有效确认时避免叠加。确认取消不导航、不提交。
- `onFormResubmission` 仍拒绝 resend；用请求计数验证 POST 错误页、PRG、登录过期后返回均不重放。
- 外跳采用一个在途确认框，期间阻止其他外跳并不排队；确认/取消后清除在途状态。现有五秒同 URL 去重可复用，但不能替代在途互斥。异常也必须释放状态。
- 错误日志仅输出固定错误类别，不直接格式化插件 error，以免带出完整 URL 参数。
- 错误覆盖层显示时遮挡触摸还不够：旧 WebView 退出键盘焦点及无障碍树，恢复后再还原。验证错误后成功导航不会被旧遮罩覆盖。
- TLS 错误必须取消，禁止忽略证书；确认实际回调路径是否会显示可恢复的错误提示。HTTP 500 仍显示业务正文，不能被归类为断网；子资源失败不盖整页。

`target=_blank` 在 Android 的行为仍为验证项：同源/外源、相对 URL、window.open 和无手势重定向分别记录。不得因为 macOS 或纯 Rust 判定通过就算 Android 通过。委托清单继续对照锁定生成类；升级新增回调需复核，禁止修改 generated 文件。

## 6. B4：日历按真实分段收紧

文件：`src/homestay_bot/templates/components/ui.html::_calendar_segments`、`src/homestay_bot/static/app.css` 和 `tests/browser/test_admin_interactions.py`。

沿用已经确认的实测修复 Spec：每段 `--rows` 使用自己的 visible.bars 数量，删除本次新增的 peak 预扫描；折叠高度仍取真实行数与 preview-rows 的最小值。空段走既有 empty 分支。无需为了分段视觉等高添加不存在的轨道。

补两类旧测试未证明的 fixture：相邻段分别 1/3 行和 1/4/0 行。分别断言折叠/展开高度、条目可见性及不支持 :has/无 JS 情况；不得以“所有段高度一致”为通过标准。保留时间条比例、客户链接、跨段标识和真实订单分组。

若用户后续明确要求等高，应作为产品取舍单独修改 Spec，而不是在实现注释中自行设定。本阶段不再调整全局字号或 topbar 已正确的实色方案。

## 7. B5：真实业务认证与真机验收

平台 fixture 只能验证壳层；A04/A07 必须接真实路由、AdminAuthService、CSRF 服务、访问复核器和隔离数据库。复用原 Spec 指定测试构造，先验证外部服务替身、无生产配置、无 worker 外发、独立上传目录，再运行设备可访问的隔离实例。

| 验收组 | 必须提供的证据 |
| --- | --- |
| 构建隔离 | B2 四组合，包属性/来源/签名，与安装设备包哈希对应 |
| 保存事务 | B1 并发、取消、内容伪装、截断、空间/provider 故障、重名、生命周期；URI 与图片内容对应 |
| 返回外跳 | B3 的 POST 计数、确认取消、多个外跳、target=_blank、错误恢复及无障碍焦点 |
| 真实认证 | 正确/错误密码、首次改密、闲置过期、停用、改密撤销旧会话、CSRF 拒绝、退出后重启 |
| 布局 | 当前已测 Android 16 机型复测；最低 API 模拟器、三键/手势、横竖屏、放大字体与键盘循环。没有实际设备的格子如实未测 |
| 网页 | 不均匀日历新旧对照、无 JS、宽屏浏览器及 topbar 既有回归；不是 Windows 客户端验证 |

不要保留生产客户截图或登录数据作为公开测试素材。过期分支可用可控测试时钟，客户端仍需实际看到失效跳转。正式环境只读/写入验收须另行授权，不能使用交接文档的历史授权。

## 8. 顺序、交付和退出条件

顺序：B2 隔离构建打通 → B1/B3 修复 → B4 修正及浏览器验证 → B5 真实业务联调与 Android 实测。先解决测试来源分叉，避免后续在错误环境验证。不重复搭建工具链，先定位既有可执行路径并核对版本；找不到时报告环境缺口。

沿用 tasks/todo.md 跟踪但不覆盖原任务。每项修复至少有一个能在旧实现失败的回归点；冻结差异后运行相关 Rust/JVM/浏览器集一次。仅修改客户端不跑 Python 全仓；真实认证与模板变更覆盖对应测试，不拿测试总数量作为充分性的替代。

提交给 Codex 的材料：最终文件/符号差异、锁定版本、实际执行命令与结果、APK 哈希/签名/身份、验收矩阵、故障证据及未执行原因。没有证据的格子不能标绿。

完成状态分开记录：

1. Android 阶段 A 能力收尾通过：B1–B5 必需项通过，或用户明确接受具体设备覆盖例外。
2. 网页源码/本地测试通过：不等于网页已部署。
3. Windows：保持未验证，Android 收尾不能把原双平台整体阶段 A 标为完成。
4. 正式发布：尚需稳定发布 keystore、验证其备份、安装/升级说明、版本日志及当前发布授权；测试签名不是正式密钥。

当前请求仅授权审查交接与编写下一阶段 Spec。本文经确认并明确“开始”后再实施。提交、推送、部署、向员工发包、卸载旧包及生产写入不在本次范围。保留原始交接文档作为历史记录，本 Spec 的源码审查纠正项作为后续实施依据。
