#!/usr/bin/env python3
"""Local acceptance tests for the Mac-first MVP pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
VALID_CN_SUMMARY = "这篇故事围绕家庭矛盾和财产冲突展开，主角发现亲人试图夺走资产后及时反击，形成适合短剧改编的反转剧情。"


def run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def assert_url_canonicalization() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import canonicalize_post_url

    urls = [
        "https://www.facebook.com/themeaningoflife88/posts/pfbid02abc?utm_source=x",
        "https://m.facebook.com/themeaningoflife88/posts/pfbid02abc?comment_id=1",
    ]
    assert len({canonicalize_post_url(url) for url in urls}) == 1
    assert (
        canonicalize_post_url("https://www.facebook.com/permalink.php?story_fbid=123&id=456")
        == "https://facebook.com/456/posts/123"
    )
    assert (
        canonicalize_post_url("https://www.facebook.com/photo.php?fbid=789&set=a.123")
        == "https://facebook.com/photo/789"
    )
    assert (
        canonicalize_post_url("https://www.facebook.com/photo/?fbid=790&set=a.123")
        == "https://facebook.com/photo/790"
    )


def assert_mobile_dom_extractor_can_see_story_links() -> None:
    script = """
const { browserExpression } = require('./scripts/fb_dom_extractors');
class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === 'h1') return node.tagName === 'H1';
      if (current === 'h2') return node.tagName === 'H2';
      if (current === 'article') return node.tagName === 'ARTICLE';
      if (current === 'div[role="article"]') return node.tagName === 'DIV' && node.attrs.role === 'article';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}
const story = new Node('div', {}, [
  new Node('a', {
    href: '/story.php?story_fbid=111&id=61584353978558',
    'aria-label': 'Wednesday, May 27, 2026 at 3:11 PM'
  }, [], '2 h'),
  new Node('p', {}, [], 'Family tries to take over the inherited apartment, but the daughter exposes the plan.'),
  new Node('a', { href: 'https://kaylestore.net/?p=54120&utm_source=fb' }, [], 'Read more'),
  new Node('a', { href: '/story.php?story_fbid=111&id=61584353978558' }, [], 'Full Story'),
  new Node('span', {}, [], 'Like'),
  new Node('span', {}, [], 'Comment'),
  new Node('span', {}, [], 'Share')
]);
const body = new Node('body', {}, [
  new Node('h1', {}, [], 'Honor Reward'),
  new Node('div', {}, [new Node('div', {}, [story])])
]);
global.document = {
  title: 'Honor Reward | Facebook',
  body,
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
global.location = new URL('https://m.facebook.com/profile.php?id=61584353978558');
const result = eval(browserExpression(800));
if (result.real_post_count !== 1) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
if (!result.candidates[0].story_summary.includes('inherited apartment')) {
  console.error(JSON.stringify(result.candidates[0], null, 2));
  process.exit(2);
}
if (result.candidates[0].source_surface !== 'mobile') {
  console.error(JSON.stringify(result.candidates[0], null, 2));
  process.exit(3);
}
if (result.candidates[0].posted_at !== '2026年5月27日 15:11' || result.candidates[0].time_source !== 'dom_aria_label') {
  console.error(JSON.stringify(result.candidates[0], null, 2));
  process.exit(4);
}
"""
    result = run(["node", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_dom_extractor_does_not_treat_story_clock_as_post_time() -> None:
    script = """
const { browserExpression } = require('./scripts/fb_dom_extractors');
class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === 'h1') return node.tagName === 'H1';
      if (current === 'h2') return node.tagName === 'H2';
      if (current === 'article') return node.tagName === 'ARTICLE';
      if (current === 'div[role="article"]') return node.tagName === 'DIV' && node.attrs.role === 'article';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}
const story = new Node('div', {}, [
  new Node('a', { href: '/themeaningoflife88/posts/pfbid02abc' }, [], '1d'),
  new Node('p', {}, [], '4:30 a.m.—My husband finally came home. I was alone, holding our baby while cooking for his entire family.'),
  new Node('a', { href: 'https://kaylestore.net/story' }, [], 'Read more'),
  new Node('span', {}, [], 'Like'),
  new Node('span', {}, [], 'Comment'),
  new Node('span', {}, [], 'Share')
]);
const body = new Node('body', {}, [
  new Node('h1', {}, [], 'The meaning of life'),
  new Node('div', {}, [story])
]);
global.document = {
  title: 'The meaning of life | Facebook',
  body,
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
global.location = new URL('https://www.facebook.com/themeaningoflife88');
const result = eval(browserExpression(800));
if (result.real_post_count !== 1) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
if (result.candidates[0].post_time_text !== '1d') {
  console.error(JSON.stringify(result.candidates[0], null, 2));
  process.exit(2);
}
"""
    result = run(["node", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_dom_extractor_excludes_profile_shell_with_external_link() -> None:
    script = """
const { browserExpression } = require('./scripts/fb_dom_extractors');
class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === 'h1') return node.tagName === 'H1';
      if (current === 'h2') return node.tagName === 'H2';
      if (current === 'article') return node.tagName === 'ARTICLE';
      if (current === 'div[role="article"]') return node.tagName === 'DIV' && node.attrs.role === 'article';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}
const shell = new Node('div', {}, [
  new Node('a', { href: '/photo/?fbid=594270278758342&set=a.594270255425011' }, [], 'Profile photo'),
  new Node('a', { href: 'https://timelesslife.info/' }, [], 'Website'),
  new Node('span', {}, [], 'The meaning of life 10M followers • 17 following Learn more Message Follow')
]);
const body = new Node('body', {}, [
  new Node('h1', {}, [], 'The meaning of life'),
  shell
]);
global.document = {
  title: 'The meaning of life | Facebook',
  body,
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
global.location = new URL('https://www.facebook.com/themeaningoflife88');
const result = eval(browserExpression(800));
if (result.candidates.length !== 0) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
"""
    result = run(["node", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_dom_extractor_blocks_visitor_preview() -> None:
    script = """
const { browserExpression } = require('./scripts/fb_dom_extractors');
class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === 'h1') return node.tagName === 'H1';
      if (current === 'h2') return node.tagName === 'H2';
      if (current === 'article') return node.tagName === 'ARTICLE';
      if (current === 'div[role="article"]') return node.tagName === 'DIV' && node.attrs.role === 'article';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}
const profilePhoto = new Node('div', {}, [
  new Node('a', { href: '/photo/?fbid=1134365378709768&set=a.471555211657458' }, [], 'Photos'),
  new Node('span', {}, [], '69 万次赞 • 69 万位粉丝')
]);
const story = new Node('div', {}, [
  new Node('a', { href: '/soulline369/posts/pfbid0UxtZ' }, [], '5小时'),
  new Node('p', {}, [], 'My School Bully Came to My Bank Begging for a $50,000 Loan.'),
  new Node('span', {}, [], '赞')
]);
const body = new Node('body', {}, [
  new Node('h1', {}, [], 'Soul Lines'),
  new Node('div', {}, [], '登录'),
  new Node('div', {}, [], '忘记账户了？'),
  profilePhoto,
  story
]);
global.document = {
  title: 'Soul Lines | Facebook',
  body,
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
global.location = new URL('https://www.facebook.com/soulline369');
const result = eval(browserExpression(800));
if (!result.visitor_preview || !result.capture_blocked) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
if (result.candidates.some((candidate) => candidate.post_url.includes('/photo/'))) {
  console.error(JSON.stringify(result.candidates, null, 2));
  process.exit(2);
}
"""
    result = run(["node", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_dom_extractor_prefers_parent_post_over_photo_link() -> None:
    script = """
const { browserExpression } = require('./scripts/fb_dom_extractors');
class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === 'h1') return node.tagName === 'H1';
      if (current === 'h2') return node.tagName === 'H2';
      if (current === 'article') return node.tagName === 'ARTICLE';
      if (current === 'div[role="article"]') return node.tagName === 'DIV' && node.attrs.role === 'article';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}
const story = new Node('div', { role: 'article' }, [
  new Node('a', { href: '/photo.php?fbid=1553393959512631&set=p.1553393959512631' }, [], 'Photo'),
  new Node('a', { href: '/themeaningoflife88/posts/pfbid-parent-post' }, [], '5h'),
  new Node('p', {}, [], 'A dog barked at the beach and led friends to a hidden rescue.'),
  new Node('a', { href: 'https://kaylestore.net/beach-dog-rescue' }, [], 'Read more'),
  new Node('span', {}, [], '12 comments'),
  new Node('span', {}, [], '3 shares')
]);
const body = new Node('body', {}, [
  new Node('h1', {}, [], 'The meaning of life'),
  story
]);
global.document = {
  title: 'The meaning of life | Facebook',
  body,
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
global.location = new URL('https://www.facebook.com/themeaningoflife88');
const result = eval(browserExpression(800));
if (result.real_post_count !== 1) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
const candidate = result.candidates[0];
if (!candidate.post_url.includes('/posts/pfbid-parent-post')) {
  console.error(JSON.stringify(candidate, null, 2));
  process.exit(2);
}
if (candidate.selected_post_link_kind !== 'post' || candidate.media_link_count !== 1) {
  console.error(JSON.stringify(candidate, null, 2));
  process.exit(3);
}
"""
    result = run(["node", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_extract_script_requires_human_intervention() -> None:
    script_text = (ROOT / "scripts" / "opencli_extract_current_tab.mjs").read_text(encoding="utf-8")
    assert "human_intervention_required" in script_text
    assert "visitor_preview" in script_text
    assert "已停止采集" in script_text
    assert "browser.user.openTabs()" not in script_text
    assert "browser.user.claimTab" not in script_text


def assert_opencli_runtime_keeps_current_bound_tab() -> None:
    script_text = (ROOT / "scripts" / "opencli_runtime.mjs").read_text(encoding="utf-8")
    ensure_start = script_text.index("async function ensureFacebookTab")
    evaluate_start = script_text.index("async function evaluateInSession")
    ensure_body = script_text[ensure_start:evaluate_start]
    assert '"tab", "select", selected.page' not in ensure_body
    assert 'tab_access_mode: selected.current ? "current_tab" : "direct_tab"' in ensure_body
    assert "allowSelectFallback = true" in script_text
    assert '"tab", "select", tab' in script_text
    assert 'tab_access_mode: "select_fallback"' in script_text
    assert "select_fallback" in (ROOT / "scripts" / "opencli_extract_current_tab.mjs").read_text(encoding="utf-8")


def assert_opencli_detail_enrichment_reuses_tab_with_fallback() -> None:
    script_text = (ROOT / "scripts" / "opencli_enrich_post_details.mjs").read_text(encoding="utf-8")
    assert "async function openReusablePostTab" in script_text
    assert "async function navigatePostTab" in script_text
    assert "async function waitForDetailReady" in script_text
    assert '"open",' in script_text
    assert '"--tab",' in script_text
    assert "async function enrichPostWithFreshTab" in script_text
    assert "shouldFallbackAfterReusable" in script_text
    assert "restorePost(post, before)" in script_text
    assert "fresh_tab_fallback" in script_text
    assert "low_disturbance" in script_text
    assert "landingUrlCache" in script_text
    assert 'resolution_source: "existing_landing_url"' in script_text
    assert "buildPerformanceSummary" in script_text
    assert "over_two_minute_posts" in script_text


def assert_opencli_detail_enrichment_blocks_for_human_login() -> None:
    script_text = (ROOT / "scripts" / "opencli_enrich_post_details.mjs").read_text(encoding="utf-8")
    assert 'status: "human_intervention_required"' in script_text
    assert 'action_required: "human_intervention_required"' in script_text
    assert 'reason: state.loggedOut ? "login_required" : "visitor_preview"' in script_text
    assert "if (isHumanInterventionResult(result))" in script_text
    assert "break;" in script_text
    assert "payload.human_intervention_required = true" in script_text
    assert "globalThis.process.exitCode = 1" in script_text


def assert_feishu_writes_require_user_identity() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from lark_io import require_user_identity
    import lark_io

    original = lark_io.run_lark

    class FakeResult:
        def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    try:
        lark_io.run_lark = lambda _config, _args: FakeResult(
            json.dumps({"identity": "user", "tokenStatus": "valid", "userName": "tester"})
        )
        assert require_user_identity({"lark_cli_path": "fake"})["identity"] == "user"
        lark_io.run_lark = lambda _config, _args: FakeResult(
            json.dumps({"identity": "bot", "tokenStatus": "valid"})
        )
        try:
            require_user_identity({"lark_cli_path": "fake"})
        except RuntimeError as exc:
            assert "有效用户身份" in str(exc)
        else:
            raise AssertionError("bot identity must be rejected")
    finally:
        lark_io.run_lark = original


def assert_check_env_prefers_opencli_route() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_env import recommended_capture_route

    assert recommended_capture_route({"opencli_browser_bridge": {"ok": True}})["route"] == "opencli_browser_bridge"
    assert recommended_capture_route({"opencli_browser_bridge": {"ok": False}})["route"] == "blocked_until_opencli_ready"


def assert_check_env_reports_opencli_route_status() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_env import check_opencli, version_ok

    assert version_ok("1.8.0") is True
    assert version_ok("1.7.9") is False
    missing = check_opencli(["/definitely/missing/opencli"], daemon_port=9)
    assert missing["status"] == "opencli_missing"
    assert missing["ok"] is False


def assert_config_resolves_platform_defaults() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from config_loader import resolve_runtime_config

    base = {
        "lark_cli_path": "auto",
        "opencli_path": "auto",
        "platform_overrides": {
            "darwin": {"lark_cli_path": "/Users/a1/.npm-global/bin/lark-cli"},
            "windows": {"lark_cli_path": "lark-cli.cmd"},
        },
    }
    mac = resolve_runtime_config(base, platform_name="Darwin", environ={"HOME": "/Users/a1", "PATH": ""})
    assert mac["runtime"]["platform"] == "darwin"
    assert mac["lark_cli_path"] == "/Users/a1/.npm-global/bin/lark-cli"
    assert mac["opencli_path"] == "opencli"
    assert mac["opencli_command"] == ["opencli"]
    assert mac["opencli_session"] == "fb-competitor"

    windows = resolve_runtime_config(
        base,
        platform_name="Windows",
        environ={"USERPROFILE": r"C:\Users\ops", "PATH": ""},
    )
    assert windows["runtime"]["platform"] == "windows"
    assert windows["lark_cli_path"] == "lark-cli.cmd"
    assert windows["opencli_path"] == "opencli.cmd"
    assert windows["opencli_command"] == ["opencli.cmd"]

    npx_fallback = resolve_runtime_config(
        {"lark_cli_path": "auto", "opencli_path": "auto"},
        platform_name="Darwin",
        environ={"HOME": "/Users/a1", "PATH": "/usr/local/bin"},
    )
    assert npx_fallback["opencli_command"][-2:] == ["-y", "@jackwener/opencli"]

    explicit = resolve_runtime_config(
        {
            "lark_cli_path": r"%USERPROFILE%\bin\lark-cli.cmd",
            "opencli_path": r"%USERPROFILE%\bin\opencli.cmd",
        },
        platform_name="Windows",
        environ={"USERPROFILE": r"C:\Users\ops", "PATH": ""},
    )
    assert explicit["lark_cli_path"] == r"C:\Users\ops\bin\lark-cli.cmd"
    assert explicit["opencli_path"] == r"C:\Users\ops\bin\opencli.cmd"


def assert_exact_time_parsing_and_relative_time_estimation() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import (
        estimate_posted_at_from_relative,
        is_relative_time_label,
        normalize_post,
        normalize_post_time,
        normalize_posted_at,
    )

    assert normalize_posted_at("Wednesday, May 27, 2026 at 2:03 PM") == "2026年5月27日 14:03"
    assert normalize_posted_at("2026年5月27日 下午3:11") == "2026年5月27日 15:11"
    assert normalize_posted_at("May 26 at 10:01 PM") == "2026年5月26日 22:01"
    assert is_relative_time_label("1h") is True
    assert is_relative_time_label("19min") is True
    assert is_relative_time_label("2 小时") is True
    assert normalize_post_time("1h") == ""
    assert normalize_post_time("19min") == ""
    assert normalize_post_time("2 小时") == ""
    assert normalize_post_time("Wednesday, May 27, 2026 at 2:03 PM") == ""
    assert estimate_posted_at_from_relative("1h", "2026-05-28T14:00:00") == "2026年5月28日 13:00"
    assert estimate_posted_at_from_relative("19min", "2026-05-28T14:00:00") == "2026年5月28日 13:41"
    assert estimate_posted_at_from_relative("yesterday", "2026-05-28T14:00:00") == "2026年5月27日 14:00"
    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/relative-time",
            "post_time_text": "1h",
            "article_summary": VALID_CN_SUMMARY,
            "crawled_at": "2026-05-28T14:00:00",
        }
    )
    assert post["posted_date"] == "260528"
    assert post["posted_at"] == "2026年5月28日 13:00"
    assert post["time_confirmed"] is False
    assert post["time_source"] == "relative_estimated"
    assert post["relative_time_text"] == "1h"

    js = """
const { parseExactFacebookTime, exactTimeFromItem, isLikelyHeaderTimeElement } = require('./scripts/fb_time_extractors');
if (parseExactFacebookTime('Wednesday, May 27, 2026 at 3:11 PM') !== '2026年5月27日 15:11') process.exit(1);
if (parseExactFacebookTime('2026年5月27日 下午3:11') !== '2026年5月27日 15:11') process.exit(2);
if (parseExactFacebookTime('May 26 at 10:01 PM') !== '2026年5月26日 22:01') process.exit(4);
const exact = exactTimeFromItem({ text: '2h', aria: 'Wednesday, May 27, 2026 at 3:11 PM', title: '' });
if (exact.posted_at !== '2026年5月27日 15:11' || exact.time_source !== 'dom_aria_label') {
  console.error(JSON.stringify(exact));
  process.exit(3);
}
const scrambled = {
  text: 'r p o n t o s S e d i t 8 3 2 u m 0 4 9 1 7 1 h t 0 m 6 m 6 5 3 h',
  aria: '',
  title: '',
  href: 'https://www.facebook.com/reel/2092487171335426/',
  x: 578,
  y: 217,
  w: 16,
  h: 15,
};
if (!isLikelyHeaderTimeElement(scrambled, 739)) process.exit(5);
const midPageRelative = {
  text: '50m',
  aria: '',
  title: '',
  href: 'https://www.facebook.com/LessonsTaughtByLifepage/posts/pfbid-real',
  x: 530,
  y: 522,
  w: 28,
  h: 16,
};
if (!isLikelyHeaderTimeElement(midPageRelative, 739)) process.exit(6);
"""
    result = run(["node", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_comments_and_shares_are_output_as_engagement() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import POST_HEADERS, normalize_post, output_row

    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/engagement",
            "posted_at": "2026年5月27日 14:03",
            "account_type": "competitor",
            "post_type": "文本",
            "views": "1.2K",
            "article_summary": VALID_CN_SUMMARY,
            "reactions": "81",
            "comments": "29",
            "shares": "3",
        }
    )
    row = output_row(post)
    assert POST_HEADERS == [
        "账号",
        "账户类型",
        "帖子链接",
        "帖子类型",
        "发帖时间",
        "文章链接",
        "故事概要",
        "互动数据（点赞量）",
        "浏览量",
        "是否采用",
        "对应站内链接",
    ]
    assert len(row) == len(POST_HEADERS)
    assert row[1] == "竞品"
    assert row[3] == "文本"
    assert "点赞量：81" in row[7]
    assert "评论数：29" in row[7]
    assert "分享数：3" in row[7]
    assert row[8] == 1200


def assert_field_schema_controls_output_rows() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from field_schema import (
        account_column_roles,
        configured_output_headers,
        normalize_account_type,
        output_row_for_headers,
    )

    post = {
        "account_name": "Story Hub",
        "account_type": "competitor",
        "post_url": "https://facebook.com/story/posts/1",
        "post_type": "文本",
        "posted_at": "2026年5月28日 13:00",
        "time_source": "relative_estimated",
        "landing_url": "https://story.example/article",
        "story_summary": VALID_CN_SUMMARY,
        "likes": 81,
        "comments": 29,
        "shares": 3,
        "views": 120000,
    }
    headers = ["文章链接", "账号", "账户类型", "发帖时间", "互动数据（点赞量）", "浏览量"]
    row = output_row_for_headers(post, headers)
    assert row == [
        "https://story.example/article",
        "Story Hub",
        "竞品",
        "约2026年5月28日 13:00",
        "点赞量：81；评论数：29；分享数：3",
        120000,
    ]
    assert configured_output_headers({"feishu": {"field_schema": {"output_headers": ["账号", "帖子链接"]}}}) == ["账号", "帖子链接"]
    assert account_column_roles(["竞品fb账户", "内部FB账户"]) == {0: "competitor", 1: "internal"}
    assert normalize_account_type("内部主页") == "internal"
    assert normalize_account_type("竞品账号") == "competitor"


def assert_generic_photo_canonical_is_recomputed() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post

    post = normalize_post(
        {
            "post_url": "https://facebook.com/photo/?fbid=790",
            "canonical_post_url": "https://facebook.com/photo",
        },
        {},
    )
    assert post["canonical_post_url"] == "https://facebook.com/photo/790"


def assert_comment_lead_link_overrides_ad_links(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post

    raw = tmp_path / "ad_polluted_raw.json"
    prepared = tmp_path / "ad_polluted_prepared.json"
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/Glasstory89/posts/pfbid-story",
                        "posted_at": "2026年5月29日 12:32",
                        "time_source": "synthetic_hover_tooltip",
                        "article_url": "https://www.proxy-cheap.com/?utm_source=facebook",
                        "landing_url": "https://www.proxy-cheap.com/?utm_source=facebook",
                        "lead_url_raw": "https://l.facebook.com/l.php?u=https%3A%2F%2Fkaylestore.net%2Fdoctors-gave-the-millionaires-son%2F%3Ffbclid%3Dabc",
                        "lead_link_source": "comment",
                        "article_summary": "富豪儿子被医生判定只剩数日生命，女孩揭开蓝色果汁背后的真相。",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run(
        [
            PYTHON,
            "scripts/prepare_capture_result.py",
            "--input",
            str(raw),
            "--output",
            str(prepared),
            "--target-date",
            "260529",
            "--account-name",
            "GLAS Story",
            "--account-url",
            "https://www.facebook.com/Glasstory89",
        ]
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(prepared.read_text(encoding="utf-8"))
    post = data["posts"][0]
    assert post["article_url"] == "https://kaylestore.net/doctors-gave-the-millionaires-son/"
    assert post["landing_url"] == "https://kaylestore.net/doctors-gave-the-millionaires-son/"
    assert post["lead_link_status"] == "qualified"
    assert post["output_status"] == "ready_for_output"

    normalized = normalize_post(
        {
            "post_url": "https://www.facebook.com/Glasstory89/posts/pfbid-story",
            "posted_at": "2026年5月29日 12:32",
            "time_source": "synthetic_hover_tooltip",
            "time_confirmed": True,
            "article_url": "https://www.shopify.com/free-trial?fbadid=1",
            "landing_url": "https://www.shopify.com/free-trial?fbadid=1",
            "lead_url_raw": "https://l.facebook.com/l.php?u=https%3A%2F%2Fkaylestore.net%2Fright-after-giving-birth%2F%3Ffbclid%3Dabc",
            "lead_link_source": "comment_reply",
            "article_summary": "产后母亲被女儿提醒有人要带走新生儿，秘密录音揭开婆婆计划。",
        },
        {"account_type": "competitor"},
    )
    assert normalized["article_url"] == "https://kaylestore.net/right-after-giving-birth/"
    assert normalized["landing_url"] == "https://kaylestore.net/right-after-giving-birth/"
    assert normalized["lead_link_status"] == "qualified"


def assert_prepare_capture_keeps_short_posts_and_blocks_sync(tmp_path: Path) -> None:
    raw = tmp_path / "raw.json"
    prepared = tmp_path / "prepared.json"
    config = tmp_path / "settings.yaml"
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/short",
                        "post_time_text": "10h",
                        "story_summary": "Short",
                        "crawled_at": "2026-05-27T14:00:00",
                    },
                    {
                        "post_url": "https://www.facebook.com/example/posts/ready",
                        "posted_at": "2026年5月27日 17:06",
                        "article_url": "https://site.test/story",
                        "landing_url": "https://site.test/story",
                        "lead_url_raw": "https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.test%2Fstory",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment_reply",
                        "article_summary": "儿子冻结母亲信用卡企图夺权，母亲发现后准备反击。",
                        "engagement_data": "1.2K likes 35 comments",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run(
        [
            PYTHON,
            "scripts/prepare_capture_result.py",
            "--input",
            str(raw),
            "--output",
            str(prepared),
            "--target-date",
            "260527",
            "--account-url",
            "https://www.facebook.com/example",
        ]
    )
    assert result.returncode == 0, result.stderr
    prepared_data = json.loads(prepared.read_text(encoding="utf-8"))
    assert prepared_data["prepared"] == 2
    assert prepared_data["needs_enrichment"] == 1
    short = prepared_data["posts"][0]
    assert short["post_url"].endswith("/short")
    assert short["crawl_status"] == "needs_enrichment"
    assert short["posted_at"] == "2026年5月27日 04:00"
    assert short["posted_date"] == "260527"
    assert short["time_confirmed"] is False
    assert short["time_source"] == "relative_estimated"
    assert "发帖时间为相对时间估算（10h），非Facebook精确时间" in short["note"]

    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'quality.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    sync = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(prepared),
            "--sync",
            "--dry-run",
        ]
    )
    assert sync.returncode == 0, sync.stdout
    assert '"rows": 1' in sync.stdout
    assert '"needs_enrichment_skipped": 1' in sync.stdout


def assert_sync_rejects_estimated_relative_time_but_allows_partial_preview(tmp_path: Path) -> None:
    sample = tmp_path / "estimated_time.json"
    config = tmp_path / "settings_estimated_time.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/estimated",
                        "posted_at": "2026年5月27日 10:00",
                        "time_confirmed": True,
                        "time_source": "relative_estimated",
                        "article_url": "https://site.test/story",
                        "landing_url": "https://site.test/story",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                        "article_summary": VALID_CN_SUMMARY,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'estimated.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    sync = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--dry-run",
        ]
    )
    assert sync.returncode == 1, sync.stdout
    assert '"ready_for_output": 0' in sync.stdout
    assert '"needs_enrichment_skipped": 1' in sync.stdout

    partial = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync-partial",
            "--dry-run",
        ]
    )
    assert partial.returncode == 0, partial.stdout
    assert '"partial_review": 1' in partial.stdout
    assert '"formal_output_unchanged": true' in partial.stdout


def assert_sync_retry_includes_previously_inserted_ready_rows(tmp_path: Path) -> None:
    sample = tmp_path / "ready_retry.json"
    config = tmp_path / "settings_ready_retry.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/retry-ready",
                        "posted_at": "2026年5月27日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://site.test/story",
                        "landing_url": "https://site.test/story",
                        "lead_url_raw": "https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.test%2Fstory",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment_reply",
                        "article_summary": VALID_CN_SUMMARY,
                        "summary_source": "article",
                        "output_status": "ready_for_output",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'retry.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    first = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--no-sync",
        ]
    )
    assert first.returncode == 0, first.stdout
    second = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--dry-run",
        ]
    )
    assert second.returncode == 0, second.stdout
    assert '"updated": 1' in second.stdout
    assert '"ready_for_output": 1' in second.stdout
    assert '"rows": 1' in second.stdout


def assert_article_url_alone_does_not_qualify_lead_link(tmp_path: Path) -> None:
    sample = tmp_path / "article_only.json"
    config = tmp_path / "settings_article_only.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/article-only",
                        "posted_at": "2026年5月27日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://site.test/story",
                        "article_summary": VALID_CN_SUMMARY,
                        "summary_source": "article",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'article_only.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    sync = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--dry-run",
        ]
    )
    assert sync.returncode == 1, sync.stdout
    assert "ready_for_output" in sync.stdout
    assert "needs_enrichment_skipped" in sync.stdout


def assert_filter_sync_applies_output_quality_gate(tmp_path: Path) -> None:
    sample = tmp_path / "filter_gate.json"
    config = tmp_path / "settings_filter_gate.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/filter-gate",
                        "posted_at": "2026年5月27日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://site.test/story",
                        "article_summary": VALID_CN_SUMMARY,
                        "summary_source": "article",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'filter_gate.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    filtered = run(
        [
            PYTHON,
            "scripts/filter_posts.py",
            "--config",
            str(config),
            "--date",
            "260527",
            "--sync",
            "--dry-run",
        ]
    )
    assert filtered.returncode == 1, filtered.stdout
    assert "quality_gate" in filtered.stdout


def assert_quality_gate_requires_comment_lead_source(tmp_path: Path) -> None:
    sample = tmp_path / "bad_lead_status.json"
    config = tmp_path / "settings_bad_lead_status.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/bad-lead-status",
                        "posted_at": "2026年5月27日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://site.test/story",
                        "landing_url": "https://site.test/story",
                        "lead_link_status": "qualified",
                        "article_summary": VALID_CN_SUMMARY,
                        "summary_source": "article",
                        "output_status": "ready_for_output",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'bad_lead_status.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    sync = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--dry-run",
        ]
    )
    assert sync.returncode == 1, sync.stdout
    assert "missing_qualified_comment_lead_link" in sync.stdout


def assert_detail_enrichment_ignores_page_shell_ad_links() -> None:
    script = """
import { leadLinkScanBrowserExpression } from './scripts/opencli_enrich_post_details.mjs';

class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    return this.attrs.href ? new URL(this.attrs.href, global.location.href).href : '';
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (selector.includes('[role="complementary"]') && node.attrs.role === 'complementary') return node;
      if (selector.includes('[role="navigation"]') && node.attrs.role === 'navigation') return node;
      if (selector.includes('[role="contentinfo"]') && node.attrs.role === 'contentinfo') return node;
      node = node.parentElement;
    }
    return null;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === '[role="article"]') return node.attrs.role === 'article';
      if (current === 'div[aria-label]') return node.tagName === 'DIV' && !!node.attrs['aria-label'];
      if (current === 'li') return node.tagName === 'LI';
      if (current === 'div') return node.tagName === 'DIV';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}
const adShell = new Node('div', { role: 'complementary' }, [
  new Node('div', {}, [
    new Node('span', {}, [], 'Sponsored'),
    new Node('a', { href: 'https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.shopify.com%2Ffree-trial' }, [], 'shopify.com')
  ], 'Harness the Power of AI')
]);
const realComment = new Node('div', { role: 'article' }, [
  new Node('span', {}, [], 'Lessons Taught By Life'),
  new Node('span', {}, [], 'Full story here'),
  new Node('a', { href: 'https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.test%2Fstory' }, [], 'example.test'),
  new Node('a', { href: '/LessonsTaughtByLifepage/posts/pfbid?comment_id=123' }, [], '48m'),
  new Node('span', {}, [], 'Reply')
]);
const body = new Node('body', {}, [adShell, realComment]);
global.document = {
  querySelectorAll: (selector) => body.querySelectorAll(selector),
};
global.location = new URL('https://www.facebook.com/LessonsTaughtByLifepage/posts/pfbid');
const results = eval(leadLinkScanBrowserExpression('Lessons Taught By Life', 'default'));
if (results.length !== 1 || !results[0].href.includes('example.test') || results[0].href.includes('shopify')) {
  console.error(JSON.stringify(results, null, 2));
  process.exit(1);
}
"""
    result = run(["node", "--input-type=module", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_detail_engagement_is_anchored_to_main_post() -> None:
    script = """
import { detailEngagementBrowserExpression } from './scripts/opencli_enrich_post_details.mjs';

class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  get href() {
    return this.attrs.href ? new URL(this.attrs.href, global.location.href).href : '';
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (selector.includes('[role="article"]') && node.attrs.role === 'article') return node;
      if (selector.includes('[role="complementary"]') && node.attrs.role === 'complementary') return node;
      node = node.parentElement;
    }
    return null;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'a') return node.tagName === 'A';
      if (current === 'span') return node.tagName === 'SPAN';
      if (current === 'div') return node.tagName === 'DIV';
      if (current === '[aria-label]') return !!node.attrs['aria-label'];
      if (current === '[title]') return !!node.attrs.title;
      if (current === 'abbr') return node.tagName === 'ABBR';
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}

const timeLink = new Node('a', { href: '/example/posts/1' }, [], '3h');
const mainPost = new Node('div', { role: 'article' }, [
  new Node('span', {}, [], 'Example Page'),
  timeLink,
  new Node('p', {}, [], 'Full story in 1st comment'),
  new Node('span', {}, [], '811 / 350 / 31'),
  new Node('span', {}, [], 'Like'),
  new Node('span', {}, [], 'Comment'),
  new Node('span', {}, [], 'Share'),
]);
const comment = new Node('div', { role: 'article' }, [
  new Node('span', {}, [], 'Reader'),
  new Node('span', {}, [], '58 29 赞'),
  new Node('span', {}, [], 'Reply'),
]);
const body = new Node('body', {}, [mainPost, comment]);
global.document = {
  querySelectorAll: (selector) => body.querySelectorAll(selector),
};
global.location = new URL('https://www.facebook.com/example/posts/1');
const elements = global.document.querySelectorAll('a, abbr, span');
const target = { index: elements.indexOf(timeLink) };
const result = eval(detailEngagementBrowserExpression(target));
if (result.confidence !== 'anchored' || result.likes !== 811 || result.comments !== 350 || result.shares !== 31) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
if (result.raw.includes('58') || result.raw.includes('29 赞')) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(2);
}
"""
    result = run(["node", "--input-type=module", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_comment_mode_expression_can_select_all_comments() -> None:
    script = """
import { commentModeBrowserExpression } from './scripts/opencli_enrich_post_details.mjs';

class Node {
  constructor(tagName, attrs = {}, children = [], ownText = '') {
    this.tagName = tagName.toUpperCase();
    this.attrs = attrs;
    this.children = children;
    this.ownText = ownText;
    this.clicked = false;
    this.parentElement = null;
    for (const child of children) child.parentElement = this;
  }
  get innerText() {
    return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\\n');
  }
  get textContent() {
    return this.innerText;
  }
  getAttribute(name) {
    return this.attrs[name] || '';
  }
  click() {
    this.clicked = true;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === 'div[role="button"]') return node.tagName === 'DIV' && node.attrs.role === 'button';
      if (current === 'span') return node.tagName === 'SPAN';
      if (current === 'a') return node.tagName === 'A';
      if (current === '[aria-label]') return !!node.attrs['aria-label'];
      return false;
    };
    const visit = (node) => {
      if (selectors.some((current) => matches(node, current))) result.push(node);
      for (const child of node.children) visit(child);
    };
    visit(this);
    return result;
  }
}

const sort = new Node('div', { role: 'button' }, [], 'Most relevant');
const all = new Node('div', { role: 'button' }, [], 'All comments');
const body = new Node('body', {}, [sort, all]);
global.document = { querySelectorAll: (selector) => body.querySelectorAll(selector) };
const result = await eval(commentModeBrowserExpression('all_comments'));
if (!result.clicked || !sort.clicked || !all.clicked) {
  console.error(JSON.stringify({ result, sort: sort.clicked, all: all.clicked }, null, 2));
  process.exit(1);
}
"""
    result = run(["node", "--input-type=module", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_extract_helpers_dedupe_homepage_candidates() -> None:
    js = """
import { postKey, validCandidate, RUN_MAIN } from './scripts/opencli_extract_current_tab.mjs';

if (RUN_MAIN) process.exit(1);
const first = {
  post_url: 'https://www.facebook.com/example/posts/1001?fbclid=abc',
  story_summary: 'A long enough story summary that should pass filtering.',
};
const duplicate = {
  post_url: 'https://m.facebook.com/example/posts/1001?ref=share',
  raw_text: 'A long enough story summary that should pass filtering.',
};
if (postKey(first) !== postKey(duplicate)) {
  console.error(JSON.stringify({ first: postKey(first), duplicate: postKey(duplicate) }, null, 2));
  process.exit(2);
}
if (!validCandidate(first)) process.exit(3);
if (validCandidate({ post_url: first.post_url, story_summary: 'short' })) process.exit(4);
"""
    result = run(["node", "--input-type=module", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_prepare_capture_has_no_base_time_argument() -> None:
    help_result = run([PYTHON, "scripts/prepare_capture_result.py", "--help"])
    assert help_result.returncode == 0, help_result.stderr
    assert "--base-time" not in help_result.stdout

    rejected = run(
        [
            PYTHON,
            "scripts/prepare_capture_result.py",
            "--input",
            "missing.json",
            "--output",
            "missing.out.json",
            "--target-date",
            "260527",
            "--base-time",
            "2026-05-27T19:00:00",
        ]
    )
    assert rejected.returncode != 0
    assert "unrecognized arguments: --base-time" in rejected.stderr


def assert_exact_time_verifier_summary_contract() -> None:
    js = """
import {
  facebookTab,
  matchesAccount,
  RUN_MAIN,
  summarizeExactTimeChecks,
  verifyExactTimeCapture,
} from './scripts/opencli_verify_exact_time.mjs';

if (RUN_MAIN) process.exit(6);
if (typeof verifyExactTimeCapture !== 'function') process.exit(7);
if (!facebookTab({ url: 'https://www.facebook.com/themeaningoflife88' })) process.exit(1);
if (facebookTab({ url: 'https://example.com/themeaningoflife88' })) process.exit(2);
if (!matchesAccount(
  { url: 'https://www.facebook.com/themeaningoflife88/posts', title: 'The meaning of life' },
  'https://www.facebook.com/themeaningoflife88'
)) process.exit(3);

const confirmed = summarizeExactTimeChecks({
  scan: { target_count: 1, exact_dom_count: 1 },
  checks: [{
    visible_text: '2h',
    posted_at_raw: 'Wednesday, May 27, 2026 at 3:11 PM',
    posted_at: '2026年5月27日 15:11',
    time_source: 'dom_aria_label',
    confirmed: true,
  }],
  tab: { title: 'The meaning of life | Facebook', url: 'https://www.facebook.com/themeaningoflife88' },
  claimedFrom: 'https://www.facebook.com/themeaningoflife88',
});
if (!confirmed.ok || confirmed.status !== 'exact_time_confirmed' || confirmed.confirmed_count !== 1) {
  console.error(JSON.stringify(confirmed, null, 2));
  process.exit(4);
}

const missing = summarizeExactTimeChecks({
  scan: { target_count: 1, exact_dom_count: 0 },
  checks: [{ visible_text: '2h', posted_at: '', confirmed: false }],
  tab: { title: 'The meaning of life | Facebook', url: 'https://www.facebook.com/themeaningoflife88' },
  claimedFrom: 'https://www.facebook.com/themeaningoflife88',
});
if (missing.ok || missing.status !== 'exact_time_not_found' || missing.confirmed_count !== 0) {
  console.error(JSON.stringify(missing, null, 2));
  process.exit(5);
}
"""
    result = run(["node", "--input-type=module", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout

    no_run = run(["node", "scripts/opencli_verify_exact_time.mjs", "--self-test"])
    assert no_run.returncode == 0
    assert no_run.stdout == ""


def assert_opencli_detail_enrichment_supports_target_date_filter() -> None:
    js = """
import { buildCoverageSummary, dateKeyFromPostedAt } from './scripts/opencli_enrich_post_details.mjs';

if (dateKeyFromPostedAt('2026年5月29日 12:32') !== '260529') process.exit(1);
if (dateKeyFromPostedAt('2026年11月3日 01:05') !== '261103') process.exit(2);
if (dateKeyFromPostedAt('3h') !== '') process.exit(3);
const payload = {
  posts: [
    { post_url: 'https://facebook.com/example/posts/1', output_status: 'ready_for_output', posted_at: '2026年6月1日 12:00', time_confirmed: true, summary_source: 'article', story_summary: '这篇故事讲述家庭冲突升级后，主角发现问题并及时反击的反转剧情。', lead_link_status: 'qualified', lead_link_source: 'comment', lead_url_raw: 'https://site.test/a', landing_url: 'https://site.test/a' },
    { post_url: 'https://facebook.com/example/posts/2', output_status: 'needs_enrichment', posted_at: '2026年6月1日 13:00', time_confirmed: true, summary_source: 'pending_article_summary', lead_link_status: 'missing', engagement_confidence: 'anchored_missing_metrics' },
  ],
  date_filtered_out: [{ post_url: 'https://facebook.com/example/posts/old' }],
};
const summary = buildCoverageSummary(payload, 3);
if (summary.input_posts !== 3 || summary.after_target_date_filter !== 2 || summary.ready_for_output !== 1 || summary.needs_enrichment !== 1) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(4);
}
if (summary.reason_counts.missing_qualified_comment_lead_link !== 1 || summary.reason_counts.engagement_unconfirmed !== 1) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(5);
}
"""
    result = run(["node", "--input-type=module", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_prepare_capture_keeps_photo_media_links_as_candidates(tmp_path: Path) -> None:
    raw = tmp_path / "photo_raw.json"
    prepared = tmp_path / "photo_prepared.json"
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/photo.php?fbid=1553393959512631&set=p.1553393959512631&type=3",
                        "post_time_text": "1h",
                        "crawled_at": "2026-05-27T14:00:00",
                        "article_url": "https://kaylestore.net/story",
                        "landing_url": "https://kaylestore.net/story",
                        "lead_url_raw": "https://l.facebook.com/l.php?u=https%3A%2F%2Fkaylestore.net%2Fstory",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment_reply",
                        "story_summary": "Author The meaning of life Full story https://kaylestore.net/story 1h 6",
                    },
                    {
                        "post_url": "https://www.facebook.com/themeaningoflife88/posts/pfbid-real",
                        "post_time_text": "1h",
                        "crawled_at": "2026-05-27T14:00:00",
                        "article_url": "https://kaylestore.net/different-story",
                        "landing_url": "https://kaylestore.net/different-story",
                        "lead_url_raw": "https://kaylestore.net/different-story",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                        "article_summary": VALID_CN_SUMMARY,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run(
        [
            PYTHON,
            "scripts/prepare_capture_result.py",
            "--input",
            str(raw),
            "--output",
            str(prepared),
            "--target-date",
            "260527",
            "--account-url",
            "https://www.facebook.com/themeaningoflife88",
        ]
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(prepared.read_text(encoding="utf-8"))
    assert data["prepared"] == 2
    assert data["media_candidate_count"] == 1
    assert data["media_suspect_count"] == 0
    assert data["covered_media_suspect_count"] == 0
    assert data["posts"][0]["fb_link_kind"] == "photo"
    assert "photo.php" in data["posts"][0]["post_url"]
    assert data["posts"][0]["crawl_status"] == "needs_enrichment"
    assert not any(item["reason"] == "media_link_requires_parent_post" for item in data["rejected"])

    config = tmp_path / "settings_media_gate.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'media_gate.sqlite'}"
    )
    config.write_text(config_text, encoding="utf-8")
    sync = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(prepared),
            "--sync-partial",
            "--dry-run",
        ]
    )
    assert sync.returncode == 0, sync.stdout
    assert '"partial_review": 2' in sync.stdout
    assert '"formal_output_unchanged": true' in sync.stdout


def assert_thirteen_incomplete_candidates_are_imported_for_enrichment(tmp_path: Path) -> None:
    config = tmp_path / "settings_13_needs_enrichment.yaml"
    sample = tmp_path / "thirteen_needs_enrichment.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'thirteen.sqlite'}"
        ),
        encoding="utf-8",
    )
    sample.write_text(
        json.dumps(
            [
                {
                    "post_url": f"https://www.facebook.com/example/posts/incomplete-{index}",
                    "posted_at": "2026年6月1日 12:00",
                    "time_confirmed": True,
                    "summary_source": "pending_article_summary",
                    "story_summary": f"候选 {index}",
                    "lead_link_status": "missing",
                    "crawl_status": "needs_enrichment",
                    "output_status": "needs_enrichment",
                }
                for index in range(1, 14)
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stderr
    data = json.loads(imported.stdout)
    assert data["inserted"] == 13, imported.stdout

    sync = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--sync", "--dry-run"])
    assert sync.returncode == 1, sync.stdout
    assert '"ready_for_output": 0' in sync.stdout
    assert '"needs_enrichment_skipped": 13' in sync.stdout


def assert_prepare_capture_does_not_alert_media_when_parent_post_is_captured(tmp_path: Path) -> None:
    raw = tmp_path / "photo_covered_raw.json"
    prepared = tmp_path / "photo_covered_prepared.json"
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/photo.php?fbid=1553393959512631&set=p.1553393959512631&type=3",
                        "post_time_text": "5h",
                        "crawled_at": "2026-05-27T14:00:00",
                        "article_url": "https://kaylestore.net/beach-dog-rescue",
                        "landing_url": "https://kaylestore.net/beach-dog-rescue",
                        "lead_url_raw": "https://kaylestore.net/beach-dog-rescue",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment_reply",
                        "story_summary": "Photo media item for same article",
                    },
                    {
                        "post_url": "https://www.facebook.com/themeaningoflife88/posts/pfbid-parent-post",
                        "post_time_text": "5h",
                        "crawled_at": "2026-05-27T14:00:00",
                        "article_url": "https://kaylestore.net/beach-dog-rescue",
                        "landing_url": "https://kaylestore.net/beach-dog-rescue",
                        "lead_url_raw": "https://kaylestore.net/beach-dog-rescue",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                        "article_summary": "朋友们在海滩休息时，一只狗异常狂吠并引导她们发现受伤男子。",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = run(
        [
            PYTHON,
            "scripts/prepare_capture_result.py",
            "--input",
            str(raw),
            "--output",
            str(prepared),
            "--target-date",
            "260527",
            "--account-url",
            "https://www.facebook.com/themeaningoflife88",
        ]
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(prepared.read_text(encoding="utf-8"))
    assert data["prepared"] == 2
    assert data["media_candidate_count"] == 1
    assert data["media_suspect_count"] == 0
    assert data["covered_media_suspect_count"] == 0
    assert data["posts"][0]["fb_link_kind"] == "photo"


def assert_article_material_extractor(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_article_material

    html_path = tmp_path / "article.html"
    html_path.write_text(
        """
        <html>
          <head>
            <title>Family betrayal story</title>
            <meta name="description" content="A son freezes his mother's credit cards.">
          </head>
          <body>
            <article>
              <p>The son froze every card his mother had and tried to take control of the family company.</p>
              <p>The mother discovered the paperwork problem and prepared a legal counterattack.</p>
            </article>
          </body>
        </html>
        """,
        encoding="utf-8",
    )
    result = fetch_article_material.extract_material(html_path.as_uri())
    assert result["ok"] is True
    assert "Family betrayal story" in result["title"]
    assert "legal counterattack" in result["text_excerpt"]


def assert_partial_review_status_and_task_queue(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, pending_enrichment_tasks

    conn = connect(tmp_path / "partial.sqlite")
    post = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/pfbid-partial",
            "post_time_text": "2h",
            "story_summary": "A visible homepage candidate.",
            "article_url": "https://story.example/a",
            "crawled_at": "2026-05-28T10:00:00",
        },
        {"source_skill": "test"},
    )
    assert post["output_status"] == "partial_review"
    enqueue_enrichment_tasks_for_posts(conn, [post])
    enqueue_enrichment_tasks_for_posts(conn, [post])
    tasks = pending_enrichment_tasks(conn, limit=20)
    assert sorted(task["stage"] for task in tasks) == ["article_material", "detail_time", "lead_link", "summary"]


def assert_enrichment_worker_groups_detail_tasks_by_post(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts
    import enrichment_worker

    conn = connect(tmp_path / "detail-grouping.sqlite")
    post = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/pfbid-detail-group",
            "post_time_text": "2h",
            "story_summary": "A visible homepage candidate.",
            "crawled_at": "2026-05-28T10:00:00",
        },
        {"source_skill": "test"},
    )
    from store import upsert_post, pending_enrichment_tasks

    upsert_post(conn, post)
    enqueue_enrichment_tasks_for_posts(conn, [post])
    detail_tasks = [task for task in pending_enrichment_tasks(conn, stages=["detail_time", "lead_link"], limit=20)]
    assert sorted(task["stage"] for task in detail_tasks) == ["detail_time", "lead_link"]

    units, missing = enrichment_worker.detail_units_for_tasks(conn, detail_tasks)
    assert missing == 0
    assert len(units) == 1
    assert units[0]["key"] == post["canonical_post_url"]
    assert sorted(units[0]["stages"]) == ["detail_time", "lead_link"]
    assert sorted(task["stage"] for task in units[0]["tasks"]) == ["detail_time", "lead_link"]

    batches = enrichment_worker.batches_for_detail_units(units, batch_size=2)
    assert len(batches) == 1
    assert len(batches[0]) == 1


def assert_enrichment_worker_article_cache_and_summary(tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    db_path = tmp_path / "worker.sqlite"
    article = tmp_path / "article.html"
    raw = tmp_path / "partial.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    article.write_text(
        """
        <html><head><title>Worker cache story</title></head>
        <body><p>The worker fetched this page once and reused the cached article material.</p></body></html>
        """,
        encoding="utf-8",
    )
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Story Hub",
                        "account_url": "https://www.facebook.com/storyhub",
                        "post_url": "https://www.facebook.com/storyhub/posts/pfbid-cache-1",
                        "posted_at": "2026年5月28日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": article.as_uri(),
                        "landing_url": article.as_uri(),
                        "lead_url_raw": article.as_uri(),
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                    },
                    {
                        "account_name": "Story Hub",
                        "account_url": "https://www.facebook.com/storyhub",
                        "post_url": "https://www.facebook.com/storyhub/posts/pfbid-cache-2",
                        "posted_at": "2026年5月28日 11:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": article.as_uri(),
                        "landing_url": article.as_uri(),
                        "lead_url_raw": article.as_uri(),
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--no-sync"])
    assert imported.returncode == 0, imported.stderr or imported.stdout
    import_data = json.loads(imported.stdout)
    assert import_data["enrichment_tasks"]["queued_or_refreshed"] >= 2

    article_worker = run(
        [
            PYTHON,
            "scripts/enrichment_worker.py",
            "--config",
            str(config),
            "--stages",
            "article_material",
            "--limit",
            "10",
            "--article-concurrency",
            "2",
        ]
    )
    assert article_worker.returncode == 0, article_worker.stdout + article_worker.stderr
    article_data = json.loads(article_worker.stdout)
    assert article_data["completed"] == 2
    assert article_data["task_counts"].get("article_material:done") == 2

    summary_worker = run(
        [
            PYTHON,
            "scripts/enrichment_worker.py",
            "--config",
            str(config),
            "--stages",
            "summary",
            "--limit",
            "10",
        ]
    )
    assert summary_worker.returncode == 1, summary_worker.stdout + summary_worker.stderr

    sys.path.insert(0, str(ROOT / "scripts"))
    from store import all_posts, cached_article_material, connect, pending_enrichment_tasks

    conn = connect(db_path)
    posts = all_posts(conn)
    assert all(post["output_status"] != "ready_for_output" for post in posts)
    assert cached_article_material(conn, article.as_uri())["ok"] is True
    failed_summary_tasks = pending_enrichment_tasks(conn, stages=["summary"], limit=10)
    assert all("requires_codex_chinese_summary" in (task.get("last_error") or "") for task in failed_summary_tasks)

    requests_path = tmp_path / "summary_requests.json"
    exported = run(
        [
            PYTHON,
            "scripts/export_summary_requests.py",
            "--config",
            str(config),
            "--output",
            str(requests_path),
        ]
    )
    assert exported.returncode == 0, exported.stderr or exported.stdout
    requests = json.loads(requests_path.read_text(encoding="utf-8"))
    assert requests["count"] == 2
    assert "Worker cache story" in requests["requests"][0]["article_material"]["title"]

    bad_summaries = tmp_path / "bad_summaries.json"
    bad_summaries.write_text(
        json.dumps({article.as_uri(): "Worker cache story"}, ensure_ascii=False),
        encoding="utf-8",
    )
    bad_apply = run(
        [
            PYTHON,
            "scripts/apply_article_summaries.py",
            "--config",
            str(config),
            "--summaries",
            str(bad_summaries),
        ]
    )
    assert bad_apply.returncode == 0, bad_apply.stderr or bad_apply.stdout
    bad_data = json.loads(bad_apply.stdout)
    assert bad_data["applied"] == 0
    assert bad_data["rejected"] == 2

    good_summaries = tmp_path / "good_summaries.json"
    good_summaries.write_text(
        json.dumps(
            {
                article.as_uri(): "这篇故事围绕家庭资产控制展开，儿子试图冻结母亲信用卡并掌控公司，母亲发现异常后准备通过法律方式反击。"
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    good_apply = run(
        [
            PYTHON,
            "scripts/apply_article_summaries.py",
            "--config",
            str(config),
            "--summaries",
            str(good_summaries),
        ]
    )
    assert good_apply.returncode == 0, good_apply.stderr or good_apply.stdout
    good_data = json.loads(good_apply.stdout)
    assert good_data["applied"] == 2
    conn = connect(db_path)
    posts = all_posts(conn)
    assert all(post["output_status"] == "ready_for_output" for post in posts)


def assert_story_summary_audit_downgrades_invalid_rows(tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    db_path = tmp_path / "audit.sqlite"
    raw = tmp_path / "ready_with_bad_summary.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Story Hub",
                        "account_url": "https://www.facebook.com/storyhub",
                        "post_url": "https://www.facebook.com/storyhub/posts/pfbid-bad-summary",
                        "posted_at": "2026年5月28日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://story.example/bad",
                        "landing_url": "https://story.example/bad",
                        "lead_url_raw": "https://story.example/bad",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                        "story_summary": "The worker fetched this page once and reused the cached article material.",
                        "summary_source": "article",
                        "output_status": "ready_for_output",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--no-sync"])
    assert imported.returncode == 0, imported.stderr or imported.stdout

    audited = run([PYTHON, "scripts/audit_story_summaries.py", "--config", str(config)])
    assert audited.returncode == 0, audited.stderr or audited.stdout
    audit_data = json.loads(audited.stdout)
    assert audit_data["invalid"] == 1
    assert "story_summary_not_chinese" in audit_data["items"][0]["errors"]

    fixed = run([PYTHON, "scripts/audit_story_summaries.py", "--config", str(config), "--fix"])
    assert fixed.returncode == 0, fixed.stderr or fixed.stdout
    fixed_data = json.loads(fixed.stdout)
    assert fixed_data["fixed"] == 1

    sys.path.insert(0, str(ROOT / "scripts"))
    from store import all_posts, connect, pending_enrichment_tasks

    conn = connect(db_path)
    post = all_posts(conn)[0]
    assert post["summary_source"] == "pending_article_summary"
    assert post["output_status"] != "ready_for_output"
    tasks = pending_enrichment_tasks(conn, stages=["summary"], limit=10)
    assert len(tasks) == 1


def assert_partial_sync_dry_run_does_not_replace_formal_gate(tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    raw = tmp_path / "partial.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'partial-sync.sqlite'}"),
        encoding="utf-8",
    )
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Story Hub",
                        "account_url": "https://www.facebook.com/storyhub",
                        "post_url": "https://www.facebook.com/storyhub/posts/pfbid-preview",
                        "post_time_text": "1h",
                        "story_summary": "Visible preview candidate.",
                        "article_url": "https://story.example/preview",
                        "crawled_at": "2026-05-28T10:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    formal = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--sync", "--dry-run"])
    assert formal.returncode == 1, formal.stdout
    assert "ready_for_output" in formal.stdout

    partial = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--sync-partial", "--dry-run"])
    assert partial.returncode == 0, partial.stdout
    data = json.loads(partial.stdout)
    assert data["feishu_sync"]["dry_run"] is True
    assert data["feishu_sync"]["partial_review"] == 1
    assert data["feishu_sync"]["formal_output_unchanged"] is True


def main() -> int:
    assert_url_canonicalization()
    assert_exact_time_parsing_and_relative_time_estimation()
    assert_comments_and_shares_are_output_as_engagement()
    assert_field_schema_controls_output_rows()
    assert_generic_photo_canonical_is_recomputed()
    assert_mobile_dom_extractor_can_see_story_links()
    assert_dom_extractor_does_not_treat_story_clock_as_post_time()
    assert_dom_extractor_excludes_profile_shell_with_external_link()
    assert_dom_extractor_blocks_visitor_preview()
    assert_dom_extractor_prefers_parent_post_over_photo_link()
    assert_detail_engagement_is_anchored_to_main_post()
    assert_detail_enrichment_ignores_page_shell_ad_links()
    assert_comment_mode_expression_can_select_all_comments()
    assert_opencli_extract_helpers_dedupe_homepage_candidates()
    assert_opencli_extract_script_requires_human_intervention()
    assert_opencli_runtime_keeps_current_bound_tab()
    assert_opencli_detail_enrichment_reuses_tab_with_fallback()
    assert_opencli_detail_enrichment_blocks_for_human_login()
    assert_feishu_writes_require_user_identity()
    assert_check_env_prefers_opencli_route()
    assert_config_resolves_platform_defaults()
    assert_check_env_reports_opencli_route_status()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = tmp_path / "settings.yaml"
        sample = tmp_path / "sample_posts.json"
        shutil.copy(ROOT / "config" / "settings.yaml.example", config)
        shutil.copy(ROOT / "samples" / "sample_posts.json", sample)
        text = config.read_text(encoding="utf-8")
        text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'posts.sqlite'}")
        config.write_text(text, encoding="utf-8")

        first = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
        assert first.returncode == 0, first.stderr
        first_data = json.loads(first.stdout)
        assert first_data["inserted"] == 1, first.stdout

        second = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
        assert second.returncode == 0, second.stderr
        second_data = json.loads(second.stdout)
        assert second_data["inserted"] == 0, second.stdout
        assert second_data["updated"] == 1, second.stdout

        filtered = run([PYTHON, "scripts/filter_posts.py", "--config", str(config), "--date", "260521", "--account-type", "competitor"])
        assert filtered.returncode == 0, filtered.stderr
        filtered_data = json.loads(filtered.stdout)
        assert filtered_data["count"] == 1, filtered.stdout

        hot = run([PYTHON, "scripts/filter_posts.py", "--config", str(config), "--hot-views"])
        assert hot.returncode == 0, hot.stderr
        hot_data = json.loads(hot.stdout)
        assert hot_data["count"] == 0, hot.stdout

        duplicate_sample = tmp_path / "sample_posts_13_with_duplicates.json"
        shutil.copy(ROOT / "samples" / "sample_posts_13_with_duplicates.json", duplicate_sample)
        many = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(duplicate_sample), "--no-sync"])
        assert many.returncode == 0, many.stderr
        many_data = json.loads(many.stdout)
        assert many_data["inserted"] == 13, many.stdout

        hot_after_many = run([PYTHON, "scripts/filter_posts.py", "--config", str(config), "--hot-views"])
        assert hot_after_many.returncode == 0, hot_after_many.stderr
        hot_after_many_data = json.loads(hot_after_many.stdout)
        assert hot_after_many_data["count"] == 1, hot_after_many.stdout
        assert_prepare_capture_keeps_short_posts_and_blocks_sync(tmp_path)
        assert_sync_rejects_estimated_relative_time_but_allows_partial_preview(tmp_path)
        assert_sync_retry_includes_previously_inserted_ready_rows(tmp_path)
        assert_article_url_alone_does_not_qualify_lead_link(tmp_path)
        assert_filter_sync_applies_output_quality_gate(tmp_path)
        assert_comment_lead_link_overrides_ad_links(tmp_path)
        assert_prepare_capture_has_no_base_time_argument()
        assert_exact_time_verifier_summary_contract()
        assert_opencli_detail_enrichment_supports_target_date_filter()
        assert_prepare_capture_keeps_photo_media_links_as_candidates(tmp_path)
        assert_thirteen_incomplete_candidates_are_imported_for_enrichment(tmp_path)
        assert_prepare_capture_does_not_alert_media_when_parent_post_is_captured(tmp_path)
        assert_article_material_extractor(tmp_path)
        assert_partial_review_status_and_task_queue(tmp_path)
        assert_enrichment_worker_groups_detail_tasks_by_post(tmp_path)
        assert_enrichment_worker_article_cache_and_summary(tmp_path)
        assert_story_summary_audit_downgrades_invalid_rows(tmp_path)
        assert_partial_sync_dry_run_does_not_replace_formal_gate(tmp_path)

    print("local pipeline acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
