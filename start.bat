@echo off
echo ===================================
echo  Bukhoro Agrocluster - Transport
echo  (Development / Test mode)
echo ===================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Install from https://python.org
    echo Make sure to check "Add to PATH"
    pause
    exit /b 1
)

if not exist "instance\transport.db" (
    echo Installing dependencies...
    pip install -r requirements.txt
    echo.
    echo Initializing database...
    python init_data.py
    echo.
)

echo Starting server...
echo Open in browser: http://localhost:5050
echo Login: admin / Password: admin123
echo.
echo Press Ctrl+C to stop.
echo.
start "" http://localhost:5050
python app.py
pause
