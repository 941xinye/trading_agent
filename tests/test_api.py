"""本地 HTTP API 测试：路由分发、鉴权与参数解析。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from crypto_analyst import api, config


def test_health_ok(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    status, _ctype, payload = api.dispatch("GET", "/health", {}, b"", {})
    data = json.loads(payload)
    assert status == 200
    assert data["status"] == "ok"
    assert data["exchange"] == config.EXCHANGE


def test_health_always_public(monkeypatch):
    # /health 始终公开，便于容器健康检查；即使配置了 API_TOKEN 也不鉴权
    monkeypatch.setattr(config, "API_TOKEN", "s3cret")
    assert api.dispatch("GET", "/health", {}, b"", {})[0] == 200
    # 其他接口仍需鉴权
    assert api.dispatch("GET", "/snapshot", {}, b"", {})[0] == 401
    assert api.dispatch("GET", "/snapshot", {}, b"", {"x-api-token": "s3cret"})[0] != 401


def test_snapshot_passes_symbols_and_source(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    captured = {}
    stub = SimpleNamespace(symbols=[object()], warnings=["缺口"], generated_at="now", to_dict=lambda: {"ok": True})

    def fake_build(**kwargs):
        captured.update(kwargs)
        return stub

    monkeypatch.setattr(api.analysis, "build_snapshot", fake_build)
    status, _ctype, payload = api.dispatch("GET", "/snapshot", {"symbols": ["btc,eth"]}, b"", {})
    assert status == 200
    assert json.loads(payload) == {"ok": True}
    assert captured["symbols"] == ["BTC", "ETH"]
    assert captured["source"] == "api"
    assert captured["include_news"] is False


def test_dashboard_renders_html(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(api.dashboard, "render", lambda days, source: f"<html>{days}-{source}</html>")
    status, ctype, payload = api.dispatch(
        "GET", "/dashboard", {"days": ["3"], "source": ["watch"]}, b"", {}
    )
    assert status == 200
    assert ctype.startswith("text/html")
    assert payload == "<html>3-watch</html>"


def test_dashboard_rejects_invalid_days(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    assert api.dispatch("GET", "/dashboard", {"days": ["abc"]}, b"", {})[0] == 400


def test_brief_returns_markdown(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    stub = SimpleNamespace(symbols=[object()], warnings=[], generated_at="now")
    monkeypatch.setattr(
        api.briefing,
        "build",
        lambda symbols, use_llm: (stub, "# md", "点评", "reports/a.md"),
    )
    status, _ctype, payload = api.dispatch("GET", "/brief", {"llm": ["0"]}, b"", {})
    data = json.loads(payload)
    assert status == 200
    assert data["markdown"] == "# md"
    assert data["path"] == "reports/a.md"
    assert data["commentary"] == "点评"


def test_ask_requires_question(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    assert api.dispatch("POST", "/ask", {}, b"{}", {})[0] == 400


def test_ask_returns_answer(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")

    class FakeAnalyst:
        def ask(self, question, symbols=None):
            return f"Q={question};S={symbols}"

    monkeypatch.setattr(api, "MarketAnalyst", FakeAnalyst)
    body = json.dumps({"question": "BTC?", "symbols": "btc,eth"}).encode("utf-8")
    status, _ctype, payload = api.dispatch("POST", "/ask", {}, body, {})
    assert status == 200
    assert json.loads(payload)["answer"] == "Q=BTC?;S=['BTC', 'ETH']"


def test_ask_rejects_when_concurrent_limit_reached(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(api, "_ask_in_flight", api.MAX_CONCURRENT_ASK)

    body = json.dumps({"question": "BTC?"}).encode("utf-8")
    status, _ctype, payload = api.dispatch("POST", "/ask", {}, body, {})
    assert status == 429
    assert "繁忙" in json.loads(payload)["error"]


def test_ask_returns_504_on_timeout(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")

    def raise_timeout(question, symbols):
        raise api._AskTimeout()

    monkeypatch.setattr(api, "_ask_with_timeout", raise_timeout)
    body = json.dumps({"question": "BTC?"}).encode("utf-8")
    status, _ctype, payload = api.dispatch("POST", "/ask", {}, body, {})
    assert status == 504
    assert "超时" in json.loads(payload)["error"]


def test_unknown_route(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    assert api.dispatch("GET", "/nope", {}, b"", {})[0] == 404
