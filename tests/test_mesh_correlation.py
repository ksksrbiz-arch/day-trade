"""Hermetic tests for trader.mesh_correlation lead/lag analysis."""
import time

from trader import mesh, mesh_correlation


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _insight(epoch, layer, symbol, salience=0.6):
    return {
        "id": f"{layer}-{symbol}-{epoch}",
        "ts": _iso(epoch),
        "day": time.strftime("%Y-%m-%d", time.gmtime(epoch)),
        "layer": layer,
        "kind": "test",
        "symbol": symbol,
        "salience": salience,
        "text": f"{layer} on {symbol}",
    }


def _patch(monkeypatch, insights):
    # mesh.recent returns NEWEST FIRST -> reverse chronological.
    ordered = sorted(insights, key=lambda r: r["ts"], reverse=True)

    def fake_recent(n=30, layers=None, symbol=""):
        return ordered[:n]

    monkeypatch.setattr(mesh, "recent", fake_recent)


def test_lead_lag_detects_tnet_leads_ml(monkeypatch):
    base = 1_700_000_000  # fixed UTC epoch
    five_min = 300
    insights = []
    # SPY: tnet fires, then ml follows 5 min later -- repeated 3x, spaced apart.
    for i in range(3):
        start = base + i * 3600  # an hour between each pattern instance
        insights.append(_insight(start, "tnet", "SPY"))
        insights.append(_insight(start + five_min, "ml", "SPY"))
    _patch(monkeypatch, insights)

    out = mesh_correlation.lead_lag(window=500, max_gap_min=30.0)
    assert "generated" in out
    pairs = out["pairs"]

    tnet_ml = [p for p in pairs if p["lead"] == "tnet" and p["follow"] == "ml"]
    assert tnet_ml, f"expected tnet->ml pair, got {pairs}"
    pair = tnet_ml[0]
    assert pair["count"] >= 2
    # gaps are all ~5 minutes
    assert 4.0 <= pair["avg_gap_min"] <= 6.0

    # reverse direction should not appear (ml never precedes tnet within gap)
    assert not any(p["lead"] == "ml" and p["follow"] == "tnet" for p in pairs)


def test_pairs_beyond_max_gap_excluded(monkeypatch):
    base = 1_700_000_000
    insights = []
    # tnet then ml, but always 45 min apart (> 30 min max_gap) -- excluded.
    for i in range(3):
        start = base + i * 7200
        insights.append(_insight(start, "tnet", "SPY"))
        insights.append(_insight(start + 45 * 60, "ml", "SPY"))
    _patch(monkeypatch, insights)

    out = mesh_correlation.lead_lag(window=500, max_gap_min=30.0)
    assert out["pairs"] == [], f"expected no pairs within gap, got {out['pairs']}"


def test_count_below_two_dropped(monkeypatch):
    base = 1_700_000_000
    # single tnet->ml observation only -> count==1 -> dropped.
    insights = [
        _insight(base, "tnet", "SPY"),
        _insight(base + 300, "ml", "SPY"),
    ]
    _patch(monkeypatch, insights)
    out = mesh_correlation.lead_lag()
    assert out["pairs"] == []


def test_empty_symbol_skipped(monkeypatch):
    base = 1_700_000_000
    insights = []
    for i in range(3):
        start = base + i * 3600
        insights.append(_insight(start, "tnet", ""))
        insights.append(_insight(start + 300, "ml", ""))
    _patch(monkeypatch, insights)
    out = mesh_correlation.lead_lag()
    assert out["pairs"] == []


def test_top_leads_limits(monkeypatch):
    base = 1_700_000_000
    five_min = 300
    insights = []
    for i in range(3):
        start = base + i * 3600
        insights.append(_insight(start, "tnet", "SPY"))
        insights.append(_insight(start + five_min, "ml", "SPY"))
        insights.append(_insight(start + 2 * five_min, "news", "SPY"))
    _patch(monkeypatch, insights)

    top1 = mesh_correlation.top_leads(1)
    assert len(top1) == 1
    full = mesh_correlation.lead_lag()["pairs"]
    assert top1[0] == full[0]


def test_fail_soft_on_bad_recent(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("mesh down")

    monkeypatch.setattr(mesh, "recent", boom)
    out = mesh_correlation.lead_lag()
    assert out["pairs"] == []
    assert "generated" in out
