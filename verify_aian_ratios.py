"""
AIAN Census of Agriculture Ratio Verification Script
====================================================
Validates output/aian_combined_ratios.csv using:

1. CV-based 95% confidence intervals (USDA NASS methodology)
   Uses coefficient-of-variation propagation to compute per-row CIs.

2. Anomaly detection
   - Impossible ratio bounds (<0, >1)
   - Zero denominators
   - High-CV unreliable estimates (CV>50%)
   - IQR-based distributional outliers per variable group

3. Census ACS demographic cross-check
   Compares AIAN producer ratios against county-level AIAN population
   share from the American Community Survey 5-year estimates.

Output:
    output/aian_ratios_verified.csv  -- original data + verification columns
    Console summary report

Usage:
    python verify_aian_ratios.py
"""

import re
import os
import sys
import time
import logging
from datetime import datetime
from typing import Optional, List

import numpy as np
import pandas as pd
import requests

# --------------------------------------------------------------------------- #
#  Configurable constants                                                    #
# --------------------------------------------------------------------------- #

DEMOGRAPHIC_MISMATCH_FACTOR = 5.0       # ratio > pop_share * N => flagged
HIGH_CV_THRESHOLD = 50                  # CV% above which estimate is unreliable
OUTLIER_IQR_MULTIPLIER = 3.0            # IQR multiplier for outlier detection
CI_Z_SCORE = 1.96                       # z-score for 95 % confidence level

INPUT_DIR = "output"
RATIOS_FILE = os.path.join(INPUT_DIR, "aian_combined_ratios.csv")
AIAN_FILE = os.path.join(INPUT_DIR, "aian_county_data.csv")
TOTALS_FILE = os.path.join(INPUT_DIR, "total_county_data.csv")
OUTPUT_FILE = os.path.join(INPUT_DIR, "aian_ratios_verified.csv")

# Map Census of Ag years => nearest ACS 5-year ending vintage.
ACS_VINTAGE_MAP = {
    2012: "2014",   # 2010-2014 window covers 2012 best available
    2017: "2017",   # 2013-2017 window exact match
    2022: "2022",   # 2018-2022 window exact match
}

# --------------------------------------------------------------------------- #
#  Logging                                                                   #
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("verify_aian.log", mode="w"),
    ],
)
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Step 1 : Load datasets & back-join CV information                         #
# --------------------------------------------------------------------------- #


def _load_csv(path: str, name: str) -> pd.DataFrame:
    """Load a CSV with existence check."""
    if not os.path.exists(path):
        log.error("Missing input file: %s", path)
        sys.exit(1)
    df = pd.read_csv(path)
    log.info("Loaded %-6s -> %d rows, %d cols", name, len(df), len(df.columns))
    return df


def join_cv_data(df_ratios: pd.DataFrame) -> pd.DataFrame:
    """
    Add numerator CV (from AIAN county CSV) and denominator CV (from totals
    CSV) via keyed merges.  The ratios DataFrame already carries a copy of
    the numerator cv_pct from build_ratio_dataset(), but we re-merge
    explicitly for clarity and to also pull in the denominator CVs.
    """

    # -- numerator CV --------------------------------------------------------
    df_aian = _load_csv(AIAN_FILE, "AIAN")
    merge_cols = ["state", "county", "year", "variable_desc"]
    cv_num = (df_aian[merge_cols + ["cv_pct"]]
              .rename(columns={"cv_pct": "cv_aian"})
              .drop_duplicates(subset=merge_cols))
    df_ratios = df_ratios.merge(cv_num, on=merge_cols, how="left")

    # -- denominator CV -----------------------------------------------------
    df_totals = _load_csv(TOTALS_FILE, "Totals")
    merge_cols_t = ["state", "county", "year", "variable_desc"]
    cv_den = (df_totals[merge_cols_t + ["cv_pct"]]
              .rename(columns={"cv_pct": "cv_total", "variable_desc": "total_variable"})
              .drop_duplicates(subset=["state", "county", "year", "total_variable"]))
    df_ratios = df_ratios.merge(cv_den, on=["state", "county", "year", "total_variable"],
                               how="left")

    missing_num  = int(df_ratios["cv_aian"].isna().sum())
    missing_den  = int(df_ratios["cv_total"].isna().sum())
    log.info("CV join -> num missing: %d, den missing: %d", missing_num, missing_den)

    return df_ratios

# --------------------------------------------------------------------------- #
#  Step 2 : Confidence-interval calculation                                  #
# --------------------------------------------------------------------------- #


def compute_cis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute approximated 95% CIs for each ratio via CV propagation-of-error.

    Method (USDA NASS guidelines, Kish 1965):
        CV_ratio = sqrt(CV_num^2 + CV_den^2)              [assume rho ~ 0]
        SE_ratio = |ratio| * CV_ratio
        CI_95    = ratio +/- z_{.975} * SE_ratio

    Reference: USDA NASS Survey Guidelines (approximate for ratio statistics).
    """

    cv_n = df["cv_aian"].fillna(0) / 100.0
    cv_d = df["cv_total"].fillna(0) / 100.0

    cv_ratio_prop = np.sqrt(cv_n ** 2 + cv_d ** 2)

    df["cv_ratio_pct"] = np.round(cv_ratio_prop * 100, 2)

    ratio_vals = df["aian_ratio"].values.astype(float)
    margin  = CI_Z_SCORE * abs(ratio_vals) * cv_ratio_prop
    lower   = ratio_vals - margin
    upper   = ratio_vals + margin

    df["ci_lower_95"] = np.round(np.clip(lower, 0, None), 4)
    df["ci_upper_95"] = np.round(np.clip(upper, None, 1), 4)

    cv_stats = df["cv_ratio_pct"].describe()
    log.info("CI computed for %d rows | CV ratio median=%.1f%%  max=%.1f%%",
             len(df), float(cv_stats.iloc[4]), float(cv_stats.iloc[7]))

    return df

# --------------------------------------------------------------------------- #
#  Step 3 : Anomaly detection                                               #
# --------------------------------------------------------------------------- #


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Flag impossible values, zero denominators, high-CV rows, IQR outliers."""

    # 1 -- ratio outside [0, 1]
    df["flag_impossible_ratio"] = (df["aian_ratio"] < 0) | (df["aian_ratio"] > 1)

    # 2 -- zero denominator
    df["flag_zero_denominator"] = df["total_value"].fillna(0) == 0

    # 3 -- high CV on either estimator
    cv_hi_num  = df["cv_aian"].fillna(999) > HIGH_CV_THRESHOLD
    cv_hi_den  = df["cv_total"].fillna(999) > HIGH_CV_THRESHOLD
    df["flag_hcv"] = cv_hi_num | cv_hi_den

    # 4 -- IQR outlier per variable_desc group
    outliers = _compute_iqr_outliers(df)
    df["flag_outlier"] = outliers

    # Summary counts
    for flag_col in ["flag_impossible_ratio", "flag_zero_denominator",
                     "flag_hcv", "flag_outlier"]:
        flagged = int(df[flag_col].sum())
        pct = 100.0 * flagged / len(df)
        log.info("Flag [%-24s]: %5d / %d  (%.1f%%)", flag_col, flagged, len(df), pct)

    return df


def _compute_iqr_outliers(df: pd.DataFrame) -> pd.Series:
    """Return boolean Series marking IQR outliers per variable_desc group."""
    results = []

    for _, grp in df.groupby("variable_desc"):
        q1 = grp["aian_ratio"].quantile(0.25)
        q3 = grp["aian_ratio"].quantile(0.75)
        iqr = q3 - q1
        upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
        lower = q1 - OUTLIER_IQR_MULTIPLIER * iqr
        mask = (grp["aian_ratio"] > upper) | (grp["aian_ratio"] < lower)
        results.append(grp.index[mask])

    out_series = pd.Series(False, index=df.index)
    if results:
        concat_idx = np.concatenate(results)
        out_series.loc[concat_idx] = True

    return out_series

# --------------------------------------------------------------------------- #
#  Step 4 : Census ACS demographic cross-check                                #
# --------------------------------------------------------------------------- #


def fetch_acs_demographics(df_ratios: pd.DataFrame) -> pd.DataFrame:
    """
    Query Census Bureau ACS 5-year profiles for AIAN population share per county.

    Uses table B02001 (Race) at base acs5 level:
        B02001_001E = Total population          (from race summary table)
        B02001_004E = American Indian & AN Alone

    Note -- Census ACS does NOT publish "alone + in-combination" as a single
    variable.  We use AIAN alone; this provides a conservative floor for the
    AIAN population share rather than an exact match to NASS methodology.

    Returns DataFrame with columns [year, fips, aian_pop_share].
    """

    if "fips" not in df_ratios.columns:
        log.warning("No FIPS column in ratios data; skipping ACS cross-check")
        return pd.DataFrame(columns=["year", "fips", "aian_pop_share"])

    year_fips = df_ratios[["year", "fips"]].drop_duplicates()
    if year_fips.empty:
        log.warning("No FIPS data for ACS cross-check")
        return pd.DataFrame(columns=["year", "fips", "aian_pop_share"])

    all_rows: List[dict] = []

    for vintage_year, grp in year_fips.groupby("year"):
        vy = int(vintage_year)
        if vy not in ACS_VINTAGE_MAP:
            continue
        vintage = ACS_VINTAGE_MAP[vy]
        fips_list = sorted(grp["fips"].astype(str).unique())

        log.info("ACS -> vintage %s  |  %d counties  (Census of Ag year %d)",
                 vintage, len(fips_list), vy)

        for start in range(0, len(fips_list), 200):
            chunk = fips_list[start:start + 200]
            time.sleep(0.5)

            result = _query_acs_batch(vintage, chunk)
            if result is not None and not result.empty:
                for _, row in result.iterrows():
                    all_rows.append({
                        "year": vy,
                        "fips": row["fips"],
                        "aian_pop_share": row["aian_pop_share"],
                    })

    if not all_rows:
        log.warning("ACS cross-check unavailable (no demographic data retrieved)")
        return pd.DataFrame(columns=["year", "fips", "aian_pop_share"])

    acs_df = pd.DataFrame(all_rows)
    log.info("ACS demographics gathered for %d county-year pairs", len(acs_df))
    return acs_df


def _query_acs_batch(vintage: str, fips_codes: List[str]) -> Optional[pd.DataFrame]:
    """Fetch total pop and AIAN pop for a batch of FIPS codes from ACS 5-year."""

    county_selector = ",".join(fips_codes)
    api_vars = "B02001_001E,B02001_004E,NAME"

    url = (
        f"https://api.census.gov/data/{vintage}/acs/acs5?get={api_vars}"
        f"&for=county:{county_selector}"
    )

    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            log.warning("ACS HTTP %d -- trying alternate endpoint", resp.status_code)
            return _query_acs_alt(vintage, fips_codes)

        raw = resp.json()
    except Exception as exc:
        log.warning("ACS request failed: %s", exc)
        return None

    if not raw or len(raw) < 2:
        return None

    header = raw[0]
    col_map = {v.lower(): i for i, v in enumerate(header)}

    name_idx   = col_map.get("name")
    total_idx  = col_map.get("b02001_001e")
    aian_idx   = col_map.get("b02001_004e")

    if any(idx is None for idx in [total_idx, aian_idx]):
        log.warning("Unexpected ACS response columns: %s", header)
        return _query_acs_alt(vintage, fips_codes)

    rows = []
    for rec in raw[1:]:
        try:
            fips_id = _extract_fips(rec[name_idx]) if name_idx is not None else None
            pop_total  = int(rec[total_idx])  if rec[total_idx] != "." else 0
            pop_aian   = int(rec[aian_idx])   if rec[aian_idx]  != "." else 0
        except (ValueError, IndexError):
            continue

        share = (pop_aian / pop_total) if pop_total > 0 else np.nan
        rows.append({
            "fips": fips_id,
            "aian_pop_share": share,
        })

    return pd.DataFrame(rows)


def _query_acs_alt(vintage: str, fips_codes: List[str]) -> Optional[pd.DataFrame]:
    """Fallback query using ACS subject tables if base endpoint fails."""
    county_selector = ",".join(fips_codes[:50])
    url = (
        f"https://api.census.gov/data/{vintage}/acs/acs5?get=B02001_001E,B02001_004E,NAME"
        f"&for=county:{county_selector}"
    )
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200 or not resp.json():
            return None
    except Exception:
        return None

    header = resp.json()[0]
    col_map = {v.lower(): i for i, v in enumerate(header)}

    tid  = col_map.get("b02001_001e")
    aid  = col_map.get("b02001_004e")

    if not tid or not aid:
        log.warning("Alt ACS query also missing usable columns")
        return None

    rows = []
    for rec in resp.json()[1:]:
        try:
            pop_total = int(rec[tid]) if rec[tid] != "." else 0
            pop_aian  = int(rec[aid]) if rec[aid] != "." else 0
        except (ValueError, IndexError):
            continue

        share = (pop_aian / pop_total) if pop_total > 0 else np.nan
        rows.append({"fips": None, "aian_pop_share": share})

    return pd.DataFrame(rows)


def _extract_fips(name_str: str) -> Optional[str]:
    """Pull a 5-digit FIPS code out of Census name field 'County Name, State (FIPS)'."""
    m = re.search(r"\((\d{5})\)$", name_str)
    return m.group(1) if m else None

# --------------------------------------------------------------------------- #
#  Step 5 : Merge ACS share & flag demographic mismatches                     #
# --------------------------------------------------------------------------- #


def merge_demographics(df_ratios: pd.DataFrame, acs_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join AIAN population share and flag gross demographic outliers."""

    df_ratios = df_ratios.merge(acs_df, on=["year", "fips"], how="left")

    df_ratios["flag_demographic_mismatch"] = (
        df_ratios["aian_ratio"].fillna(0)
        > df_ratios["aian_pop_share"].fillna(1) * DEMOGRAPHIC_MISMATCH_FACTOR
    )

    mismatched = int(df_ratios["flag_demographic_mismatch"].sum())
    log.info("Demographic mismatch flagged: %d rows", mismatched)
    return df_ratios

# --------------------------------------------------------------------------- #
#  Step 6 : Console summary report                                           #
# --------------------------------------------------------------------------- #


def print_report(df: pd.DataFrame):
    """Print structured console summary by year and variable group."""

    sep = "=" * 78
    section = "-" * 78

    print()
    print(sep)
    print("AIAN Census of Agriculture -- Ratio Verification Report")
    print(f"Generated: {datetime.now():%Y-%m-%d %H:%M}")
    print(sep)

    flag_cols = [c for c in df.columns if c.startswith("flag_")]
    total_flagged = int(df[flag_cols].any(axis=1).sum())

    print(f"\nTotal ratio records : {len(df):>8,}")
    print(f"Any flagged rows   : {total_flagged:>8,}  ({100.0 * total_flagged / len(df):.1f}%)")
    print(f"Years represented  : {sorted(df['year'].unique())}")

    for yr in sorted(df["year"].unique()):
        yr_data = df[df["year"] == yr]

        print(f"\n{section}")
        print(f"  Year {yr}")
        print(section)
        print(f"  Records          : {len(yr_data):>8,}")
        print(f"  Unique counties  : {yr_data['county'].nunique():>8,}")
        print(f"  Variables        : {yr_data['variable_desc'].nunique():>8d}")

        for fc in flag_cols:
            cnt = int(yr_data[fc].sum())
            if cnt > 0:
                print(f"  %-26s : {cnt:>5,} ({100.0 * cnt / len(yr_data):.1f}%)", fc)

        for var in sorted(yr_data["variable_desc"].unique()):
            vr = yr_data[yr_data["variable_desc"] == var]
            ratio_vals = vr["aian_ratio"].dropna()
            if ratio_vals.empty:
                continue

            has_outlier_col = "flag_outlier" in yr_data.columns
            n_rows = int(vr["flag_outlier"].sum()) if has_outlier_col else 0

            print(f"\n  {var}")
            s = ratio_vals.describe()
        # fmt: off
            n_count = int(s.iloc[0])
            mn      = float(s.iloc[1])
            sd      = float(s.iloc[2]) if not pd.isna(s.iloc[2]) else 0.0
            q_p25   = float(s.iloc[3])
            median  = float(s.iloc[4])
            q_p75   = float(s.iloc[5])
            mx      = float(s.iloc[6])
        # fmt: on
            print(f"    Count : {n_count}  |  Mean   : {mn:.4f}  |  StdDev : {sd:.4f}")
            print(f"    Min   : {s['min']:.4f}  |  "
                  f"Q1     : {s['25%']:.4f}  |  "
                  f"Median : {s['50%']:.4f}  |  "
                  f"Q3     : {s['75%']:.4f}  |  "
                  f"Max    : {s['max']:.4f}")

            if n_rows > 0:
                out_rows = vr[vr["flag_outlier"]]
                print(f"    Outliers ({len(out_rows)}):")
                for _, row in out_rows.head(5).iterrows():
                    st = getattr(row, "state", "??")
                    co = getattr(row, "county", "?")
                    val_row = getattr(row, "aian_ratio", "?")
                    print(f"      {st}/{co}  ratio={val_row}")

    print(f"\n{section}")
    print("  Confidence Interval Summary (all years)")
    print(section)
    ci_width = df["ci_upper_95"] - df["ci_lower_95"]
    print(f"  Mean CI width          : {ci_width.mean():.4f}")
    print(f"  Median CI width        : {ci_width.median():.4f}")
    print(f"  Mean CV ratio (pct)    : {df['cv_ratio_pct'].mean():.1f}%")

    unreliable = df[(df["cv_aian"].fillna(999) > HIGH_CV_THRESHOLD) |
                    (df["cv_total"].fillna(999) > HIGH_CV_THRESHOLD)]
    print(f"  Unreliable by USDA std : {len(unreliable):>6,} "
          f"({100.0 * len(unreliable) / len(df):.1f}%)")

    if "aian_pop_share" in df.columns:
        print(f"\n{section}")
        print("  ACS Demographic Cross-Check")
        print(section)
        has_share = df[df["aian_pop_share"].notna()]
        print(f"  Counties with ACS data   : {len(has_share['fips'].unique()):>6,}")

        if "flag_demographic_mismatch" in df.columns:
            mm = int(df["flag_demographic_mismatch"].sum())
            print(f"  Demographic mismatches   : {mm:>6,} "
                  f"({100.0 * mm / len(df):.1f}%)")

    impossible = int(df["flag_impossible_ratio"].sum())
    zero_den = int(df["flag_zero_denominator"].sum()) if "flag_zero_denominator" in df.columns else 0
    error_count = impossible + zero_den

    print(f"\n{'=' * 78}")
    if error_count == 0:
        print("STATUS: PASS  -- No extraction errors detected")
    else:
        print(f"STATUS: REVIEW NEEDED  -- {error_count} structural errors found")
    print("=" * 78)
    print()

# --------------------------------------------------------------------------- #
#  Step 7 : Write verified CSV                                               #
# --------------------------------------------------------------------------- #


def write_output(df: pd.DataFrame):
    """Persist the annotated DataFrame."""
    df.to_csv(OUTPUT_FILE, index=False)
    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    log.info("Wrote %s  (%.1f kB)", OUTPUT_FILE, size_kb)

# --------------------------------------------------------------------------- #
#  Main pipeline                                                             #
# --------------------------------------------------------------------------- #


def main():
    log.info("=" * 60)
    log.info("AIAN Ratio Verification Pipeline")
    log.info("=" * 60)

    # Step 1 -- load + CV join
    df = _load_csv(RATIOS_FILE, "Ratios")
    df = join_cv_data(df)

    # Step 2 -- CIs
    df = compute_cis(df)

    # Step 3 -- anomalies
    df = detect_anomalies(df)

    # Step 4-5 -- ACS demographic cross-check
    acs_df = fetch_acs_demographics(df)
    if not acs_df.empty and "fips" in acs_df.columns:
        df = merge_demographics(df, acs_df)
    else:
        log.warning("Skipping demographic cross-check (no ACS data)")

    # Step 6 -- console report
    print_report(df)

    # Step 7 -- save
    write_output(df)

    log.info("Verification complete.")


if __name__ == "__main__":
    main()
