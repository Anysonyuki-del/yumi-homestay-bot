/* YuMi 后台与认证页外观切换脚本（同源阻塞首帧执行，严守 CSP script-src 'self'）。
 * 职责：同步读取浏览器偏好并写入 <html data-appearance>，避免首帧闪屏；
 * 同步更新 <meta name="theme-color">；绑定外观切换按钮交互与跨标签页同步。
 * 认证页也只加载本脚本，绝不引入 admin.js，不影响原生表单与安全边界。
 */
(() => {
  const STORAGE_KEY = "yumi-appearance";
  const THEME_COLOR = { light: "#f5f4f0", dark: "#08080b" };
  const LABELS = { auto: "跟随系统", light: "浅色", dark: "深色" };
  const root = document.documentElement;
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  /** 读取浏览器保存的外观偏好；隐私模式或存储受限时安全回退到跟随系统。 */
  function readPreference() {
    try {
      const value = window.localStorage.getItem(STORAGE_KEY);
      if (value === "light" || value === "dark") return value;
    } catch (_) {
      // 存储不可用时仅在当前页内存生效，不阻断页面。
    }
    return "auto";
  }

  /** 将外观偏好写入浏览器存储；切回跟随系统时清除键，保持默认语义干净。 */
  function writePreference(mode) {
    try {
      if (mode === "light" || mode === "dark") {
        window.localStorage.setItem(STORAGE_KEY, mode);
      } else {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch (_) {
      // 存储不可用时忽略持久化错误。
    }
  }

  /** 返回当前操作系统偏好对应的明暗模式。 */
  function systemScheme() {
    return media && media.matches ? "dark" : "light";
  }

  /** 计算下一次点击的目标模式：从 auto 出发首次点击必定切到与当前系统相反的外观。 */
  function nextPreference(mode) {
    const sys = systemScheme();
    if (mode === "auto") return sys === "dark" ? "light" : "dark";
    if (mode === "dark") return sys === "dark" ? "auto" : "light";
    return sys === "light" ? "auto" : "dark";
  }

  /** 同步两条 theme-color meta，使浏览器地址栏/状态栏与手动外观一致。 */
  function syncThemeColorMeta(mode) {
    const lightMeta = document.querySelector('meta[name="theme-color"][media="(prefers-color-scheme: light)"]');
    const darkMeta = document.querySelector('meta[name="theme-color"][media="(prefers-color-scheme: dark)"]');
    if (mode === "light" || mode === "dark") {
      const color = THEME_COLOR[mode];
      if (lightMeta) lightMeta.setAttribute("content", color);
      if (darkMeta) darkMeta.setAttribute("content", color);
      return;
    }
    if (lightMeta) lightMeta.setAttribute("content", THEME_COLOR.light);
    if (darkMeta) darkMeta.setAttribute("content", THEME_COLOR.dark);
  }

  /** 刷新页面内所有外观切换按钮的图标、文字和无障碍说明。 */
  function syncButtons(mode) {
    const next = nextPreference(mode);
    const label = LABELS[mode] || LABELS.auto;
    const hint = `外观：${label}，点击切换为${LABELS[next]}`;
    document.querySelectorAll("[data-theme-control]").forEach((button) => {
      button.setAttribute("data-appearance-mode", mode);
      button.setAttribute("aria-label", hint);
      button.setAttribute("title", hint);
      button.querySelectorAll("[data-theme-icon]").forEach((icon) => {
        icon.hidden = icon.getAttribute("data-theme-icon") !== mode;
      });
      const text = button.querySelector("[data-theme-label]");
      if (text) text.textContent = label;
    });
  }

  let currentMode = readPreference();

  /** 应用指定外观模式到根元素、浏览器主题色与按钮状态。 */
  function applyAppearance(mode) {
    currentMode = mode === "light" || mode === "dark" ? mode : "auto";
    if (currentMode === "auto") {
      root.removeAttribute("data-appearance");
    } else {
      root.setAttribute("data-appearance", currentMode);
    }
    syncThemeColorMeta(currentMode);
    syncButtons(currentMode);
  }

  // 首帧同步标记并恢复外观，避免渲染后再切色导致闪烁。
  root.classList.add("theme-ready");
  applyAppearance(currentMode);

  /** DOM 就绪后绑定按钮状态同步与点击切换。 */
  function bindControls() {
    syncButtons(currentMode);
    document.addEventListener("click", (event) => {
      const button = event.target.closest ? event.target.closest("[data-theme-control]") : null;
      if (!button) return;
      const next = nextPreference(currentMode);
      writePreference(next);
      applyAppearance(next);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindControls, { once: true });
  } else {
    bindControls();
  }

  // 同源其他标签页修改外观偏好时实时同步当前页。
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    applyAppearance(readPreference());
  });

  // 操作系统明暗变化时刷新按钮提示文案：手动模式的「下一步」同样取决于系统明暗
  // （见 nextPreference），只刷新 auto 会让 aria-label / title 指向过期的目标模式。
  if (media) {
    const onSystemChange = () => {
      syncButtons(currentMode);
    };
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onSystemChange);
    } else if (typeof media.addListener === "function") {
      media.addListener(onSystemChange);
    }
  }
})();
