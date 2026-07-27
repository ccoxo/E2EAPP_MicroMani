from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService, default_session_id
from backend.hal_client.client import HalClient
from backend.services.command_service import CommandService
from backend.services.dataset_recorder import DatasetRecorderService
from backend.services.gripper_backend import NativeGripperAdapter
from backend.services.gripper_router import GripperRouter
from backend.services.hardware_service import HardwareService
from backend.services.policy_service import PolicyService
from backend.services.stability_monitor import StabilityMonitorService
from backend.services.telemetry_hub import TelemetryHub
from backend.services.teleop_mapping import TeleopMappingService


@dataclass
class AppServices:
    runtime_dir: Path
    startup_config: dict[str, Any]
    logs: LogService
    settings: SettingsService
    hardware: HardwareService
    telemetry: TelemetryHub
    hal: HalClient
    teleop_mapper: TeleopMappingService
    gripper_router: GripperRouter
    commands: CommandService
    recorder: DatasetRecorderService
    stability: StabilityMonitorService
    policy: PolicyService


def create_services(
    runtime_dir: Path,
    *,
    make_hal_client_fn: Callable[[dict[str, Any], LogService], HalClient],
) -> AppServices:
    session_id = default_session_id()
    logs = LogService(
        session_id=session_id,
        log_file_path=runtime_dir / "logs" / f"appstation-m0-{session_id}.log",
    )
    settings = SettingsService(runtime_dir, logs)
    startup_config = settings.get_config()
    hardware = HardwareService(settings, logs)
    telemetry = TelemetryHub(settings, hardware)
    hal = make_hal_client_fn(startup_config, logs)
    teleop_mapper = TeleopMappingService(settings, hal, logs)
    gripper_router = GripperRouter(native=NativeGripperAdapter(hal, teleop_mapper))
    commands = CommandService(
        settings,
        telemetry,
        hal,
        logs,
        hardware,
        teleop=teleop_mapper,
        gripper_router=gripper_router,
    )
    recorder = DatasetRecorderService(settings, hardware, hal, telemetry, logs, teleop_mapper)
    commands.set_origin_mutation_lock_checker(recorder.origin_mutation_locked)
    stability = StabilityMonitorService(settings, hardware, hal, logs)
    policy = PolicyService(settings, hal, logs)
    return AppServices(
        runtime_dir=runtime_dir,
        startup_config=startup_config,
        logs=logs,
        settings=settings,
        hardware=hardware,
        telemetry=telemetry,
        hal=hal,
        teleop_mapper=teleop_mapper,
        gripper_router=gripper_router,
        commands=commands,
        recorder=recorder,
        stability=stability,
        policy=policy,
    )
