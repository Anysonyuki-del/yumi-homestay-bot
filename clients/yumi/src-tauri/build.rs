//! Tauri 构建脚本。
//!
//! 除官方构建流程外，这里还承担一件事：**把构建模式与入口地址集中算一次**，
//! 再分发给 Rust 与 Android 两侧。
//!
//! 之所以要集中，是因为之前两侧各写一份：Rust 的 `WORKSTATION_URL` 随
//! `test-backend` 特性变化，而 Android 的 `values/strings.xml::workstation_url`
//! 永远是生产地址。测试构建里原生重试和图片保存会去用生产地址，
//! 与 Rust 导航策略判定的来源对不上——来源就此分叉。
//!
//! 现在两侧的值都由本文件产出，分叉在构建期即不可能发生。
//! 同时把模式写成 Gradle 可读的属性文件，让应用身份隔离不再依赖
//! 「记得在命令行加 --debug」这种人工防线。

use std::env;
use std::fs;
use std::path::Path;

/// 正式入口。启动即访问受保护工作台，由服务端决定是否跳登录页。
const PRODUCTION_ENTRY: &str = "https://akros.icu/employee/admin";

/// 诊断模式不导航任何业务服务器，入口是打包在应用内的本地测量页。
/// 这里仍给一个占位来源，只为让导航策略有一个「什么都不匹配」的基准，
/// 使任何业务地址都落到拒绝分支。
const DIAGNOSTICS_ENTRY: &str = "tauri://localhost/diagnostics.html";

/// 三种互斥的构建模式。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Mode {
    Production,
    IsolatedTest,
    Diagnostics,
}

impl Mode {
    fn as_str(self) -> &'static str {
        match self {
            Mode::Production => "production",
            Mode::IsolatedTest => "isolated-test",
            Mode::Diagnostics => "diagnostics",
        }
    }

    /// 非生产模式一律使用独立的应用标识，与生产包并存且数据目录隔离。
    fn is_production(self) -> bool {
        matches!(self, Mode::Production)
    }
}

fn main() {
    let test_backend = env::var_os("CARGO_FEATURE_TEST_BACKEND").is_some();
    let diagnostics = env::var_os("CARGO_FEATURE_DIAGNOSTICS").is_some();

    // 两个非生产特性同时启用时，入口与身份都会有两种解释，直接拒绝构建，
    // 不猜测使用者想要哪一个。
    if test_backend && diagnostics {
        panic!(
            "test-backend 与 diagnostics 互斥：前者连隔离测试后台，\
             后者只加载本地测量页且不联网。请只启用其中一个。"
        );
    }

    let mode = if diagnostics {
        Mode::Diagnostics
    } else if test_backend {
        Mode::IsolatedTest
    } else {
        Mode::Production
    };

    let entry = match mode {
        Mode::Production => PRODUCTION_ENTRY.to_string(),
        Mode::Diagnostics => DIAGNOSTICS_ENTRY.to_string(),
        Mode::IsolatedTest => {
            // 缺地址就让构建失败，而不是悄悄退回生产地址——
            // 那会让「隔离测试」在毫无提示的情况下打到生产。
            let raw = env::var("YUMI_TEST_ENTRY").unwrap_or_else(|_| {
                panic!(
                    "启用 test-backend 特性时必须提供 YUMI_TEST_ENTRY，\
                     例如 YUMI_TEST_ENTRY=http://192.0.2.10:8765/"
                )
            });
            if raw.trim().is_empty() {
                panic!("YUMI_TEST_ENTRY 不能为空");
            }
            // 隔离测试地址绝不能等于生产来源，否则「测试模式」名不副实。
            if raw.starts_with(PRODUCTION_ENTRY) || same_origin_as_production(&raw) {
                panic!("YUMI_TEST_ENTRY 指向了生产来源。隔离测试构建不允许使用正式后台。");
            }
            raw
        }
    };

    // 让 lib.rs 直接用 env!("YUMI_ENTRY")，不再按特性写两份常量。
    println!("cargo:rustc-env=YUMI_ENTRY={entry}");
    println!("cargo:rustc-env=YUMI_BUILD_MODE={}", mode.as_str());
    println!("cargo:rerun-if-env-changed=YUMI_TEST_ENTRY");

    write_android_inputs(mode, &entry);

    tauri_build::build()
}

/// 粗判一个地址是否与生产同源。
///
/// 只做主机名比较：这里的目的是拦住「把测试入口填成生产」的低级错误，
/// 真正的来源判定仍在 `lib.rs` 用解析后的 scheme/host/port 完成。
fn same_origin_as_production(raw: &str) -> bool {
    fn host_of(url: &str) -> Option<&str> {
        let rest = url.split("://").nth(1)?;
        let authority = rest.split(['/', '?', '#']).next()?;
        let host = authority.rsplit('@').next()?;
        Some(host.split(':').next().unwrap_or(host))
    }
    match (host_of(raw), host_of(PRODUCTION_ENTRY)) {
        (Some(a), Some(b)) => a.eq_ignore_ascii_case(b),
        _ => false,
    }
}

/// 把入口地址与构建模式写进 Android 生成工程。
///
/// 两个产物：
/// - `values/generated_entry.xml`：Kotlin 侧的原生重试与图片保存来源判定都读它。
/// - `build-mode.properties`：`identity.gradle` 读它决定应用标识后缀与源码集，
///   因此身份隔离由构建模式决定，而不是由命令行是否带 `--debug` 决定。
///
/// 生成工程不存在时（例如只跑桌面构建或单元测试）直接跳过，不制造无关失败。
fn write_android_inputs(mode: Mode, entry: &str) {
    let manifest = env::var("CARGO_MANIFEST_DIR").expect("缺少 CARGO_MANIFEST_DIR");
    let app_dir = Path::new(&manifest).join("gen/android/app");
    if !app_dir.is_dir() {
        return;
    }

    let values_dir = app_dir.join("src/main/res/values");
    if values_dir.is_dir() {
        let xml = format!(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n\
             <!-- 本文件由 src-tauri/build.rs 生成，请勿手工编辑。 -->\n\
             <!-- 值与 Rust 的 YUMI_ENTRY 同源，两侧不可能分叉。 -->\n\
             <resources>\n    \
             <string name=\"workstation_url\" translatable=\"false\">{}</string>\n    \
             <string name=\"build_mode\" translatable=\"false\">{}</string>\n\
             </resources>\n",
            xml_escape(entry),
            mode.as_str(),
        );
        let target = values_dir.join("generated_entry.xml");
        write_if_changed(&target, &xml);
    }

    let properties = format!(
        "# 由 src-tauri/build.rs 生成，请勿手工编辑。\n\
         mode={}\n\
         isProduction={}\n",
        mode.as_str(),
        mode.is_production(),
    );
    write_if_changed(&app_dir.join("build-mode.properties"), &properties);
}

/// 内容不变时不重写文件，避免每次构建都让 Gradle 认为输入已变而全量重跑。
fn write_if_changed(path: &Path, contents: &str) {
    if fs::read_to_string(path)
        .map(|old| old == contents)
        .unwrap_or(false)
    {
        return;
    }
    fs::write(path, contents).unwrap_or_else(|e| panic!("写入 {} 失败: {e}", path.display()));
}

/// Android 资源是 XML，入口地址进去之前必须转义。
fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
