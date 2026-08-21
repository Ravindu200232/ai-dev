@echo off
setlocal

rem Open AgentForge.
rem
rem One window, no install step. The Electron shell starts the backend and the
rem Studio itself and shows the Studio in that window, so this file only has to
rem find electron and hand it the folder.
rem
rem It used to open two console windows and a browser tab, and for a while an
rem installer built a copy of the whole project somewhere else and ran that.
rem Both are gone: this runs the backend and studio sitting next to it, so what
rem you edit is what starts.

cd /d "%~dp0"

where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js was not found on PATH.
    echo         Install it from https://nodejs.org and run this again.
    pause
    exit /b 1
)
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install it from https://python.org and run this again.
    pause
    exit /b 1
)

rem Electron is a binary in node_modules; only the first run has to fetch it.
if not exist "desktop\node_modules\electron\dist\electron.exe" (
    echo First run - fetching Electron. This happens once.
    pushd desktop
    call npm install --no-audit --no-fund
    popd
)
if not exist "desktop\node_modules\electron\dist\electron.exe" (
    echo [ERROR] Electron did not install. The npm output above says why.
    pause
    exit /b 1
)

rem "start" so this console can close and leave the app running.
start "" "desktop\node_modules\electron\dist\electron.exe" "desktop"
endlocal
exit /b 0
