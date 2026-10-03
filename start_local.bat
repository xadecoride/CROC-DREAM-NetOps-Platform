@echo off
title CROC DREAM NetOps Platform - Local Launcher
echo ==================================================
echo Starting CROC DREAM NetOps Platform Locally...
echo ==================================================

powershell -ExecutionPolicy Bypass -File "%~dp0start_local.ps1"
pause
