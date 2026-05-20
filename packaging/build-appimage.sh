#!/usr/bin/env bash
# Build a self-contained DeckDrop AppImage (Python + libtorrent + frontend).
#
# Requirements:
#   python3.10–3.13 (libtorrent has no wheels for 3.14+)
#   curl, fuse2 (optional, for running AppImages)
#
# Usage:
#   bash packaging/build-appimage.sh          # venv wiederverwenden
#   bash packaging/build-appimage.sh --clean  # venv neu anlegen
#
# Output:
#   DeckDrop-<version>-x86_64.AppImage

set -euo pipefail

CLEAN_VENV=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN_VENV=1 ;;
    esac
done

PIP_RETRIES="${PIP_RETRIES:-5}"
PIP_FLAGS=(--retries 10 --timeout 120 --default-timeout 120)

pip_retry() {
    local attempt=1
    while true; do
        if "$BUILD_VENV/bin/pip" install "${PIP_FLAGS[@]}" "$@"; then
            return 0
        fi
        if (( attempt >= PIP_RETRIES )); then
            echo "pip fehlgeschlagen nach ${PIP_RETRIES} Versuchen." >&2
            return 1
        fi
        echo "pip-Download unterbrochen, Versuch $((attempt + 1))/${PIP_RETRIES}…" >&2
        sleep $((attempt * 3))
        attempt=$((attempt + 1))
    done
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" && -x "$ROOT_DIR/.build-python/bin/python3.12" ]]; then
    PYTHON="$ROOT_DIR/.build-python/bin/python3.12"
fi
if [[ -z "$PYTHON" ]]; then
    for candidate in python3.12 python3.11 python3.13 python3.10; do
        if command -v "$candidate" &>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [[ -z "$PYTHON" ]]; then
    echo "Kein passendes Python gefunden (3.10–3.13 nötig für libtorrent)."
    echo "Setze z. B.: PYTHON=python3.10 bash packaging/build-appimage.sh"
    exit 1
fi

echo "=== DeckDrop AppImage Build ==="
echo "Python: $($PYTHON --version)"

BUILD_VENV="$ROOT_DIR/.build-appimage-venv"
if [[ "$CLEAN_VENV" -eq 1 ]]; then
    echo "Entferne alte Build-Venv…"
    rm -rf "$BUILD_VENV"
fi
if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
    echo "Erstelle Build-Venv…"
    "$PYTHON" -m venv "$BUILD_VENV"
fi

echo "Installiere Abhängigkeiten (PyPI, mit Wiederholungen)…"
pip_retry -U pip wheel setuptools
pip_retry pyinstaller
pip_retry -e ".[transfer]"

VERSION="$("$BUILD_VENV/bin/python" -c "import deckdrop; print(deckdrop.__version__)")"
ARCH="${ARCH:-x86_64}"
APPDIR="$ROOT_DIR/DeckDrop-$VERSION.AppDir"
OUTPUT="$ROOT_DIR/DeckDrop-$VERSION-$ARCH.AppImage"

echo "Version: $VERSION"
echo "PyInstaller bundle…"
rm -rf build dist "$APPDIR"
"$BUILD_VENV/bin/pyinstaller" --noconfirm --clean "$SCRIPT_DIR/deckdrop.spec"

mkdir -p "$APPDIR/usr/bin"
cp -a dist/deckdrop/. "$APPDIR/usr/bin/"

cp "$SCRIPT_DIR/deckdrop.desktop" "$APPDIR/deckdrop.desktop"
cp "$SCRIPT_DIR/deckdrop.svg" "$APPDIR/deckdrop.svg"
cp "$SCRIPT_DIR/deckdrop-kiosk.desktop" "$APPDIR/deckdrop-kiosk.desktop"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")"
export PATH="$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/bin:${LD_LIBRARY_PATH:-}"
exec "$HERE/usr/bin/deckdrop" "$@"
EOF
chmod +x "$APPDIR/AppRun"

APPIMAGETOOL="${APPIMAGETOOL:-}"
if [[ -z "$APPIMAGETOOL" ]]; then
    if command -v appimagetool &>/dev/null; then
        APPIMAGETOOL=appimagetool
    else
        APPIMAGETOOL="$ROOT_DIR/appimagetool"
        if [[ ! -x "$APPIMAGETOOL" ]]; then
            echo "Lade appimagetool…"
            curl -fsSL -o "$APPIMAGETOOL" \
                "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
            chmod +x "$APPIMAGETOOL"
        fi
    fi
fi

echo "AppImage erstellen…"
OUTPUT_NEW="${OUTPUT}.new"
rm -f "$OUTPUT_NEW"

if [[ -e "$OUTPUT" ]] && { fuser -s "$OUTPUT" 2>/dev/null || lsof -t "$OUTPUT" &>/dev/null; }; then
    echo "Hinweis: $OUTPUT ist noch geöffnet (DeckDrop läuft?)."
    echo "         Baue nach ${OUTPUT_NEW} – alte Datei bleibt unverändert."
fi

ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUTPUT_NEW"

if mv -f "$OUTPUT_NEW" "$OUTPUT" 2>/dev/null; then
    FINAL="$OUTPUT"
else
    FINAL="$OUTPUT_NEW"
    echo ""
    echo "Konnte $OUTPUT nicht ersetzen (Text file busy – AppImage noch aktiv?)."
    echo "Beende DeckDrop, dann:"
    echo "  mv -f '$OUTPUT_NEW' '$OUTPUT'"
fi

echo ""
echo "Fertig: $FINAL"
echo "Starten: chmod +x '$FINAL' && './$(basename "$FINAL")'"
echo ""
echo "Steam Deck (Gaming Mode):"
echo "  ./$(basename "$FINAL") --kiosk"
echo "  oder AppImage extrahieren und deckdrop-kiosk.desktop als Non-Steam-Spiel."
