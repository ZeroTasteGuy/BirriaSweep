#!/bin/sh
# BirriaSweep: start only the bridge, then open https://zerotasteguy.github.io/BrowSDR_Sweep/
# and pick "Wi-Fi stick (BirriaSDR bridge)". Ctrl+C stops it.
cd "$(dirname "$0")" && exec python3 bridge/birria_bridge.py --lib lib "$@"
