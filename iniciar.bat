@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Instalando/verificando dependencias...
python -m pip install -q -r requirements.txt
echo.
echo Monitor de Seguranca PB em http://localhost:8000  (feche esta janela para parar)
echo.
start "" http://localhost:8000
python -m uvicorn main:app --app-dir app --host 127.0.0.1 --port 8000
pause
