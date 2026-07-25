@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install -q fastapi "uvicorn[standard]" python-multipart
  ".venv\Scripts\python.exe" -m web
) else (
  python -m pip install -q fastapi "uvicorn[standard]" python-multipart
  python -m web
)
