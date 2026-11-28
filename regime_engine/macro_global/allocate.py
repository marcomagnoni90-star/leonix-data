"""Turn a regime and regional scores into ETF weights.

The base row comes from ``config.macro_global.ALLOCATION``. A fixed tilt
then shifts equity toward regions whose health score is above the
cross-section, without raising total equity and without passing the ceiling.
"""

from __future__ import annotations

from config.macro_global import (
    ALLOCATION,
    EQUITY_CEILING,
    EQUITY_SLEEVES,
    FALLBACK_REGIME,
    REGION_TILT,
    RISK_OFF_EQUITY_CUT,
    RISK_OFF_PROCEEDS,
    RISK_OFF_REGIMES,
    RISK_OFF_SLEEVE_SCALE,
)

REGION_SLEEVE = {
    "USA": "SPY",
    "EZ": "VGK",
    "UK": "VGK",
    "CH": "VGK",
    "JP": "EWJ",
    "CN": "FXI",
    "EM": "EEM",
}


def base_weights(regime: str | None) -> dict[str, float]:
    code = regime if regime in ALLOCATION else FALLBACK_REGIME
    raw = dict(ALLOCATION[code])
    total = sum(raw.values())
    if total <= 0:
        return raw
    return {asset: weight / total for asset, weight in raw.items()}


def _equity_total(weights: dict[str, float]) -> float:
    return sum(weights.get(asset, 0.0) for asset in EQUITY_SLEEVES)


def apply_region_tilt(weights: dict[str, float], region_scores: dict[str, float | None]) -> dict[str, float]:
    scored = {region: float(score) for region, score in region_scores.items() if score is not None and region in REGION_SLEEVE}
    if len(scored) < 2:
        return dict(weights)
    mean = sum(scored.values()) / len(scored)
    out = dict(weights)
    equity = _equity_total(out)
    if equity <= 1e-8:
        return out
    shifts = {}
    for region, score in scored.items():
        sleeve = REGION_SLEEVE[region]
        # UK, CH and EZ share VGK. Their signals are averaged by summing and
        # dividing once below.
        shifts.setdefault(sleeve, []).append(score - mean)
    for sleeve, gaps in shifts.items():
        gap = sum(gaps) / len(gaps)
        current = out.get(sleeve, 0.0)
        tilt = REGION_TILT * equity * max(-1.0, min(1.0, gap / 2.0))
        out[sleeve] = max(0.0, current + tilt)
    new_equity = _equity_total(out)
    if new_equity > EQUITY_CEILING:
        scale = EQUITY_CEILING / new_equity
        for sleeve in EQUITY_SLEEVES:
            out[sleeve] = out.get(sleeve, 0.0) * scale
        new_equity = _equity_total(out)
    if new_equity > 1e-8 and abs(new_equity - equity) > 1e-8:
        scale = equity / new_equity
        for sleeve in EQUITY_SLEEVES:
            out[sleeve] = out.get(sleeve, 0.0) * scale
    total = sum(max(0.0, value) for value in out.values())
    if total <= 0:
        return base_weights(None)
    out = {asset: max(0.0, weight) / total for asset, weight in out.items()}
    equity = _equity_total(out)
    if equity > EQUITY_CEILING + 1e-8:
        excess = equity - EQUITY_CEILING
        scale = EQUITY_CEILING / equity
        for sleeve in EQUITY_SLEEVES:
            out[sleeve] = out.get(sleeve, 0.0) * scale
        out["CASH"] = out.get("CASH", 0.0) + excess
        total = sum(out.values())
        out = {asset: weight / total for asset, weight in out.items()}
    return out


def _park(weights: dict[str, float], amount: float) -> None:
    if amount <= 0:
        return
    for asset, share in RISK_OFF_PROCEEDS.items():
        weights[asset] = weights.get(asset, 0.0) + amount * share


def apply_risk_off(
    weights: dict[str, float],
    region_regimes: dict[str, str | None],
    gdp: dict[str, float] | None = None,
) -> dict[str, float]:
    """Underweight Recession/Risk-off regions and shift the mix with their GDP share.

    The sleeve cut and the GDP cut are the constants in ``config.macro_global``.
    A sleeve shared by several regions uses the GDP-weighted average of their
    scales. Proceeds go to the bond, gold and cash shares in that config.
    """
    gdp = gdp or {}
    out = dict(weights)
    groups: dict[str, list[tuple[float, float]]] = {}
    classified: list[tuple[str, str, float]] = []
    for region, sleeve in REGION_SLEEVE.items():
        regime = region_regimes.get(region)
        if not isinstance(regime, str):
            continue
        scale = RISK_OFF_SLEEVE_SCALE if regime in RISK_OFF_REGIMES else 1.0
        weight = float(gdp.get(region, 0.0))
        groups.setdefault(sleeve, []).append((scale, weight))
        classified.append((region, regime, weight))
    if not classified:
        return out
    for sleeve, items in groups.items():
        weight_sum = sum(weight for _, weight in items)
        if weight_sum > 0:
            scale = sum(item_scale * weight for item_scale, weight in items) / weight_sum
        else:
            scale = sum(item_scale for item_scale, _ in items) / len(items)
        before = out.get(sleeve, 0.0)
        out[sleeve] = before * scale
        _park(out, before - out[sleeve])
    equity = _equity_total(out)
    denominator = sum(weight for _, _, weight in classified)
    risk = sum(weight for _, regime, weight in classified if regime in RISK_OFF_REGIMES)
    share = risk / denominator if denominator > 0 else 0.0
    cut = equity * RISK_OFF_EQUITY_CUT * share
    if equity > 1e-8 and cut > 0:
        kept = (equity - cut) / equity
        for sleeve in EQUITY_SLEEVES:
            out[sleeve] = out.get(sleeve, 0.0) * kept
        _park(out, cut)
    total = sum(max(0.0, value) for value in out.values())
    if total <= 0:
        return base_weights(None)
    out = {asset: max(0.0, weight) / total for asset, weight in out.items()}
    equity = _equity_total(out)
    if equity > EQUITY_CEILING + 1e-8:
        excess = equity - EQUITY_CEILING
        scale = EQUITY_CEILING / equity
        for sleeve in EQUITY_SLEEVES:
            out[sleeve] = out.get(sleeve, 0.0) * scale
        out["CASH"] = out.get("CASH", 0.0) + excess
        total = sum(out.values())
        out = {asset: weight / total for asset, weight in out.items()}
    return out
