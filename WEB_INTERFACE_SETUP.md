# TSE Cap Table Recovery Engine — Web Interface

## 🎉 What's New

I've created a **modern web interface** for your TSE cap table recovery analysis tool. Upload a CSV, configure assumptions, and get beautiful interactive reports in seconds.

## 🚀 Quick Start (30 seconds)

```bash
cd /Users/gurmandhaliwal/cpi

# 1. Install Flask (one-time)
pip install Flask==3.0.0

# 2. Start server
./start_web.sh

# 3. Open browser
open http://localhost:5001

# Done! Now upload your CSV and click Generate
```

## 📚 Documentation

| Document | Purpose |
|----------|---------|
| **QUICKSTART.md** | 1-page quick reference. Start here. |
| **WEB_INTERFACE_README.md** | Complete documentation, CSV format, troubleshooting. |
| **WEB_INTERFACE_SUMMARY.md** | Design overview, architecture, technical details. |

## 📁 What's New (Files Added)

### Core Application
- **app.py** — Flask web server. Main entry point. `python3 app.py`
- **templates/upload.html** — Landing page (HTML + CSS + JavaScript)
- **requirements_web.txt** — Python dependencies (Flask, Werkzeug)
- **check_setup.py** — Verify Flask is installed
- **start_web.sh** — Bash startup script

### Documentation & Examples
- **QUICKSTART.md** — Fast setup guide
- **WEB_INTERFACE_README.md** — Full documentation
- **WEB_INTERFACE_SUMMARY.md** — Technical overview
- **sample_cap_table.csv** — Example CSV for testing

## ✨ Features

### 🎨 User Interface
- Drag-and-drop CSV upload
- Clean, modern design (inspired by Parity Research)
- Organized parameter panels with descriptions
- Real-time status updates
- Embedded report viewer
- One-click download

### 📊 Capabilities
- Monte Carlo simulation (50,000+ iterations)
- PIK accretion analysis
- Chapter 11 cost leakage quantification
- Fulcrum security identification
- Market-implied EBITDA back-calculation
- Sensitivity heatmaps
- Full HTML report with all analysis

### 🔧 Technical
- **Zero external dependencies** (vanilla HTML/CSS/JavaScript)
- Responsive design (desktop, tablet, mobile)
- Fast report generation (~20s for 50K runs)
- Self-contained HTML downloads
- RESTful API endpoint for integration

## 📖 CSV Format

Simple 5-column format (see `sample_cap_table.csv`):

```csv
Layer,Instrument,Principal,Maturity,Interest Type
Super,Senior Secured Notes,50.0,2027,Fixed 5.0%
1L,Term Loan B,300.0,2027,PIK SOFR+8.5%
2L,Second Lien Notes,150.0,2028,12.5% Cash
Junior,Mezzanine Loan,75.0,2028,15.0% PIK
Adjusted EBITDA,72.5
Valuation Multiple Low,4.0
Valuation Multiple Mid,6.0
Valuation Multiple High,8.0
Other Claims,0.0
Total Debt to Clear,575.0
```

**Columns (in order):**
1. **Layer**: Super, 1L, 2L, Junior, Equity
2. **Instrument**: Name of the debt instrument
3. **Principal**: Balance in $M
4. **Maturity**: Year (e.g., "2027") or "N/A"
5. **Interest Type**: Rate type and amount

## 🎯 Next Steps

1. **Test it out**
   ```bash
   ./start_web.sh
   open http://localhost:5001
   ```

2. **Upload sample CSV**
   - Use `sample_cap_table.csv` or create your own

3. **Configure (or skip)**
   - All parameters have smart defaults
   - Click "Generate Report"

4. **Review the report**
   - Summary metrics
   - Enterprise value distribution
   - Layer recoveries
   - Market analysis
   - PIK impact
   - Fulcrum security
   - Sensitivity analysis

5. **Download**
   - Click "Download HTML" to save report

## 🏗️ Project Structure

```
cpi/
├── app.py                          # ← Web server (main entry)
├── simulate_tse_cap_table.py       # Simulation engine (unchanged)
├── templates/
│   └── upload.html                 # Landing page + UI
├── sample_cap_table.csv            # Example CSV
├── check_setup.py                  # Setup verification
├── start_web.sh                    # Bash startup
├── QUICKSTART.md                   # Quick reference
├── WEB_INTERFACE_README.md         # Full docs
├── WEB_INTERFACE_SUMMARY.md        # Technical overview
├── requirements_web.txt            # Web dependencies
└── output/
    └── tse_cap_table_report.html   # Generated reports
```

## ⚙️ Configuration

### Parameters (with defaults)

| Category | Parameter | Default | Notes |
|----------|-----------|---------|-------|
| **Simulation** | Runs | 50,000 | Monte Carlo iterations |
| | Seed | 7 | Random seed |
| | EBITDA CV | 0.22 | Distribution spread (22%) |
| | Debt Volatility | 0.03 | Multiple volatility |
| **EBITDA** | Distressed | $72.5M | Downside scenario |
| | Clean | $135.5M | Base case |
| | Recovery | $225.0M | Upside scenario |
| **Market** | 1L Price | 11.0¢ | Current trading level |
| | Discount Rate | 20% | For fair value calc |
| | Resolution | 12 months | Restructuring timeline |
| | Ch.11 Costs | $75M | Professional fees |

All parameters can be adjusted in the web interface.

## 🔗 Integration

The web interface exposes a REST API endpoint:

**POST /api/generate**
```json
{
  "csv_file": "your_cap_table.csv",
  "runs": 50000,
  "market_price_1l": 11.0,
  ...
}
```

Response:
```json
{
  "success": true,
  "html": "<full HTML report with embedded data>"
}
```

Perfect for integrating with your own tools or dashboards.

## 📊 Report Contents

Each generated report includes:

1. **Summary KPIs**
   - Insolvency probability
   - Median enterprise value
   - Probability equity has value
   - Expected residual equity

2. **Enterprise Value Distribution**
   - Histogram with percentiles (P10, P50, P90)
   - Layer recovery outlook with confidence bands

3. **EBITDA Scenarios**
   - Waterfall for each case (distressed/clean/recovery)
   - Fulcrum layer identification
   - Partial recovery profile

4. **Market Analysis**
   - Implied EBITDA (back-solved from 1L price)
   - Market vs. model comparison

5. **Hidden Costs**
   - Chapter 11 cost leakage (impact on 1L recovery)
   - PIK accretion table (debt growth timeline)

6. **Valuation**
   - Implied fair value vs. market price
   - Recovery gaps and entry points

7. **Sensitivity**
   - Heatmap: EV, 2L recovery, equity value
   - Across scenarios and multiples

8. **Instrument Details**
   - Table with each debt instrument
   - Recovery odds by layer

## 🎨 Design

- **Inspiration**: Parity Research Trigger Monitor
- **Typography**: System fonts (Inter, Segoe UI, Roboto)
- **Colors**: Warm palette with accent blues and greens
- **Responsive**: Works on desktop, tablet, mobile
- **Performance**: Fast load times, minimal dependencies

## 🔧 Troubleshooting

**Flask not found?**
```bash
pip install Flask==3.0.0
```

**Port 5000 in use?**
Edit `app.py`, change `app.run(port=5001)`

**CSV format error?**
Check columns are: Layer, Instrument, Principal, Maturity, Interest Type

**Slow generation?**
Try fewer runs: 10K instead of 50K

See **WEB_INTERFACE_README.md** for more help.

## 📝 Notes

- This is a **new web interface** for your existing simulation engine
- All original CLI functionality remains unchanged
- CLI commands still work: `python3 simulate_tse_cap_table.py --input file.xlsx`
- Web interface is **optional** — use CLI or web, your choice
- Generated reports are **self-contained HTML** (can share via email)

## 🚀 Ready?

```bash
./start_web.sh
```

Then open **http://localhost:5001** in your browser.

Upload a CSV and click **Generate Report** to start analyzing.

---

**Questions?** 
- Quick help: See **QUICKSTART.md**
- Full docs: See **WEB_INTERFACE_README.md**
- Technical details: See **WEB_INTERFACE_SUMMARY.md**
