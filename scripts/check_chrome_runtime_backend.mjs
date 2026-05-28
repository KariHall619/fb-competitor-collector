#!/usr/bin/env node
/**
 * Diagnose whether this JavaScript runtime can use the Codex Chrome Extension
 * backend. Run this only from the trusted Codex browser runtime; ordinary shell
 * execution is expected to fail because it has no nativePipe bridge.
 */

import { setupBrowserRuntime } from "/Users/a1/.codex/plugins/cache/openai-bundled/chrome/latest/scripts/browser-client.mjs";

const result = {
  ok: false,
  has_node_repl: Boolean(globalThis.nodeRepl),
  has_native_pipe: Boolean(globalThis.nodeRepl?.nativePipe),
  native_pipe_create: typeof globalThis.nodeRepl?.nativePipe?.createConnection,
  browsers: [],
  extension_error: "",
};

try {
  await setupBrowserRuntime({ globals: globalThis });
  result.browsers = await agent.browsers.list();
  const browser = await agent.browsers.get("extension");
  const tabs = await browser.user.openTabs();
  result.ok = true;
  result.open_tab_count = tabs.length;
  result.facebook_tabs = tabs
    .filter((tab) => /facebook\.com/i.test(tab.url || ""))
    .map((tab) => ({ title: tab.title, url: tab.url }))
    .slice(0, 10);
} catch (error) {
  result.extension_error = String(error.stack || error);
}

console.log(JSON.stringify(result, null, 2));
process.exitCode = result.ok ? 0 : 1;
