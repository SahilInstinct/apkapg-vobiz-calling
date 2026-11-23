param(
    [string]$ProjectDir = "",
    [int]$Port = 8001,
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
    $ProjectDir = Split-Path -Parent $PSScriptRoot
}
$ProjectDir = $ProjectDir.TrimEnd('\', '"')
$logsDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$pidFile = Join-Path $logsDir "cloudflared.pid"
$outLog = Join-Path $logsDir "cloudflared.out.log"
$errLog = Join-Path $logsDir "cloudflared.err.log"

# Stop previous tunnel started by this script if still running
if (Test-Path $pidFile) {
    $oldPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid -and $oldPid.Trim() -match '^\d+$') {
        Stop-Process -Id ([int]$oldPid.Trim()) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Remove-Item $outLog, $errLog -Force -ErrorAction SilentlyContinue

# Find cloudflared executable
$cfCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cfCmd) {
    $candidates = @(
        "$env:ProgramFiles\cloudflared\cloudflared.exe",
        "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cloudflared.exe"
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $found) {
        Write-Error "cloudflared not found. Install it first: winget install Cloudflare.cloudflared"
        exit 1
    }
    $cfExe = $found
} else {
    $cfExe = $cfCmd.Source
}

$proc = Start-Process -FilePath $cfExe `
    -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port") `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -Path $pidFile -Value $proc.Id -Encoding Ascii

# Wait for trycloudflare.com URL
$tunnelUrl = $null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
        $errText = if (Test-Path $errLog) { Get-Content $errLog -Raw } else { "" }
        Write-Error "cloudflared exited unexpectedly: $errText"
        exit 1
    }
    foreach ($logFile in @($errLog, $outLog)) {
        if (Test-Path $logFile) {
            $content = Get-Content $logFile -Raw -ErrorAction SilentlyContinue
            if ($content -match '(https://[a-z0-9-]+\.trycloudflare\.com)') {
                $tunnelUrl = $Matches[1]
                break
            }
        }
    }
    if ($tunnelUrl) { break }
    Start-Sleep -Milliseconds 500
}

if (-not $tunnelUrl) {
    Write-Error "Timed out waiting for Cloudflare URL. Check logs\cloudflared.err.log"
    exit 1
}

# Wait until the new hostname resolves in DNS so Vobiz accepts answer_url immediately
$tunnelHost = ([System.Uri]$tunnelUrl).Host
$dnsDeadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $dnsDeadline) {
    try {
        [System.Net.Dns]::GetHostAddresses($tunnelHost) | Out-Null
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

# Update PUBLIC_BASE_URL in .env cleanly (UTF-8 without BOM)
$envPath = Join-Path $ProjectDir ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $ProjectDir ".env.example") $envPath
}

$lines = Get-Content $envPath -Encoding UTF8
$updated = $false
$newLines = foreach ($line in $lines) {
    if ($line -match '^\s*PUBLIC_BASE_URL\s*=') {
        $updated = $true
        "PUBLIC_BASE_URL=$tunnelUrl"
    } else {
        $line
    }
}
if (-not $updated) {
    $newLines = @($newLines) + "PUBLIC_BASE_URL=$tunnelUrl"
}

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($envPath, ($newLines -join "`r`n") + "`r`n", $utf8NoBom)

Write-Host "[OK] Cloudflare Tunnel active: $tunnelUrl"
Write-Host "[OK] Updated PUBLIC_BASE_URL in .env"