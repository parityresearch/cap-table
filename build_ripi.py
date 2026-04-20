from pathlib import Path

import pandas as pd


DATA_PATH = Path("data/ripi_data.csv")
CPI_PATH = Path("data/cpi.csv")
OUTPUT_PATH = Path("output/ripi_index.csv")

INDEX_COLUMNS = [
    "gas_price",
    "uber_ride",
    "groceries_basket",
    "rent_index",
    "streaming_cost",
]

WEIGHTS = {
    "gas_price_idx": 0.15,
    "uber_ride_idx": 0.15,
    "groceries_basket_idx": 0.25,
    "rent_index_idx": 0.30,
    "streaming_cost_idx": 0.15,
}


def load_source_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty:
        raise ValueError(f"No rows found in {path}")
    return df.sort_values("date").reset_index(drop=True)


def build_ripi(df: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in INDEX_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    base_row = df.iloc[0]
    result = df.copy()

    for column in INDEX_COLUMNS:
        base_value = base_row[column]
        if base_value == 0:
            raise ValueError(f"Base value for {column} cannot be zero")
        result[f"{column}_idx"] = result[column] / base_value * 100

    result["RIPI"] = sum(result[column] * weight for column, weight in WEIGHTS.items())
    return result


def merge_cpi(df: pd.DataFrame, cpi_path: Path) -> pd.DataFrame:
    if not cpi_path.exists():
        return df

    cpi = pd.read_csv(cpi_path, parse_dates=["date"])
    if "cpi" not in cpi.columns:
        raise ValueError(f"{cpi_path} must contain a 'cpi' column")

    merged = df.merge(cpi, on="date", how="left")
    if merged["cpi"].notna().any():
        base_cpi = merged["cpi"].dropna().iloc[0]
        if base_cpi != 0:
            merged["cpi_idx"] = merged["cpi"] / base_cpi * 100
            merged["ripi_minus_cpi_idx"] = merged["RIPI"] - merged["cpi_idx"]
    return merged


def main() -> None:
    df = load_source_data(DATA_PATH)
    ripi = build_ripi(df)
    ripi = merge_cpi(ripi, CPI_PATH)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ripi.to_csv(OUTPUT_PATH, index=False, date_format="%Y-%m-%d")

    print(f"Wrote {len(ripi)} rows to {OUTPUT_PATH}")
    print(ripi[["date", "RIPI"]].tail().to_string(index=False))


if __name__ == "__main__":
    main()
