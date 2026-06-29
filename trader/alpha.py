"""Confluence engine -- the real entry brain.

A single thin signal (a news label, one indicator) is how the old bots lost
money. Real desks require *agreement across independent methods* before risking
capital. confluence() blends the three analytical lenses --

    technical   (trader.ta)          -- price action / indicators
    quantitative(trader.quant)       -- statistical / cross-sectional factors
    fundamental (trader.fundamentals)-- intrinsic value / quality / growth

plus an optional council (LLM) vote, into one conviction score in [-1, 1], with
regime-adaptive weights and an explicit *agreement gate*: a trade only passes if
enough methods independently agree on direction AND the blended conviction clears
a floor. Conviction magnitude then drives position size.

Everything here is pure and testable; the convenience builder wires in the
engines but each piece can be fed precomputed scores in a unit test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean


# Regime -> method weight multipliers. Trend regimes trust price/quant momentum;
# stress trusts fundamentals (quality) and shrinks risk; ranges balance.
_REGIME_W = {
    "risk_on":  {"ta": 1.2, "quant": 1.2, "fundamental": 0.8, "council": 1.0, "ml": 1.2, "prediction": 1.0, "tnet": 1.2, "cortex": 1.2},
    "risk_off": {"ta": 1.1, "quant": 1.1, "fundamental": 1.2, "council": 1.0, "ml": 1.0, "prediction": 1.0, "tnet": 1.0, "cortex": 1.1},
    "high_vol": {"ta": 0.8, "quant": 0.9, "fundamental": 1.3, "council": 0.8, "ml": 0.9, "prediction": 0.9, "tnet": 0.85, "cortex": 1.0},
    "neutral":  {"ta": 1.0, "quant": 1.0, "fundamental": 1.0, "council": 1.0, "ml": 1.1, "prediction": 1.0, "tnet": 1.1, "cortex": 1.2},
}
_BASE_W = {"ta": 0.30, "quant": 0.26, "fundamental": 0.15, "council": 0.10, "ml": 0.28, "prediction": 0.18, "tnet": 0.20, "cortex": 0.30}

_emph_cache = {"ts": 0.0, "val": None}

def _learned_base():
    """Confluence base weights LEARNED by backprop (cached 60s); None until trained."""
    import time as _t
    if _t.time() - _emph_cache["ts"] < 60:
        return _emph_cache["val"]
    try:
        from . import backprop
        _emph_cache["val"] = backprop.learned_emphasis()
    except Exception:  # noqa: BLE001
        _emph_cache["val"] = None
    _emph_cache["ts"] = _t.time()
    return _emph_cache["val"]



@dataclass
class Conviction:
    composite: float                 # blended score in [-1,1]
    side: str                        # "buy" | "sell" | "flat"
    agree: int                       # methods agreeing with composite sign
    n_methods: int                   # methods that had an opinion
    gate_pass: bool                  # passes agreement + magnitude gate
    size_mult: float                 # conviction-scaled size multiplier
    weights: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    reason: str = ""


def _norm(weights: dict, present: set[str]) -> dict:
    sub = {k: v for k, v in weights.items() if k in present and v > 0}
    tot = sum(sub.values()) or 1.0
    return {k: v / tot for k, v in sub.items()}


def confluence(ta=None, quant=None, fundamental=None, council=None, ml=None,
               prediction=None, tnet=None, cortex=None, regime: str | None = None, min_agree: int = 2,
               min_composite: float = 0.20, size_min: float = 0.5,
               size_max: float = 2.0) -> Conviction:
    """Blend method scores (each in [-1,1], or None if unavailable)."""
    raw = {"ta": ta, "quant": quant, "fundamental": fundamental, "council": council,
           "ml": ml, "prediction": prediction, "tnet": tnet, "cortex": cortex}
    # human-in-the-loop voice overrides (mute drops a voice; pin locks its weight)
    try:
        from . import voices as _voices
        _ov = _voices.overrides()
    except Exception:  # noqa: BLE001
        _ov = {"muted": set(), "pinned": {}}
    scores = {k: float(v) for k, v in raw.items() if v is not None and k not in _ov["muted"]}
    if not scores:
        return Conviction(0.0, "flat", 0, 0, False, 0.0, {}, {}, "no methods")

    rw = _REGIME_W.get(regime or "neutral", _REGIME_W["neutral"])
    _lb = _learned_base()  # backprop-learned emphasis, if available
    eff = {k: ((_ov["pinned"][k] if k in _ov["pinned"]
                else (_lb.get(k) if _lb and k in _lb else _BASE_W.get(k, 0.12))) * rw.get(k, 1.0))
           for k in scores}
    w = _norm(eff, set(scores))
    composite = sum(w[k] * scores[k] for k in scores)
    composite = max(-1.0, min(1.0, composite))

    side = "buy" if composite > 0 else "sell" if composite < 0 else "flat"
    sign = 1 if composite > 0 else -1 if composite < 0 else 0
    # agreement: methods with a meaningful opinion (|s|>=0.1) sharing the sign
    opinions = {k: s for k, s in scores.items() if abs(s) >= 0.10}
    agree = sum(1 for s in opinions.values() if (s > 0) == (sign > 0) and sign != 0)
    n_methods = len(opinions)

    gate = (sign != 0 and agree >= min_agree and abs(composite) >= min_composite)
    # size scales linearly with conviction above the floor, clamped.
    if gate:
        span = max(1e-6, 1.0 - min_composite)
        frac = (abs(composite) - min_composite) / span
        size_mult = round(size_min + (size_max - size_min) * max(0.0, min(1.0, frac)), 2)
    else:
        size_mult = 0.0

    parts = ", ".join(f"{k}={scores[k]:+.2f}" for k in scores)
    reason = (f"confluence {composite:+.2f} [{parts}] regime={regime or 'n/a'} "
              f"agree={agree}/{n_methods} -> {'PASS' if gate else 'BLOCK'}")
    return Conviction(round(composite, 3), side, agree, n_methods, gate,
                      size_mult, {k: round(v, 3) for k, v in w.items()},
                      {k: round(v, 3) for k, v in scores.items()}, reason)


# --------------------------------------------------------------------------- #
# convenience: compute method scores from raw histories, then blend            #
# --------------------------------------------------------------------------- #
def analyze(closes: list[float], panel: dict | None = None,
            volumes: dict | None = None, symbol: str | None = None,
            fundamental_score: float | None = None,
            council_score: float | None = None, regime: str | None = None,
            ml_score: float | None = None, use_ml: bool = True,
            prediction_score: float | None = None, use_prediction: bool = True,
            tnet_score: float | None = None, use_tnet: bool = True,
            use_cortex: bool = True,
            **gate_kwargs) -> Conviction:
    """Build TA + quant scores from histories and blend with optional
    fundamental/council scores. `panel` enables the cross-sectional quant view;
    otherwise single-name stats are used."""
    from . import ta as _ta
    from . import quant as _q

    ta_sig = _ta.ta_signals(closes) if closes else None
    ta_score = ta_sig.score if ta_sig else None

    quant_score = None
    if panel and symbol and len(panel) >= 3:
        cs = _q.cross_sectional(panel, volumes)
        quant_score = cs.scores.get(symbol)
    if quant_score is None and closes:
        ns = _q.name_stats(closes)
        quant_score = ns.quant_score if ns else None

    if ml_score is None and use_ml and closes:
        try:
            from .ml import infer as _ml
            ml_score = _ml.score_from_closes(closes)
        except Exception:  # noqa: BLE001
            ml_score = None
    if prediction_score is None and use_prediction and symbol:
        try:
            from .predict import engine as _pred
            prediction_score = _pred.score_signal(symbol)
        except Exception:  # noqa: BLE001
            prediction_score = None
    if tnet_score is None and use_tnet and symbol:
        try:
            from . import tnet as _tn
            tnet_score = _tn.score_signal(symbol)
        except Exception:  # noqa: BLE001
            tnet_score = None
    # neural core: a nonlinear fuser over the other voices (gated off until proven)
    cortex_score = None
    if use_cortex:
        try:
            from . import cortex as _cx
            if _cx.enabled() and _cx.card().get("trained"):
                _scores = {"ta": ta_score, "quant": quant_score, "fundamental": fundamental_score,
                           "ml": ml_score, "council": council_score, "prediction": prediction_score,
                           "tnet": tnet_score}
                _conv = _cx.conviction(_scores)
                cortex_score = _conv["conviction"]
                _cx.log_live(_scores, _conv)        # telemetry: record what the core thought
        except Exception:  # noqa: BLE001
            cortex_score = None
    conv = confluence(ta=ta_score, quant=quant_score,
                      fundamental=fundamental_score, council=council_score,
                      ml=ml_score, prediction=prediction_score, tnet=tnet_score,
                      cortex=cortex_score, regime=regime, **gate_kwargs)
    if symbol:                                  # capture the 'why' for the reasoning trace
        try:
            from . import reasoning
            reasoning.record(symbol, conv, regime)
        except Exception:  # noqa: BLE001
            pass
    return conv


if __name__ == "__main__":
    # all three agree bullish -> pass, big size
    print(confluence(ta=0.7, quant=0.6, fundamental=0.3, regime="risk_on").reason)
    # ta bullish but quant+fundamentals bearish -> blocked (no agreement)
    print(confluence(ta=0.6, quant=-0.4, fundamental=-0.3, regime="neutral").reason)
    # weak agreement under floor -> blocked
    print(confluence(ta=0.15, quant=0.12, fundamental=0.05, regime="neutral").reason)
    # stress regime leans on fundamentals
    print(confluence(ta=0.4, quant=0.3, fundamental=-0.6, regime="high_vol").reason)
