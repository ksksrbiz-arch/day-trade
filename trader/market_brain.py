"""
The Market Brain -- a cross-asset UNDERSTANDING layer.

Instead of staring at one chart, it reads the whole tape together: equities
(SPY/QQQ/IWM), bonds (TLT), gold (GLD), the dollar (UUP), credit (HYG), realized
volatility, market breadth, and crypto (BTC/ETH), plus how they relate
(BTC-vs-stocks correlation, equities-vs-gold, credit risk appetite). From those
deterministic features it classifies the REGIME the market is in -- risk_on,
risk_off, neutral, or high_vol/crisis -- which is the single most useful thing a
small systematic account can know (when NOT to push).

A council model then narrates the numbers into a plain-English read. The regime
is written to data/market_state.json so the live bots can trade differently
depending on the world they're in.

Feature math is pure + tested. Data is free (Tiingo ETFs + Binance crypto;
FreeCryptoAPI optional for crypto breadth/RSI).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev

PROJ = Path(__file__).resolve().parent.parent
STATE_PATH = PROJ / "data" / "market_state.json"

ETFS = ["SPY", "QQQ", "IWM", "TLT", "GLD", "UUP", "HYG"]
CRYPTO = ["BTC/USD", "ETH/USD"]


# ---- pure feature helpers ----

def _sma(c, n):
    return fmean(c[-n:]) if len(c) >= n else (fmean(c) if c else 0.0)


def _mom(c, n):
    if len(c) <= n or c[-n - 1] == 0:
        return 0.0
    return c[-1] / c[-n - 1] - 1.0


def trend_score(c) -> int:
    """-2..+2 trend: above/below 50dma and 200dma + 20d momentum sign."""
    if len(c) < 60:
        return 0
    s = 0
    s += 1 if c[-1] > _sma(c, 50) else -1
    if len(c) >= 200:
        s += 1 if c[-1] > _sma(c, 200) else -1
    s += 1 if _mom(c, 20) > 0 else -1
    return max(-2, min(2, s))


def realized_vol(c, n=20):
    w = c[-n - 1:]
    rets = [w[i] / w[i - 1] - 1 for i in range(1, len(w)) if w[i - 1]]
    return pstdev(rets) * math.sqrt(252) if len(rets) >= 2 else 0.0


def vol_percentile(c, n=20, lookback=252):
    """Where current 20d vol sits vs the past year (0..1)."""
    if len(c) < n + 30:
        return 0.5
    series = []
    for i in range(max(n + 1, len(c) - lookback), len(c)):
        w = c[i - n:i + 1]
        rets = [w[j] / w[j - 1] - 1 for j in range(1, len(w)) if w[j - 1]]
        if len(rets) >= 2:
            series.append(pstdev(rets))
    if not series:
        return 0.5
    cur = series[-1]
    return round(sum(1 for x in series if x <= cur) / len(series), 2)


def correlation(a, b, n=30):
    a, b = a[-n:], b[-n:]
    m = min(len(a), len(b))
    if m < 5:
        return 0.0
    a, b = a[-m:], b[-m:]
    ra = [a[i] / a[i - 1] - 1 for i in range(1, m) if a[i - 1]]
    rb = [b[i] / b[i - 1] - 1 for i in range(1, m) if b[i - 1]]
    k = min(len(ra), len(rb))
    if k < 5:
        return 0.0
    ra, rb = ra[-k:], rb[-k:]
    ma, mb = fmean(ra), fmean(rb)
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(k)) / k
    sa = pstdev(ra); sb = pstdev(rb)
    return round(cov / (sa * sb), 2) if sa > 0 and sb > 0 else 0.0


def classify(f: dict) -> str:
    """Deterministic regime from the feature dict. Pure."""
    volp = f.get("spy_vol_pct", 0.5)
    if volp >= 0.85:
        return "high_vol"
    eq = f.get("equity_trend", 0)
    risk = f.get("risk_appetite", 0)
    breadth = f.get("breadth", 0.5)
    if eq >= 1 and risk >= 1 and breadth >= 0.55:
        return "risk_on"
    if eq <= -1 and risk <= 0:
        return "risk_off"
    return "neutral"


def crypto_classify(f: dict) -> str:
    """Crypto-specific regime (crypto often decouples from equities)."""
    bt = f.get("btc_trend", 0)
    mom = f.get("btc_mom_20", 0.0)
    cb = f.get("crypto_breadth_24h")
    score = 0
    score += 1 if bt > 0 else (-1 if bt < 0 else 0)
    score += 1 if mom > 0 else -1
    if cb is not None:
        score += 1 if cb >= 0.6 else (-1 if cb <= 0.4 else 0)
    return "risk_on" if score >= 2 else ("risk_off" if score <= -2 else "neutral")


def regime_confidence(f: dict) -> float:
    """0..1 -- how aligned the cross-asset signals are with the equity regime.
    High confidence = the tape agrees with itself."""
    reg = f.get("regime", "neutral")
    signals = [f.get("spy_trend", 0), f.get("qqq_trend", 0), f.get("iwm_trend", 0),
               f.get("hyg_trend", 0), -f.get("uup_trend", 0)]  # weak dollar = risk-on
    if reg == "risk_on":
        agree = sum(1 for s in signals if s > 0)
    elif reg == "risk_off":
        agree = sum(1 for s in signals if s < 0)
    else:
        return 0.5
    base = agree / len(signals)
    if f.get("spy_vol_pct", 0.5) >= 0.85:   # high vol erodes confidence in directional calls
        base *= 0.6
    return round(base, 2)


def posture(regime: str, confidence: float) -> dict:
    """Actionable stance the bots apply: directional bias + bounded size multiplier."""
    conf = max(0.0, min(1.0, confidence))
    if regime == "high_vol":
        return {"bias": "neutral", "size_mult": 0.5, "note": "high vol -> half size / stand down"}
    if regime == "risk_on":
        return {"bias": "long", "size_mult": round(1.0 + 0.4 * conf, 2),
                "note": "risk-on -> favor longs, scale with confidence"}
    if regime == "risk_off":
        return {"bias": "short", "size_mult": round(0.7 + 0.1 * conf, 2),
                "note": "risk-off -> defensive, smaller size"}
    return {"bias": "neutral", "size_mult": 1.0, "note": "neutral -> baseline size"}


def build_features(prices: dict, crypto: dict) -> dict:
    spy = prices.get("SPY", [])
    f = {}
    f["spy_trend"] = trend_score(spy)
    f["qqq_trend"] = trend_score(prices.get("QQQ", []))
    f["iwm_trend"] = trend_score(prices.get("IWM", []))
    f["tlt_trend"] = trend_score(prices.get("TLT", []))
    f["gld_trend"] = trend_score(prices.get("GLD", []))
    f["uup_trend"] = trend_score(prices.get("UUP", []))
    f["hyg_trend"] = trend_score(prices.get("HYG", []))
    f["equity_trend"] = round((f["spy_trend"] + f["qqq_trend"] + f["iwm_trend"]) / 3, 2)
    f["spy_vol_pct"] = vol_percentile(spy)
    f["spy_vol_ann"] = round(realized_vol(spy) * 100, 1)
    # breadth: fraction of tracked assets above their 50dma
    above = []
    for s, c in prices.items():
        if len(c) >= 50:
            above.append(1 if c[-1] > _sma(c, 50) else 0)
    f["breadth"] = round(sum(above) / len(above), 2) if above else 0.5
    # risk appetite composite
    ra = 0
    ra += 1 if f["hyg_trend"] > 0 else -1            # credit risk-on
    ra += 1 if f["iwm_trend"] >= f["spy_trend"] else -1  # small caps leading
    ra += 1 if f["uup_trend"] < 0 else -1            # weak dollar = risk-on
    ra += 1 if f["spy_trend"] > f["gld_trend"] else -1   # stocks over gold
    f["risk_appetite"] = ra
    # crypto
    btc = crypto.get("BTC/USD", [])
    f["btc_trend"] = trend_score(btc)
    f["btc_mom_20"] = round(_mom(btc, 20) * 100, 1)
    f["btc_spy_corr30"] = correlation(btc, spy, 30)
    f["regime"] = classify(f)
    f["crypto_regime"] = crypto_classify(f)
    f["regime_confidence"] = regime_confidence(f)
    f["posture"] = posture(f["regime"], f["regime_confidence"])
    f["crypto_posture"] = posture(f["crypto_regime"], f["regime_confidence"])
    return f


def compute(cfg) -> dict:
    from trader import history
    from trader.freecryptoapi import FreeCryptoClient
    try:
        epanel = history.load_panel(ETFS, days=320, source="tiingo", tiingo_token=cfg.tiingo_token)
        eprices = epanel["prices"]
    except Exception as e:
        eprices = {}
    try:
        cpanel = history.load_panel(CRYPTO, days=320, source="binance")
        cprices = cpanel["prices"]
    except Exception:
        cprices = {}
    f = build_features(eprices, cprices)
    # optional FreeCryptoAPI crypto breadth/RSI
    fc = FreeCryptoClient(getattr(cfg, "freecrypto_key", ""))
    if fc.enabled:
        data = fc.get_data(["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX"])
        if data:
            rsis = [v["rsi"] for v in data.values() if v.get("rsi") is not None]
            ups = [1 for v in data.values() if (v.get("change_24h") or 0) > 0]
            f["crypto_breadth_24h"] = round(len(ups) / len(data), 2) if data else None
            f["crypto_avg_rsi"] = round(fmean(rsis), 1) if rsis else None
            f["freecryptoapi"] = "live"
    f["ts"] = datetime.now(timezone.utc).isoformat()
    return f


def narrate(cfg, f: dict, ltm_context: str = "") -> str:
    """One free-model read of the numeric state (advisory)."""
    from trader import council
    summary = json.dumps({k: v for k, v in f.items() if k != "ts"})
    prompt = ("You are a macro strategist. In 3-4 sentences, explain in plain English what "
              "regime the market is in and what it implies for a small systematic trader "
              "(when to lean in vs sit out). Be concrete and skeptical; use the numbers.\n\n"
              f"STATE: {summary}\n\nRELEVANT PAST (long-term memory, may be empty): {ltm_context[:800]}")
    for fn in ("anthropic", "groq", "openrouter", "cohere"):
        try:
            return council._member_text(cfg, fn, prompt)[:700]
        except Exception:
            continue
    return "(no model available)"


def refresh(cfg, with_narrative: bool = True) -> dict:
    from trader.pieces_ltm import PiecesLTM
    _prev = cached_regime("")
    f = compute(cfg)
    if _prev and _prev != f.get("regime"):
        f["regime_changed_from"] = _prev
    _ltm = PiecesLTM(getattr(cfg, "pieces_url", ""), getattr(cfg, "pieces_enabled", True))
    _recall = ""
    if _ltm.enabled:
        try:
            _recall = _ltm.ask(f"How have past {f.get('regime')} regimes played out for short-term systematic trading? dollar {f.get('uup_trend')} gold {f.get('gld_trend')} volpct {f.get('spy_vol_pct')}.", ["market regime", str(f.get('regime','')), "volatility"])
        except Exception:
            _recall = ""
    if _recall:
        f["ltm_recall"] = _recall[:700]
    if with_narrative:
        try:
            f["narrative"] = narrate(cfg, f, _recall)
        except Exception as e:
            f["narrative"] = f"(narrate error: {e})"
    if _ltm.enabled:
        try:
            _day = f.get("ts", "")[:10]
            _body = "# Market Brain read\n" + json.dumps({k: v for k, v in f.items() if k not in ("narrative", "ltm_recall")}, indent=2) + "\n\n## Narrative\n" + str(f.get("narrative", ""))
            _ltm.remember(f"Market regime {f.get('regime')} on {_day}", _body, dedup_key=f"regime-{_day}-{f.get('regime')}")
        except Exception:
            pass
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(f, indent=2))
    return f


def cached_posture(asset: str = "equity") -> dict:
    try:
        s = json.loads(STATE_PATH.read_text())
        return s.get("crypto_posture" if asset == "crypto" else "posture",
                     {"size_mult": 1.0, "bias": "neutral"})
    except Exception:
        return {"size_mult": 1.0, "bias": "neutral"}


def cached_regime(default: str = "neutral") -> str:
    try:
        return json.loads(STATE_PATH.read_text()).get("regime", default)
    except Exception:
        return default
