"""DiscoveryService: callback logic with mocked zeroconf."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from deckdrop.network.discovery import DiscoveryService, _Listener


def _make_service_info(peer_id: str, name: str, ip: str, port: int):
    info = MagicMock()
    info.properties = {
        b"peer_id": peer_id.encode(),
        b"name": name.encode(),
        b"version": b"2",
        b"port": str(port).encode(),
    }
    info.parsed_scoped_addresses.return_value = [ip]
    info.port = port
    return info


def test_listener_calls_on_found():
    found = []
    lost = []

    listener = _Listener("own-id", lambda *a: found.append(a), lambda p: lost.append(p))
    zc = MagicMock()
    zc.get_service_info.return_value = _make_service_info("p1", "Alice", "192.168.1.5", 7373)

    listener.add_service(zc, "_deckdrop._tcp.local.", "deckdrop-p1._deckdrop._tcp.local.")

    assert len(found) == 1
    assert found[0] == ("p1", "Alice", "192.168.1.5", 7373)
    assert lost == []


def test_listener_ignores_own_peer_id():
    found = []
    listener = _Listener("own-id", lambda *a: found.append(a), lambda p: None)
    zc = MagicMock()
    zc.get_service_info.return_value = _make_service_info("own-id", "Me", "127.0.0.1", 7373)

    listener.add_service(zc, "_deckdrop._tcp.local.", "deckdrop-own._deckdrop._tcp.local.")
    assert found == []


def test_listener_calls_on_lost():
    lost = []
    listener = _Listener("own-id", lambda *a: None, lambda p: lost.append(p))
    zc = MagicMock()
    zc.get_service_info.return_value = _make_service_info("p2", "Bob", "192.168.1.6", 7373)

    listener.remove_service(zc, "_deckdrop._tcp.local.", "deckdrop-p2._deckdrop._tcp.local.")
    assert "p2" in lost


def test_listener_handles_missing_service_info_on_remove():
    lost = []
    listener = _Listener("own-id", lambda *a: None, lambda p: lost.append(p))
    zc = MagicMock()
    zc.get_service_info.return_value = None

    # Should not raise even if info is gone
    listener.remove_service(zc, "_deckdrop._tcp.local.", "deckdrop-gone._deckdrop._tcp.local.")
    assert lost == []


def test_discovery_service_start_stop():
    svc = DiscoveryService()
    with (
        patch("deckdrop.network.discovery.Zeroconf") as MockZC,
        patch("deckdrop.network.discovery.ServiceBrowser"),
        patch.object(DiscoveryService, "_local_ip", return_value="192.168.1.1"),
    ):
        mock_zc = MagicMock()
        MockZC.return_value = mock_zc

        import copy

        from deckdrop.core import config as cfg_mod
        from deckdrop.core.config import Config

        data = copy.deepcopy(cfg_mod._DEFAULTS)
        data["user"]["peer_id"] = "test-uuid"
        data["user"]["name"] = "TestUser"
        cfg = Config(data)

        svc.start(cfg, lambda *a: None, lambda p: None)
        mock_zc.register_service.assert_called_once()

        svc.stop()
        mock_zc.unregister_service.assert_called_once()
        mock_zc.close.assert_called_once()
