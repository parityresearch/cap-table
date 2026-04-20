# TSE Cap Table Web Interface — Quick Start

## 1-Minute Setup

```bash
# Navigate to project
cd /Users/gurmandhaliwal/cpi

# Install Flask (one-time)
pip install -r requirements_web.txt

# Start server
python3 app.py

# Open browser
open http://localhost:5000
```

## What You'll See

### 🎨 Landing Page
- **Hero Section**: Explains what the tool does (Monte Carlo recovery analysis)
- **Upload Area**: Drag-and-drop CSV or click to select
- **Parameter Panels**: Pre-filled with sensible defaults
- **Generate Button**: Starts the simulation

### 📊 Generated Report
After upload and generation, you'll see:
- **Summary KPIs**: Insolvency probability, median EV, equity probability
- **EV Distribution**: Histogram with P10/P50/P90 percentiles
- **Layer Recovery**: Expected recovery by tranche with confidence bands
- **EBITDA Scenarios**: Waterfall for distressed/clean/recovery cases
- **Market Analysis**: Implied EBITDA back-solved from 1L trading price
- **Fulcrum Security**: Which tranche converts to equity, partial recovery profile
- **Ch.11 Leakage**: Cost impact on 1L recovery (in basis points)
- **PIK Accretion**: Debt growth over restructuring months
- **Implied Pricing**: Fair value vs market price for 1L debt with gap analysis
- **Sensitivity Map**: Heatmap of EV/2L recovery/equity across scenarios
- **Instrument Detail**: Table with each debt instrument and recovery odds

## CSV Format

Create a file named `my_cap_table.csv`:

```csv
Layer,Instrument,Principal,Maturity,Interest Type
Super,Senior Secured Notes,50.0,2027,Fixed 5.0%
1L,Term Loan B,300.0,2027,PIK SOFR+8.5%
2L,Second Lien Notes,150.0,2028,12.5% Cash
Junior,Mezzanine Loan,75.0,2028,15.0% PIK
Equity,Equity Rollover,25.0,N/A,Equity
Adjusted EBITDA,72.5
Valuation Multiple Low,4.0
Valuation Multiple Mid,6.0
Valuation Multiple High,8.0
Other Claims,0.0
Total Debt to Clear,575.0
```

**Required columns (in order):**
1. Layer: `Super`, `1L`, `2L`, `Junior`, `Equity`
2. Instrument: Name of the debt instrument
3. Principal: Balance in $M
4. Maturity: Year (e.g., "2027") or "N/A"
5. Interest Type: Rate type and amount (e.g., "PIK SOFR+8.5%")

**Metadata rows at bottom** (after first blank line):
- `Adjusted EBITDA`: Base EBITDA estimate in $M
- `Valuation Multiple Low/Mid/High`: Exit multiple range (e.g., 4x–8x)
- `Other Claims`: Non-debt obligations in $M
- `Total Debt to Clear`: Total leverage at entry

## Parameter Guide

### Simulation
| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| **Runs** | 50,000 | 100–500K | Monte Carlo iterations. More = more accurate, slower. Start with 5K for testing. |
| **Seed** | 7 | Any int | Random seed. Use same seed for reproducible results. |
| **EBITDA CV** | 0.22 | 0–1 | Coefficient of variation. 0.22 = 22% spread in outcomes. |
| **Debt Vol** | 0.03 | 0–0.5 | Multiple volatility. 3% = small variance in exit values. |

### EBITDA Scenarios
| Parameter | Default | Notes |
|-----------|---------|-------|
| **Distressed** | $72.5M | Annualized Q3/Q4 run-rate. Downside case. |
| **Clean** | $135.5M | Reported EBITDA minus one-time items. Base case. |
| **Recovery** | $225.0M | Management target / normalized demand. Upside case. |

### Market & Bankruptcy
| Parameter | Default | Notes |
|-----------|---------|-------|
| **Market Price (1L)** | 11.0¢ | Where 1L debt currently trades. (0–100) |
| **Discount Rate** | 20% | Distressed discount for fair value calc. Higher = more haircut. |
| **Resolution** | 12 months | Time to restructuring exit. Drives PIK accretion. |
| **Ch.11 Costs** | $75M | Professional fees (legal, accounting, restructuring). |

## Common Workflows

### 🎯 Scenario Analysis
1. Upload your cap table
2. Adjust EBITDA cases to reflect market expectations
3. Vary 1L market price (11¢ → 15¢ → 20¢) and re-run
4. Compare fulcrum layer across scenarios

### 📉 Stress Testing
1. Keep base parameters, lower distressed EBITDA by 20%
2. Increase Ch.11 costs from $75M → $150M
3. Increase resolution timeline from 12 → 18 months
4. Watch equity probability → 0%

### 💰 Valuation
1. Use market 1L price (e.g., 11¢)
2. Check "Implied fair value" for 1L in the report
3. Gap = upside (if fair value > market price)
4. Gross return = (recovery ÷ market price − 1) × 100%

### 🔍 PIK Impact
1. Check PIK accretion table
2. If $30M additional debt from TLB PIK, that's bad
3. Reduces 1L recovery by ~10% (depending on EV)
4. Highlight: every month of delay costs ~$2.5M in PIK

## Troubleshooting

**"Invalid CSV format"**
→ Check columns are: Layer, Instrument, Principal, Maturity, Interest Type (in order, no extra columns)

**"No instruments found"**
→ Layers must be one of: Super, 1L, 2L, Junior, Equity. Check spelling in Layer column.

**"Generation timed out"**
→ Try reducing runs from 50K → 5K. Still gives good results for early analysis.

**"Flask not found"**
→ Run: `pip install Flask==3.0.0`

**Port 5000 already in use**
→ Change port in `app.py`: `app.run(port=5001)`

## Advanced

### Batch Processing
If you have multiple cap tables, use the CLI directly:
```bash
python3 simulate_tse_cap_table.py \
  --input my_cap_table.xlsx \
  --output-dir results \
  --runs 10000 \
  --ebitda-distressed 60.0 \
  --market-price-1l 15.0
```

### Headless Mode
Export the generated HTML and embed in reports or dashboards:
```javascript
// After generation, download the HTML
const link = document.createElement('a');
link.href = URL.createObjectURL(new Blob([generatedHtml]));
link.download = 'report.html';
link.click();
```

### Integration
Hook the `/api/generate` endpoint into your own tools:
```python
import requests

response = requests.post('http://localhost:5000/api/generate', 
  files={'csv_file': open('my_cap_table.csv', 'rb')},
  data={'runs': 5000, 'market_price_1l': 15.0}
)
html = response.json()['html']
```

---

**Questions?** See `WEB_INTERFACE_README.md` for full documentation.
