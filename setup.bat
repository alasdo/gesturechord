@echo off
REM ═══════════════════════════════════════════════════════
REM  GestureChord — Windows Setup Script
REM ═══════════════════════════════════════════════════════
echo.
echo  ╔═══════════════════════════════════╗
echo  ║       GestureChord Setup          ║
echo  ╚═══════════════════════════════════╝
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Download from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo [1/4] Python found:
python --version
echo.

REM Create virtual environment
if not exist "venv" (
    echo [2/4] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Try: python -m ensurepip
        pause
        exit /b 1
    )
    echo       Done.
) else (
    echo [2/4] Virtual environment already exists.
)
echo.

REM Activate and install
echo [3/4] Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo       Done.
echo.

REM Check MIDI
echo [4/4] Checking MIDI ports...
python -c "import mido; ports = mido.get_output_names(); print('  Available MIDI ports:'); [print(f'    - {p}') for p in ports] if ports else print('    (none found)')"
echo.

echo ═══════════════════════════════════════════════════════
echo  Setup complete!
echo.
echo  BEFORE YOU RUN:
echo    1. Install loopBe1: https://www.nerds.de/en/loopbe1.html
echo    2. Reboot your PC after installing loopBe1
echo    3. In FL Studio: Options ^> MIDI Settings ^> enable loopBe input
echo    4. Load a synth plugin (FL Keys works for testing)
echo.
echo  TO RUN:
echo    venv\Scripts\activate
echo    python main.py
echo.
echo  Or just double-click: run.bat
echo ═══════════════════════════════════════════════════════
pause