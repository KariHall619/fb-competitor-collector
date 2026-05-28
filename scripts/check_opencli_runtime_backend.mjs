#!/usr/bin/env node
/**
 * The supported browser backend is now OpenCLI Browser Bridge.
 */

import {
  ensureFacebookTab,
  loadOpencliContext,
  outputJson,
  parseJsonOutput,
  runOpencli,
} from "./opencli_runtime.mjs";

const { opencliCommand, session } = loadOpencliContext();

const result = {
  ok: false,
  backend: "opencli_browser_bridge",
  opencli_command: opencliCommand,
  opencli_session: session,
  doctor: null,
  facebook_tabs: [],
  backend_error: "",
};

try {
  const doctor = await runOpencli(["doctor"], { command: opencliCommand });
  result.doctor = {
    ok: doctor.ok,
    code: doctor.code,
    stdout: doctor.stdout.trim(),
    stderr: doctor.stderr.trim(),
  };
  const tabResult = await ensureFacebookTab({ opencliCommand, session, accountUrl: "" });
  if (!tabResult.ok) {
    result.backend_error = tabResult.message || tabResult.status;
  } else {
    const list = await runOpencli(["browser", session, "tab", "list"], { command: opencliCommand });
    const tabs = parseJsonOutput(list);
    result.facebook_tabs = Array.isArray(tabs)
      ? tabs
        .filter((tab) => /facebook\.com/i.test(tab.url || ""))
        .map((tab) => ({ title: tab.title, url: tab.url, page: tab.page || tab.id || "" }))
        .slice(0, 10)
      : [];
    result.ok = true;
    result.open_tab_count = Array.isArray(tabs) ? tabs.length : 0;
  }
} catch (error) {
  result.backend_error = String(error.stack || error);
}

outputJson(result);
process.exitCode = result.ok ? 0 : 1;
