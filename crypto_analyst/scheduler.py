"""内置调度：单个常驻进程同时做异动监控与每日定时简报推送，替代 schtasks 双任务。

- 每 `interval` 秒跑一轮 watch（告警选择 push 前先落盘，静默/级别过滤在 alerts 内处理）；
- 每天在 `times`（如 09:00,21:00）各生成并推送一次简报，错过 30 分钟内仍会补发；
- 已触发的时间点写入 `.state/serve_slots.json`，进程重启不会重复推送；
- 单轮失败只记录日志，不退出主循环。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import alerts, briefing, config

CATCHUP_MINUTES = 30
MAX_SLEEP_SECONDS = 20


def _state_path() -> Path:
    return config.STATE_DIR / "serve_slots.json"


def _load_fired() -> dict[str, str]:
    try:
        data = json.loads(_state_path().read_text("utf-8"))
        return {str(key): str(value) for key, value in data.items()}
    except (OSError, ValueError):
        return {}


def _save_fired(fired: dict[str, str]) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fired, ensure_ascii=False, indent=2), "utf-8")
    except OSError:
        pass


def parse_times(raw: str | None) -> list[str]:
    """把 "9:00, 21:00" 归一化为 ["09:00", "21:00"]，非法项静默丢弃。"""
    slots: list[str] = []
    for part in (raw or "").split(","):
        text = part.strip()
        if not text or ":" not in text:
            continue
        try:
            hour, minute = (int(piece) for piece in text.split(":", 1))
        except ValueError:
            continue
        if 0 <= hour < 24 and 0 <= minute < 60:
            slots.append(f"{hour:02d}:{minute:02d}")
    return sorted(set(slots))


def due_slots(
    now: datetime,
    times: list[str],
    fired: dict[str, str],
    catchup_minutes: int = CATCHUP_MINUTES,
) -> list[str]:
    """返回当前应当触发的时间点（当天已触发或已超过补发窗口的不返回）。"""
    today = now.date().isoformat()
    due: list[str] = []
    for slot in times:
        if fired.get(slot) == today:
            continue
        hour, minute = (int(piece) for piece in slot.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now <= target + timedelta(minutes=catchup_minutes):
            due.append(slot)
    return due


def _run_brief(slot: str) -> bool:
    """生成简报并推送卡片；返回是否推送成功。"""
    try:
        snapshot, _text, commentary, path = briefing.build()
        if not snapshot.symbols:
            print(f"[{slot}] 简报未生成：未获取到任何标的。", flush=True)
            return False
        card = alerts.build_brief_card(snapshot, commentary=commentary, report_path=path)
        ok = alerts.push_card(card)
        print(f"[{slot}] 简报 {'已推送' if ok else '推送失败'}：{path}", flush=True)
        return ok
    except Exception as exc:  # 单次失败不影响后续调度
        print(f"[{slot}] 简报生成失败：{exc}", flush=True)
        return False


def serve(
    interval: int | None = None,
    times: str | None = None,
    once: bool = False,
) -> None:
    interval = interval or config.ALERT_INTERVAL
    slots = parse_times(times if times is not None else config.BRIEF_PUSH_TIMES)
    fired = _load_fired()
    print(
        f"内置调度启动：异动监控 {interval}s/轮，简报时间 {'/'.join(slots) or '未配置'}，"
        f"静默时段 {config.ALERT_QUIET_HOURS or '未启用'}，最低推送级别 {config.ALERT_MIN_LEVEL}（Ctrl+C 退出）",
        flush=True,
    )

    next_watch = 0.0
    while True:
        now = time.time()
        if now >= next_watch:
            try:
                alerts.run_watch_once()
            except Exception as exc:  # 单轮采集失败不影响后续轮询
                print(f"[{datetime.now():%H:%M:%S}] 本轮采集失败: {exc}", flush=True)
            next_watch = time.time() + interval

        today = datetime.now().date().isoformat()
        for slot in due_slots(datetime.now(), slots, fired):
            _run_brief(slot)
            fired[slot] = today
            _save_fired(fired)

        if once:
            return
        try:
            time.sleep(min(MAX_SLEEP_SECONDS, max(1.0, next_watch - time.time())))
        except KeyboardInterrupt:
            print("\n已停止内置调度。", flush=True)
            return
