"""分析引擎：把原始行情/链上/情绪/宏观数据加工成可解释的结构化快照与评分。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from . import config, history, indicators
from .providers import macro as macro_provider
from .providers import market, onchain, sentiment

REGIME_THRESHOLDS = [
    (75.0, "强势多头"),
    (62.0, "偏多"),
    (45.0, "震荡"),
    (32.0, "偏空"),
    (0.0, "弱势空头"),
]


def _r(value: float | None, nd: int = 2) -> float | None:
    return None if value is None else round(float(value), nd)


def regime_of(score: float) -> str:
    for threshold, label in REGIME_THRESHOLDS:
        if score >= threshold:
            return label
    return "弱势空头"


@dataclass
class TimeframeView:
    timeframe: str
    close: float
    change_pct: float | None
    trend: str
    ema20: float | None
    ema50: float | None
    ema200: float | None
    rsi: float | None
    macd_hist: float | None
    macd_hist_prev: float | None
    atr_pct: float | None
    vol_ratio: float | None
    bb_position: float | None
    structure: str
    support: float | None
    resistance: float | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("close", "ema20", "ema50", "ema200", "rsi", "atr_pct", "vol_ratio", "bb_position", "support", "resistance"):
            data[key] = _r(data[key])
        for key in ("macd_hist", "macd_hist_prev"):
            data[key] = _r(data[key], 4)
        data["change_pct"] = _r(data["change_pct"])
        return data


@dataclass
class SymbolSnapshot:
    symbol: str
    price: float
    change_24h_pct: float
    quote_volume_24h: float
    timeframes: dict[str, TimeframeView]
    derivatives: dict[str, Any] | None
    scores: dict[str, float]
    total_score: float
    regime: str
    flags: list[str] = field(default_factory=list)
    key_levels: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        derivatives: dict[str, Any] = {}
        for key, value in (self.derivatives or {}).items():
            if isinstance(value, float):
                derivatives[key] = round(value, 6 if key == "funding_rate" else 2)
            else:
                derivatives[key] = value
        return {
            "symbol": self.symbol,
            "price": _r(self.price),
            "change_24h_pct": _r(self.change_24h_pct),
            "quote_volume_24h": _r(self.quote_volume_24h, 0),
            "timeframes": {tf: view.to_dict() for tf, view in self.timeframes.items()},
            "derivatives": derivatives,
            "scores": {k: _r(v, 1) for k, v in self.scores.items()},
            "total_score": _r(self.total_score, 1),
            "regime": self.regime,
            "flags": self.flags,
            "key_levels": {k: _r(v) for k, v in self.key_levels.items()},
        }


@dataclass
class MarketSnapshot:
    generated_at: str
    symbols: list[SymbolSnapshot]
    onchain: dict[str, Any]
    btc_network: dict[str, Any]
    sentiment: dict[str, Any]
    news: list[dict[str, Any]]
    macro: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "symbols": [s.to_dict() for s in self.symbols],
            "onchain": self.onchain,
            "btc_network": self.btc_network,
            "sentiment": {k: v for k, v in self.sentiment.items() if k != "history_30d"},
            "news": self.news,
            "macro": self.macro,
            "warnings": self.warnings,
        }


def _trend_of(close: float, ema20: float | None, ema50: float | None, slope: float | None) -> str:
    if ema20 is None or ema50 is None:
        return "range"
    if close > ema20 > ema50 and (slope is None or slope > 0):
        return "up"
    if close < ema20 < ema50 and (slope is None or slope < 0):
        return "down"
    return "range"


def _structure_of(highs: list[float], lows: list[float]) -> str:
    high_idx, low_idx = indicators.pivots(highs, lows)
    if len(high_idx) >= 2 and len(low_idx) >= 2:
        higher_high = highs[high_idx[-1]] > highs[high_idx[-2]]
        higher_low = lows[low_idx[-1]] > lows[low_idx[-2]]
        if higher_high and higher_low:
            return "higher_highs_higher_lows"
        if not higher_high and not higher_low:
            return "lower_highs_lower_lows"
    return "mixed"


def _nearest_levels(close: float, highs: list[float], lows: list[float]) -> tuple[float | None, float | None]:
    high_idx, low_idx = indicators.pivots(highs, lows)
    swing_highs = [highs[i] for i in high_idx[-8:]]
    swing_lows = [lows[i] for i in low_idx[-8:]]
    above = [level for level in swing_highs if level > close]
    below = [level for level in swing_lows if level < close]
    resistance = min(above) if above else max(highs[-30:])
    support = max(below) if below else min(lows[-30:])
    return support, resistance


def build_timeframe_view(timeframe: str, candles: list[market.Candle]) -> TimeframeView:
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.quote_volume for c in candles]
    close = closes[-1]

    ema20_series = indicators.ema_series(closes, 20)
    ema50_series = indicators.ema_series(closes, 50)
    ema200_series = indicators.ema_series(closes, 200)
    ema20 = indicators.last_value(ema20_series)
    ema50 = indicators.last_value(ema50_series)
    ema200 = indicators.last_value(ema200_series)
    slope = None
    if len(closes) > 6 and ema20_series[-6] not in (None, 0):
        slope = (ema20_series[-1] - ema20_series[-6]) / ema20_series[-6]

    _, _, macd_hist = indicators.macd(closes)
    hist_now = macd_hist[-1] if macd_hist else None
    hist_prev = macd_hist[-2] if len(macd_hist) > 1 else None

    rsi_value = indicators.rsi(closes)
    atr_value = indicators.atr(highs, lows, closes)
    upper, _, lower = indicators.bollinger(closes)
    bb_position = None
    if upper is not None and lower is not None and upper > lower:
        bb_position = (close - lower) / (upper - lower)

    vol_ratio = None
    if len(volumes) > 21:
        avg = sum(volumes[-21:-1]) / 20
        if avg:
            vol_ratio = volumes[-1] / avg

    support, resistance = _nearest_levels(close, highs, lows)
    change = indicators.pct_change(closes, 1)

    return TimeframeView(
        timeframe=timeframe,
        close=close,
        change_pct=change,
        trend=_trend_of(close, ema20, ema50, slope),
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi=rsi_value,
        macd_hist=hist_now,
        macd_hist_prev=hist_prev,
        atr_pct=(atr_value / close * 100.0) if atr_value else None,
        vol_ratio=vol_ratio,
        bb_position=bb_position,
        structure=_structure_of(highs[-80:], lows[-80:]),
        support=support,
        resistance=resistance,
    )


def _technical_score(views: dict[str, TimeframeView]) -> float:
    th = config.THRESHOLDS
    score = 50.0
    for timeframe, view in views.items():
        weight = config.TIMEFRAME_WEIGHTS.get(timeframe, 0.0)
        if view.trend == "up":
            score += 18 * weight
        elif view.trend == "down":
            score -= 18 * weight

        if view.macd_hist is not None:
            score += (6 * weight) if view.macd_hist > 0 else (-6 * weight)
            if view.macd_hist_prev is not None and view.macd_hist > 0 and view.macd_hist > view.macd_hist_prev:
                score += 3 * weight

        if view.structure == "higher_highs_higher_lows":
            score += 5 * weight
        elif view.structure == "lower_highs_lower_lows":
            score -= 5 * weight

        if view.rsi is not None:
            if view.rsi >= th["rsi_overbought"]:
                score -= 8 * weight
            elif view.rsi >= th["rsi_score_strong"]:
                score += 4 * weight
            elif view.rsi <= th["rsi_score_weak"]:
                score -= 4 * weight

        if view.vol_ratio is not None and view.vol_ratio > th["vol_ratio_score"]:
            score += (4 * weight) if view.trend == "up" else (-4 * weight)
    return indicators.clamp(score)


def _positioning_score(deriv: dict[str, Any] | None) -> float:
    if not deriv:
        return 50.0
    score = 50.0

    funding = deriv.get("funding_annualized_pct") or 0.0
    if funding > 80:
        score -= 25
    elif funding > 30:
        score -= 15
    elif funding > 10:
        score -= 5
    elif funding >= 0:
        score += 5
    else:
        score += 10  # 负费率：空头拥挤，存在反向修复动能

    oi_change = deriv.get("oi_change_1h_pct")
    price_change = deriv.get("price_change_1h_pct")
    if oi_change is not None and price_change is not None and abs(oi_change) > config.THRESHOLDS["oi_score_pct"]:
        if price_change > 0 and oi_change > 0:
            score += 8  # 增仓上涨：趋势资金进场
        elif price_change > 0 and oi_change < 0:
            score -= 3  # 缩仓上涨：空头回补驱动
        elif price_change < 0 and oi_change > 0:
            score -= 8  # 增仓下跌：空头主动进攻
        else:
            score += 3  # 缩仓下跌：抛压衰减

    lsr = deriv.get("lsr_account")
    if lsr:
        if lsr >= 2.2:
            score -= 8
        elif lsr <= 1.2:
            score += 4

    top_lsr = deriv.get("top_lsr_size")
    if top_lsr and top_lsr < 0.8:
        score -= 5  # 大户持仓偏空
    return indicators.clamp(score)


def _liquidity_score(stable: dict[str, Any]) -> float:
    score = 50.0
    if not stable:
        return score
    change_7d = stable.get("change_7d_pct")
    change_30d = stable.get("change_30d_pct")
    if change_7d is not None:
        score += indicators.clamp(change_7d * 8, -20, 20)
    if change_30d is not None:
        score += indicators.clamp(change_30d * 3, -10, 10)
    return indicators.clamp(score)


def _sentiment_score(fear_greed: dict[str, Any]) -> float:
    th = config.THRESHOLDS
    value = fear_greed.get("value")
    if value is None:
        return 50.0
    score = float(value)
    if value >= th["fng_extreme_greed"]:
        score -= 10  # 极度贪婪：追高风险
    elif value <= th["fng_extreme_fear"]:
        score += 8  # 极度恐慌：反向机会
    return indicators.clamp(score)


def _macro_score(macro_data: dict[str, Any]) -> float:
    score = 50.0
    dxy = (macro_data.get("dxy") or {}).get("change_5d_pct")
    ndx = (macro_data.get("ndx") or {}).get("change_5d_pct")
    spx = (macro_data.get("spx") or {}).get("change_5d_pct")
    if dxy is not None:
        score += indicators.clamp(-dxy * 6, -15, 15)
    if ndx is not None:
        score += indicators.clamp(ndx * 2, -15, 15)
    if spx is not None:
        score += indicators.clamp(spx * 1.5, -8, 8)
    return indicators.clamp(score)


def _build_flags(
    symbol: str,
    ticker: dict[str, Any],
    views: dict[str, TimeframeView],
    deriv: dict[str, Any] | None,
    fear_greed: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    th = config.THRESHOLDS
    day = views.get("1d")
    hour = views.get("1h")

    if day and day.rsi is not None:
        if day.rsi >= th["rsi_overbought"]:
            flags.append(f"日线 RSI {day.rsi:.1f}，进入超买区")
        elif day.rsi <= th["rsi_oversold"]:
            flags.append(f"日线 RSI {day.rsi:.1f}，进入超卖区")

    if deriv:
        funding = deriv.get("funding_annualized_pct")
        pctl = deriv.get("funding_percentile")
        if funding is not None and funding > th["funding_crowded_long"]:
            extra = f"，历史分位 {pctl:.0f}%" if pctl is not None else ""
            flags.append(f"资金费率年化 {funding:.1f}%，多头拥挤{extra}")
        elif funding is not None and funding < th["funding_crowded_short"]:
            flags.append(f"资金费率年化 {funding:.1f}%，空头拥挤，存在逼空条件")

        oi_change = deriv.get("oi_change_1h_pct")
        price_change = deriv.get("price_change_1h_pct")
        if oi_change is not None and price_change is not None and abs(oi_change) > th["oi_flag_pct"]:
            if oi_change > 0 and price_change < -0.3:
                flags.append(f"1h 持仓量 +{oi_change:.1f}% 且价格下跌，空头增仓")
            elif oi_change > 0 and price_change > 0.3:
                flags.append(f"1h 持仓量 +{oi_change:.1f}% 且价格上涨，多头增仓")

    if abs(ticker["change_24h_pct"]) >= th["move_24h_pct"]:
        flags.append(f"24h 涨跌幅 {ticker['change_24h_pct']:+.1f}%，波动放大")

    if hour and hour.vol_ratio is not None and hour.vol_ratio >= th["vol_ratio_flag"]:
        flags.append(f"1h 成交量放大至 20 周期均量的 {hour.vol_ratio:.1f} 倍")

    if day and day.resistance and day.support and day.close:
        distance_resistance = (day.resistance / day.close - 1) * 100
        distance_support = (day.close / day.support - 1) * 100 if day.support else 999.0
        if distance_resistance <= distance_support:
            if distance_resistance <= th["level_flag_pct"]:
                flags.append(f"价格逼近日线阻力 {day.resistance:.2f}（距 {distance_resistance:.1f}%）")
        elif distance_support <= th["level_flag_pct"]:
            flags.append(f"价格贴近日线支撑 {day.support:.2f}（距 {distance_support:.1f}%）")

    if fear_greed.get("value", 50) >= th["fng_extreme_greed"]:
        flags.append(f"恐慌贪婪指数 {fear_greed['value']}（极度贪婪）")
    elif fear_greed.get("value", 50) <= th["fng_extreme_fear"]:
        flags.append(f"恐慌贪婪指数 {fear_greed['value']}（极度恐慌）")
    return flags


def build_symbol_snapshot(
    symbol: str,
    stable: dict[str, Any],
    fear_greed: dict[str, Any],
    macro_data: dict[str, Any],
) -> SymbolSnapshot:
    ticker = market.ticker(symbol)
    views: dict[str, TimeframeView] = {}
    for timeframe in config.TIMEFRAMES:
        views[timeframe] = build_timeframe_view(
            timeframe, market.candles(symbol, timeframe, config.KLINE_LIMIT)
        )

    deriv = market.derivatives(symbol)
    scores = {
        "technical": _technical_score(views),
        "positioning": _positioning_score(deriv),
        "liquidity": _liquidity_score(stable),
        "sentiment": _sentiment_score(fear_greed),
        "macro": _macro_score(macro_data),
    }
    total = sum(scores[key] * weight for key, weight in config.DIMENSION_WEIGHTS.items())

    day = views.get("1d")
    hour4 = views.get("4h")
    key_levels = {
        "day_support": day.support if day else None,
        "day_resistance": day.resistance if day else None,
        "h4_support": hour4.support if hour4 else None,
        "h4_resistance": hour4.resistance if hour4 else None,
    }

    return SymbolSnapshot(
        symbol=symbol,
        price=ticker["price"],
        change_24h_pct=ticker["change_24h_pct"],
        quote_volume_24h=ticker["quote_volume_24h"],
        timeframes=views,
        derivatives=deriv,
        scores=scores,
        total_score=total,
        regime=regime_of(total),
        flags=_build_flags(symbol, ticker, views, deriv, fear_greed),
        key_levels=key_levels,
    )


def build_snapshot(symbols: list[str] | None = None, include_news: bool = True) -> MarketSnapshot:
    symbols = [s.upper() for s in (symbols or config.SYMBOLS)]
    warnings: list[str] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        fut_stable = pool.submit(onchain.stablecoins)
        fut_btc = pool.submit(onchain.btc_network)
        fut_fng = pool.submit(sentiment.fear_greed)
        fut_macro = pool.submit(macro_provider.macro_overview)
        fut_news = pool.submit(sentiment.news) if include_news else None

        try:
            stable = fut_stable.result()
        except RuntimeError as exc:
            stable = {}
            warnings.append(f"稳定币数据获取失败: {exc}")
        try:
            btc = fut_btc.result()
        except RuntimeError as exc:
            btc = {}
            warnings.append(f"BTC 网络数据获取失败: {exc}")
        try:
            fng = fut_fng.result()
        except RuntimeError as exc:
            fng = {}
            warnings.append(f"恐慌贪婪指数获取失败: {exc}")
        try:
            macro_data = fut_macro.result()
        except RuntimeError as exc:
            macro_data = {}
            warnings.append(f"宏观数据获取失败: {exc}")
        news_items: list[dict[str, Any]] = []
        if fut_news:
            try:
                news_items = fut_news.result()
            except RuntimeError as exc:
                warnings.append(f"新闻获取失败: {exc}")

        def _safe(symbol: str) -> SymbolSnapshot | None:
            try:
                return build_symbol_snapshot(symbol, stable, fng, macro_data)
            except Exception as exc:  # 单个标的失败不影响整体简报
                warnings.append(f"{symbol} 分析失败: {exc}")
                return None

        results = list(pool.map(_safe, symbols))

    snapshots = [item for item in results if item is not None]
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    snapshot = MarketSnapshot(
        generated_at=generated,
        symbols=snapshots,
        onchain=stable,
        btc_network=btc,
        sentiment=fng,
        news=news_items,
        macro=macro_data,
        warnings=warnings,
    )
    history.append_snapshot(snapshot)  # 落盘积累时间序列，供 backtest 校准评分
    return snapshot
