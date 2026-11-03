param(
    [ValidateSet("Api", "Frontend", "Mqtt", "Ngrok")]
    [string]$Service,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherPath = $PSCommandPath
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$MqttExe = "C:\Program Files\Mosquitto\mosquitto.exe"

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1)
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not (Test-PortListening $Port) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
    }
    return (Test-PortListening $Port)
}

function Start-ServiceWindow([string]$Name) {
    $arguments = "-NoExit -ExecutionPolicy Bypass -File `"$LauncherPath`" -Service $Name"
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $Root
    Write-Host "Started $Name in a new PowerShell window."
}

function Test-EnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path $Path)) { return $false }
    $line = Get-Content $Path | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    return [bool]($line -and $line -notmatch "=\s*$")
}

if ($Service) {
    switch ($Service) {
        "Api" {
            Set-Location $Backend
            python -m uvicorn app.main:app --reload --port 8000
        }
        "Frontend" {
            Set-Location $Frontend
            npm.cmd run dev
        }
        "Mqtt" {
            if (-not (Test-Path $MqttExe)) {
                throw "Mosquitto was not found at $MqttExe. Install Mosquitto or update the path in start-local.ps1."
            }
            & $MqttExe -v
        }
        "Ngrok" {
            $ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
            if (-not $ngrok) { throw "ngrok was not found on PATH. Install ngrok and sign in/configure its auth token first." }
            ngrok http 8000
        }
    }
    exit $LASTEXITCODE
}

$backendEnv = Join-Path $Root ".env"
$frontendEnv = Join-Path $Frontend ".env.local"
$missing = @()
foreach ($key in @("DATABASE_URL", "SYNC_DATABASE_URL", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")) {
    if (-not (Test-EnvValue $backendEnv $key)) { $missing += ".env: $key" }
}
foreach ($key in @("NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_RAZORPAY_KEY_ID")) {
    if (-not (Test-EnvValue $frontendEnv $key)) { $missing += "frontend/.env.local: $key" }
}

if ($missing.Count -gt 0) {
    Write-Host "Configure these values before starting the payment flow:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host " - $_" }
    Write-Host "Do not paste credential values into chat or commit the env files."
    exit 1
}

if ($ValidateOnly) {
    Write-Host "Local configuration files are present and required values are non-empty. Secret values were not displayed."
    exit 0
}

if (-not (Test-PortListening 3306)) {
    $mysqlService = Get-Service -Name "MySQL80" -ErrorAction SilentlyContinue
    if ($mysqlService -and $mysqlService.Status -ne "Running") {
        Write-Host "Starting Windows service MySQL80..."
        Start-Service -Name "MySQL80"
    }
    $deadline = (Get-Date).AddSeconds(20)
    while (-not (Test-PortListening 3306) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    if (-not (Test-PortListening 3306)) {
        throw "MySQL is not listening on port 3306. Start your local MySQL service and rerun this script."
    }
}
Write-Host "MySQL is ready on port 3306."

if (Test-PortListening 1883) {
    Write-Host "Mosquitto is already listening on port 1883; reusing it."
} else {
    Start-ServiceWindow "Mqtt"
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Test-PortListening 1883) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    if (-not (Test-PortListening 1883)) { Write-Warning "Mosquitto did not become ready on port 1883. Check its window." }
}

if (Test-PortListening 8000) {
    Write-Host "An API process is already listening on port 8000; reusing it. Restart it manually if you changed .env."
} else {
    Start-ServiceWindow "Api"
    if (-not (Wait-Port 8000 30)) { Write-Warning "API did not become ready on port 8000. Check its window." }
}

if (Test-PortListening 3000) {
    Write-Host "A frontend process is already listening on port 3000; reusing it. Restart it manually if you changed frontend/.env.local."
} else {
    Start-ServiceWindow "Frontend"
    if (-not (Wait-Port 3000 60)) { Write-Warning "Frontend did not become ready on port 3000. Check its window." }
}

$tunnelReady = $false
try {
    $tunnels = (Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2).tunnels
    foreach ($tunnel in $tunnels) {
        if ($tunnel.config.addr -match "8000") {
            Write-Host "Ngrok already forwards $($tunnel.public_url) to the API."
            Write-Host "Razorpay webhook: $($tunnel.public_url)/v1/webhooks/razorpay"
            $tunnelReady = $true
            break
        }
    }
} catch {
    $tunnelReady = $false
}
if (-not $tunnelReady) {
    Start-ServiceWindow "Ngrok"
    $deadline = (Get-Date).AddSeconds(20)
    while (-not $tunnelReady -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        try {
            $tunnels = (Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2).tunnels
            foreach ($tunnel in $tunnels) {
                if ($tunnel.config.addr -match "8000") {
                    Write-Host "Ngrok forwards $($tunnel.public_url) to the API."
                    Write-Host "Razorpay webhook: $($tunnel.public_url)/v1/webhooks/razorpay"
                    $tunnelReady = $true
                    break
                }
            }
        } catch {
            $tunnelReady = $false
        }
    }
    if (-not $tunnelReady) { Write-Warning "Ngrok did not become ready. Check its window and configured auth token." }
}

Write-Host ""
Write-Host "Locker services are starting:"
Write-Host "  PWA:      http://localhost:3000"
Write-Host "  API docs: http://localhost:8000/docs"
Write-Host "  Health:   http://localhost:8000/health"
Write-Host "Keep each service window open. The local MQTT broker is not exposed through ngrok."
