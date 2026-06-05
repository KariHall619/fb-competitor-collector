#!/usr/bin/env node
/**
 * Discover Facebook account posts from the user's bound Chrome tab through
 * OpenCLI Browser Bridge commands (`browser bind`, `tab list`, `eval --tab`).
 */

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  currentPageMatchesAccount,
  ensureFacebookTab,
  evaluateInSession,
  extractArgs,
  loadOpencliContext,
  outputJson,
  runOpencli,
} from "./opencli_runtime.mjs";

const require = createRequire(import.meta.url);
const { browserExpression } = require("./fb_dom_extractors.js");
const {
  embeddedPublishTimeExpression,
  exactTimeFromTargetExpression,
  headerTimeTargetExpression,
} = require("./fb_detail_extractors.js");

const { value } = extractArgs();
const ACCOUNT_URL = value("--account-url", "");
const MAX_TEXT = Number(value("--max-text", "1500"));
const MAX_SNAPSHOTS = Number(value("--max-snapshots", "48"));
const MIN_SNAPSHOTS = Number(value("--min-snapshots", "10"));
const STABLE_SNAPSHOTS = Number(value("--stable-snapshots", "3"));
const SCROLL_PIXELS = Number(value("--scroll-pixels", "1400"));
const TARGET_DATE = value("--target-date", "");
const POSTED_AFTER = value("--posted-after", "");
const POSTED_BEFORE = value("--posted-before", "");
const CURRENT_FILE = fileURLToPath(import.meta.url);
const INVOKED_FILE = process.argv?.[1] ? path.resolve(process.argv[1]) : "";
const RUN_MAIN = CURRENT_FILE === INVOKED_FILE;

const evalAccessStats = {
  direct_tab: 0,
  select_fallback: 0,
  modes: new Set(),
};
const EVAL_TIMEOUT_MS = Number(process.env.OPENCLI_FB_DISCOVERY_EVAL_TIMEOUT_MS || "45000");
const DETAIL_BOUNDARY_TIMEOUT_MS = Number(process.env.OPENCLI_FB_DISCOVERY_DETAIL_BOUNDARY_TIMEOUT_MS || "10000");
const DETAIL_BOUNDARY_ENABLED = /^(1|true|yes)$/i.test(
  process.env.OPENCLI_FB_DISCOVERY_DETAIL_BOUNDARY || ""
);
const TERMINAL_DETAIL_BOUNDARY_ENABLED = !/^(0|false|no)$/i.test(
  process.env.OPENCLI_FB_DISCOVERY_TERMINAL_DETAIL_BOUNDARY || ""
);

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function dateKeyToDate(value) {
  const text = clean(value);
  const match = text.match(/^(\d{2})(\d{2})(\d{2})$/);
  if (!match) return null;
  const [, yy, mm, dd] = match;
  return new Date(2000 + Number(yy), Number(mm) - 1, Number(dd), 0, 0, 0);
}

function parsePostTime(value) {
  const text = clean(value);
  if (!text) return null;
  let match = text.match(/^(20\d\d)年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$/);
  if (match) {
    const [, year, month, day, hour, minute] = match;
    return new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), 0);
  }
  match = text.match(/^(20\d\d)-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$/);
  if (match) {
    const [, year, month, day, hour, minute] = match;
    return new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), 0);
  }
  return null;
}

function parseRelativePostTime(value, reference = new Date()) {
  const text = clean(value).toLowerCase();
  if (!text) return null;
  if (/^just now$|^刚刚$/.test(text)) return new Date(reference.getTime());
  if (/^yesterday$|^昨天$/.test(text)) return new Date(reference.getTime() - 24 * 60 * 60 * 1000);
  let match = text.match(/^(\d+)\s*(m|min|mins|minute|minutes)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 1000);
  match = text.match(/^(\d+)\s*(h|hr|hrs|hour|hours)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*(d|day|days)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 24 * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*(w|wk|wks|week|weeks)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 7 * 24 * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*分钟$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 1000);
  match = text.match(/^(\d+)\s*小时$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*天$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 24 * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*周$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 7 * 24 * 60 * 60 * 1000);
  return null;
}

function discoveryTimeWindow() {
  const targetDate = dateKeyToDate(TARGET_DATE);
  const postedAfter = parsePostTime(POSTED_AFTER);
  const postedBefore = parsePostTime(POSTED_BEFORE);
  let lower = postedAfter;
  let upper = postedBefore;
  if (targetDate) {
    const nextDate = new Date(targetDate.getTime() + 24 * 60 * 60 * 1000);
    lower = lower && lower > targetDate ? lower : targetDate;
    upper = upper && upper < nextDate ? upper : nextDate;
  }
  return { lower, upper, enabled: Boolean(lower || upper) };
}

function postTimeState(post, window) {
  if (!window?.enabled) return "unknown";
  const timeText = post?.posted_at || post?.posted_at_raw || post?.post_time_text;
  const parsed = parsePostTime(timeText);
  if (!parsed) return "unknown";
  if (window.lower && parsed < window.lower) return "before";
  if (window.upper && parsed >= window.upper) return "after";
  return "inside";
}

function stateFromParsedTime(parsed, window) {
  if (!window?.enabled || !parsed) return "unknown";
  if (window.lower && parsed < window.lower) return "before";
  if (window.upper && parsed >= window.upper) return "after";
  return "inside";
}

function cleanUrl(value) {
  try {
    const parsed = new URL(value);
    parsed.hash = "";
    for (const key of [...parsed.searchParams.keys()]) {
      if (key === "fbclid" || key === "comment_id" || key === "reply_comment_id" || key.startsWith("utm_") || key.startsWith("__")) {
        parsed.searchParams.delete(key);
      }
    }
    return parsed.href;
  } catch {
    return String(value || "");
  }
}

function detailNavigationUrl(post) {
  const photoQueryCandidates = [post?.post_url, post?.raw_fb_url, post?.parent_post_url, post?.canonical_post_url];
  for (const candidate of photoQueryCandidates) {
    const cleaned = cleanUrl(candidate || "");
    if (!cleaned) continue;
    try {
      const parsed = new URL(cleaned);
      const parts = parsed.pathname.split("/").filter(Boolean);
      const isPhotoPath = parts.includes("photo") || parsed.pathname.includes("photo.php");
      const fbid = parsed.searchParams.get("fbid") || (parts.includes("photo") ? parts[parts.indexOf("photo") + 1] : "");
      if (isPhotoPath && fbid) {
        return `${parsed.origin}/photo/?fbid=${encodeURIComponent(fbid)}`;
      }
    } catch {
      // Fall through to the normal candidate order.
    }
  }
  for (const candidate of [post?.canonical_post_url, post?.parent_post_url, post?.post_url, post?.raw_fb_url]) {
    const cleaned = cleanUrl(candidate || "");
    if (cleaned) return cleaned;
  }
  return "";
}

function postKey(post) {
  const url = cleanUrl(post?.post_url || "");
  if (!url) return "";
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.split("/").filter(Boolean);
    const storyFbid = parsed.searchParams.get("story_fbid");
    const photoFbid = parsed.searchParams.get("fbid");
    const id = parsed.searchParams.get("id");
    if (storyFbid && id) return `story:${id}:${storyFbid}`;
    if (parts.includes("posts")) {
      const index = parts.indexOf("posts");
      if (index > 0 && parts[index + 1]) {
        if (index >= 2 && parts[index - 2] === "groups") return `group-post:${parts[index - 1]}:${parts[index + 1]}`;
        return `post:${parts[index - 1]}:${parts[index + 1]}`;
      }
    }
    if (parts.includes("reel")) {
      const index = parts.indexOf("reel");
      if (parts[index + 1]) return `reel:${parts[index + 1]}`;
    }
    if (parts.includes("videos")) {
      const index = parts.indexOf("videos");
      if (parts[index + 1]) return `video:${parts[index + 1]}`;
    }
    if (parts.includes("video")) {
      const index = parts.indexOf("video");
      if (parts[index + 1]) return `video:${parts[index + 1]}`;
    }
    if (parts.includes("watch") && parsed.searchParams.get("v")) return `video:${parsed.searchParams.get("v")}`;
    if ((parsed.pathname.includes("photo.php") || parts.join("/") === "photo") && photoFbid) return `photo:${photoFbid}`;
    if (parts.includes("photos")) {
      const index = parts.indexOf("photos");
      const tail = parts.slice(index + 1).filter((part) => !["a", "p", "photo"].includes(part));
      const numericTail = tail.filter((part) => /^\d{6,}$/.test(part));
      const photoId = numericTail.at(-1) || tail.at(-1);
      if (photoId) return `photo:${photoId}`;
    }
    if (parts.includes("share")) {
      const index = parts.indexOf("share");
      if (parts[index + 1]) return `share:${parts.slice(index + 1).join(":")}`;
    }
    if (parsed.hostname === "fb.watch" && parts[0]) return `fb-watch:${parts[0]}`;
    return url;
  } catch {
    return url;
  }
}

function validCandidate(candidate) {
  const text = `${candidate.story_summary || ""} ${candidate.raw_text || ""}`;
  if (!candidate.post_url) return false;
  if (candidate.source_split === "media_fallback" && candidate.selected_post_link_kind === "media") return true;
  if (!text || text.length < 25) return false;
  if (/^\s*Honor Reward\s+9\.9 万次赞/i.test(text)) return false;
  return true;
}

async function waitSeconds(opencliCommand, session, tab, seconds) {
  void opencliCommand;
  void session;
  void tab;
  const scale = Number(process.env.OPENCLI_FB_DISCOVERY_WAIT_SCALE ?? "1");
  const waitMs = Math.max(0, Number(seconds) || 0) * Math.max(0, Number.isFinite(scale) ? scale : 1) * 1000;
  await new Promise((resolve) => setTimeout(resolve, waitMs));
}

async function evalPage(opencliCommand, session, tab, js) {
  const result = await evaluateInSession({ opencliCommand, session, tab, js, timeoutMs: EVAL_TIMEOUT_MS });
  if (!result.ok) {
    throw new Error(result.stderr || result.stdout || "OpenCLI eval failed");
  }
  if (result.tab_access_mode) evalAccessStats.modes.add(result.tab_access_mode);
  evalAccessStats.direct_tab += Number(result.direct_tab || 0);
  evalAccessStats.select_fallback += Number(result.select_fallback || 0);
  return result.payload || {};
}

async function scrollToTop(opencliCommand, session, tab) {
  await evalPage(opencliCommand, session, tab, `(() => {
    const closeBlockingDialogs = () => {
      for (const node of [...document.querySelectorAll('[role="dialog"], [aria-label="Messenger"], [aria-label="Chats"]')]) {
        const text = String(node.innerText || node.textContent || '');
        if (!/Messenger|Chats|Chat history|PIN|New message|聊天|消息/i.test(text)) continue;
        const closeButton = node.querySelector('[aria-label="Close"], [aria-label="关闭"], [aria-label*="Close chat"]');
        closeButton?.click?.();
        node.style.setProperty('display', 'none', 'important');
        node.style.setProperty('visibility', 'hidden', 'important');
      }
    };
    closeBlockingDialogs();
    const candidates = [
      ...document.querySelectorAll('[role="main"], [data-pagelet*="ProfileTimeline"], [aria-label*="Timeline"], [aria-label*="Posts"]')
    ];
    for (const el of candidates) {
      if (!el || el.scrollHeight <= el.clientHeight + 80) continue;
      el.scrollTop = 0;
    }
    window.scrollTo(0, 0);
    return { y: window.scrollY || 0 };
  })()`);
  await waitSeconds(opencliCommand, session, tab, 1.2);
}

async function readCurrentPageIdentity(opencliCommand, session, tab) {
  const payload = await evalPage(opencliCommand, session, tab, `(() => ({
    url: location.href,
    title: document.title,
    body_length: document.body?.innerText?.length || 0,
  }))()`);
  return {
    url: payload?.url || "",
    title: payload?.title || "",
    body_length: Number(payload?.body_length || 0),
  };
}

async function scrollDown(opencliCommand, session, tab, pixels) {
  return await evalPage(opencliCommand, session, tab, `(() => {
    const requested = ${Number(pixels) || 1400};
    const before = window.scrollY || document.documentElement.scrollTop || 0;
    window.scrollBy(0, requested);
    const after = window.scrollY || document.documentElement.scrollTop || 0;
    const pageScroller = document.scrollingElement || document.documentElement;
    return {
      before,
      after,
      moved: Math.abs(after - before),
      target: 'window',
      body_length: document.body?.innerText?.length || 0,
      scroll_height: pageScroller?.scrollHeight || document.documentElement?.scrollHeight || document.body?.scrollHeight || 0,
    };
  })()`);
}

async function openUrlInTab(opencliCommand, session, tab, url) {
  const result = await runOpencli(["browser", session, "open", url, "--tab", tab], {
    command: opencliCommand,
    timeoutMs: DETAIL_BOUNDARY_TIMEOUT_MS,
  });
  if (!result.ok) return { ok: false, error: result.stderr || result.stdout || "open url failed" };
  return { ok: true };
}

async function confirmDetailTimeState(opencliCommand, session, tab, post, timeWindow) {
  if (!timeWindow?.enabled) return { state: "unknown", skipped: true };
  const existingState = postTimeState(post, timeWindow);
  if (existingState !== "unknown") return { state: existingState, source: "homepage" };
  const url = detailNavigationUrl(post);
  if (!url) return { state: "unknown", error: "missing_detail_url" };
  const opened = await openUrlInTab(opencliCommand, session, tab, url);
  if (!opened.ok) return { state: "unknown", error: opened.error || "open_detail_failed" };
  await waitSeconds(opencliCommand, session, tab, 1.2);
  const target = await evalPage(opencliCommand, session, tab, headerTimeTargetExpression(url)).catch(() => null);
  let exact = target ? await evalPage(opencliCommand, session, tab, exactTimeFromTargetExpression(target)).catch(() => null) : null;
  const embedded = await evalPage(opencliCommand, session, tab, embeddedPublishTimeExpression(url)).catch(() => null);
  if (embedded?.posted_at && (!exact?.posted_at || exact.time_source === "synthetic_hover_tooltip")) {
    exact = embedded;
  }
  const parsed = parsePostTime(exact?.posted_at || "");
  return {
    state: stateFromParsedTime(parsed, timeWindow),
    posted_at: exact?.posted_at || "",
    time_source: exact?.time_source || "",
    detail_url: url,
  };
}

async function confirmWindowBoundaryFromDetails(opencliCommand, session, tab, posts, timeWindow) {
  if (!timeWindow?.enabled || !posts.length) {
    return { checked: 0, before: 0, inside: 0, after: 0, unknown: 0, complete: false, details: [] };
  }
  const tail = posts
    .filter((post) => postTimeState(post, timeWindow) === "unknown")
    .slice(-3);
  const details = [];
  let before = 0;
  let inside = 0;
  let after = 0;
  let unknown = 0;
  for (const post of tail) {
    const result = await confirmDetailTimeState(opencliCommand, session, tab, post, timeWindow);
    details.push({
      post_url: post.post_url || "",
      state: result.state || "unknown",
      posted_at: result.posted_at || "",
      time_source: result.time_source || "",
      error: result.error || "",
    });
    if (result.state === "before") before += 1;
    else if (result.state === "inside") inside += 1;
    else if (result.state === "after") after += 1;
    else unknown += 1;
  }
  return {
    checked: details.length,
    before,
    inside,
    after,
    unknown,
    complete: before >= 1 && inside === 0 && after === 0,
    details,
  };
}

async function findWindowCutoffFromDetails(opencliCommand, session, tab, posts, timeWindow, maxChecks = 8) {
  if (!timeWindow?.enabled || !posts.length) {
    return { checked: 0, cutoff_index: -1, before: 0, inside: 0, after: 0, unknown: 0, complete: false, details: [] };
  }
  let low = 0;
  let high = posts.length - 1;
  let cutoffIndex = -1;
  const details = [];
  let before = 0;
  let inside = 0;
  let after = 0;
  let unknown = 0;
  let checks = 0;
  while (low <= high && checks < Math.max(1, Number(maxChecks) || 1)) {
    const index = Math.floor((low + high) / 2);
    const post = posts[index];
    const result = await confirmDetailTimeState(opencliCommand, session, tab, post, timeWindow);
    checks += 1;
    details.push({
      index,
      post_url: post?.post_url || "",
      state: result.state || "unknown",
      posted_at: result.posted_at || "",
      time_source: result.time_source || "",
      error: result.error || "",
    });
    if (result.posted_at && !post.posted_at) {
      post.posted_at = result.posted_at;
      post.posted_at_raw = result.posted_at;
      post.time_source = result.time_source || "detail_boundary";
      post.time_confirmed = true;
    }
    if (result.state === "before") {
      before += 1;
      cutoffIndex = cutoffIndex < 0 ? index : Math.min(cutoffIndex, index);
      high = index - 1;
    } else if (result.state === "inside") {
      inside += 1;
      low = index + 1;
    } else if (result.state === "after") {
      after += 1;
      low = index + 1;
    } else {
      unknown += 1;
      low = index + 1;
    }
  }
  return {
    checked: checks,
    cutoff_index: cutoffIndex,
    before,
    inside,
    after,
    unknown,
    complete: cutoffIndex >= 0,
    details,
  };
}

function captureCoverageState({ blockedExtraction = null, snapshots = [], stopReason = "max_snapshots", maxSnapshots = 32 }) {
  const lastSnapshot = snapshots.at(-1) || {};
  const hitSnapshotCap =
    !blockedExtraction && snapshots.length >= Math.max(1, Number(maxSnapshots) || 1) && stopReason === "max_snapshots";
  const coverageIncomplete = hitSnapshotCap && Number(lastSnapshot.new_posts || 0) > 0;
  return {
    coverage_blocked: false,
    coverage_incomplete: coverageIncomplete,
    capture_complete: !coverageIncomplete,
  };
}

async function captureSnapshots({ opencliCommand, session, tab, maxText }) {
  const timeWindow = discoveryTimeWindow();
  const seen = new Map();
  const snapshots = [];
  let stable = 0;
  let blockedExtraction = null;
  let stopReason = "max_snapshots";
  let previousSeenCount = 0;
  let noMovementCount = 0;
  let previousScrollHeight = 0;
  let oldPostWindowCount = 0;
  let detailWindowBoundaryCount = 0;

  const readSnapshot = async (index, label) => {
    const pageIdentity = await readCurrentPageIdentity(opencliCommand, session, tab);
    if (!currentPageMatchesAccount(pageIdentity.url, pageIdentity.title, ACCOUNT_URL)) {
      blockedExtraction = {
        status: "facebook_tab_wrong_account",
        wrong_account: true,
        url: pageIdentity.url,
        title: pageIdentity.title,
        body_length: pageIdentity.body_length,
      };
      stopReason = "wrong_account";
      snapshots.push({
        index,
        label,
        blocked: true,
        wrong_account: true,
        url: pageIdentity.url,
        title: pageIdentity.title,
        body_length: pageIdentity.body_length,
        raw_candidate_count: 0,
        new_posts: 0,
        seen_posts: seen.size,
      });
      return false;
    }

    const extraction = await evalPage(opencliCommand, session, tab, browserExpression(maxText)).catch((error) => ({
      capture_blocked: true,
      eval_failed: true,
      error: String(error?.message || error),
      body_length: 0,
      real_post_count: 0,
    }));
    if (extraction.capture_blocked) {
      blockedExtraction = extraction;
      stopReason = extraction.eval_failed ? "opencli_eval_failed" : extraction.logged_out ? "login_required" : "visitor_preview";
      snapshots.push({
        index,
        label,
        blocked: true,
        eval_failed: Boolean(extraction.eval_failed),
        error: extraction.error || "",
        body_length: extraction.body_length || 0,
        raw_candidate_count: extraction.real_post_count || 0,
        new_posts: 0,
        seen_posts: seen.size,
      });
      return false;
    }

    let newPosts = 0;
    let oldWindowPosts = 0;
    let insideWindowPosts = 0;
    const visibleCandidates = [];
    for (const candidate of extraction.candidates || []) {
      if (!validCandidate(candidate)) continue;
      const timeState = postTimeState(candidate, timeWindow);
      if (timeState === "before") oldWindowPosts += 1;
      if (timeState === "inside") insideWindowPosts += 1;
      visibleCandidates.push({
        post_url: candidate.post_url || "",
        post_time_text: candidate.post_time_text || "",
        posted_at: candidate.posted_at || "",
        time_state: timeState,
        first_line: candidate.first_line || "",
      });
      const key = postKey(candidate);
      if (!key || seen.has(key)) continue;
      seen.set(key, candidate);
      newPosts += 1;
    }
    snapshots.push({
      index,
      label,
      body_length: extraction.body_length || 0,
      article_count: extraction.article_count || 0,
      raw_candidate_count: extraction.real_post_count || 0,
      new_posts: newPosts,
      seen_posts: seen.size,
      old_window_posts: oldWindowPosts,
      inside_window_posts: insideWindowPosts,
      time_window_enabled: timeWindow.enabled,
      visible_time_texts: (extraction.candidates || [])
        .flatMap((candidate) => candidate.time_texts || [candidate.post_time_text || ""])
        .filter(Boolean)
        .slice(0, 20),
      visible_candidates: visibleCandidates.slice(0, 12),
    });
    return true;
  };

  await scrollToTop(opencliCommand, session, tab);
  for (let index = 0; index < Math.max(1, MAX_SNAPSHOTS); index += 1) {
    if (!(await readSnapshot(index, "from_top"))) break;
    const current = snapshots.at(-1) || {};
    stable = seen.size === previousSeenCount ? stable + 1 : 0;
    previousSeenCount = seen.size;
    oldPostWindowCount = timeWindow.enabled && Number(current.old_window_posts || 0) > 0 && Number(current.inside_window_posts || 0) === 0
      ? oldPostWindowCount + 1
      : 0;
    if (oldPostWindowCount >= 2) {
      stopReason = "older_than_time_window";
      break;
    }
    if (
      timeWindow.enabled
      && DETAIL_BOUNDARY_ENABLED
      && index + 1 >= Math.max(4, MIN_SNAPSHOTS)
      && Number(current.inside_window_posts || 0) === 0
      && Number(current.old_window_posts || 0) === 0
      && Number(current.new_posts || 0) > 0
    ) {
      const recentPosts = [...seen.values()].slice(-Number(current.new_posts || 0));
      const detailBoundary = await confirmWindowBoundaryFromDetails(opencliCommand, session, tab, recentPosts, timeWindow);
      await openUrlInTab(opencliCommand, session, tab, ACCOUNT_URL);
      await waitSeconds(opencliCommand, session, tab, 0.8);
      snapshots[snapshots.length - 1].detail_time_boundary = detailBoundary;
      detailWindowBoundaryCount = detailBoundary.complete ? detailWindowBoundaryCount + 1 : 0;
      if (detailWindowBoundaryCount >= 1) {
        stopReason = "detail_time_older_than_time_window";
        break;
      }
    } else {
      detailWindowBoundaryCount = 0;
    }
    if (snapshots.filter((item) => item.label === "from_top").length >= Math.max(1, MIN_SNAPSHOTS) && stable >= STABLE_SNAPSHOTS && noMovementCount >= 1) {
      stopReason = "stable_no_new_posts";
      break;
    }
    const scrollState = await scrollDown(opencliCommand, session, tab, SCROLL_PIXELS);
    snapshots[snapshots.length - 1].scroll = scrollState;
    const scrollMoved = Number(scrollState?.moved || 0);
    const scrollHeight = Number(scrollState?.scroll_height || 0);
    noMovementCount = scrollMoved < 50 && scrollHeight <= previousScrollHeight ? noMovementCount + 1 : 0;
    previousScrollHeight = Math.max(previousScrollHeight, scrollHeight);
    await waitSeconds(opencliCommand, session, tab, 1.4);
  }

  if (
    TERMINAL_DETAIL_BOUNDARY_ENABLED
    && timeWindow.enabled
    && stopReason === "max_snapshots"
    && seen.size > 0
  ) {
    const posts = [...seen.values()];
    const cutoff = await findWindowCutoffFromDetails(opencliCommand, session, tab, posts, timeWindow);
    await openUrlInTab(opencliCommand, session, tab, ACCOUNT_URL);
    await waitSeconds(opencliCommand, session, tab, 0.8);
    snapshots[snapshots.length - 1].terminal_detail_time_boundary = cutoff;
    if (cutoff.complete && cutoff.cutoff_index >= 0) {
      const keptPosts = posts.slice(0, cutoff.cutoff_index);
      const droppedCount = Math.max(0, posts.length - keptPosts.length);
      seen.clear();
      for (const post of keptPosts) {
        const key = postKey(post);
        if (key) seen.set(key, post);
      }
      snapshots[snapshots.length - 1].terminal_detail_time_boundary.dropped_candidate_count = droppedCount;
      snapshots[snapshots.length - 1].terminal_detail_time_boundary.kept_candidate_count = seen.size;
      stopReason = "detail_time_older_than_time_window";
      detailWindowBoundaryCount += 1;
    }
  }

  return {
    blockedExtraction,
    snapshots,
    posts: [...seen.values()],
    stable_snapshot_count: stable,
    old_post_window_snapshot_count: oldPostWindowCount,
    detail_window_boundary_count: detailWindowBoundaryCount,
    no_movement_snapshot_count: noMovementCount,
    stopReason,
  };
}

async function main() {
  process.env.FB_EXTRACT_MAX_SNAPSHOTS = String(MAX_SNAPSHOTS);
  process.env.FB_EXTRACT_MIN_SNAPSHOTS = String(MIN_SNAPSHOTS);
  const { opencliCommand, session } = loadOpencliContext();
  const tabResult = await ensureFacebookTab({ opencliCommand, session, accountUrl: ACCOUNT_URL });
  if (!tabResult.ok) {
    outputJson(tabResult);
    return tabResult.exit_code || 1;
  }

  let capture;
  try {
    capture = await captureSnapshots({
      opencliCommand,
      session,
      tab: tabResult.tab.page,
      maxText: MAX_TEXT,
    });
  } catch (error) {
    outputJson({
      ok: false,
      status: "opencli_extract_failed",
      route: "opencli_browser_bridge",
      tab: tabResult.tab,
      error: String(error.stack || error),
    });
    return 1;
  }

  if (capture.blockedExtraction) {
    const extraction = capture.blockedExtraction;
    if (extraction.wrong_account) {
      outputJson({
        ok: false,
        status: "facebook_tab_wrong_account",
        action_required: "human_intervention_required",
        human_intervention_required: true,
        blocked_reason: "facebook_tab_wrong_account",
        route: "opencli_browser_bridge",
        message: "当前 OpenCLI 绑定标签页不属于目标 Facebook 账号，已停止采集以避免串账号数据。",
        expected_account_url: ACCOUNT_URL,
        actual_url: extraction.url || "",
        actual_title: extraction.title || "",
        tab: tabResult.tab,
        snapshots: capture.snapshots,
      });
      return 5;
    }
    outputJson({
      ok: false,
      status: extraction.logged_out ? "login_required" : "visitor_preview",
      action_required: "human_intervention_required",
      human_intervention_required: true,
      blocked_reason: extraction.logged_out ? "login_required" : "visitor_preview",
      route: "opencli_browser_bridge",
      message: "当前 Chrome/Facebook session 没有完整登录态或只显示游客预览，已停止采集。",
      tab: tabResult.tab,
      body_preview: extraction.body_preview || "",
      snapshots: capture.snapshots,
    });
    return 5;
  }

  const posts = capture.posts || [];
  const coverage = captureCoverageState({
    blockedExtraction: capture.blockedExtraction,
    snapshots: capture.snapshots,
    stopReason: capture.stopReason,
    maxSnapshots: MAX_SNAPSHOTS,
  });

  outputJson({
    ok: posts.length > 0,
    status: posts.length > 0 ? "real_posts_visible" : "no_real_posts_visible",
    route: "opencli_browser_bridge",
    opencli_command: opencliCommand,
    opencli_session: session,
    tab_access_mode: [...evalAccessStats.modes].at(-1) || tabResult.tab_access_mode || "",
    direct_tab: evalAccessStats.direct_tab,
    select_fallback: evalAccessStats.select_fallback,
    tab: tabResult.tab,
    raw_candidate_count: Math.max(0, ...capture.snapshots.map((item) => item.raw_candidate_count || 0)),
    post_count: posts.length,
    capture_complete: coverage.capture_complete,
    coverage: {
      snapshot_count: capture.snapshots.length,
      stop_reason: capture.stopReason,
      stable_snapshot_count: capture.stable_snapshot_count,
      old_post_window_snapshot_count: capture.old_post_window_snapshot_count,
      detail_window_boundary_count: capture.detail_window_boundary_count,
      no_movement_snapshot_count: capture.no_movement_snapshot_count,
      coverage_blocked: coverage.coverage_blocked,
      coverage_incomplete: coverage.coverage_incomplete,
      capture_complete: coverage.capture_complete,
      message: coverage.coverage_incomplete
        ? "已达到最大滚动快照数但最后一屏仍有新增候选；可能还有更早帖子未覆盖，请提高 --max-snapshots 或继续从页面顶部重试。"
        : "",
    },
    snapshots: capture.snapshots,
    posts,
  });
  return posts.length > 0 ? 0 : 5;
}

if (RUN_MAIN) {
  const exitCode = await main().catch((error) => {
    outputJson({
      ok: false,
      status: "opencli_extract_failed",
      error: String(error.stack || error),
    });
    return 1;
  });
  globalThis.process.exitCode = exitCode;
}

export {
  CURRENT_FILE,
  INVOKED_FILE,
  RUN_MAIN,
  captureSnapshots,
  cleanUrl,
  postKey,
  validCandidate,
};
