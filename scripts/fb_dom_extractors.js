/**
 * Shared Facebook DOM extraction helpers.
 */

const { browserExactTimeHelpersExpression } = require("./fb_time_extractors.js");

function browserExpression(maxText = 1200) {
  return `(() => {
    const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
    const exactTimeHelpers = ${browserExactTimeHelpersExpression()};
    const fullText = (node) => (node?.innerText || '').replace(/\\u00a0/g, ' ').trim();
    const sourceSurface = /(^|\\.)mbasic\\.facebook\\.com$/i.test(location.hostname)
      ? 'mbasic'
      : /(^|\\.)m\\.facebook\\.com$/i.test(location.hostname)
        ? 'mobile'
        : 'desktop';
    const isFacebookHost = (href) => {
      try {
        const parsed = new URL(href, location.href);
        return /(^|\\.)facebook\\.com$/i.test(parsed.hostname) || /(^|\\.)fb\\.watch$/i.test(parsed.hostname);
      } catch {
        return false;
      }
    };
    const postHref = (href) => {
      if (!href) return false;
      try {
        const parsed = new URL(href, location.href);
        const url = parsed.href;
        return isFacebookHost(url) && (
          /\\/posts\\//i.test(parsed.pathname) ||
          /\\/reel\\//i.test(parsed.pathname) ||
          /\\/videos\\//i.test(parsed.pathname) ||
          /\\/story\\.php/i.test(parsed.pathname) ||
          /\\/watch\\//i.test(parsed.pathname) ||
          /\\/photo\\.php/i.test(parsed.pathname) ||
          /\\/permalink\\.php/i.test(parsed.pathname) ||
          parsed.searchParams.has('story_fbid') ||
          parsed.searchParams.has('v') ||
          parsed.searchParams.has('fbid')
        );
      } catch {
        return false;
      }
    };
    const postHrefKind = (href) => {
      try {
        const parsed = new URL(href, location.href);
        if (!isFacebookHost(parsed.href)) return 'none';
        if (/\\/posts\\//i.test(parsed.pathname) || /\\/story\\.php/i.test(parsed.pathname) || /\\/permalink\\.php/i.test(parsed.pathname) || parsed.searchParams.has('story_fbid')) return 'post';
        if (/\\/photo\\.php/i.test(parsed.pathname) || /\\/photo\\//i.test(parsed.pathname) || /\\/reel\\//i.test(parsed.pathname) || /\\/watch\\//i.test(parsed.pathname) || /\\/videos\\//i.test(parsed.pathname) || parsed.searchParams.has('fbid') || parsed.searchParams.has('v')) return 'media';
        return 'other';
      } catch {
        return 'none';
      }
    };
    const bestPostLink = (links) => {
      const realPost = links.find((item) => postHrefKind(item.href) === 'post');
      if (realPost) return realPost;
      return links.find((item) => postHrefKind(item.href) === 'media') || links[0] || null;
    };
    const externalHref = (href) => {
      if (!href) return false;
      try {
        const parsed = new URL(href, location.href);
        if (/l\\.facebook\\.com$/i.test(parsed.hostname) && parsed.searchParams.get('u')) return true;
        return /^https?:/i.test(parsed.protocol) && !isFacebookHost(parsed.href);
      } catch {
        return false;
      }
    };
    const relativeTimeText = (text) => /^(just now|yesterday|\\d+\\s*(m|min|h|hr|d|day|w|wk)|刚刚|\\d+\\s*分钟|\\d+\\s*小时|昨天|\\d+\\s*天|\\d+\\s*周)$/i.test(clean(text));
    const absoluteTimeText = (text) => /^(20\\d\\d[年/-]\\d{1,2}[月/-]\\d{1,2}日?(?:\\s+\\d{1,2}:\\d{2})?|\\d{1,2}月\\d{1,2}日(?:\\s+\\d{1,2}:\\d{2})?)$/i.test(clean(text));
    const timeText = (text) => relativeTimeText(text) || absoluteTimeText(text);
    const pageNames = [
      ...[...document.querySelectorAll('h1, h2')]
        .map((node) => clean(node.innerText))
        .filter(Boolean),
      clean((document.title || '').replace(/\\s*\\|\\s*Facebook\\s*$/i, '').replace(/^\\(\\d+\\+?\\)\\s*/, ''))
    ].filter(Boolean).filter((name) => !/^(Facebook|Home|Posts|About|Reels|Photos|Details|Contact info|Intro|Notifications)$/i.test(name));
    const nodeAnchors = (node) => [...node.querySelectorAll('a[href]')].map((a) => ({
      element: a,
      text: clean(a.innerText || a.getAttribute('aria-label') || a.textContent || ''),
      href: new URL(a.getAttribute('href'), location.href).href,
      aria: clean(a.getAttribute('aria-label') || ''),
      title: clean(a.getAttribute('title') || ''),
      datetime: clean(a.getAttribute('datetime') || ''),
      tooltipContent: clean(a.getAttribute('data-tooltip-content') || ''),
      tooltipText: clean(a.getAttribute('data-tooltip-text') || ''),
    }));
    const compactStoryText = (text) => clean(
      String(text || '')
        .split('\\n')
        .map(clean)
        .filter((line) => line && !/^(Like|Comment|Share|Reply|More|Full Story|See More|赞|评论|分享|回复|更多)$/i.test(line))
        .join(' ')
    );
    const profileShellText = (text) => /\\d+\\s*万次赞\\s*[•·]\\s*\\d+\\s*万位粉丝|followers?\\s*[•·]\\s*\\d+|个人资料\\s+公共主页|Intro\\s+Photos\\s+Videos/i.test(clean(text));
    const engagementText = (text) => {
      const lines = String(text || '').split('\\n').map(clean).filter(Boolean);
      const matches = lines.filter((line) => /\\b\\d+(?:\\.\\d+)?\\s*(?:K|M|万)?\\s*(?:views|plays|likes|comments|shares)\\b|\\d+(?:\\.\\d+)?\\s*万?\\s*(?:次播放|赞|评论|分享)|All reactions/i.test(line));
      return [...new Set(matches)].slice(0, 12).join('；');
    };
    const textLines = (text) => String(text || '').split('\\n').map(clean).filter(Boolean);
    const nearestPostBlockForTimeLink = (timeLink, article) => {
      let node = timeLink?.element || null;
      let best = null;
      for (let depth = 0; node && depth < 8; depth += 1) {
        const text = fullText(node);
        const links = nodeAnchors(node);
        const postLinks = links.filter((item) => postHref(item.href));
        const timeLinks = links.filter((item) => timeText(item.text) || timeText(item.aria));
        const externalLinks = links.filter((item) => externalHref(item.href));
        const hasReaction = /All reactions|Like\\s+Comment\\s+Share|Like\\nComment\\nShare|views|plays|Full Story|完整动态|次播放|赞|评论|分享/i.test(text);
        if (
          text.length >= 25
          && text.length <= 2500
          && postLinks.length > 0
          && timeLinks.length <= 2
          && (externalLinks.length > 0 || hasReaction || timeLinks.length > 0)
        ) {
          best = node;
        }
        if (node === article) break;
        node = node.parentElement;
      }
      return best;
    };
    const storyTextNearTime = (text, timeValue) => {
      const lines = textLines(text);
      const timeIndex = lines.findIndex((line) => line === timeValue || line.includes(timeValue));
      const start = timeIndex >= 0 ? timeIndex : 0;
      return compactStoryText(lines.slice(start, start + 18).join('\\n'));
    };
    const candidateFromNode = (node, meta = {}) => {
      const text = fullText(node);
      if (!text) return null;
      const anchors = nodeAnchors(node);
      const postLinks = anchors.filter((a) => postHref(a.href));
      const selectedPostLink = meta.postLink || bestPostLink(postLinks);
      const externalLinks = anchors.filter((a) => externalHref(a.href));
      const timeLinks = anchors.filter((a) => timeText(a.text) || timeText(a.aria));
      const rawSelectedTimeLink = meta.timeLink || timeLinks[0] || {};
      const selectedTimeLink = {
        text: rawSelectedTimeLink.text || '',
        href: rawSelectedTimeLink.href || '',
        aria: rawSelectedTimeLink.aria || '',
        title: rawSelectedTimeLink.title || '',
        datetime: rawSelectedTimeLink.datetime || '',
        tooltipContent: rawSelectedTimeLink.tooltipContent || '',
        tooltipText: rawSelectedTimeLink.tooltipText || '',
      };
      const exactTime = exactTimeHelpers.exactTimeFromItem(selectedTimeLink || {});
      const firstLine = text.split('\\n').map(clean).find(Boolean) || '';
      const ownerMatched = !pageNames.length || pageNames.some((name) => firstLine === name || firstLine.includes(name));
      const reactionSignals = /All reactions|Like\\s+Comment\\s+Share|Like\\nComment\\nShare|views|plays|Full Story|完整动态|次播放|赞|评论|分享/i.test(text);
      const commentSignals = /(^|\\n)Like\\nReply(\\n|$)|Write a comment|回复/i.test(text);
      const looksLikeComment = commentSignals && !reactionSignals && externalLinks.length === 0;
      const looksLikePost = postLinks.length > 0 && (timeLinks.length > 0 || reactionSignals || externalLinks.length > 0) && !looksLikeComment;
      if (!looksLikePost) return null;
      if (pageNames.length && !ownerMatched && externalLinks.length === 0 && !reactionSignals) return null;
      const timeTextValue = selectedTimeLink.text || selectedTimeLink.aria || '';
      const storyText = meta.splitFromTime ? storyTextNearTime(text, timeTextValue) : compactStoryText(text);
      if (profileShellText(storyText)) return null;
      return {
        post_url: selectedPostLink?.href || '',
        selected_post_link_kind: postHrefKind(selectedPostLink?.href || ''),
        media_link_count: postLinks.filter((item) => postHrefKind(item.href) === 'media').length,
        article_url: externalLinks[0]?.href || '',
        story_summary: (storyText || text).slice(0, 500),
        post_time_text: timeTextValue,
        posted_at_raw: exactTime.posted_at_raw,
        posted_at: exactTime.posted_at,
        time_source: exactTime.time_source,
        time_confirmed: Boolean(exactTime.posted_at),
        engagement_data: engagementText(text),
        raw_text: text.slice(0, ${Number(maxText) || 1200}),
        source_surface: sourceSurface,
        source_split: meta.splitFromTime ? 'time_anchor' : 'article',
        first_line: firstLine,
        owner_matched: ownerMatched,
        post_url_count: postLinks.length,
        external_url_count: externalLinks.length,
        time_texts: timeLinks.map((a) => a.text || a.aria).filter(Boolean).slice(0, 8),
        link_count: anchors.length
      };
    };
    const articleNodes = [...document.querySelectorAll('div[role="article"], article')];
    const anchorSeedNodes = [];
    for (const anchor of document.querySelectorAll('a[href]')) {
      if (!postHref(anchor.href)) continue;
      let node = anchor;
      let best = null;
      for (let depth = 0; node && depth < 8; depth += 1) {
        const text = fullText(node);
        const links = nodeAnchors(node);
        const postCount = links.filter((item) => postHref(item.href)).length;
        const externalCount = links.filter((item) => externalHref(item.href)).length;
        const hasTime = links.some((item) => timeText(item.text) || timeText(item.aria));
        const goodLength = text.length >= 25 && text.length <= 7000;
        if (goodLength && postCount > 0 && (hasTime || externalCount > 0 || /Like|Comment|Share|赞|评论|分享|Full Story|完整动态/i.test(text))) {
          best = node;
          if (/^(ARTICLE|TABLE)$/i.test(node.tagName || '') || node.getAttribute('role') === 'article') break;
        }
        node = node.parentElement;
      }
      if (best) anchorSeedNodes.push(best);
    }
    const articles = [...new Set([...articleNodes, ...anchorSeedNodes])];
    const candidates = [];
    const candidateKeys = new Set();
    const pushCandidate = (candidate) => {
      if (!candidate?.post_url) return false;
      const key = [candidate.post_url, candidate.post_time_text || '', candidate.posted_at || ''].join('|');
      if (candidateKeys.has(key)) return false;
      candidateKeys.add(key);
      candidates.push(candidate);
      return true;
    };
    for (const article of articles) {
      const anchors = nodeAnchors(article);
      const timeLinks = anchors.filter((a) => timeText(a.text) || timeText(a.aria));
      const postLinks = anchors.filter((a) => postHref(a.href));
      let splitCount = 0;
      if (timeLinks.length > 1 || postLinks.length > 1) {
        for (const timeLink of timeLinks) {
          const block = nearestPostBlockForTimeLink(timeLink, article);
          if (!block) continue;
          const blockLinks = nodeAnchors(block).filter((a) => postHref(a.href));
          if (pushCandidate(candidateFromNode(block, {
            timeLink,
            postLink: bestPostLink(blockLinks),
            splitFromTime: true,
          }))) {
            splitCount += 1;
          }
        }
      }
      if (!splitCount) {
        pushCandidate(candidateFromNode(article));
      }
    }
    const bodyText = document.body?.innerText || '';
    const loggedOut = /Log in to Facebook|登录 Facebook|Forgot Account|Forgot password|Forgotten password|Create new account|新建帐户|邮箱或手机号\\s+密码\\s+登录/i.test(bodyText);
    const visitorPreview = /(登录|Log in)\\s+(忘记账户了？|Forgot Account|Forgot password|Forgotten password)/i.test(bodyText)
      || (/^\\s*登录\\s+忘记账户了？/i.test(bodyText) && bodyText.length < 20000);
    return {
      url: location.href,
      title: document.title,
      source_surface: sourceSurface,
      logged_out: loggedOut,
      visitor_preview: visitorPreview,
      capture_blocked: loggedOut || visitorPreview,
      page_names: pageNames,
      body_length: bodyText.length,
      body_preview: bodyText.slice(0, ${Number(maxText) || 1200}),
      article_count: articles.length,
      candidates,
      real_post_count: candidates.length,
      comment_count: articles.filter((article) => /(^|\\n)Like\\nReply(\\n|$)/i.test(fullText(article))).length
    };
  })()`;
}

module.exports = { browserExpression };
