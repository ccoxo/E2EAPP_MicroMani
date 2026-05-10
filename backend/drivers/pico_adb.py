from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PicoResult:
    ok: bool
    message: str
    stdout: str = ""
    stderr: str = ""


class PicoAdbDriver:
    def status(self, config: dict[str, Any]) -> PicoResult:
        script = self._script(config, "check_pico4ultra_wireless_status.bat")
        if script is not None:
            endpoint = self._endpoint(config)
            return self._run([str(script), endpoint], config)
        return self._run(["adb", "devices"], config)

    def connect(self, config: dict[str, Any]) -> PicoResult:
        script = self._script(config, "connect_pico4ultra_wireless_adb.bat")
        pico = config["picoVision"]
        if script is not None:
            return self._run([str(script), str(pico["ip"]), str(pico["adbPort"])], config)
        return self._run(["adb", "connect", f"{pico['ip']}:{pico['adbPort']}"], config)

    def start_vision(self, config: dict[str, Any]) -> PicoResult:
        script = self._script(config, "run_pico4ultra_wireless_lan_strict.bat")
        if script is not None:
            return self._run([str(script), self._endpoint(config)], config)
        pico = config["picoVision"]
        message = (
            f"ADB path ready check required before starting sender {pico['ip']}:{pico['videoPort']}; "
            "sender script not configured"
        )
        return PicoResult(
            False,
            message,
        )

    def stop_vision(self, config: dict[str, Any]) -> PicoResult:
        script = self._script(config, "stop_pico4ultra_wireless_lan.bat")
        if script is not None:
            return self._run([str(script)], config)
        return PicoResult(False, "PICO sender stop script not configured")

    def _run(self, args: list[str], config: dict[str, Any]) -> PicoResult:
        timeout = max(5.0, float(config["hal"].get("timeoutMs", 500)) / 1000.0)
        env = os.environ.copy()
        pico = config["picoVision"]
        env.update(
            {
                "PICO_GATEWAY": str(pico.get("gateway", "")),
                "PICO_IF_INDEX": str(pico.get("ifIndex", "")),
                "PICO_ADB_PORT": str(pico.get("adbPort", "")),
                "PICO_ADB_ENDPOINT": self._endpoint(config),
                "CODEX_VIDEO_ROTATION": str(pico.get("rotation", "ccw90")),
            }
        )
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=env,
            )
        except FileNotFoundError:
            return PicoResult(False, f"executable not found: {args[0]}")
        except subprocess.TimeoutExpired:
            return PicoResult(False, f"adb command timed out: {' '.join(args)}")
        return PicoResult(result.returncode == 0, " ".join(args), result.stdout, result.stderr)

    def _endpoint(self, config: dict[str, Any]) -> str:
        pico = config["picoVision"]
        return f"{pico['ip']}:{pico['adbPort']}"

    def _script(self, config: dict[str, Any], name: str) -> Path | None:
        pico = config["picoVision"]
        candidates = []
        for key in ("scriptsDir", "senderBuildDir"):
            raw = str(pico.get(key, "")).strip()
            if raw:
                candidates.append(Path(raw) / name)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
