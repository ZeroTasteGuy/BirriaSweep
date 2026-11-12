#!/bin/sh
# BirriaSweep: start the bridge and open the bundled page. Ctrl+C stops it.
cd "$(dirname "$0")" && exec python3 bridge/birria_bridge.py --lib lib --ui ui "$@"
