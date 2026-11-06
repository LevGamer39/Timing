@echo off
chcp 65001 >nul
set found=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173 " ^| findstr LISTENING') do (
  taskkill /PID %%p /F >nul 2>&1
  set found=1
)
if defined found (
  echo Тайминг остановлен.
) else (
  echo Тайминг не был запущен.
)
ping -n 3 127.0.0.1 >nul