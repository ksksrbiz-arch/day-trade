"""One-command rebuild: python -m trader.crsp [--enrich N] [--prices ASOF S E]

  build  -> fuse 5 sources into the security master (idempotent)
  enrich -> AI-classify delisting reasons (default 150)
  audit  -> print survivorship summary
"""
from __future__ import annotations
import sys
import json

from .schema import init_db
from .build import build_master
from .enrich import enrich_delistings
from .query import survivorship_audit, backfill_universe


def main(argv: list[str]) -> None:
    enrich_n = 150
    prices = None
    for i, a in enumerate(argv):
        if a == "--enrich" and i + 1 < len(argv):
            enrich_n = int(argv[i + 1])
        if a == "--prices" and i + 3 < len(argv):
            prices = (argv[i + 1], argv[i + 2], argv[i + 3])

    conn = init_db()
    print("[1/3] Fusing sources -> security master")
    build_master(conn)
    print(f"[2/3] AI-enriching up to {enrich_n} delistings")
    enrich_delistings(conn, limit=enrich_n)
    if prices:
        asof, start, end = prices
        print(f"[*] Backfilling prices for {asof} universe over {start}..{end}")
        print("   ", backfill_universe(asof, start, end))
    print("[3/3] Survivorship audit")
    print(json.dumps(survivorship_audit(conn), indent=2))
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
