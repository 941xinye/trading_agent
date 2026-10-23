"""评分回测：用落盘快照检验"综合分 vs 未来收益"的关系。

思路：
1. 按标的把历史记录排序，**按 horizon 切分非重叠窗口**：每个窗口只取首条记录作为样本，
   窗口末端之后的第一条记录作为未来价格，避免"每 5 分钟一条记录"这种重叠样本
   把相关性算虚；
2. 未来价格必须落在目标时刻附近（默认允许 0.5×horizon 的滞后），超出说明数据断档，
   该窗口丢弃，避免把 3 天收益当成 24h 收益；
3. 同时给出两类分桶：状态桶（复用 analysis.REGIME_THRESHOLDS，解释直观）与
   分位桶（按综合分五等分，样本不足时也不会出现空桶）；
4. 计算 Pearson 与 Spearman（IC）、整体胜率、盈亏比、平均最大不利偏移，
   并支持权重敏感性扫描，判断 DIMENSION_WEIGHTS 是否偏离最优。

纯离线计算，不访问网络——数据来自 history 落盘的 jsonl。
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from typing import Any, Iterable

from . import analysis, config, history

# 未来价格允许的最大滞后：默认半个 horizon（至少 10 分钟）
SLACK_RATIO = 0.5
MIN_SLACK_S = 600.0
# 分位桶数量：五等分在 30 个窗口下每桶约 6 个样本，再多就没有统计意义
QUANTILE_BUCKETS = 5
# 权重敏感性扫描的单次调整幅度
WEIGHT_STEP = 0.05
# 权重建议：单维度最低权重（避免 IC 为负的维度被直接归零）
MIN_DIM_WEIGHT = 0.05
# 权重建议：单维度权重上限，避免少数样本把某一维度推到极端
MAX_DIM_WEIGHT = 0.50
# 权重建议所需的最少有效窗口数，与回测结论口径保持一致
MIN_RECOMMEND_SAMPLES = 30


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


def _average_ranks(values: list[float]) -> list[float]:
    """返回平均化秩次（并列值共享平均秩），用于 Spearman。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """秩相关系数（业内称呼 IC），对极端收益不敏感。"""
    if len(xs) < 3:
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


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
            window = recs[i : j + 1]
            low = min(r["price"] for r in window)
            pairs.append(
                {
                    "symbol": symbol,
                    "ts": cur.get("ts"),
                    "score": float(cur["total_score"]),
                    "dim_scores": cur.get("scores") or {},
                    "forward_pct": (nxt["price"] / cur["price"] - 1.0) * 100.0,
                    "lag_hours": lag_s / 3600.0,
                    "mae_pct": (low / cur["price"] - 1.0) * 100.0,
                }
            )
            # recs[j] 已落在本窗口末端之后，作为下一个窗口的起点，避免样本重叠
            i = j
    return pairs


def _win_rate(values: list[float]) -> float | None:
    return (sum(1 for v in values if v > 0) / len(values) * 100.0) if values else None


def _quantile_rows(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(pairs)
    if n < QUANTILE_BUCKETS:
        return []
    ordered = sorted(pairs, key=lambda p: p["score"])
    rows: list[dict[str, Any]] = []
    for q in range(QUANTILE_BUCKETS):
        chunk = ordered[round(q * n / QUANTILE_BUCKETS) : round((q + 1) * n / QUANTILE_BUCKETS)]
        if not chunk:
            continue
        values = [p["forward_pct"] for p in chunk]
        rows.append(
            {
                "label": f"Q{q + 1}",
                "low": chunk[0]["score"],
                "high": chunk[-1]["score"],
                "count": len(values),
                "avg_pct": sum(values) / len(values),
                "win_rate": _win_rate(values),
            }
        )
    return rows


def summarize(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """状态桶 + 分位桶 + 相关性 + 收益质量指标。"""
    regime_values: dict[str, list[float]] = {label: [] for _t, label in analysis.REGIME_THRESHOLDS}
    regime_mae: dict[str, list[float]] = {label: [] for _t, label in analysis.REGIME_THRESHOLDS}
    for p in pairs:
        label = analysis.regime_of(p["score"])
        regime_values[label].append(p["forward_pct"])
        regime_mae[label].append(p.get("mae_pct") or 0.0)

    bucket_rows = [
        {
            "regime": label,
            "count": len(values),
            "avg_pct": (sum(values) / len(values)) if values else None,
            "win_rate": _win_rate(values),
            "avg_mae": (sum(regime_mae[label]) / len(regime_mae[label])) if values else None,
        }
        for (_t, label), values in zip(analysis.REGIME_THRESHOLDS, regime_values.values())
    ]

    scores = [p["score"] for p in pairs]
    forwards = [p["forward_pct"] for p in pairs]
    wins = [v for v in forwards if v > 0]
    losses = [v for v in forwards if v <= 0]
    total_loss = abs(sum(losses))
    return {
        "samples": len(pairs),
        "symbols": sorted({p["symbol"] for p in pairs}),
        "buckets": bucket_rows,
        "quantiles": _quantile_rows(pairs),
        "pearson": _pearson(scores, forwards),
        "spearman": _spearman(scores, forwards),
        "hit_rate": _win_rate(forwards),
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "profit_factor": (sum(wins) / total_loss) if total_loss else None,
        "avg_mae": (sum(p.get("mae_pct") or 0.0 for p in pairs) / len(pairs)) if pairs else None,
    }


def _verdict(stats: dict[str, Any]) -> str:
    r = stats["pearson"]
    ic = stats["spearman"]
    if stats["samples"] < 30:
        return "有效窗口不足 30 个，结论仅供观察——继续挂 watch 积累数据（每标的每 horizon 每天最多 1 个样本）。"
    if r is None:
        return "样本方差不足，无法计算相关性。"
    trend = f"Pearson r={r:.2f}"
    if ic is not None:
        trend += f" / Spearman IC={ic:.2f}"
    if r >= 0.3:
        return f"{trend}，评分与未来收益呈较明显正相关，权重方向可信。"
    if r >= 0.1:
        return f"{trend}，弱正相关，可考虑微调权重后复测。"
    if r > -0.1:
        return f"{trend}，无显著线性关系，建议检查分位桶收益是否单调。"
    return f"{trend}，呈反向关系，评分权重方向需要修正。"


def _pct_text(value: float | None, nd: int = 2) -> str:
    return "—" if value is None else f"{value:+.{nd}f}%"


def _section(horizon_hours: float, pairs: list[dict[str, Any]], span_days: float) -> list[str]:
    stats = summarize(pairs)
    lines: list[str] = [f"### 未来 {horizon_hours:g}h", ""]
    if not pairs:
        lines.append("该窗口没有可用样本（数据跨度不足或断档）。")
        lines.append("")
        return lines

    lines.append(
        f"样本 {stats['samples']} 个窗口 ｜ 标的 {', '.join(stats['symbols'])} ｜ 数据跨度 {span_days:.1f} 天"
    )
    lines.append("")
    lines.append("| 状态 | 窗口 | 平均收益 | 胜率 | 平均最大不利偏移 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in stats["buckets"]:
        lines.append(
            f"| {row['regime']} | {row['count']} | {_pct_text(row['avg_pct'])} | "
            f"{_pct_text(row['win_rate'], 0) if row['win_rate'] is not None else '—'} | "
            f"{_pct_text(row['avg_mae'])} |"
        )

    if stats["quantiles"]:
        lines.append("")
        lines.append("| 分位桶（按综合分五等分） | 分数区间 | 窗口 | 平均收益 | 胜率 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in stats["quantiles"]:
            lines.append(
                f"| {row['label']} | {row['low']:.1f}–{row['high']:.1f} | {row['count']} | "
                f"{_pct_text(row['avg_pct'])} | {_pct_text(row['win_rate'], 0)} |"
            )

    lines.append("")
    corr = f"Pearson r={stats['pearson']:.2f}" if stats["pearson"] is not None else "Pearson r=—"
    ic = f"Spearman IC={stats['spearman']:.2f}" if stats["spearman"] is not None else "Spearman IC=—"
    if stats["hit_rate"] is None:
        quality = "整体胜率 —"
    else:
        quality = (
            f"整体胜率 {stats['hit_rate']:.0f}% ｜ "
            f"均盈 {_pct_text(stats['avg_win'])} / 均亏 {_pct_text(stats['avg_loss'])}"
        )
        if stats["profit_factor"] is not None:
            quality += f" ｜ 盈亏比 {stats['profit_factor']:.2f}"
        else:
            quality += " ｜ 盈亏比 —（无亏损样本）"
    lines.append(f"{corr} ｜ {ic} ｜ {quality}")
    lines.append("")
    lines.append(_verdict(stats))
    lines.append("")
    return lines


def _normalize_horizons(value: float | Iterable[float]) -> list[float]:
    if isinstance(value, (int, float)):
        return [float(value)]
    horizons = [float(v) for v in value]
    return list(dict.fromkeys(horizons)) or [24.0]


def _rebalanced(dimension: str, delta: float) -> dict[str, float]:
    """把某一维度权重调整 delta，其余维度按比例缩放，保证总和仍为 1。"""
    weights = dict(config.DIMENSION_WEIGHTS)
    weights[dimension] = min(0.8, max(0.05, weights[dimension] + delta))
    rest = sum(v for k, v in weights.items() if k != dimension)
    target = 1.0 - weights[dimension]
    if rest <= 0:
        return weights
    factor = target / rest
    return {k: (weights[dimension] if k == dimension else v * factor) for k, v in weights.items()}


def _rescore(records: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in records:
        scores = rec.get("scores") or {}
        if not all(dim in scores for dim in weights):
            continue
        total = sum(scores[dim] * weights[dim] for dim in weights)
        out.append({**rec, "total_score": total})
    return out


def weight_sensitivity(
    records: list[dict[str, Any]],
    horizon_hours: float = 24.0,
) -> list[str]:
    """扫描各维度权重 ±5pp 对相关系数的影响，用于判断当前权重是否偏置。"""
    usable = [
        rec
        for rec in records
        if rec.get("scores") and all(dim in rec["scores"] for dim in config.DIMENSION_WEIGHTS)
    ]
    lines: list[str] = [f"### 权重敏感性 · 未来 {horizon_hours:g}h", ""]
    if len(usable) < 3:
        lines.append("带分维度分数的快照不足 3 条，暂不能做权重扫描。")
        lines.append("")
        return lines

    base_pairs = evaluate_pairs(_rescore(usable, config.DIMENSION_WEIGHTS), horizon_hours)
    base_r = _pearson([p["score"] for p in base_pairs], [p["forward_pct"] for p in base_pairs])
    if base_r is None:
        lines.append("基线样本不足或方差为 0，暂不能做权重扫描。")
        lines.append("")
        return lines

    rows: list[tuple[str, str, float | None, float | None]] = []
    for dimension in config.DIMENSION_WEIGHTS:
        for delta in (-WEIGHT_STEP, WEIGHT_STEP):
            pairs = evaluate_pairs(_rescore(usable, _rebalanced(dimension, delta)), horizon_hours)
            r = _pearson([p["score"] for p in pairs], [p["forward_pct"] for p in pairs])
            rows.append(
                (dimension, f"{delta:+.0%}", r, (r - base_r) if r is not None else None)
            )

    lines.append(f"基线：Pearson r={base_r:.3f}（样本 {len(base_pairs)} 个窗口）")
    lines.append("")
    lines.append("| 维度 | 权重调整 | Pearson r | Δr |")
    lines.append("| --- | --- | --- | --- |")
    for dimension, delta, r, diff in rows:
        r_text = f"{r:.3f}" if r is not None else "—"
        diff_text = f"{diff:+.3f}" if diff is not None else "—"
        lines.append(f"| {dimension} | {delta} | {r_text} | {diff_text} |")

    valid = [(dimension, delta, diff) for dimension, delta, _r, diff in rows if diff is not None]
    lines.append("")
    if not valid:
        lines.append("各方向都无法计算相关系数，样本量还不够。")
    else:
        best_dim, best_delta, best_diff = max(valid, key=lambda item: item[2])
        if best_diff <= 0.01:
            lines.append("所有 ±5pp 调整都没有带来明显提升，当前权重处于相对稳定区间。")
        else:
            lines.append(
                f"最大提升来自 {best_dim} 权重 {best_delta}（Δr={best_diff:+.3f}），"
                "可在积累更多样本后优先复测该维度。"
            )
    lines.append("")
    return lines


def _round_weights(weights: dict[str, float]) -> dict[str, float]:
    """保留两位小数，并把四舍五入误差补到最大项，保证总和为 1。"""
    rounded = {dim: round(value, 2) for dim, value in weights.items()}
    residual = round(1.0 - sum(rounded.values()), 2)
    if residual:
        biggest = max(rounded, key=lambda dim: rounded[dim])
        rounded[biggest] = round(rounded[biggest] + residual, 2)
    return rounded


def recommend_weights(
    records: list[dict[str, Any]],
    horizon_hours: float = 24.0,
) -> dict[str, Any]:
    """按各维度 IC 给出推荐权重。

    与回测使用同一批非重叠窗口样本，分别计算「各维度分数 vs 未来收益」的 Spearman IC：
    只有 IC 明显超出抽样噪声带（约 1/√n，且至少高 5pp）的维度参与权重分配，
    其余维度保留最低权重；样本不足、或所有维度都只有噪声级 IC 时不给出建议。
    """
    usable = [
        rec
        for rec in records
        if rec.get("scores") and all(dim in rec["scores"] for dim in config.DIMENSION_WEIGHTS)
    ]
    pairs = evaluate_pairs(usable, horizon_hours)
    forwards = [p["forward_pct"] for p in pairs]
    ics: dict[str, float | None] = {}
    if pairs:
        for dimension in config.DIMENSION_WEIGHTS:
            values = [float(p["dim_scores"][dimension]) for p in pairs]
            ics[dimension] = _spearman(values, forwards)

    def _rows(suggested: dict[str, float] | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for dimension, current in config.DIMENSION_WEIGHTS.items():
            target = suggested.get(dimension) if suggested else None
            rows.append(
                {
                    "dimension": dimension,
                    "current": current,
                    "ic": ics.get(dimension),
                    "suggested": target,
                    "delta": (target - current) if target is not None else None,
                }
            )
        return rows

    if len(pairs) < MIN_RECOMMEND_SAMPLES:
        return {
            "samples": len(pairs),
            "ready": False,
            "rows": _rows(None),
            "suggested": {},
            "note": (
                f"有效窗口 {len(pairs)} 个，不足 {MIN_RECOMMEND_SAMPLES} 个，"
                "暂不给出权重建议；继续挂 watch 积累样本。"
            ),
        }

    # 样本量有限时，Spearman IC 本身有约 1/√n 的抽样噪声；只有明显超出噪声带
    # （至少再高 MIN_DIM_WEIGHT）的维度才算有效信号，否则给任何权重都是过拟合。
    noise = 1.0 / (len(pairs) - 1) ** 0.5
    excess = {
        dimension: max((ic - noise), 0.0) if ic is not None else 0.0
        for dimension, ic in ics.items()
    }
    if max(excess.values()) < MIN_DIM_WEIGHT:
        return {
            "samples": len(pairs),
            "ready": False,
            "rows": _rows(None),
            "suggested": {},
            "note": (
                f"各维度 IC 相对噪声带 ±{noise:.2f} 的优势不足 {MIN_DIM_WEIGHT:.0%}"
                f"（{len(pairs)} 个窗口），当前样本无法区分维度优劣，建议维持现有权重并继续积累数据。"
            ),
        }

    raw = {
        dimension: max(value, MIN_DIM_WEIGHT) for dimension, value in excess.items()
    }
    total = sum(raw.values())
    weights = {dimension: value / total for dimension, value in raw.items()}

    # 单维度上限，避免少数样本把某一维度推到极端权重
    heavy = max(weights, key=lambda dimension: weights[dimension])
    if weights[heavy] > MAX_DIM_WEIGHT:
        excess = weights[heavy] - MAX_DIM_WEIGHT
        weights[heavy] = MAX_DIM_WEIGHT
        rest = [dimension for dimension in weights if dimension != heavy]
        rest_total = sum(weights[dimension] for dimension in rest)
        for dimension in rest:
            weights[dimension] += excess * weights[dimension] / rest_total

    suggested = _round_weights(weights)
    return {
        "samples": len(pairs),
        "ready": True,
        "rows": _rows(suggested),
        "suggested": suggested,
        "note": (
            f"建议权重按各维度 IC 超出噪声带 {noise:.2f} 的部分占比分配，"
            f"其余维度保留 {MIN_DIM_WEIGHT:.0%} 下限、单维度上限 {MAX_DIM_WEIGHT:.0%}；"
            "建议先小步调整（每次 5-10pp）并继续积累样本复测。"
        ),
    }


def _recommend_section(records: list[dict[str, Any]], horizon_hours: float) -> list[str]:
    result = recommend_weights(records, horizon_hours)
    lines: list[str] = [f"### 权重建议 · 未来 {horizon_hours:g}h", ""]
    lines.append(f"有效窗口 {result['samples']} 个（与回测同口径的非重叠样本）")
    lines.append("")
    lines.append("| 维度 | 当前权重 | 维度 IC | 建议权重 | Δ |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in result["rows"]:
        ic_text = f"{row['ic']:+.2f}" if row["ic"] is not None else "—"
        suggested_text = f"{row['suggested']:.0%}" if row["suggested"] is not None else "—"
        delta_text = f"{row['delta']:+.0%}" if row["delta"] is not None else "—"
        lines.append(
            f"| {row['dimension']} | {row['current']:.0%} | {ic_text} | {suggested_text} | {delta_text} |"
        )
    lines.append("")
    lines.append(result["note"])
    if result["ready"]:
        body = ", ".join(
            f'"{dimension}": {result["suggested"][dimension]:.2f}'
            for dimension in config.DIMENSION_WEIGHTS
        )
        lines.append("")
        lines.append("可直接替换 `config.py` 中的 `DIMENSION_WEIGHTS`：")
        lines.append("")
        lines.append("```python")
        lines.append(f"DIMENSION_WEIGHTS = {{{body}}}")
        lines.append("```")
    lines.append("")
    return lines


def run(
    horizon_hours: float | Iterable[float] = 24.0,
    source: str = "watch",
    sweep_weights: bool = False,
    recommend: bool = False,
    records: list[dict[str, Any]] | None = None,
) -> str:
    horizons = _normalize_horizons(horizon_hours)
    all_records = records if records is not None else list(history.iter_records())
    if source == "all":
        records = all_records
    else:
        records = [rec for rec in all_records if rec.get("source") == source]

    horizon_text = "/".join(f"{h:g}h" for h in horizons)
    lines: list[str] = [
        f"评分回测（来源 {source} ｜ 综合分 vs 未来 {horizon_text} 收益 ｜ 非重叠窗口采样）"
    ]
    ignored = len(all_records) - len(records)
    if ignored:
        lines.append(f"已忽略 {ignored} 条非「{source}」来源记录（全量统计可用 --source all）。")

    if not records:
        lines.append("")
        lines.append("暂无可用样本对。先运行 watch / brief / snapshot 积累快照，")
        lines.append(f"且同一标的的数据跨度需超过 {max(horizons):g} 小时（当前目录：.state/snapshots/）。")
        return "\n".join(lines)

    epochs = [rec["epoch"] for rec in records if rec.get("epoch")]
    span_days = (max(epochs) - min(epochs)) / 86400.0 if epochs else 0.0

    sections: list[list[str]] = []
    for horizon in horizons:
        sections.append(_section(horizon, evaluate_pairs(records, horizon), span_days))

    lines.append("")
    for section in sections:
        lines.extend(section)

    if all(len(section) <= 4 for section in sections):
        lines.append("暂无可用样本对。先运行 watch / brief / snapshot 积累快照，")
        lines.append(f"且同一标的的数据跨度需超过 {max(horizons):g} 小时（当前目录：.state/snapshots/）。")

    if sweep_weights:
        lines.append("")
        for horizon in horizons:
            lines.extend(weight_sensitivity(records, horizon))
    if recommend:
        lines.append("")
        for horizon in horizons:
            lines.extend(_recommend_section(records, horizon))
    return "\n".join(lines)
