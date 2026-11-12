@echo off
rem BirriaSweep: start the bridge and open the bundled page in your browser.
rem Keep this window open while you use the page. Close it (or press Ctrl+C) to stop.
cd /d "%~dp0"
python\python.exe bridge\birria_bridge.py --lib lib --ui ui %*
pause
