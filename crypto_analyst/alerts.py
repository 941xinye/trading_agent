"""异动告警引擎：阈值规则 + 冷却去重 + Webhook 推送。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Any

import requests

from . import analysis, config, history

logger = logging.getLogger(__name__)

# 告警聚合：非关键告警在缓存窗口内合并推送，避免刷屏
AGGREGATION_WINDOW = 5 * 60
# 分级升级：同一标的在窗口内累计 N 条 warning 后，本轮告警自动升级为 critical
ESCALATION_THRESHOLD = 3
ESCALATION_WINDOW = 30 * 60

_pending_alerts: list[dict[str, Any]] = []
_last_batch_push = 0.0
_warning_counts: dict[str, list[float]] = {}


def _state_path():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return config.STATE_DIR / "alerts.json"


def _load_state() -> dict[str, float]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("状态文件损坏，已重置冷却记录：%s（%s）", path, exc)
        return {}


def _save_state(state: dict[str, float]) -> None:
    try:
        _state_path().write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    except OSError:
        pass


def _prune_state(state: dict[str, float], now: float, ttl_hours: float | None = None) -> dict[str, float]:
    """丢弃超过保留时长的冷却记录，防止 alerts.json 无限增长。"""
    ttl = config.ALERT_STATE_TTL_HOURS if ttl_hours is None else ttl_hours
    if ttl <= 0:
        return dict(state)
    cutoff = now - ttl * 3600
    return {key: ts for key, ts in state.items() if ts >= cutoff}


def _previous_regimes(source: str = "watch") -> dict[str, str]:
    """从历史快照中读取每个标的"上一次"的 regime（即倒数第二条记录）。

    build_snapshot 在 evaluate 之前已把本轮写入历史，因此需要跳过最新一条。
    只比较同一来源的记录，避免手动 brief / snapshot 改动影响告警判定。
    """
    latest: dict[str, str] = {}
    previous: dict[str, str] = {}
    for rec in history.iter_records():
        if rec.get("source") != source:
            continue
        symbol = rec.get("symbol")
        regime = rec.get("regime")
        if not symbol or not regime:
            continue
        if symbol in latest:
            previous[symbol] = latest[symbol]
        latest[symbol] = regime
    return previous


def evaluate(snapshot: analysis.MarketSnapshot) -> list[dict[str, Any]]:
    """生成告警候选，并按冷却时间过滤。"""
    th = config.THRESHOLDS
    candidates: list[dict[str, Any]] = []
    prev_regimes = _previous_regimes("watch")

    def add(key: str, level: str, message: str) -> None:
        candidates.append({"key": key, "level": level, "message": message})

    for item in snapshot.symbols:
        symbol = item.symbol

        # 评分状态切换：与上一次落盘的 regime 不同时触发（结构性信号）
        prev_regime = prev_regimes.get(symbol)
        if prev_regime and prev_regime != item.regime:
            add(
                f"{symbol}:regime_shift",
                "warning",
                f"{symbol} 评分状态切换：{prev_regime} → {item.regime}（{item.total_score:.0f}分）",
            )

        if abs(item.change_24h_pct) >= th["move_24h_pct"]:
            add(
                f"{symbol}:move24h",
                "warning",
                f"{symbol} 24h {item.change_24h_pct:+.1f}%，当前 {item.regime}（{item.total_score:.0f}分）",
            )

        day = item.timeframes.get("1d")
        if day and day.rsi is not None:
            if day.rsi >= th["rsi_overbought"]:
                add(f"{symbol}:rsi_overbought", "warning", f"{symbol} 日线 RSI {day.rsi:.1f}，超买")
            elif day.rsi <= th["rsi_oversold"]:
                add(f"{symbol}:rsi_oversold", "warning", f"{symbol} 日线 RSI {day.rsi:.1f}，超卖")

        if day and day.resistance and day.support:
            to_resistance = (day.resistance / item.price - 1) * 100
            to_support = (item.price / day.support - 1) * 100
            if item.price > day.resistance:
                add(
                    f"{symbol}:break_resistance",
                    "warning",
                    f"{symbol} 已突破日线阻力 {day.resistance:,.2f}，当前 {item.price:,.2f}（+{(item.price/day.resistance-1)*100:.2f}%）",
                )
            elif item.price < day.support:
                add(
                    f"{symbol}:break_support",
                    "warning",
                    f"{symbol} 已跌破日线支撑 {day.support:,.2f}，当前 {item.price:,.2f}（{(item.price/day.support-1)*100:.2f}%）",
                )
            elif 0 <= to_resistance <= th["level_alert_pct"]:
                add(
                    f"{symbol}:test_resistance",
                    "info",
                    f"{symbol} 正在测试日线阻力 {day.resistance:,.2f}，突破需放量确认",
                )
            elif 0 <= to_support <= th["level_alert_pct"]:
                add(
                    f"{symbol}:test_support",
                    "info",
                    f"{symbol} 正在测试日线支撑 {day.support:,.2f}，跌破则结构转弱",
                )

        deriv = item.derivatives
        if deriv:
            funding = deriv.get("funding_annualized_pct")
            if funding is not None and funding > th["funding_crowded_long"]:
                add(f"{symbol}:funding_hot", "warning", f"{symbol} 资金费率年化 {funding:.0f}%，多头拥挤")
            elif funding is not None and funding < th["funding_crowded_short"]:
                add(f"{symbol}:funding_short", "info", f"{symbol} 资金费率年化 {funding:.0f}%，空头拥挤")
            oi_change = deriv.get("oi_change_1h_pct")
            if oi_change is not None and abs(oi_change) >= th["oi_alert_pct"]:
                direction = "增仓" if oi_change > 0 else "减仓"
                add(f"{symbol}:oi_change", "warning", f"{symbol} 1h 持仓量{direction} {oi_change:+.1f}%")

        hour = item.timeframes.get("1h")
        if hour and hour.vol_ratio is not None and hour.vol_ratio >= th["vol_ratio_alert"]:
            add(
                f"{symbol}:volume_spike",
                "info",
                f"{symbol} 1h 成交量为 20 周期均量的 {hour.vol_ratio:.1f} 倍",
            )

    fng = snapshot.sentiment.get("value")
    if fng is not None:
        if fng >= th["fng_extreme_greed"]:
            add("market:fng_greed", "warning", f"恐慌贪婪指数 {fng}，处于极度贪婪区间")
        elif fng <= th["fng_extreme_fear"]:
            add("market:fng_fear", "info", f"恐慌贪婪指数 {fng}，处于极度恐慌区间")

    stable_change = snapshot.onchain.get("change_7d_pct")
    if stable_change is not None and stable_change <= th["stable_outflow_7d_pct"]:
        add(
            "market:stablecoin_outflow",
            "warning",
            f"稳定币 7d 减少 {abs(stable_change):.2f}%，场内资金流出",
        )

    # 冷却 key 带交易所前缀：切换 EXCHANGE 后不会误继承另一家交易所的冷却时间
    state = _load_state()
    now = time.time()
    state = _prune_state(state, now)

    def state_key(key: str) -> str:
        return f"{config.EXCHANGE}:{key}"

    fired = [
        c for c in candidates if now - state.get(state_key(c["key"]), 0.0) >= config.ALERT_COOLDOWN
    ]
    for item in fired:
        state[state_key(item["key"])] = now
    _save_state(state)
    return fired


def _sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人加签：HmacSHA256(timestamp + "\\n" + secret, 空内容) 后 Base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _is_feishu(url: str) -> bool:
    return "feishu" in url or "larksuite" in url


def _send(payload: dict[str, Any]) -> bool:
    """发送 Webhook 请求，自动处理加签与错误码。"""
    if not config.WEBHOOK_URL:
        logger.info("推送跳过：未配置 WEBHOOK_URL")
        return False
    if config.WEBHOOK_SECRET:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _sign(timestamp, config.WEBHOOK_SECRET)

    try:
        resp = requests.post(config.WEBHOOK_URL, json=payload, timeout=10)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        code = data.get("code", data.get("errcode", data.get("StatusCode")))
        if resp.status_code >= 400 or (code is not None and code != 0):
            reason = data.get("msg") or data.get("StatusMessage") or resp.text[:200]
            logger.warning("推送失败 HTTP %s code=%s %s", resp.status_code, code, reason)
            return False
        return True
    except Exception as exc:
        logger.warning("推送失败：%s", exc)
        return False


def push(message: str) -> bool:
    """推送文本消息到 Webhook（飞书 content.text / 钉钉 text.content 自适应）。"""
    if not config.WEBHOOK_URL:
        return False
    if "dingtalk" in config.WEBHOOK_URL:
        payload: dict[str, Any] = {"msgtype": "text", "text": {"content": message}}
    else:
        payload = {"msg_type": "text", "content": {"text": message}}
    return _send(payload)


def push_card(card: dict[str, Any]) -> bool:
    """推送飞书 interactive 卡片消息。"""
    if not config.WEBHOOK_URL:
        return False
    if not _is_feishu(config.WEBHOOK_URL):
        # 钉钉等非飞书机器人不支持 interactive 卡片，降级为文本
        fallback = _card_to_text(card)
        return push(fallback)
    payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
    return _send(payload)


def _card_to_text(card: dict[str, Any]) -> str:
    """卡片降级为纯文本（用于非飞书机器人或调试）。"""
    lines: list[str] = []
    header = card.get("header") or {}
    title = (header.get("title") or {}).get("content")
    if title:
        lines.append(f"== {title} ==")
    for element in card.get("elements") or []:
        text = (element.get("text") or {}).get("content")
        if text:
            lines.append(text)
    return "\n\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    """按句末标点截断，避免把点评拦腰切断。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "！", "？", "；", "\n"):
        index = cut.rfind(sep)
        if index >= int(limit * 0.6):
            if sep == "\n":
                return cut[:index].rstrip()
            return cut[: index + 1].rstrip() + "…"
    return cut.rstrip() + "…"


def _md_div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _hr() -> dict[str, Any]:
    return {"tag": "hr"}


def _note(content: str) -> dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "lark_md", "content": content}]}


def build_brief_card(
    snapshot: analysis.MarketSnapshot,
    commentary: str | None = None,
    report_path: str | None = None,
) -> dict[str, Any]:
    """把行情简报压缩成飞书卡片（lark_md 不支持表格，改为逐行列举）。"""
    from . import report as _report

    elements: list[dict[str, Any]] = []

    # 结论速览：每币种一行
    overview_lines: list[str] = []
    for item in snapshot.symbols:
        line = (
            f"**{item.symbol}** {_report._price(item.price)}"
            f" ｜ 24h **{_report._pct(item.change_24h_pct)}**"
            f" ｜ {item.total_score:.0f}分 {item.regime}"
        )
        one_liner = _report._one_liner(item)
        if one_liner:
            line += f"\n　{one_liner}"
        overview_lines.append(line)
    elements.append(_md_div("**一、结论速览**\n" + "\n".join(overview_lines)))

    if commentary:
        elements.append(_md_div(f"**分析师点评**\n{_truncate(commentary.strip(), 600)}"))

    # 市场温度计
    thermo: list[str] = []
    stable = snapshot.onchain
    if stable:
        thermo.append(
            f"稳定币总供应 {_report._compact_usd(stable.get('total_usd'))}，7d {_report._pct(stable.get('change_7d_pct'))}"
        )
    fng = snapshot.sentiment
    if fng:
        thermo.append(
            f"恐慌贪婪 {fng.get('value')}（{fng.get('label')}），1d {fng.get('change_1d')}"
        )
    dxy = snapshot.macro.get("dxy")
    ndx = snapshot.macro.get("ndx")
    if dxy:
        thermo.append(f"美元指数 {dxy.get('last'):.2f}，5d {_report._pct(dxy.get('change_5d_pct'))}")
    if ndx:
        thermo.append(f"纳指100 {ndx.get('last'):,.0f}，5d {_report._pct(ndx.get('change_5d_pct'))}")
    if thermo:
        elements.append(_hr())
        elements.append(_md_div("**二、市场温度计**\n" + "\n".join(thermo)))

    # 币种关键价位与异动（只列有信号的）
    signal_lines: list[str] = []
    for item in snapshot.symbols:
        levels = item.key_levels or {}
        parts: list[str] = []
        if levels.get("day_support") or levels.get("day_resistance"):
            parts.append(
                f"日线支撑 {_report._price(levels.get('day_support'))} / 阻力 {_report._price(levels.get('day_resistance'))}"
            )
        if item.flags:
            parts.append("异动：" + "；".join(item.flags))
        if parts:
            signal_lines.append(f"**{item.symbol}**：" + "；".join(parts))
    if signal_lines:
        elements.append(_hr())
        elements.append(_md_div("**三、关键价位与异动**\n" + "\n".join(signal_lines)))

    # 数据缺口
    if snapshot.warnings:
        elements.append(_hr())
        elements.append(_md_div("**数据缺口**\n" + "；".join(snapshot.warnings)))

    # 底部附注
    note_parts = ["本简报由程序化指标生成，不构成投资建议"]
    if report_path:
        note_parts.append(f"完整报告：{report_path}")
    elements.append(_note(" ｜ ".join(note_parts)))

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"加密市场行情简报 · {snapshot.generated_at}"},
        },
        "elements": elements,
    }


def append_alert_log(text: str) -> None:
    """把本轮告警追加到 reports/alerts.log，推送失败时仍可按时间回溯。"""
    try:
        path = config.REPORT_DIR / "alerts.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def format_alerts(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return "本轮无触发告警。"
    stamp = datetime.now().strftime("%H:%M:%S")
    lines = [f"[{stamp}] 触发 {len(alerts)} 条异动："]
    for item in alerts:
        mark = "!" if item["level"] == "warning" else "-"
        lines.append(f"  {mark} {item['message']}")
    return "\n".join(lines)


LEVEL_ORDER = {"info": 1, "warning": 2}


def _quiet_window() -> tuple[int, int] | None:
    raw = config.ALERT_QUIET_HOURS
    if not raw or "-" not in raw:
        return None
    try:
        start_text, end_text = [part.strip() for part in raw.split("-", 1)]
        start_h, start_m = (int(part) for part in start_text.split(":"))
        end_h, end_m = (int(part) for part in end_text.split(":"))
    except ValueError:
        return None
    return start_h * 60 + start_m, end_h * 60 + end_m


def in_quiet_hours(now: datetime | None = None) -> bool:
    """判断当前是否处于夜间静默时段（支持 23:00-07:00 这类跨零点区间）。"""
    window = _quiet_window()
    if not window:
        return False
    current = now or datetime.now()
    minutes = current.hour * 60 + current.minute
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def pushable(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 ALERT_MIN_LEVEL 过滤：被过滤的告警仍会写入 alerts.log。"""
    threshold = LEVEL_ORDER.get(config.ALERT_MIN_LEVEL, 1)
    return [item for item in alerts if LEVEL_ORDER.get(item.get("level", "info"), 1) >= threshold]


def _escalate(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一标的在窗口期内累计达到阈值的 warning 后，升级为 critical。"""
    now = time.time()
    # 清理过期计数
    for symbol in list(_warning_counts):
        _warning_counts[symbol] = [t for t in _warning_counts[symbol] if now - t <= ESCALATION_WINDOW]
        if not _warning_counts[symbol]:
            del _warning_counts[symbol]

    # 记录本轮 warning
    for item in alerts:
        if item.get("level") == "warning":
            symbol = item.get("symbol", "")
            _warning_counts.setdefault(symbol, []).append(now)

    # 达到阈值的标的升级
    escalated: list[dict[str, Any]] = []
    for item in alerts:
        symbol = item.get("symbol", "")
        if len(_warning_counts.get(symbol, [])) >= ESCALATION_THRESHOLD:
            item = dict(item)
            item["level"] = "critical"
            item["message"] = f"[升级] {item['message']}"
        escalated.append(item)
    return escalated


def _flush_pending(force: bool = False) -> None:
    """把缓存的告警批量推送；非强制时需等到聚合窗口结束。"""
    global _last_batch_push
    now = time.time()
    if not _pending_alerts:
        return
    has_critical = any(a.get("level") == "critical" for a in _pending_alerts)
    if not force and not has_critical and (now - _last_batch_push) < AGGREGATION_WINDOW:
        return
    due = pushable(_pending_alerts)
    if due:
        push(format_alerts(due))
    _pending_alerts.clear()
    _last_batch_push = now


def run_watch_once() -> list[dict[str, Any]]:
    """跑一轮采集 + 告警评估 + 落盘/推送，供 watch 与内置调度共用。"""
    snapshot = analysis.build_snapshot(include_news=False, source="watch")
    fired = evaluate(snapshot)
    fired = _escalate(fired)
    text = format_alerts(fired)
    logger.info("%s", text)
    if fired:
        append_alert_log(text)
        if in_quiet_hours():
            logger.info("静默时段：本轮告警已落盘，未推送。")
        else:
            _pending_alerts.extend(fired)
            _flush_pending()
    else:
        # 无新告警时也尝试刷新缓存（把窗口内残留的告警推出去）
        _flush_pending()
    return fired


def run_watch(interval: int | None = None, once: bool = False) -> None:
    interval = interval or config.ALERT_INTERVAL
    logger.info(
        "异动监控启动，轮询间隔 %ss，冷却 %ss，静默时段 %s，最低推送级别 %s",
        interval, config.ALERT_COOLDOWN, config.ALERT_QUIET_HOURS or "未启用", config.ALERT_MIN_LEVEL,
    )
    while True:
        try:
            run_watch_once()
        except KeyboardInterrupt:
            logger.info("已停止监控。")
            return
        except Exception as exc:  # 单轮失败不影响后续轮询
            logger.warning("本轮采集失败: %s", exc)
        if once:
            _flush_pending(force=True)  # 单次模式强制推送缓冲告警
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("已停止监控。")
            return
