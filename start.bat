@echo off
setlocal
set "PROJECT_DIR=%~dp0"

echo ==================================================
echo  ApkaPG Vobiz Calling - One-Click Startup
echo ==================================================

if not exist "%PROJECT_DIR%.env" (
    copy "%PROJECT_DIR%.env.example" "%PROJECT_DIR%.env" >nul
    echo [INFO] Created .env from .env.example.
)

echo [1/3] Starting Cloudflare tunnel and updating PUBLIC_BASE_URL in .env...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%scripts\start_tunnel.ps1" -Port 8001
if errorlevel 1 (
    echo [ERROR] Could not start Cloudflare tunnel.
    pause
    exit /b 1
)

echo [2/3] Starting ApkaPG Vobiz Backend on port 8001...
docker info >nul 2>&1
if not errorlevel 1 (
    pushd "%PROJECT_DIR%"
    docker compose up -d --build
    if errorlevel 1 (
        popd
        pause
        exit /b 1
    )
    popd
) else if exist "%PROJECT_DIR%.venv\Scripts\uvicorn.exe" (
    echo [INFO] Docker Desktop not running; starting local .venv uvicorn instead...
    pushd "%PROJECT_DIR%"
    start "ApkaPG Vobiz Server" cmd /c ".venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8001 --workers 1"
    popd
) else (
    echo [ERROR] Neither Docker Desktop nor .venv was found.
    pause
    exit /b 1
)

echo [3/3] Waiting for http://127.0.0.1:8001/health ...
set /a TRIES=0
:wait_loop
set /a TRIES+=1
curl -s http://127.0.0.1:8001/health >nul 2>&1
if not errorlevel 1 goto ready
if %TRIES% GEQ 25 goto open_ui
timeout /t 2 /nobreak >nul
goto wait_loop

:ready
echo [OK] Everything is ready!

:open_ui
start "" "http://127.0.0.1:8001/"
pause