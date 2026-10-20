"""
Real Exchange Rate App (Streamlit)

Data: IMF SDMX 3.0 API (https://api.imf.org/external/sdmx/3.0)
  - Monthly CPI:            dataflow CPI, key {ISO3}.CPI._T.IX.M
                            (Euro area G163 falls back to G163.HICP._T.IX.M)
  - Monthly exchange rate:  dataflow ER,  key {ISO3}.XDC_USD.PA_RT.M
    (local-currency units per 1 USD, period average)

Definitions
  S(t)       = units of Country 2 currency per 1 unit of Country 1 currency
             = (C2 per USD) / (C1 per USD)
  RER(t)     = S(t) * P1(t) / P2(t)
  RER*       = average of RER(t) over valid months in the selected sample
  Real Index = 100 * RER(t) / RER*
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

BASE_URL = "https://api.imf.org/external/sdmx/3.0"
START_PERIOD = "1976-01"
TIMEOUT = 60

# ISO3 code -> display name. "G163" is the IMF code for the Euro area.
COUNTRIES = {
    "G163": "Euro area",
    "ARG": "Argentina", "AUS": "Australia", "AUT": "Austria", "BEL": "Belgium",
    "BGD": "Bangladesh", "BGR": "Bulgaria", "BRA": "Brazil", "CAN": "Canada",
    "CHE": "Switzerland", "CHL": "Chile", "CHN": "China", "COL": "Colombia",
    "CRI": "Costa Rica", "CZE": "Czech Republic", "DEU": "Germany",
    "DNK": "Denmark", "DOM": "Dominican Republic", "DZA": "Algeria",
    "ECU": "Ecuador", "EGY": "Egypt", "ESP": "Spain", "EST": "Estonia",
    "FIN": "Finland", "FRA": "France", "GBR": "United Kingdom", "GHA": "Ghana",
    "GRC": "Greece", "GTM": "Guatemala", "HKG": "Hong Kong SAR",
    "HRV": "Croatia", "HUN": "Hungary", "IDN": "Indonesia", "IND": "India",
    "IRL": "Ireland", "ISL": "Iceland", "ISR": "Israel", "ITA": "Italy",
    "JAM": "Jamaica", "JOR": "Jordan", "JPN": "Japan", "KAZ": "Kazakhstan",
    "KEN": "Kenya", "KOR": "Korea, Rep.", "KWT": "Kuwait", "LKA": "Sri Lanka",
    "LTU": "Lithuania", "LUX": "Luxembourg", "LVA": "Latvia", "MAR": "Morocco",
    "MEX": "Mexico", "MYS": "Malaysia", "NGA": "Nigeria", "NLD": "Netherlands",
    "NOR": "Norway", "PAK": "Pakistan", "PER": "Peru",
    "PHL": "Philippines", "POL": "Poland", "PRT": "Portugal", "PRY": "Paraguay",
    "QAT": "Qatar", "ROU": "Romania", "RUS": "Russian Federation",
    "SAU": "Saudi Arabia", "SGP": "Singapore", "SRB": "Serbia",
    "SVK": "Slovak Republic", "SVN": "Slovenia", "SWE": "Sweden",
    "THA": "Thailand", "TUN": "Tunisia", "TUR": "Turkiye",
    "UKR": "Ukraine", "URY": "Uruguay", "USA": "United States",
    "VNM": "Vietnam", "ZAF": "South Africa",
}

CURRENCY = {
    "G163": "EUR", "GBR": "GBP", "USA": "USD", "JPN": "JPY", "CHN": "CNY",
    "CAN": "CAD", "AUS": "AUD", "CHE": "CHF", "IND": "INR", "KOR": "KRW",
    "MEX": "MXN", "BRA": "BRL", "SWE": "SEK", "NOR": "NOK", "DNK": "DKK",
    "NZL": "NZD", "ZAF": "ZAR", "TUR": "TRY", "RUS": "RUB", "SGP": "SGD",
    "HKG": "HKD", "POL": "PLN", "HUN": "HUF", "CZE": "CZK", "ISR": "ILS",
    "THA": "THB", "IDN": "IDR", "MYS": "MYR", "PHL": "PHP", "ARG": "ARS",
    "CHL": "CLP", "COL": "COP", "PER": "PEN", "EGY": "EGP", "NGA": "NGN",
    "PAK": "PKR", "SAU": "SAR", "TWN": "TWD", "VNM": "VND", "ISL": "ISK",
}


# Euro members: the IMF stops their own exchange rate when they join the euro.
# After that we use the Euro area rate (G163) times the fixed conversion rate,
# so the whole series stays in the legacy national currency.
# iso3 -> (legacy currency, units per 1 EUR, first month in the euro)
EURO_LEGACY = {
    "AUT": ("ATS", 13.7603, "1999-01"), "BEL": ("BEF", 40.3399, "1999-01"),
    "DEU": ("DEM", 1.95583, "1999-01"), "ESP": ("ESP", 166.386, "1999-01"),
    "FIN": ("FIM", 5.94573, "1999-01"), "FRA": ("FRF", 6.55957, "1999-01"),
    "IRL": ("IEP", 0.787564, "1999-01"), "ITA": ("ITL", 1936.27, "1999-01"),
    "LUX": ("LUF", 40.3399, "1999-01"), "NLD": ("NLG", 2.20371, "1999-01"),
    "PRT": ("PTE", 200.482, "1999-01"), "GRC": ("GRD", 340.750, "2001-01"),
    "SVN": ("SIT", 239.640, "2007-01"), "SVK": ("SKK", 30.1260, "2009-01"),
    "EST": ("EEK", 15.6466, "2011-01"), "LVA": ("LVL", 0.702804, "2014-01"),
    "LTU": ("LTL", 3.45280, "2015-01"), "HRV": ("HRK", 7.53450, "2023-01"),
}
for _iso, (_cur, _rate, _start) in EURO_LEGACY.items():
    CURRENCY.setdefault(_iso, _cur)


def currency_label(iso3: str) -> str:
    return CURRENCY.get(iso3, f"{iso3} currency")


# ---------------------------------------------------------------------------
# IMF SDMX 3.0 download and parsing
# ---------------------------------------------------------------------------

_PERIOD_RE = re.compile(r"^(\d{4})-?M?(\d{1,2})$")


def parse_period(value) -> pd.Timestamp | None:
    """Turn '2020-M01', '2020-01' or '2020M1' into the first day of that month."""
    m = _PERIOD_RE.match(str(value).strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return pd.Timestamp(year=year, month=month, day=1)


def _to_series(pairs) -> pd.Series:
    rows = {}
    for period, value in pairs:
        ts = parse_period(period)
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        if ts is not None and pd.notna(val):
            rows[ts] = val
    s = pd.Series(rows, dtype="float64").sort_index()
    s.index.name = "date"
    return s


def parse_sdmx_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), dtype=str)
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        raise ValueError("CSV response has no TIME_PERIOD/OBS_VALUE columns")
    return _to_series(zip(df["TIME_PERIOD"], df["OBS_VALUE"]))


def parse_sdmx_json(payload: dict) -> pd.Series:
    """Parse SDMX-JSON (2.0 / 1.0) data messages."""
    data = payload.get("data", payload)
    structures = data.get("structures") or [data.get("structure", {})]
    structure = structures[0] if structures else {}
    obs_dims = structure.get("dimensions", {}).get("observation", [])
    time_values = []
    for dim in obs_dims:
        if dim.get("id") == "TIME_PERIOD":
            time_values = [v.get("id", v.get("value")) for v in dim.get("values", [])]
    if not time_values:
        raise ValueError("JSON response has no TIME_PERIOD values")

    best = pd.Series(dtype="float64")
    for dataset in data.get("dataSets", []):
        for series in (dataset.get("series") or {}).values():
            pairs = []
            for idx, obs in (series.get("observations") or {}).items():
                i = int(str(idx).split(":")[-1])
                if i < len(time_values) and obs:
                    pairs.append((time_values[i], obs[0]))
            s = _to_series(pairs)
            if len(s) > len(best):
                best = s
    return best


def parse_sdmx_xml(text: str) -> pd.Series:
    """Parse structure-specific or generic SDMX-ML data messages."""
    root = ET.fromstring(text)
    best = pd.Series(dtype="float64")
    for series in root.iter():
        if not series.tag.endswith("Series"):
            continue
        pairs = []
        for obs in series:
            if not obs.tag.endswith("Obs"):
                continue
            period = obs.attrib.get("TIME_PERIOD")
            value = obs.attrib.get("OBS_VALUE")
            for child in obs:  # generic format
                if child.tag.endswith("ObsDimension"):
                    period = child.attrib.get("value", period)
                elif child.tag.endswith("ObsValue"):
                    value = child.attrib.get("value", value)
            pairs.append((period, value))
        s = _to_series(pairs)
        if len(s) > len(best):
            best = s
    return best


def fetch_imf_series(dataflow: str, key: str) -> pd.Series:
    """Download one monthly series from the IMF SDMX 3.0 API."""
    urls = [
        f"{BASE_URL}/data/dataflow/IMF.STA/{dataflow}/+/{key}",
        f"{BASE_URL}/data/dataflow/*/{dataflow}/+/{key}",
    ]
    formats = [
        ("application/vnd.sdmx.data+csv;version=2.0.0", "csv"),
        ("application/vnd.sdmx.data+json;version=2.0.0", "json"),
        ("application/vnd.sdmx.structurespecificdata+xml;version=3.0.0", "xml"),
        ("application/xml", "xml"),
    ]
    errors = []
    for url in urls:
        for accept, kind in formats:
            try:
                r = requests.get(
                    url,
                    headers={"Accept": accept, "User-Agent": "real-exchange-rate-app"},
                    timeout=TIMEOUT,
                )
                if r.status_code == 404:
                    errors.append(f"404 (no data) {url}")
                    break  # try the next URL; other formats will also be 404
                r.raise_for_status()
                text = r.text.strip()
                if text.startswith("{"):
                    s = parse_sdmx_json(r.json())
                elif text.startswith("<"):
                    s = parse_sdmx_xml(text)
                else:
                    s = parse_sdmx_csv(text)
                if not s.empty:
                    return s[s.index >= pd.Timestamp(START_PERIOD + "-01")]
                errors.append(f"empty {kind} response from {url}")
            except Exception as exc:  # network error, bad format, ...
                errors.append(f"{kind}: {type(exc).__name__}: {exc}")
    raise RuntimeError(f"Could not download {dataflow} {key}. " + " | ".join(errors[-3:]))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_cpi(iso3: str) -> pd.Series:
    """Headline CPI index. The IMF stores the Euro area (G163) only as HICP,
    so fall back to {ISO3}.HICP._T.IX.M when the CPI key has no data."""
    try:
        return fetch_imf_series("CPI", f"{iso3}.CPI._T.IX.M")
    except RuntimeError as cpi_error:
        try:
            return fetch_imf_series("CPI", f"{iso3}.HICP._T.IX.M")
        except RuntimeError:
            raise cpi_error


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_fx_per_usd(iso3: str) -> pd.Series:
    """Local-currency units per 1 USD. The US is 1 by definition."""
    if iso3 == "USA":
        cpi = get_cpi("USA")
        idx = pd.date_range(START_PERIOD + "-01", cpi.index.max(), freq="MS")
        return pd.Series(1.0, index=idx, name="USD per USD")
    own = fetch_imf_series("ER", f"{iso3}.XDC_USD.PA_RT.M")
    if iso3 in EURO_LEGACY:
        _, rate, start = EURO_LEGACY[iso3]
        eur = fetch_imf_series("ER", "G163.XDC_USD.PA_RT.M")
        eur = eur[eur.index >= pd.Timestamp(start + "-01")] * rate
        own = own.combine_first(eur)  # IMF's own value wins where it exists
    return own


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def build_table(fx1: pd.Series, fx2: pd.Series, p1: pd.Series, p2: pd.Series) -> pd.DataFrame:
    """Combine inputs and compute S(t) and RER(t) on months where all data exist."""
    df = pd.concat(
        {
            "FX1_per_USD": fx1,
            "FX2_per_USD": fx2,
            "P1_CPI": p1,
            "P2_CPI": p2,
        },
        axis=1,
        sort=True,
    )
    df.index.name = "date"
    # S(t): units of country-2 currency per 1 unit of country-1 currency
    df["S"] = df["FX2_per_USD"] / df["FX1_per_USD"]
    df["RER"] = df["S"] * df["P1_CPI"] / df["P2_CPI"]
    return df.dropna(subset=["S", "RER"])


def add_real_index(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, float]:
    sample = df.loc[(df.index >= start) & (df.index <= end)].copy()
    rer_star = float(sample["RER"].mean())
    sample["RER_star"] = rer_star
    sample["Real_Index"] = 100 * sample["RER"] / rer_star
    return sample, rer_star


# ---------------------------------------------------------------------------
# User interface
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Real Exchange Rate App", layout="wide")
    st.title("Real Exchange Rate App")
    st.caption("Monthly CPI and exchange-rate data from the IMF SDMX 3.0 API, January 1976 onward where available.")

    codes = list(COUNTRIES.keys())
    label = lambda c: f"{COUNTRIES[c]} ({c})"

    with st.sidebar:
        st.header("Countries")
        c1 = st.selectbox("Country 1", codes, index=codes.index("GBR"), format_func=label)
        c2 = st.selectbox("Country 2", codes, index=codes.index("USA"), format_func=label)
        view = st.radio("Chart", ["Real", "Nominal", "Both"], index=2, horizontal=True)

    if c1 == c2:
        st.warning("Please choose two different countries.")
        st.stop()

    cur1, cur2 = currency_label(c1), currency_label(c2)

    try:
        with st.spinner("Downloading IMF data..."):
            p1, p2 = get_cpi(c1), get_cpi(c2)
            fx1, fx2 = get_fx_per_usd(c1), get_fx_per_usd(c2)
    except Exception as exc:
        st.error(f"IMF download failed: {exc}")
        st.info("The IMF API sometimes fails or has no data for a country. Try again later or pick another country.")
        st.stop()

    full = build_table(fx1, fx2, p1, p2)
    if full.empty:
        st.error("No months where CPI and exchange-rate data exist for both countries.")
        st.stop()

    months = list(full.index)
    with st.sidebar:
        st.header("Sample period")
        start, end = st.select_slider(
            "Start and end month",
            options=months,
            value=(months[0], months[-1]),
            format_func=lambda d: d.strftime("%Y-%m"),
        )

    sample, rer_star = add_real_index(full, start, end)

    st.subheader(f"{COUNTRIES[c1]} (Country 1) vs {COUNTRIES[c2]} (Country 2)")
    st.markdown(
        f"**S(t)** = {cur2} per 1 {cur1} &nbsp;|&nbsp; **RER(t)** = S(t) × P1(t) / P2(t) "
        f"&nbsp;|&nbsp; **Real Index(t)** = 100 × RER(t) / RER*"
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Valid months", f"{len(sample)}")
    m2.metric("RER* (sample mean)", f"{rer_star:,.4f}")
    m3.metric("Mean Real Index", f"{sample['Real_Index'].mean():.2f}")
    m4.metric(f"Latest S ({cur2}/{cur1})", f"{sample['S'].iloc[-1]:,.4f}")

    real_name = "Real Index (sample mean = 100)"
    nominal_name = f"Nominal S ({cur2} per {cur1})"
    if view == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=sample.index, y=sample["Real_Index"], name=real_name), secondary_y=False)
        fig.add_trace(go.Scatter(x=sample.index, y=sample["S"], name=nominal_name), secondary_y=True)
        fig.update_yaxes(title_text="Real Index", secondary_y=False)
        fig.update_yaxes(title_text=f"{cur2} per {cur1}", secondary_y=True)
    elif view == "Real":
        fig = go.Figure(go.Scatter(x=sample.index, y=sample["Real_Index"], name=real_name))
        fig.add_hline(y=100, line_dash="dot")
        fig.update_yaxes(title_text="Real Index")
    else:
        fig = go.Figure(go.Scatter(x=sample.index, y=sample["S"], name=nominal_name))
        fig.update_yaxes(title_text=f"{cur2} per {cur1}")
    fig.update_layout(hovermode="x unified", height=520, legend=dict(orientation="h", y=1.1))
    fig.update_xaxes(title_text="Month")
    st.plotly_chart(fig, width="stretch")

    export = sample.reset_index()
    export["date"] = export["date"].dt.strftime("%Y-%m")
    export = export.rename(
        columns={
            "FX1_per_USD": f"FX1_{c1}_{cur1}_per_USD",
            "FX2_per_USD": f"FX2_{c2}_{cur2}_per_USD",
            "P1_CPI": f"P1_CPI_{c1}",
            "P2_CPI": f"P2_CPI_{c2}",
            "S": f"S_{cur2}_per_{cur1}",
        }
    )
    st.download_button(
        "Download CSV (selected sample)",
        export.to_csv(index=False).encode("utf-8"),
        file_name=f"rer_{c1}_{c2}_{start:%Y%m}_{end:%Y%m}.csv",
        mime="text/csv",
    )
    with st.expander("Show data table"):
        st.dataframe(export, width="stretch", hide_index=True)

    st.caption(
        "IMF exchange rates are local-currency units per USD (period average). "
        "For euro members, months after euro entry use the Euro area rate times the fixed "
        "conversion rate, so S stays in the legacy currency. "
        "S(t) = (Country 2 per USD) / (Country 1 per USD). IMF data may have gaps; "
        "only months with all four inputs are used."
    )


if __name__ == "__main__":
    main()
