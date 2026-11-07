# JEV Trading Desk V1.0 - local launcher (Windows PowerShell)
#
# Starts the two localhost servers needed for the six-page Trading Desk:
#   1) adapter  127.0.0.1:4791  (server/trading_api.mjs - unified /api/jev facade)
#   2) frontend 127.0.0.1:4781  (vite dev - proxies /api/jev -> 4791)
#
# Usage (PowerShell, from repo root of this app):
#   cd apps/command_center_3d
#   pwsh scripts/start_jev_desk.ps1
#
# Then open: http://127.0.0.1:4781/trading.html
#
# Notes:
#   * The adapter imports the FROZEN authoritative Paper engine in place; it must
#     not be modified. ENGINE_DIR / V46 / V45 / NR_TRUSTED env defaults point at
#     the trusted worktree and v46 artifacts; override only if your layout differs.
#   * Each server is launched in its own minimized window so it keeps running
#     after this script exits. Close those windows to stop them.
#   * No real money, no Provider API keys, no real exchange orders.

$ErrorActionPreference = "Stop"
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$adapterDir = $appRoot

# --- optional: override engine/evidence roots (defaults live inside trading_api.mjs) ---
# $env:JCC_ENGINE_DIR   = "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted/functions/_lib"
# $env:JCC_V46_ROOT    = "D:/jev/artifacts/v46"
# $env:JCC_NR_TRUSTED  = "D:/数据分析/.workbuddy/v43-worktrees/nr-trusted"

Write-Host "[jev-desk] launching adapter on 127.0.0.1:4791 ..." -ForegroundColor Cyan
Start-Process -FilePath "node" `
  -ArgumentList "--no-warnings", "server/trading_api.mjs" `
  -WorkingDirectory $adapterDir `
  -WindowStyle Minimized `
  -RedirectStandardOutput "$adapterDir\server\runtime\adapter.out.log" `
  -RedirectStandardError  "$adapterDir\server\runtime\adapter.err.log"

# give the adapter a moment to bind and import the engine
Start-Sleep -Seconds 3

Write-Host "[jev-desk] launching vite dev on 127.0.0.1:4781 ..." -ForegroundColor Cyan
Start-Process -FilePath "npm" `
  -ArgumentList "run", "dev" `
  -WorkingDirectory $adapterDir `
  -WindowStyle Minimized

Start-Sleep -Seconds 4

Write-Host ""
Write-Host "[jev-desk] READY" -ForegroundColor Green
Write-Host "  Trading Desk : http://127.0.0.1:4781/trading.html" -ForegroundColor White
Write-Host "  Legacy center: http://127.0.0.1:4781/index.html" -ForegroundColor White
Write-Host "  API health   : http://127.0.0.1:4781/api/jev/health" -ForegroundColor White
Write-Host ""
Write-Host "  Stop: close the two launched windows (adapter + vite)." -ForegroundColor Yellow
