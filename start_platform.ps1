# =====================================================================
#  start_platform.ps1  --  bring up the ENTIRE platform (one command)
#  Python backend (dashboard + all daemons) + the Next.js Brain graph.
#  Idempotent: only starts what isn't already running. Logs to data/.
#
#  Usage:   powershell -ExecutionPolicy Bypass -File .\start_platform.ps1
# =====================================================================
$ErrorActionPreference = "SilentlyContinue"
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = "$proj\.venv\Scripts\python.exe"
$logs = "$proj\data"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
Set-Location $proj

function Start-Py($match, $argList, $name) {
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$match*" }
    if (-not $running) {
        Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $proj `
            -WindowStyle Hidden `
            -RedirectStandardOutput "$logs\$name.out.log" -RedirectStandardError "$logs\$name.err.log"
        Write-Host "  started $name"
    } else { Write-Host "  $name already running" }
}

Write-Host "== Platform backend =="

# 1) Dashboard API + UI (port 8000)
$dash = Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet
if (-not $dash) {
    Start-Process -FilePath $py -ArgumentList "-m","uvicorn","dashboard.app:app","--host","127.0.0.1","--port","8000" `
        -WorkingDirectory $proj -WindowStyle Hidden `
        -RedirectStandardOutput "$logs\dash.out.log" -RedirectStandardError "$logs\dash.err.log"
    Write-Host "  started dashboard (:8000)"; Start-Sleep -Seconds 5
} else { Write-Host "  dashboard already running (:8000)" }

# 2) Trading + intelligence daemons
Start-Py "trader.exits"              @("-m","trader.exits") "exits"
Start-Py "dashboard.optimizer"       @("-m","dashboard.optimizer","--daemon") "optimizer"
Start-Py "dashboard.autotuner"       @("-m","dashboard.autotuner") "autotuner"
Start-Py "trader.ml.daemon"          @("-m","trader.ml.daemon","--every","6") "ml"
Start-Py "trader.agents.runtime"     @("-m","trader.agents.runtime","--loop","--every","900") "agents"
Start-Py "trader.agents.supervisor"  @("-m","trader.agents.supervisor","--loop","--every","120") "supervisor"

# 3) Resume enabled bots (creates default 'main' bot on first run)
& $py -m dashboard.autostart *>> "$logs\autostart.log"
Write-Host "  resumed bots"

# 4) Brain 3D graph (Next.js, port 3000)
Write-Host "== Platform Brain (3D graph) =="
$brain = Test-NetConnection -ComputerName 127.0.0.1 -Port 3000 -InformationLevel Quiet
if (-not $brain) {
    $npm = (Get-Command npm -ErrorAction SilentlyContinue)
    if ($npm) {
        if (-not (Test-Path "$proj\brain\node_modules")) {
            Write-Host "  installing brain deps (first run)..."
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npm install" -WorkingDirectory "$proj\brain" -Wait -WindowStyle Hidden
        }
        Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npm run dev > `"$logs\brain.out.log`" 2>&1" `
            -WorkingDirectory "$proj\brain" -WindowStyle Hidden
        Write-Host "  started brain (:3000)"
    } else {
        Write-Host "  npm not found -- skipping brain. Install Node.js to enable the 3D graph."
    }
} else { Write-Host "  brain already running (:3000)" }

Write-Host ""
Write-Host "Platform up.  Dashboard: http://127.0.0.1:8000   Brain: http://localhost:3000/brain"
