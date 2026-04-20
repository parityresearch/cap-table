from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SOURCE_PATH = Path("output/monthly_source_data.csv")
OUTPUT_DIR = Path("output")

WEIGHTS = {
    "gas_price": 0.15,
    "uber_ride": 0.15,
    "groceries_basket": 0.25,
    "rent_index": 0.30,
    "streaming_cost": 0.15,
}

LABELS = {
    "gas_price": "Gas",
    "uber_ride": "Transport",
    "groceries_basket": "Groceries",
    "rent_index": "Rent",
    "streaming_cost": "Streaming",
}

COLORS = {
    "gas_price": "#d1495b",
    "uber_ride": "#edae49",
    "groceries_basket": "#00798c",
    "rent_index": "#30638e",
    "streaming_cost": "#6c757d",
}


def load_monthly_data() -> pd.DataFrame:
    df = pd.read_csv(SOURCE_PATH, parse_dates=["date"]).sort_values("date").reset_index(
        drop=True
    )
    if df.empty:
        raise ValueError(f"No data found in {SOURCE_PATH}")
    return df


def build_contributions(df: pd.DataFrame) -> pd.DataFrame:
    base = df.iloc[0]
    result = df.copy()

    for column, weight in WEIGHTS.items():
        result[f"{column}_idx"] = result[column] / base[column] * 100
        result[f"{column}_contribution"] = result[f"{column}_idx"] * weight
        result[f"{column}_monthly_change"] = result[f"{column}_contribution"].diff()

    result["RIPI"] = sum(result[f"{column}_contribution"] for column in WEIGHTS)
    result["RIPI_monthly_change"] = result["RIPI"].diff()
    result["positive_categories"] = sum(
        (result[f"{column}_monthly_change"] > 0).astype(int) for column in WEIGHTS
    )
    return result.iloc[1:].reset_index(drop=True)


def plot_contributions(df: pd.DataFrame) -> Path:
    latest_month = df["date"].max().strftime("%Y-%m")
    output_path = OUTPUT_DIR / f"inflation_breadth_through_{latest_month}.png"

    fig, ax = plt.subplots(figsize=(13, 7))

    x = range(len(df))
    positive_stack = [0.0] * len(df)
    negative_stack = [0.0] * len(df)

    for column in WEIGHTS:
        values = df[f"{column}_monthly_change"].tolist()
        positive_values = [value if value > 0 else 0 for value in values]
        negative_values = [value if value < 0 else 0 for value in values]

        ax.bar(
            x,
            positive_values,
            bottom=positive_stack,
            width=0.72,
            label=LABELS[column],
            color=COLORS[column],
        )
        ax.bar(
            x,
            negative_values,
            bottom=negative_stack,
            width=0.72,
            color=COLORS[column],
        )

        positive_stack = [
            bottom + value for bottom, value in zip(positive_stack, positive_values)
        ]
        negative_stack = [
            bottom + value for bottom, value in zip(negative_stack, negative_values)
        ]

    ax.plot(
        x,
        df["RIPI_monthly_change"],
        color="black",
        linewidth=1.8,
        marker="o",
        markersize=3.5,
        label="Total RIPI monthly change",
    )

    for idx, (change, breadth) in enumerate(
        zip(df["RIPI_monthly_change"], df["positive_categories"])
    ):
        if pd.isna(change):
            continue
        offset = 0.12 if change >= 0 else -0.18
        va = "bottom" if change >= 0 else "top"
        ax.text(idx, change + offset, f"{breadth}/5", ha="center", va=va, fontsize=8)

    ax.axhline(0, color="#444444", linewidth=1)
    ax.set_title(
        "How Broad Was Inflation Pressure?\nMonthly RIPI change contributions through "
        f"{df['date'].max():%B %Y}"
    )
    ax.set_ylabel("Monthly change in RIPI index points")
    ax.set_xticks(list(x))
    ax.set_xticklabels([d.strftime("%Y-%m") for d in df["date"]], rotation=45, ha="right")
    ax.legend(frameon=False, ncol=3)
    ax.text(
        0.01,
        -0.16,
        "Bars show each category's contribution to the monthly move in RIPI. "
        "Labels show the number of categories that increased that month.",
        transform=ax.transAxes,
        fontsize=9,
        color="#444444",
    )

    fig.tight_layout()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> None:
    monthly = load_monthly_data()
    contributions = build_contributions(monthly)
    output_path = plot_contributions(contributions)

    latest = contributions.iloc[-1]
    print(f"Wrote {output_path}")
    print(f"Latest full month: {latest['date']:%Y-%m}")
    print(f"RIPI monthly change: {latest['RIPI_monthly_change']:.3f}")
    for column in WEIGHTS:
        print(
            f"{LABELS[column]} contribution: "
            f"{latest[f'{column}_monthly_change']:.3f}"
        )


if __name__ == "__main__":
    main()
