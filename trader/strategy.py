"""
The deterministic core. Given a Label, decide whether to trade and how.

Same inputs -> same output, every time, no LLM, no randomness. If this doesn't
show an edge on a proper slippage-haircut replay, the system has no edge -- and
you learned it for $0.

The LLM upstream only produces Labels and structured context flags; it never
sizes positions, picks entries/exits, or places orders. Everything below
(decide, confirm_intent, size_and_exits, market_regime) is pure and testable.

v2 additions (all opt-in via StrategyConfig, defaults preserve v0 behaviour):
  * size_and_exits  -- signal- and volatility-scaled notional + adaptive TP/SL
  * confirm_intent  -- now also takes an optional market_regime gate
  * market_regime   -- pure helper to classify regime from SPY features
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from .labels import Label
from .risk import enforce_rr


@dataclass
class Intent:
    symbol: str
    side: str            # "buy" | "sell"
    notional: float      # dollars to deploy
    take_profit_pct: float
    stop_loss_pct: float
    reason: str = ""


@dataclass
class StrategyConfig:
    universe: set[str] = field(default_factory=set)  # tradable symbols; empty = allow all
    min_confidence: float = 0.60
    min_sentiment: float = 0.40    # magnitude required to act
    notional_per_trade: float = 5.0
    take_profit_pct: float = 0.05  # +5% -> exit
    stop_loss_pct: float = 0.03    # -3% -> exit
    allow_short: bool = False
    blocked_event_types: frozenset[str] = frozenset({"noise", "unknown"})
    # --- confirmation layer (deterministic gate over market-data + Groq flags) ---
    require_confirmation: bool = False
    confirm_fail_open: bool = True
    min_rvol: float = 0.5
    momentum_tolerance: float = 0.08
    regime_filter: bool = False     # block trades fighting the SPY regime
    # --- dynamic position sizing (signal strength x inverse volatility) ---
    dynamic_sizing: bool = False
    size_min_mult: float = 0.5      # floor on the size multiplier
    size_max_mult: float = 2.0      # cap on the size multiplier
    vol_target: float = 0.02        # daily-vol target for inverse-vol scaling
    # --- volatility-adaptive exits (TP/SL derived from realized vol) ---
    adaptive_exits: bool = False
    tp_vol_mult: float = 2.5        # take-profit = mult * vol_20d
    sl_vol_mult: float = 1.5        # stop-loss   = mult * vol_20d
    tp_floor: float = 0.02
    tp_cap: float = 0.15
    sl_floor: float = 0.015
    sl_cap: float = 0.08
    # --- per-symbol re-entry cooldown (enforced in run loop, minutes) ---
    cooldown_min: float = 0.0
    # --- survival guardrails ---
    min_rr: float = 0.0          # minimum reward:risk (0 = off); e.g. 2.0 = 1:2
    daily_max_dd: float = 0.0    # daily circuit-breaker drawdown %% (0 = off)
    # --- mode / scalper / OFI / options ---
    mode: str = "news"           # "news" | "scalper"
    scalper_universe: tuple = ()
    scalper_window: int = 20
    scalper_k: float = 2.0
    use_ofi: bool = False
    ofi_threshold: float = 0.6
    use_options: bool = False
    # --- day-trader watch->strike engine ---
    watch_buffer: float = 0.005      # confirmation breakout buffer from armed price
    watch_expiry_min: float = 180.0  # how long a catalyst stays armed
    liq_extreme: float = 0.92        # |OFI| above this = one-sided/thin book -> wait
    # --- Omni research enrichment (read-only) ---
    use_omni: bool = False
    omni_gate: bool = False        # if True, veto when Omni clearly opposes; else log-only
    omni_borderline: float = 0.15  # enrich only when confidence within band above min_confidence
    # --- confluence brain (technical + fundamental + quant + council) ---
    use_confluence: bool = False     # require multi-method agreement before trading
    confluence_min_agree: int = 2    # how many methods must agree on direction
    confluence_min_score: float = 0.20  # min |composite| conviction to act
    confluence_size: bool = True     # scale notional by conviction size_mult
    use_fundamentals: bool = True    # include fundamental score (equities only)


def _in_universe(symbol: str, cfg: StrategyConfig) -> bool:
    return (not cfg.universe) or (symbol in cfg.universe)


def decide(
    label: Label,
    cfg: StrategyConfig,
    open_symbols: Optional[set[str]] = None,
) -> Optional[Intent]:
    """Return a single Intent for the strongest qualifying ticker, or None."""
    open_symbols = open_symbols or set()

    if label.event_type in cfg.blocked_event_types:
        return None
    if label.confidence < cfg.min_confidence:
        return None

    bullish = label.sentiment >= cfg.min_sentiment
    bearish = label.sentiment <= -cfg.min_sentiment
    if not (bullish or bearish):
        return None
    if bearish and not cfg.allow_short:
        return None

    side = "buy" if bullish else "sell"

    for symbol in label.tickers:
        if not _in_universe(symbol, cfg):
            continue
        if symbol in open_symbols:
            continue
        return Intent(
            symbol=symbol,
            side=side,
            notional=cfg.notional_per_trade,
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
            reason=f"{label.event_type} sent={label.sentiment:+.2f} "
            f"conf={label.confidence:.2f}",
        )
    return None


def market_regime(spy_features) -> str:
    """Classify the broad regime from SPY technical features.

    risk_on  : SPY above its 20d SMA and 20d momentum >= 0
    risk_off : SPY below its 20d SMA and 20d momentum < 0
    neutral  : mixed signals (or no data)
    """
    if spy_features is None:
        return "neutral"
    up = spy_features.above_sma20 and spy_features.ret_20d >= 0
    down = (not spy_features.above_sma20) and spy_features.ret_20d < 0
    return "risk_on" if up else ("risk_off" if down else "neutral")


def confirm_intent(intent, features, context, cfg: StrategyConfig,
                   market_regime: Optional[str] = None) -> tuple[bool, str]:
    """Deterministic go/no-go for an Intent. Pure: same inputs -> same output.

    Gates: (1) Groq structured veto, (2) liquidity floor (min_rvol),
    (3) momentum must not strongly fight the side, (4) optional regime filter
    (longs blocked in risk_off, shorts blocked in risk_on).
    """
    if not cfg.require_confirmation:
        return True, "confirmation off"

    if features is None:
        return (cfg.confirm_fail_open,
                "no features (fail-open)" if cfg.confirm_fail_open else "no features (fail-closed)")

    if context is not None and getattr(context, "confirm", True) is False:
        return False, f"groq veto ({getattr(context, 'note', '')})"

    if features.rvol < cfg.min_rvol:
        return False, f"thin volume rvol={features.rvol:.2f}<{cfg.min_rvol}"

    tol = cfg.momentum_tolerance
    if intent.side == "buy":
        if features.ret_20d < -tol and not features.above_sma20:
            return False, f"long fights downtrend ret20={features.ret_20d:+.2f}"
    else:
        if features.ret_20d > tol and features.above_sma20:
            return False, f"short fights uptrend ret20={features.ret_20d:+.2f}"

    if cfg.regime_filter and market_regime == "high_vol":
        # Don't stand down ENTIRELY in high vol -- that starves the learning loop
        # (no trades -> no resolved outcomes). Instead trade only STRONGLY
        # trend-confirmed setups; the vol-target/Kelly sizing already shrinks size
        # here and the drawdown breaker sits below. Weak/counter-trend -> blocked.
        # Refuse COUNTER-trend setups in high vol, but allow TREND-ALIGNED ones
        # (with the SMA and not strongly fighting momentum) to trade at the
        # already-reduced high-vol size. Standing down on every setup starves the
        # learning loop for the whole duration of a choppy high-vol regime; the
        # vol-target/Kelly sizing + drawdown breaker keep the risk small.
        aligned = ((intent.side == "buy" and features.above_sma20 and features.ret_20d > -tol)
                   or (intent.side in ("sell", "short") and not features.above_sma20
                       and features.ret_20d < tol))
        if not aligned:
            return False, "high_vol: counter-trend setup blocked"
    if cfg.regime_filter and market_regime:
        if intent.side == "buy" and market_regime == "risk_off":
            return False, "long blocked in risk_off regime"
        if intent.side == "sell" and market_regime == "risk_on":
            return False, "short blocked in risk_on regime"

    return True, f"confirmed (rvol={features.rvol:.2f} regime={market_regime or 'n/a'})"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def size_and_exits(intent, label, features, cfg: StrategyConfig):
    """Return a NEW Intent with deterministic sizing + adaptive exits applied.

    Sizing multiplier = signal_strength x inverse_volatility, clamped to
    [size_min_mult, size_max_mult]:
      * signal_strength scales with |sentiment| and confidence above threshold
      * inverse_volatility = vol_target / realized_vol (calmer names get more)
    Exits (when adaptive_exits): TP/SL = mult * realized vol, clamped to bounds.

    No-ops to the original Intent when the respective features are disabled.
    """
    notional = intent.notional
    tp = intent.take_profit_pct
    sl = intent.stop_loss_pct

    if cfg.dynamic_sizing:
        strength = (min(1.0, abs(label.sentiment)) * min(1.0, label.confidence)) if label is not None else 0.6
        # map strength (0..1) to ~0.6..1.4 baseline
        smult = 0.6 + 0.8 * strength
        if features is not None and features.vol_20d > 0:
            vmult = cfg.vol_target / features.vol_20d
        else:
            vmult = 1.0
        mult = _clamp(smult * vmult, cfg.size_min_mult, cfg.size_max_mult)
        notional = round(cfg.notional_per_trade * mult, 2)

    if cfg.adaptive_exits and features is not None and features.vol_20d > 0:
        tp = _clamp(cfg.tp_vol_mult * features.vol_20d, cfg.tp_floor, cfg.tp_cap)
        sl = _clamp(cfg.sl_vol_mult * features.vol_20d, cfg.sl_floor, cfg.sl_cap)

    if cfg.min_rr > 0:
        tp, sl = enforce_rr(tp, sl, cfg.min_rr)
    return replace(intent, notional=notional,
                   take_profit_pct=round(tp, 4), stop_loss_pct=round(sl, 4),
                   reason=intent.reason + f" | sized=${notional:.0f} tp={tp:.3f} sl={sl:.3f}")
