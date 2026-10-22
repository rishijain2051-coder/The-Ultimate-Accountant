@echo off
title Ultimate Accountant
cd /d "%~dp0"

echo ====================================================================
echo    Ultimate Accountant - bills into Tally, books that tie
echo ====================================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on this machine.
  echo Install it from https://python.org and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

python launch.py
if errorlevel 1 (
  echo.
  echo The app stopped with an error. The message above says why.
  pause
)
