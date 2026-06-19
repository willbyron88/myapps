@echo off
"C:\Users\willb\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\willb\myapps\social-analytics-dashboard\social_dashboard.py"
if errorlevel 1 (
    echo.
    echo Script exited with an error. Press any key to close...
    pause >nul
)