"""录制时跳过已有长寿命对象的全代扫描，保留新对象的循环回收。"""

import gc


class RecordingGcScope:
    def __init__(self):
        self._owns_freeze = False

    def start(self):
        if not self._owns_freeze and gc.isenabled() and gc.get_freeze_count() == 0:
            gc.collect()
            gc.freeze()
            self._owns_freeze = True

    def close(self):
        if self._owns_freeze:
            gc.unfreeze()
            self._owns_freeze = False
