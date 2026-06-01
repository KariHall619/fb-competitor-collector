#!/usr/bin/env node
/**
 * Extract Facebook post candidates from the user's normal Chrome profile.
 *
 * This script records the live-capture logic used by Codex Chrome Extension:
 * read already-open user Chrome tabs, claim the matching Facebook tab, and
 * evaluate the shared DOM extractor there.
 */

import { createRequire } from "node:module";
import { setupBrowserRuntime } from "/Users/a1/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/browser-client.mjs";

const require = createRequire(import.meta.url);
const { browserExpression } = require("./fb_dom_extractors.js");

const args = process.argv.slice(2);

function argValue(name, fallback = "") {
  const index = args.indexOf(name);
  if (index >= 0 && args[index + 1]) return args[index + 1];
  return fallback;
}

const ACCOUNT_URL = argValue("--account-url", "");
const MAX_TEXT = Number(argValue("--max-text", "1500"));

function matchesAccount(tab, accountUrl) {
  if (!accountUrl) return true;
  try {
    const target = new URL(accountUrl);
    const current = new URL(tab.url || "");
    const targetId = target.searchParams.get("id");
    if (targetId && `${current.href} ${tab.title || ""}`.includes(targetId)) return true;
    const parts = target.pathname.split("/").filter(Boolean).filter((part) => !["people", "profile.php", "posts", "reels"].includes(part));
    return parts.length === 0 || parts.some((part) => `${current.href} ${tab.title || ""}`.includes(part));
  } catch {
    return true;
  }
}

async function main() {
  await setupBrowserRuntime({ globals: globalThis });
  const browser = await agent.browsers.get("extension");
  await browser.nameSession("FB current tab extract");
  const tabs = await browser.user.openTabs();
  const facebookTabs = tabs.filter((tab) => /^https?:\/\/([^/]+\.)?facebook\.com\//i.test(tab.url || ""));
  const tabInfo = facebookTabs.find((tab) => matchesAccount(tab, ACCOUNT_URL)) || facebookTabs[0];
  if (!tabInfo) {
    console.log(JSON.stringify({
      ok: false,
      status: "facebook_tab_missing",
      message: "未发现已打开的 Facebook 标签页。请先在正常 Chrome 中打开业务人员肉眼可见帖子列表的 Facebook 页面。",
      open_tab_count: tabs.length,
    }, null, 2));
    return 5;
  }
  const tab = await browser.user.claimTab(tabInfo);
  const extraction = await tab.playwright.evaluate(browserExpression(MAX_TEXT), undefined, { timeoutMs: 15000 });
  if (extraction.capture_blocked) {
    console.log(JSON.stringify({
      ok: false,
      status: extraction.logged_out ? "login_required" : "visitor_preview",
      action_required: "human_intervention_required",
      route: "codex_chrome_extension",
      message: "当前 Chrome 标签页没有完整登录态或只显示游客预览，已停止采集。请人工在该 Chrome profile 登录 Facebook，并确认页面能连续看到多条帖子后再重试。",
      tab: {
        title: await tab.title(),
        url: await tab.url(),
        claimed_from: tabInfo.url,
      },
      body_preview: extraction.body_preview || "",
    }, null, 2));
    return 5;
  }
  const posts = (extraction.candidates || []).filter((candidate) => {
    const text = `${candidate.story_summary || ""} ${candidate.raw_text || ""}`;
    if (!candidate.post_url) return false;
    if (!text || text.length < 40) return false;
    if (/^\s*Honor Reward\s+9\.9 万次赞/i.test(text)) return false;
    return true;
  });
  console.log(JSON.stringify({
    ok: posts.length > 0,
    status: posts.length > 0 ? "real_posts_visible" : "no_real_posts_visible",
    route: "codex_chrome_extension",
    tab: {
      title: await tab.title(),
      url: await tab.url(),
      claimed_from: tabInfo.url,
    },
    raw_candidate_count: extraction.real_post_count || 0,
    post_count: posts.length,
    posts,
  }, null, 2));
  return posts.length > 0 ? 0 : 5;
}

const exitCode = await main().catch((error) => {
  console.error(JSON.stringify({
    ok: false,
    status: "chrome_extension_extract_failed",
    error: String(error.stack || error),
  }, null, 2));
  return 1;
});
globalThis.process.exitCode = exitCode;
