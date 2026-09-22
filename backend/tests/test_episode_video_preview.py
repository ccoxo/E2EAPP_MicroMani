from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from backend.services.dataset_recorder import DatasetRecorderService


def test_video_fallback_uses_episode_chunk_file_and_offset(tmp_path):
    folder = tmp_path / "meta/episodes/chunk-000"
    folder.mkdir(parents=True)
    prefix = "videos/observation.images.global"
    pq.write_table(pa.Table.from_pylist([
        {"episode_index": 0, f"{prefix}/chunk_index": 0, f"{prefix}/file_index": 0,
         f"{prefix}/from_timestamp": 0.0, f"{prefix}/to_timestamp": 2.0},
        {"episode_index": 1, f"{prefix}/chunk_index": 2, f"{prefix}/file_index": 3,
         f"{prefix}/from_timestamp": 5.0, f"{prefix}/to_timestamp": 7.0},
    ]), folder / "file-000.parquet")
    recorder = DatasetRecorderService.__new__(DatasetRecorderService)
    recorder._decode_video_frame_to_jpeg = Mock(return_value=b"jpeg")
    episode = {"episodeIndex": 1, "frames": 60, "fps": 30}
    assert recorder._native_video_frame_to_jpeg(tmp_path, episode, "global", 12) == b"jpeg"
    recorder._decode_video_frame_to_jpeg.assert_called_once_with(
        tmp_path / "videos/observation.images.global/chunk-002/file-003.mp4", 162)
    for frame in (-1, 60):
        with pytest.raises(FileNotFoundError):
            recorder._native_video_frame_to_jpeg(tmp_path, episode, "global", frame)
    with pytest.raises(FileNotFoundError):
        recorder._native_video_frame_to_jpeg(tmp_path, {**episode, "episodeIndex": 9}, "global", 0)
    with pytest.raises(FileNotFoundError):
        recorder._native_video_frame_to_jpeg(tmp_path, episode, "wrist_left", 0)
    assert recorder._decode_video_frame_to_jpeg.call_count == 1
