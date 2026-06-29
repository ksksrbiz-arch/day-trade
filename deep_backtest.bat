@echo off
REM Deep-history walk-forward backtest. Run AFTER upgrading the Massive plan
REM (Starter/Developer/Advanced) so flat-file downloads are entitled.
REM Auto-uses Massive flat files when downloadable; else falls back to Alpaca.
cd /d "%~dp0"
echo Running deep walk-forward (this can take several minutes on first Massive pull)...
echo.
echo === LONG-ONLY (10y) ===
".venv\Scripts\python.exe" -m trader.walkforward --days 2520 --long-only
echo.
echo === LONG/SHORT (10y) ===
".venv\Scripts\python.exe" -m trader.walkforward --days 2520 --short
echo.
echo Report saved to data\backtests\latest.json  (view it on the dashboard)
pause
