"""Map pillar scores to a named regime.

The rules are applied in order. They were written before the backtest and
are not coefficients fitted on returns. Growth and inflation are the axes.
Liquidity and stress only move a borderline state.
"""

from __future__ import annotations

REGIME_LABELS = {
    "Expansion": "Espansione",
    "Reflation": "Reflazione",
    "Recovery": "Ripresa",
    "Slowdown": "Rallentamento",
    "Stagflation": "Stagflazione",
    "Recession": "Recessione/Risk-off",
}


def classify_regime(growth: float | None, inflation: float | None, stress: float | None, liquidity: float | None, impulse: float | None) -> str | None:
    """Return a regime code, or None when growth and inflation are both missing."""
    if growth is None and inflation is None:
        return None
    g = 0.0 if growth is None else float(growth)
    i = 0.0 if inflation is None else float(inflation)
    s = 0.0 if stress is None else float(stress)
    liq = 0.0 if liquidity is None else float(liquidity)
    imp = 0.0 if impulse is None else float(impulse)

    if s > 1.25 or (g < -0.8 and s > 0.3):
        return "Recession"
    if g < 0.0 and i > 0.6:
        return "Stagflation"
    if imp > 0.45 and g < 0.45 and i < 0.9 and s < 1.0:
        return "Recovery"
    if imp < -0.45 and g > -0.7:
        return "Slowdown"
    if g > 0.0 and i > 0.5:
        return "Reflation" if liq >= -0.2 else "Stagflation"
    if g > 0.0:
        return "Expansion"
    return "Slowdown"


def regime_text(code: str | None) -> str:
    if code is None:
        return "Lettura non disponibile: mancano crescita e inflazione."
    label = REGIME_LABELS.get(code, code)
    return (
        f"Regime globale {label}. È la posizione sui pilastri di crescita e inflazione, "
        "con liquidità e stress che spostano solo i casi vicini alla soglia. "
        "Non è una previsione e non è una raccomandazione."
    )
