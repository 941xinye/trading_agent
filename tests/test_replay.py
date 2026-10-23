"""历史重放测试：验证时间对齐、各维度历史取值与记录结构。"""
from __future__ import annotations

import time

import pytest

from crypto_analyst import backtest, config, replay
from crypto_analyst.providers.exchanges.base import Candle


def test_funding_at_annualizes_latest_rate():
    ts = [0.0, 28800.0, 57600.0]
    rates = [0.0001, -0.0002, 0.0003]
    result = replay.funding_at(ts, rates, 60000.0)
    assert result["funding_annualized_pct"] == pytest.approx(0.0003 * 1095 * 100)


def test_funding_at_without_history_is_neutral():
    assert replay.funding_at([], [], 100.0) == {}


def test_stable_at_computes_7d_and_30d_changes():
    ts = [float(i * 86400) for i in range(40)]
    totals = [10.0] * 40
    totals[9] = 100.0
    totals[32] = 120.0
    totals[39] = 123.0
    result = replay.stable_at(ts, totals, 39 * 86400.0)
    assert result["change_7d_pct"] == pytest.approx((123.0 / 120.0 - 1) * 100)
    assert result["change_30d_pct"] == pytest.approx((123.0 / 100.0 - 1) * 100)


def test_sentiment_at_picks_last_published_value():
    ts = [0.0, 86400.0, 172800.0]
    assert replay.sentiment_at(ts, [40.0, 55.0, 70.0], 90000.0) == {"value": 55.0}


def test_macro_at_uses_previous_trading_day():
    dates = ["2026-10-20", "2026-10-21", "2026-10-22"]
    entries = [{"dxy": {"change_5d_pct": v}} for v in (1.0, 2.0, 3.0)]
    # 本地时间 2026-10-22 08:00（UTC 00:00），只能用到 10-21 的数据
    t = time.mktime((2026, 10, 22, 8, 0, 0, 0, 0, -1))
    assert replay.macro_at(dates, entries, t) == {"dxy": {"change_5d_pct": 2.0}}


def test_replay_timestamps_aligns_to_step_and_span():
    step = 4 * 3600
    stamps = replay.replay_timestamps(1_792_771_200, days=1, step_hours=4)
    assert len(stamps) == 7
    assert stamps[-1] == 1_792_771_200
    assert stamps[0] == 1_792_771_200 - 86400
    assert all(stamp % step == 0 for stamp in stamps)


def _candles(count: int, step_s: int, start: float) -> list[Candle]:
    out = []
    for i in range(count):
        price = 100.0 + i * 0.5
        out.append(
            Candle(
                ts=int(start + i * step_s),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=10.0,
                quote_volume=1000.0,
            )
        )
    return out


def test_build_records_reconstructs_five_dimensions(monkeypatch):
    base = 1_792_600_000.0
    series = {
        "1h": _candles(400, 3600, base),
        "4h": _candles(200, 4 * 3600, base),
        "1d": _candles(200, 86400, base - 100 * 86400),
    }
    monkeypatch.setattr(replay, "_candles", lambda symbol, tf, limit: series[tf])
    monkeypatch.setattr(
        replay, "_funding_points", lambda symbol: ([base - 28800.0 * i for i in range(90)][::-1], [0.0001] * 90)
    )
    monkeypatch.setattr(
        replay, "_stable_points", lambda: ([base - 86400.0 * i for i in range(40)][::-1], [100.0 + i for i in range(40)])
    )
    monkeypatch.setattr(
        replay, "_fng_points", lambda: ([base - 86400.0 * i for i in range(40)][::-1], [50.0] * 40)
    )
    monkeypatch.setattr(
        replay,
        "_macro_points",
        lambda: (["2026-10-01", "2026-10-02"], [{"dxy": {"change_5d_pct": 0.5}}] * 2),
    )

    records = replay.build_records(symbols=["BTC"], days=2, step_hours=4)

    assert records
    assert all(rec["source"] == "replay" for rec in records)
    assert [rec["epoch"] for rec in records] == sorted(rec["epoch"] for rec in records)
    first = records[0]
    assert set(first["scores"]) == set(config.DIMENSION_WEIGHTS)
    assert all(0 <= value <= 100 for value in first["scores"].values())
    assert 0 <= first["total_score"] <= 100
    assert first["regime"]


def test_run_accepts_replay_records(monkeypatch):
    records = [
        {
            "symbol": "BTC",
            "epoch": i * 86400.0,
            "price": 100.0 * (1.01 ** i),
            "total_score": 50.0,
            "scores": {key: 50.0 for key in config.DIMENSION_WEIGHTS},
            "source": "replay",
        }
        for i in range(5)
    ]
    text = backtest.run(horizon_hours=24, source="replay", records=records)
    assert "来源 replay" in text
    assert "样本 4 个窗口" in text
