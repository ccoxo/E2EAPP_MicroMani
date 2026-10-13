"""把标准库日志器的输出处理器挪到后台线程。

uvicorn 访问日志在事件循环里同步写标准输出并 flush；输出被重定向到文件或隐藏控制台时，
一次慢写会卡住整个事件循环（2026-10-14 看门狗记录到 0.3～1 s 的停顿多数停在这里）。
"""
from __future__ import annotations

import logging
import logging.handlers
import queue
from dataclasses import dataclass

DEFAULT_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _PassThroughQueueHandler(logging.handlers.QueueHandler):
    """同进程内转交原始记录：不提前格式化，uvicorn 访问日志的格式化器需要原始 args。"""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


@dataclass
class QueuedLogger:
    logger: logging.Logger
    queue_handler: logging.Handler
    originals: list[logging.Handler]
    listener: logging.handlers.QueueListener


def install_queued_handlers(names: tuple[str, ...] = DEFAULT_LOGGER_NAMES) -> list[QueuedLogger]:
    """每个日志器各用一条队列和一个后台监听线程，保留原处理器、格式和级别。"""
    installed: list[QueuedLogger] = []
    for name in names:
        logger = logging.getLogger(name)
        originals = [h for h in logger.handlers if not isinstance(h, logging.handlers.QueueHandler)]
        if not originals:
            continue
        records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        listener = logging.handlers.QueueListener(records, *originals, respect_handler_level=True)
        queue_handler = _PassThroughQueueHandler(records)
        for handler in originals:
            logger.removeHandler(handler)
        logger.addHandler(queue_handler)
        listener.start()
        installed.append(QueuedLogger(logger, queue_handler, originals, listener))
    return installed


def stop_queued_handlers(installed: list[QueuedLogger]) -> None:
    """写完剩余记录后把原处理器装回去，同一进程再次启动时仍可重新接管。"""
    for item in installed:
        item.logger.removeHandler(item.queue_handler)
        item.listener.stop()
        for handler in item.originals:
            item.logger.addHandler(handler)
