#!/usr/bin/env node
/**
 * Verify that the current logged-in Facebook Chrome tab exposes exact post
 * times through timestamp DOM attributes or Facebook's hover tooltip.
 *
 * This is a validation gate only. It does not import, store, or sync data.
 */

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { browserExactTimeHelpersExpression } = require("./fb_time_extractors.js");

const PROCESS = globalThis.process || { argv: [], env: {} };
const args = Array.isArray(PROCESS.argv) ? PROCESS.argv.slice(2) : [];

function argValue(name, fallback = "") {
  const index = args.indexOf(name);
  if (index >= 0 && args[index + 1]) return args[index + 1];
  return fallback;
}

const ACCOUNT_URL = argValue("--account-url", "");
const LIMIT = Number(argValue("--limit", "8"));
const ALLOW_REAL_MOUSE_HOVER = args.includes("--allow-real-mouse-hover");
const CURRENT_FILE = fileURLToPath(import.meta.url);
const INVOKED_FILE = PROCESS.argv?.[1] ? path.resolve(PROCESS.argv[1]) : "";
const DEBUG_ENTRY = args.includes("--debug-entry") || PROCESS.env?.FB_EXACT_TIME_VERIFY_DEBUG === "1";
const RUN_MAIN = (
  args.includes("--run") || PROCESS.env?.FB_EXACT_TIME_VERIFY_RUN === "1"
) && !args.includes("--self-test") && !DEBUG_ENTRY;

if (DEBUG_ENTRY) {
  console.log(JSON.stringify({
    current_file: CURRENT_FILE,
    invoked_file: INVOKED_FILE,
    argv: PROCESS.argv,
    run_main: RUN_MAIN,
  }, null, 2));
  if (globalThis.process) globalThis.process.exitCode = 0;
}

function matchesAccount(tab, accountUrl) {
  if (!accountUrl) return true;
  try {
    const target = new URL(accountUrl);
    const current = new URL(tab.url || "");
    const targetId = target.searchParams.get("id");
    if (targetId && `${current.href} ${tab.title || ""}`.includes(targetId)) return true;
    const parts = target.pathname
      .split("/")
      .filter(Boolean)
      .filter((part) => !["people", "profile.php", "posts", "reels"].includes(part));
    return parts.length === 0 || parts.some((part) => `${current.href} ${tab.title || ""}`.includes(part));
  } catch {
    return true;
  }
}

function facebookTab(tab) {
  return /^https?:\/\/([^/]+\.)?facebook\.com\//i.test(tab.url || "");
}

function summarizeExactTimeChecks({ scan, checks, tab, claimedFrom, allowRealMouseHover = false }) {
  const confirmed = checks.filter((item) => item.confirmed);
  return {
    ok: confirmed.length > 0,
    status: confirmed.length > 0 ? "exact_time_confirmed" : "exact_time_not_found",
    route: "codex_chrome_extension",
    allow_real_mouse_hover: allowRealMouseHover,
    tab,
    target_count: scan.target_count,
    exact_dom_count: scan.exact_dom_count,
    checked_count: checks.length,
    confirmed_count: confirmed.length,
    confirmed_examples: confirmed.slice(0, 5),
    checks,
    claimed_from: claimedFrom,
    message: confirmed.length > 0
      ? "已确认能从 Facebook DOM 属性或时间悬停提示获取精确发帖时间。"
      : "未能从当前可见时间元素获取精确发帖时间；如果候选仍有相对时间标签，正式输出可以使用估算时间并在表格中标注“约”。",
  };
}

async function pageState(tab) {
  return await tab.playwright.evaluate(() => {
    const body = document.body?.innerText || "";
    return {
      logged_out: /Log in to Facebook|登录 Facebook|Forgot Account|Forgot password|Forgotten password|Create new account|新建帐户|邮箱或手机号\s+密码\s+登录/i.test(body),
      visitor_preview: /(登录|Log in)\s+(忘记账户了？|Forgot Account|Forgot password|Forgotten password)/i.test(body)
        || (/^\s*登录\s+忘记账户了？/i.test(body) && body.length < 20000),
      body_length: body.length,
      body_preview: body.slice(0, 1000),
    };
  }, undefined, { timeoutMs: 10000 });
}

async function timestampTargets(tab) {
  return await tab.playwright.evaluate(`(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const clean = helpers.clean;
    const viewportHeight = window.innerHeight || 800;
    const all = [...document.querySelectorAll("a, abbr, span")].map((el, index) => {
      const rect = el.getBoundingClientRect();
      const item = {
        index,
        tag: el.tagName,
        text: clean(el.innerText || el.textContent || ""),
        aria: clean(el.getAttribute("aria-label") || ""),
        title: clean(el.getAttribute("title") || ""),
        datetime: clean(el.getAttribute("datetime") || ""),
        tooltipContent: clean(el.getAttribute("data-tooltip-content") || ""),
        tooltipText: clean(el.getAttribute("data-tooltip-text") || ""),
        href: el.href || "",
        x: rect.x,
        y: rect.y,
        w: rect.width,
        h: rect.height,
      };
      return { ...item, exact: helpers.exactTimeFromItem(item) };
    });

    const targets = all
      .filter((item) => helpers.isLikelyHeaderTimeElement(item, viewportHeight))
      .sort((a, b) => a.y - b.y || a.x - b.x)
      .slice(0, 20);

    const exactDomTargets = targets.filter((item) => item.exact.posted_at);
    return {
      target_count: targets.length,
      exact_dom_count: exactDomTargets.length,
      targets,
      exact_dom_targets: exactDomTargets,
    };
  })()`, undefined, { timeoutMs: 15000 });
}

async function readTooltipWithSyntheticHover(tab, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  return await tab.playwright.evaluate(`(async () => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const elements = [...document.querySelectorAll("a, abbr, span")];
    const el = elements[${JSON.stringify(target.index)}];
    if (!el) return { posted_at_raw: "", posted_at: "", time_source: "" };
    const rect = el.getBoundingClientRect();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: Math.floor(rect.x + rect.width / 2),
      clientY: Math.floor(rect.y + rect.height / 2),
      view: window,
    };
    for (const eventName of ["pointerover", "mouseover", "mouseenter", "focus"]) {
      if (eventName === "focus" && typeof el.focus === "function") {
        el.focus();
        continue;
      }
      const EventCtor = eventName.startsWith("pointer") && typeof PointerEvent === "function"
        ? PointerEvent
        : typeof MouseEvent === "function"
          ? MouseEvent
          : null;
      if (!EventCtor) continue;
      el.dispatchEvent(new EventCtor(eventName, eventInit));
    }
    await sleep(1200);
    const texts = [...document.querySelectorAll('[role="tooltip"], div, span')]
      .map((node) => helpers.clean(node.innerText || node.textContent || ""))
      .filter(Boolean);
    for (const text of texts) {
      const parsed = helpers.parseExactFacebookTime(text);
      if (parsed) return { posted_at_raw: text, posted_at: parsed, time_source: "synthetic_hover_tooltip" };
    }
    return { posted_at_raw: "", posted_at: "", time_source: "" };
  })()`, undefined, { timeoutMs: 12000 });
}

async function readTooltipWithRealMouse(tab, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  await tab.cua.move({ x: Math.floor(target.x + target.w / 2), y: Math.floor(target.y + target.h / 2) });
  await tab.playwright.waitForTimeout(1500);
  return await tab.playwright.evaluate(`(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const tooltip = [...document.querySelectorAll('[role="tooltip"], div, span')]
      .map((node) => helpers.clean(node.innerText || node.textContent || ""))
      .find((text) => helpers.parseExactFacebookTime(text));
    if (!tooltip) return { posted_at_raw: "", posted_at: "", time_source: "" };
    return { posted_at_raw: tooltip, posted_at: helpers.parseExactFacebookTime(tooltip), time_source: "real_mouse_tooltip" };
  })()`, undefined, { timeoutMs: 10000 });
}

async function main() {
  const result = await verifyExactTimeCapture({
    accountUrl: ACCOUNT_URL,
    limit: LIMIT,
    allowRealMouseHover: ALLOW_REAL_MOUSE_HOVER,
  });
  console.log(JSON.stringify(result, null, 2));
  return result.exit_code ?? (result.ok ? 0 : 1);
}

async function verifyExactTimeCapture(options = {}) {
  const accountUrl = options.accountUrl ?? ACCOUNT_URL;
  const limit = Number(options.limit ?? LIMIT);
  const allowRealMouseHover = Boolean(options.allowRealMouseHover ?? ALLOW_REAL_MOUSE_HOVER);
  if (!options.browser && !globalThis.agent?.browsers) {
    const { setupBrowserRuntime } = await import("/Users/a1/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/browser-client.mjs");
    await setupBrowserRuntime({ globals: globalThis });
  }
  const browser = options.browser || globalThis.browser || await agent.browsers.get("extension");
  await browser.nameSession("FB exact time verification");
  const tabs = await browser.user.openTabs();
  const facebookTabs = tabs.filter(facebookTab);
  const tabInfo = facebookTabs.find((tab) => matchesAccount(tab, accountUrl)) || facebookTabs[0];
  if (!tabInfo) {
    return {
      ok: false,
      status: "facebook_tab_missing",
      exit_code: 5,
      action_required: "human_intervention_required",
      message: "未发现已打开的 Facebook 标签页。请业务同学先在正常 Chrome 中打开已登录且能看到帖子列表的 Facebook 页面。",
      open_tab_count: tabs.length,
    };
  }

  const tab = await browser.user.claimTab(tabInfo);
  const state = await pageState(tab);
  if (state.logged_out || state.visitor_preview) {
    return {
      ok: false,
      status: state.logged_out ? "login_required" : "visitor_preview",
      exit_code: 5,
      action_required: "human_intervention_required",
      message: "当前 Facebook 标签页没有完整登录态或只显示游客预览，无法验证精确时间。请人工登录并确认页面能看到多条帖子后再重试。",
      tab: {
        title: await tab.title(),
        url: await tab.url(),
        claimed_from: tabInfo.url,
      },
      body_preview: state.body_preview,
    };
  }

  const scan = await timestampTargets(tab);
  const checks = [];
  for (const target of scan.targets.slice(0, limit || scan.targets.length)) {
    let exact = target.exact || { posted_at_raw: "", posted_at: "", time_source: "" };
    if (!exact.posted_at) exact = await readTooltipWithSyntheticHover(tab, target);
    if (!exact.posted_at && allowRealMouseHover) exact = await readTooltipWithRealMouse(tab, target);
    checks.push({
      visible_text: target.text,
      href: target.href,
      source_rect: { x: target.x, y: target.y, w: target.w, h: target.h },
      posted_at_raw: exact.posted_at_raw,
      posted_at: exact.posted_at,
      time_source: exact.time_source,
      confirmed: Boolean(exact.posted_at),
    });
  }

  const summary = summarizeExactTimeChecks({
    scan,
    checks,
    allowRealMouseHover,
    claimedFrom: tabInfo.url,
    tab: {
      title: await tab.title(),
      url: await tab.url(),
      claimed_from: tabInfo.url,
    },
  });
  return {
    ...summary,
    exit_code: summary.ok ? 0 : 6,
  };
}

if (RUN_MAIN) {
  const exitCode = await main().catch((error) => {
    console.error(JSON.stringify({
      ok: false,
      status: "chrome_extension_exact_time_verify_failed",
      error: String(error.stack || error),
    }, null, 2));
    return 1;
  });

  if (globalThis.process) globalThis.process.exitCode = exitCode;
}

export {
  facebookTab,
  matchesAccount,
  summarizeExactTimeChecks,
  verifyExactTimeCapture,
  CURRENT_FILE,
  INVOKED_FILE,
  RUN_MAIN,
};
