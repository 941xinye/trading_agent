"""评分规则回归测试：把权重与阈值行为固化成基线，改动评分逻辑时必须同步更新。"""
from __future__ import annotations

import time

from crypto_analyst import analysis, config
from conftest import make_view


def _views(**overrides):
    return {
        timeframe: make_view(timeframe, **overrides)
        for timeframe in config.TIMEFRAMES
    }


def test_regime_boundaries():
    assert analysis.regime_of(75.0) == "强势多头"
    assert analysis.regime_of(74.9) == "偏多"
    assert analysis.regime_of(62.0) == "偏多"
    assert analysis.regime_of(45.0) == "震荡"
    assert analysis.regime_of(32.0) == "偏空"
    assert analysis.regime_of(31.9) == "弱势空头"


def test_technical_score_direction():
    up = analysis._technical_score(_views(trend="up"))
    down = analysis._technical_score(
        _views(
            trend="down",
            macd_hist=-0.5,
            macd_hist_prev=-0.4,
            structure="lower_highs_lower_lows",
            rsi=40.0,
        )
    )
    assert up > 50 > down


def test_technical_score_rsi_overbought_penalty():
    normal = analysis._technical_score(_views(rsi=55.0))
    overbought = analysis._technical_score(_views(rsi=85.0))
    assert overbought < normal


def test_positioning_funding_tiers():
    assert analysis._positioning_score({"funding_annualized_pct": 90}) == 25.0
    assert analysis._positioning_score({"funding_annualized_pct": 40}) == 35.0
    assert analysis._positioning_score({"funding_annualized_pct": 20}) == 45.0
    assert analysis._positioning_score({"funding_annualized_pct": 5}) == 55.0
    assert analysis._positioning_score({"funding_annualized_pct": -5}) == 60.0


def test_positioning_oi_and_lsr():
    # 增仓上涨 +8，账户多空比 2.5 扣 8，大户偏空再扣 5
    score = analysis._positioning_score(
        {
            "funding_annualized_pct": 5,
            "oi_change_1h_pct": 2.0,
            "price_change_1h_pct": 1.0,
            "lsr_account": 2.5,
            "top_lsr_size": 0.7,
        }
    )
    assert score == 50.0


def test_liquidity_score_scales_with_stablecoin_flow():
    assert analysis._liquidity_score({}) == 50.0
    assert analysis._liquidity_score({"change_7d_pct": 2.5}) == 70.0
    assert analysis._liquidity_score({"change_7d_pct": -5.0}) == 30.0


def test_sentiment_extremes():
    assert analysis._sentiment_score({"value": 90}) == 80.0
    assert analysis._sentiment_score({"value": 5}) == 13.0
    assert analysis._sentiment_score({"value": 50}) == 50.0
    assert analysis._sentiment_score({}) == 50.0


def test_macro_score_reacts_to_dollar():
    strong_dollar = analysis._macro_score({"dxy": {"change_5d_pct": 1.0}})
    weak_dollar = analysis._macro_score({"dxy": {"change_5d_pct": -1.0}})
    assert strong_dollar == 44.0
    assert weak_dollar == 56.0


def test_total_score_is_weighted_sum():
    scores = {"technical": 80.0, "positioning": 80.0, "liquidity": 80.0, "sentiment": 80.0, "macro": 80.0}
    total = sum(scores[key] * weight for key, weight in config.DIMENSION_WEIGHTS.items())
    assert total == 80.0


def test_flags_capture_overbought_and_funding_crowding():
    views = _views()
    views["1d"] = make_view("1d", rsi=85.0)
    deriv = {"funding_annualized_pct": 60.0, "oi_change_1h_pct": 0.0, "price_change_1h_pct": 0.0}
    flags = analysis._build_flags("BTC", {"change_24h_pct": 1.0}, views, deriv, {"value": 50})
    assert any("超买区" in flag for flag in flags)
    assert any("多头拥挤" in flag for flag in flags)


def test_normalize_ts_handles_seconds_and_milliseconds():
    now = time.time()
    assert analysis._normalize_ts(now) == now  # 秒级不变
    assert analysis._normalize_ts(now * 1000) == now  # 毫秒级转秒


def test_freshness_warnings_flags_stale_data():
    now = time.time()
    stale = {"1h": make_view("1h", last_ts=now - 5 * 3600)}  # 1h 数据过期 5 小时
    fresh = {"1h": make_view("1h", last_ts=now - 600)}  # 10 分钟前，新鲜

    assert analysis._freshness_warnings(stale)  # 有过期告警
    assert not analysis._freshness_warnings(fresh)  # 无告警


def test_freshness_warnings_skips_missing_timestamp():
    views = {"1h": make_view("1h", last_ts=None)}
    assert analysis._freshness_warnings(views) == []
