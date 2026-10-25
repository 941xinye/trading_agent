"""情绪与消息面：恐慌贪婪指数 + 多源快讯（英文 RSS + 中文金色财经）。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .. import net

# 英文 RSS（item 格式）
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# 金色财经资讯 API（JSON，返回深度文章，published_at 为秒级时间戳）
JINSE_INFORMATION_URLS = [
    "https://www.jinse.com.cn/api/v6/www/information/list?catelogue_key=defi&page=1",
    "https://www.jinse.com.cn/api/v6/www/information/list?catelogue_key=market&page=1",
]

# 金色财经快讯页（HTML 服务端渲染，含今日实时快讯）
JINSE_LIVES_URL = "https://www.jinse.com.cn/lives"

FNG_LABELS = {
    "Extreme Fear": "极度恐慌",
    "Fear": "恐慌",
    "Neutral": "中性",
    "Greed": "贪婪",
    "Extreme Greed": "极度贪婪",
}


def fear_greed() -> dict[str, Any]:
    data = net.get_json("https://api.alternative.me/fng/", {"limit": 30}, ttl=900, retries=1)
    items = data.get("data") or []
    if not items:
        return {}
    values = [int(item["value"]) for item in items]
    latest = items[0]
    return {
        "value": values[0],
        "label": FNG_LABELS.get(latest.get("value_classification"), latest.get("value_classification")),
        "change_1d": values[0] - values[1] if len(values) > 1 else None,
        "change_7d": values[0] - values[7] if len(values) > 7 else None,
        "avg_30d": round(sum(values) / len(values), 1),
        "history_30d": values[::-1],
    }


def _local_ts(dt: datetime) -> str:
    return dt.astimezone().strftime("%m-%d %H:%M")


def _strip_namespaces(root: ET.Element) -> ET.Element:
    """去掉所有元素的命名空间前缀，便于统一用本地名查找。"""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _parse_feed(url: str, limit: int, max_age_hours: int) -> list[dict[str, Any]]:
    """解析 RSS 2.0（<item>）与 Atom 1.0（<entry>）。"""
    try:
        raw = net.get_text(url, ttl=900, retries=1)
        root = _strip_namespaces(ET.fromstring(raw))
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict[str, Any]] = []

    # RSS 2.0: <item> + <pubDate>（RFC 2822），<link> 为文本
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = (node.findtext("pubDate") or "").strip()
        if not title:
            continue
        ts = _parse_time(published, cutoff, rfc2822=True)
        if ts is _SKIP:
            continue
        items.append({"title": title, "link": link, "time": ts})
        if len(items) >= limit:
            break

    # Atom 1.0: <entry> + <published>/<updated>（ISO 8601），<link href="..."/>
    if not items:
        for node in root.iter("entry"):
            title = (node.findtext("title") or "").strip()
            link_node = node.find("link")
            link = (link_node.get("href") if link_node is not None else "") or ""
            published = (node.findtext("published") or node.findtext("updated") or "").strip()
            if not title:
                continue
            ts = _parse_time(published, cutoff, rfc2822=False)
            if ts is _SKIP:
                continue
            items.append({"title": title, "link": link, "time": ts})
            if len(items) >= limit:
                break
    return items


_SKIP = object()


def _parse_time(value: str, cutoff: datetime, rfc2822: bool) -> str | object:
    """返回本地时间字符串，过期返回 _SKIP，解析失败返回 None。"""
    if not value:
        return None
    try:
        if rfc2822:
            dt = parsedate_to_datetime(value)
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            return _SKIP
        return _local_ts(dt)
    except (TypeError, ValueError):
        return None


def _parse_jinse_information(url: str, limit: int, max_age_hours: int) -> list[dict[str, Any]]:
    """金色财经资讯 API：JSON list[].extra.published_at 为秒级时间戳。"""
    try:
        data = net.get_json(url, ttl=900, retries=1)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict[str, Any]] = []
    for node in data.get("list") or []:
        extra = node.get("extra") or {}
        title = (node.get("title") or extra.get("title") or "").strip()
        if not title:
            continue
        link = extra.get("topic_url") or ""
        published_at = extra.get("published_at")
        ts = None
        if isinstance(published_at, (int, float)):
            dt = datetime.fromtimestamp(published_at, tz=timezone.utc)
            if dt < cutoff:
                continue
            ts = _local_ts(dt)
        items.append({"title": title, "link": link, "time": ts})
        if len(items) >= limit:
            break
    return items


_LIVES_ITEM_RE = re.compile(
    r'js-lives__item">.*?<div class="time">\s*([^<\s]+).*?'
    r'<a href="([^"]+)"[^>]*class="title"[^>]*>(?:<!--.*?-->)?\s*([^<]+?)\s*</a>',
    re.DOTALL,
)


def _parse_jinse_lives(url: str, limit: int, max_age_hours: int) -> list[dict[str, Any]]:
    """金色财经快讯页：HTML 服务端渲染，取今日 HH:MM 快讯。"""
    try:
        raw = net.get_text(url, ttl=300, retries=1)
    except Exception:
        return []
    today = datetime.now().date()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict[str, Any]] = []
    for time_str, href, title in _LIVES_ITEM_RE.findall(raw):
        title = title.strip()
        if not title:
            continue
        link = href if href.startswith("http") else f"https://www.jinse.com.cn{href}"
        ts = None
        m = re.match(r"(\d{1,2}):(\d{2})", time_str.strip())
        if m:
            local_dt = datetime(
                today.year, today.month, today.day,
                int(m.group(1)), int(m.group(2)),
                tzinfo=datetime.now().astimezone().tzinfo,
            )
            if local_dt.astimezone(timezone.utc) < cutoff:
                continue
            ts = local_dt.strftime("%m-%d %H:%M")
        items.append({"title": title, "link": link, "time": ts})
        if len(items) >= limit:
            break
    return items


def news(limit: int = 8, max_age_hours: int = 48) -> list[dict[str, Any]]:
    """聚合多源快讯（英文 RSS + 中文金色财经），按发布时间倒序。"""
    collected: list[dict[str, Any]] = []

    for url in RSS_FEEDS:
        for item in _parse_feed(url, limit, max_age_hours):
            item["source"] = url.split("/")[2]
            collected.append(item)

    for url in JINSE_INFORMATION_URLS:
        for item in _parse_jinse_information(url, limit, max_age_hours):
            item["source"] = "jinse.com.cn"
            collected.append(item)

    for item in _parse_jinse_lives(JINSE_LIVES_URL, limit, max_age_hours):
        item["source"] = "jinse.com.cn"
        collected.append(item)

    collected.sort(key=lambda x: x.get("time") or "", reverse=True)
    return collected[:limit]
