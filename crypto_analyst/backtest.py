"""评分回测雏形：用落盘快照检验"综合分 vs 未来收益"的关系。

思路：
1. 按标的把历史记录排序，对每条记录找 horizon 之后的第一条记录，得到未来收益；
2. 按综合分所处的状态分桶（复用 analysis.REGIME_THRESHOLDS），统计平均收益与胜率；
3. 计算 Pearson 相关系数，判断评分是否有方向性预测力。

纯离线计算，不访问网络——数据来自 history 落盘的 jsonl。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from . import analysis, history


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (vx ** 0.5 * vy ** 0.5)


def evaluate_pairs(records: Iterable[dict[str, Any]], horizon_hours: float = 24.0) -> list[dict[str, Any]]:
    """把历史记录配成 (当时综合分, horizon 后收益) 样本对。"""
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        if rec.get("epoch") is not None and rec.get("price") and rec.get("total_score") is not None:
            by_symbol[rec["symbol"]].append(rec)

    horizon_s = horizon_hours * 3600.0
    pairs: list[dict[str, Any]] = []
    for symbol, recs in by_symbol.items():
        recs.sort(key=lambda r: r["epoch"])
        epochs = [r["epoch"] for r in recs]
        for i, cur in enumerate(recs):
            target = cur["epoch"] + horizon_s
            nxt = None
            for j in range(i + 1, len(recs)):
                if epochs[j] >= target:
                    nxt = recs[j]
                    break
            if nxt is None or not nxt.get("price"):
                continue
            pairs.append(
                {
                    "symbol": symbol,
                    "ts": cur.get("ts"),
                    "score": float(cur["total_score"]),
                    "forward_pct": (nxt["price"] / cur["price"] - 1.0) * 100.0,
                }
            )
    return pairs


def summarize(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """分桶统计 + Pearson 相关。分桶边界复用 analysis.REGIME_THRESHOLDS。"""
    buckets: dict[str, list[float]] = {label: [] for _t, label in analysis.REGIME_THRESHOLDS}
    for p in pairs:
        buckets[analysis.regime_of(p["score"])].append(p["forward_pct"])

    bucket_rows = [
        {
            "regime": label,
            "count": len(values),
            "avg_pct": (sum(values) / len(values)) if values else None,
            "win_rate": (sum(1 for v in values if v > 0) / len(values) * 100.0) if values else None,
        }
        for (_t, label), values in zip(analysis.REGIME_THRESHOLDS, buckets.values())
    ]
    scores = [p["score"] for p in pairs]
    forwards = [p["forward_pct"] for p in pairs]
    return {
        "samples": len(pairs),
        "symbols": sorted({p["symbol"] for p in pairs}),
        "buckets": bucket_rows,
        "pearson": _pearson(scores, forwards),
    }


def _verdict(stats: dict[str, Any]) -> str:
    r = stats["pearson"]
    if stats["samples"] < 30:
        return "样本量不足 30 对，结论仅供观察——继续挂 watch 积累数据。"
    if r is None:
        return "样本方差不足，无法计算相关性。"
    if r >= 0.3:
        return f"Pearson r={r:.2f}，评分与未来收益呈较明显正相关，权重方向可信。"
    if r >= 0.1:
        return f"Pearson r={r:.2f}，弱正相关，可考虑微调权重后复测。"
    if r > -0.1:
        return f"Pearson r={r:.2f}，无显著线性关系，建议检查分桶收益是否单调。"
    return f"Pearson r={r:.2f}，呈反向关系，评分权重方向需要修正。"


def run(horizon_hours: float = 24.0) -> str:
    records = list(history.iter_records())
    pairs = evaluate_pairs(records, horizon_hours)
    stats = summarize(pairs)

    lines: list[str] = [f"评分回测（综合分 vs 未来 {horizon_hours:g}h 收益）"]
    if not pairs:
        lines.append("")
        lines.append("暂无可用样本对。先运行 watch / brief / snapshot 积累快照，")
        lines.append(f"且数据跨度需超过 {horizon_hours:g} 小时（当前目录：.state/snapshots/）。")
        return "\n".join(lines)

    span_days = 0.0
    epochs = [rec["epoch"] for rec in records if rec.get("epoch")]
    if epochs:
        span_days = (max(epochs) - min(epochs)) / 86400.0

    lines.append(f"样本：{stats['samples']} 对 ｜ 标的：{', '.join(stats['symbols'])} ｜ 数据跨度 {span_days:.1f} 天")
    lines.append("")
    lines.append("| 状态 | 样本 | 平均未来收益 | 胜率 |")
    lines.append("| --- | --- | --- | --- |")
    for row in stats["buckets"]:
        avg = f"{row['avg_pct']:+.2f}%" if row["avg_pct"] is not None else "—"
        win = f"{row['win_rate']:.0f}%" if row["win_rate"] is not None else "—"
        lines.append(f"| {row['regime']} | {row['count']} | {avg} | {win} |")
    lines.append("")
    lines.append(_verdict(stats))
    return "\n".join(lines)
