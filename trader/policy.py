"""
Policy engine -- ONE auditable chokepoint every intended trade passes through.

This is the "policy graph / approved-action scopes": instead of guardrails
scattered across the code, every order is evaluated here against explicit,
inspectable rules and gets one verdict: APPROVE, DENY, or ESCALATE, with reasons
and a confidence score. Trading analogues of the classic agent failure modes:

  duplicate payment   -> duplicate open position on the same symbol   (DENY)
  bad vendor          -> symbol not in approved universe / halted      (DENY)
  hallucinated SKU    -> ticker the news invented, not real/tradable   (DENY)
  over-budget         -> notional beyond approved size scope           (ESCALATE)
  wrong refund call   -> low-confidence short / option (low reversibility) (ESCALATE)
  runaway agent       -> daily trade cap exceeded                      (DENY)

ESCALATE = "needs human review" -> in this paper system it means DO NOT auto-
execute; log it for review. Pure + heavily eval-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PolicyConfig:
    max_notional: float = 5000.0        # per-trade size scope
    max_concurrent: int = 25            # max simultaneous open positions
    max_daily_trades: int = 40          # runaway-agent cap
    min_conf_short: float = 0.45        # shorts are lower-reversibility -> need conviction
    min_conf_option: float = 0.55       # options lower-reversibility -> need more
    approved_instruments: frozenset = field(
        default_factory=lambda: frozenset({"equity", "crypto", "option"}))
    allow_real_money: bool = False      # hard line; never True here


def evaluate(intent: dict, ctx: dict, pol: PolicyConfig = None) -> dict:
    """Pure verdict. intent: {symbol, side, notional, instrument}.
    ctx: {open_symbols, n_positions, day_trades, confidence, universe, halted, paper}.
    Returns {decision, reasons, confidence, reversibility}."""
    pol = pol or PolicyConfig()
    reasons = []
    decision = "approve"

    def deny(msg):
        nonlocal decision
        decision = "deny"; reasons.append("DENY: " + msg)

    def escalate(msg):
        nonlocal decision
        if decision != "deny":
            decision = "escalate"
        reasons.append("ESCALATE: " + msg)

    sym = (intent.get("symbol") or "").upper()
    side = intent.get("side")
    notional = float(intent.get("notional") or 0)
    instrument = intent.get("instrument", "equity")
    conf = float(ctx.get("confidence", 0.0))

    # --- hard denials ---
    if not ctx.get("paper", True) or pol.allow_real_money:
        deny("real-money execution is not permitted")
    if not sym:
        deny("hallucinated/empty symbol")
    uni = ctx.get("universe")
    if uni and sym not in uni and instrument != "crypto":
        deny(f"{sym} not in approved universe")
    if sym in ctx.get("halted", set()):
        deny(f"{sym} halted/restricted")
    if sym in ctx.get("open_symbols", set()):
        deny(f"duplicate open position on {sym}")
    if ctx.get("day_trades", 0) >= pol.max_daily_trades:
        deny(f"daily trade cap {pol.max_daily_trades} reached")
    if ctx.get("n_positions", 0) >= pol.max_concurrent:
        deny(f"max concurrent positions {pol.max_concurrent} reached")
    if notional <= 0:
        deny("non-positive notional")
    if instrument not in pol.approved_instruments:
        deny(f"instrument '{instrument}' out of approved scope")

    # --- escalations (scope / reversibility) ---
    if notional > pol.max_notional:
        escalate(f"notional ${notional:.0f} exceeds size scope ${pol.max_notional:.0f}")
    if side == "sell" and instrument == "equity" and conf < pol.min_conf_short:
        escalate(f"low-confidence short (conf {conf:.2f} < {pol.min_conf_short})")
    if instrument == "option" and conf < pol.min_conf_option:
        escalate(f"low-confidence option (conf {conf:.2f} < {pol.min_conf_option})")

    # reversibility: paper equity fully reversible; options/shorts less so
    reversibility = 1.0
    if instrument == "option":
        reversibility = 0.5
    elif side == "sell":
        reversibility = 0.7
    if not reasons:
        reasons.append("all policy checks passed")
    return {"decision": decision, "reasons": reasons,
            "confidence": round(conf, 2), "reversibility": reversibility}
