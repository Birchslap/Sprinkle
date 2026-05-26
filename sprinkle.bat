@echo off
title Sprinkle
cd /d "C:\Users\Prism\Desktop\AI DMs\Sprinkle"
start "" http://localhost:8000
python -m uvicorn server:app --host 127.0.0.1 --port 8000
pause
