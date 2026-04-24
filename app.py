#!/usr/bin/env python3
"""
TSE Cap Table Simulation Web Interface
Accepts CSV uploads and generates cap table recovery analysis reports
"""

from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path
import tempfile
import json
import shutil
import csv
from datetime import datetime
import sys
import os

# Import the simulation module
sys.path.insert(0, str(Path(__file__).parent))
from simulate_tse_cap_table import (
    simulate,
    SimulationAssumptions,
    CapTableModel,
    Instrument,
    render_report,
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()

@app.after_request
def add_dev_cors_headers(response):
    """Allow the hosted frontend to call the public API without credentials."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

def normalize_text(value):
    """Normalize text values"""
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return str(value).strip()

def to_float(value):
    """Convert to float, handling errors"""
    if value in (None, "", "#DIV/0!"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

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

def canonicalize_layer(priority_label):
    """Canonicalize priority label to standard layer name"""
    normalized = priority_label.lower()
    compact = normalized.replace(".", "").replace("-", " ").strip()
    if "super" in compact:
        return "Super", LAYER_ORDER["Super"]
    if compact.startswith("1l") or " 1l" in compact or "1st" in compact or "first lien" in compact or "senior secured" in compact:
        return "1L", LAYER_ORDER["1L"]
    if compact.startswith("2l") or " 2l" in compact or "2nd" in compact or "second lien" in compact or "2nd lien" in compact:
        return "2L", LAYER_ORDER["2L"]
    if "junior" in compact:
        return "Junior", LAYER_ORDER["Junior"]
    if "equity" in compact:
        return "Equity", LAYER_ORDER["Equity"]
    raise ValueError(f"Unsupported priority label: {priority_label!r}")

def maybe_layer(priority_label):
    """Try to canonicalize layer, return None if fails"""
    try:
        return canonicalize_layer(priority_label)
    except ValueError:
        return None

def first_numeric_value(*values):
    """Return the first non-empty numeric value from a list of candidates."""
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
    """Load cap table from CSV file instead of Excel workbook"""
    instruments: list[Instrument] = []
    base_ebitda = 0.0
    multiple_low = 0.0
    multiple_mid = 0.0
    multiple_high = 0.0
    total_debt_to_clear = 0.0
    other_claims = 0.0
    in_stack_section = True

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        for row in reader:
            # Pad row to ensure we have 5 columns
            while len(row) < 5:
                row.append('')
            
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

            # Parse metadata rows (EBITDA, multiples, other claims)
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
    """Return clear user-facing validation errors for malformed uploads."""
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

@app.route('/')
def index():
    """Landing page with upload form"""
    return render_template('upload.html')

@app.route('/api/generate', methods=['POST', 'OPTIONS'])
def generate_report():
    """
    Accept CSV file and parameters, generate cap table report
    """
    temp_dir = None
    try:
        if request.method == 'OPTIONS':
            return ('', 204)

        # Get file
        if 'csv_file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['csv_file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Get parameters from form
        runs = int(request.form.get('runs', 50000))
        seed = int(request.form.get('seed', 7))
        ebitda_cv = float(request.form.get('ebitda_cv', 0.22))
        debt_volatility = float(request.form.get('debt_volatility', 0.03))
        
        ebitda_distressed = float(request.form.get('ebitda_distressed', 72.5))
        ebitda_clean = float(request.form.get('ebitda_clean', 135.5))
        ebitda_recovery = float(request.form.get('ebitda_recovery', 225.0))
        
        market_price_1l = float(request.form.get('market_price_1l', 11.0))
        discount_rate = float(request.form.get('discount_rate', 20.0))
        resolution_months = int(request.form.get('resolution_months', 12))
        ch11_costs = float(request.form.get('ch11_costs', 75.0))

        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp()
        temp_csv_path = Path(temp_dir) / 'upload.csv'
        file.save(str(temp_csv_path))

        # Load cap table from CSV
        model = load_cap_table_from_csv(temp_csv_path)
        validate_uploaded_csv(file.filename, model)

        # Create simulation assumptions
        assumptions = SimulationAssumptions(
            runs=runs,
            seed=seed,
            ebitda_cv=ebitda_cv,
            debt_volatility=debt_volatility,
            ebitda_distressed=ebitda_distressed,
            ebitda_clean=ebitda_clean,
            ebitda_recovery=ebitda_recovery,
            market_price_1l=market_price_1l,
            discount_rate=discount_rate,
            resolution_months=resolution_months,
            ch11_costs=ch11_costs,
        )

        # Run simulation
        report = simulate(model, assumptions)

        # Render HTML report
        html_content = render_report(report)

        return jsonify({
            'success': True,
            'html': html_content,
        })

    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'type': type(e).__name__,
            'traceback': traceback.format_exc(),
        }), 500
    
    finally:
        # Cleanup
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == '__main__':
    # Development server (port 5000 conflicts with macOS AirPlay, use 5001 instead)
    app.run(debug=True, port=5001)
