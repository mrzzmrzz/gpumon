#!/bin/sh
# Install gpumon as a single executable. Usage:
#   curl -fsSL https://raw.githubusercontent.com/mrzzmrzz/gpumon/main/install.sh | sh
# Set GPUMON_BIN to change the destination (default ~/.local/bin).
set -eu

URL="https://raw.githubusercontent.com/mrzzmrzz/gpumon/main/gpumon.py"
BIN="${GPUMON_BIN:-$HOME/.local/bin}"
DEST="$BIN/gpumon"

command -v python3 >/dev/null || { echo "gpumon: python3 is required" >&2; exit 1; }

mkdir -p "$BIN"
if command -v curl >/dev/null; then
    curl -fsSL "$URL" -o "$DEST.tmp"
elif command -v wget >/dev/null; then
    wget -qO "$DEST.tmp" "$URL"
else
    echo "gpumon: need curl or wget" >&2; exit 1
fi
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$DEST.tmp"
chmod +x "$DEST.tmp"
mv "$DEST.tmp" "$DEST"

echo "installed $DEST"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "add it to PATH:  export PATH=\"$BIN:\$PATH\"" ;;
esac
echo "next:  gpumon discover && gpumon"
