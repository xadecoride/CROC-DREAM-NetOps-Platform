# CROC DREAM NetOps Platform - Local Standalone Startup Script
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "🚀 Starting CROC DREAM NetOps Platform Locally" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$root = $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"

Write-Host "1. Starting Backend API (FastAPI + SQLite + Threaded Worker)..." -ForegroundColor Yellow
$backendJob = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$backendDir'; uv run python run_local.py" -PassThru

Start-Sleep -Seconds 2

Write-Host "2. Starting Frontend Web UI (Vite + React)..." -ForegroundColor Yellow
$frontendJob = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$frontendDir'; npm run dev -- --host 127.0.0.1 --port 5173" -PassThru

Start-Sleep -Seconds 2

Write-Host "3. Opening Browser at http://127.0.0.1:5173 ..." -ForegroundColor Green
Start-Process "http://127.0.0.1:5173"

Write-Host "✅ NetOps Platform is running!" -ForegroundColor Green
Write-Host "• Frontend UI: http://127.0.0.1:5173"
Write-Host "• Backend API & Swagger: http://127.0.0.1:8000/docs"
Write-Host "• Default Token: dev-admin-token"
