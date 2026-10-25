"""Agent 工具层测试：符号归一化、作用域、快照缓存与派发异常处理。"""
from __future__ import annotations

import time

from crypto_analyst import analysis, tools


def test_normalize_symbol_strips_quote_and_separators(monkeypatch):
    monkeypatch.setattr(tools.config, "QUOTE", "USDT")
    assert tools._normalize_symbol("btc") == "BTC"
    assert tools._normalize_symbol("ETH/USDT") == "ETH"
    assert tools._normalize_symbol("sol-usdt") == "SOL"
    assert tools._normalize_symbol("BTC_USDT") == "BTC"


def test_set_symbol_scope_returns_previous_and_sets_new():
    old = tools.set_symbol_scope(["BTC", "ETH"])
    assert old is None  # 初始为 None
    assert tools._SCOPE == ["BTC", "ETH"]

    restored = tools.set_symbol_scope(["SOL"])
    assert restored == ["BTC", "ETH"]
    assert tools._SCOPE == ["SOL"]

    # 恢复初始状态
    tools.set_symbol_scope(None)


def test_get_snapshot_caches_within_ttl(monkeypatch, sample_snapshot):
    calls = {"count": 0}

    def fake_build(**kwargs):
        calls["count"] += 1
        return sample_snapshot

    monkeypatch.setattr(tools.analysis, "build_snapshot", fake_build)
    monkeypatch.setattr(tools, "_snapshot_cache", {"ts": 0.0, "data": None, "scope": None})

    first = tools._get_snapshot()
    second = tools._get_snapshot()

    assert first is sample_snapshot
    assert second is sample_snapshot
    assert calls["count"] == 1  # TTL 内只采集一次


def test_get_snapshot_invalidates_on_scope_change(monkeypatch, sample_snapshot):
    calls = {"count": 0}

    def fake_build(**kwargs):
        calls["count"] += 1
        return sample_snapshot

    monkeypatch.setattr(tools.analysis, "build_snapshot", fake_build)
    monkeypatch.setattr(tools, "_snapshot_cache", {"ts": time.time(), "data": sample_snapshot, "scope": None})

    # scope 从 None 变为 ["BTC"] 应触发重建
    tools.set_symbol_scope(["BTC"])
    try:
        tools._get_snapshot()
        assert calls["count"] == 1
    finally:
        tools.set_symbol_scope(None)


def test_dispatch_unknown_tool_returns_error():
    result = tools.dispatch("nonexistent_tool", {})
    assert "error" in result
    assert "未知工具" in result["error"]


def test_dispatch_handles_type_error(monkeypatch):
    def handler(**kwargs):
        raise TypeError("missing required argument: symbol")

    monkeypatch.setitem(tools._HANDLERS, "get_market_overview", handler)
    result = tools.dispatch("get_market_overview", {})
    assert "error" in result
    assert "参数错误" in result["error"]


def test_dispatch_wraps_unexpected_exception(monkeypatch):
    def handler(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(tools._HANDLERS, "get_market_overview", handler)
    result = tools.dispatch("get_market_overview", {})
    assert "error" in result
    assert "RuntimeError" in result["error"]


def test_market_overview_filters_by_symbols(monkeypatch, sample_snapshot):
    monkeypatch.setattr(tools, "_snapshot_cache", {"ts": time.time(), "data": sample_snapshot, "scope": None})

    result = tools._market_overview(symbols=["BTC"])
    assert len(result["symbols"]) == 1
    assert result["symbols"][0]["symbol"] == "BTC"

    # 过滤不存在的标的返回空列表
    result = tools._market_overview(symbols=["DOGE"])
    assert result["symbols"] == []


def test_symbol_analysis_falls_back_to_realtime_for_unwatched(monkeypatch, sample_snapshot):
    monkeypatch.setattr(tools, "_snapshot_cache", {"ts": time.time(), "data": sample_snapshot, "scope": None})

    captured = {}

    def fake_build_symbol(symbol, stable, fng, macro):
        captured["symbol"] = symbol
        return analysis.SymbolSnapshot(
            symbol=symbol, price=1.0, change_24h_pct=0.0, quote_volume_24h=0.0,
            timeframes={}, derivatives=None,
            scores={k: 50.0 for k in ("technical", "positioning", "liquidity", "sentiment", "macro")},
            total_score=50.0, regime="震荡",
        )

    monkeypatch.setattr(tools.analysis, "build_symbol_snapshot", fake_build_symbol)

    result = tools._symbol_analysis("DOGE")
    assert result["symbol"] == "DOGE"
    assert captured["symbol"] == "DOGE"
