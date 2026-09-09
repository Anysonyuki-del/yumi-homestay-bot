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
  return text.replace("{n}", String(selected.size)).replace("{employee}", chosen);
}

// 确认文案必须跟随「这次点的是哪个动作」，而不是表单的默认动作。同一个表单上
// 挂着归档、分派、取消、永久删除四个按钮，后三个靠 formaction 改写目标；表单级
// data-confirm 是写给默认动作（归档）的，被它们继承就会出现「点分派，问你是否
// 移入归档」这种把人引向错误心理模型的提示。
document.querySelectorAll("form[data-confirm], form[data-danger-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    if (submitter instanceof HTMLElement) {
      // 已经做过更强的手输确认，不再叠加一句更弱的。
      if (submitter.hasAttribute("data-typed-confirm")) return;
      const own = submitter.getAttribute("data-confirm");
      if (own) {
        if (!window.confirm(fillConfirmPlaceholders(own, form))) event.preventDefault();
        return;
      }
      // 改写了目标动作却没带自己的文案：表单级文案不属于它，宁可不问也不误导。
      if (submitter.hasAttribute("formaction")) return;
    }
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

selectionForms.forEach((form) => {
  const toggle = form.querySelector("[data-select-all]");
  const refreshToggle = () => {
    if (!(toggle instanceof HTMLInputElement)) return;
    const all = selectableBoxes(form);
    const checked = all.filter((box) => box.checked);
    toggle.checked = checked.length === all.length && all.length > 0;
    // 部分选中时显示不确定态，避免全选框看起来是「已全选」。
    toggle.indeterminate = checked.length > 0 && checked.length < all.length;
  };
  syncMirroredSelection(form);
  if (toggle instanceof HTMLInputElement) {
    toggle.addEventListener("change", () => {
      selectableBoxes(form).forEach((box) => {
        box.checked = toggle.checked;
      });
    });
  }
  form.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.name !== "task_ids") return;
    refreshToggle();
  });
  // 换断点会互换可见副本，重新对齐后全选框也要跟着回到正确状态。
  window.addEventListener("resize", () => {
    syncMirroredSelection(form);
    refreshToggle();
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

// 不可逆的批量操作要求手输条数：挡住「习惯性点确定」这一整类事故，
// 提交者必须真的看过数量。脚本缺失时该按钮提交的确认数为 0，服务端会拒绝。
document.querySelectorAll("button[data-typed-confirm]").forEach((button) => {
  button.addEventListener("click", (event) => {
    const form = button.form;
    if (!form) return;
    // 按任务编号去重：镜像副本理应已被禁用，这里再兜一次，确保用户看到的
    // 数字、输入的数字和服务端收到的编号数三者一致。
    const selected = new Set(
      Array.from(
        form.querySelectorAll('input[name="task_ids"]:checked:not(:disabled)'),
      ).map((box) => box.value),
    );
    const field = form.querySelector("[data-confirm-count]");
    if (selected.size === 0) {
      window.alert("请先勾选要删除的任务。");
      event.preventDefault();
      return;
    }
    const label = button.getAttribute("data-typed-confirm") || "删除";
    // 后果由按钮自己说明：同一套手输确认现在服务于删除和取消两种不可逆动作，
    // 把措辞写死在脚本里，另一种动作就会读到一句与它无关的警告。
    const detail = (button.getAttribute("data-typed-confirm-detail")
      || "即将处理 {n} 条任务，此操作不可恢复。").replace("{n}", String(selected.size));
    const answer = window.prompt(
      `${label}：${detail}\n确认请输入数字 ${selected.size}。`,
    );
    if (answer === null || answer.trim() !== String(selected.size)) {
      event.preventDefault();
      return;
    }
    if (field) field.value = String(selected.size);
  });
});

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

  function crossedSignature(now) {
    // 越过的节点数：所有目标里 now 已达到或超过的个数；再拼当地日期。
    let passed = 0;
    cells.forEach((cell) => {
      const t = Date.parse(cell.getAttribute("data-target"));
      if (!Number.isNaN(t) && now >= t) passed += 1;
    });
    return new Date(now).toDateString() + "#" + passed;
  }

  function hasUnsavedWork() {
    // 复用未保存表单集合；提交中的按钮也算进行中，不打断。
    if (typeof dirtyForms !== "undefined" && dirtyForms.size > 0) return true;
    return document.querySelector("button[aria-busy='true']") !== null;
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
