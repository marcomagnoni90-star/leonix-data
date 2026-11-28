"""Allocation maps for the global macro regime.

Weights are round and fixed before the backtest. They were not searched on
returns. Equity never exceeds EQUITY_CEILING. Cash yield in the backtest is
zero, the same assumption as the published Run 3.
"""

from __future__ import annotations

EQUITY_CEILING = 0.90
ONE_WAY_COST = 0.001  # 10 bp, same as the published backtest

EQUITY_SLEEVES = ("SPY", "EFA", "VGK", "EWJ", "FXI", "EEM")

# Base mix by regime. Each row sums to 1.
ALLOCATION = {
    "Expansion": {
        "SPY": 0.22, "EFA": 0.16, "VGK": 0.10, "EWJ": 0.08, "FXI": 0.04, "EEM": 0.08,
        "TLT": 0.08, "TIP": 0.06, "LQD": 0.06, "GLD": 0.06, "DBC": 0.02, "CASH": 0.04,
    },
    "Reflation": {
        "SPY": 0.20, "EFA": 0.14, "VGK": 0.10, "EWJ": 0.08, "FXI": 0.06, "EEM": 0.10,
        "TLT": 0.04, "TIP": 0.08, "LQD": 0.04, "GLD": 0.08, "DBC": 0.06, "CASH": 0.02,
    },
    "Recovery": {
        "SPY": 0.18, "EFA": 0.14, "VGK": 0.10, "EWJ": 0.08, "FXI": 0.06, "EEM": 0.10,
        "TLT": 0.08, "TIP": 0.06, "LQD": 0.06, "GLD": 0.06, "DBC": 0.04, "CASH": 0.04,
    },
    "Slowdown": {
        "SPY": 0.12, "EFA": 0.08, "VGK": 0.06, "EWJ": 0.04, "FXI": 0.02, "EEM": 0.04,
        "TLT": 0.18, "TIP": 0.12, "LQD": 0.10, "GLD": 0.10, "DBC": 0.02, "CASH": 0.12,
    },
    "Stagflation": {
        "SPY": 0.08, "EFA": 0.05, "VGK": 0.04, "EWJ": 0.03, "FXI": 0.02, "EEM": 0.03,
        "TLT": 0.10, "TIP": 0.18, "LQD": 0.08, "GLD": 0.16, "DBC": 0.10, "CASH": 0.13,
    },
    "Recession": {
        "SPY": 0.06, "EFA": 0.04, "VGK": 0.03, "EWJ": 0.02, "FXI": 0.01, "EEM": 0.02,
        "TLT": 0.28, "TIP": 0.14, "LQD": 0.08, "GLD": 0.14, "DBC": 0.02, "CASH": 0.16,
    },
}

# Used when growth or inflation cannot be scored.
FALLBACK_REGIME = "Slowdown"

# How far a regional equity sleeve may move toward a stronger region, in
# fraction of the equity bucket. Fixed, not fitted.
REGION_TILT = 0.15

# Market prices (12-month momentum and drawdown) may not take more than this
# share of a pillar. Fixed before the corrected backtest, not fitted.
PRICE_CAP = 1.0 / 3.0

# A region in Recession/Risk-off is underweighted in two steps, both in this
# file. First its equity sleeve is multiplied by RISK_OFF_SLEEVE_SCALE.
# Sleeves shared by several regions (VGK) use the GDP-weighted average of
# those scales. The weight removed from equity is parked in RISK_OFF_PROCEEDS.
# Then, if q is the GDP share of regions in Recession/Risk-off, total equity
# is multiplied by (1 - RISK_OFF_EQUITY_CUT * q) and the proceeds are parked
# the same way. The regime names themselves are not an input to this cut
# except through the Recession code.
RISK_OFF_REGIMES = ("Recession",)
RISK_OFF_SLEEVE_SCALE = 0.50
RISK_OFF_EQUITY_CUT = 0.25
RISK_OFF_PROCEEDS = {"TLT": 0.50, "GLD": 0.25, "CASH": 0.25}

# Pillars in the regional health score. Signs: positive helps health.
# Missing pillars are dropped and the remaining weights are rescaled.
HEALTH_WEIGHTS = {
    "growth": 0.35,
    "inflation": -0.20,
    "policy": -0.15,
    "liquidity": 0.15,
    "stress": -0.15,
}
