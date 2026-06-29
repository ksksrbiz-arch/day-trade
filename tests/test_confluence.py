"""Deterministic tests for the analysis engines + confluence brain."""
import math

from trader import ta, quant, alpha
from trader.fundamentals import score_overview


# --------------------------- technical -------------------------------------- #
def test_ta_uptrend_is_bullish():
    up = [100 * (1.01 ** i) for i in range(60)]
    s = ta.ta_signals(up)
    assert s is not None
    assert s.score > 0.4 and s.label in ("buy", "strong_buy")
    assert s.trend_strength > 0.9


def test_ta_downtrend_is_bearish():
    dn = [100 * (0.99 ** i) for i in range(60)]
    s = ta.ta_signals(dn)
    assert s.score < 0 and s.label in ("sell", "strong_sell")


def test_ta_too_little_data_returns_none():
    assert ta.ta_signals([1, 2, 3]) is None


def test_rsi_bounds_and_extremes():
    assert ta.rsi([100 + i for i in range(20)]) > 90      # pure gains
    assert ta.rsi([100 - i for i in range(20)]) < 10      # pure losses


def test_ta_determinism():
    series = [100 + 3 * math.sin(i / 4) for i in range(80)]
    assert ta.ta_signals(series).score == ta.ta_signals(series).score


# --------------------------- quant ------------------------------------------ #
def test_cross_sectional_ranks_trender_top():
    panel = {
        "TREND": [100 * (1.008 ** i) for i in range(260)],
        "FLAT":  [100 + 0.02 * i for i in range(260)],
        "FADER": [100 * (0.997 ** i) for i in range(260)],
    }
    cs = quant.cross_sectional(panel)
    # the clean trender must rank top and outscore both other names;
    # FLAT vs FADER ordering is a legitimate momentum/reversal tension.
    assert cs.ranks[0][0] == "TREND"
    assert cs.scores["TREND"] > cs.scores["FLAT"]
    assert cs.scores["TREND"] > cs.scores["FADER"]


def test_name_stats_sign_and_clamp():
    up = quant.name_stats([100 * (1.01 ** i) for i in range(40)])
    dn = quant.name_stats([100 * (0.99 ** i) for i in range(40)])
    assert up.quant_score > 0 and dn.quant_score < 0
    assert -1.0 <= up.quant_score <= 1.0


def test_zscores_centered():
    z = quant.zscores({"a": 1, "b": 2, "c": 3})
    assert abs(sum(z.values())) < 1e-9


# --------------------------- fundamentals ----------------------------------- #
def test_fundamentals_cheap_quality_strong():
    ov = {"Symbol": "X", "PERatio": "9", "PEGRatio": "0.8", "PriceToBookRatio": "1.5",
          "ReturnOnEquityTTM": "0.28", "ProfitMargin": "0.22",
          "QuarterlyRevenueGrowthYOY": "0.18", "QuarterlyEarningsGrowthYOY": "0.25"}
    f = score_overview(ov)
    assert f.fundamental_score > 0.3 and f.value_score > 0 and f.quality_score > 0


def test_fundamentals_handles_missing_fields():
    f = score_overview({"Symbol": "Y"})
    assert -1.0 <= f.fundamental_score <= 1.0


# --------------------------- confluence ------------------------------------- #
def test_confluence_all_agree_passes():
    c = alpha.confluence(ta=0.7, quant=0.6, fundamental=0.3, regime="risk_on")
    assert c.gate_pass and c.side == "buy" and c.agree == 3 and c.size_mult > 0


def test_confluence_conflict_blocks():
    c = alpha.confluence(ta=0.6, quant=-0.4, fundamental=-0.3, regime="neutral")
    assert not c.gate_pass


def test_confluence_weak_under_floor_blocks():
    c = alpha.confluence(ta=0.15, quant=0.12, fundamental=0.05, min_composite=0.20)
    assert not c.gate_pass


def test_confluence_size_scales_with_conviction():
    weak = alpha.confluence(ta=0.3, quant=0.3, fundamental=0.25)
    strong = alpha.confluence(ta=0.95, quant=0.9, fundamental=0.8)
    assert strong.size_mult > weak.size_mult


def test_confluence_no_methods_is_flat():
    c = alpha.confluence()
    assert c.side == "flat" and not c.gate_pass


def test_confluence_regime_shifts_weight_to_fundamentals_in_stress():
    # same scores, fundamentals bearish: high_vol should pull composite lower
    neutral = alpha.confluence(ta=0.5, quant=0.4, fundamental=-0.6, regime="neutral")
    stress = alpha.confluence(ta=0.5, quant=0.4, fundamental=-0.6, regime="high_vol")
    assert stress.composite < neutral.composite
