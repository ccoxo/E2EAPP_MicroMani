# 阅读导航 03｜后端契约与配置
# 职责：按依赖顺序创建配置、硬件、遥测、HAL、录制和策略服务，并集中返回 AppServices。
# 先看：AppServices → create_services。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService, default_session_id
from backend.core.motion_safety import MotionSafetyGate
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
    # 先创建配置与驱动聚合，再创建使用它们的服务；依赖关系沿下方构造参数即可追踪。
    telemetry = TelemetryHub(settings, hardware)
    hal = make_hal_client_fn(startup_config, logs)
    safety = MotionSafetyGate()
    teleop_mapper = TeleopMappingService(settings, hal, logs, safety=safety)
    gripper_router = GripperRouter(native=NativeGripperAdapter(hal, teleop_mapper))
    commands = CommandService(
        settings,
        telemetry,
        hal,
        logs,
        hardware,
        teleop=teleop_mapper,
        gripper_router=gripper_router,
        safety=safety,
    )
    recorder = DatasetRecorderService(settings, hardware, hal, telemetry, logs, teleop_mapper, safety=safety)
    safety.on_emergency = recorder.interrupt_for_safety
    recorder.validate_start_origin = lambda: commands.validate_record_origin(recorder._reset_required_sides_locked())
    # 把录制器的原点锁定条件接入命令服务，避免录制过程中更换坐标基准。
    commands.set_origin_mutation_lock_checker(recorder.origin_mutation_locked)
    stability = StabilityMonitorService(settings, hardware, hal, logs)
    policy = PolicyService(settings, hal, logs, safety=safety)
    policy.validate_hardware_action = commands.validate_policy_axis_action
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
