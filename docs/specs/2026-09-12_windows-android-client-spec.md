# YuMi Windows / Android 客户端 Spec

制定日期：2026-09-12。修订：R2，已完成交接前静态审查；待用户确认并授权实施，未开始实现。平台技术验证仍是阶段 A 的交付物，不能把方案审查通过当作运行验收通过。

目标：员工安装软件后，在独立应用内登录并操作现有线上后台。使用 Rust + Tauri 2，共享客户端工程，分别交付 Windows EXE 和 Android APK。

本文定义产品行为、信任边界、文件范围、实施顺序和验收标准，不是已经完成的功能或测试报告。源码基线为本地 Git `ec74610`；本次未检查生产运行状态。后续实施以重新读取的代码为准。

## 1. 已确认决策与范围

| 决策 | 结果 |
| --- | --- |
| 目标设备 | Windows 电脑和 Android 手机；不做 macOS、iPhone |
| 使用者权限 | 沿用管理员登录，成功登录者按现有服务端规则拥有管理员权限 |
| 业务界面 | 加载现有线上网页，不重写业务前端或后端 |
| 核心功能 | 登录、页面浏览和表单操作、上传下载、退出 |
| 客户端升级 | 手动分发新版安装包，不做更新检查或自动安装 |
| 后台能力 | 不做托盘、后台任务服务、消息通知、开机启动 |
| 联网要求 | 在线使用，不做离线业务、离线提交队列 |
| 分发方式 | 内部直接分发，不上架应用商店 |
| 签名条件 | Windows 暂无商业代码签名；Android 自建发布密钥并备份 |

用户确认的是分发方式，不能据此认定所有设备均允许安装。受系统版本、厂商策略或设备管理限制的安装结果，需要实际设备验证。

以下为本 Spec 提议的首版默认值，随本文一起确认：

- 名称：`YuMi 工作台`；固定应用标识：`icu.akros.yumi`；客户端首版版本：`0.1.0`。
- Windows 支持目标：Windows 11 x64。Windows 10、Windows ARM 不纳入首版验收承诺。
- Android 支持目标：Android 10 及以上、ARM64、具备可用 Android System WebView 的设备。最低版本设备及员工实际机型必须覆盖；不将整个版本范围等同于所有厂商机型已验证。
- 正式入口：`https://akros.icu/employee/admin`。先访问受保护工作台，由服务器在未登录时跳转登录页；不能每次启动固定打开 `/employee/login`，该 GET 路由会直接渲染登录页。域名来自当前任务上下文，发包前复核域名及页面跳转，不在客户端提供任意服务器地址输入框。
- 主工程目录使用 `clients/yumi/`，避免继续以 `desktop/` 命名同时包含手机的工程。
- Windows 默认窗口 1280×800，可调整大小，最低 800×600；Android 跟随系统方向和字体大小，不强制锁定横竖屏。

## 2. 当前代码依据

下面是源码事实，不代表已经在 Tauri WebView 或生产环境验收。

| 事实 | 文件与符号 | 对客户端的约束 |
| --- | --- | --- |
| 登录是服务端表单，包含一次性 CSRF 校验，成功后建立管理员会话 | `src/homestay_bot/routes/employee_auth.py`：`employee_login`、`employee_login_submit` | 加载原登录页，不另写登录 API 或保存密码 |
| 登录 GET 直接渲染页面；受保护页面在 HTML 请求缺失有效身份时跳转登录 | 同上：`employee_login`、`_clear_and_reject`；`src/homestay_bot/routes/admin.py`：`admin_dashboard` | 启动访问工作台，不用登录 GET 验证现有会话 |
| 会话检查包括八小时闲置、管理员存在和启用状态、会话版本及首次改密 | 同上：`SESSION_IDLE_TIMEOUT`、`require_employee_session` | 保持服务端裁决，不增加保活请求绕过闲置过期 |
| 退出通过带 CSRF 的服务端操作清除会话 | 同上：`employee_logout` | 使用页面原有退出入口，不伪造客户端退出成功 |
| Cookie 使用 SessionMiddleware，SameSite 为 lax，Secure 配置依环境决定 | `src/homestay_bot/main.py`：`_session_configuration`、`app.add_middleware(SessionMiddleware, ...)` | 正式客户端只用 HTTPS；源码配置不能替代发布环境 Cookie 检查 |
| 后台已有 viewport、共享 CSS/JS 和移动导航结构 | `src/homestay_bot/templates/layouts/admin.html`：`head`、`admin-shell`、`admin-drawer` | 复用现有页面，具体移动可用性仍需真机测试 |
| 任务照片上传复用当前身份、CSRF 和服务端照片处理 | `src/homestay_bot/routes/tasks.py`：`upload_task_photo` | 系统文件选择结果交给原 HTML 表单 |
| 房源凭证包含图片上传和管理员私有二维码读取 | `src/homestay_bot/routes/properties.py`：`replace_property_credentials`、`download_property_qr` | 不复制业务接口或将私有文件变成公开链接 |
| 私有任务照片依赖会话和关联任务授权；响应为 inline FileResponse、no-store | `src/homestay_bot/routes/private_files.py`：`download_private_file` | 图片预览不等于下载保存完成；文件保存必须保留鉴权 |

已查阅《YuMi民宿AI开发经验与防回归手册》的“后台管理台安全边界”章节，尤其是一次性令牌、流式文件响应、角色检查、移动界面和重复提交约束。客户端不能削弱这些边界。

## 3. 架构与方案取舍

采用 Tauri 2 + Rust + 系统 WebView。Rust 管理应用生命周期、导航策略与必要的平台适配；网页继续由服务器直接返回。Android 必要的平台回调允许少量 Kotlin，不追求所有平台代码都用 Rust。

```text
Windows EXE / Android APK
          │
          ├─ 本机：窗口、文件选择/保存、受控外链、连接错误提示
          │
          └─ 系统 WebView ── HTTPS ── akros.icu
                                      ├─ 现有登录、Cookie、CSRF、权限
                                      ├─ 现有网页、表单、业务服务
                                      └─ 现有数据库及私有附件
```

业务页面在 WebView 顶层加载，不用本地页面 iframe 嵌套后台，不启本地 HTTP 代理。普通网页请求直接访问服务器，不在 Rust 中复制业务 API 客户端。

其他方向：浏览器快捷方式不能满足本次独立安装包目标；分别写 Windows 与 Android 原生客户端会增加重复工作。Tauri 满足当前共享工程目标，但上传下载和返回行为必须分别验证，不能假定两个平台实现相同。

不新增数据库、账号模型、业务接口、React/Vue、消息队列、自动更新服务或通用插件框架。远程网页更新会直接影响客户端，后续后台发布需保留两端核心流程的兼容性抽查。

## 4. 功能规格

### F-01 安装与启动

首次安装后显示应用图标和名称。启动只打开一个业务主视图并导航到正式入口，已有有效会话时由服务器决定跳转；不能靠客户端隐藏登录页来推定已登录。

Windows 正常关闭窗口即结束本实例及其持有的 WebView，不留下自建后台服务。首版不额外实现 Windows 单实例锁，多次启动可能打开多个窗口，用户必须显式关闭各实例。

Android 返回首页后再次返回，确认退出当前 Activity。按 Home 或切换应用遵循 Android 生命周期，不强制杀进程；“不后台运行”指不启动后台业务服务、不保活、不推送，并非要求系统立即回收进程。

### F-02 登录、会话、改密和退出

- 原样使用现有账号密码页面、首次改密页面和退出表单；不嵌入任何账号密码，不增加员工账号配置页面。
- WebView 使用应用私有持久数据目录；会话 Cookie 是否仍有效由服务端决定。正常重启和覆盖升级不主动清除会话。
- Chrome/Edge 的登录态不导入客户端，也不导出给系统浏览器。
- 锁定、停用、改密撤销会话、超时、无效 CSRF 均按服务器响应显示，不自动重试认证请求，不修改客户端时间来绕过过期。
- “关闭软件”和“退出登录”不同；关闭不会替代服务端退出。安装说明明确共用设备应退出登录。
- 本期不增加身份或审计模型；沿用现有管理员凭证时，不承诺能区分实际操作员工。

### F-03 页面操作与导航

保持已有菜单、筛选、分页、确认弹窗、提交中状态和错误提示。禁止通过隐藏字段注入、自动点击或跳过确认使操作更快。

Windows 使用原生标题栏；保留键盘复制粘贴和缩放，增加最小的“返回”“重新打开工作台”“关于”菜单。关于只显示客户端版本；网页上的服务器版本保持原来源，两者不冒充同一版本。

Android 返回顺序：先收起系统键盘或系统选择器；页面弹层保留页面自身关闭入口，不假设原生返回键会自动关闭网页抽屉；再回退可安全导航的页面历史；在根页面确认退出。不得使用会重放 POST 的刷新或回退路径，也不恢复未经确认的 POST 历史。原生层无法证明目标是安全 GET 时，确认后 GET 固定工作台，不能盲目调用 history.back。服务端 PRG 路径与直接 POST 错误页都需覆盖。

`src/homestay_bot/static/admin.js` 的 `dirtyForms` 和 `beforeunload` 提供网页内的未保存保护，但不能证明原生窗口关闭、Activity 退出也会触发该保护。阶段 A 必须实测；Windows 关闭/返回/打开工作台及 Android 应用内返回/退出如果无法可靠复用此提示，使用原生确认“离开当前页面？未保存的修改可能丢失。”作为首版兜底，不通过读取表单正文或新增远程特权桥来检测。网页已可靠提示时不叠加第二次确认。系统强制回收、杀进程不能保证提示或恢复，安装说明明确此限制，不新增持久化草稿。

外链仅在用户主动点击时打开系统浏览器，传递前完成 URL 校验。新窗口请求遵守同样策略，同源页面优先在当前视图打开。首版禁止任意自定义 scheme、`intent:`、`file:` 和脚本 URL 跳转。

`on_navigation` 只有 URL，不能单凭它证明用户点击。没有可靠用户手势来源时，阻止外域导航并显示含目标域名的原生确认，用户再次确认才调用系统浏览器；无操作时不自动外跳。确认仅限合法 HTTPS、无用户信息、默认端口，自动重定向产生的重复提示必须抑制。系统浏览器不传 Cookie、认证头或私有文件链接。

### F-04 上传与文件保存

上传使用原 HTML 文件输入框和系统文件选择器。必须验证任务照片、房源二维码、中文文件名、取消选择及服务端拒绝文件的路径。首版支持已有照片/文件选择，不承诺直接拍照、批量上传或 HEIC 转码；服务端不支持的格式仍显示原错误。

普通下载优先使用平台 WebView 下载机制和系统保存对话框。私有文件必须保持当前应用登录态，不能将 URL 交给未登录的外部浏览器作为完成下载的替代。

对 inline 私有图片，支持用户明确触发“保存图片”，不能仅打开预览就计为下载通过。如果平台默认机制不足，使用限于该能力的原生回调适配，规则如下：

1. 仅允许当前用户主动请求、来自正式 HTTPS 同源的文件 GET；不构建通用 HTTP 下载器。
2. 若需要原生请求，只从平台 Cookie 管理器读取适用于该 URL 的 Cookie，不用网页 JavaScript 读取或传输 Cookie。
3. 重定向逐跳限制为同源；不向外部地址发送 Cookie。登录重定向、401/403、错误正文不能保存成图片并报告成功。
4. Windows 通过保存对话框，Android 通过系统文档创建/保存流程写入用户选择的位置，不申请整个磁盘或全部照片访问权限。
5. 文件名去除路径成分和危险字符。首版只创建新文件，重名则建议新名称或要求重新选择，不实现覆盖已有文件。Windows 先写应用临时文件、完成后再以拒绝覆盖的方式提交到选择位置；Android 使用文档创建流程取得新 URI，不能使用“打开已有文件”后直接截断写入。下载失败只清理本次新建的临时文件/URI；不能确认归属时不删除。空间不足、取消和中断不报告成功，也不删除或覆盖已有文件。
6. 完整下载并成功关闭输出后才显示成功。下载只限用户本次选择，不加入持久后台队列；离开应用导致中断时，下次由用户重新发起。

保存入口明确为：Windows 图片上下文菜单“保存图片”，Android 长按图片后“保存图片”；只对本次命中的受保护图片提供操作。阶段 A 核对系统 WebView 默认菜单是否可用，不可用则在原生菜单/命中测试回调中实现，不以抓取所有图片或扫描页面替代用户选择。只接受成功的文件响应，inline 图片检查最终 URL、2xx 状态及受支持图片类型；原生实现流式传输，不将完整附件装入内存。对失效 Cookie 的 200 登录 HTML 场景单独测试。

上传下载先做双平台技术验证。如果必须放宽同源限制、开放远程通用 IPC 或修改服务端授权才能实现，应更新本 Spec 并重新确认，不能以“封装网页”为由默许。

### F-05 网络错误和恢复

启动连接等待超过 15 秒可显示“连接较慢”，允许用户等待或重新打开工作台；这不是后台业务请求的超时设置，不能取消正在提交的业务。

DNS、断网、TLS 错误显示可辨识的本地提示。重试按钮只重新 GET 固定工作台入口，不重放上一次 POST。业务请求结果不确定时提示用户先检查记录状态，不宣称失败、更不能自动再提交一次。

HTTP 登录拒绝、限流、业务错误保留服务器正文，不全部替换成“断网”。不以 `/health` 是否为 200 作为允许打开后台的前置条件。

应用切回前台不自动刷新未提交的表单；系统回收后重新启动从入口进入，由服务端重新检查会话。

### F-06 小屏可用性

首版验证 360、390 CSS 像素宽度及真实 Android 设备。核心操作无整页横向溢出；确需横向滚动的数据表限制在局部容器。系统键盘不遮挡当前输入框和提交入口，状态栏/导航栏不覆盖按钮。字体放大后仍可导航和提交。

保留语义表单、焦点、确认提示和现有移动抽屉无障碍行为。只修复经复现、阻塞本次核心路径的页面问题，不顺带重做视觉设计或所有后台页面。

## 5. 安全、隐私和配置边界

正式版本仅允许顶层业务导航到 `https://akros.icu`，用 URL 解析后的 scheme、host、有效端口判断，不用字符串前缀。拒绝 `akros.icu.evil.example`、带用户信息的伪装 URL 和非标准端口。导航规则不是全部子资源白名单：现有图片和静态资源按服务端策略加载，不能未经检查一律拦截跨源资源。

远程网页没有 Tauri 本机特权。明确审查 capabilities、插件权限和注册命令；不能只设置一个前端变量就认为隔离成立。不启用远程通配权限或通用文件、Shell、HTTP IPC。平台适配通过用户交互产生的原生回调执行，不向网页暴露任意原生操作。

本地连接错误页只包含打包资源，使用同一主视图的打包页面，无任意路径/URL参数；重试为指向固定工作台的普通链接，不需要本地 IPC 或额外窗口。原生控制层只允许自身触发进入该精确本地错误资源；来自远程页的任意本地协议导航一律拒绝。第一版不注册自定义 invoke 命令，显式配置空 capabilities 并删除模板自动授权，不开放远程原生权限。不能仅凭 `withGlobalTauri=false` 声称 IPC 已隔离。正式版关闭调试入口和远程调试。

正式版本不允许明文 HTTP、不忽略 TLS 错误。测试构建使用不同应用标识 `icu.akros.yumi.test` 和不同数据目录；测试地址只经构建配置指定，不把任意地址配置带进正式包。Android 测试 HTTP 如有必要，仅限 debug 配置，发布包检查禁止泄漏。

Android 测试包以 Gradle build variant/applicationIdSuffix 分离运行身份，保持生成的 Kotlin namespace、Activity 类名与 Rust JNI 入口匹配，不能只改 Tauri identifier 后盲目复用已生成工程。正式、debug、升级验收包都要检查最终 APK manifest 的 applicationId、Activity 和 debuggable。测试构建只允许选定的测试来源，不同时允许正式来源，从而避免误连生产。

不新增分析 SDK、Cookie 导出、屏幕内容采集或业务持久缓存。诊断只保留平台/客户端版本与脱敏错误类别，不记录表单、密码、Cookie、私有附件 URL 参数或客户正文。Android 禁止系统备份迁移应用认证数据；用户主动保存的附件属于用户文件，退出登录或卸载不能偷偷删除。

## 6. 构建、签名和分发

### 6.1 工具与构建目标

使用 Tauri 2、Rust stable 工具链、Cargo CLI；实施时选择兼容的具体版本并在工程中锁定 Rust、依赖和 Android Gradle 工具版本，不使用无约束的“始终最新版”。Android 使用 Tauri 生成的 Android Studio/Gradle 工程，开发环境需要 JDK、SDK、NDK。Windows 在 Windows 构建环境生成并验收安装包，不以 Mac 上交叉编译成功替代 Windows 验收。

Windows 打包 NSIS EXE，按当前用户安装。检查 WebView2，缺失时使用官方运行库引导安装；离线且缺少运行库时明确提示需要联网，不显示安装成功但无法使用。

Android 输出 ARM64 release APK，并设置最低 Android 版本与递增 versionCode。只交付 APK，不交付 AAB、debug APK 或模拟器专用包。该平台打包方式见 [Tauri APK 文档](https://v2.tauri.app/distribute/google-play/#build-apks)；开发依赖见 [官方环境要求](https://v2.tauri.app/start/prerequisites/)。Windows 安装机制见 [NSIS 与 WebView2 文档](https://v2.tauri.app/distribute/windows-installer/)。

### 6.2 签名和密钥

Android 首次 release 前生成专用发布 keystore，私钥及密码不进入仓库、安装包、日志或本 Spec。保存在受控位置，另做一份受控备份；从备份恢复到隔离环境，核对证书指纹并签署测试包，才算备份通过。后续更新保持应用标识和签名身份不变。密钥丢失可能破坏覆盖更新能力，不能临时重新生成一把冒充原版本。参考 [Android 签名文档](https://v2.tauri.app/distribute/sign/android/)。

Windows 首版无商业签名；安装包和说明如实标明内部测试分发。系统可能显示未知发布者或拦截，不能承诺签名缺失对所有电脑无影响。Android 自建签名也不保证每种厂商安装策略都会放行。安装说明只记录实际设备允许的正常安装流程，不要求关闭系统整体安全防护。

### 6.3 手动更新与恢复

客户端版本独立于后端。每次发包同步维护客户端更新日志，安装包名分别为 `YuMi-Workstation_<version>_windows-x64-setup.exe` 和 `YuMi-Workstation_<version>_android-arm64.apk`。

交付安装包、SHA-256 清单、更新说明和安装步骤；校验值与已确认分发渠道配合使用，不将哈希本身当作发布者身份证明。分发渠道由用户手动操作，本任务不自动发送文件给员工。

覆盖升级在应用退出后进行，保持应用标识、签名和私有数据目录稳定。保留登录资料不等于延长服务器会话。安装失败不能以先卸载并清空数据作为默认修复。

Android 不将安装旧 versionCode 当作可靠回滚方案。遇到客户端回归，采用旧源码修复分支重新生成更高 versionCode 的恢复版本并使用原签名；Windows 同样优先发布前向恢复包。旧包归档用于定位和重建，不承诺直接降级可用。

服务器页面故障无法通过降级客户端自动解决。客户端发布不包含后端部署或数据库迁移。

## 7. 文件与实现边界

以下均为拟新增文件，当前只创建本 Spec。路径相对仓库根目录 `/Volumes/02/obsidian codex/homestay-bot`。

| 文件/目录 | 职责及计划符号 |
| --- | --- |
| `clients/yumi/src-tauri/Cargo.toml`、`Cargo.lock` | 客户端依赖、库产物和二进制入口 |
| `clients/yumi/rust-toolchain.toml` | 锁定实施时已验证工具链 |
| `clients/yumi/src-tauri/build.rs` | Tauri 构建和最小权限声明 |
| `clients/yumi/src-tauri/src/main.rs` | Windows 启动，调用共享 `run` |
| `clients/yumi/src-tauri/src/lib.rs` | 共享 `run`、`classify_navigation`、`open_workstation`；平台分支和最小 URL 策略单元测试 |
| `clients/yumi/src-tauri/tauri.conf.json` | 固定身份、版本、视图和最小能力配置 |
| `clients/yumi/src-tauri/tauri.windows.conf.json`、`tauri.android.conf.json` | 各平台安装与运行配置 |
| `clients/yumi/src-tauri/tauri.test.conf.json` | 仅测试包标识和测试入口；不得混入正式构建 |
| `clients/yumi/ui/index.html` | 最小连接错误页，无业务前端框架 |
| `clients/yumi/src-tauri/icons/` | Windows 和 Android 所需品牌图标 |
| `clients/yumi/src-tauri/gen/android/` | 生成的 Gradle 工程、Manifest、Activity；保留可重复构建所需源码，排除缓存及密钥 |
| `clients/yumi/tests/webview_fixture.py` | 仅技术验证的 HTTP fixture：GET/POST 计数、登录重定向、错误页、表单及图片保存故障；标准库即可，无真实账号或业务能力 |
| `clients/yumi/CHANGELOG.md` | 独立客户端版本日志 |
| `clients/yumi/INSTALL.md` | 实测安装、覆盖升级、退出和卸载说明 |
| 根 `.gitignore` | 只新增客户端 target、构建产物、本机 SDK 路径、keystore 与签名配置忽略规则 |

生成工程的 Kotlin 包路径以 `icu.akros.yumi` 为准；Android 返回和文件回调优先落在生成的 `MainActivity` 对应文件。不得修改 Cargo registry 或 Gradle 缓存中的 Tauri/Wry 源码，不以重新生成工程覆盖已维护的适配。只有共享文件确实变得难以维护时才拆平台模块，不预建插件系统。

### 7.1 平台能力实施约束

审查时官方 Rust API 文档为 Tauri 2.11.5；实施以锁定版本再核对，不将 `latest` 文档直接视为锁定版本契约。

| 能力 | Windows 路径 | Android 路径与限制 |
| --- | --- | --- |
| 主视图 | 稳定的 `WebviewWindowBuilder`/配置窗口；创建方式二选一避免重复窗口 | Tauri 生成 Activity、共享 `run` 使用移动入口属性及相应库产物；桌面菜单代码用平台 cfg 隔离 |
| 新窗口 | `on_new_window` 拦截后同源复用主视图 | 官方该回调标为不支持 Android；需验证 WebChromeClient/原生接入点，不能照搬 Rust 回调后声称已支持 |
| 远程网络错误 | 平台 WebView2 的导航完成/错误事件 | WebViewClient 主框架错误事件；子资源错误不替换整页 |
| 文件选择/保存 | WebView2 默认能力优先，原生菜单和下载处理补足 | 验证 Tauri 的文件选择行为、原生命中测试、CookieManager 和文档创建回调；系统选择器 result 生命周期要正确交还 |

Tauri `on_web_resource_request` 的文档说明目前只处理 Tauri 协议，不可拿它拦截线上 HTTPS、重写响应头、读取远程 Cookie 或辨认所有网络错误。不能把 `on_page_load(Finished)` 当作 HTTP 成功证据。Android 适配不得直接替换 Tauri 的 WebViewClient/WebChromeClient 而丢失已有导航、文件选择和对话框行为；必须保留/调用兼容的基类或正式扩展点。若锁定版本无可维护的扩展点，先报告阶段 A 失败证据与最小方案调整，不偷偷 fork 框架。

参考：[稳定 WebviewWindowBuilder API](https://docs.rs/tauri/2.11.5/tauri/webview/struct.WebviewWindowBuilder.html)、[Tauri 移动适配机制](https://v2.tauri.app/develop/plugins/develop-mobile/)。此处为实施边界，不保证这些原生适配已实现。

现有 Python 服务、数据库和权限代码不在默认修改范围。若 F-06 发现阻塞性兼容问题，先给出复现，再将具体模板及 `src/homestay_bot/static/app.css`、`admin.js` 的符号纳入更新后的 Spec。接口或权限变化必须重新确认。

## 8. 实施顺序与阶段门禁

本文确认且用户明确回复“开始”后，才建立实施任务并编码。中等以上实施任务按项目规则使用 `tasks/todo.md`，保留文件中已有无关任务。

### 阶段 A：工具链和双端技术验证

- [ ] 复核工作区、作用域规则、正式入口及构建机器条件；读取需要触及的文件。
- [ ] 在 `clients/yumi/` 创建最小 Tauri 共享库、Windows 入口及 Android 生成工程，锁定依赖。
- [ ] 测试构建加载隔离测试后台，验证登录、首次改密和普通 GET 导航。
- [ ] 两端实测带会话的私有图片保存、文件上传取消、确认对话框和 Android 返回事件。
- [ ] 核对第 7.1 节各能力在锁定版本的公开接入点，记录实际符号、平台代码路径与结果；验证原生退出保护和外跳确认。

退出条件：两端关键平台能力都有运行证据，原生适配可在第 5 节权限边界内完成。无法满足时记录具体失败并更新方案，不带着未验证假设进入完整开发。没有 Windows 验证环境时可继续独立的 Android/共享代码工作，但 Windows 能力标为未验证，不进入“两端技术验证通过”的状态。

### 阶段 B：完成功能和安全边界

- [ ] 实现 F-01 至 F-06，先复用 WebView/操作系统能力，再补已证明必要的平台回调。
- [ ] 在 `lib.rs` 的测试模块覆盖 URL 策略边界；上传下载失败和业务提交不确定性用隔离后台验证。
- [ ] 检查生成模板的远程 IPC、调试、备份和网络配置；删除默认生成但本期不需要的能力。
- [ ] 记录确需修改的网页兼容问题，按第 7 节约束处理。

退出条件：表格中所有功能与安全项有对应实现位置和测试目标，没有以外部浏览器替代私有附件下载的隐藏降级。

### 阶段 C：安装、更新和最终验收

- [ ] 生成并验证 Android 发布密钥备份，构建 release APK 和 Windows NSIS 安装包。
- [ ] 在干净设备验证安装，并用同一标识/签名的递增版本测试覆盖升级；升级测试包不面向员工分发。
- [ ] 差异自审冻结后运行一次最终充分验证；输入未变化不反复跑全量检查。
- [ ] 写入客户端 CHANGELOG、INSTALL，整理安装包、哈希、构建版本、设备矩阵和验收结果。

退出条件：第 9 节必需项全部通过，或者用户明确接受具体缩减后的范围。文档、编译或模拟器截图不能替代真机安装验收。

## 9. 验收矩阵

| 编号 | 场景与判据 | 环境 |
| --- | --- | --- |
| A01 | Windows 安装、启动、关闭、卸载正常；无自建残留后台服务 | Windows 11 x64 干净测试机 |
| A02 | Android 发布签名可核验，安装启动正常；返回根页面退出 Activity，无后台业务服务 | Android 最低版本模拟器及至少一台实际员工手机 |
| A03 | WebView2 缺失时可完成引导；缺网时给出明确安装失败原因 | Windows 干净快照 |
| A04 | 正确/错误密码、首次改密、八小时闲置、停用和会话撤销均按服务端结果处理 | 隔离后台，两端 |
| A05 | 有效会话重启直接进入工作台；退出后受保护页面不可访问；重启不绕过过期；Chrome Cookie 不被导入 | 两端 |
| A06 | 工作台、任务列表与详情、客户详情、房源凭证页可导航，筛选和错误正文正常 | 两端，测试数据 |
| A07 | 测试任务写操作保留确认及 CSRF；重复点击不因客户端增加第二次请求；断网不自动重放 POST | 隔离后台，两端 |
| A08 | 照片和二维码上传成功；中文名、取消、格式拒绝可解释且不产生假成功 | 两端 |
| A09 | 私有图片预览和保存分别通过；无权限、过期返回登录 HTML、外域重定向、取消和空间不足不能产生成功文件提示；重名和失败均不修改已有文件 | 两端 |
| A10 | 360/390 宽度、字体放大、键盘、返回手势、横竖屏及锁屏恢复可完成核心操作；原生关闭/返回遇未保存内容有可靠确认 | Android 模拟器和真机；关闭保护同时验 Windows |
| A11 | 假域名、HTTP、非标准端口、用户信息伪装、脚本/intent URL 被正确处理；远程网页不能调用本机特权 | URL 单元测试及两端运行验证 |
| A12 | 断网/TLS 错误可恢复，业务 HTTP 错误仍可读；重试仅 GET 固定入口 | 隔离故障场景 |
| A13 | 覆盖升级保留应用身份和私有目录；有效/失效会话都按服务端处理，业务数据不被本地安装改动 | 两端，递增测试版本 |
| A14 | 发布包无 debug 开关、测试域名、密钥和通用远程权限；Android 备份禁用配置生效；测试包/正式包可并存，Activity/JNI 可启动，Cookie 相互隔离 | 构建配置、包内容与运行检查 |
| A15 | 密钥备份恢复、证书指纹一致、恢复副本可签测试包；两端产物哈希重新计算一致 | 隔离构建验证 |

拟使用的 Rust 验证命令（实现后在 `clients/yumi/` 工作目录运行，目前未执行；不能在仓库根仅用 manifest-path 就假定自动采用子目录 rust-toolchain）：

```text
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path src-tauri/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml --locked
```

在 `clients/yumi/` 中使用 `cargo tauri build --bundles nsis` 构建 Windows，用 `cargo tauri android build --apk --target aarch64` 构建 Android；实际锁定 CLI 版本后复核参数并确保构建使用锁文件。Windows cfg 代码需在 Windows 上检查；Mac 主机 cargo test 不覆盖该分支，Android target 编译也不等于 Windows 通过。构建通过还需上述安装与业务验收。

### 9.1 测试后台和证据分层

- 阶段 A 使用 `clients/yumi/tests/webview_fixture.py` 生成无敏感数据的页面，提供 Cookie/重定向、带计数的 POST、inline 图片、下载错误、自动外跳及 `target=_blank` 场景。只能证明 WebView 技术能力，不能证明真实业务认证、CSRF 或权限正确。
- `tests/admin_auth_helpers.py` 的 `MemoryAdminCsrfService` 和 `tests/integration/test_admin_auth_routes.py` 的 `AdminAuthStub` 是替身，复用它们只能验证路由/客户端交互，不能证明真实密码校验、CSRF 原子消费或会话撤销。`test_admin_dashboard_routes.py` 的构造方式也只作测试组装参考。
- A04/A07 的业务验证必须连接真实路由、`AdminAuthService`、真实 CSRF 服务及访问复核器，数据放入独立测试数据库/上传目录；外部服务全部替身、后台 worker 禁止访问生产，且不加载生产环境文件。运行可供设备访问的隔离测试实例前逐项证明这些条件。不能以假登录 fixture 冒充真实认证通过。
- 需要新增可访问的业务测试启动入口时，仅增加 `clients/yumi/tests/isolated_backend.py`，复用现有测试构造，不改生产启动逻辑；隔离条件不成立则不启动。API 级过期/撤销测试可用可控时钟，不实际等待八小时；还需两端至少走一次失效会话的可见跳转。
- 手机通过受控本地网络或模拟器宿主地址访问测试实例；只监听测试所需接口，不创建公网转发。测试凭据仅运行时提供，不写入文档、日志或安装包。

URL 单测至少包含：正式域名正常路径、相同有效 HTTPS 端口、伪子域、非标准端口、用户名伪装、HTTP、file、intent、javascript 以及普通外部 HTTPS 链接。外部 HTTPS 只能在明确用户动作下打开系统浏览器，不进入业务视图。

仅增加客户端时不跑 Python 全仓回归；现有页面若确有修改，则运行对应页面测试和两端浏览器/WebView 验证。测试与真实外部结果分开记录，不触发真实 DeepSeek、Hostex 或企业微信调用。

生产只读页面验收需要当次授权和有效登录；真实订单、凭证、员工/客人消息或其他生产写入必须另行授权。未获授权时使用隔离数据完成验证并明确生产项未验，不能假称全部生产通过。

## 10. 风险、交付与完成定义

| 风险/条件 | 处理方式 |
| --- | --- |
| 员工设备版本尚未统计 | 本文列明首版目标，发包前核对实际设备；不支持设备不算验收通过 |
| Android WebView 文件与返回能力存在平台差异 | 阶段 A 先验证，必要时最小 Kotlin 回调；不开放通用远程权限 |
| Windows 无商业签名、Android 厂商安装限制 | 真机试装并记录正常安装步骤；不能安装时报告实际阻碍 |
| 现有网页局部不适合手机 | 复现后只提出阻塞性兼容改动，不扩大为视觉重构 |
| 所有登录者使用管理员权限 | 按用户决策保留，不新增员工权限；安装包本身不携带凭据 |
| 后台网页更新影响旧客户端 | 后续网页发布抽查两端核心流程；客户端降级不能恢复服务器网页 |
| 构建或验收设备不足 | 可以完成源码和已有平台验证，但安装包/平台完成状态必须分别报告 |

最终交付：客户端源码、Windows EXE、Android release APK、哈希清单、客户端更新日志、安装/升级说明，以及包含操作系统、架构、WebView 版本、构建提交和失败/跳过项的验收记录。签名密钥单独受控交接，不与安装包同发。

完成定义：核心两端能力通过、安装和覆盖升级通过、信任边界检查通过、所有实际支持条件明确。不得把“源码已写”“构建成功”“网页能打开”单独称作客户端完成。

粗估工作量为 5–8 个开发工作日，假设构建环境和实际设备可用；平台适配失败或网页范围变化后重新估算。本估算不是交付日期承诺。

本次授权止于编写 Spec。本文完整确认后，等待用户明确“开始”才实施；提交、推送、部署、发包给员工和生产写入仍按当次授权分别执行。

### 给 Claude 的交接约束

本 Spec 是唯一实现范围依据，先读取当前 AGENTS.md 和本 Spec，再核实工作区。用户在 Claude 会话明确授权开始后，先完成阶段 A 并记录证据，再连续推进 B、C；不要因为本文包含阶段清单就自动提交、推送或部署。

名称、标识、最低系统版本等第 1 节默认值仍属 Spec 提案；用户批准整份 Spec 后按这些值执行，不逐项重新追问。实际设备不符合支持范围时报告差异，不暗自扩大兼容层。

已确认无需再次询问的事项：Android + Windows、管理员登录、手动分发、无后台通知、无自动更新、无 macOS/iOS。不得删除安全校验来满足功能，也不得在阶段 A 失败时用纯截图、仅 GET 成功或伪造结果声称客户端可交付。

交回审查时提供最终差异、锁定工具链版本、各平台实际产物和验收表，逐项标注通过/失败/未执行及原因；尤其列明第 7.1 节采用的真实平台接入点。不要输出密钥或客户数据。文档静态审查结论为“可进入阶段 A”，不代表剩余实现风险已被运行证据消除。

## 11. 官方技术依据

- [Tauri 权限与远程 API 边界](https://v2.tauri.app/security/capabilities/)：远程授权和注册命令需要单独审查。
- [Tauri 配置参考](https://v2.tauri.app/reference/config/)：视图、应用标识及平台打包配置以锁定版本为准。
- [Tauri 开发流程](https://v2.tauri.app/develop/)：桌面和 Android 分别开发、构建，不依赖前端框架。
- [Tauri Android APK 构建](https://v2.tauri.app/distribute/google-play/#build-apks)、[签名](https://v2.tauri.app/distribute/sign/android/)及 [Windows 安装包](https://v2.tauri.app/distribute/windows-installer/)：用于选择首版构建和分发路径，不构成对员工设备安装结果的保证。
