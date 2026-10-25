import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crypto_analyst import analysis


def make_view(timeframe: str, **overrides) -> analysis.TimeframeView:
    """构造一个默认多头、无极端信号的周期视图，测试按需覆盖字段。"""
    data = {
        "timeframe": timeframe,
        "close": 100.0,
        "change_pct": 0.5,
        "trend": "up",
        "ema20": 99.0,
        "ema50": 98.0,
        "ema200": 90.0,
        "rsi": 55.0,
        "macd_hist": 0.5,
        "macd_hist_prev": 0.4,
        "atr_pct": 2.0,
        "vol_ratio": 1.0,
        "bb_position": 0.6,
        "structure": "higher_highs_higher_lows",
        "support": 95.0,
        "resistance": 110.0,
    }
    data.update(overrides)
    return analysis.TimeframeView(**data)


def make_snapshot(**kwargs) -> analysis.MarketSnapshot:
    views = {
        "1h": make_view("1h"),
        "4h": make_view("4h"),
        "1d": make_view("1d"),
    }
    symbol = analysis.SymbolSnapshot(
        symbol=kwargs.pop("symbol", "BTC"),
        price=kwargs.pop("price", 100.0),
        change_24h_pct=kwargs.pop("change_24h_pct", 1.2),
        quote_volume_24h=kwargs.pop("quote_volume_24h", 1.5e9),
        timeframes=kwargs.pop("timeframes", views),
        derivatives=kwargs.pop(
            "derivatives",
            {
                "funding_rate": 0.0001,
                "funding_annualized_pct": 8.0,
                "funding_percentile": 55.0,
                "open_interest_usd": 2.0e9,
                "oi_change_24h_pct": 1.5,
                "oi_change_1h_pct": 0.3,
                "price_change_1h_pct": 0.2,
                "lsr_account": 1.5,
                "top_lsr_size": 1.1,
            },
        ),
        scores=kwargs.pop(
            "scores",
            {"technical": 60.0, "positioning": 55.0, "liquidity": 50.0, "sentiment": 50.0, "macro": 50.0},
        ),
        total_score=kwargs.pop("total_score", 55.5),
        regime=kwargs.pop("regime", "震荡"),
        flags=kwargs.pop("flags", []),
        key_levels=kwargs.pop("key_levels", {"day_support": 95.0, "day_resistance": 110.0}),
    )
    return analysis.MarketSnapshot(
        generated_at=kwargs.pop("generated_at", "2026-10-23 23:00:00"),
        symbols=[symbol],
        onchain=kwargs.pop("onchain", {"total_usd": 1.6e11, "change_7d_pct": 0.8, "change_30d_pct": 1.2}),
        btc_network=kwargs.pop("btc_network", {}),
        eth_network=kwargs.pop("eth_network", {}),
        sentiment=kwargs.pop("sentiment", {"value": 50, "label": "中性", "change_1d": 0}),
        news=kwargs.pop("news", []),
        macro=kwargs.pop("macro", {}),
        warnings=kwargs.pop("warnings", []),
    )


@pytest.fixture
def snapshot_factory():
    return make_snapshot


@pytest.fixture
def sample_snapshot():
    return make_snapshot()
