from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.defaults import default_config  # noqa: E402
from backend.core.units import pulses_to_ui_state  # noqa: E402

MOTION_STATE_INDICES = (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize LeRobot motion state/action to a dataset work origin.")
    parser.add_argument("--dataset", required=True, help="Local LeRobot dataset directory")
    parser.add_argument("--apply", action="store_true", help="Rewrite parquet files. Default is dry-run.")
    parser.add_argument(
        "--target-origin",
        default="first",
        help="Target origin: first, current, or a JSON file containing a motionOrigin object.",
    )
    parser.add_argument("--show-history", action="store_true", help="Only report origin groups and manual-review issues.")
    return parser.parse_args()


def normalize_frame_to_origin(
    frame: dict[str, Any],
    target_origin: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = _float_list(frame.get("observation.state"), 14)
    action = _float_list(frame.get("action"), 14)
    pulses = _float_list(frame.get("observation.pulses"), 12)
    if state is None:
        return {"needs_manual_review": True, "reason": "invalid observation.state"}
    if action is None:
        return {"needs_manual_review": True, "reason": "invalid action"}
    if pulses is None:
        return {"needs_manual_review": True, "reason": "invalid observation.pulses"}
    origin_pulses = _origin_pulses(target_origin)
    if origin_pulses is None:
        return {"needs_manual_review": True, "reason": "invalid target origin"}

    conversion_config = config or default_config()
    relative_pulses = [float(pulses[index]) - origin_pulses[index] for index in range(12)]
    motion_ui = pulses_to_ui_state(relative_pulses, conversion_config)
    motion_state = _motion_ui_to_lerobot_state(motion_ui)

    next_state = list(state)
    for state_index, value in zip(MOTION_STATE_INDICES, motion_state):
        next_state[state_index] = _clean_float(value)

    next_action = list(action)
    for state_index in MOTION_STATE_INDICES:
        next_action[state_index] = _clean_float(action[state_index] + next_state[state_index] - state[state_index])

    return {
        "needs_manual_review": False,
        "observation.state": next_state,
        "action": next_action,
        "changed": _different(state, next_state) or _different(action, next_action),
    }


def normalize_dataset(dataset_dir: Path, *, apply: bool = False, target_origin: str = "first") -> dict[str, Any]:
    dataset_dir = Path(dataset_dir).expanduser()
    if not (dataset_dir / "meta" / "info.json").exists():
        raise RuntimeError(f"local dataset path is invalid or missing meta/info.json: {dataset_dir}")

    episodes = _read_episodes(dataset_dir)
    app_info = _read_json(dataset_dir / "meta" / "appstation_info.json")
    target = _target_origin(target_origin, episodes, app_info)
    config = _conversion_config(app_info)
    target_hash = _target_calibration_hash(target_origin, episodes, app_info)
    origin_groups = _origin_groups(episodes)
    manual_review: list[dict[str, Any]] = []
    missing_origin = [
        str(episode.get("id") or episode.get("episodeIndex"))
        for episode in episodes
        if not isinstance(episode.get("motionOrigin"), dict)
    ]
    for episode_id in missing_origin:
        manual_review.append({"episode": episode_id, "reason": "missing motionOrigin"})

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"pyarrow is required to normalize parquet files: {exc}") from exc

    episodes_by_index = {
        int(episode.get("episodeIndex")): episode
        for episode in episodes
        if isinstance(episode.get("episodeIndex"), int)
    }
    changed_rows = 0
    skipped_rows = 0
    files: list[dict[str, Any]] = []
    for parquet_path in sorted((dataset_dir / "data").glob("chunk-*/*.parquet")):
        table = pq.read_table(parquet_path)
        required_columns = ("episode_index", "observation.pulses", "observation.state", "action")
        missing = [name for name in required_columns if name not in table.column_names]
        if missing:
            manual_review.append({"file": str(parquet_path), "reason": f"missing columns: {', '.join(missing)}"})
            continue

        episode_indices = table.column("episode_index").to_pylist()
        pulses_values = table.column("observation.pulses").to_pylist()
        state_values = table.column("observation.state").to_pylist()
        action_values = table.column("action").to_pylist()
        file_changed_rows = 0
        file_skipped_rows = 0

        for row_index, episode_index in enumerate(episode_indices):
            episode = episodes_by_index.get(int(episode_index))
            if episode is None:
                file_skipped_rows += 1
                manual_review.append(
                    {"file": str(parquet_path), "row": row_index, "reason": f"missing episode metadata: {episode_index}"}
                )
                continue
            calibration_hash = _calibration_hash(episode)
            if target_hash and calibration_hash and calibration_hash != target_hash:
                file_skipped_rows += 1
                manual_review.append(
                    {
                        "file": str(parquet_path),
                        "row": row_index,
                        "episode": episode.get("id"),
                        "reason": "needs_manual_review: motion calibration differs from target origin group",
                    }
                )
                continue
            if not isinstance(episode.get("motionOrigin"), dict):
                file_skipped_rows += 1
                continue

            result = normalize_frame_to_origin(
                {
                    "observation.pulses": pulses_values[row_index],
                    "observation.state": state_values[row_index],
                    "action": action_values[row_index],
                },
                target,
                config,
            )
            if result["needs_manual_review"]:
                file_skipped_rows += 1
                manual_review.append(
                    {
                        "file": str(parquet_path),
                        "row": row_index,
                        "episode": episode.get("id"),
                        "reason": f"needs_manual_review: {result['reason']}",
                    }
                )
                continue
            if bool(result.get("changed")):
                state_values[row_index] = result["observation.state"]
                action_values[row_index] = result["action"]
                file_changed_rows += 1

        if apply and file_changed_rows:
            table = table.set_column(
                table.schema.get_field_index("observation.state"),
                "observation.state",
                pa.array(state_values, type=table.schema.field("observation.state").type),
            )
            table = table.set_column(
                table.schema.get_field_index("action"),
                "action",
                pa.array(action_values, type=table.schema.field("action").type),
            )
            pq.write_table(table, parquet_path)

        changed_rows += file_changed_rows
        skipped_rows += file_skipped_rows
        files.append(
            {
                "path": str(parquet_path),
                "changedRows": file_changed_rows,
                "skippedRows": file_skipped_rows,
                "wouldWrite": bool(file_changed_rows),
                "wrote": bool(apply and file_changed_rows),
            }
        )

    return {
        "dataset": str(dataset_dir),
        "dryRun": not apply,
        "targetOrigin": target,
        "originGroups": origin_groups,
        "changedRows": changed_rows,
        "skippedRows": skipped_rows,
        "files": files,
        "needsManualReview": manual_review,
    }


def _float_list(raw: Any, expected: int) -> list[float] | None:
    if not isinstance(raw, list) or len(raw) != expected:
        return None
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return None


def _origin_pulses(origin: dict[str, Any]) -> list[float] | None:
    left = _float_list(origin.get("leftPulse"), 6)
    right = _float_list(origin.get("rightPulse"), 6)
    if left is None or right is None:
        return None
    if not bool(origin.get("leftValid", origin.get("valid", False))):
        return None
    if not bool(origin.get("rightValid", origin.get("valid", False))):
        return None
    return left + right


def _motion_ui_to_lerobot_state(motion_ui: list[float]) -> list[float]:
    values = (list(motion_ui) + [0.0] * 12)[:12]
    return [
        values[0],
        values[1],
        values[2],
        values[3] * 1000.0,
        values[4] * 1000.0,
        values[5] * 1000.0,
        values[6],
        values[7],
        values[8],
        values[9] * 1000.0,
        values[10] * 1000.0,
        values[11] * 1000.0,
    ]


def _clean_float(value: float) -> float:
    value = float(value)
    return 0.0 if abs(value) < 1e-9 else value


def _different(left: list[float], right: list[float]) -> bool:
    return any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(left, right))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_episodes(dataset_dir: Path) -> list[dict[str, Any]]:
    path = dataset_dir / "meta" / "episodes.jsonl"
    if not path.exists():
        return []
    episodes: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            episodes.append(item)
    return episodes


def _target_origin(raw: str, episodes: list[dict[str, Any]], app_info: dict[str, Any]) -> dict[str, Any]:
    if raw == "first":
        for episode in episodes:
            origin = episode.get("motionOrigin")
            if isinstance(origin, dict) and _origin_pulses(origin) is not None:
                return deepcopy(origin)
        raise RuntimeError("no valid episode motionOrigin found")
    if raw == "current":
        origin = app_info.get("sessionOrigin")
        if isinstance(origin, dict) and _origin_pulses(origin) is not None:
            return deepcopy(origin)
        return _target_origin("first", episodes, app_info)
    path = Path(raw).expanduser()
    payload = _read_json(path)
    origin = payload.get("motionOrigin") if isinstance(payload.get("motionOrigin"), dict) else payload
    if not isinstance(origin, dict) or _origin_pulses(origin) is None:
        raise RuntimeError(f"target origin file is invalid: {path}")
    return deepcopy(origin)


def _conversion_config(app_info: dict[str, Any]) -> dict[str, Any]:
    config = default_config()
    motion = app_info.get("hardware", {}).get("motion") if isinstance(app_info.get("hardware"), dict) else None
    if isinstance(motion, dict) and isinstance(motion.get("kinematics"), dict):
        config["motion"]["kinematics"] = deepcopy(motion["kinematics"])
    return config


def _origin_groups(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for episode in episodes:
        origin = episode.get("motionOrigin")
        if not isinstance(origin, dict):
            continue
        key = json.dumps(
            {
                "leftPulse": origin.get("leftPulse"),
                "rightPulse": origin.get("rightPulse"),
                "leftValid": origin.get("leftValid", origin.get("valid", False)),
                "rightValid": origin.get("rightValid", origin.get("valid", False)),
                "updatedAt": origin.get("updatedAt", 0),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        group = groups.setdefault(key, {"origin": origin, "episodes": []})
        group["episodes"].append(episode.get("id") or episode.get("episodeIndex"))
    return list(groups.values())


def _calibration_hash(episode: dict[str, Any]) -> str:
    calibration = episode.get("motionCalibration")
    if not isinstance(calibration, dict):
        return ""
    return str(calibration.get("configHash", ""))


def _target_calibration_hash(raw: str, episodes: list[dict[str, Any]], app_info: dict[str, Any]) -> str:
    if raw == "first":
        for episode in episodes:
            if isinstance(episode.get("motionOrigin"), dict):
                return _calibration_hash(episode)
        return ""
    if raw == "current":
        motion = app_info.get("hardware", {}).get("motion") if isinstance(app_info.get("hardware"), dict) else {}
        return str(motion.get("configHash", "")) if isinstance(motion, dict) else ""
    return ""


def main() -> int:
    args = parse_args()
    result = normalize_dataset(
        Path(args.dataset),
        apply=bool(args.apply and not args.show_history),
        target_origin=str(args.target_origin),
    )
    if args.show_history:
        result = {
            "dataset": result["dataset"],
            "dryRun": True,
            "originGroups": result["originGroups"],
            "needsManualReview": result["needsManualReview"],
        }
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
