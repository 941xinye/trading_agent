"""指标库手算对拍测试：所有期望值均不依赖被测实现，独立演算得出。"""
from __future__ import annotations

import pytest

from crypto_analyst import indicators


# ---------- clamp ----------

def test_clamp_bounds():
    assert indicators.clamp(120) == 100.0
    assert indicators.clamp(-5) == 0.0
    assert indicators.clamp(55) == 55.0


def test_clamp_custom_range():
    assert indicators.clamp(25, -20, 20) == 20
    assert indicators.clamp(-25, -20, 20) == -20


# ---------- sma ----------

def test_sma_known_series():
    assert indicators.sma([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]


def test_sma_insufficient_data():
    assert indicators.sma([1, 2], 3) == []


# ---------- ema_series ----------

def test_ema_series_known_values():
    # k = 0.5，种子为前 3 项均值 2.0，之后逐项递推
    out = indicators.ema_series([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_ema_series_insufficient_data():
    assert indicators.ema_series([1, 2], 3) == [None, None]


# ---------- rsi ----------

def test_rsi_all_gains():
    closes = [float(i) for i in range(1, 20)]
    assert indicators.rsi(closes, 14) == 100.0


def test_rsi_all_losses():
    closes = [float(20 - i) for i in range(19)]
    assert indicators.rsi(closes, 14) == pytest.approx(0.0, abs=1e-9)


def test_rsi_constant_prices_is_neutral():
    # 无涨无跌：avg_gain = avg_loss = 0，应返回中性 50 而非 100
    assert indicators.rsi([5.0] * 20, 14) == 50.0


def test_rsi_wilder_example():
    # 经典 Wilder 样例（手工递推，含一次平滑迭代）期望值 ≈ 68.80
    closes = [
        44.00, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
    ]
    assert indicators.rsi(closes, 14) == pytest.approx(68.80, abs=0.01)


def test_rsi_insufficient_data():
    assert indicators.rsi([1, 2, 3], 14) is None


# ---------- macd ----------

def test_macd_uptrend_positive():
    closes = [float(i) for i in range(1, 61)]
    line, signal, hist = indicators.macd(closes)
    assert len(line) == len(signal) == len(hist)
    assert line[-1] is not None and line[-1] > 0
    # hist 定义即 macd 线 - 信号线
    if hist[-1] is not None:
        assert hist[-1] == pytest.approx(line[-1] - signal[-1])


def test_macd_insufficient_data():
    assert indicators.macd([1.0] * 10) == ([], [], [])


# ---------- atr ----------

def test_atr_constant_range():
    # 每根 K 线 TR 恒为 2
    assert indicators.atr([10, 11, 12], [8, 9, 10], [9, 10, 11], 2) == pytest.approx(2.0)


def test_atr_wilder_smoothing():
    # TR = [3, 2, 3]，n=2：初值 (3+2)/2=2.5，再平滑 (2.5+3)/2=2.75
    assert indicators.atr([12, 14, 13, 15], [10, 11, 11, 12], [11, 13, 12, 14], 2) == pytest.approx(2.75)


def test_atr_insufficient_data():
    assert indicators.atr([1, 2], [0, 1], [1, 1], 14) is None


# ---------- bollinger ----------

def test_bollinger_constant_series():
    upper, mid, lower = indicators.bollinger([5.0] * 20)
    assert upper == mid == lower == 5.0


def test_bollinger_linear_series():
    # 1..20：mid=10.5，var=33.25，std=√33.25≈5.7662813
    std = 33.25 ** 0.5
    upper, mid, lower = indicators.bollinger([float(i) for i in range(1, 21)])
    assert mid == pytest.approx(10.5)
    assert upper == pytest.approx(10.5 + 2 * std, abs=1e-9)
    assert lower == pytest.approx(10.5 - 2 * std, abs=1e-9)


def test_bollinger_insufficient_data():
    assert indicators.bollinger([1.0] * 10) == (None, None, None)


# ---------- pivots ----------

def test_pivots_detects_fractal():
    highs = [1, 2, 5, 2, 1, 3, 1]
    lows = [5, 4, 1, 4, 5, 2, 5]
    high_idx, low_idx = indicators.pivots(highs, lows, left=2, right=2)
    assert high_idx == [2]
    assert low_idx == [2]


def test_pivots_monotonic_series_empty():
    # 单调递增无局部极值
    high_idx, low_idx = indicators.pivots([1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 6])
    assert high_idx == []
    assert low_idx == []


# ---------- pct_change ----------

def test_pct_change_basic():
    assert indicators.pct_change([100.0, 110.0], 1) == pytest.approx(10.0)


def test_pct_change_zero_base():
    assert indicators.pct_change([0.0, 100.0], 1) is None


def test_pct_change_insufficient_data():
    assert indicators.pct_change([100.0], 1) is None


# ---------- last_value ----------

def test_last_value_skips_none():
    assert indicators.last_value([None, 1.0, None]) == 1.0


def test_last_value_all_none():
    assert indicators.last_value([None, None]) is None
