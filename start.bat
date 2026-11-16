@echo off
chcp 65001 >nul
title Skillset
cd /d "%~dp0"
python app\server.py %*
if errorlevel 1 pause
