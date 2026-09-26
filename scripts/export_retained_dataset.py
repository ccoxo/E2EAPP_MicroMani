"""将明确保留的片段导出为新 LeRobot 数据集；默认仅检查，不修改原集。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.data_contract import validate_data_contract
from backend.services.dataset_episode_index import is_retained, match_records, read_native_index, read_records


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def export_plan(source: Path, confirmed_discarded: list[int]) -> dict:
    info = read_json(source / "meta/info.json")
    app = read_json(source / "meta/appstation_info.json")
    validate_data_contract(info.get("dataContract"))
    validate_data_contract(app.get("dataContract"))
    native = read_native_index(source, info)
    matched, untracked = match_records(read_records(source), native)
    if any(type(index) is not int for index in confirmed_discarded) or len(set(confirmed_discarded)) != len(confirmed_discarded):
        raise ValueError("已确认弃用索引必须为不重复的整数")
    discarded = set(confirmed_discarded)
    all_indices = {row["episode_index"] for row in native}
    keep = sorted(row["episode_index"] for item, row in matched if is_retained(item))
    if discarded - all_indices or discarded.intersection(keep):
        raise ValueError("弃用证据与现有保留记录冲突或索引越界")
    if set(untracked) - discarded:
        raise ValueError("存在没有保留/弃用记录的片段，需要提供经日志核实的原生弃用索引")
    if not keep:
        raise ValueError("没有可保留片段")
    return {"source": str(source), "keepNativeIndices": keep,
            "excludedNativeIndices": sorted(all_indices - set(keep)),
            "confirmedHistoricalDiscardIndices": sorted(discarded),
            "untrackedNativeIndices": untracked,
            "frames": sum(row["length"] for row in native if row["episode_index"] in keep)}


def hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(root))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def export_dataset(source: Path, destination: Path, confirmed_discarded: list[int]) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("输出必须是与原集分离的新目录")
    if destination.exists():
        raise FileExistsError("输出目录已经存在，拒绝覆盖")
    plan = export_plan(source, confirmed_discarded)
    if not plan["excludedNativeIndices"]:
        raise ValueError("没有需要排除的片段，无需导出")
    before = hashes(source)
    info = read_json(source / "meta/info.json")
    app = read_json(source / "meta/appstation_info.json")
    native = read_native_index(source, info)
    matched, _ = match_records(read_records(source), native)
    old_records = {row["episode_index"]: item for item, row in matched}
    # 仅使用本地数据，缓存放到目标父目录，禁止数据加载器写入原集。
    os.environ.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    os.environ["HF_DATASETS_CACHE"] = str(destination.parent / ".retained-export-cache")
    def protect_source(event, args):
        paths = []
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                paths = [path]
        elif event in {"os.remove", "os.rmdir", "os.rename", "os.mkdir"}:
            paths = args[:2] if event == "os.rename" else args[:1]
        for value in paths:
            if isinstance(value, (str, bytes, os.PathLike)) and Path(os.fsdecode(value)).resolve().is_relative_to(source):
                if event == "os.mkdir" and Path(value).is_dir():
                    continue
                raise PermissionError("导出期间禁止修改原始数据集")
    sys.addaudithook(protect_source)
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from lerobot.datasets.dataset_tools import delete_episodes
    dataset = LeRobotDataset(f"local/{source.name}", root=source, video_backend="pyav")
    result = delete_episodes(dataset, plan["excludedNativeIndices"], output_dir=destination,
                             repo_id=f"local/{destination.name}")
    result.finalize()
    new_info = read_json(destination / "meta/info.json")
    new_info["dataContract"] = deepcopy(info["dataContract"])
    write_json(destination / "meta/info.json", new_info)
    new_native = read_native_index(destination, new_info)
    if len(new_native) != len(plan["keepNativeIndices"]) or new_info["total_frames"] != plan["frames"]:
        raise RuntimeError("导出数量与保留清单不符")
    output_records = []
    for new, old_index in zip(new_native, plan["keepNativeIndices"], strict=True):
        item = deepcopy(old_records[old_index])
        item["sourceEpisode"] = {"dataset": str(source), "id": item["id"], "nativeEpisodeIndex": old_index,
                                 "datasetFromIndex": item["datasetFromIndex"], "datasetToIndex": item["datasetToIndex"]}
        item.update(id=f'episode_{new["episode_index"]:06d}', episodeIndex=new["episode_index"],
                    datasetFromIndex=new["dataset_from_index"], datasetToIndex=new["dataset_to_index"])
        output_records.append(item)
    (destination / "meta/episodes.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in output_records), encoding="utf-8")
    app["name"] = destination.name
    app["retainedExport"] = plan
    selections = [item.get("participation") for item in output_records if "participation" in item]
    if len(selections) == len(output_records) and all(value == selections[0] for value in selections):
        app["participation"] = selections[0]
    write_json(destination / "meta/appstation_info.json", app)
    # 逐片段对照数值列，确保索引重排没有改变示教值或帧顺序。
    import pyarrow.dataset as ds
    columns = [key for key, spec in info["features"].items() if spec["dtype"] not in {"video", "image"}
               and key not in {"index", "episode_index"}]
    old_data = ds.dataset([str(p) for p in sorted((source / "data").glob("chunk-*/*.parquet"))], format="parquet")
    new_data = ds.dataset([str(p) for p in sorted((destination / "data").glob("chunk-*/*.parquet"))], format="parquet")
    for new_index, old_index in enumerate(plan["keepNativeIndices"]):
        old = old_data.to_table(columns=columns, filter=ds.field("episode_index") == old_index).sort_by("frame_index")
        new = new_data.to_table(columns=columns, filter=ds.field("episode_index") == new_index).sort_by("frame_index")
        if not old.equals(new):
            raise RuntimeError(f"导出数值校验失败：原生片段 {old_index}")
    after = hashes(source)
    if before != after:
        raise RuntimeError("原集文件校验值发生变化")
    report = {**plan, "destination": str(destination), "sourceUnchanged": True,
              "numericFramesVerified": True, "sourceSha256": before}
    write_json(destination / "meta/retained_export_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--discarded-indices-file", type=Path, help="日志核实后的原生弃用索引 JSON 数组")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    confirmed = read_json(args.discarded_indices_file) if args.discarded_indices_file else []
    if not isinstance(confirmed, list):
        raise ValueError("弃用索引文件必须是 JSON 数组")
    report = export_dataset(args.source, args.destination, confirmed) if args.apply else export_plan(args.source, confirmed)
    print(json.dumps({k: v for k, v in report.items() if k != "sourceSha256"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
