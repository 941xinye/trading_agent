"""维护任务测试：缓存淘汰与告警冷却记录过期清理。"""
from __future__ import annotations

import os
import time

from crypto_analyst import alerts, net


def _write(path, content="{}"):
    path.write_text(content, "utf-8")


def test_prune_cache_removes_expired_files(tmp_path, monkeypatch):
    monkeypatch.setattr(net.config, "CACHE_DIR", tmp_path)
    now = time.time()
    old = tmp_path / "old.json"
    edge = tmp_path / "edge.json"
    fresh = tmp_path / "fresh.json"
    for path in (old, edge, fresh):
        _write(path)
    os.utime(old, (now - 10 * 86400, now - 10 * 86400))
    os.utime(edge, (now - 8 * 86400, now - 8 * 86400))
    os.utime(fresh, (now - 3600, now - 3600))

    removed = net.prune_cache(max_age_days=7, max_files=0)

    assert removed == 2
    assert fresh.exists()
    assert not old.exists()
    assert not edge.exists()


def test_prune_cache_caps_file_count(tmp_path, monkeypatch):
    monkeypatch.setattr(net.config, "CACHE_DIR", tmp_path)
    now = time.time()
    paths = []
    for index in range(5):
        path = tmp_path / f"c{index}.json"
        _write(path)
        os.utime(path, (now - index * 60, now - index * 60))
        paths.append(path)

    removed = net.prune_cache(max_age_days=0, max_files=2)

    assert removed == 3
    remaining = {path.name for path in tmp_path.glob("*.json")}
    assert remaining == {"c0.json", "c1.json"}


def test_prune_state_drops_expired_keys():
    now = time.time()
    state = {
        "gate:BTC:move24h": now - 10 * 86400,
        "gate:ETH:move24h": now - 2 * 86400,
        "gate:SOL:funding_hot": now - 60,
    }

    pruned = alerts._prune_state(state, now, ttl_hours=168.0)

    assert set(pruned) == {"gate:ETH:move24h", "gate:SOL:funding_hot"}


def test_prune_state_keeps_all_when_ttl_disabled():
    now = time.time()
    state = {"old": 1.0}
    assert alerts._prune_state(state, now, ttl_hours=0) == state
