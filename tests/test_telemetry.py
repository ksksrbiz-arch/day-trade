"""Tests for the brain telemetry wire (topology + fire-event contract).

Guards the brain's data contract so the visualization can't silently break.
"""
from dashboard import telemetry


VALID_KINDS = {"model_call", "tool_call", "response", "data_read", "data_write", "error"}


def test_topology_shape_and_unique_ids():
    topo = telemetry.build_topology()
    assert "nodes" in topo and "edges" in topo
    ids = [n["id"] for n in topo["nodes"]]
    assert len(ids) == len(set(ids))                      # unique ids
    for n in topo["nodes"]:
        assert {"id", "label", "kind", "group"} <= set(n)


def test_connectors_present_with_health():
    nodes = telemetry.build_topology()["nodes"]
    conns = [n for n in nodes if n["kind"] == "connector"]
    labels = {n["label"] for n in conns}
    assert "Pieces MCP" in labels                          # the MCP connector exists
    assert {"Alpaca", "CoinEx", "WallStreetBets", "News RSS"} <= labels
    for c in conns:
        assert c["group"] == "Connectors"
        assert c["meta"].get("status") in ("online", "offline", "unknown")


def test_edges_reference_real_nodes():
    topo = telemetry.build_topology()
    ids = {n["id"] for n in topo["nodes"]}
    for e in topo["edges"]:
        assert e["source"] in ids and e["target"] in ids


def test_fire_events_contract():
    ids = {n["id"] for n in telemetry.build_topology()["nodes"]}
    events, cursor = telemetry.fire_events_since(0)
    assert isinstance(events, list) and isinstance(cursor, (int, float))
    for e in events:
        assert {"id", "source", "target", "kind", "ts"} <= set(e)
        assert e["kind"] in VALID_KINDS
        assert e["source"] in ids and e["target"] in ids   # never dangling
