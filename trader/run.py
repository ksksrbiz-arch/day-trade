"""
The live paper loop. Two modes, both ending at the same deterministic execution:

  news    : news -> label -> decide -> [features -> groq -> regime -> confirm] -> size -> execute
  scalper : price -> Bollinger snapback -> [OFI gate] -> size -> execute

Execution is Alpaca PAPER. If a bot has use_options on, a confirmed signal buys 1
near-the-money long option (call for bullish, put for bearish -- defined risk)
instead of shares; otherwise it trades equity with bracket TP/SL. The daily
circuit breaker can halt either mode. Guardrails (1:2 RR, breaker) live in risk.py.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timezone

from . import config, news
from .labeler import Labeler
from .labels import Label
from .broker import AlpacaBroker
from .strategy import decide, confirm_intent, size_and_exits, market_regime, Intent
from . import alpha as _alpha
try:
    from . import fundamentals as _fund
except Exception:  # noqa: BLE001
    _fund = None


def _confluence_gate(intent, md, regime, cfg):
    """Multi-method conviction (technical + quant + fundamental). None when off."""
    if not cfg.strategy.use_confluence or md is None:
        return None
    try:
        closes = md.recent_closes(intent.symbol, lookback_days=160)
    except Exception:  # noqa: BLE001
        closes = []
    fscore = None
    if cfg.strategy.use_fundamentals and _fund is not None and "/" not in intent.symbol:
        try:
            f = _fund.get_fundamentals(intent.symbol)
            fscore = f.fundamental_score if f else None
        except Exception:  # noqa: BLE001
            fscore = None
    reg = regime if regime in ("risk_on", "risk_off", "high_vol", "neutral") else "neutral"
    return _alpha.analyze(closes, symbol=intent.symbol, fundamental_score=fscore,
                          regime=reg, min_agree=cfg.strategy.confluence_min_agree,
                          min_composite=cfg.strategy.confluence_min_score)

from . import market_brain
from .watchlist import WatchList
from .marketdata import MarketData, is_crypto
from .massive import MassiveClient
from .context import GroqContext
from .clearstreet import ClearStreetClient
from .omni import OmniClient, research as omni_research, opposes as omni_opposes
from .risk import RiskState, roll_day, circuit_breaker
from .scalper import scalper_signal
from .ofi import confirms as ofi_confirms
from .options import OptionsBroker

FIELDNAMES = [
    "ts", "action", "symbol", "side", "instrument", "notional", "tp", "sl",
    "sentiment", "confidence", "event", "news_src", "feat_src",
    "ret5", "ret20", "vol20", "rvol", "above_sma20", "ofi",
    "regime", "trend_align", "risk_flags", "groq_confirm", "gate_reason", "omni",
    "headline",
]

_last_entry: dict[str, float] = {}
_risk = None


_watch = WatchList()  # day-trader watch->strike state


def _log_row(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", newline="") as f:
            first = f.readline().strip()
        if first and first != ",".join(FIELDNAMES):
            os.replace(path, path + ".v1.bak")
    exists = os.path.exists(path)
    full = {k: row.get(k, "") for k in FIELDNAMES}
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            w.writeheader()
        w.writerow(full)


def _cs_news(cs, seen_path="data/seen_cs.json") -> list[dict]:
    if cs is None:
        return []
    try:
        items = cs.news(limit=25)
    except Exception as e:
        print(f"[clearstreet] news error: {e}")
        return []
    seen = set()
    if os.path.exists(seen_path):
        try:
            seen = set(json.load(open(seen_path)))
        except Exception:
            seen = set()
    fresh = []
    for it in items:
        iid = "cs:" + str(it.get("id", ""))
        if iid in seen or not it.get("title"):
            continue
        seen.add(iid); it = dict(it); it["source"] = "clearstreet"; fresh.append(it)
    if fresh:
        os.makedirs(os.path.dirname(seen_path) or ".", exist_ok=True)
        json.dump(list(seen)[-5000:], open(seen_path, "w"))
    return fresh


def _ctx_fields(features, context, imb=None) -> dict:
    d = {}
    if features is not None:
        d.update({"feat_src": features.source, "ret5": features.ret_5d,
                  "ret20": features.ret_20d, "vol20": features.vol_20d,
                  "rvol": features.rvol, "above_sma20": int(features.above_sma20)})
    if context is not None:
        d.update({"regime": context.regime, "trend_align": int(context.trend_alignment),
                  "risk_flags": "|".join(context.risk_flags), "groq_confirm": int(context.confirm)})
    if imb is not None:
        d["ofi"] = imb
    return d


_day_state = {"date": "", "n": 0}


def _day_count() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _day_state["date"] != today:
        _day_state["date"] = today
        _day_state["n"] = 0
    return _day_state["n"]


def _day_inc():
    _day_count()
    _day_state["n"] += 1


def _risk_scale(cfg, conf: float = 0.6, symbol: str | None = None, p_up: float | None = None):
    """Conviction-scaled risk. DOWN-size on no-edge / stress (capital preservation);
    LEAN IN (up-size) only where there's real measured edge + high conviction + a
    favorable tape, dialed by AGGRESSION (0..1.5). The drawdown breaker sits below
    this, so aggression can't blow up the account."""
    scale, why, rg, edge = 1.0, [], "neutral", 0.0
    try:
        from .agents import state as _st
        aggression = float(_st.kv_get("aggression", os.getenv("AGGRESSION", "0.6")))
    except Exception:  # noqa: BLE001
        aggression = float(os.getenv("AGGRESSION", "0.6"))
    aggression = max(0.0, min(1.5, aggression))
    try:
        from .market_brain import cached_regime
        rg = cached_regime("neutral")
        if rg == "high_vol":
            scale *= 0.55; why.append("high_vol x0.55")
        elif rg == "risk_off":
            scale *= 0.7; why.append("risk_off x0.7")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .ml import infer
        edge = float(infer.model_card().get("edge", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        pass
    if edge <= 0.0:
        scale *= 0.3; why.append("no-edge x0.3")
    elif edge < 0.03:
        scale *= 0.6; why.append("thin-edge x0.6")
    # AGGRESSION lean-in: bet bigger on genuine, high-conviction, trend-aligned edge
    if edge > 0.02 and rg in ("risk_on", "neutral") and conf >= 0.65:
        lean = 1.0 + aggression * min(1.0, edge / 0.05) * min(1.0, (conf - 0.6) / 0.3)
        scale *= lean; why.append(f"lean-in x{lean:.2f}")
    # PRINCIPLED SIZING: volatility-target x fractional-Kelly (bounded, under the
    # breaker). Sizes inversely to realized vol and by calibrated edge (p_up from
    # the meta-labeler when available, else conviction).
    if symbol:
        try:
            from . import sizing
            _sm, _sw = sizing.size_multiplier(symbol, conf, p_up=p_up)
            scale *= _sm; why.append(_sw)
        except Exception:  # noqa: BLE001
            pass
    cap = 1.0 + aggression * 1.6
    return max(0.1, min(cap, round(scale, 3))), ", ".join(why) or "full size"


def _execute(intent: Intent, broker, optbroker, cfg, conf: float = 0.6, p_up: float | None = None):
    """Route a confirmed Intent to options (1 ATM contract) or equity bracket.
    Returns (order_id, instrument, exec_symbol, note)."""
    _instr = "option" if (cfg.strategy.use_options and optbroker is not None) else ("crypto" if is_crypto(intent.symbol) else "equity")
    from .safety import lock_active, lock_reason
    if lock_active():
        return None, _instr, intent.symbol, "SAFETY-LOCK: " + lock_reason()[:90]
    from .policy import evaluate as _pol_eval
    _open = broker.open_symbols()
    _v = _pol_eval({"symbol": intent.symbol, "side": intent.side, "notional": intent.notional, "instrument": _instr},
                   {"open_symbols": _open, "n_positions": len(_open), "day_trades": _day_count(),
                    "confidence": conf, "paper": True, "universe": cfg.strategy.universe})
    if _v["decision"] != "approve":
        return None, _instr, intent.symbol, "POLICY-" + _v["decision"] + ": " + "; ".join(_v["reasons"])[:110]
    _day_inc()
    _sc, _why = _risk_scale(cfg, conf, getattr(intent, 'symbol', None), p_up)
    if _sc < 1.0:
        try:
            intent.notional = round(float(intent.notional) * _sc, 2)
        except Exception:
            pass
    if cfg.strategy.use_options and optbroker is not None:
        contract, spot = optbroker.choose(intent.symbol, intent.side)
        if contract is None:
            return None, "option", intent.symbol, "no contract found"
        oid = optbroker.buy(contract.symbol, qty=1)
        return oid, "option", contract.symbol, f"ATM {('call' if intent.side=='buy' else 'put')} @~{spot}"
    oid = broker.submit(intent)
    kind = "crypto" if is_crypto(intent.symbol) else "equity"
    return oid, kind, intent.symbol, f"{kind} order"


def _log_episode(exec_sym, side, price, regime=None):
    """Record an executed trade into episodic memory so EVERY real position --
    equity, crypto, scalper, beta floor -- feeds the belief-utility learning
    loop, not just the news/daytrader path. Best-effort + self-contained."""
    if not exec_sym or not price:
        return
    try:
        from . import episodes as _ep, psyche as _psy, beliefs as _bel, market_brain as _mb
        reg = regime or _mb.cached_regime("neutral")
        _ps = _psy.state()
        _ep.log(exec_sym, side, price, regime=reg,
                mood=_ps.get("mood", ""), valence=_ps.get("valence", 0.0),
                curiosity=_ps.get("curiosity", 0.0),
                active_beliefs=[b["id"] for b in _bel.active(reg)][:6])
    except Exception:  # noqa: BLE001
        pass


def _breaker_ok(cfg, broker) -> bool:
    """Returns False if the daily circuit breaker is active (halt trading)."""
    global _risk
    if cfg.strategy.daily_max_dd <= 0:
        return True
    eq = broker.account_value()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _risk is None:
        _risk = RiskState(day_start_equity=eq, day=today)
    _risk = roll_day(_risk, eq, today)
    tripped, dd = circuit_breaker(eq, _risk, cfg.strategy.daily_max_dd)
    if tripped and not _risk.tripped:
        n = broker.cancel_all_orders()
        _risk.tripped = True
        print(f"[CIRCUIT BREAKER] daily dd {dd}% -- cancelled {n} orders; halting until next day")
    if _risk.tripped:
        print(f"[halted] breaker active (dd {dd}%).")
        return False
    return True


def run_news(cfg, labeler, broker, md, groq, cs, optbroker, omni=None) -> int:
    if not _breaker_ok(cfg, broker):
        return 0
    items = _cs_news(cs) + news.fetch(cfg.feeds, cfg.seen_path)
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {len(items)} new items")
    regime = "neutral"
    if md is not None:
        try:
            regime = market_brain.cached_regime() or market_regime(md.features("SPY"))
        except Exception:
            pass
    open_syms = broker.open_symbols()
    acted = 0
    for item in items:
        label = labeler.label(item)
        if label is None:
            continue
        intent = decide(label, cfg.strategy, open_syms)
        ts = datetime.now(timezone.utc).isoformat()
        base = {"ts": ts, "sentiment": label.sentiment, "confidence": label.confidence,
                "event": label.event_type, "news_src": item.get("source", ""),
                "regime": regime, "headline": item.get("title", "")[:120]}
        if intent is None:
            _log_row(cfg.trade_log, {**base, "action": "skip",
                                     "symbol": ",".join(label.tickers), "side": ""})
            continue
        cd = cfg.strategy.cooldown_min
        if cd > 0 and intent.symbol in _last_entry and (time.time() - _last_entry[intent.symbol]) < cd * 60:
            _log_row(cfg.trade_log, {**base, "action": "skip_cooldown", "symbol": intent.symbol,
                                     "side": intent.side, "gate_reason": f"cooldown {cd}m"})
            continue
        features = md.features(intent.symbol) if md is not None else None
        context = groq.enrich(intent.side, label, features) if groq is not None else None
        ok, reason = confirm_intent(intent, features, context, cfg.strategy, regime)
        imb = md.ofi(intent.symbol) if (md is not None and cfg.strategy.use_ofi) else None
        if ok and imb is not None and not ofi_confirms(intent.side, imb):
            ok, reason = False, f"ofi opposes ({imb:+.2f})"
        ctx = _ctx_fields(features, context, imb)
        if not ok:
            _log_row(cfg.trade_log, {**base, **ctx, "action": "skip_unconfirmed",
                                     "symbol": intent.symbol, "side": intent.side, "gate_reason": reason})
            continue
        omni_view = ""
        if omni is not None and label.confidence <= cfg.strategy.min_confidence + cfg.strategy.omni_borderline:
            rr = omni_research(omni, intent.symbol, intent.side)
            omni_view = rr.get("stance", "")
            if cfg.strategy.omni_gate and omni_opposes(intent.side, omni_view):
                _log_row(cfg.trade_log, {**base, **ctx, "action": "skip_omni",
                                         "symbol": intent.symbol, "side": intent.side,
                                         "omni": omni_view, "gate_reason": f"omni {omni_view} opposes {intent.side}"})
                continue
        conv = _confluence_gate(intent, md, regime, cfg)
        if conv is not None and (conv.side != intent.side or not conv.gate_pass):
            _log_row(cfg.trade_log, {**base, **ctx, "action": "skip_confluence",
                                     "symbol": intent.symbol, "side": intent.side,
                                     "confluence": conv.composite, "gate_reason": conv.reason})
            continue
        intent = size_and_exits(intent, label, features, cfg.strategy)
        _pm = market_brain.cached_posture("equity").get("size_mult", 1.0)
        intent.notional = round(intent.notional * max(0.3, min(2.0, _pm)), 2)
        if conv is not None and cfg.strategy.confluence_size and conv.size_mult > 0:
            intent.notional = round(intent.notional * conv.size_mult, 2)
        _pup = None                              # meta-labeler P(winner) -> Kelly sizing
        try:
            from .calibrate import p_correct as _pc
            _pup = _pc(conv.scores) if conv is not None else None
        except Exception:  # noqa: BLE001
            _pup = None
        oid, instrument, exec_sym, note = _execute(intent, broker, optbroker, cfg, p_up=_pup)
        if oid:                                  # EPISODIC MEMORY: record the decision + its state
            _log_episode(exec_sym, intent.side, broker.last_price(intent.symbol), regime)
        open_syms.add(intent.symbol)
        _last_entry[intent.symbol] = time.time()
        acted += 1
        _log_row(cfg.trade_log, {**base, **ctx, "action": "order" if oid else "order_failed",
                                 "symbol": exec_sym, "side": intent.side, "instrument": instrument,
                                 "notional": round(intent.notional, 2), "tp": intent.take_profit_pct,
                                 "sl": intent.stop_loss_pct, "omni": omni_view, "gate_reason": f"{reason} | {note}"})
    return acted


def run_scalper(cfg, broker, md, optbroker) -> int:
    if not _breaker_ok(cfg, broker):
        return 0
    uni = list(cfg.strategy.scalper_universe)
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] scalper scan {len(uni)} symbols")
    open_syms = broker.open_symbols()
    acted = 0
    for sym in uni:
        if sym in open_syms:
            continue
        closes = md.recent_closes(sym)
        sig = scalper_signal(closes, cfg.strategy.scalper_window, cfg.strategy.scalper_k)
        ts = datetime.now(timezone.utc).isoformat()
        if not sig or (sig == "sell" and not cfg.strategy.allow_short):
            continue
        imb = md.ofi(sym)
        if imb is not None and abs(imb) >= cfg.strategy.liq_extreme:
            _log_row(cfg.trade_log, {"ts": ts, "action": "skip_liquidity", "symbol": sym,
                                     "side": sig, "event": "scalp", "ofi": imb,
                                     "gate_reason": f"liquidity gate: thin/one-sided book ofi={imb:+.2f}, waiting"})
            continue
        if cfg.strategy.use_ofi and imb is not None and not ofi_confirms(sig, imb):
            _log_row(cfg.trade_log, {"ts": ts, "action": "skip_unconfirmed", "symbol": sym,
                                     "side": sig, "event": "scalp", "ofi": imb,
                                     "gate_reason": f"ofi opposes ({imb:+.2f})"})
            continue
        cd = cfg.strategy.cooldown_min
        if cd > 0 and sym in _last_entry and (time.time() - _last_entry[sym]) < cd * 60:
            continue
        synth = Label(tickers=[sym], sentiment=(0.6 if sig == "buy" else -0.6),
                      confidence=0.6, event_type="scalp")
        features = md.features(sym)
        intent = Intent(symbol=sym, side=sig, notional=cfg.strategy.notional_per_trade,
                        take_profit_pct=cfg.strategy.take_profit_pct,
                        stop_loss_pct=cfg.strategy.stop_loss_pct, reason="bollinger snapback")
        intent = size_and_exits(intent, synth, features, cfg.strategy)
        _asset = "crypto" if is_crypto(sym) else "equity"
        _pm = market_brain.cached_posture(_asset).get("size_mult", 1.0)
        intent.notional = round(intent.notional * max(0.3, min(2.0, _pm)), 2)
        oid, instrument, exec_sym, note = _execute(intent, broker, optbroker, cfg)
        if oid:                                  # EPISODIC MEMORY: scalper trades count too
            _log_episode(exec_sym, sig, broker.last_price(sym))
        open_syms.add(sym)
        _last_entry[sym] = time.time()
        acted += 1
        _log_row(cfg.trade_log, {"ts": ts, "action": "order" if oid else "order_failed",
                                 "symbol": exec_sym, "side": sig, "event": "scalp",
                                 "instrument": instrument, "notional": round(intent.notional, 2),
                                 "tp": intent.take_profit_pct, "sl": intent.stop_loss_pct,
                                 "ofi": imb if imb is not None else "",
                                 **_ctx_fields(features, None), "gate_reason": note})
    return acted


def run_daytrader(cfg, labeler, broker, md, groq, optbroker, omni=None) -> int:
    """Watch -> wait -> strike. News ARMS watches; the bot only STRIKES when price
    confirms the thesis (breakout/breakdown), else the watch expires."""
    if not _breaker_ok(cfg, broker):
        return 0
    regime = market_brain.cached_regime() or "neutral"
    _watch.reload()          # see watches armed by other processes (autonomy scanner)
    # 1) discovery: news arms watches (no trade yet)
    items = news.fetch(cfg.feeds, cfg.seen_path)
    armed = 0
    for item in items:
        label = labeler.label(item)
        if label is None:
            continue
        intent = decide(label, cfg.strategy, set())
        if intent is None:
            continue
        price = broker.last_price(intent.symbol)
        if price is None:
            continue
        _watch.arm(intent.symbol, intent.side, price, item.get("title", ""),
                   buffer=cfg.strategy.watch_buffer, expiry_min=cfg.strategy.watch_expiry_min,
                   confidence=label.confidence, source=item.get("source", ""), sentiment=label.sentiment)
        armed += 1
        _log_row(cfg.trade_log, {"ts": datetime.now(timezone.utc).isoformat(), "action": "watch_armed",
                                 "symbol": intent.symbol, "side": intent.side, "sentiment": label.sentiment,
                                 "confidence": label.confidence, "event": label.event_type,
                                 "regime": regime, "headline": item.get("title", "")[:120]})
    # also arm SYSTEMATIC candidates each cycle: momentum scanner + cross-sectional
    # factor ranking. They become price-confirmed strikes through the same gate.
    try:
        from . import scanner as _scan, factors as _fac
        _scan.arm_top(n=5, min_conf=0.66, wl=_watch)
        _fac.arm_top(n=3, wl=_watch, allow_short=cfg.strategy.allow_short)
    except Exception as _ae:  # noqa: BLE001
        print(f"[daytrader] systematic arm skipped: {_ae}")
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] daytrader: {len(items)} news, {armed} armed, {len(_watch.active())} watching")
    # 2) monitor: strike on confirmation
    open_syms = broker.open_symbols()
    acted = 0
    for e in list(_watch.active()):
        sym = e["symbol"]
        cur = broker.last_price(sym)
        res = _watch.evaluate(sym, cur)
        ts = datetime.now(timezone.utc).isoformat()
        if res == "expired":
            _log_row(cfg.trade_log, {"ts": ts, "action": "watch_expired", "symbol": sym,
                                     "side": e["thesis"], "regime": regime, "headline": e.get("catalyst", "")[:120]})
            continue
        if res != "fire" or sym in open_syms:
            continue
        synth = Label(tickers=[sym],
                      sentiment=e.get("sentiment", 0.0) or (0.5 if e["thesis"] == "buy" else -0.5),
                      confidence=e.get("confidence", 0.6) or 0.6, event_type="daytrade")
        _side = "sell" if e["thesis"] == "short" else e["thesis"]   # broker-side (legacy watches)
        intent = Intent(symbol=sym, side=_side, notional=cfg.strategy.notional_per_trade,
                        take_profit_pct=cfg.strategy.take_profit_pct, stop_loss_pct=cfg.strategy.stop_loss_pct,
                        reason="confirmed breakout: " + e.get("catalyst", "")[:50])
        features = md.features(sym) if md is not None else None
        context = groq.enrich(intent.side, synth, features) if groq is not None else None
        ok, reason = confirm_intent(intent, features, context, cfg.strategy, regime)
        if not ok:
            _log_row(cfg.trade_log, {"ts": ts, "action": "skip_unconfirmed", "symbol": sym,
                                     "side": e["thesis"], "regime": regime, "gate_reason": reason,
                                     "headline": e.get("catalyst", "")[:120]})
            continue
        conv = _confluence_gate(intent, md, regime, cfg)
        if conv is not None and (conv.side != intent.side or not conv.gate_pass):
            _log_row(cfg.trade_log, {"ts": ts, "action": "skip_confluence", "symbol": sym,
                                     "side": e["thesis"], "regime": regime,
                                     "confluence": conv.composite, "gate_reason": conv.reason})
            continue
        intent = size_and_exits(intent, synth, features, cfg.strategy)
        _pm = market_brain.cached_posture("equity").get("size_mult", 1.0)
        intent.notional = round(intent.notional * max(0.3, min(2.0, _pm)), 2)
        if conv is not None and cfg.strategy.confluence_size and conv.size_mult > 0:
            intent.notional = round(intent.notional * conv.size_mult, 2)
        _pup = None                              # meta-labeler P(winner) -> Kelly sizing
        try:
            from .calibrate import p_correct as _pc
            _pup = _pc(conv.scores) if conv is not None else None
        except Exception:  # noqa: BLE001
            _pup = None
        oid, instrument, exec_sym, note = _execute(intent, broker, optbroker, cfg, p_up=_pup)
        if oid:                                  # EPISODIC MEMORY: record the decision + its state
            _log_episode(exec_sym, intent.side, broker.last_price(intent.symbol), regime)
        open_syms.add(sym)
        _last_entry[sym] = time.time()
        acted += 1
        _log_row(cfg.trade_log, {"ts": ts, "action": "order" if oid else "order_failed",
                                 "symbol": exec_sym, "side": e["thesis"], "instrument": instrument,
                                 "notional": round(intent.notional, 2), "tp": intent.take_profit_pct,
                                 "sl": intent.stop_loss_pct, "event": "daytrade", "regime": regime,
                                 "gate_reason": "STRIKE " + reason, "headline": e.get("catalyst", "")[:120]})
    return acted


def main() -> None:
    cfg = config.load()
    _pf = os.getenv("BOT_PID_FILE")
    if _pf:
        try:
            os.makedirs(os.path.dirname(_pf) or ".", exist_ok=True)
            open(_pf, "w").write(str(os.getpid()))
        except Exception:
            pass

    missing = [n for n, v in [("ALPACA_API_KEY", cfg.alpaca_key),
                              ("ALPACA_SECRET_KEY", cfg.alpaca_secret)] if not v]
    if missing and cfg.strategy.mode == "news":
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")
    if not cfg.alpaca_paper:
        raise SystemExit("ALPACA_PAPER is false. Paper only.")

    broker = AlpacaBroker(cfg.alpaca_key, cfg.alpaca_secret, paper=True)
    mode = cfg.strategy.mode
    labeler = Labeler() if mode in ("news", "daytrader") else None

    md = groq = None
    need_md = cfg.strategy.require_confirmation or mode in ("scalper", "daytrader") or cfg.strategy.use_ofi
    if need_md:
        massive = MassiveClient(cfg.massive_access, cfg.massive_secret, cfg.massive_endpoint, cfg.massive_bucket)
        md = MarketData(cfg.alpaca_key, cfg.alpaca_secret, massive=massive)
    if cfg.strategy.require_confirmation:
        groq = GroqContext(cfg.groq_key, cfg.groq_model)

    cs = None
    if cfg.use_clearstreet and mode == "news":
        cs = ClearStreetClient(cfg.cs_client_id, cfg.cs_client_secret, cfg.cs_audience, cfg.cs_base_url)
        if not cs.can_auth():
            cs = None

    optbroker = OptionsBroker(cfg.alpaca_key, cfg.alpaca_secret) if cfg.strategy.use_options else None
    omni = None
    if cfg.strategy.use_omni and cfg.clearstreet_token and cfg.cs_account_id:
        omni = OmniClient(cfg.clearstreet_token, cfg.cs_account_id)
        print(f"Omni research: ON (gate={cfg.strategy.omni_gate}, borderline band={cfg.strategy.omni_borderline})")

    print(f"MODE={mode} | options={cfg.strategy.use_options} | ofi={cfg.strategy.use_ofi} | "
          f"confirm={cfg.strategy.require_confirmation} | RR>={cfg.strategy.min_rr} | breaker={cfg.strategy.daily_max_dd}%")
    print(f"Paper equity: ${broker.account_value():.2f} | base ${cfg.strategy.notional_per_trade:.0f}/trade | "
          f"short={cfg.strategy.allow_short} | poll {cfg.poll_seconds}s")
    print("Ctrl-C to stop.\n")

    while True:
        try:
            if mode == "scalper":
                run_scalper(cfg, broker, md, optbroker)
            else:
                if mode == "daytrader":
                    run_daytrader(cfg, labeler, broker, md, groq, optbroker, omni)
                else:
                    run_news(cfg, labeler, broker, md, groq, cs, optbroker, omni)
            try:                              # beta-capture floor: track the index when flat
                from . import beta
                beta.rebalance(cfg, broker)
            except Exception:  # noqa: BLE001
                pass
        except KeyboardInterrupt:
            print("\nstopped."); break
        except Exception as e:
            print(f"[loop] error (continuing): {e}")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
