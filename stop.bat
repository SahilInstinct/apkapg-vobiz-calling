@echo off
setlocal
set "PROJECT_DIR=%~dp0"

echo Stopping ApkaPG Vobiz Backend...
docker info >nul 2>&1
if not errorlevel 1 (
    pushd "%PROJECT_DIR%"
    docker compose down
    popd
)

taskkill /FI "WINDOWTITLE eq ApkaPG Vobiz Server*" /T /F >nul 2>&1

if exist "%PROJECT_DIR%logs\cloudflared.pid" (
    for /f "usebackq delims=" %%p in ("%PROJECT_DIR%logs\cloudflared.pid") do (
        taskkill /PID %%p /F >nul 2>&1
    )
    del "%PROJECT_DIR%logs\cloudflared.pid" >nul 2>&1
)

echo All services stopped.
pause