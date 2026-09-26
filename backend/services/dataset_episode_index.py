"""按实际帧范围关联软件清单和 native 片段，不根据显示编号猜测。"""
from __future__ import annotations

import json
from pathlib import Path


def read_records(root: Path) -> list[dict]:
    path = root / "meta/episodes.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(item, dict) for item in records):
        raise ValueError("片段清单格式无效")
    return records


def read_native_index(root: Path, info: dict) -> list[dict]:
    import pyarrow.parquet as pq

    columns = ["episode_index", "dataset_from_index", "dataset_to_index", "length"]
    rows = [row for path in sorted((root / "meta/episodes").glob("chunk-*/*.parquet"))
            for row in pq.read_table(path, columns=columns).to_pylist()]
    for row in rows:
        if any(type(row.get(key)) is not int for key in columns):
            raise ValueError("原生片段索引包含无效整数")
    rows.sort(key=lambda row: row["episode_index"])
    end = 0
    for index, row in enumerate(rows):
        if (row["episode_index"] != index or row["length"] <= 0
                or row["dataset_from_index"] != end
                or row["dataset_to_index"] - end != row["length"]):
            raise ValueError("原生片段索引不连续或帧范围不一致")
        end = row["dataset_to_index"]
    if len(rows) != info.get("total_episodes") or end != info.get("total_frames"):
        raise ValueError("原生片段索引与总片段数或总帧数不一致")
    return rows


def match_records(records: list[dict], native: list[dict]) -> tuple[list[tuple[dict, dict]], list[int]]:
    ranges = {(row["dataset_from_index"], row["dataset_to_index"]): row for row in native}
    matched = []
    used = set()
    ids = set()
    for item in records:
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("软件片段编号缺失或重复")
        ids.add(identity)
        # 未保存的丢弃记录没有 native 数据，不要求伪造帧范围。
        if item.get("status") == "discarded" and item.get("frames", 0) == 0:
            continue
        values = [item.get(key) for key in ("datasetFromIndex", "datasetToIndex", "frames")]
        if any(type(value) is not int for value in values):
            raise ValueError(f"片段 {identity} 缺少有效帧范围，无法确认原生编号")
        start, stop, frames = values
        row = ranges.get((start, stop))
        if row is None or row["length"] != frames:
            raise ValueError(f"片段 {identity} 的帧范围与原生数据不符")
        index = row["episode_index"]
        if index in used:
            raise ValueError("多条软件记录指向同一个原生片段")
        used.add(index)
        matched.append((item, row))
    return matched, [row["episode_index"] for row in native if row["episode_index"] not in used]


def is_retained(item: dict) -> bool:
    return not item.get("deleted", False) and item.get("status") not in {"invalid", "discarded", "deleted"}
