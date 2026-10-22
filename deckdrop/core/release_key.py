"""Public Ed25519 keys that may sign DeckDrop release manifests (hex).

Set up once (see docs/RELEASING.md): `python packaging/release_tool.py
generate-key` prints the public key for this list and writes the private key
for the GitHub secret DECKDROP_SIGNING_KEY. While the list is empty, no
update can be verified – DeckDrop then refuses to self-update at all.
"""

PUBLIC_KEYS: list[str] = ["5a5139ebd6f3242f8f79d285db1f867048adb69a04bec9d1ee232a260e32133f"]
