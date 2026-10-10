"""录制会话内申请 Windows 毫秒级定时，退出时成对释放。"""

import ctypes
import sys


class RecordingTimerScope:
    def __init__(self) -> None:
        self._winmm = None

    def start(self) -> None:
        if self._winmm is not None or sys.platform != "win32":
            return
        winmm = ctypes.WinDLL("winmm.dll")
        for name in ("timeBeginPeriod", "timeEndPeriod"):
            function = getattr(winmm, name)
            function.argtypes = [ctypes.c_uint]
            function.restype = ctypes.c_uint
        result = winmm.timeBeginPeriod(1)
        if result != 0:
            raise RuntimeError(f"Windows 录制定时精度申请失败: {result}")
        self._winmm = winmm

    def close(self) -> None:
        if self._winmm is not None:
            result = self._winmm.timeEndPeriod(1)
            if result != 0:
                raise RuntimeError(f"Windows 录制定时精度释放失败: {result}")
            self._winmm = None
