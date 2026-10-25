"""仪表盘渲染测试：卡片、空状态与文件落盘。"""
from __future__ import annotations

import time
from pathlib import Path

from crypto_analyst import dashboard


def _records() -> list[dict]:
    now = time.time()
    return [
        {
            "epoch": now - (4 - index) * 3600,
            "ts": f"2026-10-23 0{index + 1}:00:00",
            "source": "watch",
            "symbol": "BTC",
            "price": 100.0 + index,
            "total_score": 40.0 + index * 2,
            "regime": "震荡" if index < 2 else "偏空",
            "scores": {
                "technical": 60.0,
                "positioning": 55.0,
                "liquidity": 50.0,
                "sentiment": 45.0,
                "macro": 50.0,
            },
        }
        for index in range(4)
    ]


def test_render_contains_sparkline_and_scores(monkeypatch):
    monkeypatch.setattr(dashboard.history, "iter_records", lambda: iter(_records()))
    page = dashboard.render(days=7)

    assert "链析 · 评分仪表盘" in page
    assert "BTC" in page
    assert "<svg" in page
    assert "技术面" in page
    assert "偏空" in page
    assert "快照 4 条" in page


def test_render_empty_state(monkeypatch):
    monkeypatch.setattr(dashboard.history, "iter_records", lambda: iter([]))
    page = dashboard.render()
    assert "暂无快照" in page


def test_render_filters_by_source(monkeypatch):
    records = _records()
    records[0]["source"] = "brief"
    monkeypatch.setattr(dashboard.history, "iter_records", lambda: iter(records))
    page = dashboard.render(source="watch")
    assert "快照 3 条" in page


def test_save_writes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard.history, "iter_records", lambda: iter(_records()))
    path = dashboard.save(out=str(tmp_path / "d.html"))
    assert Path(path).exists()
    assert "评分仪表盘" in Path(path).read_text("utf-8")


def test_render_includes_dimension_lines(monkeypatch):
    monkeypatch.setattr(dashboard.history, "iter_records", lambda: iter(_records()))
    page = dashboard.render(days=7)
    # 五维走势区域与图例
    assert "五维评分走势" in page
    assert "持仓面" in page
    assert "流动性" in page
    # 五条折线（每个维度一个 polyline，dims svg 内）
    assert page.count("class=\"dims\"") >= 1


def test_regime_change_markers_detected():
    rows = [
        {"regime": "震荡"},
        {"regime": "震荡"},
        {"regime": "偏空"},  # 切换
        {"regime": "偏空"},
        {"regime": "偏多"},  # 切换
    ]
    assert dashboard._regime_change_markers(rows) == [2, 4]


def test_regime_change_markers_empty_when_stable():
    rows = [{"regime": "震荡"}, {"regime": "震荡"}, {"regime": "震荡"}]
    assert dashboard._regime_change_markers(rows) == []


def test_sparkline_renders_markers():
    values = [40.0, 42.0, 38.0, 45.0]
    svg = dashboard._sparkline(values, "test", "#38bdf8", markers=[1, 3])
    # 两个标记圆环
    assert svg.count('stroke="#fbbf24"') == 2
