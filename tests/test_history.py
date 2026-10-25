"""快照落盘测试：JSONL 写入、读取、source 标记与坏行跳过。"""
from __future__ import annotations

import json
import time

from crypto_analyst import history


def test_append_snapshot_writes_one_line_per_symbol(tmp_path, monkeypatch, sample_snapshot):
    monkeypatch.setattr(history.config, "STATE_DIR", tmp_path)

    history.append_snapshot(sample_snapshot, source="watch")

    files = list(tmp_path.glob("snapshots/*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text("utf-8").strip().splitlines()
    assert len(lines) == 1  # 单标的快照只写一行
    record = json.loads(lines[0])
    assert record["symbol"] == "BTC"
    assert record["source"] == "watch"
    assert record["price"] == 100.0
    assert "total_score" in record
    assert "scores" in record
    assert "epoch" in record
    assert record["ts"] == sample_snapshot.generated_at


def test_iter_records_reads_all_files(tmp_path, monkeypatch, sample_snapshot):
    monkeypatch.setattr(history.config, "STATE_DIR", tmp_path)
    history.append_snapshot(sample_snapshot, source="brief")

    records = list(history.iter_records())
    assert len(records) == 1
    assert records[0]["source"] == "brief"


def test_iter_records_skips_malformed_lines(tmp_path, monkeypatch, sample_snapshot):
    monkeypatch.setattr(history.config, "STATE_DIR", tmp_path)
    history.append_snapshot(sample_snapshot, source="watch")

    # 手动插入坏行
    snap_dir = tmp_path / "snapshots"
    file = next(snap_dir.glob("*.jsonl"))
    with file.open("a", encoding="utf-8") as fh:
        fh.write("not valid json\n")
        fh.write("\n")  # 空行

    records = list(history.iter_records())
    assert len(records) == 1  # 坏行与空行被静默跳过


def test_iter_records_returns_empty_when_no_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(history.config, "STATE_DIR", tmp_path / "nonexistent")
    assert list(history.iter_records()) == []


def test_append_snapshot_is_safe_on_io_error(tmp_path, monkeypatch, sample_snapshot):
    """落盘失败不应抛异常（主流程不中断）。"""
    # 把 STATE_DIR 指向一个文件，mkdir 会抛 NotADirectoryError（OSError 子类）
    blocker = tmp_path / "blocker"
    blocker.write_text("x", "utf-8")
    monkeypatch.setattr(history.config, "STATE_DIR", blocker)

    # 不应抛出
    history.append_snapshot(sample_snapshot, source="watch")
