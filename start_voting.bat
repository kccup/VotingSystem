@echo off
echo ===============================================
echo Starting Voting System...
echo ===============================================
cd "C:\Users\Daniel Lai\OneDrive - St. Louis Community College\Desktop\GitHub\VotingSystem"
call venv\Scripts\activate.bat
echo.
echo Server starting at http://localhost:5000
echo.
echo *** TO STOP THE SERVER: Close this window ***
echo.
python app.py
pause