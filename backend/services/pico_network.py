from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass
from importlib import import_module
from ipaddress import IPv4Address, IPv4Network
from typing import Any


class PicoNetworkDetectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class IPv4Adapter:
    name: str
    if_index: int
    metric: int
    local_ip: str
    prefix_length: int


@dataclass(frozen=True)
class IPv4Route:
    destination: str
    prefix_length: int
    gateway: str
    interface_ip: str
    metric: int

    @property
    def network(self) -> IPv4Network:
        return IPv4Network(f"{self.destination}/{self.prefix_length}", strict=False)


def _common_prefix_length(first: IPv4Address, second: IPv4Address) -> int:
    difference = int(first) ^ int(second)
    return 32 if difference == 0 else 32 - difference.bit_length()


def _is_virtual_adapter(name: str) -> bool:
    normalized = name.casefold()
    return any(
        marker in normalized
        for marker in ("virtual", "vethernet", "hyper-v", "wsl", "vmware", "vpn", "loopback", "tunnel", "mihomo")
    )


def _best_route(routes: list[IPv4Route], target: IPv4Address) -> IPv4Route | None:
    matching = [route for route in routes if target in route.network]
    if not matching:
        return None
    return max(matching, key=lambda route: (route.prefix_length, -route.metric))


def _gateway_for_adapter(
    adapter: IPv4Adapter,
    routes: list[IPv4Route],
    target: IPv4Address,
    preferred_gateway: str,
) -> str:
    candidates = [
        route
        for route in routes
        if route.interface_ip == adapter.local_ip and route.gateway not in {"", "0.0.0.0"}
    ]
    if preferred_gateway and any(route.gateway == preferred_gateway for route in candidates):
        return preferred_gateway
    matching = [route for route in candidates if target in route.network]
    if matching:
        return max(matching, key=lambda route: (route.prefix_length, -route.metric)).gateway
    defaults = [route for route in candidates if route.prefix_length == 0]
    if defaults:
        return min(defaults, key=lambda route: route.metric).gateway
    return min(candidates, key=lambda route: route.metric).gateway if candidates else ""


def select_pico_network(
    pico_ip: str,
    adapters: list[IPv4Adapter],
    routes: list[IPv4Route],
    *,
    preferred_gateway: str = "",
) -> dict[str, Any]:
    try:
        target = IPv4Address(pico_ip)
    except ValueError as exc:
        raise PicoNetworkDetectionError(f"invalid PICO IPv4 address: {pico_ip}") from exc
    if not adapters:
        raise PicoNetworkDetectionError("no active IPv4 network adapter was found")

    effective_route = _best_route(routes, target)

    def score(adapter: IPv4Adapter) -> tuple[int, int, int, int, int, int, int]:
        local_ip = IPv4Address(adapter.local_ip)
        network = IPv4Network(f"{adapter.local_ip}/{adapter.prefix_length}", strict=False)
        shared_prefix = _common_prefix_length(target, local_ip)
        related = shared_prefix >= 16
        preferred = bool(
            preferred_gateway
            and any(
                route.interface_ip == adapter.local_ip and route.gateway == preferred_gateway
                for route in routes
            )
        )
        system_route = effective_route is not None and effective_route.interface_ip == adapter.local_ip
        return (
            int(target in network),
            int(related),
            int(preferred),
            shared_prefix if related else 0,
            int(system_route),
            int(not _is_virtual_adapter(adapter.name)),
            -adapter.metric,
        )

    selected = max(adapters, key=score)
    selected_ip = IPv4Address(selected.local_ip)
    selected_network = IPv4Network(f"{selected.local_ip}/{selected.prefix_length}", strict=False)
    shared_prefix = _common_prefix_length(target, selected_ip)
    if target in selected_network:
        selection = "direct-subnet"
    elif shared_prefix >= 16:
        selection = "related-address"
    elif effective_route is not None and effective_route.interface_ip == selected.local_ip:
        selection = "system-route"
    else:
        selection = "active-interface"
    return {
        "ifIndex": selected.if_index,
        "gateway": _gateway_for_adapter(selected, routes, target, preferred_gateway),
        "localIp": selected.local_ip,
        "interfaceAlias": selected.name,
        "prefixLength": selected.prefix_length,
        "selection": selection,
    }


def _run_command(args: list[str]) -> str:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        args,
        capture_output=True,
        timeout=8,
        check=False,
        creationflags=creation_flags,
    )
    encoding = "mbcs" if os.name == "nt" else "utf-8"
    try:
        stdout = result.stdout.decode("utf-8")
        stderr = result.stderr.decode("utf-8")
    except UnicodeDecodeError:
        stdout = result.stdout.decode(encoding, errors="replace")
        stderr = result.stderr.decode(encoding, errors="replace")
    if result.returncode != 0:
        message = stderr.strip() or stdout.strip() or f"exit code {result.returncode}"
        raise PicoNetworkDetectionError(f"{' '.join(args[:2])} failed: {message}")
    return stdout


def _parse_interfaces(output: str) -> dict[str, tuple[int, int]]:
    interfaces: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) != 5 or not all(part.isdigit() for part in parts[:3]):
            continue
        interfaces[parts[4].casefold()] = (int(parts[0]), int(parts[1]))
    return interfaces


def _parse_routes(output: str) -> list[IPv4Route]:
    routes: list[IPv4Route] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            destination = IPv4Address(parts[0])
            mask = IPv4Address(parts[1])
            metric = int(parts[-1])
        except ValueError:
            continue
        middle_addresses: list[str] = []
        for value in parts[2:-1]:
            try:
                middle_addresses.append(str(IPv4Address(value)))
            except ValueError:
                continue
        if not middle_addresses:
            continue
        interface_ip = middle_addresses[-1]
        gateway = middle_addresses[-2] if len(middle_addresses) >= 2 else ""
        try:
            prefix_length = IPv4Network(f"0.0.0.0/{mask}").prefixlen
        except ValueError:
            continue
        routes.append(
            IPv4Route(str(destination), prefix_length, gateway, interface_ip, metric)
        )
    return routes


def _active_adapters(interface_rows: dict[str, tuple[int, int]]) -> list[IPv4Adapter]:
    psutil = import_module("psutil")
    addresses_by_name = psutil.net_if_addrs()
    stats_by_name = psutil.net_if_stats()
    adapters: list[IPv4Adapter] = []
    for name, addresses in addresses_by_name.items():
        stats = stats_by_name.get(name)
        interface = interface_rows.get(name.casefold())
        if stats is None or not stats.isup or interface is None:
            continue
        if_index, metric = interface
        for address in addresses:
            if address.family != socket.AF_INET or not address.netmask:
                continue
            local_ip = IPv4Address(address.address)
            if local_ip.is_loopback or local_ip.is_link_local:
                continue
            try:
                prefix_length = IPv4Network(f"0.0.0.0/{address.netmask}").prefixlen
            except ValueError:
                continue
            adapters.append(IPv4Adapter(name, if_index, metric, str(local_ip), prefix_length))
    return adapters


def detect_pico_network(pico_ip: str, *, preferred_gateway: str = "") -> dict[str, Any]:
    if os.name != "nt":
        raise PicoNetworkDetectionError("automatic PICO network detection currently requires Windows")
    interfaces = _parse_interfaces(_run_command(["netsh", "interface", "ipv4", "show", "interfaces"]))
    routes = _parse_routes(_run_command(["route", "print", "-4"]))
    return select_pico_network(
        pico_ip,
        _active_adapters(interfaces),
        routes,
        preferred_gateway=preferred_gateway,
    )
