"""内置调度测试：时间解析、补发窗口、每日去重与单轮执行。"""
from __future__ import annotations

from datetime import datetime

from crypto_analyst import scheduler


def test_parse_times_normalizes_and_drops_invalid():
    assert scheduler.parse_times("9:05, 21:00, 21:00") == ["09:05", "21:00"]
    assert scheduler.parse_times("25:00,abc,09:70") == []
    assert scheduler.parse_times("") == []


def test_due_slots_exact_and_within_catchup():
    now = datetime(2026, 10, 24, 9, 0, 5)
    assert scheduler.due_slots(now, ["09:00"], {}) == ["09:00"]

    late = datetime(2026, 10, 24, 9, 25)
    assert scheduler.due_slots(late, ["09:00"], {}) == ["09:00"]


def test_due_slots_skips_fired_and_expired():
    now = datetime(2026, 10, 24, 9, 40)
    assert scheduler.due_slots(now, ["09:00"], {}) == []
    assert scheduler.due_slots(now, ["09:00"], {"09:00": "2026-10-24"}) == []


def test_due_slots_next_day_resets():
    now = datetime(2026, 10, 25, 9, 1)
    assert scheduler.due_slots(now, ["09:00"], {"09:00": "2026-10-24"}) == ["09:00"]


def test_due_slots_ignores_future_slot():
    now = datetime(2026, 10, 24, 20, 0)
    assert scheduler.due_slots(now, ["21:00"], {}) == []


def test_run_brief_pushes_card(monkeypatch, sample_snapshot):
    monkeypatch.setattr(
        scheduler.briefing,
        "build",
        lambda symbols=None, use_llm=True: (sample_snapshot, "# 简报", "点评", "reports/brief.md"),
    )
    pushed = {}
    monkeypatch.setattr(scheduler.alerts, "push_card", lambda card: pushed.update(card) or True)

    assert scheduler._run_brief("09:00") is True
    assert pushed["header"]["title"]["content"].startswith("加密市场行情简报")


def test_run_brief_without_symbols_returns_false(monkeypatch, sample_snapshot):
    empty = sample_snapshot
    empty.symbols = []
    monkeypatch.setattr(
        scheduler.briefing,
        "build",
        lambda symbols=None, use_llm=True: (empty, "", None, ""),
    )
    monkeypatch.setattr(scheduler.alerts, "push_card", lambda card: True)
    assert scheduler._run_brief("09:00") is False


def test_serve_once_runs_watch_and_due_brief(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(scheduler.alerts, "run_watch_once", lambda: calls.append("watch") or [])
    monkeypatch.setattr(scheduler, "due_slots", lambda *args, **kwargs: ["09:00"])
    monkeypatch.setattr(scheduler, "_run_brief", lambda slot: calls.append(f"brief:{slot}") or True)
    monkeypatch.setattr(scheduler, "_load_fired", lambda: {})
    monkeypatch.setattr(scheduler, "_save_fired", lambda data: calls.append("save"))

    scheduler.serve(interval=300, times="09:00", once=True)
    assert calls[0] == "watch"
    assert "brief:09:00" in calls
    assert "save" in calls
