"""告警引擎测试：加签、冷却、卡片构建与推送降级。"""
from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from crypto_analyst import alerts, config


def test_sign_matches_feishu_algorithm():
    timestamp, secret = "1700000000", "test-secret"
    expected = base64.b64encode(
        hmac.new(f"{timestamp}\n{secret}".encode("utf-8"), digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    assert alerts._sign(timestamp, secret) == expected


def test_push_uses_feishu_text_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/x")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(alerts, "_send", lambda payload: captured.update(payload) or True)
    assert alerts.push("hello") is True
    assert captured == {"msg_type": "text", "content": {"text": "hello"}}


def test_push_uses_dingtalk_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=x")
    monkeypatch.setattr(alerts, "_send", lambda payload: captured.update(payload) or True)
    alerts.push("hello")
    assert captured == {"msgtype": "text", "text": {"content": "hello"}}


def test_push_card_falls_back_to_text_for_dingtalk(monkeypatch, snapshot_factory):
    captured = {}
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=x")
    monkeypatch.setattr(alerts, "_send", lambda payload: captured.update(payload) or True)
    card = alerts.build_brief_card(snapshot_factory(), commentary="点评正文")
    assert alerts.push_card(card) is True
    assert captured["msgtype"] == "text"
    assert "加密市场行情简报" in captured["text"]["content"]


def test_push_card_uses_interactive_for_feishu(monkeypatch, snapshot_factory):
    captured = {}
    monkeypatch.setattr(config, "WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/x")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(alerts, "_send", lambda payload: captured.update(payload) or True)
    alerts.push_card(alerts.build_brief_card(snapshot_factory()))
    assert captured["msg_type"] == "interactive"
    assert captured["card"]["header"]["title"]["content"].startswith("加密市场行情简报")


def test_send_adds_signature_when_secret_configured(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"code": 0}

    def fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        return _Resp()

    monkeypatch.setattr(config, "WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/x")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(alerts.requests, "post", fake_post)

    assert alerts._send({"msg_type": "text", "content": {"text": "x"}}) is True
    assert "timestamp" in captured and "sign" in captured
    assert captured["sign"] == alerts._sign(captured["timestamp"], "s3cret")


def test_card_contains_symbol_and_truncated_commentary(snapshot_factory):
    long_comment = "第一句话说明趋势。" * 200
    card = alerts.build_brief_card(snapshot_factory(), commentary=long_comment)
    texts = [element["text"]["content"] for element in card["elements"] if element.get("text")]
    joined = "\n".join(texts)
    assert "**BTC**" in joined
    assert "分析师点评" in joined
    commentary_block = next(text for text in texts if "分析师点评" in text)
    assert len(commentary_block) < 700
    assert commentary_block.endswith("…")


def test_truncate_keeps_short_text_unchanged():
    assert alerts._truncate("很短的点评。", 600) == "很短的点评。"


def test_truncate_cuts_at_sentence_boundary():
    text = "甲" * 400 + "。" + "乙" * 400
    result = alerts._truncate(text, 600)
    assert result.endswith("。…")
    assert len(result) <= 601


def test_previous_regimes_ignores_other_sources(monkeypatch):
    monkeypatch.setattr(
        alerts.history,
        "iter_records",
        lambda: iter(
            [
                {"symbol": "BTC", "regime": "偏空", "source": "watch"},
                {"symbol": "BTC", "regime": "震荡", "source": "snapshot"},
                {"symbol": "BTC", "regime": "强势多头", "source": "watch"},
            ]
        ),
    )
    assert alerts._previous_regimes("watch") == {"BTC": "偏空"}


def test_cooldown_blocks_repeat_and_prefixes_exchange(monkeypatch, snapshot_factory):
    saved: dict[str, float] = {}
    monkeypatch.setattr(alerts, "_load_state", lambda: dict(saved))
    monkeypatch.setattr(alerts, "_save_state", lambda data: saved.update(data))
    monkeypatch.setattr(alerts.history, "iter_records", lambda: iter([]))

    snapshot = snapshot_factory(change_24h_pct=12.0)
    first = alerts.evaluate(snapshot)
    assert any(item["key"] == "BTC:move24h" for item in first)
    assert all(key.startswith(f"{config.EXCHANGE}:") for key in saved)

    second = alerts.evaluate(snapshot)
    assert second == []
