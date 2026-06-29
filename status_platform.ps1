# =====================================================================
#  status_platform.ps1  --  show what's running + port health
#  Usage:  powershell -ExecutionPolicy Bypass -File .\status_platform.ps1
# =====================================================================
$ErrorActionPreference = "SilentlyContinue"

function Check($name, $match) {
    $n = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$match*" } | Measure-Object).Count
    $tag = if ($n -gt 0) { "UP  " } else { "DOWN" }
    "{0}  {1,-14} ({2})" -f $tag, $name, $n
}
Write-Host "== Python daemons =="
Check "dashboard"    "uvicorn dashboard.app"
Check "exits"        "trader.exits"
Check "optimizer"    "dashboard.optimizer"
Check "autotuner"    "dashboard.autotuner"
Check "ml.daemon"    "trader.ml.daemon"
Check "agents"       "trader.agents.runtime"
Check "supervisor"   "trader.agents.supervisor"

Write-Host "== Ports / endpoints =="
$d = Test-NetConnection 127.0.0.1 -Port 8000 -InformationLevel Quiet
$b = Test-NetConnection 127.0.0.1 -Port 3000 -InformationLevel Quiet
"  dashboard :8000  -> $(if($d){'LISTENING'}else{'down'})"
"  brain     :3000  -> $(if($b){'LISTENING'}else{'down'})"
$nodes = (Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -like '*brain*' -or $_.CommandLine -like '*next*' } | Measure-Object).Count
"  brain node procs : $nodes"
