"""历史重放：用真实历史数据重建评分快照，供回测与权重推荐取数。

只做离线校准，不写入 `.state/snapshots`。重建口径与实时分析保持一致，
但受数据源限制的部分会降级并在返回结果的 `notes` 中标注：

- 技术面：按 1h/4h/1d 截止该时刻的最后 KLINE_LIMIT 根 K 线重算，完全可复现；
- 持仓面：资金费率历史可重建（Gate 约 90 条 / 30 天，8h 一条）；历史持仓量与
  多空比接口只保留约 24h，回放时按中性处理；
- 流动性：DeFiLlama 稳定币总供应历史，按日回推 7d / 30d 变化；
- 情绪：alternative.me 恐慌贪婪指数历史，按日取最近一次公布值；
- 宏观：东方财富指数日线，按上一交易日收盘回推 5d 变化，避免未来函数。
"""
from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from typing import Any

from . import analysis, config, net
from .providers import macro as macro_provider
from .providers import market
from .providers.exchanges import binance, gate

# 资金费率结算间隔（Gate / Binance 永续均为 8 小时）
FUNDING_INTERVAL_HOURS = 8.0
PERIODS_PER_YEAR = 365 * 24 / FUNDING_INTERVAL_HOURS


def _last_le(ts: list[float], t: float) -> int:
    """返回最后一个 <= t 的下标，没有则 -1。"""
    return bisect_right(ts, t) - 1


def _candles(symbol: str, timeframe: str, limit: int) -> list[market.Candle]:
    return market.candles(symbol, timeframe, limit=limit)


def _funding_points(symbol: str) -> tuple[list[float], list[float]]:
    """资金费率历史，返回 (时间戳, 费率) 两个升序列表。"""
    if config.EXCHANGE == "binance":
        rows = net.get_json(
            f"{binance.FUTURES_BASE}/fapi/v1/fundingRate",
            {"symbol": binance.pair(symbol), "limit": 1000},
            ttl=900,
        )
        points = [
            (float(row["fundingTime"]) / 1000.0, float(row["fundingRate"]))
            for row in rows
            if row.get("fundingTime") and row.get("fundingRate") not in (None, "")
        ]
    else:
        rows = net.get_json(
            f"{gate.BASE}/futures/usdt/funding_rate",
            {"contract": gate.pair(symbol), "limit": 1000},
            ttl=900,
        )
        points = [
            (float(row["t"]), float(row["r"]))
            for row in rows
            if row.get("t") and row.get("r") not in (None, "")
        ]
    points.sort()
    return [p[0] for p in points], [p[1] for p in points]


def _stable_points() -> tuple[list[float], list[float]]:
    """稳定币总供应（USD 锚定）历史，按日。"""
    data = net.get_json(
        "https://stablecoins.llama.fi/stablecoincharts/all",
        {},
        ttl=1800,
        retries=1,
    )
    points: list[tuple[float, float]] = []
    for row in data or []:
        value = (row.get("totalCirculating") or {}).get("peggedUSD")
        if row.get("date") and value:
            points.append((float(row["date"]), float(value)))
    points.sort()
    return [p[0] for p in points], [p[1] for p in points]


def _fng_points() -> tuple[list[float], list[float]]:
    """恐慌贪婪指数历史（最多 2000 天）。"""
    data = net.get_json(
        "https://api.alternative.me/fng/",
        {"limit": 2000},
        ttl=1800,
        retries=1,
    )
    points: list[tuple[float, float]] = []
    for item in (data or {}).get("data") or []:
        try:
            points.append((float(item["timestamp"]), float(item["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort()
    return [p[0] for p in points], [p[1] for p in points]


def _macro_points() -> tuple[list[str], list[dict[str, Any]]]:
    """三大指数的 5 日变化，返回 (日期, 每日宏观快照)，日期升序。

    用上一交易日收盘，避免把当日收盘价当成盘中已知信息。
    """
    closes: dict[str, dict[str, float]] = {}
    for key, (secid, _name) in macro_provider.INDICES.items():
        data = net.get_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {
                "secid": secid,
                "fields1": "f1,f2,f3",
                "fields2": "f51,f53",
                "klt": "101",
                "fqt": "1",
                "end": "20500101",
                "lmt": "200",
            },
            ttl=1800,
            retries=1,
        )
        klines = ((data or {}).get("data") or {}).get("klines") or []
        for row in klines:
            parts = row.split(",")
            if len(parts) < 2:
                continue
            try:
                closes.setdefault(key, {})[parts[0]] = float(parts[1])
            except ValueError:
                continue

    all_dates = sorted({date for series in closes.values() for date in series})
    entries: list[dict[str, Any]] = []
    for date in all_dates:
        day: dict[str, Any] = {}
        for key, series in closes.items():
            dates = sorted(series)
            i = bisect_left(dates, date)
            if i < 5 or i >= len(dates) or dates[i] != date:
                continue
            prev = series[dates[i - 5]]
            if prev:
                day[key] = {"change_5d_pct": (series[date] / prev - 1) * 100.0}
        entries.append(day)
    return all_dates, entries


def funding_at(ts: list[float], rates: list[float], t: float) -> dict[str, Any]:
    i = _last_le(ts, t)
    if i < 0:
        return {}
    return {"funding_annualized_pct": rates[i] * PERIODS_PER_YEAR * 100.0}


def stable_at(ts: list[float], totals: list[float], t: float) -> dict[str, Any]:
    i = _last_le(ts, t)
    if i < 0:
        return {}
    out: dict[str, Any] = {}
    if i >= 7 and totals[i - 7]:
        out["change_7d_pct"] = (totals[i] / totals[i - 7] - 1) * 100.0
    if i >= 30 and totals[i - 30]:
        out["change_30d_pct"] = (totals[i] / totals[i - 30] - 1) * 100.0
    return out


def sentiment_at(ts: list[float], values: list[float], t: float) -> dict[str, Any]:
    i = _last_le(ts, t)
    if i < 0:
        return {}
    return {"value": values[i]}


def macro_at(dates: list[str], entries: list[dict[str, Any]], t: float) -> dict[str, Any]:
    """取本地日期之前最近的交易日，避免使用当日尚未收盘的数据。"""
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    i = bisect_left(dates, day) - 1
    return entries[i] if i >= 0 else {}


def replay_timestamps(last_ts: int, days: float, step_hours: float) -> list[int]:
    """从最后一个已收盘 K 线倒推 days 天，按 step_hours 对齐采样。"""
    step = max(1, int(step_hours * 3600))
    end = (int(last_ts) // step) * step
    start = (int(end - days * 86400) // step) * step
    return list(range(start, end + 1, step))


def _slice(candles: list[market.Candle], ts: list[float], t: int, limit: int) -> list[market.Candle]:
    """只取截止 t 已收盘的 K 线（ts < t），等价实时看到的历史窗口。"""
    end = bisect_left(ts, t)
    if end <= 0:
        return []
    return candles[max(0, end - limit) : end]


def _limit_for(timeframe: str) -> int:
    # 1h / 4h 多取一些，保证回放起点也有足够预热窗口；日线用配置值即可
    if timeframe in ("1h", "4h"):
        return max(config.KLINE_LIMIT, 1000)
    return max(config.KLINE_LIMIT, 300)


def build_records(
    symbols: list[str] | None = None,
    days: float = 28.0,
    step_hours: float = 4.0,
) -> list[dict[str, Any]]:
    """重建历史评分记录，字段与 history.append_snapshot 保持一致。"""
    symbols = [s.upper() for s in (symbols or config.SYMBOLS)]
    stable_ts, stable_totals = _stable_points()
    fng_ts, fng_values = _fng_points()
    macro_dates, macro_entries = _macro_points()

    per_symbol: dict[str, dict[str, Any]] = {}
    last_ts: int | None = None
    for symbol in symbols:
        candles = {
            timeframe: _candles(symbol, timeframe, _limit_for(timeframe))
            # 回放不重建 5m 等短周期：交易所单次最多 1000 根（5m 仅约 3.5 天），
            # 覆盖不了长回放窗口，且窗口间口径必须一致
            for timeframe in config.TIMEFRAMES
            if timeframe in ("1h", "4h", "1d")
        }
        if "1h" not in candles or not candles["1h"]:
            continue
        ts_1h = [c.ts for c in candles["1h"]]
        symbol_last = ts_1h[-1]
        last_ts = symbol_last if last_ts is None else min(last_ts, symbol_last)
        per_symbol[symbol] = {
            "candles": candles,
            "ts": {tf: [c.ts for c in series] for tf, series in candles.items()},
            "funding": _funding_points(symbol),
        }

    if not per_symbol or last_ts is None:
        return []

    timestamps = replay_timestamps(last_ts, days, step_hours)
    records: list[dict[str, Any]] = []
    for t in timestamps:
        stable = stable_at(stable_ts, stable_totals, t)
        fng = sentiment_at(fng_ts, fng_values, t)
        macro_data = macro_at(macro_dates, macro_entries, t)
        liquidity = analysis._liquidity_score(stable)
        sentiment_score = analysis._sentiment_score(fng)
        macro_score = analysis._macro_score(macro_data)

        for symbol, payload in per_symbol.items():
            views: dict[str, analysis.TimeframeView] = {}
            price: float | None = None
            for timeframe, series in payload["candles"].items():
                window = _slice(series, payload["ts"][timeframe], t, _limit_for(timeframe))
                if len(window) < 30:
                    views = {}
                    break
                views[timeframe] = analysis.build_timeframe_view(timeframe, window)
                if timeframe == "1h":
                    price = window[-1].close
            if not views or price is None:
                continue

            funding = funding_at(*payload["funding"], t)
            scores = {
                "technical": analysis._technical_score(views),
                "positioning": analysis._positioning_score(funding),
                "liquidity": liquidity,
                "sentiment": sentiment_score,
                "macro": macro_score,
            }
            total = sum(scores[key] * weight for key, weight in config.DIMENSION_WEIGHTS.items())
            records.append(
                {
                    "epoch": float(t),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)),
                    "source": "replay",
                    "symbol": symbol,
                    "price": price,
                    "total_score": total,
                    "regime": analysis.regime_of(total),
                    "scores": scores,
                }
            )
    records.sort(key=lambda rec: (rec["epoch"], rec["symbol"]))
    return records
