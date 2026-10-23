"""交易所注册表：按 EXCHANGE 环境变量或 --exchange 参数选择实现。"""
from __future__ import annotations

from ... import config
from . import binance, gate

EXCHANGE_NAMES = ("gate", "binance")
_IMPLS = {"gate": gate, "binance": binance}


def get_exchange(name: str | None = None):
    key = (name or config.EXCHANGE or "gate").strip().lower()
    module = _IMPLS.get(key)
    if module is None:
        raise ValueError(
            f"不支持的交易所 {key!r}，可选：{', '.join(EXCHANGE_NAMES)}"
        )
    return module
