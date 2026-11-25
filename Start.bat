@echo off
setlocal
title CS2 Squad Tracker

if not exist "%~dp0bootstrap.ps1" (
    echo Unzip the whole folder first ^(right-click the zip, Extract All^), then run Start.bat from the extracted folder.
    pause
    exit /b 1
)

if not exist "%~dp0runtime\.ready" (
    echo First start: setting things up. This needs internet and takes a minute or two.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"
    if not exist "%~dp0runtime\.ready" (
        echo.
        echo Setup failed. Check your internet connection and double-click Start.bat again.
        pause
        exit /b 1
    )
)

echo Starting CS2 Squad Tracker. Keep this window open; close it to quit.
"%~dp0runtime\python.exe" -m cs2tracker
if errorlevel 1 (
    echo.
    echo CS2 Squad Tracker stopped with an error.
    pause
)
endlocal
