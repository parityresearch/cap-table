# TSE Cap Table Recovery Engine — Web Interface

A modern web application for Monte Carlo cap table analysis with PIK accretion, Chapter 11 cost leakage, and fulcrum security identification.

## Fastest Public Deploy

The easiest public app route is Streamlit Community Cloud.

Official docs:
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- https://docs.streamlit.io/deploy/streamlit-community-cloud

Use:
- Repository: `parityresearch/cap-table`
- Branch: `cap-table`
- Entrypoint: `streamlit_app.py`

If you want to keep the current Flask UI instead, use Render to deploy the whole Flask app as a single service:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/parityresearch/cap-table/tree/cap-table)

This repo includes:
- `render.yaml` for the Render service definition
- `.python-version` to pin Python 3.11
- `requirements_web.txt` for the production web dependency set

After deploy, use the generated `https://<service>.onrender.com` URL as the live site. That URL serves both the upload page and the `/api/generate` backend.

## Features

✨ **Upload & Generate**
- Drag-and-drop CSV upload
- Automatic parameter configuration with sensible defaults
- One-click report generation

📊 **Comprehensive Analysis**
- Monte Carlo simulation (50,000+ runs default)
- Market-implied EBITDA back-calculation
- PIK accretion over restructuring timeline
- Chapter 11 cost leakage impact
- Fulcrum security identification
- Sensitivity analysis heatmaps

🎨 **Modern Interface**
- Clean, minimal design inspired by Parity Research
- Responsive layout (desktop & mobile)
- Real-time status updates
- Embeddable report viewer

## Setup

### Prerequisites
- Python 3.9+
- pip or conda

### Installation

1. **Clone and navigate to the project**
   ```bash
   cd /Users/gurmandhaliwal/cpi
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements_web.txt
   ```

3. **Prepare your CSV**
   
   Your CSV should have these columns (in order):
   ```
   Layer,Instrument,Principal,Maturity,Interest Type
   Super,Senior Secured Notes,50.0,2027,Fixed 5%
   1L,Term Loan B,300.0,2027,PIK SOFR+8.5%
   2L,Second Lien,150.0,2028,12.5%
   Junior,Mezzanine,75.0,2028,15.0%
   ```

   Followed by metadata rows:
   ```
   Adjusted EBITDA,72.5
   Valuation Multiple Low,4.0
   Valuation Multiple Mid,6.0
   Valuation Multiple High,8.0
   Other Claims,0.0
   Total Debt to Clear,575.0
   ```

4. **Start the server**
   ```bash
   ./start_web.sh
   ```

5. **Open in browser**
   ```
   http://localhost:5001
   ```

## Usage

1. **Upload your cap table CSV** — drag/drop or click to select
2. **Configure assumptions** — most are pre-filled with defaults:
   - **Simulation**: Runs, seed, EBITDA volatility
   - **EBITDA cases**: Distressed / Clean / Recovery scenarios
   - **Market inputs**: 1L trading price, discount rate, resolution timeline
   - **Bankruptcy costs**: Ch.11 professional fees ($M)
3. **Click "Generate Report"** — simulation runs in the background
4. **Review the report** — embedded viewer with all analysis
5. **Download as HTML** — fully self-contained for sharing

## CSV Format Details

### Required Columns (in order)

| Column | Description | Example |
|--------|-------------|---------|
| **Layer** | Priority level (Super, 1L, 2L, Junior, Equity) | "Super" |
| **Instrument** | Debt instrument name | "Senior Secured Notes" |
| **Principal** | Balance in $M | 50.0 |
| **Maturity** | Year or date | "2027" |
| **Interest Type** | Rate type and amount | "PIK SOFR+8.5%" |

### Optional Metadata Rows (at bottom)

```
Adjusted EBITDA,72.5
Valuation Multiple Low,4.0
Valuation Multiple Mid,6.0
Valuation Multiple High,8.0
Other Claims,0.0
Total Debt to Clear,575.0
```

## Parameters Explained

### Simulation
- **Runs**: Number of Monte Carlo iterations (more = more precision, slower)
- **Seed**: Random seed for reproducibility
- **EBITDA CV**: Distribution coefficient of variation (0.22 = 22% spread)
- **Debt Volatility**: Multiple volatility in simulations

### EBITDA Cases
- **Distressed**: Annualized Q3/Q4 exit run-rate (downside scenario)
- **Clean**: Reported EBITDA minus one-time items (base case)
- **Recovery**: Normalized demand / management target (upside scenario)

### Market & Bankruptcy
- **Market Price (1L)**: Current 1L debt trading price in cents on dollar
- **Discount Rate**: Distressed discount rate for implied fair value (%)
- **Resolution Timeline**: Months to restructuring exit (PIK accrues monthly)
- **Ch.11 Costs**: Professional fees (legal, accounting, advisors) in $M

## Report Sections

🎯 **Summary Metrics**
- Insolvency probability (EV < debt)
- Median enterprise value
- Equity probability of having value
- Expected residual equity

📈 **Distribution Analysis**
- Enterprise value histogram with P10/P50/P90
- Layer-by-layer recovery outlook
- Money-good probabilities

🔍 **Market Intelligence**
- Three EBITDA case scenarios with waterfalls
- Market-implied EBITDA (back-solved from 1L trading price)
- Fulcrum security identification

⚠️ **Hidden Costs**
- Chapter 11 cost leakage (reduction in 1L recovery)
- PIK accretion table (debt growth over restructuring timeline)

💰 **Valuation**
- Implied fair value vs. market price for 1L debt
- Recovery gaps and entry points

🔥 **Sensitivity**
- Heatmap: Enterprise value, 2L recovery, equity value
- Across EBITDA cases and valuation multiples

## Development

### Project Structure
```
cpi/
├── app.py                      # Flask web server
├── simulate_tse_cap_table.py  # Core simulation engine
├── requirements_web.txt        # Web app dependencies
├── requirements.txt            # CLI dependencies
├── templates/
│   └── upload.html            # Landing page with upload UI
└── output/
    └── tse_cap_table_report.html  # Generated reports
```

### API Endpoints

**POST /api/generate**
- Accepts: multipart/form-data (CSV file + parameters)
- Returns: JSON with `html` field (full HTML report)
- Status codes: 200 (success), 400 (bad request), 500 (error)

## Troubleshooting

**"File upload failed"**
- Ensure CSV is valid UTF-8 encoding
- Check column order: Layer, Instrument, Principal, Maturity, Interest Type
- No empty rows between data

**"Generation timed out"**
- Reduce `--runs` parameter (default 50,000)
- Smaller number still gives good results; try 5,000–10,000

**"Column not found"**
- CSV must have exactly 5 columns in the right order
- Check for accidental spaces or tab characters

## Performance

| Parameter | Impact |
|-----------|--------|
| Runs | Linear (10K takes ~5s, 50K takes ~20s) |
| EBITDA CV | Minimal (~5% variance in time) |
| Debt Volatility | Minimal (~5% variance in time) |

For real-time use, recommend 10,000–50,000 runs.

## License & Attribution

Built with:
- **Flask** — web framework
- **NumPy** — Monte Carlo math
- **openpyxl** — workbook parsing
- Design inspired by **Parity Research Trigger Monitor**

## Support

For bugs or feature requests, open an issue on GitHub or contact [your-email@example.com].

---

**Not investment advice.** This tool is for educational and analysis purposes only. Always consult qualified legal and financial advisors before making investment decisions.
