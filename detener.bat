@echo off
setlocal
call "%~dp0iniciar.bat" detener
exit /b %errorlevel%
