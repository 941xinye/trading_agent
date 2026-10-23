"""情绪与消息面：恐慌贪婪指数 + 英文 RSS 快讯（可自行替换为中文源）。"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .. import net

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

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


def _parse_feed(url: str, limit: int, max_age_hours: int) -> list[dict[str, Any]]:
    try:
        raw = net.get_text(url, ttl=900, retries=1)
        root = ET.fromstring(raw)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict[str, Any]] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = (node.findtext("pubDate") or "").strip()
        if not title:
            continue
        ts = None
        if published:
            try:
                dt = parsedate_to_datetime(published)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                ts = dt.astimezone().strftime("%m-%d %H:%M")
            except (TypeError, ValueError):
                pass
        items.append({"title": title, "link": link, "time": ts})
        if len(items) >= limit:
            break
    return items


def news(limit: int = 8, max_age_hours: int = 48) -> list[dict[str, Any]]:
    """聚合 RSS 快讯，按发布时间倒序。"""
    collected: list[dict[str, Any]] = []
    for url in RSS_FEEDS:
        source = url.split("/")[2]
        for item in _parse_feed(url, limit, max_age_hours):
            item["source"] = source
            collected.append(item)
    collected.sort(key=lambda x: x.get("time") or "", reverse=True)
    return collected[:limit]
