param(
    [ValidateRange(0, 65535)]
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "The project virtual environment was not found." -ForegroundColor Red
    Write-Host "Run these commands first:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .\dev-scripts\install-backend.ps1"
    exit 1
}

$selectedPort = $Port
if ($selectedPort -eq 0) {
    $portInput = Read-Host "Backend port [8000]"
    $selectedPort = if ([string]::IsNullOrWhiteSpace($portInput)) { 8000 } else { 0 }

    if (-not [string]::IsNullOrWhiteSpace($portInput) -and
        (-not [int]::TryParse($portInput, [ref]$selectedPort) -or $selectedPort -lt 1 -or $selectedPort -gt 65535)) {
        Write-Host "Enter a valid TCP port from 1 to 65535." -ForegroundColor Red
        exit 1
    }
}

$portInUse = Get-NetTCPConnection -LocalPort $selectedPort -State Listen -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Host "Port $selectedPort is already in use. Run the script again and choose another port." -ForegroundColor Red
    exit 1
}

$baseUrl = "http://127.0.0.1:$selectedPort"
$env:QIESI_PUBLIC_BASE_URL = $baseUrl
$localOrigins = @(
    $baseUrl,
    "http://localhost:$selectedPort",
    "http://127.0.0.1:5173",
    "http://localhost:5173"
)

if ($env:QIESI_CORS_ORIGINS) {
    $localOrigins += $env:QIESI_CORS_ORIGINS.Split(",")
}
$env:QIESI_CORS_ORIGINS = ($localOrigins | Where-Object { $_ } | Select-Object -Unique) -join ","

$arguments = @(
    "-m", "uvicorn", "main:app",
    "--host", "127.0.0.1",
    "--port", $selectedPort.ToString()
)

Write-Host "Starting QIESI API Toolkit on port $selectedPort..." -ForegroundColor Cyan
$backend = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $repoRoot -NoNewWindow -PassThru

try {
    $started = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if ($backend.HasExited) {
            throw "The backend stopped before startup completed (exit code $($backend.ExitCode))."
        }

        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
            if ($health.status -eq "ok") {
                $started = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }

    if (-not $started) {
        throw "The backend did not become ready within 15 seconds."
    }

    Write-Host ""
    Write-Host "QIESI API Toolkit is running" -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop it." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "Test UI          $baseUrl/test-api/"
    Write-Host "Swagger docs     $baseUrl/docs"
    Write-Host "OpenAPI schema   $baseUrl/openapi.json"
    Write-Host "Health           $baseUrl/health"
    Write-Host "Service info     $baseUrl/info"
    Write-Host "Ping             $baseUrl/ping"
    Write-Host "Document Markdown POST $baseUrl/documents/markdown"
    Write-Host "K2 Markdown       POST $baseUrl/documents/markdown/k2"
    Write-Host "K2 SmartObject    POST $baseUrl/K2-Markdown"
    Write-Host "K2 File XML       POST $baseUrl/K2-Markdown-Xml"
    Write-Host "K2 File XML JSON  POST $baseUrl/K2-Markdown-Xml-Json"
    Write-Host "Document OCR      POST $baseUrl/documents/markdown/ocr"
    Write-Host "JSON to Excel     POST $baseUrl/JSON-to-XLSX"
    Write-Host "Text to CSV       POST $baseUrl/TXT-to-CSV"
    Write-Host ""

    Wait-Process -Id $backend.Id
} finally {
    if (-not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    }
}
