@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\arrancar.ps1" %*
set "UNAMBA_RESULT=%errorlevel%"
popd
if not "%UNAMBA_RESULT%"=="0" (
 echo Revisa el mensaje anterior y la carpeta registros.
 if not defined UNAMBA_NO_PAUSE pause
)
exit /b %UNAMBA_RESULT%
