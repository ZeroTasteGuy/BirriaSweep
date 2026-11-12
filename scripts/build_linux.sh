#!/bin/sh
# Builds BirriaSweep on Linux into build/BirriaSweep (no Python bundled: uses python3 + numpy).
# Needs: git, cmake, a C compiler, libusb-1.0 dev files, Node.js 22 (only for the bundled page).
# Usage: scripts/build_linux.sh [--no-page]
set -eu
BIRRIA_REPO=${BIRRIA_REPO:-https://github.com/ot0tot/BirriaSDR}
BIRRIA_REF=${BIRRIA_REF:-35439a93fd845c1105559382b17f35c1a0abb5b9}
PAGE_REPO=${PAGE_REPO:-https://github.com/ZeroTasteGuy/BrowSDR_Sweep}
PAGE_REF=${PAGE_REF:-main}
root=$(cd "$(dirname "$0")/.." && pwd)
work=$root/build
mkdir -p "$work"

[ -d "$work/BirriaSDR" ] || git clone -q "$BIRRIA_REPO" "$work/BirriaSDR"
cd "$work/BirriaSDR"
git fetch -q origin "$BIRRIA_REF" && git checkout -qf "$BIRRIA_REF" && git clean -fdq -e build
git apply "$root/driver/birriasdr-fast-sweep.patch"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build --parallel
ctest --test-dir build --output-on-failure

pkg=$work/BirriaSweep
rm -rf "$pkg" && mkdir -p "$pkg/bridge" "$pkg/lib" "$pkg/docs"
cp "$root"/start/*.sh "$pkg/"
cp "$root/bridge/birria_bridge.py" "$pkg/bridge/"
find build -maxdepth 2 \( -name 'libbirriasdr.so*' -o -name 'libbirria_display.so*' \) -exec cp -P {} "$pkg/lib/" \;
cp "$root"/docs/*.md "$pkg/docs/"
cp "$root/README.md" "$root/LICENSE" "$pkg/"

if [ "${1:-}" != --no-page ]; then
  [ -d "$work/BrowSDR" ] || git clone -q "$PAGE_REPO" "$work/BrowSDR"
  cd "$work/BrowSDR" && git fetch -q origin "$PAGE_REF" && git checkout -qf FETCH_HEAD
  npm ci --no-audit --no-fund && BROWSDR_BASE=/ npx vite build
  cp -r dist "$pkg/ui"
fi
echo "Package: $pkg  (start it with start_with_page.sh or start_bridge_only.sh)"
