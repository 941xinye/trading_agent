"""评分回测：用落盘快照检验"综合分 vs 未来收益"的关系。

思路：
1. 按标的把历史记录排序，**按 horizon 切分非重叠窗口**：每个窗口只取首条记录作为样本，
   窗口末端之后的第一条记录作为未来价格，避免"每 5 分钟一条记录"这种重叠样本
   把相关性算虚；
2. 未来价格必须落在目标时刻附近（默认允许 0.5×horizon 的滞后），超出说明数据断档，
   该窗口丢弃，避免把 3 天收益当成 24h 收益；
3. 按综合分所处的状态分桶（复用 analysis.REGIME_THRESHOLDS），统计平均收益与胜率；
4. 计算 Pearson 相关系数，判断评分是否有方向性预测力。

纯离线计算，不访问网络——数据来自 history 落盘的 jsonl。
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from typing import Any, Iterable

from . import analysis, history

# 未来价格允许的最大滞后：默认半个 horizon（至少 10 分钟）
SLACK_RATIO = 0.5
MIN_SLACK_S = 600.0


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


def evaluate_pairs(
    records: Iterable[dict[str, Any]],
    horizon_hours: float = 24.0,
    slack_hours: float | None = None,
) -> list[dict[str, Any]]:
    """把历史记录配成 (当时综合分, horizon 后收益) 样本对。

    每个 horizon 窗口只产出一个样本：窗口起点取该窗口内的第一条记录，
    随后跳过整个窗口，保证样本之间不重叠。
    """
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        if rec.get("epoch") is not None and rec.get("price") and rec.get("total_score") is not None:
            by_symbol[rec["symbol"]].append(rec)

    horizon_s = horizon_hours * 3600.0
    slack_s = (
        slack_hours * 3600.0
        if slack_hours is not None
        else max(horizon_s * SLACK_RATIO, MIN_SLACK_S)
    )
    pairs: list[dict[str, Any]] = []
    for symbol, recs in by_symbol.items():
        recs.sort(key=lambda r: r["epoch"])
        epochs = [r["epoch"] for r in recs]
        i = 0
        while i < len(recs):
            cur = recs[i]
            target = cur["epoch"] + horizon_s
            j = bisect_left(epochs, target, lo=i + 1)
            if j >= len(recs):
                break
            nxt = recs[j]
            lag_s = nxt["epoch"] - target
            if lag_s > slack_s:
                # 数据断档：该窗口没有可信的未来价格，从断档后的首条记录重新开窗
                i = j
                continue
            pairs.append(
                {
                    "symbol": symbol,
                    "ts": cur.get("ts"),
                    "score": float(cur["total_score"]),
                    "forward_pct": (nxt["price"] / cur["price"] - 1.0) * 100.0,
                    "lag_hours": lag_s / 3600.0,
                }
            )
            # recs[j] 已落在本窗口末端之后，作为下一个窗口的起点，避免样本重叠
            i = j
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
        return "有效窗口不足 30 个，结论仅供观察——继续挂 watch 积累数据（每标的每 horizon 每天最多 1 个样本）。"
    if r is None:
        return "样本方差不足，无法计算相关性。"
    if r >= 0.3:
        return f"Pearson r={r:.2f}，评分与未来收益呈较明显正相关，权重方向可信。"
    if r >= 0.1:
        return f"Pearson r={r:.2f}，弱正相关，可考虑微调权重后复测。"
    if r > -0.1:
        return f"Pearson r={r:.2f}，无显著线性关系，建议检查分桶收益是否单调。"
    return f"Pearson r={r:.2f}，呈反向关系，评分权重方向需要修正。"


def run(horizon_hours: float = 24.0, source: str = "watch") -> str:
    all_records = list(history.iter_records())
    if source == "all":
        records = all_records
    else:
        records = [rec for rec in all_records if rec.get("source") == source]

    pairs = evaluate_pairs(records, horizon_hours)
    stats = summarize(pairs)

    lines: list[str] = [
        f"评分回测（来源 {source} ｜ 综合分 vs 未来 {horizon_hours:g}h 收益 ｜ 非重叠窗口采样）"
    ]
    ignored = len(all_records) - len(records)
    if ignored:
        lines.append(f"已忽略 {ignored} 条非「{source}」来源记录（全量统计可用 --source all）。")
    if not pairs:
        lines.append("")
        lines.append("暂无可用样本对。先运行 watch / brief / snapshot 积累快照，")
        lines.append(f"且同一标的的数据跨度需超过 {horizon_hours:g} 小时（当前目录：.state/snapshots/）。")
        return "\n".join(lines)

    span_days = 0.0
    epochs = [rec["epoch"] for rec in records if rec.get("epoch")]
    if epochs:
        span_days = (max(epochs) - min(epochs)) / 86400.0

    lines.append("")
    lines.append(
        f"样本：{stats['samples']} 个窗口 ｜ 标的：{', '.join(stats['symbols'])} ｜ 数据跨度 {span_days:.1f} 天"
    )
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
