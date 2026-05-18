@echo off
pip install pyinstaller
pyinstaller --clean packaging\deckdrop.spec
echo Done: dist\deckdrop\deckdrop.exe
