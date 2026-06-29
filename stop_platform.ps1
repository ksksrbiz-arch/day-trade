# =====================================================================
#  stop_platform.ps1  --  stop ALL platform processes (backend + brain)
#  Usage:  powershell -ExecutionPolicy Bypass -File .\stop_platform.ps1
# =====================================================================
$ErrorActionPreference = "SilentlyContinue"

$patterns = @(
    "uvicorn dashboard.app", "trader.exits", "dashboard.optimizer",
    "dashboard.autotuner", "trader.ml.daemon", "trader.agents.runtime",
    "trader.agents.supervisor", "dashboard.bots", "trader.run"
)
$killed = 0
foreach ($p in $patterns) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$p*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $killed++ }
}
# Brain (Next.js dev server: node processes under the brain dir)
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -like "*brain*" -or $_.CommandLine -like "*next*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $killed++ }

Write-Host "Stopped $killed platform process(es)."
