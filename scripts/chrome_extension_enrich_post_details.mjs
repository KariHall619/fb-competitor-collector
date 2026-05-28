#!/usr/bin/env node
/**
 * Enrich prepared posts by opening each post detail page in the user's normal
 * Chrome profile, hovering the post-header relative time, and reading the
 * Facebook tooltip that contains exact minute-level post time.
 */

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const { browserExactTimeHelpersExpression } = require("./fb_time_extractors.js");
const args = process.argv.slice(2);

function argValue(name, fallback = "") {
  const index = args.indexOf(name);
  if (index >= 0 && args[index + 1]) return args[index + 1];
  return fallback;
}

const INPUT = argValue("--input");
const OUTPUT = argValue("--output");
const CONFIG = argValue("--config", "config/settings.yaml");
const LIMIT = Number(argValue("--limit", "0"));
const ALLOW_REAL_MOUSE_HOVER = args.includes("--allow-real-mouse-hover");
const configuredLeadLink = readLeadLinkConfig(CONFIG);
const COMMENT_EXPAND_ROUNDS = Number(argValue("--comment-expand-rounds", configuredLeadLink.commentExpandRounds));
const REPLY_EXPAND_ROUNDS = Number(argValue("--reply-expand-rounds", configuredLeadLink.replyExpandRounds));
const RESOLVE_TIMEOUT_MS = Number(argValue("--resolve-timeout-ms", String(configuredLeadLink.resolveTimeoutSeconds * 1000)));
const ALLOWED_DOMAINS = argValue("--allowed-domains", configuredLeadLink.allowedDomains.join(","))
  .split(",")
  .map((item) => item.trim().replace(/^www\./i, "").toLowerCase())
  .filter(Boolean);

if (!INPUT || !OUTPUT) {
  console.error("Usage: chrome_extension_enrich_post_details.mjs --input prepared.json --output enriched.json [--limit N]");
  process.exitCode = 2;
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pageState(tab) {
  return await tab.playwright.evaluate(() => {
    const body = document.body?.innerText || "";
    return {
      loggedOut: /Log in to Facebook|登录 Facebook|Forgot Account|Forgot password|Create new account|邮箱或手机号\s+密码\s+登录/i.test(body),
      visitorPreview: /(登录|Log in)\s+(忘记账户了？|Forgot Account|Forgot password|Forgotten password)/i.test(body),
      bodyPreview: body.slice(0, 1200),
    };
  }, undefined, { timeoutMs: 10000 });
}

async function findHeaderTime(tab) {
  return await tab.playwright.evaluate(`(() => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
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
  })()`, undefined, { timeoutMs: 10000 });
}

async function readExactTimeFromDom(tab, target) {
  if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
  return await tab.playwright.evaluate(`(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    return helpers.exactTimeFromItem(${JSON.stringify(target)});
  })()`, undefined, { timeoutMs: 10000 });
}

async function readTooltipTimeWithSyntheticHover(tab, target) {
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
  })()`, undefined, { timeoutMs: 10000 });
}

async function readTooltipTimeWithRealMouse(tab, target) {
  if (!target) return "";
  await tab.cua.move({ x: Math.floor(target.x + target.w / 2), y: Math.floor(target.y + target.h / 2) });
  await sleep(1800);
  return await tab.playwright.evaluate(`(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const tooltip = [...document.querySelectorAll('[role="tooltip"], div, span')]
      .map((el) => helpers.clean(el.innerText || el.textContent || ""))
      .find((text) => helpers.parseExactFacebookTime(text));
    if (!tooltip) return { posted_at_raw: "", posted_at: "", time_source: "" };
    return { posted_at_raw: tooltip, posted_at: helpers.parseExactFacebookTime(tooltip), time_source: "real_mouse_tooltip" };
  })()`, undefined, { timeoutMs: 10000 });
}

async function extractEngagement(tab) {
  return await tab.playwright.evaluate(() => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const body = clean(document.body?.innerText || "");
    const text = body.slice(0, 15000);
    const reactionCluster = text.match(/Full story in 1st comment\s+(\d+(?:[.,]\d+)?[KkMm万]?)\s+(\d+(?:[.,]\d+)?[KkMm万]?)\s+(\d+(?:[.,]\d+)?[KkMm万]?)/)
      || text.match(/Full Story\s+(\d+(?:[.,]\d+)?[KkMm万]?)\s+(\d+(?:[.,]\d+)?[KkMm万]?)\s+(\d+(?:[.,]\d+)?[KkMm万]?)/i);
    if (reactionCluster) {
      return {
        raw: reactionCluster.slice(1, 4).join("；"),
        reactions: reactionCluster[1],
        comments: reactionCluster[2],
        shares: reactionCluster[3],
      };
    }
    const line = (text.match(/(?:\d+(?:[.,]\d+)?[KkMm万]?\s*){2,3}(?:comments?|shares?|评论|分享|赞)/i) || [""])[0];
    return { raw: line };
  }, undefined, { timeoutMs: 10000 });
}

async function expandCommentsAndReplies(tab) {
  for (let round = 0; round < Math.max(COMMENT_EXPAND_ROUNDS, REPLY_EXPAND_ROUNDS); round += 1) {
    await tab.playwright.evaluate(() => {
      const labels = [
        /view more comments/i,
        /see more comments/i,
        /view previous comments/i,
        /more comments/i,
        /view replies/i,
        /see replies/i,
        /reply/i,
        /查看更多评论/,
        /查看更多回复/,
        /查看回复/,
      ];
      const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
      for (const el of document.querySelectorAll('div[role="button"], span, a')) {
        const text = clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
        if (!text || !labels.some((re) => re.test(text))) continue;
        try {
          el.click();
        } catch {
          // Ignore click failures on virtualized comment controls.
        }
      }
    }, undefined, { timeoutMs: 10000 }).catch(() => {});
    await sleep(900);
  }
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
    // Some story sites block HEAD; fall back to a small GET request and still
    // rely on fetch redirect handling to determine the final URL.
  }
  try {
    const resolved = await tryFetch("GET");
    if (allowedLandingUrl(resolved)) return resolved;
  } catch {
    // Keep the cleaned URL as a candidate only when it already satisfies domain policy.
  }
  return allowedLandingUrl(cleaned) ? cleaned : "";
}

async function extractLeadLink(tab, accountName = "") {
  await expandCommentsAndReplies(tab);
  const candidates = await tab.playwright.evaluate((expectedAccountName) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const linesFrom = (value) => String(value || "").split(/\n+/).map(clean).filter(Boolean);
    const isExternalHref = (href) => {
      try {
        const parsed = new URL(href, location.href);
        const host = parsed.hostname.replace(/^www\./i, "").toLowerCase();
        if (!/^https?:$/i.test(parsed.protocol)) return false;
        if (/l\.facebook\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) return true;
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
    const commentTimeLine = (line) => /^(just now|\d+\s*(m|min|h|hr|d|day|w|wk)|刚刚|\d+\s*分钟|\d+\s*小时|\d+\s*天)$/i.test(line);
    const forbiddenChrome = (node) => {
      const shell = node.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header');
      return Boolean(shell);
    };
    const looksLikePageShellOrAd = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\s*·\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const ownerMatchedNearTop = (lines) => {
      if (!ownerName) return true;
      return lines.slice(0, 12).some((line) => {
        const lower = line.toLowerCase();
        return lower === ownerNameLower
          || lower.startsWith(`${ownerNameLower} replied`)
          || lower.includes(`${ownerNameLower} replied`);
      });
    };
    const looksCommentContext = (lines, links) => {
      const shortText = lines.slice(0, 24).join(" ");
      const hasCommentPermalink = links.some((link) => /[?&]comment_id=|comment_id%3D/i.test(link.href));
      return hasCommentPermalink
        || /\bReply\b|replied|回复/.test(shortText)
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
      const looksReply = /reply|replied|回复/i.test(lines.slice(0, 24).join(" "));
      results.push({
        href: links[0].href,
        text: links[0].text,
        block_text: text.slice(0, 800),
        source: looksReply ? "comment_reply" : "comment",
        owner_matched: ownerMatched,
        comment_context: commentContext,
      });
    }
    results.sort((a, b) => Number(b.owner_matched) - Number(a.owner_matched));
    return results.slice(0, 20);
  }, accountName, { timeoutMs: 15000 });
  const selected = (candidates || []).find((item) => item.owner_matched && item.comment_context) || null;
  if (!selected) {
    return { status: "missing", candidates: candidates || [] };
  }
  const landingUrl = await resolveLandingUrl(selected.href);
  return {
    status: landingUrl ? "qualified" : "missing",
    lead_url_raw: selected.href,
    landing_url: landingUrl,
    lead_link_source: selected.source,
    owner_matched: selected.owner_matched,
    comment_excerpt: selected.block_text,
    candidates: candidates || [],
  };
}

function outputStatusFor(post) {
  const requiredOk = Boolean(
    post.post_url
    && post.posted_at
    && post.story_summary
    && post.summary_source === "article"
    && post.lead_link_status === "qualified"
    && (post.landing_url || post.article_url)
  );
  return requiredOk ? "ready_for_output" : "needs_enrichment";
}

async function main() {
  const payload = JSON.parse(fs.readFileSync(INPUT, "utf8"));
  const posts = payload.posts || [];
  const { setupBrowserRuntime } = await import("/Users/a1/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/browser-client.mjs");
  await setupBrowserRuntime({ globals: globalThis });
  const browser = await agent.browsers.get("extension");
  await browser.nameSession("FB detail enrichment");

  const enriched = [];
  const errors = [];
  for (const [index, post] of posts.entries()) {
    if (LIMIT && index >= LIMIT) break;
    if (!post.post_url) continue;
    const tab = await browser.tabs.new();
    try {
      await tab.goto(post.post_url);
      await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 20000 }).catch(() => {});
      await sleep(3500);
      const state = await pageState(tab);
      if (state.loggedOut || state.visitorPreview) {
        errors.push({ post_url: post.post_url, error: "human_intervention_required", body_preview: state.bodyPreview });
        continue;
      }
      const target = await findHeaderTime(tab);
      let exactTime = await readExactTimeFromDom(tab, target);
      if (!exactTime.posted_at) exactTime = await readTooltipTimeWithSyntheticHover(tab, target);
      if (!exactTime.posted_at && ALLOW_REAL_MOUSE_HOVER) exactTime = await readTooltipTimeWithRealMouse(tab, target);
      const engagement = await extractEngagement(tab);
      if (exactTime.posted_at) {
        post.posted_at_raw = exactTime.posted_at_raw;
        post.posted_at = exactTime.posted_at;
        post.time_source = exactTime.time_source;
        post.time_confirmed = true;
      }
      if (engagement.raw) {
        post.engagement_data = engagement.raw;
        if (engagement.reactions) post.reactions = engagement.reactions;
        if (engagement.comments) post.comments = engagement.comments;
        if (engagement.shares) post.shares = engagement.shares;
      }
      const leadLink = await extractLeadLink(tab, post.account_name || "");
      if (leadLink.status === "qualified") {
        post.lead_url_raw = leadLink.lead_url_raw;
        post.landing_url = leadLink.landing_url;
        post.article_url = leadLink.landing_url;
        post.lead_link_status = "qualified";
        post.lead_link_source = leadLink.lead_link_source;
        post.comment_lead_excerpt = leadLink.comment_excerpt;
      } else if (!post.lead_link_status) {
        post.lead_link_status = "missing";
      }
      post.output_status = outputStatusFor(post);
      post.crawl_status = post.output_status === "ready_for_output" ? "ready_for_output" : "needs_enrichment";
      enriched.push({ post_url: post.post_url, exact_time: exactTime, engagement, lead_link: leadLink });
    } catch (error) {
      errors.push({ post_url: post.post_url, error: String(error.stack || error) });
    }
  }
  payload.detail_enriched = enriched.length;
  payload.detail_enrichment_errors = errors;
  fs.writeFileSync(OUTPUT, JSON.stringify(payload, null, 2), "utf8");
  console.log(JSON.stringify({ ok: true, enriched: enriched.length, errors: errors.length, output: OUTPUT }, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: String(error.stack || error) }, null, 2));
  process.exitCode = 1;
});
