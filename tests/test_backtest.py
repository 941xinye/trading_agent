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


def test_spearman_catches_monotonic_nonlinear():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 4.0, 9.0, 16.0, 25.0]
    assert backtest._spearman(xs, ys) == pytest.approx(1.0)
    assert backtest._pearson(xs, ys) < 1.0


def test_evaluate_pairs_records_max_adverse_excursion():
    # 窗口内最低 95（-5%），期末 110（+10%）
    records = [
        _rec("BTC", 0.0, 100.0, 80.0),
        _rec("BTC", 43200.0, 95.0, 75.0),
        _rec("BTC", 86400.0, 110.0, 70.0),
    ]
    pair = backtest.evaluate_pairs(records, horizon_hours=24)[0]
    assert pair["mae_pct"] == pytest.approx(-5.0)
    assert pair["forward_pct"] == pytest.approx(10.0)


def test_summarize_quantiles_and_quality_metrics():
    pairs = [
        {"symbol": "BTC", "score": 10.0 * i + 5.0, "forward_pct": i * 2.0 - 8.0, "mae_pct": -1.0}
        for i in range(10)
    ]
    stats = backtest.summarize(pairs)
    assert len(stats["quantiles"]) == backtest.QUANTILE_BUCKETS
    assert stats["quantiles"][0]["avg_pct"] < 0 < stats["quantiles"][-1]["avg_pct"]
    assert stats["hit_rate"] == pytest.approx(50.0)
    assert stats["profit_factor"] is not None
    assert stats["avg_mae"] == pytest.approx(-1.0)


def test_run_supports_multiple_horizons(monkeypatch):
    # 30 小时、每小时一条记录：4h 窗口有多个样本，24h 窗口只有 1 个
    records = [
        {**_rec("BTC", i * 3600.0, 100.0 + i, 60.0), "source": "watch"}
        for i in range(31)
    ]
    monkeypatch.setattr(backtest.history, "iter_records", lambda: iter(records))
    text = backtest.run(horizon_hours=[4, 24], source="watch")
    assert "### 未来 4h" in text
    assert "### 未来 24h" in text


def test_weight_sensitivity_requires_dimension_scores(monkeypatch):
    records = []
    for i in range(6):
        scores = {
            "technical": 40.0 + i * 6.0,
            "positioning": 55.0,
            "liquidity": 50.0,
            "sentiment": 45.0,
            "macro": 50.0,
        }
        records.append(
            {**_rec("BTC", i * 86400.0, 100.0 * (1.01 ** i), 56.0), "scores": scores, "source": "watch"}
        )
    monkeypatch.setattr(backtest.history, "iter_records", lambda: iter(records))
    text = backtest.run(horizon_hours=24, source="watch", sweep_weights=True)
    assert "权重敏感性" in text
    assert "基线：Pearson r=" in text
    assert "| technical |" in text


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
    assert "样本 1 个窗口" in text


def _scored_records(count: int) -> list[dict]:
    """构造 count 个非重叠窗口：技术面与收益同向，情绪与收益反向。"""
    records = []
    price = 100.0
    for i in range(count + 1):
        records.append(
            {
                "symbol": "BTC",
                "epoch": i * 86400.0,
                "price": price,
                "total_score": 50.0,
                "scores": {
                    "technical": 40.0 + i,
                    "positioning": 50.0,
                    "liquidity": 50.0,
                    "sentiment": 60.0 - i,
                    "macro": 50.0,
                },
                "ts": "",
            }
        )
        # 未来收益随 i 递增，确保技术面分数与收益同向
        price *= 1.0 + 0.01 * i
    return records


def test_recommend_weights_uses_dimension_ic():
    result = backtest.recommend_weights(_scored_records(32), horizon_hours=24)
    assert result["ready"] is True
    assert result["samples"] == 32
    rows = {row["dimension"]: row for row in result["rows"]}
    assert rows["technical"]["ic"] == pytest.approx(1.0)
    assert rows["sentiment"]["ic"] == pytest.approx(-1.0)
    # 单维度封顶 50%（两位小数舍入会有 ±0.02 误差），其余维度均分剩余权重
    assert rows["technical"]["suggested"] == pytest.approx(backtest.MAX_DIM_WEIGHT, abs=0.03)
    assert rows["sentiment"]["suggested"] < rows["sentiment"]["current"]
    assert sum(result["suggested"].values()) == pytest.approx(1.0)


def test_recommend_weights_rejects_ic_within_noise(monkeypatch):
    fixed = {"technical": 0.05, "positioning": -0.02, "liquidity": 0.03, "sentiment": -0.01, "macro": 0.04}
    # 用闭包按调用顺序返回，模拟所有维度都只有微弱相关
    values = iter(fixed.values())
    monkeypatch.setattr(backtest, "_spearman", lambda xs, ys: next(values))

    result = backtest.recommend_weights(_scored_records(60), horizon_hours=24)
    assert result["ready"] is False
    assert "噪声带" in result["note"]
    assert all(row["suggested"] is None for row in result["rows"])


def test_recommend_weights_requires_enough_samples():
    result = backtest.recommend_weights(_scored_records(3), horizon_hours=24)
    assert result["ready"] is False
    assert "不足" in result["note"]
    assert all(row["suggested"] is None for row in result["rows"])


def test_recommend_section_renders_copyable_config(monkeypatch):
    monkeypatch.setattr(backtest.history, "iter_records", lambda: iter(_scored_records(32)))
    text = backtest.run(horizon_hours=24, source="all", recommend=True)
    assert "### 权重建议 · 未来 24h" in text
    assert "DIMENSION_WEIGHTS = {" in text
    assert "| technical |" in text

