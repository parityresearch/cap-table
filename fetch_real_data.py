import argparse
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
DATA_DIR = Path("data")
OUTPUT_DIR = Path("output")

RIPI_DATA_PATH = DATA_DIR / "ripi_data.csv"
CPI_DATA_PATH = DATA_DIR / "cpi.csv"
MONTHLY_OUTPUT_PATH = OUTPUT_DIR / "monthly_source_data.csv"

SERIES = {
    "gas_price": {
        "series_id": "APU000074714",
        "kind": "direct",
        "description": "Average Price: Gasoline, Unleaded Regular",
    },
    "milk_price": {
        "series_id": "APU0000709112",
        "kind": "direct",
        "description": "Average Price: Milk, Fresh, Whole, Fortified",
    },
    "bread_price": {
        "series_id": "APU0000702111",
        "kind": "direct",
        "description": "Average Price: Bread, White, Pan",
    },
    "eggs_price": {
        "series_id": "APU0000708111",
        "kind": "direct",
        "description": "Average Price: Eggs, Grade A, Large",
    },
    "chicken_price": {
        "series_id": "APU0000FF1101",
        "kind": "direct",
        "description": "Average Price: Chicken Breast, Boneless",
    },
    "uber_ride": {
        "series_id": "CUSR0000SETG",
        "kind": "proxy_index",
        "description": "CPI proxy for public transportation",
    },
    "rent_index": {
        "series_id": "CUSR0000SEHA",
        "kind": "index",
        "description": "CPI rent of primary residence",
    },
    "streaming_cost": {
        "series_id": "CUSR0000SERA02",
        "kind": "index",
        "description": "CPI cable, satellite, and live streaming television service",
    },
    "cpi": {
        "series_id": "CUSR0000SA0",
        "kind": "index",
        "description": "Headline CPI",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch live RIPI source data from the BLS public API."
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=date.today().year - 5,
        help="First year to request from the BLS API.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=date.today().year,
        help="Last year to request from the BLS API.",
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="Last date to include in the forward-filled daily panel (YYYY-MM-DD).",
    )
    return parser.parse_args()


def fetch_bls_series(start_year: int, end_year: int) -> dict[str, pd.DataFrame]:
    payload = {
        "seriesid": [config["series_id"] for config in SERIES.values()],
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    response = requests.post(BLS_API_URL, json=payload, timeout=60)
    response.raise_for_status()

    body = response.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API request failed: {body}")

    returned_series = body.get("Results", {}).get("series", [])
    if not returned_series:
        raise RuntimeError("BLS API returned no series data")

    parsed: dict[str, pd.DataFrame] = {}
    for series in returned_series:
        series_id = series["seriesID"]
        rows = []
        for point in series.get("data", []):
            period = point.get("period", "")
            if not period.startswith("M") or period == "M13":
                continue
            try:
                value = float(point["value"])
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "date": pd.Timestamp(
                        year=int(point["year"]),
                        month=int(period[1:]),
                        day=1,
                    ),
                    "value": value,
                }
            )
        if rows:
            parsed[series_id] = (
                pd.DataFrame(rows).sort_values("date").drop_duplicates("date")
            )

    missing = [
        config["series_id"]
        for config in SERIES.values()
        if config["series_id"] not in parsed
    ]
    if missing:
        raise RuntimeError(f"Missing series in BLS response: {missing}")

    return parsed


def build_monthly_frame(series_map: dict[str, pd.DataFrame]) -> pd.DataFrame:
    monthly: Optional[pd.DataFrame] = None

    for column, config in SERIES.items():
        frame = series_map[config["series_id"]].rename(columns={"value": column})
        monthly = (
            frame
            if monthly is None
            else monthly.merge(frame, on="date", how="outer")
        )

    if monthly is None:
        raise RuntimeError("No monthly data was assembled")

    monthly = monthly.sort_values("date").reset_index(drop=True)
    monthly = monthly.ffill()
    monthly["groceries_basket"] = (
        monthly["milk_price"]
        + monthly["bread_price"]
        + monthly["eggs_price"]
        + monthly["chicken_price"]
    )
    monthly = monthly.dropna(
        subset=[
            "gas_price",
            "uber_ride",
            "groceries_basket",
            "rent_index",
            "streaming_cost",
            "cpi",
        ]
    ).reset_index(drop=True)
    if monthly.empty:
        raise RuntimeError("No complete monthly observations were available after alignment")
    return monthly


def monthly_to_daily(monthly: pd.DataFrame, end_date: str) -> pd.DataFrame:
    end_timestamp = pd.Timestamp(end_date)
    if end_timestamp < monthly["date"].min():
        raise ValueError("end-date is earlier than the first source observation")

    full_range = pd.date_range(monthly["date"].min(), end_timestamp, freq="D")
    daily = (
        monthly.set_index("date")
        .reindex(full_range)
        .ffill()
        .rename_axis("date")
        .reset_index()
    )
    return daily


def write_outputs(monthly: pd.DataFrame, daily: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ripi_columns = [
        "date",
        "gas_price",
        "uber_ride",
        "groceries_basket",
        "rent_index",
        "streaming_cost",
    ]
    cpi_columns = ["date", "cpi"]

    daily[ripi_columns].to_csv(RIPI_DATA_PATH, index=False, date_format="%Y-%m-%d")
    daily[cpi_columns].to_csv(CPI_DATA_PATH, index=False, date_format="%Y-%m-%d")
    monthly.to_csv(MONTHLY_OUTPUT_PATH, index=False, date_format="%Y-%m-%d")


def main() -> None:
    args = parse_args()
    if args.start_year > args.end_year:
        raise ValueError("start-year must be less than or equal to end-year")

    series_map = fetch_bls_series(args.start_year, args.end_year)
    monthly = build_monthly_frame(series_map)
    daily = monthly_to_daily(monthly, args.end_date)
    write_outputs(monthly, daily)

    print(
        f"Wrote live data to {RIPI_DATA_PATH}, {CPI_DATA_PATH}, and {MONTHLY_OUTPUT_PATH}"
    )
    print(monthly.tail().to_string(index=False))


if __name__ == "__main__":
    main()
