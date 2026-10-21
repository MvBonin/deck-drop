#!/usr/bin/env python3
"""DeckDrop release tooling (used by .github/workflows/release.yml).

generate-key OUT        one-time: write a new Ed25519 private key (hex) to
                        OUT (keep it secret → GitHub secret
                        DECKDROP_SIGNING_KEY) and print the public key for
                        deckdrop/core/release_key.py
check-version TAG       fail unless TAG (e.g. v2.1.0) == deckdrop.__version__
manifest OUT FILE...    write release.json (version, commit, sha256/size)
sign MANIFEST           sign with $DECKDROP_SIGNING_KEY → MANIFEST.sig (hex)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deckdrop import __version__  # noqa: E402
from deckdrop.core import ed25519  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def generate_key(out: str) -> int:
    path = Path(out)
    if path.exists():
        print(f"{path} existiert schon – nichts überschrieben.", file=sys.stderr)
        return 1
    secret = secrets.token_bytes(32)
    path.write_text(secret.hex() + "\n", encoding="ascii")
    os.chmod(path, 0o600)
    print(
        "Privater Schlüssel:", path, "(→ GitHub-Secret DECKDROP_SIGNING_KEY, sonst geheim halten)"
    )
    print("Öffentlicher Schlüssel (in deckdrop/core/release_key.py eintragen):")
    print(ed25519.public_key(secret).hex())
    return 0


def check_version(tag: str) -> int:
    if tag.lstrip("v") != __version__:
        print(f"Tag {tag} passt nicht zu deckdrop.__version__ = {__version__}", file=sys.stderr)
        return 1
    print(f"OK: {tag} == {__version__}")
    return 0


def build_manifest(out: str, files: list[str]) -> int:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        commit = os.getenv("GITHUB_SHA", "")
    manifest = {
        "app": "deckdrop",
        "version": __version__,
        "commit": commit,
        "published_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "files": [
            {"name": Path(f).name, "size": Path(f).stat().st_size, "sha256": _sha256(Path(f))}
            for f in files
        ],
    }
    Path(out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(files)} Datei(en), Version {__version__}")
    return 0


def sign(manifest_path: str, key_hex: str | None = None) -> int:
    key_hex = (key_hex or os.getenv("DECKDROP_SIGNING_KEY") or "").strip()
    if not key_hex:
        print("DECKDROP_SIGNING_KEY fehlt – kein unsigniertes Release.", file=sys.stderr)
        return 1
    secret = bytes.fromhex(key_hex)
    data = Path(manifest_path).read_bytes()
    sig = ed25519.sign(secret, data)
    from deckdrop.core import release_key

    pub = ed25519.public_key(secret).hex()
    if pub not in release_key.PUBLIC_KEYS:
        print("Schlüssel passt zu keinem Eintrag in release_key.PUBLIC_KEYS.", file=sys.stderr)
        return 1
    Path(manifest_path + ".sig").write_text(sig.hex() + "\n", encoding="ascii")
    print(f"{manifest_path}.sig geschrieben")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args = argv[1], argv[2:]
    if cmd == "generate-key" and len(args) == 1:
        return generate_key(args[0])
    if cmd == "check-version" and len(args) == 1:
        return check_version(args[0])
    if cmd == "manifest" and len(args) >= 2:
        return build_manifest(args[0], args[1:])
    if cmd == "sign" and len(args) == 1:
        return sign(args[0])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
