"""
SwineIntel — Production Data Ingestion Module
===============================================
Follows industry best practices from ImportKit, CSVBox, and Sherlock/Sato research.

Multi-signal mapping pipeline:
  Step 1: Exact match (column name identical)
  Step 2: Case-insensitive + whitespace normalization
  Step 3: Abbreviation expansion (hd→head, wt→weight, amt→amount, qty→quantity)
  Step 4: Fuzzy string matching (edit distance)
  Step 5: AI semantic matching (Claude — handles "compensation"→"Total_Cost")
  Step 6: Value-based type inference (dates, numbers, categories from content)

Each mapping gets a confidence score:
  HIGH (≥0.9): auto-selected, green badge
  MEDIUM (0.5-0.89): suggested, amber badge — user should verify
  LOW (<0.5): flagged, red badge — user must select manually

Post-mapping validation:
  - Date format detection and parsing
  - Numeric range checks (negative costs, unreasonable weights)
  - Required field presence
  - Row-level error reporting
"""

import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import re
from difflib import SequenceMatcher

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


def has_ai():
    return HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# STANDARD SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMAS = {
    "Nursery Intake": {
        "description": "Pig group placements into nursery barns",
        "columns": {
            "Pig_Group_ID": {"type": "string", "required": True, "desc": "Group identifier", "pattern": r"^[A-Z]{2,4}[-_]?\d+"},
            "Placement_Date": {"type": "date", "required": True, "desc": "Date pigs arrived"},
            "Nursery_Site": {"type": "string", "required": False, "desc": "Nursery facility"},
            "Nursery_Barn": {"type": "string", "required": False, "desc": "Barn within site"},
            "Expected_Head": {"type": "numeric", "required": False, "desc": "Expected pig count", "min": 0, "max": 10000},
            "Received_Head": {"type": "numeric", "required": True, "desc": "Actual pigs received", "min": 0, "max": 10000},
            "Avg_Weight_In_lb": {"type": "numeric", "required": False, "desc": "Arrival weight lbs", "min": 5, "max": 50},
            "Health_Status": {"type": "category", "required": False, "desc": "Negative/Monitored/Positive", "values": ["Negative", "Monitored", "Positive"]},
        }
    },
    "Packer Settlement": {
        "description": "Settlement details from packing plants",
        "columns": {
            "Kill_Date": {"type": "date", "required": True, "desc": "Processing date"},
            "Packer": {"type": "string", "required": True, "desc": "Packing company"},
            "Plant": {"type": "string", "required": False, "desc": "Plant location"},
            "Contract_Type": {"type": "category", "required": False, "desc": "LEAN/NEGOTIATED/CARCASS", "values": ["LEAN", "NEGOTIATED", "CARCASS"]},
            "Delivered_Head": {"type": "numeric", "required": True, "desc": "Head delivered", "min": 0, "max": 5000},
            "Avg_Live_Wt_lb": {"type": "numeric", "required": False, "desc": "Live weight", "min": 200, "max": 350},
            "Avg_Carc_Wt_lb": {"type": "numeric", "required": False, "desc": "Carcass weight", "min": 150, "max": 280},
            "Base_Price_CWT": {"type": "numeric", "required": True, "desc": "Base price $/cwt", "min": 40, "max": 150},
            "Matrix_Premium_CWT": {"type": "numeric", "required": False, "desc": "Quality premium"},
            "Sort_Loss_CWT": {"type": "numeric", "required": False, "desc": "Sort penalty"},
            "Net_Payment_$": {"type": "numeric", "required": False, "desc": "Final payment"},
        }
    },
    "Accounting / Costs": {
        "description": "Production cost records",
        "columns": {
            "Date": {"type": "date", "required": True, "desc": "Expense date"},
            "Cost_Category": {"type": "category", "required": True, "desc": "Feed/Health/Labor/etc.", "values": ["Feed", "Health", "Labor", "Facilities", "Transportation", "Overhead", "Animal"]},
            "Cost_Subcategory": {"type": "string", "required": False, "desc": "Specific cost type"},
            "Total_Cost": {"type": "numeric", "required": True, "desc": "Dollar amount", "min": 0},
            "Quantity": {"type": "numeric", "required": False, "desc": "Units"},
            "Unit_Cost": {"type": "numeric", "required": False, "desc": "Cost per unit"},
            "Vendor": {"type": "string", "required": False, "desc": "Supplier"},
            "Site": {"type": "string", "required": False, "desc": "Farm site"},
            "Production_Phase": {"type": "category", "required": False, "desc": "Breeding/Nursery/Finishing", "values": ["Breeding", "Nursery", "Finishing", "Gilt Development"]},
            "Pig_Group_ID": {"type": "string", "required": False, "desc": "Group identifier"},
        }
    },
    "Hedging Positions": {
        "description": "Futures, options, and LRP hedge positions",
        "columns": {
            "Pig_Group_ID": {"type": "string", "required": True, "desc": "Group this hedge covers"},
            "Instrument_Type": {"type": "category", "required": True, "desc": "Futures/Put/LRP", "values": ["Lean Hog Futures", "Lean Hog Put Option", "LRP Policy"]},
            "Trade_Date": {"type": "date", "required": True, "desc": "When hedge was placed"},
            "Expiration_Date": {"type": "date", "required": False, "desc": "When it expires"},
            "Strike_or_Futures_Price_CWT": {"type": "numeric", "required": True, "desc": "Price $/cwt", "min": 40, "max": 150},
            "Coverage_Percent": {"type": "numeric", "required": False, "desc": "Fraction hedged", "min": 0, "max": 1},
            "Head_Covered": {"type": "numeric", "required": False, "desc": "Head covered", "min": 0, "max": 10000},
            "Contract_Month": {"type": "string", "required": False, "desc": "e.g., 25-Aug"},
        }
    },
    "Barn Environmental": {
        "description": "Daily barn conditions and utility readings",
        "columns": {
            "Date": {"type": "date", "required": True, "desc": "Reading date"},
            "Site": {"type": "string", "required": True, "desc": "Farm site"},
            "Barn": {"type": "string", "required": True, "desc": "Specific barn"},
            "Avg_Temperature_F": {"type": "numeric", "required": False, "desc": "Temperature °F", "min": -20, "max": 120},
            "Avg_Humidity_pct": {"type": "numeric", "required": False, "desc": "Humidity %", "min": 0, "max": 100},
            "Water_Usage_Gallons": {"type": "numeric", "required": False, "desc": "Water gal/day", "min": 0},
            "Power_Usage_kWh": {"type": "numeric", "required": False, "desc": "Electricity kWh", "min": 0},
        }
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# ABBREVIATION MAP — expands common shorthands
# ═══════════════════════════════════════════════════════════════════════════════

ABBREVIATIONS = {
    'hd': 'head', 'wt': 'weight', 'amt': 'amount', 'qty': 'quantity',
    'avg': 'average', 'num': 'number', 'dt': 'date', 'pct': 'percent',
    'temp': 'temperature', 'carc': 'carcass', 'cwt': 'hundredweight',
    'del': 'delivered', 'recv': 'received', 'grp': 'group',
    'loc': 'location', 'desc': 'description', 'cat': 'category',
    'sub': 'subcategory', 'prod': 'production', 'fac': 'facilities',
    'trans': 'transportation', 'freq': 'frequency', 'yr': 'year',
    'mo': 'month', 'wk': 'week', 'lbs': 'pounds', 'lb': 'pounds',
    'gal': 'gallons', 'kwh': 'kilowatt_hours',
    # Domain synonyms — hog production
    'expense': 'cost', 'acct': 'accounting', 'supplier': 'vendor',
    'farm': 'site', 'stage': 'phase', 'type': 'category',
    'pay': 'payment', 'settlement': 'payment', 'ship': 'delivery',
    'barn': 'barn', 'bldg': 'barn', 'building': 'barn',
    'pig': 'pig', 'hog': 'pig', 'swine': 'pig',
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1-4: RULE-BASED MATCHING CASCADE
# ═══════════════════════════════════════════════════════════════════════════════

def normalize(name):
    """Normalize column name for comparison."""
    s = name.lower().strip()
    s = re.sub(r'[_\-\s/\\]+', ' ', s)
    s = re.sub(r'[^\w\s]', '', s)
    return s.strip()


def expand_abbreviations(name):
    """Expand known abbreviations."""
    words = normalize(name).split()
    expanded = [ABBREVIATIONS.get(w, w) for w in words]
    return ' '.join(expanded)


def fuzzy_score(a, b):
    """SequenceMatcher ratio between two normalized strings."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def detect_data_type(series):
    """Infer semantic type from actual values (Step 6)."""
    sample = series.dropna().head(100)
    if len(sample) == 0:
        return "unknown", 0.0

    str_vals = sample.astype(str)

    # Try numeric FIRST (before dates, since numbers like 12 or 4250 can parse as dates)
    try:
        numeric = pd.to_numeric(sample, errors='coerce')
        if numeric.notna().mean() > 0.8:
            return "numeric", numeric.notna().mean()
    except:
        pass

    # Try date — only if values look like dates (contain / or - with letters/long strings)
    has_date_chars = str_vals.str.contains(r'[/\-]', regex=True).mean() > 0.5
    avg_len = str_vals.str.len().mean()
    if has_date_chars and avg_len > 6:
        try:
            parsed = pd.to_datetime(sample, format='mixed', errors='coerce')
            if parsed.notna().mean() > 0.8:
                return "date", parsed.notna().mean()
        except:
            pass

    # Category (few unique values relative to row count)
    if sample.nunique() <= 10 and len(sample) > 5:
        return "category", 0.8

    return "string", 0.5


def cascade_match(uploaded_col, uploaded_series, schema_cols):
    """
    Run the 4-step matching cascade against all columns in a schema.
    Returns: (best_match_col, confidence, match_method)
    
    Key improvement: value-based type detection STRONGLY influences scoring.
    A column with date values gets boosted for date targets and penalized for
    string targets. This prevents "Expense Type" from claiming "Date" over "Acct Date".
    """
    best_match = None
    best_score = 0.0
    best_method = None

    u_norm = normalize(uploaded_col)
    u_expanded = expand_abbreviations(uploaded_col)
    u_type, u_type_conf = detect_data_type(uploaded_series)

    for std_col, spec in schema_cols.items():
        s_norm = normalize(std_col)
        s_expanded = expand_abbreviations(std_col)
        s_type = spec['type']

        # Step 1: Exact match
        if u_norm == s_norm:
            return std_col, 1.0, "exact"

        # Step 2: Case-insensitive + whitespace normalized
        if u_norm.replace(' ', '') == s_norm.replace(' ', ''):
            if 0.95 > best_score:
                best_match, best_score, best_method = std_col, 0.95, "normalized"

        # Step 3: Abbreviation expansion
        if u_expanded == s_expanded:
            if 0.90 > best_score:
                best_match, best_score, best_method = std_col, 0.90, "abbreviation"

        # Step 4: Fuzzy string matching
        # Name-to-name match gets full weight, description match gets 0.7 weight
        name_score = max(
            fuzzy_score(uploaded_col, std_col),
            fuzzy_score(u_expanded, s_expanded),
        )
        desc_score = fuzzy_score(uploaded_col, spec['desc']) * 0.7  # downweight description matches
        score = max(name_score, desc_score)

        # Step 6: Type alignment — STRONG signal
        if u_type == s_type:
            score += 0.20  # big boost for matching types
        elif u_type == 'date' and s_type != 'date':
            score -= 0.15  # penalty: date values shouldn't map to non-date columns
        elif u_type != 'date' and s_type == 'date':
            score -= 0.15  # penalty: non-date values shouldn't map to date columns
        elif u_type == 'numeric' and s_type == 'string':
            score -= 0.10  # mild penalty for type mismatch
        elif u_type == 'string' and s_type == 'numeric':
            score -= 0.10

        score = max(score, 0)  # don't go negative
        score = min(score, 1.0)  # cap at 100%

        if score > best_score and score > 0.45:
            best_match, best_score, best_method = std_col, round(score, 2), "fuzzy+type"

    return best_match, best_score, best_method


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: AI SEMANTIC MATCHING (handles "Expense Type"→"Cost_Category")
# ═══════════════════════════════════════════════════════════════════════════════

AI_MAPPING_PROMPT = """You are a data schema matching expert for hog production systems.

Map each uploaded column to the closest standard column. Consider:
- Column name meaning (not just spelling)
- Sample data values
- Domain context (hog farming, packer settlements, accounting)

STANDARD SCHEMA ({schema_name}):
{schema_desc}

UPLOADED COLUMNS with sample values:
{uploaded_desc}

Return ONLY JSON — a dict mapping uploaded column names to standard names (or null):
{{"uploaded_col": "standard_col_or_null", ...}}"""


def ai_semantic_match(df, schema_name, schema_cols):
    """Step 5: Use Claude for columns that Steps 1-4 couldn't match."""
    if not has_ai():
        return {}

    schema_desc = "\n".join(f"  {col}: {spec['desc']} ({spec['type']})" for col, spec in schema_cols.items())
    uploaded_desc = ""
    for col in df.columns:
        sample = df[col].dropna().head(3).tolist()
        uploaded_desc += f"  {col}: sample values = {sample}\n"

    try:
        client = Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system="You are a schema matching expert. Return only valid JSON.",
            messages=[{"role": "user", "content": AI_MAPPING_PROMPT.format(
                schema_name=schema_name, schema_desc=schema_desc, uploaded_desc=uploaded_desc
            )}]
        )
        text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA DETECTION — which data type is this file?
# ═══════════════════════════════════════════════════════════════════════════════

def detect_schema(df):
    """Score each schema by how well the uploaded columns match."""
    scores = {}
    for schema_name, schema in SCHEMAS.items():
        total_score = 0
        for col in df.columns:
            _, score, _ = cascade_match(col, df[col], schema['columns'])
            total_score += score
        avg = total_score / max(len(df.columns), 1)
        scores[schema_name] = round(avg, 3)

    best = max(scores, key=scores.get)
    confidence = "high" if scores[best] > 0.5 else "medium" if scores[best] > 0.3 else "low"
    return best, confidence, scores


# ═══════════════════════════════════════════════════════════════════════════════
# FULL MAPPING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def map_columns(df, schema_name):
    """Run full mapping pipeline: cascade Steps 1-4, then AI Step 5 for unmatched."""
    schema_cols = SCHEMAS[schema_name]['columns']
    mappings = {}
    unmatched = []

    # Steps 1-4: Rule-based cascade
    for col in df.columns:
        match, score, method = cascade_match(col, df[col], schema_cols)
        mappings[col] = {
            'target': match,
            'confidence': score,
            'method': method or 'none',
        }
        if match is None or score < 0.5:
            unmatched.append(col)

    # Step 5: AI for unmatched columns
    if unmatched and has_ai():
        unmatched_df = df[unmatched]
        ai_results = ai_semantic_match(unmatched_df, schema_name, schema_cols)
        for col, target in ai_results.items():
            if target and col in mappings and target in schema_cols:
                if mappings[col]['confidence'] < 0.7:  # only override low-confidence
                    mappings[col] = {
                        'target': target,
                        'confidence': 0.75,
                        'method': 'ai_semantic',
                    }

    # Step 7: Deduplicate — when two columns claim the same target, keep higher confidence
    target_claims = {}
    for col, m in mappings.items():
        t = m['target']
        if t is None:
            continue
        if t not in target_claims or m['confidence'] > target_claims[t]['confidence']:
            if t in target_claims:
                prev_col = target_claims[t]['col']
                mappings[prev_col] = {'target': None, 'confidence': 0, 'method': 'demoted (duplicate)'}
            target_claims[t] = {'col': col, 'confidence': m['confidence']}
        else:
            mappings[col] = {'target': None, 'confidence': 0, 'method': 'demoted (duplicate)'}

    # Step 8: Re-match demoted columns against UNCLAIMED targets
    claimed_targets = set(m['target'] for m in mappings.values() if m['target'])
    unclaimed = {k: v for k, v in schema_cols.items() if k not in claimed_targets}

    if unclaimed:
        for col, m in mappings.items():
            if m['target'] is None and m['method'] == 'demoted (duplicate)':
                match, score, method = cascade_match(col, df[col], unclaimed)
                if match and score > 0.40:
                    mappings[col] = {'target': match, 'confidence': score, 'method': method + ' (rematch)'}
                    claimed_targets.add(match)
                    unclaimed = {k: v for k, v in unclaimed.items() if k not in claimed_targets}

    return mappings


# ═══════════════════════════════════════════════════════════════════════════════
# POST-MAPPING VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_mapped_data(df, schema_name, column_mapping):
    """Validate data after mapping — row-level error detection."""
    schema_cols = SCHEMAS[schema_name]['columns']
    errors = []
    warnings = []

    # Rename columns
    rename = {orig: m['target'] for orig, m in column_mapping.items() if m['target']}
    mapped_df = df.rename(columns=rename)

    # Check required fields
    for col, spec in schema_cols.items():
        if spec.get('required') and col not in mapped_df.columns:
            errors.append(f"Required column '{col}' not mapped")
        elif col in mapped_df.columns:
            nulls = mapped_df[col].isna().sum()
            if nulls > 0 and spec.get('required'):
                warnings.append(f"'{col}' has {nulls} null values ({nulls/len(mapped_df):.0%})")

            # Type validation
            if spec['type'] == 'date' and col in mapped_df.columns:
                parsed = pd.to_datetime(mapped_df[col], format='mixed', errors='coerce')
                failed = parsed.isna().sum() - mapped_df[col].isna().sum()
                if failed > 0:
                    warnings.append(f"'{col}': {failed} values couldn't be parsed as dates")
                mapped_df[col] = parsed

            if spec['type'] == 'numeric' and col in mapped_df.columns:
                numeric = pd.to_numeric(mapped_df[col], errors='coerce')
                if spec.get('min') is not None:
                    below = (numeric < spec['min']).sum()
                    if below > 0:
                        warnings.append(f"'{col}': {below} values below minimum ({spec['min']})")
                if spec.get('max') is not None:
                    above = (numeric > spec['max']).sum()
                    if above > 0:
                        warnings.append(f"'{col}': {above} values above maximum ({spec['max']})")

            if spec['type'] == 'category' and 'values' in spec and col in mapped_df.columns:
                valid = set(spec['values'])
                actual = set(mapped_df[col].dropna().unique())
                invalid = actual - valid
                if invalid:
                    warnings.append(f"'{col}': unexpected values: {invalid}")

    # Keep only mapped columns
    keep = [c for c in mapped_df.columns if c in schema_cols]
    mapped_df = mapped_df[keep]

    return mapped_df, errors, warnings


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ═══════════════════════════════════════════════════════════════════════════════

def confidence_badge(score):
    if score >= 0.9:
        return "🟢 HIGH"
    elif score >= 0.5:
        return "🟡 MEDIUM"
    else:
        return "🔴 LOW"


def render_upload_section(conn):
    """Render the complete data import UI."""
    st.subheader("📤 Import your data")
    st.caption("Upload CSV or Excel from any system. AI maps your columns automatically.")

    uploaded = st.file_uploader("Drop a file here", type=["csv", "xlsx", "xls", "tsv"],
                                 label_visibility="collapsed")
    if not uploaded:
        # Show downloadable templates
        with st.expander("📋 Download templates"):
            for name, schema in SCHEMAS.items():
                cols = list(schema['columns'].keys())
                template = pd.DataFrame(columns=cols)
                csv_data = template.to_csv(index=False)
                st.download_button(f"📄 {name} template", csv_data,
                                   f"{name.lower().replace(' ','_').replace('/','_')}_template.csv",
                                   "text/csv", key=f"tmpl_{name}")
        return None

    # Load file
    try:
        if uploaded.name.endswith('.csv') or uploaded.name.endswith('.tsv'):
            sep = '\t' if uploaded.name.endswith('.tsv') else ','
            df = pd.read_csv(uploaded, sep=sep)
        else:
            df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        return None

    st.success(f"**{uploaded.name}** — {len(df):,} rows × {len(df.columns)} columns")

    with st.expander("Preview raw data", expanded=False):
        st.dataframe(df.head(5), use_container_width=True, hide_index=True)

    # Detect schema
    detected, confidence, scores = detect_schema(df)
    st.info(f"🔍 **Detected:** {detected} ({confidence} confidence)")

    with st.expander("Schema scores"):
        for name, score in sorted(scores.items(), key=lambda x: -x[1]):
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            st.text(f"  {name:25s} {bar} {score:.2f}")

    schema_name = st.selectbox("Confirm data type:", list(SCHEMAS.keys()),
                                index=list(SCHEMAS.keys()).index(detected))

    # Run mapping pipeline
    with st.spinner("Mapping columns (Steps 1-5)..."):
        mappings = map_columns(df, schema_name)

    # Display mappings with confidence
    st.markdown("**Column mapping** — verify and adjust:")
    schema_cols = list(SCHEMAS[schema_name]['columns'].keys())
    options = ["(skip)"] + schema_cols

    edited = {}
    for col in df.columns:
        m = mappings.get(col, {'target': None, 'confidence': 0, 'method': 'none'})
        suggested = m['target']
        score = m['confidence']
        method = m['method']
        badge = confidence_badge(score)

        default_idx = options.index(suggested) if suggested in options else 0
        selected = st.selectbox(
            f"`{col}` → {badge} ({method}, {score:.0%})",
            options, index=default_idx, key=f"map_{col}"
        )
        if selected != "(skip)":
            edited[col] = {'target': selected, 'confidence': score, 'method': method}

    # Check for duplicate mappings
    targets = [m['target'] for m in edited.values()]
    dupes = [t for t in set(targets) if targets.count(t) > 1]
    if dupes:
        st.error(f"⚠️ Duplicate mappings: {dupes}. Each standard column should map to at most one uploaded column.")
        return None

    # Unmapped required columns
    mapped_targets = set(m['target'] for m in edited.values())
    schema_spec = SCHEMAS[schema_name]['columns']
    unmapped_req = [c for c, s in schema_spec.items() if s.get('required') and c not in mapped_targets]
    if unmapped_req:
        st.warning(f"⚠️ Required columns not mapped: {', '.join(unmapped_req)}")

    # Confirm and validate
    if st.button("✅ Validate and import", type="primary", use_container_width=True):
        mapped_df, errors, warnings = validate_mapped_data(df, schema_name, edited)

        # Show validation results
        if errors:
            for e in errors:
                st.error(f"❌ {e}")
            return None

        if warnings:
            st.markdown("**Validation warnings:**")
            for w in warnings:
                st.warning(f"⚠️ {w}")

        # Quality summary
        st.markdown("**Import summary:**")
        col1, col2, col3 = st.columns(3)
        col1.metric("Rows", f"{len(mapped_df):,}")
        col2.metric("Columns mapped", f"{len(mapped_df.columns)}/{len(schema_spec)}")
        col3.metric("Null rate", f"{mapped_df.isna().mean().mean():.0%}")

        st.dataframe(mapped_df.head(5), use_container_width=True, hide_index=True)

        # Save
        table_name = f"imported_{schema_name.lower().replace(' ','_').replace('/','_')}"
        st.session_state[f"imported_{table_name}"] = mapped_df

        try:
            if conn:
                conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM mapped_df")
                st.success(f"💾 Saved to database as `{table_name}` ({len(mapped_df)} rows)")
        except Exception as e:
            st.info(f"Data available in session (DB save skipped: {e})")

        return mapped_df

    return None
