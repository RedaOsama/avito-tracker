@echo off
rem Runs one check. Used by Windows Task Scheduler if you run the tracker on your PC.
cd /d "%~dp0"
python -m tracker.main >> tracker.log 2>&1
