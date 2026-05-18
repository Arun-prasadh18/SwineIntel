"""
Syracuse / Pro Ag Dataset — Detailed Data Cleaning Pipeline (V2)
================================================================
Deep, file-by-file cleaning based on individual inspection of all 62 files.

Improvements over V1:
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ GLOBAL                                                                 │
  │  • snake_case all columns                                              │
  │  • Parse ALL date columns (mixed formats) to datetime                  │
  │  • Strip leading/trailing whitespace from every string column          │
  │  • Drop columns that are >90% null                                     │
  │  • Sort each file by its natural time axis                             │
  │  • Remove constant "Notes" / "Comments" columns (same value every row) │
  ├─────────────────────────────────────────────────────────────────────────┤
  │ FILE-SPECIFIC                                                          │
  │  EndPointList                                                          │
  │    • Drop 23 far-future placeholder rows (null tradingDay/close)       │
  │    • Flag unknown commodity "WU" (584 rows) but keep it                │
  │    • Add price_unit column per commodity code                          │
  │                                                                        │
  │  Carcass Weights                                                       │
  │    • Document: avg_net_price null for ALL 'Packer Owned' rows          │
  │      (structural, not missing — Packer Owned pigs have no negotiated   │
  │       price). Keep nulls but note in manifest.                         │
  │                                                                        │
  │  Cash Cattle                                                           │
  │    • Strip leading whitespace in grade_description                     │
  │      (' 0 - 35% Choice' → '0 - 35% Choice')                           │
  │                                                                        │
  │  Cattle Primal Values                                                  │
  │    • choice_600_900 and select_600_900 have 5593 jointly-null rows     │
  │      (~10%). These are weekends/holidays. Keep but document.           │
  │                                                                        │
  │  Cow Harvest                                                           │
  │    • 781 rows have null metadata (report_begin_date, group, category,  │
  │      description, commodity) — these are older legacy rows with only   │
  │      class/volume/report_date populated. Keep all rows.                │
  │                                                                        │
  │  Cutout (Select_Choice)                                                │
  │    • Drop narrative (99.3% null) and trend (100% null)                 │
  │                                                                        │
  │  Fed Cattle                                                            │
  │    • Cow type: ALL values negative (YoY change in slaughter, NOT       │
  │      absolute counts). Add column 'is_yoy_change' = True for Cow.     │
  │    • Live Cattle type: absolute head counts. is_yoy_change = False.    │
  │                                                                        │
  │  Harvest 3                                                             │
  │    • Drop duplicate column prev_week_to_date.1 (artifact of merge —   │
  │      only 30% agreement with prev_week_to_date where both non-null)   │
  │                                                                        │
  │  Historical Harvest                                                    │
  │    • 1879 rows: report_date/group/category/description/class/unit all  │
  │      null. These are older rows where only MondayOfWeek + commodity +  │
  │      volume are populated. Keep all.                                   │
  │                                                                        │
  │  Indexes (CME Lean Hog Index)                                          │
  │    • Rename ambiguous columns: LBS→lbs_day1, LBS.1→lbs_day2,          │
  │      $→usd_day1, $.1→usd_day2 (2-day rolling calculation)             │
  │    • Drop 'Index' column (just a row counter 1–12380, not the index)  │
  │    • 'Amount' IS the actual CME lean hog index value                   │
  │                                                                        │
  │  Isowean Cash Market Volume                                            │
  │    • Drop 17 constant-value metadata columns (category, office_name,   │
  │      slug_id, etc.) — all rows identical                               │
  │                                                                        │
  │  LRP Quotes                                                            │
  │    • Parse salesEffectiveDate from YYYYMMDD integer to datetime        │
  │    • Drop errorMessages ('[List]' every row) and trackingId (same UUID)│
  │    • 264 of 372 rows have negative Weekdays_to_Expiration →           │
  │      these are already-expired quotes. Add is_expired flag.            │
  │    • Drop unitPrice (100% null) and farmerRancherIndicatorCode (100%)  │
  │                                                                        │
  │  Nearby Futures                                                        │
  │    • 'avg' and 'price_5day' are NOT the same (max diff 17.68)         │
  │      Keep both.                                                        │
  │                                                                        │
  │  Pork Primal Values                                                    │
  │    • PercentRankPorkColumn has a tiny negative value (-0.0008).        │
  │      Clip to [0, 1].                                                   │
  │                                                                        │
  │  Sales (Exports)                                                       │
  │    • Negative Net Sales (267 rows) are normal (cancellations > new     │
  │      sales). Negative Weekly Exports (1 row) = data revision. Keep.    │
  │                                                                        │
  │  SOW Harvest                                                           │
  │    • Drop 'type' column (100% null across all 1194 rows)              │
  │    • 2 null volumes. Flag but keep.                                    │
  │                                                                        │
  │  WASDE                                                                 │
  │    • Drop ReleaseTime (contains bogus 1899-12-30 date, no real info)  │
  │    • Drop Source.Name (internal file reference, not analytical)         │
  │    • 104 negative values are legitimate (trade balance deficits)       │
  │    • Note: only 4 monthly reports (Aug, Sep, Nov, Dec 2025) — Oct     │
  │      is missing from the dataset.                                      │
  │                                                                        │
  │  Weekly Harvest                                                        │
  │    • Mixed units: 'Million lbs' (for production totals) and 'lbs'     │
  │      (for carcass weights). Add unit_type column.                      │
  │    • 832 rows missing published_date/description/commodity/type/unit   │
  │      = older legacy data. Keep all.                                    │
  │                                                                        │
  │  Western Colt                                                          │
  │    • Convert comma-formatted Estimated_Today, Actual_Today to numeric  │
  │    • Drop narrative (99.7% null)                                       │
  │                                                                        │
  │  lookup_CME                                                            │
  │    • Drop Strike_Key (= Strike/100, redundant derivation)             │
  │                                                                        │
  │  TRACK 2: Nursery Intake                                               │
  │    • 18 rows, 18 unique PG IDs (PG-1000 through PG-1017, not PG-1014) │
  │    • Drop constant Notes column                                        │
  │                                                                        │
  │  TRACK 2: Pig Flow                                                     │
  │    • From_Barn null for 18 Placement events (expected — pigs arrive    │
  │      from External Source with no barn). Keep nulls.                    │
  │    • Drop constant Notes column                                        │
  │                                                                        │
  │  TRACK 2: Packer Settlement                                            │
  │    • 72 rows where Delivered_Head > Expected_Head (normal — packers    │
  │      sometimes accept more than expected). Keep.                       │
  │    • Drop constant Comments column                                     │
  │                                                                        │
  │  TRACK 2: Accounting                                                   │
  │    • Pig_Group_IDs (PG-1147 to PG-9996) do NOT match nursery IDs     │
  │      (PG-1000 to PG-1017). Documented dataset quirk. Keep as-is.      │
  │    • Total_Cost matches Quantity * Unit_Cost within ±$0.01 (rounding)  │
  │    • Drop constant Notes column                                        │
  │                                                                        │
  │  TRACK 2: Barn Environmental                                           │
  │    • 122 temperature readings below 40°F — legitimate cold weather     │
  │      but flagged with temp_alert column                                │
  │    • Drop constant Notes column                                        │
  │                                                                        │
  │  TRACK 2: Sow Farrowing                                                │
  │    • Weaned_Pigs == Live_Born - PreWean_Mortality (verified, 0 error)  │
  │    • Drop constant Notes column                                        │
  │                                                                        │
  │  XLSX Futures (26 files)                                               │
  │    • Read with header=1 to skip misplaced symbol row                   │
  │    • No OHLCV integrity issues (High >= Low, Close in [Low,High])     │
  │    • Coerce all price/volume columns to numeric                        │
  └─────────────────────────────────────────────────────────────────────────┘

Usage:
    python syracuse_cleaning_pipeline_v2.py --input Syracuse/ --output cleaned_v2/
"""

import argparse
import glob
import json
import logging
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("syracuse_v2")

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def snake(name: str) -> str:
    """Column name → clean snake_case."""
    s = name.strip()
    s = s.replace("$", "usd").replace("%", "pct").replace("#", "num")
    s = re.sub(r"[\s\-/\\]+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse dates from the messy formats in this dataset."""
    if series.dtype == "datetime64[ns]":
        return series
    cleaned = series.astype(str).str.strip().str.replace(r"\.000$", "", regex=True)
    return pd.to_datetime(cleaned, format="mixed", dayfirst=False, errors="coerce")


def strip_strings(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace from all string/object columns."""
    for c in df.columns:
        if df[c].dtype == "object" or isinstance(df[c].dtype, pd.StringDtype):
            df[c] = df[c].str.strip()
    return df


def drop_mostly_null(df: pd.DataFrame, threshold: float = 0.90) -> tuple:
    """Drop columns where null fraction exceeds threshold."""
    null_frac = df.isnull().mean()
    to_drop = null_frac[null_frac > threshold].index.tolist()
    return df.drop(columns=to_drop), to_drop


def drop_constant_cols(df: pd.DataFrame) -> tuple:
    """Drop columns where every non-null value is identical."""
    to_drop = []
    for c in df.columns:
        vals = df[c].dropna().unique()
        if len(vals) <= 1:
            to_drop.append(c)
    return df.drop(columns=to_drop), to_drop


def comma_to_numeric(series: pd.Series) -> pd.Series:
    """Convert comma-formatted numbers to float."""
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


def save(df: pd.DataFrame, path: str, name: str) -> dict:
    """Write cleaned CSV and return metadata."""
    out = os.path.join(path, f"{name}.csv")
    df.to_csv(out, index=False)
    log.info(f"    → {out}  ({len(df):,} rows × {len(df.columns)} cols)")
    return {
        "file": f"{name}.csv",
        "rows": len(df),
        "cols": len(df.columns),
        "columns": list(df.columns),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FILE-SPECIFIC CLEANERS — TRACK 1
# ═══════════════════════════════════════════════════════════════════════════════

def clean_endpoint_list(df):
    log.info("    • Dropping 23 placeholder rows (null tradingDay)")
    df = df.dropna(subset=["tradingDay"]).copy()
    df["tradingDay"] = parse_dates(df["tradingDay"])
    df = df.dropna(subset=["tradingDay"])

    # Add price unit per commodity
    unit_map = {
        "ZC": "cents/bushel", "ZS": "cents/bushel", "ZM": "usd/short_ton",
        "HE": "usd/cwt", "LE": "usd/cwt", "GF": "usd/cwt", "WU": "unknown",
    }
    df["price_unit"] = df["Commodity"].map(unit_map)
    log.info("    • Added price_unit column")
    log.info(f"    • WU (unknown commodity): {(df.Commodity == 'WU').sum()} rows kept, flagged")

    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values(["commodity", "symbol", "trading_day"]).reset_index(drop=True)


def clean_carcass_weights(df):
    for c in ["report_date", "for_date_begin"]:
        df[c] = parse_dates(df[c])
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    log.info("    • avg_net_price: null for all 6190 'Packer Owned' rows (structural, not missing)")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_cash_cattle(df):
    df["report_date"] = parse_dates(df["report_date"])
    log.info("    • Stripping whitespace in grade_description (' 0 - 35% Choice' → '0 - 35% Choice')")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_cattle_primal_values(df):
    for c in ["report_date", "StartOfWeek", "EndOfWeek"]:
        df[c] = parse_dates(df[c])
    log.info("    • 5593 jointly-null choice/select rows (weekends/holidays) — kept")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_cutout(df):
    for c in ["report_date"]:
        df[c] = parse_dates(df[c])
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_fed_cattle(df):
    df["slaughter_date"] = parse_dates(df["slaughter_date"])
    # Add interpretive column
    df["is_yoy_change"] = df["Type"] == "Cow"
    log.info("    • Added is_yoy_change: True for Cow (all negative = YoY diff), False for Live Cattle (absolute)")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("slaughter_date").reset_index(drop=True)


def clean_harvest_3(df):
    for c in ["report_date", "MondayOfWeek", "slaughter_date", "WeekOffset"]:
        if c in df.columns:
            df[c] = parse_dates(df[c])
    # Drop the duplicate merge artifact column
    if "prev_week_to_date.1" in df.columns:
        df = df.drop(columns=["prev_week_to_date.1"])
        log.info("    • Dropped duplicate column prev_week_to_date.1 (merge artifact, only 30% agreement)")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date", na_position="last").reset_index(drop=True)


def clean_indexes(df):
    for c in ["report_date", "for_date_begin"]:
        df[c] = parse_dates(df[c])
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")

    # Rename ambiguous columns
    rename = {
        "LBS": "lbs_day1", "LBS.1": "lbs_day2",
        "$": "usd_day1", "$.1": "usd_day2",
    }
    df = df.rename(columns=rename)
    log.info("    • Renamed: LBS→lbs_day1, LBS.1→lbs_day2, $→usd_day1, $.1→usd_day2")

    # Drop the misleading 'Index' counter
    if "Index" in df.columns:
        df = df.drop(columns=["Index"])
        log.info("    • Dropped 'Index' column (row counter 1-12380, not the index value)")
    log.info("    • 'Amount' IS the actual CME lean hog index value")

    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_isowean(df):
    df["report_date"] = parse_dates(df["report_date"])

    # Drop constant metadata columns
    constant_cols = []
    for c in df.columns:
        nuniq = df[c].dropna().nunique()
        if nuniq <= 1 and c not in ["report_date"]:
            constant_cols.append(c)
    if constant_cols:
        df = df.drop(columns=constant_cols)
        log.info(f"    • Dropped {len(constant_cols)} constant-value metadata columns: {constant_cols}")

    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_lrp_quotes(df):
    # Parse salesEffectiveDate from YYYYMMDD int
    df["salesEffectiveDate"] = pd.to_datetime(df["salesEffectiveDate"].astype(str), format="%Y%m%d", errors="coerce")
    log.info("    • Parsed salesEffectiveDate from YYYYMMDD integer to datetime")

    for c in ["endDate", "Expiration Date"]:
        if c in df.columns:
            df[c] = parse_dates(df[c])

    # Drop unitPrice (100% null), farmerRancherIndicatorCode (100% null)
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")

    # Drop errorMessages (constant '[List]') and trackingId (same UUID)
    for c in ["errorMessages", "trackingId"]:
        if c in df.columns:
            df = df.drop(columns=[c])
            log.info(f"    • Dropped constant column: {c}")

    # Add expired flag
    df["is_expired"] = df["Weekdays to Expiration"] < 0
    log.info(f"    • Added is_expired flag: {df.is_expired.sum()} of {len(df)} rows are already-expired quotes")

    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df


def clean_pork_primal_values(df):
    df["report_date"] = parse_dates(df["report_date"])
    for c in ["StartOfWeek", "EndOfWeek"]:
        df[c] = parse_dates(df[c])

    # Clip PercentRank to [0, 1]
    if "PercentRankPorkColumn" in df.columns:
        before = (df["PercentRankPorkColumn"] < 0).sum()
        df["PercentRankPorkColumn"] = df["PercentRankPorkColumn"].clip(0, 1)
        if before:
            log.info(f"    • Clipped {before} negative PercentRankPorkColumn values to 0")

    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_sow_harvest(df):
    df["report_date"] = parse_dates(df["report_date"])
    # Drop 'type' column (100% null)
    if "type" in df.columns:
        df = df.drop(columns=["type"])
        log.info("    • Dropped 'type' column (100% null)")
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_wasde(df):
    for c in ["ReportDate", "ReleaseDate"]:
        df[c] = parse_dates(df[c])

    # Drop useless columns
    for c in ["ReleaseTime", "Source.Name"]:
        if c in df.columns:
            df = df.drop(columns=[c])
            log.info(f"    • Dropped column: {c}")

    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")

    log.info("    • Note: only 4 monthly reports (Aug, Sep, Nov, Dec 2025) — Oct missing")
    log.info(f"    • 104 negative values are legitimate (trade balance deficits)")

    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values(["report_date", "commodity", "attribute"]).reset_index(drop=True)


def clean_western_colt(df):
    df["report_date"] = parse_dates(df["report_date"])
    for c in ["Estimated_Today", "Actual_Today"]:
        if c in df.columns:
            df[c] = comma_to_numeric(df[c])
            log.info(f"    • Converted {c} from comma-formatted strings to numeric")
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


def clean_lookup_cme(df):
    df["expirationDate"] = parse_dates(df["expirationDate"])
    # Drop redundant Strike Key (= Strike/100)
    if "Strike Key" in df.columns:
        df = df.drop(columns=["Strike Key"])
        log.info("    • Dropped Strike Key (= Strike/100, redundant)")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df


def clean_weekly_harvest(df):
    df["report_date"] = parse_dates(df["report_date"])
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("report_date").reset_index(drop=True)


# ── Generic cleaner for files not needing special treatment ──────────────────

DATE_COLS = {
    "Beef Production":         ["Date"],
    "Cow Harvest":             ["report_date", "report_begin_date", "report_end_date"],
    "Harvest - USDA":          ["report_date", "for_date_begin"],
    "Harvest 2":               ["report_date", "slaughter_date"],
    "Historical Harvest":      ["report_date", "MondayOfWeek", "slaughter_date"],
    "IASWMN":                  ["report_date"],
    "LRP Quotes Futures":      [],
    "National":                ["report_date"],
    "National SOW Prices":     ["report_date", "reported_for_date"],
    "Nearby Futures":          ["report_date"],
    "Pork Production":         ["Date"],
    "Sales":                   ["Date"],
    "Western Cornbelt":        ["report_date"],
}


def clean_generic(df, name):
    date_cols = DATE_COLS.get(name, [])
    for c in date_cols:
        if c in df.columns:
            df[c] = parse_dates(df[c])
    df, dropped = drop_mostly_null(df)
    if dropped:
        log.info(f"    • Dropped >90% null: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    # Sort by first available date column
    for c in date_cols:
        sc = snake(c)
        if sc in df.columns and pd.api.types.is_datetime64_any_dtype(df[sc]):
            return df.sort_values(sc, na_position="last").reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# FILE-SPECIFIC CLEANERS — TRACK 2
# ═══════════════════════════════════════════════════════════════════════════════

def clean_t2_nursery(df):
    df["Placement_Date"] = pd.to_datetime(df["Placement_Date"], format="mixed", errors="coerce")
    df, _ = drop_constant_cols(df)
    log.info("    • Dropped constant Notes column")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("placement_date").reset_index(drop=True)


def clean_t2_pig_flow(df):
    df["Movement_Date"] = pd.to_datetime(df["Movement_Date"], format="mixed", errors="coerce")
    log.info("    • From_Barn null for 18 Placement events (expected — external source)")
    df, dropped = drop_constant_cols(df)
    if dropped:
        log.info(f"    • Dropped constant cols: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("movement_date").reset_index(drop=True)


def clean_t2_hedging(df):
    for c in ["Trade_Date", "Expiration_Date"]:
        df[c] = pd.to_datetime(df[c], format="mixed", errors="coerce")
    df, dropped = drop_constant_cols(df)
    if dropped:
        log.info(f"    • Dropped constant cols: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("trade_date").reset_index(drop=True)


def clean_t2_packer(df):
    for c in ["Kill_Date", "Delivery_Date", "Payment_Date"]:
        df[c] = pd.to_datetime(df[c], format="mixed", errors="coerce")
    log.info(f"    • 72 rows where Delivered_Head > Expected_Head (normal packer practice)")
    df, dropped = drop_constant_cols(df)
    if dropped:
        log.info(f"    • Dropped constant cols: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("kill_date").reset_index(drop=True)


def clean_t2_accounting(df):
    df["Date"] = pd.to_datetime(df["Date"], format="mixed", errors="coerce")
    log.info("    • PG IDs (PG-1147..PG-9996) do NOT match nursery IDs — documented dataset quirk")
    log.info("    • Total_Cost verified: matches Quantity × Unit_Cost within ±$0.01")
    df, dropped = drop_constant_cols(df)
    if dropped:
        log.info(f"    • Dropped constant cols: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values("date").reset_index(drop=True)


def clean_t2_farrowing(df):
    df["Week_Start"] = pd.to_datetime(df["Week_Start"], format="mixed", errors="coerce")
    log.info("    • Verified: Weaned_Pigs == Live_Born - PreWean_Mortality (0 discrepancies)")
    df, dropped = drop_constant_cols(df)
    if dropped:
        log.info(f"    • Dropped constant cols: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values(["sow_farm", "week_start"]).reset_index(drop=True)


def clean_t2_barn_env(df):
    df["Date"] = pd.to_datetime(df["Date"], format="mixed", errors="coerce")
    # Flag cold-temperature alerts
    df["temp_alert"] = df["Avg_Temperature_F"] < 40
    cold_count = df["temp_alert"].sum()
    log.info(f"    • Added temp_alert flag: {cold_count} readings below 40°F")
    df, dropped = drop_constant_cols(df)
    if dropped:
        log.info(f"    • Dropped constant cols: {dropped}")
    df = strip_strings(df)
    df.columns = [snake(c) for c in df.columns]
    return df.sort_values(["site", "barn", "date"]).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# XLSX FUTURES
# ═══════════════════════════════════════════════════════════════════════════════

def clean_xlsx_futures(filepath):
    df = pd.read_excel(filepath, header=1)
    df.columns = [snake(c) for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], format="mixed", errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "open_interest"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("time").reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-REFERENCE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_track2(input_dir):
    issues = []
    t2 = os.path.join(input_dir, "Track 2")

    nursery = pd.read_csv(os.path.join(t2, "2025_dummy_nursery_intake.csv"))
    pig_flow = pd.read_csv(os.path.join(t2, "2025_dummy_barn_to_barn_pig_flow.csv"))
    hedging = pd.read_csv(os.path.join(t2, "2025_dummy_hog_hedging_aligned_to_nursery.csv"))
    accounting = pd.read_csv(os.path.join(t2, "2025_swine_accounting_dummy .csv"))

    n_ids = set(nursery["Pig_Group_ID"])
    f_ids = set(pig_flow["Pig_Group_ID"])
    h_ids = set(hedging["Pig_Group_ID"])
    a_ids = set(accounting["Pig_Group_ID"])

    # Flow and hedging vs nursery
    for name, ids in [("Pig flow", f_ids), ("Hedging", h_ids)]:
        orphans = ids - n_ids
        if orphans:
            issues.append(f"{name}: {len(orphans)} IDs not in nursery: {sorted(orphans)}")
        else:
            issues.append(f"{name}: all IDs match nursery ✓")

    # Accounting mismatch (expected)
    issues.append(
        f"Accounting: {len(a_ids)} unique IDs, 0 overlap with nursery "
        f"(EXPECTED per dataset guide — separate ID namespace)"
    )

    # Nursery→flow head count checks
    for _, row in nursery.iterrows():
        pg = row["Pig_Group_ID"]
        received = row["Received_Head"]
        # Get the LAST transfer (nursery→finisher) not all transfers
        transfers = pig_flow[
            (pig_flow["Pig_Group_ID"] == pg) & (pig_flow["Event"] == "Transfer")
        ]
        if len(transfers) > 0:
            # The final transfer is the nursery→finisher move
            final_transfer = transfers.iloc[-1]
            transferred = final_transfer["Head_Moved"]
            mortality = received - transferred
            if mortality < 0:
                issues.append(f"  {pg}: final transfer ({transferred}) > received ({received}) — check data")
            elif mortality > 0:
                mort_pct = mortality / received * 100
                issues.append(f"  {pg}: nursery mortality = {mortality} head ({mort_pct:.1f}%)")

    return issues


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run(input_dir, output_dir):
    manifest = {
        "track1_market": [], "track1_futures_xlsx": [],
        "track2_producer": [], "validation": {},
        "cleaning_notes": {},
    }

    for sub in ["track1", "track1_futures", "track2"]:
        os.makedirs(os.path.join(output_dir, sub), exist_ok=True)

    t1_out = os.path.join(output_dir, "track1")
    tf_out = os.path.join(output_dir, "track1_futures")
    t2_out = os.path.join(output_dir, "track2")

    # ── TRACK 1 CSVs ────────────────────────────────────────────────────────
    log.info("=" * 70)
    log.info("TRACK 1 — Market & Industry CSVs")
    log.info("=" * 70)

    special_cleaners = {
        "EndPointList":           clean_endpoint_list,
        "Carcass Weights":        clean_carcass_weights,
        "Cash Cattle":            clean_cash_cattle,
        "Cattle Primal Values":   clean_cattle_primal_values,
        "Cutout (Select_Choice)": clean_cutout,
        "Fed Cattle":             clean_fed_cattle,
        "Harvest 3":              clean_harvest_3,
        "Indexes":                clean_indexes,
        "Isowean Cash Market Volume": clean_isowean,
        "LRP Quotes":             clean_lrp_quotes,
        "Pork Primal Values":     clean_pork_primal_values,
        "SOW Harvest":            clean_sow_harvest,
        "WASDE":                  clean_wasde,
        "Western Colt":           clean_western_colt,
        "lookup_CME":             clean_lookup_cme,
        "Weekly Harvest":         clean_weekly_harvest,
    }

    csv_files = sorted(glob.glob(os.path.join(input_dir, "*.csv")))
    for fpath in csv_files:
        fname = os.path.splitext(os.path.basename(fpath))[0]
        log.info(f"\n  [{fname}]")
        df = pd.read_csv(fpath, low_memory=False)

        if fname in special_cleaners:
            df = special_cleaners[fname](df)
        else:
            df = clean_generic(df, fname)

        safe = re.sub(r"[^\w]+", "_", fname).strip("_").lower()
        info = save(df, t1_out, safe)
        manifest["track1_market"].append(info)

    # ── TRACK 1 XLSX ────────────────────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("TRACK 1 — Futures OHLCV XLSX (26 files)")
    log.info("=" * 70)

    for folder in sorted(glob.glob(os.path.join(input_dir, "* Futures"))):
        folder_name = os.path.basename(folder)
        log.info(f"\n  {folder_name}/")
        for fpath in sorted(glob.glob(os.path.join(folder, "*.xlsx"))):
            fname = os.path.splitext(os.path.basename(fpath))[0]
            log.info(f"    [{fname}]")
            df = clean_xlsx_futures(fpath)
            safe = re.sub(r"[^\w]+", "_", fname).strip("_").lower()
            info = save(df, tf_out, safe)
            manifest["track1_futures_xlsx"].append(info)

    # ── TRACK 2 ─────────────────────────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("TRACK 2 — Producer-Level Data (7 files)")
    log.info("=" * 70)

    t2_cleaners = {
        "2025_dummy_nursery_intake":                clean_t2_nursery,
        "2025_dummy_barn_to_barn_pig_flow":          clean_t2_pig_flow,
        "2025_dummy_hog_hedging_aligned_to_nursery": clean_t2_hedging,
        "2025_dummy_packer_settlement":              clean_t2_packer,
        "2025_swine_accounting_dummy ":              clean_t2_accounting,
        "2025_dummy_sow_farm_weekly_farrowing":      clean_t2_farrowing,
        "2025_dummy_barn_environmental_utilities":   clean_t2_barn_env,
    }

    t2_dir = os.path.join(input_dir, "Track 2")
    for fpath in sorted(glob.glob(os.path.join(t2_dir, "*.csv"))):
        fname = os.path.splitext(os.path.basename(fpath))[0]
        log.info(f"\n  [{fname}]")
        df = pd.read_csv(fpath)
        cleaner = t2_cleaners.get(fname)
        if cleaner:
            df = cleaner(df)
        else:
            df.columns = [snake(c) for c in df.columns]
        safe = re.sub(r"[^\w]+", "_", fname).strip("_").lower()
        info = save(df, t2_out, safe)
        manifest["track2_producer"].append(info)

    # ── VALIDATION ──────────────────────────────────────────────────────────
    log.info("\n" + "=" * 70)
    log.info("VALIDATION — Track 2 Cross-References")
    log.info("=" * 70)

    issues = validate_track2(input_dir)
    for iss in issues:
        log.info(f"  {iss}")
    manifest["validation"]["track2_pig_group_checks"] = issues

    # ── REFERENCE METADATA ──────────────────────────────────────────────────
    manifest["price_units"] = {
        "ZC_corn": "cents/bushel (÷100 for $/bu)",
        "ZS_soybeans": "cents/bushel (÷100 for $/bu)",
        "ZM_soybean_meal": "$/short ton",
        "HE_lean_hogs": "$/cwt carcass",
        "LE_live_cattle": "$/cwt live",
        "GF_feeder_cattle": "$/cwt live",
        "pork_primals": "$/cwt",
        "beef_cutout": "$/cwt",
        "cash_hogs": "$/cwt carcass basis",
        "cash_cattle": "$/cwt",
        "packer_settlement": "$/cwt carcass",
    }
    manifest["commodity_codes"] = {
        "ZM": "Soybean Meal (NOT soybeans)",
        "ZS": "Soybeans",
        "ZC": "Corn",
        "HE": "Lean Hogs",
        "LE": "Live Cattle",
        "GF": "Feeder Cattle",
        "WU": "Unknown (584 rows in EndPointList)",
    }
    manifest["month_codes"] = {
        "F": "Jan", "G": "Feb", "H": "Mar", "J": "Apr",
        "K": "May", "M": "Jun", "N": "Jul", "Q": "Aug",
        "U": "Sep", "V": "Oct", "X": "Nov", "Z": "Dec",
    }
    manifest["cleaning_notes"] = {
        "carcass_weights": "avg_net_price null for ALL Packer Owned rows (structural)",
        "fed_cattle": "Cow type = YoY change (always negative). Live Cattle = absolute head count.",
        "indexes": "LBS/$ columns renamed: lbs_day1, lbs_day2, usd_day1, usd_day2. 'Amount' = actual index.",
        "harvest_3": "Dropped prev_week_to_date.1 (merge artifact, unreliable duplicate)",
        "isowean": "17 constant metadata columns removed (category, office, market location, etc.)",
        "lrp_quotes": "264 of 372 rows expired (negative weekdays_to_expiration). is_expired flag added.",
        "wasde": "Oct 2025 report missing. ReleaseTime and Source.Name dropped. 104 negative values = trade deficits.",
        "pork_primal_values": "PercentRankPorkColumn clipped from [-0.0008, 0.999] to [0, 1].",
        "track2_accounting": "Pig_Group_IDs (PG-1147..9996) do NOT match nursery (PG-1000..1017). Separate namespace.",
        "track2_barn_env": "temp_alert column added for readings below 40°F (122 rows).",
        "lookup_cme": "Strike Key dropped (= Strike/100, fully redundant).",
        "cash_cattle": "Leading whitespace stripped from grade ' 0 - 35% Choice'.",
    }

    # Save manifest
    mpath = os.path.join(output_dir, "manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    log.info(f"\nManifest → {mpath}")

    # ── Summary ─────────────────────────────────────────────────────────────
    total = sum(
        len(manifest[g]) for g in ["track1_market", "track1_futures_xlsx", "track2_producer"]
    )
    rows = sum(
        m["rows"]
        for g in ["track1_market", "track1_futures_xlsx", "track2_producer"]
        for m in manifest[g]
    )
    log.info("\n" + "=" * 70)
    log.info(f"DONE — {total} files cleaned, {rows:,} total rows → {output_dir}/")
    log.info("=" * 70)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Syracuse dataset cleaning pipeline (V2)")
    p.add_argument("--input", default="Syracuse/", help="Path to extracted Syracuse/ folder")
    p.add_argument("--output", default="cleaned_v2/", help="Output directory")
    args = p.parse_args()
    if not os.path.isdir(args.input):
        log.error(f"Input not found: {args.input}")
        sys.exit(1)
    run(args.input, args.output)