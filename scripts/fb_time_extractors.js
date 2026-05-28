const EN_MONTHS = {
  january: 1,
  february: 2,
  march: 3,
  april: 4,
  may: 5,
  june: 6,
  july: 7,
  august: 8,
  september: 9,
  october: 10,
  november: 11,
  december: 12,
};

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function parseExactFacebookTime(value) {
  const text = clean(value);
  if (!text) return "";

  const currentYear = new Date().getFullYear();

  const english = text.match(
    /^(?:[A-Za-z]+,\s+)?([A-Za-z]+)\s+(\d{1,2}),\s+(20\d\d)\s+at\s+(\d{1,2}):(\d{2})\s+([AP]M)$/i
  );
  if (english) {
    const [, monthName, day, year, hour, minute, ampm] = english;
    const month = EN_MONTHS[monthName.toLowerCase()];
    if (!month) return "";
    let hourNum = Number(hour);
    if (ampm.toUpperCase() === "PM" && hourNum !== 12) hourNum += 12;
    if (ampm.toUpperCase() === "AM" && hourNum === 12) hourNum = 0;
    return `${year}年${month}月${Number(day)}日 ${String(hourNum).padStart(2, "0")}:${minute}`;
  }

  const englishWithoutYear = text.match(
    /^(?:[A-Za-z]+,\s+)?([A-Za-z]+)\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})\s+([AP]M)$/i
  );
  if (englishWithoutYear) {
    const [, monthName, day, hour, minute, ampm] = englishWithoutYear;
    const month = EN_MONTHS[monthName.toLowerCase()];
    if (!month) return "";
    let hourNum = Number(hour);
    if (ampm.toUpperCase() === "PM" && hourNum !== 12) hourNum += 12;
    if (ampm.toUpperCase() === "AM" && hourNum === 12) hourNum = 0;
    return `${currentYear}年${month}月${Number(day)}日 ${String(hourNum).padStart(2, "0")}:${minute}`;
  }

  const chineseAmPm = text.match(
    /^(?:星期[一二三四五六日天]\s*)?(20\d\d)[年/-](\d{1,2})[月/-](\d{1,2})日?\s*(上午|下午|中午|凌晨|晚上)\s*(\d{1,2}):(\d{2})$/
  );
  if (chineseAmPm) {
    const [, year, month, day, marker, hour, minute] = chineseAmPm;
    let hourNum = Number(hour);
    if ((marker === "下午" || marker === "晚上") && hourNum !== 12) hourNum += 12;
    if (marker === "凌晨" && hourNum === 12) hourNum = 0;
    return `${year}年${Number(month)}月${Number(day)}日 ${String(hourNum).padStart(2, "0")}:${minute}`;
  }

  const chinese = text.match(
    /^(?:星期[一二三四五六日天]\s*)?(20\d\d)[年/-](\d{1,2})[月/-](\d{1,2})日?\s*(\d{1,2}):(\d{2})$/
  );
  if (chinese) {
    const [, year, month, day, hour, minute] = chinese;
    return `${year}年${Number(month)}月${Number(day)}日 ${String(Number(hour)).padStart(2, "0")}:${minute}`;
  }

  return "";
}

function isRelativeTimeText(value) {
  return /^(just now|yesterday|\d+\s*(m|min|h|hr|d|day|w|wk)|刚刚|\d+\s*分钟|\d+\s*小时|昨天|\d+\s*天|\d+\s*周)$/i.test(
    clean(value)
  );
}

function isFacebookPostTimeHref(value) {
  const href = clean(value);
  if (!href) return false;
  try {
    const parsed = new URL(href, "https://www.facebook.com");
    if (!/(^|\.)facebook\.com$/i.test(parsed.hostname)) return false;
    const path = parsed.pathname;
    return (
      /\/posts\//i.test(path) ||
      /\/reel\//i.test(path) ||
      /\/videos\//i.test(path) ||
      /\/watch\//i.test(path) ||
      /\/story\.php/i.test(path) ||
      /\/permalink\.php/i.test(path) ||
      parsed.searchParams.has("story_fbid") ||
      parsed.searchParams.has("v")
    );
  } catch {
    return false;
  }
}

function looksLikeFacebookScrambledTimestampText(value) {
  const text = clean(value);
  if (!text) return false;
  if (isRelativeTimeText(text) || parseExactFacebookTime(text)) return true;
  if (!/[mhdw]/i.test(text)) return false;
  if (!/\d/.test(text)) return false;
  if (text.length > 220) return false;
  const compact = text.replace(/\s+/g, "");
  return compact.length >= 3 && compact.length <= 120;
}

function isLikelyHeaderTimeElement(item, viewportHeight = 800) {
  if (!item) return false;
  const text = clean(item.text);
  const aria = clean(item.aria);
  const title = clean(item.title);
  const hasTime = isRelativeTimeText(text) || parseExactFacebookTime(aria) || parseExactFacebookTime(title);
  if (item.w <= 0 || item.h <= 0) return false;
  const inHeaderBand = item.y > 40 && item.y < Math.min(300, viewportHeight * 0.45);
  if (!inHeaderBand) return false;
  if (hasTime) return true;

  // Facebook may render the visible "3h" as a tiny link whose DOM text is
  // split into obfuscated single-character spans. In that case the link href
  // still points to the post/reel permalink and hover still shows the exact
  // timestamp tooltip.
  return (
    isFacebookPostTimeHref(item.href) &&
    item.w > 0 &&
    item.w <= 140 &&
    item.h > 0 &&
    item.h <= 32 &&
    looksLikeFacebookScrambledTimestampText(text)
  );
}

function exactTimeFromItem(item) {
  if (!item) return { posted_at_raw: "", posted_at: "", time_source: "" };
  const fields = [
    ["aria_label", item.aria],
    ["title", item.title],
    ["datetime", item.datetime],
    ["data_tooltip_content", item.tooltipContent],
    ["data_tooltip_text", item.tooltipText],
    ["text", item.text],
  ];
  for (const [source, raw] of fields) {
    const parsed = parseExactFacebookTime(raw);
    if (parsed) {
      return { posted_at_raw: clean(raw), posted_at: parsed, time_source: `dom_${source}` };
    }
  }
  return { posted_at_raw: "", posted_at: "", time_source: "" };
}

function browserExactTimeHelpersExpression() {
  return `(() => {
    const EN_MONTHS = ${JSON.stringify(EN_MONTHS)};
    const clean = ${clean.toString()};
    const parseExactFacebookTime = ${parseExactFacebookTime.toString()};
    const isRelativeTimeText = ${isRelativeTimeText.toString()};
    const isFacebookPostTimeHref = ${isFacebookPostTimeHref.toString()};
    const looksLikeFacebookScrambledTimestampText = ${looksLikeFacebookScrambledTimestampText.toString()};
    const isLikelyHeaderTimeElement = ${isLikelyHeaderTimeElement.toString()};
    const exactTimeFromItem = ${exactTimeFromItem.toString()};
    return { clean, parseExactFacebookTime, isRelativeTimeText, isFacebookPostTimeHref, looksLikeFacebookScrambledTimestampText, isLikelyHeaderTimeElement, exactTimeFromItem };
  })()`;
}

module.exports = {
  clean,
  exactTimeFromItem,
  isFacebookPostTimeHref,
  isLikelyHeaderTimeElement,
  isRelativeTimeText,
  looksLikeFacebookScrambledTimestampText,
  parseExactFacebookTime,
  browserExactTimeHelpersExpression,
};
