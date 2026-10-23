"""纯 Python 技术指标实现，避免引入 pandas / numpy 依赖。"""
from __future__ import annotations


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def sma(values: list[float], n: int) -> list[float]:
    if len(values) < n:
        return []
    window_sum = sum(values[:n])
    out = [window_sum / n]
    for i in range(n, len(values)):
        window_sum += values[i] - values[i - n]
        out.append(window_sum / n)
    return out


def ema_series(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain, avg_loss = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(diff, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-diff, 0.0)) / n
    if avg_loss == 0:
        # 恒定价格（无涨无跌）时多空平衡，返回中性 50 而非 100
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (macd 线, 信号线, 柱状图)，均截断为有效部分。"""
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    line = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(fast_series, slow_series)
    ]
    valid = [v for v in line if v is not None]
    if len(valid) < signal:
        return [], [], []
    signal_series = ema_series(valid, signal)
    hist = [(v - s) if s is not None else None for v, s in zip(valid, signal_series)]
    return valid, signal_series, hist


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float | None:
    trs: list[float] = []
    for i in range(1, len(closes)):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    if len(trs) < n:
        return None
    value = sum(trs[:n]) / n
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def bollinger(closes: list[float], n: int = 20, k: float = 2.0):
    if len(closes) < n:
        return None, None, None
    window = closes[-n:]
    mid = sum(window) / n
    variance = sum((x - mid) ** 2 for x in window) / n
    std = variance ** 0.5
    return mid + k * std, mid, mid - k * std


def pivots(highs: list[float], lows: list[float], left: int = 2, right: int = 2):
    """分形高低点：左右各 left/right 根K线确认的局部极值。"""
    high_idx: list[int] = []
    low_idx: list[int] = []
    for i in range(left, len(highs) - right):
        window_high = highs[i - left : i + right + 1]
        window_low = lows[i - left : i + right + 1]
        if highs[i] == max(window_high):
            high_idx.append(i)
        if lows[i] == min(window_low):
            low_idx.append(i)
    return high_idx, low_idx


def pct_change(series: list[float], periods: int) -> float | None:
    if len(series) <= periods or series[-periods - 1] == 0:
        return None
    return (series[-1] / series[-periods - 1] - 1.0) * 100.0


def last_value(series: list[float | None]) -> float | None:
    for value in reversed(series):
        if value is not None:
            return value
    return None
