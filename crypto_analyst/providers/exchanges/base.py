"""交易所适配层公共类型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
