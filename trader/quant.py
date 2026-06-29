"""Quantitative-analysis engine.

Two complementary views, both pure and testable:

  * cross_sectional()  -- rank a whole universe on standard equity factors
        (momentum, low-volatility, short-term reversal, volume trend), z-scored
        across names and blended into one factor score per symbol. This is the
        statistical-edge layer: it exploits *relative* mispricing, which is far
        more robust than any single-name signal.

  * name_stats()       -- single-name statistics (risk-adjusted drift, distance
        from mean in sigmas, trend persistence) for when only one symbol's
        history is available (e.g. the live news loop).

Outputs are mapped to a quant_score in [-1, 1] so they compose with the TA and
fundamental engines in alpha.confluence().
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import fmean, pstdev


def _rets(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]


def zscores(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    xs = list(values.values())
    mu = fmean(xs)
    sd = pstdev(xs) or 1.0
    return {k: (v - mu) / sd for k, v in values.items()}


def _tanh(x: float) -> float:
    # squander-free squash to [-1,1] without importing math.tanh edge issues
    if x > 20:
        return 1.0
    if x < -20:
        return -1.0
    import math
    return math.tanh(x)


# --------------------------------------------------------------------------- #
# single-name statistics                                                        #
# --------------------------------------------------------------------------- #
@dataclass
class NameStats:
    n: int
    sharpe_20: float          # annualized risk-adjusted drift over ~20d
    z_vs_sma20: float         # (price - sma20) / sigma  (mean-reversion gauge)
    persistence: float        # autocorrelation of daily returns (trend memory)
    quant_score: float        # composite in [-1,1]


def name_stats(closes: list[float]) -> NameStats | None:
    if len(closes) < 21:
        return None
    rets = _rets(closes[-21:])
    if len(rets) < 5:
        return None
    mu, sd = fmean(rets), pstdev(rets)
    # floor sigma at a realistic daily level so near-zero-vol synthetic
    # series don't produce absurd Sharpes; clamp to a sane band.
    sd = max(sd, 0.001)
    sharpe = max(-10.0, min(10.0, (mu / sd) * sqrt(252)))
    sma = fmean(closes[-20:])
    csd = pstdev(closes[-20:]) or 1e-9
    z = (closes[-1] - sma) / csd
    # lag-1 autocorrelation of returns -> momentum persistence
    if len(rets) >= 6:
        a = rets[:-1]
        b = rets[1:]
        ma, mb = fmean(a), fmean(b)
        cov = fmean([(x - ma) * (y - mb) for x, y in zip(a, b)])
        va = pstdev(a) or 1e-9
        vb = pstdev(b) or 1e-9
        persistence = cov / (va * vb)
    else:
        persistence = 0.0
    # composite: risk-adjusted drift, tempered by extreme mean-reversion stretch.
    score = _tanh(0.6 * sharpe) - 0.25 * _tanh(z / 2)
    return NameStats(n=len(closes), sharpe_20=round(sharpe, 3),
                     z_vs_sma20=round(z, 3), persistence=round(persistence, 3),
                     quant_score=round(max(-1.0, min(1.0, score)), 3))


# --------------------------------------------------------------------------- #
# cross-sectional factor model                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class CrossSectional:
    scores: dict[str, float]          # symbol -> blended factor score in [-1,1]
    ranks: list[tuple[str, float]]    # sorted best -> worst
    factors: dict[str, dict[str, float]]   # symbol -> raw factor values


def cross_sectional(panel: dict[str, list[float]],
                    volumes: dict[str, list[float]] | None = None,
                    weights: dict[str, float] | None = None) -> CrossSectional:
    """panel: {symbol: closes(oldest->newest)}. Returns blended factor scores.

    Factors (each z-scored across the universe, then summed with weights):
      momentum   : 12-1 month return  (skip last 21d to avoid reversal)
      low_vol    : NEGATIVE realized vol (calmer names score higher)
      reversal   : NEGATIVE last-5d return (short-term mean reversion)
      vol_trend  : recent vs older average volume (participation)
    """
    w = {"momentum": 1.0, "low_vol": 0.5, "reversal": 0.5, "vol_trend": 0.3}
    if weights:
        w.update(weights)

    mom, lowvol, rev, vtr, raw = {}, {}, {}, {}, {}
    for s, c in panel.items():
        if len(c) < 30:
            continue
        # 12-1 momentum: total return excluding the most recent ~21 days
        if len(c) >= 252:
            base, recent = c[-252], c[-21]
        else:
            base, recent = c[0], c[-21] if len(c) > 21 else c[-1]
        mom[s] = (recent / base - 1) if base else 0.0
        rr = _rets(c[-21:])
        lowvol[s] = -(pstdev(rr) if len(rr) >= 2 else 0.0)
        rev[s] = -(c[-1] / c[-6] - 1) if len(c) > 6 and c[-6] else 0.0
        if volumes and s in volumes and len(volumes[s]) >= 20:
            v = volumes[s]
            recent_v = fmean(v[-5:])
            older_v = fmean(v[-20:-5]) or 1e-9
            vtr[s] = recent_v / older_v - 1
        else:
            vtr[s] = 0.0
        raw[s] = {"momentum": round(mom[s], 4), "low_vol": round(lowvol[s], 4),
                  "reversal": round(rev[s], 4), "vol_trend": round(vtr[s], 4)}

    zm, zl, zr, zv = zscores(mom), zscores(lowvol), zscores(rev), zscores(vtr)
    out: dict[str, float] = {}
    for s in raw:
        blended = (w["momentum"] * zm.get(s, 0) + w["low_vol"] * zl.get(s, 0) +
                   w["reversal"] * zr.get(s, 0) + w["vol_trend"] * zv.get(s, 0))
        out[s] = round(_tanh(blended / 2), 3)   # squash composite z to [-1,1]
    ranks = sorted(out.items(), key=lambda kv: kv[1], reverse=True)
    return CrossSectional(scores=out, ranks=ranks, factors=raw)


if __name__ == "__main__":
    # synthetic universe: a strong trender, a calm drifter, a wild name
    panel = {
        "TREND": [100 * (1.008 ** i) for i in range(260)],
        "CALM":  [100 + 0.05 * i for i in range(260)],
        "WILD":  [100 + (8 if i % 2 else -8) + 0.2 * i for i in range(260)],
        "FADER": [100 * (0.997 ** i) for i in range(260)],
    }
    cs = cross_sectional(panel)
    print("cross-sectional ranks:")
    for s, sc in cs.ranks:
        print(f"  {s:6s} {sc:+.3f}  {cs.factors[s]}")
    print("\nname_stats(TREND):", name_stats(panel["TREND"]))
    print("name_stats(FADER):", name_stats(panel["FADER"]))
