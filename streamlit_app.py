from __future__ import annotations

import csv
import shutil
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from simulate_tse_cap_table import (
    CapTableModel,
    Instrument,
    SimulationAssumptions,
    render_report,
    simulate,
)


LAYER_ORDER = {
    "Super": 1,
    "1L": 2,
    "2L": 3,
    "Junior": 4,
    "Equity": 5,
}

TLB_NAME_FRAGMENT = "term loan b"
SOFR = 0.053
TLB_SPREAD = 0.085
TLB_PIK_RATE = SOFR + TLB_SPREAD


def normalize_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return str(value).strip()


def to_float(value):
    if value in (None, "", "#DIV/0!"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def canonicalize_layer(priority_label):
    normalized = priority_label.lower()
    compact = normalized.replace(".", "").replace("-", " ").strip()
    if "super" in compact:
        return "Super", LAYER_ORDER["Super"]
    if (
        compact.startswith("1l")
        or " 1l" in compact
        or "1st" in compact
        or "first lien" in compact
        or "senior secured" in compact
    ):
        return "1L", LAYER_ORDER["1L"]
    if (
        compact.startswith("2l")
        or " 2l" in compact
        or "2nd" in compact
        or "second lien" in compact
        or "2nd lien" in compact
    ):
        return "2L", LAYER_ORDER["2L"]
    if "junior" in compact:
        return "Junior", LAYER_ORDER["Junior"]
    if "equity" in compact:
        return "Equity", LAYER_ORDER["Equity"]
    raise ValueError(f"Unsupported priority label: {priority_label!r}")


def maybe_layer(priority_label):
    try:
        return canonicalize_layer(priority_label)
    except ValueError:
        return None


def first_numeric_value(*values):
    for value in values:
        text = normalize_text(value)
        if text == "":
            continue
        if text == "#DIV/0!":
            return 0.0
        try:
            return float(text)
        except ValueError:
            continue
    return 0.0


def load_cap_table_from_csv(csv_path: Path) -> CapTableModel:
    instruments: list[Instrument] = []
    base_ebitda = 0.0
    multiple_low = 0.0
    multiple_mid = 0.0
    multiple_high = 0.0
    total_debt_to_clear = 0.0
    other_claims = 0.0
    in_stack_section = True

    with open(csv_path, "r", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        for row in reader:
            while len(row) < 5:
                row.append("")

            priority = normalize_text(row[0])
            raw_instrument_name = row[1]
            instrument_name = normalize_text(raw_instrument_name)
            principal = to_float(row[2])
            maturity = normalize_text(row[3])
            interest_type = normalize_text(row[4])

            parsed_layer = maybe_layer(priority)
            row_labels = {priority.lower(), instrument_name.lower()}
            if "total debt to clear" in row_labels or "adjusted ebitda" in row_labels:
                in_stack_section = False

            if (
                in_stack_section
                and instrument_name
                and isinstance(raw_instrument_name, str)
                and "total" not in instrument_name.lower()
                and parsed_layer
            ):
                layer, rank = parsed_layer
                is_pik = "pik" in interest_type.lower() or "pik" in instrument_name.lower()
                pik_rate = TLB_PIK_RATE if (
                    is_pik or TLB_NAME_FRAGMENT in instrument_name.lower()
                ) else 0.0
                instruments.append(
                    Instrument(
                        layer=layer,
                        rank=rank,
                        priority_label=priority,
                        name=instrument_name,
                        principal=principal,
                        maturity=maturity or "n/a",
                        interest_type=interest_type or "n/a",
                        is_pik=is_pik,
                        pik_rate=pik_rate,
                    )
                )

            if not in_stack_section:
                metadata_value = first_numeric_value(row[2], row[1], row[0])
                if "adjusted ebitda" in row_labels:
                    base_ebitda = metadata_value
                elif (
                    "valuation multiple low" in row_labels
                    or "low (distressed case)" in row_labels
                    or any("low" in label and "multiple" in label for label in row_labels)
                ):
                    multiple_low = metadata_value
                elif (
                    "valuation multiple high" in row_labels
                    or "high (healthy case)" in row_labels
                    or any("high" in label and "multiple" in label for label in row_labels)
                ):
                    multiple_high = metadata_value
                elif (
                    "valuation multiple mid" in row_labels
                    or "mid" in row_labels
                    or any("mid" in label and "multiple" in label for label in row_labels)
                ):
                    multiple_mid = metadata_value
                elif "other claims" in row_labels:
                    other_claims = metadata_value
                elif "total debt to clear" in row_labels:
                    total_debt_to_clear = metadata_value

    listed_debt = sum(item.principal for item in instruments)

    return CapTableModel(
        workbook_name=csv_path.name,
        instruments=sorted(instruments, key=lambda item: (item.rank, item.name)),
        base_ebitda=base_ebitda,
        multiple_low=multiple_low,
        multiple_mid=multiple_mid,
        multiple_high=multiple_high,
        listed_debt=listed_debt,
        other_claims=other_claims,
        total_debt_to_clear=total_debt_to_clear or (listed_debt + other_claims),
    )


def validate_uploaded_csv(filename: str, model: CapTableModel) -> None:
    if not filename.lower().endswith(".csv"):
        raise ValueError("Please upload a CSV file.")
    if not model.instruments:
        raise ValueError(
            "No debt instruments were found in the CSV. "
            "Expected columns like: Layer, Instrument, Principal, Maturity, Interest Type."
        )
    if model.base_ebitda <= 0:
        raise ValueError("Missing or invalid 'Adjusted EBITDA' metadata row.")
    if model.multiple_low <= 0 or model.multiple_mid <= 0 or model.multiple_high <= 0:
        raise ValueError(
            "Missing valuation multiple metadata. Add Low, Mid, and High multiple rows."
        )


def run_uploaded_analysis(uploaded_file, assumptions: SimulationAssumptions) -> str:
    temp_dir = tempfile.mkdtemp()
    try:
        temp_csv_path = Path(temp_dir) / uploaded_file.name
        temp_csv_path.write_bytes(uploaded_file.getvalue())
        model = load_cap_table_from_csv(temp_csv_path)
        validate_uploaded_csv(uploaded_file.name, model)
        report = simulate(model, assumptions)
        return render_report(report)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


st.set_page_config(
    page_title="TSE Cap Table Recovery Engine",
    page_icon="📊",
    layout="wide",
)

st.title("TSE Cap Table Recovery Engine")
st.caption("Public upload app for Monte Carlo recovery analysis, PIK accretion, and Chapter 11 leakage.")

with st.expander("CSV format", expanded=False):
    st.code(
        """Layer,Instrument,Principal,Maturity,Interest Type
Super,A/R Facility,118.0,n/a,n/a
Super,Super-Priority Revolver (RCF),75.0,2028,Cash
1L,Refi Term Loans,1238.0,n/a,n/a
1L,Term Loan B,715.0,2028,SOFR + 8.5% (PIK Option)
2L,Senior Secured Notes (2029),442.0,2029,7.625% Cash
Junior,Unsecured Notes / Trade Claims,0.0,2025/29,Various
Adjusted EBITDA,162.5
Valuation Multiple Low,5.0
Valuation Multiple Mid,6.0
Valuation Multiple High,7.0
Other Claims,3.9
Total Debt to Clear,2591.9""",
        language="csv",
    )

uploaded_file = st.file_uploader("Upload cap table CSV", type=["csv"])

with st.sidebar:
    st.header("Simulation Inputs")
    runs = st.number_input("Runs", min_value=100, value=5000, step=1000)
    seed = st.number_input("Seed", value=7, step=1)
    ebitda_cv = st.number_input("EBITDA CV", min_value=0.0, max_value=1.0, value=0.22, step=0.01)
    debt_volatility = st.number_input("Debt volatility", min_value=0.0, max_value=1.0, value=0.03, step=0.01)
    ebitda_distressed = st.number_input("Distressed EBITDA ($M)", value=72.5, step=0.5)
    ebitda_clean = st.number_input("Clean EBITDA ($M)", value=135.5, step=0.5)
    ebitda_recovery = st.number_input("Recovery EBITDA ($M)", value=225.0, step=0.5)
    market_price_1l = st.number_input("1L market price (cents)", value=11.0, step=0.5)
    discount_rate = st.number_input("Discount rate (%)", value=20.0, step=0.5)
    resolution_months = st.number_input("Resolution months", min_value=1, value=12, step=1)
    ch11_costs = st.number_input("Chapter 11 costs ($M)", min_value=0.0, value=75.0, step=1.0)
    run_button = st.button("Generate Report", type="primary", use_container_width=True)

if "generated_html" not in st.session_state:
    st.session_state.generated_html = None

if run_button:
    if uploaded_file is None:
        st.error("Upload a CSV file first.")
    else:
        assumptions = SimulationAssumptions(
            runs=int(runs),
            seed=int(seed),
            ebitda_cv=float(ebitda_cv),
            debt_volatility=float(debt_volatility),
            ebitda_distressed=float(ebitda_distressed),
            ebitda_clean=float(ebitda_clean),
            ebitda_recovery=float(ebitda_recovery),
            market_price_1l=float(market_price_1l),
            discount_rate=float(discount_rate),
            resolution_months=int(resolution_months),
            ch11_costs=float(ch11_costs),
        )
        with st.spinner("Running analysis..."):
            try:
                st.session_state.generated_html = run_uploaded_analysis(uploaded_file, assumptions)
                st.success("Analysis complete.")
            except Exception as exc:
                st.session_state.generated_html = None
                st.error(str(exc))

if st.session_state.generated_html:
    st.download_button(
        "Download HTML report",
        data=st.session_state.generated_html,
        file_name="tse_recovery_report.html",
        mime="text/html",
        use_container_width=False,
    )
    components.html(st.session_state.generated_html, height=1400, scrolling=True)
