#!/usr/bin/env bash
# Generate python3-deckdrop-deps.json for the Flatpak build.
#
# Prerequisites:
#   pip install flatpak-pip-generator
#
# Usage:
#   cd /path/to/deck-drop && bash packaging/generate-flatpak-sources.sh
#
# The generated file is committed to the repo so flatpak-builder can build
# offline. Re-run whenever flatpak-requirements.txt changes.
#
# NOTE: libtorrent only ships platform wheels. After running this script,
# manually add the manylinux x86_64 wheel for the desired Python version:
#   https://pypi.org/pypi/libtorrent/json → releases → cp312 manylinux x86_64
# Then add "only-arches": ["x86_64"] to that source entry.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$SCRIPT_DIR/python3-deckdrop-deps.json"
REQS="$SCRIPT_DIR/flatpak-requirements.txt"

if ! python3 -m flatpak_pip_generator --help &>/dev/null; then
    echo "flatpak-pip-generator not found. Install with:"
    echo "  pip install flatpak-pip-generator"
    exit 1
fi

echo "Generating Flatpak Python sources…"
# --ignore-errors: continue even if libtorrent can't be resolved (no flatpak runtime available)
python3 -m flatpak_pip_generator \
    --output "$OUTPUT" \
    --requirements-file "$REQS" \
    --ignore-errors || true

# Fix module name (generator uses filename as name)
python3 - << 'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
data['name'] = 'python3-deckdrop-deps'
with open(path, 'w') as f:
    json.dump(data, f, indent=4)
PYEOF "$OUTPUT"

echo ""
echo "Generated: $OUTPUT"
echo ""
echo "IMPORTANT: Check if python3-libtorrent sources are empty."
echo "If so, manually add the manylinux x86_64 wheel from:"
echo "  https://pypi.org/pypi/libtorrent/json"
echo "Look for: cp312-cp312-manylinux_2_17_x86_64 wheel"
echo "Add 'only-arches': ['x86_64'] to the source entry."
echo ""
echo "Then commit python3-deckdrop-deps.json alongside com.deckdrop.DeckDrop.json."
