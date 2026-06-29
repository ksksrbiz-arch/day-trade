"""
Massive flat-files (Polygon-style) S3 client.

The bucket holds whole-market daily OHLCV files (one gzipped CSV per trading
day) plus minute/trade/quote data, options, indices, futures, crypto, forex.
Schema of us_stocks_sip/day_aggs_v1/<YYYY>/<MM>/<YYYY-MM-DD>.csv.gz is:

    ticker,volume,open,close,high,low,window_start,transactions

This is the *bulk / backtest* feature source: one file = every ticker for a day,
so it's ideal for replaying history, not for per-symbol live lookups (use the
Alpaca path in marketdata.py for that).

NOTE: as of setup the provided keys can ListBucket but GetObject returns 403
(the flat-file download entitlement is not active on the account). Every method
here fails soft -- returns None / [] instead of raising -- so the rest of the
system keeps running. The moment the entitlement is enabled, this starts
returning data with zero code changes.
"""
from __future__ import annotations

import gzip
import io
import os
from typing import Optional

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError, BotoCoreError
    _HAVE_BOTO = True
except Exception:  # boto3 not installed
    _HAVE_BOTO = False


class MassiveClient:
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        endpoint: str = "https://files.massive.com",
        bucket: str = "flatfiles",
    ):
        self.bucket = bucket
        self.enabled = bool(_HAVE_BOTO and access_key and secret_key)
        self._download_ok: Optional[bool] = None  # cached entitlement probe
        if self.enabled:
            self.s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        else:
            self.s3 = None

    def can_download(self) -> bool:
        """Probe once whether GetObject is actually permitted (entitlement)."""
        if not self.enabled:
            return False
        if self._download_ok is not None:
            return self._download_ok
        # find any object, then try to head it
        try:
            r = self.s3.list_objects_v2(
                Bucket=self.bucket, Prefix="us_stocks_sip/day_aggs_v1/", MaxKeys=1
            )
            keys = [o["Key"] for o in r.get("Contents", [])]
            if not keys:
                # only common-prefixes returned; drill one level is overkill here
                self._download_ok = False
                return False
            self.s3.head_object(Bucket=self.bucket, Key=keys[0])
            self._download_ok = True
        except Exception:
            self._download_ok = False
        return self._download_ok

    def day_aggs_csv(self, date_str: str, asset: str = "us_stocks_sip") -> Optional[str]:
        """Return the decompressed CSV text for one trading day, or None.

        date_str: 'YYYY-MM-DD'
        """
        if not self.enabled:
            return None
        y, m, _ = date_str.split("-")
        key = f"{asset}/day_aggs_v1/{y}/{m}/{date_str}.csv.gz"
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            return gzip.decompress(body).decode()
        except Exception:
            return None

    @staticmethod
    def parse_day_aggs(csv_text: str) -> dict[str, dict]:
        """ticker -> {open,high,low,close,volume} for one day's file."""
        out: dict[str, dict] = {}
        lines = csv_text.splitlines()
        if not lines:
            return out
        header = [h.strip() for h in lines[0].split(",")]
        idx = {name: i for i, name in enumerate(header)}
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < len(header):
                continue
            try:
                t = parts[idx["ticker"]]
                out[t] = {
                    "open": float(parts[idx["open"]]),
                    "high": float(parts[idx["high"]]),
                    "low": float(parts[idx["low"]]),
                    "close": float(parts[idx["close"]]),
                    "volume": float(parts[idx["volume"]]),
                }
            except (KeyError, ValueError, IndexError):
                continue
        return out
