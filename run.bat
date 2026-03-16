@echo off
REM Quick launcher for GestureChord
if not exist "venv\Scripts\activate.bat" (
    echo Run setup.bat first!
    pause
    exit /b 1
)
call venv\Scripts\activate.bat
python main.py