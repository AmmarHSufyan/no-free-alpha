@echo off
rem Runs the bot with the project's own virtualenv (.venv). Usage: bot.cmd backtest
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m bot %*
