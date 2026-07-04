"""
Central config. Reads from environment (.env supported via python-dotenv).
Everything tunable lives here so the strategy code stays clean.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()  # local ./.env
    # Also honor a Render/hosted Secret File mounted outside the working dir.
    for _p in ("/etc/secrets/.env", "/app/.env"):
        if os.path.exists(_p):
            load_dotenv(_p, override=False)
except ImportError:
    pass

from .strategy import StrategyConfig
from .simbroker import SimConfig


# --- default RSS feeds (broad market / business). Swap for your own. ---
DEFAULT_FEEDS = [
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",      # WSJ Markets
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # CNBC Top News
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",  # MarketWatch
]


@dataclass
class AppConfig:
    alpaca_key: str = ""
    alpaca_secret: str = ""
    alpaca_paper: bool = True
    anthropic_key: str = ""
    model: str = "llama-3.3-70b-versatile"
    # Groq context enricher
    groq_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    # Massive flat-files
    massive_access: str = ""
    massive_secret: str = ""
    massive_endpoint: str = "https://files.massive.com"
    massive_bucket: str = "flatfiles"
    # Clear Street (read-only: news + market data; NO execution)
    use_clearstreet: bool = False
    cs_client_id: str = ""
    cs_client_secret: str = ""
    cs_audience: str = "https://api.clearstreet.io"
    cs_base_url: str = "https://api.clearstreet.io"
    # backtest data sources
    tiingo_token: str = ""
    binance_key: str = ""
    binance_secret: str = ""
    clearstreet_token: str = ""
    cs_account_id: str = ""
    # reasoning council providers
    cohere_key: str = ""
    cohere_model: str = "command-r-08-2024"
    cf_account_id: str = ""
    cf_api_token: str = ""
    cf_model: str = "@cf/meta/llama-3.1-8b-instruct"
    replicate_key: str = ""
    replicate_model: str = "meta/meta-llama-3.1-8b-instruct"
    replicate_webhook_secret: str = ""
    openrouter_key: str = ""
    openrouter_models: tuple = ()
    freecrypto_key: str = ""
    pieces_url: str = "http://localhost:39300/model_context_protocol/2025-03-26/mcp"
    pieces_enabled: bool = True
    feeds: list[str] = field(default_factory=lambda: list(DEFAULT_FEEDS))
    seen_path: str = "data/seen.json"
    trade_log: str = "data/trades.csv"
    poll_seconds: int = 300
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    sim: SimConfig = field(default_factory=SimConfig)


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def load() -> AppConfig:
    universe = {
        s.strip().upper()
        for s in os.getenv("UNIVERSE", "").split(",")
        if s.strip()
    }
    strat = StrategyConfig(
        universe=universe,
        min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.60")),
        min_sentiment=float(os.getenv("MIN_SENTIMENT", "0.40")),
        notional_per_trade=float(os.getenv("NOTIONAL_PER_TRADE", "5.0")),
        take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.05")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.03")),
        allow_short=_env_bool("ALLOW_SHORT", False),
        require_confirmation=_env_bool("USE_CONFIRMATION", False),
        confirm_fail_open=_env_bool("CONFIRM_FAIL_OPEN", True),
        min_rvol=float(os.getenv("MIN_RVOL", "0.5")),
        momentum_tolerance=float(os.getenv("MOMENTUM_TOLERANCE", "0.08")),
        regime_filter=_env_bool("REGIME_FILTER", True),
        dynamic_sizing=_env_bool("DYNAMIC_SIZING", True),
        size_min_mult=float(os.getenv("SIZE_MIN_MULT", "0.5")),
        size_max_mult=float(os.getenv("SIZE_MAX_MULT", "2.2")),
        vol_target=float(os.getenv("VOL_TARGET", "0.02")),
        adaptive_exits=_env_bool("ADAPTIVE_EXITS", False),
        tp_vol_mult=float(os.getenv("TP_VOL_MULT", "2.5")),
        sl_vol_mult=float(os.getenv("SL_VOL_MULT", "1.5")),
        cooldown_min=float(os.getenv("COOLDOWN_MIN", "0")),
        min_rr=float(os.getenv("MIN_RR", "0")),
        daily_max_dd=float(os.getenv("DAILY_MAX_DD", "0")),
        mode=os.getenv("MODE", "news"),
        scalper_universe=tuple(s.strip().upper() for s in os.getenv("SCALPER_UNIVERSE", "").split(",") if s.strip()),
        scalper_window=int(os.getenv("SCALPER_WINDOW", "20")),
        scalper_k=float(os.getenv("SCALPER_K", "2.0")),
        use_ofi=_env_bool("USE_OFI", False),
        ofi_threshold=float(os.getenv("OFI_THRESHOLD", "0.6")),
        use_options=_env_bool("USE_OPTIONS", False),
        watch_buffer=float(os.getenv("WATCH_BUFFER", "0.005")),
        watch_expiry_min=float(os.getenv("WATCH_EXPIRY_MIN", "180")),
        liq_extreme=float(os.getenv("LIQ_EXTREME", "0.92")),
        use_omni=_env_bool("USE_OMNI", False),
        omni_gate=_env_bool("OMNI_GATE", False),
        omni_borderline=float(os.getenv("OMNI_BORDERLINE", "0.15")),
        use_confluence=_env_bool("USE_CONFLUENCE", False),
        confluence_min_agree=int(os.getenv("CONFLUENCE_MIN_AGREE", "2")),
        confluence_min_score=float(os.getenv("CONFLUENCE_MIN_SCORE", "0.20")),
        confluence_size=_env_bool("CONFLUENCE_SIZE", True),
        use_fundamentals=_env_bool("USE_FUNDAMENTALS", True),
    )
    # autonomous-agent overrides (bounded by the governor) take effect on reload
    try:
        from trader.agents.governor import load_overrides
        _ov = load_overrides()
        if "CONFLUENCE_MIN_SCORE" in _ov: strat.confluence_min_score = float(_ov["CONFLUENCE_MIN_SCORE"])
        if "CONFLUENCE_MIN_AGREE" in _ov: strat.confluence_min_agree = int(_ov["CONFLUENCE_MIN_AGREE"])
        if "MIN_CONFIDENCE" in _ov: strat.min_confidence = float(_ov["MIN_CONFIDENCE"])
        if "MIN_SENTIMENT" in _ov: strat.min_sentiment = float(_ov["MIN_SENTIMENT"])
        if "COOLDOWN_MIN" in _ov: strat.cooldown_min = float(_ov["COOLDOWN_MIN"])
        if "ALLOW_SHORT" in _ov: strat.allow_short = bool(_ov["ALLOW_SHORT"])
    except Exception:
        pass
    sim = SimConfig(
        slippage_bps=float(os.getenv("SLIPPAGE_BPS", "10")),
        fee_bps=float(os.getenv("FEE_BPS", "0")),
        starting_cash=float(os.getenv("SIM_STARTING_CASH", "100")),
    )
    feeds_env = os.getenv("FEEDS", "")
    feeds = [u.strip() for u in feeds_env.split(",") if u.strip()] or list(DEFAULT_FEEDS)

    return AppConfig(
        alpaca_key=os.getenv("ALPACA_API_KEY", ""),
        alpaca_secret=os.getenv("ALPACA_SECRET_KEY", ""),
        alpaca_paper=_env_bool("ALPACA_PAPER", True),
        anthropic_key="",  # Anthropic removed; field kept dormant for back-compat
        model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
        groq_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        massive_access=os.getenv("MASSIVE_ACCESS_KEY", ""),
        massive_secret=os.getenv("MASSIVE_SECRET_KEY", ""),
        massive_endpoint=os.getenv("MASSIVE_ENDPOINT", "https://files.massive.com"),
        massive_bucket=os.getenv("MASSIVE_BUCKET", "flatfiles"),
        use_clearstreet=_env_bool("USE_CLEARSTREET", False),
        cs_client_id=os.getenv("CLEARSTREET_CLIENT_ID", ""),
        cs_client_secret=os.getenv("CLEARSTREET_CLIENT_SECRET", ""),
        cs_audience=os.getenv("CLEARSTREET_AUDIENCE", "https://api.clearstreet.io"),
        cs_base_url=os.getenv("CLEARSTREET_BASE_URL", "https://api.clearstreet.io"),
        tiingo_token=os.getenv("TIINGO_TOKEN", ""),
        binance_key=os.getenv("BINANCE_KEY", ""),
        binance_secret=os.getenv("BINANCE_SECRET", ""),
        clearstreet_token=os.getenv("CLEARSTREET_TOKEN", ""),
        cs_account_id=os.getenv("CLEARSTREET_ACCOUNT_ID", ""),
        cohere_key=os.getenv("COHERE_API_KEY", ""),
        cohere_model=os.getenv("COHERE_MODEL", "command-r-08-2024"),
        cf_account_id=os.getenv("CF_ACCOUNT_ID", ""),
        cf_api_token=os.getenv("CF_API_TOKEN", ""),
        cf_model=os.getenv("CF_MODEL", "@cf/meta/llama-3.1-8b-instruct"),
        replicate_key=os.getenv("REPLICATE_API_TOKEN", ""),
        replicate_model=os.getenv("REPLICATE_MODEL", "meta/meta-llama-3.1-8b-instruct"),
        replicate_webhook_secret=os.getenv("REPLICATE_WEBHOOK_SECRET", ""),
        openrouter_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_models=tuple(m.strip() for m in os.getenv("OPENROUTER_MODELS", "").split(",") if m.strip()),
        freecrypto_key=os.getenv("FREECRYPTOAPI_KEY", ""),
        pieces_url=os.getenv("PIECES_MCP_URL", "http://localhost:39300/model_context_protocol/2025-03-26/mcp"),
        pieces_enabled=(os.getenv("USE_PIECES", "true").strip().lower() in ("1","true","yes","on")),
        feeds=feeds,
        seen_path=os.getenv("SEEN_PATH", "data/seen.json"),
        trade_log=os.getenv("TRADE_LOG", "data/trades.csv"),
        poll_seconds=int(os.getenv("POLL_SECONDS", "300")),
        strategy=strat,
        sim=sim,
    )
