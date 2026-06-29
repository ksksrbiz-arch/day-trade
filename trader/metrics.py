"""
The scoreboard. Pure math, fully tested. These are the numbers that tell you
the truth -- not 'did the account go up this week', but 'is there an edge, and
does it beat just holding the index'.

The benchmark comparison is the one most home-grown bots skip and the one that
kills most of them: if your strategy can't beat buy-and-hold SPY over the same
window, it has no reason to exist.
"""
from __future__ import annotations

from typing import Sequence

from .simbroker import ClosedTrade


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Largest peak-to-trough drop as a fraction (0.25 = -25%)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            worst = max(worst, dd)
    return worst


def summarize(
    trades: Sequence[ClosedTrade],
    equity_curve: Sequence[float],
    benchmark_return: float = 0.0,
) -> dict:
    """benchmark_return: fractional return of buy-and-hold over the same window
    (e.g. SPY up 1.2% -> 0.012). Lets us report strategy-minus-benchmark."""
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "total_pnl": 0.0,
            "total_return": 0.0, "max_drawdown": max_drawdown(equity_curve),
            "benchmark_return": benchmark_return, "vs_benchmark": -benchmark_return,
        }

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)  # positive number

    start_equity = equity_curve[0] if equity_curve else 0.0
    end_equity = equity_curve[-1] if equity_curve else 0.0
    total_return = (end_equity - start_equity) / start_equity if start_equity else 0.0

    win_rate = len(wins) / n
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    profit_factor = gross_win / gross_loss if gross_loss else float("inf")
    expectancy = sum(pnls) / n  # avg $ per trade

    return {
        "trades": n,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "total_pnl": sum(pnls),
        "total_return": total_return,
        "max_drawdown": max_drawdown(equity_curve),
        "benchmark_return": benchmark_return,
        "vs_benchmark": total_return - benchmark_return,
    }


def format_report(stats: dict) -> str:
    pf = stats["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    verdict = "BEATS benchmark" if stats["vs_benchmark"] > 0 else "loses to benchmark"
    return (
        f"Trades:         {stats['trades']}\n"
        f"Win rate:       {stats['win_rate']*100:.1f}%\n"
        f"Avg win:        ${stats['avg_win']:.4f}\n"
        f"Avg loss:       ${stats['avg_loss']:.4f}\n"
        f"Profit factor:  {pf_s}\n"
        f"Expectancy:     ${stats['expectancy']:.4f} / trade\n"
        f"Total return:   {stats['total_return']*100:+.2f}%\n"
        f"Max drawdown:   {stats['max_drawdown']*100:.2f}%\n"
        f"Benchmark:      {stats['benchmark_return']*100:+.2f}%\n"
        f"Vs benchmark:   {stats['vs_benchmark']*100:+.2f}%  ({verdict})"
    )
