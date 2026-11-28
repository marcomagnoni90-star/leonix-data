"""Series the global model is allowed to use.

Government, central-bank, OECD (CC BY 4.0), BIS and World Bank sources.
Third-party licensed series are listed in EXCLUDED and are not fetched.
Equity prices from Yahoo are internal to the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    region: str
    pillar: str
    name: str
    source: str
    license: str
    frequency: str
    lag_months: int
    transform: str  # level | yoy | diff6
    sign: int
    weight: float = 1.0
    kind: str = "official"
    fred_id: str | None = None


def _s(**kwargs) -> SeriesSpec:
    return SeriesSpec(**kwargs)


OECD = "OECD Data Explorer, CC BY 4.0"
OECD_SRC = "OECD KEI / short-term statistics"
# A monthly print dated month t is used from month t+lag.
# OECD prints are lagged two months. BLS and the Fed's G.17 are lagged one.
# Rates, yields, FX, stress indexes and the Fed's weekly balance sheet are
# treated as known at that month's close.

REGION_OECD = {
    "USA": "USA",
    "EZ": "EA20",
    "UK": "GBR",
    "CH": "CHE",
    "JP": "JPN",
    "CN": "CHN",
}
EM_OECD = ("BRA", "IND", "IDN", "MEX", "TUR", "ZAF")

# suffix, measure, unit, activity, adjustment, transformation,
# pillar, name, transform, sign, lag
OECD_VARS = (
    ("IP", "PRVM", "IX", "C", "Y", "_Z", "growth", "Manufacturing production", "yoy", 1, 2),
    ("RS", "RS", "IX", "_T", "RT", "_Z", "growth", "Real retail sales", "yoy", 1, 2),
    ("UN", "UNEMP", "PT_LF", "_T", "Y", "_Z", "growth", "Unemployment rate", "level", -1, 2),
    ("CLI", "LI", "IX", "_T", "AA", "_Z", "growth", "OECD CLI, amplitude adjusted", "level", 1, 2),
    ("CPI", "CP", "IX", "_Z", "_Z", "_Z", "inflation", "Consumer price index", "yoy", 1, 2),
    ("RATE", "IRSTCI", "PA", "_Z", "_Z", "_Z", "policy", "Immediate policy rate", "level", 1, 0),
    ("M2", "MABM", "IX", "_Z", "Y", "_Z", "liquidity", "Broad money", "yoy", 1, 2),
)


def _build() -> list[SeriesSpec]:
    specs: list[SeriesSpec] = []
    for region in REGION_OECD:
        for suffix, _m, _u, _a, _j, _t, pillar, name, kind, sign, lag in OECD_VARS:
            if region == "EZ" and suffix == "RATE":
                continue
            fred = None
            if region == "USA" and suffix == "UN":
                fred = "UNRATE"
            elif region == "USA" and suffix == "CPI":
                fred = "CPIAUCSL"
            elif region == "USA" and suffix == "IP":
                fred = "INDPRO"
            elif region == "USA" and suffix == "M2":
                fred = "M2SL"
            elif region == "USA" and suffix == "RATE":
                fred = "FEDFUNDS"
            specs.append(_s(
                series_id=f"{region}_{suffix}",
                region=region,
                pillar=pillar,
                name=name,
                source=OECD_SRC,
                license=OECD,
                frequency="monthly",
                lag_months=lag,
                transform=kind,
                sign=sign,
                fred_id=fred,
            ))
        specs.append(_s(
            series_id=f"{region}_SLOPE",
            region=region,
            pillar="policy",
            name="Long rate minus 3-month rate",
            source=OECD_SRC,
            license=OECD,
            frequency="monthly",
            lag_months=0,
            transform="level",
            sign=-1,
            fred_id="T10Y3M" if region == "USA" else None,
        ))
    for area in EM_OECD:
        for suffix, pillar, name, kind, sign in (
            ("IP", "growth", "Manufacturing production", "yoy", 1),
            ("UN", "growth", "Unemployment rate", "level", -1),
            ("CPI", "inflation", "Consumer price index", "yoy", 1),
            ("EX", "growth", "Exports, USD", "yoy", 1),
        ):
            specs.append(_s(
                series_id=f"EM_{area}_{suffix}",
                region="EM",
                pillar=pillar,
                name=f"{area} {name}",
                source=OECD_SRC,
                license=OECD,
                frequency="monthly",
                lag_months=2,
                transform=kind,
                sign=sign,
            ))
    specs.extend([
        _s(series_id="USA_PAY", region="USA", pillar="growth", name="Nonfarm payrolls", source="BLS CES", license="US government public domain", frequency="monthly", lag_months=1, transform="yoy", sign=1, fred_id="PAYEMS"),
        _s(series_id="USA_CORE", region="USA", pillar="inflation", name="CPI less food and energy", source="BLS CPI", license="US government public domain", frequency="monthly", lag_months=1, transform="yoy", sign=1, fred_id="CPILFESL"),
        _s(series_id="USA_PPI", region="USA", pillar="inflation", name="PPI all commodities", source="BLS PPI", license="US government public domain", frequency="monthly", lag_months=1, transform="yoy", sign=1, fred_id="PPIACO"),
        _s(series_id="USA_BE", region="USA", pillar="inflation", name="10-year breakeven (nominal minus real Treasury)", source="US Treasury", license="US government public domain", frequency="daily", lag_months=0, transform="level", sign=1, fred_id="T10YIE"),
        _s(series_id="USA_S2", region="USA", pillar="policy", name="10-year minus 2-year Treasury", source="Federal Reserve H.15", license="US government public domain", frequency="daily", lag_months=0, transform="level", sign=-1, fred_id="T10Y2Y"),
        _s(series_id="USA_WALCL", region="USA", pillar="liquidity", name="Fed total assets, Wednesday", source="Federal Reserve H.4.1", license="US government public domain", frequency="weekly", lag_months=0, transform="yoy", sign=1, fred_id="WALCL"),
        _s(series_id="USA_NFCI", region="USA", pillar="stress", name="Chicago Fed NFCI", source="FRED NFCI if FRED_API_KEY is set, else Chicago Fed CSV", license="US government public domain", frequency="weekly", lag_months=0, transform="level", sign=1, fred_id="NFCI"),
        _s(series_id="USA_STLFSI", region="USA", pillar="stress", name="St. Louis Fed financial stress", source="FRED STLFSI4 when FRED_API_KEY is set", license="US government public domain", frequency="weekly", lag_months=0, transform="level", sign=1, fred_id="STLFSI4"),
        _s(series_id="USA_INV", region="USA", pillar="stress", name="Treasury inversion, 3-month above 10-year", source="Federal Reserve H.15", license="US government public domain", frequency="daily", lag_months=0, transform="level", sign=1),
        _s(series_id="EZ_DFR", region="EZ", pillar="policy", name="ECB deposit facility rate", source="ECB Data Portal FM", license="ECB, free reuse with attribution", frequency="daily", lag_months=0, transform="level", sign=1, fred_id="ECBDFR"),
        _s(series_id="EZ_CISS", region="EZ", pillar="stress", name="ECB CISS (SS_CIN)", source="ECB Data Portal CISS, series SS_CIN", license="ECB, free reuse with attribution", frequency="daily", lag_months=0, transform="level", sign=1),
        _s(series_id="EZ_EURUSD", region="EZ", pillar="stress", name="USD per EUR", source="ECB Data Portal EXR", license="ECB, free reuse with attribution", frequency="monthly", lag_months=0, transform="yoy", sign=-1),
        _s(series_id="UK_FX", region="UK", pillar="stress", name="USD per GBP", source="ECB Data Portal EXR", license="ECB, free reuse with attribution", frequency="monthly", lag_months=0, transform="yoy", sign=-1),
        _s(series_id="CH_FX", region="CH", pillar="stress", name="CHF per USD", source="ECB Data Portal EXR", license="ECB, free reuse with attribution", frequency="monthly", lag_months=0, transform="yoy", sign=1),
        _s(series_id="JP_FX", region="JP", pillar="stress", name="JPY per USD", source="ECB Data Portal EXR", license="ECB, free reuse with attribution", frequency="monthly", lag_months=0, transform="yoy", sign=1),
        _s(series_id="CN_FX", region="CN", pillar="stress", name="CNY per USD", source="ECB Data Portal EXR", license="ECB, free reuse with attribution", frequency="monthly", lag_months=0, transform="yoy", sign=1),
        _s(series_id="CN_EX", region="CN", pillar="growth", name="China exports, USD", source=OECD_SRC, license=OECD, frequency="monthly", lag_months=2, transform="yoy", sign=1),
        _s(series_id="GL_CLI", region="GLOBAL", pillar="growth", name="OECD area CLI", source=OECD_SRC, license=OECD, frequency="monthly", lag_months=2, transform="level", sign=1),
        _s(series_id="GL_CLI_G20", region="GLOBAL", pillar="growth", name="G20 CLI", source=OECD_SRC, license=OECD, frequency="monthly", lag_months=2, transform="level", sign=1),
        _s(series_id="GL_KOR_IP", region="GLOBAL", pillar="growth", name="Korea manufacturing production", source=OECD_SRC, license=OECD, frequency="monthly", lag_months=2, transform="yoy", sign=1),
        _s(series_id="GL_KOR_EX", region="GLOBAL", pillar="growth", name="Korea exports, USD", source=OECD_SRC, license=OECD, frequency="monthly", lag_months=2, transform="yoy", sign=1),
        _s(series_id="GL_DOLLAR", region="GLOBAL", pillar="stress", name="US real broad dollar", source="BIS effective exchange rates", license="BIS terms, free with attribution", frequency="monthly", lag_months=1, transform="yoy", sign=1, fred_id="DTWEXBGS"),
    ])
    refreshed = []
    for spec in specs:
        if spec.series_id == "EZ_CPI":
            # Same yoy transform and real-rate rule. The print is known the
            # following month, like the US CPI, so the lag is one month.
            spec = replace(
                spec,
                name="Euro area HICP",
                source="FRED CP0000EZ19M086NEST, else Eurostat prc_hicp_minr TOTAL",
                license="Eurostat / FRED, free reuse with attribution",
                lag_months=1,
                fred_id="CP0000EZ19M086NEST",
            )
        elif spec.series_id == "CH_CPI":
            spec = replace(
                spec,
                name="Swiss CPI (LIK)",
                source="BFS LIK, base December 2025",
                license="BFS, free reuse with attribution",
                lag_months=1,
                fred_id=None,
            )
        refreshed.append(spec)
    return refreshed


SPECS: list[SeriesSpec] = _build()

YAHOO_EQUITY = {
    "USA": "SPY",
    "EZ": "VGK",
    "UK": "EWU",
    "CH": "EWL",
    "JP": "EWJ",
    "CN": "FXI",
    "EM": "EEM",
    "GLOBAL": "ACWI",
}

EXCLUDED = [
    {"id": "VIXCLS", "reason": "Licenza Cboe. Non usata. Lo stress usa NFCI fino all'ultima stampa, CISS e il drawdown degli indici."},
    {"id": "NFCI dopo il 2026-04-24", "reason": "Il CSV della Fed di Chicago termina il 24 aprile 2026. STLFSI4 entra nello stress quando FRED_API_KEY è presente: il CSV pubblico di FRED non risponde da questa rete."},
    {"id": "CISS SS_CI", "reason": "La serie ECB SS_CI si ferma a maggio 2025. Sostituita con SS_CIN, aggiornata, stessa licenza BCE."},
    {"id": "BAMLH0A0HYM2", "reason": "Licenza ICE BofA. Non usata. Nessuna opzione attiva."},
    {"id": "BAML*", "reason": "Famiglia ICE BofA. Non scaricata."},
    {"id": "CSUSHPINSA", "reason": "Licenza S&P Case-Shiller. Non usata."},
    {"id": "UMCSENT", "reason": "Licenza University of Michigan. Non usata."},
    {"id": "PMI S&P / Caixin", "reason": "Licenza S&P Global / Caixin. L'attività usa produzione industriale e CLI OCSE."},
    {"id": "LRHUTTTTEZM156S", "reason": "Serie OCSE su FRED ferma. Sostituita dalla disoccupazione EA20 nel KEI OCSE."},
    {"id": "NETLIQ_US", "reason": "WALCL è disponibile (H.4.1). Il conto del Tesoro e l'ON RRP non sono nello stesso file pubblico scaricabile qui, quindi la liquidità netta non viene costruita con zeri al posto dei dati mancanti."},
    {"id": "PCEPILFE", "reason": "Il PCE core è BEA/FRED. Senza CSV FRED e senza chiave BEA non c'è un file pubblico usato in questo run. Resta l'alias FRED se la chiave c'è."},
    {"id": "ALFRED", "reason": "Il CSV ALFRED sullo stesso host di FRED non risponde da questa rete (HTTP/2 chiuso). Il backtest è point-in-time solo tramite i lag di pubblicazione, non tramite vintage."},
    {"id": "EZ_CLI / CH_CLI / GL_CLI", "reason": "Nel KEI OCSE il CLI mensile c'è per USA, Giappone, Cina, G20 e diversi emergenti. Non c'è per l'area euro EA20, la Svizzera e l'aggregato OECD. Non è stato sostituito con un'altra serie."},
    {"id": "CN_IP", "reason": "Il KEI OCSE per la Cina ha la produzione delle costruzioni, non la produzione manifatturiera. Le costruzioni non sono state usate al posto dell'industria. Restano CLI, prezzi, export e tassi."},
]

REGIONS = ("USA", "EZ", "UK", "CH", "JP", "CN", "EM", "GLOBAL")
PILLARS = ("growth", "inflation", "policy", "liquidity", "stress")
