"""简报生成与落盘：CLI 与内置调度共用同一条链路，避免两处逻辑漂移。"""
from __future__ import annotations

import logging

from . import analysis, report
from .agent import MarketAnalyst
from .llm import LLMClient, LLMError

logger = logging.getLogger(__name__)


def build(
    symbols: list[str] | None = None,
    use_llm: bool = True,
) -> tuple[analysis.MarketSnapshot, str, str | None, str]:
    """采集 → 可选点评 → 渲染 → 落盘，返回 (快照, 正文, 点评, 文件路径)。"""
    snapshot = analysis.build_snapshot(symbols=symbols, source="brief")
    if not snapshot.symbols:
        return snapshot, "", None, ""

    commentary = None
    if use_llm:
        llm = LLMClient()
        if llm.available:
            try:
                commentary = MarketAnalyst(llm).comment(report.facts_for_llm(snapshot))
            except LLMError as exc:
                logger.warning("分析师点评生成失败，仅输出规则部分：%s", exc)

    text = report.generate(
        snapshot,
        commentary=commentary,
        baselines=report.score_baselines(snapshot),
    )
    path = report.save(text)
    return snapshot, text, commentary, path
