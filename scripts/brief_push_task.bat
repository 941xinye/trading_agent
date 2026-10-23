@echo off
REM 链析 - 每日简报推送计划任务入口
REM 由 Windows 任务计划程序调用；失败时写入 reports\brief_task.log
cd /d "E:\Work\币分析助手"
set LOG=reports\brief_task.log
if not exist reports mkdir reports
echo ===== %DATE% %TIME% ===== >> "%LOG%"
.venv\Scripts\python.exe main.py brief --push >> "%LOG%" 2>&1
echo exit=%ERRORLEVEL% >> "%LOG%"
