@echo off
setlocal
cd /d "%~dp0"
"venv\Scripts\python.exe" scripts\servicios_web.py web
set RESULTADO=%ERRORLEVEL%
pause
exit /b %RESULTADO%
