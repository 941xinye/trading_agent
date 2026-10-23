"""简报渲染测试：结构、关键字段与文件落盘。"""
from __future__ import annotations

from crypto_analyst import config, report


def test_generate_contains_sections_and_symbol(sample_snapshot):
    text = report.generate(sample_snapshot, commentary="点评第一行\n点评第二行")
    assert text.startswith("# 加密市场行情简报")
    assert "## 一、结论速览" in text
    assert "## 三、币种明细" in text
    assert "BTC/USDT" in text
    assert "> **分析师点评**" in text
    assert "## 六、风险声明" in text


def test_generate_shows_warnings_section(snapshot_factory):
    text = report.generate(snapshot_factory(warnings=["新闻获取失败: timeout"]))
    assert "## 五、数据缺口" in text
    assert "- 新闻获取失败: timeout" in text


def test_generate_reports_all_sources_ok(snapshot_factory):
    text = report.generate(snapshot_factory(warnings=[]))
    assert "- 全部数据源正常。" in text


def test_facts_for_llm_is_compact(sample_snapshot):
    facts = report.facts_for_llm(sample_snapshot)
    assert "BTC: 价格 100.00" in facts
    assert "（震荡）" in facts
    assert "日线趋势" in facts


def test_symbol_section_lists_timeframes(sample_snapshot):
    lines = report._symbol_section(sample_snapshot.symbols[0])
    body = "\n".join(lines)
    for timeframe in config.TIMEFRAMES:
        assert f"| {timeframe} |" in body
    assert "评分拆解：" in body
    assert "关键价位：" in body


def test_save_writes_utf8_file(tmp_path, monkeypatch, sample_snapshot):
    from pathlib import Path

    monkeypatch.setattr(config, "REPORT_DIR", tmp_path)
    path = report.save(report.generate(sample_snapshot), prefix="test")
    assert path.endswith(".md")
    assert "加密市场行情简报" in Path(path).read_text("utf-8")
