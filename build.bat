@echo off
rem --------------------------------------------------------------
rem Build a standalone kradio.exe with Nuitka (compiles to C).
rem
rem   build.bat            -> single-exe kradio.exe (needs FFmpeg
rem                           installed on the target machine)
rem   build.bat --fat      -> also bundles ffplay.exe (fully
rem                           portable, ~120 MB)
rem
rem Output: dist\kradio.exe + dist\stations.json
rem
rem Note: if the build crashes in "onefile_compressor", uninstall
rem the zstandard package and rebuild (a Nuitka/zstd bug on this
rem machine):  pip uninstall -y zstandard
rem --------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist icon.ico python make_icon.py || goto :err

set DATA=--include-data-files=stations.json=stations.json --include-data-files=icon.ico=icon.ico
if "%~1"=="--fat" set DATA=%DATA% --include-data-files=C:\programs\exes\ffplay.exe=ffplay.exe

python -m nuitka --onefile --windows-console-mode=disable ^
    --enable-plugin=tk-inter ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=icon.ico ^
    --output-filename=kradio.exe ^
    --output-dir=dist ^
    %DATA% radio_gui.py || goto :err

rem tidy: drop Nuitka intermediates, keep the exe
rmdir /s /q dist\radio_gui.dist      2>nul
rmdir /s /q dist\radio_gui.build     2>nul
rmdir /s /q dist\radio_gui.onefile-build 2>nul

copy /y stations.json dist\ >nul

echo.
echo Done. dist\kradio.exe (stations.json sits next to it, editable).
endlocal & exit /b 0

:err
echo BUILD FAILED.
endlocal & exit /b 1
