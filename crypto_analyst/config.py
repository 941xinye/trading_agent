"""全局配置：优先读环境变量 / .env，默认值按国内网络可达性选择数据源。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# 行情/永续数据源：gate 或 binance，可用命令行 --exchange 临时覆盖
EXCHANGE = os.getenv("EXCHANGE", "gate").strip().lower()
# Binance 公共接口：现货默认走国内可直连的 data-api 域名；永续域名不可达时自动降级
BINANCE_SPOT_BASE = os.getenv("BINANCE_SPOT_BASE", "https://data-api.binance.vision").rstrip("/")
BINANCE_FUTURES_BASE = os.getenv("BINANCE_FUTURES_BASE", "https://fapi.binance.com").rstrip("/")

# 标的与周期
SYMBOLS = [s.upper() for s in _env_list("SYMBOLS", "BTC,ETH,SOL")]
QUOTE = os.getenv("QUOTE", "USDT").upper()
TIMEFRAMES = _env_list("TIMEFRAMES", "1h,4h,1d")
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "300"))

# 周期权重：日线定方向，4h 定节奏，1h 找入场
TIMEFRAME_WEIGHTS = {"1h": 0.20, "4h": 0.35, "1d": 0.45}

# 五维评分权重（技术面 / 持仓面 / 流动性 / 情绪 / 宏观）
DIMENSION_WEIGHTS = {
    "technical": 0.40,
    "positioning": 0.20,
    "liquidity": 0.15,
    "sentiment": 0.15,
    "macro": 0.10,
}

# 信号 / 告警 / 评分共用阈值（flags、alerts、scoring 统一引用，调参只改这里）
THRESHOLDS = {
    # RSI
    "rsi_overbought": 78.0,       # 日线 RSI 超买（flags / 告警 / 技术面评分）
    "rsi_oversold": 25.0,         # 日线 RSI 超卖（flags / 告警）
    "rsi_score_weak": 30.0,       # 技术面评分：弱势区
    "rsi_score_strong": 55.0,     # 技术面评分：强势区
    # 资金费率（年化 %，flags / 告警）
    "funding_crowded_long": 50.0,
    "funding_crowded_short": -15.0,
    # 持仓量 1h 变化（%）
    "oi_flag_pct": 2.0,           # flags 触发
    "oi_alert_pct": 5.0,          # 告警触发
    "oi_score_pct": 1.5,          # 持仓面评分判定显著变化
    # 波动与量能
    "move_24h_pct": 5.0,          # 24h 涨跌幅异动（flags / 告警）
    "vol_ratio_flag": 2.2,        # flags 量比
    "vol_ratio_alert": 2.5,       # 告警量比
    "vol_ratio_score": 1.3,       # 技术面评分放量确认
    # 关键价位贴近距离（%）
    "level_flag_pct": 1.0,
    "level_alert_pct": 0.5,
    # 恐慌贪婪指数
    "fng_extreme_greed": 85,
    "fng_extreme_fear": 15,
    # 稳定币 7d 变化（%，流出告警）
    "stable_outflow_7d_pct": -1.0,
    # 资金费率历史分位（%，极端拥挤告警）
    "funding_pctl_hot": 90.0,
    "funding_pctl_cold": 10.0,
}

# 网络与缓存
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(BASE_DIR / ".cache")))
STATE_DIR = Path(os.getenv("STATE_DIR", str(BASE_DIR / ".state")))
REPORT_DIR = Path(os.getenv("REPORT_DIR", str(BASE_DIR / "reports")))

# LLM（任意 OpenAI 兼容接口：DeepSeek / Kimi / 通义 / 本地 vLLM 等）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 告警
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", "300"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "1800"))
