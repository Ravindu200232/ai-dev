@echo off
setlocal EnableExtensions

rem AgentForge one-click setup and launcher.

cd /d "%~dp0"

set "NODE_VERSION=20.18.1"
set "NODE_ARCH=x64"
set "GO_VERSION=1.24.7"
set "GO_ARCH=amd64"
set "RUNTIME_DIR=%CD%\.agentforge-runtime"

echo.
echo ============================================================
echo   AgentForge - setup and start
echo ============================================================
echo.

call :ensure_node
if errorlevel 1 goto :failed

rem Pass the selected runtime to Electron and the backend.
set "PATH=%NODE_HOME%;%PATH%"
set "AGENTFORGE_NODE=%NODE_EXE%"
set "AGENTFORGE_NPM=%NPM_CMD%"

call :ensure_npm_packages "desktop" "desktop\node_modules\electron\dist\electron.exe" "Desktop and Electron"
if errorlevel 1 goto :failed

call :ensure_npm_packages "studio" "studio\node_modules\.bin\next.cmd" "Studio"
if errorlevel 1 goto :failed

call :ensure_backend
if errorlevel 1 goto :failed

rem Electron rebuilds the backend itself if the binary ever goes missing, and
rem the Go we fetched is not on PATH. runtime.js reads this to find it.
if defined GO_EXE set "AGENTFORGE_GO=%GO_EXE%"

if not exist "desktop\node_modules\electron\dist\electron.exe" (
    echo [ERROR] Electron is still missing after npm setup.
    goto :failed
)

echo.
echo [OK] Setup is complete. Starting AgentForge...
start "" "desktop\node_modules\electron\dist\electron.exe" "desktop"
endlocal
exit /b 0


:ensure_node
echo [1/3] Checking Node.js...
set "NODE_EXE="
set "NPM_CMD="
set "NODE_HOME="
set "NODE_MAJOR="

where node.exe >nul 2>&1
if errorlevel 1 goto :download_node

for /f "usebackq delims=" %%V in (`node.exe -p "Number(process.versions.node.split('.')[0])" 2^>nul`) do set "NODE_MAJOR=%%V"
if not defined NODE_MAJOR goto :download_node
if %NODE_MAJOR% LSS 20 goto :download_node

for /f "delims=" %%P in ('where node.exe 2^>nul') do if not defined NODE_EXE set "NODE_EXE=%%P"
for /f "delims=" %%P in ('where npm.cmd 2^>nul') do if not defined NPM_CMD set "NPM_CMD=%%P"
if not defined NPM_CMD goto :download_node
for %%P in ("%NODE_EXE%") do set "NODE_HOME=%%~dpP"
echo       Using installed Node.js.
"%NODE_EXE%" --version
exit /b 0

:download_node
set "NODE_FOLDER=node-v%NODE_VERSION%-win-%NODE_ARCH%"
set "NODE_HOME=%RUNTIME_DIR%\%NODE_FOLDER%"
set "NODE_EXE=%NODE_HOME%\node.exe"
set "NPM_CMD=%NODE_HOME%\npm.cmd"
set "NODE_ZIP=%RUNTIME_DIR%\%NODE_FOLDER%.zip"
set "NODE_URL=https://nodejs.org/dist/v%NODE_VERSION%/%NODE_FOLDER%.zip"

if exist "%NODE_EXE%" (
    echo       Using the AgentForge Node.js runtime.
    "%NODE_EXE%" --version
    exit /b 0
)

echo       Node.js 20 or newer was not found.
echo       Downloading Node.js v%NODE_VERSION% from nodejs.org...
if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%NODE_URL%' -OutFile '%NODE_ZIP%'"
if errorlevel 1 (
    echo [ERROR] Node.js download failed. Check the internet connection.
    exit /b 1
)

call :unzip "%NODE_ZIP%" "%RUNTIME_DIR%"
if errorlevel 1 (
    echo [ERROR] Node.js extraction failed.
    exit /b 1
)

if not exist "%NODE_EXE%" (
    echo [ERROR] The downloaded Node.js runtime is incomplete.
    exit /b 1
)

del /q "%NODE_ZIP%" >nul 2>&1
echo       Node.js is ready.
"%NODE_EXE%" --version
exit /b 0


:ensure_npm_packages
set "PACKAGE_DIR=%~1"
set "READY_FILE=%~2"
set "PACKAGE_NAME=%~3"

echo [2/3] Checking %PACKAGE_NAME% packages...
if not exist "%READY_FILE%" goto :install_npm_packages

pushd "%PACKAGE_DIR%"
call "%NPM_CMD%" ls --depth=0 --silent >nul 2>&1
set "NPM_RESULT=%ERRORLEVEL%"
popd
if "%NPM_RESULT%"=="0" (
    echo       %PACKAGE_NAME% packages are already installed.
    exit /b 0
)

:install_npm_packages
echo       Installing %PACKAGE_NAME% packages. First run can take a few minutes...
pushd "%PACKAGE_DIR%"
rem npm install is incremental.
call "%NPM_CMD%" install --no-audit --no-fund
set "NPM_RESULT=%ERRORLEVEL%"
popd

if not "%NPM_RESULT%"=="0" (
    echo [ERROR] npm could not install %PACKAGE_NAME% packages.
    exit /b 1
)
if not exist "%READY_FILE%" (
    echo [ERROR] %PACKAGE_NAME% package installation is incomplete.
    exit /b 1
)
exit /b 0


:ensure_backend
rem The backend is a Go binary. Building it needs Go once; running it does not.
echo [3/3] Checking the AgentForge backend...
set "BACKEND_EXE=%CD%\backend\agent\bin\agentforge.exe"
if exist "%BACKEND_EXE%" (
    echo       The backend is already built.
    call :locate_go
    exit /b 0
)

call :ensure_go
if errorlevel 1 exit /b 1

echo       Building the backend. This happens once...
pushd "%CD%\backend\agent"
set "GOFLAGS=-mod=mod"
"%GO_EXE%" build -o "bin\agentforge.exe" ".\cmd\agentforge"
set "BUILD_RESULT=%ERRORLEVEL%"
popd
if not "%BUILD_RESULT%"=="0" (
    echo [ERROR] The backend did not build.
    exit /b 1
)
echo       Backend built.
exit /b 0


:locate_go
rem Point GO_EXE at a Go that is already on this machine. It fetches nothing
rem and checks nothing, because a built backend needs no toolchain today.
if defined GO_EXE exit /b 0
for /f "delims=" %%G in ('where go.exe 2^>nul') do if not defined GO_EXE set "GO_EXE=%%G"
if not defined GO_EXE if exist "%ProgramFiles%\Go\bin\go.exe" set "GO_EXE=%ProgramFiles%\Go\bin\go.exe"
if not defined GO_EXE if exist "%RUNTIME_DIR%\go%GO_VERSION%\go\bin\go.exe" set "GO_EXE=%RUNTIME_DIR%\go%GO_VERSION%\go\bin\go.exe"
exit /b 0


:ensure_go
rem Go only has to be new enough for backend\agent\go.mod, which asks for 1.24.
set "GO_EXE="
set "GO_VER="
set "GO_MAJOR="
set "GO_MINOR="

for /f "delims=" %%G in ('where go.exe 2^>nul') do if not defined GO_EXE set "GO_EXE=%%G"
if not defined GO_EXE if exist "%ProgramFiles%\Go\bin\go.exe" set "GO_EXE=%ProgramFiles%\Go\bin\go.exe"
if not defined GO_EXE goto :download_go

rem "go version go1.24.7 windows/amd64" -> 1.24.7
for /f "usebackq tokens=3" %%V in (`"%GO_EXE%" version 2^>nul`) do if not defined GO_VER set "GO_VER=%%V"
if not defined GO_VER goto :download_go
set "GO_VER=%GO_VER:go=%"
for /f "tokens=1,2 delims=." %%A in ("%GO_VER%") do (
    set "GO_MAJOR=%%A"
    set "GO_MINOR=%%B"
)
if not defined GO_MINOR goto :download_go
if %GO_MAJOR% LSS 1 goto :download_go
if %GO_MAJOR% EQU 1 if %GO_MINOR% LSS 24 goto :download_go
echo       Using installed Go.
"%GO_EXE%" version
exit /b 0

:download_go
set "GO_ROOT=%RUNTIME_DIR%\go%GO_VERSION%\go"
set "GO_EXE=%GO_ROOT%\bin\go.exe"
set "GO_ZIP=%RUNTIME_DIR%\go%GO_VERSION%.windows-%GO_ARCH%.zip"
set "GO_URL=https://go.dev/dl/go%GO_VERSION%.windows-%GO_ARCH%.zip"

if exist "%GO_EXE%" (
    echo       Using the AgentForge Go toolchain.
    goto :verify_go
)

echo       Go 1.24 or newer was not found.
echo       Downloading Go v%GO_VERSION% from go.dev. This is about 80 MB...
if not exist "%RUNTIME_DIR%" mkdir "%RUNTIME_DIR%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%GO_URL%' -OutFile '%GO_ZIP%'"
if errorlevel 1 (
    echo [ERROR] The Go download failed. Check the internet connection, or
    echo         install Go 1.24 or newer from https://go.dev/dl/ by hand.
    exit /b 1
)

call :unzip "%GO_ZIP%" "%RUNTIME_DIR%\go%GO_VERSION%"
if errorlevel 1 (
    echo [ERROR] The Go extraction failed.
    exit /b 1
)

if not exist "%GO_EXE%" (
    echo [ERROR] The downloaded Go toolchain is incomplete.
    exit /b 1
)

del /q "%GO_ZIP%" >nul 2>&1
echo       Go is ready.

:verify_go
rem A half-written toolchain still has go.exe, so check the library it needs.
echo       Verifying the Go toolchain...
"%GO_EXE%" list std >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The Go standard library is incomplete. Delete
    echo         "%RUNTIME_DIR%\go%GO_VERSION%" and start AgentForge again.
    exit /b 1
)
"%GO_EXE%" version
exit /b 0


:unzip
rem %1 = zip file, %2 = destination directory.
rem Expand-Archive can stop partway through a big archive and still report
rem success, which leaves a half-unpacked toolchain. tar.exe does not.
if not exist "%~2" mkdir "%~2"
if not exist "%SystemRoot%\System32\tar.exe" goto :unzip_powershell
"%SystemRoot%\System32\tar.exe" -xf "%~1" -C "%~2"
exit /b %ERRORLEVEL%

:unzip_powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath '%~1' -DestinationPath '%~2' -Force"
exit /b %ERRORLEVEL%


:failed
echo.
echo ============================================================
echo [ERROR] AgentForge setup did not finish.
echo         Fix the error shown above, then run this file again.
echo ============================================================
pause
endlocal
exit /b 1
