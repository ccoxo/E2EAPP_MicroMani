from pathlib import Path

from backend.services.dataset_recorder import LIST_EPISODE_WARNINGS_LIMIT, DatasetRecorderService


def _episode() -> dict:
    return {"id": "episode_000000", "frames": 10, "warnings": [f"late frame {i}" for i in range(500)]}


def test_list_summary_caps_warnings_and_keeps_total(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    result = recorder._episode_for_api(tmp_path, "unit", _episode(), include_samples=False,
                                       warnings_limit=LIST_EPISODE_WARNINGS_LIMIT, features={}, camera_resolutions={})
    assert result["warnings"] == [f"late frame {i}" for i in range(LIST_EPISODE_WARNINGS_LIMIT)]
    assert result["warningCount"] == 500
    assert result["features"] == result["featureSummary"] == {}


def test_episode_detail_keeps_all_warnings(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    result = recorder._episode_for_api(tmp_path, "unit", _episode(), include_samples=False)
    assert len(result["warnings"]) == 500
    assert result["warningCount"] == 500
