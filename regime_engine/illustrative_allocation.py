"""One illustrative allocation for every user.

The shares are round and public. They were not searched on returns.
A named expansion is mostly global equity. A panic is mostly ballast.
Two modifiers reuse thresholds that already define the regime, so they
are not new fitted knobs:

- Repair: a Crash or Panic month that closed up, with the VIX back under
  the crash level of 32 and without a high-yield spread of 6 points, uses
  the stress map instead of the panic map.
- Credit veto: an otherwise calm label with a high-yield spread of at least
  6 points uses the stress map.

The published book is asset classes. Tickers are examples of those classes.
"""

from __future__ import annotations

import pandas as pd

from regime_engine.observable_regime import HY_SPREAD_GENUINE, VIX_CRASH

CLASS_ORDER = ["Azionario globale", "Obbligazionario", "Oro", "Liquidità"]

# Equity, bonds, gold, cash. Each row sums to 1.
CLASS_MAP = {
    "Expansion": (0.80, 0.15, 0.05, 0.00),
    "Bubble Risk": (0.70, 0.15, 0.10, 0.05),
    "Neutral": (0.60, 0.25, 0.10, 0.05),
    "Deleveraging": (0.40, 0.30, 0.15, 0.15),
    "Stress": (0.35, 0.35, 0.15, 0.15),
    "Repair": (0.35, 0.35, 0.15, 0.15),
    "Crash": (0.20, 0.40, 0.20, 0.20),
    "Panic": (0.10, 0.35, 0.25, 0.30),
}

# How fast the implemented book moves toward the new map.
# A cut is done in one month. An increase covers three quarters of the gap.
# A small change keeps the older halfway step. Round choices, not a search.
CUT_BLEND = 1.0
ADD_BLEND = 0.75
HOLD_BLEND = 0.50
BLEND_BAND = 0.05

CLASS_NOTES = {
    "Expansion": (
        "Storicamente, quando il modello legge un'espansione, questa mappa tiene "
        "l'azionario globale all'80% e lascia un piccolo stabilizzatore in obbligazioni e oro."
    ),
    "Bubble Risk": (
        "Con un rialzo molto tirato e volatilità bassa, la mappa resta perlopiù azionaria "
        "ma tiene un po' più di oro e di liquidità rispetto all'espansione."
    ),
    "Neutral": (
        "Quando trend, volatilità e drawdown non raccontano la stessa storia, "
        "la mappa è un 60% azionario con più obbligazioni."
    ),
    "Deleveraging": (
        "Se i tassi salgono e l'azionario a sei mesi è negativo, la mappa riduce "
        "l'azionario e alza liquidità e oro."
    ),
    "Stress": (
        "Con volatilità elevata o una correzione, la mappa sta intorno a un terzo "
        "di azionario e un terzo di obbligazioni."
    ),
    "Repair": (
        "Il regime resta di crisi perché il prezzo è ancora lontano dal massimo, "
        "ma l'ultimo mese è positivo e il VIX è sotto 32, senza uno spread high yield da 6 punti. "
        "In quel caso la mappa usa i pesi dello stress, non quelli del panico."
    ),
    "Crash": (
        "Con un drawdown da crash o un mese molto negativo, la mappa scende al 20% di azionario."
    ),
    "Panic": (
        "Con il VIX da panico o un drawdown oltre il 30%, la mappa scende al 10% di azionario "
        "e alza oro e liquidità."
    ),
}

CREDIT_NOTE = (
    " Lo spread high yield è almeno a 6 punti, la stessa soglia già usata per uno stress di credito: "
    "la mappa dell'espansione o del neutro non viene usata."
)


def allocation_key(regime: str, observations: dict | None) -> tuple[str, str]:
    """Return ``(map name, reason tag)`` from the coincident regime."""
    obs = observations or {}
    hy = obs.get("hy_spread")
    credit = hy is not None and float(hy) >= HY_SPREAD_GENUINE
    if regime in {"Expansion", "Neutral", "Bubble Risk"} and credit:
        return "Stress", "credit"
    if regime in {"Crash", "Panic"}:
        vix = obs.get("vix")
        spy_1m = obs.get("spy_1m")
        month_up = spy_1m is not None and float(spy_1m) > 0.0
        vix_eased = vix is not None and float(vix) < VIX_CRASH
        if month_up and vix_eased and not credit:
            return "Repair", "repair"
    if regime not in CLASS_MAP:
        return "Neutral", "regime"
    return regime, "regime"


def class_weights(key: str) -> pd.DataFrame:
    shares = CLASS_MAP[key]
    frame = pd.DataFrame({"classe": CLASS_ORDER, "peso": shares})
    frame = frame[frame["peso"] > 0].reset_index(drop=True)
    return frame


def example_tickers(key: str) -> pd.DataFrame:
    """Representative instruments. They illustrate the class, they are not a list to trade."""
    shares = dict(zip(CLASS_ORDER, CLASS_MAP[key]))
    rows = [
        ("SPY", shares["Azionario globale"], "Esempio della classe azionario globale"),
        ("TLT", 0.5 * shares["Obbligazionario"], "Esempio della classe obbligazionaria"),
        ("TIP", 0.5 * shares["Obbligazionario"], "Esempio della classe obbligazionaria"),
        ("GLD", shares["Oro"], "Esempio della classe oro"),
        ("CASH", shares["Liquidità"], "Esempio della classe liquidità"),
    ]
    frame = pd.DataFrame(rows, columns=["asset", "weight", "ruolo"])
    frame = frame[frame["weight"] > 1e-8].reset_index(drop=True)
    frame["score"] = 0.0
    return frame


def illustrative_book(regime: str, observations: dict | None = None) -> dict:
    key, tag = allocation_key(regime, observations)
    note = CLASS_NOTES[key]
    if tag == "credit":
        note = CLASS_NOTES["Stress"] + CREDIT_NOTE
    tickers = example_tickers(key)
    return {
        "key": key,
        "tag": tag,
        "note": note,
        "classes": class_weights(key),
        "tickers": tickers[["asset", "weight", "score"]],
        "examples": tickers,
    }


def equity_fraction(weights: pd.Series) -> float:
    if weights is None or len(weights) == 0:
        return 0.0
    total = 0.0
    for asset, weight in weights.items():
        if str(asset).upper() in {"SPY", "ACWI"}:
            total += float(weight)
    return total


def implementation_blend(previous_equity: float, target_equity: float) -> float:
    """Move fully when cutting risk, and three quarters of the way when adding it."""
    if target_equity < previous_equity - BLEND_BAND:
        return CUT_BLEND
    if target_equity > previous_equity + BLEND_BAND:
        return ADD_BLEND
    return HOLD_BLEND
