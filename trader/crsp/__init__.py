"""CRSP-lite: a survivorship-bias-reduced, AI-enriched point-in-time security master
built entirely from free data sources.

Sources fused:
  1. Wikipedia          -> point-in-time S&P 500 index membership (no key)
  2. Alpha Vantage      -> LISTING_STATUS active + delisted universe (free key)
  3. SEC EDGAR          -> CIK permanent identity anchor (no key)
  4. Tiingo             -> daily prices, retains delisted tickers (have key)
  5. FMP                -> fallback delisted list / prices (optional key)

This is NOT a clone of CRSP (decades of hand-curated PIT data). It is an
honest, free reconstruction that removes the largest chunk of survivorship
bias for backtesting: it keeps companies that were later removed, acquired,
or went bankrupt, assigns CRSP-style permanent ids (PERMNO), and layers AI
cross-reference enrichment on top.
"""

from .schema import connect, init_db, DB_PATH  # noqa: F401
