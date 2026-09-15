# YuMi 客户端阶段 A 交接（Claude → Codex）

交接日期：2026-09-13。对应 Spec：`docs/specs/2026-09-12_windows-android-client-spec.md`（R2）。
源码基线 `ec74610`，本次工作全部为新增文件，未修改任何现有业务代码。

> **本文档已被 `2026-09-14_yumi-client-phase-a-final-handoff.md` 取代。**
> 其中第 7 节「返回键」一行的结论已被证伪并在下方标注；其余内容仍然有效，
> 但运行验证状态请以新文档为准（本文写作时尚无任何真机证据）。

本文只记录**已经发生的事实**和**据此得出的结论**，并严格区分「运行验证过」与「读代码推断」。
不要把推断项当作已验收；Spec 第 8 节阶段 A 的退出条件目前**尚未满足**。

---

## 1. 当前进度一句话

Android 侧从零到「真机可安装可启动」已打通，导航策略有单元测试和运行证据；
Windows 侧按用户决策**整体标记为未验证**（本机为 macOS，Spec 第 6.1 节禁止用交叉编译替代 Windows 验收）。
阶段 A 仍有若干**只靠读代码推断、缺运行反证**的结论，是下一步的主要工作。

## 2. 授权边界（务必先读）

- 用户已明确回复「开始」，授权进入实施。
- 用户单独授权：在本机安装全套构建工具链；Windows 侧先不做，标为未验证。
- 安装 Android SDK 时**已代为接受 Google SDK 许可**（该授权的必要环节，许可文件在 `$ANDROID_HOME/licenses`）。
- **未授权**：提交、推送、部署、发包给员工、任何生产写入。
- **未授权**：生产数据验收。用户本人用真机连生产后台登录测试是用户自己的操作，不构成对 agent 的生产授权。
- 本仓库在 GitHub 上是公开仓库。密钥、密码、本机绝对路径、客户数据一律不得写入任何文件。

## 3. 已验证 / 未验证对照表

这张表是本次交接最重要的内容。**不要跨栏引用**。

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 导航策略正确性 | ✅ 已验证 | `cargo test` 22 个单元测试通过，覆盖伪子域、同形异义、用户信息伪装、非标准端口、危险 scheme、IP 字面量、测试来源隔离 |
| `on_navigation` 真的会触发 | ✅ 已验证（macOS） | 测试构建加载 fixture，日志 `http://127.0.0.1 -> AllowInApp`、`https://example.com -> ConfirmExternal` |
| 无用户手势的自动外跳被拦住 | ✅ 已验证（macOS） | 入口指向 fixture 的 `/autoredirect`，fixture 日志 `GET /autoredirect -> 302`，客户端拦下后未外跳 |
| 测试地址不进正式包 | ✅ 已验证 | 缺 `YUMI_TEST_ENTRY` 时编译失败；release APK 内检索不到测试地址，正式入口在 `.so` 内 |
| Android release APK 可构建 | ✅ 已验证 | `cargo tauri android build --apk --target aarch64` 成功 |
| APK 包属性合规 | ✅ 已验证 | `minSdkVersion=29`、`targetSdk=36`、`native-code` 仅 `arm64-v8a`、`usesCleartextTraffic=false`、无 `debuggable`、`allowBackup=false`、权限仅 INTERNET |
| 真机可安装可启动 | ✅ 已验证 | 用户真机实测，能装能启动，后台页面正常渲染 |
| fixture 场景行为 | ✅ 已验证 | 22 项场景全通过（见第 6 节） |
| **Android 上 `target=_blank` 的实际行为** | ⚠️ **仅推断** | 见第 7 节，必须真机反证 |
| **Android 返回键实际表现** | ⚠️ **仅推断** | 默认实现已知与 Spec F-03 冲突，但未实测 |
| **私有图片长按保存** | ⚠️ **未实现也未测** | 接入点已确认空闲，功能未写 |
| **登录 / 首次改密 / 会话过期** | ⚠️ **未测** | 需连隔离测试后台，尚未在 Android 上跑通 |
| **Windows 全部能力** | ❌ **未验证** | 无 Windows 构建环境 |
| macOS 上的运行结果 | ℹ️ 参考 | macOS **不是交付平台**。它只证明共享 Rust 代码可用，不能推广到 WebView2 或 Android WebView |

## 4. 构建环境

### 4.1 锁定版本

| 组件 | 版本 |
| --- | --- |
| Rust | 1.98.1（`clients/yumi/rust-toolchain.toml` 已锁定） |
| tauri / tauri-build / tauri-cli | 2.11.5 / 2.6.3 / 2.11.4 |
| wry / tao / url | 0.55.1 / 0.35.3 / 2.5.8 |
| JDK | Eclipse Temurin 17.0.20.1（装在用户级 JVM 目录，无需 sudo） |
| Android cmdline-tools | 12.0 |
| platform-tools | 37.0.1 |
| platforms | android-36 |
| build-tools | **35.0.0**（AGP 8.11 实际要求）与 36.0.0 |
| NDK | **27.3.13750724**（r27d） |
| Gradle / AGP / Kotlin | 8.14.3 / 8.11.0 / 1.9.25 |

Rust target 已装：`aarch64-linux-android`、`armv7-linux-androideabi`、`i686-linux-android`、`x86_64-linux-android`。
首版只交付 ARM64（Spec 第 6.1 节）。

### 4.2 三个必须知道的环境坑

**坑一：默认下载源在本网络下不可用。**
adoptium 约 60 KB/s；dl.google.com 约 34–546 KB/s 且会传坏文件（实测 build-tools 下载后
`Error on ZipFile unknown archive`）；ghcr.io 直接 `curl: (92) HTTP/2 PROTOCOL_ERROR`。
国内镜像快 45–120 倍。**换源后必须比对官方校验和**——本次每个产物都比对过：
JDK 对 Adoptium API 的 SHA256，SDK/NDK/build-tools 对 Google `repository2-3.xml` 的 SHA1，
Gradle 对官方 `.sha256`。全部一致才使用。不要因为换源就跳过校验。

**坑二：Gradle 的 JVM 不读 `HTTP_PROXY` 环境变量。**
环境里有代理也没用，Gradle 会在依赖解析处**静默挂死**：日志停在
`Starting build in new daemon` 之后再无输出，缓存零增长，没有任何报错。
本次是靠 `jstack` 抓线程栈才定位到阻塞在 `DownloadArtifactFile`。
必须通过 `GRADLE_OPTS` 传 `-Dhttp.proxyHost/-Dhttp.proxyPort/-Dhttps.proxyHost/-Dhttps.proxyPort`。
注意 `GRADLE_OPTS` 会被 `gradlew` 用 shell eval 展开，值里**不能出现未转义的 `|`**
（`-Dhttp.nonProxyHosts=a|b` 会被当成管道，报 `command not found`）。

**坑三：Gradle 依赖走官方源同样极慢（约 34 KB/s）。**
本次解法是**会话级** `GRADLE_USER_HOME`：指向一个临时目录，里面 symlink 复用真实的
`~/.gradle/wrapper` 与 `~/.gradle/caches`，另加一个 `init.d/mirrors.gradle` 把仓库换成国内镜像。
这样**不修改用户全局 Gradle 配置，也不把镜像地址写进生成工程**，别处构建仍走官方源。
Gradle 分发本身也已按其缓存命名规则（分发 URL 的 MD5 转 Base36）预置，
因此 `gradle-wrapper.properties` 保持官方 URL 不变，可复现性未受影响。

这三条应当写进最终的 `INSTALL.md` / 交接说明，否则换台机器会重踩。

## 5. 代码现状

### 5.1 文件清单（全部为新增）

```
clients/yumi/
├── rust-toolchain.toml              锁定 1.98.1 + android target
├── src-tauri/
│   ├── Cargo.toml / Cargo.lock      依赖锁定；含 test-backend 特性
│   ├── build.rs                     仅调官方 tauri_build::build()
│   ├── tauri.conf.json              身份/版本/空 capabilities
│   ├── src/lib.rs                   ★ 导航策略 + 共享 run + 22 个单元测试
│   ├── src/main.rs                  Windows 入口
│   ├── icons/                       占位图标（见 8.4）
│   └── gen/android/                 生成工程，其中三处为我们维护（见 5.3）
├── ui/index.html                    打包连接错误页，无 JS、无 IPC
└── tests/webview_fixture.py         隔离测试后台，标准库，22 场景
```

根 `.gitignore` 已加客户端构建产物与 `*.keystore` / `*.jks` / `keystore.properties` 忽略规则。

### 5.2 关键设计决策：允许来源 = 编译期入口 URL 的来源

Spec 原文假定硬编码允许 `https://akros.icu`。实施时发现这样**测试构建进不去自己的测试后台**
（fixture 在 `http://127.0.0.1:8765`），于是按 Spec 第 5 节「测试地址只经构建配置指定」重构为：

- `WORKSTATION_URL` 是编译期常量，**它的来源就是唯一允许的来源**，不另设可能分叉的白名单。
- 正式构建：`https://akros.icu/employee/admin`。
- 测试构建：启用 `test-backend` 特性，地址由编译期 `env!("YUMI_TEST_ENTRY")` 注入。
  **缺这个变量会编译失败**（已实测），所以正式构建不可能含测试地址——这是 A14 的实现手段，不是承诺。
- `classify_url_against(origin, url)` 是可测核心，判定顺序固定：
  先拒带用户信息的 URL → 再判是否与允许来源完全同源 → 剩下的按外部链接处理
  （必须 HTTPS + 默认端口 + 域名主机才给 `ConfirmExternal`，其余 `Block`）。
- 同源比较用解析后的 scheme/host/`port_or_known_default`，**不做任何字符串前缀后缀匹配**。

### 5.3 生成工程里哪些能改、哪些不能

`gen/android/app/.gitignore` 声明 `/src/main/**/generated` 等为生成物，**每次构建重新生成**。

- ❌ **不能改**：`gen/android/app/src/main/java/icu/akros/yumi/generated/` 下所有 Kotlin
  （`WryActivity.kt`、`RustWebView.kt`、`RustWebViewClient.kt`、`RustWebChromeClient.kt` 等）。
- ✅ **可维护**：`MainActivity.kt`、`app/build.gradle.kts`、`app/src/main/AndroidManifest.xml`、`app/src/main/res/`。

本次在可维护文件里做了三处改动，均有 Spec 依据：

1. `app/build.gradle.kts`：`minSdk` 24 → **29**（Spec 第 1 节 Android 10+）。
2. `AndroidManifest.xml`：补 `allowBackup="false"` + `fullBackupContent` + `dataExtractionRules`，
   并新增 `res/xml/backup_rules.xml`、`res/xml/data_extraction_rules.xml`
   排除云备份与换机直传（Spec 第 5 节禁止备份迁移认证数据）。生成工程默认**没有**设 `allowBackup`，
   即默认为 `true`，会让会话 Cookie 随云备份离开设备——这是个真实安全缺口，已修。
3. `MainActivity.kt`：在 `onWebViewCreate` 中按 `systemBars() or ime()` inset 给 WebView 加 padding。
   **注意**：targetSdk 35+ 在 Android 15 起强制全屏绘制，删掉 `enableEdgeToEdge()` 无效，必须自行处理 inset。

## 6. 隔离测试后台

`clients/yumi/tests/webview_fixture.py`，仅标准库，测试凭据由运行时参数提供。
覆盖场景：登录重定向、一次性 CSRF（重放必失败）、带计数的 POST 与 PRG、
inline 私有图片、**失效会话返回 200 登录 HTML**、403、外域重定向、中断传输、
中文文件名上传、取消选择、格式拒绝、自动外跳、`target=_blank`、慢响应、业务 500。
22 项场景已全部验证通过（验证脚本在 scratchpad，未入库）。Ruff 与 mypy 干净。

**它能证明什么、不能证明什么**：只能证明 WebView 外壳的平台能力。
**不能**用于 A04/A07 的业务验收——没有真实账号库、真实 CSRF 服务和权限模型。
按 Spec 第 9.1 节，A04/A07 必须连真实路由、`AdminAuthService`、真实 CSRF 服务与访问复核器。

## 7. Spec 第 7.1 节接入点核对结果（★ 核心产出）

以下全部基于 Tauri 2.11.5 生成代码的**实际阅读**，除注明外**均未运行验证**。

| 能力 | 实测结论 | 置信度 |
| --- | --- | --- |
| 导航策略 | **两端共享**。`RustWebViewClient.shouldOverrideUrlLoading` 与 `RustWebView.loadUrl` 都调 `Rust.shouldOverride(id, url)`，即 Rust 侧 `on_navigation`。已写好的 `classify_url` 在 Android 上同样生效，无需另写一套。**比 Spec 预期好** | 高（代码路径明确） |
| 新窗口 | `RustWebView` 只设了 `javaScriptCanOpenWindowsAutomatically = true`，**未启用 `setSupportMultipleWindows`**，wry 也**未实现 `onCreateWindow`**。该配置下 Android WebView 会把 `target=_blank` 当同视图普通导航处理，因而落入上面的共享策略 | ⚠️ **仅推断，必须真机反证** |
| 返回键 | ~~默认实现是 `canGoBack() → goBack()` 无条件回退历史，与 Spec F-03 冲突~~ **本行结论有误，已于 2026-09-13 撤销**：`WryActivity.handleBackNavigation` 虽默认 true，但 `generated/TauriActivity.kt:35` 已 `override val handleBackNavigation: Boolean = false`，Wry 的默认回退根本没有生效。当时只读了 WryActivity 就下结论，漏看中间那一层。`onWebViewCreate(webView)` 是 `open fun` 可直接覆盖仍然成立 | ❌ 已证伪，见 2026-09-14 交接文档 |
| 文件选择 | `RustWebChromeClient.onShowFileChooser` 已完整实现（含权限请求与 ActivityResult 生命周期），F-04 上传侧可直接复用 | 高／未测 |
| 网络错误 | `RustWebViewClient.onReceivedError` 存在且区分 `isForMainFrame`，可用于 F-05 整页错误判断，不会被子资源错误误触发 | 高／未测 |
| 下载 / 长按保存 | wry **未设置 `DownloadListener`、未占用长按与命中测试**，三个接入点在 `onWebViewCreate` 中完全空闲。平台 `CookieManager` 可用（wry 自身也在用），满足 F-04 第 2 条「只从平台 Cookie 管理器读取」 | 高／功能未实现 |

**一条硬约束**：`RustWebChromeClient` 与 `RustWebViewClient` 都是 Kotlin 默认的 `final` 类，
**无法继承**。将来若确需改写其行为，唯一受支持的路径是在 `onWebViewCreate` 里用**委托包装**，
既不能靠继承，也不能改 `generated/` 目录。若连委托都不可行，按 Spec 第 7.1 节末段
**报告阶段 A 失败证据并调整方案，不得偷偷 fork 框架**。

## 8. 需并入 Spec 修订的发现

### 8.1 私有附件不发 `Content-Disposition`

`routes/private_files.py::download_private_file` 与 `routes/properties.py::download_property_qr`
都以 `filename=None` 构造 `FileResponse`，只设 `media_type`、`Cache-Control: no-store`、
`X-Content-Type-Options: nosniff`。

**对客户端的后果**：原生「保存图片」**拿不到服务器给定的文件名**，必须自行从 URL 末段与
响应 `Content-Type` 推导，再按 Spec F-04 第 5 条清理路径成分与危险字符。
Spec 第 2 节未记录这一点。

顺带：HTTP 头只能是 latin-1，中文文件名本来就放不进 `Content-Disposition`，
真实后台回避该问题的方式正是不发该头。客户端不应假定能从响应头取到中文名。

### 8.2 Spec 第 1 节的默认窗口尺寸只对 Windows 有意义

`WebviewWindowBuilder` 的 `inner_size` / `min_inner_size` / `resizable` 在移动端无意义，
当前用 `#[cfg(desktop)]` 隔离。Android 跟随系统，未强制方向或尺寸，符合 Spec。

### 8.3 真机发现的 UI 问题（用户 2026-09-13 反馈）

- **状态栏遮挡**：已在 `MainActivity` 修复（见 5.3 第 3 条），等待用户复测确认。
- **整体偏大 / 缩放**：根因未定位。`RustWebView` 未设任何缩放项（全用 Android 默认），
  后台模板 viewport 为标准 `width=device-width, initial-scale=1`，两侧都无明显错误配置，
  最可能是系统字体或显示大小设置在生效。**已向用户询问设置值，尚未回复。**
  注意 Spec F-06 要求「字体放大后仍可导航和提交」——系统字体放大是**必须支持的场景，不是 bug**。
- **标题与卡片内容重叠**：疑似后台布局 sticky 头部未盖住滚动内容。若复测确认，
  属**网页侧**问题；按 Spec 第 7 节，改后台模板必须先复现、再把具体模板与
  `static/app.css`、`admin.js` 的符号纳入更新后的 Spec，**不在当前授权范围内**。

### 8.4 Android 启动图标仍是 Tauri 默认图标

`cargo tauri android init` 生成的 `res/mipmap-*/ic_launcher*.png` 是 **Tauri 模板默认图标**，
没有使用 `src-tauri/icons/` 下的图。桌面侧 `tauri.conf.json` 引用的 `icons/*.png` 才是占位图
（后台主色 `#2563eb` 的圆角方块 + 白色民宿剪影，纯标准库脚本生成）。

**待办**：拿到用户提供的正式品牌图后跑 `cargo tauri icon <源图>`，它会同时生成桌面图标与
Android mipmap。目前**两端图标不一致**，发包前必须统一。

## 9. 下一步优先级

1. **补上真机反证**（最高优先）。第 7 节标 ⚠️ 的推断项必须跑实，尤其
   `target=_blank` 的实际行为——它是 Spec 第 7.1 节原本最担心的风险点，
   目前的乐观结论完全建立在读代码上。**在不熟悉的框架里下否定结论的门槛应当更高。**
2. **覆盖 Android 返回键**。（注：`handleBackNavigation = false` 已由 `TauriActivity` 设好，
   不需要再覆盖一次；本条写于该事实被发现之前。）
   在 `MainActivity` 自行实现 Spec F-03 的顺序：先收键盘/选择器 → 页面弹层用页面自己的入口 →
   再回退**可安全导航**的历史 → 根页面确认退出。原生层无法证明目标是安全 GET 时，
   确认后 GET 固定工作台，**不能盲目调 `history.back`**。
3. **实现 F-04 私有图片保存**。接入点已确认空闲；严格遵守 Spec F-04 的 6 条规则，
   特别是「失效 Cookie 返回的 200 登录 HTML 不能存成图片并报告成功」——
   fixture 的 `/private/expired.png` 就是为这条准备的固定复现路径。
4. **隔离测试后台跑通 Android 端登录链路**。需要 fixture 监听受控局域网地址
   （Spec 第 9.1 节：只监听测试所需接口，不创建公网转发），
   且测试构建须用 `icu.akros.yumi.test` 标识与独立数据目录（Spec 第 5 节，**当前尚未实现**）。
5. **等 Windows 环境**。在此之前 Windows 侧一切结论保持「未验证」，
   不得因为共享代码在别的平台通过就推广。

## 10. 明确的禁止事项

- 不得把第 3、7 节标注为推断/未测的项当作已验收。
- 不得用 macOS 运行结果替代 Windows 或 Android 验收。
- 不得用 fixture 结果替代 A04/A07 的真实业务认证验收。
- 不得修改 `generated/` 目录或 Cargo registry / Gradle 缓存中的 Tauri/Wry 源码。
- 不得为实现功能删除或削弱安全校验；不得开放远程通配权限或通用文件/Shell/HTTP IPC。
- 不得在阶段 A 失败时用截图、仅 GET 成功或伪造结果声称客户端可交付。
- 未获当次授权不得提交、推送、部署、发包或进行任何生产写入。
- 密钥、密码、本机绝对路径、客户数据不得写入仓库任何文件（本仓库公开）。

## 11. 分发状态

已向用户发送两个**测试签名**的 APK 用于真机验证：
`YuMi-Workstation_0.1.0_android-arm64_phaseA-testsign.apk` 与 `...-b2.apk`（b2 含状态栏修复）。

签名用的是一次性测试密钥（`OU=NOT FOR RELEASE`，4096 位 RSA，v3 签名方案），
**密钥与密码只存在于本次会话的临时目录，未入库、未记入本文**。
Spec 第 6.2 节要求的**正式发布 keystore 尚未生成**（属阶段 C）。

**后果**：将来换正式签名时，这两个测试包必须**先卸载**才能安装正式包——签名身份不同，
系统会拒绝覆盖升级。这一点必须在发给员工前讲清楚，避免员工装了测试包后无法升级。
