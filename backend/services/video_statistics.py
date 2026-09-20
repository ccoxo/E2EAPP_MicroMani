"""视频统计在平方之前转为浮点，避免 uint8 溢出。"""

import numpy as np
from lerobot.datasets.compute_stats import RunningQuantileStats


class FloatVideoStatistics(RunningQuantileStats):
    def update(self, batch):
        super().update(np.asarray(batch, dtype=np.float64))


def decoded_video_statistics(path, start_s, end_s, expected_frames):
    """从已存视频重算 RGB 统计，逐帧空间下采样，不改视频。"""
    import av
    from lerobot.datasets.compute_stats import auto_downsample_height_width

    histogram = np.zeros((3, 256), dtype=np.int64)
    frames = 0
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            timestamp = float(frame.pts * frame.time_base)
            if timestamp < start_s - 1e-6 or timestamp >= end_s - 1e-6:
                continue
            image = auto_downsample_height_width(frame.to_ndarray(format="rgb24").transpose(2, 0, 1))
            for channel in range(3):
                histogram[channel] += np.bincount(image[channel].ravel(), minlength=256)
            frames += 1
    if frames != expected_frames or frames == 0:
        raise ValueError(f"video frame count mismatch: {path}: {frames} != {expected_frames}")
    counts = histogram.sum(axis=1)
    levels = np.arange(256, dtype=np.float64)
    mean = (histogram * levels).sum(axis=1) / counts
    variance = (histogram * (levels[None, :] - mean[:, None]) ** 2).sum(axis=1) / counts
    stats = {"min": np.array([np.flatnonzero(h)[0] for h in histogram]),
             "max": np.array([np.flatnonzero(h)[-1] for h in histogram]),
             "mean": mean, "std": np.sqrt(variance)}
    for quantile in (0.01, 0.1, 0.5, 0.9, 0.99):
        values = []
        for hist, count in zip(histogram, counts):
            rank = (count - 1) * quantile
            lower = np.searchsorted(hist.cumsum(), int(np.floor(rank)), side="right")
            upper = np.searchsorted(hist.cumsum(), int(np.ceil(rank)), side="right")
            values.append(lower + (upper - lower) * (rank - np.floor(rank)))
        stats[f"q{int(quantile * 100):02d}"] = np.array(values)
    result = {key: (value.astype(np.float64) / 255).reshape(3, 1, 1) for key, value in stats.items()}
    result["count"] = np.array([counts[0]])
    return result
