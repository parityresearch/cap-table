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
    "Super": 0,
    "1L": 1,
    "2L": 2,
    "Junior": 3,
    "Equity": 4,
}

TLB_NAME_FRAGMENT = "term loan b"
SOFR = 0.053
TLB_SPREAD = 0.085
TLB_PIK_RATE = SOFR + TLB_SPREAD

def canonicalize_layer(priority_label):
    """Canonicalize priority label to standard layer name"""
    normalized = priority_label.lower()
    if "super" in normalized:
        return "Super", LAYER_ORDER["Super"]
    if "1st" in normalized or "senior secured" in normalized:
        return "1L", LAYER_ORDER["1L"]
    if "2nd" in normalized or "2nd lien" in normalized:
        return "2L", LAYER_ORDER["2L"]
    if "junior" in normalized:
        return "Junior", LAYER_ORDER["Junior"]
    if "equity" in normalized:
        return "Equity", LAYER_ORDER["Equity"]
    raise ValueError(f"Unsupported priority label: {priority_label!r}")

def maybe_layer(priority_label):
    """Try to canonicalize layer, return None if fails"""
    try:
        return canonicalize_layer(priority_label)
    except ValueError:
        return None

def load_cap_table_from_csv(csv_path: Path) -> CapTableModel:
    """Load cap table from CSV file instead of Excel workbook"""
    instruments: list[Instrument] = []
    base_ebitda = 0.0
    multiple_low = 0.0
    multiple_mid = 0.0
    multiple_high = 0.0
    total_debt_to_clear = 0.0
    other_claims = 0.0
    listed_debt = 0.0
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
            if instrument_name == "Total Debt to Clear" or priority == "Adjusted EBITDA":
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
                if layer == "1L":
                    listed_debt += principal

            # Parse metadata rows (EBITDA, multiples, other claims)
            if not in_stack_section:
                if "EBITDA" in instrument_name:
                    base_ebitda = principal
                if "multiple" in instrument_name.lower():
                    if "low" in instrument_name.lower():
                        multiple_low = principal
                    elif "high" in instrument_name.lower():
                        multiple_high = principal
                    else:
                        multiple_mid = principal
                if "other" in instrument_name.lower() and "claims" in instrument_name.lower():
                    other_claims = principal
                if "total" in instrument_name.lower() and "debt" in instrument_name.lower():
                    total_debt_to_clear = principal

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

@app.route('/')
def index():
    """Landing page with upload form"""
    return render_template('upload.html')

@app.route('/api/generate', methods=['POST'])
def generate_report():
    """
    Accept CSV file and parameters, generate cap table report
    """
    temp_dir = None
    try:
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

