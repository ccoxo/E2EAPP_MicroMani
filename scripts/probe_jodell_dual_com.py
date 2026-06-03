from __future__ import annotations

import argparse
import ctypes
import json
import math
import threading
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DLL = REPO_ROOT / "backend" / "vendor" / "jodell" / "jodellTool.dll"


def parse_port(value: str | int) -> int:
    if isinstance(value, int):
        return value
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        raise argparse.ArgumentTypeError(f"invalid COM port: {value!r}")
    return int(digits)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.nan


class ReadStats:
    def __init__(self, name: str) -> None:
        self.name = name
        self.samples_ms: list[float] = []
        self.raw_values: list[int] = []
        self.failures = 0
        self.notes: list[str] = []

    def add(self, elapsed_ms: float, ok: bool, raw: int | None = None, note: str | None = None) -> None:
        if ok:
            self.samples_ms.append(elapsed_ms)
            if raw is not None and raw >= 0:
                self.raw_values.append(raw)
        else:
            self.failures += 1
            if note and len(self.notes) < 8:
                self.notes.append(note)

    def summary(self) -> dict[str, Any]:
        attempts = len(self.samples_ms) + self.failures
        latency = {
            "min": min(self.samples_ms) if self.samples_ms else math.nan,
            "mean": mean(self.samples_ms),
            "p50": percentile(self.samples_ms, 50),
            "p95": percentile(self.samples_ms, 95),
            "max": max(self.samples_ms) if self.samples_ms else math.nan,
        }
        return {
            "name": self.name,
            "attempts": attempts,
            "ok": len(self.samples_ms),
            "failures": self.failures,
            "success_rate": len(self.samples_ms) / attempts if attempts else math.nan,
            "latency_ms": latency,
            "effective_hz": 1000.0 / latency["mean"] if self.samples_ms and latency["mean"] > 0 else math.nan,
            "raw_min": min(self.raw_values) if self.raw_values else None,
            "raw_max": max(self.raw_values) if self.raw_values else None,
            "raw_last": self.raw_values[-1] if self.raw_values else None,
            "notes": list(self.notes),
        }


def bind_symbol(
    dll: Any,
    plain: str,
    decorated: str | None,
    argtypes: list[Any],
    restype: Any = ctypes.c_int,
) -> Any:
    func = getattr(dll, plain, None)
    if func is None and decorated:
        func = getattr(dll, decorated, None)
    if func is None:
        raise RuntimeError(f"missing Jodell export: {plain}")
    func.argtypes = argtypes
    func.restype = restype
    return func


class JodellSdk:
    def __init__(self, dll_path: Path) -> None:
        if not dll_path.exists():
            raise FileNotFoundError(f"jodellTool.dll not found: {dll_path}")
        loader = ctypes.WinDLL if hasattr(ctypes, "WinDLL") else ctypes.CDLL
        self._dll = loader(str(dll_path))
        self.serial_operation = bind_symbol(
            self._dll,
            "serialOperation",
            "?serialOperation@@YAHHH_N@Z",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self.claw_enable = bind_symbol(
            self._dll,
            "clawEnable",
            "?clawEnable@@YAHH_N@Z",
            [ctypes.c_int, ctypes.c_int],
        )
        self.run_with_param = bind_symbol(
            self._dll,
            "runWithParam",
            "?runWithParam@@YAHHHHH@Z",
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self.get_location = bind_symbol(
            self._dll,
            "getClawCurrentLocation",
            "?getClawCurrentLocation@@YAHH@Z",
            [ctypes.c_int],
        )

    def open_port(self, port: int, baudrate: int) -> int:
        return int(self.serial_operation(port, baudrate, 1))

    def close_port(self, port: int, baudrate: int) -> int:
        return int(self.serial_operation(port, baudrate, 0))

    def enable(self, slave: int) -> int:
        return int(self.claw_enable(slave, 1))

    def read_raw(self, slave: int) -> int:
        return int(self.get_location(slave))

    def command_raw(self, slave: int, raw: int, speed: int, torque: int) -> int:
        return int(self.run_with_param(slave, raw, speed, torque))


def timed_read(
    sdk: JodellSdk,
    stats: ReadStats,
    slave: int,
    *,
    enable_on_negative: bool,
) -> int:
    started = time.perf_counter()
    raw = -1
    ok = False
    note = None
    try:
        raw = sdk.read_raw(slave)
        if raw < 0 and enable_on_negative:
            enable_ret = sdk.enable(slave)
            time.sleep(0.05)
            raw = sdk.read_raw(slave)
            if raw < 0:
                note = f"read slave={slave} failed after enable ret={enable_ret}, raw={raw}"
        ok = raw >= 0
        if not ok and note is None:
            note = f"read slave={slave} failed raw={raw}"
        return raw
    except Exception as exc:  # noqa: BLE001 - probe reports SDK failures.
        note = f"read slave={slave} exception: {exc}"
        return raw
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        stats.add(elapsed_ms, ok, raw, note)


def print_summary(summary: dict[str, Any]) -> None:
    latency = summary["latency_ms"]
    print(f"\n{summary['name']}")
    print("-" * len(summary["name"]))
    print(
        f"attempts={summary['attempts']} ok={summary['ok']} "
        f"failures={summary['failures']} success={summary['success_rate']:.3f}"
    )
    print(
        "latency_ms "
        f"min={latency['min']:.3f} mean={latency['mean']:.3f} "
        f"p50={latency['p50']:.3f} p95={latency['p95']:.3f} max={latency['max']:.3f}"
    )
    print(
        f"effective_hz={summary['effective_hz']:.2f} "
        f"raw_min={summary['raw_min']} raw_max={summary['raw_max']} raw_last={summary['raw_last']}"
    )
    for note in summary["notes"]:
        print(f"note: {note}")


def run_single_baseline(
    sdk: JodellSdk,
    *,
    name: str,
    port: int,
    slave: int,
    baudrate: int,
    iterations: int,
    warmup: int,
    enable_on_negative: bool,
) -> ReadStats:
    stats = ReadStats(name)
    open_ret = sdk.open_port(port, baudrate)
    if open_ret not in {0, 1}:
        stats.add(0.0, False, -1, f"open COM{port} failed ret={open_ret}")
        return stats
    try:
        warmup_stats = ReadStats(f"{name} warmup")
        for index in range(iterations + warmup):
            target = stats if index >= warmup else warmup_stats
            timed_read(sdk, target, slave, enable_on_negative=enable_on_negative)
    finally:
        sdk.close_port(port, baudrate)
    return stats


def run_dual_open(
    sdk: JodellSdk,
    *,
    left_port: int,
    right_port: int,
    left_slave: int,
    right_slave: int,
    baudrate: int,
    iterations: int,
    warmup: int,
    enable_on_negative: bool,
) -> tuple[ReadStats, ReadStats, dict[str, int]]:
    open_returns = {
        "left_open_ret": sdk.open_port(left_port, baudrate),
        "right_open_ret": sdk.open_port(right_port, baudrate),
    }
    left_stats = ReadStats(f"dual-open COM{left_port} slave={left_slave}")
    right_stats = ReadStats(f"dual-open COM{right_port} slave={right_slave}")
    if open_returns["left_open_ret"] not in {0, 1} or open_returns["right_open_ret"] not in {0, 1}:
        left_stats.add(0.0, False, -1, f"dual open returns {open_returns}")
        right_stats.add(0.0, False, -1, f"dual open returns {open_returns}")
        return left_stats, right_stats, open_returns
    try:
        warmup_left = ReadStats("dual-open left warmup")
        warmup_right = ReadStats("dual-open right warmup")
        for index in range(iterations + warmup):
            left_target = left_stats if index >= warmup else warmup_left
            right_target = right_stats if index >= warmup else warmup_right
            timed_read(sdk, left_target, left_slave, enable_on_negative=enable_on_negative)
            timed_read(sdk, right_target, right_slave, enable_on_negative=enable_on_negative)
    finally:
        sdk.close_port(left_port, baudrate)
        sdk.close_port(right_port, baudrate)
    return left_stats, right_stats, open_returns


def run_concurrent_read(
    sdk: JodellSdk,
    *,
    left_port: int,
    right_port: int,
    left_slave: int,
    right_slave: int,
    baudrate: int,
    iterations: int,
    enable_on_negative: bool,
) -> tuple[ReadStats, ReadStats, dict[str, int]]:
    open_returns = {
        "left_open_ret": sdk.open_port(left_port, baudrate),
        "right_open_ret": sdk.open_port(right_port, baudrate),
    }
    left_stats = ReadStats(f"concurrent COM{left_port} slave={left_slave}")
    right_stats = ReadStats(f"concurrent COM{right_port} slave={right_slave}")
    if open_returns["left_open_ret"] not in {0, 1} or open_returns["right_open_ret"] not in {0, 1}:
        left_stats.add(0.0, False, -1, f"dual open returns {open_returns}")
        right_stats.add(0.0, False, -1, f"dual open returns {open_returns}")
        return left_stats, right_stats, open_returns

    def worker(stats: ReadStats, slave: int) -> None:
        for _ in range(iterations):
            timed_read(sdk, stats, slave, enable_on_negative=enable_on_negative)

    try:
        left_thread = threading.Thread(target=worker, args=(left_stats, left_slave), name="jodell-left-read")
        right_thread = threading.Thread(target=worker, args=(right_stats, right_slave), name="jodell-right-read")
        left_thread.start()
        right_thread.start()
        left_thread.join()
        right_thread.join()
    finally:
        sdk.close_port(left_port, baudrate)
        sdk.close_port(right_port, baudrate)
    return left_stats, right_stats, open_returns


def run_unsafe_route_check(
    sdk: JodellSdk,
    *,
    left_port: int,
    right_port: int,
    left_slave: int,
    right_slave: int,
    baudrate: int,
    left_raw: int,
    right_raw: int,
    speed: int,
    torque: int,
    settle_s: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "left_open_ret": sdk.open_port(left_port, baudrate),
        "right_open_ret": sdk.open_port(right_port, baudrate),
    }
    try:
        result["before_left_raw"] = sdk.read_raw(left_slave)
        result["before_right_raw"] = sdk.read_raw(right_slave)
        result["left_command_ret"] = sdk.command_raw(left_slave, left_raw, speed, torque)
        time.sleep(settle_s)
        result["after_left_command_left_raw"] = sdk.read_raw(left_slave)
        result["after_left_command_right_raw"] = sdk.read_raw(right_slave)
        result["right_command_ret"] = sdk.command_raw(right_slave, right_raw, speed, torque)
        time.sleep(settle_s)
        result["after_right_command_left_raw"] = sdk.read_raw(left_slave)
        result["after_right_command_right_raw"] = sdk.read_raw(right_slave)
    finally:
        sdk.close_port(left_port, baudrate)
        sdk.close_port(right_port, baudrate)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe whether Jodell jodellTool.dll can keep COM8 and COM9 open in one process. "
            "Default tests are read-only and do not send runWithParam commands."
        )
    )
    parser.add_argument("--dll", default=str(DEFAULT_DLL), help="Path to jodellTool.dll")
    parser.add_argument("--left-port", default="COM8", help="Left gripper COM port")
    parser.add_argument("--right-port", default="COM9", help="Right gripper COM port")
    parser.add_argument("--left-slave", type=int, default=10)
    parser.add_argument("--right-slave", type=int, default=9)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--enable-on-negative", action="store_true", help="Call clawEnable only after a negative read")
    parser.add_argument("--run-concurrent-read", action="store_true", help="Also run two Python threads reading both slaves")
    parser.add_argument(
        "--unsafe-command-check",
        action="store_true",
        help="Send runWithParam test targets to verify command routing. This moves the grippers.",
    )
    parser.add_argument("--left-test-raw", type=int, default=60)
    parser.add_argument("--right-test-raw", type=int, default=180)
    parser.add_argument("--test-speed", type=int, default=30)
    parser.add_argument("--test-torque", type=int, default=1)
    parser.add_argument("--command-settle-s", type=float, default=0.8)
    parser.add_argument("--json", action="store_true", help="Print a JSON summary at the end")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dll_path = Path(args.dll).expanduser().resolve()
    left_port = parse_port(args.left_port)
    right_port = parse_port(args.right_port)
    iterations = max(1, int(args.iterations))
    warmup = max(0, int(args.warmup))

    print("Jodell dual-COM probe")
    print(f"dll={dll_path}")
    print(f"left=COM{left_port}/slave={args.left_slave}, right=COM{right_port}/slave={args.right_slave}")
    print("default mode is read-only; runWithParam is skipped unless --unsafe-command-check is set")

    sdk = JodellSdk(dll_path)
    summaries: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "dll": str(dll_path),
        "left_port": left_port,
        "right_port": right_port,
        "left_slave": args.left_slave,
        "right_slave": args.right_slave,
        "baudrate": args.baudrate,
    }

    for stats in [
        run_single_baseline(
            sdk,
            name=f"baseline COM{left_port} slave={args.left_slave}",
            port=left_port,
            slave=args.left_slave,
            baudrate=args.baudrate,
            iterations=iterations,
            warmup=warmup,
            enable_on_negative=args.enable_on_negative,
        ),
        run_single_baseline(
            sdk,
            name=f"baseline COM{right_port} slave={args.right_slave}",
            port=right_port,
            slave=args.right_slave,
            baudrate=args.baudrate,
            iterations=iterations,
            warmup=warmup,
            enable_on_negative=args.enable_on_negative,
        ),
    ]:
        summary = stats.summary()
        summaries.append(summary)
        print_summary(summary)

    left_stats, right_stats, open_returns = run_dual_open(
        sdk,
        left_port=left_port,
        right_port=right_port,
        left_slave=args.left_slave,
        right_slave=args.right_slave,
        baudrate=args.baudrate,
        iterations=iterations,
        warmup=warmup,
        enable_on_negative=args.enable_on_negative,
    )
    metadata["dual_open_returns"] = open_returns
    for stats in [left_stats, right_stats]:
        summary = stats.summary()
        summaries.append(summary)
        print_summary(summary)

    if args.run_concurrent_read:
        left_stats, right_stats, open_returns = run_concurrent_read(
            sdk,
            left_port=left_port,
            right_port=right_port,
            left_slave=args.left_slave,
            right_slave=args.right_slave,
            baudrate=args.baudrate,
            iterations=iterations,
            enable_on_negative=args.enable_on_negative,
        )
        metadata["concurrent_open_returns"] = open_returns
        for stats in [left_stats, right_stats]:
            summary = stats.summary()
            summaries.append(summary)
            print_summary(summary)

    if args.unsafe_command_check:
        print("\nUNSAFE command routing check is enabled; runWithParam will move grippers.")
        metadata["unsafe_route_check"] = run_unsafe_route_check(
            sdk,
            left_port=left_port,
            right_port=right_port,
            left_slave=args.left_slave,
            right_slave=args.right_slave,
            baudrate=args.baudrate,
            left_raw=max(0, min(255, args.left_test_raw)),
            right_raw=max(0, min(255, args.right_test_raw)),
            speed=max(1, min(255, args.test_speed)),
            torque=max(1, min(255, args.test_torque)),
            settle_s=max(0.0, args.command_settle_s),
        )
        print(json.dumps(metadata["unsafe_route_check"], ensure_ascii=False, indent=2))
    else:
        print("\nUnsafe command routing check skipped.")

    result = {"metadata": metadata, "summaries": summaries}
    if args.json:
        print("\nJSON_SUMMARY")
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
