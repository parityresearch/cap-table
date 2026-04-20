from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


SOURCE_PATH = Path("output/monthly_source_data.csv")
OUTPUT_PATH = Path("output/substack_ripi_vs_cpi_gap.png")

WEIGHTS = {
    "gas_price": 0.15,
    "uber_ride": 0.15,
    "groceries_basket": 0.25,
    "rent_index": 0.30,
    "streaming_cost": 0.15,
}


def load_data() -> pd.DataFrame:
    df = pd.read_csv(SOURCE_PATH, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        raise ValueError(f"No data found in {SOURCE_PATH}")

    base = df.iloc[0]
    for column in WEIGHTS:
        df[f"{column}_idx"] = df[column] / base[column] * 100

    df["RIPI"] = sum(df[f"{column}_idx"] * weight for column, weight in WEIGHTS.items())
    df["cpi_idx"] = df["cpi"] / base["cpi"] * 100
    df["gap"] = df["RIPI"] - df["cpi_idx"]
    return df


def annotate(ax: plt.Axes, row: pd.Series, text: str, color: str, dx_days: int, dy: float) -> None:
    ax.scatter(row["date"], row["RIPI"], s=40, color=color, zorder=5, edgecolor="white", linewidth=1.2)
    ax.annotate(
        text,
        xy=(row["date"], row["RIPI"]),
        xytext=(row["date"] + pd.Timedelta(days=dx_days), row["RIPI"] + dy),
        textcoords="data",
        fontsize=10,
        color="#1f2430",
        ha="left",
        va="center",
        arrowprops={"arrowstyle": "-", "color": color, "lw": 1.3},
        bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": color, "lw": 1.1},
    )


def plot(df: pd.DataFrame) -> Path:
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(13.5, 8))
    fig.patch.set_facecolor("#f6f1e8")
    ax.set_facecolor("#fdfbf7")

    ripi_color = "#c05621"
    cpi_color = "#1f4e79"
    positive_fill = "#f6ad55"
    negative_fill = "#90cdf4"

    ax.fill_between(
        df["date"],
        df["RIPI"],
        df["cpi_idx"],
        where=df["RIPI"] >= df["cpi_idx"],
        color=positive_fill,
        alpha=0.28,
        interpolate=True,
    )
    ax.fill_between(
        df["date"],
        df["RIPI"],
        df["cpi_idx"],
        where=df["RIPI"] < df["cpi_idx"],
        color=negative_fill,
        alpha=0.30,
        interpolate=True,
    )

    ax.plot(df["date"], df["RIPI"], color=ripi_color, linewidth=3.0, label="RIPI basket")
    ax.plot(df["date"], df["cpi_idx"], color=cpi_color, linewidth=2.6, label="Headline CPI")

    peak_gap = df.loc[df["gap"].idxmax()]
    trough_gap = df.loc[df["gap"].idxmin()]
    rebound = df.loc[df["date"] == df["date"].max()].iloc[0]

    annotate(
        ax,
        peak_gap,
        "Mar 2025\nRIPI ran 5.8 pts above CPI.\nGroceries were the main driver.",
        ripi_color,
        dx_days=-160,
        dy=2.5,
    )
    annotate(
        ax,
        trough_gap,
        "Feb 2026\nGap flipped to -2.9 pts\nas gas and groceries cooled.",
        cpi_color,
        dx_days=-170,
        dy=-3.0,
    )
    annotate(
        ax,
        rebound,
        "Mar 2026\nA gas rebound nearly erased\nthe gap again.",
        ripi_color,
        dx_days=-110,
        dy=2.0,
    )

    ax.axhline(100, color="#9aa5b1", linewidth=1.0, linestyle=(0, (4, 4)))
    ax.text(
        df["date"].iloc[0],
        100.35,
        "Jan 2024 = 100",
        fontsize=9,
        color="#606f7b",
        va="bottom",
    )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e0")
    ax.spines["bottom"].set_color("#cbd5e0")
    ax.grid(axis="y", color="#e2e8f0", linewidth=1.0)
    ax.grid(axis="x", visible=False)

    ax.set_title(
        "Perceived Inflation Swung Far More Than Headline CPI",
        loc="left",
        fontsize=22,
        fontweight="bold",
        color="#1a202c",
        pad=12,
    )

    ax.set_ylabel("Index level (Jan 2024 = 100)", fontsize=11, color="#2d3748")
    ax.set_xlabel("")
    ax.set_xlim(df["date"].min(), df["date"].max() + pd.Timedelta(days=15))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.tick_params(axis="x", labelsize=10, colors="#4a5568")
    ax.tick_params(axis="y", labelsize=10, colors="#4a5568")

    legend = ax.legend(loc="upper left", frameon=False, fontsize=11, ncol=2, bbox_to_anchor=(0.0, 0.96))
    for line in legend.get_lines():
        line.set_linewidth(3)

    latest = df.iloc[-1]
    fig.text(
        0.012,
        0.02,
        "Source: BLS series assembled in this repo. RIPI weights: gas 15%, transport 15%, groceries 25%, rent 30%, streaming 15%.",
        fontsize=9.5,
        color="#4a5568",
    )
    fig.text(
        0.988,
        0.02,
        f"Latest month: {latest['date']:%b %Y}",
        fontsize=9.5,
        color="#4a5568",
        ha="right",
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    fig.savefig(OUTPUT_PATH, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return OUTPUT_PATH


def main() -> None:
    df = load_data()
    output_path = plot(df)
    latest = df.iloc[-1]
    print(f"Wrote {output_path}")
    print(f"Latest month: {latest['date']:%Y-%m}")
    print(f"RIPI: {latest['RIPI']:.3f}")
    print(f"CPI index: {latest['cpi_idx']:.3f}")
    print(f"Gap: {latest['gap']:.3f}")


if __name__ == "__main__":
    main()
