# Paper-trader autostart: dashboard + bots + exits manager + daily optimizer.
# Registered to run at logon via the HKCU Run key (PaperTraderAutostart).
$ErrorActionPreference = "SilentlyContinue"
$proj = "$env:USERPROFILE\Desktop\1commerce-setup\paper-trader\paper-trader"
$py   = "$proj\.venv\Scripts\python.exe"
Set-Location $proj

function Start-IfMissing($match, $args, $out, $err) {
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*$match*" }
    if (-not $running) {
        Start-Process -FilePath $py -ArgumentList $args -WorkingDirectory $proj `
            -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    }
}

# 1) Dashboard
$listening = Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet
if (-not $listening) {
    Start-Process -FilePath $py -ArgumentList "-m","uvicorn","dashboard.app:app","--host","127.0.0.1","--port","8000" `
        -WorkingDirectory $proj -WindowStyle Hidden `
        -RedirectStandardOutput "$proj\data\dash.log" -RedirectStandardError "$proj\data\dash.err.log"
    Start-Sleep -Seconds 6
}

# 2) Trailing-stop exits manager
Start-IfMissing "trader.exits" @("-m","trader.exits") "$proj\data\exits.out.log" "$proj\data\exits.err.log"

# 3) Daily optimizer daemon (runs the closed-loop review each morning)
Start-IfMissing "dashboard.optimizer" @("-m","dashboard.optimizer","--daemon") "$proj\data\opt.out.log" "$proj\data\opt.err.log"

# 3b) 24/7 autotuner (continuous OOS backtest + bounded auto-optimize)
Start-IfMissing "dashboard.autotuner" @("-m","dashboard.autotuner") "$proj\data\at.out.log" "$proj\data\at.err.log"

# 3c) ML retrain daemon (champion/challenger; the live model only improves)
Start-IfMissing "trader.ml.daemon" @("-m","trader.ml.daemon","--every","6") "$proj\data\ml.out.log" "$proj\data\ml.err.log"

# 3d) Autonomous agent desk (independent agents + governor, continuous)
Start-IfMissing "trader.agents.runtime" @("-m","trader.agents.runtime","--loop","--every","900") "$proj\data\agents.out.log" "$proj\data\agents.err.log"

# 3e) Self-repair supervisor (watchdog -- restarts any dead daemon)
Start-IfMissing "trader.agents.supervisor" @("-m","trader.agents.supervisor","--loop","--every","120") "$proj\data\sup.out.log" "$proj\data\sup.err.log"

# 4) Resume enabled bots (creates a default 'main' bot on first run)
& $py -m dashboard.autostart *>> "$proj\data\autostart.log"
