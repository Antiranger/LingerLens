@echo off
setlocal
py -3.14 "%~dp0..\companion\native_host.py" 2>nul || python "%~dp0..\companion\native_host.py"
