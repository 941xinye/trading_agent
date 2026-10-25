"""静态 HTML 仪表盘：把落盘快照渲染成可直接用浏览器打开的本地页面。

不依赖前端框架与外部资源（图表为内联 SVG），适合定时生成后本地查看或归档。
"""
from __future__ import annotations

import html
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, history

DIMENSION_LABELS = {
    "technical": "技术面",
    "positioning": "持仓面",
    "liquidity": "流动性",
    "sentiment": "情绪",
    "macro": "宏观",
}
DIMENSION_COLORS = {
    "technical": "#38bdf8",
    "positioning": "#a78bfa",
    "liquidity": "#34d399",
    "sentiment": "#fbbf24",
    "macro": "#fb7185",
}
REGIME_COLORS = {
    "强势多头": "#22c55e",
    "偏多": "#4ade80",
    "震荡": "#94a3b8",
    "偏空": "#fb7185",
    "弱势空头": "#ef4444",
}


def _records(days: int, source: str = "all") -> list[dict[str, Any]]:
    cutoff = time.time() - days * 86400
    return [
        rec
        for rec in history.iter_records()
        if rec.get("epoch", 0) >= cutoff
        and rec.get("price")
        and rec.get("total_score") is not None
        and (source == "all" or rec.get("source") == source)
    ]


def _price(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def _sparkline(values: list[float], gid: str, color: str, markers: list[int] | None = None) -> str:
    if not values:
        return ""
    if len(values) == 1:
        values = values * 2
    width, height, pad = 560.0, 140.0, 14.0
    low, high = min(values), max(values)
    if high - low < 1:
        low, high = low - 1, high + 1
    step = (width - 2 * pad) / (len(values) - 1)
    points = [
        (pad + index * step, height - pad - (value - low) / (high - low) * (height - 2 * pad))
        for index, value in enumerate(values)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = f"{pad:.1f},{height - pad:.1f} " + line + f" {width - pad:.1f},{height - pad:.1f}"
    last_x, last_y = points[-1]
    marker_dots = ""
    if markers:
        for idx in markers:
            if 0 <= idx < len(points):
                mx, my = points[idx]
                marker_dots += f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="5" fill="none" stroke="#fbbf24" stroke-width="2"/>'
    return f"""<svg class="spark" viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" role="img">
      <defs>
        <linearGradient id="g{gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="{color}" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <polygon points="{area}" fill="url(#g{gid})"/>
      <polyline points="{line}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
      {marker_dots}
      <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="{color}"/>
      <text x="{width - pad - 2}" y="{pad + 10}" text-anchor="end" class="axis">{high:.0f}</text>
      <text x="{width - pad - 2}" y="{height - pad - 4}" text-anchor="end" class="axis">{low:.0f}</text>
    </svg>"""


def _dimension_lines(rows: list[dict[str, Any]], gid: str) -> str:
    """五维评分走势折线图：同一坐标系叠加技术/持仓/流动性/情绪/宏观五条线。"""
    width, height, pad = 560.0, 120.0, 16.0
    # 收集所有维度分数，统一 Y 轴范围
    all_values: list[float] = []
    series: dict[str, list[float]] = {}
    for dim in DIMENSION_LABELS:
        values = [float(row.get("scores", {}).get(dim, 50.0)) for row in rows]
        series[dim] = values
        all_values.extend(values)
    if not all_values:
        return ""
    low, high = min(all_values), max(all_values)
    if high - low < 1:
        low, high = low - 1, high + 1
    n = len(rows)
    if n < 2:
        n = 2
    step = (width - 2 * pad) / (n - 1)

    lines = []
    for dim, label in DIMENSION_LABELS.items():
        values = series[dim]
        if len(values) == 1:
            values = values * 2
        pts = [
            (pad + i * step, height - pad - (v - low) / (high - low) * (height - 2 * pad))
            for i, v in enumerate(values)
        ]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        color = DIMENSION_COLORS[dim]
        lines.append(
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )

    legend = "".join(
        f'<span style="color:{DIMENSION_COLORS[d]}">{label}</span>'
        for d, label in DIMENSION_LABELS.items()
    )
    return f"""<svg class="dims" viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" role="img">
      {''.join(lines)}
      <text x="{width - pad - 2}" y="{pad + 8}" text-anchor="end" class="axis">{high:.0f}</text>
      <text x="{width - pad - 2}" y="{height - pad - 4}" text-anchor="end" class="axis">{low:.0f}</text>
    </svg>
    <div class="legend">{legend}</div>"""


def _bars(scores: dict[str, float]) -> str:
    rows = []
    for key, label in DIMENSION_LABELS.items():
        if key not in scores:
            continue
        value = float(scores[key])
        rows.append(
            f'<div class="row"><span>{label}</span>'
            f'<div class="track"><i style="width:{max(0.0, min(100.0, value)):.0f}%"></i></div>'
            f"<b>{value:.0f}</b></div>"
        )
    return "".join(rows)


def _regime_change_markers(rows: list[dict[str, Any]]) -> list[int]:
    """返回状态发生切换的快照索引，用于在综合分走势上标注。"""
    markers: list[int] = []
    prev_regime = None
    for i, row in enumerate(rows):
        regime = row.get("regime")
        if prev_regime is not None and regime and regime != prev_regime:
            markers.append(i)
        prev_regime = regime
    return markers


def _card(symbol: str, rows: list[dict[str, Any]]) -> str:
    latest = rows[-1]
    first = rows[0]
    diff = latest["total_score"] - first["total_score"]
    color = REGIME_COLORS.get(latest.get("regime") or "", "#38bdf8")
    change_24h = (
        (latest["price"] / rows[-2]["price"] - 1) * 100 if len(rows) > 1 and rows[-2].get("price") else None
    )
    change_text = f"{change_24h:+.2f}%" if change_24h is not None else "—"
    change_class = "up" if (change_24h or 0) > 0 else ("down" if (change_24h or 0) < 0 else "flat")
    gid = "".join(ch for ch in symbol if ch.isalnum())
    markers = _regime_change_markers(rows)
    marker_note = f'<div class="marker-note">● 状态切换 {len(markers)} 次</div>' if markers else ""
    return f"""<section class="card">
      <header>
        <div>
          <h2>{html.escape(symbol)}<small>{html.escape(str(latest.get("regime") or "—"))}</small></h2>
          <div class="sub">最近 {len(rows)} 条快照 ｜ {html.escape(str(latest.get("ts") or ""))}</div>
        </div>
        <div class="score">
          <strong>{latest["total_score"]:.1f}</strong>
          <span class="{change_class}">区间 {diff:+.1f}</span>
        </div>
      </header>
      <div class="price">最新价 {_price(latest["price"])} <span class="{change_class}">{change_text}</span></div>
      <div class="chart-label">综合分走势 {marker_note}</div>
      {_sparkline([float(row["total_score"]) for row in rows], gid, color, markers=markers)}
      <div class="chart-label">五维评分走势</div>
      {_dimension_lines(rows, gid + "d")}
      <div class="bars">{_bars(latest.get("scores") or {})}</div>
    </section>"""


PAGE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 24px 48px; background: #0b1220; color: #e2e8f0;
  font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; }
.wrap { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 8px; letter-spacing: .5px; }
.meta { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 18px; }
.card { background: linear-gradient(180deg, #131c2e, #0e1626); border: 1px solid #1f2b3f;
  border-radius: 14px; padding: 18px; }
.card header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.card h2 { margin: 0; font-size: 18px; display: flex; align-items: center; gap: 8px; }
.card h2 small { font-size: 12px; font-weight: 500; color: #cbd5e1; background: #1e293b;
  padding: 2px 8px; border-radius: 999px; }
.sub { color: #7c8aa0; font-size: 12px; margin-top: 6px; }
.score { text-align: right; }
.score strong { display: block; font-size: 30px; line-height: 1; }
.score span { font-size: 12px; }
.price { font-size: 13px; color: #a9b6c9; margin: 14px 0 4px; }
.up { color: #4ade80; } .down { color: #fb7185; } .flat { color: #94a3b8; }
.spark { width: 100%; height: 132px; display: block; }
.axis { fill: #64748b; font-size: 11px; }
.chart-label { font-size: 11px; color: #64748b; margin: 10px 0 2px; display: flex; justify-content: space-between; }
.marker-note { color: #fbbf24; font-size: 11px; }
.dims { width: 100%; height: 110px; display: block; margin-top: 2px; }
.legend { display: flex; gap: 12px; font-size: 11px; margin-top: 2px; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 4px; }
.legend span::before { content: ""; display: inline-block; width: 14px; height: 2px; background: currentColor; border-radius: 1px; }
.bars { margin-top: 10px; display: grid; gap: 6px; }
.row { display: grid; grid-template-columns: 52px 1fr 30px; align-items: center; gap: 8px;
  font-size: 12px; color: #a9b6c9; }
.track { height: 6px; background: #1c2839; border-radius: 999px; overflow: hidden; }
.track i { display: block; height: 100%; background: linear-gradient(90deg, #38bdf8, #22d3ee); }
.row b { color: #e2e8f0; font-weight: 500; text-align: right; }
.empty { border: 1px dashed #334155; border-radius: 14px; padding: 48px; text-align: center; color: #94a3b8; }
footer { margin-top: 28px; color: #64748b; font-size: 12px; line-height: 1.7; }
"""


def render(days: int = 7, source: str = "all") -> str:
    records = _records(days, source)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        grouped[rec["symbol"]].append(rec)

    cards = []
    for symbol, rows in sorted(
        grouped.items(),
        key=lambda pair: pair[1][-1]["total_score"],
        reverse=True,
    ):
        rows.sort(key=lambda rec: rec["epoch"])
        cards.append(_card(symbol, rows))

    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = (
        "\n".join(cards)
        if cards
        else '<div class="empty">暂无快照。运行 <code>watch</code> 或 <code>brief</code> 积累数据后重新生成。</div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>链析 · 评分仪表盘</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>链析 · 评分仪表盘</h1>
  <div class="meta">生成时间 {generated} ｜ 回看 {days} 天 ｜ 来源 {source} ｜ 快照 {len(records)} 条 ｜ 标的 {len(cards)} 个</div>
  <div class="grid">
    {body}
  </div>
  <footer>
    数据来自本地落盘快照（.state/snapshots），走势为综合分序列，不是价格走势。<br>
    评分由程序化指标生成，仅用于研究与信息参考，不构成投资建议。
  </footer>
</div>
</body>
</html>
"""


def save(days: int = 7, source: str = "all", out: str | None = None) -> str:
    path = Path(out) if out else config.REPORT_DIR / "dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(days=days, source=source), "utf-8")
    return str(path)
