@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo No existe venv. Crea el entorno e instala requirements.txt.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" scripts\servicios_web.py integrado
set RESULTADO=%ERRORLEVEL%
pause
exit /b %RESULTADO%
