"""Agent 工具层：把内部数据能力暴露成 LLM 可调用函数（JSON Schema）。"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import analysis, config
from .providers import macro, market, onchain, sentiment

_SNAPSHOT_TTL = 60
_snapshot_cache: dict[str, Any] = {"ts": 0.0, "data": None, "scope": None}
# 当前问答会话的标的作用域（ask --symbols 覆盖），None 表示使用 config.SYMBOLS
_SCOPE: list[str] | None = None


def set_symbol_scope(symbols: list[str] | None) -> list[str] | None:
    """设置本次会话的标的作用域，返回旧值以便恢复。"""
    global _SCOPE
    previous = _SCOPE
    _SCOPE = [s.upper() for s in symbols] if symbols else None
    return previous


def _normalize_symbol(symbol: str) -> str:
    text = symbol.upper().replace("/", "").replace("-", "")
    # 兼容 BTC_USDT / BTCUSDT / BTC-USDT 三种写法，剥离计价货币后缀
    if text.endswith(f"_{config.QUOTE}"):
        text = text[: -(len(config.QUOTE) + 1)]
    elif text.endswith(config.QUOTE) and text != config.QUOTE:
        text = text[: -len(config.QUOTE)]
    return text


def _get_snapshot() -> analysis.MarketSnapshot:
    now = time.time()
    scope = tuple(_SCOPE) if _SCOPE else None
    cache_invalid = (
        _snapshot_cache["data"] is None
        or now - _snapshot_cache["ts"] > _SNAPSHOT_TTL
        or _snapshot_cache["scope"] != scope
    )
    if cache_invalid:
        _snapshot_cache["data"] = analysis.build_snapshot(
            symbols=list(scope) if scope else None, source="ask"
        )
        _snapshot_cache["ts"] = now
        _snapshot_cache["scope"] = scope
    return _snapshot_cache["data"]


def _market_overview(symbols: list[str] | None = None) -> dict[str, Any]:
    snapshot = _get_snapshot()
    wanted = {s.upper() for s in symbols} if symbols else None
    rows = []
    for item in snapshot.symbols:
        if wanted and item.symbol not in wanted:
            continue
        rows.append(
            {
                "symbol": item.symbol,
                "price": round(item.price, 4),
                "change_24h_pct": round(item.change_24h_pct, 2),
                "total_score": round(item.total_score, 1),
                "regime": item.regime,
                "scores": {k: round(v, 1) for k, v in item.scores.items()},
                "flags": item.flags,
            }
        )
    return {
        "generated_at": snapshot.generated_at,
        "symbols": rows,
        "sentiment": {k: v for k, v in snapshot.sentiment.items() if k != "history_30d"},
        "macro": snapshot.macro,
        "onchain": snapshot.onchain,
        "btc_network": snapshot.btc_network,
        "eth_network": snapshot.eth_network,
        "warnings": snapshot.warnings,
    }


def _symbol_analysis(symbol: str) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    snapshot = _get_snapshot()
    for item in snapshot.symbols:
        if item.symbol == symbol:
            return item.to_dict()
    # 不在默认观察列表里，单独实时分析
    return analysis.build_symbol_snapshot(
        symbol, snapshot.onchain, snapshot.sentiment, snapshot.macro
    ).to_dict()


def _klines(symbol: str, timeframe: str = "4h", limit: int = 120) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    if timeframe not in {"1h", "4h", "1d"}:
        raise ValueError("timeframe 仅支持 1h / 4h / 1d")
    candles = market.candles(symbol, timeframe, limit)
    view = analysis.build_timeframe_view(timeframe, candles)
    closes = [c.close for c in candles]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": len(candles),
        "view": view.to_dict(),
        "recent_closes": [round(c, 4) for c in closes[-20:]],
    }


def _derivatives(symbol: str) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    data = market.derivatives(symbol)
    return {"symbol": symbol, "derivatives": data} if data else {"symbol": symbol, "derivatives": None, "note": "该标的无永续合约数据"}


def _liquidity() -> dict[str, Any]:
    return {
        "stablecoins": onchain.stablecoins(),
        "btc_network": onchain.btc_network(),
        "eth_network": onchain.eth_network(),
    }


def _sentiment_news(limit: int = 8) -> dict[str, Any]:
    return {"fear_greed": {k: v for k, v in sentiment.fear_greed().items() if k != "history_30d"}, "news": sentiment.news(limit=limit)}


def _macro() -> dict[str, Any]:
    return macro.macro_overview()


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_market_overview",
            "description": "获取观察列表内所有币种的价格、综合评分、状态、风险标记，以及市场情绪/宏观/链上/链上网络概览。适合回答大盘整体问题。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_symbol_analysis",
            "description": "获取单个币种的完整分析：1h/4h/1d 三个周期的趋势、RSI、MACD、量比、支撑阻力、结构，以及合约持仓结构和五维评分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "币种，如 BTC、ETH、SOL"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_klines",
            "description": "获取指定币种、指定周期的K线形态摘要（趋势、均线、RSI、MACD、量比、支撑阻力）与最近20根收盘价。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "币种，如 BTC"},
                    "timeframe": {"type": "string", "enum": ["1h", "4h", "1d"], "description": "周期，默认 4h"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_derivatives",
            "description": "获取永续合约持仓结构：资金费率及年化、90天分位、持仓量及1h/24h变化、多空账户比、大户多空比、最近爆仓量。",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "币种，如 BTC"}},
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_liquidity",
            "description": "获取稳定币总供应量及1d/7d/30d变化（场内增量资金），以及 BTC 网络算力、交易数、内存池拥堵程度。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sentiment_news",
            "description": "获取恐慌贪婪指数（当前值、1d/7d变化）与最近加密新闻标题。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "新闻条数，默认8"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro",
            "description": "获取美元指数、纳斯达克100、标普500的最新点位与1d/5d涨跌幅，用于判断宏观风险偏好。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_market_overview": _market_overview,
    "get_symbol_analysis": _symbol_analysis,
    "get_klines": _klines,
    "get_derivatives": _derivatives,
    "get_liquidity": _liquidity,
    "get_sentiment_news": _sentiment_news,
    "get_macro": _macro,
}


def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"未知工具: {name}"}
    try:
        return handler(**arguments)
    except TypeError as exc:
        return {"error": f"参数错误: {exc}"}
    except Exception as exc:  # 工具异常回传给模型，避免整个对话中断
        return {"error": f"{type(exc).__name__}: {exc}"}
