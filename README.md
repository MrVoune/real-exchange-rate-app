# real-exchange-rate-app

Exchange Rate APP for International Finance: a Streamlit app that computes and plots the bilateral **real exchange rate** between two countries using monthly IMF data.

## What it does

- Choose **Country 1** and **Country 2** from IMF countries and the Euro area (IMF code `G163`).
- Downloads monthly **CPI** and **exchange-rate** data from the IMF SDMX 3.0 API, from January 1976 where available.
- Calculates:
  - `S(t)` = units of Country 2 currency per 1 unit of Country 1 currency
    = (Country 2 per USD) / (Country 1 per USD)
  - `RER(t) = S(t) × P1(t) / P2(t)`
  - `RER*` = average of `RER(t)` over valid months in the selected start and end months (recalculated whenever the dates change)
  - `Real Index(t) = 100 × RER(t) / RER*`
- Interactive chart: **Real**, **Nominal** (S in actual currency units, not normalized), or **Both** (separate y-axes).
- **CSV download** of all observations and calculations in the selected sample.

## Data source

IMF SDMX 3.0 API: `https://api.imf.org/external/sdmx/3.0`

| Series | Dataflow | Key |
|---|---|---|
| Monthly CPI (index) | `CPI` | `{ISO3}.CPI._T.IX.M` |
| Exchange rate, local currency per USD, period average | `ER` | `{ISO3}.XDC_USD.PA_RT.M` |

The IMF has no `CPI` series for the Euro area, so for `G163` the app uses the HICP index (`G163.HICP._T.IX.M`, from December 1999).

For euro members (Germany, France, ...), the IMF's own exchange rate stops when the country joins the euro. After that month the app uses the Euro area rate times the fixed euro conversion rate (for example 1.95583 DEM per EUR), so the series stays in the legacy currency and covers 1976 onward.

The US exchange rate is set to 1 (USD per USD). Only months where all four inputs exist are used. IMF data may have gaps or limited coverage, and live downloads can occasionally fail.

## Run locally (Python 3.12)

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Sign in at https://share.streamlit.io with GitHub.
2. Create app → "Yup, I have an app" → repository `real-exchange-rate-app`, branch `main`, file `app.py`.
3. Advanced settings → Python 3.12, leave Secrets empty → Deploy.

## Check

UK as Country 1 and US as Country 2: S is USD per GBP (about 1.2–1.4 in recent years), and the exported `Real_Index` column averages 100 for any sample period.

Live check on 2026-10-20 (IMF data, 1976-01 to 2026-08, 607 valid months; US CPI for 2025-10 is missing at the IMF, so that month is skipped):

| Month | GBP per USD | UK CPI | US CPI | S = 1 / 0.738599 | RER = S × P1 / P2 | Real Index |
|---|---|---|---|---|---|---|
| 2026-08 | 0.738599 | 143.4 | 153.62144 | 1.353914 USD/GBP | 1.263830 | 100 × 1.263830 / 1.425162 = 88.68 |
