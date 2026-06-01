#!/usr/bin/env node
/**
 * Enrich prepared posts by opening each post detail page in the user's normal
 * Chrome profile through OpenCLI Browser Bridge, reading exact post time, and
 * checking comments/replies for account-owned lead links.
 *
 * The backend is OpenCLI Browser Bridge.
 */

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  evaluateInSession,
  extractArgs,
  loadOpencliContext,
  outputJson,
  parseJsonOutput,
  runOpencli,
} from "./opencli_runtime.mjs";

const require = createRequire(import.meta.url);
const { browserExactTimeHelpersExpression } = require("./fb_time_extractors.js");
const PROCESS = globalThis.process || { argv: [], env: {} };
const { value, has } = extractArgs(Array.isArray(PROCESS.argv) ? PROCESS.argv.slice(2) : []);

const INPUT = value("--input");
const OUTPUT = value("--output");
const CONFIG = value("--config", "config/settings.yaml");
const LIMIT = Number(value("--limit", "0"));
const TARGET_DATE = value("--target-date", "");
const ALLOW_REAL_MOUSE_HOVER = has("--allow-real-mouse-hover");
const configuredLeadLink = readLeadLinkConfig(CONFIG);
const COMMENT_EXPAND_ROUNDS = Number(value("--comment-expand-rounds", configuredLeadLink.commentExpandRounds));
const REPLY_EXPAND_ROUNDS = Number(value("--reply-expand-rounds", configuredLeadLink.replyExpandRounds));
const RESOLVE_TIMEOUT_MS = Number(value("--resolve-timeout-ms", String(configuredLeadLink.resolveTimeoutSeconds * 1000)));
const ALLOWED_DOMAINS = value("--allowed-domains", configuredLeadLink.allowedDomains.join(","))
  .split(",")
  .map((item) => item.trim().replace(/^www\./i, "").toLowerCase())
  .filter(Boolean);
const COMMENT_MODE_SEQUENCE = ["default", "all_comments", "newest"];
const CURRENT_FILE = fileURLToPath(import.meta.url);
const INVOKED_FILE = PROCESS.argv?.[1] ? path.resolve(PROCESS.argv[1]) : "";
const RUN_MAIN = CURRENT_FILE === INVOKED_FILE || has("--run");

if (RUN_MAIN && (!INPUT || !OUTPUT)) {
  console.error("Usage: opencli_enrich_post_details.mjs --input prepared.json --output enriched.json [--limit N]");
  if (globalThis.process) globalThis.process.exitCode = 2;
  throw new Error("missing required arguments");
}

function readLeadLinkConfig(configPath) {
  const fallback = {
    commentExpandRounds: 3,
    replyExpandRounds: 3,
    resolveTimeoutSeconds: 20,
    allowedDomains: [],
  };
  try {
    const resolved = path.resolve(configPath);
    if (!fs.existsSync(resolved)) return fallback;
    const text = fs.readFileSync(resolved, "utf8");
    const section = text.match(/^lead_link:\s*\n([\s\S]*?)(?=^\S|\z)/m);
    if (!section) return fallback;
    const body = section[1];
    const valueFor = (key) => {
      const match = body.match(new RegExp(`^\\s+${key}:\\s*(.*)$`, "m"));
      return match ? match[1].trim() : "";
    };
    const domainsRaw = valueFor("allowed_domains");
    const allowedDomains = domainsRaw.startsWith("[")
      ? domainsRaw.replace(/[[\]"']/g, "").split(",").map((item) => item.trim()).filter(Boolean)
      : [];
    return {
      commentExpandRounds: Number(valueFor("comment_expand_rounds") || fallback.commentExpandRounds),
      replyExpandRounds: Number(valueFor("reply_expand_rounds") || fallback.replyExpandRounds),
      resolveTimeoutSeconds: Number(valueFor("resolve_timeout_seconds") || fallback.resolveTimeoutSeconds),
      allowedDomains,
    };
  } catch {
    return fallback;
  }
}

function dateKeyFromPostedAt(postedAt) {
  const match = String(postedAt || "").match(/^(20\d\d)年(\d{1,2})月(\d{1,2})日\s+\d{2}:\d{2}$/);
  if (!match) return "";
  const [, year, month, day] = match;
  return `${year.slice(2)}${month.padStart(2, "0")}${day.padStart(2, "0")}`;
}

function appendSemicolonNote(existing, item) {
  const parts = String(existing || "").split("；").filter(Boolean);
  if (item && !parts.includes(item)) parts.push(item);
  return parts.join("；");
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

async function waitSeconds(context, seconds) {
  await runOpencli([
    "browser",
    context.session,
    "wait",
    "time",
    String(seconds),
    "--tab",
    context.tab.page,
  ], { command: context.opencliCommand });
}

async function openPostTab(baseContext, url) {
  const result = await runOpencli(["browser", baseContext.session, "tab", "new", url], {
    command: baseContext.opencliCommand,
  });
  const payload = parseJsonOutput(result);
  if (!result.ok || !payload?.page) {
    throw new Error(result.stderr || result.stdout || `OpenCLI failed to open tab for ${url}`);
  }
  const tab = { page: payload.page, url, title: "" };
  const context = { ...baseContext, tab };
  await waitSeconds(context, 3.5);
  return context;
}

async function closePostTab(context) {
  if (!context?.tab?.page) return;
  await runOpencli(["browser", context.session, "tab", "close", context.tab.page], {
    command: context.opencliCommand,
  });
}

async function pageState(context) {
  return await evalPayload(context, `(() => {
    const body = document.body?.innerText || "";
    return {
      loggedOut: /Log in to Facebook|登录 Facebook|Forgot Account|Forgot password|Create new account|邮箱或手机号\\s+密码\\s+登录/i.test(body),
      visitorPreview: /(登录|Log in)\\s+(忘记账户了？|Forgot Account|Forgot password|Forgotten password)/i.test(body),
      bodyPreview: body.slice(0, 1200),
    };
  })()`);
}

async function findHeaderTime(context) {
  return await evalPayload(context, `(() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const helpers = ${browserExactTimeHelpersExpression()};
    const viewportHeight = window.innerHeight || 800;
    const candidates = [...document.querySelectorAll("a, abbr, span")].map((el, index) => {
      const rect = el.getBoundingClientRect();
      return {
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
    }).filter((item) => helpers.isLikelyHeaderTimeElement(item, viewportHeight));
    candidates.sort((a, b) => a.y - b.y || a.x - b.x);
    return candidates[0] || null;
  })()`);
}

async function readExactTimeFromDom(context, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  return await evalPayload(context, `(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    return helpers.exactTimeFromItem(${JSON.stringify(target)});
  })()`);
}

async function readTooltipTimeWithSyntheticHover(context, target) {
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

async function readTooltipTimeWithRealMouse(context, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  await runOpencli([
    "browser",
    context.session,
    "hover",
    "a, abbr, span",
    "--nth",
    String(target.index),
    "--tab",
    context.tab.page,
  ], { command: context.opencliCommand });
  await waitSeconds(context, 1.8);
  return await evalPayload(context, `(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const tooltip = [...document.querySelectorAll('[role="tooltip"], div, span')]
      .map((el) => helpers.clean(el.innerText || el.textContent || ""))
      .find((text) => helpers.parseExactFacebookTime(text));
    if (!tooltip) return { posted_at_raw: "", posted_at: "", time_source: "" };
    return { posted_at_raw: tooltip, posted_at: helpers.parseExactFacebookTime(tooltip), time_source: "real_mouse_tooltip" };
  })()`);
}

function detailEngagementBrowserExpression(target) {
  return `(() => {
    const target = ${JSON.stringify(target || null)};
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const linesFrom = (value) => String(value || "").split(/\\n+/).map(clean).filter(Boolean);
    const countToken = "(\\\\d+(?:[.,]\\\\d+)?\\\\s*(?:K|k|M|m|万)?)";
    const parseCount = (value) => {
      const text = clean(value).replace(/,/g, "");
      const match = text.match(/(\\d+(?:\\.\\d+)?)\\s*(K|k|M|m|万)?/);
      if (!match) return null;
      let number = Number(match[1]);
      const unit = match[2] || "";
      if (/^k$/i.test(unit)) number *= 1000;
      if (/^m$/i.test(unit)) number *= 1000000;
      if (unit === "万") number *= 10000;
      return Number.isFinite(number) ? Math.round(number) : null;
    };
    const forbiddenChrome = (node) => Boolean(node?.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header'));
    const looksLikeAdOrShell = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\\s*·\\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const hasActionOrMetric = (text) => /\\bLike\\b|\\bComment\\b|\\bShare\\b|赞|评论|分享|All reactions|reactions?|likes?|comments?|shares?|views?|plays?|次播放/i.test(text);
    const scoreRoot = (node) => {
      const text = clean(node?.innerText || node?.textContent || "");
      if (!text || forbiddenChrome(node) || looksLikeAdOrShell(text)) return -1000;
      let score = 0;
      if (node.getAttribute?.("role") === "article" || /^(ARTICLE)$/i.test(node.tagName || "")) score += 30;
      if (hasActionOrMetric(text)) score += 20;
      if (/All reactions|comments?|shares?|评论|分享|赞/i.test(text)) score += 15;
      if (text.length >= 80) score += 5;
      if (text.length > 6500) score -= 40;
      return score;
    };
    const targetElement = target && Number.isInteger(target.index)
      ? [...document.querySelectorAll("a, abbr, span")][target.index]
      : null;
    const roots = [];
    const pushRoot = (node) => {
      if (node && !roots.includes(node)) roots.push(node);
    };
    pushRoot(targetElement?.closest?.('[role="article"], article'));
    let cursor = targetElement;
    for (let depth = 0; cursor && depth < 10; depth += 1) {
      pushRoot(cursor);
      cursor = cursor.parentElement;
    }
    const scored = roots
      .map((node) => ({ node, score: scoreRoot(node), text: clean(node?.innerText || node?.textContent || "") }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || a.text.length - b.text.length);
    const root = scored[0]?.node || null;
    if (!root) {
      return {
        raw: "",
        source: "detail_main_post_dom",
        confidence: "unanchored",
        warnings: ["main_post_root_not_found"],
      };
    }

    const result = {
      raw: "",
      detail_engagement_data: "",
      source: "detail_main_post_dom",
      confidence: "anchored",
      reactions: null,
      likes: null,
      comments: null,
      shares: null,
      views: null,
      root_text_preview: clean(root.innerText || root.textContent || "").slice(0, 600),
      warnings: [],
    };
    const setMetric = (key, value, rawText) => {
      const parsed = parseCount(value);
      if (parsed === null || parsed === undefined) return;
      if (result[key] === null || result[key] === undefined) result[key] = parsed;
      if (key === "reactions" && (result.likes === null || result.likes === undefined)) result.likes = parsed;
      if (rawText) result.raw = result.raw || clean(rawText);
    };
    const readMetricText = (text) => {
      const item = clean(text);
      if (!item || item.length > 180) return;
      const patterns = [
        ["views", new RegExp(countToken + "\\\\s*(?:views?|plays?|次播放|播放|浏览)", "i")],
        ["comments", new RegExp(countToken + "\\\\s*(?:comments?|评论)", "i")],
        ["shares", new RegExp(countToken + "\\\\s*(?:shares?|分享)", "i")],
        ["reactions", new RegExp("(?:All reactions|reactions?|likes?|赞)[^0-9]{0,20}" + countToken, "i")],
        ["reactions", new RegExp(countToken + "\\\\s*(?:reactions?|likes?|赞)", "i")],
      ];
      for (const [key, pattern] of patterns) {
        const match = item.match(pattern);
        if (match) setMetric(key, match[1], item);
      }
    };
    const metricNodes = [...root.querySelectorAll('a, span, div, [aria-label], [title]')];
    for (const node of metricNodes) {
      if (node === root) continue;
      if (node !== root) {
        const ownerArticle = node.closest?.('[role="article"], article');
        if (ownerArticle && ownerArticle !== root) continue;
      }
      for (const text of [
        node.getAttribute?.("aria-label") || "",
        node.getAttribute?.("title") || "",
        node.innerText || node.textContent || "",
      ]) {
        readMetricText(text);
      }
    }

    const lines = linesFrom(root.innerText || root.textContent || "");
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      readMetricText(line);
      const slashCluster = line.match(new RegExp("^" + countToken + "\\\\s*[/|]\\\\s*" + countToken + "\\\\s*[/|]\\\\s*" + countToken + "$", "i"));
      if (slashCluster) {
        setMetric("reactions", slashCluster[1], line);
        setMetric("comments", slashCluster[2], line);
        setMetric("shares", slashCluster[3], line);
      }
      const triple = lines.slice(index, index + 3);
      const nextText = lines.slice(index + 3, index + 8).join(" ");
      if (
        triple.length === 3
        && triple.every((item) => new RegExp("^" + countToken + "$", "i").test(item))
        && /\\bLike\\b|\\bComment\\b|\\bShare\\b|赞|评论|分享/i.test(nextText)
      ) {
        setMetric("reactions", triple[0], triple.join("；"));
        setMetric("comments", triple[1], triple.join("；"));
        setMetric("shares", triple[2], triple.join("；"));
      }
    }

    const parts = [];
    if (result.views !== null && result.views !== undefined) parts.push("浏览量：" + result.views);
    if (result.likes !== null && result.likes !== undefined) parts.push("点赞量：" + result.likes);
    if (result.comments !== null && result.comments !== undefined) parts.push("评论数：" + result.comments);
    if (result.shares !== null && result.shares !== undefined) parts.push("分享数：" + result.shares);
    result.detail_engagement_data = parts.join("；");
    result.raw = result.detail_engagement_data || result.raw;
    if (!result.raw) {
      result.confidence = "anchored_missing_metrics";
      result.warnings.push("main_post_metrics_not_found");
    }
    return result;
  })()`;
}

async function extractEngagement(context, target) {
  return await evalPayload(context, detailEngagementBrowserExpression(target));
}

async function expandCommentsAndReplies(context) {
  for (let round = 0; round < Math.max(COMMENT_EXPAND_ROUNDS, REPLY_EXPAND_ROUNDS); round += 1) {
    await evalPayload(context, `(() => {
      const labels = [
        /view more comments/i,
        /see more comments/i,
        /view previous comments/i,
        /more comments/i,
        /view replies/i,
        /see replies/i,
        /\\d+\\s+repl(?:y|ies)/i,
        /view\\s+\\d+\\s+repl(?:y|ies)/i,
        /see\\s+\\d+\\s+repl(?:y|ies)/i,
        /查看更多评论/,
        /查看更多回复/,
        /查看回复/,
      ];
      const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
      for (const el of document.querySelectorAll('div[role="button"], span, a')) {
        const text = clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
        if (!text || !labels.some((re) => re.test(text))) continue;
        try {
          el.click();
        } catch {
          // Ignore click failures on virtualized comment controls.
        }
      }
      return true;
    })()`).catch(() => {});
    await waitSeconds(context, 0.9);
  }
}

function commentModeBrowserExpression(mode) {
  return `(async () => {
    const mode = ${JSON.stringify(mode)};
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const modeLabels = {
      all_comments: [/all comments/i, /所有评论/, /全部评论/],
      newest: [/newest/i, /most recent/i, /最新评论/, /最新/],
      most_relevant: [/most relevant/i, /top comments/i, /最相关/, /热门评论/],
    };
    const sortControlLabels = [
      /most relevant/i,
      /top comments/i,
      /all comments/i,
      /newest/i,
      /comment ranking/i,
      /最相关/,
      /热门评论/,
      /所有评论/,
      /全部评论/,
      /最新评论/,
      /评论排序/,
    ];
    const clickable = () => [...document.querySelectorAll('div[role="button"], span, a, [aria-label]')];
    const labelFor = (el) => clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
    const clickMatching = (patterns) => {
      const el = clickable().find((item) => {
        const text = labelFor(item);
        return text && patterns.some((pattern) => pattern.test(text));
      });
      if (!el) return "";
      try {
        el.click();
        return labelFor(el);
      } catch {
        return "";
      }
    };
    if (mode === "default") return { mode, clicked: false };
    const opened = clickMatching(sortControlLabels);
    if (opened) await sleep(500);
    const selected = clickMatching(modeLabels[mode] || []);
    if (selected) await sleep(700);
    return { mode, clicked: Boolean(selected), opened };
  })()`;
}

async function selectCommentMode(context, mode) {
  return await evalPayload(context, commentModeBrowserExpression(mode)).catch((error) => ({
    mode,
    clicked: false,
    error: String(error),
  }));
}

function leadLinkScanBrowserExpression(accountName = "", mode = "default") {
  return `((expectedAccountName, commentMode) => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const linesFrom = (value) => String(value || "").split(/\\n+/).map(clean).filter(Boolean);
    const isExternalHref = (href) => {
      try {
        const parsed = new URL(href, location.href);
        const host = parsed.hostname.replace(/^www\\./i, "").toLowerCase();
        if (!/^https?:$/i.test(parsed.protocol)) return false;
        if (/l\\.facebook\\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) return true;
        return host !== "facebook.com"
          && !host.endsWith(".facebook.com")
          && host !== "fb.watch"
          && host !== "meta.com"
          && !host.endsWith(".meta.com");
      } catch {
        return false;
      }
    };
    const ownerName = clean(expectedAccountName);
    const ownerNameLower = ownerName.toLowerCase();
    const commentTimeLine = (line) => /^(just now|\\d+\\s*(m|min|h|hr|d|day|w|wk)|刚刚|\\d+\\s*分钟|\\d+\\s*小时|\\d+\\s*天)$/i.test(line);
    const forbiddenChrome = (node) => {
      const shell = node.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header');
      return Boolean(shell);
    };
    const looksLikePageShellOrAd = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\\s*·\\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const ownerMatchedNearTop = (lines) => {
      if (!ownerName) return true;
      return lines.slice(0, 18).some((line) => {
        const lower = line.toLowerCase();
        return lower === ownerNameLower
          || lower.startsWith(ownerNameLower + " replied")
          || lower.includes(ownerNameLower + " replied")
          || lower.startsWith(ownerNameLower + " responded")
          || lower.includes(ownerNameLower + " responded");
      });
    };
    const looksCommentContext = (lines, links) => {
      const shortText = lines.slice(0, 30).join(" ");
      const hasCommentPermalink = links.some((link) => /[?&]comment_id=|comment_id%3D/i.test(link.href));
      return hasCommentPermalink
        || /\\bReply\\b|replied|responded|回复/.test(shortText)
        || lines.some(commentTimeLine);
    };
    const blocks = [...document.querySelectorAll('[role="article"], div[aria-label], li, div')];
    const results = [];
    for (const block of blocks) {
      if (forbiddenChrome(block)) continue;
      const rawText = block.innerText || block.textContent || "";
      const text = clean(rawText);
      if (!text || text.length > 3000 || looksLikePageShellOrAd(text)) continue;
      const lines = linesFrom(rawText);
      const links = [...block.querySelectorAll("a[href]")]
        .map((a) => ({
          href: new URL(a.getAttribute("href"), location.href).href,
          text: clean(a.innerText || a.textContent || a.getAttribute("aria-label") || ""),
        }))
        .filter((link) => isExternalHref(link.href));
      if (!links.length) continue;
      const commentContext = looksCommentContext(lines, links);
      if (!commentContext) continue;
      const ownerMatched = ownerMatchedNearTop(lines);
      if (!ownerMatched) continue;
      const looksReply = /reply|replied|responded|回复/i.test(lines.slice(0, 30).join(" "));
      results.push({
        href: links[0].href,
        text: links[0].text,
        block_text: text.slice(0, 800),
        source: looksReply ? "comment_reply" : "comment",
        owner_matched: ownerMatched,
        comment_context: commentContext,
        comment_mode: commentMode,
      });
    }
    results.sort((a, b) => Number(b.owner_matched) - Number(a.owner_matched));
    return results.slice(0, 20);
  })(${JSON.stringify(accountName)}, ${JSON.stringify(mode)})`;
}

function cleanExternalUrl(href) {
  if (!href) return "";
  try {
    const parsed = new URL(href);
    if (/l\.facebook\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) {
      return cleanExternalUrl(parsed.searchParams.get("u"));
    }
    const host = parsed.hostname.replace(/^www\./i, "").toLowerCase();
    if (!/^https?:$/i.test(parsed.protocol)) return "";
    if (host === "facebook.com" || host.endsWith(".facebook.com") || host === "fb.watch" || host === "meta.com" || host.endsWith(".meta.com")) {
      return "";
    }
    for (const key of [...parsed.searchParams.keys()]) {
      if (key === "fbclid" || key.startsWith("utm_") || key.startsWith("__")) parsed.searchParams.delete(key);
    }
    parsed.hash = "";
    return parsed.href;
  } catch {
    return "";
  }
}

function allowedLandingUrl(href) {
  if (!href) return false;
  if (!ALLOWED_DOMAINS.length) return true;
  try {
    const host = new URL(href).hostname.replace(/^www\./i, "").toLowerCase();
    return ALLOWED_DOMAINS.some((domain) => host === domain || host.endsWith(`.${domain}`));
  } catch {
    return false;
  }
}

function sameNormalizedUrl(left, right) {
  const cleanLeft = cleanExternalUrl(left);
  const cleanRight = cleanExternalUrl(right);
  return Boolean(cleanLeft && cleanRight && cleanLeft === cleanRight);
}

async function resolveLandingUrl(href) {
  const cleaned = cleanExternalUrl(href);
  if (!cleaned) return "";
  const tryFetch = async (method) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), RESOLVE_TIMEOUT_MS);
    const response = await fetch(cleaned, {
      method,
      redirect: "follow",
      signal: controller.signal,
    });
    clearTimeout(timeout);
    return cleanExternalUrl(response.url || cleaned) || cleaned;
  };
  try {
    const resolved = await tryFetch("HEAD");
    if (allowedLandingUrl(resolved)) return resolved;
  } catch {
    // Some story sites block HEAD; fall back to GET redirect handling.
  }
  try {
    const resolved = await tryFetch("GET");
    if (allowedLandingUrl(resolved)) return resolved;
  } catch {
    // Keep cleaned URL only when it already satisfies domain policy.
  }
  return allowedLandingUrl(cleaned) ? cleaned : "";
}

async function extractLeadLink(context, accountName = "") {
  const attempts = [];
  let fallbackSelected = null;
  for (const mode of COMMENT_MODE_SEQUENCE) {
    const modeResult = await selectCommentMode(context, mode);
    await expandCommentsAndReplies(context);
    const candidates = await evalPayload(context, leadLinkScanBrowserExpression(accountName, mode));
    const selected = (candidates || []).find((item) => item.owner_matched) || (candidates || [])[0] || null;
    attempts.push({
      mode,
      mode_result: modeResult,
      candidate_count: (candidates || []).length,
      selected: selected
        ? {
            href: selected.href,
            source: selected.source,
            owner_matched: selected.owner_matched,
            block_text: selected.block_text,
          }
        : null,
    });
    if (!fallbackSelected && selected) fallbackSelected = selected;
    if (!selected) continue;
    const landingUrl = await resolveLandingUrl(selected.href);
    if (landingUrl) {
      return {
        status: "qualified",
        lead_url_raw: selected.href,
        landing_url: landingUrl,
        lead_link_source: selected.source,
        owner_matched: selected.owner_matched,
        comment_excerpt: selected.block_text,
        candidates: candidates || [],
        attempts,
      };
    }
  }
  if (!fallbackSelected) {
    return { status: "missing", candidates: [], attempts };
  }
  return {
    status: "missing",
    lead_url_raw: fallbackSelected.href,
    landing_url: "",
    lead_link_source: fallbackSelected.source,
    owner_matched: fallbackSelected.owner_matched,
    comment_excerpt: fallbackSelected.block_text,
    candidates: [],
    attempts,
  };
}

function hasQualifiedLeadLink(post) {
  return Boolean(
    post.lead_link_status === "qualified"
    && ["comment", "comment_reply"].includes(post.lead_link_source || "")
    && post.lead_url_raw
    && (post.landing_url || post.article_url)
  );
}

async function resolvedExistingLeadLink(post) {
  if (!hasQualifiedLeadLink(post)) return null;
  const resolved = await resolveLandingUrl(post.lead_url_raw);
  const current = post.landing_url || post.article_url || "";
  const landingUrl = resolved || cleanExternalUrl(current);
  if (!landingUrl) return null;
  return {
    status: "qualified",
    lead_url_raw: post.lead_url_raw,
    landing_url: landingUrl,
    lead_link_source: post.lead_link_source,
    owner_matched: true,
    comment_excerpt: post.comment_lead_excerpt || "",
    candidates: [],
    preserved_existing: true,
  };
}

function shouldReplaceLeadLink(post, leadLink) {
  if (!leadLink || leadLink.status !== "qualified") return false;
  if (!hasQualifiedLeadLink(post)) return true;
  return sameNormalizedUrl(post.lead_url_raw, leadLink.lead_url_raw)
    || sameNormalizedUrl(post.landing_url || post.article_url, leadLink.landing_url);
}

function outputStatusFor(post) {
  const requiredOk = Boolean(
    post.post_url
    && post.posted_at
    && post.story_summary
    && post.summary_source === "article"
    && post.lead_link_status === "qualified"
    && ["comment", "comment_reply"].includes(post.lead_link_source || "")
    && (post.landing_url || post.article_url)
  );
  return requiredOk ? "ready_for_output" : "needs_enrichment";
}

function enrichmentReasonCounts(posts) {
  const counts = {};
  const add = (key) => {
    counts[key] = (counts[key] || 0) + 1;
  };
  for (const post of posts || []) {
    if (post.output_status === "ready_for_output") continue;
    if (!post.posted_at || !post.time_confirmed) add("missing_confirmed_posted_at");
    if (post.summary_source !== "article" || !post.story_summary) add("missing_article_summary");
    if (!hasQualifiedLeadLink(post)) add("missing_qualified_comment_lead_link");
    if (post.engagement_confidence && post.engagement_confidence !== "anchored") add("engagement_unconfirmed");
  }
  return counts;
}

function buildCoverageSummary(payload, inputCount) {
  const posts = payload.posts || [];
  const readyForOutput = posts.filter((post) => post.output_status === "ready_for_output").length;
  const needsEnrichment = posts.length - readyForOutput;
  return {
    input_posts: inputCount,
    after_target_date_filter: posts.length,
    date_filtered_out: (payload.date_filtered_out || []).length,
    ready_for_output: readyForOutput,
    needs_enrichment: needsEnrichment,
    reason_counts: enrichmentReasonCounts(posts),
  };
}

async function main() {
  const payload = JSON.parse(fs.readFileSync(INPUT, "utf8"));
  const posts = payload.posts || [];
  const inputPostCount = posts.length;
  const baseContext = loadOpencliContext();

  const enriched = [];
  const errors = [];
  for (const [index, post] of posts.entries()) {
    if (LIMIT && index >= LIMIT) break;
    if (!post.post_url) continue;
    let context = null;
    try {
      context = await openPostTab(baseContext, post.post_url);
      const state = await pageState(context);
      if (state.loggedOut || state.visitorPreview) {
        errors.push({ post_url: post.post_url, error: "human_intervention_required", body_preview: state.bodyPreview });
        continue;
      }
      const target = await findHeaderTime(context);
      let exactTime = await readExactTimeFromDom(context, target);
      if (!exactTime.posted_at) exactTime = await readTooltipTimeWithSyntheticHover(context, target);
      if (!exactTime.posted_at && ALLOW_REAL_MOUSE_HOVER) exactTime = await readTooltipTimeWithRealMouse(context, target);
      const engagement = await extractEngagement(context, target);
      if (exactTime.posted_at) {
        post.posted_at_raw = exactTime.posted_at_raw;
        post.posted_at = exactTime.posted_at;
        post.posted_date = dateKeyFromPostedAt(exactTime.posted_at) || post.posted_date || "";
        post.time_source = exactTime.time_source;
        post.time_confirmed = true;
      }
      if (engagement.raw && engagement.confidence === "anchored") {
        post.engagement_data = engagement.detail_engagement_data || engagement.raw;
        post.detail_engagement_data = engagement.detail_engagement_data || engagement.raw;
        post.engagement_source = engagement.source;
        post.engagement_confidence = engagement.confidence;
        if (engagement.likes !== null && engagement.likes !== undefined) post.likes = engagement.likes;
        if (engagement.reactions !== null && engagement.reactions !== undefined) post.reactions = engagement.reactions;
        if (engagement.comments !== null && engagement.comments !== undefined) post.comments = engagement.comments;
        if (engagement.shares !== null && engagement.shares !== undefined) post.shares = engagement.shares;
        if (engagement.views !== null && engagement.views !== undefined) post.views = engagement.views;
      } else {
        post.engagement_source = engagement.source || "detail_main_post_dom";
        post.engagement_confidence = engagement.confidence || "unconfirmed";
        post.note = appendSemicolonNote(post.note, "互动数据待补采：详情页未能锚定当前主帖互动区");
      }
      let leadLink = await extractLeadLink(context, post.account_name || "");
      if (!shouldReplaceLeadLink(post, leadLink)) {
        const preserved = await resolvedExistingLeadLink(post);
        if (preserved) leadLink = preserved;
      }
      if (leadLink.status === "qualified" && shouldReplaceLeadLink(post, leadLink)) {
        post.lead_url_raw = leadLink.lead_url_raw;
        post.landing_url = leadLink.landing_url;
        post.article_url = leadLink.landing_url;
        post.lead_link_status = "qualified";
        post.lead_link_source = leadLink.lead_link_source;
        post.comment_lead_excerpt = leadLink.comment_excerpt;
      } else if (!post.lead_link_status) {
        post.lead_link_status = "missing";
      }
      if (leadLink.status !== "qualified") {
        post.note = appendSemicolonNote(post.note, "评论区或评论回复引流链接待确认");
      }
      post.output_status = outputStatusFor(post);
      post.crawl_status = post.output_status === "ready_for_output" ? "ready_for_output" : "needs_enrichment";
      enriched.push({ post_url: post.post_url, exact_time: exactTime, engagement, lead_link: leadLink });
    } catch (error) {
      errors.push({ post_url: post.post_url, error: String(error.stack || error) });
    } finally {
      if (context) await closePostTab(context);
    }
  }
  if (TARGET_DATE) {
    const kept = [];
    const dateFilteredOut = [];
    for (const post of posts) {
      const exactDate = post.posted_date || dateKeyFromPostedAt(post.posted_at);
      if (exactDate && exactDate !== TARGET_DATE) {
        dateFilteredOut.push({
          post_url: post.post_url,
          posted_at: post.posted_at || "",
          posted_date: exactDate,
          target_date: TARGET_DATE,
          reason: "outside_target_date_after_detail_enrichment",
        });
      } else {
        kept.push(post);
      }
    }
    payload.posts = kept;
    payload.date_filtered_out = dateFilteredOut;
    payload.input_after_date_filter = kept.length;
  }
  payload.detail_enriched = enriched.length;
  payload.detail_enrichment_errors = errors;
  payload.coverage_summary = buildCoverageSummary(payload, inputPostCount);
  fs.writeFileSync(OUTPUT, JSON.stringify(payload, null, 2), "utf8");
  outputJson({ ok: true, route: "opencli_browser_bridge", enriched: enriched.length, errors: errors.length, output: OUTPUT });
}

if (RUN_MAIN) {
  main().catch((error) => {
    outputJson({ ok: false, route: "opencli_browser_bridge", error: String(error.stack || error) });
    if (globalThis.process) globalThis.process.exitCode = 1;
  });
}

export {
  CURRENT_FILE,
  INVOKED_FILE,
  RUN_MAIN,
  buildCoverageSummary,
  commentModeBrowserExpression,
  dateKeyFromPostedAt,
  detailEngagementBrowserExpression,
  enrichmentReasonCounts,
  leadLinkScanBrowserExpression,
};
