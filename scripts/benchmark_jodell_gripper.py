from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.defaults import default_config
from backend.drivers.gripper_rs485 import Rs485GripperDriver


@dataclass
class BenchResult:
    name: str
    samples_ms: list[float] = field(default_factory=list)
    failures: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, elapsed_ms: float, ok: bool, note: str | None = None) -> None:
        if ok:
            self.samples_ms.append(elapsed_ms)
        else:
            self.failures += 1
        if note and len(self.notes) < 5:
            self.notes.append(note)


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


def parse_port(driver: Rs485GripperDriver, value: str) -> int:
    return driver._port_number(value)  # Benchmark intentionally follows production parsing.


def timed(result: BenchResult, action: Callable[[], bool], note: str | None = None) -> bool:
    start = time.perf_counter()
    ok = False
    error_note = note
    try:
        ok = action()
        return ok
    except Exception as exc:  # noqa: BLE001 - benchmark should keep running and report failures.
        error_note = f"{note or result.name}: {exc}"
        return False
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        result.add(elapsed_ms, ok, error_note if not ok else None)


def read_location(dll: Any, slave: int, enable_on_negative: bool) -> tuple[bool, int]:
    raw = int(dll.getClawCurrentLocation(slave))
    if raw >= 0 or not enable_on_negative:
        return raw >= 0, raw
    enable_ret = int(dll.clawEnable(slave, True))
    time.sleep(0.05)
    raw = int(dll.getClawCurrentLocation(slave))
    return raw >= 0, raw if raw >= 0 else enable_ret


def print_result(result: BenchResult, unit: str = "op") -> None:
    values = result.samples_ms
    total = len(values) + result.failures
    avg = mean(values)
    hz = 1000.0 / avg if values and avg > 0 else math.nan
    print(f"\n{result.name}")
    print("-" * len(result.name))
    print(f"attempts: {total}, ok: {len(values)}, failures: {result.failures}")
    if values:
        print(
            "latency ms: "
            f"min={min(values):.3f}, mean={avg:.3f}, "
            f"p50={percentile(values, 50):.3f}, p95={percentile(values, 95):.3f}, "
            f"max={max(values):.3f}"
        )
        print(f"effective rate: {hz:.2f} {unit}/s")
    for note in result.notes:
        print(f"note: {note}")


def select_port(driver: Rs485GripperDriver, dll: Any, port: int, baudrate: int) -> bool:
    ret = driver._select_port(dll, port, baudrate)
    return int(ret) in {0, 1}


def close_active(driver: Rs485GripperDriver, dll: Any) -> None:
    if driver._active_port is not None and driver._active_baudrate is not None:
        driver._close_port(dll, driver._active_port, driver._active_baudrate)


def bench_single_held(
    driver: Rs485GripperDriver,
    dll: Any,
    *,
    name: str,
    port: int,
    slave: int,
    baudrate: int,
    iterations: int,
    warmup: int,
    enable_on_negative: bool,
) -> BenchResult:
    result = BenchResult(name)
    if not select_port(driver, dll, port, baudrate):
        result.add(0.0, False, f"open COM{port} failed")
        return result
    try:
        for idx in range(iterations + warmup):
            target = result if idx >= warmup else BenchResult("warmup")
            timed(
                target,
                lambda: read_location(dll, slave, enable_on_negative)[0],
                f"read COM{port} slave={slave}",
            )
    finally:
        close_active(driver, dll)
    return result


def bench_same_bus(
    driver: Rs485GripperDriver,
    dll: Any,
    *,
    port: int,
    left_slave: int,
    right_slave: int,
    baudrate: int,
    iterations: int,
    warmup: int,
    enable_on_negative: bool,
) -> BenchResult:
    result = BenchResult(f"same COM{port}: read left+right")
    if not select_port(driver, dll, port, baudrate):
        result.add(0.0, False, f"open COM{port} failed")
        return result
    try:
        for idx in range(iterations + warmup):
            target = result if idx >= warmup else BenchResult("warmup")

            def action() -> bool:
                left_ok, _ = read_location(dll, left_slave, enable_on_negative)
                right_ok, _ = read_location(dll, right_slave, enable_on_negative)
                return left_ok and right_ok

            timed(target, action, f"read COM{port} slaves={left_slave},{right_slave}")
    finally:
        close_active(driver, dll)
    return result


def bench_alternating_ports(
    driver: Rs485GripperDriver,
    dll: Any,
    *,
    left_port: int,
    right_port: int,
    left_slave: int,
    right_slave: int,
    baudrate: int,
    iterations: int,
    warmup: int,
    enable_on_negative: bool,
) -> BenchResult:
    result = BenchResult(f"alternate COM{left_port}/COM{right_port}: read left+right")
    for idx in range(iterations + warmup):
        target = result if idx >= warmup else BenchResult("warmup")

        def action() -> bool:
            if not select_port(driver, dll, left_port, baudrate):
                return False
            left_ok, _ = read_location(dll, left_slave, enable_on_negative)
            if not select_port(driver, dll, right_port, baudrate):
                return False
            right_ok, _ = read_location(dll, right_slave, enable_on_negative)
            return left_ok and right_ok

        timed(
            target,
            action,
            f"read COM{left_port} slave={left_slave}, COM{right_port} slave={right_slave}",
        )
    close_active(driver, dll)
    return result


def bench_port_switch_only(
    driver: Rs485GripperDriver,
    dll: Any,
    *,
    left_port: int,
    right_port: int,
    baudrate: int,
    iterations: int,
    warmup: int,
) -> BenchResult:
    result = BenchResult(f"switch only COM{left_port}<->COM{right_port}")
    for idx in range(iterations + warmup):
        target = result if idx >= warmup else BenchResult("warmup")

        def action() -> bool:
            return (
                select_port(driver, dll, left_port, baudrate)
                and select_port(driver, dll, right_port, baudrate)
            )

        timed(target, action, f"switch COM{left_port}<->COM{right_port}")
    close_active(driver, dll)
    return result


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    config = default_config()
    config["gripper"]["leftPort"] = args.left_port
    config["gripper"]["rightPort"] = args.right_port
    config["gripper"]["leftSlaveId"] = args.left_slave
    config["gripper"]["rightSlaveId"] = args.right_slave
    config["gripper"]["baudrate"] = args.baudrate
    if args.dll:
        config["gripper"]["jodellDllPath"] = str(Path(args.dll).expanduser())
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark Jodell EPG006 serial read latency. The script only reads "
            "getClawCurrentLocation by default; it does not send motion commands."
        )
    )
    parser.add_argument("--dll", default=None, help="Path to jodellTool.dll")
    parser.add_argument("--left-port", default="COM8")
    parser.add_argument("--right-port", default="COM9")
    parser.add_argument("--same-port", default=None, help="Benchmark both slaves on one shared COM port")
    parser.add_argument("--left-slave", type=int, default=10)
    parser.add_argument("--right-slave", type=int, default=9)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument(
        "--enable-on-negative",
        action="store_true",
        help="If a position read returns <0, call clawEnable(slave, true), wait 50ms, and retry.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    driver = Rs485GripperDriver()
    config = build_config(args)
    dll = driver._load_dll(config)

    left_port = parse_port(driver, args.left_port)
    right_port = parse_port(driver, args.right_port)
    same_port = parse_port(driver, args.same_port) if args.same_port else None

    print("Jodell gripper benchmark")
    print(f"dll: {driver._dll_path}")
    print(f"baudrate: {args.baudrate}")
    print(f"left: COM{left_port} slave={args.left_slave}")
    print(f"right: COM{right_port} slave={args.right_slave}")
    print(f"iterations: {args.iterations}, warmup: {args.warmup}")
    print(f"enable_on_negative: {args.enable_on_negative}")

    results = [
        bench_single_held(
            driver,
            dll,
            name=f"held COM{left_port}: read left",
            port=left_port,
            slave=args.left_slave,
            baudrate=args.baudrate,
            iterations=args.iterations,
            warmup=args.warmup,
            enable_on_negative=args.enable_on_negative,
        ),
        bench_single_held(
            driver,
            dll,
            name=f"held COM{right_port}: read right",
            port=right_port,
            slave=args.right_slave,
            baudrate=args.baudrate,
            iterations=args.iterations,
            warmup=args.warmup,
            enable_on_negative=args.enable_on_negative,
        ),
    ]

    if left_port != right_port:
        results.append(
            bench_port_switch_only(
                driver,
                dll,
                left_port=left_port,
                right_port=right_port,
                baudrate=args.baudrate,
                iterations=args.iterations,
                warmup=args.warmup,
            )
        )
        results.append(
            bench_alternating_ports(
                driver,
                dll,
                left_port=left_port,
                right_port=right_port,
                left_slave=args.left_slave,
                right_slave=args.right_slave,
                baudrate=args.baudrate,
                iterations=args.iterations,
                warmup=args.warmup,
                enable_on_negative=args.enable_on_negative,
            )
        )

    if same_port is not None:
        results.append(
            bench_same_bus(
                driver,
                dll,
                port=same_port,
                left_slave=args.left_slave,
                right_slave=args.right_slave,
                baudrate=args.baudrate,
                iterations=args.iterations,
                warmup=args.warmup,
                enable_on_negative=args.enable_on_negative,
            )
        )

    for result in results:
        print_result(result, unit="cycles" if "left+right" in result.name else "ops")

    print("\n30fps checks")
    print("------------")
    print("Per-side 30fps budget: one left+right cycle <= 33.333 ms.")
    print("Combined 30fps budget: one left+right cycle <= 66.667 ms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
