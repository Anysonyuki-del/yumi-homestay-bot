"use strict";

document.documentElement.classList.add("js-enabled");

const drawer = document.querySelector("[data-drawer]");
const drawerTrigger = document.querySelector("[data-drawer-open]");
const drawerClosers = document.querySelectorAll("[data-drawer-close]");
const drawerBackdrop = document.querySelector(".drawer-backdrop");
const workspace = document.querySelector(".admin-workspace");
const desktopBreakpoint = window.matchMedia("(min-width: 1024px)");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let focusBeforeDrawer = null;
let drawerTransitionToken = 0;
let cancelPendingDrawerClose = null;

/** 取消旧的抽屉收尾回调，并返回本次状态变化的唯一编号。 */
function beginDrawerTransition() {
  drawerTransitionToken += 1;
  if (typeof cancelPendingDrawerClose === "function") cancelPendingDrawerClose();
  cancelPendingDrawerClose = null;
  return drawerTransitionToken;
}

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
  const transitionToken = beginDrawerTransition();
  if (!document.body.classList.contains("drawer-is-open")) {
    focusBeforeDrawer = document.activeElement;
  }
  drawerBackdrop.hidden = false;
  drawer.classList.remove("is-closing");
  drawer.classList.add("is-open");
  syncDrawerAccessibility();
  drawerTrigger.setAttribute("aria-expanded", "true");
  workspace.inert = true;
  workspace.setAttribute("aria-hidden", "true");
  document.body.classList.add("drawer-is-open");
  // 遮罩从 hidden 恢复后要等一帧再切换透明度，才能稳定触发淡入。
  window.requestAnimationFrame(() => {
    if (
      transitionToken === drawerTransitionToken
      && drawer.classList.contains("is-open")
    ) {
      drawerBackdrop.classList.add("is-visible");
    }
  });
  drawer.querySelector("[data-drawer-close]")?.focus();
}

/** 完成抽屉关闭后的可访问状态和焦点恢复。 */
function finishDrawerClose(restoreFocus, transitionToken) {
  if (!drawer || !drawerTrigger || !drawerBackdrop || !workspace) return;
  if (transitionToken !== drawerTransitionToken || drawer.classList.contains("is-open")) {
    return;
  }
  drawer.classList.remove("is-closing");
  drawerTrigger.setAttribute("aria-expanded", "false");
  drawerBackdrop.hidden = true;
  drawerBackdrop.classList.remove("is-visible");
  workspace.inert = false;
  workspace.removeAttribute("aria-hidden");
  document.body.classList.remove("drawer-is-open");
  syncDrawerAccessibility();
  if (restoreFocus && focusBeforeDrawer instanceof HTMLElement) focusBeforeDrawer.focus();
}

/** 先播放关闭动画，再恢复页面操作；减少动态和桌面切换直接收尾。 */
function closeDrawer(restoreFocus = true, immediate = false) {
  if (!drawer || !drawerTrigger || !drawerBackdrop || !workspace) return;
  if (!document.body.classList.contains("drawer-is-open")) {
    syncDrawerAccessibility();
    return;
  }
  const transitionToken = beginDrawerTransition();
  drawer.classList.add("is-closing");
  drawer.classList.remove("is-open");
  drawerBackdrop.classList.remove("is-visible");
  if (immediate || reducedMotion.matches) {
    finishDrawerClose(restoreFocus, transitionToken);
    return;
  }

  let fallbackTimer = null;
  const cleanup = () => {
    drawer.removeEventListener("transitionend", handleTransitionEnd);
    if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
    if (cancelPendingDrawerClose === cleanup) cancelPendingDrawerClose = null;
  };
  const finish = () => {
    cleanup();
    finishDrawerClose(restoreFocus, transitionToken);
  };
  const handleTransitionEnd = (event) => {
    if (event.target === drawer && event.propertyName === "transform") finish();
  };
  drawer.addEventListener("transitionend", handleTransitionEnd);
  // 浏览器丢失 transitionend 时仍须释放 inert 和页面滚动。
  fallbackTimer = window.setTimeout(finish, 240);
  cancelPendingDrawerClose = cleanup;
}

drawerTrigger?.addEventListener("click", openDrawer);
drawerClosers.forEach((element) => element.addEventListener("click", closeDrawer));
desktopBreakpoint.addEventListener("change", (event) => {
  if (event.matches && document.body.classList.contains("drawer-is-open")) {
    closeDrawer(false, true);
  }
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

/** 删除 GET 筛选表单中的空字段，让地址只保留真实生效的筛选条件。 */
function removeEmptyFilterValues(event) {
  if (!(event.formData instanceof FormData)) return;
  const emptyNames = [...event.formData.entries()]
    .filter(([, value]) => typeof value === "string" && value.trim() === "")
    .map(([name]) => name);
  emptyNames.forEach((name) => event.formData.delete(name));
}

document.querySelectorAll("form[data-filter-form]").forEach((form) => {
  // `formdata` 只调整浏览器即将提交的副本，不禁用控件，也不接管服务端筛选。
  form.addEventListener("formdata", removeEmptyFilterValues);
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
  submitter.classList.add("is-submitting");
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

// 全选本页：仅在脚本可用时增强；脚本缺失时逐条勾选仍然可用，不影响归档提交。
document.querySelectorAll("[data-select-all]").forEach((toggle) => {
  const form = toggle.closest("form");
  if (!form) return;
  const boxes = () =>
    Array.from(form.querySelectorAll('input[name="task_ids"]'));
  toggle.addEventListener("change", () => {
    boxes().forEach((box) => {
      box.checked = toggle.checked;
    });
  });
  form.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.name !== "task_ids") return;
    const all = boxes();
    const checked = all.filter((box) => box.checked);
    toggle.checked = checked.length === all.length && all.length > 0;
    // 部分选中时显示不确定态，避免全选框看起来是「已全选」。
    toggle.indeterminate = checked.length > 0 && checked.length < all.length;
  });
});
