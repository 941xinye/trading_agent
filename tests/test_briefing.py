"""简报链路测试：snapshot → 点评 → 渲染 → 落盘，含 LLM 失败降级。"""
from __future__ import annotations

from types import SimpleNamespace

from crypto_analyst import briefing


def test_build_returns_full_tuple(monkeypatch, sample_snapshot):
    monkeypatch.setattr(briefing.analysis, "build_snapshot", lambda **kw: sample_snapshot)
    monkeypatch.setattr(briefing.report, "score_baselines", lambda snap: {})
    monkeypatch.setattr(briefing.report, "generate", lambda snap, commentary, baselines: "# 简报")
    monkeypatch.setattr(briefing.report, "save", lambda content: "reports/brief.md")

    class FakeLLM:
        available = True

        def __init__(self):
            pass

    class FakeAnalyst:
        def __init__(self, llm=None):
            pass

        def comment(self, facts):
            return "分析师点评正文"

    monkeypatch.setattr(briefing, "LLMClient", FakeLLM)
    monkeypatch.setattr(briefing, "MarketAnalyst", FakeAnalyst)

    snapshot, text, commentary, path = briefing.build(use_llm=True)

    assert snapshot is sample_snapshot
    assert text == "# 简报"
    assert commentary == "分析师点评正文"
    assert path == "reports/brief.md"


def test_build_skips_llm_when_unavailable(monkeypatch, sample_snapshot):
    monkeypatch.setattr(briefing.analysis, "build_snapshot", lambda **kw: sample_snapshot)
    monkeypatch.setattr(briefing.report, "score_baselines", lambda snap: {})
    monkeypatch.setattr(briefing.report, "generate", lambda snap, commentary, baselines: "# 简报")
    monkeypatch.setattr(briefing.report, "save", lambda content: "reports/brief.md")

    class FakeLLM:
        available = False  # 未配置 API Key

    monkeypatch.setattr(briefing, "LLMClient", FakeLLM)

    snapshot, text, commentary, path = briefing.build(use_llm=True)

    assert commentary is None
    assert text == "# 简报"


def test_build_degrades_on_llm_error(monkeypatch, sample_snapshot):
    monkeypatch.setattr(briefing.analysis, "build_snapshot", lambda **kw: sample_snapshot)
    monkeypatch.setattr(briefing.report, "score_baselines", lambda snap: {})
    monkeypatch.setattr(briefing.report, "generate", lambda snap, commentary, baselines: "# 简报")
    monkeypatch.setattr(briefing.report, "save", lambda content: "reports/brief.md")

    class FakeLLM:
        available = True

    class FailingAnalyst:
        def __init__(self, llm=None):
            pass

        def comment(self, facts):
            from crypto_analyst.llm import LLMError
            raise LLMError("接口超时")

    monkeypatch.setattr(briefing, "LLMClient", FakeLLM)
    monkeypatch.setattr(briefing, "MarketAnalyst", FailingAnalyst)

    # 不应抛出，点评降级为 None
    snapshot, text, commentary, path = briefing.build(use_llm=True)
    assert commentary is None
    assert text == "# 简报"


def test_build_no_llm_flag(monkeypatch, sample_snapshot):
    monkeypatch.setattr(briefing.analysis, "build_snapshot", lambda **kw: sample_snapshot)
    monkeypatch.setattr(briefing.report, "score_baselines", lambda snap: {})
    monkeypatch.setattr(briefing.report, "generate", lambda snap, commentary, baselines: "# 简报")
    monkeypatch.setattr(briefing.report, "save", lambda content: "reports/brief.md")

    snapshot, text, commentary, path = briefing.build(use_llm=False)
    assert commentary is None


def test_build_empty_symbols_returns_empty(monkeypatch):
    empty = SimpleNamespace(symbols=[])
    monkeypatch.setattr(briefing.analysis, "build_snapshot", lambda **kw: empty)

    snapshot, text, commentary, path = briefing.build()
    assert snapshot is empty
    assert text == ""
    assert commentary is None
    assert path == ""


def test_build_passes_symbols_to_snapshot(monkeypatch, sample_snapshot):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return sample_snapshot

    monkeypatch.setattr(briefing.analysis, "build_snapshot", fake_build)
    monkeypatch.setattr(briefing.report, "score_baselines", lambda snap: {})
    monkeypatch.setattr(briefing.report, "generate", lambda snap, commentary, baselines: "#")
    monkeypatch.setattr(briefing.report, "save", lambda content: "x.md")

    briefing.build(symbols=["BTC", "ETH"], use_llm=False)

    assert captured["symbols"] == ["BTC", "ETH"]
    assert captured["source"] == "brief"
