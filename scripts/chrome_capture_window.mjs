#!/usr/bin/env node
/**
 * Helpers for running Facebook capture in an isolated Chrome window while
 * keeping the same Chrome profile/account selected by Codex Chrome Extension.
 */

import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";

const DEFAULT_FACEBOOK_URL = "https://www.facebook.com/";
const CHROME_OPEN_WINDOW_SCRIPT_ENV = "CODEX_CHROME_OPEN_WINDOW_SCRIPT";
const OPEN_WINDOW_WAIT_MS = 1200;
const CLAIM_RETRY_COUNT = 8;

function pluginOpenWindowScript() {
  if (process.env[CHROME_OPEN_WINDOW_SCRIPT_ENV]) {
    return path.resolve(process.env[CHROME_OPEN_WINDOW_SCRIPT_ENV]);
  }
  return path.join(
    os.homedir(),
    ".codex",
    "plugins",
    "cache",
    "openai-bundled",
    "chrome",
    "latest",
    "scripts",
    "open-chrome-window.js",
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeFacebookCaptureUrl(accountUrl = "") {
  if (!accountUrl) return DEFAULT_FACEBOOK_URL;
  try {
    const url = new URL(accountUrl);
    if (!/(^|\.)facebook\.com$/i.test(url.hostname)) return DEFAULT_FACEBOOK_URL;
    return url.href;
  } catch {
    return DEFAULT_FACEBOOK_URL;
  }
}

function openChromeCaptureWindow({ dryRun = false } = {}) {
  const script = pluginOpenWindowScript();
  const args = [script, "--json"];
  if (dryRun) args.splice(1, 0, "--dry-run");
  const stdout = execFileSync("node", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
  return stdout ? JSON.parse(stdout) : {};
}

async function newCaptureTab(browser, {
  accountUrl = "",
  url = "",
  name = "FB isolated capture",
  waitMs = OPEN_WINDOW_WAIT_MS,
  dryRunOpenWindow = false,
} = {}) {
  if (name) await browser.nameSession(name);
  const beforeTabs = await browser.user.openTabs().catch(() => []);
  const beforeIds = new Set(beforeTabs.map((tab) => tab.id));
  const openedWindow = openChromeCaptureWindow({ dryRun: dryRunOpenWindow });
  const openedBlank = dryRunOpenWindow
    ? null
    : await waitForOpenedBlankTab(browser, beforeIds, waitMs);
  if (!openedBlank) {
    throw new Error("Chrome capture window opened, but Codex could not find its new about:blank tab. Stop before touching existing user Chrome tabs.");
  }
  const tab = await browser.user.claimTab(openedBlank);
  const targetUrl = normalizeFacebookCaptureUrl(url || accountUrl);
  await tab.goto(targetUrl);
  await tab.playwright.waitForLoadState({ state: "domcontentloaded", timeoutMs: 20000 }).catch(() => {});
  return {
    tab,
    openedWindow,
    targetUrl,
    profileDirectory: openedWindow.profileDirectory || "",
  };
}

async function waitForOpenedBlankTab(browser, beforeIds, waitMs) {
  for (let attempt = 0; attempt < CLAIM_RETRY_COUNT; attempt += 1) {
    if (waitMs > 0) await sleep(waitMs);
    const tabs = await browser.user.openTabs().catch(() => []);
    const candidates = tabs.filter((tab) => {
      if (beforeIds.has(tab.id)) return false;
      return !tab.url || tab.url === "about:blank";
    });
    if (candidates.length > 0) return candidates.at(-1);
  }
  return null;
}

export {
  DEFAULT_FACEBOOK_URL,
  CHROME_OPEN_WINDOW_SCRIPT_ENV,
  normalizeFacebookCaptureUrl,
  openChromeCaptureWindow,
  waitForOpenedBlankTab,
  newCaptureTab,
};
