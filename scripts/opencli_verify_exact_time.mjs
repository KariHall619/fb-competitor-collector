#!/usr/bin/env node
/**
 * Verify that the current logged-in Facebook Chrome tab exposes exact post
 * times through timestamp DOM attributes or Facebook's hover tooltip.
 *
 * The backend is OpenCLI Browser Bridge.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import {
  ensureFacebookTab,
  evaluateInSession,
  extractArgs,
  facebookTab,
  loadOpencliContext,
  matchesAccount,
  outputJson,
  runOpencli,
} from "./opencli_runtime.mjs";

const require = createRequire(import.meta.url);
const { browserExactTimeHelpersExpression } = require("./fb_time_extractors.js");

const PROCESS = globalThis.process || { argv: [], env: {} };
const { value, has, argv: args } = extractArgs(Array.isArray(PROCESS.argv) ? PROCESS.argv.slice(2) : []);
const ACCOUNT_URL = value("--account-url", "");
const LIMIT = Number(value("--limit", "8"));
const ALLOW_REAL_MOUSE_HOVER = has("--allow-real-mouse-hover");
const CURRENT_FILE = fileURLToPath(import.meta.url);
const INVOKED_FILE = PROCESS.argv?.[1] ? path.resolve(PROCESS.argv[1]) : "";
const DEBUG_ENTRY = has("--debug-entry") || PROCESS.env?.FB_EXACT_TIME_VERIFY_DEBUG === "1";
const RUN_MAIN = (
  has("--run") || PROCESS.env?.FB_EXACT_TIME_VERIFY_RUN === "1"
) && !has("--self-test") && !DEBUG_ENTRY;

if (DEBUG_ENTRY) {
  outputJson({
    current_file: CURRENT_FILE,
    invoked_file: INVOKED_FILE,
    argv: PROCESS.argv,
    run_main: RUN_MAIN,
    backend: "opencli_browser_bridge",
  });
  if (globalThis.process) globalThis.process.exitCode = 0;
}

function summarizeExactTimeChecks({ scan, checks, tab, claimedFrom, allowRealMouseHover = false }) {
  const confirmed = checks.filter((item) => item.confirmed);
  return {
    ok: confirmed.length > 0,
    status: confirmed.length > 0 ? "exact_time_confirmed" : "exact_time_not_found",
    route: "opencli_browser_bridge",
    allow_real_mouse_hover: allowRealMouseHover,
    capture_profile: captureProfile,
    tab,
    target_count: scan.target_count,
    exact_dom_count: scan.exact_dom_count,
    checked_count: checks.length,
    confirmed_count: confirmed.length,
    confirmed_examples: confirmed.slice(0, 5),
    checks,
    opened_from: openedFrom || claimedFrom,
    message: confirmed.length > 0
      ? "已确认能从 Facebook DOM 属性或时间悬停提示获取精确发帖时间。"
      : "未能从当前可见时间元素获取精确发帖时间；如果候选仍有相对时间标签，正式输出可以使用估算时间并在表格中标注“约”。",
  };
}

async function evalPayload(context, js) {
  const result = await evaluateInSession({
    opencliCommand: context.opencliCommand,
    session: context.session,
    tab: context.tab.page,
    js,
  });
  if (!result.ok) {
    throw new Error(result.stderr || result.stdout || "OpenCLI eval failed");
  }
  return result.payload;
}

async function pageState(context) {
  return await evalPayload(context, `(() => {
    const body = document.body?.innerText || "";
    return {
      logged_out: /Log in to Facebook|登录 Facebook|Forgot Account|Forgot password|Forgotten password|Create new account|新建帐户|邮箱或手机号\\s+密码\\s+登录/i.test(body),
      visitor_preview: /(登录|Log in)\\s+(忘记账户了？|Forgot Account|Forgot password|Forgotten password)/i.test(body)
        || (/^\\s*登录\\s+忘记账户了？/i.test(body) && body.length < 20000),
      body_length: body.length,
      body_preview: body.slice(0, 1000),
    };
  })()`);
}

async function timestampTargets(context) {
  return await evalPayload(context, `(() => {
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
  })()`);
}

async function readTooltipWithSyntheticHover(context, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  return await evalPayload(context, `(async () => {
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
  })()`);
}

async function readTooltipWithRealMouse(context, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  await runOpencli([
    "browser",
    context.session,
    "hover",
    `a, abbr, span`,
    "--nth",
    String(target.index),
    "--tab",
    context.tab.page,
  ], { command: context.opencliCommand });
  await runOpencli(["browser", context.session, "wait", "time", "1.5", "--tab", context.tab.page], { command: context.opencliCommand });
  return await evalPayload(context, `(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const tooltip = [...document.querySelectorAll('[role="tooltip"], div, span')]
      .map((node) => helpers.clean(node.innerText || node.textContent || ""))
      .find((text) => helpers.parseExactFacebookTime(text));
    if (!tooltip) return { posted_at_raw: "", posted_at: "", time_source: "" };
    return { posted_at_raw: tooltip, posted_at: helpers.parseExactFacebookTime(tooltip), time_source: "real_mouse_tooltip" };
  })()`);
}

async function main() {
  const result = await verifyExactTimeCapture({
    accountUrl: ACCOUNT_URL,
    limit: LIMIT,
    allowRealMouseHover: ALLOW_REAL_MOUSE_HOVER,
  });
  outputJson(result);
  return result.exit_code ?? (result.ok ? 0 : 1);
}

async function verifyExactTimeCapture(options = {}) {
  const accountUrl = options.accountUrl ?? ACCOUNT_URL;
  const limit = Number(options.limit ?? LIMIT);
  const allowRealMouseHover = Boolean(options.allowRealMouseHover ?? ALLOW_REAL_MOUSE_HOVER);
  const baseContext = options.context || loadOpencliContext(args);
  const tabResult = options.tab
    ? { ok: true, tab: options.tab }
    : await ensureFacebookTab({
      opencliCommand: baseContext.opencliCommand,
      session: baseContext.session,
      accountUrl,
    });
  if (!tabResult.ok) return tabResult;
  const context = { ...baseContext, tab: tabResult.tab };

  const state = await pageState(context);
  if (state.logged_out || state.visitor_preview) {
    return {
      ok: false,
      status: state.logged_out ? "login_required" : "visitor_preview",
      exit_code: 5,
      action_required: "human_intervention_required",
      message: "当前 Facebook 标签页没有完整登录态或只显示游客预览，无法验证精确时间。请人工登录并确认页面能看到多条帖子后再重试。",
      tab: tabResult.tab,
      body_preview: state.body_preview,
    };
  }

  const scan = await timestampTargets(context);
  const checks = [];
  for (const target of scan.targets.slice(0, limit || scan.targets.length)) {
    let exact = target.exact || { posted_at_raw: "", posted_at: "", time_source: "" };
    if (!exact.posted_at) exact = await readTooltipWithSyntheticHover(context, target);
    if (!exact.posted_at && allowRealMouseHover) exact = await readTooltipWithRealMouse(context, target);
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
    claimedFrom: tabResult.tab.url,
    tab: tabResult.tab,
  });
  return {
    ...summary,
    exit_code: summary.ok ? 0 : 6,
  };
}

if (RUN_MAIN) {
  const exitCode = await main().catch((error) => {
    outputJson({
      ok: false,
      status: "opencli_exact_time_verify_failed",
      error: String(error.stack || error),
    });
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
