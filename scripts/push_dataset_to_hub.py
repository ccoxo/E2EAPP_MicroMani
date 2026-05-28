from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push a local LeRobot dataset to Hugging Face Hub.")
    parser.add_argument("--repo-id", required=True, help="Target Hugging Face repo id, for example org/dataset")
    parser.add_argument("--local-path", required=True, help="Local LeRobot dataset directory")
    parser.add_argument("--private", action="store_true", help="Create or update the Hub repo as private")
    return parser.parse_args()


def push_dataset(repo_id: str, local_path: Path, *, private: bool) -> dict[str, Any]:
    info_path = local_path / "meta" / "info.json"
    if not info_path.exists():
        raise RuntimeError(f"local dataset path is invalid or missing meta/info.json: {local_path}")

    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"lerobot[dataset] is not installed in backend runtime: {exc}") from exc

    dataset = LeRobotDataset(repo_id, root=local_path)
    kwargs: dict[str, Any] = {}
    try:
        parameters = dict(inspect.signature(dataset.push_to_hub).parameters)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    except (TypeError, ValueError):
        parameters = {}
        accepts_kwargs = True
    if "private" in parameters or accepts_kwargs:
        kwargs["private"] = private
    dataset.push_to_hub(**kwargs)
    return {"repoId": repo_id, "localPath": str(local_path), "private": private}


def main() -> int:
    args = parse_args()
    result = push_dataset(args.repo_id, Path(args.local_path).expanduser(), private=bool(args.private))
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
