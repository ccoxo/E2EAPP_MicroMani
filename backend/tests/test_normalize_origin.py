from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from backend.core.defaults import default_config


def load_normalize_origin_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "normalize_origin.py"
    spec = importlib.util.spec_from_file_location("normalize_origin", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_frame_to_origin_updates_motion_state_and_action_only() -> None:
    module = load_normalize_origin_module()
    config = default_config()
    frame = {
        "observation.state": [999.0] * 14,
        "action": [1000.0] * 14,
        "observation.pulses": [
            100.0,
            200.0,
            300.0,
            1666.666667,
            -2500.0,
            -3333.333,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ],
    }
    target_origin = {
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [100.0, 200.0, 300.0, 0.0, 0.0, 0.0],
        "rightPulse": [0.0] * 6,
    }

    result = module.normalize_frame_to_origin(frame, target_origin, config)

    assert result["needs_manual_review"] is False
    assert result["observation.state"] == [
        0.0,
        0.0,
        0.0,
        1000.0,
        1000.0,
        1000.0,
        999.0,
        -0.0,
        -0.0,
        -0.0,
        0.0,
        0.0,
        0.0,
        999.0,
    ]
    assert result["action"] == [
        1.0,
        1.0,
        1.0,
        1001.0,
        1001.0,
        1001.0,
        1000.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1000.0,
    ]


def test_normalize_origin_dataset_dry_run_and_apply_rewrite_parquet(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    module = load_normalize_origin_module()
    import pyarrow as pa
    import pyarrow.parquet as pq

    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "data" / "chunk-000").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text('{"format":"lerobot-v3-native"}', encoding="utf-8")
    origin = {
        "leftValid": True,
        "rightValid": True,
        "valid": True,
        "leftPulse": [100.0, 200.0, 300.0, 0.0, 0.0, 0.0],
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    episode = {
        "id": "episode_000000",
        "episodeIndex": 0,
        "motionOrigin": origin,
        "motionCalibration": {"configHash": "same"},
    }
    (dataset_dir / "meta" / "episodes.jsonl").write_text(json.dumps(episode) + "\n", encoding="utf-8")
    table = pa.table(
        {
            "episode_index": pa.array([0]),
            "observation.pulses": pa.array(
                [[100.0, 200.0, 300.0, 1666.666667, -2500.0, -3333.333, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                type=pa.list_(pa.float32()),
            ),
            "observation.state": pa.array([[999.0] * 14], type=pa.list_(pa.float32())),
            "action": pa.array([[1000.0] * 14], type=pa.list_(pa.float32())),
        }
    )
    parquet_path = dataset_dir / "data" / "chunk-000" / "file-000.parquet"
    pq.write_table(table, parquet_path)
    before = parquet_path.read_bytes()

    dry_run = module.normalize_dataset(dataset_dir, apply=False, target_origin="first")
    assert dry_run["dryRun"] is True
    assert dry_run["changedRows"] == 1
    assert parquet_path.read_bytes() == before

    applied = module.normalize_dataset(dataset_dir, apply=True, target_origin="first")
    assert applied["dryRun"] is False
    assert applied["changedRows"] == 1
    rewritten = pq.read_table(parquet_path)
    assert rewritten.column("observation.state").to_pylist()[0][0:7] == [
        0.0,
        0.0,
        0.0,
        1000.0,
        1000.0,
        1000.0,
        999.0,
    ]
    assert rewritten.column("action").to_pylist()[0][6] == 1000.0
