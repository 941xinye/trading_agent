"""简报生成：规则引擎负责事实与结构，LLM 只负责撰写点评。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import analysis, config
from .providers import market

TREND_LABELS = {"up": "多头", "down": "空头", "range": "震荡"}
STRUCTURE_LABELS = {
    "higher_highs_higher_lows": "更高高点+更高低点",
    "lower_highs_lower_lows": "更低高点+更低低点",
    "mixed": "结构混乱",
}
DIMENSION_LABELS = {
    "technical": "技术面",
    "positioning": "持仓面",
    "liquidity": "流动性",
    "sentiment": "情绪",
    "macro": "宏观",
}


def _price(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 10:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def _pct(value: float | None, nd: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:+.{nd}f}%"


def _compact_usd(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def _one_liner(item: analysis.SymbolSnapshot) -> str:
    day = item.timeframes.get("1d")
    h4 = item.timeframes.get("4h")
    parts: list[str] = []
    if day and h4:
        if day.trend == "up" and h4.trend == "up":
            parts.append("日线与4h同向多头")
        elif day.trend == "down" and h4.trend == "down":
            parts.append("日线与4h同向空头")
        elif day.trend != h4.trend:
            parts.append("周期方向冲突，以日线为准")
    if item.flags:
        parts.append(item.flags[0])
    elif day and day.rsi is not None:
        parts.append(f"日线 RSI {day.rsi:.0f}")
    return "；".join(parts) if parts else "无显著信号"


def _thermometer_rows(snapshot: analysis.MarketSnapshot) -> list[str]:
    rows: list[str] = []
    stable = snapshot.onchain
    if stable:
        note = "增量资金流入" if (stable.get("change_7d_pct") or 0) > 0.5 else (
            "增量资金流出" if (stable.get("change_7d_pct") or 0) < -0.5 else "资金面平稳"
        )
        rows.append(
            f"| 稳定币总供应 | {_compact_usd(stable.get('total_usd'))} | 7d {_pct(stable.get('change_7d_pct'))} | {note} |"
        )

    fng = snapshot.sentiment
    if fng:
        rows.append(
            f"| 恐慌贪婪指数 | {fng.get('value')}（{fng.get('label')}） | 1d {fng.get('change_1d')} | 30日均值 {fng.get('avg_30d')} |"
        )

    macro_data = snapshot.macro
    dxy = macro_data.get("dxy")
    if dxy:
        direction = "美元走强，压制风险资产" if (dxy.get("change_5d_pct") or 0) > 0.5 else (
            "美元走弱，利好风险资产" if (dxy.get("change_5d_pct") or 0) < -0.5 else "美元窄幅波动"
        )
        rows.append(
            f"| 美元指数 | {dxy.get('last'):.2f} | 5d {_pct(dxy.get('change_5d_pct'))} | {direction} |"
        )
    ndx = macro_data.get("ndx")
    if ndx:
        direction = "科技股风险偏好回升" if (ndx.get("change_5d_pct") or 0) > 1 else (
            "科技股风险偏好回落" if (ndx.get("change_5d_pct") or 0) < -1 else "科技股横盘"
        )
        rows.append(
            f"| 纳斯达克100 | {ndx.get('last'):,.0f} | 5d {_pct(ndx.get('change_5d_pct'))} | {direction} |"
        )

    btc = snapshot.btc_network
    if btc:
        blocks_time = btc.get("minutes_between_blocks")
        blocks_time_text = f"{blocks_time:.1f}" if isinstance(blocks_time, (int, float)) else "—"
        rows.append(
            f"| BTC 全网算力 | {btc.get('hash_rate_zh', 0):.2f} ZH/s | 24h 出块 {btc.get('blocks_mined_24h') or '—'} | 平均出块 {blocks_time_text} 分钟 |"
        )
        if btc.get("mempool_size_mb") is not None:
            rows.append(
                f"| BTC 内存池 | {btc.get('mempool_size_mb')} MB | 快速费率 {btc.get('fee_fast_sat_vb')} sat/vB | 链上结算需求 |"
            )
    return rows


def _symbol_section(item: analysis.SymbolSnapshot) -> list[str]:
    lines: list[str] = []
    lines.append(f"### {item.symbol}/{config.QUOTE} — {item.regime} · 综合 {item.total_score:.1f}/100")
    lines.append("")
    lines.append(
        f"最新价 **{_price(item.price)}** ｜ 24h **{_pct(item.change_24h_pct)}** ｜ 24h 成交额 **{_compact_usd(item.quote_volume_24h)}**"
    )
    lines.append("")
    lines.append("| 周期 | 趋势 | RSI | MACD柱(占价格%) | 量比 | 结构 | 支撑 | 阻力 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for timeframe in config.TIMEFRAMES:
        view = item.timeframes.get(timeframe)
        if not view:
            continue
        rsi_text = f"{view.rsi:.1f}" if view.rsi is not None else "—"
        macd_text = f"{view.macd_hist / view.close * 100:+.3f}%" if view.macd_hist is not None and view.close else "—"
        vol_text = f"{view.vol_ratio:.2f}x" if view.vol_ratio else "—"
        lines.append(
            f"| {timeframe} | {TREND_LABELS.get(view.trend, view.trend)} | {rsi_text} | {macd_text} | "
            f"{vol_text} | {STRUCTURE_LABELS.get(view.structure, view.structure)} | "
            f"{_price(view.support)} | {_price(view.resistance)} |"
        )

    score_bits = " ｜ ".join(
        f"{DIMENSION_LABELS[key]} {value:.0f}" for key, value in item.scores.items()
    )
    lines.append("")
    lines.append(f"评分拆解：{score_bits}")

    deriv = item.derivatives
    if deriv:
        percentile = deriv.get("funding_percentile")
        percentile_text = f"{percentile:.0f}%" if percentile is not None else "—"
        lines.append(
            f"合约结构：资金费率 {deriv.get('funding_rate', 0) * 100:+.3f}%"
            f"（年化 {deriv.get('funding_annualized_pct', 0):.1f}%，90日分位 {percentile_text}）"
            f" ｜ 持仓量 {_compact_usd(deriv.get('open_interest_usd'))}"
            f"（24h {_pct(deriv.get('oi_change_24h_pct'))}）"
            f" ｜ 账户多空比 {deriv.get('lsr_account') or 0:.2f}"
            f" ｜ 大户多空比 {deriv.get('top_lsr_size') or 0:.2f}"
        )

    levels = item.key_levels
    if any(levels.values()):
        lines.append(
            f"关键价位：日线支撑 {_price(levels.get('day_support'))} / 阻力 {_price(levels.get('day_resistance'))}；"
            f"4h 支撑 {_price(levels.get('h4_support'))} / 阻力 {_price(levels.get('h4_resistance'))}"
        )

    if item.flags:
        lines.append("")
        lines.append("风险与异动：")
        lines.extend(f"- {flag}" for flag in item.flags)
    else:
        lines.append("")
        lines.append("风险与异动：未触发阈值。")
    lines.append("")
    return lines


def facts_for_llm(snapshot: analysis.MarketSnapshot) -> str:
    """给 LLM 的精简事实包（不含表格，降低 token 消耗）。"""
    lines: list[str] = [f"生成时间：{snapshot.generated_at}"]
    for item in snapshot.symbols:
        day = item.timeframes.get("1d")
        h4 = item.timeframes.get("4h")
        trend_text = TREND_LABELS.get(day.trend, "—") if day else "—"
        rsi_text = f"{day.rsi:.0f}" if day and day.rsi is not None else "—"
        lines.append(
            f"{item.symbol}: 价格 {_price(item.price)}，24h {_pct(item.change_24h_pct)}，"
            f"综合 {item.total_score:.0f} 分（{item.regime}），日线趋势 {trend_text}，RSI {rsi_text}"
        )
        if h4:
            lines.append(
                f"  4h 支撑 {_price(h4.support)} / 阻力 {_price(h4.resistance)}；"
                f"日线支撑 {_price(day.support if day else None)} / 阻力 {_price(day.resistance if day else None)}"
            )
        if item.flags:
            lines.append("  异动：" + "；".join(item.flags))
    stable = snapshot.onchain
    if stable:
        lines.append(
            f"稳定币总供应 {_compact_usd(stable.get('total_usd'))}，7d {_pct(stable.get('change_7d_pct'))}，"
            f"30d {_pct(stable.get('change_30d_pct'))}"
        )
    if snapshot.sentiment:
        lines.append(
            f"恐慌贪婪指数 {snapshot.sentiment.get('value')}（{snapshot.sentiment.get('label')}），"
            f"1d 变化 {snapshot.sentiment.get('change_1d')}"
        )
    dxy = snapshot.macro.get("dxy")
    ndx = snapshot.macro.get("ndx")
    if dxy or ndx:
        parts = []
        if dxy:
            parts.append(f"美元指数 {dxy.get('last', 0):.2f}（5d {_pct(dxy.get('change_5d_pct'))}）")
        if ndx:
            parts.append(f"纳指100 {ndx.get('last', 0):.0f}（5d {_pct(ndx.get('change_5d_pct'))}）")
        lines.append("宏观：" + "，".join(parts))
    if snapshot.warnings:
        lines.append("数据缺口：" + "；".join(snapshot.warnings))
    return "\n".join(lines)


def generate(
    snapshot: analysis.MarketSnapshot,
    commentary: str | None = None,
    title: str = "加密市场行情简报",
) -> str:
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    note = market.source_note()
    source_text = f"{market.source_name()} 行情/永续"
    if note:
        source_text += f"（{note}）"
    lines.append(f"**生成时间**：{snapshot.generated_at}　｜　**数据源**：{source_text} · DefiLlama 稳定币 · blockchain.info & mempool.space · alternative.me · 东方财富")
    lines.append("")
    lines.append("## 一、结论速览")
    lines.append("")
    lines.append("| 标的 | 最新价 | 24h | 综合分 | 状态 | 关键信号 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in snapshot.symbols:
        lines.append(
            f"| {item.symbol} | {_price(item.price)} | {_pct(item.change_24h_pct)} | "
            f"{item.total_score:.1f} | {item.regime} | {_one_liner(item)} |"
        )
    lines.append("")
    if commentary:
        quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in commentary.strip().splitlines())
        lines.append("> **分析师点评**")
        lines.append(">")
        lines.append(quoted)
        lines.append("")

    lines.append("## 二、市场温度计")
    lines.append("")
    lines.append("| 指标 | 最新 | 变化 | 解读 |")
    lines.append("| --- | --- | --- | --- |")
    lines.extend(_thermometer_rows(snapshot))
    lines.append("")

    lines.append("## 三、币种明细")
    lines.append("")
    for item in snapshot.symbols:
        lines.extend(_symbol_section(item))

    lines.append("## 四、消息面")
    lines.append("")
    if snapshot.news:
        for item in snapshot.news:
            time_text = item.get("time") or ""
            source = item.get("source") or ""
            lines.append(f"- {time_text} [{source}] {item.get('title')}")
    else:
        lines.append("- 暂无可用快讯。")
    lines.append("")

    if snapshot.warnings:
        lines.append("## 五、数据缺口")
        lines.append("")
        lines.extend(f"- {warning}" for warning in snapshot.warnings)
    else:
        lines.append("## 五、数据缺口")
        lines.append("")
        lines.append("- 全部数据源正常。")
    lines.append("")

    lines.append("## 六、风险声明")
    lines.append("")
    lines.append("本简报由程序化指标与公开数据生成，仅用于研究与信息参考，不构成投资建议。加密市场波动剧烈，任何交易决策请自行评估风险并做好仓位管理。")
    lines.append("")
    return "\n".join(lines)


def save(content: str, prefix: str = "brief") -> str:
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = config.REPORT_DIR / f"{prefix}_{stamp}.md"
    path.write_text(content, "utf-8")
    return str(path)
