@echo off
chcp 65001 >nul
title Facefold - Kurulum
cd /d "%~dp0"

echo.
echo   Facefold kurulumu
echo   ================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo   [HATA] Python bulunamadi.
  echo.
  echo   python.org/downloads adresinden Python 3.10 veya ustunu kurun.
  echo   Kurulum ekraninda "Add Python to PATH" kutusunu isaretlemeyi unutmayin.
  echo.
  pause
  exit /b 1
)

echo   Gerekli paketler kuruluyor. Ilk kurulum birkac dakika surebilir...
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo   [HATA] Paketler kurulamadi. Yukaridaki mesaji kontrol edin.
  pause
  exit /b 1
)

echo.
echo   Kurulum tamam. Artik baslat.bat dosyasina cift tiklayabilirsiniz.
echo.
pause
