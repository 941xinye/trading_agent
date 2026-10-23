"""宏观联动：美元指数、纳指、标普（东方财富公开行情，国内可直连）。"""
from __future__ import annotations

from typing import Any

from .. import net

INDICES = {
    "dxy": ("100.UDI", "美元指数"),
    "ndx": ("100.NDX", "纳斯达克100"),
    "spx": ("100.SPX", "标普500"),
}


def index_daily(secid: str, limit: int = 30) -> dict[str, Any] | None:
    data = net.get_json(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": secid,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f53",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": str(limit),
        },
        ttl=1800,
        retries=1,
    )
    payload = (data or {}).get("data") or {}
    klines = payload.get("klines") or []
    if not klines:
        return None
    dates: list[str] = []
    closes: list[float] = []
    for row in klines:
        parts = row.split(",")
        if len(parts) < 2:
            continue
        dates.append(parts[0])
        closes.append(float(parts[1]))

    def change(periods: int) -> float | None:
        if len(closes) <= periods or closes[-periods - 1] == 0:
            return None
        return (closes[-1] / closes[-periods - 1] - 1) * 100

    return {
        "name": payload.get("name") or secid,
        "last": closes[-1],
        "date": dates[-1],
        "change_1d_pct": change(1),
        "change_5d_pct": change(5),
        "series_20d": closes[-20:],
    }


def macro_overview() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, (secid, _name) in INDICES.items():
        try:
            item = index_daily(secid)
        except RuntimeError:
            item = None
        result[key] = item
    return result
