"""
SwineIntel — Build Database
============================
Takes raw Syracuse/ folder + external data → produces swineintel.duckdb
with all joined tables ready for the dashboard.

Usage:
    python build_database.py --input Syracuse/ --weather data/weather.csv --disease data/mshmp_disease_incidence.csv
"""

import argparse
import os
import sys
import glob
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np

try:
    import duckdb
except ImportError:
    print("ERROR: pip install duckdb")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# LOADING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def parse_dates(s):
    if s.dtype == "datetime64[ns]":
        return s
    return pd.to_datetime(s.astype(str).str.strip().str.replace(r"\.000$", "", regex=True),
                          format="mixed", dayfirst=False, errors="coerce")


def load_track2(base):
    """Load all 7 Track 2 files with proper date parsing."""
    d = {}
    d['nursery'] = pd.read_csv(f"{base}/2025_dummy_nursery_intake.csv")
    d['nursery']['Placement_Date'] = parse_dates(d['nursery']['Placement_Date'])

    d['flow'] = pd.read_csv(f"{base}/2025_dummy_barn_to_barn_pig_flow.csv")
    d['flow']['Movement_Date'] = parse_dates(d['flow']['Movement_Date'])

    d['hedging'] = pd.read_csv(f"{base}/2025_dummy_hog_hedging_aligned_to_nursery.csv")
    d['hedging']['Trade_Date'] = parse_dates(d['hedging']['Trade_Date'])
    d['hedging']['Expiration_Date'] = parse_dates(d['hedging']['Expiration_Date'])

    d['packer'] = pd.read_csv(f"{base}/2025_dummy_packer_settlement.csv")
    for c in ['Kill_Date', 'Delivery_Date', 'Payment_Date']:
        d['packer'][c] = parse_dates(d['packer'][c])

    d['accounting'] = pd.read_csv(f"{base}/2025_swine_accounting_dummy .csv")
    d['accounting']['Date'] = parse_dates(d['accounting']['Date'])

    d['farrowing'] = pd.read_csv(f"{base}/2025_dummy_sow_farm_weekly_farrowing.csv")
    d['farrowing']['Week_Start'] = parse_dates(d['farrowing']['Week_Start'])

    d['barn_env'] = pd.read_csv(f"{base}/2025_dummy_barn_environmental_utilities.csv")
    d['barn_env']['Date'] = parse_dates(d['barn_env']['Date'])
    return d


def load_endpoint(path):
    ep = pd.read_csv(path, low_memory=False)
    ep['tradingDay'] = parse_dates(ep['tradingDay'])
    return ep.dropna(subset=['tradingDay'])


# ═══════════════════════════════════════════════════════════════════════════════
# TRACK 2 JOINS
# ═══════════════════════════════════════════════════════════════════════════════

def build_lifecycle(data):
    """Nursery + Flow + Hedging → master lifecycle table (18 rows)."""
    nursery = data['nursery']
    flow = data['flow']
    hedging = data['hedging']

    # Get finisher destination per group
    fin = flow[flow.To_Site.str.contains('Finisher', na=False)] \
        .sort_values('Movement_Date') \
        .groupby('Pig_Group_ID').last().reset_index()

    result = nursery.merge(fin[['Pig_Group_ID', 'Movement_Date', 'To_Site', 'To_Barn',
                                 'Head_Moved', 'Avg_Weight_lb']].rename(columns={
        'Movement_Date': 'finisher_entry_date', 'To_Site': 'finisher_site',
        'To_Barn': 'finisher_barn', 'Head_Moved': 'head_to_finisher',
        'Avg_Weight_lb': 'weight_at_finisher_entry'
    }), on='Pig_Group_ID', how='left')

    # Derived
    result['nursery_mortality'] = result['Received_Head'] - result['head_to_finisher']
    result['nursery_mortality_pct'] = (result['nursery_mortality'] / result['Received_Head'] * 100).round(1)
    result['days_in_nursery'] = (result['finisher_entry_date'] - result['Placement_Date']).dt.days
    result['est_market_date'] = result['finisher_entry_date'] + pd.Timedelta(days=112)

    # Merge hedging
    hedge_cols = ['Pig_Group_ID', 'Instrument_Type', 'Buy_Sell', 'Trade_Date',
                  'Expiration_Date', 'Strike_or_Futures_Price_CWT',
                  'Option_or_LRP_Premium_CWT', 'Contract_Month',
                  'Coverage_Percent', 'Head_Covered', 'Contracts']
    result = result.merge(hedging[hedge_cols], on='Pig_Group_ID', how='left')

    # Effective floor
    mask = result['Instrument_Type'].isin(['Lean Hog Put Option', 'LRP Policy'])
    result['effective_floor_cwt'] = np.where(
        mask,
        result['Strike_or_Futures_Price_CWT'] - result['Option_or_LRP_Premium_CWT'],
        result['Strike_or_Futures_Price_CWT']
    )
    result['unhedged_head'] = result['head_to_finisher'] - result['Head_Covered']
    result['unhedged_pct'] = ((1 - result['Coverage_Percent']) * 100).round(1)

    return result


def build_barn_env_stats(data, lifecycle):
    """Aggregate barn environmental stats per group per phase."""
    barn_env = data['barn_env']
    results = []
    for _, row in lifecycle.iterrows():
        pg = row['Pig_Group_ID']
        # Nursery phase
        n_env = barn_env[
            (barn_env.Site == row['Nursery_Site']) & (barn_env.Barn == row['Nursery_Barn']) &
            (barn_env.Date >= row['Placement_Date']) & (barn_env.Date <= row['finisher_entry_date'])
        ]
        # Finisher phase
        f_env = barn_env[
            (barn_env.Site == row['finisher_site']) & (barn_env.Barn == row['finisher_barn']) &
            (barn_env.Date >= row['finisher_entry_date']) & (barn_env.Date <= row['est_market_date'])
        ]
        results.append({
            'Pig_Group_ID': pg,
            'nursery_env_days': len(n_env),
            'nursery_avg_temp_f': n_env.Avg_Temperature_F.mean() if len(n_env) else None,
            'nursery_avg_humidity': n_env.Avg_Humidity_pct.mean() if len(n_env) else None,
            'nursery_avg_water': n_env.Water_Usage_Gallons.mean() if len(n_env) else None,
            'nursery_temp_alerts': (n_env.Avg_Temperature_F < 40).sum() if len(n_env) else 0,
            'finisher_env_days': len(f_env),
            'finisher_avg_temp_f': f_env.Avg_Temperature_F.mean() if len(f_env) else None,
            'finisher_avg_humidity': f_env.Avg_Humidity_pct.mean() if len(f_env) else None,
            'finisher_avg_water': f_env.Water_Usage_Gallons.mean() if len(f_env) else None,
            'finisher_temp_alerts': (f_env.Avg_Temperature_F < 40).sum() if len(f_env) else 0,
        })
    return pd.DataFrame(results)


def build_packer_analysis(data, track1_dir):
    """Packer settlements with cash market basis (merge_asof)."""
    packer = data['packer'].copy().sort_values('Kill_Date')

    # Eval price decomposition
    packer['eval_check'] = packer['Base_Price_CWT'] + packer['Matrix_Premium_CWT'] + \
                           packer['Sort_Loss_CWT'] + packer['VOB_CWT']

    # Cash price join
    national = pd.read_csv(f"{track1_dir}/National.csv")
    national['report_date'] = parse_dates(national['report_date'])

    indexes = pd.read_csv(f"{track1_dir}/Indexes.csv")
    indexes['report_date'] = parse_dates(indexes['report_date'])
    idx_neg = indexes[indexes.purchase_type == 'Prod. Sold Negotiated'][
        ['report_date', 'Amount']
    ].rename(columns={'Amount': 'cme_index_value'}).sort_values('report_date')

    tol = pd.Timedelta(days=5)
    result = pd.merge_asof(
        packer,
        national[['report_date', 'wtd_avg']].rename(
            columns={'report_date': 'Kill_Date', 'wtd_avg': 'national_cash_cwt'}
        ).sort_values('Kill_Date'),
        on='Kill_Date', direction='nearest', tolerance=tol
    )
    result = pd.merge_asof(
        result.sort_values('Kill_Date'),
        idx_neg.rename(columns={'report_date': 'Kill_Date'}).sort_values('Kill_Date'),
        on='Kill_Date', direction='nearest', tolerance=tol
    )
    result['basis_vs_cme'] = result['Base_Price_CWT'] - result['cme_index_value']
    return result


def build_hedge_pnl(data, ep):
    """Hedge P&L using latest futures close from EndPointList."""
    hedging = data['hedging'].copy()

    MONTH_CODE = {'Jan': 'F', 'Feb': 'G', 'Mar': 'H', 'Apr': 'J', 'May': 'K', 'Jun': 'M',
                  'Jul': 'N', 'Aug': 'Q', 'Sep': 'U', 'Oct': 'V', 'Nov': 'X', 'Dec': 'Z'}
    HE_VALID = {'G', 'J', 'K', 'M', 'N', 'Q', 'V', 'Z'}
    HE_FALLBACK = {'F': 'G', 'H': 'J', 'U': 'V', 'X': 'Z'}

    def to_symbol(cm):
        parts = cm.split('-')
        code = MONTH_CODE.get(parts[1], '?')
        if code not in HE_VALID:
            code = HE_FALLBACK.get(code, code)
        return f"HE{code}{parts[0]}"

    hedging['symbol'] = hedging['Contract_Month'].apply(to_symbol)

    he = ep[ep.Commodity == 'HE']
    results = []
    for _, row in hedging.iterrows():
        sym = row['symbol']
        latest = he[he.symbol == sym].sort_values('tradingDay').tail(1)
        current = latest.close.values[0] if len(latest) else None
        current_date = latest.tradingDay.values[0] if len(latest) else None

        hedge_price = row['Strike_or_Futures_Price_CWT']
        premium = row['Option_or_LRP_Premium_CWT']
        instrument = row['Instrument_Type']

        if current is not None:
            if instrument == 'Lean Hog Futures':
                pnl_cwt = hedge_price - current
            elif instrument in ('Lean Hog Put Option', 'LRP Policy'):
                floor = hedge_price - premium
                pnl_cwt = max(0, floor - current) - premium
            else:
                pnl_cwt = 0
            pnl_per_head = pnl_cwt * 2.10
            total_pnl = pnl_per_head * row['Head_Covered']
        else:
            pnl_cwt = pnl_per_head = total_pnl = None

        results.append({
            'Pig_Group_ID': row['Pig_Group_ID'],
            'symbol': sym,
            'contract_month': row['Contract_Month'],
            'instrument_type': instrument,
            'hedge_price_cwt': hedge_price,
            'premium_cwt': premium,
            'current_market_cwt': current,
            'market_date': current_date,
            'pnl_per_cwt': pnl_cwt,
            'pnl_per_head': pnl_per_head,
            'total_pnl': total_pnl,
            'head_covered': row['Head_Covered'],
            'coverage_pct': row['Coverage_Percent'],
        })

    return pd.DataFrame(results)


def build_packer_ranking(data):
    """Rank packer/contract combos by eval price."""
    packer = data['packer']
    ranking = packer.groupby(['Packer', 'Contract_Type']).agg(
        avg_eval_cwt=('Eval_Price_CWT', 'mean'),
        avg_matrix=('Matrix_Premium_CWT', 'mean'),
        avg_sort_loss=('Sort_Loss_CWT', 'mean'),
        avg_vob=('VOB_CWT', 'mean'),
        loads=('Settlement_ID', 'count'),
        total_head=('Delivered_Head', 'sum'),
    ).reset_index().sort_values('avg_eval_cwt', ascending=False)
    ranking['rank'] = range(1, len(ranking) + 1)
    # Revenue per head vs worst combo
    worst = ranking.avg_eval_cwt.min()
    ranking['advantage_per_head'] = ((ranking.avg_eval_cwt - worst) * 2.10).round(2)
    return ranking


def build_feed_cost(ep):
    """Daily feed cost proxy from corn + soybean meal futures."""
    zc = ep[ep.Commodity == 'ZC'].groupby('tradingDay')['close'].first().reset_index()
    zc.columns = ['date', 'corn_cents_bu']
    zm = ep[ep.Commodity == 'ZM'].groupby('tradingDay')['close'].first().reset_index()
    zm.columns = ['date', 'sbm_usd_ton']
    zc = zc.sort_values('date')
    zm = zm.sort_values('date')

    result = pd.merge_asof(zc, zm, on='date', direction='nearest', tolerance=pd.Timedelta(days=3))
    result['corn_usd_ton'] = result['corn_cents_bu'] / 100 * (2000 / 56)
    result['feed_cost_usd_ton'] = (0.75 * result['corn_usd_ton']) + (0.20 * result['sbm_usd_ton'])
    result['feed_cost_per_head'] = result['feed_cost_usd_ton'] * (800 / 2000)
    return result


def build_hog_dashboard(ep, track1_dir):
    """Daily hog market dashboard: futures + cash + index + cutout."""
    he = ep[ep.Commodity == 'HE'].sort_values('tradingDay')
    he_daily = he.groupby('tradingDay')['close'].first().reset_index()
    he_daily.columns = ['date', 'he_front_close']

    national = pd.read_csv(f"{track1_dir}/National.csv")
    national['report_date'] = parse_dates(national['report_date'])
    nat = national[['report_date', 'wtd_avg']].rename(
        columns={'report_date': 'date', 'wtd_avg': 'national_cash'}
    ).sort_values('date')

    indexes = pd.read_csv(f"{track1_dir}/Indexes.csv")
    indexes['report_date'] = parse_dates(indexes['report_date'])
    cme_idx = indexes[indexes.purchase_type == 'Prod. Sold Negotiated'][
        ['report_date', 'Amount']
    ].rename(columns={'report_date': 'date', 'Amount': 'cme_index'}).sort_values('date')

    tol = pd.Timedelta(days=3)
    result = he_daily.copy()
    for df in [nat, cme_idx]:
        result = pd.merge_asof(result, df, on='date', direction='nearest', tolerance=tol)
    result['basis'] = result['national_cash'] - result['he_front_close']
    return result


def build_seasonal_avg(ep):
    """24-year monthly average CME lean hog index for seasonal context."""
    indexes = pd.read_csv(os.path.join(os.path.dirname(ep.tradingDay.dtype.name or ''), ''),
                          low_memory=False) if False else None
    # Use EndPointList HE for seasonal pattern
    he = ep[ep.Commodity == 'HE'].copy()
    he['month'] = he.tradingDay.dt.month
    seasonal = he.groupby('month')['close'].agg(['mean', 'min', 'max', 'std']).reset_index()
    seasonal.columns = ['month', 'avg_price', 'min_price', 'max_price', 'std_price']
    return seasonal


def build_hedge_vs_packer_period(data):
    """Proxy P&L: hedge level vs packer avg price in target month."""
    hedging = data['hedging']
    packer = data['packer'].copy()

    month_map = {
        '25-Jun': (2025, 6), '25-Jul': (2025, 7), '25-Aug': (2025, 8),
        '25-Sep': (2025, 9), '25-Oct': (2025, 10), '25-Nov': (2025, 11),
        '26-Jan': (2026, 1),
    }

    results = []
    for _, row in hedging.iterrows():
        cm = row['Contract_Month']
        yr, mo = month_map.get(cm, (None, None))
        month_pk = packer[(packer.Kill_Date.dt.month == mo) & (packer.Kill_Date.dt.year == yr)] if yr else pd.DataFrame()

        hedge_price = row['Strike_or_Futures_Price_CWT']
        premium = row['Option_or_LRP_Premium_CWT']
        instrument = row['Instrument_Type']

        avg_eval = month_pk.Eval_Price_CWT.mean() if len(month_pk) else None
        if avg_eval is not None:
            if instrument == 'Lean Hog Futures':
                pnl = hedge_price - avg_eval
            else:
                floor = hedge_price - premium
                pnl = max(0, floor - avg_eval) - premium
        else:
            pnl = None

        results.append({
            'Pig_Group_ID': row['Pig_Group_ID'],
            'contract_month': cm,
            'instrument': instrument,
            'hedge_price_cwt': hedge_price,
            'premium_cwt': premium,
            'packer_avg_eval_cwt': avg_eval,
            'proxy_hedge_pnl_cwt': pnl,
            'has_packer_data': len(month_pk) > 0,
        })
    return pd.DataFrame(results)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — BUILD THE DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

def build(input_dir, weather_path=None, disease_path=None, output="swineintel.duckdb"):
    print("=" * 60)
    print("SwineIntel — Building Database")
    print("=" * 60)

    t2_dir = os.path.join(input_dir, "Track 2")
    if not os.path.isdir(t2_dir):
        print(f"ERROR: {t2_dir} not found")
        sys.exit(1)

    # Load raw data
    print("\n[1/8] Loading Track 2 data...")
    data = load_track2(t2_dir)

    print("[2/8] Loading EndPointList...")
    ep = load_endpoint(os.path.join(input_dir, "EndPointList.csv"))

    # Build joined tables
    print("[3/8] Building lifecycle (nursery + flow + hedging)...")
    lifecycle = build_lifecycle(data)
    env_stats = build_barn_env_stats(data, lifecycle)
    lifecycle = lifecycle.merge(env_stats, on='Pig_Group_ID')

    print("[4/8] Building hedge P&L (live mark-to-market)...")
    hedge_pnl = build_hedge_pnl(data, ep)

    print("[5/8] Building packer analysis...")
    packer_basis = build_packer_analysis(data, input_dir)
    packer_ranking = build_packer_ranking(data)
    hedge_vs_packer = build_hedge_vs_packer_period(data)

    print("[6/8] Building feed cost + hog dashboard...")
    feed_cost = build_feed_cost(ep)
    hog_dashboard = build_hog_dashboard(ep, input_dir)

    print("[7/8] Building seasonal averages...")
    he_seasonal = ep[ep.Commodity == 'HE'].copy()
    he_seasonal['month'] = he_seasonal.tradingDay.dt.month
    seasonal = he_seasonal.groupby('month')['close'].agg(['mean', 'min', 'max']).reset_index()
    seasonal.columns = ['month', 'avg_price', 'min_price', 'max_price']

    # Load into DuckDB
    print(f"[8/8] Loading into DuckDB → {output}...")
    if os.path.exists(output):
        os.remove(output)
    conn = duckdb.connect(output)

    tables = {
        'lifecycle': lifecycle,
        'hedge_pnl': hedge_pnl,
        'packer_basis': packer_basis,
        'packer_ranking': packer_ranking,
        'hedge_vs_packer': hedge_vs_packer,
        'feed_cost': feed_cost,
        'hog_dashboard': hog_dashboard,
        'seasonal_avg': seasonal,
        'barn_env': data['barn_env'],
        'farrowing': data['farrowing'],
        'accounting': data['accounting'],
        'nursery': data['nursery'],
        'endpointlist': ep,
    }

    # External data
    if weather_path and os.path.exists(weather_path):
        tables['weather'] = pd.read_csv(weather_path)
        tables['weather']['date'] = parse_dates(tables['weather']['date'])
        print(f"  + weather: {len(tables['weather'])} rows")

    if disease_path and os.path.exists(disease_path):
        tables['disease'] = pd.read_csv(disease_path)
        print(f"  + disease: {len(tables['disease'])} rows")

    # Also load LRP and CME options for hedge comparison
    lrp_path = os.path.join(input_dir, "LRP Quotes.csv")
    if os.path.exists(lrp_path):
        tables['lrp_quotes'] = pd.read_csv(lrp_path)
    cme_path = os.path.join(input_dir, "lookup_CME.csv")
    if os.path.exists(cme_path):
        tables['lookup_cme'] = pd.read_csv(cme_path)

    # Synthetic data for barn monitoring (investigation mode)
    synthetic_dir = os.path.join(os.path.dirname(weather_path), '') if weather_path else 'data/'
    synthetic_files = {
        'feed_consumption': 'synthetic_feed_consumption.csv',
        'weekly_mortality': 'synthetic_weekly_mortality.csv',
        'treatment_logs': 'synthetic_treatment_logs.csv',
        'feed_delivery': 'synthetic_feed_delivery.csv',
        'employee_entry': 'synthetic_employee_entry.csv',
    }
    for table_name, filename in synthetic_files.items():
        for search_dir in [synthetic_dir, 'data/', '']:
            fpath = os.path.join(search_dir, filename)
            if os.path.exists(fpath):
                tables[table_name] = pd.read_csv(fpath)
                print(f"  + {table_name}: {len(tables[table_name])} rows")
                break

    for name, df in tables.items():
        conn.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        print(f"  {name:20s}: {len(df):>6,} rows × {len(df.columns)} cols")

    conn.close()
    print(f"\n✓ Database ready: {output}")
    print(f"  Tables: {len(tables)}")
    print(f"  Size: {os.path.getsize(output) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build SwineIntel database")
    p.add_argument("--input", default="Syracuse/", help="Path to Syracuse/ folder")
    p.add_argument("--weather", default="data/barn_env_with_weather.csv", help="NOAA weather CSV")
    p.add_argument("--disease", default="data/mshmp_disease_incidence.csv", help="MSHMP disease CSV")
    p.add_argument("--output", default="swineintel.duckdb", help="Output DuckDB file")
    args = p.parse_args()
    build(args.input, args.weather, args.disease, args.output)
