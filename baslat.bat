@echo off
chcp 65001 >nul
title Facefold
cd /d "%~dp0"

python calistir.py
if errorlevel 1 (
  echo.
  echo   Program baslatilamadi. Once kur.bat dosyasini calistirin.
  pause
)
