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
  // 抽屉里还有主题选择等表单控件；隐藏元素无法获得焦点，排除后首尾判断才准确。
  const focusable = [...drawer.querySelectorAll(
    "a[href], button:not([disabled]), select:not([disabled]), input:not([disabled]):not([type='hidden']), textarea:not([disabled])",
  )].filter((element) => element instanceof HTMLElement && element.getClientRects().length > 0);
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

/** 把确认文案里的占位符换成本次提交的真实数值。 */
function fillConfirmPlaceholders(text, form) {
  const selected = new Set(
    Array.from(
      form.querySelectorAll('input[name="task_ids"]:checked:not(:disabled)'),
    ).map((box) => box.value),
  );
  const employee = form.querySelector('select[name="assigned_employee_id"]');
  const chosen = employee instanceof HTMLSelectElement
    ? employee.options[employee.selectedIndex]?.textContent?.trim() || ""
    : "";
  // {draft}：客诉发送确认要让员工看到这次真正要发出去的正文，而不是泛泛一句「确定吗」。
  const draft = form.querySelector('textarea[name="draft"]');
  const draftText = draft instanceof HTMLTextAreaElement ? draft.value.trim() : "";
  // {reservation_code}：审批回填订单号时，确认框里重复显示所填订单号，填错一眼能看出。
  const code = form.querySelector('input[name="reservation_code"]');
  const codeText = code instanceof HTMLInputElement ? code.value.trim() : "";
  // 用函数作替换值：正文里的 $&、$1 等不会被当成替换模式；replaceAll 覆盖同一占位符出现多次的文案。
  return text
    .replaceAll("{n}", () => String(selected.size))
    .replaceAll("{employee}", () => chosen)
    .replaceAll("{draft}", () => draftText)
    .replaceAll("{reservation_code}", () => codeText);
}

let toastHideTimer = null;

/**
 * 在页面右下角展示温和轻提示，纯文本写入并由 aria-live 播报。
 * 提示区只在 layouts/admin.html 定义一份，脚本不再自建同构节点，避免两处结构漂移。
 */
function showToast(message) {
  const region = document.querySelector("[data-toast-region]");
  if (!message || !(region instanceof HTMLElement)) return;
  region.textContent = message;
  region.hidden = false;
  window.clearTimeout(toastHideTimer);
  toastHideTimer = window.setTimeout(() => {
    region.hidden = true;
    region.textContent = "";
  }, 2600);
}

/**
 * 浏览器不支持原生 <dialog>（如 iOS 15.4 以前的 Safari）或布局缺少弹窗时的兜底：
 * 退回同步原生确认框，保证需要确认的提交不会因为打不开页内弹窗而无声失效。
 */
function nativeConfirmFallback({ title, message, typedExpected }) {
  if (typeof typedExpected === "string" && typedExpected.length > 0) {
    const answer = window.prompt(`${title}：${message}\n确认请输入数字 ${typedExpected}。`);
    return answer !== null && answer.trim() === typedExpected;
  }
  return window.confirm(message);
}

/** 打开页内温和确认框，支持普通确认与不可逆手输数量校验，关闭后归还焦点。 */
function openConfirmDialog({
  title = "请确认本次操作",
  message = "确定继续吗？",
  confirmLabel = "确认继续",
  danger = false,
  typedExpected = null,
}) {
  // 弹窗结构只由 layouts/admin.html 提供；缺失或浏览器不支持 showModal 时走原生兜底。
  const dialog = document.querySelector("[data-confirm-dialog]");
  if (!(dialog instanceof HTMLElement) || typeof dialog.showModal !== "function") {
    // 必须放到下一个任务再弹：当前仍处于 submit 事件派发中，浏览器会忽略此时的 requestSubmit，
    // 确认后的重新提交会被静默丢弃。
    return new Promise((resolve) => {
      window.setTimeout(() => resolve(nativeConfirmFallback({ title, message, typedExpected })), 0);
    });
  }
  if (dialog.open) return Promise.resolve(false);
  const previousFocus = document.activeElement;
  const titleEl = dialog.querySelector("[data-confirm-title]");
  const messageEl = dialog.querySelector("[data-confirm-message]");
  const typedBox = dialog.querySelector("[data-confirm-typed]");
  const typedLabel = dialog.querySelector("[data-confirm-typed-label]");
  const typedInput = dialog.querySelector("[data-confirm-input]");
  const typedError = dialog.querySelector("[data-confirm-typed-error]");
  const cancelBtn = dialog.querySelector("[data-confirm-cancel]");
  const okBtn = dialog.querySelector("[data-confirm-ok]");
  if (!(okBtn instanceof HTMLButtonElement) || !(cancelBtn instanceof HTMLButtonElement)) {
    return Promise.resolve(false);
  }
  if (titleEl) titleEl.textContent = title;
  if (messageEl) messageEl.textContent = message;
  okBtn.textContent = confirmLabel;
  okBtn.classList.toggle("button--danger", Boolean(danger));
  const needsTyped = typeof typedExpected === "string" && typedExpected.length > 0;
  if (typedBox instanceof HTMLElement) typedBox.hidden = !needsTyped;
  if (typedLabel && needsTyped) typedLabel.textContent = `确认请输入数字 ${typedExpected}`;
  if (typedInput instanceof HTMLInputElement) typedInput.value = "";
  if (typedError instanceof HTMLElement) {
    typedError.hidden = true;
    typedError.textContent = "";
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (accepted) => {
      if (settled) return;
      settled = true;
      okBtn.removeEventListener("click", handleOk);
      cancelBtn.removeEventListener("click", handleCancel);
      dialog.removeEventListener("cancel", handleCancel);
      dialog.removeEventListener("click", handleBackdropClick);
      typedInput?.removeEventListener("keydown", handleInputKeydown);
      if (dialog.open) dialog.close();
      if (!accepted && previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus();
      }
      resolve(accepted);
    };
    const handleOk = () => {
      if (needsTyped && typedInput instanceof HTMLInputElement) {
        if (typedInput.value.trim() !== typedExpected) {
          if (typedError instanceof HTMLElement) {
            typedError.textContent = `请输入数字 ${typedExpected} 以确认`;
            typedError.hidden = false;
          }
          typedInput.focus();
          return;
        }
      }
      finish(true);
    };
    const handleCancel = (event) => {
      if (event) event.preventDefault();
      finish(false);
    };
    const handleBackdropClick = (event) => {
      if (event.target === dialog) finish(false);
    };
    const handleInputKeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        handleOk();
      }
    };

    okBtn.addEventListener("click", handleOk);
    cancelBtn.addEventListener("click", handleCancel);
    dialog.addEventListener("cancel", handleCancel);
    dialog.addEventListener("click", handleBackdropClick);
    typedInput?.addEventListener("keydown", handleInputKeydown);

    dialog.showModal();
    if (needsTyped && typedInput instanceof HTMLInputElement) typedInput.focus();
    else if (danger) cancelBtn.focus();
    else okBtn.focus();
  });
}

const dirtyForms = new Set();

/** 解析本次提交动作对应的确认文案，避免按钮级 formaction 误继承表单级文案。 */
function resolveActionConfirm(form, submitter) {
  if (submitter instanceof HTMLElement) {
    if (submitter.hasAttribute("data-typed-confirm")) return null;
    const own = submitter.getAttribute("data-confirm");
    if (own) {
      return {
        message: fillConfirmPlaceholders(own, form),
        danger: submitter.classList.contains("button--danger") || form.hasAttribute("data-danger-confirm"),
      };
    }
    if (submitter.hasAttribute("formaction")) return null;
  }
  if (!form.hasAttribute("data-confirm") && !form.hasAttribute("data-danger-confirm")) return null;
  const prompt = form.getAttribute("data-confirm")
    || form.getAttribute("data-danger-confirm")
    || "确定继续吗？";
  const danger = form.hasAttribute("data-danger-confirm")
    || (submitter instanceof HTMLElement && submitter.classList.contains("button--danger"));
  return { message: fillConfirmPlaceholders(prompt, form), danger };
}

/** 统一拦截需要页内确认（含手输数量、动作确认、跨表单未保存提示）的提交。 */
document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    if (form.dataset.dialogConfirmed === "true") {
      delete form.dataset.dialogConfirmed;
      return;
    }

    const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
    const isTyped = Boolean(submitter && submitter.hasAttribute("data-typed-confirm"));
    const actionConfirm = isTyped ? null : resolveActionConfirm(form, submitter);
    const hasOtherDirtyForm = [...dirtyForms].some((dirtyForm) => dirtyForm !== form);

    if (!isTyped && !actionConfirm && !hasOtherDirtyForm) return;

    event.preventDefault();
    event.stopImmediatePropagation();

    const runConfirmFlow = async () => {
      if (isTyped && submitter) {
        const selected = selectedTaskIds(form);
        const field = form.querySelector("[data-confirm-count]");
        if (field instanceof HTMLInputElement) field.value = "0";
        if (!selected.size) {
          showToast("请先勾选要处理的任务。");
          return;
        }
        const detail = (submitter.getAttribute("data-typed-confirm-detail")
          || "即将处理 {n} 条任务，此操作不可恢复。").replaceAll("{n}", () => String(selected.size));
        const ok = await openConfirmDialog({
          title: (submitter.getAttribute("data-typed-confirm") || "确认不可逆操作")
            .replaceAll("{n}", () => String(selected.size)),
          message: detail,
          confirmLabel: "确认执行",
          danger: true,
          typedExpected: String(selected.size),
        });
        if (!ok) return;
        if (field instanceof HTMLInputElement) field.value = String(selected.size);
      } else if (actionConfirm) {
        const ok = await openConfirmDialog({
          title: actionConfirm.danger ? "请确认敏感操作" : "请确认本次操作",
          message: actionConfirm.message,
          confirmLabel: actionConfirm.danger ? "确认执行" : "确认继续",
          danger: actionConfirm.danger,
        });
        if (!ok) return;
      }

      if (hasOtherDirtyForm) {
        const discardOk = await openConfirmDialog({
          title: "当前页面还有未保存内容",
          message: "继续提交将丢弃本页其它表单尚未保存的修改。确定继续吗？",
          confirmLabel: "丢弃并继续",
          danger: true,
        });
        if (!discardOk) return;
        dirtyForms.clear();
      }

      if (submitter instanceof HTMLButtonElement) {
        const accepted = submitter.getAttribute("data-confirm-accept-value");
        if (accepted !== null) submitter.value = accepted;
      }

      form.dataset.dialogConfirmed = "true";
      form.requestSubmit(submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement
        ? submitter
        : undefined);
    };

    void runConfirmFlow();
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

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    // 服务端已生成脱敏纯文本；浏览器只复制，不读取或过滤任何 raw 对象。
    const targetId = button.getAttribute("data-copy-target");
    const target = targetId ? document.getElementById(targetId) : null;
    if (!(target instanceof HTMLTextAreaElement)) return;
    // 反馈 2 秒后恢复原文案，避免按钮长期停在「已复制」让人误以为后续内容也复制过。
    const original = button.dataset.copyLabel || button.textContent || "";
    button.dataset.copyLabel = original;
    let feedback;
    try {
      await navigator.clipboard.writeText(target.value);
      feedback = "已复制";
      showToast("已复制到剪贴板");
    } catch {
      target.focus();
      target.select();
      feedback = "复制失败，已选中内容，请手动复制";
      showToast("复制失败，已选中文本，请手动复制");
    }
    button.textContent = feedback;
    window.clearTimeout(Number(button.dataset.copyTimer || 0));
    button.dataset.copyTimer = String(window.setTimeout(() => {
      button.textContent = original;
    }, 2000));
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

// 浏览器后退从往返缓存恢复页面时，脚本不会重跑，提交中的表单会一直锁着。
// 恢复时解除锁定并还原按钮文案，让用户能再次提交。
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  document.querySelectorAll("form[data-submitting='true']").forEach((form) => {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
  });
  document.querySelectorAll(".is-submitting").forEach((submitter) => {
    if (!(submitter instanceof HTMLButtonElement || submitter instanceof HTMLInputElement)) return;
    const label = submitter.dataset.originalLabel;
    if (label !== undefined) {
      if (submitter instanceof HTMLInputElement) submitter.value = label;
      else submitter.textContent = label;
    }
    submitter.classList.remove("is-submitting");
    submitter.removeAttribute("aria-busy");
    submitter.removeAttribute("aria-disabled");
    submitter.disabled = false;
  });
});

// 桌面表格和手机卡片各渲染一份同名勾选框，同一个任务因此有两个控件。
// 只让当前可见的那一份可提交：另一份保持同步但禁用，否则「全选」会连隐藏
// 副本一起勾上，而用户取消可见那份时隐藏副本仍然被提交，条数也会翻倍。
function selectableBoxes(form) {
  return Array.from(
    form.querySelectorAll('input[name="task_ids"]:not(:disabled)'),
  );
}

/** 让每个任务只保留一个可提交的勾选框，并在断点切换后继承已有选择。 */
function syncMirroredSelection(form) {
  const groups = new Map();
  form.querySelectorAll('input[name="task_ids"]').forEach((box) => {
    const peers = groups.get(box.value) || [];
    peers.push(box);
    groups.set(box.value, peers);
  });
  groups.forEach((peers) => {
    if (peers.length < 2) return;
    // 只有仍然启用的副本代表真实选择；禁用副本的 checked 是上一轮的残留。
    const checked = peers.some((box) => box.checked && !box.disabled);
    const visible = peers.filter((box) => box.offsetParent !== null);
    const active = visible.length ? visible[0] : peers[0];
    peers.forEach((box) => {
      box.disabled = box !== active;
      box.checked = box === active && checked;
    });
  });
}

// 全选本页与镜像同步都只在脚本可用时增强；脚本缺失时逐条勾选仍然可用，且此时
// 只有可见副本会被用户勾上，不会出现看不见的选择。
const selectionForms = new Set();
document.querySelectorAll('input[name="task_ids"]').forEach((box) => {
  if (box.form) selectionForms.add(box.form);
});

// 空队列也必须拦住程序化提交，不能等有任务复选框才注册保护。
document.querySelectorAll("form[data-default-action]").forEach((form) => selectionForms.add(form));

/** 本次会真正提交的任务编号：只算启用的勾选框，并按编号去重。 */
function selectedTaskIds(form) {
  return new Set(
    selectableBoxes(form).filter((box) => box.checked).map((box) => box.value),
  );
}

selectionForms.forEach((form) => {
  const toggles = Array.from(form.querySelectorAll("[data-select-all]"));
  const counter = form.querySelector("[data-selection-count]");
  const toolbar = form.querySelector(".selection-actions");
  const enhanced = form.hasAttribute("data-default-action");
  const actions = Array.from(form.querySelectorAll("button[data-requires]"));
  const labels = new Map(actions.map((button) => [button, button.textContent.trim()]));
  const verbs = { assign: "分派", cancel: "取消", archive: "归档", purge: "永久删除" };
  const employee = form.querySelector('select[name="assigned_employee_id"]');
  const employeePrompt = "请先选择要分派给哪位员工";
  // 只在分派被拒后提示缺员工；资格错误优先，补选员工或清空任务后移除旧提示。
  let missingEmployee = false;
  form.querySelectorAll("[data-selection-enhanced]").forEach((element) => {
    element.hidden = false;
  });
  /** 按实际提交副本与编号计数，资格完全来自服务端，不在脚本复制状态规则。 */
  const eligibleIds = (action) => new Set(selectableBoxes(form)
    .filter((box) => box.checked && (box.dataset.eligible || "").split(" ").includes(action))
    .map((box) => box.value));
  /** 手机操作区高度由原生观察器测量，给整个页面留空间，保护分页与新建表单。 */
  const measureToolbar = () => {
    const height = toolbar && toolbar.hasAttribute("data-fixed-bulk")
      ? toolbar.getBoundingClientRect().height : 0;
    document.documentElement.style.setProperty("--bulk-toolbar-height", `${height}px`);
    // 操作区出现或增高后，原生滚动避开顶栏和底栏，不遮住当前勾选焦点。
    const focused = document.activeElement;
    if (height && focused instanceof HTMLElement && form.contains(focused) && !toolbar.contains(focused)) {
      focused.scrollIntoView({ block: "nearest" });
    }
  };
  const refresh = () => {
    const all = selectableBoxes(form);
    const count = selectedTaskIds(form).size;
    const checked = all.filter((box) => box.checked).length;
    toggles.forEach((toggle) => {
      toggle.checked = checked === all.length && all.length > 0;
      toggle.indeterminate = checked > 0 && checked < all.length;
    });
    if (counter) counter.textContent = `已选 ${count} 项`;
    // 选择或布局发生变化后，旧手输确认作废；提交时必须重新确认当前数量。
    const field = form.querySelector("[data-confirm-count]");
    if (field) field.value = "0";
    actions.forEach((button) => {
      // 提交后的按钮必须保持忙态；镜像和操作栏尺寸仍在下方继续同步。
      if (form.dataset.submitting === "true") return;
      const action = button.dataset.requires;
      const eligible = eligibleIds(action).size;
      button.disabled = count > 0 && eligible !== count;
      button.textContent = labels.get(button) + (count ? `（${eligible === count ? count : `${eligible}/${count}`}）` : "");
      const hint = form.querySelector(`[data-bulk-hint="${action}"]`);
      const keep = form.querySelector(`[data-keep-eligible="${action}"]`);
      if (hint) hint.textContent = !button.disabled
        ? (action === "assign" && missingEmployee && count && !employee?.value ? employeePrompt : "") : eligible
        ? `已选 ${count} 条，其中 ${count - eligible} 条不能${verbs[action]}`
        : `已选的任务都不能${verbs[action]}`;
      if (keep) {
        keep.hidden = !button.disabled || eligible === 0;
        keep.textContent = `只保留可${verbs[action]}的 ${eligible} 条`;
      }
    });
    if (toolbar && enhanced) toolbar.toggleAttribute("data-fixed-bulk", count > 0 && window.matchMedia("(max-width: 767.98px)").matches);
    measureToolbar();
  };
  syncMirroredSelection(form);
  refresh();
  if (toolbar && enhanced) new ResizeObserver(measureToolbar).observe(toolbar);
  toggles.forEach((toggle) => toggle.addEventListener("change", () => {
    selectableBoxes(form).forEach((box) => { box.checked = toggle.checked; });
    refresh();
  }));
  const clear = form.querySelector("[data-select-clear]");
  if (clear) clear.addEventListener("click", () => {
    selectableBoxes(form).forEach((box) => { box.checked = false; });
    refresh();
  });
  form.querySelectorAll("[data-keep-eligible]").forEach((button) => {
    button.addEventListener("click", () => {
      const allowed = eligibleIds(button.dataset.keepEligible);
      selectableBoxes(form).forEach((box) => { box.checked = allowed.has(box.value); });
      syncMirroredSelection(form);
      refresh();
    });
  });
  form.addEventListener("change", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.name === "task_ids") refresh();
    if (event.target === employee) {
      if (employee.value) missingEmployee = false;
      refresh();
    }
  });
  /** 捕获点击和提交两条入口，先挡住无效批次，再进入确认、手输与忙态。 */
  const guard = (event, submitter) => {
    // 重复入口在捕获阶段结束，避免到冒泡忙态锁之前再次弹出确认框。
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    const count = selectedTaskIds(form).size;
    const action = submitter?.dataset.requires || form.dataset.defaultAction;
    if (count > 0 && (!enhanced || eligibleIds(action).size === count)) {
      // 捕获阶段先检查目标，避免以「选择员工…」弹出确认并在回跳时丢掉勾选。
      if (enhanced && action === "assign" && employee && !employee.value) {
        event.preventDefault();
        event.stopImmediatePropagation();
        missingEmployee = true;
        refresh();
        employee.focus();
      }
      return;
    }
    // 未标记的旧表单保留原手输空选择提示。
    if (!enhanced && submitter?.hasAttribute("data-typed-confirm")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const actionHint = count ? form.querySelector(`[data-bulk-hint="${action}"]`) : null;
    const hint = actionHint || counter;
    if (hint) {
      if (!count) hint.textContent = "请先勾选任务";
      else if (!actionHint) hint.textContent = `已选 ${count} 项，已选的任务都不能${verbs[action]}`;
      hint.focus();
    } else showToast("请先勾选任务。");
  };
  form.addEventListener("click", (event) => {
    const button = event.target.closest("button[type=submit]");
    if (button && enhanced) guard(event, button);
  }, true);
  form.addEventListener("submit", (event) => guard(event, event.submitter), true);
  window.addEventListener("resize", () => {
    syncMirroredSelection(form);
    refresh();
  });
});

// 待我关注的提醒分组全选：与任务列表不同，这里每个勾选框代表一整组提醒，
// 不存在双布局镜像，因此只需要一个直白的全选。脚本缺失时逐组勾选仍然可用。
document.querySelectorAll("[data-select-all-reminders]").forEach((toggle) => {
  const form = toggle.closest("form");
  if (!form) return;
  const groups = () =>
    Array.from(form.querySelectorAll("[data-reminder-group]"));
  toggle.addEventListener("change", () => {
    groups().forEach((box) => {
      box.checked = toggle.checked;
    });
  });
  form.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (!target.hasAttribute("data-reminder-group")) return;
    const all = groups();
    const checked = all.filter((box) => box.checked);
    toggle.checked = checked.length === all.length && all.length > 0;
    toggle.indeterminate = checked.length > 0 && checked.length < all.length;
  });
});

// 不可逆的批量手输确认已统一并入上方的页内 <dialog> 提交确认链，此处不再重复弹 window.prompt。

// 入住安排倒计时与跨节点刷新（Spec §6/§9）。
// 只建立一个页面级分钟定时器；倒计时按「服务器观察时刻 + 页面加载后经过的时间」
// 推进，不依赖设备时钟正确。跨过计划节点／当地午夜／隐藏超过一分钟后恢复时，若无
// 未保存或提交中的内容，则对本只读页执行一次 GET 刷新；有未保存内容则只提示不导航。
(function initOperationsCountdown() {
  const root = document.querySelector("[data-operations-refresh][data-server-now]");
  const cells = Array.from(document.querySelectorAll(".countdown"));
  if (!root || cells.length === 0) return;

  const serverNowMs = Date.parse(root.getAttribute("data-server-now"));
  if (Number.isNaN(serverNowMs)) return;
  const perfStart = performance.now();
  // 以「加载时已越过的节点数 + 当地日期」为签名，签名变化即说明跨过了节点或午夜。
  let baselineSignature = null;
  let refreshing = false;

  function serverNow() {
    return serverNowMs + (performance.now() - perfStart);
  }

  function formatDuration(totalMinutes) {
    if (totalMinutes < 1) return "不足 1 分钟";
    const days = Math.floor(totalMinutes / 1440);
    const hours = Math.floor((totalMinutes % 1440) / 60);
    const minutes = totalMinutes % 60;
    if (totalMinutes >= 1440) {
      return hours ? `${days} 天 ${hours} 小时` : `${days} 天`;
    }
    if (!hours) return `${minutes} 分钟`;
    return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
  }

  // 倒计时数字单独强调，保留完整的计划／待确认语义，不新增计时器。
  function renderDuration(cell, prefix, minutes, suffix = "") {
    const duration = document.createElement("strong");
    duration.textContent = formatDuration(minutes);
    cell.replaceChildren(prefix + " ", duration, suffix);
  }

  // 只根据服务器观察时间渲染文案，不把到达计划节点推断为已入住或已退房。
  function renderCell(cell, now) {
    const kind = cell.getAttribute("data-kind");
    const verified = cell.getAttribute("data-verified") === "1";
    const ambiguous = cell.getAttribute("data-ambiguous") === "1";
    const targetMs = Date.parse(cell.getAttribute("data-target"));
    if (ambiguous || Number.isNaN(targetMs)) return; // 服务端静态文案已足够
    const diffMs = targetMs - now;
    // 用原始差值判正负，避免四舍五入提前跨节点。
    const mins = Math.floor(Math.abs(diffMs) / 60000);
    if (kind === "checkout") {
      if (verified) { cell.textContent = "已退房"; return; }
      if (diffMs > 0) {
        renderDuration(cell, "距计划退房还有", mins);
      } else if (diffMs > -60000) {
        cell.textContent = "已到计划退房时间 · 退房待确认";
      } else {
        renderDuration(cell, "计划退房时间已过", mins, " · 退房待确认");
      }
      return;
    }
    if (diffMs > 0) {
      renderDuration(cell, "距可入住时间还有", mins);
    } else {
      cell.textContent = "已到可入住时间 · 到店待确认";
    }
  }

  const shanghaiDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" });

  function crossedSignature(now) {
    // 越过的节点数：所有目标里 now 已达到或超过的个数；再拼当地日期。
    let passed = 0;
    cells.forEach((cell) => {
      const t = Date.parse(cell.getAttribute("data-target"));
      if (!Number.isNaN(t) && now >= t) passed += 1;
    });
    // 午夜按民宿所在时区判断，不随员工设备时区变化。
    return shanghaiDate.format(new Date(now)) + "#" + passed;
  }

  function hasUnsavedWork() {
    // 复用未保存表单集合；提交中的按钮也算进行中，不打断。
    if (typeof dirtyForms !== "undefined" && dirtyForms.size > 0) return true;
    // 表单在 submit 事件里同步标记，按钮要等 0 ms 定时器才标记；两者都查，堵住这段空档。
    return document.querySelector("form[data-submitting='true'], [aria-busy='true']") !== null;
  }

  function showStaleHint() {
    if (document.querySelector("[data-schedule-stale-hint]")) return;
    const hint = document.createElement("div");
    hint.className = "alert alert--warning";
    hint.setAttribute("role", "status");
    hint.setAttribute("data-schedule-stale-hint", "");
    hint.innerHTML =
      '安排可能已变化，保存后刷新。<a href="">刷新入住安排</a>';
    root.appendChild(hint);
  }

  function tick() {
    const now = serverNow();
    cells.forEach((cell) => renderCell(cell, now));
    const signature = crossedSignature(now);
    if (baselineSignature === null) { baselineSignature = signature; return; }
    if (signature === baselineSignature || refreshing) return;
    // 跨过节点或午夜：无未保存内容则单次 GET 刷新，否则只提示不导航。
    if (hasUnsavedWork()) { showStaleHint(); return; }
    refreshing = true;
    window.location.reload();
  }

  tick();
  window.setInterval(tick, 60000);
  let hiddenAt = null;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { hiddenAt = Date.now(); return; }
    // 恢复可见且隐藏超过一分钟：立即重算一次（tick 内部再决定是否刷新）。
    if (hiddenAt !== null && Date.now() - hiddenAt > 60000) tick();
    hiddenAt = null;
  });
})();
