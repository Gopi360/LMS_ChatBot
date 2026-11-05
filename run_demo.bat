@echo off
rem Starts the demo: the API and the chat page, with the local model. Press Ctrl+C to stop it.
cd /d "%~dp0"

rem This file calls itself in the background to open the page once the server answers.
rem A slow start would make a fixed delay open an error page. After about a minute it opens anyway.
if "%~1"=="open-browser" (
    for /l %%i in (1,1,60) do (
        curl -s -o nul --noproxy "*" http://127.0.0.1:8000/api/v1/health && (
            start "" http://127.0.0.1:8000
            exit /b 0
        )
        ping -n 2 127.0.0.1 >nul
    )
    start "" http://127.0.0.1:8000
    exit /b 0
)

set LLM_PROVIDER=ollama
set HF_HUB_OFFLINE=1

if not exist ".venv\Scripts\python.exe" (
    echo The Python environment was not found in .venv
    echo Create it first, as the Setup section of README.md describes.
    pause
    exit /b 1
)

echo Starting the chatbot. The page opens when the server is up.
echo The first reply can be slow while the model loads. Press Ctrl+C here to stop.
start "" /b cmd /c ""%~f0" open-browser"
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
pause
