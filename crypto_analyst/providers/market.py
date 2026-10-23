"""行情与衍生品数据入口：按 config.EXCHANGE 在 Gate.io / Binance 之间切换。"""
from __future__ import annotations

from typing import Any

from .exchanges import get_exchange
from .exchanges.base import Candle

__all__ = [
    "Candle",
    "candles",
    "derivatives",
    "funding_history",
    "pair",
    "source_name",
    "source_note",
    "ticker",
]


def source_name() -> str:
    return get_exchange().NAME


def source_note() -> str | None:
    return get_exchange().source_note()


def pair(symbol: str) -> str:
    return get_exchange().pair(symbol)


def candles(symbol: str, interval: str = "4h", limit: int = 300) -> list[Candle]:
    return get_exchange().candles(symbol, interval, limit)


def ticker(symbol: str) -> dict[str, Any]:
    return get_exchange().ticker(symbol)


def funding_history(symbol: str, limit: int = 270) -> list[float]:
    return get_exchange().funding_history(symbol, limit)


def derivatives(symbol: str) -> dict[str, Any] | None:
    return get_exchange().derivatives(symbol)
