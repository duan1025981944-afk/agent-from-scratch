@echo off
rem Start mini_nanobot from any folder. The folder you are in becomes the workspace.
rem   %~dp0 = the folder this script lives in (the project folder)
rem   %*    = every argument you typed, passed through (e.g. --list, --session KEY)
rem Comments are ASCII on purpose: a UTF-8 .cmd with Chinese text can break on a GBK console.
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*
