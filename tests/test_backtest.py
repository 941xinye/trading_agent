"""回测模块离线测试：构造伪造的历史记录，验证配对、分桶与相关性计算。"""
from __future__ import annotations

import pytest

from crypto_analyst import backtest


def _rec(symbol: str, epoch: float, price: float, score: float) -> dict:
    return {"symbol": symbol, "epoch": epoch, "price": price, "total_score": score, "ts": ""}


def test_pearson_perfect_positive():
    assert backtest._pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)


def test_pearson_zero_variance():
    assert backtest._pearson([1, 1, 1], [2, 3, 4]) is None


def test_pearson_too_few_samples():
    assert backtest._pearson([1, 2], [3, 4]) is None


def test_evaluate_pairs_matches_horizon():
    # t0 打 80 分价格 100，24h 后价格 110 → 收益 +10%
    records = [
        _rec("BTC", 0.0, 100.0, 80.0),
        _rec("BTC", 86400.0, 110.0, 60.0),
    ]
    pairs = backtest.evaluate_pairs(records, horizon_hours=24)
    assert len(pairs) == 1
    assert pairs[0]["score"] == 80.0
    assert pairs[0]["forward_pct"] == pytest.approx(10.0)


def test_evaluate_pairs_skips_short_span():
    # 数据跨度不足 24h，无样本对
    records = [_rec("BTC", 0.0, 100.0, 80.0), _rec("BTC", 3600.0, 101.0, 70.0)]
    assert backtest.evaluate_pairs(records, horizon_hours=24) == []


def test_evaluate_pairs_filters_bad_records():
    records = [
        {"symbol": "BTC", "epoch": 0.0, "price": 100.0},  # 缺 total_score
        _rec("BTC", 0.0, 100.0, 80.0),
        _rec("BTC", 86400.0, 110.0, 60.0),
    ]
    assert len(backtest.evaluate_pairs(records, horizon_hours=24)) == 1


def test_summarize_buckets_and_verdict_path():
    # 每个标的每 48h 一对记录：高分 BTC 随后 +10%，低分 ETH 随后 -10%
    records = []
    for i in range(5):
        t = i * 2 * 86400.0
        records.append(_rec("BTC", t, 100.0, 80.0))           # 强势多头
        records.append(_rec("BTC", t + 86400.0, 110.0, 60.0))
        records.append(_rec("ETH", t, 50.0, 20.0))            # 弱势空头
        records.append(_rec("ETH", t + 86400.0, 45.0, 50.0))  # 中继记录用中性分，避免串桶
    pairs = backtest.evaluate_pairs(records, horizon_hours=24)
    stats = backtest.summarize(pairs)
    by_regime = {row["regime"]: row for row in stats["buckets"]}
    # 除每标的最后一条外都能配成对：9 + 9
    assert stats["samples"] == 18
    assert by_regime["强势多头"]["avg_pct"] == pytest.approx(10.0)
    assert by_regime["弱势空头"]["avg_pct"] == pytest.approx(-10.0)
    assert stats["pearson"] is not None


def test_run_without_data(tmp_path, monkeypatch):
    # 无落盘数据时给出引导文案而非报错
    monkeypatch.setattr(backtest.history, "iter_records", lambda: iter([]))
    text = backtest.run()
    assert "暂无可用样本对" in text


def test_evaluate_pairs_no_overlap():
    # 每 5 分钟一条记录、跨度 30h：24h 窗口只能产出 1 个样本，而不是数百个重叠样本
    records = [_rec("BTC", i * 300.0, 100.0 + i, 60.0) for i in range(361)]
    pairs = backtest.evaluate_pairs(records, horizon_hours=24)
    assert len(pairs) == 1
    assert pairs[0]["lag_hours"] == pytest.approx(0.0)


def test_evaluate_pairs_drops_long_gap():
    # 数据断档 72h：24h 窗口的目标时刻没有可信价格，样本应被丢弃
    records = [_rec("BTC", 0.0, 100.0, 80.0), _rec("BTC", 72 * 3600.0, 110.0, 60.0)]
    assert backtest.evaluate_pairs(records, horizon_hours=24) == []


def test_evaluate_pairs_resumes_after_gap():
    # 断档后仍可重新开窗：0h → 72h（丢弃），72h → 96h（有效）
    records = [
        _rec("BTC", 0.0, 100.0, 80.0),
        _rec("BTC", 72 * 3600.0, 110.0, 60.0),
        _rec("BTC", 96 * 3600.0, 121.0, 55.0),
    ]
    pairs = backtest.evaluate_pairs(records, horizon_hours=24)
    assert len(pairs) == 1
    assert pairs[0]["score"] == 60.0
    assert pairs[0]["forward_pct"] == pytest.approx(10.0)


def test_run_filters_by_source(monkeypatch):
    # 只有 watch 来源进入统计，brief 记录被忽略并给出提示
    records = [
        {**_rec("BTC", 0.0, 100.0, 80.0), "source": "watch"},
        {**_rec("BTC", 86400.0, 110.0, 60.0), "source": "watch"},
        {**_rec("BTC", 3600.0, 99.0, 20.0), "source": "brief"},
    ]
    monkeypatch.setattr(backtest.history, "iter_records", lambda: iter(records))
    text = backtest.run(horizon_hours=24, source="watch")
    assert "已忽略 1 条" in text
    assert "样本：1 个窗口" in text
