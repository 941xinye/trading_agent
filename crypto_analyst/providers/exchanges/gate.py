"""Gate.io 公共接口（国内可直连，无需 Key）。"""
from __future__ import annotations

from typing import Any

from ... import config, net
from .base import Candle

NAME = "Gate.io"
BASE = "https://api.gateio.ws/api/v4"


def source_note() -> str | None:
    return None


def pair(symbol: str) -> str:
    return f"{symbol.upper()}_{config.QUOTE}"


def candles(symbol: str, interval: str = "4h", limit: int = 300) -> list[Candle]:
    """现货K线，按时间升序返回。"""
    rows = net.get_json(
        f"{BASE}/spot/candlesticks",
        {"currency_pair": pair(symbol), "interval": interval, "limit": limit},
        ttl=45,
    )
    out: list[Candle] = []
    for row in rows:
        # [时间戳, 计价币成交额, 收盘, 最高, 最低, 开盘, 基础币成交量, 是否收盘]
        ts, quote_volume, close, high, low, open_, volume = row[:7]
        out.append(
            Candle(
                ts=int(ts),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
                quote_volume=float(quote_volume),
            )
        )
    out.sort(key=lambda c: c.ts)
    return out


def ticker(symbol: str) -> dict[str, Any]:
    rows = net.get_json(
        f"{BASE}/spot/tickers", {"currency_pair": pair(symbol)}, ttl=20
    )
    if not rows:
        raise RuntimeError(f"未获取到 {symbol} 行情")
    row = rows[0]
    return {
        "symbol": symbol,
        "price": float(row["last"]),
        "change_24h_pct": float(row["change_percentage"]),
        "high_24h": float(row["high_24h"]),
        "low_24h": float(row["low_24h"]),
        "quote_volume_24h": float(row["quote_volume"]),
    }


def funding_history(symbol: str, limit: int = 270) -> list[float]:
    """历史资金费率（8小时结算一次），约 270 次覆盖 90 天，用于计算拥挤度分位。"""
    rows = net.get_json(
        f"{BASE}/futures/usdt/funding_rate",
        {"contract": pair(symbol), "limit": limit},
        ttl=900,
    )
    return [float(row["r"]) for row in rows if row.get("r") not in (None, "")]


def derivatives(symbol: str) -> dict[str, Any] | None:
    """永续合约持仓结构：资金费率、持仓量、多空账户比、大户多空比、爆仓量。"""
    p = pair(symbol)
    try:
        contract = net.get_json(f"{BASE}/futures/usdt/contracts/{p}", ttl=60)
        stats = net.get_json(
            f"{BASE}/futures/usdt/contract_stats",
            {"contract": p, "interval": "5m", "limit": 288},
            ttl=120,
        )
    except RuntimeError:
        return None  # 该标的不支持永续合约

    funding_rate = float(contract.get("funding_rate") or 0.0)
    interval = float(contract.get("funding_interval") or 28800)
    periods_per_year = (365 * 24 * 3600) / interval
    last_price = float(contract.get("last_price") or 0.0)
    index_price = float(contract.get("index_price") or 0.0)

    latest = stats[-1] if stats else {}

    def _change(field: str, index: int) -> float | None:
        if len(stats) <= abs(index):
            return None
        prev = float(stats[index].get(field) or 0.0)
        current = float(latest.get(field) or 0.0)
        if not prev:
            return None
        return (current / prev - 1) * 100.0

    oi_usd = float(latest.get("open_interest_usd") or 0.0)

    history = funding_history(symbol)
    history_sorted = sorted(history)
    percentile = None
    if history_sorted:
        below = sum(1 for r in history_sorted if r <= funding_rate)
        percentile = below / len(history_sorted) * 100.0

    return {
        "funding_rate": funding_rate,
        "funding_interval_hours": round(interval / 3600, 1),
        "funding_annualized_pct": funding_rate * periods_per_year * 100.0,
        "funding_percentile": percentile,
        "mark_price": float(contract.get("mark_price") or 0.0),
        "index_price": index_price,
        "basis_pct": ((last_price / index_price - 1) * 100.0) if index_price else None,
        "open_interest_usd": oi_usd,
        "oi_change_1h_pct": _change("open_interest_usd", -12),
        "oi_change_24h_pct": _change("open_interest_usd", 0),
        "price_change_1h_pct": _change("mark_price", -12),
        "lsr_account": latest.get("lsr_account"),
        "lsr_taker": latest.get("lsr_taker"),
        "top_lsr_account": latest.get("top_lsr_account"),
        "top_lsr_size": latest.get("top_lsr_size"),
        "long_users": contract.get("long_users"),
        "short_users": contract.get("short_users"),
        "long_liq_usd_1m": latest.get("long_liq_usd"),
        "short_liq_usd_1m": latest.get("short_liq_usd"),
    }
