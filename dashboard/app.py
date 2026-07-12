"""
FastAPI backend for the paper-trader control center.

Serves a single-page dashboard + a JSON API that wraps the trader package:
account/positions, portfolio vs SPY, price charts, live news + signals, and
multi-bot launch/stop. Execution is Alpaca PAPER only -- no real-money path.

Run:  python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from trader import config, news as news_mod
from trader.clearstreet import ClearStreetClient
from dashboard import bots as botmgr
from dashboard import dash_metrics as M

PROJ = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="paper-trader control center")
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
cfg = config.load()

_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    now = time.time()
    if key in _cache and now - _cache[key][0] < ttl:
        return _cache[key][1]
    val = fn()
    _cache[key] = (now, val)
    return val


# --- lazy Alpaca clients ---
_trading = None
_data = None


def trading():
    global _trading
    if _trading is None:
        from alpaca.trading.client import TradingClient
        _trading = TradingClient(cfg.alpaca_key, cfg.alpaca_secret, paper=True)
    return _trading


def data_client():
    global _data
    if _data is None:
        from alpaca.data.historical import StockHistoricalDataClient
        _data = StockHistoricalDataClient(cfg.alpaca_key, cfg.alpaca_secret)
    return _data


@app.get("/")
def root():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/api/account")
def account():
    try:
        a = trading().get_account()
        pos = trading().get_all_positions()
        positions = [{
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": getattr(p.side, "value", str(p.side)),
            "market_value": float(p.market_value),
            "avg_entry": float(p.avg_entry_price),
            "current": float(p.current_price) if p.current_price else None,
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": round(float(p.unrealized_plpc) * 100, 2),
        } for p in pos]
        return {
            "equity": float(a.equity),
            "last_equity": float(a.last_equity),
            "cash": float(a.cash),
            "buying_power": float(a.buying_power),
            "portfolio_value": float(a.portfolio_value),
            "day_pl": round(float(a.equity) - float(a.last_equity), 2),
            "day_pl_pct": round((float(a.equity) / float(a.last_equity) - 1) * 100, 3) if float(a.last_equity) else 0,
            "positions": positions,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


def _spy_curve(days: int):
    from datetime import datetime, timedelta, timezone
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    start = datetime.now(timezone.utc) - timedelta(days=days + 5)
    req = StockBarsRequest(symbol_or_symbols="SPY", timeframe=TimeFrame.Day,
                           start=start, feed=DataFeed.IEX)
    bars = data_client().get_stock_bars(req).data.get("SPY", [])
    t = [b.timestamp.strftime("%Y-%m-%d") for b in bars]
    c = [float(b.close) for b in bars]
    pct = [round((x / c[0] - 1) * 100, 3) for x in c] if c else []
    return t, pct


@app.get("/api/portfolio_history")
def portfolio_history(days: int = 30):
    out = {"t": [], "equity": [], "spy_pct": [], "equity_pct": []}
    # SPY benchmark
    try:
        t, pct = cached(f"spy{days}", 300, lambda: _spy_curve(days))
        out["t"], out["spy_pct"] = t, pct
    except Exception as e:
        out["spy_error"] = str(e)
    # account equity curve
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        ph = trading().get_portfolio_history(
            GetPortfolioHistoryRequest(period=f"{days}D", timeframe="1D"))
        eq = [float(x) for x in ph.equity if x is not None]
        out["equity"] = eq
        if eq:
            out["equity_pct"] = [round((x / eq[0] - 1) * 100, 3) for x in eq]
    except Exception:
        # fallback: flat line at current equity
        try:
            a = trading().get_account()
            out["equity"] = [float(a.last_equity), float(a.equity)]
            base = float(a.last_equity) or float(a.equity)
            out["equity_pct"] = [0.0, round((float(a.equity) / base - 1) * 100, 3)] if base else [0, 0]
        except Exception as e:
            out["equity_error"] = str(e)
    return out


@app.get("/api/bars")
def bars(symbol: str = "AAPL", days: int = 120):
    try:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
        symbol = symbol.upper()
        start = datetime.now(timezone.utc) - timedelta(days=days + 10)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                               start=start, feed=DataFeed.IEX)
        bars = cached(f"bars{symbol}{days}", 120,
                      lambda: data_client().get_stock_bars(req).data.get(symbol, []))
        return {"symbol": symbol,
                "t": [b.timestamp.strftime("%Y-%m-%d") for b in bars],
                "o": [float(b.open) for b in bars],
                "h": [float(b.high) for b in bars],
                "l": [float(b.low) for b in bars],
                "c": [float(b.close) for b in bars],
                "v": [float(b.volume) for b in bars]}
    except Exception as e:
        return JSONResponse({"error": str(e), "symbol": symbol}, status_code=502)


def _fetch_news():
    items = []
    try:
        # non-destructive read: parse feeds without touching the seen file
        import feedparser
        for url in cfg.feeds:
            parsed = feedparser.parse(url)
            src = parsed.feed.get("title", url) if hasattr(parsed, "feed") else url
            for e in parsed.entries[:15]:
                items.append({"title": e.get("title", ""),
                              "summary": (e.get("summary", "") or "")[:200],
                              "link": e.get("link", ""), "source": src})
    except Exception as e:
        items.append({"title": f"(news error: {e})", "summary": "", "source": ""})
    # Clear Street news if enabled + authed
    if cfg.use_clearstreet:
        cs = ClearStreetClient(cfg.cs_client_id, cfg.cs_client_secret,
                               cfg.cs_audience, cfg.cs_base_url)
        for n in cs.news(limit=15):
            items.insert(0, {"title": n["title"], "summary": (n.get("summary") or "")[:200],
                             "link": "", "source": "Clear Street"})
    return items[:40]


@app.get("/api/news")
def news():
    return {"items": cached("news", 60, _fetch_news)}


@app.get("/api/timeline")
def timeline_view(limit: int = 70, kind: str = ""):
    def _b():
        from trader import timeline
        extra = []
        try:
            for r in M.read_ledger(None, limit=30):
                act = str(r.get("action") or r.get("side") or "trade").lower()
                tone = 1 if act in ("buy", "open", "long") else (-1 if act in ("sell", "close", "short") else 0)
                sym = (r.get("symbol") or "").upper()
                extra.append({"ts": r.get("ts"), "kind": "trade", "source": "broker",
                              "text": f"{act.upper()} {sym}".strip(), "symbol": sym, "tone": tone})
        except Exception:  # noqa: BLE001
            pass
        return timeline.events(limit=140, extra=extra)
    evs = cached("timeline", 30, _b)
    if kind:
        evs = [e for e in evs if e["kind"] == kind]
    return {"events": evs[:limit]}


@app.get("/api/newshub")
def newshub_view(symbol: str = "", category: str = "", q: str = "", limit: int = 60):
    def _b():
        from trader import newshub
        return newshub.aggregate(symbols=[symbol.upper()] if symbol else None, limit=120)
    data = cached(f"newshub:{symbol}", 120, _b)
    items = data.get("items", [])
    if category:
        items = [it for it in items if it["category"] == category]
    if q:
        ql = q.lower()
        items = [it for it in items if ql in it["title"].lower() or ql in (it.get("summary") or "").lower()]
    return {"items": items[:limit], "market_sentiment": data.get("market_sentiment"),
            "counts": data.get("counts"), "universe": data.get("universe"),
            "generated": data.get("generated")}


@app.get("/api/signals")
def signals(bot: str | None = None, limit: int = 40):
    rows = M.read_ledger(bot, limit=limit)
    keys = ["ts", "action", "symbol", "side", "sentiment", "confidence", "event",
            "news_src", "regime", "rvol", "ret20", "groq_confirm", "gate_reason",
            "headline", "_ledger"]
    return {"signals": [{k: r.get(k, "") for k in keys} for r in rows]}


@app.get("/api/scoreboard")
def scoreboard(bot: str | None = None):
    return M.summary(bot)


@app.get("/api/rl")
def rl_status():
    """TensorTrade RL trader telemetry: extra availability, config, and the
    trained models on disk (read from .meta.json sidecars -- no TensorFlow load)."""
    def _b():
        import glob
        import json
        out = {
            "available": False,
            "mode": cfg.strategy.mode,
            "voice_enabled": cfg.strategy.use_rl_voice,
            "universe": list(cfg.strategy.rl_universe),
            "window": cfg.strategy.rl_window,
            "models": [],
        }
        try:
            from trader import rl as _rl
            out["available"] = _rl.available()
            model_dir = cfg.strategy.rl_model_dir or _rl.trader.DEFAULT_MODEL_DIR
        except Exception:  # noqa: BLE001
            return out
        out["model_dir"] = model_dir
        for meta_path in sorted(glob.glob(os.path.join(model_dir, "*.meta.json"))):
            try:
                d = json.load(open(meta_path))
            except Exception:  # noqa: BLE001
                continue
            base = os.path.basename(meta_path)[:-len(".meta.json")]
            meta = d.get("meta", {})
            out["models"].append({
                "symbol": meta.get("symbol", base),
                "window": meta.get("window", d.get("obs_shape", [None])[0]),
                "episodes_trained": meta.get("episodes_trained"),
                "last_rewards": meta.get("last_rewards"),
                "trained": os.path.exists(os.path.join(model_dir, base + ".keras")),
            })
        return out
    return cached("rl_status", 15.0, _b)


@app.get("/api/bots")
def list_bots():
    bots = botmgr.list_bots()
    for b in bots:
        b["summary"] = M.summary(b["id"])
    return {"bots": bots, "defaults": botmgr.DEFAULT_PARAMS}


def _max_drawdown(eq):
    peak = None
    mdd = 0.0
    for x in eq:
        peak = x if peak is None else max(peak, x)
        if peak and peak > 0:
            mdd = min(mdd, x / peak - 1.0)
    return round(mdd * 100, 2)


@app.get("/api/analytics")
def analytics():
    out = {"starting": 100000.0}
    try:
        a = trading().get_account()
        pos = trading().get_all_positions()
        eq = float(a.equity)
        out["equity"] = eq
        out["total_return_pct"] = round((eq / 100000.0 - 1) * 100, 3)
        unreal = sum(float(p.unrealized_pl) for p in pos)
        out["unrealized_pl"] = round(unreal, 2)
        out["realized_pl"] = round((eq - 100000.0) - unreal, 2)
        out["open_positions"] = len(pos)
        wins = sum(1 for p in pos if float(p.unrealized_pl) > 0)
        out["open_win_rate"] = round(100 * wins / len(pos), 1) if pos else 0
        out["day_pl"] = round(eq - float(a.last_equity), 2)
    except Exception as e:
        out["account_error"] = str(e)
    try:
        ph = portfolio_history(30)
        eqc = ph.get("equity") or []
        out["max_drawdown_pct"] = _max_drawdown(eqc) if eqc else 0
        sp = ph.get("spy_pct") or []
        ep = ph.get("equity_pct") or []
        if sp and ep:
            out["vs_spy_pct"] = round(ep[-1] - sp[-1], 3)
    except Exception as e:
        out["hist_error"] = str(e)
    out["decisions"] = M.summary()
    return out


@app.post("/api/bots")
def create_bot(payload: dict = Body(...)):
    name = payload.get("name", "")
    params = payload.get("params", {})
    bot = botmgr.create_bot(name, params)
    if payload.get("start", True):
        botmgr.start_bot(bot["id"])
        bot = botmgr.get_bot(bot["id"])
    return bot


@app.post("/api/bots/{bot_id}/start")
def start_bot(bot_id: str):
    b = botmgr.start_bot(bot_id)
    return b or JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/bots/{bot_id}/stop")
def stop_bot(bot_id: str):
    b = botmgr.stop_bot(bot_id)
    return b or JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/bots/{bot_id}")
def delete_bot(bot_id: str):
    return {"deleted": botmgr.delete_bot(bot_id)}


@app.get("/api/crypto_bars")
def crypto_bars(symbol: str = "BTC/USD", hours: int = 168):
    try:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame
        cd = CryptoHistoricalDataClient(cfg.alpaca_key, cfg.alpaca_secret)
        start = datetime.now(timezone.utc) - timedelta(hours=hours + 2)
        req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=start)
        bars = cached(f"cb{symbol}{hours}", 120, lambda: cd.get_crypto_bars(req).data.get(symbol, []))
        return {"symbol": symbol,
                "t": [b.timestamp.strftime("%m-%d %H:%M") for b in bars],
                "c": [float(b.close) for b in bars]}
    except Exception as e:
        return JSONResponse({"error": str(e), "symbol": symbol}, status_code=502)


@app.post("/api/council/chat")
def council_chat(payload: dict = Body(...)):
    from trader import council
    q = (payload.get("q") or "").strip()
    if not q:
        return {"error": "empty question"}
    try:
        from trader import desk_chat
        return desk_chat.answer(cfg, q, (payload.get("symbol") or "").upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/safety")
def safety_view(clear: int = 0):
    from trader import safety
    if clear:
        safety.clear_lock()
    return {"locked": safety.lock_active(), "reason": safety.lock_reason() if safety.lock_active() else ""}


@app.get("/api/scorecard")
def scorecard_view(bot: str = None):
    from dashboard import scorecard
    realized = 0.0
    try:
        a = trading().get_account(); pos = trading().get_all_positions()
        unreal = sum(float(p.unrealized_pl) for p in pos)
        realized = (float(a.equity) - 100000.0) - unreal
    except Exception:
        pass
    return scorecard.score(bot, realized_pl=realized)


@app.get("/api/watchlist")
def watchlist():
    import json as _j
    p = PROJ / "data" / "watchlist.json"
    items = []
    if p.exists():
        try:
            items = list(_j.loads(p.read_text()).values())
        except Exception:
            items = []
    return {"items": items}


@app.get("/api/marketbrain")
def marketbrain(refresh: int = 0):
    from trader import market_brain
    import json as _j
    if refresh:
        try:
            return market_brain.refresh(cfg, with_narrative=True)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)
    p = PROJ / "data" / "market_state.json"
    return _j.loads(p.read_text()) if p.exists() else {"empty": True}


@app.get("/api/research")
def research():
    import json as _j
    p = PROJ / "data" / "research" / "latest.json"
    return _j.loads(p.read_text()) if p.exists() else {"empty": True}


@app.get("/api/freemodels")
def freemodels():
    from trader import council
    return {"models": council.openrouter_free_models(cfg, limit=40)}


@app.get("/api/council")
def council_view(symbol: str = "SPY", side: str = "buy"):
    from trader import council
    try:
        return council.convene(cfg, symbol.upper(), "buy" if side == "buy" else "sell")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/omni")
def omni_ask(payload: dict = Body(...)):
    from trader.omni import OmniClient
    q = (payload.get("q") or "").strip()
    if not q:
        return {"error": "empty question"}
    mode = "DEEP_INSIGHTS" if payload.get("deep") else "INSTANT"
    cli = OmniClient(cfg.clearstreet_token, cfg.cs_account_id)
    return cli.ask(q, mode=mode)


@app.get("/api/backtest")
def backtest():
    import json as _j
    p = PROJ / "data" / "backtests" / "latest.json"
    if not p.exists():
        return {"empty": True}
    try:
        return _j.loads(p.read_text())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/digest")
def digest():
    p = PROJ / "data" / "digests" / "latest.md"
    return {"markdown": p.read_text(encoding="utf-8") if p.exists() else "_No optimizer digest yet. It generates at the daily run._"}


@app.get("/api/strategy_curves")
def strategy_curves():
    from dashboard import perf
    id_to_name = {b["id"]: b["name"] for b in botmgr.list_bots()}
    id_to_name.setdefault("main", "main")
    try:
        return {"curves": perf.realized_curves(trading(), id_to_name)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/confluence")
def confluence_scores(symbols: str = ""):
    """Live multi-method conviction (technical+quant+fundamental) for symbols.
    Offline + cached: prices from CRSP-lite, fundamentals cached. ?symbols=A,B."""
    def _build():
        from trader import alpha
        from trader.crsp import query as crsp
        try:
            from trader import fundamentals as fund
        except Exception:
            fund = None
        from trader import market_brain
        try:
            regime = market_brain.cached_regime() or "neutral"
        except Exception:
            regime = "neutral"
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:14]
        out = []
        for t in syms:
            try:
                bars = crsp.get_prices(t, "2024-06-01", None)
                closes = [b["close"] for b in bars if b.get("close")]
                fs = None
                if fund and "/" not in t:
                    f = fund.get_fundamentals(t)
                    fs = f.fundamental_score if f else None
                conv = alpha.analyze(closes, symbol=t, fundamental_score=fs,
                                     regime=regime if regime in ("risk_on","risk_off","high_vol","neutral") else "neutral")
                out.append({"symbol": t, "composite": conv.composite, "side": conv.side,
                            "agree": conv.agree, "n": conv.n_methods, "pass": conv.gate_pass,
                            "size_mult": conv.size_mult, "scores": conv.scores})
            except Exception as e:  # noqa: BLE001
                out.append({"symbol": t, "error": str(e)[:80]})
        return {"regime": regime, "items": out}
    return cached("confluence:" + symbols, 120, _build)


@app.get("/api/ml")
def ml_card():
    """ML model card: validation AUC/accuracy/edge, feature importances, history."""
    def _build():
        import json as _j, os
        from trader.ml.infer import model_card
        card = model_card()
        hist = []
        hp = PROJ / "data" / "ml" / "history.json"
        if hp.exists():
            try:
                hist = _j.loads(hp.read_text())[-40:]
            except Exception:
                hist = []
        card["history"] = hist
        return card
    return cached("ml_card", 60, _build)


@app.get("/api/agents")
def agents_feed():
    """Autonomous agent desk: roster, recent activity, current bounded overrides."""
    def _build():
        from trader.agents import governor
        try:
            from trader.agents.orchestrator import ROSTER
            roster = [{"name": a["name"], "provider": a["provider"],
                       "model": a.get("model", ""), "tools": a["tools"]} for a in ROSTER]
        except Exception:
            roster = []
        return {"roster": roster,
                "overrides": governor.load_overrides(),
                "bounds": governor.PARAM_BOUNDS,
                "activity": governor.recent_activity(40)}
    return cached("agents_feed", 8, _build)


@app.get("/api/agents/traces")
def agent_traces():
    from trader.agents import state
    return {"traces": state.recent_traces(60)}


@app.get("/api/agents/approvals")
def agent_approvals():
    from trader.agents import state
    return {"pending": state.pending_approvals()}


@app.post("/api/agents/approve")
def agent_approve(body: dict = Body(...)):
    from trader.agents import state
    from trader.agents.runtime import execute_approved
    aid = int(body.get("id"))
    decision = body.get("decision", "approved")
    r = state.resolve_approval(aid, decision, by="dashboard")
    executed = execute_approved() if decision == "approved" else []
    return {"resolved": r, "executed": executed}


@app.get("/api/agents/health")
def agent_health():
    from trader.agents import state
    return state.kv_get("system_health", {"services": {}})


@app.get("/api/wsb")
def wsb_feed():
    """WallStreetBets RSS buzz + latest posts (cached)."""
    def _build():
        from trader import wsb
        return wsb.buzz()
    return cached("wsb", 180, _build)


@app.get("/api/signal_scorecard")
def signal_scorecard():
    """Per-source live-signal hit-rate + forward edge (confluence/ml/wsb/...)."""
    def _build():
        from trader import sigtrack
        return sigtrack.scoreboard()
    return cached("sig_scorecard", 30, _build)


@app.get("/api/predictions")
def predictions_feed():
    """Active watched hypotheses (ranked) + resolution stats."""
    def _build():
        from trader.predict import store
        return {"watching": store.predictions(status="watching", limit=40),
                "recent": store.predictions(limit=20), "stats": store.stats()}
    return cached("predictions", 30, _build)


@app.get("/api/decision_matrix")
def decision_matrix_feed():
    """The learned decision matrix: hit-rate by feature bucket."""
    def _build():
        from trader.predict import store
        return {"matrix": store.decision_matrix(min_n=1)}
    return cached("decision_matrix", 30, _build)


@app.get("/api/reasoning")
def reasoning_feed(limit: int = 50, symbol: str = "", gated: int = 0):
    def _b():
        from trader import reasoning
        return {"decisions": reasoning.recent(limit=limit, symbol=symbol or None, gated_only=bool(gated)),
                "leaderboard": reasoning.voice_leaderboard(), "stats": reasoning.stats()}
    return cached(f"reasoning:{symbol}:{gated}", 15, _b)


@app.get("/api/mesh")
def mesh_feed():
    """Cross-layer insight mesh: what the brain, prediction, ML, council, agents
    are telling each other (+ recent LTM-mirrored insights)."""
    def _build():
        from trader import mesh
        return {"insights": mesh.recent(40)}
    return cached("mesh", 15, _build)


@app.get("/api/mesh/graph")
def mesh_graph():
    def _b():
        from trader import mesh
        return mesh.graph(300)
    return cached("mesh_graph", 20, _b)


@app.get("/api/mesh/intel")
def mesh_intel():
    """Cross-layer mesh intelligence: consensus, priority inbox, themes, anomalies."""
    def _b():
        out = {}
        try:
            from trader import mesh_consensus
            out["consensus"] = mesh_consensus.consensus(300).get("symbols", [])[:8]
        except Exception as e:  # noqa: BLE001
            out["consensus"] = []; out["consensus_err"] = str(e)[:80]
        try:
            from trader import mesh_priority
            out["inbox"] = mesh_priority.inbox(limit=10).get("items", [])
            out["inbox_counts"] = mesh_priority.counts()
        except Exception:  # noqa: BLE001
            out["inbox"] = []
        try:
            from trader import mesh_themes
            out["themes"] = mesh_themes.themes(200, 8).get("themes", [])
        except Exception:  # noqa: BLE001
            out["themes"] = []
        try:
            from trader import mesh_anomaly
            out["anomalies"] = mesh_anomaly.anomalies(150)
        except Exception:  # noqa: BLE001
            out["anomalies"] = []
        try:
            from trader import mesh_narrative
            out["narrative"] = mesh_narrative.narrative().get("text", "")
        except Exception:  # noqa: BLE001
            out["narrative"] = ""
        try:
            from trader import mesh_sla
            out["sla_overdue"] = mesh_sla.overdue()
        except Exception:  # noqa: BLE001
            out["sla_overdue"] = []
        try:
            from trader import mesh_signal
            out["signals"] = mesh_signal.signals(300).get("signals", {})
        except Exception:  # noqa: BLE001
            out["signals"] = {}
        try:
            from trader import mesh_health
            out["health"] = mesh_health.score()
        except Exception:  # noqa: BLE001
            out["health"] = {}
        try:
            from trader import mesh_cluster
            out["clusters"] = mesh_cluster.clusters(400).get("clusters", [])[:5]
        except Exception:  # noqa: BLE001
            out["clusters"] = []
        try:
            from trader import mesh_forecast
            out["next_layer"] = mesh_forecast.most_likely(400)
        except Exception:  # noqa: BLE001
            out["next_layer"] = {}
        return out
    return cached("mesh_intel", 20, _b)


@app.get("/api/mesh/forecast")
def mesh_forecast_view():
    def _b():
        from trader import mesh_forecast
        return mesh_forecast.transitions(400)
    return cached("mesh_forecast", 30, _b)


@app.get("/api/mesh/cluster")
def mesh_cluster_view():
    def _b():
        from trader import mesh_cluster
        return mesh_cluster.clusters(400)
    return cached("mesh_cluster", 60, _b)


@app.get("/api/mesh/gc")
def mesh_gc_preview():
    def _b():
        from trader import mesh_gc
        return mesh_gc.preview()
    return cached("mesh_gc", 60, _b)


@app.get("/api/mesh/sla")
def mesh_sla_view():
    def _b():
        from trader import mesh_sla
        return mesh_sla.sla(400)
    return cached("mesh_sla", 30, _b)


@app.get("/api/mesh/correlation")
def mesh_correlation_view():
    def _b():
        from trader import mesh_correlation
        return mesh_correlation.lead_lag(500)
    return cached("mesh_corr", 60, _b)


@app.get("/api/mesh/search")
def mesh_search_view(q: str = "", limit: int = 20):
    def _b():
        from trader import mesh_search
        return mesh_search.search(q, limit=limit)
    return cached(f"mesh_search:{q}:{limit}", 20, _b)


@app.get("/api/mesh/digest")
def mesh_digest_view():
    def _b():
        from trader import mesh_digest
        try:
            mesh_digest.write()      # persist data/digests/mesh_latest.md
        except Exception:
            pass
        return {"markdown": mesh_digest.build()}
    return cached("mesh_digest", 60, _b)


@app.get("/api/health/full")
def health_full():
    """Consolidated system status: daemons, Pieces LTM online, and live counts."""
    def _build():
        import socket
        from urllib.parse import urlparse
        from trader.agents import state
        out = {"services": state.kv_get("system_health", {}).get("services", {})}
        # LTM reachability: the platform's durable memory is the local SQLite+CF
        # store (trader.ltm); Pieces is disabled on the cloud, so probe the REAL
        # backend in use, not the deprecated localhost:39300 MCP server.
        try:
            from trader import ltm as _ltm
            _st = _ltm.stats()
            out["ltm_online"] = "error" not in _st
            out["ltm"] = {"items": _st.get("items"), "embedded": _st.get("embedded"),
                          "backend": "local-sqlite+cf"}
        except Exception:
            out["ltm_online"] = False
        if os.getenv("USE_PIECES", "false").strip().lower() in ("1", "true", "yes", "on"):
            url = os.environ.get("PIECES_MCP_URL",
                                 "http://localhost:39300/model_context_protocol/2025-03-26/mcp")
            try:
                u = urlparse(url); host = u.hostname or "localhost"; port = u.port or 39300
                sk = socket.socket(); sk.settimeout(0.8); sk.connect((host, port)); sk.close()
                out["pieces_online"] = True
            except Exception:
                out["pieces_online"] = False
        try:
            from trader.predict import store as pstore
            out["predictions"] = pstore.stats()
        except Exception:
            out["predictions"] = {}
        try:
            from trader import mesh
            out["mesh_insights"] = len(mesh.recent(50))
        except Exception:
            out["mesh_insights"] = 0
        try:
            from trader import sigtrack
            sb = sigtrack.scoreboard().get("by_source", [])
            out["signals"] = sum(r["signals"] for r in sb)
        except Exception:
            out["signals"] = 0
        return out
    return cached("health_full", 12, _build)


@app.get("/api/telemetry/topology")
def telemetry_topology():
    from dashboard import telemetry
    return telemetry.build_topology()


@app.get("/api/telemetry/stream")
async def telemetry_stream():
    import asyncio, json as _json, time as _t
    from fastapi.responses import StreamingResponse
    from dashboard import telemetry

    async def gen():
        cursor = _t.time() - 10  # last 10s on connect
        yield "retry: 3000\n\n"
        while True:
            try:
                events, cursor = await asyncio.to_thread(telemetry.fire_events_since, cursor)
                for e in events:
                    yield "data: " + _json.dumps(e) + "\n\n"
                if not events:
                    yield ": keepalive\n\n"
            except Exception as ex:  # noqa: BLE001
                yield "data: " + _json.dumps({"id": "err", "kind": "error",
                      "source": "s:dashboard", "target": "l:mesh", "ts": int(_t.time()*1000),
                      "status": "error", "summary": str(ex)[:80]}) + "\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/backprop")
def backprop_card():
    def _b():
        import json as _j, os
        from trader import backprop
        c = backprop.card()
        hp = PROJ / "data" / "backprop" / "history.json"
        c["history"] = _j.loads(hp.read_text()) if hp.exists() else []
        return c
    return cached("backprop", 20, _b)


@app.get("/api/attention")
def attention_view(symbol: str = "SPY"):
    def _b():
        from trader import tnet
        return tnet.analyze(symbol.upper())
    return cached("attn:" + symbol, 120, _b)


@app.get("/api/tnet/forecast")
def tnet_forecast(symbol: str = "SPY", horizon: int = 5):
    def _b():
        from trader import tnet
        return tnet.forecast(symbol.upper(), horizon=horizon)
    return cached(f"tnetfc:{symbol}:{horizon}", 120, _b)


@app.get("/api/tnet/accuracy")
def tnet_accuracy():
    def _b():
        from trader import tnet
        out = {"accuracy": tnet.accuracy()}
        try:
            import json as _j
            cp = PROJ / "data" / "tnet" / "calib.json"
            out["calibration"] = _j.loads(cp.read_text()) if cp.exists() else {}
        except Exception:
            out["calibration"] = {}
        return out
    return cached("tnet_acc", 120, _b)


@app.get("/api/tnet/scan")
def tnet_scan(symbols: str = ""):
    def _b():
        from trader import tnet
        import json as _j
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if not syms:
            syms = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META"]
            wp = PROJ / "data" / "watchlist.json"
            if wp.exists():
                try:
                    wl = list(_j.loads(wp.read_text()).keys())
                    if wl:
                        syms = wl[:10]
                except Exception:
                    pass
        return tnet.scan(syms)
    return cached(f"tnet_scan:{symbols}", 300, _b)


@app.get("/api/edge")
def edge_report(window: int = 90):
    def _b():
        from trader import edge
        return edge.report(window_days=window)
    return cached(f"edge:{window}", 300, _b)


@app.get("/api/attribution")
def attribution_report():
    def _b():
        from trader import attribution
        return attribution.report()
    return cached("attribution", 300, _b)


@app.get("/api/shadow")
def shadow_view():
    def _b():
        from trader import shadow
        return shadow.status()
    return cached("shadow", 60, _b)


@app.get("/api/cortex")
def cortex_view():
    def _b():
        from trader import cortex, shadow
        sh = next((b for b in shadow.standings().get("books", []) if b["book"] == "cortex"), None)
        live = next((b for b in shadow.standings().get("books", []) if b["book"] == "live"), None)
        return {"card": cortex.card(), "enabled": cortex.enabled(), "shadow": sh, "live_shadow": live,
                "calibration": cortex.calibration(), "history": cortex.history(120)}
    return cached("cortex", 15, _b)


@app.post("/api/cortex/enable")
def cortex_enable(body: dict = Body(...)):
    from trader import cortex
    res = cortex.set_enabled(bool(body.get("enabled", True)))
    for k in [k for k in _cache if k.startswith("cortex")]:
        _cache.pop(k, None)
    return res


@app.get("/api/alpha-engine")
def alpha_engine_view(symbols: str = ""):
    def _b():
        from trader import alpha_engine
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if not syms:
            syms = ["SPY", "QQQ", "BTC"]
        return {"status": alpha_engine.status(),
                "signals": alpha_engine.signals(syms)}
    return cached(f"alpha_engine:{symbols}", 60, _b)


@app.get("/api/autonomy")
def autonomy_view():
    def _b():
        from trader import autonomy
        return autonomy.status()
    return cached("autonomy", 5, _b)


@app.post("/api/autonomy/mode")
def autonomy_mode(body: dict = Body(...)):
    from trader import autonomy
    res = autonomy.set_policy(mode=body.get("mode"),
                              kill_switch=body.get("kill_switch"))
    for k in [k for k in _cache if k.startswith("autonomy")]:
        _cache.pop(k, None)
    return res


@app.get("/api/voices")
def voices_view(regime: str = ""):
    def _b():
        from trader import voices
        return voices.summary(regime or None)
    return cached(f"voices:{regime}", 5, _b)


@app.post("/api/voices/override")
def voices_override(body: dict = Body(...)):
    from trader import voices
    v = body.get("voice", "")
    action = body.get("action", "")
    if action == "mute":
        res = voices.set_mute(v, bool(body.get("on", True)))
    elif action == "pin":
        res = voices.set_pin(v, body.get("weight"))     # weight=None unpins
    else:
        return JSONResponse({"error": "action must be mute|pin"}, status_code=400)
    for k in [k for k in _cache if k.startswith("voices:")]:  # bust stale cache
        _cache.pop(k, None)
    return res


@app.get("/api/alerts")
def alerts_view(limit: int = 60, unack: int = 0):
    def _b():
        from trader import alerts
        return {"alerts": alerts.recent(limit=limit, unack_only=bool(unack)),
                "counts": alerts.counts()}
    return cached(f"alerts:{limit}:{unack}", 8, _b)


@app.post("/api/alerts/ack")
def alerts_ack(body: dict = Body(...)):
    from trader import alerts
    res = alerts.ack(body.get("id"))
    for k in [k for k in _cache if k.startswith("alerts")]:
        _cache.pop(k, None)
    return res


@app.get("/api/learning")
def learning():
    """Self-improvement telemetry: autonomy policy + actions, ML improvement
    curve (AUC/edge over time), and the kill-switch/circuit-breaker status."""
    import json as _j, os as _o
    out = {}
    try:
        from trader import autonomy
        out["autonomy"] = autonomy.status()
        out["breaker"] = autonomy.circuit_breaker_check()
    except Exception as e:  # noqa: BLE001
        out["autonomy"] = {"error": str(e)[:120]}
    try:
        from trader.ml.train import HISTORY
        hist = _j.load(open(HISTORY)) if _o.path.exists(HISTORY) else []
        out["ml_history"] = [{"trained_at": h.get("trained_at"), "auc": h.get("auc"),
                              "edge": h.get("edge"), "acc": h.get("acc"),
                              "promoted": h.get("promoted")} for h in hist[-80:]]
    except Exception:  # noqa: BLE001
        out["ml_history"] = []
    try:
        from trader import hypolab
        out["hypotheses"] = hypolab.latest()
    except Exception:  # noqa: BLE001
        out["hypotheses"] = {}
    try:
        from trader.agents import state as _st
        aggr = float(_st.kv_get("aggression", 0.6) or 0.6)
        edge = 0.0
        try:
            from trader.ml import infer as _inf
            edge = float(_inf.model_card().get("edge", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            pass
        br = out.get("breaker", {}) or {}
        eq_hist = []
        try:
            import json as _json
            raw = _st.kv_get("equity_hist", "[]")
            eq_hist = _json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:  # noqa: BLE001
            eq_hist = []
        out["risk"] = {"aggression": round(aggr, 2), "edge": round(edge, 4),
                       "equity": br.get("eq"), "high_water": br.get("hw"), "drawdown": br.get("dd"),
                       "aggression_reco": _st.kv_get("aggression_reco", None),
                       "equity_history": eq_hist[-120:]}
    except Exception:  # noqa: BLE001
        out["risk"] = {}
    return out


@app.get("/api/transformer/trace")
def transformer_trace(symbol: str = "SPY"):
    """Real single-symbol transformer trace (encoder internals + cross-attention)."""
    from trader import tnet
    return tnet.live_trace(symbol)


@app.get("/api/transformer/stream")
async def transformer_stream():
    """SSE: emit a REAL transformer forward-pass trace every few seconds,
    rotating through a small symbol set. Actual computation, not pre-baked."""
    import asyncio, json as _json
    from fastapi.responses import StreamingResponse
    from trader import tnet

    async def gen():
        yield "retry: 4000\n\n"
        i = 0
        syms = tnet._TRACE_SYMBOLS
        while True:
            sym = syms[i % len(syms)]; i += 1
            try:
                tr = await asyncio.to_thread(tnet.live_trace, sym)
                yield "data: " + _json.dumps(tr) + "\n\n"
            except Exception as ex:  # noqa: BLE001
                yield "data: " + _json.dumps({"symbol": sym, "error": str(ex)[:120]}) + "\n\n"
            await asyncio.sleep(4.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})



@app.get("/api/pretrain/status")
def pretrain_status():
    """Cold-start report + current fusion decision-store size."""
    try:
        from trader import pretrain
        st = pretrain.status()
    except Exception as e:  # noqa: BLE001
        st = {"error": str(e)[:160]}
    return st


@app.get("/api/pretrain/run")
def pretrain_run(max_symbols: int = 24, step: int = 5, horizon: int = 10, warmup: int = 70):
    """Run a historical cold-start of the fusion brain and return the result.

    Idempotent + paper-only: backfills the training-decision store from
    point-in-time historical voices (dedup'd, so re-runs add ~0 rows) and trains
    the champion/challenger-gated fusion layers. Runs synchronously -- the
    backfill is cheap on re-run and training on the resolved rows takes a few
    seconds -- so the response carries the real training metrics or error."""
    try:
        from trader import pretrain
        return pretrain.run(max_symbols=max_symbols, step=step, horizon=horizon, warmup=warmup)
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"ok": False, "error": str(e)[:300], "trace": traceback.format_exc()[-800:]}


@app.get("/api/spot")
def api_spot(symbols: str = "BTCUSD,ETHUSD,SOLUSD"):
    """Resilient real-time crypto spot (CryptoCompare -> Coinbase -> CoinGecko)."""
    try:
        from trader import spot
        syms = [x.strip() for x in symbols.split(",") if x.strip()]
        got, src = spot.spots(syms)
        return {"spots": got, "source": src, "status": spot.status()}
    except Exception as e:  # noqa: BLE001
        return {"spots": {}, "source": "error", "error": str(e)[:160]}


@app.get("/api/scanner")
def api_scanner(arm: bool = False, fade: bool = False, n: int = 8, min_conf: float = 0.62):
    """Momentum/catalyst scanner over daily bars. arm=true also arms the strongest
    into the watch->wait->strike list (fills WATCHLIST - ARMED)."""
    try:
        from trader import scanner
        cats = scanner.scan(fade=fade, min_conf=min_conf)[:n]
        out = {"catalysts": cats, "count": len(cats), "fade": fade}
        if arm:
            out["armed"] = scanner.arm_top(n=min(n, 6), fade=fade)
        return out
    except Exception as e:  # noqa: BLE001
        return {"catalysts": [], "error": str(e)[:160]}


@app.get("/api/factors")
def api_factors():
    """Cross-sectional factor ranking (momentum/reversal/low-vol/trend z-scores)."""
    try:
        from trader import factors
        return {"ranking": factors.ranking()}
    except Exception as e:  # noqa: BLE001
        return {"ranking": [], "error": str(e)[:160]}


@app.get("/api/calibration")
def api_calibration():
    """Meta-labeler + probability-calibration card (Brier before/after)."""
    try:
        from trader import calibrate
        return calibrate.card()
    except Exception as e:  # noqa: BLE001
        return {"trained": False, "error": str(e)[:160]}


@app.get("/api/psyche")
def api_psyche():
    """The system's internal state (mood/affect/drives), its self-built beliefs,
    and how the state is modulating behaviour. Honest: a grounded model of an
    internal state, not literal feeling -- every field maps to a measured cause."""
    try:
        from trader import psyche
        st = psyche.state()
        st["beliefs"] = psyche.beliefs(8)
        return st
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


@app.get("/api/beliefs")
def api_beliefs():
    """The system's self-built structured knowledge: beliefs (with decayed
    confidence + utility), the per-voice multipliers they apply to strategy
    weighting, and any unresolved conflicts."""
    try:
        from trader import beliefs
        return beliefs.stats()
    except Exception as e:  # noqa: BLE001
        return {"n": 0, "error": str(e)[:160]}


@app.get("/api/episodes")
def api_episodes():
    """Episodic decision memory: what the system did, in what state, and how it
    turned out -- plus its behaviour broken out by mood/curiosity state."""
    try:
        from trader import episodes
        return episodes.stats()
    except Exception as e:  # noqa: BLE001
        return {"total": 0, "error": str(e)[:160]}


@app.get("/api/psyche/reflect")
def api_psyche_reflect():
    """Trigger one introspection cycle now: the system reflects on its state +
    experience and forms/updates structured beliefs (which feed strategy
    weighting). Idempotent-ish; returns the reflection + what it formed."""
    try:
        from trader import psyche
        return psyche.reflect()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


@app.get("/api/search")
def api_search(q: str = "", k: int = 5):
    """The brain's live web lookup (DuckDuckGo, no API key). The agent mesh can
    also call this autonomously as the 'web_search' tool."""
    try:
        from trader import websearch
        return {"query": q, "results": websearch.search(q, k=max(1, min(10, k)))}
    except Exception as e:  # noqa: BLE001
        return {"query": q, "results": [], "error": str(e)[:160]}


@app.get("/api/dream")
def api_dream():
    """What the system does while the market sleeps. Returns the latest dream
    journal (replay/consolidate/counterfactual-dream/study/retrain), recent
    journal lines, and the current market session so the brain can show whether
    it is awake or dreaming right now."""
    try:
        from trader import dream, marketclock
        return {"session": marketclock.session(), "open": marketclock.is_open(),
                "last": dream.last(), "journal": dream.journal(20)}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160], "last": {}, "journal": []}


@app.post("/api/dream/run")
def api_dream_run():
    """Trigger a dream cycle now (safe: memory + training only, never trades).
    Handy for testing; normally the autonomy governor runs it while closed."""
    try:
        from trader import dream
        return dream.run(reason="manual trigger")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


@app.get("/api/cognition")
def api_cognition():
    """Latest free-model cognition output: market brief, extracted news catalysts,
    trade post-mortem lessons, risk-sentinel warnings, and belief adjudications."""
    try:
        from trader import cognition
        return {"brief": cognition.last("brief"), "catalysts": cognition.last("catalysts"),
                "postmortem": cognition.last("postmortem"), "risk": cognition.last("risk"),
                "adjudicate": cognition.last("adjudicate"),
                "macro": cognition.last("macro"), "second_opinion": cognition.last("second_opinion"),
                "theory": cognition.last("theory"), "watchlist_review": cognition.last("watchlist_review"),
                "strategy_review": cognition.last("strategy_review"), "anomaly": cognition.last("anomaly")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


@app.post("/api/cognition/run")
def api_cognition_run(job: str = "brief"):
    """Trigger one cognition job now (free-model, memory/analysis only, no trades).
    job in: brief | catalysts | postmortem | risk | adjudicate."""
    try:
        from trader import cognition
        from trader import cognition2
        fn = {"brief": cognition.brief, "catalysts": cognition.news_catalysts,
              "postmortem": cognition.postmortem, "risk": cognition.risk_scan,
              "adjudicate": cognition.adjudicate,
              "macro": cognition2.macro_analysis, "second_opinion": cognition2.second_opinion,
              "theory": cognition2.theory_synthesis, "watchlist_review": cognition2.watchlist_review,
              "strategy_review": cognition2.strategy_review, "anomaly": cognition2.anomaly_explain}.get(job)
        if not fn:
            return {"ok": False, "error": f"unknown job {job}"}
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


@app.get("/api/ml/retrain")
def api_ml_retrain(force: int = 0, horizon: int = 20, lookback: int = 130):
    """Retrain the ML model now and report the challenger metrics. force=1 adopts
    the new model regardless of the champion (use after a label-definition change)."""
    try:
        from trader.ml.train import train_once
        return train_once(horizon=horizon, lookback=lookback, force_promote=bool(force))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


@app.get("/api/loop_health")
def api_loop_health():
    """End-to-end health of the trade->learn loop: does executing feed episodes,
    do episodes resolve into post-mortem lessons, and do lessons earn belief
    utility? Surfaces every link so a starved loop is obvious at a glance."""
    out = {}
    try:
        from dashboard import dash_metrics
        sm = dash_metrics.summary()
        out["trading"] = {"decisions": sm.get("total_decisions", 0), "orders": sm.get("orders", 0),
                          "top_skip_reasons": sm.get("by_reason", {})}
    except Exception as e:  # noqa: BLE001
        out["trading"] = {"error": str(e)[:80]}
    try:
        from trader import episodes
        out["episodes"] = episodes.stats()
    except Exception as e:  # noqa: BLE001
        out["episodes"] = {"error": str(e)[:80]}
    try:
        from trader import beliefs
        bs = beliefs.all_beliefs()
        out["beliefs"] = {"total": len(bs),
                          "with_utility": sum(1 for b in bs if abs(b.get("utility", 0.0)) > 1e-9)}
    except Exception as e:  # noqa: BLE001
        out["beliefs"] = {"error": str(e)[:80]}
    try:
        from trader import cognition
        pm = cognition.last("postmortem")
        out["postmortem"] = {"ts": pm.get("ts", ""), "lessons": len(pm.get("lessons", [])),
                             "reviewed": pm.get("reviewed", 0)}
    except Exception as e:  # noqa: BLE001
        out["postmortem"] = {"error": str(e)[:80]}
    try:
        from trader.ml.infer import model_card
        mc = model_card()
        out["ml"] = {"auc": mc.get("auc"), "auc_lo": mc.get("auc_lo"),
                     "trained_at": mc.get("trained_at"), "n_features": len(mc.get("importances", {}))}
    except Exception as e:  # noqa: BLE001
        out["ml"] = {"error": str(e)[:80]}
    try:
        from trader import alpha_engine
        out["alpha_engine"] = alpha_engine.status()
    except Exception as e:  # noqa: BLE001
        out["alpha_engine"] = {"error": str(e)[:80]}
    # a one-line verdict
