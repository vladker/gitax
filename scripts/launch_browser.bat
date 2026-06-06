@echo off
REM Launches Chrome SxS with CDP port 9222 for MAX automation
REM Waits until Chrome is actually ready on port 9222 before exiting
setlocal
echo Launching Chrome with remote debugger (port 9222)...

set "CHROME_PATH=C:\Users\vldkr\AppData\Local\Google\Chrome SxS\Application\chrome.exe"

if not exist "%CHROME_PATH%" (
    echo [ERROR] Chrome SxS not found at: %CHROME_PATH%
    pause
    exit /b 1
)

start "" "%CHROME_PATH%" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome SxS\User Data"

echo Waiting for Chrome to be ready on port 9222...
REM Poll port 9222 until Chrome responds (max 30 seconds)
set /a attempts=0
:waitloop
set /a attempts+=1
powershell -Command "(New-Object Net.Sockets.TcpClient).Connect('localhost',9222)" >nul 2>&1
if errorlevel 1 (
    if %attempts% GEQ 30 (
        echo [ERROR] Chrome did not start in time (30s timeout)
        pause
        exit /b 1
    )
    timeout /t 1 /nobreak >nul
    goto waitloop
)

echo Chrome is ready on port 9222 (%attempts%s). You can now run run_archiver.bat
pause
