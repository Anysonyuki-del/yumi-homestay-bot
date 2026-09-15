# YuMi 客户端阶段 A 交接（第二版）

交接日期：2026-09-14。**取代** `2026-09-13_yumi-client-phase-a-handoff.md`（该文已加标注）。

对应 Spec：
- `2026-09-12_windows-android-client-spec.md`（R2，主 Spec）
- `2026-09-13_android-field-test-fixes-spec.md`（实测问题修复 Spec，Codex 编写）

源码基线 `ec74610`。业务代码改动仅两处（见第 6 节），其余全部为新增。

本文严格区分**运行验证过**与**只读代码推断**。不要跨栏引用，不要把推断当验收。

---

## 1. 一句话现状

R-01/R-02/R-03 三项实测问题全部修复并有真机或测试证据；
Spec 第 7 节列的阶段 A 剩余能力（返回键、错误页、外链确认、图片保存、测试身份、图标）
全部**已实现且有编译与单测证据，但尚无真机运行证据**。
Windows 侧整体未验证。**阶段 A 退出条件仍未满足。**

## 2. 授权边界

- 用户已授权：实施、安装工具链、真机验证。Windows 侧先不做。
- 安装 Android SDK 时**已代为接受 Google SDK 许可**（许可文件在 `$ANDROID_HOME/licenses`）。
- **未授权**：提交、推送、部署、发包给员工、任何生产写入。
- 用户本人用真机连生产后台登录测试，是用户自己的操作，**不构成对 agent 的生产授权**。
- 本仓库在 GitHub 公开。密钥、密码、本机绝对路径、客户数据不得写入任何文件。

## 3. 已验证 / 未验证对照表

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 导航策略正确性 | ✅ | `cargo test` 28 项通过 |
| 系统栏避让（R-01） | ✅ **真机** | 见第 4 节，含精确验算 |
| 键盘避让（R-01） | ✅ **真机** | 见第 4 节 |
| 显示比例（R-02） | ✅ **真机** | 实测无缩放异常，按 Spec 不改 |
| 顶栏透字（R-02） | ✅ 浏览器回归 | alpha 红测先失败后通过 |
| 日历空轨道（R-03） | ✅ 浏览器回归 | 5 条回归 |
| 测试身份隔离（A14） | ✅ 构建产物 | debug 包 applicationId 实测带 `.test` |
| 正式包属性（A14 部分） | ✅ 构建产物 | 见第 7 节 |
| Kotlin 安全边界 | ✅ JVM 单测 | 5 项通过，含反向验证 |
| **返回键实际表现** | ⚠️ **仅编译+单测** | 代码已写，未真机跑 |
| **连接错误界面** | ⚠️ **仅编译** | 未断网实测 |
| **外链确认** | ⚠️ **仅编译+单测** | 去重逻辑有测试，弹窗未实测 |
| **图片保存** | ⚠️ **仅编译+单测** | 同源判定有测试，端到端未实测 |
| **`target=_blank` 行为** | ⚠️ **仅推断** | 仍未反证，见第 8 节 |
| **登录/改密/会话过期** | ⚠️ **未测** | 需隔离测试后台 |
| **Windows 全部能力** | ❌ **未验证** | 无 Windows 环境 |
| macOS 运行结果 | ℹ️ 参考 | 非交付平台，不能推广到 WebView2 或 Android WebView |

## 4. 真机实测数据（vivo V2502DA / Android 16 API 36 / WebView 151 / density 3.5 / 手势导航）

### 4.1 R-01：padding 被证伪，margin 才有效

Spec 原方案是给 WebView 设 padding。**实测无效**：

| | padding 方案 | margin 方案 |
| --- | --- | --- |
| WebView 屏幕坐标 | 0, 0 | **0, 140** |
| WebView 宽 × 高 | 1260 × 2800 | **1260 × 2660** |
| 网页 `innerHeight` | **800** | **760** |
| 顶栏 | 被状态栏压住 | 完整可见 |

判据是 `innerHeight`：WebView 高 2800 物理 px ÷ 3.5 = 800 CSS px。
padding 方案下 `innerHeight` 仍是 800，说明**网页视口根本没缩小**；
`visualViewport.offsetTop` 为 0 也印证了这点。改 margin 后变成 (2800−140)/3.5 = 760，与预期一致。

**教训**：诊断里「已施加 padding 上 140」只证明我传了这个值，不证明它生效。
必须找一个由被测对象自己报告的、能反映最终效果的量（这里是 `innerHeight`）。

实现在 `MainActivity.onWebViewCreate`，父容器实测为 `ContentFrameLayout`，支持 margin。
安全边界取 `systemBars` 与 `displayCutout` 各边较大值，全部来自系统派发，不写死常量。

### 4.2 R-01：键盘避让

键盘弹出时 WebView 高 1614，边距 上 140 / 下 1046。
验算 `2800 − 140 − 1046 = 1614` 吻合，且为**单次补偿**——
底部取 `max(systemBars.bottom, cutout.bottom, ime.bottom)`，
不存在 Spec 第 4 节警告的「resize + padding + safe-area 三重补偿」。

### 4.3 R-02：显示比例不是 bug

`系统 fontScale = 1`（默认未放大）、`visualViewport.scale = 1`、
根元素 `font-size 16px`（浏览器默认）、`innerWidth 360 CSS px`、无整页横向溢出。

1260 物理 px ÷ 3.5 = 360，是小屏手机的正常视口宽度。
用户感知的「偏大」来自 3.5 倍 DPR 下的物理字号，属版式密度问题。
按修复 Spec 第 5 节第一条：**不改 WebView 缩放**。

### 4.4 一条重要的负面结论

`env(safe-area-inset-top/bottom/left/right)` 四项实测**全为 0px**。
页面未声明 `viewport-fit=cover`，CSS 安全区拿不到任何值。
因此「在网页里补状态栏高度」这条路走不通，必须由原生层负责。

## 5. 新增/修改的客户端代码

### 5.1 可维护区与生成区的边界

`gen/android/app/.gitignore` 声明 `/src/main/**/generated` 为生成物。

- ❌ **不能改**：`generated/` 下所有 Kotlin（`WryActivity`、`RustWebView`、
  `RustWebViewClient`、`RustWebChromeClient`）。
- ✅ **可维护**：`MainActivity.kt`、同目录下自建的 Kotlin 文件、
  `app/build.gradle.kts`、`app/identity.gradle`、`AndroidManifest.xml`、`res/`。

### 5.2 Kotlin 侧

| 文件 | 职责 |
| --- | --- |
| `MainActivity.kt` | inset 避让、返回键、错误覆盖层、图片保存入口、诊断注入 |
| `SafeWebViewClient.kt` | 委托包装 Tauri 的 client，补 `onFormResubmission` 与主框架错误 |
| `ImageSaver.kt` | 受保护图片的下载校验与写入 |
| `DiagnosticsGate.kt` | 诊断注入门控（纯函数，可单测） |
| `app/identity.gradle` | 测试包身份隔离 |
| `app/src/test/.../SecurityBoundaryTest.kt` | 5 项 JVM 单测 |

### 5.3 四个关键设计决策及其理由

**① 连接错误页做成原生覆盖层，而不是导航到打包 HTML。**
导航到本地资源就要在导航策略上为本地协议开口子，与 Spec 第 5 节
「来自远程页的任意本地协议导航一律拒绝」冲突。原生绘制完全不产生导航。
重试只 GET 固定工作台入口——地址存在 `strings.xml`，
由 Rust 单测 `android资源里的入口地址与常量一致` 锁定与 `WORKSTATION_URL` 不分叉
（已做反向验证：故意改坏资源，测试立即失败）。

**② `SafeWebViewClient` 用委托而非继承。**
`RustWebViewClient` 是 Kotlin 默认 final 且在 `generated/`，既不能继承也不能改。
直接换一个全新 client 会丢掉 Tauri 的资源拦截（`shouldInterceptRequest`）
和导航策略（`shouldOverrideUrlLoading` → Rust `on_navigation`），等于把安全边界一起换掉。

> **维护约束**：转发清单必须与 `generated/RustWebViewClient.kt` 的 override 列表一致。
> 当前它覆盖 5 个方法：`shouldInterceptRequest`、`shouldOverrideUrlLoading`、
> `onPageStarted`、`onPageFinished`、`onReceivedError`。
> **升级 Tauri/wry 后如果那边新增 override 而这里没跟上，对应行为会静默丢失。**

**③ POST 不重放是显式写出来的，不依赖默认值。**
Android 的 `onFormResubmission` 默认实现确实是不重发，但那是隐式依赖。
`SafeWebViewClient` 显式 `dontResend.sendToTarget()`，让这条安全属性可读可审查。

**④ 图片保存的顺序是「先下载校验、再让用户选位置」。**
反过来做，一旦响应其实是会话失效的 200 登录页 HTML，
用户选中的位置上就已经躺着一份 HTML——正是 Spec F-04 点名禁止的假成功。
实现要点：逐跳同源检查、Cookie 只取自平台 `CookieManager`（不经网页 JS）、
非 2xx 与非受支持图片类型一律拒绝、文件名为中性时间戳（**不沿用私有附件标识**）、
只走「创建新文档」不覆盖既有文件、失败只删本次自建的临时文件。

### 5.4 Rust 侧

`lib.rs` 新增：`bundled_page_path`（打包页面精确判定）、
`should_prompt_external`（外链确认去重，5 秒窗口）、
`confirm_and_open_external`（拦下后弹确认再交系统浏览器）。

新增依赖 **tauri-plugin-dialog 2.7.3**、**tauri-plugin-opener 2.5.5**。
两者**只在 Rust 侧调用**；`capabilities` 仍为空，远程网页无法经 IPC 触达其命令。

## 6. 业务代码改动（仅两处，均有 Spec 依据）

| 文件 | 改动 | 依据 |
| --- | --- | --- |
| `static/app.css` | `.topbar` 由 `rgb(255 255 255 / 82%)` + `backdrop-filter` 改为实色 `var(--surface)` | 修复 Spec 第 5 节 |
| `templates/components/ui.html` | `_calendar_segments` 的 `--rows` 由 `max(bars,3)` 改为真实行数 | 修复 Spec 第 6 节 |

配套 CSS：`@supports selector(:has(*))` 折叠态高度改为 `min(--rows, --preview-rows)`，
否则只改模板不生效（修复 Spec 明确要求两处一起改）。

回归：`tests/browser/test_admin_interactions.py` 新增 7 条
（顶栏 alpha、层级、日历 0/1/2/3/4/6 行）。**全部先红后绿。**

> ⚠️ **网页改动需后端单独发布才会到达生产 APK。重新打包 APK 不会让线上样式更新。**
> 当前仅本地验证，未部署。

## 7. 构建与验证命令

```text
# Rust（在 clients/yumi/）
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo clippy --manifest-path src-tauri/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml --locked          # 28 项

# Kotlin（在 clients/yumi/src-tauri/gen/android/）
./gradlew :app:testUniversalReleaseUnitTest                        # 5 项
#  注意任务名有 flavor 前缀，testReleaseUnitTest 会因歧义失败

# 正式包
cargo tauri android build --apk --target aarch64
# 诊断包（本地测量页，不联网）
cargo tauri android build --apk --target aarch64 -f diagnostics \
  --config src-tauri/tauri.diagnostics.conf.json
```

正式包实测属性：`applicationId=icu.akros.yumi`、`minSdkVersion=29`、
`allowBackup=false`、`usesCleartextTraffic=false`、无 `debuggable`、
`native-code` 仅 `arm64-v8a`、权限仅 INTERNET。

### 7.1 环境坑（换机器必重踩）

1. **默认下载源不可用**：adoptium 约 60 KB/s，dl.google.com 会传坏文件，
   ghcr.io 直接 `HTTP/2 PROTOCOL_ERROR`。国内镜像快 45–120 倍。
   **换源后必须比对官方校验和**（本次每个产物都比对过 SHA1/SHA256）。
2. **Gradle 的 JVM 不读 `HTTP_PROXY`**，会在依赖解析处**静默挂死**：
   日志停住、缓存零增长、无任何报错。必须用 `GRADLE_OPTS` 传
   `-Dhttp.proxyHost/-Dhttp.proxyPort/-Dhttps.proxyHost/-Dhttps.proxyPort`。
   该变量会被 `gradlew` 用 shell eval 展开，**值里不能有未转义的 `|`**。
3. **`cargo tauri android build` 会重写 `app/build.gradle.kts`**，
   并**精准删掉写在那里的 `applicationIdSuffix`**（同文件的 `minSdk`、
   `versionNameSuffix`、注释都保留）。因此身份隔离放在独立的
   `app/identity.gradle` 再 apply 进来，该写法实测在重写后留存。

## 8. 仍未解决 / 已知偏差

**不得当作已解决。**

1. **全部 Android 行为缺真机证据**：返回键、错误界面、图片保存、外链确认，
   目前只有编译与单测。必须真机验证后才能计入 Spec 第 9 节验收。
2. **`target=_blank` 仍是推断**。`RustWebView` 未启用 `setSupportMultipleWindows`，
   wry 也未实现 `onCreateWindow`，据此推断会落入共享导航策略。
   **这是本项目里最久未被反证的假设**，优先安排。
3. **正式包 dex 仍含 `diagnostics.html` 与 `__yumiNative` 字符串**。
   Kotlin 侧是运行时门控而非构建期剔除。该页面在正式构建中既不在打包资源里、
   也被导航策略拒绝，因此不可达；`DiagnosticsGateTest` 证明门控不会命中任何业务地址。
   但「字符串不在包里」这一条**并未达成**。
4. **测试签名密钥已更换**。原密钥随临时目录被清理而丢失，新密钥放在
   `clients/yumi/.local/`（已 gitignore，含 README 说明为何不放临时目录）。
   **新签名与此前发出的 b1–b5 不同，安装 b6 前必须先卸载旧测试包。**
   这不是 Spec 6.2 的正式发布密钥，正式密钥尚未生成。
5. **图标仍为占位图**（后台主色圆角方块 + 白色房子剪影）。
   两端已统一，拿到正式品牌图后重跑 `cargo tauri icon <源图>` 即可。
6. **Windows 侧一切未验证**。

## 9. 建议的下一步顺序

1. 真机验收第 8 节第 1、2 条——这是阶段 A 退出条件的主要缺口。
2. 隔离测试后台跑通 Android 端登录链路（A04/A07）。
   注意 fixture **不能**替代业务验收：无真实账号库、CSRF 服务和权限模型。
   按 Spec 第 9.1 节必须连真实路由与 `AdminAuthService`。
3. 网页侧两处改动的部署（需单独授权）。
4. Windows 环境就绪后再开 Windows 验收。

## 10. 禁止事项

- 不得把第 3、8 节标注为推断/未测的项当作已验收。
- 不得用 macOS 结果替代 Windows 或 Android 验收。
- 不得用 fixture 结果替代 A04/A07 的真实业务认证验收。
- 不得修改 `generated/` 目录或 Cargo registry / Gradle 缓存中的 Tauri/Wry 源码。
- 不得为实现功能删除或削弱安全校验；不得开放远程通配权限或通用文件/Shell/HTTP IPC。
- 未获当次授权不得提交、推送、部署、发包或进行任何生产写入。
- 密钥、密码、本机绝对路径、客户数据不得写入仓库任何文件。

## 11. 方法论教训（建议沿用）

- **判据必须由被测对象报告最终效果**，不能报告「我传了什么值」。
  padding 那次，诊断显示「已施加 140」但实际无效，是 `innerHeight` 拆穿的。
- **下否定结论前走完真实链路**。「返回键默认会无条件 goBack」这个错误结论，
  源于只读了 `WryActivity` 而漏看中间的 `TauriActivity` 覆盖。
- **工具行为本身也要验证**。`strings` 在 macOS 默认只提取 ASCII，
  用它搜中文字符串得到 0 命中，曾两次被误读为「资源没打包」「代码没进包」。
- **安全边界要有可执行的反向证据**。本次对跨语言一致性测试和诊断门控测试
  都做了「故意改坏 → 确认测试失败 → 恢复」的验证。
