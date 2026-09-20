"""真实 HAL DDS 控制服务 + 真实后端 DLL 的跨进程测试；不加载设备 SDK。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "hal/build/shm-tests"
sys.path.insert(0, str(REPO))
os.environ["APPSTATION_FASTDDS_BINDING_DLL"] = str(BUILD / "appstation_fastdds_transport.dll")

from backend.hal_client.dds_runtime import FastDdsHalTransport
from backend.hal_client.dds_types import (
    HalCommandRequest, now_unix_ms, TOPIC_HAL_HEALTH, TOPIC_HAL_MOTION_STATE,
    TOPIC_HAL_OMEGA_STATE, TOPIC_HAL_NATIVE_TELEOP_STATUS, TOPIC_HAL_FORCE_STATE,
)


class Server:
    def __init__(self, domain: int):
        self.process = subprocess.Popen(
            [str(BUILD / "ShmControlServerFixture.exe"), str(domain)], cwd=BUILD,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.lines = queue.Queue()
        self.output = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.output.append(line.rstrip())
            self.lines.put(line.rstrip())

    def expect(self, expected: str):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            try:
                if self.lines.get(timeout=0.1) == expected:
                    return
            except queue.Empty:
                if self.process.poll() is not None:
                    break
        raise AssertionError(f"expected {expected}, server output: {self.output}")

    def send(self, command: str):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def stop(self):
        if self.process.poll() is None:
            self.send("quit")
            assert self.process.wait(timeout=10) == 0, self.output

    def cleanup(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)


def request(transport, name, *, emergency=False, payload="{}"):
    sample = HalCommandRequest(uuid.uuid4().hex, now_unix_ms(), name, payload)
    publish = transport.publish_emergency_stop if emergency else transport.publish_command_request
    publish(sample)
    reply = transport.wait_for_command_reply(sample.request_id, 4)
    assert reply is not None and reply.request_id == sample.request_id, f"no reply for {name[:80]}"
    return reply


def await_telemetry(transport, after=0):
    topics = (TOPIC_HAL_HEALTH, TOPIC_HAL_MOTION_STATE, TOPIC_HAL_OMEGA_STATE,
              TOPIC_HAL_NATIVE_TELEOP_STATUS, TOPIC_HAL_FORCE_STATE)
    end = time.monotonic() + 8
    while time.monotonic() < end:
        samples = [transport.get_latest(topic) for topic in topics]
        if all(sample and sample.stamp_unix_ms > after for sample in samples):
            for sample in samples:
                assert isinstance(json.loads(sample.payload_json), dict)
            return
        time.sleep(0.02)
    raise AssertionError("telemetry missing or stale")


def main():
    # 与现场 domain 42 分离；每次进程选择独立测试域。
    domain = 160 + os.getpid() % 30
    servers = []
    transport = None
    try:
        server = Server(domain)
        servers.append(server)
        server.expect("READY")
        transport = FastDdsHalTransport(domain_id=domain)
        transport.start()
        await_telemetry(transport)
        assert request(transport, "force.state").ok

        # 大请求及大错误应答，覆盖 SHM 分片和 ctypes 动态扩容。
        name = "unknown_" + "x" * 262144
        reply = request(transport, name)
        assert not reply.ok and reply.error == "unknown HAL command: " + name

        # 非急停命令不能借急停 Topic 获得权限。
        reply = request(transport, "motion.enable_side", emergency=True)
        assert not reply.ok and "not allowed" in reply.error

        # 执行器与驱动状态锁都被占用，急停仍需收到应答并锁存。
        server.send("lock")
        server.expect("LOCKED")
        try:
            assert request(transport, "motion.emergency_stop", emergency=True).ok
        finally:
            server.send("unlock")
        server.expect("ESTOP_LATCHED")
        reply = request(transport, "motion.enable_side", payload='{"side":"left"}')
        assert not reply.ok
        print("PASS: SHM discovery, 5 telemetry topics, large request/reply, emergency preemption", flush=True)

        # 后端不退出，HAL 进程退出后重建；旧缓存不得被当成重连成功。
        server.stop()
        assert transport.wait_for_command_reply("missing-request", 0.05) is None
        restarted_at = now_unix_ms()
        server = Server(domain)
        servers.append(server)
        server.expect("READY")
        await_telemetry(transport, after=restarted_at)
        assert request(transport, "force.state").ok
        server.stop()
        print("PASS: independent HAL process restart and fresh telemetry; no hardware", flush=True)
    finally:
        # 先结束测试服务，确保异常时没有残留 DDS 进程。
        for server in servers:
            server.cleanup()
        if transport:
            transport.close()


if __name__ == "__main__":
    main()
