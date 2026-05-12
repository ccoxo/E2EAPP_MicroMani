from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConnectionState = Literal["ok", "warn", "error", "checking", "pending"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogChannel = Literal[
    "[HAL]",
    "[BACKEND]",
    "[CAMERA]",
    "[FORCE]",
    "[SAFETY]",
    "[ZMQ]",
    "[POLICY]",
    "[LEROBOT]",
    "[GRIPPER]",
]
SnapshotScope = Literal["all", "motion-left", "motion-right"]
ManualSide = Literal["left", "right"]
ManualAxis = Literal["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
ManualSpeedMode = Literal["fine", "medium", "coarse"]
ManualGripperCommand = Literal["enable", "disable", "open", "close", "home", "target", "stop"]


class ApiEnvelope(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    ts: int


class ErrorEnvelope(BaseModel):
    ok: Literal[False] = False
    code: str
    message: str


class LogEntry(BaseModel):
    id: int
    ts: int
    channel: LogChannel
    level: LogLevel
    msg: str


class ProcessStatus(BaseModel):
    name: Literal["hal", "backend", "policy", "recorder", "wsl"]
    label: str
    status: Literal["running", "not_running", "degraded", "error"]
    pid: int | None = None
    cpuPct: float
    memMb: float
    vramGb: float | None = None
    autoRestart: bool | None = None


class CameraTelemetry(BaseModel):
    key: Literal["global", "wrist_left", "wrist_right"]
    label: str
    fps: float
    timestampSkewMs: float
    frameAgeMs: float
    health: ConnectionState


class Omega7Telemetry(BaseModel):
    side: Literal["left", "right"]
    connected: bool
    calibrated: bool
    openId: int
    deviceId: int
    serial: str = ""
    systemName: str = ""
    leftHanded: bool | None = None
    pose: list[float] = Field(min_length=6, max_length=6)
    clutchPressed: bool = False
    gripperPressed: bool = False
    gripperGapMm: float | None = None
    lastReadOk: bool = False
    message: str = ""


class TelemetryFrame(BaseModel):
    timestamp: int
    elapsedSec: float
    jointPositions: list[float] = Field(min_length=12, max_length=12)
    gripperPositions: list[float] = Field(min_length=2, max_length=2)
    motionEnabled: dict[Literal["left", "right"], bool | None] = Field(
        default_factory=lambda: {"left": None, "right": None}
    )
    motionAxisEnabled: dict[Literal["left", "right"], list[bool | None]] = Field(
        default_factory=lambda: {"left": [None] * 6, "right": [None] * 6}
    )
    forceLeft: list[float] = Field(min_length=6, max_length=6)
    forceRight: list[float] = Field(min_length=6, max_length=6)
    dangerIndex: float
    recording: bool
    episodeCount: int
    frameCount: int
    halOk: bool
    wsOk: bool
    cameras: list[CameraTelemetry]
    teleopHands: list[Omega7Telemetry] = Field(min_length=2, max_length=2)
    queueDepth: dict[Literal["left", "right"], int]
    resource: dict[Literal["uiFps", "wsHz", "cpuPct", "memMb"], float]
    processStatus: list[ProcessStatus]


class AxisSoftLimitConfig(BaseModel):
    min: float
    max: float


class ArmSoftLimitConfig(BaseModel):
    x: AxisSoftLimitConfig
    y: AxisSoftLimitConfig
    z: AxisSoftLimitConfig
    roll: AxisSoftLimitConfig
    pitch: AxisSoftLimitConfig
    yaw: AxisSoftLimitConfig


class MotionAxisProfile(BaseModel):
    startSpeed: float
    maxSpeed: float
    accTimeSec: float
    decTimeSec: float


class ArmMotionProfile(BaseModel):
    translation: MotionAxisProfile
    rotation: MotionAxisProfile


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hal: dict[str, Any]
    cameras: dict[str, Any]
    force: dict[str, Any]
    motion: dict[str, Any]
    gripper: dict[str, Any]
    safety: dict[str, Any]
    zmq: dict[str, Any]
    storage: dict[str, Any]
    auto: dict[str, Any]
    picoVision: dict[str, Any]
    teleop: dict[str, Any]
    wsl: dict[str, Any]


class MotionCardSnapshotConfig(BaseModel):
    cardNo: int
    motionThreadHz: int
    yawSoftLimitDeg: float
    positionSource: Literal["dmc_get_position", "dmc_get_encoder"]
    profile: ArmMotionProfile
    softLimits: ArmSoftLimitConfig


class ParameterSnapshot(BaseModel):
    id: str
    name: str
    createdAt: int
    scope: SnapshotScope
    config: dict[str, Any] | MotionCardSnapshotConfig


class SnapshotCreateRequest(BaseModel):
    scope: SnapshotScope
    name: str
    config: dict[str, Any] | MotionCardSnapshotConfig | None = None


class ManualAxisMoveRequest(BaseModel):
    side: ManualSide
    axis: ManualAxis
    direction: Literal[-1, 1]
    step: float
    speedMode: ManualSpeedMode


class GripperCommandRequest(BaseModel):
    side: ManualSide
    command: ManualGripperCommand
    targetMm: float | None = None
    forceLimitN: float | None = None


class SettingsCommandRequest(BaseModel):
    channel: LogChannel = "[BACKEND]"
    msg: str
    level: LogLevel = "INFO"
    data: dict[str, Any] = Field(default_factory=dict)
