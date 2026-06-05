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
          /\\/groups\\/[^/]+\\/posts\\//i.test(parsed.pathname) ||
          /\\/reel\\//i.test(parsed.pathname) ||
          /\\/videos\\//i.test(parsed.pathname) ||
          /\\/video\\//i.test(parsed.pathname) ||
          /\\/story\\.php/i.test(parsed.pathname) ||
          /\\/watch\\//i.test(parsed.pathname) ||
          /\\/photo\\.php/i.test(parsed.pathname) ||
          /\\/photo\\//i.test(parsed.pathname) ||
          /\\/photos\\//i.test(parsed.pathname) ||
          /\\/share\\//i.test(parsed.pathname) ||
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
        if (/\\/photo\\.php/i.test(parsed.pathname) || /\\/photos?\\//i.test(parsed.pathname) || /\\/reel\\//i.test(parsed.pathname) || /\\/watch\\//i.test(parsed.pathname) || /\\/videos?\\//i.test(parsed.pathname) || /\\/share\\//i.test(parsed.pathname) || parsed.searchParams.has('fbid') || parsed.searchParams.has('v')) return 'media';
        return 'other';
      } catch {
        return 'none';
      }
    };
    const hasCommentParam = (href) => {
      try {
        const parsed = new URL(href, location.href);
        return parsed.searchParams.has('comment_id') || parsed.searchParams.has('reply_comment_id');
      } catch {
        return false;
      }
    };
    const mainPostHref = (href) => postHref(href) && !hasCommentParam(href);
    const bestPostLink = (links) => {
      const eligible = links.filter((item) => mainPostHref(item.href));
      const realPost = eligible.find((item) => postHrefKind(item.href) === 'post');
      if (realPost) return realPost;
      return eligible.find((item) => postHrefKind(item.href) === 'media') || eligible[0] || null;
    };
    const cleanFacebookContentUrl = (href) => {
      if (!href) return '';
      try {
        const parsed = new URL(href, location.href);
        parsed.hash = '';
        const isPhotoQuery = /\\/photo\\/?$/i.test(parsed.pathname) && parsed.searchParams.get('fbid');
        for (const key of [...parsed.searchParams.keys()]) {
          if (isPhotoQuery && key !== 'fbid') {
            parsed.searchParams.delete(key);
            continue;
          }
          if (key === 'comment_id' || key === 'reply_comment_id' || key === 'fbclid' || key.startsWith('utm_') || key.startsWith('__')) {
            parsed.searchParams.delete(key);
          }
        }
        parsed.hostname = parsed.hostname.replace(/^www\\./i, '');
        return parsed.href;
      } catch {
        return String(href || '');
      }
    };
    const postIdentityKey = (href) => {
      if (!href) return '';
      try {
        const parsed = new URL(href, location.href);
        parsed.hash = '';
        const parts = parsed.pathname.split('/').filter(Boolean);
        const storyFbid = parsed.searchParams.get('story_fbid');
        const photoFbid = parsed.searchParams.get('fbid');
        const id = parsed.searchParams.get('id');
        if (storyFbid && id) return 'story:' + id + ':' + storyFbid;
        if (parts.includes('posts')) {
          const index = parts.indexOf('posts');
          if (index > 0 && parts[index + 1]) {
            if (index >= 2 && parts[index - 2] === 'groups') return 'group-post:' + parts[index - 1] + ':' + parts[index + 1];
            return 'post:' + parts[index - 1] + ':' + parts[index + 1];
          }
        }
        if (parts.includes('reel')) {
          const index = parts.indexOf('reel');
          if (parts[index + 1]) return 'reel:' + parts[index + 1];
        }
        if (parts.includes('videos')) {
          const index = parts.indexOf('videos');
          if (parts[index + 1]) return 'video:' + parts[index + 1];
        }
        if (parts.includes('video')) {
          const index = parts.indexOf('video');
          if (parts[index + 1]) return 'video:' + parts[index + 1];
        }
        if (parts.includes('watch') && parsed.searchParams.get('v')) return 'video:' + parsed.searchParams.get('v');
        if ((parsed.pathname.includes('photo.php') || parts.join('/') === 'photo') && photoFbid) return 'photo:' + photoFbid;
        if (parts.includes('photos')) {
          const index = parts.indexOf('photos');
          const tail = parts.slice(index + 1).filter((part) => !['a', 'p', 'photo'].includes(part));
          const numericTail = tail.filter((part) => /^\\d{6,}$/.test(part));
          const photoId = numericTail.at(-1) || tail.at(-1);
          if (photoId) return 'photo:' + photoId;
        }
        if (parts.includes('share')) {
          const index = parts.indexOf('share');
          if (parts[index + 1]) return 'share:' + parts.slice(index + 1).join(':');
        }
        if (parsed.hostname === 'fb.watch' && parts[0]) return 'fb-watch:' + parts[0];
        for (const key of [...parsed.searchParams.keys()]) {
          if (key === 'comment_id' || key === 'reply_comment_id' || key === 'fbclid' || key.startsWith('utm_') || key.startsWith('__')) {
            parsed.searchParams.delete(key);
          }
        }
        return parsed.href;
      } catch {
        return String(href || '');
      }
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
    const parseCount = (value) => {
      const text = clean(value).replace(/,/g, '');
      const match = text.match(/([\\d.]+)\\s*([kKmMwW万]?)/);
      if (!match) return null;
      let number = Number(match[1]);
      if (!Number.isFinite(number)) return null;
      const unit = String(match[2] || '').toLowerCase();
      if (unit === 'k') number *= 1000;
      if (unit === 'm') number *= 1000000;
      if (unit === 'w' || unit === '万') number *= 10000;
      return Math.round(number);
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
    const engagementMetrics = (node) => {
      const result = {
        raw: '',
        views: null,
        likes: null,
        reactions: null,
        comments: null,
        shares: null,
        source: 'homepage_post_block',
      };
      const countToken = '([\\\\d.,]+\\\\s*(?:K|M|万|w)?)';
      const relativeTimeOnly = (value) => /^(?:just now|yesterday|刚刚|昨天|\\d+\\s*(?:m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks)(?:\\s+ago)?|\\d+\\s*(?:分钟|小时|天|周))$/i.test(clean(value));
      const setMetric = (key, value, rawText) => {
        const parsed = parseCount(value);
        if (parsed === null || parsed === undefined) return;
        if (result[key] === null || result[key] === undefined) result[key] = parsed;
        if (key === 'reactions' && (result.likes === null || result.likes === undefined)) result.likes = parsed;
        if (rawText) result.raw = result.raw || clean(rawText);
      };
      const readMetricText = (text) => {
        const item = clean(text);
        if (!item || item.length > 220) return;
        if (relativeTimeOnly(item)) return;
        const patterns = [
          ['views', new RegExp(countToken + '\\\\s*(?:views?|plays?|次播放|播放|浏览)', 'i')],
          ['comments', new RegExp(countToken + '\\\\s*(?:comments?|评论)', 'i')],
          ['shares', new RegExp(countToken + '\\\\s*(?:shares?|分享)', 'i')],
          ['reactions', new RegExp('(?:All reactions|reactions?|likes?|赞)[^0-9]{0,20}' + countToken, 'i')],
          ['reactions', new RegExp(countToken + '\\\\s*(?:reactions?|likes?|赞)', 'i')],
        ];
        for (const [key, pattern] of patterns) {
          const match = item.match(pattern);
          if (match) setMetric(key, match[1], item);
        }
      };
      for (const metricNode of [...node.querySelectorAll('a, span, div, [aria-label], [title]')]) {
        const ownerArticle = metricNode.closest?.('[role="article"], article');
        if (ownerArticle && ownerArticle !== node) continue;
        for (const text of [
          metricNode.getAttribute?.('aria-label') || '',
          metricNode.getAttribute?.('title') || '',
          metricNode.innerText || metricNode.textContent || '',
        ]) {
          readMetricText(text);
        }
      }
      const lines = textLines(fullText(node));
      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        readMetricText(line);
        const slashCluster = line.match(new RegExp('^' + countToken + '\\\\s*[/|]\\\\s*' + countToken + '\\\\s*[/|]\\\\s*' + countToken + '$', 'i'));
        if (slashCluster) {
          setMetric('reactions', slashCluster[1], line);
          setMetric('comments', slashCluster[2], line);
          setMetric('shares', slashCluster[3], line);
        }
        const triple = lines.slice(index, index + 3);
        const nextText = lines.slice(index + 3, index + 8).join(' ');
        if (
          triple.length === 3
          && new RegExp('^' + countToken + '$', 'i').test(triple[0])
          && new RegExp('^' + countToken + '\\\\s*(?:comments?|评论)$', 'i').test(triple[1])
          && new RegExp('^' + countToken + '\\\\s*(?:shares?|分享)$', 'i').test(triple[2])
        ) {
          setMetric('reactions', triple[0], triple.join('；'));
          readMetricText(triple[1]);
          readMetricText(triple[2]);
        }
        if (
          triple.length === 3
          && triple.every((item) => new RegExp('^' + countToken + '$', 'i').test(item))
          && /\\bLike\\b|\\bComment\\b|\\bShare\\b|赞|评论|分享/i.test(nextText)
        ) {
          setMetric('reactions', triple[0], triple.join('；'));
          setMetric('comments', triple[1], triple.join('；'));
          setMetric('shares', triple[2], triple.join('；'));
        }
      }
      const parts = [];
      if (result.views !== null && result.views !== undefined) parts.push('浏览量：' + result.views);
      if (result.likes !== null && result.likes !== undefined) parts.push('点赞量：' + result.likes);
      if (result.comments !== null && result.comments !== undefined) parts.push('评论数：' + result.comments);
      if (result.shares !== null && result.shares !== undefined) parts.push('分享数：' + result.shares);
      result.raw = parts.join('；') || result.raw;
      return result;
    };
    const engagementText = (text) => {
      const lines = String(text || '').split('\\n').map(clean).filter(Boolean);
      const matches = lines.filter((line) => /\\b\\d+(?:\\.\\d+)?\\s*(?:K|M|万)?\\s*(?:views|plays|likes|comments|shares)\\b|\\d+(?:\\.\\d+)?\\s*万?\\s*(?:次播放|赞|评论|分享)|All reactions/i.test(line));
      return [...new Set(matches)].slice(0, 12).join('；');
    };
    const textLines = (text) => String(text || '').split('\\n').map(clean).filter(Boolean);
    const commentMediaFallbackText = (text) => {
      const lines = textLines(text);
      const compact = clean(lines.join(' '));
      if (!compact) return false;
      if (/\\bAuthor\\b.{0,120}\\bPart\\s*\\d+\\b/i.test(compact) && /\\bLike\\b.{0,30}\\bReply\\b/i.test(compact)) return true;
      if (/\\bLike\\b.{0,20}\\bReply\\b/i.test(compact) && !/\\bComment\\b.{0,20}\\bShare\\b/i.test(compact)) return true;
      if (/^Author\\b/i.test(lines[0] || '') && /\\bReply\\b/i.test(compact)) return true;
      return false;
    };
    const selectHomepageLeadLink = (node, externalLinks) => {
      if (!externalLinks.length) return null;
      const authorNames = pageNames.map((name) => name.toLowerCase());
      const scored = externalLinks.map((link, index) => {
        let cursor = link.element;
        let score = 0;
        let blockText = '';
        for (let depth = 0; cursor && depth < 6; depth += 1) {
          const text = clean(cursor.innerText || cursor.textContent || '');
          if (text && (!blockText || text.length < blockText.length)) blockText = text;
          if (/\\bAuthor\\b|作者/i.test(text)) score += 30;
          if (/\\bReply\\b|回复|\\bLike\\b/i.test(text)) score += 10;
          if (authorNames.some((name) => name && text.toLowerCase().includes(name))) score += 20;
          if (cursor === node) break;
          cursor = cursor.parentElement;
        }
        if (index === 0) score += 3;
        return { link, score, blockText };
      }).sort((a, b) => b.score - a.score);
      const selected = scored[0];
      if (!selected) return null;
      return {
        href: selected.link.href,
        source: selected.score >= 30 ? 'comment_reply' : 'post_cta',
        excerpt: selected.blockText.slice(0, 300),
      };
    };
    const nearestPostBlockForTimeLink = (timeLink, article) => {
      let node = timeLink?.element || null;
      let best = null;
      for (let depth = 0; node && depth < 8; depth += 1) {
        const text = fullText(node);
        const links = nodeAnchors(node);
        const postLinks = links.filter((item) => mainPostHref(item.href));
        const timeLinks = links.filter((item) => timeText(item.text) || timeText(item.aria));
        const externalLinks = links.filter((item) => externalHref(item.href));
        const hasReaction = /All reactions|Like\\s+Comment\\s+Share|Like\\nComment\\nShare|\\b\\d+(?:\\.\\d+)?\\s*(?:K|M|万)?\\s*(?:views|plays|likes|comments|shares)\\b|\\d+(?:\\.\\d+)?\\s*万?\\s*(?:次播放|赞|评论|分享)|views|plays|Full Story|完整动态|次播放|赞|评论|分享/i.test(text);
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
      const mainPostLinks = postLinks.filter((a) => mainPostHref(a.href));
      const selectedPostLink = meta.postLink || bestPostLink(postLinks);
      const externalLinks = anchors.filter((a) => externalHref(a.href));
      const leadLink = selectHomepageLeadLink(node, externalLinks);
      const timeLinks = anchors.filter((a) => mainPostHref(a.href) && (timeText(a.text) || timeText(a.aria)));
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
      const reactionSignals = /All reactions|Like\\s+Comment\\s+Share|Like\\nComment\\nShare|\\b\\d+(?:\\.\\d+)?\\s*(?:K|M|万)?\\s*(?:views|plays|likes|comments|shares)\\b|\\d+(?:\\.\\d+)?\\s*万?\\s*(?:次播放|赞|评论|分享)|views|plays|Full Story|完整动态|次播放|赞|评论|分享/i.test(text);
      const commentSignals = /(^|\\n)Like\\nReply(\\n|$)|\\bLikeReply\\b|Write a comment|回复/i.test(text);
      const looksLikeComment = commentSignals && !reactionSignals && externalLinks.length === 0 && postLinks.length === 0;
      const looksLikePost = Boolean(selectedPostLink) && mainPostLinks.length > 0 && (timeLinks.length > 0 || reactionSignals || externalLinks.length > 0) && !looksLikeComment;
      if (!looksLikePost) return null;
      if (pageNames.length && !ownerMatched && externalLinks.length === 0 && !reactionSignals) return null;
      if (firstLine === 'Facebook' && timeLinks.length === 0) return null;
      if (postHrefKind(selectedPostLink?.href || '') === 'media' && timeLinks.length === 0) return null;
      const timeTextValue = selectedTimeLink.text || selectedTimeLink.aria || '';
      const storyText = meta.splitFromTime ? storyTextNearTime(text, timeTextValue) : compactStoryText(text);
      if (profileShellText(storyText)) return null;
      const metrics = engagementMetrics(node);
      return {
        post_url: cleanFacebookContentUrl(selectedPostLink?.href || ''),
        raw_fb_url: selectedPostLink?.href || '',
        selected_post_link_kind: postHrefKind(selectedPostLink?.href || ''),
        media_link_count: postLinks.filter((item) => postHrefKind(item.href) === 'media').length,
        article_url: leadLink?.href || externalLinks[0]?.href || '',
        lead_url_raw: leadLink?.href || '',
        landing_url: leadLink?.href || externalLinks[0]?.href || '',
        lead_link_status: leadLink?.href ? 'qualified' : '',
        lead_link_source: leadLink?.source || '',
        comment_lead_excerpt: leadLink?.excerpt || '',
        story_summary: (storyText || text).slice(0, 500),
        post_time_text: timeTextValue,
        posted_at_raw: exactTime.posted_at_raw,
        posted_at: exactTime.posted_at,
        time_source: exactTime.time_source,
        time_confirmed: Boolean(exactTime.posted_at),
        engagement_data: metrics.raw || engagementText(text),
        engagement_source: metrics.raw ? metrics.source : '',
        reactions: metrics.reactions,
        likes: metrics.likes,
        comments: metrics.comments,
        shares: metrics.shares,
        views: metrics.views,
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
      if (!mainPostHref(anchor.href)) continue;
      let node = anchor;
      let best = null;
      for (let depth = 0; node && depth < 8; depth += 1) {
        const text = fullText(node);
        const links = nodeAnchors(node);
        const postCount = links.filter((item) => mainPostHref(item.href)).length;
        const externalCount = links.filter((item) => externalHref(item.href)).length;
        const hasTime = links.some((item) => mainPostHref(item.href) && (timeText(item.text) || timeText(item.aria)));
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
      const key = postIdentityKey(candidate.post_url) || [candidate.post_url, candidate.post_time_text || '', candidate.posted_at || ''].join('|');
      if (candidateKeys.has(key)) return false;
      candidateKeys.add(key);
      candidates.push(candidate);
      return true;
    };
    for (const article of articles) {
      const anchors = nodeAnchors(article);
      const timeLinks = anchors.filter((a) => mainPostHref(a.href) && (timeText(a.text) || timeText(a.aria)));
      const postLinks = anchors.filter((a) => postHref(a.href));
      const distinctRealPostKeys = new Set(
        postLinks
          .filter((item) => mainPostHref(item.href) && postHrefKind(item.href) === 'post')
          .map((item) => postIdentityKey(item.href))
          .filter(Boolean)
      );
      const preferArticleCandidate = distinctRealPostKeys.size <= 1;
      let articlePushed = false;
      let splitCount = 0;
      if (preferArticleCandidate) {
        articlePushed = pushCandidate(candidateFromNode(article));
      }
      if (!articlePushed && (timeLinks.length > 1 || postLinks.length > 1)) {
        for (const timeLink of timeLinks) {
          const block = nearestPostBlockForTimeLink(timeLink, article);
          if (!block) continue;
          const blockLinks = nodeAnchors(block).filter((a) => mainPostHref(a.href));
          if (pushCandidate(candidateFromNode(block, {
            timeLink,
            postLink: bestPostLink(blockLinks),
            splitFromTime: true,
          }))) {
            splitCount += 1;
          }
        }
      }
      if (!articlePushed && !splitCount) {
        pushCandidate(candidateFromNode(article));
      }
    }
    const mediaFallbackLinks = [...document.querySelectorAll('a[href]')]
      .map((element) => {
        const href = new URL(element.getAttribute('href'), location.href).href;
        let text = '';
        let contextText = '';
        let cursor = element;
        for (let depth = 0; cursor && depth < 5; depth += 1) {
          const currentText = clean(cursor.innerText || cursor.textContent || '');
          if (currentText && (!text || currentText.length < text.length)) text = currentText;
          if (currentText && currentText.length > contextText.length) contextText = currentText;
          cursor = cursor.parentElement;
        }
        return { element, href, text, contextText };
      })
      .filter((item) => mainPostHref(item.href) && postHrefKind(item.href) === 'media')
      .filter((item) => !commentMediaFallbackText(item.contextText || item.text))
      .filter((item) => {
        const parsed = new URL(item.href, location.href);
        const parts = parsed.pathname.split('/').filter(Boolean);
        if (parts.includes('reel') && !parts[parts.indexOf('reel') + 1]) return false;
        if ((parts.includes('watch') || parts.includes('video') || parts.includes('videos')) && !parsed.searchParams.get('v') && !parts.at(-1)?.match(/^\\d{6,}$/)) return false;
        return true;
      })
      .filter((item) => !/\\/photos\\/?$/i.test(new URL(item.href, location.href).pathname))
      .filter((item) => !/^(photos|see all photos?|profile photo|cover photo|照片|查看所有照片|头像|封面)$/i.test(item.text));
    const hasParentPostCandidate = candidates.some((candidate) => postHrefKind(candidate.post_url) === 'post');
    for (const media of mediaFallbackLinks.slice(0, 24)) {
      if (hasParentPostCandidate) break;
      pushCandidate({
        post_url: cleanFacebookContentUrl(media.href),
        raw_fb_url: media.href,
        selected_post_link_kind: postHrefKind(media.href),
        media_link_count: 1,
        article_url: '',
        lead_url_raw: '',
        landing_url: '',
        lead_link_status: '',
        lead_link_source: '',
        comment_lead_excerpt: '',
        story_summary: media.text.slice(0, 500),
        post_time_text: '',
        posted_at_raw: '',
        posted_at: '',
        time_source: '',
        time_confirmed: false,
        engagement_data: '',
        engagement_source: '',
        reactions: null,
        likes: null,
        comments: null,
        shares: null,
        views: null,
        raw_text: media.text.slice(0, ${Number(maxText) || 1200}),
        source_surface: sourceSurface,
        source_split: 'media_fallback',
        first_line: textLines(media.text)[0] || '',
        owner_matched: true,
        post_url_count: 1,
        external_url_count: 0,
        time_texts: [],
        link_count: 1,
      });
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
