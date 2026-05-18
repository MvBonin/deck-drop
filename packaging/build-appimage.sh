#!/usr/bin/env bash
set -e

ARCH=x86_64
VERSION=$(python3 -c "import deckdrop; print(deckdrop.__version__)")
APPDIR="DeckDrop-$VERSION.AppDir"

pip install pyinstaller
pyinstaller --clean packaging/deckdrop.spec

mkdir -p "$APPDIR/usr/bin"
cp -r dist/deckdrop/* "$APPDIR/usr/bin/"

cp packaging/deckdrop.desktop "$APPDIR/deckdrop.desktop"
cp packaging/deckdrop.svg     "$APPDIR/deckdrop.svg"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
exec "$(dirname "$0")/usr/bin/deckdrop" "$@"
EOF
chmod +x "$APPDIR/AppRun"

if ! command -v appimagetool &>/dev/null; then
    curl -Lo appimagetool "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool
    APPIMAGETOOL=./appimagetool
else
    APPIMAGETOOL=appimagetool
fi

ARCH=$ARCH $APPIMAGETOOL "$APPDIR" "DeckDrop-$VERSION-$ARCH.AppImage"
echo "Done: DeckDrop-$VERSION-$ARCH.AppImage"
