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


def assert_chrome_extract_script_requires_human_intervention() -> None:
    script_text = (ROOT / "scripts" / "chrome_extension_extract_current_tab.mjs").read_text(encoding="utf-8")
    assert "human_intervention_required" in script_text
    assert "visitor_preview" in script_text
    assert "已停止采集" in script_text


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


def assert_check_env_prefers_chrome_extension_route() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_env import recommended_capture_route

    assert recommended_capture_route({"codex_chrome_extension": {"ok": True}})["route"] == "codex_chrome_extension"
    assert recommended_capture_route({"codex_chrome_extension": {"ok": False}})["route"] == "blocked_until_chrome_extension_ready"


def assert_check_env_discovers_versioned_chrome_plugin(tmp_root: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env

    original_base = check_env.CHROME_PLUGIN_BASE
    try:
        check_env.CHROME_PLUGIN_BASE = tmp_root
        older = tmp_root / "1.0.0" / "scripts"
        newer = tmp_root / "26.519.81530" / "scripts"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        for folder in (older, newer):
            (folder / "browser-client.mjs").write_text("", encoding="utf-8")
            (folder / "check-extension-installed.js").write_text("", encoding="utf-8")
        assert check_env.find_chrome_plugin_root() == newer.parent
    finally:
        check_env.CHROME_PLUGIN_BASE = original_base


def assert_config_resolves_platform_defaults() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from config_loader import resolve_runtime_config

    base = {
        "lark_cli_path": "auto",
        "codex_home": "auto",
        "codex_chrome_plugin_base": "auto",
        "platform_overrides": {
            "darwin": {"lark_cli_path": "/Users/a1/.npm-global/bin/lark-cli"},
            "windows": {"lark_cli_path": "lark-cli.cmd"},
        },
    }
    mac = resolve_runtime_config(base, platform_name="Darwin", environ={"HOME": "/Users/a1", "PATH": ""})
    assert mac["runtime"]["platform"] == "darwin"
    assert mac["lark_cli_path"] == "/Users/a1/.npm-global/bin/lark-cli"
    assert mac["codex_chrome_plugin_base"] == "/Users/a1/.codex/plugins/cache/openai-bundled/chrome"

    windows = resolve_runtime_config(
        base,
        platform_name="Windows",
        environ={"USERPROFILE": r"C:\Users\ops", "PATH": ""},
    )
    assert windows["runtime"]["platform"] == "windows"
    assert windows["lark_cli_path"] == "lark-cli.cmd"
    assert windows["codex_chrome_plugin_base"].endswith(".codex/plugins/cache/openai-bundled/chrome")

    explicit = resolve_runtime_config(
        {"lark_cli_path": r"%USERPROFILE%\bin\lark-cli.cmd", "codex_home": r"%USERPROFILE%\.codex"},
        platform_name="Windows",
        environ={"USERPROFILE": r"C:\Users\ops", "PATH": ""},
    )
    assert explicit["lark_cli_path"] == r"C:\Users\ops\bin\lark-cli.cmd"
    assert explicit["codex_home"] == r"C:\Users\ops\.codex"


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
            "article_summary": "这是文章概要",
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
"""
    result = run(["node", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_comments_and_shares_are_output_as_engagement() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post, output_row

    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/engagement",
            "posted_at": "2026年5月27日 14:03",
            "article_summary": "文章概要",
            "reactions": "81",
            "comments": "29",
            "shares": "3",
        }
    )
    row = output_row(post)
    assert "点赞量：81" in row[5]
    assert "评论数：29" in row[5]
    assert "分享数：3" in row[5]


def assert_output_rows_follow_feishu_headers() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from field_schema import output_row_for_headers

    post = {
        "account_name": "Story Hub",
        "account_type": "competitor",
        "post_url": "https://facebook.com/story/posts/1",
        "post_type": "reel",
        "posted_at": "2026年5月27日 14:03",
        "landing_url": "https://story.example/article",
        "story_summary": "文章概要",
        "likes": 81,
        "views": 120000,
        "comments": 29,
        "shares": 3,
    }
    current_headers = [
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
    row = output_row_for_headers(post, current_headers)
    assert row == [
        "Story Hub",
        "competitor",
        "https://facebook.com/story/posts/1",
        "reel",
        "2026年5月27日 14:03",
        "https://story.example/article",
        "文章概要",
        81,
        120000,
        "",
        "",
    ]

    shuffled_headers = ["文章链接", "账号", "浏览量", "帖子链接", "故事概要"]
    assert output_row_for_headers(post, shuffled_headers) == [
        "https://story.example/article",
        "Story Hub",
        120000,
        "https://facebook.com/story/posts/1",
        "文章概要",
    ]
    legacy_headers = ["互动数据（浏览量、点赞量）"]
    assert output_row_for_headers(post, legacy_headers) == ["浏览量：120000；点赞量：81；评论数：29；分享数：3"]

    estimated = {
        **post,
        "posted_at": "2026年5月28日 13:00",
        "time_source": "relative_estimated",
    }
    assert output_row_for_headers(estimated, ["发帖时间"]) == ["约2026年5月28日 13:00"]


def assert_read_accounts_uses_header_roles() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import read_accounts

    payload = {
        "data": {
            "valueRange": {
                "values": [
                    ["主页名称", "内部FB账户", "备注", "竞品fb账户"],
                    [
                        "Story Hub",
                        [{"link": "https://facebook.com/internal", "text": "internal"}],
                        "",
                        [{"link": "https://facebook.com/competitor", "text": "competitor"}],
                    ],
                ]
            }
        }
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload, ensure_ascii=False)
        stderr = ""

    original = read_accounts.read_source_range
    try:
        read_accounts.read_source_range = lambda _config, range_expr: Result()
        accounts = read_accounts.read_accounts({"feishu": {"sheets": {"accounts": "accounts"}}})
    finally:
        read_accounts.read_source_range = original

    assert accounts == [
        {
            "account_name": "Story Hub",
            "account_url": "https://facebook.com/internal",
            "account_type": "internal",
            "enabled": True,
            "note": "飞书账号配置：internal",
        },
        {
            "account_name": "Story Hub",
            "account_url": "https://facebook.com/competitor",
            "account_type": "competitor",
            "enabled": True,
            "note": "飞书账号配置：competitor",
        },
    ]


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


def assert_sync_allows_estimated_relative_time_with_marker(tmp_path: Path) -> None:
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
                        "article_summary": "文章来源概要",
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
    assert sync.returncode == 0, sync.stdout
    assert '"ready_for_output": 1' in sync.stdout
    assert '"rows": 1' in sync.stdout


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
                        "article_summary": "文章来源概要",
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
                        "article_summary": "文章来源概要",
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
                        "article_summary": "文章来源概要",
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
} from './scripts/chrome_extension_verify_exact_time.mjs';

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

    no_run = run(["node", "scripts/chrome_extension_verify_exact_time.mjs", "--self-test"])
    assert no_run.returncode == 0
    assert no_run.stdout == ""


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
                        "article_summary": "可用概要",
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
            "--sync",
            "--dry-run",
        ]
    )
    assert sync.returncode == 0, sync.stdout
    assert '"ready_for_output": 1' in sync.stdout
    assert '"needs_enrichment_skipped": 1' in sync.stdout


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


def main() -> int:
    assert_url_canonicalization()
    assert_exact_time_parsing_and_relative_time_estimation()
    assert_comments_and_shares_are_output_as_engagement()
    assert_mobile_dom_extractor_can_see_story_links()
    assert_dom_extractor_does_not_treat_story_clock_as_post_time()
    assert_dom_extractor_excludes_profile_shell_with_external_link()
    assert_dom_extractor_blocks_visitor_preview()
    assert_dom_extractor_prefers_parent_post_over_photo_link()
    assert_chrome_extract_script_requires_human_intervention()
    assert_feishu_writes_require_user_identity()
    assert_check_env_prefers_chrome_extension_route()
    assert_config_resolves_platform_defaults()
    with tempfile.TemporaryDirectory() as plugin_tmp:
        assert_check_env_discovers_versioned_chrome_plugin(Path(plugin_tmp))
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
        assert_sync_allows_estimated_relative_time_with_marker(tmp_path)
        assert_sync_retry_includes_previously_inserted_ready_rows(tmp_path)
        assert_article_url_alone_does_not_qualify_lead_link(tmp_path)
        assert_filter_sync_applies_output_quality_gate(tmp_path)
        assert_prepare_capture_has_no_base_time_argument()
        assert_exact_time_verifier_summary_contract()
        assert_prepare_capture_keeps_photo_media_links_as_candidates(tmp_path)
        assert_prepare_capture_does_not_alert_media_when_parent_post_is_captured(tmp_path)
        assert_article_material_extractor(tmp_path)

    print("local pipeline acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
