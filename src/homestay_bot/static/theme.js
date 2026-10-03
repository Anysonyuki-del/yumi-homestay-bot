"use strict";

// 两套公共布局在 CSS 前同步加载：只恢复外观，不初始化后台业务或认证状态。
(() => {
  const preferenceKey = "yumi.admin.theme";
  const root = document.documentElement;

  /** 只接受现有两种主题，并同步浏览器顶栏；标签缺失时仍可正常切换。 */
  function applyTheme(value) {
    const theme = value === "warm" ? "warm" : "classic";
    root.dataset.theme = theme;
    const browserColor = document.querySelector('meta[name="theme-color"]');
    if (browserColor) browserColor.content = theme === "warm" ? "#faf9f5" : "#f4f6fa";
    return theme;
  }

  let savedTheme = "classic";
  try {
    savedTheme = window.localStorage.getItem(preferenceKey);
  } catch {
    // 浏览器拒绝存储时维持经典；不能让偏好恢复阻断页面和业务脚本。
  }
  applyTheme(savedTheme);

  /** 页面结构就绪后显示控件；切换仅改外观，保留表单、勾选及抽屉焦点。 */
  document.addEventListener("DOMContentLoaded", () => {
    const controls = document.querySelectorAll("[data-theme-control]");
    controls.forEach((control) => {
      control.value = root.dataset.theme;
      control.closest("[data-theme-selector]").hidden = false;
      // 控件在业务表单外，不派发业务 change 或提交事件。
      control.addEventListener("change", () => {
        const theme = applyTheme(control.value);
        controls.forEach((other) => { other.value = theme; });
        let feedback = "";
        try {
          window.localStorage.setItem(preferenceKey, theme);
        } catch {
          feedback = "本次已切换，浏览器无法记住此选择";
        }
        document.querySelectorAll("[data-theme-feedback]").forEach((message) => {
          message.textContent = feedback;
        });
      });
    });
  }, { once: true });
})();
