# 阅读导航 04｜后端业务与采集
# 职责：在独立进程内打开并读取相机，把原始 BGR 帧和采集时刻写入共享内存（不压缩），
#       主进程只拷贝最新帧；采集时刻在本进程读到帧时记录，不受主进程 GIL 繁忙影响。
# 启用：APPSTATION_CAMERA_CAPTURE_MODE=shm（见 backend/drivers/camera_opencv.py）。

from __future__ import annotations

import json
import queue
import sys
import time
from multiprocessing import shared_memory
from typing import Any

from backend.workers.camera_capture_worker import _CameraCaptureWorker

# 共享内存布局：64 字节头 + 两个帧槽（双缓冲）。
# 头（小端 int64）：[0] seq（提交序号，最后写入），[1] 写入槽号，[2] 采集时刻 monotonic 纳秒，
#                    [3] width，[4] height，[5] channels，[6] 采集线程最长间隔（毫秒，诊断用）。
SHM_HEADER_BYTES = 64
SHM_HEADER_FIELDS = 8
SHM_STOP_FIELD = 7  # 主进程置 1 请求停止；不经 stdin（Windows 上阻塞读管道会连带卡住同进程其他 I/O）
SHM_SLOTS = 2
GAP_REPORT_MS = 100.0


def shm_size(width: int, height: int, channels: int = 3) -> int:
    return SHM_HEADER_BYTES + SHM_SLOTS * width * height * channels


class ShmFrameWriter:
    """单写者双缓冲：先写另一槽的像素，再写时刻和槽号，最后递增 seq 作为提交。"""

    def __init__(self, buf: Any, width: int, height: int, channels: int = 3) -> None:
        import numpy as np

        self.np = np
        self.width, self.height, self.channels = width, height, channels
        self.frame_bytes = width * height * channels
        self.header = np.ndarray((SHM_HEADER_FIELDS,), dtype=np.int64, buffer=buf, offset=0)
        self.slots = [
            np.ndarray((height, width, channels), dtype=np.uint8, buffer=buf,
                       offset=SHM_HEADER_BYTES + i * self.frame_bytes)
            for i in range(SHM_SLOTS)
        ]
        self.header[3], self.header[4], self.header[5] = width, height, channels

    def write(self, frame: Any, monotonic_s: float, max_gap_ms: float = 0.0) -> int:
        seq = int(self.header[0]) + 1
        slot = seq % SHM_SLOTS
        self.np.copyto(self.slots[slot], frame)
        self.header[6] = int(max_gap_ms)
        self.header[2] = int(monotonic_s * 1e9)
        self.header[1] = slot
        self.header[0] = seq
        return seq


class ShmFrameReader:
    """读者拷贝最新已提交槽；拷贝前后 seq 相差不超过 1 时该槽未被覆盖，否则重试。"""

    def __init__(self, buf: Any, width: int, height: int, channels: int = 3) -> None:
        import numpy as np

        self.np = np
        self.frame_bytes = width * height * channels
        self.header = np.ndarray((SHM_HEADER_FIELDS,), dtype=np.int64, buffer=buf, offset=0)
        self.slots = [
            np.ndarray((height, width, channels), dtype=np.uint8, buffer=buf,
                       offset=SHM_HEADER_BYTES + i * self.frame_bytes)
            for i in range(SHM_SLOTS)
        ]

    def seq(self) -> int:
        return int(self.header[0])

    def request_stop(self) -> None:
        self.header[SHM_STOP_FIELD] = 1

    def read_latest(self, last_seq: int) -> tuple[int, Any, float, float] | None:
        for _ in range(4):
            seq1 = int(self.header[0])
            if seq1 <= 0 or seq1 == last_seq:
                return None
            slot = int(self.header[1])
            ts_ns = int(self.header[2])
            gap_ms = float(self.header[6])
            frame = self.slots[slot].copy()
            seq2 = int(self.header[0])
            if seq2 - seq1 <= 1 and slot == seq1 % SHM_SLOTS:
                return seq1, frame, ts_ns / 1e9, gap_ms
        return None


class _ShmCaptureWorker(_CameraCaptureWorker):
    """复用 _CameraCaptureWorker 的打开/参数设置/父进程检测，只替换取帧与发布方式。"""

    def __init__(self, startup: dict[str, Any], status_queue: Any, command_queue: Any) -> None:
        parent_pid = startup.get("parentPid")
        super().__init__(
            int(startup["index"]), int(startup["width"]), int(startup["height"]), float(startup["fps"]),
            startup.get("profile"),
            [(int(backend), str(label)) for backend, label in startup["backendCandidates"]],
            status_queue, command_queue, int(parent_pid) if parent_pid is not None else None,
        )
        self.shm_name = str(startup["shmName"])

    def _stage(self, stage: str) -> None:
        self._publish({"type": "stage", "ok": True, "opened": False, "stage": stage,
                       "monotonicMs": int(time.monotonic() * 1000)})

    def run(self) -> None:
        self._stage("started")
        com_token: object | None = None
        shm = None
        try:
            import cv2

            self.cv2 = cv2
            self._stage("cv2_imported")
            shm = shared_memory.SharedMemory(name=self.shm_name)
            import numpy as np

            control = np.ndarray((SHM_HEADER_FIELDS,), dtype=np.int64, buffer=shm.buf, offset=0)
            self._stage("shm_attached")
            writer: ShmFrameWriter | None = None
            # 与进程内模式一致：在未做 COM 初始化的线程里打开 DirectShow 设备，打开后再初始化 COM 读帧。
            if not self._open_capture():
                return
            self._stage("capture_opened")
            com_token = self._co_initialize()
            last_at = None
            window_max_gap = 0.0
            last_report = time.monotonic()
            while self.running:
                if int(control[SHM_STOP_FIELD]) != 0 or self._parent_exited():
                    break
                assert self.capture is not None
                ok, frame = self.capture.read()
                now = time.monotonic()
                if not ok or frame is None:
                    time.sleep(0.02)
                    continue
                if frame.ndim != 3 or frame.shape[2] != 3:
                    continue
                height, width = frame.shape[:2]
                if writer is None:
                    if shm.size < shm_size(width, height):
                        raise RuntimeError(f"shared memory too small for {width}x{height}")
                    writer = ShmFrameWriter(shm.buf, width, height)
                    self._stage(f"first_frame {width}x{height}")
                elif (width, height) != (writer.width, writer.height):
                    raise RuntimeError(f"frame size changed to {width}x{height}")
                gap_ms = (now - last_at) * 1000.0 if last_at is not None else 0.0
                last_at = now
                window_max_gap = max(window_max_gap, gap_ms)
                self.sequence = writer.write(frame, now, window_max_gap)
                self._record_fps(now)
                if gap_ms > GAP_REPORT_MS:
                    self._publish({"type": "gap", "ok": True, "opened": True, "gapMs": round(gap_ms, 1),
                                   "sequence": self.sequence, "monotonicMs": int(now * 1000)})
                if now - last_report >= 1.0:
                    self._publish({"type": "status", "ok": True, "opened": True, "backend": self.backend_label,
                                   "sequence": self.sequence, "fps": round(self.actual_fps, 1),
                                   "maxGapMs": round(window_max_gap, 1), "monotonicMs": int(now * 1000),
                                   "actualWidth": width, "actualHeight": height,
                                   "actualFps": self._safe_get("CAP_PROP_FPS") or self.fps, "message": "frame"})
                    last_report = now
                    window_max_gap = 0.0
        except BaseException as exc:  # noqa: BLE001 - keep startup failures visible to the parent.
            self._publish({"type": "status", "ok": False, "opened": False, "message": str(exc)})
        finally:
            self._release_capture()
            if shm is not None:
                try:
                    shm.close()
                except Exception:
                    pass
            self._co_uninitialize(com_token)


class _StdoutJsonQueue:
    def put_nowait(self, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def run_camera_shm_worker_stdio(startup: dict[str, Any]) -> None:
    # 停止请求经共享内存标志传入，stdin 不使用（父进程以 DEVNULL 启动）。
    command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    _ShmCaptureWorker(startup, _StdoutJsonQueue(), command_queue).run()


def _main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("camera shm worker requires one JSON startup argument")
    run_camera_shm_worker_stdio(json.loads(sys.argv[1]))


if __name__ == "__main__":
    _main()
