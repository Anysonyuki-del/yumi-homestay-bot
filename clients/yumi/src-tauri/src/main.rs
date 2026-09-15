//! Windows 桌面入口。
//!
//! 正式构建禁止附带控制台窗口；调试构建保留，便于阶段 A 观察导航日志。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    yumi_lib::run()
}
