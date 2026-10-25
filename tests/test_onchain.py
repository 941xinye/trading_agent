"""链上数据源测试：BTC 难度调整、ETH 网络拥堵、稳定币供应。"""
from __future__ import annotations

from crypto_analyst.providers import onchain

_BTC_STATS = {
    "hash_rate": 8.5e20,
    "n_tx": 350000,
    "n_blocks_mined": 144,
    "minutes_between_blocks": 9.8,
    "totalbc": 1.97e15,
    "miners_revenue_usd": 4.5e7,
    "estimated_transaction_volume_usd": 2.0e10,
    "difficulty": 8.5e13,
}

_DIFFICULTY = {
    "progressPercent": 54.07,
    "difficultyChange": 5.49,
    "previousRetarget": 5.92,
    "remainingBlocks": 926,
}

_RECOMMENDED = {"fastestFee": 12}
_MEMPOOL = {"vsize": 1.5e7}


def _make_get_json_responder(mapping: dict):
    def fake_get_json(url, *a, **k):
        for key, val in mapping.items():
            if key in url:
                return val
        raise RuntimeError(f"unexpected url: {url}")
    return fake_get_json


def test_btc_network_includes_difficulty(monkeypatch):
    responder = _make_get_json_responder({
        "blockchain.info/stats": _BTC_STATS,
        "fees/recommended": _RECOMMENDED,
        "space/api/mempool": _MEMPOOL,
        "difficulty-adjustment": _DIFFICULTY,
    })
    monkeypatch.setattr(onchain.net, "get_json", responder)

    net = onchain.btc_network()
    assert net["diff_change_pct"] == 5.49
    assert net["diff_prev_change_pct"] == 5.92
    assert net["diff_progress_pct"] == 54.1
    assert net["diff_remaining_blocks"] == 926
    assert net["fee_fast_sat_vb"] == 12
    assert net["mempool_size_mb"] == 15.0


def test_btc_network_difficulty_none_on_failure(monkeypatch):
    def fake_get_json(url, *a, **k):
        if "blockchain.info/stats" in url:
            return _BTC_STATS
        raise RuntimeError("mempool down")
    monkeypatch.setattr(onchain.net, "get_json", fake_get_json)

    net = onchain.btc_network()
    assert net["diff_change_pct"] is None
    assert net["diff_progress_pct"] is None
    # 主数据源仍正常
    assert net["hash_rate_zh"] > 0


def test_eth_network_parses_gas_and_base_fee(monkeypatch):
    rpc_responses = {
        "eth_gasPrice": {"jsonrpc": "2.0", "id": 1, "result": "0xee6b2800"},  # 4000000000 wei = 4 Gwei
        "eth_blockNumber": {"jsonrpc": "2.0", "id": 1, "result": "0x190a51f"},
        "eth_feeHistory": {
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "baseFeePerGas": ["0x12a05f200", "0x11e1a3000", "0x10f73c800"],
                "gasUsedRatio": [0.5, 0.8, 0.3],
            },
        },
    }

    def fake_post_json(url, payload, *a, **k):
        return rpc_responses[payload["method"]]

    monkeypatch.setattr(onchain.net, "post_json", fake_post_json)

    net = onchain.eth_network()
    assert net["gas_price_gwei"] == 4.0
    assert net["latest_block"] == 0x190a51f
    assert net["base_fee_gwei"] == 4.55  # 0x10f73c800 = 4554213376 wei = 4.55 Gwei
    assert net["gas_used_ratio_avg"] == 0.533  # (0.5+0.8+0.3)/3 = 0.5333


def test_eth_network_handles_rpc_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rpc unreachable")
    monkeypatch.setattr(onchain.net, "post_json", boom)

    net = onchain.eth_network()
    assert net["gas_price_gwei"] is None
    assert net["base_fee_gwei"] is None
    assert net["latest_block"] is None


def test_stablecoins_aggregates_usd_assets(monkeypatch):
    data = {
        "peggedAssets": [
            {"pegType": "peggedUSD", "symbol": "USDT",
             "circulating": {"peggedUSD": 1.1e11},
             "circulatingPrevDay": {"peggedUSD": 1.0e11},
             "circulatingPrevWeek": {"peggedUSD": 9.5e10},
             "circulatingPrevMonth": {"peggedUSD": 9.0e10}},
            {"pegType": "peggedUSD", "symbol": "USDC",
             "circulating": {"peggedUSD": 3.5e10},
             "circulatingPrevDay": {"peggedUSD": 3.5e10},
             "circulatingPrevWeek": {"peggedUSD": 3.5e10},
             "circulatingPrevMonth": {"peggedUSD": 3.5e10}},
            {"pegType": "peggedEUR", "symbol": "EURT",
             "circulating": {"peggedUSD": 1e9}},  # 非美元，应跳过
        ]
    }
    monkeypatch.setattr(onchain.net, "get_json", lambda *a, **k: data)

    sc = onchain.stablecoins()
    assert sc["total_usd"] == 1.45e11  # USDT + USDC
    assert "USDT" in sc["majors_usd"]
    assert "USDC" in sc["majors_usd"]
    assert "EURT" not in sc["majors_usd"]
    # 7d 变化 = (1.45e11 / 1.3e11 - 1) * 100 ≈ 11.54
    assert sc["change_7d_pct"] is not None
    assert sc["change_7d_pct"] > 10


def test_stablecoins_safe_when_prev_missing(monkeypatch):
    data = {
        "peggedAssets": [
            {"pegType": "peggedUSD", "symbol": "USDT",
             "circulating": {"peggedUSD": 1e11},
             "circulatingPrevDay": {}, "circulatingPrevWeek": {}, "circulatingPrevMonth": {}},
        ]
    }
    monkeypatch.setattr(onchain.net, "get_json", lambda *a, **k: data)

    sc = onchain.stablecoins()
    # prev 缺失时用当前值兜底，变化为 0（但 prev=total 时 change=None? 实际是 (total/prev-1)*100 = 0）
    assert sc["total_usd"] == 1e11
