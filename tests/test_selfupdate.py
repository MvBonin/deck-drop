"""DeckDrop self-update: Ed25519 (RFC 8032 vectors), signed manifest, GitHub
check, install with verification + backup, rollback, LAN offers, restart."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from deckdrop import __version__
from deckdrop.core import config as cfg_mod
from deckdrop.core import ed25519, release_key, selfupdate

# -- Ed25519 against RFC 8032 test vectors --

RFC_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


@pytest.mark.parametrize(("sk", "pk", "msg", "sig"), RFC_VECTORS)
def test_ed25519_rfc8032_vectors(sk, pk, msg, sig):
    sk_b, pk_b, msg_b, sig_b = map(bytes.fromhex, (sk, pk, msg, sig))
    assert ed25519.public_key(sk_b) == pk_b
    assert ed25519.sign(sk_b, msg_b) == sig_b
    assert ed25519.verify(pk_b, msg_b, sig_b)
    assert not ed25519.verify(pk_b, msg_b + b"x", sig_b)
    bad = bytearray(sig_b)
    bad[5] ^= 1
    assert not ed25519.verify(pk_b, msg_b, bytes(bad))
    assert not ed25519.verify(pk_b[:31], msg_b, sig_b)


def test_version_compare():
    assert selfupdate.is_newer("2.1.0", "2.0.0")
    assert selfupdate.is_newer("v2.0.1", "2.0")
    assert not selfupdate.is_newer("2.0.0", "2.0.0")
    assert not selfupdate.is_newer("1.9.9", "2.0.0")
    assert not selfupdate.is_newer("garbage", "2.0.0")


# -- fixtures --

SECRET = secrets.token_bytes(32)
PUBKEY = ed25519.public_key(SECRET).hex()
NEW = "9.9.9"
PAYLOAD = b"#!fake appimage " + os.urandom(2048)


def _manifest(version=NEW, payload=PAYLOAD, name=None) -> bytes:
    return json.dumps(
        {
            "app": "deckdrop",
            "version": version,
            "commit": "abc",
            "files": [
                {
                    "name": name or f"DeckDrop-{version}-x86_64.AppImage",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
    ).encode()


def _signed(**kw) -> tuple[bytes, str]:
    m = _manifest(**kw)
    return m, ed25519.sign(SECRET, m).hex()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(release_key, "PUBLIC_KEYS", [PUBKEY])
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "cfg" / "config.toml")
    app = tmp_path / "apps" / "DeckDrop-2.0.0-x86_64.AppImage"
    app.parent.mkdir()
    app.write_bytes(b"old appimage")
    os.chmod(app, 0o755)
    monkeypatch.setenv("APPIMAGE", str(app))
    selfupdate.reset_state()
    yield app
    selfupdate.reset_state()


# -- signed manifest --


def test_verify_manifest_accepts_valid_and_rejects_tampering():
    m, sig = _signed()
    assert selfupdate.verify_manifest(m, sig.encode())["version"] == NEW
    tampered = m.replace(b"9.9.9", b"9.9.8")
    with pytest.raises(selfupdate.UpdateError, match="Signatur"):
        selfupdate.verify_manifest(tampered, sig.encode())
    other = ed25519.sign(secrets.token_bytes(32), m).hex()
    with pytest.raises(selfupdate.UpdateError, match="Signatur"):
        selfupdate.verify_manifest(m, other.encode())
    with pytest.raises(selfupdate.UpdateError, match="Signaturschlüssel"):
        selfupdate.verify_manifest(m, sig.encode(), keys=[])


# -- GitHub check --


def _release(assets: dict[str, str], tag=f"v{NEW}") -> dict:
    return {
        "tag_name": tag,
        "body": "- Reparieren\n- Update-Neustart",
        "published_at": "2026-10-21T10:00:00Z",
        "html_url": "https://github.com/MvBonin/deck-drop/releases/tag/" + tag,
        "assets": [{"name": n, "browser_download_url": u} for n, u in assets.items()],
    }


@respx.mock
def test_check_github_newer_signed_release():
    m, sig = _signed()
    app_name = f"DeckDrop-{NEW}-x86_64.AppImage"
    respx.get(selfupdate.GITHUB_LATEST).mock(
        return_value=httpx.Response(
            200,
            json=_release(
                {
                    "release.json": "https://dl/release.json",
                    "release.json.sig": "https://dl/release.json.sig",
                    app_name: "https://dl/app",
                }
            ),
        )
    )
    respx.get("https://dl/release.json").mock(return_value=httpx.Response(200, content=m))
    respx.get("https://dl/release.json.sig").mock(return_value=httpx.Response(200, text=sig))

    r = selfupdate.check_github()
    assert r["newer"] and r["verified"] and r["error"] is None
    assert r["_source"]["url"] == "https://dl/app"
    assert "_source" not in selfupdate.public_github()


@respx.mock
def test_check_github_unsigned_release_is_not_installable():
    respx.get(selfupdate.GITHUB_LATEST).mock(
        return_value=httpx.Response(200, json=_release({"DeckDrop-9.9.9-x86_64.AppImage": "u"}))
    )
    r = selfupdate.check_github()
    assert r["newer"] and not r["verified"]
    assert "nicht signiert" in r["error"]


@respx.mock
def test_check_github_no_release_and_up_to_date():
    respx.get(selfupdate.GITHUB_LATEST).mock(return_value=httpx.Response(404))
    assert selfupdate.check_github()["newer"] is False
    respx.get(selfupdate.GITHUB_LATEST).mock(
        return_value=httpx.Response(200, json=_release({}, tag=f"v{__version__}"))
    )
    assert selfupdate.check_github()["newer"] is False


# -- install --


def _source(m, sig, url="https://dl/app"):
    return {
        "kind": "github",
        "url": url,
        "manifest": m.decode(),
        "signature": sig,
        "label": "GitHub",
    }


@respx.mock
def test_install_replaces_appimage_keeps_backup(env):
    m, sig = _signed()
    respx.get("https://dl/app").mock(return_value=httpx.Response(200, content=PAYLOAD))

    selfupdate.start_install(_source(m, sig), background=False)

    assert selfupdate._state.install["state"] == "ready", selfupdate._state.install
    assert env.read_bytes() == PAYLOAD
    assert os.access(env, os.X_OK)
    assert selfupdate.backup_path().read_bytes() == b"old appimage"
    assert (selfupdate.release_dir() / "release.json").read_bytes() == m
    assert not env.with_name(".DeckDrop-update.part").exists()


@respx.mock
def test_install_wrong_checksum_leaves_original_untouched(env):
    m, sig = _signed()
    respx.get("https://dl/app").mock(return_value=httpx.Response(200, content=b"evil" + PAYLOAD))

    selfupdate.start_install(_source(m, sig), background=False)

    assert selfupdate._state.install["state"] == "error"
    assert (
        "Prüfsumme" in selfupdate._state.install["error"]
        or "größer" in selfupdate._state.install["error"]
    )
    assert env.read_bytes() == b"old appimage"
    assert not selfupdate.backup_path().exists()


def test_install_refuses_bad_signature_downgrade_and_non_appimage(env, monkeypatch):
    m, sig = _signed()
    bad_sig = ed25519.sign(secrets.token_bytes(32), m).hex()
    with pytest.raises(selfupdate.UpdateError, match="Signatur"):
        selfupdate.start_install(_source(m, bad_sig), background=False)
    old_m, old_sig = _signed(version="1.0.0")
    with pytest.raises(selfupdate.UpdateError, match="nicht neuer"):
        selfupdate.start_install(_source(old_m, old_sig), background=False)
    monkeypatch.delenv("APPIMAGE")
    with pytest.raises(selfupdate.UpdateError, match="AppImage"):
        selfupdate.start_install(_source(m, sig), background=False)
    assert env.read_bytes() == b"old appimage"


@respx.mock
def test_rollback_swaps_back(env):
    m, sig = _signed()
    respx.get("https://dl/app").mock(return_value=httpx.Response(200, content=PAYLOAD))
    selfupdate.start_install(_source(m, sig), background=False)

    selfupdate.rollback()

    assert env.read_bytes() == b"old appimage"
    assert selfupdate.backup_path().read_bytes() == PAYLOAD
    assert not (selfupdate.release_dir() / "release.json").exists()


# -- LAN --


def _store_manifest(version, payload):
    m, sig = _signed(version=version, payload=payload)
    d = selfupdate.release_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "release.json").write_bytes(m)
    (d / "release.json.sig").write_text(sig)
    return m, sig


def test_local_offer_only_for_signed_matching_appimage(env):
    assert selfupdate.local_offer() is None  # nothing stored
    _store_manifest(__version__, b"old appimage")
    offer = selfupdate.local_offer()
    assert offer and offer["version"] == __version__
    env.write_bytes(b"locally rebuilt")  # file no longer matches the manifest
    assert selfupdate.local_offer() is None


def test_lan_offers_lists_newer_valid_peers_only():
    m, sig = _signed()
    good = SimpleNamespace(
        peer_id="pc",
        name="MVB Desktop",
        app_offer={"version": NEW, "manifest": m.decode(), "signature": sig},
    )
    forged = SimpleNamespace(
        peer_id="x",
        name="Evil",
        app_offer={"version": NEW, "manifest": m.decode().replace("abc", "abd"), "signature": sig},
    )
    old_m, old_sig = _signed(version="1.0.0")
    older = SimpleNamespace(
        peer_id="o",
        name="Old",
        app_offer={"version": "1.0.0", "manifest": old_m.decode(), "signature": old_sig},
    )
    none = SimpleNamespace(peer_id="n", name="None", app_offer=None)
    registry = SimpleNamespace(all=lambda: [good, forged, older, none])

    offers = selfupdate.lan_offers(registry)

    assert [o["peer_name"] for o in offers] == ["MVB Desktop"]
    assert offers[0]["version"] == NEW


def test_api_offer_install_from_peer(env, monkeypatch, isolated_config):
    """Peer A serves its signed AppImage via /offer + /package; peer B installs it."""
    from deckdrop.api import state as app_state
    from deckdrop.api.server import create_app
    from deckdrop.core.library import Library
    from deckdrop.network.peer_registry import PeerEntry, PeerRegistry

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", env.parent.parent / "cfg" / "config.toml")
    registry = PeerRegistry()
    app_state.init(isolated_config, Library(), registry)
    client = TestClient(create_app())
    assert client.get("/api/selfupdate/offer").status_code == 404

    # As peer A (newer version on disk, signed manifest stored):
    _store_manifest(__version__, b"old appimage")
    offer = client.get("/api/selfupdate/offer").json()
    package = client.get("/api/selfupdate/package").content
    assert package == b"old appimage"
    assert offer["version"] == __version__

    # As peer B: a peer offering a newer signed build.
    m, sig = _signed()
    registry._peers["pc"] = PeerEntry(
        "pc",
        "MVB Desktop",
        "192.168.1.5",
        7373,
        app_offer={"version": NEW, "manifest": m.decode(), "signature": sig},
    )
    status = client.get("/api/selfupdate").json()
    assert status["lan"][0]["peer_name"] == "MVB Desktop"
    with respx.mock:
        respx.get("http://192.168.1.5:7373/api/selfupdate/package").mock(
            return_value=httpx.Response(200, content=PAYLOAD)
        )
        r = client.post("/api/selfupdate/install", json={"source": "pc"})
        assert r.status_code == 202, r.text
        for _ in range(100):
            if selfupdate._state.install["state"] in ("ready", "error"):
                break
            import time

            time.sleep(0.02)
    assert selfupdate._state.install["state"] == "ready", selfupdate._state.install
    assert env.read_bytes() == PAYLOAD


def test_install_route_needs_checked_github_update(isolated_config):
    from deckdrop.api import state as app_state
    from deckdrop.api.server import create_app
    from deckdrop.core.library import Library

    app_state.init(isolated_config, Library())
    r = TestClient(create_app()).post("/api/selfupdate/install", json={"source": "github"})
    assert r.status_code == 409


def test_exec_restart_reexecs_appimage_with_flag(env, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "execve", lambda path, argv, e: calls.append((path, argv, e)))
    selfupdate.exec_restart(["deckdrop", "--kiosk"])
    path, argv, e = calls[0]
    assert path == str(env) and argv == [str(env), "--kiosk"]
    assert e["DECKDROP_RESTARTED"] == "1"


# -- release tooling --


def test_release_tool_manifest_sign_roundtrip(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "release_tool",
        os.path.join(os.path.dirname(__file__), "..", "packaging", "release_tool.py"),
    )
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)

    assert rt.check_version(f"v{__version__}") == 0
    assert rt.check_version("v0.0.1") == 1

    app = tmp_path / f"DeckDrop-{__version__}-x86_64.AppImage"
    app.write_bytes(PAYLOAD)
    out = tmp_path / "release.json"
    assert rt.build_manifest(str(out), [str(app)]) == 0
    assert rt.sign(str(out), key_hex=SECRET.hex()) == 0
    manifest = selfupdate.verify_manifest(
        out.read_bytes(), (tmp_path / "release.json.sig").read_bytes()
    )
    assert manifest["files"][0]["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    # A key not listed in PUBLIC_KEYS must not sign.
    assert rt.sign(str(out), key_hex=secrets.token_bytes(32).hex()) == 1
