import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';
import { cli, Strategy } from '@jackwener/opencli/registry';

const require = createRequire(import.meta.url);
const projectRoot = process.env.FB_COLLECTOR_PROJECT_ROOT || process.cwd();
const { browserExpression } = require(path.join(projectRoot, 'scripts', 'fb_dom_extractors.js'));
const {
  allowedLandingUrl,
  cleanExternalUrl,
  commentModeBrowserExpression,
  detailEngagementBrowserExpression,
  detailPostTypeBrowserExpression,
  embeddedPublishTimeExpression,
  exactTimeFromTargetExpression,
  expandCommentsExpression,
  focusDetailConversationExpression,
  headerTimeTargetExpression,
  leadLinkScanBrowserExpression,
  pageStateExpression,
  postCtaLeadLinkScanBrowserExpression,
  realMouseTooltipTimeExpression,
  sameNormalizedUrl,
  syntheticHoverTimeExpression,
} = require(path.join(projectRoot, 'scripts', 'fb_detail_extractors.js'));

function clean(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function intArg(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}

function dateKeyToDate(value) {
  const text = clean(value);
  const match = text.match(/^(\d{2})(\d{2})(\d{2})$/);
  if (!match) return null;
  const [, yy, mm, dd] = match;
  const year = 2000 + Number(yy);
  const month = Number(mm);
  const day = Number(dd);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return new Date(Date.UTC(year, month - 1, day, 0, 0, 0));
}

function parsePostTime(value) {
  const text = clean(value);
  if (!text) return null;
  let match = text.match(/^(20\d\d)年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$/);
  if (match) {
    const [, year, month, day, hour, minute] = match;
    return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), 0));
  }
  match = text.match(/^(20\d\d)-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$/);
  if (match) {
    const [, year, month, day, hour, minute] = match;
    return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), 0));
  }
  return null;
}

function parseRelativePostTime(value, reference = new Date()) {
  const text = clean(value).toLowerCase();
  if (!text) return null;
  if (/^just now$|^刚刚$/.test(text)) return new Date(reference.getTime());
  if (/^yesterday$|^昨天$/.test(text)) return new Date(reference.getTime() - 24 * 60 * 60 * 1000);
  let match = text.match(/^(\d+)\s*(m|min|mins|minute|minutes)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 1000);
  match = text.match(/^(\d+)\s*(h|hr|hrs|hour|hours)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*(d|day|days)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 24 * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*(w|wk|wks|week|weeks)(?:\s+ago)?$/i);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 7 * 24 * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*分钟$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 1000);
  match = text.match(/^(\d+)\s*小时$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*天$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 24 * 60 * 60 * 1000);
  match = text.match(/^(\d+)\s*周$/);
  if (match) return new Date(reference.getTime() - Number(match[1]) * 7 * 24 * 60 * 60 * 1000);
  return null;
}

function discoveryTimeWindow(kwargs) {
  const targetDate = dateKeyToDate(kwargs['target-date']);
  const postedAfter = parsePostTime(kwargs['posted-after']);
  const postedBefore = parsePostTime(kwargs['posted-before']);
  let lower = postedAfter;
  let upper = postedBefore;
  if (targetDate) {
    const nextDate = new Date(targetDate.getTime() + 24 * 60 * 60 * 1000);
    lower = lower && lower > targetDate ? lower : targetDate;
    upper = upper && upper < nextDate ? upper : nextDate;
  }
  return { lower, upper, enabled: Boolean(lower || upper) };
}

function postTimeState(post, window) {
  if (!window?.enabled) return 'unknown';
  const timeText = post?.posted_at || post?.posted_at_raw || post?.post_time_text;
  const parsed = parsePostTime(timeText);
  if (!parsed) return 'unknown';
  if (window.lower && parsed < window.lower) return 'before';
  if (window.upper && parsed >= window.upper) return 'after';
  return 'inside';
}

function boolArg(value) {
  if (value === true) return true;
  return /^(1|true|yes)$/i.test(String(value || ''));
}

function unwrap(value) {
  return value && typeof value === 'object' && 'data' in value ? value.data : value;
}

async function wait(page, seconds) {
  await page.wait({ time: Number(seconds) || 1 });
}

async function readPage(page, expression) {
  return unwrap(await page.evaluate(expression));
}

function cleanUrl(value) {
  try {
    const parsed = new URL(value);
    parsed.hash = '';
    const trackingKeys = new Set(['fbclid', 'comment_id', 'reply_comment_id', 'notif_id', 'notif_t', 'ref', 'refid', 'mibextid', 'rdid']);
    for (const key of [...parsed.searchParams.keys()]) {
      if (trackingKeys.has(key) || key.startsWith('utm_') || key.startsWith('__')) parsed.searchParams.delete(key);
    }
    return parsed.href;
  } catch {
    return String(value || '');
  }
}

function postKey(post) {
  const url = cleanUrl(post?.post_url || '');
  if (!url) return '';
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.split('/').filter(Boolean);
    const storyFbid = parsed.searchParams.get('story_fbid');
    const photoFbid = parsed.searchParams.get('fbid');
    const id = parsed.searchParams.get('id');
    if (storyFbid && id) return `story:${id}:${storyFbid}`;
    if (parts.includes('posts')) {
      const index = parts.indexOf('posts');
      if (index > 0 && parts[index + 1]) {
        if (index >= 2 && parts[index - 2] === 'groups') return `group-post:${parts[index - 1]}:${parts[index + 1]}`;
        return `post:${parts[index - 1]}:${parts[index + 1]}`;
      }
    }
    if (parts.includes('reel')) {
      const index = parts.indexOf('reel');
      if (parts[index + 1]) return `reel:${parts[index + 1]}`;
    }
    if (parts.includes('videos')) {
      const index = parts.indexOf('videos');
      if (parts[index + 1]) return `video:${parts[index + 1]}`;
    }
    if (parts.includes('video')) {
      const index = parts.indexOf('video');
      if (parts[index + 1]) return `video:${parts[index + 1]}`;
    }
    if (parts.includes('watch') && parsed.searchParams.get('v')) return `video:${parsed.searchParams.get('v')}`;
    if ((parsed.pathname.includes('photo.php') || parts.join('/') === 'photo') && photoFbid) return `photo:${photoFbid}`;
    if (parts.includes('photos')) {
      const index = parts.indexOf('photos');
      const tail = parts.slice(index + 1).filter((part) => !['a', 'p', 'photo'].includes(part));
      const numericTail = tail.filter((part) => /^\d{6,}$/.test(part));
      const photoId = numericTail.at(-1) || tail.at(-1);
      if (photoId) return `photo:${photoId}`;
    }
    if (parts.includes('share')) {
      const index = parts.indexOf('share');
      if (parts[index + 1]) return `share:${parts.slice(index + 1).join(':')}`;
    }
    if (parsed.hostname === 'fb.watch' && parts[0]) return `fb-watch:${parts[0]}`;
    return url;
  } catch {
    return url;
  }
}

function detailNavigationUrl(post) {
  const candidates = [
    post?.canonical_post_url,
    post?.parent_post_url,
    post?.post_url,
    post?.raw_fb_url,
  ];
  for (const candidate of candidates) {
    const cleaned = cleanUrl(candidate || '');
    if (cleaned) return cleaned;
  }
  return '';
}

function validCandidate(candidate) {
  const text = `${candidate.story_summary || ''} ${candidate.raw_text || ''}`;
  if (!candidate.post_url) return false;
  if (/^\s*Honor Reward\s+9\.9 万次赞/i.test(text)) return false;
  return true;
}

function captureCoverageState({ blockedExtraction = null, snapshots = [], stopReason = 'max_snapshots', maxSnapshots = 32 }) {
  const lastSnapshot = snapshots.at(-1) || {};
  const hitSnapshotCap =
    !blockedExtraction && snapshots.length >= Math.max(1, Number(maxSnapshots) || 1) && stopReason === 'max_snapshots';
  const coverageIncomplete = hitSnapshotCap && Number(lastSnapshot.new_posts || 0) > 0;
  return {
    coverage_blocked: false,
    coverage_incomplete: coverageIncomplete,
    capture_complete: !coverageIncomplete,
  };
}

function dateKeyFromPostedAt(postedAt) {
  const match = String(postedAt || '').match(/^(20\d\d)年(\d{1,2})月(\d{1,2})日\s+\d{2}:\d{2}$/);
  if (!match) return '';
  const [, year, month, day] = match;
  return `${year.slice(2)}${month.padStart(2, '0')}${day.padStart(2, '0')}`;
}

function parseBool(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (value === null || value === undefined || value === '') return false;
  return ['1', 'true', 'yes', 'y', 'on', 'confirmed'].includes(String(value).trim().toLowerCase());
}

function hasConfirmedTime(post) {
  return Boolean(
    post?.posted_at
    && parseBool(post?.time_confirmed)
    && !['relative_estimated', 'relative_hour', 'relative_label'].includes(post?.time_source || '')
  );
}

function appendSemicolonNote(existing, item) {
  const parts = String(existing || '').split('；').filter(Boolean);
  if (item && !parts.includes(item)) parts.push(item);
  return parts.join('；');
}

function hasQualifiedLeadLink(post, allowedDomains = []) {
  return Boolean(
    post.lead_link_status === 'qualified'
    && ['comment', 'comment_reply', 'post_cta'].includes(post.lead_link_source || '')
    && post.lead_url_raw
    && allowedLandingUrl(cleanExternalUrl(post.landing_url || post.article_url || ''), allowedDomains)
  );
}

function shouldReplaceLeadLink(post, leadLink, allowedDomains = []) {
  if (!leadLink || leadLink.status !== 'qualified') return false;
  if (!hasQualifiedLeadLink(post, allowedDomains)) return true;
  return sameNormalizedUrl(post.lead_url_raw, leadLink.lead_url_raw)
    || sameNormalizedUrl(post.landing_url || post.article_url, leadLink.landing_url);
}

async function resolveLandingUrl(href, allowedDomains = [], timeoutMs = 20000) {
  const cleaned = cleanExternalUrl(href);
  if (!cleaned || !allowedLandingUrl(cleaned, allowedDomains)) return '';
  const tryFetch = async (method) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(cleaned, { method, redirect: 'follow', signal: controller.signal });
      return cleanExternalUrl(response.url || cleaned) || cleaned;
    } finally {
      clearTimeout(timeout);
    }
  };
  for (const method of ['HEAD', 'GET']) {
    try {
      const resolved = await tryFetch(method);
      if (allowedLandingUrl(resolved, allowedDomains)) return resolved;
    } catch {
      // Some story sites block probes from Node; keep the cleaned URL below.
    }
  }
  return cleaned;
}

async function scrollToTop(page) {
  await page.evaluate(`(() => {
    const closeBlockingDialogs = () => {
      for (const node of [...document.querySelectorAll('[role="dialog"], [aria-label="Messenger"], [aria-label="Chats"]')]) {
        const text = String(node.innerText || node.textContent || '');
        if (!/Messenger|Chats|Chat history|PIN|New message|聊天|消息/i.test(text)) continue;
        const closeButton = node.querySelector('[aria-label="Close"], [aria-label="关闭"], [aria-label*="Close chat"]');
        closeButton?.click?.();
        node.style.setProperty('display', 'none', 'important');
        node.style.setProperty('visibility', 'hidden', 'important');
      }
    };
    closeBlockingDialogs();
    const candidates = [
      ...document.querySelectorAll('[role="main"], [data-pagelet*="ProfileTimeline"], [aria-label*="Timeline"], [aria-label*="Posts"]')
    ];
    for (const el of candidates) {
      if (!el || el.scrollHeight <= el.clientHeight + 80) continue;
      el.scrollTop = 0;
    }
    window.scrollTo(0, 0);
    return { y: window.scrollY || 0 };
  })()`);
  await wait(page, 1.2);
}

async function scrollDown(page, pixels) {
  return readPage(page, `(() => {
    const requested = ${Number(pixels) || 520};
    const viewportStep = Math.max(320, Math.floor((window.innerHeight || 800) * 0.55));
    const delta = Math.min(requested, viewportStep);
    const isBlockedOverlay = (el) => {
      const dialog = el.closest?.('[role="dialog"], [aria-label="Messenger"], [aria-label="Chats"]');
      if (!dialog) return false;
      const text = String(dialog.innerText || dialog.textContent || '');
      return /Messenger|Chats|Chat history|PIN|New message|聊天|消息/i.test(text);
    };
    const feedScore = (el) => {
      const attr = [
        el.getAttribute?.('role') || '',
        el.getAttribute?.('aria-label') || '',
        el.getAttribute?.('data-pagelet') || '',
        el.id || '',
        el.className || '',
      ].join(' ');
      const text = String(el.innerText || el.textContent || '').slice(0, 2000);
      let score = 0;
      if (el.matches?.('[role="main"]')) score += 100;
      if (/ProfileTimeline|Timeline|Posts|pagelet_timeline|recent/i.test(attr)) score += 70;
      if (/Follow|Followers|About|Photos|Videos|Reels|Posts/i.test(text)) score += 8;
      if (/Messenger|Chats|Chat history|PIN/i.test(text)) score -= 200;
      if (isBlockedOverlay(el)) score -= 500;
      return score;
    };
    const visibleEnough = (el) => {
      const rect = el.getBoundingClientRect?.();
      if (!rect) return false;
      return rect.width > 320 && rect.height > 300 && rect.bottom > 120 && rect.top < window.innerHeight - 80;
    };
    const scrollables = [
      ...document.querySelectorAll('[role="main"], [data-pagelet*="ProfileTimeline"], [aria-label*="Timeline"], [aria-label*="Posts"], div')
    ].filter((el) => {
      const style = getComputedStyle(el);
      const overflow = [style.overflowY, style.overflow].join(' ');
      return visibleEnough(el)
        && !isBlockedOverlay(el)
        && el.scrollHeight > el.clientHeight + 120
        && /(auto|scroll)/i.test(overflow);
    }).sort((a, b) => {
      const scoreDelta = feedScore(b) - feedScore(a);
      if (scoreDelta) return scoreDelta;
      return (b.clientHeight || 0) - (a.clientHeight || 0);
    });
    const pageScroller = document.scrollingElement || document.documentElement;
    const target = scrollables[0] && feedScore(scrollables[0]) >= 50 ? scrollables[0] : pageScroller;
    const before = target === document.scrollingElement || target === document.documentElement
      ? (window.scrollY || document.documentElement.scrollTop || 0)
      : target.scrollTop;
    if (target === document.scrollingElement || target === document.documentElement) {
      window.scrollBy(0, delta);
    } else {
      target.scrollBy(0, delta);
    }
    const after = target === document.scrollingElement || target === document.documentElement
      ? (window.scrollY || document.documentElement.scrollTop || 0)
      : target.scrollTop;
    return {
      before,
      after,
      moved: Math.abs(after - before),
      target: target === document.scrollingElement || target === document.documentElement ? 'window' : 'container',
      target_role: target.getAttribute?.('role') || '',
      target_label: target.getAttribute?.('aria-label') || '',
      target_score: feedScore(target),
      body_length: document.body?.innerText?.length || 0,
      scroll_height: target.scrollHeight || document.documentElement?.scrollHeight || document.body?.scrollHeight || 0,
    };
  })()`);
}

async function discover(page, kwargs) {
  const accountUrl = clean(kwargs['account-url']);
  if (!accountUrl) {
    throw new CommandExecutionError('Missing --account-url');
  }
  const maxSnapshots = intArg(kwargs['max-snapshots'], 32);
  const minSnapshots = Math.min(intArg(kwargs['min-snapshots'], 6), maxSnapshots);
  const stableSnapshots = intArg(kwargs['stable-snapshots'], 3);
  const scrollPixels = intArg(kwargs['scroll-pixels'], 520);
  const maxText = intArg(kwargs['max-text'], 1500);
  const timeWindow = discoveryTimeWindow(kwargs);
  const oldPostStopSnapshots = intArg(kwargs['old-post-stop-snapshots'], 2);
  await page.goto(accountUrl, { settleMs: 4000 });
  await scrollToTop(page);

  const seen = new Map();
  const snapshots = [];
  let stable = 0;
  let blockedExtraction = null;
  let stopReason = 'max_snapshots';
  let previousSeenCount = 0;
  let noMovementCount = 0;
  let previousScrollHeight = 0;
  let oldPostWindowCount = 0;
  for (let index = 0; index < maxSnapshots; index += 1) {
    const extraction = await readPage(page, browserExpression(maxText));
    if (extraction.capture_blocked) {
      blockedExtraction = extraction;
      stopReason = extraction.logged_out ? 'login_required' : 'visitor_preview';
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
    let oldWindowPosts = 0;
    let insideWindowPosts = 0;
    for (const candidate of extraction.candidates || []) {
      if (!validCandidate(candidate)) continue;
      const timeState = postTimeState(candidate, timeWindow);
      if (timeState === 'before') oldWindowPosts += 1;
      if (timeState === 'inside') insideWindowPosts += 1;
      const key = postKey(candidate);
      if (!key || seen.has(key)) continue;
      seen.set(key, candidate);
      newPosts += 1;
    }
    snapshots.push({
      index,
      body_length: extraction.body_length || 0,
      article_count: extraction.article_count || 0,
      raw_candidate_count: extraction.real_post_count || 0,
      new_posts: newPosts,
      seen_posts: seen.size,
      old_window_posts: oldWindowPosts,
      inside_window_posts: insideWindowPosts,
      time_window_enabled: timeWindow.enabled,
      visible_time_texts: (extraction.candidates || [])
        .flatMap((candidate) => candidate.time_texts || [candidate.post_time_text || ''])
        .filter(Boolean)
        .slice(0, 20),
    });
    stable = seen.size === previousSeenCount ? stable + 1 : 0;
    previousSeenCount = seen.size;
    oldPostWindowCount = timeWindow.enabled && oldWindowPosts > 0 && insideWindowPosts === 0
      ? oldPostWindowCount + 1
      : 0;
    if (oldPostWindowCount >= oldPostStopSnapshots) {
      stopReason = 'older_than_time_window';
      break;
    }
    if (snapshots.length >= minSnapshots && stable >= stableSnapshots && noMovementCount >= 1) {
      stopReason = 'stable_no_new_posts';
      break;
    }
    const scrollState = await scrollDown(page, scrollPixels);
    snapshots[snapshots.length - 1].scroll = scrollState;
    const scrollMoved = Number(scrollState?.moved || 0);
    const scrollHeight = Number(scrollState?.scroll_height || 0);
    noMovementCount = scrollMoved < 50 && scrollHeight <= previousScrollHeight ? noMovementCount + 1 : 0;
    previousScrollHeight = Math.max(previousScrollHeight, scrollHeight);
    await wait(page, 1.4);
  }
  if (blockedExtraction) {
    return {
      ok: false,
      status: blockedExtraction.logged_out ? 'login_required' : 'visitor_preview',
      action_required: 'human_intervention_required',
      human_intervention_required: true,
      blocked_reason: blockedExtraction.logged_out ? 'login_required' : 'visitor_preview',
      route: 'opencli_adapter',
      message: '当前 Chrome/Facebook session 没有完整登录态或只显示游客预览，已停止采集。',
      body_preview: blockedExtraction.body_preview || '',
      snapshots,
    };
  }
  if (!snapshots.length) {
    throw new EmptyResultError('facebook fb-competitor-posts', 'No page snapshot could be read.');
  }
  const posts = [...seen.values()];
  const coverage = captureCoverageState({ blockedExtraction, snapshots, stopReason, maxSnapshots });
  return {
    ok: posts.length > 0,
    status: posts.length > 0 ? 'real_posts_visible' : 'no_real_posts_visible',
    route: 'opencli_adapter',
    raw_candidate_count: Math.max(0, ...snapshots.map((item) => item.raw_candidate_count || 0)),
    post_count: posts.length,
    capture_complete: coverage.capture_complete,
    coverage: {
      snapshot_count: snapshots.length,
      stop_reason: stopReason,
      stable_snapshot_count: stable,
      old_post_window_snapshot_count: oldPostWindowCount,
      no_movement_snapshot_count: noMovementCount,
      coverage_blocked: coverage.coverage_blocked,
      coverage_incomplete: coverage.coverage_incomplete,
      capture_complete: coverage.capture_complete,
      message: coverage.coverage_incomplete
        ? '已达到最大滚动快照数但最后一屏仍有新增候选；可能还有更早帖子未覆盖，请提高 --max-snapshots 或继续从页面顶部重试。'
        : '',
    },
    snapshots,
    posts,
  };
}

async function extractExactTime(page, post, options) {
  if (options.skipTime) {
    return { posted_at_raw: '', posted_at: '', time_source: '', skipped: true };
  }
  const shouldRecheckExisting = hasConfirmedTime(post) && post.time_source === 'synthetic_hover_tooltip';
  if (hasConfirmedTime(post) && !shouldRecheckExisting) {
    return {
      posted_at_raw: post.posted_at_raw || '',
      posted_at: post.posted_at || '',
      time_source: post.time_source || '',
      preserved_existing: true,
    };
  }
  const target = await readPage(page, headerTimeTargetExpression(detailNavigationUrl(post)));
  let exact = await readPage(page, exactTimeFromTargetExpression(target));
  let hoverExact = null;
  if (options.allowRealMouseHover) {
    hoverExact = await readRealMouseTooltipTime(page, target, options.realMouseTooltipWaitMs);
    if (hoverExact?.posted_at) {
      exact = hoverExact;
    }
  }
  const embeddedExact = await readPage(page, embeddedPublishTimeExpression(detailNavigationUrl(post)));
  if (embeddedExact?.posted_at && (!exact?.posted_at || exact.time_source === 'synthetic_hover_tooltip')) {
    exact = embeddedExact;
  }
  if (!exact?.posted_at) {
    exact = await readPage(page, syntheticHoverTimeExpression(target, options.syntheticTooltipWaitMs));
  }
  if (!exact?.posted_at && hoverExact?.posted_at) {
    exact = hoverExact;
  }
  if (!exact?.posted_at && shouldRecheckExisting) {
    exact = {
      posted_at_raw: post.posted_at_raw || '',
      posted_at: post.posted_at || '',
      time_source: post.time_source || '',
      preserved_existing: true,
      rechecked_existing: true,
    };
  }
  return { ...(exact || {}), target };
}

async function readRealMouseTooltipTime(page, target, timeoutMs = 1800) {
  if (!target || target.index === undefined || target.index === null || typeof page.hover !== 'function') {
    return { posted_at_raw: '', posted_at: '', time_source: '', skipped: true };
  }
  try {
    await page.hover('a, abbr, span', { nth: Number(target.index) });
  } catch (error) {
    return { posted_at_raw: '', posted_at: '', time_source: '', error: String(error?.message || error) };
  }
  return readPage(page, realMouseTooltipTimeExpression(timeoutMs));
}

async function extractLeadLink(page, post, options) {
  if (options.skipLeadLink) {
    return { status: post.lead_link_status || 'skipped', skipped: true };
  }
  const existingLanding = cleanExternalUrl(post.landing_url || post.article_url || '');
  if (hasQualifiedLeadLink(post, options.allowedDomains) && existingLanding) {
    return {
      status: 'qualified',
      lead_url_raw: post.lead_url_raw,
      landing_url: existingLanding,
      lead_link_source: post.lead_link_source,
      owner_matched: true,
      comment_excerpt: post.comment_lead_excerpt || '',
      candidates: [],
      preserved_existing: true,
      resolution_source: 'existing_landing_url',
    };
  }
  const attempts = [];
  let fallbackSelected = null;
  for (const mode of ['default', 'all_comments', 'newest']) {
    await readPage(page, focusDetailConversationExpression()).catch(() => ({}));
    const modeResult = await readPage(page, commentModeBrowserExpression(mode)).catch((error) => ({ mode, error: String(error) }));
    await readPage(page, expandCommentsExpression(options.commentExpandRounds, options.replyExpandRounds)).catch(() => []);
    const candidates = await readPage(page, leadLinkScanBrowserExpression(post.account_name || '', mode));
    const selected = (candidates || []).find((item) => item.owner_matched) || (candidates || [])[0] || null;
    attempts.push({
      mode,
      mode_result: modeResult,
      candidate_count: (candidates || []).length,
      selected: selected
        ? {
            href: selected.href,
            source: selected.source,
            owner_matched: selected.owner_matched,
            block_text: selected.block_text,
          }
        : null,
    });
    if (!fallbackSelected && selected) fallbackSelected = selected;
    if (!selected) continue;
    const landingUrl = await resolveLandingUrl(selected.href, options.allowedDomains, options.resolveTimeoutMs);
    if (landingUrl) {
      return {
        status: 'qualified',
        lead_url_raw: selected.href,
        landing_url: landingUrl,
        lead_link_source: selected.source,
        owner_matched: selected.owner_matched,
        comment_excerpt: selected.block_text,
        candidates: candidates || [],
        attempts,
      };
    }
  }
  const ctaCandidates = await readPage(page, postCtaLeadLinkScanBrowserExpression(post.account_name || ''));
  const ctaSelected = (ctaCandidates || []).find((item) => item.owner_matched) || (ctaCandidates || [])[0] || null;
  attempts.push({
    mode: 'post_cta',
    candidate_count: (ctaCandidates || []).length,
    selected: ctaSelected
      ? {
          href: ctaSelected.href,
          source: ctaSelected.source,
          owner_matched: ctaSelected.owner_matched,
          cta_text: ctaSelected.cta_text,
          block_text: ctaSelected.block_text,
        }
      : null,
  });
  if (ctaSelected) {
    const landingUrl = await resolveLandingUrl(ctaSelected.href, options.allowedDomains, options.resolveTimeoutMs);
    if (landingUrl) {
      return {
        status: 'qualified',
        lead_url_raw: ctaSelected.href,
        landing_url: landingUrl,
        lead_link_source: 'post_cta',
        owner_matched: ctaSelected.owner_matched,
        comment_excerpt: ctaSelected.block_text,
        cta_text: ctaSelected.cta_text,
        candidates: ctaCandidates || [],
        attempts,
      };
    }
    if (!fallbackSelected) fallbackSelected = ctaSelected;
  }
  if (!fallbackSelected) return { status: 'missing', candidates: [], attempts };
  return {
    status: 'missing',
    lead_url_raw: fallbackSelected.href,
    landing_url: '',
    lead_link_source: fallbackSelected.source,
    owner_matched: fallbackSelected.owner_matched,
    comment_excerpt: fallbackSelected.block_text,
    candidates: [],
    attempts,
  };
}

async function enrichCurrentDetailPage(page, post, options) {
  const state = await readPage(page, pageStateExpression());
  if (state.loggedOut || state.visitorPreview) {
    return {
      ok: false,
      human_intervention_required: true,
      action_required: 'human_intervention_required',
      status: state.loggedOut ? 'login_required' : 'visitor_preview',
      blocked_reason: state.loggedOut ? 'login_required' : 'visitor_preview',
      body_preview: state.bodyPreview,
    };
  }
  const exactTime = await extractExactTime(page, post, options);
  const focus = await readPage(page, focusDetailConversationExpression()).catch(() => ({}));
  const target = exactTime.target || null;
  const engagement = options.skipEngagement
    ? { skipped: true, raw: '', confidence: 'skipped' }
    : await readPage(page, detailEngagementBrowserExpression(target));
  const postType = options.skipPostType
    ? { skipped: true, post_type: post.post_type || '' }
    : await readPage(page, detailPostTypeBrowserExpression());
  const leadLink = await extractLeadLink(page, post, options);

  const nextPost = { ...post };
  if (exactTime.posted_at) {
    nextPost.posted_at_raw = exactTime.posted_at_raw;
    nextPost.posted_at = exactTime.posted_at;
    nextPost.posted_date = dateKeyFromPostedAt(exactTime.posted_at) || nextPost.posted_date || '';
    nextPost.time_source = exactTime.time_source;
    nextPost.time_confirmed = true;
  }
  if (postType.post_type) {
    nextPost.post_type = postType.post_type;
    nextPost.post_type_source = postType.source || 'detail_main_post_dom';
  } else if (!options.skipPostType) {
    nextPost.note = appendSemicolonNote(nextPost.note, '帖子类型待补采：详情页未能判断图文/视频/仅图片/仅文字');
  }
  if (engagement.raw && ['anchored', 'anchored_incomplete_metrics', 'reel_action_buttons'].includes(engagement.confidence)) {
    nextPost.engagement_data = engagement.detail_engagement_data || engagement.raw;
    nextPost.detail_engagement_data = engagement.detail_engagement_data || engagement.raw;
    nextPost.engagement_raw = nextPost.engagement_data;
    nextPost.engagement_source = engagement.source;
    nextPost.engagement_confidence = engagement.confidence;
    if (engagement.likes !== null && engagement.likes !== undefined) nextPost.likes = engagement.likes;
    if (engagement.reactions !== null && engagement.reactions !== undefined) nextPost.reactions = engagement.reactions;
    if (engagement.comments !== null && engagement.comments !== undefined) nextPost.comments = engagement.comments;
    if (engagement.shares !== null && engagement.shares !== undefined) nextPost.shares = engagement.shares;
    if (engagement.views !== null && engagement.views !== undefined) nextPost.views = engagement.views;
  } else if (!options.skipEngagement) {
    nextPost.engagement_source = engagement.source || 'detail_main_post_dom';
    nextPost.engagement_confidence = engagement.confidence || 'unconfirmed';
    nextPost.note = appendSemicolonNote(nextPost.note, '互动数据待补采：详情页未能锚定当前主帖互动区');
  }
  if (!options.skipLeadLink) {
    if (leadLink.status === 'qualified' && shouldReplaceLeadLink(nextPost, leadLink, options.allowedDomains)) {
      nextPost.lead_url_raw = leadLink.lead_url_raw;
      nextPost.landing_url = leadLink.landing_url;
      nextPost.article_url = leadLink.landing_url;
      nextPost.lead_link_status = 'qualified';
      nextPost.lead_link_source = leadLink.lead_link_source;
      nextPost.comment_lead_excerpt = leadLink.comment_excerpt;
    } else if (!nextPost.lead_link_status) {
      nextPost.lead_link_status = 'missing';
    }
    if (leadLink.status !== 'qualified') {
      nextPost.note = appendSemicolonNote(nextPost.note, '评论区、评论回复或主帖CTA引流链接待确认');
    }
  }
  return { ok: true, post: nextPost, exact_time: exactTime, engagement, lead_link: leadLink, post_type: postType, focus };
}

async function detail(page, kwargs) {
  const inputPath = clean(kwargs.input);
  if (!inputPath) {
    throw new CommandExecutionError('Missing --input for detail mode');
  }
  const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  const posts = Array.isArray(input) ? input : Array.isArray(input.posts) ? input.posts : [];
  const options = {
    skipTime: boolArg(kwargs['skip-time']),
    skipLeadLink: boolArg(kwargs['skip-lead-link']),
    skipEngagement: boolArg(kwargs['skip-engagement']),
    skipPostType: boolArg(kwargs['skip-post-type']),
    allowedDomains: clean(kwargs['allowed-domains'])
      .split(',')
      .map((item) => item.trim().replace(/^www\./i, '').toLowerCase())
      .filter(Boolean),
    commentExpandRounds: intArg(kwargs['comment-expand-rounds'], 3),
    replyExpandRounds: intArg(kwargs['reply-expand-rounds'], 3),
    resolveTimeoutMs: intArg(kwargs['resolve-timeout-ms'], 20000),
    syntheticTooltipWaitMs: intArg(kwargs['synthetic-tooltip-wait-ms'], 1200),
    realMouseTooltipWaitMs: intArg(kwargs['real-mouse-tooltip-wait-ms'], 1800),
    allowRealMouseHover: boolArg(kwargs['allow-real-mouse-hover']),
  };
  const enriched = [];
  const details = [];
  for (const post of posts) {
    const url = detailNavigationUrl(post);
    if (!url) {
      enriched.push(post);
      continue;
    }
    const started = Date.now();
    await page.goto(url, { settleMs: 3000 });
    await wait(page, 1.5);
    const payload = await enrichCurrentDetailPage(page, post, options);
    if (payload?.human_intervention_required) {
      const result = {
        ok: false,
        human_intervention_required: true,
        action_required: 'human_intervention_required',
        status: payload.status || 'login_required',
        blocked_reason: payload.blocked_reason || 'login_required',
        reason: payload.blocked_reason || 'login_required',
        body_preview: payload.body_preview || '',
        posts: enriched,
      };
      const outputPath = clean(kwargs.output);
      if (outputPath) fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
      return result;
    }
    enriched.push(payload.post || post);
    details.push({
      post_url: url,
      duration_ms: Date.now() - started,
      exact_time: payload.exact_time,
      focus: payload.focus,
      engagement: payload.engagement,
      lead_link: payload.lead_link,
      post_type: payload.post_type,
    });
  }
  const result = { ok: true, posts: enriched, post_count: enriched.length, details };
  const outputPath = clean(kwargs.output);
  if (outputPath) fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
  return result;
}

cli({
  site: 'facebook',
  name: 'fb-competitor-posts',
  description: 'Collect visible Facebook competitor posts for the fb-competitor-collector project',
  access: 'read',
  example: 'opencli facebook fb-competitor-posts --mode discover --account-url <url> -f json',
  domain: 'www.facebook.com',
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false,
  defaultFormat: 'json',
  args: [
    { name: 'mode', default: 'discover', choices: ['discover', 'detail'], help: 'Run homepage discovery or detail enrichment' },
    { name: 'account-url', help: 'Facebook account/page URL for discovery mode' },
    { name: 'input', help: 'JSON input file for detail mode' },
    { name: 'output', help: 'Optional JSON output file for detail mode' },
    { name: 'target-date', help: 'Target date key carried through for project jobs' },
    { name: 'max-text', type: 'int', default: 1500, help: 'Maximum raw text captured per post' },
    { name: 'max-snapshots', type: 'int', default: 32, help: 'Maximum homepage snapshots' },
    { name: 'min-snapshots', type: 'int', default: 6, help: 'Minimum homepage snapshots before stable stop' },
    { name: 'stable-snapshots', type: 'int', default: 3, help: 'Stable snapshot count before stopping discovery' },
    { name: 'scroll-pixels', type: 'int', default: 520, help: 'Pixels to scroll between discovery snapshots' },
    { name: 'posted-after', help: 'Discovery time-window lower bound, YYYY-MM-DD HH:MM' },
    { name: 'posted-before', help: 'Discovery time-window upper bound, YYYY-MM-DD HH:MM' },
    { name: 'old-post-stop-snapshots', type: 'int', default: 2, help: 'Stop discovery after this many snapshots are older than the requested time window' },
    { name: 'skip-time', type: 'bool', default: false, help: 'Skip exact time enrichment' },
    { name: 'skip-lead-link', type: 'bool', default: false, help: 'Skip lead link enrichment' },
    { name: 'skip-engagement', type: 'bool', default: false, help: 'Skip engagement enrichment' },
    { name: 'skip-post-type', type: 'bool', default: false, help: 'Skip post type enrichment' },
    { name: 'allowed-domains', help: 'Comma-separated allowed landing domains' },
    { name: 'comment-expand-rounds', type: 'int', default: 3, help: 'Comment expansion rounds' },
    { name: 'reply-expand-rounds', type: 'int', default: 3, help: 'Reply expansion rounds' },
    { name: 'resolve-timeout-ms', type: 'int', default: 20000, help: 'Landing URL redirect timeout in milliseconds' },
    { name: 'synthetic-tooltip-wait-ms', type: 'int', default: 1200, help: 'Synthetic hover tooltip wait in milliseconds' },
    { name: 'real-mouse-tooltip-wait-ms', type: 'int', default: 1800, help: 'Real mouse hover tooltip wait in milliseconds' },
    { name: 'allow-real-mouse-hover', type: 'bool', default: false, help: 'Use OpenCLI page.hover when synthetic tooltip extraction fails' },
  ],
  columns: ['ok', 'mode', 'post_count', 'status'],
  func: async (page, kwargs) => {
    const mode = clean(kwargs.mode || 'discover');
    if (mode === 'detail') return detail(page, kwargs);
    return discover(page, kwargs);
  },
});
