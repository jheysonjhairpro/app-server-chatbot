@echo off
setlocal
cd /d "%~dp0"
"venv\Scripts\python.exe" scripts\servicios_web.py preguntar --interactivo %*
set RESULTADO=%ERRORLEVEL%
pause
exit /b %RESULTADO%
