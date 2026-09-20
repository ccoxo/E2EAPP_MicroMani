"""将 LeRobot 编码及图像统计隔离到独立进程，不改变视频和统计格式。"""

from __future__ import annotations

import multiprocessing as mp


class _WorkerError(RuntimeError):
    pass


def _worker(commands, replies, options):
    encoder = None
    original_statistics = None
    try:
        from lerobot.datasets.video_utils import StreamingVideoEncoder
        from lerobot.datasets import compute_stats
        from backend.services.video_statistics import FloatVideoStatistics

        # 仅替换独立编码进程内的统计实现，不修改安装目录和后端全局状态。
        original_statistics = compute_stats.RunningQuantileStats
        compute_stats.RunningQuantileStats = FloatVideoStatistics

        encoder = StreamingVideoEncoder(**options)
        replies.send({"ok": True})
        while True:
            command, args = commands.get()
            try:
                result = getattr(encoder, command)(*args)
                dropped = dict(encoder._dropped_frames)
                if command == "feed_frame" and any(dropped.values()):
                    raise RuntimeError(f"video encoder dropped frames: {dropped}")
                replies.send({"ok": True, "result": result, "drops": dropped,
                              "queues": {key: q.qsize() for key, q in encoder._frame_queues.items()}})
            except Exception as exc:
                replies.send({"ok": False, "error": str(exc)})
            if command == "close":
                break
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            replies.send({"ok": False, "error": str(exc)})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        if encoder is not None:
            encoder.close()
        if original_statistics is not None:
            compute_stats.RunningQuantileStats = original_statistics
        replies.close()
        commands.close()


class ProcessVideoEncoder:
    """与 DatasetWriter 使用的 StreamingVideoEncoder 接口保持一致。"""

    def __init__(self, **options):
        self._options = options
        context = mp.get_context("spawn")
        self._commands = context.Queue(maxsize=1)
        self._replies, child_reply = context.Pipe(duplex=False)
        self._process = context.Process(target=_worker, args=(self._commands, child_reply, options),
                                        name="recording-video-encoder", daemon=True)
        self._closed = False
        self._dropped_frames = {}
        self.queue_depths = {}
        try:
            self._process.start()
            child_reply.close()
            self._receive(30.0)
        except BaseException:
            child_reply.close()
            self._shutdown()
            raise

    def _receive(self, timeout):
        if not self._replies.poll(timeout):
            raise RuntimeError("video encoder process timed out")
        try:
            response = self._replies.recv()
        except EOFError as exc:
            raise RuntimeError("video encoder process exited") from exc
        if not response["ok"]:
            raise _WorkerError(response["error"])
        self._dropped_frames = response.get("drops", {})
        self.queue_depths = response.get("queues", {})
        return response.get("result")

    def _call(self, command, *args, timeout=5.0):
        if self._closed or not self._process.is_alive():
            raise RuntimeError("video encoder process is not running")
        self._commands.put((command, args), timeout=timeout)
        try:
            return self._receive(timeout)
        except _WorkerError:
            raise
        except BaseException:
            # 超时后不能让迟到的回复被下一条命令误收。
            self._shutdown()
            raise

    def start_episode(self, video_keys, temp_dir):
        if self._closed or not self._process.is_alive():
            self._shutdown()
            self.__init__(**self._options)
        self._call("start_episode", video_keys, temp_dir)

    def feed_frame(self, video_key, image):
        # 调用返回前子进程已读取数组，后续调用者修改不会污染编码队列。
        self._call("feed_frame", video_key, image)

    def finish_episode(self):
        return self._call("finish_episode", timeout=50.0)

    def cancel_episode(self):
        if not self._closed and self._process.is_alive():
            self._call("cancel_episode", timeout=20.0)

    def close(self):
        if self._closed:
            return
        try:
            if self._process.is_alive():
                self._call("close", timeout=20.0)
        finally:
            self._shutdown()

    def _shutdown(self):
        self._closed = True
        if self._process.pid is not None:
            self._process.join(timeout=0.2)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2.0)
        self._commands.cancel_join_thread()
        self._commands.close()
        self._replies.close()


def isolate_dataset_encoder(dataset):
    writer = getattr(dataset, "writer", None)
    encoder = getattr(writer, "_streaming_encoder", None)
    if encoder is None or isinstance(encoder, ProcessVideoEncoder):
        return
    replacement = ProcessVideoEncoder(**{key: getattr(encoder, key) for key in (
        "fps", "vcodec", "pix_fmt", "g", "crf", "preset", "queue_maxsize", "encoder_threads")})
    encoder.close()
    writer._streaming_encoder = replacement
