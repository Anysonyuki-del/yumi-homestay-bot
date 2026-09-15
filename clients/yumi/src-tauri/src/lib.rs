//! YuMi 工作台客户端共享逻辑。
//!
//! 本文件是 Windows 与 Android 共享的入口库，承担两件事：
//! 一是与平台无关的导航策略判定（纯函数，可单元测试）；
//! 二是两端共用的应用启动流程。
//!
//! 业务页面全部由服务器返回，这里不复制任何业务接口、认证逻辑或 CSRF 处理。

#[cfg(not(feature = "diagnostics"))]
use std::sync::Mutex;
use std::sync::OnceLock;
#[cfg(not(feature = "diagnostics"))]
use std::time::{Duration, Instant};
use url::{Host, Url};

/// 本次构建的工作台入口。
///
/// 值由 `build.rs` 按构建模式算出并通过 `cargo:rustc-env` 注入，
/// 同一个值同时写进 Android 的 `values/generated_entry.xml`，
/// 因此 Rust 导航策略、原生重试与图片来源判定用的是同一份来源，
/// 不可能像先前那样一侧是测试地址、另一侧是生产地址。
///
/// 允许在应用内打开的来源就是这个 URL 的来源（scheme + 主机 + 有效端口），
/// 不另设一份可能与入口分叉的白名单。
pub const WORKSTATION_URL: &str = env!("YUMI_ENTRY");

/// 本次构建的模式：production / isolated-test / diagnostics。
///
/// 仅用于自检与诊断展示，不参与任何安全判定——
/// 判定一律基于上面的入口来源本身。
pub const BUILD_MODE: &str = env!("YUMI_BUILD_MODE");

/// 解析一次入口 URL 并缓存，供导航策略比对来源。
///
/// 入口常量在编译期固定，解析失败属于构建配置错误，应当直接 panic，
/// 而不是退化成一个“什么都不允许”或“什么都允许”的策略。
fn allowed_origin() -> &'static Url {
    static ORIGIN: OnceLock<Url> = OnceLock::new();
    ORIGIN.get_or_init(|| {
        WORKSTATION_URL
            .parse()
            .expect("入口 URL 必须是合法的绝对 URL")
    })
}

/// 判断两个 URL 是否同源。
///
/// 按 scheme、主机、有效端口三项比较。使用 `port_or_known_default`
/// 是为了让显式写出的默认端口（如 `:443`）与省略端口等价。
fn same_origin(a: &Url, b: &Url) -> bool {
    a.scheme() == b.scheme()
        && a.host() == b.host()
        && a.port_or_known_default() == b.port_or_known_default()
}

/// 顶层导航的处置结果。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NavigationDecision {
    /// 正式业务域名的同源导航：在当前主视图加载。
    AllowInApp,
    /// 合法的外部 HTTPS 链接：阻止在应用内加载，经用户确认后交系统浏览器。
    ConfirmExternal,
    /// 一律拒绝，且不提供任何外跳入口。
    Block,
}

/// 判定一个顶层导航目标应当如何处置。
///
/// 输入是远程页面或用户操作产生的原始 URL 字符串。解析失败一律拒绝，
/// 不做任何字符串前缀或后缀匹配。
pub fn classify_navigation(raw: &str) -> NavigationDecision {
    match Url::parse(raw) {
        Ok(url) => classify_url(&url),
        Err(_) => NavigationDecision::Block,
    }
}

/// 已解析 URL 的导航策略，比对当前构建编译进来的入口来源。
pub fn classify_url(url: &Url) -> NavigationDecision {
    classify_url_against(allowed_origin(), url)
}

/// 导航策略本体，允许来源作为参数传入以便单元测试覆盖多种构建配置。
///
/// 判定顺序是固定的：先排除带用户信息的 URL，再判断是否与允许来源完全同源，
/// 最后才按外部链接处理。任一步不满足即得出结论，不依赖后续条件兜底。
pub fn classify_url_against(origin: &Url, url: &Url) -> NavigationDecision {
    // 用户信息伪装：https://akros.icu@evil.example 的实际主机是 evil.example。
    // 业务服务与可确认的外部链接都不需要 URL 内嵌凭据，一律先拒绝，
    // 避免后面的同源判断被一个看起来眼熟的用户名骗过去。
    if !url.username().is_empty() || url.password().is_some() {
        return NavigationDecision::Block;
    }

    // 与允许来源完全同源才进入业务视图。这里比较的是解析后的
    // scheme、主机和有效端口，不做任何字符串前缀或后缀匹配，
    // 因此 akros.icu.evil.example、evil-akros.icu、非标准端口都不会命中。
    // 同形异义域名（如西里尔字母 а）在解析时已转成 punycode，同样落空。
    if same_origin(url, origin) {
        return NavigationDecision::AllowInApp;
    }

    // 剩下的只可能作为外部链接处理，条件比业务来源更严：
    // 必须是 HTTPS、默认端口、可供用户辨认的域名主机。
    // 明文 HTTP、file、intent、javascript、自定义 scheme
    // 以及 Tauri 本地协议都在这里落到拒绝分支；打包错误页不走本函数，
    // 由原生控制层通过 is_exact_local_error_page 单独放行。
    if url.scheme() != "https" || url.port().is_some() {
        return NavigationDecision::Block;
    }

    match url.host() {
        // 外部域名：不进入业务视图，只能在用户确认后交系统浏览器。
        Some(Host::Domain(_)) => NavigationDecision::ConfirmExternal,
        // IP 字面量没有可供用户辨认的域名，确认提示失去意义，直接拒绝。
        _ => NavigationDecision::Block,
    }
}

/// 判断目标是否为本客户端打包的那一个连接错误页。
///
/// 错误页只允许原生控制层在连接失败时自行进入，且必须是不带查询串、
/// 不带片段的精确资源路径；来自远程页的任意本地协议导航仍由
/// `classify_url` 拒绝。
///
/// Tauri 2 在不同平台使用不同的本地资源来源（`tauri://localhost` 与
/// `http://tauri.localhost`），两种形式都在这里接受；实际生效的形式
/// 必须在阶段 A 于各平台运行确认，不能只依据本函数声称已隔离。
pub fn is_exact_local_error_page(raw: &str) -> bool {
    matches!(bundled_page_path(raw), Some("/") | Some("/index.html"))
}

/// 取出一个「干净的打包页面 URL」的路径，不是这种 URL 则返回 None。
///
/// 干净的定义：Tauri 本地资源来源、无查询串、无片段、无用户信息。
/// 任何一项不满足都直接落空，因此远程页面无法借由本地协议携带参数进来。
///
/// Tauri 2 在不同平台使用不同的本地资源来源（`tauri://localhost` 与
/// `http://tauri.localhost`），两种形式都在这里接受；实际生效的形式
/// 必须在各平台运行确认，不能只依据本函数声称已隔离。
fn bundled_page_path(raw: &str) -> Option<&'static str> {
    let url = Url::parse(raw).ok()?;

    if url.query().is_some() || url.fragment().is_some() {
        return None;
    }
    if !url.username().is_empty() || url.password().is_some() {
        return None;
    }

    let host_ok = match (url.scheme(), url.host()) {
        ("tauri", Some(Host::Domain(domain))) => domain.eq_ignore_ascii_case("localhost"),
        ("http", Some(Host::Domain(domain))) => domain.eq_ignore_ascii_case("tauri.localhost"),
        _ => false,
    };
    if !host_ok {
        return None;
    }

    // 只认打包时确实存在的页面，返回 'static 字面量而非借用输入，
    // 避免把任意路径当作「本地页面」放行。
    match url.path() {
        "/" => Some("/"),
        "/index.html" => Some("/index.html"),
        "/diagnostics.html" => Some("/diagnostics.html"),
        _ => None,
    }
}

/// 诊断构建的本地测量页是否为该 URL。
///
/// 只有启用 `diagnostics` 特性的构建才会用到；正式构建里这个页面
/// 既不是启动入口，也不会被导航策略放行。
#[cfg(feature = "diagnostics")]
fn is_diagnostics_page(raw: &str) -> bool {
    matches!(bundled_page_path(raw), Some("/diagnostics.html"))
}

/// 记录一次导航判定，用于阶段 A 观察策略是否真的生效。
///
/// 刻意只输出 scheme、主机和判定结果：路径可能带私有附件标识，
/// 查询串可能带业务参数，都不进日志。更不记录 Cookie、表单正文或凭据。
fn log_navigation(url: &Url, decision: NavigationDecision) {
    let host = url.host_str().unwrap_or("(无主机)");
    eprintln!("[导航] {}://{} -> {:?}", url.scheme(), host, decision);
}

/// 外链确认的在途与去重状态。
///
/// 两件事必须分开：
/// - **在途互斥**：同一时刻只允许一个确认框。五秒去重只按 URL 比对，
///   页面连续跳到几个不同外域时它一个都拦不住，确认框会叠起来。
/// - **短时去重**：一次点击可能触发多跳重定向，每跳都会再走一遍导航策略，
///   没有去重就会对同一个目标连问几次。
#[cfg(not(feature = "diagnostics"))]
#[derive(Default)]
struct ExternalPromptState {
    /// 已弹出但尚未得到用户答复的确认框。
    in_flight: bool,
    /// 最近一次询问过的目标与时刻。
    last: Option<(String, Instant)>,
}

#[cfg(not(feature = "diagnostics"))]
impl ExternalPromptState {
    /// 判断是否应当为该目标弹出确认框；返回 true 时即占用在途名额。
    fn should_prompt(&mut self, url: &str, now: Instant) -> bool {
        if self.in_flight {
            return false;
        }
        if let Some((last, at)) = &self.last {
            if last == url && now.duration_since(*at) < EXTERNAL_PROMPT_DEDUP {
                return false;
            }
        }
        self.last = Some((url.to_string(), now));
        self.in_flight = true;
        true
    }

    /// 确认框关闭后释放在途名额。确认、取消和异常路径都必须走到这里，
    /// 否则一次失败会让外链功能此后一直沉默。
    fn finish(&mut self) {
        self.in_flight = false;
    }
}

#[cfg(not(feature = "diagnostics"))]
static EXTERNAL_PROMPT: OnceLock<Mutex<ExternalPromptState>> = OnceLock::new();

/// 同一外链在这个时间窗内不重复弹确认。
#[cfg(not(feature = "diagnostics"))]
const EXTERNAL_PROMPT_DEDUP: Duration = Duration::from_secs(5);

/// 取回全局状态；锁被毒化也要继续工作，不能把用户永久拦在外面。
#[cfg(not(feature = "diagnostics"))]
fn external_prompt_state() -> &'static Mutex<ExternalPromptState> {
    EXTERNAL_PROMPT.get_or_init(|| Mutex::new(ExternalPromptState::default()))
}

#[cfg(not(feature = "diagnostics"))]
fn release_external_prompt() {
    let mut guard = external_prompt_state()
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    guard.finish();
}

/// 拦下外部链接，经用户确认后交给系统浏览器。
///
/// `on_navigation` 拿到的只有 URL，证明不了这次导航来自用户点击，
/// 所以这里一律先阻止，再显示带目标域名的原生确认；用户不操作就什么都不会发生。
/// 只把 URL 交给系统浏览器，不传 Cookie、认证头或任何本应用的会话信息。
#[cfg(not(feature = "diagnostics"))]
fn confirm_and_open_external(app: &tauri::AppHandle, url: &Url) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
    use tauri_plugin_opener::OpenerExt;

    let target = url.as_str().to_string();
    let host = url.host_str().unwrap_or("未知站点").to_string();

    {
        let mut guard = external_prompt_state()
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if !guard.should_prompt(&target, Instant::now()) {
            return;
        }
    }

    let handle = app.clone();
    let opened = target.clone();
    app.dialog()
        .message(format!(
            "即将离开 YuMi 工作台，在系统浏览器中打开：\n\n{host}\n\n\
             系统浏览器不会带上你在工作台的登录状态。"
        ))
        .title("打开外部链接？")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "用浏览器打开".to_string(),
            "留在工作台".to_string(),
        ))
        .show(move |confirmed| {
            // 无论确认、取消还是打开失败，都必须释放在途名额。
            if confirmed && handle.opener().open_url(opened, None::<&str>).is_err() {
                // 只记固定类别：插件错误里可能带完整 URL 及其查询参数。
                eprintln!("[外链] 交给系统浏览器失败");
            }
            release_external_prompt();
        });
}

/// 把主视图导航回固定的工作台入口。
///
/// 供「重新打开工作台」菜单与连接错误页的重试使用。只发普通 GET 导航，
/// 永远不重放上一次的 POST，也不接受调用方传入的任意地址。
pub fn open_workstation(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    window.navigate(allowed_origin().clone())
}

/// Windows 与 Android 共享的应用启动流程。
///
/// 只做三件事：建立唯一的业务主视图、把导航策略挂到该视图上、交给 Tauri 运行。
/// 不注册任何 invoke 命令，配置中的 capabilities 为空，
/// 因此远程网页拿不到任何本机特权。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // 两个插件只在 Rust 侧调用。capabilities 为空，
        // 远程网页无法通过 IPC 触达它们注册的任何命令。
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            // 窗口只在这里创建一次。tauri.conf.json 的 windows 保持为空数组，
            // 避免配置与代码各建一个窗口。
            // 诊断构建从打包的本地测量页启动，完全不联网，也不触碰业务后台；
            // 正式构建仍然直接进入受保护工作台，由服务端决定是否跳登录页。
            #[cfg(feature = "diagnostics")]
            let start = {
                // 打包资源缺页时窗口只会显示空白，从外面看不出是构建配置问题。
                // 这里向资源解析器确认一次并直接报错，把「页面没打进去」
                // 挡在启动阶段，而不是留给使用者去猜。
                // 诊断构建用 tauri.diagnostics.conf.json 换掉 frontendDist，
                // 打包的是 ui-diagnostics/ 而不是正式的 ui/，
                // 因此诊断页不会混进正式产物（Spec 第 5 节）。
                assert!(
                    app.asset_resolver()
                        .get("diagnostics.html".into())
                        .is_some(),
                    "打包资源缺少 diagnostics.html：诊断构建必须带 \
                     --config src-tauri/tauri.diagnostics.conf.json"
                );
                tauri::WebviewUrl::App("diagnostics.html".into())
            };
            #[cfg(not(feature = "diagnostics"))]
            let start = tauri::WebviewUrl::External(
                WORKSTATION_URL
                    .parse::<Url>()
                    .expect("正式入口常量必须是合法 URL"),
            );

            // 闭包要在导航被拦下后弹确认，因此先取一份句柄给它。
            #[cfg(not(feature = "diagnostics"))]
            let nav_handle = app.handle().clone();

            let builder = tauri::WebviewWindowBuilder::new(app, "main", start)
                .title("YuMi 工作台")
                .on_navigation(move |url| {
                    // 诊断构建额外放行那一个打包测量页。放行条件是精确路径且
                    // 无查询串、无片段，因此不会变成「本地页面任意可达」。
                    #[cfg(feature = "diagnostics")]
                    if is_diagnostics_page(url.as_str()) {
                        return true;
                    }

                    // 返回 false 会阻止本次导航。外部链接在这里一律先拦下，
                    // 是否交给系统浏览器由后续的用户确认流程决定，
                    // 不在没有用户手势的情况下自动外跳。
                    let decision = classify_url(url);
                    log_navigation(url, decision);

                    // 合法外链先拦下，再弹确认交给系统浏览器；
                    // 用户不点确认就什么都不会发生，不存在自动外跳。
                    // 诊断构建不联网、也不需要外链能力，因此不编译这段。
                    #[cfg(not(feature = "diagnostics"))]
                    if decision == NavigationDecision::ConfirmExternal {
                        confirm_and_open_external(&nav_handle, url);
                    }

                    matches!(decision, NavigationDecision::AllowInApp)
                });

            // 桌面端才有窗口尺寸概念；Android 跟随系统，不强制尺寸或方向。
            #[cfg(desktop)]
            let builder = builder
                .inner_size(1280.0, 800.0)
                .min_inner_size(800.0, 600.0)
                .resizable(true);

            builder.build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("客户端启动失败");
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 隔离测试后台的典型来源：明文 HTTP + 非默认端口 + 回环地址。
    /// 正式策略必须拒绝它，测试构建策略必须接受它。
    const TEST_ORIGIN: &str = "http://127.0.0.1:8765/workstation";

    fn 测试来源() -> Url {
        TEST_ORIGIN.parse().expect("测试来源必须可解析")
    }

    fn 正式来源() -> Url {
        "https://akros.icu/employee/admin"
            .parse()
            .expect("正式来源必须可解析")
    }

    /// 测试构建的策略必须能进入自己的测试后台，
    /// 否则阶段 A 的 WebView 验证根本跑不起来。
    #[test]
    fn 测试构建可进入隔离后台() {
        let origin = 测试来源();
        for raw in [
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/workstation",
            "http://127.0.0.1:8765/private/image.png",
        ] {
            let url: Url = raw.parse().unwrap();
            assert_eq!(
                classify_url_against(&origin, &url),
                NavigationDecision::AllowInApp,
                "{raw} 是测试后台自身，应当允许"
            );
        }
    }

    /// 放开测试来源不等于放开整个明文 HTTP：
    /// 同一台机器上换个端口、换成别的主机都必须继续拒绝。
    #[test]
    fn 测试构建不放开其他明文来源() {
        let origin = 测试来源();
        for raw in [
            "http://127.0.0.1:9000/",
            "http://localhost:8765/",
            "http://192.168.1.10:8765/",
            "http://akros.icu/employee/admin",
        ] {
            let url: Url = raw.parse().unwrap();
            assert_eq!(
                classify_url_against(&origin, &url),
                NavigationDecision::Block,
                "{raw} 不是测试后台来源，应当拒绝"
            );
        }
    }

    /// 正式策略必须拒绝测试后台地址。
    /// 这条与 `test-backend` 特性一起构成「测试地址不进正式包」的双重保证。
    #[test]
    fn 正式策略拒绝测试后台地址() {
        let url: Url = TEST_ORIGIN.parse().unwrap();
        assert_eq!(
            classify_url_against(&正式来源(), &url),
            NavigationDecision::Block
        );
    }

    /// 即便主机与允许来源一致，带用户信息的 URL 也必须先被拒绝，
    /// 确认同源判断不会被内嵌凭据绕过。
    #[test]
    fn 同源但带用户信息仍被拒绝() {
        let url: Url = "https://someone@akros.icu/employee/admin".parse().unwrap();
        assert_eq!(
            classify_url_against(&正式来源(), &url),
            NavigationDecision::Block
        );
    }

    /// 正式入口常量本身必须被策略判为应用内导航，
    /// 避免常量与策略在后续修改中分叉。
    #[test]
    fn 正式入口可在应用内打开() {
        assert_eq!(
            classify_navigation(WORKSTATION_URL),
            NavigationDecision::AllowInApp
        );
    }

    #[test]
    fn 正式域名的普通业务路径可在应用内打开() {
        for raw in [
            "https://akros.icu/",
            "https://akros.icu/employee/admin",
            "https://akros.icu/employee/login",
            "https://akros.icu/tasks?status=open&page=2",
            "https://akros.icu/static/admin.js",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::AllowInApp,
                "{raw} 应当在应用内打开"
            );
        }
    }

    /// url crate 会把 https 的默认端口归一掉，显式写 :443 与不写等价。
    #[test]
    fn 显式默认端口等价于不写端口() {
        assert_eq!(
            classify_navigation("https://akros.icu:443/employee/admin"),
            NavigationDecision::AllowInApp
        );
    }

    #[test]
    fn 非标准端口被拒绝() {
        for raw in [
            "https://akros.icu:8443/employee/admin",
            "https://akros.icu:80/employee/admin",
            "https://akros.icu:8080/",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::Block,
                "{raw} 使用非默认端口，应当拒绝"
            );
        }
    }

    /// 伪子域必须落空，证明判定不是字符串前缀或后缀匹配。
    #[test]
    fn 伪子域不被当作正式域名() {
        for raw in [
            "https://akros.icu.evil.example/employee/admin",
            "https://evil-akros.icu/employee/admin",
            "https://notakros.icu/",
            "https://akros.icu.example.com/",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::ConfirmExternal,
                "{raw} 是外部域名，不能进入业务视图"
            );
        }
    }

    /// 正式域名的子域不是正式入口，按外部链接处理，不进入业务视图。
    #[test]
    fn 正式域名的子域不进入业务视图() {
        assert_eq!(
            classify_navigation("https://www.akros.icu/"),
            NavigationDecision::ConfirmExternal
        );
    }

    /// 主机名大小写不敏感，大写写法仍是正式域名。
    #[test]
    fn 主机名大小写不影响判定() {
        for raw in [
            "https://AKROS.ICU/employee/admin",
            "https://Akros.Icu/employee/admin",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::AllowInApp,
                "{raw} 与正式域名等价"
            );
        }
    }

    /// 同形异义攻击：西里尔字母 а 与拉丁 a 视觉相同。
    /// url crate 会把它转成 punycode，因此不会与正式域名相等。
    #[test]
    fn 同形异义域名不被当作正式域名() {
        // "аkros.icu" 的首字母是 U+0430 西里尔小写 а。
        let raw = "https://\u{0430}kros.icu/employee/admin";
        assert_ne!(
            classify_navigation(raw),
            NavigationDecision::AllowInApp,
            "同形异义域名不能进入业务视图"
        );
        assert_eq!(
            classify_navigation(raw),
            NavigationDecision::ConfirmExternal
        );
    }

    /// 末尾带点的 FQDN 写法在多数解析器里指向同一主机，
    /// 但不等于常量字符串；这里确认它落到保守的外部分支而不是应用内。
    #[test]
    fn 末尾带点的写法不进入业务视图() {
        assert_eq!(
            classify_navigation("https://akros.icu./employee/admin"),
            NavigationDecision::ConfirmExternal
        );
    }

    #[test]
    fn 用户信息伪装被拒绝() {
        for raw in [
            "https://akros.icu@evil.example/employee/admin",
            "https://user:pass@akros.icu/employee/admin",
            "https://akros.icu:token@evil.example/",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::Block,
                "{raw} 带用户信息，应当拒绝"
            );
        }
    }

    #[test]
    fn 明文http被拒绝() {
        for raw in ["http://akros.icu/employee/admin", "http://example.com/"] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::Block,
                "{raw} 是明文 HTTP，应当拒绝"
            );
        }
    }

    #[test]
    fn 危险scheme被拒绝() {
        for raw in [
            "file:///etc/passwd",
            "file://akros.icu/employee/admin",
            "javascript:alert(1)",
            "intent://akros.icu/#Intent;scheme=https;end",
            "data:text/html,<h1>x</h1>",
            "about:blank",
            "tauri://localhost/index.html",
            "http://tauri.localhost/index.html",
            "yumi://open",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::Block,
                "{raw} 应当拒绝"
            );
        }
    }

    #[test]
    fn ip字面量被拒绝() {
        for raw in [
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://93.184.216.34/",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::Block,
                "{raw} 是 IP 字面量，应当拒绝"
            );
        }
    }

    #[test]
    fn 普通外部https进入用户确认分支() {
        for raw in [
            "https://example.com/",
            "https://www.gov.cn/notice",
            "https://maps.example.org/route?to=1",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::ConfirmExternal,
                "{raw} 应当经用户确认后交系统浏览器"
            );
        }
    }

    #[test]
    fn 无法解析的输入被拒绝() {
        for raw in [
            "",
            "   ",
            "akros.icu/employee/admin",
            "//akros.icu/",
            "https://",
        ] {
            assert_eq!(
                classify_navigation(raw),
                NavigationDecision::Block,
                "{raw:?} 无法解析为绝对 URL，应当拒绝"
            );
        }
    }

    #[test]
    fn 本地错误页只接受精确资源() {
        assert!(is_exact_local_error_page("tauri://localhost/"));
        assert!(is_exact_local_error_page("tauri://localhost/index.html"));
        assert!(is_exact_local_error_page(
            "http://tauri.localhost/index.html"
        ));
    }

    #[test]
    fn 本地错误页拒绝参数与其他路径() {
        for raw in [
            "tauri://localhost/index.html?url=https://evil.example",
            "tauri://localhost/index.html#/admin",
            "tauri://localhost/other.html",
            "tauri://localhost/../secret",
            "http://tauri.localhost.evil.example/index.html",
            "https://akros.icu/index.html",
            "file:///index.html",
        ] {
            assert!(
                !is_exact_local_error_page(raw),
                "{raw} 不是打包错误页，应当拒绝"
            );
        }
    }

    /// 错误页判定与远程导航判定互不放宽：本地错误页在远程导航策略下仍是拒绝。
    #[test]
    fn 本地错误页在远程导航策略下仍被拒绝() {
        assert_eq!(
            classify_navigation("tauri://localhost/index.html"),
            NavigationDecision::Block
        );
    }

    /// Android 侧的原生重试与图片来源判定都读生成资源，本测试锁定它与
    /// `WORKSTATION_URL` 同值。
    ///
    /// 这条以前只在正式配置下检查，恰好漏掉了真正会分叉的场景：
    /// 测试构建里 Rust 用测试地址、Android 资源仍是生产地址。
    /// 现在两侧都由 build.rs 产出，这里对**当前构建**做无条件校验。
    #[test]
    fn android资源里的入口地址与常量一致() {
        let generated = include_str!("../gen/android/app/src/main/res/values/generated_entry.xml");
        assert_eq!(
            extract_string(generated, "workstation_url"),
            WORKSTATION_URL,
            "Android 资源与 WORKSTATION_URL 已分叉，原生重试会打到错误地址"
        );
        assert_eq!(
            extract_string(generated, "build_mode"),
            BUILD_MODE,
            "构建模式在两侧不一致"
        );
    }

    /// 从生成的 Android 资源里取出某个字符串项的值。
    fn extract_string<'a>(xml: &'a str, name: &str) -> &'a str {
        let needle = format!("<string name=\"{name}\" translatable=\"false\">");
        let start = xml
            .find(&needle)
            .unwrap_or_else(|| panic!("generated_entry.xml 缺少 {name}"))
            + needle.len();
        let end = start
            + xml[start..]
                .find("</string>")
                .expect("字符串项缺少结束标签");
        &xml[start..end]
    }

    /// 外链确认的去重：同一目标在时间窗内只问一次。
    #[test]
    #[cfg(not(feature = "diagnostics"))]
    fn 外链确认对同一目标只弹一次() {
        let mut state = ExternalPromptState::default();
        let t0 = Instant::now();

        assert!(state.should_prompt("https://a.example/x", t0));
        assert!(!state.should_prompt("https://a.example/x", t0));
        assert!(!state.should_prompt("https://a.example/x", t0 + EXTERNAL_PROMPT_DEDUP / 2));
    }

    /// 去重不能把不同目标一起吞掉，也不能永久生效。
    #[test]
    #[cfg(not(feature = "diagnostics"))]
    fn 外链去重不跨目标且会过期() {
        let mut state = ExternalPromptState::default();
        let t0 = Instant::now();

        assert!(state.should_prompt("https://a.example/x", t0));
        state.finish();
        assert!(state.should_prompt("https://b.example/y", t0));
        state.finish();
        assert!(state.should_prompt(
            "https://b.example/y",
            t0 + EXTERNAL_PROMPT_DEDUP + Duration::from_millis(1)
        ));
    }

    /// 在途确认期间不得再弹第二个框——去重窗口管不了「不同 URL 连续跳转」。
    #[test]
    #[cfg(not(feature = "diagnostics"))]
    fn 确认框在途时拒绝第二个外跳() {
        let mut state = ExternalPromptState::default();
        let t0 = Instant::now();

        assert!(state.should_prompt("https://a.example/x", t0));
        // 不同目标、窗口内：五秒去重拦不住，必须由在途互斥拦住。
        assert!(!state.should_prompt("https://b.example/y", t0));
        // 确认框关闭后恢复。
        state.finish();
        assert!(state.should_prompt("https://b.example/y", t0));
    }

    /// 诊断页是打包页面，但不是错误页——两个入口不能互相顶替。
    #[test]
    fn 诊断页与错误页互不混淆() {
        assert_eq!(
            bundled_page_path("tauri://localhost/diagnostics.html"),
            Some("/diagnostics.html")
        );
        assert!(!is_exact_local_error_page(
            "tauri://localhost/diagnostics.html"
        ));
        assert_eq!(bundled_page_path("tauri://localhost/"), Some("/"));
    }

    /// 打包页面判定只认精确路径：带参数、带片段、换路径一律落空，
    /// 因此远程页面无法借本地协议把任意内容塞进来。
    #[test]
    fn 打包页面判定拒绝参数与未知路径() {
        for raw in [
            "tauri://localhost/diagnostics.html?url=https://evil.example",
            "tauri://localhost/diagnostics.html#/admin",
            "tauri://localhost/diagnostics.html/../secret",
            "tauri://localhost/unknown.html",
            "tauri://user@localhost/diagnostics.html",
            "https://akros.icu/diagnostics.html",
            "http://tauri.localhost.evil.example/diagnostics.html",
        ] {
            assert_eq!(bundled_page_path(raw), None, "{raw} 不是打包页面，应当落空");
        }
    }

    /// 即使在诊断构建里，诊断页也只由原生层的显式放行进入；
    /// 远程导航策略本身仍然拒绝它。
    #[test]
    fn 诊断页在远程导航策略下仍被拒绝() {
        assert_eq!(
            classify_navigation("tauri://localhost/diagnostics.html"),
            NavigationDecision::Block
        );
    }
}
