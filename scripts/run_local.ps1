param(
    [switch]$SkipInstall,
    [switch]$SkipOllamaPull
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path "venv\Scripts\python.exe")) {
    python -m venv venv
}

$Python = Join-Path $Root "venv\Scripts\python.exe"
if (-not $SkipInstall) {
    & $Python -m pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[SETUP] .env를 생성했습니다. 외부 API 수집 전 인증키를 입력하세요."
}

if (-not $SkipOllamaPull -and (Get-Command ollama -ErrorAction SilentlyContinue)) {
    ollama pull qwen2.5:3b
}

& $Python -m pytest -q
& $Python app.py
