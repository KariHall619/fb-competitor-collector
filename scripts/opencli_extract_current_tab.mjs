#!/usr/bin/env node
/**
 * Live capture now uses OpenCLI Browser Bridge to bind the user's normal Chrome
 * Facebook tab, then evaluates the project-owned DOM extractor in that tab.
 */

import { createRequire } from "node:module";
import {
  ensureFacebookTab,
  evaluateInSession,
  extractArgs,
  loadOpencliContext,
  outputJson,
} from "./opencli_runtime.mjs";

const require = createRequire(import.meta.url);
const { browserExpression } = require("./fb_dom_extractors.js");

const { value } = extractArgs();
const ACCOUNT_URL = value("--account-url", "");
const MAX_TEXT = Number(value("--max-text", "1500"));

async function main() {
  const { opencliCommand, session } = loadOpencliContext();
  const tabResult = await ensureFacebookTab({ opencliCommand, session, accountUrl: ACCOUNT_URL });
  if (!tabResult.ok) {
    outputJson(tabResult);
    return tabResult.exit_code || 1;
  }

  const evalResult = await evaluateInSession({
    opencliCommand,
    session,
    tab: tabResult.tab.page,
    js: browserExpression(MAX_TEXT),
  });
  if (!evalResult.ok) {
    outputJson({
      ok: false,
      status: "opencli_extract_failed",
      route: "opencli_browser_bridge",
      tab: tabResult.tab,
      stdout: evalResult.stdout.trim(),
      stderr: evalResult.stderr.trim(),
    });
    return evalResult.code || 1;
  }

  const extraction = evalResult.payload || {};
  if (extraction.capture_blocked) {
    outputJson({
      ok: false,
      status: extraction.logged_out ? "login_required" : "visitor_preview",
      action_required: "human_intervention_required",
      route: "opencli_browser_bridge",
      message: "当前 Chrome 标签页没有完整登录态或只显示游客预览，已停止采集。请人工在该 Chrome profile 登录 Facebook，并确认页面能连续看到多条帖子后再重试。",
      tab: tabResult.tab,
      body_preview: extraction.body_preview || "",
    });
    return 5;
  }

  const posts = (extraction.candidates || []).filter((candidate) => {
    const text = `${candidate.story_summary || ""} ${candidate.raw_text || ""}`;
    if (!candidate.post_url) return false;
    if (!text || text.length < 40) return false;
    if (/^\s*Honor Reward\s+9\.9 万次赞/i.test(text)) return false;
    return true;
  });

  outputJson({
    ok: posts.length > 0,
    status: posts.length > 0 ? "real_posts_visible" : "no_real_posts_visible",
    route: "opencli_browser_bridge",
    opencli_command: opencliCommand,
    opencli_session: session,
    tab_access_mode: evalResult.tab_access_mode || tabResult.tab_access_mode || "",
    direct_tab: evalResult.direct_tab || 0,
    select_fallback: evalResult.select_fallback || 0,
    tab: tabResult.tab,
    raw_candidate_count: extraction.real_post_count || 0,
    post_count: posts.length,
    posts,
  });
  return posts.length > 0 ? 0 : 5;
}

const exitCode = await main().catch((error) => {
  outputJson({
    ok: false,
    status: "opencli_extract_failed",
    error: String(error.stack || error),
  });
  return 1;
});

globalThis.process.exitCode = exitCode;
