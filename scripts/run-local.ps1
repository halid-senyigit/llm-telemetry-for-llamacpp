param(
  [int]$BackendPort = 7171,
  [int]$FrontendPort = 5173,
  [string]$LlamaMetricsUrl = "http://localhost:6688/metrics",
  [string]$ModelName = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$env:BACKEND_PORT = "$BackendPort"
$env:FRONTEND_PORT = "$FrontendPort"
$env:LLAMA_METRICS_URL = $LlamaMetricsUrl
if ($ModelName) {
  $env:MODEL_NAME = $ModelName
} else {
  Remove-Item Env:\MODEL_NAME -ErrorAction SilentlyContinue
}

if (-not (Test-Path "node_modules")) {
  npm install
}

@'
import importlib.util
import subprocess
import sys

missing = [name for name in ("fastapi", "uvicorn", "httpx") if importlib.util.find_spec(name) is None]
if missing:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
'@ | python -

Write-Host "LLMBrief.local starting..."
Write-Host "Frontend: http://127.0.0.1:$FrontendPort"
Write-Host "Backend:  http://127.0.0.1:$BackendPort"
Write-Host "llama.cpp metrics: $LlamaMetricsUrl"

npm run dev
