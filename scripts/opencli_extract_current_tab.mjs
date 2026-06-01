#!/usr/bin/env node
/**
 * Live capture now uses OpenCLI Browser Bridge to bind the user's normal Chrome
 * Facebook tab, then evaluates the project-owned DOM extractor in that tab.
 */

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ensureFacebookTab,
  evaluateInSession,
  extractArgs,
  loadOpencliContext,
  outputJson,
  runOpencli,
} from "./opencli_runtime.mjs";

const require = createRequire(import.meta.url);
const { browserExpression } = require("./fb_dom_extractors.js");

const { value } = extractArgs();
const ACCOUNT_URL = value("--account-url", "");
const MAX_TEXT = Number(value("--max-text", "1500"));
const MAX_SNAPSHOTS = Number(value("--max-snapshots", "10"));
const STABLE_SNAPSHOTS = Number(value("--stable-snapshots", "3"));
const SCROLL_PIXELS = Number(value("--scroll-pixels", "1400"));
const CURRENT_FILE = fileURLToPath(import.meta.url);
const INVOKED_FILE = process.argv?.[1] ? path.resolve(process.argv[1]) : "";
const RUN_MAIN = CURRENT_FILE === INVOKED_FILE;

function cleanUrl(value) {
  try {
    const parsed = new URL(value);
    parsed.hash = "";
    for (const key of [...parsed.searchParams.keys()]) {
      if (key === "fbclid" || key.startsWith("utm_") || key.startsWith("__")) parsed.searchParams.delete(key);
    }
    return parsed.href;
  } catch {
    return String(value || "");
  }
}

function postKey(post) {
  const url = cleanUrl(post?.post_url || "");
  if (!url) return "";
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.split("/").filter(Boolean);
    const storyFbid = parsed.searchParams.get("story_fbid") || parsed.searchParams.get("fbid");
    const id = parsed.searchParams.get("id");
    if (storyFbid && id) return `story:${id}:${storyFbid}`;
    if (parts.includes("posts")) {
      const index = parts.indexOf("posts");
      if (index > 0 && parts[index + 1]) return `post:${parts[index - 1]}:${parts[index + 1]}`;
    }
    if (parts.includes("reel")) {
      const index = parts.indexOf("reel");
      if (parts[index + 1]) return `reel:${parts[index + 1]}`;
    }
    return url;
  } catch {
    return url;
  }
}

function validCandidate(candidate) {
  const text = `${candidate.story_summary || ""} ${candidate.raw_text || ""}`;
  if (!candidate.post_url) return false;
  if (!text || text.length < 40) return false;
  if (/^\s*Honor Reward\s+9\.9 万次赞/i.test(text)) return false;
  return true;
}

async function waitSeconds(opencliCommand, session, tab, seconds) {
  await runOpencli(["browser", session, "wait", "time", String(seconds), "--tab", tab], { command: opencliCommand });
}

async function evalPage(opencliCommand, session, tab, js) {
  const result = await evaluateInSession({ opencliCommand, session, tab, js });
  if (!result.ok) {
    throw new Error(result.stderr || result.stdout || "OpenCLI eval failed");
  }
  return result.payload || {};
}

async function scrollToTop(opencliCommand, session, tab) {
  await evalPage(opencliCommand, session, tab, "(() => { window.scrollTo(0, 0); return { y: window.scrollY || 0 }; })()");
  await waitSeconds(opencliCommand, session, tab, 1.2);
}

async function scrollDown(opencliCommand, session, tab, pixels) {
  return await evalPage(opencliCommand, session, tab, `(() => {
    const before = window.scrollY || document.documentElement.scrollTop || 0;
    window.scrollBy(0, ${Number(pixels) || 1400});
    const after = window.scrollY || document.documentElement.scrollTop || 0;
    return {
      before,
      after,
      body_length: document.body?.innerText?.length || 0,
      scroll_height: document.documentElement?.scrollHeight || document.body?.scrollHeight || 0,
    };
  })()`);
}

async function captureSnapshots({ opencliCommand, session, tab, maxText }) {
  await scrollToTop(opencliCommand, session, tab);
  const seen = new Map();
  const snapshots = [];
  let stableCount = 0;
  let blockedExtraction = null;
  let previousSeenCount = 0;
  for (let index = 0; index < Math.max(1, MAX_SNAPSHOTS); index += 1) {
    const extraction = await evalPage(opencliCommand, session, tab, browserExpression(maxText));
    if (extraction.capture_blocked) {
      blockedExtraction = extraction;
      snapshots.push({
        index,
        blocked: true,
        body_length: extraction.body_length || 0,
        raw_candidate_count: extraction.real_post_count || 0,
        new_posts: 0,
        seen_posts: seen.size,
      });
      break;
    }
    let newPosts = 0;
    for (const candidate of extraction.candidates || []) {
      if (!validCandidate(candidate)) continue;
      const key = postKey(candidate);
      if (!key || seen.has(key)) continue;
      seen.set(key, candidate);
      newPosts += 1;
    }
    const bodyLength = extraction.body_length || 0;
    snapshots.push({
      index,
      body_length: bodyLength,
      article_count: extraction.article_count || 0,
      raw_candidate_count: extraction.real_post_count || 0,
      new_posts: newPosts,
      seen_posts: seen.size,
      visible_time_texts: (extraction.candidates || [])
        .flatMap((candidate) => candidate.time_texts || [candidate.post_time_text || ""])
        .filter(Boolean)
        .slice(0, 20),
    });
    stableCount = seen.size === previousSeenCount ? stableCount + 1 : 0;
    previousSeenCount = seen.size;
    if (stableCount >= Math.max(1, STABLE_SNAPSHOTS)) break;
    const scrollState = await scrollDown(opencliCommand, session, tab, SCROLL_PIXELS);
    snapshots[snapshots.length - 1].scroll = scrollState;
    await waitSeconds(opencliCommand, session, tab, 1.4);
  }
  const status = blockedExtraction
    ? (blockedExtraction.logged_out ? "login_required" : "visitor_preview")
    : seen.size > 0
      ? "real_posts_visible"
      : "no_real_posts_visible";
  return {
    status,
    blockedExtraction,
    snapshots,
    posts: [...seen.values()],
    stable_snapshot_count: stableCount,
    coverage_blocked: !blockedExtraction && seen.size > 0 && snapshots.length > 1 && snapshots.at(-1)?.new_posts === 0,
  };
}

async function main() {
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
    outputJson({
      ok: false,
      status: extraction.logged_out ? "login_required" : "visitor_preview",
      action_required: "human_intervention_required",
      route: "opencli_browser_bridge",
      message: "当前 Chrome 标签页没有完整登录态或只显示游客预览，已停止采集。请人工在该 Chrome profile 登录 Facebook，并确认页面能连续看到多条帖子后再重试。",
      tab: tabResult.tab,
      body_preview: extraction.body_preview || "",
      snapshots: capture.snapshots,
    });
    return 5;
  }

  const posts = capture.posts;

  outputJson({
    ok: posts.length > 0,
    status: capture.status,
    route: "opencli_browser_bridge",
    opencli_command: opencliCommand,
    opencli_session: session,
    tab: tabResult.tab,
    raw_candidate_count: Math.max(0, ...capture.snapshots.map((item) => item.raw_candidate_count || 0)),
    post_count: posts.length,
    coverage: {
      snapshot_count: capture.snapshots.length,
      stable_snapshot_count: capture.stable_snapshot_count,
      coverage_blocked: capture.coverage_blocked,
      message: capture.coverage_blocked
        ? "连续滚动后未发现新增候选；如果人工仍能看到更多目标窗口帖子，请从页面顶部重试或检查 Facebook 虚拟列表是否未加载。"
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
