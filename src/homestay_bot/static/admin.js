"use strict";

document.documentElement.classList.add("js");

const drawer = document.querySelector("[data-drawer]");
const drawerTrigger = document.querySelector("[data-drawer-open]");
const drawerClosers = document.querySelectorAll("[data-drawer-close]");
const drawerBackdrop = document.querySelector(".drawer-backdrop");
let focusBeforeDrawer = null;

/** 打开移动导航并把键盘焦点移到关闭按钮。 */
function openDrawer() {
  if (!drawer || !drawerTrigger || !drawerBackdrop) return;
  focusBeforeDrawer = document.activeElement;
  drawer.classList.add("is-open");
  drawerTrigger.setAttribute("aria-expanded", "true");
  drawerBackdrop.hidden = false;
  drawer.querySelector("[data-drawer-close]")?.focus();
}

/** 关闭移动导航并恢复触发前焦点。 */
function closeDrawer() {
  if (!drawer || !drawerTrigger || !drawerBackdrop) return;
  drawer.classList.remove("is-open");
  drawerTrigger.setAttribute("aria-expanded", "false");
  drawerBackdrop.hidden = true;
  if (focusBeforeDrawer instanceof HTMLElement) focusBeforeDrawer.focus();
}

drawerTrigger?.addEventListener("click", openDrawer);
drawerClosers.forEach((element) => element.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (event) => {
  if (!drawer?.classList.contains("is-open")) return;
  if (event.key === "Escape") {
    closeDrawer();
    return;
  }
  if (event.key !== "Tab") return;
  // 抽屉打开时把键盘焦点限制在导航内，避免落到遮罩后的页面。
  const focusable = [...drawer.querySelectorAll("a, button:not([disabled])")]
    .filter((element) => element instanceof HTMLElement);
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const prompt = form.getAttribute("data-confirm") || "确定继续吗？";
    if (!window.confirm(prompt)) event.preventDefault();
  });
});

let hasUnsavedChanges = false;
document.querySelectorAll("form[data-unsaved-warning]").forEach((form) => {
  form.addEventListener("input", () => { hasUnsavedChanges = true; });
  form.addEventListener("submit", () => { hasUnsavedChanges = false; });
});
window.addEventListener("beforeunload", (event) => {
  if (!hasUnsavedChanges) return;
  event.preventDefault();
  event.returnValue = "";
});

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    // 被确认框等前置校验取消时不得锁定表单；仅首个有效提交进入锁定状态。
    if (event.defaultPrevented) return;
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    const submitter = form.querySelector('button[type="submit"], input[type="submit"]');
    if (!(submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement)) return;
    window.setTimeout(() => {
      submitter.disabled = true;
      submitter.setAttribute("aria-disabled", "true");
    }, 0);
  });
});
