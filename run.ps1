$ErrorActionPreference = "Stop"

function Assert-Command($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Required command not found: $name"
  }
}

function Ensure-Venv {
  param(
    [Parameter(Mandatory=$true)][string]$BackendDir,
    [Parameter(Mandatory=$true)][string]$VenvDir
  )

  $python = Join-Path $VenvDir "Scripts\python.exe"
  $pip = Join-Path $VenvDir "Scripts\pip.exe"

  if (-not (Test-Path $python)) {
    Write-Host "[kairos] Creating backend venv at $VenvDir"
    & python -m venv $VenvDir
  }

  Write-Host "[kairos] Installing backend dependencies"
  & $pip install -r (Join-Path $BackendDir "requirements.txt") | Out-Host
}

function Ensure-FrontendDeps {
  param([Parameter(Mandatory=$true)][string]$FrontendDir)
  $nodeModules = Join-Path $FrontendDir "node_modules"
  if (-not (Test-Path $nodeModules)) {
    Write-Host "[kairos] Installing frontend dependencies"
    Push-Location $FrontendDir
    try {
      & npm install | Out-Host
    } finally {
      Pop-Location
    }
  }
}

function Start-BackendJob {
  param(
    [Parameter(Mandatory=$true)][string]$BackendDir,
    [Parameter(Mandatory=$true)][string]$VenvDir
  )
  $python = Join-Path $VenvDir "Scripts\python.exe"
  $cmd = "& `"$python`" -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"
  return Start-Job -Name "kairos-backend" -ScriptBlock {
    param($BackendDir, $Cmd)
    Set-Location $BackendDir
    Invoke-Expression $Cmd
  } -ArgumentList $BackendDir, $cmd
}

function Start-FrontendJob {
  param([Parameter(Mandatory=$true)][string]$FrontendDir)
  return Start-Job -Name "kairos-frontend" -ScriptBlock {
    param($FrontendDir)
    Set-Location $FrontendDir
    npm run dev
  } -ArgumentList $FrontendDir
}

function Start-NgrokJob {
  param(
    [Parameter(Mandatory=$true)][int]$Port
  )

  # ngrok must already be installed + authenticated (ngrok config add-authtoken ...)
  Assert-Command "ngrok"

  return Start-Job -Name "kairos-ngrok" -ScriptBlock {
    param($Port)
    ngrok http $Port --log=stdout
  } -ArgumentList $Port
}

function Wait-ForNgrokUrl {
  param(
    [Parameter(Mandatory=$true)][int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
      if ($r -and $r.tunnels) {
        $https = $r.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($https -and $https.public_url) { return [string]$https.public_url }
        $any = $r.tunnels | Select-Object -First 1
        if ($any -and $any.public_url) { return [string]$any.public_url }
      }
    } catch {
      # keep retrying until timeout
    }
    Start-Sleep -Milliseconds 500
  }
  return $null
}

try {
  Assert-Command "docker"
  Assert-Command "python"
  Assert-Command "npm"

  $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
  $BackendDir = Join-Path $Root "backend"
  $FrontendDir = Join-Path $Root "frontend"
  $VenvDir = Join-Path $BackendDir ".venv"

  if (-not (Test-Path (Join-Path $BackendDir ".env"))) {
    throw "Missing backend\.env. Create it (copy backend\.env.example) and set required keys."
  }

  Write-Host "[kairos] Starting Docker services (Qdrant)"
  Push-Location $Root
  try {
    docker compose up -d | Out-Host
  } finally {
    Pop-Location
  }

  Ensure-Venv -BackendDir $BackendDir -VenvDir $VenvDir
  Ensure-FrontendDeps -FrontendDir $FrontendDir

  Write-Host "[kairos] Starting backend + frontend (Ctrl+C to stop)"
  $backendJob = Start-BackendJob -BackendDir $BackendDir -VenvDir $VenvDir
  $frontendJob = Start-FrontendJob -FrontendDir $FrontendDir

  Write-Host "[kairos] Backend:  http://localhost:8000/docs"
  Write-Host "[kairos] Frontend: http://localhost:5173"

  $enableNgrok = ($env:KAIROS_NGROK -eq "1") -or ($env:KAIROS_NGROK -eq "true")
  if ($enableNgrok) {
    Write-Host "[kairos] Starting ngrok tunnel -> http://localhost:8000"
    $ngrokJob = Start-NgrokJob -Port 8000
    $publicUrl = Wait-ForNgrokUrl -TimeoutSeconds 20
    if ($publicUrl) {
      Write-Host "[kairos] ngrok public URL: $publicUrl"
      Write-Host "[kairos] Jira webhook URL:  $publicUrl/api/webhook/jira"
      Write-Host "[kairos] ngrok inspector:   http://127.0.0.1:4040"
    } else {
      Write-Host "[kairos] ngrok started but URL not ready yet. Check: http://127.0.0.1:4040"
    }
  } else {
    Write-Host "[kairos] ngrok disabled. Enable with: `$env:KAIROS_NGROK=1; .\\run.ps1"
  }

  while ($true) {
    Start-Sleep -Seconds 2
    $bj = Get-Job -Id $backendJob.Id -ErrorAction SilentlyContinue
    $fj = Get-Job -Id $frontendJob.Id -ErrorAction SilentlyContinue
    $nj = Get-Job -Name "kairos-ngrok" -ErrorAction SilentlyContinue

    if ($bj -and $bj.State -eq "Failed") { throw "Backend job failed. Run: Receive-Job -Name kairos-backend -Keep" }
    if ($fj -and $fj.State -eq "Failed") { throw "Frontend job failed. Run: Receive-Job -Name kairos-frontend -Keep" }
    if ($nj -and $nj.State -eq "Failed") { throw "ngrok job failed. Run: Receive-Job -Name kairos-ngrok -Keep" }
    if (-not $bj -or -not $fj) { break }
  }
} finally {
  Write-Host "`n[kairos] Stopping jobs"
  Get-Job -Name "kairos-backend" -ErrorAction SilentlyContinue | Stop-Job -Force -ErrorAction SilentlyContinue
  Get-Job -Name "kairos-frontend" -ErrorAction SilentlyContinue | Stop-Job -Force -ErrorAction SilentlyContinue
  Get-Job -Name "kairos-ngrok" -ErrorAction SilentlyContinue | Stop-Job -Force -ErrorAction SilentlyContinue
  Get-Job -Name "kairos-backend" -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
  Get-Job -Name "kairos-frontend" -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
  Get-Job -Name "kairos-ngrok" -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
  Write-Host "[kairos] Docker services are still running. To stop them: docker compose down"
}

