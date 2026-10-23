"""快照落盘：每次 build_snapshot 顺手把评分快照追加到 .state/snapshots/YYYYMMDD.jsonl。

为回测积累时间序列——评分的权重是经验值，只有攒下"当时打分 + 后来价格"
才能校准。落盘失败不影响主流程。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

from . import config


def _dir() -> Path:
    return config.STATE_DIR / "snapshots"


def append_snapshot(snapshot: Any, source: str = "unknown") -> None:
    """每个标的写一行（symbol 级粒度，回测按行流式读取）。

    source 标记数据来源（watch / brief / snapshot / ask），回测默认只吃 watch，
    避免手动调试与问答触发的快照混入样本。
    """
    try:
        path = _dir() / time.strftime("%Y%m%d.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        epoch = time.time()
        with path.open("a", encoding="utf-8") as fh:
            for item in snapshot.symbols:
                fh.write(
                    json.dumps(
                        {
                            "epoch": epoch,
                            "ts": snapshot.generated_at,
                            "source": source,
                            "symbol": item.symbol,
                            "price": item.price,
                            "total_score": item.total_score,
                            "regime": item.regime,
                            "scores": item.scores,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except OSError:
        pass


def iter_records() -> Iterator[dict[str, Any]]:
    """按文件名升序产出全部历史记录，坏行静默跳过。"""
    directory = _dir()
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue
