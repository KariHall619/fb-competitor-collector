#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { basename, delimiter } from "node:path";

const require = createRequire(import.meta.url);

function parseSimpleYaml(text) {
  const root = {};
  const stack = [{ indent: -1, value: root }];
  const scalar = (value) => {
    const trimmed = String(value || "").trim();
    if (!trimmed) return "";
    if (trimmed === "true") return true;
    if (trimmed === "false") return false;
    if (trimmed === "null" || trimmed === "~") return null;
    if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
      return trimmed.slice(1, -1);
    }
    if (/^-?\d+$/.test(trimmed)) return Number(trimmed);
    return trimmed;
  };
  const parentFor = (indent) => {
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
    return stack[stack.length - 1].value;
  };
  const lines = text.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const indent = raw.length - raw.trimStart().length;
    const stripped = raw.trim();
    if (stripped.startsWith("- ")) {
      const parent = parentFor(indent);
      if (Array.isArray(parent)) parent.push(scalar(stripped.slice(2)));
      continue;
    }
    if (!stripped.includes(":")) continue;
    const [keyRaw, ...rest] = stripped.split(":");
    const key = keyRaw.trim();
    const value = rest.join(":").trim();
    const parent = parentFor(indent);
    if (value) {
      parent[key] = scalar(value);
      continue;
    }
    const next = lines.slice(index + 1).find((line) => line.trim() && !line.trim().startsWith("#")) || "";
    const child = next.trim().startsWith("- ") ? [] : {};
    parent[key] = child;
    stack.push({ indent, value: child });
  }
  return root;
}

function readConfig(configPath) {
  if (!configPath || !existsSync(configPath)) return {};
  const text = readFileSync(configPath, "utf8");
  if (configPath.endsWith(".json")) return JSON.parse(text);
  try {
    return require("js-yaml").load(text) || {};
  } catch {
    return parseSimpleYaml(text);
  }
}

function platformKey() {
  if (process.platform === "win32") return "windows";
  if (process.platform === "darwin") return "darwin";
  if (process.platform === "linux") return "linux";
  return process.platform || "unknown";
}

function platformAliases(key) {
  const aliases = {
    darwin: ["darwin", "macos", "mac"],
    windows: ["windows", "win32", "win"],
    linux: ["linux"],
  };
  return aliases[key] || [key];
}

function platformOverride(config) {
  const overrides = config.platform_overrides && typeof config.platform_overrides === "object"
    ? config.platform_overrides
    : {};
  for (const alias of platformAliases(platformKey())) {
    if (overrides[alias] && typeof overrides[alias] === "object") return overrides[alias];
  }
  return {};
}

function isAuto(value) {
  return value === undefined || value === null || String(value).trim().toLowerCase() === "auto" || String(value).trim() === "";
}

function whichCommand(names) {
  const pathEnv = process.env.PATH || "";
  const extensions = process.platform === "win32"
    ? (process.env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";")
    : [""];
  for (const dir of pathEnv.split(delimiter).filter(Boolean)) {
    for (const name of names) {
      const candidates = process.platform === "win32" && !/\.[a-z0-9]+$/i.test(name)
        ? extensions.map((ext) => `${dir}/${name}${ext}`)
        : [`${dir}/${name}`];
      for (const candidate of candidates) {
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return "";
}

function commandFromConfig(config) {
  if (Array.isArray(config.opencli_command) && config.opencli_command.length) return config.opencli_command.map(String);
  const raw = config.opencli_path;
  const override = platformOverride(config).opencli_path;
  const configured = isAuto(raw) ? override : raw;
  if (!isAuto(configured)) return [String(configured).trim()];
  const opencli = whichCommand(process.platform === "win32" ? ["opencli.cmd", "opencli.exe", "opencli"] : ["opencli"]);
  if (opencli) return [opencli];
  const npx = whichCommand(process.platform === "win32" ? ["npx.cmd", "npx.exe", "npx"] : ["npx"]);
  if (npx) return [npx, "-y", config.opencli_package || "@jackwener/opencli"];
  return ["npx", "-y", config.opencli_package || "@jackwener/opencli"];
}

function defaultSession(config) {
  const session = String(config.opencli_session || "").trim();
  return session && session.toLowerCase() !== "auto" ? session : "fb-competitor";
}

function runOpencli(args, options = {}) {
  const command = options.command || ["npx", "-y", "@jackwener/opencli"];
  const env = { ...process.env, ...(options.env || {}) };
  return new Promise((resolve) => {
    const child = spawn(command[0], [...command.slice(1), ...args], {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      resolve({ ok: false, code: 1, stdout, stderr: `${stderr}${String(error.message || error)}` });
    });
    child.on("close", (code) => {
      resolve({ ok: code === 0, code: code ?? 1, stdout, stderr });
    });
  });
}

function parseJsonOutput(result) {
  const text = String(result.stdout || "").trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function outputJson(payload) {
  console.log(JSON.stringify(payload, null, 2));
}

function matchesAccount(tab, accountUrl) {
  if (!accountUrl) return true;
  try {
    const target = new URL(accountUrl);
    const current = new URL(tab.url || "");
    const targetId = target.searchParams.get("id");
    if (targetId && `${current.href} ${tab.title || ""}`.includes(targetId)) return true;
    const parts = target.pathname
      .split("/")
      .filter(Boolean)
      .filter((part) => !["people", "profile.php", "posts", "reels"].includes(part));
    return parts.length === 0 || parts.some((part) => `${current.href} ${tab.title || ""}`.includes(part));
  } catch {
    return true;
  }
}

async function refreshTab({ opencliCommand, session, tab }) {
  const tabsResult = await runOpencli(["browser", session, "tab", "list"], { command: opencliCommand });
  const tabsPayload = parseJsonOutput(tabsResult);
  const tabs = normalizeTabs(tabsPayload);
  return tabs.find((item) => item.page === tab.page) || tab;
}

function facebookTab(tab) {
  return /^https?:\/\/([^/]+\.)?facebook\.com\//i.test(tab.url || "");
}

function normalizeTabs(payload) {
  if (!Array.isArray(payload)) return [];
  return payload.map((tab, index) => ({
    index,
    page: tab.page || tab.targetId || tab.id || "",
    title: tab.title || tab.name || "",
    url: tab.url || "",
    current: Boolean(tab.current || tab.active || tab.selected),
    raw: tab,
  }));
}

async function ensureFacebookTab({ opencliCommand, session, accountUrl }) {
  const bind = await runOpencli(["browser", session, "bind"], { command: opencliCommand });
  if (!bind.ok) {
    return {
      ok: false,
      status: "opencli_bind_failed",
      exit_code: 69,
      message: "OpenCLI Browser Bridge 无法绑定当前 Chrome 标签页；请先启用 OpenCLI 扩展，保持目标 Facebook 页面在当前 Chrome 窗口可见。",
      stdout: bind.stdout.trim(),
      stderr: bind.stderr.trim(),
    };
  }

  const tabsResult = await runOpencli(["browser", session, "tab", "list"], { command: opencliCommand });
  const tabsPayload = parseJsonOutput(tabsResult);
  const tabs = normalizeTabs(tabsPayload);
  const facebookTabs = tabs.filter(facebookTab);
  const matched = facebookTabs.find((tab) => matchesAccount(tab, accountUrl));
  const selected = matched || facebookTabs[0];
  if (!selected) {
    return {
      ok: false,
      status: "facebook_tab_missing",
      exit_code: 5,
      action_required: "human_intervention_required",
      message: "未发现已打开的 Facebook 标签页。请先在正常 Chrome 中打开业务人员肉眼可见帖子列表的 Facebook 页面。",
      open_tab_count: tabs.length,
      tabs: tabs.slice(0, 10),
    };
  }
  if (accountUrl && !matched) {
    const opened = await runOpencli(["browser", session, "open", accountUrl, "--tab", selected.page], { command: opencliCommand });
    if (!opened.ok) {
      return {
        ok: false,
        status: "facebook_tab_wrong_account",
        exit_code: 5,
        action_required: "human_intervention_required",
        message: "已发现 Facebook 标签页，但无法在该标签页打开目标账号主页。请手动打开目标账号主页后重试。",
        stdout: opened.stdout.trim(),
        stderr: opened.stderr.trim(),
        tab: selected,
      };
    }
    const refreshed = await refreshTab({ opencliCommand, session, tab: selected });
    return {
      ok: true,
      tab: refreshed,
      open_tab_count: tabs.length,
      facebook_tab_count: facebookTabs.length,
      tab_access_mode: refreshed.current ? "current_tab_opened_target" : "direct_tab_opened_target",
      opened_target_url: accountUrl,
    };
  }

  return {
    ok: true,
    tab: selected,
    open_tab_count: tabs.length,
    facebook_tab_count: facebookTabs.length,
    tab_access_mode: selected.current ? "current_tab" : "direct_tab",
  };
}

async function selectTab({ opencliCommand, session, tab }) {
  return await runOpencli(["browser", session, "tab", "select", tab], { command: opencliCommand });
}

async function evaluateInSession({ opencliCommand, session, js, tab, allowSelectFallback = true }) {
  const args = ["browser", session, "eval", js];
  if (tab) args.push("--tab", tab);
  const direct = await runOpencli(args, { command: opencliCommand });
  if (direct.ok || !tab || !allowSelectFallback) {
    return {
      ...direct,
      payload: parseJsonOutput(direct),
      tab_access_mode: tab ? "direct_tab" : "current_session",
      direct_tab: tab ? 1 : 0,
      select_fallback: 0,
    };
  }

  const select = await selectTab({ opencliCommand, session, tab });
  if (!select.ok) {
    return {
      ...direct,
      payload: parseJsonOutput(direct),
      tab_access_mode: "direct_tab_failed",
      direct_tab: 1,
      select_fallback: 1,
      select_stdout: select.stdout.trim(),
      select_stderr: select.stderr.trim(),
    };
  }

  const retry = await runOpencli(args, { command: opencliCommand });
  return {
    ...retry,
    payload: parseJsonOutput(retry),
    tab_access_mode: "select_fallback",
    direct_tab: 0,
    select_fallback: 1,
    direct_stdout: direct.stdout.trim(),
    direct_stderr: direct.stderr.trim(),
    select_stdout: select.stdout.trim(),
    select_stderr: select.stderr.trim(),
  };
}

function extractArgs(argv = process.argv.slice(2)) {
  const value = (name, fallback = "") => {
    const index = argv.indexOf(name);
    if (index >= 0 && argv[index + 1]) return argv[index + 1];
    return fallback;
  };
  return { value, has: (name) => argv.includes(name), argv };
}

function loadOpencliContext(argv = process.argv.slice(2)) {
  const { value } = extractArgs(argv);
  const configPath = value("--config", "config/settings.yaml");
  const config = readConfig(configPath);
  return {
    configPath,
    config,
    opencliCommand: commandFromConfig(config),
    session: value("--session", defaultSession(config)),
  };
}

function currentScriptName() {
  return basename(process.argv[1] || "");
}

export {
  currentScriptName,
  ensureFacebookTab,
  evaluateInSession,
  extractArgs,
  facebookTab,
  loadOpencliContext,
  matchesAccount,
  outputJson,
  parseJsonOutput,
  readConfig,
  refreshTab,
  runOpencli,
  selectTab,
};
