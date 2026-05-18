# 🐷 SwineIntel — Pro Ag Analytics Platform

**Syracuse CCDS AI & Analytics Innovation Challenge 2026 — Track 2**

---

## Quick Start (5 minutes)

### Step 1: Place all files

```
SwineIntel/
├── Syracuse/                              ← unzip Syracuse.zip here
│   ├── Track 2/
│   │   ├── 2025_dummy_nursery_intake.csv
│   │   ├── 2025_dummy_barn_to_barn_pig_flow.csv
│   │   ├── 2025_dummy_hog_hedging_aligned_to_nursery.csv
│   │   ├── 2025_dummy_packer_settlement.csv
│   │   ├── 2025_swine_accounting_dummy .csv
│   │   ├── 2025_dummy_sow_farm_weekly_farrowing.csv
│   │   └── 2025_dummy_barn_environmental_utilities.csv
│   ├── EndPointList.csv
│   ├── National.csv
│   ├── Indexes.csv
│   ├── LRP Quotes.csv
│   ├── lookup_CME.csv
│   └── ... (other Track 1 CSVs)
│
├── data/
│   ├── barn_env_with_weather.csv
│   ├── mshmp_disease_incidence.csv
│   ├── mshmp_2025_summary.csv
│   ├── synthetic_feed_consumption.csv
│   ├── synthetic_weekly_mortality.csv
│   ├── synthetic_treatment_logs.csv
│   ├── synthetic_feed_delivery.csv
│   └── synthetic_employee_entry.csv
│
├── build_database.py
├── ai_assistant.py
├── app.py
├── requirements.txt
└── README.md
```

### Step 2: Create virtual environment and install

```powershell
cd SwineIntel
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you get an execution policy error:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Step 3: Build the database

```powershell
python build_database.py --input Syracuse/ --weather data/barn_env_with_weather.csv --disease data/mshmp_disease_incidence.csv
```

Creates `swineintel.duckdb` (~50 MB) with 22 tables.

### Step 4: Run the dashboard

```powershell
streamlit run app.py
```

Opens at `http://localhost:8501`

---

## Optional: Enable AI Narrative (Claude API)

Without a key, the AI chat uses built-in formatted responses (still functional).
With a key, responses read like a consultant wrote them.

Create a `.env` file in the project root:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Get your key at: https://console.anthropic.com

---

## What You'll See

### Level 1 — Overview (5 seconds)
Three metric cards + one alert bar:
- Operation margin ($/head across all groups)
- Groups needing action (low coverage or tight margin)
- Packer opportunity ($6.70/head Smithfield CARCASS advantage)
- Barn alert (water anomaly detection)

### Level 2 — Groups needing attention (30 seconds)
Simplified table showing only groups that need action.
Click "View all 18 groups" to expand.

### Level 3 — Group detail + AI assistant
**Left panel** — select any group:
- Margin, hedge P&L, coverage, health, packer recommendation
- 🌡️ Day-to-day monitoring: temp, humidity, water, outdoor weather, feed chart
- 📋 Weekly mortality trend: deaths per nursery week
- 🔍 Investigation mode: treatment logs, feed delivery, employee entry
- 🦠 Disease context: PRRS/PED incidence from MSHMP
- 🛡️ Hedge comparison: LRP vs CME put

**Right panel** — AI assistant:
- Quick scenarios: Hogs ±$5, Corn $5, Packer ranking
- Chat: ask anything about the operation

### Sample AI chat questions
```
How is PG-1007 doing?
Which groups are losing money?
What if hogs drop $10?
Where should I ship PG-1004?
Compare LRP vs put
Any barn alerts?
Add 20% hedge to PG-1004
What if corn goes to $6?
Give me a weekly summary
```

---

## Data Sources

| Source | Type | Files |
|--------|------|-------|
| Pro Ag (Syracuse.zip) | Contest | 7 Track 2 + 29 Track 1 CSVs + 26 XLSX |
| NOAA Climate Data Online | Public | Daily temp, wind — Sioux Falls SD |
| MSHMP (U. of Minnesota) | Public | PRRS + PED disease incidence |
| ISU Growth Model | Published | Feed intake curves by pig weight |
| Synthetic placeholders | Generated | Treatment, delivery, employee logs |

---

## Database Tables (swineintel.duckdb)

| Table | Rows | Description |
|-------|------|-------------|
| lifecycle | 18 | Master: nursery + flow + hedging + barn env per group |
| hedge_pnl | 18 | Live mark-to-market from EndPointList futures |
| packer_basis | 180 | Settlements with cash market basis |
| packer_ranking | ~12 | Packer × contract ranked by eval price |
| feed_cost | ~1,800 | Daily feed cost from corn + SBM futures |
| hog_dashboard | ~1,800 | Daily HE futures + cash + index + basis |
| seasonal_avg | 12 | Monthly HE averages for seasonal context |
| weather | 3,285 | Barn env merged with NOAA outdoor weather |
| disease | 116 | PRRS + PED cumulative incidence |
| lrp_quotes | 372 | LRP insurance with CME comparison |
| lookup_cme | 13,049 | CME put option premiums |
| feed_consumption | 2,016 | Synthetic daily feed intake (ISU model) |
| weekly_mortality | 144 | Synthetic weekly nursery mortality |
| treatment_logs | 30 | Synthetic treatment records |
| feed_delivery | 336 | Synthetic feed delivery logs |
| employee_entry | 6,792 | Synthetic barn entry records |
| endpointlist | ~84,000 | All CME futures daily closes |

---

## Architecture

```
Syracuse.zip (raw contest data)
       │
       ▼
build_database.py ──→ swineintel.duckdb (22 tables)
       │                      │
       │                      ▼
       │               app.py (Streamlit)
       │                 ├── Level 1: metrics + alert
       │                 ├── Level 2: attention table
       │                 └── Level 3: detail + AI chat
       │                              │
       │                              ▼
       │                      ai_assistant.py
       │                        ├── Intent classification
       │                        ├── DuckDB queries
       │                        └── Claude API (optional)
data/ (external + synthetic)
  ├── NOAA weather (real)
  ├── MSHMP disease (real)
  └── Synthetic placeholders (labeled)
```

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `No module named 'duckdb'` | `pip install duckdb` |
| `No module named 'dotenv'` | `pip install python-dotenv` |
| `FileNotFoundError: swineintel.duckdb` | Run `build_database.py` first |
| Execution policy error (Windows) | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| AI returns raw data | Set `ANTHROPIC_API_KEY` in `.env` file |
| All zeros / NaN | Check Syracuse/ and data/ folders have all files |
