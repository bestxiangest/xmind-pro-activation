@echo off
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python xmind.py
pause