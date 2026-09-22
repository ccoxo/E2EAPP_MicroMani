"""复制已保存的数据集并重算图像统计；原目录、视频和数值标签保持原样。"""

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyarrow as pa
import pyarrow.parquet as pq
from lerobot.datasets.compute_stats import aggregate_stats
from backend.services.video_statistics import decoded_video_statistics


def repair(source: Path, destination: Path):
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents or destination.exists():
        raise ValueError("输出必须是原数据集之外尚不存在的新目录")
    info = json.loads((source / "meta/info.json").read_text(encoding="utf-8"))
    keys = [key for key, value in info["features"].items() if value["dtype"] == "video"]
    updates = []
    episode_stats = []
    for path in sorted((source / "meta/episodes").rglob("*.parquet")):
        table = pq.read_table(path)
        rows = table.to_pylist()
        for row in rows:
            stats = {}
            for key in keys:
                prefix = f"videos/{key}"
                video = (source / info["video_path"].format(video_key=key,
                    chunk_index=row[f"{prefix}/chunk_index"], file_index=row[f"{prefix}/file_index"])).resolve()
                if source not in video.parents:
                    raise ValueError("video path is outside dataset")
                stats[key] = decoded_video_statistics(video, row[f"{prefix}/from_timestamp"],
                                                       row[f"{prefix}/to_timestamp"], row["length"])
                for name, value in stats[key].items():
                    row[f"stats/{key}/{name}"] = value.tolist()
            episode_stats.append(stats)
            print(f"verified episode {row['episode_index']}: {row['length']} frames", flush=True)
        updates.append((path.relative_to(source), pa.Table.from_pylist(rows, schema=table.schema)))
    if not episode_stats:
        raise ValueError("没有已保存的 episode")
    stats = json.loads((source / "meta/stats.json").read_text(encoding="utf-8"))
    repaired = aggregate_stats(episode_stats)
    for key in keys:
        stats[key] = {name: value.tolist() for name, value in repaired[key].items()}
    # 完成全部读取和帧数校验之后才创建副本。
    shutil.copytree(source, destination)
    for path, table in updates:
        pq.write_table(table, destination / path)
    (destination / "meta/stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "meta/video_statistics_repair.json").write_text(json.dumps({
        "source": str(source), "method": "all decoded RGB frames; LeRobot spatial downsampling; uint8 histogram with float64 moments",
        "episodes": len(episode_stats), "videos_and_numeric_data": "unchanged",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(repair(args.source, args.destination))
