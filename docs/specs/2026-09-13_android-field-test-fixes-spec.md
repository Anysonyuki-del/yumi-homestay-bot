# YuMi Android 实测问题修复 Spec

日期：2026-09-13。状态：待确认，仅方案，未修改实现、未构建 APK、未连接生产或手机。

本 Spec 补充 `2026-09-12_windows-android-client-spec.md` R2。输入为用户截图、Claude 阶段 A 交接和当前工作区代码。交接文档中的历史授权、运行结论和建议均为待核对材料，不构成本次执行授权。本文不复述截图中的客户身份信息。

## 1. 目标和范围

先解决 Android 实测的状态栏遮挡、顶部滚动内容透出、显示比例诊断和日历无意义留白，随后补齐阶段 A 原有的返回、图片保存及身份隔离验证。Windows 保持原项目目标，但本轮不实施或宣称已验证。

用户所说“代码阶段完成”表示进入实测，并不代表所有原 Spec 功能已经实现：当前 `MainActivity.onWebViewCreate` 只处理 inset；`lib.rs::run` 只创建视图和拦截导航，外跳确认、网络错误页切换和图片保存等仍不能据此判定完成。

保留工作区已有 `.gitignore`、`tasks/todo.md`、`clients/` 等改动；不重建整个客户端。截图尺寸为物理像素，不能直接当作 CSS viewport 或据此指定全局缩放比例。

## 2. 证据与失效假设

| 问题 | 已确认事实 | 尚不能得出的结论 |
| --- | --- | --- |
| 状态栏与网页标题重叠 | 用户截图中系统时间/图标占用网页标题区域；`MainActivity.onWebViewCreate` 当前对 WebView 设置 systemBars 与 IME padding | 截图是否来自 b2、listener 是否已调用、padding 是否使实际 WebView viewport 避开系统栏均未核实，不能称已修复 |
| 顶部滚动内容可见 | `static/app.css::.topbar` 为 sticky、z-index 20、82% 白色背景和 backdrop blur | 不能只凭截图认定 z-index 错误；透明背景、系统遮挡和正常裁切必须分辨 |
| 整体偏大 | 截图显示的信息密度偏低；`layouts/admin.html` 有标准 viewport；当前 Activity 未写缩放设置 | 尚不能归因系统字体、显示密度、WebView textZoom 或 CSS 中任何一个因素 |
| 日历空白 | `templates/components/ui.html::_calendar_segments` 的轨道容器将 `--rows` 设为条目数与 3 的最大值；`app.css` 手机轨道高 88px | 留白不是房态缺数据；不能删掉真实订单或缩短横向时间跨度解决 |
| 时间条姓名截断 | `.cal__bar-label` 使用省略号；手机另有 `.cal__identity` 完整姓名入口 | 不应给短时间条强制最小宽度，否则改变 15:00/12:00 对应几何位置 |
| 返回键 | `MainActivity → TauriActivity → WryActivity`；实际 `TauriActivity.handleBackNavigation=false` | 交接仅据 WryActivity 默认 true 推断正在无条件 goBack，忽略覆盖，撤销此判断；实际系统返回表现仍待测 |

实施前复核 `ui.html` 宏实际名称及所有调用方，不以本文符号描述替代读取。当前源码结论不代表手机安装包或线上 CSS 一定与本地一致。

交接另有两项需纠正：

- “缺 YUMI_TEST_ENTRY 会编译失败”只约束启用 test-backend 特性的构建，不证明任何 release 都不会带测试来源；必须核对 feature、产物来源和最终包内容。
- 中文下载名可按 RFC 6266 用 `filename*` 编码，不是协议无法支持中文。现有私有文件响应缺少文件名可由本地兜底处理，不能推断服务端为规避中文而刻意省略。[RFC 6266](https://www.rfc-editor.org/rfc/rfc6266#section-4.3)

## 3. 首先建立同一台手机的可比基线

不先改字号或 inset。用无客户数据的 fixture 复现，并记录：

1. APK SHA-256、versionCode、签名指纹和测试来源；将现场旧包、b2、修复包分别编号，不能只比较相同的 0.1.0 文本。
2. Android/API、WebView 版本、机型、横竖屏、手势或三键导航、系统 fontScale/display density。
3. 原生 WebView 的屏幕坐标、宽高、padding/margin、systemBars/displayCutout/IME inset、键盘显隐与可见窗口边界。
4. 网页 `innerWidth/innerHeight`、devicePixelRatio、visualViewport 尺寸/scale，根元素及 `.topbar` 的 computed font-size/line-height/background/z-index 和 bounding rect。
5. 同一台手机以相同系统设置打开同一 fixture：APK 与 Chrome 对照；只比较视口内网页区域，不把浏览器工具栏高度算进网页差异。

诊断只在测试包/隔离页面启用。保存数值和匿名截图，不导出生产 DOM、Cookie、表单正文或客户数据。无法取得设备时允许完成静态方案，但所有运行原因保持未确认。

## 4. R-01 系统栏、刘海和键盘遮挡

修改候选仅为 `clients/yumi/src-tauri/gen/android/app/src/main/java/icu/akros/yumi/MainActivity.kt::onWebViewCreate`，必要时涉及维护区的 Manifest 软键盘配置；不改 generated 类。

默认设计：由原生层统一负责 WebView 的安全可视区域，网页不再补状态栏高度。先测现有 padding；若有效则保留并只补缺失的 cutout/初次派发，不为换写法重构。若 padding 后 fixed/sticky 内容仍绘制在系统栏内，则改为对 WebView 容器或 MarginLayoutParams 留出边界，使实际视图矩形落在安全区域内。

- safeBars 取 systemBars 与 displayCutout 各边最大值；不写固定 24dp/状态栏像素常量。
- 每次由初始布局基值重新计算，不在已有 padding/margin 上累加。应用到已 attach 的视图后请求一次 insets 派发。
- 底部仅有一个 IME 避让责任方。先观察系统 adjustResize 是否已缩小根布局；已缩小时不再扣完整 IME 高度，未缩小时按实际被遮挡区域处理，不能同时 resize、padding 和网页 safe-area 三次补偿。
- 重复键盘打开/关闭和横竖屏后尺寸可恢复；刘海横屏、三键导航也不能覆盖交互入口。保持 WebView 背景和系统图标明暗可读。
- 不降 targetSdk、禁用 edge-to-edge 或隐藏系统栏规避问题。

Android 官方要求 target 35+ 在 Android 15+ 处理 edge-to-edge 的系统栏和 cutout；文档支持 padding/margin，但不证明任意 WebView padding 实现已正确。[Android 官方说明](https://developer.android.com/develop/ui/views/layout/edge-to-edge)

通过标准：键盘关闭时 WebView 有效网页区域不与系统栏/cutout 相交；键盘打开后当前输入框、提交入口可见或可滚动到；循环十次无累积空白，滚动时标题始终不进入状态栏。

## 5. R-02 顶部遮盖与显示比例

先完成 R-01，再复现网页滚动，防止把同一个原生遮挡问题同时在 CSS 中补两次。

### 顶部遮盖

若匿名页面可见卡片文字从 topbar 背景透出，最小修复为 `src/homestay_bot/static/app.css::.topbar` 使用实色 `var(--surface)` 并移除不再需要的 backdrop-filter；保留 sticky、现有 z-index、布局和抽屉层级，不全站提高 z-index。若仅是内容在标题下边缘被正常裁切，不新增卡片 margin。

验收覆盖滚动顶部/中部、抽屉打开、焦点跳转；标题背景不透字，抽屉和遮罩仍高于 topbar，Chrome 和 Android 客户端一致。

### 显示比例

决策由第 3 节的测量决定：

- Chrome 与 APK 在相同有效 viewport、系统缩放下近似一致：不改 WebView 缩放；解决局部留白和布局，不将系统无障碍字号视为故障。
- APK 单独出现非预期初始 scale/textZoom：追踪设置来源，只移除重复或错误设置并恢复平台正常行为；不能固定 `textZoom=100`、`setInitialScale`、CSS zoom/transform 或 user-scalable=no 作为全局补丁。
- viewport 宽度异常：检查原生布局与页面 viewport 是否重复定义；只修实际异常边界，不强制虚假的屏幕宽度。

默认及增大字体/显示设置均必须保留。用户若希望改变视觉密度，优先 R-03 的局部空白优化；本期不引入用户缩放设置页或全站字体改版。

## 6. R-03 日历多余轨道和长文字

触及 `src/homestay_bot/templates/components/ui.html` 中 `_calendar_segments` 的 `--rows`、`src/homestay_bot/static/app.css` 的 `.cal__tracks`、`.cal__segment` 及 compact identity 规则；实施前检查宏所有调用方和 `tests/browser/test_admin_interactions.py` 现有覆盖。

目标：一条订单占一条真实轨道，两条订单不保留第三条空轨道。维持桌面/手机按现有天数分段，不改变日期、入住退房小时、客户关联、冲突轨道和订单状态。

- 模板保留真实条目数作为 `--rows`，有条目时高度按实际行数；空日历沿用已有 empty 分支。
- 当前 `@supports selector(:has(*))` 折叠态又固定使用三行，必须一起修正：预览高度使用实际行数与三者最小值；展开高度使用真实行数。只改模板不算完成。
- 1/2/3 行高度分别精确占 1/2/3 条轨道；4 行以上默认三条，展开显示全部。无 JS 与不支持 :has 的路径仍可读全部必要信息，不把隐藏轨道造成的数据缺失当作紧凑。
- 普通手机行继续使用现有 44px 身份区与 44px 时间条，暂不再缩小点击目标。增大字体/长姓名使身份区与时间条相撞时，允许提高统一身份区高度，并同步 bar-offset/track-height；不对绝对定位标签单独放高后任其覆盖下一条轨道。
- 短时间条允许省略内部标签，完整身份入口和续段说明必须可读、可点；不得给时间条设置 min-width 改变横向时间含义，不按姓名合并订单。

截图中的大段空白与最少三行规则有代码对应，但实际减少量需新旧同 fixture 对照后记录，不能从截图推定字号故障已解决。

## 7. 原阶段 A 未完成项的处置

这些不是截图证明的新 bug，不与界面修复混报完成。

| 项目 | 下一步及边界 |
| --- | --- |
| 返回 | 保留 TauriActivity 已禁用 Wry 默认历史回退的事实；在 MainActivity 通过 OnBackPressedDispatcher 提供应用行为，键盘优先、安全 GET/根页面退出遵循 R2；覆盖未保存确认、登录跳转和 POST 错误页，不能只新增 false 覆盖后称修复 |
| target=_blank | 在 Android 测相对同源、外域、window.open、自动跳转；读到多窗口默认关闭不等于运行验证通过 |
| 外链 | `lib.rs::run` 当前只 Block/Allow，ConfirmExternal 只是枚举结果；后续补用户确认才能计为外链功能完成 |
| 本地错误页 | `is_exact_local_error_page` 和 `open_workstation` 存在不等于 run 已接线；必须验证断网切页、固定 GET 重试及远程无法主动进入本地资源 |
| 图片保存 | 按 R2 实现受控长按/保存、Cookie、同源重定向、拒绝 HTML 伪图片、不覆盖旧文件；缺名时用中性文件名与已确认的 MIME 扩展名，不沿用客户姓名或暴露私有 URL 标识 |
| 测试身份 | 当前 Gradle debug 未见 applicationIdSuffix；先按 R2 隔离测试包和正式包身份/JNI/数据目录，再联调隔离服务。release 与 test-backend 特性不天然互斥，产物检查必须覆盖二者组合 |
| 图标 | 作为原交付清单补齐项；先统一已选图源，不为本轮遮挡修复要求用户重新设计品牌 |
| Windows | 仅保留未验证记录，不将 macOS 测试升级为 Windows 证据 |

交接所述 temporary 测试签名与正式签名不同属于迁移风险，应核对已装包签名。禁止自动卸载清数据；正式分发前提供明确迁移步骤并由用户决定处理测试包。

## 8. 实施顺序、文件和验证

1. 建立设备/包/页面基线，确认截图对应版本。Android 对照先用 `clients/yumi/tests/webview_fixture.py` 添加匿名 sticky、输入框和缩放诊断页，避免在生产试错。
2. R-01 只改 MainActivity 和确有必要的维护区配置；固定同 fixture 做新旧 APK 对照。
3. R-02/R-03 经隔离复现后修改共享 CSS 和宏，并补 `tests/browser/test_admin_interactions.py` 的最小回归。网页改动需要单独后端发布才能到生产 APK，不能靠重新打 APK 宣称线上样式已更新。
4. 执行下列验收，再按 R2 继续原阶段 A 的未完成能力；任务状态分开记录。实施跟踪更新现有 tasks/todo.md 时保留无关内容。

| 编号 | 必需验收 |
| --- | --- |
| V01 | Android 15+ 真机，手势/三键、横竖屏、cutout 下顶部和底部不遮挡 |
| V02 | 键盘十次显隐后页面尺寸恢复，提交入口可达，无双重 IME 留白 |
| V03 | 匿名长页面滚动时标题不透出正文，抽屉、遮罩、焦点顺序不退化 |
| V04 | 360/390 CSS px，默认及放大字体/显示设置：完整身份信息可读、无整页溢出，APK/Chrome 差异有测量解释 |
| V05 | 日历 0/1/2/3/4/6 条、跨段订单、长姓名、冲突、无 JS、展开收起；无空轨道且无数据消失 |
| V06 | 15:00 入住/12:00 退房及分段边界前后，条宽/起点与原时间数据一致 |
| V07 | 新增网页规则在宽屏浏览器不退化；该项不是 Windows 客户端验收 |
| V08 | release 包正式来源、cleartext、debuggable、身份/签名和 test-backend 构建特性可核验；测试包与正式包不共享登录态 |

自动化测试只验证 DOM、几何关系和请求行为，不用自定义数字固定通过的截图断言。Android 单元测试或 HTML fixture 不能替代原生 inset 真机表现。若仅改 Kotlin，不重跑 Python 全仓；若改模板/CSS，跑相关浏览器回归并检查窄屏和桌面。运行命令从当前项目配置确认，不照抄历史通过数量。

测试素材用合成姓名、合成订单和时间，不把本次原始截图或客户信息新增到公开仓库。已有生产页面/截图仅作用户提供证据，不自动登录生产或执行表单。

## 9. 交接和完成条件

本次只新增本 Spec，不更改 Claude 原交接记录；以上失效推断以本 Spec 的证据说明为准。本文只覆盖观察到的问题和原阶段 A 明确缺口，不要求重写框架、复制服务端业务或增加新权限体系。

实现交回时提供：改动文件和符号、修复前后同一匿名场景的数据/截图、APK 哈希与签名指纹、测试矩阵以及失败/未执行项。状态栏“代码已改”“b2 已发送”“用户能登录”均不能替代 V01/V02；网页本地通过也不能替代已部署页面验收。

本 Spec 经用户确认并明确授权开始后再编码。提交、推送、服务器发布、发包和生产写入不包含在当前写方案请求内。无法取得真机或必要设置时，明确保留证据缺口，不能宣布实测问题全部修复。
