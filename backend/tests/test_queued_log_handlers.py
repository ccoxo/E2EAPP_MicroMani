import io
import logging
import threading
import time

from backend.core.logging import LogService
from backend.core.queued_log_handlers import install_queued_handlers, stop_queued_handlers


class _SlowStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.writer_threads: set[int] = set()

    def write(self, text: str) -> int:
        self.writer_threads.add(threading.get_ident())
        time.sleep(0.05)
        return super().write(text)


def test_queued_handlers_keep_access_formatting_and_write_off_caller_thread():
    logger = logging.getLogger("test.queued.access")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    stream = _SlowStream()
    handler = logging.StreamHandler(stream)
    # 与 uvicorn 访问日志一样，格式依赖原始 args。
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    installed = install_queued_handlers(("test.queued.access",))
    try:
        started = time.perf_counter()
        logger.info('%s - "%s %s"', "127.0.0.1:1", "GET", "/api/record/status")
        assert time.perf_counter() - started < 0.03
    finally:
        stop_queued_handlers(installed)
    assert stream.getvalue() == 'INFO 127.0.0.1:1 - "GET /api/record/status"\n'
    assert threading.get_ident() not in stream.writer_threads
    assert logger.handlers == [handler]
    logger.removeHandler(handler)


def test_log_service_file_writes_are_queued_and_flushable(tmp_path):
    log_file = tmp_path / "appstation-m0-test.log"
    logs = LogService(emit_startup=False, log_file_path=log_file)
    for index in range(200):
        logs.info("[BACKEND]", f"line {index}")
    assert logs.flush()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200
    assert lines[-1].endswith("[BACKEND] INFO line 199")
