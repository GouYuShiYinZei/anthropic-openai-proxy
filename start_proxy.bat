@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

echo ============================================
echo   Anthropic -^> OpenAI Protocol Proxy
echo ============================================
echo.
echo   Close this window to stop.
echo ============================================
echo.

REM Find Python
set PYTHON=
python --version >nul 2>nul && set PYTHON=python
if not "%PYTHON%"=="" goto :found
python3 --version >nul 2>nul && set PYTHON=python3
if not "%PYTHON%"=="" goto :found
py --version >nul 2>nul && set PYTHON=py
if not "%PYTHON%"=="" goto :found

for %%d in (
    C:\Python312 C:\Python311 C:\Python310 C:\Python39
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python310"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python312"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python311"
) do (
    if exist "%%~d\python.exe" (
        set PYTHON=%%~d\python.exe
        goto :found
    )
)

echo [ERROR] Python 3.7+ not found. Install Python first.
pause
exit /b 1

:found
echo Using: %PYTHON%
echo.
"%PYTHON%" "%~dp0anthropic_openai_proxy.py" %*
pause
