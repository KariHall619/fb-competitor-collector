/**
 * Shared Facebook detail-page extraction helpers.
 *
 * These helpers only build DOM expressions evaluated inside OpenCLI's official
 * browser adapter. They do not start OpenCLI, choose tabs, or manage sessions.
 */

const { browserExactTimeHelpersExpression } = require("./fb_time_extractors.js");

function pageStateExpression() {
  return `(() => {
    const body = document.body?.innerText || "";
    return {
      loggedOut: /Log in to Facebook|登录 Facebook|Forgot Account|Forgot password|Create new account|邮箱或手机号\\s+密码\\s+登录/i.test(body),
      visitorPreview: /(登录|Log in)\\s+(忘记账户了？|Forgot Account|Forgot password|Forgotten password)/i.test(body),
      bodyPreview: body.slice(0, 1200),
    };
  })()`;
}

function headerTimeTargetExpression() {
  return `(() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const helpers = ${browserExactTimeHelpersExpression()};
    const viewportHeight = window.innerHeight || 800;
    const candidates = [...document.querySelectorAll("a, abbr, span")].map((el, index) => {
      const rect = el.getBoundingClientRect();
      return {
        index,
        tag: el.tagName,
        text: clean(el.innerText || el.textContent || ""),
        aria: clean(el.getAttribute("aria-label") || ""),
        title: clean(el.getAttribute("title") || ""),
        datetime: clean(el.getAttribute("datetime") || ""),
        tooltipContent: clean(el.getAttribute("data-tooltip-content") || ""),
        tooltipText: clean(el.getAttribute("data-tooltip-text") || ""),
        href: el.href || "",
        x: rect.x,
        y: rect.y,
        w: rect.width,
        h: rect.height,
      };
    }).filter((item) => helpers.isLikelyHeaderTimeElement(item, viewportHeight));
    candidates.sort((a, b) => a.y - b.y || a.x - b.x);
    return candidates[0] || null;
  })()`;
}

function exactTimeFromTargetExpression(target) {
  return `(() => {
    const helpers = ${browserExactTimeHelpersExpression()};
    return helpers.exactTimeFromItem(${JSON.stringify(target || null)});
  })()`;
}

function syntheticHoverTimeExpression(target, timeoutMs = 1200) {
  return `(async () => {
    const helpers = ${browserExactTimeHelpersExpression()};
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const timeoutMs = Math.max(300, Number(${JSON.stringify(timeoutMs)}) || 1200);
    const target = ${JSON.stringify(target || null)};
    if (!target) return { posted_at_raw: "", posted_at: "", time_source: "" };
    const elements = [...document.querySelectorAll("a, abbr, span")];
    const el = elements[target.index];
    if (!el) return { posted_at_raw: "", posted_at: "", time_source: "" };
    const rect = el.getBoundingClientRect();
    const eventInit = {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: Math.floor(rect.x + rect.width / 2),
      clientY: Math.floor(rect.y + rect.height / 2),
      view: window,
    };
    for (const eventName of ["pointerover", "mouseover", "mouseenter", "focus"]) {
      if (eventName === "focus" && typeof el.focus === "function") {
        el.focus();
        continue;
      }
      const EventCtor = eventName.startsWith("pointer") && typeof PointerEvent === "function"
        ? PointerEvent
        : typeof MouseEvent === "function"
          ? MouseEvent
          : null;
      if (!EventCtor) continue;
      el.dispatchEvent(new EventCtor(eventName, eventInit));
    }
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const texts = [...document.querySelectorAll('[role="tooltip"], div, span')]
        .map((node) => helpers.clean(node.innerText || node.textContent || ""))
        .filter(Boolean);
      for (const text of texts) {
        const parsed = helpers.parseExactFacebookTime(text);
        if (parsed) return { posted_at_raw: text, posted_at: parsed, time_source: "synthetic_hover_tooltip" };
      }
      await sleep(100);
    }
    return { posted_at_raw: "", posted_at: "", time_source: "" };
  })()`;
}

function detailEngagementBrowserExpression(target) {
  return `(() => {
    const target = ${JSON.stringify(target || null)};
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const linesFrom = (value) => String(value || "").split(/\\n+/).map(clean).filter(Boolean);
    const countToken = "(\\\\d+(?:[.,]\\\\d+)?\\\\s*(?:K|k|M|m|万)?)";
    const parseCount = (value) => {
      const text = clean(value).replace(/,/g, "");
      const match = text.match(/(\\d+(?:\\.\\d+)?)\\s*(K|k|M|m|万)?/);
      if (!match) return null;
      let number = Number(match[1]);
      const unit = match[2] || "";
      if (/^k$/i.test(unit)) number *= 1000;
      if (/^m$/i.test(unit)) number *= 1000000;
      if (unit === "万") number *= 10000;
      return Number.isFinite(number) ? Math.round(number) : null;
    };
    const forbiddenChrome = (node) => Boolean(node?.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header'));
    const looksLikeAdOrShell = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\\s*·\\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const hasActionOrMetric = (text) => /\\bLike\\b|\\bComment\\b|\\bShare\\b|赞|评论|分享|All reactions|reactions?|likes?|comments?|shares?|views?|plays?|次播放/i.test(text);
    const scoreRoot = (node) => {
      const text = clean(node?.innerText || node?.textContent || "");
      if (!text || forbiddenChrome(node) || looksLikeAdOrShell(text)) return -1000;
      let score = 0;
      if (node.getAttribute?.("role") === "article" || /^(ARTICLE)$/i.test(node.tagName || "")) score += 30;
      if (hasActionOrMetric(text)) score += 20;
      if (/All reactions|comments?|shares?|评论|分享|赞/i.test(text)) score += 15;
      if (text.length >= 80) score += 5;
      if (text.length > 6500) score -= 40;
      return score;
    };
    const targetElement = target && Number.isInteger(target.index)
      ? [...document.querySelectorAll("a, abbr, span")][target.index]
      : null;
    const roots = [];
    const pushRoot = (node) => {
      if (node && !roots.includes(node)) roots.push(node);
    };
    pushRoot(targetElement?.closest?.('[role="article"], article'));
    let cursor = targetElement;
    for (let depth = 0; cursor && depth < 10; depth += 1) {
      pushRoot(cursor);
      cursor = cursor.parentElement;
    }
    for (const node of document.querySelectorAll('[role="article"], article')) pushRoot(node);
    const scored = roots
      .map((node) => ({ node, score: scoreRoot(node), text: clean(node?.innerText || node?.textContent || "") }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || a.text.length - b.text.length);
    const root = scored[0]?.node || null;
    if (!root) {
      return {
        raw: "",
        source: "detail_main_post_dom",
        confidence: "unanchored",
        warnings: ["main_post_root_not_found"],
      };
    }

    const result = {
      raw: "",
      detail_engagement_data: "",
      source: "detail_main_post_dom",
      confidence: "anchored",
      reactions: null,
      likes: null,
      comments: null,
      shares: null,
      views: null,
      root_text_preview: clean(root.innerText || root.textContent || "").slice(0, 600),
      warnings: [],
    };
    const setMetric = (key, value, rawText) => {
      const parsed = parseCount(value);
      if (parsed === null || parsed === undefined) return;
      if (result[key] === null || result[key] === undefined) result[key] = parsed;
      if (key === "reactions" && (result.likes === null || result.likes === undefined)) result.likes = parsed;
      if (rawText) result.raw = result.raw || clean(rawText);
    };
    const readMetricText = (text) => {
      const item = clean(text);
      if (!item || item.length > 180) return;
      const patterns = [
        ["views", new RegExp(countToken + "\\\\s*(?:views?|plays?|次播放|播放|浏览)", "i")],
        ["comments", new RegExp(countToken + "\\\\s*(?:comments?|评论)", "i")],
        ["shares", new RegExp(countToken + "\\\\s*(?:shares?|分享)", "i")],
        ["reactions", new RegExp("(?:All reactions|reactions?|likes?|赞)[^0-9]{0,20}" + countToken, "i")],
        ["reactions", new RegExp(countToken + "\\\\s*(?:reactions?|likes?|赞)", "i")],
      ];
      for (const [key, pattern] of patterns) {
        const match = item.match(pattern);
        if (match) setMetric(key, match[1], item);
      }
    };
    const metricNodes = [...root.querySelectorAll('a, span, div, [aria-label], [title]')];
    for (const node of metricNodes) {
      if (node === root) continue;
      const ownerArticle = node.closest?.('[role="article"], article');
      if (ownerArticle && ownerArticle !== root) continue;
      for (const text of [
        node.getAttribute?.("aria-label") || "",
        node.getAttribute?.("title") || "",
        node.innerText || node.textContent || "",
      ]) {
        readMetricText(text);
      }
    }

    const lines = linesFrom(root.innerText || root.textContent || "");
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      readMetricText(line);
      const slashCluster = line.match(new RegExp("^" + countToken + "\\\\s*[/|]\\\\s*" + countToken + "\\\\s*[/|]\\\\s*" + countToken + "$", "i"));
      if (slashCluster) {
        setMetric("reactions", slashCluster[1], line);
        setMetric("comments", slashCluster[2], line);
        setMetric("shares", slashCluster[3], line);
      }
      const triple = lines.slice(index, index + 3);
      const nextText = lines.slice(index + 3, index + 8).join(" ");
      if (
        triple.length === 3
        && triple.every((item) => new RegExp("^" + countToken + "$", "i").test(item))
        && /\\bLike\\b|\\bComment\\b|\\bShare\\b|赞|评论|分享/i.test(nextText)
      ) {
        setMetric("reactions", triple[0], triple.join("；"));
        setMetric("comments", triple[1], triple.join("；"));
        setMetric("shares", triple[2], triple.join("；"));
      }
    }

    const parts = [];
    if (result.views !== null && result.views !== undefined) parts.push("浏览量：" + result.views);
    if (result.likes !== null && result.likes !== undefined) parts.push("点赞量：" + result.likes);
    if (result.comments !== null && result.comments !== undefined) parts.push("评论数：" + result.comments);
    if (result.shares !== null && result.shares !== undefined) parts.push("分享数：" + result.shares);
    result.detail_engagement_data = parts.join("；");
    result.raw = result.detail_engagement_data || result.raw;
    const missing = [];
    if (result.likes === null || result.likes === undefined) missing.push("likes");
    if (result.comments === null || result.comments === undefined) missing.push("comments");
    if (result.shares === null || result.shares === undefined) missing.push("shares");
    if (!result.raw) {
      result.confidence = "anchored_missing_metrics";
      result.warnings.push("main_post_metrics_not_found");
    } else if (missing.length) {
      result.confidence = "anchored_incomplete_metrics";
      result.warnings.push("missing_" + missing.join("_"));
    }
    return result;
  })()`;
}

function detailPostTypeBrowserExpression() {
  return `(() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const forbiddenChrome = (node) => Boolean(node?.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header'));
    const looksLikeAdOrShell = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\\s*·\\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const roots = [...document.querySelectorAll('[role="article"], article')]
      .map((node) => ({ node, text: clean(node.innerText || node.textContent || "") }))
      .filter((item) => item.text && !forbiddenChrome(item.node) && !looksLikeAdOrShell(item.text))
      .sort((a, b) => b.text.length - a.text.length);
    const root = roots[0]?.node || document.body;
    const text = clean(root.innerText || root.textContent || "");
    const hrefs = [...root.querySelectorAll("a[href]")].map((a) => {
      try { return new URL(a.getAttribute("href"), location.href).href; } catch { return ""; }
    }).filter(Boolean);
    const hasVideo = hrefs.some((href) => /\\/reel\\/|\\/watch\\/|\\/videos?\\/|[?&]v=|fb\\.watch/i.test(href))
      || Boolean(root.querySelector('video, [aria-label*="Reel" i], [aria-label*="Video" i], [aria-label*="Watch" i]'));
    const hasImage = Boolean(root.querySelector('img[src], [style*="background-image"]'))
      || hrefs.some((href) => /photo\\.php|\\/photo\\/|[?&]fbid=/i.test(href));
    const textWithoutUi = text
      .replace(/\\b(Like|Comment|Share|Reply|Follow|See more|All reactions)\\b/gi, " ")
      .replace(/\\d+(?:[.,]\\d+)?\\s*(?:K|M|万)?\\s*(?:likes?|comments?|shares?|views?|plays?)/gi, " ")
      .replace(/\\s+/g, " ")
      .trim();
    const hasBodyText = textWithoutUi.length >= 24 || hrefs.some((href) => !/facebook\\.com|fb\\.watch|meta\\.com/i.test(href));
    let post_type = "";
    if (hasVideo) post_type = "视频";
    else if (hasImage && hasBodyText) post_type = "图文";
    else if (hasImage) post_type = "仅图片";
    else if (hasBodyText) post_type = "仅文字";
    return {
      post_type,
      source: "detail_main_post_dom",
      has_video: hasVideo,
      has_image: hasImage,
      has_body_text: hasBodyText,
      root_text_preview: text.slice(0, 400),
    };
  })()`;
}

function expandCommentsExpression(commentRounds = 3, replyRounds = 3) {
  return `(async () => {
    const maxRounds = Math.max(${Number(commentRounds) || 3}, ${Number(replyRounds) || 3});
    const labels = [
      /view more comments/i,
      /see more comments/i,
      /view previous comments/i,
      /more comments/i,
      /view replies/i,
      /see replies/i,
      /\\d+\\s+repl(?:y|ies)/i,
      /view\\s+\\d+\\s+repl(?:y|ies)/i,
      /see\\s+\\d+\\s+repl(?:y|ies)/i,
      /查看更多评论/,
      /查看更多回复/,
      /查看回复/,
    ];
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const visible = (el) => {
      const rect = el?.getBoundingClientRect?.();
      return Boolean(rect && rect.width > 240 && rect.height > 80 && rect.bottom > 80 && rect.top < window.innerHeight - 40);
    };
    const textLength = (el) => el?.innerText?.length || el?.textContent?.length || 0;
    const findConversationRoot = () => {
      const articles = [...document.querySelectorAll('[role="article"], article')]
        .filter(visible)
        .filter((el) => /Like|Comment|Share|赞|评论|分享|Reply|回复|All reactions|comments?|shares?/i.test(el.innerText || el.textContent || ""));
      const article = articles.sort((a, b) => textLength(b) - textLength(a))[0] || null;
      if (!article) return document.scrollingElement || document.documentElement;
      let current = article;
      let best = article;
      for (let depth = 0; current && depth < 8; depth += 1) {
        const style = getComputedStyle(current);
        const overflow = [style.overflowY, style.overflow].join(" ");
        if (current.scrollHeight > current.clientHeight + 120 && /(auto|scroll)/i.test(overflow)) {
          best = current;
          break;
        }
        current = current.parentElement;
      }
      return best || article;
    };
    const scrollInside = (root, amount = 420) => {
      const target = root || document.scrollingElement || document.documentElement;
      if (target === document.scrollingElement || target === document.documentElement || target === document.body) {
        window.scrollBy(0, amount);
      } else if (target.scrollHeight > target.clientHeight + 20) {
        target.scrollBy(0, amount);
      } else {
        target.scrollIntoView?.({ block: "center", inline: "nearest" });
      }
    };
    const summary = [];
    const root = findConversationRoot();
    root?.scrollIntoView?.({ block: "center", inline: "nearest" });
    await sleep(250);
    for (let round = 0; round < maxRounds; round += 1) {
      const bodyLengthBefore = root?.innerText?.length || document.body?.innerText?.length || 0;
      let clicked = 0;
      const clickScope = root && root.querySelectorAll ? root : document;
      for (const el of clickScope.querySelectorAll('div[role="button"], span, a')) {
        const text = clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
        if (!text || !labels.some((re) => re.test(text))) continue;
        try {
          el.scrollIntoView?.({ block: "center", inline: "nearest" });
          await sleep(80);
          el.click();
          clicked += 1;
        } catch {
          // Ignore click failures on virtualized comment controls.
        }
      }
      if (!clicked) break;
      scrollInside(root, 360);
      const started = Date.now();
      let changed = false;
      while (Date.now() - started < 900) {
        await sleep(100);
        const bodyLengthAfter = root?.innerText?.length || document.body?.innerText?.length || 0;
        if (bodyLengthAfter > bodyLengthBefore + 20) {
          changed = true;
          break;
        }
      }
      summary.push({
        round,
        clicked,
        body_length_changed: changed,
        root_tag: root?.tagName || "",
        root_role: root?.getAttribute?.("role") || "",
      });
    }
    return summary;
  })()`;
}

function focusDetailConversationExpression() {
  return `(() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const visible = (el) => {
      const rect = el?.getBoundingClientRect?.();
      return Boolean(rect && rect.width > 260 && rect.height > 80 && rect.bottom > 80 && rect.top < window.innerHeight - 40);
    };
    const textLength = (el) => el?.innerText?.length || el?.textContent?.length || 0;
    const scoreArticle = (el) => {
      const text = clean(el.innerText || el.textContent || "");
      let score = Math.min(text.length, 6000) / 100;
      if (/Like|Comment|Share|赞|评论|分享|All reactions|comments?|shares?/i.test(text)) score += 80;
      if (/Write a comment|View more comments|All comments|Most relevant|回复|查看更多评论|所有评论/i.test(text)) score += 60;
      if (/Sponsored|Suggested for you|Create a post|What's on your mind/i.test(text)) score -= 120;
      return score;
    };
    const articles = [...document.querySelectorAll('[role="article"], article')]
      .filter(visible)
      .sort((a, b) => scoreArticle(b) - scoreArticle(a));
    const article = articles[0] || null;
    let root = article;
    let current = article;
    for (let depth = 0; current && depth < 8; depth += 1) {
      const style = getComputedStyle(current);
      const overflow = [style.overflowY, style.overflow].join(" ");
      if (current.scrollHeight > current.clientHeight + 120 && /(auto|scroll)/i.test(overflow)) {
        root = current;
        break;
      }
      current = current.parentElement;
    }
    const target = root || article || document.scrollingElement || document.documentElement;
    target?.scrollIntoView?.({ block: "center", inline: "nearest" });
    if (target && target !== document.scrollingElement && target !== document.documentElement && target.scrollHeight > target.clientHeight + 20) {
      target.scrollBy(0, 220);
    } else {
      window.scrollBy(0, 220);
    }
    return {
      ok: Boolean(article),
      target: target === document.scrollingElement || target === document.documentElement ? "window" : "container",
      article_text_length: textLength(article),
      root_text_length: textLength(root),
      root_tag: root?.tagName || "",
      root_role: root?.getAttribute?.("role") || "",
      root_label: root?.getAttribute?.("aria-label") || "",
    };
  })()`;
}

function commentModeBrowserExpression(mode) {
  return `(async () => {
    const mode = ${JSON.stringify(mode)};
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const modeLabels = {
      all_comments: [/all comments/i, /所有评论/, /全部评论/],
      newest: [/newest/i, /most recent/i, /最新评论/, /最新/],
      most_relevant: [/most relevant/i, /top comments/i, /最相关/, /热门评论/],
    };
    const sortControlLabels = [
      /most relevant/i,
      /top comments/i,
      /all comments/i,
      /newest/i,
      /comment ranking/i,
      /最相关/,
      /热门评论/,
      /所有评论/,
      /全部评论/,
      /最新评论/,
      /评论排序/,
    ];
    const clickable = () => [...document.querySelectorAll('div[role="button"], span, a, [aria-label]')];
    const labelFor = (el) => clean(el.innerText || el.textContent || el.getAttribute("aria-label") || "");
    const clickMatching = (patterns) => {
      const el = clickable().find((item) => {
        const text = labelFor(item);
        return text && patterns.some((pattern) => pattern.test(text));
      });
      if (!el) return "";
      try {
        el.click();
        return labelFor(el);
      } catch {
        return "";
      }
    };
    if (mode === "default") return { mode, clicked: false };
    const opened = clickMatching(sortControlLabels);
    if (opened) await sleep(500);
    const selected = clickMatching(modeLabels[mode] || []);
    if (selected) await sleep(700);
    return { mode, clicked: Boolean(selected), opened };
  })()`;
}

function leadLinkScanBrowserExpression(accountName = "", mode = "default") {
  return `((expectedAccountName, commentMode) => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const linesFrom = (value) => String(value || "").split(/\\n+/).map(clean).filter(Boolean);
    const isExternalHref = (href) => {
      try {
        const parsed = new URL(href, location.href);
        const host = parsed.hostname.replace(/^www\\./i, "").toLowerCase();
        if (!/^https?:$/i.test(parsed.protocol)) return false;
        if (/l\\.facebook\\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) return true;
        return host !== "facebook.com"
          && !host.endsWith(".facebook.com")
          && host !== "fb.watch"
          && host !== "meta.com"
          && !host.endsWith(".meta.com");
      } catch {
        return false;
      }
    };
    const normalizeCandidateHref = (href) => {
      try {
        const parsed = new URL(href, location.href);
        if (/l\\.facebook\\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) {
          return new URL(parsed.searchParams.get("u"), location.href).href;
        }
        return parsed.href;
      } catch {
        return "";
      }
    };
    const plainTextLinks = (text) => {
      const found = [];
      const pattern = /(?:https?:\\/\\/|www\\.)[^\\s<>"'，。；、)）\\]]+/gi;
      let match = null;
      while ((match = pattern.exec(text))) {
        const raw = match[0].replace(/[.,;!?]+$/g, "");
        const href = raw.startsWith("http") ? raw : "https://" + raw;
        if (isExternalHref(href)) {
          found.push({ href: normalizeCandidateHref(href), text: raw, source_kind: "plain_text" });
        }
      }
      return found;
    };
    const ownerName = clean(expectedAccountName);
    const ownerNameLower = ownerName.toLowerCase();
    const commentTimeLine = (line) => /^(just now|\\d+\\s*(m|min|h|hr|d|day|w|wk)|刚刚|\\d+\\s*分钟|\\d+\\s*小时|\\d+\\s*天)$/i.test(line);
    const forbiddenChrome = (node) => {
      const shell = node.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header');
      return Boolean(shell);
    };
    const looksLikePageShellOrAd = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\\s*·\\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const ownerMatchedNearTop = (lines) => {
      if (!ownerName) return true;
      return lines.slice(0, 18).some((line) => {
        const lower = line.toLowerCase();
        return lower === ownerNameLower
          || lower.startsWith(ownerNameLower + " replied")
          || lower.includes(ownerNameLower + " replied")
          || lower.startsWith(ownerNameLower + " responded")
          || lower.includes(ownerNameLower + " responded");
      });
    };
    const looksCommentContext = (lines, links) => {
      const shortText = lines.slice(0, 30).join(" ");
      const hasCommentPermalink = links.some((link) => /[?&]comment_id=|comment_id%3D/i.test(link.href));
      return hasCommentPermalink
        || /\\bReply\\b|replied|responded|回复/.test(shortText)
        || lines.some(commentTimeLine);
    };
    const blocks = [...document.querySelectorAll('[role="article"], div[aria-label], li, div')];
    const results = [];
    for (const block of blocks) {
      if (forbiddenChrome(block)) continue;
      const rawText = block.innerText || block.textContent || "";
      const text = clean(rawText);
      if (!text || text.length > 3000 || looksLikePageShellOrAd(text)) continue;
      const lines = linesFrom(rawText);
      const anchorLinks = [...block.querySelectorAll("a[href]")]
        .map((a) => ({
          href: normalizeCandidateHref(a.getAttribute("href")),
          text: clean(a.innerText || a.textContent || a.getAttribute("aria-label") || ""),
          source_kind: "anchor",
        }))
        .filter((link) => isExternalHref(link.href));
      const links = [...anchorLinks, ...plainTextLinks(rawText)]
        .filter((link, index, items) => link.href && items.findIndex((item) => item.href === link.href) === index);
      if (!links.length) continue;
      const commentContext = looksCommentContext(lines, links);
      if (!commentContext) continue;
      const ownerMatched = ownerMatchedNearTop(lines);
      if (!ownerMatched) continue;
      const looksReply = /reply|replied|responded|回复/i.test(lines.slice(0, 30).join(" "));
      results.push({
        href: links[0].href,
        text: links[0].text,
        block_text: text.slice(0, 800),
        source: looksReply ? "comment_reply" : "comment",
        owner_matched: ownerMatched,
        comment_context: commentContext,
        comment_mode: commentMode,
      });
    }
    results.sort((a, b) => Number(b.owner_matched) - Number(a.owner_matched));
    return results.slice(0, 20);
  })(${JSON.stringify(accountName)}, ${JSON.stringify(mode)})`;
}

function postCtaLeadLinkScanBrowserExpression(accountName = "") {
  return `((expectedAccountName) => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const linesFrom = (value) => String(value || "").split(/\\n+/).map(clean).filter(Boolean);
    const ctaPattern = /\\b(watch more|watch now|learn more|read more|shop now|sign up|subscribe|get offer|apply now|book now|download)\\b|观看更多|继续观看|了解更多|阅读更多|查看完整|阅读全文|完整内容/i;
    const isExternalHref = (href) => {
      try {
        const parsed = new URL(href, location.href);
        const host = parsed.hostname.replace(/^www\\./i, "").toLowerCase();
        if (!/^https?:$/i.test(parsed.protocol)) return false;
        if (/l\\.facebook\\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) return true;
        return host !== "facebook.com"
          && !host.endsWith(".facebook.com")
          && host !== "fb.watch"
          && host !== "meta.com"
          && !host.endsWith(".meta.com");
      } catch {
        return false;
      }
    };
    const normalizeCandidateHref = (href) => {
      try {
        const parsed = new URL(href, location.href);
        if (/l\\.facebook\\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) {
          return new URL(parsed.searchParams.get("u"), location.href).href;
        }
        return parsed.href;
      } catch {
        return "";
      }
    };
    const ownerName = clean(expectedAccountName);
    const ownerNameLower = ownerName.toLowerCase();
    const forbiddenChrome = (node) => {
      const shell = node.closest?.('[role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"], nav, aside, footer, header');
      return Boolean(shell);
    };
    const looksLikePageShellOrAd = (text) => /Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\\s*·\\s*Terms|Ads Manager|Harness the Power of AI|Feed posts/i.test(text);
    const ownerMatchedNearTop = (lines) => {
      if (!ownerName) return true;
      return lines.slice(0, 18).some((line) => line.toLowerCase() === ownerNameLower);
    };
    const outerArticle = (node) => {
      let current = node;
      let found = null;
      while (current) {
        if (current.matches?.('[role="article"], article')) found = current;
        current = current.parentElement;
      }
      return found || node;
    };
    const rawRoots = [...document.querySelectorAll('[role="article"], article')]
      .map(outerArticle)
      .filter((node, index, items) => node && items.indexOf(node) === index);
    const results = [];
    for (const root of rawRoots) {
      if (forbiddenChrome(root)) continue;
      const rawText = root.innerText || root.textContent || "";
      const text = clean(rawText);
      if (!text || text.length > 12000 || !ctaPattern.test(text) || looksLikePageShellOrAd(text)) continue;
      const lines = linesFrom(rawText);
      const ownerMatched = ownerMatchedNearTop(lines);
      if (!ownerMatched) continue;
      const links = [...root.querySelectorAll("a[href]")]
        .map((a) => {
          const rect = a.getBoundingClientRect?.() || { x: 0, y: 0 };
          return {
            href: normalizeCandidateHref(a.getAttribute("href")),
            text: clean(a.innerText || a.textContent || ""),
            aria: clean(a.getAttribute("aria-label") || ""),
            title: clean(a.getAttribute("title") || ""),
            x: rect.x || 0,
            y: rect.y || 0,
          };
        })
        .filter((link) => link.href && isExternalHref(link.href))
        .filter((link, index, items) => items.findIndex((item) => item.href === link.href) === index);
      if (!links.length) continue;
      const ctaLinks = links.filter((link) => ctaPattern.test([link.text, link.aria, link.title].join(" ")));
      const selected = ctaLinks[0] || links[0];
      const ctaLine = lines.find((line) => ctaPattern.test(line)) || selected.text || selected.aria || selected.title || "post_cta";
      results.push({
        href: selected.href,
        text: selected.text || selected.aria || selected.title || ctaLine,
        block_text: text.slice(0, 900),
        source: "post_cta",
        owner_matched: ownerMatched,
        cta_text: ctaLine,
        cta_link_text_matched: ctaLinks.length > 0,
      });
    }
    results.sort((a, b) => Number(b.cta_link_text_matched) - Number(a.cta_link_text_matched));
    return results.slice(0, 10);
  })(${JSON.stringify(accountName)})`;
}

function cleanExternalUrl(href) {
  if (!href) return "";
  try {
    const parsed = new URL(href);
    if (/l\.facebook\.com$/i.test(parsed.hostname) && parsed.searchParams.get("u")) {
      return cleanExternalUrl(parsed.searchParams.get("u"));
    }
    const host = parsed.hostname.replace(/^www\./i, "").toLowerCase();
    if (!/^https?:$/i.test(parsed.protocol)) return "";
    if (host === "facebook.com" || host.endsWith(".facebook.com") || host === "fb.watch" || host === "meta.com" || host.endsWith(".meta.com")) {
      return "";
    }
    for (const key of [...parsed.searchParams.keys()]) {
      if (key === "fbclid" || key.startsWith("utm_") || key.startsWith("__")) parsed.searchParams.delete(key);
    }
    parsed.hash = "";
    return parsed.href;
  } catch {
    return "";
  }
}

function allowedLandingUrl(href, allowedDomains = []) {
  if (!href) return false;
  if (!allowedDomains.length) return true;
  try {
    const host = new URL(href).hostname.replace(/^www\./i, "").toLowerCase();
    return allowedDomains.some((domain) => host === domain || host.endsWith(`.${domain}`));
  } catch {
    return false;
  }
}

function sameNormalizedUrl(left, right) {
  const cleanLeft = cleanExternalUrl(left);
  const cleanRight = cleanExternalUrl(right);
  return Boolean(cleanLeft && cleanRight && cleanLeft === cleanRight);
}

module.exports = {
  allowedLandingUrl,
  cleanExternalUrl,
  commentModeBrowserExpression,
  detailEngagementBrowserExpression,
  detailPostTypeBrowserExpression,
  exactTimeFromTargetExpression,
  expandCommentsExpression,
  focusDetailConversationExpression,
  headerTimeTargetExpression,
  leadLinkScanBrowserExpression,
  pageStateExpression,
  postCtaLeadLinkScanBrowserExpression,
  sameNormalizedUrl,
  syntheticHoverTimeExpression,
};
