@echo off
setlocal

if not exist ".env" (
  echo [ОШИБКА] Скопируйте .env.example в .env и вставьте BOT_TOKEN.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

python -m app
pause

