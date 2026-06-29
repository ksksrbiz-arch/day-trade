"""Tests for mesh garbage-collection / forgetting (hermetic).

We monkeypatch mesh.DB to a tmp_path sqlite file so mesh.conn() builds a fresh,
isolated store (conn() runs the schema for us). Stale + low-salience rows are
prune candidates; recent and/or high-salience rows must always survive.
"""
import time

from trader import mesh, mesh_gc

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(days_ago: float) -> str:
    return time.strftime(_FMT, time.gmtime(time.time() - days_ago * 86400.0))


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh, "DB", str(tmp_path / "mesh.db"))
    c = mesh.conn()  # conn() runs the schema
    rows = [
        # id,           ts,             day, layer, kind, symbol, salience, text
        ("old_low_1",   _iso(30), "d", "ml",   "k", "",  0.10, "stale noise"),
        ("old_low_2",   _iso(20), "d", "news", "k", "",  0.30, "stale noise"),
        ("old_high",    _iso(40), "d", "brain","k", "",  0.90, "stale but salient"),
        ("recent_low",  _iso(1),  "d", "ml",   "k", "",  0.10, "fresh noise"),
        ("recent_high", _iso(0),  "d", "brain","k", "",  0.95, "fresh signal"),
    ]
    c.executemany(
        "INSERT INTO insights(id,ts,day,layer,kind,symbol,salience,text)"
        " VALUES(?,?,?,?,?,?,?,?)", rows)
    c.commit(); c.close()


def test_preview_counts_candidates(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    p = mesh_gc.preview(ttl_days=14.0, min_salience=0.5)
    assert p["total"] == 5
    # only old_low_1 and old_low_2 are stale AND below salience
    assert p["would_prune"] == 2
    assert p["kept"] == 3
    assert p["cutoff"].endswith("Z")


def test_compact_dry_run_deletes_nothing(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = mesh_gc.compact(ttl_days=14.0, min_salience=0.5, dry_run=True)
    assert r["dry_run"] is True
    assert r["pruned"] == 2
    assert r["remaining"] == 5  # nothing actually removed
    c = mesh.conn()
    n = c.execute("SELECT COUNT(*) AS n FROM insights").fetchone()["n"]
    c.close()
    assert n == 5


def test_compact_prunes_only_stale_low_salience(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    r = mesh_gc.compact(ttl_days=14.0, min_salience=0.5, dry_run=False)
    assert r["dry_run"] is False
    assert r["pruned"] == 2
    assert r["remaining"] == 3
    c = mesh.conn()
    ids = {row["id"] for row in c.execute("SELECT id FROM insights").fetchall()}
    c.close()
    assert ids == {"old_high", "recent_low", "recent_high"}
    assert "old_low_1" not in ids and "old_low_2" not in ids
