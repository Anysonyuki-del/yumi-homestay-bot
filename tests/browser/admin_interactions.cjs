"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const projectRoot = path.resolve(__dirname, "../..");
const adminScript = fs.readFileSync(
  path.join(projectRoot, "src/homestay_bot/static/admin.js"),
  "utf8",
);
const adminCss = fs.readFileSync(
  path.join(projectRoot, "src/homestay_bot/static/app.css"),
  "utf8",
);

/** 优先使用当前 Playwright 路径，版本不匹配时回退到本机已安装 Chromium。 */
function chromiumExecutable() {
  const declared = chromium.executablePath();
  if (fs.existsSync(declared)) return declared;
  const cacheRoot = path.join(process.env.HOME, "Library/Caches/ms-playwright");
  const revisions = fs.readdirSync(cacheRoot)
    .filter((name) => name.startsWith("chromium_headless_shell-"))
    .sort()
    .reverse();
  for (const revision of revisions) {
    const candidate = path.join(
      cacheRoot,
      revision,
      "chrome-headless-shell-mac-arm64/chrome-headless-shell",
    );
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error("未找到可用的 Chromium 可执行文件");
}

/** 返回包含真实后台选择器的最小页面，用于验证渐进增强行为。 */
function adminFixture({ includeScript = true } = {}) {
  return `<!doctype html>
  <html lang="zh-CN"><head><style>${adminCss}</style></head>
  <body class="admin-body">
    <div class="admin-shell">
      <aside class="admin-sidebar" id="admin-drawer" data-drawer inert aria-hidden="true">
        <button type="button" data-drawer-close>关闭</button>
        <a href="#drawer-target">任务中心</a>
      </aside>
      <div class="drawer-backdrop" data-drawer-close hidden></div>
      <div class="admin-workspace">
        <header class="topbar"><button class="drawer-trigger" type="button" data-drawer-open aria-controls="admin-drawer" aria-expanded="false">打开导航</button><div class="topbar-title">后台页面标题</div><a href="#account">账号</a></header>
        <a href="#workspace-target">工作区链接</a>
        <form id="form-a" data-unsaved-warning><input name="a"><button type="submit">保存 A</button></form>
        <form id="form-b" data-unsaved-warning><input name="b"><button type="submit">保存 B</button></form>
      </div>
    </div>
    <details class="no-script-nav"><summary>打开页面导航</summary><a href="#fallback-target">任务中心</a></details>
    <div id="drawer-target"></div><div id="workspace-target"></div><div id="fallback-target"></div>
    ${includeScript ? `<script>${adminScript}</script>` : ""}
  </body></html>`;
}

/** 验证跨表单未保存确认取消后不锁按钮，确认后清理并防重复提交。 */
async function testCrossFormSubmission(browser) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.setContent(adminFixture());
  await page.fill("#form-a input", "尚未保存");
  await page.evaluate(() => {
    window.confirmCalls = [];
    window.confirm = (message) => {
      window.confirmCalls.push(message);
      return false;
    };
  });

  const currentForm = await page.evaluate(async () => {
    const form = document.querySelector("#form-a");
    const accepted = form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    return {
      accepted,
      submitting: form.dataset.submitting,
      disabled: form.querySelector("button").disabled,
      unloadPrevented: unload.defaultPrevented,
      confirmCalls: window.confirmCalls.length,
    };
  });
  assert.deepEqual(currentForm, {
    accepted: true,
    submitting: "true",
    disabled: true,
    unloadPrevented: false,
    confirmCalls: 0,
  });

  // 重新载入页面，单独验证 A 未保存时提交 B 的取消与确认分支。
  await page.goto("about:blank");
  await page.setContent(adminFixture());
  await page.fill("#form-a input", "尚未保存");
  await page.evaluate(() => {
    window.confirmCalls = [];
    window.confirm = (message) => {
      window.confirmCalls.push(message);
      return false;
    };
  });

  const cancelled = await page.evaluate(async () => {
    const form = document.querySelector("#form-b");
    const accepted = form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
    return {
      accepted,
      submitting: form.dataset.submitting || "",
      disabled: form.querySelector("button").disabled,
      confirmCalls: window.confirmCalls,
    };
  });
  assert.equal(cancelled.accepted, false);
  assert.equal(cancelled.submitting, "");
  assert.equal(cancelled.disabled, false);
  assert.equal(cancelled.confirmCalls.length, 1);

  await page.evaluate(() => {
    window.confirm = (message) => {
      window.confirmCalls.push(message);
      return true;
    };
  });
  const confirmed = await page.evaluate(async () => {
    const form = document.querySelector("#form-b");
    const first = form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    const second = form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 10));
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    return {
      first,
      second,
      submitting: form.dataset.submitting,
      disabled: form.querySelector("button").disabled,
      unloadPrevented: unload.defaultPrevented,
    };
  });
  assert.equal(confirmed.first, true);
  assert.equal(confirmed.second, false);
  assert.equal(confirmed.submitting, "true");
  assert.equal(confirmed.disabled, true);
  assert.equal(confirmed.unloadPrevented, false);
  await page.close();
}

/** 验证移动抽屉关闭不进入 Tab，打开可访问，ESC 恢复焦点，桌面自动恢复。 */
async function testDrawerAccessibility(browser) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.setContent(adminFixture());
  const initial = await page.locator("[data-drawer]").evaluate((drawer) => ({
    inert: drawer.inert,
    ariaHidden: drawer.getAttribute("aria-hidden"),
    visibility: getComputedStyle(drawer).visibility,
  }));
  assert.deepEqual(initial, { inert: true, ariaHidden: "true", visibility: "hidden" });

  await page.focus("[data-drawer-open]");
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement.closest("[data-drawer]") !== null), false);

  await page.click("[data-drawer-open]");
  assert.equal(await page.locator("[data-drawer]").evaluate((drawer) => drawer.inert), false);
  assert.equal(await page.locator("[data-drawer]").getAttribute("aria-hidden"), null);
  assert.equal(await page.locator("[data-drawer] [data-drawer-close]").evaluate((button) => button === document.activeElement), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("[data-drawer-open]").evaluate((button) => button === document.activeElement), true);
  assert.equal(await page.locator("[data-drawer]").evaluate((drawer) => drawer.inert), true);
  assert.equal(await page.locator("[data-drawer]").evaluate((drawer) => getComputedStyle(drawer).visibility), "hidden");

  await page.setViewportSize({ width: 1100, height: 844 });
  await page.waitForTimeout(20);
  assert.equal(await page.locator("[data-drawer]").evaluate((drawer) => drawer.inert), false);
  assert.equal(await page.locator("[data-drawer]").getAttribute("aria-hidden"), null);
  await page.focus('[data-drawer] a[href="#drawer-target"]');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(20);
  assert.equal(await page.locator("[data-drawer]").evaluate((drawer) => drawer.inert), true);
  await page.close();
}

/** 验证脚本不可用时隐藏抽屉，并保留可见且可点击的后备导航。 */
async function testNoScriptFallback(browser) {
  const page = await browser.newPage({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  await page.setContent(adminFixture({ includeScript: false }));
  assert.equal(await page.locator("[data-drawer]").evaluate((drawer) => getComputedStyle(drawer).display), "none");
  assert.notEqual(await page.locator(".no-script-nav").evaluate((nav) => getComputedStyle(nav).display), "none");
  assert.ok((await page.locator(".topbar-title").boundingBox()).width > 200);
  await page.locator(".no-script-nav summary").click();
  await page.locator('.no-script-nav a[href="#fallback-target"]').click();
  assert.equal(await page.evaluate(() => location.hash), "#fallback-target");
  await page.setViewportSize({ width: 1100, height: 844 });
  assert.notEqual(await page.locator(".no-script-nav").evaluate((nav) => getComputedStyle(nav).display), "none");
  assert.equal(await page.locator(".admin-workspace").evaluate((workspace) => getComputedStyle(workspace).marginLeft), "0px");
  await page.close();
}

/** 运行全部浏览器交互检查，并确保 Chromium 始终关闭。 */
async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromiumExecutable(),
  });
  try {
    const tests = [
      ["cross-form submission", testCrossFormSubmission],
      ["drawer accessibility", testDrawerAccessibility],
      ["no-script fallback", testNoScriptFallback],
    ];
    const failures = [];
    for (const [name, test] of tests) {
      try {
        await test(browser);
      } catch (error) {
        failures.push({ name, error });
        console.error(`${name}: failed\n`, error);
      }
    }
    if (failures.length > 0) throw new Error(`${failures.length} browser checks failed`);
    console.log(`admin interactions: ${tests.length} passed`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
