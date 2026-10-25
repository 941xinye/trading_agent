"""情绪与消息面测试：RSS/Atom 解析、金色财经 JSON/HTML 解析、聚合。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from crypto_analyst.providers import sentiment

# 构造一个"刚刚"的 RFC 2822 时间，确保不过期
_NOW = datetime.now(timezone.utc)
_RFC2822_NOW = _NOW.strftime("%a, %d %b %Y %H:%M:%S +0000")
_ISO_NOW = _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
_TS_NOW = _NOW.astimezone().strftime("%m-%d %H:%M")
_EPOCH_NOW = int(_NOW.timestamp())

_RSS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>比特币突破新高</title><link>https://example.com/btc</link><pubDate>{_RFC2822_NOW}</pubDate></item>
<item><title>以太坊升级完成</title><link>https://example.com/eth</link><pubDate>{_RFC2822_NOW}</pubDate></item>
</channel></rss>"""

_ATOM_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Solana 生态爆发</title><link href="https://example.com/sol"/><published>{_ISO_NOW}</published></entry>
<entry><title>Layer2 交易量创新高</title><link href="https://example.com/l2"/><updated>{_ISO_NOW}</updated></entry>
</feed>"""

_JINSE_JSON = {
    "list": [
        {"id": 1, "title": "金色头条：BTC 创新高", "extra": {"published_at": _EPOCH_NOW, "topic_url": "https://www.jinse.com.cn/1.html"}},
        {"id": 2, "title": "", "extra": {"published_at": _EPOCH_NOW}},  # 空标题跳过
        {"id": 3, "title": "DeFi 总锁仓量回升", "extra": {"published_at": _EPOCH_NOW, "topic_url": "https://www.jinse.com.cn/3.html"}},
    ]
}

_JINSE_LIVES_HTML = f"""<div class="js-lives js-lives__item"><p class="select-alerts-border"></p>
<div class="intro"><div class="time">
      23:55
    </div> <div class="content"><!---->
<a href="/lives/537479.html" target="_blank" class="title"><!---->
        Initia链疑似遭攻击，黑客已套现约260万美元
      </a></div></div></div>
<div class="js-lives js-lives__item"><div class="intro"><div class="time">
      23:33
    </div> <div class="content">
<a href="/lives/537480.html" target="_blank" class="title"><!---->
        Bankless创始人：比特币克隆币再度走热
      </a></div></div></div>"""


def test_parse_feed_rss_items(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_text", lambda *a, **k: _RSS_XML)
    items = sentiment._parse_feed("https://example.com/rss", limit=5, max_age_hours=48)
    assert len(items) == 2
    assert items[0]["title"] == "比特币突破新高"
    assert items[0]["link"] == "https://example.com/btc"
    assert items[0]["time"] == _TS_NOW


def test_parse_feed_atom_entries(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_text", lambda *a, **k: _ATOM_XML)
    items = sentiment._parse_feed("https://example.com/atom", limit=5, max_age_hours=48)
    assert len(items) == 2
    assert items[0]["title"] == "Solana 生态爆发"
    assert items[0]["link"] == "https://example.com/sol"
    assert items[0]["time"] == _TS_NOW


def test_parse_feed_malformed_returns_empty(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_text", lambda *a, **k: "not xml")
    items = sentiment._parse_feed("https://example.com/bad", limit=5, max_age_hours=48)
    assert items == []


def test_parse_feed_filters_old_items(monkeypatch):
    old = (_NOW - timedelta(hours=100)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    xml = f"""<?xml version="1.0"?><rss><channel>
<item><title>旧闻</title><link>x</link><pubDate>{old}</pubDate></item>
</channel></rss>"""
    monkeypatch.setattr(sentiment.net, "get_text", lambda *a, **k: xml)
    items = sentiment._parse_feed("https://example.com/rss", limit=5, max_age_hours=48)
    assert items == []


def test_parse_jinse_information(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_json", lambda *a, **k: _JINSE_JSON)
    items = sentiment._parse_jinse_information("https://www.jinse.com.cn/api/...", limit=5, max_age_hours=48)
    assert len(items) == 2  # 空标题被跳过
    assert items[0]["title"] == "金色头条：BTC 创新高"
    assert items[0]["link"] == "https://www.jinse.com.cn/1.html"
    assert items[0]["time"] == _TS_NOW


def test_parse_jinse_information_empty(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_json", lambda *a, **k: {"list": []})
    items = sentiment._parse_jinse_information("https://www.jinse.com.cn/api/...", limit=5, max_age_hours=48)
    assert items == []


def test_parse_jinse_information_api_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(sentiment.net, "get_json", boom)
    items = sentiment._parse_jinse_information("https://www.jinse.com.cn/api/...", limit=5, max_age_hours=48)
    assert items == []


def test_parse_jinse_information_filters_old(monkeypatch):
    old_epoch = int((_NOW - timedelta(hours=100)).timestamp())
    data = {"list": [{"id": 1, "title": "旧闻", "extra": {"published_at": old_epoch}}]}
    monkeypatch.setattr(sentiment.net, "get_json", lambda *a, **k: data)
    items = sentiment._parse_jinse_information("https://www.jinse.com.cn/api/...", limit=5, max_age_hours=48)
    assert items == []


def test_parse_jinse_lives(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_text", lambda *a, **k: _JINSE_LIVES_HTML)
    items = sentiment._parse_jinse_lives("https://www.jinse.com.cn/lives", limit=5, max_age_hours=48)
    assert len(items) == 2
    assert items[0]["title"] == "Initia链疑似遭攻击，黑客已套现约260万美元"
    assert items[0]["link"] == "https://www.jinse.com.cn/lives/537479.html"
    # 今日时间，格式为 MM-DD HH:MM
    assert items[0]["time"].endswith("23:55")


def test_parse_jinse_lives_no_match(monkeypatch):
    monkeypatch.setattr(sentiment.net, "get_text", lambda *a, **k: "<html><body>no lives here</body></html>")
    items = sentiment._parse_jinse_lives("https://www.jinse.com.cn/lives", limit=5, max_age_hours=48)
    assert items == []


def test_parse_jinse_lives_fetch_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(sentiment.net, "get_text", boom)
    items = sentiment._parse_jinse_lives("https://www.jinse.com.cn/lives", limit=5, max_age_hours=48)
    assert items == []


def test_news_aggregates_all_sources(monkeypatch):
    def fake_get_text(url, *a, **k):
        if "jinse.com.cn/lives" in url:
            return _JINSE_LIVES_HTML
        return _RSS_XML

    def fake_get_json(url, *a, **k):
        return _JINSE_JSON

    monkeypatch.setattr(sentiment.net, "get_text", fake_get_text)
    monkeypatch.setattr(sentiment.net, "get_json", fake_get_json)

    items = sentiment.news(limit=20, max_age_hours=48)
    # RSS 2 feeds × 2 items + jinse info 2 urls × 2 items + jinse lives 2 = 10
    assert len(items) == 10
    sources = {it["source"] for it in items}
    assert "jinse.com.cn" in sources
    assert "cointelegraph.com" in sources
    assert "decrypt.co" in sources


def test_news_respects_limit(monkeypatch):
    def fake_get_text(url, *a, **k):
        return _RSS_XML

    def fake_get_json(url, *a, **k):
        return _JINSE_JSON

    monkeypatch.setattr(sentiment.net, "get_text", fake_get_text)
    monkeypatch.setattr(sentiment.net, "get_json", fake_get_json)

    items = sentiment.news(limit=3, max_age_hours=48)
    assert len(items) == 3


def test_parse_time_rfc2822():
    cutoff = _NOW - timedelta(hours=48)
    ts = sentiment._parse_time(_RFC2822_NOW, cutoff, rfc2822=True)
    assert ts == _TS_NOW


def test_parse_time_iso8601():
    cutoff = _NOW - timedelta(hours=48)
    ts = sentiment._parse_time(_ISO_NOW, cutoff, rfc2822=False)
    assert ts == _TS_NOW


def test_parse_time_expired_returns_skip():
    old = (_NOW - timedelta(hours=100)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    cutoff = _NOW - timedelta(hours=48)
    ts = sentiment._parse_time(old, cutoff, rfc2822=True)
    assert ts is sentiment._SKIP


def test_parse_time_invalid_returns_none():
    cutoff = _NOW - timedelta(hours=48)
    assert sentiment._parse_time("not a date", cutoff, rfc2822=True) is None
    assert sentiment._parse_time("", cutoff, rfc2822=True) is None


def test_parse_time_naive_treated_as_utc():
    # 无时区的 ISO 时间按 UTC 处理
    naive = _NOW.strftime("%Y-%m-%dT%H:%M:%S")
    cutoff = _NOW - timedelta(hours=48)
    ts = sentiment._parse_time(naive, cutoff, rfc2822=False)
    assert ts == _TS_NOW
