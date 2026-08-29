"use strict";

document.documentElement.classList.add("js-enabled");

const drawer = document.querySelector("[data-drawer]");
const drawerTrigger = document.querySelector("[data-drawer-open]");
const drawerClosers = document.querySelectorAll("[data-drawer-close]");
const drawerBackdrop = document.querySelector(".drawer-backdrop");
const workspace = document.querySelector(".admin-workspace");
const desktopBreakpoint = window.matchMedia("(min-width: 1024px)");
let focusBeforeDrawer = null;

/** 根据断点和开关状态同步抽屉的可访问树状态。 */
function syncDrawerAccessibility() {
  if (!drawer) return;
  const shouldExpose = desktopBreakpoint.matches || drawer.classList.contains("is-open");
  drawer.inert = !shouldExpose;
  if (shouldExpose) drawer.removeAttribute("aria-hidden");
  else drawer.setAttribute("aria-hidden", "true");
}

/** 打开移动导航并把键盘焦点移到关闭按钮。 */
function openDrawer() {
  if (!drawer || !drawerTrigger || !drawerBackdrop || !workspace) return;
  focusBeforeDrawer = document.activeElement;
  drawer.classList.add("is-open");
  syncDrawerAccessibility();
  drawerTrigger.setAttribute("aria-expanded", "true");
  drawerBackdrop.hidden = false;
  workspace.inert = true;
  workspace.setAttribute("aria-hidden", "true");
  document.body.classList.add("drawer-is-open");
  drawer.querySelector("[data-drawer-close]")?.focus();
}

/** 关闭移动导航并恢复触发前焦点。 */
function closeDrawer(restoreFocus = true) {
  if (!drawer || !drawerTrigger || !drawerBackdrop || !workspace) return;
  drawer.classList.remove("is-open");
  drawerTrigger.setAttribute("aria-expanded", "false");
  drawerBackdrop.hidden = true;
  workspace.inert = false;
  workspace.removeAttribute("aria-hidden");
  document.body.classList.remove("drawer-is-open");
  syncDrawerAccessibility();
  if (restoreFocus && focusBeforeDrawer instanceof HTMLElement) focusBeforeDrawer.focus();
}

drawerTrigger?.addEventListener("click", openDrawer);
drawerClosers.forEach((element) => element.addEventListener("click", closeDrawer));
desktopBreakpoint.addEventListener("change", (event) => {
  if (event.matches && drawer?.classList.contains("is-open")) closeDrawer(false);
  else syncDrawerAccessibility();
});
syncDrawerAccessibility();
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

document.querySelectorAll("form[data-confirm], form[data-danger-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const prompt = form.getAttribute("data-confirm")
      || form.getAttribute("data-danger-confirm")
      || "确定继续吗？";
    if (!window.confirm(prompt)) event.preventDefault();
  });
});

const dirtyForms = new Set();

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    // 提交一个表单前，必须明确处理同页其它表单尚未保存的内容。
    const hasOtherDirtyForm = [...dirtyForms].some((dirtyForm) => dirtyForm !== form);
    if (!hasOtherDirtyForm) return;
    const discardConfirmed = window.confirm("当前页面还有其它未保存内容，继续提交将丢弃这些修改。确定继续吗？");
    if (!discardConfirmed) {
      event.preventDefault();
      return;
    }
    dirtyForms.clear();
  });
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    // 服务端已生成脱敏纯文本；浏览器只复制，不读取或过滤任何 raw 对象。
    const targetId = button.getAttribute("data-copy-target");
    const target = targetId ? document.getElementById(targetId) : null;
    if (!(target instanceof HTMLTextAreaElement)) return;
    try {
      await navigator.clipboard.writeText(target.value);
      button.textContent = "已复制";
    } catch {
      target.focus();
      target.select();
    }
  });
});

document.querySelectorAll("form[data-unsaved-warning]").forEach((form) => {
  form.addEventListener("input", () => { dirtyForms.add(form); });
  form.addEventListener("submit", (event) => {
    // 只清除当前成功提交的表单，其他表单的未保存内容仍需提醒。
    if (!event.defaultPrevented) dirtyForms.delete(form);
  });
});
window.addEventListener("beforeunload", (event) => {
  if (dirtyForms.size === 0) return;
  event.preventDefault();
  event.returnValue = "";
});

/** 在浏览器确认提交后锁定提交按钮，并提供可感知的处理中反馈。 */
function setSubmittingState(submitter) {
  if (!(submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement)) return;
  submitter.dataset.originalLabel = submitter instanceof HTMLInputElement
    ? submitter.value
    : submitter.textContent || "";
  if (submitter instanceof HTMLInputElement) submitter.value = "正在处理…";
  else submitter.textContent = "正在处理…";
  submitter.setAttribute("aria-busy", "true");
  submitter.setAttribute("aria-disabled", "true");
  submitter.disabled = true;
}

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    // 被确认框等前置校验取消时不得锁定表单；仅首个有效提交进入锁定状态。
    if (event.defaultPrevented) return;
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    const requestedSubmitter = event.submitter;
    const submitter = requestedSubmitter instanceof HTMLElement
      ? requestedSubmitter
      : form.querySelector('button[type="submit"], input[type="submit"]');
    // 延迟到浏览器完成本次提交事件后再禁用，避免丢失带 name/value 的提交按钮。
    window.setTimeout(() => {
      setSubmittingState(submitter);
    }, 0);
  });
});
