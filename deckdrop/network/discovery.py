"""
mDNS peer discovery via zeroconf.

Announces this DeckDrop instance on the LAN and listens for others.
Service type: _deckdrop._tcp.local.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Callable
from typing import Any

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncZeroconf

from deckdrop.core.config import Config

log = logging.getLogger(__name__)

SERVICE_TYPE = "_deckdrop._tcp.local."
PROTOCOL_VERSION = "2"


def _pick_lan_address(addresses: list[str]) -> str | None:
    """Prefer non-loopback IPv4 for cross-device HTTP fetches on the LAN."""
    v4: list[str] = []
    v6: list[str] = []
    for raw in addresses:
        host = raw.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.is_loopback:
            continue
        if ip.version == 4:
            v4.append(host)
        else:
            v6.append(host)
    if v4:
        return v4[0]
    return v6[0] if v6 else None


class _Listener(ServiceListener):
    def __init__(
        self,
        own_peer_id: str,
        on_found: Callable[[str, str, str, int], None],
        on_lost: Callable[[str], None],
    ) -> None:
        self._own_peer_id = own_peer_id
        self._on_found = on_found
        self._on_lost = on_lost

    def _extract(self, zc: Zeroconf, name: str) -> dict[str, Any] | None:
        info = zc.get_service_info(SERVICE_TYPE, name)
        if not info:
            return None
        props = {k.decode(): v.decode() for k, v in info.properties.items()}
        peer_id = props.get("peer_id", "")
        if peer_id == self._own_peer_id:
            return None  # ignore ourselves
        address = _pick_lan_address(info.parsed_scoped_addresses())
        if not address:
            return None
        return {
            "peer_id": peer_id,
            "name": props.get("name", "unknown"),
            "address": address,
            "port": int(props.get("port", info.port)),
        }

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        data = self._extract(zc, name)
        if data:
            log.info("Peer found: %s @ %s:%s", data["name"], data["address"], data["port"])
            self._on_found(data["peer_id"], data["name"], data["address"], data["port"])

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        # Best-effort: extract peer_id from cached info before it's gone
        info = zc.get_service_info(SERVICE_TYPE, name)
        if info:
            props = {k.decode(): v.decode() for k, v in info.properties.items()}
            peer_id = props.get("peer_id", "")
            if peer_id and peer_id != self._own_peer_id:
                log.info("Peer lost: %s", peer_id)
                self._on_lost(peer_id)


class DiscoveryService:
    def __init__(self) -> None:
        self._zc: AsyncZeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._service_info: ServiceInfo | None = None
        self._ip_watch_task: asyncio.Task | None = None
        self._cfg: Config | None = None

    def _build_service_info(self, cfg: Config, lan_ip: str) -> ServiceInfo:
        hostname = socket.gethostname()
        service_name = f"deckdrop-{cfg.peer_id[:8]}.{SERVICE_TYPE}"
        return ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(lan_ip)],
            port=cfg.port,
            properties={
                "peer_id": cfg.peer_id,
                "name": cfg.user_name or hostname,
                "version": PROTOCOL_VERSION,
                "port": str(cfg.port),
            },
        )

    async def _register_service(self, cfg: Config) -> str:
        lan_ip = self._local_ip()
        self._service_info = self._build_service_info(cfg, lan_ip)
        if self._zc is None:
            self._zc = AsyncZeroconf()
        await self._zc.async_register_service(self._service_info)
        return lan_ip

    async def _watch_lan_ip(self) -> None:
        """Re-register mDNS when the LAN IP becomes available (systemd boot race)."""
        while self._zc and self._cfg:
            await asyncio.sleep(5)
            if not self._service_info:
                continue
            current = self._local_ip()
            announced = socket.inet_ntoa(self._service_info.addresses[0])
            if current == announced or current == "127.0.0.1":
                continue
            if announced != "127.0.0.1":
                continue
            try:
                await self._zc.async_unregister_service(self._service_info)
            except Exception:
                pass
            announced = await self._register_service(self._cfg)
            log.info("Discovery re-registered on LAN IP %s", announced)

    async def start(
        self,
        cfg: Config,
        on_peer_found: Callable[[str, str, str, int], None],
        on_peer_lost: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        lan_ip = await self._register_service(cfg)
        self._browser = ServiceBrowser(
            self._zc.zeroconf,
            SERVICE_TYPE,
            _Listener(cfg.peer_id, on_peer_found, on_peer_lost),
        )
        log.info(
            "Discovery started as deckdrop-%s.%s (%s)",
            cfg.peer_id[:8],
            SERVICE_TYPE,
            lan_ip,
        )
        if lan_ip == "127.0.0.1":
            log.warning("LAN IP not ready yet – retrying mDNS registration every 5s")
            self._ip_watch_task = asyncio.create_task(self._watch_lan_ip())

    async def stop(self) -> None:
        if self._ip_watch_task:
            self._ip_watch_task.cancel()
            try:
                await self._ip_watch_task
            except asyncio.CancelledError:
                pass
            self._ip_watch_task = None
        if self._zc and self._service_info:
            try:
                await self._zc.async_unregister_service(self._service_info)
            except Exception:
                pass
        if self._zc:
            await self._zc.async_close()
            self._zc = None
        self._service_info = None
        self._cfg = None
        log.info("Discovery stopped")

    @staticmethod
    def _local_ip() -> str:
        """Get the primary LAN IP (not 127.0.0.1)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.168.0.1", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
