#!/bin/bash
# Build the native h3.c Metal binary into third_party/h3.c/h3
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/third_party/h3.c"
if [[ ! -f "$SRC/Makefile" ]]; then
  echo "h3.c submodule missing. Run:"
  echo "  git submodule update --init --recursive"
  exit 1
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "h3.c targets Apple Silicon macOS."
  exit 1
fi
jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"
echo "Building h3.c with make -j${jobs} …"
make -C "$SRC" -j"$jobs"
if [[ -x "$SRC/h3" ]]; then
  echo "OK: $SRC/h3"
  "$SRC/h3" --help | head -n 20 || true
else
  echo "Build finished but $SRC/h3 is missing."
  exit 1
fi
