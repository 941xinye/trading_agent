"""链上数据源：稳定币供应（场内增量资金）+ BTC 网络活动。"""
from __future__ import annotations

from typing import Any

from .. import net


def stablecoins() -> dict[str, Any]:
    """统计 USD 锚定稳定币总供应量及 1d / 7d / 30d 变化。"""
    data = net.get_json(
        "https://stablecoins.llama.fi/stablecoins",
        {"includePrices": "false"},
        ttl=1800,
        retries=1,
    )
    total = prev_day = prev_week = prev_month = 0.0
    majors: dict[str, float] = {}
    for asset in data.get("peggedAssets", []):
        if asset.get("pegType") != "peggedUSD":
            continue
        circulating = (asset.get("circulating") or {}).get("peggedUSD") or 0.0
        if circulating <= 0:
            continue
        total += circulating
        prev_day += (asset.get("circulatingPrevDay") or {}).get("peggedUSD") or circulating
        prev_week += (asset.get("circulatingPrevWeek") or {}).get("peggedUSD") or circulating
        prev_month += (asset.get("circulatingPrevMonth") or {}).get("peggedUSD") or circulating
        symbol = asset.get("symbol")
        if symbol in {"USDT", "USDC", "DAI", "USDE", "FDUSD"}:
            majors[symbol] = circulating

    def _change(prev: float) -> float | None:
        return ((total / prev - 1) * 100.0) if prev else None

    return {
        "total_usd": total,
        "change_1d_pct": _change(prev_day),
        "change_7d_pct": _change(prev_week),
        "change_30d_pct": _change(prev_month),
        "majors_usd": {k: v for k, v in sorted(majors.items(), key=lambda x: -x[1])},
    }


def btc_network() -> dict[str, Any]:
    """BTC 网络基本面：算力、交易笔数、出块时间、链上转账额、内存池拥堵。"""
    stats = net.get_json("https://api.blockchain.info/stats", ttl=900, retries=1)
    network: dict[str, Any] = {
        "hash_rate_zh": (float(stats.get("hash_rate") or 0.0)) / 1e12,
        "tx_count_24h": stats.get("n_tx"),
        "blocks_mined_24h": stats.get("n_blocks_mined"),
        "minutes_between_blocks": stats.get("minutes_between_blocks"),
        "total_btc": (float(stats.get("totalbc") or 0.0)) / 1e8,
        "miners_revenue_usd": stats.get("miners_revenue_usd"),
        "est_tx_volume_usd": stats.get("estimated_transaction_volume_usd"),
        "difficulty": stats.get("difficulty"),
        "mempool_size_mb": None,
        "fee_fast_sat_vb": None,
    }
    try:
        recommended = net.get_json(
            "https://mempool.space/api/v1/fees/recommended", ttl=900, retries=1
        )
        network["fee_fast_sat_vb"] = recommended.get("fastestFee")
        mempool = net.get_json("https://mempool.space/api/mempool", ttl=900, retries=1)
        network["mempool_size_mb"] = round(float(mempool.get("vsize", 0)) / 1e6, 2)
    except RuntimeError:
        pass
    return network
