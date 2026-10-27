@echo off
rem Start or resume a paper trading run in this window. Close the window (or Ctrl+C) to stop; state is kept.
rem Usage: start-paper.cmd --name main --strategies all
title trader paper run - close this window to stop
cd /d "%~dp0"
if "%~1"=="" (call "%~dp0bot.cmd" paper --name main --strategies all) else (call "%~dp0bot.cmd" paper %*)
pause
