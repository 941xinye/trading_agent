"""Binance 公共接口（无需 Key）。

现货走 data-api.binance.vision，该域名在国内可直连；永续走 fapi.binance.com，
若该域名不可达（国内多数网络）则 derivatives 返回 None，持仓面自动降级为中性评分。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from ... import config, net
from .base import Candle

NAME = "Binance"
SPOT_BASE = config.BINANCE_SPOT_BASE
FUTURES_BASE = config.BINANCE_FUTURES_BASE

# Binance 永续默认 8 小时结算一次
FUNDING_INTERVAL_HOURS = 8.0
# 永续域名可达性探测结果缓存（秒）：不可达时整个进程内直接降级，避免逐标的重试等待
_FUTURES_PROBE_TTL = 600
_futures_state = {"checked_at": 0.0, "ok": False}
_probe_lock = threading.Lock()


def pair(symbol: str) -> str:
    return f"{symbol.upper()}{config.QUOTE}"


def source_note() -> str | None:
    if _futures_ready():
        return None
    return "永续接口不可达，持仓面按中性处理"


def candles(symbol: str, interval: str = "4h", limit: int = 300) -> list[Candle]:
    """现货K线，按时间升序返回。"""
    rows = net.get_json(
        f"{SPOT_BASE}/api/v3/klines",
        {"symbol": pair(symbol), "interval": interval, "limit": limit},
        ttl=45,
    )
    out = [
        Candle(
            ts=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]),
        )
        for row in rows
    ]
    out.sort(key=lambda c: c.ts)
    return out


def ticker(symbol: str) -> dict[str, Any]:
    row = net.get_json(
        f"{SPOT_BASE}/api/v3/ticker/24hr", {"symbol": pair(symbol)}, ttl=20
    )
    if not isinstance(row, dict) or "lastPrice" not in row:
        raise RuntimeError(f"未获取到 {symbol} 行情")
    return {
        "symbol": symbol,
        "price": float(row["lastPrice"]),
        "change_24h_pct": float(row["priceChangePercent"]),
        "high_24h": float(row["highPrice"]),
        "low_24h": float(row["lowPrice"]),
        "quote_volume_24h": float(row["quoteVolume"]),
    }


def funding_history(symbol: str, limit: int = 270) -> list[float]:
    """历史资金费率（8小时结算一次），约 270 次覆盖 90 天，用于计算拥挤度分位。"""
    rows = net.get_json(
        f"{FUTURES_BASE}/fapi/v1/fundingRate",
        {"symbol": pair(symbol), "limit": limit},
        ttl=900,
    )
    return [
        float(row["fundingRate"])
        for row in rows
        if row.get("fundingRate") not in (None, "")
    ]


def _ratio(rows: Any, field: str) -> float | None:
    """从多空比序列里取最新一条。"""
    if not isinstance(rows, list) or not rows:
        return None
    row = max(rows, key=lambda item: int(item.get("timestamp") or 0))
    value = row.get(field)
    return float(value) if value not in (None, "") else None


def _futures_ready() -> bool:
    """探测永续域名是否可达，结果缓存 10 分钟，避免国内网络下反复超时等待。"""
    now = time.time()
    if now - _futures_state["checked_at"] < _FUTURES_PROBE_TTL:
        return _futures_state["ok"]
    with _probe_lock:
        now = time.time()
        if now - _futures_state["checked_at"] < _FUTURES_PROBE_TTL:
            return _futures_state["ok"]
        try:
            net.get_json(f"{FUTURES_BASE}/fapi/v1/ping", ttl=0, retries=0, timeout=5)
            ok = True
        except RuntimeError:
            ok = False
        _futures_state.update(checked_at=now, ok=ok)
        return ok


def derivatives(symbol: str) -> dict[str, Any] | None:
    """永续合约持仓结构：资金费率、持仓量、多空账户比、大户多空比。"""
    if not _futures_ready():
        return None  # 永续域名不可达，持仓面降级为中性
    p = pair(symbol)
    try:
        premium = net.get_json(
            f"{FUTURES_BASE}/fapi/v1/premiumIndex", {"symbol": p}, ttl=60
        )
        oi_rows = net.get_json(
            f"{FUTURES_BASE}/futures/data/openInterestHist",
            {"symbol": p, "period": "5m", "limit": 288},
            ttl=120,
        )
        price_rows = net.get_json(
            f"{FUTURES_BASE}/fapi/v1/klines",
            {"symbol": p, "interval": "5m", "limit": 13},
            ttl=60,
        )
        account_rows = net.get_json(
            f"{FUTURES_BASE}/futures/data/globalLongShortAccountRatio",
            {"symbol": p, "period": "5m", "limit": 1},
            ttl=120,
        )
        top_account_rows = net.get_json(
            f"{FUTURES_BASE}/futures/data/topLongShortAccountRatio",
            {"symbol": p, "period": "5m", "limit": 1},
            ttl=120,
        )
        top_position_rows = net.get_json(
            f"{FUTURES_BASE}/futures/data/topLongShortPositionRatio",
            {"symbol": p, "period": "5m", "limit": 1},
            ttl=120,
        )
        taker_rows = net.get_json(
            f"{FUTURES_BASE}/futures/data/takerlongshortRatio",
            {"symbol": p, "period": "5m", "limit": 1},
            ttl=120,
        )
    except RuntimeError:
        return None  # 永续接口不可达或该标的无永续

    funding_rate = float(premium.get("lastFundingRate") or 0.0)
    periods_per_year = 365 * 24 / FUNDING_INTERVAL_HOURS
    mark_price = float(premium.get("markPrice") or 0.0)
    index_price = float(premium.get("indexPrice") or 0.0)

    stats = sorted(oi_rows, key=lambda item: int(item.get("timestamp") or 0))
    latest = stats[-1] if stats else {}

    def _change(field: str, index: int) -> float | None:
        if len(stats) <= abs(index):
            return None
        prev = float(stats[index].get(field) or 0.0)
        current = float(latest.get(field) or 0.0)
        if not prev:
            return None
        return (current / prev - 1) * 100.0

    price_change_1h = None
    if len(price_rows) >= 13:
        first = float(price_rows[-13][4])
        last = float(price_rows[-1][4])
        if first:
            price_change_1h = (last / first - 1) * 100.0

    funding_rate_history = sorted(funding_history(symbol))
    percentile = None
    if funding_rate_history:
        below = sum(1 for rate in funding_rate_history if rate <= funding_rate)
        percentile = below / len(funding_rate_history) * 100.0

    return {
        "funding_rate": funding_rate,
        "funding_interval_hours": FUNDING_INTERVAL_HOURS,
        "funding_annualized_pct": funding_rate * periods_per_year * 100.0,
        "funding_percentile": percentile,
        "mark_price": mark_price,
        "index_price": index_price,
        "basis_pct": ((mark_price / index_price - 1) * 100.0) if index_price else None,
        "open_interest_usd": float(latest.get("sumOpenInterestValue") or 0.0),
        "oi_change_1h_pct": _change("sumOpenInterestValue", -12),
        "oi_change_24h_pct": _change("sumOpenInterestValue", 0),
        "price_change_1h_pct": price_change_1h,
        "lsr_account": _ratio(account_rows, "longShortRatio"),
        "lsr_taker": _ratio(taker_rows, "buySellRatio"),
        "top_lsr_account": _ratio(top_account_rows, "longShortRatio"),
        "top_lsr_size": _ratio(top_position_rows, "longShortRatio"),
        # Binance 公共 REST 不提供逐笔爆仓数据
        "long_liq_usd_1m": None,
        "short_liq_usd_1m": None,
    }
