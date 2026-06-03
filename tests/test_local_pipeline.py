#!/usr/bin/env python3
"""Local acceptance tests for the Mac-first MVP pipeline."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
VALID_CN_SUMMARY = "这篇故事围绕家庭矛盾和财产冲突展开，主角发现亲人试图夺走资产后及时反击，形成适合短剧改编的反转剧情。"


def run(command: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)


class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class OpenCLIStatusHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "ok": True,
            "daemonVersion": "1.8.1",
            "extensionConnected": True,
            "profileRequired": False,
            "profileDisconnected": False,
            "profiles": [{"id": "test", "connected": True}],
            "pending": 0,
            "commandResultUnknown": 0,
            "port": self.server.server_port,
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_static_http_server(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = partial(QuietHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def start_opencli_status_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), OpenCLIStatusHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def assert_url_canonicalization() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import canonicalize_post_url, facebook_content_key

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
    assert (
        canonicalize_post_url("https://www.facebook.com/themeaningoflife88/photos/a.123/9876543212345678/?type=3")
        == "https://facebook.com/photo/9876543212345678"
    )
    assert (
        facebook_content_key("https://www.facebook.com/photo.php?fbid=9876543212345678&set=p.9876543212345678")
        == facebook_content_key("https://www.facebook.com/themeaningoflife88/photos/a.123/9876543212345678/?type=3")
    )
    assert (
        canonicalize_post_url("https://www.facebook.com/storyhub/videos/1234567890123456/?ref=embed_video")
        == "https://facebook.com/video/1234567890123456"
    )
    assert (
        facebook_content_key("https://www.facebook.com/watch/?v=1234567890123456")
        == facebook_content_key("https://www.facebook.com/storyhub/videos/1234567890123456/?ref=embed_video")
    )
    assert (
        canonicalize_post_url("https://www.facebook.com/groups/778899/posts/112233445566")
        == "https://facebook.com/groups/778899/posts/112233445566"
    )
    assert (
        canonicalize_post_url("https://www.facebook.com/share/p/abcDEF123/?mibextid=wwXIfr")
        == "https://facebook.com/share/p/abcDEF123"
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


def assert_dom_extractor_splits_multi_post_container() -> None:
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
  get textContent() { return this.innerText; }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) { return this.attrs[name] || ''; }
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
const postBlock = (id, time, story) => new Node('div', {}, [
  new Node('a', { href: `/LessonsTaughtByLifepage/posts/${id}` }, [], time),
  new Node('p', {}, [], story),
  new Node('a', { href: `https://kaylestore.net/story-${id}` }, [], 'Read more'),
  new Node('span', {}, [], 'Like'),
  new Node('span', {}, [], 'Comment'),
  new Node('span', {}, [], 'Share')
]);
const container = new Node('div', { role: 'article' }, [
  postBlock('1001', '46m', 'Doctors reveal a breakfast habit that protects bones and joints.'),
  postBlock('1002', '3h', 'A bride discovers a hidden document before her wedding and changes everything.'),
  postBlock('1003', '4h', 'A daughter finds the truth about the family house and fights back.')
]);
const body = new Node('body', {}, [
  new Node('h1', {}, [], 'Lessons Taught By Life'),
  container
]);
global.document = {
  title: 'Lessons Taught By Life | Facebook',
  body,
  querySelectorAll: (selector) => body.querySelectorAll(selector)
};
global.location = new URL('https://www.facebook.com/LessonsTaughtByLifepage');
const result = eval(browserExpression(900));
const urls = [...new Set(result.candidates.map((item) => item.post_url))];
if (urls.length !== 3 || result.candidates.length !== 3) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
}
if (!result.candidates.every((item) => item.source_split === 'time_anchor')) {
  console.error(JSON.stringify(result.candidates, null, 2));
  process.exit(2);
}
const first = result.candidates.find((item) => item.post_url.includes('/1001'));
if (!first || first.story_summary.includes('A bride discovers') || first.story_summary.includes('family house')) {
  console.error(JSON.stringify(result.candidates, null, 2));
  process.exit(3);
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


def assert_dom_extractor_keeps_path_photo_without_parent_post() -> None:
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
  get textContent() { return this.innerText; }
  get href() {
    if (!this.attrs.href) return '';
    return new URL(this.attrs.href, global.location.href).href;
  }
  getAttribute(name) { return this.attrs[name] || ''; }
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
  new Node('a', { href: '/themeaningoflife88/photos/a.123/9876543212345678/?type=3' }, [], '2h'),
  new Node('p', {}, [], 'A photo story reveals a hidden family secret after dinner.'),
  new Node('span', {}, [], '27 comments'),
  new Node('span', {}, [], '5 shares')
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
if (!candidate.post_url.includes('/photos/a.123/9876543212345678/')) {
  console.error(JSON.stringify(candidate, null, 2));
  process.exit(2);
}
if (candidate.selected_post_link_kind !== 'media' || candidate.media_link_count !== 1) {
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
    assert "function createOpenedTabTracker" in script_text
    assert '"tab", "close", tab.page' in script_text
    assert "closeEnabled" in script_text
    assert "tabs.reverse()" in script_text


def assert_opencli_runtime_requires_matching_account_tab() -> None:
    script = """
import { ensureFacebookTab } from './scripts/opencli_runtime.mjs';

const calls = [];
async function runCommand(args) {
  calls.push(args);
  if (args.slice(0, 3).join(' ') === 'browser fb-competitor bind') {
    return { ok: true, stdout: '{}', stderr: '' };
  }
  if (args.slice(0, 4).join(' ') === 'browser fb-competitor tab list') {
    return {
      ok: true,
      stdout: JSON.stringify([
        { page: 'wrong-page', url: 'https://www.facebook.com/wrongaccount', title: 'Wrong Account' }
      ]),
      stderr: ''
    };
  }
  return { ok: false, stdout: '', stderr: 'unexpected command' };
}

const result = await ensureFacebookTab({
  opencliCommand: ['opencli'],
  session: 'fb-competitor',
  accountUrl: 'https://www.facebook.com/targetaccount',
  runCommand,
});

if (result.ok || result.status !== 'facebook_tab_missing' || !result.account_url.includes('targetaccount')) {
  console.error(JSON.stringify(result, null, 2));
  process.exit(2);
}
if (!/目标账号/.test(result.message)) {
  console.error(result.message);
  process.exit(3);
}
"""
    result = run(["node", "--input-type=module", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_tab_tracker_closes_only_registered_tabs() -> None:
    script = """
import { createOpenedTabTracker } from './scripts/opencli_runtime.mjs';
const calls = [];
const tracker = createOpenedTabTracker({
  opencliCommand: ['opencli'],
  session: 'fb-competitor',
  runCommand: async (args, options) => {
    calls.push({ args, options });
    return { ok: args.at(-1) !== 'keep-open', stdout: '', stderr: args.at(-1) === 'keep-open' ? 'close failed' : '' };
  },
});
tracker.remember({ page: 'detail-a', url: 'https://www.facebook.com/a' }, { role: 'detail_page' });
tracker.remember({ page: 'keep-open', url: 'https://www.facebook.com/b' }, { role: 'detail_page' });
const summary = await tracker.closeAll();
if (summary.opened !== 2 || summary.closed !== 1 || summary.failed !== 1 || summary.kept_open !== 1) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(2);
}
if (calls.length !== 2 || !calls.every((call) => call.args.slice(0, 4).join(' ') === 'browser fb-competitor tab close')) {
  console.error(JSON.stringify(calls, null, 2));
  process.exit(3);
}
const disabled = createOpenedTabTracker({
  opencliCommand: ['opencli'],
  session: 'fb-competitor',
  closeEnabled: false,
  runCommand: async () => {
    throw new Error('disabled tracker should not close tabs');
  },
});
disabled.remember({ page: 'debug-tab', url: 'https://www.facebook.com/debug' });
const disabledSummary = await disabled.closeAll();
if (disabledSummary.opened !== 1 || disabledSummary.closed !== 0 || disabledSummary.kept_open !== 1) {
  console.error(JSON.stringify(disabledSummary, null, 2));
  process.exit(4);
}
"""
    result = run(["node", "--input-type=module", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_detail_session_lock_recovers_stale_files() -> None:
    script = """
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { acquireSessionLock } from './scripts/opencli_enrich_post_details.mjs';

const session = `unit-stale-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const lockPath = path.join(os.tmpdir(), `fb-competitor-opencli-${session}.lock`);
fs.writeFileSync(lockPath, JSON.stringify({ pid: 99999999, started_at: '2000-01-01T00:00:00.000Z' }));
const recovered = acquireSessionLock(session);
if (!recovered.ok || !recovered.recovered || recovered.previous?.reason !== 'dead_pid') {
  console.error(JSON.stringify(recovered, null, 2));
  process.exit(2);
}
const busy = acquireSessionLock(session);
if (busy.ok || busy.stale?.stale) {
  console.error(JSON.stringify(busy, null, 2));
  process.exit(3);
}
recovered.release();
const fresh = acquireSessionLock(session);
if (!fresh.ok || fresh.recovered) {
  console.error(JSON.stringify(fresh, null, 2));
  process.exit(4);
}
fresh.release();
"""
    result = run(["node", "--input-type=module", "-e", script])
    assert result.returncode == 0, result.stderr or result.stdout


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
    assert "createOpenedTabTracker" in script_text
    assert "openedTabTracker.closeAll()" in script_text
    assert "finally" in script_text
    assert "tab_cleanup" in script_text
    assert "--keep-opened-tabs" in script_text


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
    from lark_io import ensure_user_identity, require_user_identity
    import lark_io

    original = lark_io.run_lark

    class FakeResult:
        def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    try:
        def valid_run(_config, args):
            if args == ["config", "default-as"]:
                return FakeResult("default-as: user")
            if args == ["config", "strict-mode"]:
                return FakeResult("strict-mode: user")
            return FakeResult(json.dumps({"identity": "user", "tokenStatus": "valid", "userName": "tester"}))

        lark_io.run_lark = valid_run
        assert require_user_identity({"lark_cli_path": "fake"})["identity"] == "user"

        calls = []

        def refresh_run(_config, args):
            calls.append(args)
            if args == ["config", "default-as"]:
                return FakeResult("default-as: user")
            if args == ["config", "strict-mode"]:
                return FakeResult("strict-mode: user")
            if args == ["doctor"]:
                return FakeResult(json.dumps({"ok": True}))
            auth_count = sum(1 for item in calls if item == ["auth", "status"])
            if auth_count == 1:
                return FakeResult(json.dumps({"identity": "user", "tokenStatus": "needs_refresh", "userName": "tester"}))
            return FakeResult(json.dumps({"identity": "user", "tokenStatus": "valid", "userName": "tester"}))

        lark_io.run_lark = refresh_run
        refreshed = ensure_user_identity({"lark_cli_path": "fake"})
        assert refreshed["tokenStatus"] == "valid"
        assert refreshed["_auth_recovery"]["attempted"] is True
        assert ["doctor"] in calls

        def bot_run(_config, args):
            if args == ["config", "default-as"]:
                return FakeResult("default-as: user")
            if args == ["config", "strict-mode"]:
                return FakeResult("strict-mode: user")
            return FakeResult(json.dumps({"identity": "bot", "tokenStatus": "valid"}))

        lark_io.run_lark = bot_run
        try:
            require_user_identity({"lark_cli_path": "fake"})
        except RuntimeError as exc:
            assert "有效用户身份" in str(exc)
        else:
            raise AssertionError("bot identity must be rejected")

        failed_calls = []

        def failed_refresh_run(_config, args):
            failed_calls.append(args)
            if args == ["config", "default-as"]:
                return FakeResult("default-as: user")
            if args == ["config", "strict-mode"]:
                return FakeResult("strict-mode: user")
            if args == ["doctor"]:
                return FakeResult(json.dumps({"ok": False}))
            if args[:4] == ["auth", "login", "--json", "--no-wait"]:
                return FakeResult(json.dumps({"verification_uri": "https://example.test/verify"}))
            return FakeResult(json.dumps({
                "identity": "user",
                "tokenStatus": "needs_refresh",
                "scope": "sheets:spreadsheet:read sheets:spreadsheet:write_only",
            }))

        lark_io.run_lark = failed_refresh_run
        try:
            ensure_user_identity({"lark_cli_path": "fake"})
        except RuntimeError as exc:
            assert "已自动发起设备登录" in str(exc)
            assert "verification_uri" in str(exc)
        else:
            raise AssertionError("unrefreshed token must block real write")

        login_calls = []

        def missing_user_run(_config, args):
            login_calls.append(args)
            if args == ["config", "default-as"]:
                return FakeResult("default-as: user")
            if args == ["config", "strict-mode"]:
                return FakeResult("strict-mode: user")
            if args[:4] == ["auth", "login", "--json", "--no-wait"]:
                return FakeResult(json.dumps({"verification_uri": "https://example.test/login"}))
            return FakeResult(json.dumps({"identity": "", "tokenStatus": "missing"}))

        lark_io.run_lark = missing_user_run
        try:
            ensure_user_identity({"lark_cli_path": "fake"})
        except RuntimeError as exc:
            assert "已自动发起设备登录" in str(exc)
            assert "https://example.test/login" in str(exc)
        else:
            raise AssertionError("missing user login must start device auth and block write")
        assert any(args[:4] == ["auth", "login", "--json", "--no-wait"] for args in login_calls)
    finally:
        lark_io.run_lark = original


def assert_cli_feishu_auth_blockers_report_run_status(tmp_path: Path) -> None:
    fake_lark = tmp_path / "fake-lark-cli"
    config = tmp_path / "settings_auth_blockers.yaml"
    sample = tmp_path / "auth_blocker_posts.json"
    db_path = tmp_path / "auth-blockers.sqlite"
    fake_lark.write_text(
        """#!/bin/sh
if [ "$1" = "config" ]; then
  echo "$2: user"
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  echo '{"identity":"bot","tokenStatus":"valid"}'
  exit 0
fi
echo '{}'
exit 0
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Auth Blocked",
                        "account_url": "https://www.facebook.com/authblocked",
                        "post_url": "https://www.facebook.com/authblocked/posts/one",
                        "relative_time_text": "1h",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import_cmd = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
        ]
    )
    assert import_cmd.returncode == 1, import_cmd.stdout
    import_data = json.loads(import_cmd.stdout)
    assert import_data["run_status"] == "blocked_auth"
    assert import_data["complete"] is False
    assert import_data["stage"] == "feishu_auth_preflight"
    assert "next_actions" in import_data
    assert import_data["completion_blockers"][0]["code"] == "blocked_auth"

    filter_cmd = run([PYTHON, "scripts/filter_posts.py", "--config", str(config), "--sync"])
    assert filter_cmd.returncode == 1, filter_cmd.stdout
    filter_data = json.loads(filter_cmd.stdout)
    assert filter_data["feishu_sync"]["run_status"] == "blocked_auth"
    assert filter_data["feishu_sync"]["complete"] is False
    assert filter_data["feishu_sync"]["stage"] == "feishu_auth_preflight"
    assert "next_actions" in filter_data["feishu_sync"]
    assert filter_data["feishu_sync"]["completion_blockers"][0]["code"] == "blocked_auth"

    sync_cmd = run([PYTHON, "scripts/sync_feishu.py", "--config", str(config)])
    assert sync_cmd.returncode == 1, sync_cmd.stdout
    sync_data = json.loads(sync_cmd.stdout)
    assert sync_data["run_status"] == "blocked_auth"
    assert sync_data["complete"] is False
    assert sync_data["stage"] == "feishu_auth_preflight"
    assert "next_actions" in sync_data
    assert sync_data["completion_blockers"][0]["code"] == "blocked_auth"


def assert_import_existing_result_reports_structured_input_failures(tmp_path: Path) -> None:
    config = tmp_path / "settings_import_failures.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'import-failures.sqlite'}"
        ),
        encoding="utf-8",
    )

    cases: list[tuple[str, Path]] = [
        ("missing", tmp_path / "missing.json"),
        ("malformed_json", tmp_path / "malformed.json"),
        ("bad_shape", tmp_path / "bad_shape.json"),
        ("unsupported_suffix", tmp_path / "unsupported.txt"),
    ]
    cases[1][1].write_text('{"posts": [', encoding="utf-8")
    cases[2][1].write_text(json.dumps({"posts": {"not": "a-list"}}), encoding="utf-8")
    cases[3][1].write_text("post_url=https://www.facebook.com/x/posts/1", encoding="utf-8")

    for case_name, input_path in cases:
        result = run(
            [
                PYTHON,
                "scripts/import_existing_result.py",
                "--config",
                str(config),
                "--input",
                str(input_path),
                "--no-sync",
            ]
        )
        assert result.returncode == 1, f"{case_name}: {result.stdout}\n{result.stderr}"
        assert "Traceback" not in result.stderr
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["stage"] == "input_load"
        assert data["run_status"] == "import_failed"
        assert data["complete"] is False
        assert data["input_path"] == str(input_path)
        assert data["config_path"] == str(config)
        assert data["next_actions"]


def assert_prepare_capture_reports_structured_input_failures(tmp_path: Path) -> None:
    output = tmp_path / "prepared_failures.json"
    cases: list[tuple[str, Path]] = [
        ("missing", tmp_path / "missing_raw.json"),
        ("malformed_json", tmp_path / "malformed_raw.json"),
        ("bad_shape", tmp_path / "bad_shape_raw.json"),
    ]
    cases[1][1].write_text('{"posts": [', encoding="utf-8")
    cases[2][1].write_text(json.dumps({"posts": {"not": "a-list"}}), encoding="utf-8")

    for case_name, input_path in cases:
        result = run(
            [
                PYTHON,
                "scripts/prepare_capture_result.py",
                "--input",
                str(input_path),
                "--output",
                str(output),
                "--target-date",
                "260603",
            ]
        )
        assert result.returncode == 1, f"{case_name}: {result.stdout}\n{result.stderr}"
        assert "Traceback" not in result.stderr
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["stage"] == "input_load"
        assert data["run_status"] == "prepare_failed"
        assert data["complete"] is False
        assert data["input_path"] == str(input_path)
        assert data["output_path"] == str(output)
        assert data["next_actions"]


def assert_article_summary_scripts_report_structured_input_failures(tmp_path: Path) -> None:
    config = tmp_path / "settings_article_structured_failures.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'article-structured.sqlite'}"
        ),
        encoding="utf-8",
    )

    article_output = tmp_path / "with_material.json"
    malformed_article_input = tmp_path / "malformed_article_input.json"
    malformed_article_input.write_text('{"posts": [', encoding="utf-8")
    article_result = run(
        [
            PYTHON,
            "scripts/enrich_article_summaries.py",
            "--config",
            str(config),
            "--input",
            str(malformed_article_input),
            "--output",
            str(article_output),
        ]
    )
    assert article_result.returncode == 1, article_result.stdout + article_result.stderr
    assert "Traceback" not in article_result.stderr
    article_data = json.loads(article_result.stdout)
    assert article_data["run_status"] == "article_material_failed"
    assert article_data["stage"] == "input_load"
    assert article_data["complete"] is False
    assert article_data["input_path"] == str(malformed_article_input)
    assert article_data["output_path"] == str(article_output)
    assert not article_output.exists()

    summaries_output = tmp_path / "summary_applied.json"
    valid_posts = tmp_path / "valid_posts.json"
    valid_posts.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/one",
                        "article_url": "https://example.com/story-one",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bad_summaries = tmp_path / "bad_summaries.json"
    bad_summaries.write_text('{"https://example.com/story-one": ', encoding="utf-8")
    summary_result = run(
        [
            PYTHON,
            "scripts/apply_article_summaries.py",
            "--input",
            str(valid_posts),
            "--summaries",
            str(bad_summaries),
            "--output",
            str(summaries_output),
        ]
    )
    assert summary_result.returncode == 1, summary_result.stdout + summary_result.stderr
    assert "Traceback" not in summary_result.stderr
    summary_data = json.loads(summary_result.stdout)
    assert summary_data["run_status"] == "summary_apply_failed"
    assert summary_data["stage"] == "summaries_load"
    assert summary_data["complete"] is False
    assert summary_data["summaries_path"] == str(bad_summaries)
    assert summary_data["input_path"] == str(valid_posts)
    assert summary_data["output_path"] == str(summaries_output)
    assert not summaries_output.exists()

    valid_summaries = tmp_path / "valid_summaries.json"
    valid_summaries.write_text(json.dumps({"https://example.com/story-one": VALID_CN_SUMMARY}, ensure_ascii=False), encoding="utf-8")
    bad_file_input = tmp_path / "bad_file_input.json"
    bad_file_input.write_text(json.dumps({"posts": {"not": "a-list"}}, ensure_ascii=False), encoding="utf-8")
    input_result = run(
        [
            PYTHON,
            "scripts/apply_article_summaries.py",
            "--input",
            str(bad_file_input),
            "--summaries",
            str(valid_summaries),
            "--output",
            str(summaries_output),
        ]
    )
    assert input_result.returncode == 1, input_result.stdout + input_result.stderr
    assert "Traceback" not in input_result.stderr
    input_data = json.loads(input_result.stdout)
    assert input_data["run_status"] == "summary_apply_failed"
    assert input_data["stage"] == "input_load"
    assert input_data["input_path"] == str(bad_file_input)
    assert not summaries_output.exists()


def assert_check_env_prefers_opencli_route() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_env import recommended_capture_route

    ready = recommended_capture_route({"opencli_browser_bridge": {"ok": True, "next_actions": ["run account job"]}})
    assert ready["route"] == "opencli_browser_bridge"
    assert ready["blocked"] is False
    assert ready["next_actions"] == ["run account job"]
    blocked = recommended_capture_route(
        {
            "opencli_browser_bridge": {
                "ok": False,
                "status": "browser_bridge_not_connected",
                "blocking_issue": "browser_bridge_not_connected",
                "next_actions": ["install extension"],
            }
        }
    )
    assert blocked["route"] == "blocked_until_opencli_ready"
    assert blocked["blocked"] is True
    assert blocked["blocking_issue"] == "browser_bridge_not_connected"
    assert blocked["next_actions"] == ["install extension"]


def assert_check_env_reports_opencli_route_status() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env

    assert check_env.version_ok("1.8.0") is True
    assert check_env.version_ok("1.7.9") is False
    missing = check_env.check_opencli(["/definitely/missing/opencli"], daemon_port=9)
    assert missing["status"] == "opencli_missing"
    assert missing["ok"] is False
    assert missing["operator_action_required"] is True
    assert missing["blocking_issue"] == "opencli_missing"
    assert missing["next_actions"]
    original_read = check_env.read_opencli_daemon_status
    original_run = check_env.run_opencli_command
    original_check = check_env.check_invocation
    calls = []

    try:
        check_env.check_invocation = lambda command: {
            "command": command,
            "path": command[0],
            "resolved_path": command[0],
            "exists": True,
            "ok": True,
            "stdout": "1.8.1",
            "stderr": "",
        }
        check_env.read_opencli_daemon_status = lambda _port: {"ok": False, "error": "connection refused"}

        def fake_run(command, args, timeout=20):
            calls.append((command, args, timeout))
            return {"ok": True, "returncode": 0, "stdout": "doctor ok", "stderr": ""}

        check_env.run_opencli_command = fake_run
        fixed = check_env.check_opencli(["opencli"], daemon_port=19825, auto_fix=True)
        assert fixed["auto_fix_attempted"] is True
        assert fixed["auto_fix_steps"][0]["step"] == "opencli_doctor"
        assert calls and calls[0][1] == ["doctor"]

        check_env.read_opencli_daemon_status = lambda _port: {
            "ok": True,
            "status": {"ok": True, "extensionConnected": False},
        }
        bridge_blocked = check_env.check_opencli(["opencli"], daemon_port=19825, auto_fix=False)
        assert bridge_blocked["status"] == "browser_bridge_not_connected"
        assert bridge_blocked["operator_action_required"] is True
        assert bridge_blocked["blocking_issue"] == "browser_bridge_not_connected"
        assert any("chrome://extensions" in action for action in bridge_blocked["next_actions"])
    finally:
        check_env.read_opencli_daemon_status = original_read
        check_env.run_opencli_command = original_run
        check_env.check_invocation = original_check


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


def assert_time_confirmed_string_false_is_not_ready() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from output_quality import output_quality_errors

    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/string-false-time",
            "posted_at": "2026年5月27日 10:00",
            "time_confirmed": "false",
            "time_source": "dom_aria_label",
            "article_url": "https://site.test/story",
            "landing_url": "https://site.test/story",
            "lead_url_raw": "https://site.test/story",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "article_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "output_status": "ready_for_output",
        }
    )
    assert post["time_confirmed"] is False
    assert post["output_status"] != "ready_for_output"
    errors = output_quality_errors([{**post, "output_status": "ready_for_output"}])
    assert errors
    assert "unconfirmed_or_estimated_posted_at" in errors[0]["errors"]


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


def assert_field_audit_marks_refetchable_missing_fields(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from field_audit import audit_post_fields
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, pending_enrichment_tasks, row_for_post, upsert_post

    config = {
        "quality_audit": {
            "required_engagement_fields": ["likes", "comments", "shares"],
            "low_like_threshold": 5,
            "required_post_types": ["图文", "视频", "仅图片", "仅文字"],
            "assume_lead_link_exists": True,
        }
    }
    missing = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/audit-missing",
            "posted_at": "2026年5月27日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://site.test/story",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 2,
        }
    )
    audit = audit_post_fields(missing, config)
    assert audit["field_audit_status"] == "needs_refetch"
    assert audit["field_audit_reasons"] == ["lead_link", "comments", "shares", "likes_low", "post_type"]
    assert audit["refetch_stages"] == ["lead_link", "engagement", "post_type"]
    assert "待补抓：引流链接、评论数、分享数、点赞数异常低、帖子类型" == audit["field_audit_note"]

    no_summary = {**missing, "story_summary": "", "summary_source": "pending_article_summary"}
    no_summary_audit = audit_post_fields(no_summary, config)
    assert "article_summary" in no_summary_audit["field_audit_reasons"]
    assert "summary" in no_summary_audit["refetch_stages"]

    good = {
        **missing,
        "post_type": "图文",
        "lead_url_raw": "https://site.test/story",
        "landing_url": "https://site.test/story",
        "lead_link_status": "qualified",
        "lead_link_source": "comment",
        "summary_source": "article",
        "likes": 6,
        "comments": 3,
        "shares": 1,
    }
    assert audit_post_fields(good, config)["field_audit_status"] == "passed"

    conn = connect(tmp_path / "field-audit.sqlite")
    upsert_post(conn, missing)
    stored = row_for_post(conn, missing)
    assert stored is not None
    assert stored["field_audit_status"] == "needs_refetch"
    assert "likes_low" in stored["field_audit_reasons"]
    enqueue_enrichment_tasks_for_posts(conn, [stored])
    stages = sorted(task["stage"] for task in pending_enrichment_tasks(conn, limit=20))
    assert stages == ["engagement", "lead_link", "post_type"]


def assert_audit_marker_is_written_to_adoption_status() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from field_schema import output_row_for_headers

    headers = ["帖子链接", "是否采用"]
    post = {
        "post_url": "https://facebook.com/example/posts/audit-marker",
        "field_audit_reasons": '["lead_link", "comments", "shares", "post_type"]',
    }
    row = output_row_for_headers(post, headers)
    assert row == [
        "https://facebook.com/example/posts/audit-marker",
        "待补抓：引流链接、评论数、分享数、帖子类型",
    ]
    manual = {**post, "adoption_status": "采用"}
    assert output_row_for_headers(manual, headers)[1] == "采用"
    strict_config = {
        "quality_audit": {
            "required_engagement_fields": ["likes"],
            "low_like_threshold": 10,
            "required_post_types": ["视频"],
        }
    }
    strict_post = {
        "post_url": "https://facebook.com/example/posts/strict-marker",
        "posted_at": "2026年6月3日 12:00",
        "time_confirmed": True,
        "time_source": "dom_aria_label",
        "landing_url": "https://story.example/strict-marker",
        "lead_url_raw": "https://story.example/strict-marker",
        "lead_link_status": "qualified",
        "lead_link_source": "comment",
        "story_summary": VALID_CN_SUMMARY,
        "summary_source": "article",
        "likes": 8,
        "post_type": "图文",
        "field_audit_reasons": "",
    }
    assert output_row_for_headers(strict_post, headers, strict_config)[1] == "待补抓：点赞数异常低、帖子类型"
    assert output_row_for_headers({**strict_post, "adoption_status": "不采用"}, headers, strict_config)[1] == "不采用"


def assert_ledger_marker_includes_time_summary_and_coverage() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from field_schema import output_row_for_headers

    headers = ["帖子链接", "发帖时间", "是否采用"]
    post = {
        "post_url": "https://facebook.com/example/posts/ledger",
        "posted_at": "2026年6月2日 14:00",
        "time_source": "relative_estimated",
        "coverage_note": "本次覆盖不完整，需补抓。",
        "field_audit_reasons": '["exact_time", "article_summary", "coverage"]',
    }
    assert output_row_for_headers(post, headers) == [
        "https://facebook.com/example/posts/ledger",
        "约2026年6月2日 14:00",
        "待补抓：精确时间、文章概要、覆盖不足",
    ]


def assert_feishu_upsert_merges_rows_without_overwriting_manual_adoption() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from lark_io import merge_upsert_row, normalized_upsert_key

    headers = ["帖子链接", "故事概要", "是否采用"]
    existing = ["https://facebook.com/post/1", "旧概要", "采用"]
    incoming = ["https://facebook.com/post/1", "新概要", "待补抓：引流链接"]
    assert merge_upsert_row(existing, incoming, headers) == ["https://facebook.com/post/1", "新概要", "采用"]

    existing_marker = ["https://facebook.com/post/2", "旧概要", "待补抓：评论数"]
    incoming_ready = ["https://facebook.com/post/2", "新概要", ""]
    assert merge_upsert_row(existing_marker, incoming_ready, headers) == ["https://facebook.com/post/2", "新概要", ""]
    assert normalized_upsert_key(
        "https://www.facebook.com/storyhub/posts/pfbid123?utm_source=x",
        "post_url",
    ) == normalized_upsert_key("https://facebook.com/storyhub/posts/pfbid123", "post_url")
    assert normalized_upsert_key(
        "https://m.facebook.com/story.php?story_fbid=123&id=456&ref=share",
        "post_url",
    ) == normalized_upsert_key("https://facebook.com/456/posts/123", "post_url")
    assert normalized_upsert_key(
        "https://www.facebook.com/photo.php?fbid=9876543212345678&set=p.9876543212345678",
        "post_url",
    ) == normalized_upsert_key(
        "https://www.facebook.com/themeaningoflife88/photos/a.123/9876543212345678/?type=3",
        "post_url",
    )
    assert normalized_upsert_key(
        "https://www.facebook.com/watch/?v=1234567890123456",
        "post_url",
    ) == normalized_upsert_key(
        "https://www.facebook.com/storyhub/videos/1234567890123456/",
        "post_url",
    )
    assert normalized_upsert_key(
        "https://www.facebook.com/share/p/abcDEF123/?mibextid=wwXIfr",
        "post_url",
    ) == "share:p:abcDEF123"


def assert_feishu_upsert_matches_canonical_post_urls(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import lark_io

    original_require_user_identity = lark_io.require_user_identity
    original_ensure_sheet = lark_io.ensure_sheet
    original_read_range = lark_io.read_range
    original_write_range = lark_io.write_range

    class FakeResult:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    written: dict[str, Any] = {}
    headers = ["账号", "帖子链接", "故事概要", "是否采用"]
    existing = [
        headers,
        ["Story Hub", "https://www.facebook.com/storyhub/posts/pfbid123?utm_source=x", "旧概要", "采用"],
    ]
    incoming = [["Story Hub", "https://facebook.com/storyhub/posts/pfbid123", "新概要", "待补抓：引流链接"]]
    config = {"feishu": {"sheets": {"all_posts": "FB竞品帖子链接"}}}
    try:
        lark_io.require_user_identity = lambda _config: {"identity": "user", "tokenStatus": "valid"}
        lark_io.ensure_sheet = lambda _config, _title: {"ok": True, "sheet": {"sheet_id": "sheet123", "title": "FB竞品帖子链接"}}
        lark_io.read_range = lambda _config, _range: FakeResult(
            json.dumps({"data": {"valueRange": {"values": existing}}}, ensure_ascii=False)
        )

        def fake_write(_config, range_expr, values):
            written["range"] = range_expr
            written["values"] = values
            return FakeResult("{}")

        lark_io.write_range = fake_write
        result = lark_io.upsert_rows(config, "all_posts", incoming, headers=headers, dry_run=False)
        assert result["ok"] is True
        assert result["updated"] == 1
        assert result["inserted"] == 0
        assert written["values"][1] == [
            "Story Hub",
            "https://facebook.com/storyhub/posts/pfbid123",
            "新概要",
            "采用",
        ]
    finally:
        lark_io.require_user_identity = original_require_user_identity
        lark_io.ensure_sheet = original_ensure_sheet
        lark_io.read_range = original_read_range
        lark_io.write_range = original_write_range


def assert_sqlite_upsert_dedupes_equivalent_media_urls(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, row_for_post, upsert_post

    conn = connect(tmp_path / "media-dedupe.sqlite")
    first = normalize_post(
        {
            "account_name": "The meaning of life",
            "post_url": "https://www.facebook.com/photo.php?fbid=9876543212345678&set=p.9876543212345678&type=3",
            "post_time_text": "1h",
            "crawled_at": "2026-06-02T12:00:00",
            "story_summary": "Photo candidate first seen from photo.php",
        }
    )
    second = normalize_post(
        {
            "account_name": "The meaning of life",
            "post_url": "https://www.facebook.com/themeaningoflife88/photos/a.123/9876543212345678/?type=3",
            "posted_at": "2026年6月2日 11:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/photo",
            "landing_url": "https://story.example/photo",
            "lead_url_raw": "https://story.example/photo",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "article_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 8,
            "comments": 2,
            "shares": 1,
        }
    )
    assert first["canonical_post_url"] == second["canonical_post_url"]
    assert upsert_post(conn, first) == "inserted"
    assert upsert_post(conn, second) == "updated"
    rows = conn.execute("SELECT * FROM posts").fetchall()
    assert len(rows) == 1
    stored = row_for_post(conn, second)
    assert stored is not None
    assert stored["posted_at"] == "2026年6月2日 11:00"
    assert stored["lead_link_status"] == "qualified"
    assert stored["likes"] == 8


def assert_sync_feishu_audit_and_strict_modes() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_feishu

    config = {
        "feishu": {
            "sheets": {"all_posts": "FB竞品帖子链接"},
            "field_schema": {"output_headers": ["帖子链接", "是否采用"]},
        }
    }
    incomplete = [
        {
            "account_name": "Example Page",
            "post_url": "https://facebook.com/example/posts/incomplete",
            "output_status": "needs_enrichment",
            "field_audit_reasons": '["exact_time", "lead_link", "article_summary"]',
        }
    ]
    audit = sync_feishu.sync_posts(config, incomplete, "all_posts", "append", True, audit=True)
    assert audit["ok"] is True
    assert audit["dry_run"] is True
    assert audit["audit_output"] is True
    assert audit["output_candidates"] == 1
    assert audit["keys"] == ["https://facebook.com/example/posts/incomplete"]
    assert audit["audit_missing_field_counts"] == {
        "article_summary": 1,
        "comments": 1,
        "exact_time": 1,
        "lead_link": 1,
        "likes": 1,
        "post_type": 1,
        "shares": 1,
    }
    assert audit["audit_missing_field_summary"][0]["label"] == "文章概要"
    assert "文章概要：1 条" in audit["audit_missing_field_notes"]
    assert "评论数：1 条" in audit["audit_missing_field_notes"]
    synced = [{**incomplete[0], "output_status": "output_synced"}]
    audit_synced = sync_feishu.sync_posts(config, synced, "all_posts", "append", True, audit=True)
    assert audit_synced["ok"] is True
    assert audit_synced["output_candidates"] == 1
    assert audit_synced["keys"] == ["https://facebook.com/example/posts/incomplete"]

    strict = sync_feishu.sync_posts(config, incomplete, "all_posts", "append", True, audit=False)
    assert strict["ok"] is False
    assert strict["stage"] == "quality_gate"
    assert strict["ready_for_output"] == 0
    assert strict["needs_enrichment_skipped"] == 1
    assert strict["next_actions"]


def assert_sync_failures_include_recovery_actions() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_feishu
    import sync_status

    quality = sync_status.annotate_sync_result(
        {"ok": False, "stage": "quality_gate", "ready_for_output": 0},
        {"post_count": 1, "next_actions": []},
        ledger_mode=False,
    )
    assert quality["complete"] is False
    assert quality["run_status"] == "quality_gate"
    assert quality["next_actions"]
    assert "精确时间" in quality["next_actions"][0]

    empty_audit = sync_status.annotate_sync_result(
        {"ok": False, "stage": "audit_output_gate", "output_candidates": 0},
        {"post_count": 0, "next_actions": []},
        ledger_mode=True,
    )
    assert empty_audit["complete"] is False
    assert empty_audit["run_status"] == "audit_output_gate"
    assert "主页顶部" in empty_audit["next_actions"][0]

    original_write_rows = sync_feishu.write_rows
    try:
        sync_feishu.write_rows = lambda *_args, **_kwargs: {
            "ok": False,
            "stage": "feishu_write",
            "stderr": "simulated write failure",
            "rows": 1,
        }
        config = {
            "feishu": {
                "sheets": {"all_posts": "FB竞品帖子链接"},
                "field_schema": {"output_headers": ["帖子链接", "是否采用"]},
            }
        }
        result = sync_feishu.sync_posts(
            config,
            [
                {
                    "account_name": "Example Page",
                    "post_url": "https://facebook.com/example/posts/write-fail",
                    "output_status": "needs_enrichment",
                }
            ],
            "all_posts",
            "append",
            False,
            audit=True,
            conn=None,
        )
    finally:
        sync_feishu.write_rows = original_write_rows
    assert result["ok"] is False
    assert result["stage"] == "feishu_write"
    assert result["run_status"] == "sync_failed"
    assert result["complete"] is False
    assert result["next_actions"]
    assert "SQLite" in result["next_actions"][0]


def assert_sync_status_marks_incomplete_ledger(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_feishu
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, row_for_post, upsert_post

    conn = connect(tmp_path / "sync-status.sqlite")
    post = normalize_post(
        {
            "account_name": "Example Page",
            "account_url": "https://www.facebook.com/example",
            "post_url": "https://www.facebook.com/example/posts/incomplete-ledger",
            "post_time_text": "1h",
            "story_summary": "Visible homepage candidate.",
            "coverage_note": "采集达到快照上限时仍有新增候选，本次覆盖不完整，需从主页顶部继续补抓。",
            "crawled_at": "2026-06-02T12:00:00",
        }
    )
    upsert_post(conn, post)
    stored = row_for_post(conn, post)
    assert stored is not None
    enqueue_enrichment_tasks_for_posts(conn, [stored])

    config = {
        "feishu": {
            "sheets": {"all_posts": "FB竞品帖子链接"},
            "field_schema": {"output_headers": ["帖子链接", "是否采用"]},
        }
    }
    result = sync_feishu.sync_posts(config, [stored], "all_posts", "append", True, audit=True, conn=conn)
    assert result["ok"] is True
    assert result["run_status"] == "synced_ledger_incomplete"
    assert result["complete"] is False
    blocker_codes = [item["code"] for item in result["completion_blockers"]]
    assert "coverage_incomplete" in blocker_codes
    assert "field_gaps" in blocker_codes
    assert "ledger_not_final" in blocker_codes
    completion = result["enrichment_completion"]
    assert completion["post_count"] == 1
    assert completion["ledger_candidate_count"] == 1
    assert completion["ledger_usable_rate"] == 1.0
    assert completion["final_usable_rate"] == 0.0
    assert completion["completion_rate"] == 0.0
    assert completion["coverage_complete"] is False
    assert completion["coverage_health"] == "incomplete"
    assert completion["coverage_incomplete_count"] == 1
    assert completion["open_task_count"] > 0
    assert completion["missing_stage_counts"]["coverage"] == 1
    assert completion["field_gap_counts"]["exact_time"] == 1
    assert completion["field_gap_counts"]["lead_link"] == 1
    assert completion["field_gap_counts"]["article_summary"] == 1
    assert completion["field_gap_counts"]["coverage"] == 1
    assert completion["top_field_gaps"][0]["count"] == 1
    assert any(item["label"] == "覆盖不足" for item in completion["top_field_gaps"])
    assert "覆盖不足：1 条" in completion["field_gap_notes"]
    assert any("最终输出字段缺口" in action for action in completion["next_actions"])
    assert any("覆盖未完成" in action for action in completion["next_actions"])


def assert_sync_status_promotes_summary_only_work(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from sync_status import enrichment_completion_summary
    from store import connect, enqueue_enrichment_tasks_for_posts, row_for_post, upsert_post

    conn = connect(tmp_path / "summary-only-status.sqlite")
    article_material = {
        "article_material": {
            "ok": True,
            "article_url": "https://story.example/summary",
            "title": "Summary-only story",
            "text_excerpt": "A complete article material payload exists and only needs a Chinese summary.",
        }
    }
    post = normalize_post(
        {
            "account_name": "Example Page",
            "account_url": "https://www.facebook.com/example",
            "post_url": "https://www.facebook.com/example/posts/summary-only",
            "posted_at": "2026年6月2日 12:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/summary",
            "landing_url": "https://story.example/summary",
            "lead_url_raw": "https://story.example/summary",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "likes": 20,
            "comments": 3,
            "shares": 1,
            "post_type": "图文",
            **article_material,
        }
    )
    upsert_post(conn, post)
    stored = row_for_post(conn, post)
    assert stored is not None
    enqueue_enrichment_tasks_for_posts(conn, [stored])
    completion = enrichment_completion_summary(conn, [stored])
    assert completion["requires_codex_summary_count"] == 1
    assert completion["requires_codex_summary_urls"] == [stored["canonical_post_url"]]
    assert completion["open_task_count"] == 1
    assert completion["summary_open_task_count"] == 1
    assert completion["auto_open_task_count"] == 0
    assert completion["has_summary_only_work"] is True
    assert completion["has_auto_enrichment_work"] is False
    assert completion["missing_stage_counts"] == {"summary": 1}
    assert any("导出 summary requests" in action for action in completion["next_actions"])
    assert not any("继续运行 enrichment_worker" in action for action in completion["next_actions"])


def assert_sync_status_prioritizes_auto_work_over_summary(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_feishu
    from models import normalize_post
    from sync_status import completion_run_status, enrichment_completion_summary
    from store import connect, enqueue_enrichment_tasks_for_posts, row_for_post, upsert_post

    conn = connect(tmp_path / "mixed-auto-summary-status.sqlite")
    post = normalize_post(
        {
            "account_name": "Example Page",
            "account_url": "https://www.facebook.com/example",
            "post_url": "https://www.facebook.com/example/posts/mixed-auto-summary",
            "posted_at": "2026年6月2日 12:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/mixed",
            "landing_url": "https://story.example/mixed",
            "lead_url_raw": "https://story.example/mixed",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "post_type": "图文",
            "article_material": {
                "ok": True,
                "title": "Mixed story",
                "text_excerpt": "The article material is ready, but engagement still needs an automatic detail refetch.",
            },
        }
    )
    upsert_post(conn, post)
    stored = row_for_post(conn, post)
    assert stored is not None
    enqueue_enrichment_tasks_for_posts(conn, [stored])
    completion = enrichment_completion_summary(conn, [stored])
    assert completion["requires_codex_summary_count"] == 1
    assert completion["has_summary_only_work"] is False
    assert completion["has_auto_enrichment_work"] is True
    assert completion["missing_stage_counts"]["engagement"] == 1
    assert completion_run_status(completion, ledger_mode=False) == "incomplete_pending_tasks"
    assert completion_run_status(completion, ledger_mode=True) == "synced_ledger_incomplete"

    result = sync_feishu.sync_posts(
        {
            "feishu": {
                "sheets": {"all_posts": "FB竞品帖子链接"},
                "field_schema": {"output_headers": ["帖子链接", "是否采用"]},
            }
        },
        [stored],
        "all_posts",
        "append",
        True,
        audit=True,
        conn=conn,
    )
    assert result["ok"] is True
    assert result["run_status"] == "synced_ledger_incomplete"
    assert result["complete"] is False


def assert_completion_summary_uses_quality_audit_config(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from sync_status import enrichment_completion_summary
    from store import connect, enqueue_enrichment_tasks_for_posts, pending_enrichment_tasks, row_for_post, upsert_post

    conn = connect(tmp_path / "completion-audit-config.sqlite")
    config = {
        "quality_audit": {
            "required_engagement_fields": ["likes"],
            "low_like_threshold": 10,
            "required_post_types": ["视频"],
        }
    }
    post = normalize_post(
        {
            "account_name": "Example Page",
            "account_url": "https://www.facebook.com/example",
            "post_url": "https://www.facebook.com/example/posts/config-gap",
            "posted_at": "2026年6月2日 12:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/config-gap",
            "landing_url": "https://story.example/config-gap",
            "lead_url_raw": "https://story.example/config-gap",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 8,
            "comments": 3,
            "shares": 1,
            "post_type": "图文",
        }
    )
    upsert_post(conn, post)
    stored = row_for_post(conn, post)
    assert stored is not None
    default_completion = enrichment_completion_summary(conn, [stored])
    strict_completion = enrichment_completion_summary(conn, [stored], config)
    assert "likes_low" not in default_completion["field_gap_counts"]
    assert "post_type" not in default_completion["field_gap_counts"]
    assert strict_completion["field_gap_counts"]["likes_low"] == 1
    assert strict_completion["field_gap_counts"]["post_type"] == 1
    assert strict_completion["top_field_gaps"][0]["reason"] in {"likes_low", "post_type"}
    default_tasks = enqueue_enrichment_tasks_for_posts(conn, [stored])
    assert default_tasks["stage_counts"] == {}
    strict_tasks = enqueue_enrichment_tasks_for_posts(conn, [stored], config)
    assert strict_tasks["stage_counts"] == {"engagement": 1, "post_type": 1}
    assert sorted(task["stage"] for task in pending_enrichment_tasks(conn, limit=20)) == ["engagement", "post_type"]


def assert_strict_sync_uses_quality_audit_config(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_feishu
    from models import normalize_post
    from store import connect, row_for_post, upsert_post

    conn = connect(tmp_path / "strict-audit-config.sqlite")
    config = {
        "feishu": {
            "sheets": {"all_posts": "FB竞品帖子链接"},
            "field_schema": {"output_headers": ["帖子链接", "是否采用"]},
        },
        "quality_audit": {
            "required_engagement_fields": ["likes"],
            "low_like_threshold": 10,
            "required_post_types": ["视频"],
        },
    }
    post = normalize_post(
        {
            "account_name": "Example Page",
            "account_url": "https://www.facebook.com/example",
            "post_url": "https://www.facebook.com/example/posts/strict-audit-config",
            "posted_at": "2026年6月2日 12:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/strict-audit-config",
            "landing_url": "https://story.example/strict-audit-config",
            "lead_url_raw": "https://story.example/strict-audit-config",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 8,
            "comments": 3,
            "shares": 1,
            "post_type": "图文",
        }
    )
    assert post["output_status"] == "ready_for_output"
    upsert_post(conn, post)
    stored = row_for_post(conn, post)
    assert stored is not None
    result = sync_feishu.sync_posts(config, [stored], "all_posts", "append", True, audit=False, conn=conn)
    assert result["ok"] is False
    assert result["stage"] == "quality_gate"
    assert result["ready_for_output"] == 0
    assert result["needs_enrichment_skipped"] == 1
    completion = result["enrichment_completion"]
    assert completion["field_gap_counts"]["likes_low"] == 1
    assert completion["field_gap_counts"]["post_type"] == 1
    assert completion["final_usable_rate"] == 0.0


def assert_export_summary_requests_can_scope_account_job(tmp_path: Path) -> None:
    config = tmp_path / "settings_summary_scope.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    db_path = tmp_path / "summary-scope.sqlite"
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, upsert_post

    conn = connect(db_path)
    material = {
        "article_material": {
            "ok": True,
            "title": "Scoped story",
            "text_excerpt": "A story with enough article material for a scoped summary request.",
        }
    }
    raw_target = {
        "account_name": "Target",
        "account_url": "https://www.facebook.com/target",
        "account_type": "competitor",
        "post_url": "https://www.facebook.com/target/posts/summary-scope",
        "posted_at": "2026年6月2日 12:00",
        "time_confirmed": True,
        "time_source": "dom_aria_label",
        "article_url": "https://story.example/target",
        "landing_url": "https://story.example/target",
        "lead_url_raw": "https://story.example/target",
        "lead_link_status": "qualified",
        "lead_link_source": "comment",
        "likes": 20,
        "comments": 3,
        "shares": 1,
        "post_type": "图文",
        **material,
    }
    raw_other = {
        **raw_target,
        "account_name": "Other",
        "account_url": "https://www.facebook.com/other",
        "post_url": "https://www.facebook.com/other/posts/summary-scope",
        "article_url": "https://story.example/other",
        "landing_url": "https://story.example/other",
        "lead_url_raw": "https://story.example/other",
    }
    target = normalize_post(raw_target)
    other = normalize_post(raw_other)
    upsert_post(conn, target)
    upsert_post(conn, other)
    output = tmp_path / "summary_requests_scoped.json"
    exported = run(
        [
            PYTHON,
            "scripts/export_summary_requests.py",
            "--config",
            str(config),
            "--output",
            str(output),
            "--date",
            "260602",
            "--account-url",
            "https://www.facebook.com/target",
            "--account-type",
            "competitor",
        ]
    )
    assert exported.returncode == 0, exported.stderr or exported.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["scope"]["enabled"] is True
    assert payload["scope"]["source_post_count"] == 1
    assert payload["count"] == 1
    assert payload["requests"][0]["post_url"] == "https://facebook.com/target/posts/summary-scope"


def assert_apply_article_summaries_scopes_account_job(tmp_path: Path) -> None:
    config = tmp_path / "settings_summary_apply_scope.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    db_path = tmp_path / "summary-apply-scope.sqlite"
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, row_for_post, upsert_post

    conn = connect(db_path)
    material = {
        "article_material": {
            "ok": True,
            "title": "Scoped apply story",
            "text_excerpt": "A story with enough article material for a scoped summary application.",
        }
    }
    raw_target = {
        "account_name": "Target",
        "account_url": "https://www.facebook.com/target",
        "account_type": "competitor",
        "post_url": "https://www.facebook.com/target/posts/summary-apply",
        "posted_at": "2026年6月2日 12:00",
        "time_confirmed": True,
        "time_source": "dom_aria_label",
        "article_url": "https://story.example/target-apply",
        "landing_url": "https://story.example/target-apply",
        "lead_url_raw": "https://story.example/target-apply",
        "lead_link_status": "qualified",
        "lead_link_source": "comment",
        "likes": 20,
        "comments": 3,
        "shares": 1,
        "post_type": "图文",
        **material,
    }
    raw_other = {
        **raw_target,
        "account_name": "Other",
        "account_url": "https://www.facebook.com/other",
        "post_url": "https://www.facebook.com/other/posts/summary-apply",
        "article_url": "https://story.example/other-apply",
        "landing_url": "https://story.example/other-apply",
        "lead_url_raw": "https://story.example/other-apply",
    }
    target = normalize_post(raw_target)
    other = normalize_post(raw_other)
    upsert_post(conn, target)
    upsert_post(conn, other)
    summaries = tmp_path / "article_summaries.json"
    summaries.write_text(
        json.dumps(
            {
                "https://story.example/target-apply": "这篇故事围绕家庭资产争夺展开，主角发现亲人试图控制财产后及时反击，适合提炼成短剧素材。",
                "https://story.example/other-apply": "这篇故事描述另一组家庭矛盾，主角面对亲属压力后选择保护自己的权益。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    applied = run(
        [
            PYTHON,
            "scripts/apply_article_summaries.py",
            "--config",
            str(config),
            "--summaries",
            str(summaries),
            "--date",
            "260602",
            "--account-url",
            "https://www.facebook.com/target",
            "--account-type",
            "competitor",
            "--dry-run",
        ]
    )
    assert applied.returncode == 0, applied.stdout
    data = json.loads(applied.stdout)
    assert data["mode"] == "sqlite"
    assert data["applied"] == 1
    assert data["scope"]["enabled"] is True
    assert data["scope"]["source_post_count"] == 1
    assert data["next_commands"][0]["reason"] == "resume_account_job_after_summary_apply"
    assert "run_account_job.py" in data["next_commands"][0]["command"]
    assert "--account-url https://www.facebook.com/target" in data["next_commands"][0]["command"]
    assert "--target-date 260602" in data["next_commands"][0]["command"]
    assert "--resume-only" in data["next_commands"][0]["command"]
    assert "--sync" in data["next_commands"][0]["command"]
    assert "--dry-run" in data["next_commands"][0]["command"]

    target_after = row_for_post(conn, target)
    other_after = row_for_post(conn, other)
    assert target_after is not None
    assert other_after is not None
    assert target_after["output_status"] == "ready_for_output"
    assert target_after["summary_source"] == "article"
    assert not other_after.get("story_summary")
    assert other_after["output_status"] != "ready_for_output"

    resumed = run(shlex.split(data["next_commands"][0]["command"]))
    assert resumed.returncode == 0, resumed.stdout
    resumed_data = json.loads(resumed.stdout)
    assert resumed_data["run_status"] == "complete"
    assert resumed_data["quality_summary"]["final_usable_rate"] == 1.0
    assert resumed_data["quality_summary"]["ledger_usable_rate"] == 1.0


def assert_generate_article_summaries_from_requests(tmp_path: Path) -> None:
    requests = tmp_path / "summary_requests.json"
    output = tmp_path / "article_summaries.json"
    requests.write_text(
        json.dumps(
            {
                "ok": True,
                "requests": [
                    {
                        "post_url": "https://facebook.com/storyhub/posts/generated",
                        "canonical_post_url": "https://facebook.com/storyhub/posts/generated",
                        "article_url": "https://story.example/generated",
                        "account_name": "Story Hub",
                        "article_material": {
                            "title": "Mother discovers the family secret",
                            "meta_description": "A dramatic family conflict escalates after a hidden plan is exposed.",
                            "text_excerpt": "The heroine notices suspicious behavior, uncovers a plan, and takes action to protect the newborn.",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    generated = run([PYTHON, "scripts/generate_article_summaries.py", "--input", str(requests), "--output", str(output)])
    assert generated.returncode == 0, generated.stdout
    data = json.loads(generated.stdout)
    assert data["generated"] == 1
    assert data["summary_key_count"] == 2
    summaries = json.loads(output.read_text(encoding="utf-8"))
    summary = summaries["https://facebook.com/storyhub/posts/generated"]
    assert "这篇故事讲述" in summary
    assert "家庭关系" in summary
    assert "秘密曝光" in summary
    assert "冲突" in summary
    assert "Mother discovers the family secret" not in summary
    sys.path.insert(0, str(ROOT / "scripts"))
    from story_summary_policy import story_summary_errors

    assert story_summary_errors(
        {
            "story_summary": summary,
            "summary_source": "article",
            "article_material": {
                "title": "Mother discovers the family secret",
                "meta_description": "A dramatic family conflict escalates after a hidden plan is exposed.",
                "text_excerpt": "The heroine notices suspicious behavior, uncovers a plan, and takes action to protect the newborn.",
            },
        }
    ) == []


def assert_summary_request_prefers_article_material_source() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from export_summary_requests import needs_summary, summary_request_for

    assert needs_summary(
        {
            "post_url": "https://facebook.com/example/posts/no-material",
            "article_url": "https://story.example/no-material",
            "landing_url": "https://story.example/no-material",
        },
        only_invalid=False,
    ) is False

    request = summary_request_for(
        {
            "post_url": "https://facebook.com/example/posts/source",
            "article_url": "https://story.example/article-source",
            "landing_url": "https://landing.example/redirect-shell",
            "raw_payload": json.dumps(
                {
                    "article_material": {
                        "ok": True,
                        "article_url": "https://story.example/material-source",
                        "title": "Article source",
                        "text_excerpt": "Material source should be represented in the request.",
                    }
                },
                ensure_ascii=False,
            ),
        }
    )
    assert request["article_url"] == "https://story.example/article-source"

    fallback_request = summary_request_for(
        {
            "post_url": "https://facebook.com/example/posts/source-fallback",
            "landing_url": "https://landing.example/redirect-shell",
            "raw_payload": json.dumps(
                {
                    "article_material": {
                        "ok": True,
                        "article_url": "https://story.example/material-source",
                        "title": "Material source",
                    }
                },
                ensure_ascii=False,
            ),
        }
    )
    assert fallback_request["article_url"] == "https://story.example/material-source"


def assert_export_summary_requests_skips_rows_without_material(tmp_path: Path) -> None:
    config = tmp_path / "settings_summary_material_only.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    db_path = tmp_path / "summary-material-only.sqlite"
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, upsert_post

    conn = connect(db_path)
    with_material = normalize_post(
        {
            "account_name": "Summary Source",
            "account_url": "https://www.facebook.com/summarysource",
            "account_type": "competitor",
            "post_url": "https://www.facebook.com/summarysource/posts/with-material",
            "posted_at": "2026年6月3日 12:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/with-material",
            "landing_url": "https://story.example/with-material",
            "lead_url_raw": "https://story.example/with-material",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "article_material": {
                "ok": True,
                "title": "With material",
                "text_excerpt": "This row has enough article material for a Codex-written Chinese summary.",
            },
        }
    )
    without_material = normalize_post(
        {
            "account_name": "Summary Source",
            "account_url": "https://www.facebook.com/summarysource",
            "account_type": "competitor",
            "post_url": "https://www.facebook.com/summarysource/posts/without-material",
            "posted_at": "2026年6月3日 13:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/without-material",
            "landing_url": "https://story.example/without-material",
            "lead_url_raw": "https://story.example/without-material",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
        }
    )
    upsert_post(conn, with_material)
    upsert_post(conn, without_material)
    output = tmp_path / "summary_requests_material_only.json"
    exported = run(
        [
            PYTHON,
            "scripts/export_summary_requests.py",
            "--config",
            str(config),
            "--output",
            str(output),
            "--date",
            "260603",
            "--account-url",
            "https://www.facebook.com/summarysource",
        ]
    )
    assert exported.returncode == 0, exported.stderr or exported.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["scope"]["source_post_count"] == 2
    assert payload["count"] == 1
    assert payload["requests"][0]["post_url"] == "https://facebook.com/summarysource/posts/with-material"
    assert payload["requests"][0]["article_material"]["title"] == "With material"


def assert_enrich_article_summaries_prefers_article_url(tmp_path: Path) -> None:
    config = tmp_path / "settings_article_source.yaml"
    source = tmp_path / "article_source.json"
    output = tmp_path / "with_material.json"
    article = tmp_path / "article-source.html"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    article.write_text(
        """
        <html><head><title>Preferred article source</title></head>
        <body><p>This article source should be fetched even when landing_url points elsewhere.</p></body></html>
        """,
        encoding="utf-8",
    )
    server, base_url = start_static_http_server(tmp_path)
    article_url = f"{base_url}/{article.name}"
    source.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://facebook.com/example/posts/article-source",
                        "article_url": article_url,
                        "landing_url": "https://127.0.0.1:1/unreachable-landing",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        enriched = run(
            [
                PYTHON,
                "scripts/enrich_article_summaries.py",
                "--config",
                str(config),
                "--input",
                str(source),
                "--output",
                str(output),
                "--concurrency",
                "1",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    assert enriched.returncode == 0, enriched.stderr or enriched.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    material = payload["posts"][0]["article_material"]
    assert material["ok"] is True
    assert material["article_url"] == article_url
    assert "Preferred article source" in material["title"]
    assert payload["article_summary_errors"] == []


def assert_sync_feishu_strict_marks_ready_rows_synced(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_feishu
    from models import normalize_post
    from store import connect, row_for_post, upsert_post

    original_write_rows = sync_feishu.write_rows
    conn = connect(tmp_path / "strict-sync-mark.sqlite")
    ready = normalize_post(
        {
            "account_name": "Ready Page",
            "account_url": "https://www.facebook.com/readypage",
            "post_url": "https://www.facebook.com/readypage/posts/ready",
            "posted_at": "2026年6月2日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/ready",
            "landing_url": "https://story.example/ready",
            "lead_url_raw": "https://story.example/ready",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 20,
            "comments": 3,
            "shares": 1,
            "post_type": "图文",
        }
    )
    upsert_post(conn, ready)
    stored_ready = row_for_post(conn, ready)
    assert stored_ready is not None
    incomplete = normalize_post(
        {
            "account_name": "Ready Page",
            "account_url": "https://www.facebook.com/readypage",
            "post_url": "https://www.facebook.com/readypage/posts/incomplete",
            "story_summary": "Visible homepage candidate.",
        }
    )
    upsert_post(conn, incomplete)
    stored_incomplete = row_for_post(conn, incomplete)
    assert stored_incomplete is not None
    config = {
        "feishu": {
            "sheets": {"all_posts": "FB竞品帖子链接"},
            "field_schema": {"output_headers": ["账号", "帖子链接", "是否采用"]},
        }
    }
    try:
        sync_feishu.write_rows = lambda *_args, **_kwargs: {"ok": True, "rows": 1, "mode": "append"}
        strict = sync_feishu.sync_posts(config, [stored_ready], "all_posts", "append", False, audit=False, conn=conn)
        assert strict["ok"] is True
        synced = row_for_post(conn, ready)
        assert synced is not None
        assert synced["output_status"] == "output_synced"

        audit = sync_feishu.sync_posts(config, [stored_incomplete], "all_posts", "append", False, audit=True, conn=conn)
        assert audit["ok"] is True
        still_incomplete = row_for_post(conn, incomplete)
        assert still_incomplete is not None
        assert still_incomplete["output_status"] != "output_synced"
    finally:
        sync_feishu.write_rows = original_write_rows


def assert_strict_sync_completion_uses_full_candidate_scope(tmp_path: Path) -> None:
    config = tmp_path / "settings_strict_scope.yaml"
    sample = tmp_path / "strict_scope.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'strict-scope.sqlite'}"
        ),
        encoding="utf-8",
    )
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Story Hub",
                        "account_url": "https://www.facebook.com/storyhub",
                        "post_url": "https://www.facebook.com/storyhub/posts/ready-scope",
                        "posted_at": "2026年6月2日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://story.example/ready-scope",
                        "landing_url": "https://story.example/ready-scope",
                        "lead_url_raw": "https://story.example/ready-scope",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                        "story_summary": VALID_CN_SUMMARY,
                        "summary_source": "article",
                        "likes": 80,
                        "comments": 12,
                        "shares": 3,
                        "post_type": "图文",
                    },
                    {
                        "account_name": "Story Hub",
                        "account_url": "https://www.facebook.com/storyhub",
                        "post_url": "https://www.facebook.com/storyhub/posts/incomplete-scope",
                        "relative_time_text": "2h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-02T12:00:00",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sync = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--strict-ready-only",
            "--dry-run",
        ]
    )
    assert sync.returncode == 0, sync.stdout
    data = json.loads(sync.stdout)
    completion = data["feishu_sync"]["enrichment_completion"]
    assert data["feishu_sync"]["ready_for_output"] == 1
    assert data["feishu_sync"]["complete"] is False
    assert data["feishu_sync"]["run_status"] == "incomplete_pending_tasks"
    assert "field_gaps" in [item["code"] for item in data["feishu_sync"]["completion_blockers"]]
    assert completion["post_count"] == 2
    assert completion["ready_or_synced_posts"] == 1
    assert completion["final_usable_rate"] == 0.5
    assert completion["has_incomplete_enrichment"] is True


def assert_minimal_ledger_candidate_syncs_to_formal_sheet(tmp_path: Path) -> None:
    config = tmp_path / "settings_minimal_ledger.yaml"
    sample = tmp_path / "minimal_ledger.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'minimal-ledger.sqlite'}"
        ),
        encoding="utf-8",
    )
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Ledger Page",
                        "account_url": "https://www.facebook.com/ledgerpage",
                        "post_url": "https://www.facebook.com/ledgerpage/posts/minimal",
                        "relative_time_text": "1h",
                        "story_summary": "Visible candidate from homepage.",
                        "crawled_at": "2026-06-02T14:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sync = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--sync", "--dry-run"])
    assert sync.returncode == 0, sync.stdout
    sync_data = json.loads(sync.stdout)
    assert sync_data["feishu_sync"]["audit_output"] is True
    assert sync_data["feishu_sync"]["output_candidates"] == 1
    assert sync_data["feishu_sync"]["audit_missing_field_counts"]["exact_time"] == 1
    assert sync_data["feishu_sync"]["audit_missing_field_counts"]["lead_link"] == 1
    assert sync_data["feishu_sync"]["audit_missing_field_counts"]["article_summary"] == 1
    assert "精确时间：1 条" in sync_data["feishu_sync"]["audit_missing_field_notes"]
    assert '"rows": 1' in sync.stdout

    strict = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--strict-ready-only",
            "--dry-run",
        ]
    )
    assert strict.returncode == 1, strict.stdout
    assert '"ready_for_output": 0' in strict.stdout


def assert_sqlite_upsert_preserves_enriched_fields(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, row_for_post, upsert_post

    conn = connect(tmp_path / "idempotent-upsert.sqlite")
    ready = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/pfbid-idempotent",
            "posted_at": "2026年5月28日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/ready",
            "landing_url": "https://story.example/ready",
            "lead_url_raw": "https://story.example/ready",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 80,
            "comments": 12,
            "shares": 3,
            "post_type": "图文",
            "adoption_status": "采用",
        }
    )
    upsert_post(conn, ready)
    partial = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/pfbid-idempotent",
            "post_time_text": "2h",
            "story_summary": "Visible homepage candidate.",
            "crawled_at": "2026-05-28T12:00:00",
        }
    )
    upsert_post(conn, partial)
    stored = row_for_post(conn, ready)
    assert stored is not None
    assert stored["output_status"] == "ready_for_output"
    assert stored["posted_at"] == "2026年5月28日 10:00"
    assert stored["time_source"] == "dom_aria_label"
    assert stored["lead_link_status"] == "qualified"
    assert stored["landing_url"] == "https://story.example/ready"
    assert stored["story_summary"] == VALID_CN_SUMMARY
    assert stored["likes"] == 80
    assert stored["comments"] == 12
    assert stored["shares"] == 3
    assert stored["post_type"] == "图文"
    assert stored["adoption_status"] == "采用"


def assert_sqlite_upsert_preserves_article_material_payload(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from story_summary_policy import article_material_for_post
    from store import connect, row_for_post, upsert_post

    conn = connect(tmp_path / "article-material-upsert.sqlite")
    with_material = normalize_post(
        {
            "account_name": "Material Page",
            "account_url": "https://www.facebook.com/materialpage",
            "post_url": "https://www.facebook.com/materialpage/posts/keep-material",
            "article_url": "https://story.example/material",
            "landing_url": "https://story.example/material",
            "article_material": {
                "ok": True,
                "title": "Existing material",
                "text_excerpt": "Fetched article text that should remain available for summary export.",
            },
        }
    )
    upsert_post(conn, with_material)
    partial = normalize_post(
        {
            "account_name": "Material Page",
            "account_url": "https://www.facebook.com/materialpage",
            "post_url": "https://www.facebook.com/materialpage/posts/keep-material",
            "post_time_text": "2h",
            "story_summary": "Visible homepage candidate.",
            "raw_payload": {"story_summary": "Visible homepage candidate."},
        }
    )
    upsert_post(conn, partial)
    stored = row_for_post(conn, with_material)
    assert stored is not None
    material = article_material_for_post(stored)
    assert material["ok"] is True
    assert material["title"] == "Existing material"


def assert_sqlite_upsert_resyncs_previously_synced_rows(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, mark_output_synced, row_for_post, upsert_post, upsert_posts

    conn = connect(tmp_path / "resync-output-synced.sqlite")
    ready = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/resync",
            "posted_at": "2026年5月28日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/resync",
            "landing_url": "https://story.example/resync",
            "lead_url_raw": "https://story.example/resync",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 80,
            "comments": 12,
            "shares": 3,
            "post_type": "图文",
        }
    )
    upsert_post(conn, ready)
    stored_ready = row_for_post(conn, ready)
    assert stored_ready is not None
    mark_output_synced(conn, [stored_ready])
    synced = row_for_post(conn, ready)
    assert synced is not None
    assert synced["output_status"] == "output_synced"

    refreshed = normalize_post(
        {
            **ready,
            "likes": 120,
            "comments": 18,
            "shares": 5,
        }
    )
    result = upsert_posts(conn, [refreshed])
    assert result["updated"] == 1
    assert len(result["sync_candidates"]) == 1
    assert result["sync_candidates"][0]["post_url"] == "https://facebook.com/storyhub/posts/resync"
    assert result["sync_candidates"][0]["likes"] == 120


def assert_sqlite_upsert_does_not_protect_internal_lead_links(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, row_for_post, upsert_post

    conn = connect(tmp_path / "internal-lead-upsert.sqlite")
    internal = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/internal-lead",
            "posted_at": "2026年5月28日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://www.facebook.com/storyhub/posts/internal-lead",
            "landing_url": "https://www.facebook.com/storyhub/posts/internal-lead",
            "lead_url_raw": "https://www.facebook.com/storyhub/posts/internal-lead",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
        }
    )
    upsert_post(conn, internal)

    external = normalize_post(
        {
            "account_name": "Story Hub",
            "account_url": "https://www.facebook.com/storyhub",
            "post_url": "https://www.facebook.com/storyhub/posts/internal-lead",
            "article_url": "https://story.example/real",
            "landing_url": "https://story.example/real",
            "lead_url_raw": "https://story.example/real",
            "lead_link_status": "qualified",
            "lead_link_source": "comment_reply",
        }
    )
    upsert_post(conn, external)
    stored = row_for_post(conn, internal)
    assert stored is not None
    assert stored["landing_url"] == "https://story.example/real"
    assert stored["article_url"] == "https://story.example/real"
    assert stored["lead_link_source"] == "comment_reply"


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


def assert_prepare_capture_skips_bad_candidate_without_failing_batch(tmp_path: Path) -> None:
    raw = tmp_path / "raw_bad_candidate.json"
    prepared = tmp_path / "prepared_bad_candidate.json"
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "post_url": "https://www.facebook.com/example/posts/bad-time",
                        "posted_at": "2026年13月99日 25:99",
                        "article_summary": "Bad candidate should not stop the whole batch.",
                    },
                    {
                        "post_url": "https://www.facebook.com/example/posts/good",
                        "post_time_text": "1h",
                        "story_summary": "Good visible candidate.",
                        "crawled_at": "2026-05-27T14:00:00",
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
    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(prepared.read_text(encoding="utf-8"))
    assert data["prepared"] == 1
    assert data["posts"][0]["post_url"].endswith("/good")
    assert any(item["reason"] == "prepare_record_error" for item in data["rejected"])


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
    assert '"audit_output": true' in sync.stdout, sync.stdout
    assert '"output_candidates": 2' in sync.stdout, sync.stdout
    assert '"rows": 2' in sync.stdout, sync.stdout

    audit = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(prepared),
            "--sync-audit",
            "--dry-run",
        ]
    )
    assert audit.returncode == 0, audit.stdout
    assert '"audit_output": true' in audit.stdout
    assert '"output_candidates": 2' in audit.stdout
    assert '"rows": 2' in audit.stdout


def assert_prepare_capture_preserves_type_and_article_summary(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from field_schema import output_row_for_headers
    from store import connect, row_for_post

    raw = tmp_path / "raw_with_summary.json"
    prepared = tmp_path / "prepared_with_summary.json"
    config = tmp_path / "settings_with_summary.yaml"
    db_path = tmp_path / "summary-preserve.sqlite"
    raw.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Summary Page",
                        "account_url": "https://www.facebook.com/summarypage",
                        "post_url": "https://www.facebook.com/summarypage/posts/keeps-fields",
                        "posted_at": "2026年5月27日 17:06",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "https://story.example/keeps-fields",
                        "landing_url": "https://story.example/keeps-fields",
                        "lead_url_raw": "https://story.example/keeps-fields",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                        "post_type": "图文",
                        "story_summary": VALID_CN_SUMMARY,
                        "summary_source": "article",
                        "likes": 80,
                        "comments": 12,
                        "shares": 3,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prepared_result = run(
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
            "https://www.facebook.com/summarypage",
            "--account-name",
            "Summary Page",
        ]
    )
    assert prepared_result.returncode == 0, prepared_result.stderr or prepared_result.stdout
    prepared_data = json.loads(prepared.read_text(encoding="utf-8"))
    post = prepared_data["posts"][0]
    assert post["post_type"] == "图文"
    assert post["story_summary"] == VALID_CN_SUMMARY
    assert post["summary_source"] == "article"
    assert "文章概要待生成" not in post["note"]
    assert "帖子类型待确认" not in post["note"]

    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(prepared), "--no-sync"])
    assert imported.returncode == 0, imported.stderr or imported.stdout
    conn = connect(db_path)
    stored = row_for_post(conn, post)
    assert stored is not None
    assert stored["post_type"] == "图文"
    assert stored["story_summary"] == VALID_CN_SUMMARY
    assert stored["summary_source"] == "article"
    row = output_row_for_headers(
        stored,
        ["账号", "帖子链接", "帖子类型", "故事概要", "是否采用"],
        {"quality_audit": {"required_engagement_fields": ["likes", "comments", "shares"]}},
    )
    assert row[2] == "图文"
    assert row[3] == VALID_CN_SUMMARY
    assert row[4] == ""


def assert_normalize_post_marks_existing_story_summary_as_article() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post

    post = normalize_post(
        {
            "account_name": "Imported Sheet",
            "post_url": "https://www.facebook.com/imported/posts/summary",
            "posted_at": "2026年5月27日 17:06",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/imported",
            "landing_url": "https://story.example/imported",
            "lead_url_raw": "https://story.example/imported",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "帖子类型": "视频",
            "故事概要": VALID_CN_SUMMARY,
            "likes": 80,
            "comments": 12,
            "shares": 3,
        }
    )
    assert post["post_type"] == "视频"
    assert post["story_summary"] == VALID_CN_SUMMARY
    assert post["summary_source"] == "article"
    assert post["output_status"] == "ready_for_output"


def assert_sync_rejects_estimated_relative_time_but_allows_partial_preview(tmp_path: Path) -> None:
    sample = tmp_path / "estimated_time.json"
    config = tmp_path / "settings_estimated_time.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Example Page",
                        "account_url": "https://www.facebook.com/example",
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
    assert sync.returncode == 0, sync.stdout
    assert '"audit_output": true' in sync.stdout
    assert '"output_candidates": 1' in sync.stdout

    strict = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync",
            "--strict-ready-only",
            "--dry-run",
        ]
    )
    assert strict.returncode == 1, strict.stdout
    assert '"ready_for_output": 0' in strict.stdout
    assert '"needs_enrichment_skipped": 1' in strict.stdout

    audit = run(
        [
            PYTHON,
            "scripts/import_existing_result.py",
            "--config",
            str(config),
            "--input",
            str(sample),
            "--sync-audit",
            "--dry-run",
        ]
    )
    assert audit.returncode == 0, audit.stdout
    assert '"audit_output": true' in audit.stdout
    assert '"output_candidates": 1' in audit.stdout

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
                        "likes": 12,
                        "comments": 3,
                        "shares": 1,
                        "post_type": "图文",
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
            "--strict-ready-only",
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
            "--strict-ready-only",
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
            "--strict-ready-only",
            "--dry-run",
        ]
    )
    assert filtered.returncode == 1, filtered.stdout
    filtered_data = json.loads(filtered.stdout)
    assert filtered_data["count"] == 1
    assert filtered_data["hit_rule"] == "date=260527"
    assert filtered_data["feishu_sync"]["stage"] == "quality_gate"
    assert filtered_data["feishu_sync"]["run_status"] == "quality_gate"
    assert filtered_data["feishu_sync"]["complete"] is False
    assert filtered_data["feishu_sync"]["completion_blockers"][0]["code"] == "quality_gate"
    assert filtered_data["feishu_sync"]["enrichment_completion"]["post_count"] == 1


def assert_filter_sync_reports_audit_missing_field_counts(tmp_path: Path) -> None:
    sample = tmp_path / "filter_audit_gaps.json"
    config = tmp_path / "settings_filter_audit_gaps.yaml"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Filter Gap Page",
                        "account_url": "https://www.facebook.com/filtergap",
                        "post_url": "https://www.facebook.com/filtergap/posts/gap",
                        "relative_time_text": "1h",
                        "story_summary": "Visible candidate from homepage.",
                        "crawled_at": "2026-05-27T12:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'filter_audit_gaps.sqlite'}"
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
    assert filtered.returncode == 0, filtered.stdout
    filtered_data = json.loads(filtered.stdout)
    sync = filtered_data["feishu_sync"]
    assert sync["audit_output"] is True
    assert sync["output_candidates"] == 1
    assert sync["audit_missing_field_counts"]["exact_time"] == 1
    assert sync["audit_missing_field_counts"]["lead_link"] == 1
    assert sync["audit_missing_field_counts"]["article_summary"] == 1
    assert "引流链接：1 条" in sync["audit_missing_field_notes"]
    blocker_codes = [item["code"] for item in sync["completion_blockers"]]
    assert "field_gaps" in blocker_codes
    assert "ledger_not_final" in blocker_codes


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
            "--strict-ready-only",
            "--dry-run",
        ]
    )
    assert sync.returncode == 1, sync.stdout
    assert "missing_qualified_comment_lead_link" in sync.stdout


def assert_quality_gate_rejects_internal_landing_url(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from output_quality import output_quality_errors

    sample = tmp_path / "internal_landing_url.json"
    config = tmp_path / "settings_internal_landing_url.yaml"
    raw_post = {
        "post_url": "https://www.facebook.com/example/posts/internal-landing",
        "posted_at": "2026年5月27日 10:00",
        "time_confirmed": True,
        "time_source": "dom_aria_label",
        "article_url": "https://www.facebook.com/example/posts/not-a-story",
        "landing_url": "https://www.facebook.com/example/posts/not-a-story",
        "lead_url_raw": "https://www.facebook.com/example/posts/not-a-story",
        "lead_link_status": "qualified",
        "lead_link_source": "comment",
        "article_summary": VALID_CN_SUMMARY,
        "summary_source": "article",
        "output_status": "ready_for_output",
    }
    normalized = normalize_post(raw_post)
    assert normalized["output_status"] != "ready_for_output"
    errors = output_quality_errors([{**normalized, "output_status": "ready_for_output"}])
    assert errors
    assert "missing_qualified_comment_lead_link" in errors[0]["errors"]
    sample.write_text(
        json.dumps(
            {
                "posts": [raw_post]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config_text = config.read_text(encoding="utf-8").replace(
        "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'internal_landing_url.sqlite'}"
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
            "--strict-ready-only",
            "--dry-run",
        ]
    )
    assert sync.returncode == 1, sync.stdout
    assert '"ready_for_output": 0' in sync.stdout
    assert '"needs_enrichment_skipped": 1' in sync.stdout


def assert_quality_gate_requires_raw_comment_lead_url(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from output_quality import output_quality_errors

    normalized = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/missing-raw-lead",
            "posted_at": "2026年5月27日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://site.test/story",
            "landing_url": "https://site.test/story",
            "lead_url_raw": "",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "article_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "output_status": "ready_for_output",
        }
    )
    assert normalized["output_status"] != "ready_for_output"
    errors = output_quality_errors([{**normalized, "output_status": "ready_for_output"}])
    assert errors
    assert "missing_qualified_comment_lead_link" in errors[0]["errors"]


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


def assert_detail_enrichment_detects_plain_text_comment_links() -> None:
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
const realComment = new Node('div', { role: 'article' }, [
  new Node('span', {}, [], 'The meaning of life'),
  new Node('span', {}, [], 'Full story: https://kaylestore.net/i-took-care-of-my-85-year-old-neighbor/'),
  new Node('a', { href: '/themeaningoflife/posts/pfbid?comment_id=123' }, [], '48m'),
  new Node('span', {}, [], 'Reply')
]);
const body = new Node('body', {}, [realComment]);
global.document = { querySelectorAll: (selector) => body.querySelectorAll(selector) };
global.location = new URL('https://www.facebook.com/themeaningoflife/posts/pfbid');
const results = eval(leadLinkScanBrowserExpression('The meaning of life', 'default'));
if (results.length !== 1 || !results[0].href.includes('kaylestore.net/i-took-care')) {
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


def assert_detail_post_type_expression_classifies_business_types() -> None:
    script = """
import { detailPostTypeBrowserExpression } from './scripts/opencli_enrich_post_details.mjs';

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
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim());
    const result = [];
    const matches = (node, current) => {
      if (current === '[role="article"]') return node.attrs.role === 'article';
      if (current === 'article') return node.tagName === 'ARTICLE';
      if (current === 'a[href]') return node.tagName === 'A' && !!node.attrs.href;
      if (current === 'img[src]') return node.tagName === 'IMG' && !!node.attrs.src;
      if (current === 'video') return node.tagName === 'VIDEO';
      if (current.startsWith('[aria-label')) return !!node.attrs['aria-label'];
      if (current.startsWith('[style')) return !!node.attrs.style;
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
const body = new Node('body', {}, [
  new Node('div', { role: 'article' }, [
    new Node('span', {}, [], 'Story page'),
    new Node('p', {}, [], 'Full story in comment with enough article text to count as body.'),
    new Node('img', { src: 'https://cdn.test/image.jpg' }),
    new Node('a', { href: 'https://kaylestore.net/story' }, [], 'kaylestore.net')
  ])
]);
global.document = { querySelectorAll: (selector) => body.querySelectorAll(selector), body };
global.location = new URL('https://www.facebook.com/example/posts/1');
const result = eval(detailPostTypeBrowserExpression());
if (result.post_type !== '图文') {
  console.error(JSON.stringify(result, null, 2));
  process.exit(1);
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
const photoA = { post_url: 'https://www.facebook.com/photo.php?fbid=9876543212345678&set=p.9876543212345678' };
const photoB = { post_url: 'https://www.facebook.com/themeaningoflife88/photos/a.123/9876543212345678/?type=3' };
if (postKey(photoA) !== postKey(photoB)) {
  console.error(JSON.stringify({ photoA: postKey(photoA), photoB: postKey(photoB) }, null, 2));
  process.exit(6);
}
const videoA = { post_url: 'https://www.facebook.com/watch/?v=1234567890123456' };
const videoB = { post_url: 'https://www.facebook.com/storyhub/videos/1234567890123456/' };
if (postKey(videoA) !== postKey(videoB)) {
  console.error(JSON.stringify({ videoA: postKey(videoA), videoB: postKey(videoB) }, null, 2));
  process.exit(7);
}
if (postKey({ post_url: 'https://www.facebook.com/groups/778899/posts/112233445566?ref=share' }) !== 'group-post:778899:112233445566') {
  process.exit(8);
}
if (postKey({ post_url: 'https://www.facebook.com/share/p/abcDEF123/?mibextid=wwXIfr' }) !== 'share:p:abcDEF123') {
  process.exit(9);
}
if (!validCandidate(first)) process.exit(3);
if (!validCandidate({ post_url: first.post_url, story_summary: 'short' })) process.exit(4);
if (validCandidate({ story_summary: 'short text without any post URL' })) process.exit(5);
"""
    result = run(["node", "--input-type=module", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_extract_has_under_capture_guards() -> None:
    script_text = (ROOT / "scripts" / "opencli_extract_current_tab.mjs").read_text(encoding="utf-8")
    assert 'value("--max-snapshots", "32")' in script_text
    assert 'value("--min-snapshots", "6")' in script_text
    assert "minSnapshotsReached" in script_text
    assert "noMovementCount" in script_text
    assert "coverage_incomplete" in script_text
    assert "capture_complete" in script_text
    assert "已达到最大滚动快照数但最后一屏仍有新增候选" in script_text


def assert_opencli_extract_stable_end_is_complete_coverage() -> None:
    js = """
import { captureCoverageState } from './scripts/opencli_extract_current_tab.mjs';

const stable = captureCoverageState({
  snapshots: [
    { index: 0, new_posts: 4 },
    { index: 1, new_posts: 0 },
    { index: 2, new_posts: 0 },
  ],
  stopReason: 'stable_no_new_posts',
  maxSnapshots: 3,
});
if (stable.coverage_incomplete || !stable.capture_complete || stable.coverage_blocked) {
  console.error(JSON.stringify(stable, null, 2));
  process.exit(1);
}

const cappedWithNewPosts = captureCoverageState({
  snapshots: [
    { index: 0, new_posts: 4 },
    { index: 1, new_posts: 2 },
    { index: 2, new_posts: 1 },
  ],
  stopReason: 'max_snapshots',
  maxSnapshots: 3,
});
if (!cappedWithNewPosts.coverage_incomplete || cappedWithNewPosts.capture_complete) {
  console.error(JSON.stringify(cappedWithNewPosts, null, 2));
  process.exit(2);
}

const cappedWithoutNewPosts = captureCoverageState({
  snapshots: [
    { index: 0, new_posts: 4 },
    { index: 1, new_posts: 0 },
    { index: 2, new_posts: 0 },
  ],
  stopReason: 'max_snapshots',
  maxSnapshots: 3,
});
if (cappedWithoutNewPosts.coverage_incomplete || !cappedWithoutNewPosts.capture_complete) {
  console.error(JSON.stringify(cappedWithoutNewPosts, null, 2));
  process.exit(3);
}
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
    { post_url: 'https://facebook.com/example/posts/3', output_status: 'needs_enrichment', posted_at: '2026年6月1日 14:00', time_confirmed: true, summary_source: 'article', story_summary: '这篇故事讲述家庭冲突升级后，主角发现问题并及时反击的反转剧情。', lead_link_status: 'qualified', lead_link_source: 'comment', lead_url_raw: 'https://www.facebook.com/example/posts/3', landing_url: 'https://www.facebook.com/example/posts/3' },
  ],
  date_filtered_out: [{ post_url: 'https://facebook.com/example/posts/old' }],
};
const summary = buildCoverageSummary(payload, 4);
if (summary.input_posts !== 4 || summary.after_target_date_filter !== 3 || summary.ready_for_output !== 1 || summary.needs_enrichment !== 2) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(4);
}
if (summary.reason_counts.missing_qualified_comment_lead_link !== 2 || summary.reason_counts.engagement_unconfirmed !== 1) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(5);
}
"""
    result = run(["node", "--input-type=module", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_detail_enrichment_rejects_string_false_time() -> None:
    js = """
import { buildCoverageSummary, enrichmentReasonCounts, outputStatusFor, parseBool } from './scripts/opencli_enrich_post_details.mjs';

if (parseBool('false') !== false) process.exit(1);
if (parseBool('0') !== false) process.exit(2);
if (parseBool('confirmed') !== true) process.exit(3);

const dirtyReady = {
  post_url: 'https://facebook.com/example/posts/string-false-time',
  output_status: 'ready_for_output',
  posted_at: '2026年6月1日 12:00',
  time_confirmed: 'false',
  time_source: 'dom_aria_label',
  summary_source: 'article',
  story_summary: '这篇故事讲述家庭冲突升级后，主角发现问题并及时反击的反转剧情。',
  lead_link_status: 'qualified',
  lead_link_source: 'comment',
  lead_url_raw: 'https://site.test/a',
  landing_url: 'https://site.test/a',
};

if (outputStatusFor(dirtyReady) === 'ready_for_output') process.exit(4);
const counts = enrichmentReasonCounts([dirtyReady]);
if (counts.missing_confirmed_posted_at !== 1) {
  console.error(JSON.stringify(counts, null, 2));
  process.exit(5);
}
const summary = buildCoverageSummary({ posts: [dirtyReady], date_filtered_out: [] }, 1);
if (summary.ready_for_output !== 0 || summary.needs_enrichment !== 1) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(6);
}
"""
    result = run(["node", "--input-type=module", "-e", js])
    assert result.returncode == 0, result.stderr or result.stdout


def assert_opencli_detail_enrichment_rejects_copied_article_summary() -> None:
    js = """
import { buildCoverageSummary, enrichmentReasonCounts, hasValidStorySummary, outputStatusFor } from './scripts/opencli_enrich_post_details.mjs';

const copiedSummary = {
  post_url: 'https://facebook.com/example/posts/copied-summary',
  output_status: 'ready_for_output',
  posted_at: '2026年6月1日 12:00',
  time_confirmed: true,
  time_source: 'dom_aria_label',
  summary_source: 'article',
  story_summary: '母亲发现儿子冻结信用卡并控制公司资产后准备反击',
  article_material: {
    ok: true,
    title: '母亲发现儿子冻结信用卡并控制公司资产后准备反击',
    text_excerpt: '母亲发现儿子冻结信用卡并控制公司资产后准备反击，随后通过法律方式处理家庭资产问题。',
  },
  lead_link_status: 'qualified',
  lead_link_source: 'comment',
  lead_url_raw: 'https://site.test/a',
  landing_url: 'https://site.test/a',
};

if (hasValidStorySummary(copiedSummary)) process.exit(1);
if (outputStatusFor(copiedSummary) === 'ready_for_output') process.exit(2);
const counts = enrichmentReasonCounts([copiedSummary]);
if (counts.missing_article_summary !== 1) {
  console.error(JSON.stringify(counts, null, 2));
  process.exit(3);
}
const summary = buildCoverageSummary({ posts: [copiedSummary], date_filtered_out: [] }, 1);
if (summary.ready_for_output !== 0 || summary.needs_enrichment !== 1) {
  console.error(JSON.stringify(summary, null, 2));
  process.exit(4);
}

const rawPayloadCopied = {
  ...copiedSummary,
  article_material: undefined,
  raw_payload: JSON.stringify({ article_material: copiedSummary.article_material }),
};
if (hasValidStorySummary(rawPayloadCopied)) process.exit(5);
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
                    "account_name": "Example Page",
                    "account_url": "https://www.facebook.com/example",
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
    assert sync.returncode == 0, sync.stdout
    assert '"audit_output": true' in sync.stdout
    assert '"output_candidates": 13' in sync.stdout

    audit = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--sync-audit", "--dry-run"])
    assert audit.returncode == 0, audit.stdout
    assert '"audit_output": true' in audit.stdout
    assert '"output_candidates": 13' in audit.stdout


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
    task_summary = enqueue_enrichment_tasks_for_posts(conn, [post])
    repeat_summary = enqueue_enrichment_tasks_for_posts(conn, [post])
    tasks = pending_enrichment_tasks(conn, limit=20)
    assert sorted(task["stage"] for task in tasks) == [
        "article_material",
        "detail_time",
        "engagement",
        "lead_link",
        "post_type",
        "summary",
    ]
    assert task_summary["candidate_count"] == 1
    assert task_summary["open_task_count"] == 6
    assert task_summary["stage_counts"] == {
        "article_material": 1,
        "detail_time": 1,
        "engagement": 1,
        "lead_link": 1,
        "post_type": 1,
        "summary": 1,
    }
    assert repeat_summary["open_stage_counts"] == task_summary["stage_counts"]


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
    detail_tasks = [task for task in pending_enrichment_tasks(conn, stages=["detail_time", "lead_link", "engagement", "post_type"], limit=20)]
    assert sorted(task["stage"] for task in detail_tasks) == ["detail_time", "engagement", "lead_link", "post_type"]

    units, missing = enrichment_worker.detail_units_for_tasks(conn, detail_tasks)
    assert missing == 0
    assert len(units) == 1
    assert units[0]["key"] == post["canonical_post_url"]
    assert sorted(units[0]["stages"]) == ["detail_time", "engagement", "lead_link", "post_type"]
    assert sorted(task["stage"] for task in units[0]["tasks"]) == ["detail_time", "engagement", "lead_link", "post_type"]

    batches = enrichment_worker.batches_for_detail_units(units, batch_size=2)
    assert len(batches) == 1
    assert len(batches[0]) == 1


def assert_enrichment_worker_requeues_opencli_session_busy(tmp_path: Path) -> None:
    config = tmp_path / "settings_worker_session_busy.yaml"
    db_path = tmp_path / "worker-session-busy.sqlite"
    fake_bin = tmp_path / "bin-session-busy"
    fake_node = fake_bin / "node"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sample = tmp_path / "session_busy_posts.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Busy Page",
                        "account_url": "https://www.facebook.com/busypage",
                        "post_url": "https://www.facebook.com/busypage/posts/one",
                        "relative_time_text": "1h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-03T12:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    fake_bin.mkdir()
    fake_node.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({
  "ok": False,
  "status": "opencli_session_busy",
  "action_required": "retry_later",
  "message": "detail navigation already running"
}, ensure_ascii=False))
raise SystemExit(73)
""",
        encoding="utf-8",
    )
    fake_node.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    worker = run(
        [
            PYTHON,
            "scripts/enrichment_worker.py",
            "--config",
            str(config),
            "--stages",
            "detail_time",
            "--account-url",
            "https://www.facebook.com/busypage",
            "--account-type",
            "competitor",
            "--date",
            "260603",
            "--limit",
            "10",
        ],
        env=env,
    )
    assert worker.returncode == 0, worker.stdout
    data = json.loads(worker.stdout)
    assert data["run_status"] == "incomplete_pending_tasks"
    assert data["retry_later"] is True
    assert data["requeued"] == 1
    assert data["failed"] == 0
    assert data["task_counts"].get("detail_time:pending") == 1
    assert "detail_time:failed" not in data["task_counts"]

    sys.path.insert(0, str(ROOT / "scripts"))
    from store import connect

    conn = connect(db_path)
    task = conn.execute("SELECT status, attempts, next_run_at, locked_at FROM enrichment_tasks WHERE stage = 'detail_time'").fetchone()
    assert task["status"] == "pending"
    assert task["attempts"] == 0
    assert task["next_run_at"]
    assert task["locked_at"] is None


def assert_enrichment_worker_lead_stage_requires_external_landing_url() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import enrichment_worker

    estimated_time = {
        "posted_at": "2026年6月3日 10:00",
        "time_confirmed": True,
        "time_source": "relative_estimated",
    }
    exact_time = {
        "posted_at": "2026年6月3日 10:00",
        "time_confirmed": True,
        "time_source": "dom_aria_label",
    }
    assert enrichment_worker.detail_stage_satisfied(estimated_time, "detail_time") is False
    assert enrichment_worker.detail_stage_satisfied(exact_time, "detail_time") is True

    low_quality_engagement = {
        "likes": 2,
        "comments": 3,
        "shares": 1,
    }
    complete_engagement = {
        "likes": 8,
        "comments": 3,
        "shares": 1,
    }
    missing_engagement = {
        "likes": 8,
    }
    invalid_post_type = {"post_type": "文本"}
    valid_post_type = {"post_type": "图文"}
    strict_audit_config = {
        "quality_audit": {
            "low_like_threshold": 10,
            "required_post_types": ["视频"],
        }
    }
    assert enrichment_worker.detail_stage_satisfied(low_quality_engagement, "engagement") is False
    assert enrichment_worker.detail_stage_satisfied(missing_engagement, "engagement") is False
    assert enrichment_worker.detail_stage_satisfied(complete_engagement, "engagement") is True
    assert enrichment_worker.detail_stage_satisfied(complete_engagement, "engagement", strict_audit_config) is False
    assert enrichment_worker.detail_stage_satisfied(invalid_post_type, "post_type") is False
    assert enrichment_worker.detail_stage_satisfied(valid_post_type, "post_type") is True
    assert enrichment_worker.detail_stage_satisfied(valid_post_type, "post_type", strict_audit_config) is False

    internal_lead = {
        "lead_link_status": "qualified",
        "lead_link_source": "comment",
        "landing_url": "https://www.facebook.com/example/posts/not-a-story",
        "article_url": "https://www.facebook.com/example/posts/not-a-story",
    }
    external_lead = {
        "lead_link_status": "qualified",
        "lead_link_source": "comment_reply",
        "lead_url_raw": "https://story.example/usable",
        "landing_url": "https://story.example/usable",
        "article_url": "https://story.example/usable",
    }
    assert enrichment_worker.detail_stage_satisfied(internal_lead, "lead_link") is False
    assert enrichment_worker.detail_stage_satisfied(external_lead, "lead_link") is True


def assert_stale_running_enrichment_tasks_are_recovered(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, pending_enrichment_tasks, upsert_post

    conn = connect(tmp_path / "stale-running.sqlite")
    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/stale-running",
            "post_time_text": "1h",
            "story_summary": "Visible homepage candidate.",
            "crawled_at": "2026-05-28T10:00:00",
        }
    )
    upsert_post(conn, post)
    enqueue_enrichment_tasks_for_posts(conn, [post])
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'running',
            locked_at = '2000-01-01T00:00:00',
            next_run_at = NULL
        WHERE stage = 'detail_time'
        """
    )
    conn.commit()
    tasks = pending_enrichment_tasks(conn, stages=["detail_time"], limit=10, stale_running_seconds=60)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"


def assert_enqueue_does_not_steal_active_running_tasks(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, pending_enrichment_tasks, upsert_post

    conn = connect(tmp_path / "active-running.sqlite")
    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/active-running",
            "post_time_text": "1h",
            "story_summary": "Visible homepage candidate.",
            "crawled_at": "2026-06-03T10:00:00",
        }
    )
    upsert_post(conn, post)
    enqueue_enrichment_tasks_for_posts(conn, [post])
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'running',
            locked_at = CURRENT_TIMESTAMP,
            next_run_at = NULL
        WHERE stage = 'detail_time'
        """
    )
    conn.commit()
    enqueue_enrichment_tasks_for_posts(conn, [post])
    running = conn.execute("SELECT status FROM enrichment_tasks WHERE stage = 'detail_time'").fetchone()
    assert running["status"] == "running"
    tasks = pending_enrichment_tasks(conn, stages=["detail_time"], limit=10, stale_running_seconds=3600)
    assert tasks == []


def assert_enqueue_reopens_done_tasks_when_fields_are_missing_again(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, pending_enrichment_tasks, upsert_post

    conn = connect(tmp_path / "reopen-done-tasks.sqlite")
    post = normalize_post(
        {
            "post_url": "https://www.facebook.com/example/posts/reopen-post-type",
            "posted_at": "2026年6月3日 10:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "lead_url_raw": "https://story.example/reopen",
            "landing_url": "https://story.example/reopen",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "article_url": "https://story.example/reopen",
            "story_summary": VALID_CN_SUMMARY,
            "summary_source": "article",
            "likes": 38,
            "comments": 12,
            "shares": 4,
            "post_type": "",
        }
    )
    upsert_post(conn, post)
    first = enqueue_enrichment_tasks_for_posts(conn, [post])
    assert first["stage_counts"] == {"post_type": 1}
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'done',
            last_error = 'old success marker',
            next_run_at = NULL
        WHERE stage = 'post_type'
        """
    )
    conn.commit()

    second = enqueue_enrichment_tasks_for_posts(conn, [post])
    assert second["stage_counts"] == {"post_type": 1}
    assert second["open_stage_counts"] == {"post_type": 1}
    tasks = pending_enrichment_tasks(conn, stages=["post_type"], limit=10)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["last_error"] is None


def assert_enrichment_worker_scopes_tasks_to_account(tmp_path: Path) -> None:
    config = tmp_path / "settings_worker_scope.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'worker-scope.sqlite'}"),
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, upsert_post

    conn = connect(tmp_path / "worker-scope.sqlite")
    target = normalize_post(
        {
            "account_name": "Target",
            "account_url": "https://www.facebook.com/target",
            "post_url": "https://www.facebook.com/target/posts/one",
            "post_time_text": "1h",
            "story_summary": "Visible target candidate.",
            "crawled_at": "2026-06-02T12:00:00",
        }
    )
    other = normalize_post(
        {
            "account_name": "Other",
            "account_url": "https://www.facebook.com/other",
            "post_url": "https://www.facebook.com/other/posts/one",
            "post_time_text": "1h",
            "story_summary": "Visible other candidate.",
            "crawled_at": "2026-06-02T12:00:00",
        }
    )
    for post in (target, other):
        upsert_post(conn, post)
        enqueue_enrichment_tasks_for_posts(conn, [post])

    worker = run(
        [
            PYTHON,
            "scripts/enrichment_worker.py",
            "--config",
            str(config),
            "--stages",
            "summary",
            "--date",
            "260602",
            "--account-url",
            "https://www.facebook.com/target",
            "--account-type",
            "competitor",
            "--limit",
            "10",
        ]
    )
    assert worker.returncode == 2, worker.stdout
    data = json.loads(worker.stdout)
    assert data["run_status"] == "needs_codex_summary"
    assert data["codex_summary_required"] is True
    assert data["scope"]["enabled"] is True
    assert data["scope"]["post_count"] == 1
    assert data["input_tasks"] == 1
    assert data["task_counts"].get("summary:failed") == 1
    assert "Other" not in worker.stdout


def assert_enrichment_worker_scope_includes_unknown_date_candidates(tmp_path: Path) -> None:
    config = tmp_path / "settings_worker_unknown_date.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'worker-unknown-date.sqlite'}"
        ),
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, upsert_post

    conn = connect(tmp_path / "worker-unknown-date.sqlite")
    known = normalize_post(
        {
            "account_name": "Target",
            "account_url": "https://www.facebook.com/target",
            "post_url": "https://www.facebook.com/target/posts/known",
            "posted_date": "260602",
            "post_time_text": "1h",
            "story_summary": "Visible target candidate.",
            "crawled_at": "2026-06-02T12:00:00",
        }
    )
    unknown = normalize_post(
        {
            "account_name": "Target",
            "account_url": "https://www.facebook.com/target",
            "post_url": "https://www.facebook.com/target/posts/date-pending",
            "story_summary": "Visible target candidate with unknown date.",
            "crawled_at": "2026-06-02T12:00:00",
        }
    )
    for post in (known, unknown):
        upsert_post(conn, post)
        enqueue_enrichment_tasks_for_posts(conn, [post])

    worker = run(
        [
            PYTHON,
            "scripts/enrichment_worker.py",
            "--config",
            str(config),
            "--stages",
            "detail_time",
            "--date",
            "260602",
            "--account-url",
            "https://www.facebook.com/target",
            "--account-type",
            "competitor",
            "--limit",
            "10",
        ]
    )
    assert worker.returncode == 1, worker.stdout
    data = json.loads(worker.stdout)
    assert data["scope"]["enabled"] is True
    assert data["scope"]["post_count"] == 2
    assert data["input_tasks"] == 2


def assert_run_account_job_resume_status_reports_incomplete(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'account-job.sqlite'}"),
        encoding="utf-8",
    )
    sample = tmp_path / "account_job.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Resume Page",
                        "account_url": "https://www.facebook.com/resumepage",
                        "post_url": "https://www.facebook.com/resumepage/posts/one",
                        "relative_time_text": "1h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-02T12:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260602",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
            "--allow-incomplete-success",
        ]
    )
    assert job.returncode == 0, job.stdout
    data = json.loads(job.stdout)
    assert data["post_count"] == 1
    assert data["run_status"] == "incomplete_pending_tasks"
    assert data["complete"] is False
    assert data["feishu_sync"]["run_status"] == "synced_ledger_incomplete"
    assert data["enrichment_completion"]["open_task_count"] > 0
    assert data["quality_summary"]["run_status"] == "incomplete_pending_tasks"
    assert data["quality_summary"]["coverage_health"] == "not_run"
    assert data["quality_summary"]["post_count"] == 1
    assert data["quality_summary"]["ledger_candidate_count"] == 1
    assert data["quality_summary"]["ledger_usable_rate"] == 1.0
    assert data["quality_summary"]["final_usable_count"] == 0
    assert data["quality_summary"]["final_usable_rate"] == 0.0
    assert data["quality_summary"]["open_task_count"] > 0
    assert data["quality_summary"]["open_task_stage_counts"]["detail_time"] == 1
    assert data["quality_summary"]["missing_stage_counts"]["detail_time"] == 1
    assert data["quality_summary"]["stage_pressure"][0]["stage"] == "detail_time"
    assert any("精确时间" in note for note in data["quality_summary"]["stage_pressure_notes"])
    assert data["quality_summary"]["top_field_gaps"]
    assert data["quality_summary"]["feishu_sync"]["enabled"] is True
    assert data["quality_summary"]["feishu_sync"]["run_status"] == "synced_ledger_incomplete"
    blocker_codes = [item["code"] for item in data["completion_blockers"]]
    assert blocker_codes[0] == "stage_detail_time"
    assert "field_gaps" in blocker_codes
    assert data["completion_blockers"] == data["quality_summary"]["completion_blockers"]
    assert any(item["reason"] == "pending_enrichment" for item in data["next_commands"])
    assert "--resume-only" in data["next_commands"][0]["command"]
    assert "--force-recover-running" in data["next_commands"][0]["command"]

    default_strict_job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260602",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
        ]
    )
    assert default_strict_job.returncode == 2, default_strict_job.stdout
    default_strict_data = json.loads(default_strict_job.stdout)
    assert default_strict_data["run_status"] == "incomplete_pending_tasks"
    assert default_strict_data["exit_status_reason"] == "incomplete_run_status"

    strict_job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260602",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
        ]
    )
    assert strict_job.returncode == 2, strict_job.stdout
    strict_data = json.loads(strict_job.stdout)
    assert strict_data["run_status"] == "incomplete_pending_tasks"
    assert strict_data["complete"] is False
    assert strict_data["exit_status_reason"] == "incomplete_run_status"


def assert_run_account_job_quality_thresholds_fail_low_usable_rate(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_threshold.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'account-job-threshold.sqlite'}"),
        encoding="utf-8",
    )
    sample = tmp_path / "account_job_threshold.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Threshold Page",
                        "account_url": "https://www.facebook.com/thresholdpage",
                        "post_url": "https://www.facebook.com/thresholdpage/posts/incomplete",
                        "relative_time_text": "1h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-02T12:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/thresholdpage",
            "--account-name",
            "Threshold Page",
            "--target-date",
            "260602",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
            "--min-ledger-usable-rate",
            "1",
            "--min-final-usable-rate",
            "0.9",
        ]
    )
    assert job.returncode == 2, job.stdout
    data = json.loads(job.stdout)
    assert data["run_status"] == "incomplete_pending_tasks"
    assert data["complete"] is False
    assert data["quality_threshold_failed"] is True
    assert data["exit_status_reason"] == "quality_threshold_failed"
    thresholds = data["quality_summary"]["quality_thresholds"]
    assert thresholds["enabled"] is True
    assert thresholds["ok"] is False
    assert thresholds["thresholds"]["min_ledger_usable_rate"] == 1.0
    assert thresholds["thresholds"]["min_final_usable_rate"] == 0.9
    assert data["quality_summary"]["ledger_usable_rate"] == 1.0
    assert data["quality_summary"]["final_usable_rate"] == 0.0
    assert [failure["metric"] for failure in thresholds["failures"]] == ["final_usable_rate"]
    assert any(item["reason"] == "quality_threshold_failed" for item in data["next_commands"])


def assert_run_account_job_quality_threshold_failure_has_recovery_command() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/thresholdpage",
            "account_name": "Threshold Page",
            "account_type": "competitor",
            "sync": True,
            "dry_run": True,
            "strict_ready_only": False,
            "resume_only": False,
            "max_snapshots": 32,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "expected_post_count": 0,
            "expected_labels": "",
            "require_coverage_complete": True,
            "min_ledger_usable_rate": 1.0,
            "min_final_usable_rate": 0.9,
            "min_completion_rate": 0.8,
            "min_expected_post_coverage_rate": 0.7,
            "min_expected_label_coverage_rate": 0.6,
        },
    )()
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260602"],
        run_status="quality_threshold_failed",
        completion={"post_count": 3},
        discover_coverage={"source": "discover", "complete": True, "incomplete": False, "reasons": []},
    )
    assert commands[0]["reason"] == "quality_threshold_failed"
    assert "--resume-only" in commands[0]["command"]
    assert "--status-only" in commands[0]["command"]
    assert "--force-recover-running" in commands[0]["command"]
    assert "--require-coverage-complete" in commands[0]["command"]
    assert "--min-ledger-usable-rate 1.0" in commands[0]["command"]
    assert "--min-final-usable-rate 0.9" in commands[0]["command"]
    assert "--min-completion-rate 0.8" in commands[0]["command"]
    assert "--min-expected-post-coverage-rate 0.7" in commands[0]["command"]
    assert "--min-expected-label-coverage-rate 0.6" in commands[0]["command"]


def assert_run_account_job_resume_blocks_opencli_before_detail_tasks(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_resume_opencli.yaml"
    fake_opencli = tmp_path / "fake-opencli-resume"
    db_path = tmp_path / "account-job-resume-opencli.sqlite"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text(
        """#!/bin/sh
echo 'opencli unavailable' >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_opencli.chmod(0o755)
    sample = tmp_path / "resume_opencli.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Resume Page",
                        "account_url": "https://www.facebook.com/resumepage",
                        "post_url": "https://www.facebook.com/resumepage/posts/opencli-needed",
                        "relative_time_text": "1h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-02T12:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260602",
            "--resume-only",
            "--sync",
            "--dry-run",
        ]
    )
    assert job.returncode == 1, job.stdout
    data = json.loads(job.stdout)
    assert data["run_status"] == "blocked_opencli"
    assert data["complete"] is False
    assert data["post_count"] == 1
    assert data["opencli_preflight"]["ok"] is False
    assert data["task_counts"].get("detail_time:pending") == 1
    assert "detail_time:failed" not in data["task_counts"]
    assert data["quality_summary"]["run_status"] == "blocked_opencli"
    assert data["quality_summary"]["coverage_health"] == "not_run"
    assert data["quality_summary"]["post_count"] == 1
    assert data["quality_summary"]["ledger_candidate_count"] == 1
    assert data["quality_summary"]["final_usable_rate"] == 0.0
    assert data["quality_summary"]["open_task_count"] > 0
    assert "worker_passes" not in data
    assert any(item["reason"] == "blocked_opencli" for item in data["next_commands"])
    assert any(item["reason"] == "resume_after_opencli" for item in data["next_commands"])
    resume = next(item for item in data["next_commands"] if item["reason"] == "resume_after_opencli")
    assert "--resume-only" in resume["command"]
    assert "--force-recover-running" in resume["command"]
    assert not any(item["reason"] == "rerun_full_capture" for item in data["next_commands"])


def assert_run_account_job_recovers_scoped_running_tasks(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_running.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    db_path = tmp_path / "account-job-running.sqlite"
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sample = tmp_path / "account_job_running.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Resume Page",
                        "account_url": "https://www.facebook.com/resumepage",
                        "post_url": "https://www.facebook.com/resumepage/posts/running",
                        "relative_time_text": "1h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-03T10:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    sys.path.insert(0, str(ROOT / "scripts"))
    from store import connect

    conn = connect(db_path)
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'running',
            locked_at = '2000-01-01T00:00:00',
            next_run_at = NULL
        WHERE stage = 'detail_time'
        """
    )
    conn.commit()
    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260603",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
            "--force-recover-running",
            "--allow-incomplete-success",
        ]
    )
    assert job.returncode == 0, job.stdout
    data = json.loads(job.stdout)
    assert data["recovered_running_tasks"] == 1
    assert data["task_counts"].get("detail_time:pending") == 1
    assert "detail_time:running" not in data["task_counts"]
    assert data["enrichment_completion"]["open_task_count"] > 0


def assert_run_account_job_does_not_recover_fresh_running_tasks(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_fresh_running.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    db_path = tmp_path / "account-job-fresh-running.sqlite"
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sample = tmp_path / "account_job_fresh_running.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Resume Page",
                        "account_url": "https://www.facebook.com/resumepage",
                        "post_url": "https://www.facebook.com/resumepage/posts/fresh-running",
                        "relative_time_text": "1h",
                        "story_summary": "Visible homepage candidate.",
                        "crawled_at": "2026-06-03T10:00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    sys.path.insert(0, str(ROOT / "scripts"))
    from store import connect

    conn = connect(db_path)
    conn.execute(
        """
        UPDATE enrichment_tasks
        SET status = 'running',
            locked_at = datetime('now', '-60 seconds'),
            next_run_at = NULL
        WHERE stage = 'detail_time'
        """
    )
    conn.commit()
    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260603",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
            "--allow-incomplete-success",
        ]
    )
    assert job.returncode == 0, job.stdout
    data = json.loads(job.stdout)
    assert data["recovered_running_tasks"] == 0
    assert data["task_counts"].get("detail_time:running") == 1
    assert data["run_status"] == "incomplete_pending_tasks"


def assert_run_account_job_next_commands_force_recover_running() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example Page",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "enrichment_limit": 17,
            "resume_stale_running_seconds": 90,
            "expected_post_count": 0,
            "expected_labels": "",
        },
    )()
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status="incomplete_pending_tasks",
        completion={"open_task_count": 4},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    pending = next(item for item in commands if item["reason"] == "pending_enrichment")
    assert "--resume-only" in pending["command"]
    assert "--force-recover-running" in pending["command"]
    assert "--max-resume-passes 2" in pending["command"]
    assert "--enrichment-limit 17" in pending["command"]
    assert "--resume-stale-running-seconds 90" in pending["command"]


def assert_run_account_job_recovery_commands_preserve_resume_budget() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    base_attrs = {
        "config": "config/settings.yaml",
        "account_url": "https://www.facebook.com/budgetpage",
        "account_name": "Budget Page",
        "account_type": "competitor",
        "sync": True,
        "dry_run": True,
        "strict_ready_only": False,
        "max_snapshots": 32,
        "min_snapshots": 6,
        "max_resume_passes": 4,
        "enrichment_limit": 25,
        "resume_stale_running_seconds": 120,
        "expected_post_count": 13,
        "expected_labels": "1h,2h",
        "require_coverage_complete": False,
        "min_ledger_usable_rate": 0.0,
        "min_final_usable_rate": 0.0,
        "min_completion_rate": 0.0,
        "min_expected_post_coverage_rate": 0.0,
        "min_expected_label_coverage_rate": 0.0,
    }
    completion = {"post_count": 3, "open_task_count": 2, "has_auto_enrichment_work": True}
    discover = {"source": "discover", "complete": False, "incomplete": True, "reasons": ["coverage_incomplete"]}

    capture_args = type("Args", (), {**base_attrs, "resume_only": False})()
    coverage_commands = run_account_job.next_commands_for_status(
        args=capture_args,
        target_dates=["260603"],
        run_status="coverage_incomplete",
        completion=completion,
        discover_coverage=discover,
    )
    assert coverage_commands[0]["reason"] == "pending_enrichment"
    coverage_command = next(item for item in coverage_commands if item["reason"] == "coverage_incomplete")
    coverage_parts = shlex.split(coverage_command["command"])
    assert coverage_parts[coverage_parts.index("--max-resume-passes") + 1] == "4"
    assert coverage_parts[coverage_parts.index("--enrichment-limit") + 1] == "25"
    assert coverage_parts[coverage_parts.index("--resume-stale-running-seconds") + 1] == "120"
    assert coverage_parts[coverage_parts.index("--expected-post-count") + 1] == "13"
    assert coverage_parts[coverage_parts.index("--expected-labels") + 1] == "1h,2h"
    assert coverage_parts[coverage_parts.index("--max-snapshots") + 1] == "44"

    sync_commands = run_account_job.next_commands_for_status(
        args=capture_args,
        target_dates=["260603"],
        run_status="sync_failed",
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert sync_commands[0]["reason"] == "pending_enrichment"
    assert sync_commands[1]["reason"] == "sync_failed"
    sync_parts = shlex.split(sync_commands[1]["command"])
    assert sync_parts[sync_parts.index("--max-resume-passes") + 1] == "4"
    assert sync_parts[sync_parts.index("--enrichment-limit") + 1] == "25"
    assert sync_parts[sync_parts.index("--resume-stale-running-seconds") + 1] == "120"
    assert "--resume-only" in sync_parts
    assert "--force-recover-running" in sync_parts

    resume_args = type("Args", (), {**base_attrs, "resume_only": True})()
    blocked_auth_commands = run_account_job.next_commands_for_status(
        args=resume_args,
        target_dates=["260603"],
        run_status="blocked_auth",
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert blocked_auth_commands[0]["reason"] == "blocked_auth"
    blocked_auth_parts = shlex.split(blocked_auth_commands[0]["command"])
    assert blocked_auth_parts[blocked_auth_parts.index("--max-resume-passes") + 1] == "4"
    assert blocked_auth_parts[blocked_auth_parts.index("--enrichment-limit") + 1] == "25"
    assert blocked_auth_parts[blocked_auth_parts.index("--resume-stale-running-seconds") + 1] == "120"
    assert "--resume-only" in blocked_auth_parts

    human_commands = run_account_job.next_commands_for_status(
        args=resume_args,
        target_dates=["260603"],
        run_status="human_intervention_required",
        completion=completion,
        discover_coverage={"source": "worker", "complete": True, "incomplete": False, "reasons": ["visitor_preview"]},
    )
    assert human_commands[0]["reason"] == "human_intervention_required"
    human_parts = shlex.split(human_commands[0]["command"])
    assert human_parts[human_parts.index("--max-resume-passes") + 1] == "4"
    assert human_parts[human_parts.index("--enrichment-limit") + 1] == "25"
    assert human_parts[human_parts.index("--resume-stale-running-seconds") + 1] == "120"
    assert "--resume-only" in human_parts


def assert_run_account_job_does_not_resume_empty_coverage_scope() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/emptycoverage",
            "account_name": "Empty Coverage",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "strict_ready_only": False,
            "resume_only": False,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "enrichment_limit": 25,
            "resume_stale_running_seconds": 120,
            "expected_post_count": 13,
            "expected_labels": "1h,2h",
            "require_coverage_complete": False,
            "min_ledger_usable_rate": 0.0,
            "min_final_usable_rate": 0.0,
            "min_completion_rate": 0.0,
            "min_expected_post_coverage_rate": 0.0,
            "min_expected_label_coverage_rate": 0.0,
        },
    )()
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status="coverage_incomplete",
        completion={
            "post_count": 0,
            "open_task_count": 0,
            "auto_open_task_count": 0,
            "coverage_incomplete_count": 1,
            "missing_stage_counts": {"coverage": 1},
            "open_task_stage_counts": {},
        },
        discover_coverage={"source": "discover", "complete": False, "incomplete": True, "reasons": ["coverage_incomplete"]},
    )
    assert [item["reason"] for item in commands] == ["coverage_incomplete"]
    assert "--resume-only" not in commands[0]["command"]
    assert "--max-snapshots" in commands[0]["command"]


def assert_run_account_job_reports_unsynced_local_completion_command() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job
    import run_accounts_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/unsyncedpage",
            "account_name": "Unsynced Page",
            "account_type": "competitor",
            "sync": False,
            "dry_run": False,
            "strict_ready_only": False,
            "resume_only": False,
            "max_snapshots": 32,
            "min_snapshots": 6,
            "max_resume_passes": 4,
            "enrichment_limit": 25,
            "resume_stale_running_seconds": 120,
            "expected_post_count": 0,
            "expected_labels": "",
            "require_coverage_complete": False,
            "min_ledger_usable_rate": 0.0,
            "min_final_usable_rate": 0.0,
            "min_completion_rate": 0.0,
            "min_expected_post_coverage_rate": 0.0,
            "min_expected_label_coverage_rate": 0.0,
        },
    )()
    completion = {
        "post_count": 2,
        "has_incomplete_enrichment": False,
        "requires_codex_summary_count": 0,
        "coverage_incomplete_count": 0,
    }
    captured_status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import={"ok": True},
        worker_passes=[],
        sync_result={"ok": True, "skipped": True, "run_status": "not_synced", "stage": "sync_disabled"},
        completion=completion,
    )
    assert captured_status == "captured_not_synced"
    captured_commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status=captured_status,
        completion=completion,
        discover_coverage={"source": "discover", "complete": True, "incomplete": False, "reasons": []},
    )
    assert captured_commands[0]["reason"] == "captured_not_synced"
    captured_parts = shlex.split(captured_commands[0]["command"])
    assert "scripts/run_account_job.py" in captured_parts
    assert "--sync" in captured_parts
    assert "--resume-only" in captured_parts
    assert "--force-recover-running" in captured_parts
    assert captured_parts[captured_parts.index("--target-date") + 1] == "260603"
    assert captured_parts[captured_parts.index("--max-resume-passes") + 1] == "4"
    assert captured_parts[captured_parts.index("--enrichment-limit") + 1] == "25"
    assert captured_parts[captured_parts.index("--resume-stale-running-seconds") + 1] == "120"

    resumed_status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[{"ok": True, "run_status": "complete"}],
        sync_result={"ok": True, "skipped": True, "run_status": "not_synced", "stage": "sync_disabled"},
        completion=completion,
    )
    assert resumed_status == "resumed_not_synced"
    resumed_commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status=resumed_status,
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert resumed_commands[0]["reason"] == "resumed_not_synced"
    assert "--sync" in shlex.split(resumed_commands[0]["command"])

    account_summary = {
        "complete": False,
        "run_status": captured_status,
        "next_commands": captured_commands,
    }
    assert run_accounts_job.next_auto_follow_command(
        account_summary,
        {"account_url": "https://www.facebook.com/unsyncedpage"},
    ) == []


def assert_run_account_job_reports_worker_retry_later() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/retrylater",
            "account_name": "Retry Later Page",
            "account_type": "competitor",
            "enrichment_limit": 10,
        },
    )()
    original_run_command = run_account_job.run_command

    class FakeWorker:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": True,
                "run_status": "incomplete_pending_tasks",
                "retry_later": True,
                "retry_later_reasons": ["detail navigation already running"],
                "requeued": 2,
                "failed": 0,
            },
            ensure_ascii=False,
        )
        stderr = ""

    try:
        run_account_job.run_command = lambda _command: FakeWorker()
        worker_pass = run_account_job.run_worker_pass(args, target_dates=["260603"], pass_index=1)
    finally:
        run_account_job.run_command = original_run_command
    assert worker_pass["ok"] is True
    assert worker_pass["retry_later"] is True
    assert worker_pass["retry_later_count"] == 2
    assert worker_pass["retry_later_reasons"] == ["detail navigation already running"]
    retry_summary = run_account_job.worker_retry_summary([worker_pass])
    quality = run_account_job.account_job_quality_summary(
        run_status="incomplete_pending_tasks",
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
        completion={"post_count": 2, "open_task_count": 2},
        sync_result={"ok": True, "skipped": True, "run_status": "not_synced"},
        worker_retry=retry_summary,
    )
    assert quality["worker_retry_later"] is True
    assert quality["worker_retry_later_count"] == 2
    assert quality["worker_retry_later_reasons"] == ["detail navigation already running"]
    assert quality["completion_blockers"][0]["code"] == "worker_retry_later"
    assert quality["completion_blockers"][0]["metrics"]["retry_later_count"] == 2

    class FakeWorkerWithoutCount:
        returncode = 0
        stdout = json.dumps(
            {
                "ok": True,
                "run_status": "incomplete_pending_tasks",
                "retry_later": True,
                "retry_later_reasons": ["opencli_session_busy"],
                "failed": 0,
            },
            ensure_ascii=False,
        )
        stderr = ""

    try:
        run_account_job.run_command = lambda _command: FakeWorkerWithoutCount()
        worker_pass = run_account_job.run_worker_pass(args, target_dates=["260603"], pass_index=2)
    finally:
        run_account_job.run_command = original_run_command
    assert worker_pass["retry_later"] is True
    assert worker_pass["retry_later_count"] == 0
    retry_summary = run_account_job.worker_retry_summary([worker_pass])
    assert retry_summary["retry_later"] is True
    assert retry_summary["retry_later_count"] == 0


def assert_run_account_job_summary_only_next_command_exports_requests() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example Page",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "expected_post_count": 0,
            "expected_labels": "",
        },
    )()
    completion = {
        "open_task_count": 1,
        "summary_open_task_count": 1,
        "auto_open_task_count": 0,
        "requires_codex_summary_count": 1,
        "has_summary_only_work": True,
        "has_auto_enrichment_work": False,
    }
    status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[],
        sync_result={"ok": True},
        completion=completion,
    )
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status=status,
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert status == "needs_codex_summary"
    assert [item["reason"] for item in commands] == ["needs_codex_summary", "needs_codex_summary"]
    assert "export_summary_requests.py" in commands[0]["command"]
    assert "--date 260603" in commands[0]["command"]
    assert "--account-url https://www.facebook.com/example" in commands[0]["command"]
    assert "--account-type competitor" in commands[0]["command"]
    assert "--resume-only" not in commands[0]["command"]
    assert "run_account_job.py" in commands[1]["command"]
    assert "--resume-only" in commands[1]["command"]
    assert "--force-recover-running" in commands[1]["command"]
    assert "--max-resume-passes 2" in commands[1]["command"]
    assert "--account-url https://www.facebook.com/example" in commands[1]["command"]
    range_commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260602", "260603"],
        run_status=status,
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert "summary_requests_260602_260603.json" in range_commands[0]["command"]
    assert "--start-date 260602 --end-date 260603" in range_commands[0]["command"]
    assert "--target-date 260603" in range_commands[1]["command"]

    failed_commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status="summary_auto_apply_failed",
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert [item["reason"] for item in failed_commands] == ["summary_auto_apply_failed", "summary_auto_apply_failed"]
    assert "export_summary_requests.py" in failed_commands[0]["command"]
    assert "run_account_job.py" in failed_commands[1]["command"]
    assert "--resume-only" in failed_commands[1]["command"]
    assert "--force-recover-running" in failed_commands[1]["command"]


def assert_run_account_job_skips_worker_for_summary_only_completion() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    summary_only = {
        "open_task_count": 2,
        "summary_open_task_count": 2,
        "auto_open_task_count": 0,
        "requires_codex_summary_count": 2,
        "has_summary_only_work": True,
        "has_auto_enrichment_work": False,
    }
    mixed = {
        **summary_only,
        "auto_open_task_count": 1,
        "has_auto_enrichment_work": True,
        "open_task_stage_counts": {"article_material": 1, "summary": 2},
    }
    no_work = {**summary_only, "open_task_count": 0}
    assert run_account_job.should_run_worker_for_completion(summary_only) is False
    assert run_account_job.should_run_worker_for_completion(mixed) is True
    assert run_account_job.should_run_worker_for_completion(no_work) is False


def assert_run_account_job_worker_pass_surfaces_summary_required() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    original_run_command = run_account_job.run_command
    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example Page",
            "account_type": "competitor",
            "enrichment_limit": 50,
        },
    )()

    class FakeSummaryRequiredWorker:
        returncode = 2
        stdout = json.dumps(
            {
                "ok": False,
                "run_status": "needs_codex_summary",
                "codex_summary_required": True,
                "codex_summary_required_count": 1,
                "codex_summary_required_urls": ["https://facebook.com/example/posts/summary-needed"],
                "failed": 1,
            },
            ensure_ascii=False,
        )
        stderr = ""

    try:
        run_account_job.run_command = lambda _command: FakeSummaryRequiredWorker()
        worker_pass = run_account_job.run_worker_pass(args, target_dates=["260603"], pass_index=1)
    finally:
        run_account_job.run_command = original_run_command
    assert worker_pass["ok"] is True
    assert worker_pass["codex_summary_required"] is True
    assert worker_pass["codex_summary_required_count"] == 1
    assert worker_pass["codex_summary_required_urls"] == ["https://facebook.com/example/posts/summary-needed"]
    summary_requirement = run_account_job.worker_summary_requirement_summary([worker_pass])
    assert summary_requirement["codex_summary_required"] is True
    assert summary_requirement["codex_summary_required_count"] == 1


def assert_run_account_job_continues_worker_passes_until_complete() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    assert run_account_job.auto_resume_pass_limit(0) >= 8
    assert run_account_job.auto_resume_pass_limit(999) == run_account_job.MAX_AUTO_RESUME_PASSES
    assert run_account_job.completion_improved(
        {"open_task_count": 3, "auto_open_task_count": 3, "incomplete_post_count": 1},
        {"open_task_count": 2, "auto_open_task_count": 2, "incomplete_post_count": 1},
    )
    assert not run_account_job.completion_improved(
        {"open_task_count": 2, "auto_open_task_count": 2, "incomplete_post_count": 1},
        {"open_task_count": 2, "auto_open_task_count": 2, "incomplete_post_count": 1},
    )

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/continue",
            "account_name": "Continue Page",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "strict_ready_only": False,
            "resume_only": True,
            "max_snapshots": 32,
            "min_snapshots": 6,
            "max_resume_passes": 0,
            "expected_post_count": 0,
            "expected_labels": "",
            "require_coverage_complete": False,
            "min_ledger_usable_rate": 0.0,
            "min_final_usable_rate": 0.0,
            "min_completion_rate": 0.0,
            "min_expected_post_coverage_rate": 0.0,
            "min_expected_label_coverage_rate": 0.0,
        },
    )()
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status="incomplete_pending_tasks",
        completion={"open_task_count": 4, "has_auto_enrichment_work": True},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    pending = next(item for item in commands if item["reason"] == "pending_enrichment")
    assert "--max-resume-passes 8" in pending["command"]


def assert_run_account_job_auto_exports_summary_requests(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_summary_export.yaml"
    db_path = tmp_path / "account-summary-export.sqlite"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace("database_path: data/posts.sqlite", f"database_path: {db_path}"),
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import connect, enqueue_enrichment_tasks_for_posts, row_for_post, upsert_post

    conn = connect(db_path)
    post = normalize_post(
        {
            "account_name": "Summary Export",
            "account_url": "https://www.facebook.com/summaryexport",
            "account_type": "competitor",
            "post_url": "https://www.facebook.com/summaryexport/posts/needs-summary",
            "posted_at": "2026年6月2日 12:00",
            "time_confirmed": True,
            "time_source": "dom_aria_label",
            "article_url": "https://story.example/summary-export",
            "landing_url": "https://story.example/summary-export",
            "lead_url_raw": "https://story.example/summary-export",
            "lead_link_status": "qualified",
            "lead_link_source": "comment",
            "likes": 20,
            "comments": 3,
            "shares": 1,
            "post_type": "图文",
            "article_material": {
                "ok": True,
                "title": "Auto exported summary request",
                "text_excerpt": "A complete article material payload exists and only needs a Chinese summary.",
            },
        }
    )
    upsert_post(conn, post)
    stored = row_for_post(conn, post)
    assert stored is not None
    enqueue_enrichment_tasks_for_posts(conn, [stored])

    status_only = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/summaryexport",
            "--account-name",
            "Summary Export",
            "--target-date",
            "260602",
            "--resume-only",
            "--status-only",
            "--dry-run",
            "--allow-incomplete-success",
        ]
    )
    assert status_only.returncode == 0, status_only.stdout
    status_data = json.loads(status_only.stdout)
    assert status_data["run_status"] == "needs_codex_summary"
    assert status_data["summary_requests_export"]["skipped"] is True
    assert status_data["summary_requests_export"]["reason"] == "status_only"

    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/summaryexport",
            "--account-name",
            "Summary Export",
            "--target-date",
            "260602",
            "--resume-only",
            "--sync",
            "--dry-run",
        ]
    )
    assert job.returncode == 0, job.stdout
    data = json.loads(job.stdout)
    assert data["run_status"] == "complete"
    assert data["summary_auto_apply"]["ok"] is True
    assert data["summary_auto_apply"]["generate"]["generated"] >= 1
    assert data["quality_summary"]["final_usable_rate"] == 1.0
    assert data["summary_requests_export"]["ok"] is True
    assert data["summary_requests_export"]["count"] == 1
    output_path = Path(data["summary_requests_export"]["output_path"])
    assert output_path.name == "summary_requests_260602.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["scope"]["enabled"] is True
    assert payload["scope"]["date"] == "260602"
    assert payload["scope"]["account_url"] == "https://www.facebook.com/summaryexport"
    assert payload["count"] == 1
    assert payload["requests"][0]["article_material"]["title"] == "Auto exported summary request"
    stored_after = row_for_post(conn, post)
    assert stored_after is not None
    assert stored_after["summary_source"] == "article"
    assert stored_after["story_summary"]
    assert stored_after["output_status"] == "ready_for_output"


def assert_run_account_job_applies_partial_generated_summaries() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    original_run_command = run_account_job.run_command
    calls: list[list[str]] = []
    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/partialsummary",
            "account_name": "Partial Summary",
            "account_type": "competitor",
            "status_only": False,
            "dry_run": True,
        },
    )()

    class FakeResult:
        def __init__(self, returncode: int, payload: dict[str, object]):
            self.returncode = returncode
            self.stdout = json.dumps(payload, ensure_ascii=False)
            self.stderr = ""

    def fake_run(command: list[str]) -> FakeResult:
        calls.append(command)
        script = command[1] if len(command) > 1 else ""
        if script.endswith("export_summary_requests.py"):
            return FakeResult(0, {"ok": True, "count": 2})
        if script.endswith("generate_article_summaries.py"):
            return FakeResult(
                2,
                {
                    "ok": True,
                    "run_status": "summary_generated",
                    "generated": 1,
                    "summary_key_count": 2,
                    "rejected": [{"post_url": "https://facebook.com/p/needs-manual", "reason": "summary_policy_rejected"}],
                },
            )
        if script.endswith("apply_article_summaries.py"):
            return FakeResult(0, {"ok": True, "applied": 1, "missing": 1, "rejected": 0})
        return FakeResult(1, {"ok": False, "error": "unexpected command", "command": command})

    try:
        run_account_job.run_command = fake_run
        result = run_account_job.auto_generate_and_apply_summaries(
            args,
            ["260603"],
            {
                "requires_codex_summary_count": 2,
                "has_auto_enrichment_work": False,
                "auto_open_task_count": 0,
                "coverage_incomplete_count": 1,
            },
        )
    finally:
        run_account_job.run_command = original_run_command

    assert run_account_job.has_pre_summary_auto_enrichment_work(
        {
            "requires_codex_summary_count": 1,
            "has_auto_enrichment_work": False,
            "auto_open_task_count": 0,
            "coverage_incomplete_count": 1,
        }
    ) is False
    scripts = [call[1] for call in calls]
    assert any(script.endswith("generate_article_summaries.py") for script in scripts)
    assert any(script.endswith("apply_article_summaries.py") for script in scripts)
    assert result["ok"] is True
    assert result["partial_generation"] is True
    assert result["generated_summary_count"] == 2
    assert result["generate"]["returncode"] == 2
    assert result["apply"]["applied"] == 1


def assert_run_account_job_generates_summary_while_post_type_pending() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    calls: list[list[str]] = []
    original_run_command = run_account_job.run_command
    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/summarywithtypegap",
            "account_name": "Summary With Type Gap",
            "account_type": "competitor",
            "dry_run": False,
            "status_only": False,
        },
    )()

    class FakeResult:
        def __init__(self, returncode: int, payload: dict) -> None:
            self.returncode = returncode
            self.stdout = json.dumps(payload, ensure_ascii=False)
            self.stderr = ""

    def fake_run(command: list[str]) -> FakeResult:
        calls.append(command)
        script = command[1] if len(command) > 1 else ""
        if script.endswith("export_summary_requests.py"):
            return FakeResult(0, {"ok": True, "count": 1})
        if script.endswith("generate_article_summaries.py"):
            return FakeResult(0, {"ok": True, "generated": 1, "summary_key_count": 1, "rejected": []})
        if script.endswith("apply_article_summaries.py"):
            return FakeResult(0, {"ok": True, "applied": 1, "missing": 0, "rejected": 0})
        return FakeResult(1, {"ok": False, "error": "unexpected command", "command": command})

    completion = {
        "requires_codex_summary_count": 1,
        "has_auto_enrichment_work": True,
        "auto_open_task_count": 1,
        "open_task_stage_counts": {"post_type": 1, "summary": 1},
        "missing_stage_counts": {"post_type": 1, "summary": 1},
    }
    try:
        run_account_job.run_command = fake_run
        result = run_account_job.auto_generate_and_apply_summaries(args, ["260603"], completion)
    finally:
        run_account_job.run_command = original_run_command

    assert run_account_job.has_pre_summary_auto_enrichment_work(completion) is False
    assert result["ok"] is True
    assert result["applied_summary_count"] == 1
    scripts = [call[1] for call in calls]
    assert any(script.endswith("export_summary_requests.py") for script in scripts)
    assert any(script.endswith("generate_article_summaries.py") for script in scripts)
    assert any(script.endswith("apply_article_summaries.py") for script in scripts)


def assert_run_account_job_rejects_noop_summary_apply() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    original_run_command = run_account_job.run_command
    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/noopsummary",
            "account_name": "Noop Summary",
            "account_type": "competitor",
            "status_only": False,
            "dry_run": True,
        },
    )()

    class FakeResult:
        def __init__(self, returncode: int, payload: dict[str, object]):
            self.returncode = returncode
            self.stdout = json.dumps(payload, ensure_ascii=False)
            self.stderr = ""

    def fake_run(command: list[str]) -> FakeResult:
        script = command[1] if len(command) > 1 else ""
        if script.endswith("export_summary_requests.py"):
            return FakeResult(0, {"ok": True, "count": 1})
        if script.endswith("generate_article_summaries.py"):
            return FakeResult(0, {"ok": True, "generated": 1, "summary_key_count": 1, "rejected": []})
        if script.endswith("apply_article_summaries.py"):
            return FakeResult(
                0,
                {
                    "ok": True,
                    "mode": "sqlite",
                    "applied": 0,
                    "missing": 1,
                    "rejected": 0,
                    "article_summary_missing": ["https://facebook.com/noopsummary/posts/1"],
                },
            )
        return FakeResult(1, {"ok": False, "error": "unexpected command", "command": command})

    try:
        run_account_job.run_command = fake_run
        result = run_account_job.auto_generate_and_apply_summaries(
            args,
            ["260603"],
            {
                "requires_codex_summary_count": 1,
                "has_auto_enrichment_work": False,
                "auto_open_task_count": 0,
            },
        )
    finally:
        run_account_job.run_command = original_run_command

    assert result["ok"] is False
    assert result["run_status"] == "summary_auto_apply_failed"
    assert result["applied_summary_count"] == 0
    assert result["required_summary_count"] == 1
    assert result["apply"]["run_status"] == "summary_apply_noop"
    assert "没有任何 scoped 帖子被更新" in result["apply"]["message"]


def assert_run_account_job_worker_pass_reports_non_json_failure() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    original_run_command = run_account_job.run_command
    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example Page",
            "account_type": "competitor",
            "enrichment_limit": 50,
            "sync": True,
            "dry_run": False,
            "max_resume_passes": 2,
            "strict_ready_only": False,
            "max_snapshots": 32,
            "min_snapshots": 6,
            "expected_post_count": 0,
            "expected_labels": "",
            "require_coverage_complete": False,
            "min_ledger_usable_rate": 0.0,
            "min_final_usable_rate": 0.0,
            "min_completion_rate": 0.0,
            "min_expected_post_coverage_rate": 0.0,
            "min_expected_label_coverage_rate": 0.0,
        },
    )()

    class FakeBrokenWorker:
        returncode = 2
        stdout = "Traceback: worker crashed before JSON\n"
        stderr = "boom\n"

    try:
        run_account_job.run_command = lambda _command: FakeBrokenWorker()
        worker_pass = run_account_job.run_worker_pass(args, target_dates=["260603"], pass_index=1)
    finally:
        run_account_job.run_command = original_run_command

    assert worker_pass["ok"] is False
    assert worker_pass["worker_failed"] is True
    assert "non_json_worker_output" in worker_pass["worker_failure_reasons"]
    failure_summary = run_account_job.worker_failure_summary([worker_pass])
    assert failure_summary["worker_failed"] is True
    assert failure_summary["worker_failed_pass_count"] == 1
    status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[worker_pass],
        sync_result={"ok": True},
        completion={"post_count": 1, "open_task_count": 1, "has_auto_enrichment_work": True},
    )
    assert status == "worker_failed"
    quality = run_account_job.account_job_quality_summary(
        run_status=status,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
        completion={"post_count": 1, "open_task_count": 1, "has_auto_enrichment_work": True},
        sync_result={"ok": True, "skipped": True, "run_status": "not_synced"},
        worker_failure=failure_summary,
    )
    assert quality["completion_blockers"][0]["code"] == "worker_failed"
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status=status,
        completion={"post_count": 1, "open_task_count": 1, "has_auto_enrichment_work": True},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert commands[0]["reason"] == "worker_failed"
    assert "--resume-only" in commands[0]["command"]
    assert "--force-recover-running" in commands[0]["command"]


def assert_run_account_job_waits_for_article_material_before_summary_export() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example Page",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "expected_post_count": 0,
            "expected_labels": "",
        },
    )()
    completion = {
        "open_task_count": 2,
        "summary_open_task_count": 1,
        "auto_open_task_count": 1,
        "requires_codex_summary_count": 1,
        "has_summary_only_work": False,
        "has_auto_enrichment_work": True,
        "open_task_stage_counts": {"article_material": 1, "summary": 1},
        "missing_stage_counts": {"article_material": 1, "summary": 1},
        "has_incomplete_enrichment": True,
    }
    status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[],
        sync_result={"ok": True},
        completion=completion,
    )
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260603"],
        run_status=status,
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert status == "incomplete_pending_tasks"
    assert [item["reason"] for item in commands] == ["pending_enrichment"]
    assert run_account_job.has_pre_summary_auto_enrichment_work(completion) is True
    assert "--resume-only" in commands[0]["command"]
    assert "export_summary_requests.py" not in commands[0]["command"]


def assert_run_capture_pipeline_uses_completion_status_helpers() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_capture_pipeline

    assert run_capture_pipeline.capture_pipeline_run_status(
        {"ok": True, "capture_complete": False, "coverage": {"coverage_incomplete": True}},
        {"post_count": 2},
    ) == "coverage_incomplete"
    assert run_capture_pipeline.capture_pipeline_run_status(
        {"ok": True, "capture_complete": True, "coverage": {}},
        {"post_count": 0},
    ) == "no_candidates"
    assert run_capture_pipeline.capture_pipeline_run_status(
        {"ok": True, "capture_complete": True, "coverage": {}},
        {
            "post_count": 1,
            "has_auto_enrichment_work": True,
            "auto_open_task_count": 1,
            "has_incomplete_enrichment": True,
            "requires_codex_summary_count": 1,
        },
    ) == "incomplete_pending_tasks"
    assert run_capture_pipeline.capture_pipeline_run_status(
        {"ok": True, "capture_complete": True, "coverage": {}},
        {
            "post_count": 1,
            "has_auto_enrichment_work": False,
            "auto_open_task_count": 0,
            "has_summary_only_work": True,
            "requires_codex_summary_count": 1,
            "has_incomplete_enrichment": True,
        },
    ) == "needs_codex_summary"
    assert run_capture_pipeline.capture_pipeline_run_status(
        {"ok": True, "capture_complete": True, "coverage": {}},
        {"post_count": 1, "has_incomplete_enrichment": False},
    ) == "complete"
    assert "--fix-opencli" in run_capture_pipeline.capture_pipeline_next_actions("blocked_opencli", {})[0]
    assert "飞书用户授权" in run_capture_pipeline.capture_pipeline_next_actions("blocked_auth", {})[0]


def assert_run_capture_pipeline_blocks_auth_before_opencli(tmp_path: Path) -> None:
    config = tmp_path / "settings_capture_auth.yaml"
    fake_lark = tmp_path / "fake-lark-cli"
    fake_opencli = tmp_path / "fake-opencli"
    opencli_called = tmp_path / "opencli-called"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'capture-auth.sqlite'}")
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/bin/sh
if [ "$1" = "config" ]; then
  echo "$2: user"
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  echo '{"identity":"bot","tokenStatus":"valid"}'
  exit 0
fi
echo '{}'
exit 0
""",
        encoding="utf-8",
    )
    fake_opencli.write_text(
        f"""#!/bin/sh
touch {opencli_called}
echo '1.8.1'
exit 0
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_opencli.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_capture_pipeline.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/authblocked",
            "--target-date",
            "260603",
            "--sync-partial",
        ]
    )
    assert result.returncode == 1, result.stdout
    data = json.loads(result.stdout)
    assert data["stage"] == "feishu_auth_preflight"
    assert data["run_status"] == "blocked_auth"
    assert data["complete"] is False
    assert any("飞书用户授权" in action for action in data["next_actions"])
    assert data["next_commands"][0]["reason"] == "blocked_auth"
    assert "run_account_job.py" in data["next_commands"][0]["command"]
    assert "--resume-only" not in data["next_commands"][0]["command"]
    assert "--sync" in data["next_commands"][0]["command"]
    assert data["completion_blockers"][0]["code"] == "blocked_auth"
    assert "飞书授权" in data["completion_blockers"][0]["label"]
    assert not opencli_called.exists()


def assert_run_capture_pipeline_reports_opencli_blocker(tmp_path: Path) -> None:
    config = tmp_path / "settings_capture_opencli.yaml"
    fake_opencli = tmp_path / "fake-opencli"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'capture-opencli.sqlite'}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text(
        """#!/bin/sh
echo 'opencli unavailable' >&2
exit 1
""",
        encoding="utf-8",
    )
    fake_opencli.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_capture_pipeline.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/opencliblocked",
            "--target-date",
            "260603",
        ]
    )
    assert result.returncode == 1, result.stdout
    data = json.loads(result.stdout)
    assert data["stage"] == "opencli_preflight"
    assert data["run_status"] == "blocked_opencli"
    assert data["complete"] is False
    assert any("--fix-opencli" in action for action in data["next_actions"])
    assert [item["reason"] for item in data["next_commands"][:2]] == ["blocked_opencli", "rerun_full_capture"]
    assert "--fix-opencli" in data["next_commands"][0]["command"]
    assert "run_account_job.py" in data["next_commands"][1]["command"]
    assert "--resume-only" not in data["next_commands"][1]["command"]
    assert data["completion_blockers"][0]["code"] == "blocked_opencli"
    assert data["completion_blockers"][0]["severity"] == "hard_blocker"


def assert_run_capture_pipeline_applies_expected_coverage(tmp_path: Path) -> None:
    config = tmp_path / "settings_capture_expected.yaml"
    fake_bin = tmp_path / "bin"
    fake_opencli = tmp_path / "fake-opencli"
    db_path = tmp_path / "capture-expected.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text(
        """#!/bin/sh
echo '1.8.1'
exit 0
""",
        encoding="utf-8",
    )
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "ok": True,
  "post_count": 9,
  "raw_candidate_count": 9,
  "capture_complete": True,
  "coverage": {"capture_complete": True},
  "snapshots": [
    {"visible_time_texts": ["38m", "1h", "2h", "3h"]},
    {"visible_time_texts": ["4h", "5h", "6h", "7h", "8h"]}
  ],
  "posts": [
    {
      "post_url": f"https://www.facebook.com/expectedpage/posts/item-{index}",
      "post_time_text": f"{index + 1}h",
      "crawled_at": "2026-06-03T12:00:00",
      "raw_text": f"candidate {index}"
    }
    for index in range(9)
  ]
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/expectedpage",
                "--account-name",
                "Expected Page",
                "--target-date",
                "260603",
                "--expected-post-count",
                "13",
                "--expected-labels",
                "38m,1h,2h,10h",
            ],
            env=env,
        )
        strict_result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/expectedpage",
                "--account-name",
                "Expected Page",
                "--target-date",
                "260603",
                "--expected-post-count",
                "13",
                "--expected-labels",
                "38m,1h,2h,10h",
                "--fail-on-incomplete",
                "--min-final-usable-rate",
                "0.9",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["run_status"] == "coverage_incomplete"
    assert data["complete"] is False
    assert data["prepared"] == 9
    assert data["enrichment_completion"]["post_count"] == 9
    assert data["expected_coverage"]["missing_post_count"] == 4
    assert data["expected_coverage"]["missing_labels"] == ["10h"]
    assert data["coverage"]["expected_coverage_failed"] is True
    assert data["quality_summary"]["run_status"] == "coverage_incomplete"
    assert data["quality_summary"]["coverage_health"] == "incomplete"
    assert data["quality_summary"]["ledger_candidate_count"] == 9
    assert data["quality_summary"]["ledger_usable_rate"] == 1.0
    assert data["quality_summary"]["final_usable_rate"] == 0.0
    assert data["quality_summary"]["open_task_stage_counts"]["detail_time"] == 9
    assert data["quality_summary"]["missing_stage_counts"]["detail_time"] == 9
    assert any("精确时间" in note for note in data["quality_summary"]["stage_pressure_notes"])
    assert data["feishu_sync"]["run_status"] == "not_synced"
    blocker_codes = [item["code"] for item in data["completion_blockers"]]
    assert blocker_codes[0] == "coverage_incomplete"
    assert "stage_detail_time" in blocker_codes
    assert "field_gaps" in blocker_codes
    assert data["completion_blockers"] == data["quality_summary"]["completion_blockers"]
    assert any("覆盖未完成" in action for action in data["next_actions"])
    command_reasons = [item["reason"] for item in data["next_commands"]]
    assert command_reasons[:2] == ["pending_enrichment", "coverage_incomplete"]
    assert "--resume-only" in data["next_commands"][0]["command"]
    assert "--force-recover-running" in data["next_commands"][0]["command"]
    assert "--max-snapshots 44" in data["next_commands"][1]["command"]
    assert "--expected-post-count 13" in data["next_commands"][1]["command"]
    assert strict_result.returncode == 2, strict_result.stdout
    strict_data = json.loads(strict_result.stdout)
    assert strict_data["run_status"] == "coverage_incomplete"
    assert strict_data["complete"] is False
    assert strict_data["quality_threshold_failed"] is True
    assert strict_data["exit_status_reason"] == "quality_threshold_failed"
    assert [failure["metric"] for failure in strict_data["quality_threshold_failures"]] == ["final_usable_rate"]
    assert "quality_threshold_failed" in [item["code"] for item in strict_data["completion_blockers"]]
    strict_command_reasons = [item["reason"] for item in strict_data["next_commands"]]
    assert strict_command_reasons[:2] == ["pending_enrichment", "coverage_incomplete"]


def assert_run_account_job_passes_snapshot_budget(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_snapshots.yaml"
    fake_bin = tmp_path / "bin-account-snapshots"
    fake_opencli = tmp_path / "fake-opencli-account-snapshots"
    args_file = tmp_path / "account-node-argv.json"
    db_path = tmp_path / "account-snapshots.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
pathlib.Path(r"{args_file}").write_text(json.dumps(sys.argv), encoding="utf-8")
payload = {{
  "ok": True,
  "post_count": 1,
  "raw_candidate_count": 1,
  "capture_complete": True,
  "coverage": {{"capture_complete": True}},
  "posts": [
    {{
      "post_url": "https://www.facebook.com/accountsnapshot/posts/one",
      "post_time_text": "1h",
      "crawled_at": "2026-06-03T12:00:00",
      "raw_text": "candidate one"
    }}
  ]
}}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_account_job.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/accountsnapshot",
                "--account-name",
                "Account Snapshot",
                "--target-date",
                "260603",
                "--dry-run",
                "--status-only",
                "--allow-incomplete-success",
                "--max-snapshots",
                "44",
                "--min-snapshots",
                "9",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode == 0, result.stdout or result.stderr
    argv = json.loads(args_file.read_text(encoding="utf-8"))
    assert "--max-snapshots" in argv
    assert argv[argv.index("--max-snapshots") + 1] == "44"
    assert "--min-snapshots" in argv
    assert argv[argv.index("--min-snapshots") + 1] == "9"


def assert_run_account_job_auto_retries_snapshot_cap(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_retry_snapshots.yaml"
    fake_bin = tmp_path / "bin-account-retry-snapshots"
    fake_opencli = tmp_path / "fake-opencli-account-retry-snapshots"
    calls_file = tmp_path / "account-retry-calls.json"
    db_path = tmp_path / "account-retry-snapshots.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        f"""#!{PYTHON}
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
max_snapshots = int(sys.argv[sys.argv.index("--max-snapshots") + 1])
base = {{
    "ok": True,
    "snapshots": [{{"visible_time_texts": ["1h", "2h"], "new_posts": 0}}],
}}
if max_snapshots < 32:
    payload = {{
        **base,
        "post_count": 1,
        "raw_candidate_count": 1,
        "capture_complete": False,
        "coverage_incomplete": True,
        "coverage": {{
            "capture_complete": False,
            "coverage_incomplete": True,
            "stop_reason": "max_snapshots",
            "message": "hit cap"
        }},
        "posts": [
            {{
                "post_url": "https://www.facebook.com/accountretry/posts/one",
                "post_time_text": "1h",
                "crawled_at": "2026-06-03T12:00:00",
                "raw_text": "candidate one"
            }}
        ]
    }}
else:
    payload = {{
        **base,
        "post_count": 2,
        "raw_candidate_count": 2,
        "capture_complete": True,
        "coverage": {{
            "capture_complete": True,
            "coverage_incomplete": False,
            "stop_reason": "stable_no_new_posts"
        }},
        "posts": [
            {{
                "post_url": "https://www.facebook.com/accountretry/posts/one",
                "post_time_text": "1h",
                "crawled_at": "2026-06-03T12:00:00",
                "raw_text": "candidate one"
            }},
            {{
                "post_url": "https://www.facebook.com/accountretry/posts/two",
                "post_time_text": "2h",
                "crawled_at": "2026-06-03T12:00:00",
                "raw_text": "candidate two"
            }}
        ]
    }}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_account_job.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/accountretry",
                "--account-name",
                "Account Retry",
                "--target-date",
                "260603",
                "--dry-run",
                "--status-only",
                "--allow-incomplete-success",
                "--max-snapshots",
                "20",
                "--min-snapshots",
                "6",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert calls[0][calls[0].index("--max-snapshots") + 1] == "20"
    assert calls[1][calls[1].index("--max-snapshots") + 1] == "32"
    assert data["post_count"] == 2
    assert data["discover_import"]["discover"]["post_count"] == 2
    assert data["discover_import"]["discover_retry"]["attempted"] is True
    assert data["discover_import"]["discover"]["auto_retry"]["resolved"] is True
    assert data["discover_coverage"]["complete"] is True
    assert data["discover_coverage"]["incomplete"] is False
    assert data["quality_summary"]["coverage_health"] == "complete"
    assert data["quality_summary"]["ledger_candidate_count"] == 2
    assert data["run_status"] != "coverage_incomplete"


def assert_run_account_job_auto_retries_expected_coverage_gap(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_expected_retry.yaml"
    fake_bin = tmp_path / "bin-account-expected-retry"
    fake_opencli = tmp_path / "fake-opencli-account-expected-retry"
    calls_file = tmp_path / "account-expected-retry-calls.json"
    db_path = tmp_path / "account-expected-retry.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        f"""#!{PYTHON}
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
max_snapshots = int(sys.argv[sys.argv.index("--max-snapshots") + 1])
if max_snapshots < 32:
    posts = [
        {{
            "post_url": "https://www.facebook.com/expectedretry/posts/one",
            "post_time_text": "1h",
            "crawled_at": "2026-06-03T12:00:00",
            "raw_text": "candidate one"
        }}
    ]
    labels = ["1h"]
else:
    posts = [
        {{
            "post_url": "https://www.facebook.com/expectedretry/posts/one",
            "post_time_text": "1h",
            "crawled_at": "2026-06-03T12:00:00",
            "raw_text": "candidate one"
        }},
        {{
            "post_url": "https://www.facebook.com/expectedretry/posts/two",
            "post_time_text": "2h",
            "crawled_at": "2026-06-03T12:00:00",
            "raw_text": "candidate two"
        }},
    ]
    labels = ["1h", "2h"]
payload = {{
    "ok": True,
    "post_count": len(posts),
    "raw_candidate_count": len(posts),
    "capture_complete": True,
    "coverage": {{"capture_complete": True, "coverage_incomplete": False, "stop_reason": "stable_no_new_posts"}},
    "snapshots": [{{"visible_time_texts": labels, "new_posts": len(posts)}}],
    "posts": posts,
}}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_account_job.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/expectedretry",
                "--account-name",
                "Expected Retry",
                "--target-date",
                "260603",
                "--dry-run",
                "--status-only",
                "--allow-incomplete-success",
                "--max-snapshots",
                "20",
                "--min-snapshots",
                "6",
                "--expected-post-count",
                "2",
                "--expected-labels",
                "1h,2h",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert calls[0][calls[0].index("--max-snapshots") + 1] == "20"
    assert calls[1][calls[1].index("--max-snapshots") + 1] == "32"
    assert data["post_count"] == 2
    assert data["discover_import"]["discover"]["expected_coverage"]["ok"] is True
    assert data["discover_import"]["discover_retry"]["attempted"] is True
    assert data["discover_import"]["discover"]["auto_retry"]["resolved"] is True
    assert data["discover_coverage"]["complete"] is True
    assert data["quality_summary"]["coverage_health"] == "complete"


def assert_run_accounts_job_runs_all_accounts_and_aggregates_status(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_accounts.yaml"
    fake_lark = tmp_path / "fake-lark-cli"
    fake_python = tmp_path / "fake-python"
    fake_opencli = tmp_path / "fake-opencli"
    calls_file = tmp_path / "batch-account-calls.json"
    opencli_calls_file = tmp_path / "batch-opencli-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Competitor One", "https://www.facebook.com/competitorone", ""],
        ["Internal One", "", "https://www.facebook.com/internalone"]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_opencli.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{opencli_calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
if sys.argv[1:4] == ["browser", "fb-competitor", "tab"] and len(sys.argv) >= 6 and sys.argv[4] == "new":
    print(json.dumps({{"page": "opened-" + str(len(calls)), "url": sys.argv[5]}}))
    sys.exit(0)
if sys.argv[1:4] == ["browser", "fb-competitor", "tab"] and len(sys.argv) >= 6 and sys.argv[4] == "close":
    print(json.dumps({{"ok": True, "closed": sys.argv[5]}}))
    sys.exit(0)
print("unexpected opencli call", sys.argv, file=sys.stderr)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_opencli.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
account_calls = [call for call in calls if "--account-url" in call and call[call.index("--account-url") + 1] == account_url]
if account_type == "competitor" or len(account_calls) > 1:
    payload = {{
        "ok": True,
        "run_status": "complete",
        "complete": True,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 2,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 2 if account_type == "competitor" else 1,
            "final_usable_count": 2 if account_type == "competitor" else 1,
            "final_usable_rate": 1.0,
            "open_task_count": 0
        }},
        "next_commands": []
    }}
    code = 0
else:
    export_command = "python3 scripts/export_summary_requests.py --config " + sys.argv[sys.argv.index("--config") + 1] + " --output exports/summary_requests_260603.json --date 260603 --account-name '" + account_name + "' --account-url " + account_url + " --account-type " + account_type
    resume_command = "python3 scripts/run_account_job.py --config " + sys.argv[sys.argv.index("--config") + 1] + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --resume-only --force-recover-running --sync --dry-run --fail-on-incomplete --max-resume-passes 8"
    payload = {{
        "ok": True,
        "run_status": "needs_codex_summary",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 1,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 1,
            "final_usable_count": 0,
            "final_usable_rate": 0.0,
            "open_task_count": 1,
            "top_field_gaps": [{{"reason": "article_summary", "count": 1}}]
        }},
        "completion_blockers": [{{"code": "codex_summary_required"}}],
        "next_commands": [
            {{
                "reason": "needs_codex_summary",
                "description": "export scoped summary requests",
                "command": export_command
            }},
            {{
                "reason": "needs_codex_summary",
                "description": "continue same account",
                "command": resume_command
            }}
        ]
    }}
    code = 2
print(json.dumps(payload, ensure_ascii=False))
sys.exit(code)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--max-resume-passes",
            "8",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 3
    assert data["account_count"] == 2
    assert data["run_status"] == "complete"
    assert data["complete"] is True
    assert data["accounts_completed"] == 2
    assert data["accounts_needing_codex_summary"] == 0
    assert data["ledger_candidate_count"] == 3
    assert data["final_usable_count"] == 3
    assert data["next_commands"] == []
    internal = next(item for item in data["accounts"] if item["account_url"] == "https://www.facebook.com/internalone")
    assert internal["complete"] is True
    assert internal["auto_follow_attempted"] is True
    assert [attempt["run_status"] for attempt in internal["attempts"]] == ["needs_codex_summary", "complete"]
    internal_calls = [call for call in calls if "--account-url" in call and call[call.index("--account-url") + 1] == "https://www.facebook.com/internalone"]
    assert len(internal_calls) == 2
    assert "--resume-only" not in internal_calls[0]
    assert "--resume-only" in internal_calls[1]
    assert "--force-recover-running" in internal_calls[1]
    assert "export_summary_requests.py" not in internal_calls[1]
    opencli_calls = json.loads(opencli_calls_file.read_text(encoding="utf-8"))
    assert len([call for call in opencli_calls if "new" in call]) == 2
    assert len([call for call in opencli_calls if "close" in call]) == 2
    assert all("--target-date" in call and "260603" in call for call in calls)
    assert all("--sync" in call and "--dry-run" in call for call in calls)

    calls_file.unlink()
    no_follow_result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--dry-run",
            "--auto-follow-attempts",
            "1",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert no_follow_result.returncode == 2, no_follow_result.stdout
    no_follow_data = json.loads(no_follow_result.stdout)
    assert no_follow_data["run_status"] == "accounts_need_codex_summary"
    assert no_follow_data["complete"] is False
    no_follow_internal = next(item for item in no_follow_data["accounts"] if item["account_url"] == "https://www.facebook.com/internalone")
    assert no_follow_internal["auto_follow_attempt_limit"] == 1
    assert no_follow_internal["auto_follow_exhausted"] is True
    assert no_follow_internal["attempts"][-1]["auto_follow_stopped_reason"] == "max_attempts_reached"

    calls_file.unlink()
    status_only_result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--dry-run",
            "--status-only",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert status_only_result.returncode == 2, status_only_result.stdout
    status_only_data = json.loads(status_only_result.stdout)
    status_calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(status_calls) == 2
    assert status_only_data["run_status"] == "accounts_need_codex_summary"
    internal_status = next(item for item in status_only_data["accounts"] if item["account_url"] == "https://www.facebook.com/internalone")
    assert internal_status["auto_follow_attempted"] is False

    previous_opencli_call_count = len(json.loads(opencli_calls_file.read_text(encoding="utf-8")))
    no_open_result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--dry-run",
            "--no-open-account-tabs",
            "--limit",
            "1",
            "--allow-incomplete-success",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert no_open_result.returncode == 0, no_open_result.stdout
    assert len(json.loads(opencli_calls_file.read_text(encoding="utf-8"))) == previous_opencli_call_count


def assert_run_accounts_job_auto_follows_coverage_recovery(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_coverage.yaml"
    fake_lark = tmp_path / "fake-lark-cli-coverage"
    fake_python = tmp_path / "fake-python-coverage"
    calls_file = tmp_path / "batch-coverage-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_coverage.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Coverage Page", "https://www.facebook.com/coveragepage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
config_path = sys.argv[sys.argv.index("--config") + 1]
if len(calls) == 1:
    payload = {{
        "ok": True,
        "run_status": "coverage_incomplete",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 3,
        "quality_summary": {{
            "coverage_health": "incomplete",
            "ledger_candidate_count": 3,
            "final_usable_count": 0,
            "final_usable_rate": 0.0,
            "open_task_count": 3,
            "top_field_gaps": [
                {{"reason": "post_type", "count": 3}},
                {{"reason": "article_summary", "count": 3}}
            ]
        }},
        "completion_blockers": [{{"code": "coverage_incomplete"}}],
        "next_commands": [{{
            "reason": "coverage_incomplete",
            "description": "rerun from top with larger snapshot budget",
            "command": "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --sync --dry-run --fail-on-incomplete --max-snapshots 44 --min-snapshots 6"
        }}]
    }}
    code = 2
else:
    payload = {{
        "ok": True,
        "run_status": "complete",
        "complete": True,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 3,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 3,
            "final_usable_count": 3,
            "final_usable_rate": 1.0,
            "open_task_count": 0
        }},
        "next_commands": []
    }}
    code = 0
print(json.dumps(payload, ensure_ascii=False))
sys.exit(code)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--no-open-account-tabs",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert calls[1][calls[1].index("--max-snapshots") + 1] == "44"
    assert data["run_status"] == "complete"
    assert data["complete"] is True
    account = data["accounts"][0]
    assert account["auto_follow_attempted"] is True
    assert [attempt["run_status"] for attempt in account["attempts"]] == ["coverage_incomplete", "complete"]
    assert data["next_commands"] == []


def assert_run_accounts_job_prioritizes_detail_resume_over_coverage_rerun(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_coverage_with_detail.yaml"
    fake_lark = tmp_path / "fake-lark-cli-coverage-detail"
    fake_python = tmp_path / "fake-python-coverage-detail"
    calls_file = tmp_path / "batch-coverage-detail-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_coverage_detail.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Coverage Detail Page", "https://www.facebook.com/coveragedetailpage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
config_path = sys.argv[sys.argv.index("--config") + 1]
account_calls = [call for call in calls if "--account-url" in call and call[call.index("--account-url") + 1] == account_url]
coverage_command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --sync --dry-run --fail-on-incomplete --max-snapshots 44 --min-snapshots 6"
resume_command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --resume-only --force-recover-running --sync --dry-run --fail-on-incomplete --max-resume-passes 8"
if len(account_calls) == 1:
    payload = {{
        "ok": True,
        "run_status": "coverage_incomplete",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 6,
        "quality_summary": {{
            "coverage_health": "incomplete",
            "ledger_candidate_count": 6,
            "final_usable_count": 0,
            "final_usable_rate": 0.0,
            "open_task_count": 6,
            "open_task_stage_counts": {{"detail_time": 6, "post_type": 6}},
            "missing_stage_counts": {{"detail_time": 6, "post_type": 6}},
            "top_field_gaps": [
                {{"reason": "exact_time", "stage": "detail_time", "count": 6}},
                {{"reason": "post_type", "stage": "post_type", "count": 6}}
            ]
        }},
        "completion_blockers": [
            {{"code": "coverage_incomplete"}},
            {{"code": "stage_detail_time"}},
            {{"code": "stage_post_type"}}
        ],
        "next_commands": [
            {{"reason": "coverage_incomplete", "description": "rerun from top", "command": coverage_command}},
            {{"reason": "pending_enrichment", "description": "continue detail fields", "command": resume_command}}
        ]
    }}
    code = 2
else:
    payload = {{
        "ok": True,
        "run_status": "complete",
        "complete": True,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 6,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 6,
            "final_usable_count": 6,
            "final_usable_rate": 1.0,
            "open_task_count": 0
        }},
        "next_commands": []
    }}
    code = 0
print(json.dumps(payload, ensure_ascii=False))
sys.exit(code)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--no-open-account-tabs",
            "--auto-follow-attempts",
            "3",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert "--resume-only" in calls[1]
    assert "--force-recover-running" in calls[1]
    assert "--max-snapshots" not in calls[1]
    account = data["accounts"][0]
    assert [attempt["run_status"] for attempt in account["attempts"]] == ["coverage_incomplete", "complete"]
    assert account["attempts"][1].get("auto_follow_repeated_command") is None


def assert_run_accounts_job_repeats_same_resume_until_complete(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_repeated_resume.yaml"
    fake_lark = tmp_path / "fake-lark-cli-repeated-resume"
    fake_python = tmp_path / "fake-python-repeated-resume"
    calls_file = tmp_path / "batch-repeated-resume-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_repeated_resume.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Slow Field Page", "https://www.facebook.com/slowfieldpage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
config_path = sys.argv[sys.argv.index("--config") + 1]
resume_command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --resume-only --force-recover-running --sync --dry-run --fail-on-incomplete --max-resume-passes 8"
if len(calls) < 5:
    remaining = 5 - len(calls)
    payload = {{
        "ok": True,
        "run_status": "incomplete_pending_tasks",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 12,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 12,
            "final_usable_count": 12 - remaining,
            "final_usable_rate": round((12 - remaining) / 12, 4),
            "open_task_count": remaining,
            "top_field_gaps": [
                {{"reason": "post_type", "count": remaining}},
                {{"reason": "article_summary", "count": remaining}}
            ]
        }},
        "completion_blockers": [
            {{"code": "stage_post_type"}},
            {{"code": "codex_summary_required"}}
        ],
        "next_commands": [{{
            "reason": "pending_enrichment",
            "description": "continue same scoped queue",
            "command": resume_command
        }}]
    }}
    code = 2
else:
    payload = {{
        "ok": True,
        "run_status": "complete",
        "complete": True,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 12,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 12,
            "final_usable_count": 12,
            "final_usable_rate": 1.0,
            "open_task_count": 0
        }},
        "next_commands": []
    }}
    code = 0
print(json.dumps(payload, ensure_ascii=False))
sys.exit(code)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--no-open-account-tabs",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 5
    assert data["run_status"] == "complete"
    account = data["accounts"][0]
    assert account["complete"] is True
    assert account["auto_follow_attempted"] is True
    assert account["auto_follow_attempt_limit"] == 8
    assert account["auto_follow_exhausted"] is False
    assert [attempt["run_status"] for attempt in account["attempts"]] == [
        "incomplete_pending_tasks",
        "incomplete_pending_tasks",
        "incomplete_pending_tasks",
        "incomplete_pending_tasks",
        "complete",
    ]
    assert any(attempt.get("auto_follow_repeated_command") for attempt in account["attempts"])


def assert_run_accounts_job_extends_attempts_while_quality_improves(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_extend_progress.yaml"
    fake_lark = tmp_path / "fake-lark-cli-extend-progress"
    fake_python = tmp_path / "fake-python-extend-progress"
    calls_file = tmp_path / "batch-extend-progress-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_extend_progress.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Progress Page", "https://www.facebook.com/progresspage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
config_path = sys.argv[sys.argv.index("--config") + 1]
call_number = len(calls)
resume_command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --resume-only --force-recover-running --sync --dry-run --fail-on-incomplete --max-resume-passes 8"
if call_number < 4:
    usable = call_number
    remaining = 4 - call_number
    payload = {{
        "ok": True,
        "run_status": "incomplete_pending_tasks",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 4,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 4,
            "final_usable_count": usable,
            "final_usable_rate": round(usable / 4, 4),
            "open_task_count": remaining,
            "top_field_gaps": [
                {{"reason": "post_type", "count": remaining}},
                {{"reason": "article_summary", "count": remaining}}
            ]
        }},
        "next_commands": [{{
            "reason": "pending_enrichment",
            "description": "continue same scoped queue",
            "command": resume_command
        }}]
    }}
    code = 2
else:
    payload = {{
        "ok": True,
        "run_status": "complete",
        "complete": True,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 4,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 4,
            "final_usable_count": 4,
            "final_usable_rate": 1.0,
            "open_task_count": 0
        }},
        "next_commands": []
    }}
    code = 0
print(json.dumps(payload, ensure_ascii=False))
sys.exit(code)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--no-open-account-tabs",
            "--auto-follow-attempts",
            "2",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 4
    assert data["run_status"] == "complete"
    account = data["accounts"][0]
    assert account["auto_follow_attempt_limit"] == 2
    assert account["auto_follow_extended_after_budget_count"] == 2
    assert account["auto_follow_exhausted"] is False
    assert [attempt["run_status"] for attempt in account["attempts"]] == [
        "incomplete_pending_tasks",
        "incomplete_pending_tasks",
        "incomplete_pending_tasks",
        "complete",
    ]
    assert account["attempts"][1]["auto_follow_extended_after_budget"] is True
    assert account["attempts"][2]["auto_follow_extended_after_budget"] is True


def assert_run_accounts_job_treats_stage_progress_as_quality_improvement(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_stage_progress.yaml"
    fake_lark = tmp_path / "fake-lark-cli-stage-progress"
    fake_python = tmp_path / "fake-python-stage-progress"
    calls_file = tmp_path / "batch-stage-progress-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_stage_progress.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Stage Progress Page", "https://www.facebook.com/stageprogresspage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
config_path = sys.argv[sys.argv.index("--config") + 1]
call_number = len(calls)
resume_command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --resume-only --force-recover-running --sync --dry-run --fail-on-incomplete --max-resume-passes 8"
if call_number == 1:
    quality = {{
        "coverage_health": "complete",
        "ledger_candidate_count": 2,
        "final_usable_count": 0,
        "final_usable_rate": 0.0,
        "open_task_count": 2,
        "open_task_stage_counts": {{"article_material": 2}},
        "missing_stage_counts": {{"article_material": 2}},
        "top_field_gaps": [{{"reason": "article_summary", "stage": "summary", "count": 2}}]
    }}
    complete = False
    status = "incomplete_pending_tasks"
    code = 2
elif call_number == 2:
    quality = {{
        "coverage_health": "complete",
        "ledger_candidate_count": 2,
        "final_usable_count": 0,
        "final_usable_rate": 0.0,
        "open_task_count": 2,
        "open_task_stage_counts": {{"summary": 2}},
        "missing_stage_counts": {{"summary": 2}},
        "top_field_gaps": [{{"reason": "article_summary", "stage": "summary", "count": 2}}]
    }}
    complete = False
    status = "needs_codex_summary"
    code = 2
else:
    quality = {{
        "coverage_health": "complete",
        "ledger_candidate_count": 2,
        "final_usable_count": 2,
        "final_usable_rate": 1.0,
        "open_task_count": 0,
        "open_task_stage_counts": {{}},
        "missing_stage_counts": {{}},
        "top_field_gaps": []
    }}
    complete = True
    status = "complete"
    code = 0
payload = {{
    "ok": True,
    "run_status": status,
    "complete": complete,
    "account_url": account_url,
    "account_name": account_name,
    "account_type": account_type,
    "post_count": 2,
    "quality_summary": quality,
    "next_commands": [] if complete else [{{
        "reason": "pending_enrichment",
        "description": "continue same scoped queue",
        "command": resume_command
    }}]
}}
print(json.dumps(payload, ensure_ascii=False))
sys.exit(code)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--no-open-account-tabs",
            "--auto-follow-attempts",
            "2",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 3
    account = data["accounts"][0]
    assert account["complete"] is True
    assert account["auto_follow_attempt_limit"] == 2
    assert account["auto_follow_extended_after_budget_count"] == 1
    assert account["attempts"][1]["quality_improved"] is True
    assert account["attempts"][1]["auto_follow_extended_after_budget"] is True
    assert account["attempts"][1]["quality_progress_key"][3] > account["attempts"][0]["quality_progress_key"][3]


def assert_run_accounts_job_follows_recoverable_exit_one_commands(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_recover_exit_one.yaml"
    fake_lark = tmp_path / "fake-lark-cli-recover-exit-one"
    fake_python = tmp_path / "fake-python-recover-exit-one"
    calls_file = tmp_path / "batch-recover-exit-one-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_recover_exit_one.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Recoverable Sync Page", "https://www.facebook.com/recoverablesync", ""],
        ["No Work Page", "https://www.facebook.com/noworkpage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
config_path = sys.argv[sys.argv.index("--config") + 1]
account_calls = [call for call in calls if "--account-url" in call and call[call.index("--account-url") + 1] == account_url]
if "recoverablesync" in account_url and len(account_calls) == 1:
    command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --resume-only --force-recover-running --sync --dry-run --fail-on-incomplete --max-resume-passes 8"
    payload = {{
        "ok": False,
        "run_status": "sync_failed",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 2,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 2,
            "final_usable_count": 2,
            "final_usable_rate": 1.0,
            "open_task_count": 0
        }},
        "next_commands": [{{"reason": "sync_failed", "description": "retry scoped sync", "command": command}}]
    }}
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(1)
if "noworkpage" in account_url and len(account_calls) == 1:
    command = "python3 scripts/run_account_job.py --config " + config_path + " --account-url " + account_url + " --account-name '" + account_name + "' --account-type " + account_type + " --target-date 260603 --sync --dry-run --fail-on-incomplete --max-snapshots 32 --min-snapshots 6 --max-resume-passes 8"
    payload = {{
        "ok": True,
        "run_status": "no_work",
        "complete": False,
        "account_url": account_url,
        "account_name": account_name,
        "account_type": account_type,
        "post_count": 0,
        "quality_summary": {{
            "coverage_health": "complete",
            "ledger_candidate_count": 0,
            "final_usable_count": 0,
            "final_usable_rate": 0.0,
            "open_task_count": 0
        }},
        "next_commands": [{{"reason": "no_local_work", "description": "rerun full capture", "command": command}}]
    }}
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(2)
payload = {{
    "ok": True,
    "run_status": "complete",
    "complete": True,
    "account_url": account_url,
    "account_name": account_name,
    "account_type": account_type,
    "post_count": 2,
    "quality_summary": {{
        "coverage_health": "complete",
        "ledger_candidate_count": 2,
        "final_usable_count": 2,
        "final_usable_rate": 1.0,
        "open_task_count": 0
    }},
    "next_commands": []
}}
print(json.dumps(payload, ensure_ascii=False))
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--no-open-account-tabs",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 4
    assert data["run_status"] == "complete"
    sync_calls = [call for call in calls if "--account-url" in call and call[call.index("--account-url") + 1] == "https://www.facebook.com/recoverablesync"]
    no_work_calls = [call for call in calls if "--account-url" in call and call[call.index("--account-url") + 1] == "https://www.facebook.com/noworkpage"]
    assert len(sync_calls) == 2
    assert "--resume-only" in sync_calls[1]
    assert "--force-recover-running" in sync_calls[1]
    assert len(no_work_calls) == 2
    assert "--resume-only" not in no_work_calls[1]
    assert "--max-snapshots" in no_work_calls[1]
    sync_account = next(item for item in data["accounts"] if item["account_url"] == "https://www.facebook.com/recoverablesync")
    assert sync_account["attempts"][0]["auto_follow_nonstandard_returncode"] == 1
    assert [attempt["run_status"] for attempt in sync_account["attempts"]] == ["sync_failed", "complete"]
    no_work_account = next(item for item in data["accounts"] if item["account_url"] == "https://www.facebook.com/noworkpage")
    assert [attempt["run_status"] for attempt in no_work_account["attempts"]] == ["no_work", "complete"]


def assert_run_accounts_job_opencli_blocker_preserves_batch_retry(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_opencli_blocker.yaml"
    fake_lark = tmp_path / "fake-lark-cli-opencli-blocker"
    fake_python = tmp_path / "fake-python-opencli-blocker"
    fake_opencli = tmp_path / "fake-opencli-opencli-blocker"
    calls_file = tmp_path / "batch-opencli-blocker-calls.json"
    opencli_calls_file = tmp_path / "batch-opencli-blocker-opencli-calls.json"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_opencli_blocker.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "data": {
    "valueRange": {
      "values": [
        ["主页名称", "竞品fb账户", "内部FB账户"],
        ["Blocked Page", "https://www.facebook.com/blockedpage", ""],
        ["Good Page", "https://www.facebook.com/goodpage", ""]
      ]
    }
  }
}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_opencli.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{opencli_calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
if sys.argv[1:4] == ["browser", "fb-competitor", "tab"] and len(sys.argv) >= 6 and sys.argv[4] == "new":
    url = sys.argv[5]
    if "blockedpage" in url:
        print(json.dumps({{"ok": False, "error": "browser_bridge_not_connected"}}))
        sys.exit(1)
    print(json.dumps({{"page": "opened-good", "url": url}}))
    sys.exit(0)
if sys.argv[1:4] == ["browser", "fb-competitor", "tab"] and len(sys.argv) >= 6 and sys.argv[4] == "close":
    print(json.dumps({{"ok": True, "closed": sys.argv[5]}}))
    sys.exit(0)
print("unexpected opencli call", sys.argv, file=sys.stderr)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_opencli.chmod(0o755)
    fake_python.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
account_url = sys.argv[sys.argv.index("--account-url") + 1]
account_name = sys.argv[sys.argv.index("--account-name") + 1]
account_type = sys.argv[sys.argv.index("--account-type") + 1]
payload = {{
    "ok": True,
    "run_status": "complete",
    "complete": True,
    "account_url": account_url,
    "account_name": account_name,
    "account_type": account_type,
    "post_count": 2,
    "quality_summary": {{
        "coverage_health": "complete",
        "ledger_candidate_count": 2,
        "final_usable_count": 2,
        "final_usable_rate": 1.0,
        "open_task_count": 0
    }},
    "next_commands": []
}}
print(json.dumps(payload, ensure_ascii=False))
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--sync",
            "--dry-run",
            "--auto-follow-attempts",
            "6",
            "--max-snapshots",
            "40",
            "--min-snapshots",
            "8",
            "--max-resume-passes",
            "9",
            "--enrichment-limit",
            "25",
            "--require-coverage-complete",
            "--min-final-usable-rate",
            "0.9",
        ],
        env={**os.environ, "PYTHON": str(fake_python)},
    )
    assert result.returncode == 2, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert data["run_status"] == "accounts_blocked"
    assert data["accounts_completed"] == 1
    assert data["accounts_hard_blocked"] == 1
    blocked = next(item for item in data["accounts"] if item["account_url"] == "https://www.facebook.com/blockedpage")
    assert blocked["run_status"] == "blocked_opencli"
    assert [item["reason"] for item in blocked["next_commands"]] == ["blocked_opencli", "rerun_batch_after_opencli"]
    assert data["next_commands"][0]["reason"] == "blocked_opencli"
    assert data["next_commands"][1]["reason"] == "rerun_batch_after_opencli"
    rerun = shlex.split(data["next_commands"][1]["command"])
    assert "scripts/run_accounts_job.py" in rerun
    assert rerun[rerun.index("--config") + 1] == str(config)
    assert rerun[rerun.index("--target-date") + 1] == "260603"
    assert "--sync" in rerun
    assert "--dry-run" in rerun
    assert "--open-account-tabs" in rerun
    assert rerun[rerun.index("--auto-follow-attempts") + 1] == "6"
    assert rerun[rerun.index("--max-snapshots") + 1] == "40"
    assert rerun[rerun.index("--min-snapshots") + 1] == "8"
    assert rerun[rerun.index("--max-resume-passes") + 1] == "9"
    assert rerun[rerun.index("--enrichment-limit") + 1] == "25"
    assert "--require-coverage-complete" in rerun
    assert rerun[rerun.index("--min-final-usable-rate") + 1] == "0.9"
    opencli_calls = json.loads(opencli_calls_file.read_text(encoding="utf-8"))
    assert len([call for call in opencli_calls if "new" in call]) == 2
    assert len([call for call in opencli_calls if "close" in call]) == 1


def assert_run_accounts_job_auth_blocker_preserves_batch_retry(tmp_path: Path) -> None:
    config = tmp_path / "settings_batch_auth_blocker.yaml"
    fake_lark = tmp_path / "fake-lark-cli-batch-auth"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'batch_auth_blocker.sqlite'}")
    text = text.replace('source_spreadsheet_url: ""', 'source_spreadsheet_url: "https://fake.feishu.cn/sheets/source"')
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/bin/sh
if [ "$1" = "config" ]; then
  echo "$2: user"
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  echo '{"identity":"bot","tokenStatus":"valid"}'
  exit 0
fi
echo '{}'
exit 0
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)

    result = run(
        [
            PYTHON,
            "scripts/run_accounts_job.py",
            "--config",
            str(config),
            "--target-date",
            "260603",
            "--account-type",
            "competitor",
            "--limit",
            "2",
            "--sync",
            "--auto-follow-attempts",
            "6",
            "--max-snapshots",
            "40",
            "--min-snapshots",
            "8",
            "--max-resume-passes",
            "9",
            "--enrichment-limit",
            "25",
            "--require-coverage-complete",
            "--min-final-usable-rate",
            "0.9",
        ]
    )
    assert result.returncode == 1, result.stdout or result.stderr
    data = json.loads(result.stdout)
    assert data["run_status"] == "blocked_auth"
    assert data["complete"] is False
    assert data["feishu_auth_preflight"]["stage"] == "feishu_auth_preflight"
    assert data["next_commands"][0]["reason"] == "blocked_auth"
    rerun = shlex.split(data["next_commands"][0]["command"])
    assert "scripts/run_accounts_job.py" in rerun
    assert rerun[rerun.index("--config") + 1] == str(config)
    assert rerun[rerun.index("--target-date") + 1] == "260603"
    assert rerun[rerun.index("--account-type") + 1] == "competitor"
    assert rerun[rerun.index("--limit") + 1] == "2"
    assert "--sync" in rerun
    assert "--dry-run" not in rerun
    assert rerun[rerun.index("--auto-follow-attempts") + 1] == "6"
    assert rerun[rerun.index("--max-snapshots") + 1] == "40"
    assert rerun[rerun.index("--min-snapshots") + 1] == "8"
    assert rerun[rerun.index("--max-resume-passes") + 1] == "9"
    assert rerun[rerun.index("--enrichment-limit") + 1] == "25"
    assert "--require-coverage-complete" in rerun
    assert rerun[rerun.index("--min-final-usable-rate") + 1] == "0.9"


def assert_run_account_job_structures_prepare_and_import_failures(tmp_path: Path) -> None:
    opencli_status = start_opencli_status_server()
    try:
        prepare_config = tmp_path / "settings_account_prepare_fail.yaml"
        prepare_bin = tmp_path / "bin-account-prepare-fail"
        prepare_opencli = tmp_path / "fake-opencli-account-prepare-fail"
        prepare_db_path = tmp_path / "account-prepare-fail.sqlite"
        shutil.copy(ROOT / "config" / "settings.yaml.example", prepare_config)
        text = prepare_config.read_text(encoding="utf-8")
        text = text.replace("opencli_path: auto", f"opencli_path: {prepare_opencli}")
        text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
        text = text.replace("database_path: data/posts.sqlite", f"database_path: {prepare_db_path}")
        prepare_config.write_text(text, encoding="utf-8")
        prepare_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
        prepare_opencli.chmod(0o755)
        prepare_bin.mkdir()
        (prepare_bin / "node").write_text(
            f"#!{PYTHON}\n"
            + """import json
payload = {
  "ok": True,
  "post_count": 1,
  "raw_candidate_count": 1,
  "capture_complete": True,
  "coverage": {"capture_complete": True},
  "posts": [
    {
      "post_url": "https://www.facebook.com/accountpreparefail/posts/one",
      "post_time_text": "1h",
      "crawled_at": "2026-06-03T12:00:00",
      "raw_text": "candidate one"
    }
  ]
}
print(json.dumps(payload, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        (prepare_bin / "node").chmod(0o755)
        (prepare_bin / "python3").write_text(
            f"#!{PYTHON}\n"
            + """import json
import os
import subprocess
import sys
if len(sys.argv) > 1 and sys.argv[1] == "scripts/prepare_capture_result.py":
    output = sys.argv[sys.argv.index("--output") + 1]
    with open(output, "w", encoding="utf-8") as handle:
        handle.write("{not-json")
    print(json.dumps({"ok": True, "prepared": 1}, ensure_ascii=False))
    sys.exit(0)
real_python = os.environ["REAL_PYTHON"]
completed = subprocess.run([real_python, *sys.argv[1:]], text=True)
sys.exit(completed.returncode)
""",
            encoding="utf-8",
        )
        (prepare_bin / "python3").chmod(0o755)
        prepare_env = dict(os.environ)
        prepare_env["PATH"] = f"{prepare_bin}:{prepare_env.get('PATH', '')}"
        prepare_env["REAL_PYTHON"] = PYTHON

        prepare_result = run(
            [
                PYTHON,
                "scripts/run_account_job.py",
                "--config",
                str(prepare_config),
                "--account-url",
                "https://www.facebook.com/accountpreparefail",
                "--account-name",
                "Account Prepare Fail",
                "--target-date",
                "260603",
                "--dry-run",
            ],
            env=prepare_env,
        )
        assert prepare_result.returncode == 1, prepare_result.stdout
        prepare_data = json.loads(prepare_result.stdout)
        assert prepare_data["run_status"] == "prepare_failed"
        assert prepare_data["complete"] is False
        assert prepare_data["discover_import"]["stage"] == "prepare"
        assert prepare_data["discover_import"]["prepare"]["stage"] == "output_load"
        assert prepare_data["discover_import"]["discover"]["post_count"] == 1
        assert prepare_data["quality_summary"]["run_status"] == "prepare_failed"
        assert prepare_data["quality_summary"]["coverage_health"] == "incomplete"
        assert "discover_failed_before_import" in prepare_data["quality_summary"]["coverage_reasons"]
        assert prepare_data["quality_summary"]["discovered_post_count"] == 1
        assert prepare_data["quality_summary"]["post_count"] == 0
        assert any(item["reason"] == "prepare_failed" for item in prepare_data["next_commands"])
        assert "--resume-only" not in prepare_data["next_commands"][0]["command"]

        import_config = tmp_path / "settings_account_import_fail.yaml"
        import_bin = tmp_path / "bin-account-import-fail"
        import_opencli = tmp_path / "fake-opencli-account-import-fail"
        import_db_path = tmp_path / "account-import-fail.sqlite"
        shutil.copy(ROOT / "config" / "settings.yaml.example", import_config)
        text = import_config.read_text(encoding="utf-8")
        text = text.replace("opencli_path: auto", f"opencli_path: {import_opencli}")
        text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
        text = text.replace("database_path: data/posts.sqlite", f"database_path: {import_db_path}")
        import_config.write_text(text, encoding="utf-8")
        import_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
        import_opencli.chmod(0o755)
        import_bin.mkdir()
        (import_bin / "node").write_text(
            f"#!{PYTHON}\n"
            + """import json
payload = {
  "ok": True,
  "post_count": 1,
  "raw_candidate_count": 1,
  "capture_complete": True,
  "coverage": {"capture_complete": True},
  "posts": [
    {
      "post_url": "https://www.facebook.com/accountimportfail/posts/one",
      "post_time_text": "1h",
      "crawled_at": "2026-06-03T12:00:00",
      "raw_text": "candidate one"
    }
  ]
}
print(json.dumps(payload, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        (import_bin / "node").chmod(0o755)
        (import_bin / "python3").write_text(
            f"#!{PYTHON}\n"
            + """import json
import os
import subprocess
import sys
if len(sys.argv) > 1 and sys.argv[1] == "scripts/import_existing_result.py":
    print(json.dumps({
        "ok": False,
        "stage": "sqlite_write",
        "run_status": "import_failed",
        "complete": False,
        "message": "本地内容库不可写，已停止导入。",
        "error": "simulated sqlite write failure",
    }, ensure_ascii=False))
    sys.exit(1)
real_python = os.environ["REAL_PYTHON"]
completed = subprocess.run([real_python, *sys.argv[1:]], text=True)
sys.exit(completed.returncode)
""",
            encoding="utf-8",
        )
        (import_bin / "python3").chmod(0o755)
        import_env = dict(os.environ)
        import_env["PATH"] = f"{import_bin}:{import_env.get('PATH', '')}"
        import_env["REAL_PYTHON"] = PYTHON

        import_result = run(
            [
                PYTHON,
                "scripts/run_account_job.py",
                "--config",
                str(import_config),
                "--account-url",
                "https://www.facebook.com/accountimportfail",
                "--account-name",
                "Account Import Fail",
                "--target-date",
                "260603",
                "--dry-run",
            ],
            env=import_env,
        )
        assert import_result.returncode == 1, import_result.stdout
        import_data = json.loads(import_result.stdout)
        assert import_data["run_status"] == "import_failed"
        assert import_data["complete"] is False
        assert import_data["discover_import"]["stage"] == "import"
        assert import_data["discover_import"]["prepared"] == 1
        assert import_data["discover_import"]["import"]["run_status"] == "import_failed"
        assert import_data["quality_summary"]["run_status"] == "import_failed"
        assert import_data["quality_summary"]["coverage_health"] == "incomplete"
        assert "discover_failed_before_import" in import_data["quality_summary"]["coverage_reasons"]
        assert import_data["quality_summary"]["discovered_post_count"] == 1
        assert import_data["quality_summary"]["post_count"] == 0
        assert any(item["reason"] == "import_failed" for item in import_data["next_commands"])
        assert "--resume-only" not in import_data["next_commands"][0]["command"]

        sqlite_config = tmp_path / "settings_account_sqlite_connect_fail.yaml"
        sqlite_opencli = tmp_path / "fake-opencli-sqlite-connect-fail"
        shutil.copy(ROOT / "config" / "settings.yaml.example", sqlite_config)
        text = sqlite_config.read_text(encoding="utf-8")
        text = text.replace("opencli_path: auto", f"opencli_path: {sqlite_opencli}")
        text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
        text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path}")
        sqlite_config.write_text(text, encoding="utf-8")
        sqlite_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
        sqlite_opencli.chmod(0o755)
        sqlite_result = run(
            [
                PYTHON,
                "scripts/run_account_job.py",
                "--config",
                str(sqlite_config),
                "--account-url",
                "https://www.facebook.com/accountsqlitefail",
                "--account-name",
                "Account SQLite Fail",
                "--target-date",
                "260603",
                "--dry-run",
            ]
        )
        assert sqlite_result.returncode == 1, sqlite_result.stdout
        sqlite_data = json.loads(sqlite_result.stdout)
        assert sqlite_data["stage"] == "sqlite_connect"
        assert sqlite_data["run_status"] == "import_failed"
        assert sqlite_data["complete"] is False
        assert sqlite_data["quality_summary"]["run_status"] == "import_failed"
        assert sqlite_data["quality_summary"]["coverage_health"] == "incomplete"
        assert "sqlite_connect" in sqlite_data["quality_summary"]["coverage_reasons"]
        assert any(item["reason"] == "import_failed" for item in sqlite_data["next_commands"])
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()


def assert_run_capture_pipeline_passes_snapshot_budget(tmp_path: Path) -> None:
    config = tmp_path / "settings_capture_snapshots.yaml"
    fake_bin = tmp_path / "bin-snapshots"
    fake_opencli = tmp_path / "fake-opencli-snapshots"
    args_file = tmp_path / "node-argv.json"
    db_path = tmp_path / "capture-snapshots.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys
pathlib.Path(r"{args_file}").write_text(json.dumps(sys.argv), encoding="utf-8")
payload = {{
  "ok": True,
  "post_count": 1,
  "raw_candidate_count": 1,
  "capture_complete": True,
  "coverage": {{"capture_complete": True}},
  "posts": [
    {{
      "post_url": "https://www.facebook.com/snapshotpage/posts/one",
      "post_time_text": "1h",
      "crawled_at": "2026-06-03T12:00:00",
      "raw_text": "candidate one"
    }}
  ]
}}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/snapshotpage",
                "--account-name",
                "Snapshot Page",
                "--target-date",
                "260603",
                "--max-snapshots",
                "44",
                "--min-snapshots",
                "9",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode == 0, result.stdout or result.stderr
    argv = json.loads(args_file.read_text(encoding="utf-8"))
    assert "--max-snapshots" in argv
    assert argv[argv.index("--max-snapshots") + 1] == "44"
    assert "--min-snapshots" in argv
    assert argv[argv.index("--min-snapshots") + 1] == "9"


def assert_run_capture_pipeline_auto_retries_snapshot_cap(tmp_path: Path) -> None:
    config = tmp_path / "settings_capture_retry_snapshots.yaml"
    fake_bin = tmp_path / "bin-capture-retry-snapshots"
    fake_opencli = tmp_path / "fake-opencli-capture-retry-snapshots"
    calls_file = tmp_path / "capture-retry-calls.json"
    db_path = tmp_path / "capture-retry-snapshots.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        f"""#!{PYTHON}
import json
import pathlib
import sys
calls_path = pathlib.Path(r"{calls_file}")
calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
calls.append(sys.argv)
calls_path.write_text(json.dumps(calls), encoding="utf-8")
max_snapshots = int(sys.argv[sys.argv.index("--max-snapshots") + 1])
if max_snapshots < 32:
    payload = {{
        "ok": True,
        "post_count": 1,
        "raw_candidate_count": 1,
        "capture_complete": False,
        "coverage_incomplete": True,
        "coverage": {{
            "capture_complete": False,
            "coverage_incomplete": True,
            "stop_reason": "max_snapshots",
            "message": "hit cap"
        }},
        "snapshots": [{{"visible_time_texts": ["1h"], "new_posts": 1}}],
        "posts": [
            {{
                "post_url": "https://www.facebook.com/captureretry/posts/one",
                "post_time_text": "1h",
                "crawled_at": "2026-06-03T12:00:00",
                "raw_text": "candidate one"
            }}
        ]
    }}
else:
    payload = {{
        "ok": True,
        "post_count": 2,
        "raw_candidate_count": 2,
        "capture_complete": True,
        "coverage": {{
            "capture_complete": True,
            "coverage_incomplete": False,
            "stop_reason": "stable_no_new_posts"
        }},
        "snapshots": [{{"visible_time_texts": ["1h", "2h"], "new_posts": 0}}],
        "posts": [
            {{
                "post_url": "https://www.facebook.com/captureretry/posts/one",
                "post_time_text": "1h",
                "crawled_at": "2026-06-03T12:00:00",
                "raw_text": "candidate one"
            }},
            {{
                "post_url": "https://www.facebook.com/captureretry/posts/two",
                "post_time_text": "2h",
                "crawled_at": "2026-06-03T12:00:00",
                "raw_text": "candidate two"
            }}
        ]
    }}
print(json.dumps(payload, ensure_ascii=False))
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/captureretry",
                "--account-name",
                "Capture Retry",
                "--target-date",
                "260603",
                "--max-snapshots",
                "20",
                "--min-snapshots",
                "6",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode == 0, result.stdout or result.stderr
    data = json.loads(result.stdout)
    calls = json.loads(calls_file.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert calls[0][calls[0].index("--max-snapshots") + 1] == "20"
    assert calls[1][calls[1].index("--max-snapshots") + 1] == "32"
    assert data["run_status"] != "coverage_incomplete"
    assert data["post_count"] == 2
    assert data["coverage"]["coverage_incomplete"] is False
    assert data["coverage"]["auto_retry"]["attempted"] is True
    assert data["coverage"]["auto_retry"]["resolved"] is True
    assert data["discover_retry"]["attempted"] is True
    assert data["discover_retry"]["attempts"][0]["coverage_incomplete"] is True
    assert data["discover_retry"]["attempts"][1]["coverage_incomplete"] is False
    assert data["quality_summary"]["coverage_health"] == "complete"


def assert_run_capture_pipeline_promotes_human_intervention_discover(tmp_path: Path) -> None:
    config = tmp_path / "settings_capture_login.yaml"
    fake_bin = tmp_path / "bin-login"
    fake_opencli = tmp_path / "fake-opencli-login"
    db_path = tmp_path / "capture-login.sqlite"
    opencli_status = start_opencli_status_server()
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {db_path}")
    config.write_text(text, encoding="utf-8")
    fake_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
    fake_opencli.chmod(0o755)
    fake_bin.mkdir()
    (fake_bin / "node").write_text(
        """#!/usr/bin/env python3
import json
payload = {
  "ok": False,
  "status": "login_required",
  "action_required": "human_intervention_required",
  "message": "Facebook login required"
}
print(json.dumps(payload, ensure_ascii=False))
raise SystemExit(3)
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    try:
        result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(config),
                "--account-url",
                "https://www.facebook.com/loginblocked",
                "--target-date",
                "260603",
            ],
            env=env,
        )
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()
    assert result.returncode != 0, result.stdout
    data = json.loads(result.stdout)
    assert data["stage"] == "human_intervention_required"
    assert data["run_status"] == "human_intervention_required"
    assert data["complete"] is False
    assert data["human_intervention_required"] is True
    assert any("登录态" in action or "Chrome Profile" in action for action in data["next_actions"])
    assert data["next_commands"][0]["reason"] == "human_intervention_required"
    assert "run_account_job.py" in data["next_commands"][0]["command"]
    assert "--resume-only" not in data["next_commands"][0]["command"]


def assert_run_capture_pipeline_structures_prepare_and_import_failures(tmp_path: Path) -> None:
    opencli_status = start_opencli_status_server()
    try:
        prepare_config = tmp_path / "settings_capture_prepare_fail.yaml"
        prepare_bin = tmp_path / "bin-prepare-fail"
        prepare_opencli = tmp_path / "fake-opencli-prepare-fail"
        shutil.copy(ROOT / "config" / "settings.yaml.example", prepare_config)
        text = prepare_config.read_text(encoding="utf-8")
        text = text.replace("opencli_path: auto", f"opencli_path: {prepare_opencli}")
        text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
        text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'prepare-fail.sqlite'}")
        prepare_config.write_text(text, encoding="utf-8")
        prepare_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
        prepare_opencli.chmod(0o755)
        prepare_bin.mkdir()
        (prepare_bin / "node").write_text(
            """#!/usr/bin/env python3
import json
payload = {
  "ok": True,
  "post_count": 1,
  "raw_candidate_count": 1,
  "capture_complete": True,
  "coverage": {"capture_complete": True},
  "posts": {"bad": "shape"}
}
print(json.dumps(payload, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        (prepare_bin / "node").chmod(0o755)
        prepare_env = dict(os.environ)
        prepare_env["PATH"] = f"{prepare_bin}:{prepare_env.get('PATH', '')}"
        prepare_result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(prepare_config),
                "--account-url",
                "https://www.facebook.com/preparefail",
                "--target-date",
                "260603",
            ],
            env=prepare_env,
        )
        assert prepare_result.returncode != 0, prepare_result.stdout
        prepare_data = json.loads(prepare_result.stdout)
        assert prepare_data["run_status"] == "prepare_failed"
        assert prepare_data["complete"] is False
        assert prepare_data["discover"]["post_count"] == 1
        assert any("标准化失败" in action for action in prepare_data["next_actions"])
        assert prepare_data["next_commands"][0]["reason"] == "prepare_failed"
        assert "run_account_job.py" in prepare_data["next_commands"][0]["command"]
        assert "--resume-only" not in prepare_data["next_commands"][0]["command"]

        import_config = tmp_path / "settings_capture_import_fail.yaml"
        import_bin = tmp_path / "bin-import-fail"
        import_opencli = tmp_path / "fake-opencli-import-fail"
        shutil.copy(ROOT / "config" / "settings.yaml.example", import_config)
        text = import_config.read_text(encoding="utf-8")
        text = text.replace("opencli_path: auto", f"opencli_path: {import_opencli}")
        text = text.replace("opencli_daemon_port: 19825", f"opencli_daemon_port: {opencli_status.server_port}")
        text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path}")
        import_config.write_text(text, encoding="utf-8")
        import_opencli.write_text("#!/bin/sh\necho '1.8.1'\nexit 0\n", encoding="utf-8")
        import_opencli.chmod(0o755)
        import_bin.mkdir()
        (import_bin / "node").write_text(
            """#!/usr/bin/env python3
import json
payload = {
  "ok": True,
  "post_count": 1,
  "raw_candidate_count": 1,
  "capture_complete": True,
  "coverage": {"capture_complete": True},
  "posts": [
    {
      "post_url": "https://www.facebook.com/importfail/posts/one",
      "post_time_text": "1h",
      "crawled_at": "2026-06-03T12:00:00",
      "raw_text": "candidate one"
    }
  ]
}
print(json.dumps(payload, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        (import_bin / "node").chmod(0o755)
        import_env = dict(os.environ)
        import_env["PATH"] = f"{import_bin}:{import_env.get('PATH', '')}"
        import_result = run(
            [
                PYTHON,
                "scripts/run_capture_pipeline.py",
                "--config",
                str(import_config),
                "--account-url",
                "https://www.facebook.com/importfail",
                "--target-date",
                "260603",
            ],
            env=import_env,
        )
        assert import_result.returncode != 0, import_result.stdout
        import_data = json.loads(import_result.stdout)
        assert import_data["run_status"] == "import_failed"
        assert import_data["complete"] is False
        assert import_data["prepared"] == 1
        assert any("本地入库失败" in action for action in import_data["next_actions"])
        assert import_data["next_commands"][0]["reason"] == "import_failed"
        assert "run_account_job.py" in import_data["next_commands"][0]["command"]
        assert "--resume-only" not in import_data["next_commands"][0]["command"]
    finally:
        opencli_status.shutdown()
        opencli_status.server_close()


def assert_run_account_job_scope_includes_unknown_date_candidates(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_unknown_date.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "database_path: data/posts.sqlite", f"database_path: {tmp_path / 'account-job-unknown-date.sqlite'}"
        ),
        encoding="utf-8",
    )
    sample = tmp_path / "unknown_date_job.json"
    sample.write_text(
        json.dumps(
            {
                "posts": [
                    {
                        "account_name": "Resume Page",
                        "account_url": "https://www.facebook.com/resumepage",
                        "post_url": "https://www.facebook.com/resumepage/posts/known-date",
                        "posted_date": "260602",
                        "posted_at": "2026年6月2日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "story_summary": "Visible homepage candidate.",
                    },
                    {
                        "account_name": "Resume Page",
                        "account_url": "https://www.facebook.com/resumepage",
                        "post_url": "https://www.facebook.com/resumepage/posts/date-pending",
                        "story_summary": "Visible homepage candidate with date pending.",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(sample), "--no-sync"])
    assert imported.returncode == 0, imported.stdout
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job
    from store import connect

    conn = connect(tmp_path / "account-job-unknown-date.sqlite")
    scoped = run_account_job.scoped_posts(
        conn,
        account_name="Resume Page",
        account_url="https://www.facebook.com/resumepage",
        account_type="competitor",
        dates=["260602"],
    )
    assert {post["post_url"] for post in scoped} == {
        "https://facebook.com/resumepage/posts/known-date",
        "https://facebook.com/resumepage/posts/date-pending",
    }

    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/resumepage",
            "--account-name",
            "Resume Page",
            "--target-date",
            "260602",
            "--resume-only",
            "--status-only",
            "--sync",
            "--dry-run",
            "--allow-incomplete-success",
        ]
    )
    assert job.returncode == 0, job.stdout
    data = json.loads(job.stdout)
    assert data["post_count"] == 2
    assert data["feishu_sync"]["output_candidates"] == 2


def assert_expected_coverage_marks_missing_posts() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import coverage_expectations

    payload = {
        "ok": True,
        "post_count": 9,
        "coverage": {"capture_complete": True},
        "snapshots": [
            {"visible_time_texts": ["38m", "1h", "2h", "3h"]},
            {"visible_time_texts": ["4h", "5h", "6h", "7h", "8h"]},
        ],
        "posts": [{"post_time_text": "9h"}],
    }
    checked = coverage_expectations.apply_expected_coverage(
        payload,
        expected_post_count=13,
        expected_labels=coverage_expectations.split_expected_labels("38m,1h,2h,10h,11h"),
    )
    expected = checked["coverage"]["expected"]
    assert checked["coverage_incomplete"] is True
    assert checked["capture_complete"] is False
    assert checked["coverage"]["expected_coverage_failed"] is True
    assert expected["enabled"] is True
    assert expected["ok"] is False
    assert expected["missing_post_count"] == 4
    assert expected["post_count_coverage_rate"] == 0.6923
    assert expected["expected_label_count"] == 5
    assert expected["matched_label_count"] == 3
    assert expected["matched_labels"] == ["38m", "1h", "2h"]
    assert expected["label_coverage_rate"] == 0.6
    assert expected["missing_labels"] == ["10h", "11h"]
    assert "期望至少 13 条" in expected["message"]
    assert "10h" in checked["coverage"]["message"]

    normalized = coverage_expectations.expected_coverage_check(
        {
            "post_count": 3,
            "snapshots": [{"visible_time_texts": ["38m", "1h", "2 小时"]}],
        },
        expected_post_count=0,
        expected_labels=coverage_expectations.split_expected_labels("38 min,1 hour ago,2小时"),
    )
    assert normalized["ok"] is True
    assert normalized["matched_labels"] == ["38 min", "1 hour ago", "2小时"]
    assert normalized["missing_labels"] == []

    clean = coverage_expectations.apply_expected_coverage(payload, expected_post_count=0, expected_labels=[])
    assert clean == payload


def assert_run_account_job_expected_coverage_marks_missing_posts() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    payload = {
        "ok": True,
        "post_count": 9,
        "coverage": {"capture_complete": True},
        "snapshots": [{"visible_time_texts": ["38m", "1h", "2h"]}],
    }
    checked = run_account_job.apply_expected_coverage(
        payload,
        expected_post_count=13,
        expected_labels=["38m", "1h", "10h"],
    )
    assert checked["coverage_incomplete"] is True
    assert checked["coverage"]["expected"]["missing_post_count"] == 4
    assert checked["coverage"]["expected"]["missing_labels"] == ["10h"]


def assert_run_account_job_blocks_auth_before_capture(tmp_path: Path) -> None:
    config = tmp_path / "settings_account_job_auth.yaml"
    fake_lark = tmp_path / "fake-lark-cli"
    fake_opencli = tmp_path / "fake-opencli"
    opencli_called = tmp_path / "opencli-called"
    shutil.copy(ROOT / "config" / "settings.yaml.example", config)
    text = config.read_text(encoding="utf-8")
    text = text.replace("lark_cli_path: auto", f"lark_cli_path: {fake_lark}")
    text = text.replace("opencli_path: auto", f"opencli_path: {fake_opencli}")
    text = text.replace("database_path: data/posts.sqlite", f"database_path: {tmp_path / 'account-job-auth.sqlite'}")
    config.write_text(text, encoding="utf-8")
    fake_lark.write_text(
        """#!/bin/sh
if [ "$1" = "config" ]; then
  echo "$2: user"
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  echo '{"identity":"bot","tokenStatus":"valid"}'
  exit 0
fi
echo '{}'
exit 0
""",
        encoding="utf-8",
    )
    fake_opencli.write_text(
        f"""#!/bin/sh
touch {opencli_called}
echo '1.8.1'
exit 0
""",
        encoding="utf-8",
    )
    fake_lark.chmod(0o755)
    fake_opencli.chmod(0o755)

    job = run(
        [
            PYTHON,
            "scripts/run_account_job.py",
            "--config",
            str(config),
            "--account-url",
            "https://www.facebook.com/authblocked",
            "--account-name",
            "Auth Blocked",
            "--target-date",
            "260602",
            "--sync",
        ]
    )
    assert job.returncode == 1, job.stdout
    data = json.loads(job.stdout)
    assert data["run_status"] == "blocked_auth"
    assert data["complete"] is False
    assert data["feishu_auth_preflight"]["ok"] is False
    assert any(item["reason"] == "blocked_auth" for item in data["next_commands"])
    assert "--resume-only" not in data["next_commands"][0]["command"]
    assert "--force-recover-running" not in data["next_commands"][0]["command"]
    assert "--target-date 260602" in data["next_commands"][0]["command"]
    assert "--sync" in data["next_commands"][0]["command"]
    assert "opencli_preflight" not in data
    assert not opencli_called.exists()


def assert_run_account_job_blocked_auth_resume_only_keeps_resume_command() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/authblocked",
            "account_name": "Auth Blocked",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "strict_ready_only": False,
            "resume_only": True,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "expected_post_count": 0,
            "expected_labels": "",
        },
    )()
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260602"],
        run_status="blocked_auth",
        completion={"post_count": 1, "has_incomplete_enrichment": True},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert commands[0]["reason"] == "blocked_auth"
    assert "--resume-only" in commands[0]["command"]
    assert "--force-recover-running" in commands[0]["command"]


def assert_run_account_job_promotes_sync_failure_status() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    completion = {
        "post_count": 1,
        "has_incomplete_enrichment": False,
        "requires_codex_summary_count": 0,
        "coverage_incomplete_count": 0,
    }
    status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[],
        sync_result={"ok": False, "run_status": "sync_failed", "stage": "feishu_write"},
        completion=completion,
    )
    assert status == "sync_failed"

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/syncfailed",
            "account_name": "Sync Failed",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "strict_ready_only": False,
            "resume_only": False,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "expected_post_count": 0,
            "expected_labels": "",
        },
    )()
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260602"],
        run_status=status,
        completion=completion,
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert commands[0]["reason"] == "sync_failed"
    assert "--resume-only" in commands[0]["command"]
    assert "--sync" in commands[0]["command"]

    quality_status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[],
        sync_result={"ok": False, "run_status": "quality_gate", "stage": "quality_gate"},
        completion=completion,
    )
    assert quality_status == "quality_gate"
    quality_commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260602"],
        run_status=quality_status,
        completion={**completion, "has_auto_enrichment_work": True, "auto_open_task_count": 1},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert quality_commands[0]["reason"] == "pending_enrichment"
    assert "先补齐详情字段并回写飞书" in quality_commands[0]["description"]
    assert quality_commands[1]["reason"] == "quality_gate"


def assert_run_account_job_blocked_opencli_resume_command_matches_context() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    base_attrs = {
        "config": "config/settings.yaml",
        "account_url": "https://www.facebook.com/opencliblocked",
        "account_name": "OpenCLI Blocked",
        "account_type": "competitor",
        "sync": True,
        "dry_run": False,
        "strict_ready_only": False,
        "max_snapshots": 20,
        "min_snapshots": 6,
        "max_resume_passes": 2,
        "expected_post_count": 0,
        "expected_labels": "",
    }
    resume_args = type("Args", (), {**base_attrs, "resume_only": True})()
    resume_commands = run_account_job.next_commands_for_status(
        args=resume_args,
        target_dates=["260602"],
        run_status="blocked_opencli",
        completion={"post_count": 1, "has_auto_enrichment_work": True, "auto_open_task_count": 1},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert [item["reason"] for item in resume_commands] == ["blocked_opencli", "resume_after_opencli"]
    assert "--resume-only" in resume_commands[1]["command"]
    assert "--force-recover-running" in resume_commands[1]["command"]

    capture_args = type("Args", (), {**base_attrs, "resume_only": False})()
    capture_commands = run_account_job.next_commands_for_status(
        args=capture_args,
        target_dates=["260602"],
        run_status="blocked_opencli",
        completion={"post_count": 0, "has_auto_enrichment_work": False},
        discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
    )
    assert [item["reason"] for item in capture_commands] == ["blocked_opencli", "rerun_full_capture"]
    assert "--resume-only" not in capture_commands[1]["command"]


def assert_run_account_job_promotes_discover_coverage_status() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    discover_import = {
        "ok": True,
        "discover": {
            "capture_complete": False,
            "coverage": {
                "coverage_incomplete": True,
                "capture_complete": False,
                "stop_reason": "max_snapshots",
                "message": "采集达到快照上限时仍有新增候选。",
            },
            "raw_candidate_count": 12,
            "post_count": 12,
        },
    }
    completion = {
        "requires_codex_summary_count": 0,
        "coverage_incomplete_count": 0,
        "has_incomplete_enrichment": False,
        "open_task_count": 9,
        "auto_open_task_count": 6,
        "open_task_stage_counts": {"detail_time": 6, "post_type": 3},
        "missing_stage_counts": {"detail_time": 6, "post_type": 3},
    }
    status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=discover_import,
        worker_passes=[],
        sync_result={"ok": True},
        completion=completion,
    )
    summary = run_account_job.discover_coverage_summary(discover_import)
    next_commands = run_account_job.next_commands_for_status(
        args=type(
            "Args",
            (),
            {
                "config": "config/settings.yaml",
                "account_url": "https://www.facebook.com/example",
                "account_name": "Example Page",
                "account_type": "competitor",
                "sync": True,
                "dry_run": False,
                "max_snapshots": 20,
                "min_snapshots": 6,
                "max_resume_passes": 2,
                "expected_post_count": 13,
                "expected_labels": "38m,1h,2h",
            },
        )(),
        target_dates=["260602"],
        run_status=status,
        completion=completion,
        discover_coverage=summary,
    )
    assert status == "coverage_incomplete"
    assert summary["complete"] is False
    assert summary["incomplete"] is True
    assert summary["reasons"] == ["capture_incomplete", "coverage_incomplete"]
    assert summary["raw_candidate_count"] == 12
    assert summary["stop_reason"] == "max_snapshots"
    assert [item["reason"] for item in next_commands[:2]] == ["pending_enrichment", "coverage_incomplete"]
    assert "--resume-only" in next_commands[0]["command"]
    assert "--force-recover-running" in next_commands[0]["command"]
    coverage_command = next_commands[1]["command"]
    assert "--max-snapshots 32" in coverage_command
    assert "--min-snapshots 6" in coverage_command
    assert "--expected-post-count 13" in coverage_command
    assert "--expected-labels" in coverage_command
    assert "38m,1h,2h" in coverage_command
    quality = run_account_job.account_job_quality_summary(
        run_status=status,
        discover_coverage=summary,
        completion={
            "post_count": 12,
            "ledger_candidate_count": 12,
            "ledger_usable_rate": 1.0,
            "final_usable_count": 0,
            "final_usable_rate": 0.0,
            "completion_rate": 0.25,
            "incomplete_post_count": 9,
            "coverage_incomplete_count": 0,
            "open_task_count": 9,
            "auto_open_task_count": 6,
            "requires_codex_summary_count": 3,
            "top_field_gaps": [{"reason": "exact_time", "label": "精确时间", "count": 12, "stage": "detail_time"}],
        },
        sync_result={"ok": True, "run_status": "synced_ledger_incomplete", "dry_run": True, "output_candidates": 12},
        thresholds={
            "require_coverage_complete": True,
            "min_ledger_usable_rate": 1.0,
            "min_final_usable_rate": 0.5,
            "min_completion_rate": 0.5,
        },
    )
    assert quality["run_status"] == "coverage_incomplete"
    assert quality["coverage_health"] == "incomplete"
    assert quality["coverage_complete"] is False
    assert quality["coverage_stop_reason"] == "max_snapshots"
    assert quality["discovered_post_count"] == 12
    assert quality["ledger_candidate_count"] == 12
    assert quality["ledger_usable_rate"] == 1.0
    assert quality["final_usable_rate"] == 0.0
    assert quality["top_field_gaps"][0]["reason"] == "exact_time"
    assert quality["feishu_sync"]["enabled"] is True
    assert quality["feishu_sync"]["output_candidates"] == 12
    blocker_codes = [item["code"] for item in quality["completion_blockers"]]
    assert blocker_codes[:2] == ["coverage_incomplete", "codex_summary_required"]
    assert "field_gaps" in blocker_codes
    assert "quality_threshold_failed" in blocker_codes
    coverage_blocker = next(item for item in quality["completion_blockers"] if item["code"] == "coverage_incomplete")
    assert coverage_blocker["metrics"]["coverage_stop_reason"] == "max_snapshots"
    assert coverage_blocker["metrics"]["discovered_post_count"] == 12
    threshold_result = quality["quality_thresholds"]
    assert threshold_result["enabled"] is True
    assert threshold_result["ok"] is False
    assert [failure["metric"] for failure in threshold_result["failures"]] == [
        "coverage_health",
        "final_usable_rate",
        "completion_rate",
    ]
    assert any("覆盖率未达标" in action for action in threshold_result["next_actions"])
    assert any("可用率未达标" in action for action in threshold_result["next_actions"])


def assert_run_account_job_promotes_human_intervention_status() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_account_job

    args = type(
        "Args",
        (),
        {
            "config": "config/settings.yaml",
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example Page",
            "account_type": "competitor",
            "sync": True,
            "dry_run": False,
            "max_snapshots": 20,
            "min_snapshots": 6,
            "max_resume_passes": 2,
            "expected_post_count": 0,
            "expected_labels": "",
        },
    )()
    discover_import = {
        "ok": False,
        "stage": "human_intervention_required",
        "discover": {
            "ok": False,
            "status": "login_required",
            "action_required": "human_intervention_required",
        },
    }
    completion = {
        "requires_codex_summary_count": 0,
        "coverage_incomplete_count": 0,
        "has_incomplete_enrichment": False,
        "open_task_count": 0,
    }
    status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=discover_import,
        worker_passes=[],
        sync_result={"ok": True},
        completion=completion,
    )
    commands = run_account_job.next_commands_for_status(
        args=args,
        target_dates=["260602"],
        run_status=status,
        completion=completion,
        discover_coverage=run_account_job.discover_coverage_summary(discover_import),
    )
    assert status == "human_intervention_required"
    assert commands[0]["reason"] == "human_intervention_required"
    assert "--resume-only" not in commands[0]["command"]
    assert "--force-recover-running" not in commands[0]["command"]
    assert "--target-date 260602" in commands[0]["command"]
    assert "check_env.py" not in commands[0]["command"]

    worker_status = run_account_job.summarize_job_status(
        preflight={"ok": True},
        discover_import=None,
        worker_passes=[{"human_intervention_required": True, "human_intervention_reasons": ["visitor_preview"]}],
        sync_result={"ok": True},
        completion={**completion, "has_incomplete_enrichment": True},
    )
    assert worker_status == "human_intervention_required"


def assert_enrichment_worker_reports_human_intervention_batch(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import enrichment_worker

    fake_node = tmp_path / "node"
    payload_path = tmp_path / "blocked_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "posts": [],
                "status": "human_intervention_required",
                "action_required": "human_intervention_required",
                "blocked_reason": "login_required",
                "human_intervention_required": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_node.write_text(
        f"""#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    shift
    cp {payload_path} "$1"
  fi
  shift
done
echo '{{"ok":false,"status":"human_intervention_required","action_required":"human_intervention_required"}}'
exit 1
""",
        encoding="utf-8",
    )
    fake_node.chmod(0o755)
    original_root = enrichment_worker.ROOT
    original_run = enrichment_worker.subprocess.run

    def fake_run(command, **kwargs):
        return original_run([str(fake_node), *command[1:]], **kwargs)

    try:
        enrichment_worker.subprocess.run = fake_run
        result = enrichment_worker.run_detail_batch(
            "config/settings.yaml",
            {"performance": {"detail_timeout_seconds": 1}},
            [{"post_url": "https://facebook.com/example/posts/one"}],
            {"detail_time"},
            "260602",
        )
    finally:
        enrichment_worker.subprocess.run = original_run
        enrichment_worker.ROOT = original_root
    assert result["ok"] is False
    assert result["human_intervention_required"] is True
    assert result["status"] == "human_intervention_required"
    assert result["reason"] == "login_required"


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
    server, base_url = start_static_http_server(tmp_path)
    article_url = f"{base_url}/{article.name}"
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
                        "article_url": article_url,
                        "landing_url": article_url,
                        "lead_url_raw": article_url,
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
                        "article_url": article_url,
                        "landing_url": article_url,
                        "lead_url_raw": article_url,
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
    assert import_data["enrichment_tasks"]["candidate_count"] == 2
    assert import_data["enrichment_tasks"]["stage_counts"]["article_material"] == 2
    assert import_data["enrichment_tasks"]["open_stage_counts"]["summary"] == 2

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
    assert summary_worker.returncode == 2, summary_worker.stdout + summary_worker.stderr
    summary_data = json.loads(summary_worker.stdout)
    assert summary_data["run_status"] == "needs_codex_summary"
    assert summary_data["codex_summary_required"] is True
    assert summary_data["codex_summary_required_count"] == 2
    assert len(summary_data["codex_summary_required_urls"]) == 2

    sys.path.insert(0, str(ROOT / "scripts"))
    from store import all_posts, cached_article_material, connect, pending_enrichment_tasks

    conn = connect(db_path)
    posts = all_posts(conn)
    assert all(post["output_status"] != "ready_for_output" for post in posts)
    assert cached_article_material(conn, article_url)["ok"] is True
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
        json.dumps({article_url: "Worker cache story"}, ensure_ascii=False),
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
                article_url: "这篇故事围绕家庭资产控制展开，儿子试图冻结母亲信用卡并掌控公司，母亲发现异常后准备通过法律方式反击。"
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
    assert all(post["summary_source"] == "article" for post in posts)
    assert all(post["story_summary"] for post in posts)
    assert all(post["output_status"] == "ready_for_output" for post in posts)
    server.shutdown()
    server.server_close()


def assert_enrichment_worker_keeps_failed_article_material_open(tmp_path: Path) -> None:
    config = tmp_path / "settings_article_fail.yaml"
    db_path = tmp_path / "article-fail.sqlite"
    raw = tmp_path / "article_fail.json"
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
                        "account_name": "Article Fail",
                        "account_url": "https://www.facebook.com/articlefail",
                        "post_url": "https://www.facebook.com/articlefail/posts/one",
                        "posted_at": "2026年6月3日 10:00",
                        "time_confirmed": True,
                        "time_source": "dom_aria_label",
                        "article_url": "http://127.0.0.1:1/unreachable-article",
                        "landing_url": "http://127.0.0.1:1/unreachable-article",
                        "lead_url_raw": "http://127.0.0.1:1/unreachable-article",
                        "lead_link_status": "qualified",
                        "lead_link_source": "comment",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--no-sync"])
    assert imported.returncode == 0, imported.stderr or imported.stdout
    worker = run(
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
            "1",
        ]
    )
    assert worker.returncode == 1, worker.stderr or worker.stdout
    data = json.loads(worker.stdout)
    assert data["completed"] == 0
    assert data["failed"] == 1
    assert data["task_counts"].get("article_material:failed") == 1
    sys.path.insert(0, str(ROOT / "scripts"))
    from store import all_posts, connect
    from story_summary_policy import article_material_for_post

    conn = connect(db_path)
    post = all_posts(conn)[0]
    assert article_material_for_post(post) == {}


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
    formal = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--sync", "--strict-ready-only", "--dry-run"])
    assert formal.returncode == 1, formal.stdout
    assert '"ready_for_output": 0' in formal.stdout

    audit = run([PYTHON, "scripts/import_existing_result.py", "--config", str(config), "--input", str(raw), "--sync-audit", "--dry-run"])
    assert audit.returncode == 0, audit.stdout
    assert '"audit_output": true' in audit.stdout
    assert '"output_candidates": 1' in audit.stdout

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
    assert_time_confirmed_string_false_is_not_ready()
    assert_field_schema_controls_output_rows()
    assert_audit_marker_is_written_to_adoption_status()
    assert_ledger_marker_includes_time_summary_and_coverage()
    assert_feishu_upsert_merges_rows_without_overwriting_manual_adoption()
    with tempfile.TemporaryDirectory() as tmp:
        assert_feishu_upsert_matches_canonical_post_urls(Path(tmp))
    assert_sync_feishu_audit_and_strict_modes()
    assert_generic_photo_canonical_is_recomputed()
    assert_mobile_dom_extractor_can_see_story_links()
    assert_dom_extractor_does_not_treat_story_clock_as_post_time()
    assert_dom_extractor_splits_multi_post_container()
    assert_dom_extractor_excludes_profile_shell_with_external_link()
    assert_dom_extractor_blocks_visitor_preview()
    assert_dom_extractor_prefers_parent_post_over_photo_link()
    assert_dom_extractor_keeps_path_photo_without_parent_post()
    assert_detail_engagement_is_anchored_to_main_post()
    assert_detail_enrichment_ignores_page_shell_ad_links()
    assert_detail_enrichment_detects_plain_text_comment_links()
    assert_detail_post_type_expression_classifies_business_types()
    assert_comment_mode_expression_can_select_all_comments()
    assert_opencli_extract_helpers_dedupe_homepage_candidates()
    assert_opencli_extract_has_under_capture_guards()
    assert_opencli_extract_stable_end_is_complete_coverage()
    assert_opencli_extract_script_requires_human_intervention()
    assert_opencli_runtime_keeps_current_bound_tab()
    assert_opencli_runtime_requires_matching_account_tab()
    assert_opencli_tab_tracker_closes_only_registered_tabs()
    assert_opencli_detail_session_lock_recovers_stale_files()
    assert_opencli_detail_enrichment_reuses_tab_with_fallback()
    assert_opencli_detail_enrichment_blocks_for_human_login()
    assert_feishu_writes_require_user_identity()
    assert_sync_failures_include_recovery_actions()
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
        assert_sqlite_upsert_preserves_enriched_fields(tmp_path)
        assert_sqlite_upsert_preserves_article_material_payload(tmp_path)
        assert_sqlite_upsert_resyncs_previously_synced_rows(tmp_path)
        assert_sqlite_upsert_does_not_protect_internal_lead_links(tmp_path)
        assert_sqlite_upsert_dedupes_equivalent_media_urls(tmp_path)
        assert_field_audit_marks_refetchable_missing_fields(tmp_path)
        assert_cli_feishu_auth_blockers_report_run_status(tmp_path)
        assert_import_existing_result_reports_structured_input_failures(tmp_path)
        assert_prepare_capture_reports_structured_input_failures(tmp_path)
        assert_article_summary_scripts_report_structured_input_failures(tmp_path)
        assert_sync_status_marks_incomplete_ledger(tmp_path)
        assert_sync_status_promotes_summary_only_work(tmp_path)
        assert_sync_status_prioritizes_auto_work_over_summary(tmp_path)
        assert_completion_summary_uses_quality_audit_config(tmp_path)
        assert_strict_sync_uses_quality_audit_config(tmp_path)
        assert_export_summary_requests_can_scope_account_job(tmp_path)
        assert_apply_article_summaries_scopes_account_job(tmp_path)
        assert_generate_article_summaries_from_requests(tmp_path)
        assert_summary_request_prefers_article_material_source()
        assert_export_summary_requests_skips_rows_without_material(tmp_path)
        assert_enrich_article_summaries_prefers_article_url(tmp_path)
        assert_sync_feishu_strict_marks_ready_rows_synced(tmp_path)
        assert_minimal_ledger_candidate_syncs_to_formal_sheet(tmp_path)
        assert_strict_sync_completion_uses_full_candidate_scope(tmp_path)
        assert_prepare_capture_keeps_short_posts_and_blocks_sync(tmp_path)
        assert_prepare_capture_preserves_type_and_article_summary(tmp_path)
        assert_normalize_post_marks_existing_story_summary_as_article()
        assert_sync_rejects_estimated_relative_time_but_allows_partial_preview(tmp_path)
        assert_sync_retry_includes_previously_inserted_ready_rows(tmp_path)
        assert_article_url_alone_does_not_qualify_lead_link(tmp_path)
        assert_filter_sync_applies_output_quality_gate(tmp_path)
        assert_filter_sync_reports_audit_missing_field_counts(tmp_path)
        assert_quality_gate_rejects_internal_landing_url(tmp_path)
        assert_quality_gate_requires_raw_comment_lead_url(tmp_path)
        assert_comment_lead_link_overrides_ad_links(tmp_path)
        assert_prepare_capture_skips_bad_candidate_without_failing_batch(tmp_path)
        assert_prepare_capture_has_no_base_time_argument()
        assert_exact_time_verifier_summary_contract()
        assert_opencli_detail_enrichment_supports_target_date_filter()
        assert_opencli_detail_enrichment_rejects_string_false_time()
        assert_opencli_detail_enrichment_rejects_copied_article_summary()
        assert_prepare_capture_keeps_photo_media_links_as_candidates(tmp_path)
        assert_thirteen_incomplete_candidates_are_imported_for_enrichment(tmp_path)
        assert_prepare_capture_does_not_alert_media_when_parent_post_is_captured(tmp_path)
        assert_article_material_extractor(tmp_path)
        assert_partial_review_status_and_task_queue(tmp_path)
        assert_enrichment_worker_groups_detail_tasks_by_post(tmp_path)
        assert_enrichment_worker_requeues_opencli_session_busy(tmp_path)
        assert_enrichment_worker_lead_stage_requires_external_landing_url()
        assert_stale_running_enrichment_tasks_are_recovered(tmp_path)
        assert_enqueue_does_not_steal_active_running_tasks(tmp_path)
        assert_enqueue_reopens_done_tasks_when_fields_are_missing_again(tmp_path)
        assert_enrichment_worker_scopes_tasks_to_account(tmp_path)
        assert_enrichment_worker_scope_includes_unknown_date_candidates(tmp_path)
        assert_enrichment_worker_article_cache_and_summary(tmp_path)
        assert_enrichment_worker_keeps_failed_article_material_open(tmp_path)
        assert_story_summary_audit_downgrades_invalid_rows(tmp_path)
        assert_partial_sync_dry_run_does_not_replace_formal_gate(tmp_path)
        assert_run_account_job_resume_status_reports_incomplete(tmp_path)
        assert_run_account_job_quality_thresholds_fail_low_usable_rate(tmp_path)
        assert_run_account_job_quality_threshold_failure_has_recovery_command()
        assert_run_account_job_resume_blocks_opencli_before_detail_tasks(tmp_path)
        assert_run_account_job_recovers_scoped_running_tasks(tmp_path)
        assert_run_account_job_does_not_recover_fresh_running_tasks(tmp_path)
        assert_run_account_job_next_commands_force_recover_running()
        assert_run_account_job_recovery_commands_preserve_resume_budget()
        assert_run_account_job_does_not_resume_empty_coverage_scope()
        assert_run_account_job_reports_unsynced_local_completion_command()
        assert_run_account_job_reports_worker_retry_later()
        assert_run_account_job_summary_only_next_command_exports_requests()
        assert_run_account_job_skips_worker_for_summary_only_completion()
        assert_run_account_job_worker_pass_surfaces_summary_required()
        assert_run_account_job_continues_worker_passes_until_complete()
        assert_run_account_job_auto_exports_summary_requests(tmp_path)
        assert_run_account_job_applies_partial_generated_summaries()
        assert_run_account_job_generates_summary_while_post_type_pending()
        assert_run_account_job_rejects_noop_summary_apply()
        assert_run_account_job_worker_pass_reports_non_json_failure()
        assert_run_account_job_waits_for_article_material_before_summary_export()
        assert_run_capture_pipeline_uses_completion_status_helpers()
        assert_run_capture_pipeline_blocks_auth_before_opencli(tmp_path)
        assert_run_capture_pipeline_reports_opencli_blocker(tmp_path)
        assert_run_capture_pipeline_applies_expected_coverage(tmp_path)
        assert_run_account_job_passes_snapshot_budget(tmp_path)
        assert_run_account_job_auto_retries_snapshot_cap(tmp_path)
        assert_run_account_job_auto_retries_expected_coverage_gap(tmp_path)
        assert_run_accounts_job_runs_all_accounts_and_aggregates_status(tmp_path)
        assert_run_accounts_job_auto_follows_coverage_recovery(tmp_path)
        assert_run_accounts_job_prioritizes_detail_resume_over_coverage_rerun(tmp_path)
        assert_run_accounts_job_repeats_same_resume_until_complete(tmp_path)
        assert_run_accounts_job_extends_attempts_while_quality_improves(tmp_path)
        assert_run_accounts_job_treats_stage_progress_as_quality_improvement(tmp_path)
        assert_run_accounts_job_follows_recoverable_exit_one_commands(tmp_path)
        assert_run_accounts_job_opencli_blocker_preserves_batch_retry(tmp_path)
        assert_run_accounts_job_auth_blocker_preserves_batch_retry(tmp_path)
        assert_run_capture_pipeline_passes_snapshot_budget(tmp_path)
        assert_run_capture_pipeline_auto_retries_snapshot_cap(tmp_path)
        assert_run_capture_pipeline_promotes_human_intervention_discover(tmp_path)
        assert_run_capture_pipeline_structures_prepare_and_import_failures(tmp_path)
        assert_run_account_job_scope_includes_unknown_date_candidates(tmp_path)
        assert_expected_coverage_marks_missing_posts()
        assert_run_account_job_expected_coverage_marks_missing_posts()
        assert_run_account_job_blocks_auth_before_capture(tmp_path)
        assert_run_account_job_structures_prepare_and_import_failures(tmp_path)
        assert_run_account_job_blocked_auth_resume_only_keeps_resume_command()
        assert_run_account_job_promotes_sync_failure_status()
        assert_run_account_job_blocked_opencli_resume_command_matches_context()
        assert_run_account_job_promotes_discover_coverage_status()
        assert_run_account_job_promotes_human_intervention_status()
        assert_enrichment_worker_reports_human_intervention_batch(tmp_path)

    print("local pipeline acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
