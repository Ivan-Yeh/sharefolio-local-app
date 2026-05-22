#!/bin/bash
set -e

echo "Installing build dependencies..."
pip install pyinstaller pywebview requests --quiet

echo "Building Sharefolio.app..."
pyinstaller --noconfirm Sharefolio.spec

echo ""
echo "Done. App bundle is at: dist/Sharefolio.app"
echo "To run: open dist/Sharefolio.app"
