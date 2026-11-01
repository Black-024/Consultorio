@echo off
rem Inicia el sistema del consultorio. Ponerlo dentro de la carpeta "consultorio".
cd /d "%~dp0"

rem Solo la primera vez (si no existe .venv): crea el entorno e instala lo necesario.
if not exist .venv (
    echo Primera vez: instalando, esto tarda un momento...
    py -m venv .venv
    call .venv\Scripts\activate
    pip install fastapi "uvicorn[standard]" httpx tzdata
) else (
    call .venv\Scripts\activate
)

set PANEL_USERS=laura:1234
set STAFF_PHONES=+54 9 11 0000 0001

rem Abre el navegador a los 3 segundos, cuando el servidor ya arranco.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"

echo.
echo Sistema en http://localhost:8000  (usuario: laura / clave: 1234)
echo Para cerrarlo, cierra esta ventana o presiona Ctrl+C.
echo.
uvicorn app.main:create_app --factory --port 8000
pause
