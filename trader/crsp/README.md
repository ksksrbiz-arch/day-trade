# CRSP-lite — free, AI-enriched, survivorship-bias-reduced security master

A free reconstruction of the kind of point-in-time database CRSP sells, fused
from five no-/low-cost sources and cross-referenced with the AI council.

It is **not** a CRSP clone (CRSP is decades of hand-curated PIT data with full
delisting returns). It is an honest free substitute that removes the biggest
chunk of survivorship bias for backtesting: it keeps the companies that were
later removed, acquired, or went bankrupt — the names a "today's tickers"
universe silently omits.

## Sources fused
1. **Wikipedia** — point-in-time S&P 500 membership from the change log (no key)
2. **Alpha Vantage** `LISTING_STATUS` — active + delisted universe (free key)
3. **SEC EDGAR** — CIK permanent identity anchor (no key)
4. **Tiingo** — daily prices, retains many delisted tickers (have key)
5. **FMP** — optional fallback delisted list / prices (`FMP_API_KEY`, optional)

## What it produces (SQLite at `data/crsp_lite.db`)
- `securities` — permanent ids (**PERMNO**), CIK, ticker, status, ipo/delist dates
- `membership` — PIT index intervals (start/end per ticker)
- `delistings` — events + **AI-classified reason** (merger/acquisition/bankruptcy/…)
- `prices` — cached daily OHLCV (incl. delisted names), fetched on demand
- `enrichment` — sector + AI key/values with provenance + confidence
- `reconciliation` — cross-source name agreement / conflict ledger

## Use
```bash
# build + AI-enrich + audit (idempotent; permnos stay stable)
python -m trader.crsp --enrich 150

# also cache prices for a PIT universe over a window
python -m trader.crsp --prices 2015-06-30 2015-01-01 2017-01-01

# run the walk-forward backtest on the bias-reduced as-of universe
python -m trader.walkforward --pit 2015-06-30 --days 500
```

```python
from trader.crsp import query as crsp
crsp.constituents_asof("2008-09-15")   # who was in the S&P 500 that day
crsp.get_prices("ETFC", "2007-01-01", "2009-12-31")  # delisted name, cached
crsp.delist_reason("LEH")              # {'reason_class':'bankruptcy', ...}
crsp.survivorship_audit()
```

## Honest limitations (the residual bias)
- Wikipedia's change log thins out before ~2000; pre-2000 PIT membership is partial.
- Tiingo does not retain *every* dead ticker's deep history (e.g. Lehman 2008),
  so price coverage of long-dead names is incomplete — those rows carry the
  delisting reason but may lack prices.
- No true *delisting returns* (the final liquidation value) — CRSP's edge.
  The AI reason class is a proxy: merger/acquisition ≈ capital preserved,
  bankruptcy ≈ capital destroyed.
- Index membership is reconstructed, not official; treat counts as ±1–2%.
