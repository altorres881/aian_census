"""
AIAN Census of Agriculture Data Extraction Pipeline
====================================================
Extracts AIAN (American Indian or Alaska Native) producer data from
USDA NASS Quick Stats API for all counties/states across census years.

Key finding: AIAN data lives in the `short_desc` field (data item name),
not as a domain filter. The working query pattern is:
  - source_desc = CENSUS
  - commodity_desc = PRODUCERS
  - domain_desc = TOTAL
  - agg_level_desc = COUNTY (or STATE, or AMERICAN INDIAN RESERVATION)
  - Filter results where short_desc contains "AMERICAN INDIAN"

Usage:
    python aian_census_extract.py

Output:
    output/aian_county_data.csv       - AIAN-specific county records
    output/total_county_data.csv      - Total (all producers) county records
    output/aian_reservation_data.csv  - AIAN reservation-level data
    output/aian_combined_ratios.csv   - Merged with AIAN/Total ratios
"""

import requests
import pandas as pd
from io import StringIO
from urllib.parse import urlencode
import time
import logging
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("aian_census.log", mode="w"),
    ],
)
log = logging.getLogger(__name__)

API_KEY = os.environ["NASS_API_KEY"]
BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

CENSUS_YEARS = [2022, 2017, 2012]

STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]



def api_get(params: dict, max_retries: int = 3) -> Optional[pd.DataFrame]:
    """Query Quick Stats API with retries and rate limiting."""
    query_params = {"key": API_KEY, "format": "CSV", **params}
    url = BASE_URL + "?" + urlencode(query_params)

    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=90)

            if resp.status_code == 200:
                text = resp.text.strip()
                if not text or "no data available" in text.lower():
                    return None
                df = pd.read_csv(StringIO(text))
                if df.empty:
                    return None
                return df

            elif resp.status_code == 413:
                log.warning("Query too large (413). Need to narrow params: %s", params)
                return None

            elif resp.status_code in (429, 403):
                wait = 2 ** (attempt + 3)  # 8s, 16s, 32s
                log.warning("Rate limited (%d). Waiting %ds...", resp.status_code, wait)
                time.sleep(wait)
                continue

            elif resp.status_code == 400:
                log.debug("Bad request (400) for params: %s", params)
                return None

            else:
                log.error("HTTP %d: %s", resp.status_code, resp.text[:200])
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return None

        except requests.exceptions.Timeout:
            log.warning("Timeout on attempt %d", attempt + 1)
            time.sleep(5)
        except Exception as e:
            log.error("Request error: %s", e)
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                return None

    return None


def _commodity_for_year(year: int) -> str:
    """2017+ uses PRODUCERS; 2012 and earlier uses OPERATORS."""
    return "OPERATORS" if year <= 2012 else "PRODUCERS"


def fetch_producers_county(year: int, state: str) -> Optional[pd.DataFrame]:
    """
    Fetch ALL producer/operator data at county level for a state+year.
    This returns both AIAN and total data in a single call.
    AIAN data is identified by short_desc containing "AMERICAN INDIAN".
    """
    params = {
        "source_desc": "CENSUS",
        "year": str(year),
        "state_alpha": state,
        "agg_level_desc": "COUNTY",
        "commodity_desc": _commodity_for_year(year),
        "domain_desc": "TOTAL",
    }
    return api_get(params)


def fetch_producers_state(year: int, state: str) -> Optional[pd.DataFrame]:
    """Fetch state-level producer/operator data (includes more detailed AIAN breakdowns)."""
    params = {
        "source_desc": "CENSUS",
        "year": str(year),
        "state_alpha": state,
        "agg_level_desc": "STATE",
        "commodity_desc": _commodity_for_year(year),
        "domain_desc": "TOTAL",
    }
    return api_get(params)


def fetch_reservation_data(year: int) -> Optional[pd.DataFrame]:
    """Fetch American Indian Reservation aggregation level data."""
    commodity = _commodity_for_year(year)
    params = {
        "source_desc": "CENSUS",
        "year": str(year),
        "agg_level_desc": "AMERICAN INDIAN RESERVATION",
        "commodity_desc": commodity,
    }
    return api_get(params)


def split_aian_total(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a dataframe into AIAN-specific rows and total/all-producer rows."""
    is_aian = df["short_desc"].str.contains(
        "AMERICAN INDIAN|ALASKA NATIVE", case=False, na=False
    )
    return df[is_aian].copy(), df[~is_aian].copy()


def harmonize_operators_to_producers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize 2012 OPERATORS terminology to match 2017+ PRODUCERS terminology.
    This allows consistent variable names across years.
    """
    if df.empty or "variable_desc" not in df.columns:
        return df
    df = df.copy()
    df["variable_desc"] = (
        df["variable_desc"]
        .str.replace("OPERATORS", "PRODUCERS", regex=False)
        .str.replace("NUMBER OF PRODUCERS,", "NUMBER OF PRODUCERS", regex=False)
    )
    return df


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize column names and extract key fields."""
    if df.empty:
        return df

    df = df.copy()

    col_map = {
        "state_alpha": "state",
        "state_fips_code": "state_fips",
        "county_code": "county_fips",
        "county_name": "county",
        "year": "year",
        "short_desc": "variable_desc",
        "domain_desc": "domain",
        "domaincat_desc": "domain_category",
        "Value": "value",
        "CV (%)": "cv_pct",
        "agg_level_desc": "agg_level",
    }

    available = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=available)

    keep = list(available.values())
    keep = [c for c in keep if c in df.columns]
    df = df[keep]

    if "value" in df.columns:
        df["value"] = df["value"].astype(str).str.replace(",", "").str.strip()
        # Handle suppressed values (D), not available (Z), etc.
        df["value_numeric"] = pd.to_numeric(df["value"], errors="coerce")

    if "state_fips" in df.columns and "county_fips" in df.columns:
        df["state_fips"] = df["state_fips"].astype(str).str.zfill(2)
        df["county_fips"] = df["county_fips"].astype(str).str.zfill(3)
        df["fips"] = df["state_fips"] + df["county_fips"]

    return df


def create_variable_short_name(desc: str) -> str:
    """Create a concise variable identifier from the full short_desc."""
    if not isinstance(desc, str):
        return "UNKNOWN"

    d = desc.upper().strip()

    # Remove the common prefix
    d = d.replace("PRODUCERS, ", "").replace("PRODUCERS ", "")
    d = d.replace("AMERICAN INDIAN OR ALASKA NATIVE, ", "AIAN_")
    d = d.replace("AMERICAN INDIAN OR ALASKA NATIVE", "AIAN")
    d = d.replace("ALONE OR COMBINED WITH OTHER RACES, ", "COMBINED_")
    d = d.replace("ALONE OR COMBINED WITH OTHER RACES", "COMBINED")
    d = d.replace("(ALL), ", "ALL_")
    d = d.replace("(ALL)", "ALL")
    d = d.replace(" - NUMBER OF PRODUCERS", "_N_PRODUCERS")
    d = d.replace(" - ACRES OPERATED", "_ACRES")
    d = d.replace(" - NUMBER OF OPERATIONS", "_N_OPS")
    d = d.replace(" - AGE, AVG, MEASURED IN YEARS", "_AVG_AGE")
    d = d.replace(" - AREA OPERATED, MEASURED IN ACRES / OPERATION", "_ACRES_PER_OP")
    d = d.replace(" - OPERATIONS WITH RECEIPTS", "_OPS_W_RECEIPTS")
    d = d.replace(" - PERSONS IN HOUSEHOLD, MEASURED IN PERSONS", "_PERSONS_HH")
    d = d.replace(", ", "_").replace(" ", "_").replace("(", "").replace(")", "")

    return d[:80]


def infer_total_variable(aian_var: str) -> str:
    """
    Derive the corresponding total variable name from an AIAN variable name.

    Pattern: strip the AIAN race identifier and replace with (ALL).
    e.g. "PRODUCERS, AMERICAN INDIAN OR ALASKA NATIVE, FEMALE - NUMBER OF PRODUCERS"
      -> "PRODUCERS, (ALL), FEMALE - NUMBER OF PRODUCERS"
    """
    v = aian_var
    # Remove "ALONE OR COMBINED WITH OTHER RACES" qualifier (comes after AIAN)
    v = v.replace(", ALONE OR COMBINED WITH OTHER RACES", "")
    # Replace the AIAN race identifier with (ALL)
    v = v.replace("AMERICAN INDIAN OR ALASKA NATIVE", "(ALL)")
    # Also handle Hispanic AIAN variants
    v = v.replace("HISPANIC, (ALL)", "(ALL)")
    return v


def build_ratio_dataset(df_aian: pd.DataFrame, df_total: pd.DataFrame) -> pd.DataFrame:
    """
    Create a merged dataset matching AIAN values to their corresponding totals
    to enable AIAN/Total ratio calculation. Automatically maps all AIAN variables
    to their total counterparts by inferring the naming pattern.
    """
    if df_aian.empty or df_total.empty:
        return pd.DataFrame()

    # Build mapping dynamically from actual AIAN variables present
    aian_vars = df_aian["variable_desc"].unique()
    total_vars_available = set(df_total["variable_desc"].unique())

    aian_to_total = {}
    for av in aian_vars:
        inferred = infer_total_variable(av)
        if inferred in total_vars_available:
            aian_to_total[av] = inferred

    if not aian_to_total:
        log.warning("No AIAN variables could be matched to totals. "
                    "AIAN sample: %s | Total sample: %s",
                    list(aian_vars)[:3], list(total_vars_available)[:3])
        return pd.DataFrame()

    log.info("Matched %d/%d AIAN variables to totals for ratio calculation",
             len(aian_to_total), len(aian_vars))

    # Filter to matchable variables
    aian_key = df_aian[df_aian["variable_desc"].isin(aian_to_total.keys())].copy()
    aian_key["total_variable"] = aian_key["variable_desc"].map(aian_to_total)

    # Get totals
    total_subset = df_total[df_total["variable_desc"].isin(aian_to_total.values())].copy()
    if total_subset.empty:
        return pd.DataFrame()

    geo_keys = ["state", "county", "year"]
    available_keys = [k for k in geo_keys if k in aian_key.columns and k in total_subset.columns]

    if not available_keys:
        return pd.DataFrame()

    # Prepare totals for merge: rename variable_desc -> total_variable
    total_for_merge = total_subset[available_keys + ["variable_desc", "value_numeric"]].copy()
    total_for_merge = total_for_merge.rename(columns={
        "variable_desc": "total_variable",
        "value_numeric": "total_value",
    })

    merged = aian_key.merge(
        total_for_merge,
        on=available_keys + ["total_variable"],
        how="left",
    )

    merged["aian_ratio"] = (merged["value_numeric"] / merged["total_value"]).round(4)

    return merged


def run_pipeline():
    """Main extraction pipeline."""
    log.info("=" * 60)
    log.info("AIAN Census of Agriculture - Data Extraction Pipeline")
    log.info("=" * 60)
    log.info("Years: %s", CENSUS_YEARS)
    log.info("States: %d", len(STATES))

    all_aian_county = []
    all_total_county = []
    all_reservation = []

    # --- Phase 1: County-level data ---
    log.info("\n--- PHASE 1: County-Level Producer Data ---")
    total_queries = len(CENSUS_YEARS) * len(STATES)
    completed = 0
    failed_queries = []

    for year in CENSUS_YEARS:
        log.info("Year %d:", year)

        for state in STATES:
            completed += 1
            if completed % 25 == 0:
                log.info(
                    "  Progress: %d/%d (%.0f%%) | AIAN records so far: %d",
                    completed, total_queries, 100 * completed / total_queries,
                    sum(len(d) for d in all_aian_county),
                )

            df = fetch_producers_county(year, state)
            if df is not None:
                df_aian, df_total = split_aian_total(df)
                if not df_aian.empty:
                    all_aian_county.append(df_aian)
                if not df_total.empty:
                    all_total_county.append(df_total)
            else:
                failed_queries.append((year, state))

            time.sleep(1.2)

    # Retry failed queries with longer pause
    if failed_queries:
        log.info("Retrying %d failed queries...", len(failed_queries))
        time.sleep(5)
        still_failed = []
        for year, state in failed_queries:
            df = fetch_producers_county(year, state)
            if df is not None:
                df_aian, df_total = split_aian_total(df)
                if not df_aian.empty:
                    all_aian_county.append(df_aian)
                if not df_total.empty:
                    all_total_county.append(df_total)
                log.info("  Retry OK: %s %d", state, year)
            else:
                still_failed.append((year, state))
            time.sleep(1)

        if still_failed:
            log.warning("Still failed after retry: %s", still_failed)
        log.info("County queries complete. Failed: %d/%d (recovered %d on retry)",
                 len(still_failed), total_queries, len(failed_queries) - len(still_failed))
    else:
        log.info("County queries complete. Failed: 0/%d", total_queries)

    # --- Phase 2: Reservation-level data ---
    log.info("\n--- PHASE 2: American Indian Reservation Data ---")
    for year in CENSUS_YEARS:
        df = fetch_reservation_data(year)
        if df is not None:
            all_reservation.append(df)
            log.info("  %d: %d reservation records", year, len(df))
        else:
            log.info("  %d: no reservation data", year)
        time.sleep(1)

    # --- Phase 3: Normalize and save ---
    log.info("\n--- PHASE 3: Normalize and Export ---")

    # AIAN county data
    if all_aian_county:
        df_aian = pd.concat(all_aian_county, ignore_index=True)
        df_aian_norm = normalize_columns(df_aian)
        df_aian_norm = harmonize_operators_to_producers(df_aian_norm)
        df_aian_norm["variable_short"] = df_aian_norm["variable_desc"].apply(create_variable_short_name)

        path = os.path.join(OUTPUT_DIR, "aian_county_data.csv")
        df_aian_norm.to_csv(path, index=False)
        log.info("AIAN county: %d records -> %s", len(df_aian_norm), path)
        log.info("  States: %d | Counties: %d | Variables: %d",
                 df_aian_norm["state"].nunique() if "state" in df_aian_norm.columns else 0,
                 df_aian_norm["county"].nunique() if "county" in df_aian_norm.columns else 0,
                 df_aian_norm["variable_desc"].nunique() if "variable_desc" in df_aian_norm.columns else 0)
    else:
        df_aian_norm = pd.DataFrame()
        log.warning("No AIAN county data found!")

    # Total county data
    if all_total_county:
        df_total = pd.concat(all_total_county, ignore_index=True)
        df_total_norm = normalize_columns(df_total)
        df_total_norm = harmonize_operators_to_producers(df_total_norm)
        df_total_norm["variable_short"] = df_total_norm["variable_desc"].apply(create_variable_short_name)

        path = os.path.join(OUTPUT_DIR, "total_county_data.csv")
        df_total_norm.to_csv(path, index=False)
        log.info("Total county: %d records -> %s", len(df_total_norm), path)
    else:
        df_total_norm = pd.DataFrame()

    # Reservation data
    if all_reservation:
        df_res = pd.concat(all_reservation, ignore_index=True)
        df_res_norm = normalize_columns(df_res)
        df_res_norm = harmonize_operators_to_producers(df_res_norm)

        path = os.path.join(OUTPUT_DIR, "aian_reservation_data.csv")
        df_res_norm.to_csv(path, index=False)
        log.info("Reservation: %d records -> %s", len(df_res_norm), path)

    # --- Phase 4: Build combined ratio dataset ---
    log.info("\n--- PHASE 4: AIAN/Total Ratios ---")
    if not df_aian_norm.empty and not df_total_norm.empty:
        df_combined = build_ratio_dataset(df_aian_norm, df_total_norm)
        if not df_combined.empty:
            path = os.path.join(OUTPUT_DIR, "aian_combined_ratios.csv")
            df_combined.to_csv(path, index=False)
            log.info("Combined ratios: %d records -> %s", len(df_combined), path)
            log.info("  Mean AIAN ratio (producers): %.4f",
                     df_combined[df_combined["variable_desc"].str.contains("NUMBER OF PRODUCERS")]["aian_ratio"].mean())
        else:
            log.info("Could not compute ratios (no matching variables)")

    # --- Summary ---
    log.info("\n" + "=" * 60)
    log.info("EXTRACTION COMPLETE")
    log.info("=" * 60)

    if not df_aian_norm.empty and "variable_desc" in df_aian_norm.columns:
        log.info("\nAIAN variables found at county level:")
        for v in sorted(df_aian_norm["variable_desc"].unique()):
            count = len(df_aian_norm[df_aian_norm["variable_desc"] == v])
            log.info("  [%4d records] %s", count, v)

        log.info("\nYears coverage:")
        if "year" in df_aian_norm.columns:
            for year in sorted(df_aian_norm["year"].unique()):
                yr_data = df_aian_norm[df_aian_norm["year"] == year]
                log.info("  %d: %d records, %d states, %d counties",
                         year, len(yr_data),
                         yr_data["state"].nunique() if "state" in yr_data.columns else 0,
                         yr_data["county"].nunique() if "county" in yr_data.columns else 0)

    log.info("\nOutput files in '%s/':", OUTPUT_DIR)
    for f in sorted(os.listdir(OUTPUT_DIR)):
        size = os.path.getsize(os.path.join(OUTPUT_DIR, f))
        log.info("  %s (%.1f KB)", f, size / 1024)

    return df_aian_norm, df_total_norm


if __name__ == "__main__":
    run_pipeline()
