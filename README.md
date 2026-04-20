# RIPI Starter Pack

This repo gives you a local starting point for a `Real-Time Inflation Perception Index` workflow:

- a live BLS ingestion script
- a daily panel generated from official monthly series
- a script that normalizes categories to a base date
- a weighted composite RIPI calculation
- an output CSV you can use for charts or a site

## Files

- [fetch_real_data.py](/Users/gurmandhaliwal/cpi/fetch_real_data.py)
- [data/ripi_data.csv](/Users/gurmandhaliwal/cpi/data/ripi_data.csv)
- [data/cpi.csv](/Users/gurmandhaliwal/cpi/data/cpi.csv)
- [build_ripi.py](/Users/gurmandhaliwal/cpi/build_ripi.py)
- [requirements.txt](/Users/gurmandhaliwal/cpi/requirements.txt)

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python fetch_real_data.py
python build_ripi.py
```

The script writes:

- `data/ripi_data.csv`
- `data/cpi.csv`
- `output/monthly_source_data.csv`
- `output/ripi_index.csv`

`fetch_real_data.py` pulls official BLS series, converts the monthly data to a forward-filled daily panel, and writes the inputs that `build_ripi.py` consumes.

## Starter Schema

The source dataset is structured as a daily panel:

| date | gas_price | uber_ride | groceries_basket | rent_index | streaming_cost |
| --- | --- | --- | --- | --- | --- |
| 2026-04-01 | 4.85 | 18.20 | 12.40 | 2150 | 78 |

Each metric is normalized to `100` on the base date, then combined into a weighted composite index.

The live pipeline uses monthly official source data and forward-fills each value across the calendar days until the next monthly release.

## Default Weights

These are the initial weights used in `build_ripi.py`:

- `gas_price`: `0.15`
- `uber_ride`: `0.15`
- `groceries_basket`: `0.25`
- `rent_index`: `0.30`
- `streaming_cost`: `0.15`

You should treat these as a starting assumption, not a final methodology.

## Live Series Mapping

`fetch_real_data.py` uses the BLS public API and the following source series:

- `gas_price`: `APU000074714` average price of unleaded regular gasoline
- `groceries_basket`: sum of:
  - `APU0000709112` milk
  - `APU0000702111` bread
  - `APU0000708111` eggs
  - `APU0000FF1101` boneless chicken breast
- `uber_ride`: `CUSR0000SETG` public transportation CPI proxy
- `rent_index`: `CUSR0000SEHA` rent of primary residence CPI
- `streaming_cost`: `CUSR0000SERA02` cable, satellite, and live streaming television CPI
- `cpi`: `CUSR0000SA0` headline CPI

## Important Caveat

`uber_ride` is not a direct Uber fare feed. Uber does not provide a stable public fare-estimate API for a simple unauthenticated pull, so the live pipeline currently uses the official BLS public transportation CPI as a transport-cost proxy while keeping the existing column name for schema compatibility.

## TSE Cap Table Simulation

`simulate_tse_cap_table.py` reads `TSE Cap Table .xlsx`, runs a Monte Carlo exit-value simulation, and writes a standalone HTML dashboard plus structured outputs into `output/`.

```bash
python3 simulate_tse_cap_table.py
```

Optional knobs:

- `--runs 100000`
- `--ebitda-cv 0.25`
- `--debt-volatility 0.04`
- `--output-dir output`

Generated artifacts:

- `output/tse_cap_table_simulation.json`
- `output/tse_cap_table_layer_stats.csv`
- `output/tse_cap_table_instrument_stats.csv`
- `output/tse_cap_table_scenarios.csv`
- `output/tse_cap_table_report.html`
