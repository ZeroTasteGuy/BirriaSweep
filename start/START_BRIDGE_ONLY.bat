@echo off
rem BirriaSweep: start only the bridge. Then open the hosted page
rem https://zerotasteguy.github.io/BrowSDR_Sweep/ and pick "Wi-Fi stick (BirriaSDR bridge)".
rem Keep this window open while you use the page. Close it (or press Ctrl+C) to stop.
cd /d "%~dp0"
python\python.exe bridge\birria_bridge.py --lib lib %*
pause
