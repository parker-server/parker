@echo off
echo Starting Parker...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed. Please install Python 3.8 or higher.
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
if not exist "venv\installed" (
    echo Installing dependencies...
    pip install -r requirements.txt
    echo. > venv\installed
)

REM Check if .env exists
if not exist ".env" (
    echo Creating .env file from template...
    copy .env.example .env
    echo Please edit .env file with your settings!
)

REM Create necessary directories
if not exist storage\database mkdir storage\database
if not exist storage\cache mkdir storage\cache
if not exist storage\cover mkdir storage\cover
if not exist storage\avatars mkdir storage\avatars
if not exist storage\logs mkdir storage\logs


echo.
echo Setup complete!
echo.
echo Starting server at http://localhost:8000
echo API docs at http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop the server
echo.

REM Start the server
python main.py

pause
