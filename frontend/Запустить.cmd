@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Старый сервер на том же порту (в т.ч. на IPv6) отдавал бы прежнюю версию — закрываем его.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173 " ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1
echo Вектор запущен: http://127.0.0.1:5173
echo Чтобы остановить, закройте это окно.
start "" http://127.0.0.1:5173
rem no-store: после правок браузер всегда берёт свежие файлы.
python -c "import http.server as s;H=s.SimpleHTTPRequestHandler;e=H.end_headers;H.end_headers=lambda h:(h.send_header('Cache-Control','no-store'),e(h))[1];s.test(H,port=5173,bind='127.0.0.1')"
