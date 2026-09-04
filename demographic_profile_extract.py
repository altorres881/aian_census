"""
NASS Demographic Profile PDF Extraction Pipeline
=================================================
Downloads and parses USDA NASS 2022 Census of Agriculture "Demographic
Profile" PDFs, published one per county at:

  https://www.nass.usda.gov/Publications/AgCensus/2022/Online_Resources/
      Demographic_Profiles/{StateName}/cpd{FIPS}.pdf

Each county PDF has 14 pages: 7 race/sex producer categories (American
Indian/Alaska Native, Asian, Black or African American, Native Hawaiian or
Other Pacific Islander, White, Hispanic/Latino/Spanish, Female), each
spanning 2 pages:
  - Page 1 ("overview"): farm counts, land in farms, market value of sales,
    government payments, income/expenses, land use %, sales-by-size,
    farms-by-size, top crops.
  - Page 2 ("demographics"): sex, age, primary occupation, days worked off
    farm, military service, Hispanic origin, tenure, land ownership,
    internet access, direct sales, organic farming, new/beginning farmers.

Not all data is published for every county/category (small counties are
often suppressed for disclosure) -- suppressed pages are recorded with a
single AVAILABILITY row rather than silently dropped.

The output `fips` column (2-digit state FIPS + 3-digit county FIPS) is the
same join key already produced in aian_census_extract.py's
normalize_columns(), so this dataset is directly joinable to
output/aian_county_data.csv / output/total_county_data.csv on `fips`
(+ year == 2022).

Usage:
    python demographic_profile_extract.py

Output:
    output/demographic_profile_county_data.csv
"""

import io
import logging
import os
import re
import time
from typing import Optional

import pandas as pd
import pdfplumber
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("demographic_profile.log", mode="w"),
    ],
)
log = logging.getLogger(__name__)

BASE_URL = "https://www.nass.usda.gov/Publications/AgCensus/2017/Online_Resources/Demographic_Profiles"
OUTPUT_DIR = "output"
PDF_CACHE_DIR = "pdf_cache"
YEAR = 2017
CHECKPOINT_PATH = os.path.join(OUTPUT_DIR, f"demographic_profile_county_data_{YEAR}.partial.csv")
FINAL_OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"demographic_profile_county_data_{YEAR}.csv")
CHECKPOINT_EVERY = 100

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(PDF_CACHE_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "aian_census research script (contact via github.com/altorres881/aian_census)"
    )
}

STATE_DIR_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New_Hampshire", "NJ": "New_Jersey", "NM": "New_Mexico", "NY": "New_York",
    "NC": "North_Carolina", "ND": "North_Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode_Island", "SC": "South_Carolina",
    "SD": "South_Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West_Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

VALUE_RE = re.compile(r"^\(?-?\$?[\d,]+\.?\d*%?\)?$|^-$|^\(D\)$|^\(Z\)$|^\(NA\)$")
CONTINUATION_LABELS = {"farms", "acres"}
SUPPRESSED_RE = re.compile(r"Datanotavailable", re.IGNORECASE)
CATEGORY_RE = re.compile(r"Farms with\s+(.+?)\s+Producers", re.IGNORECASE | re.DOTALL)


# --------------------------------------------------------------------------
# County list
# --------------------------------------------------------------------------

def get_county_list() -> pd.DataFrame:
    """Reuse the validated county/FIPS list already produced by
    aian_census_extract.py instead of sourcing a separate FIPS reference."""
    src = os.path.join(OUTPUT_DIR, "total_county_data.csv")
    df = pd.read_csv(src, dtype={"state_fips": str, "county_fips": str, "fips": str})
    counties = df[["state", "state_fips", "county_fips", "county", "fips"]].drop_duplicates()
    counties = counties[counties["state"].isin(STATE_DIR_NAMES.keys())]
    counties["state_fips"] = counties["state_fips"].str.zfill(2)
    counties["county_fips"] = counties["county_fips"].str.zfill(3)
    counties["fips"] = counties["state_fips"] + counties["county_fips"]
    return counties.sort_values(["state", "county_fips"]).reset_index(drop=True)


def build_pdf_url(state: str, fips: str) -> str:
    state_dir = STATE_DIR_NAMES[state]
    return f"{BASE_URL}/{state_dir}/cpd{fips}.pdf"


# --------------------------------------------------------------------------
# Download with local cache
# --------------------------------------------------------------------------

def download_pdf(url: str, fips: str, max_retries: int = 3) -> Optional[bytes]:
    cache_path = os.path.join(PDF_CACHE_DIR, f"{YEAR}_cpd{fips}.pdf")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()

    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 200:
                with open(cache_path, "wb") as f:
                    f.write(resp.content)
                return resp.content
            elif resp.status_code == 404:
                log.debug("No profile published for %s (404)", fips)
                return None
            elif resp.status_code in (429, 503):
                wait = 2 ** (attempt + 3)
                log.warning("Rate limited/unavailable (%d) for %s. Waiting %ds...",
                            resp.status_code, fips, wait)
                time.sleep(wait)
                continue
            else:
                log.warning("HTTP %d for %s", resp.status_code, fips)
                return None
        except requests.exceptions.Timeout:
            log.warning("Timeout on attempt %d for %s", attempt + 1, fips)
            time.sleep(5)
        except Exception as e:
            log.error("Request error for %s: %s", fips, e)
            time.sleep(2)

    return None


# --------------------------------------------------------------------------
# PDF layout parsing
# --------------------------------------------------------------------------

def cluster_words_into_lines(words: list, y_tol: float = 2.5) -> list:
    """Group words sharing (approximately) the same vertical position into lines."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    current = [words[0]]
    current_top = words[0]["top"]
    for w in words[1:]:
        if abs(w["top"] - current_top) <= y_tol:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
            current_top = w["top"]
    lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


COLUMN_SPLIT_RATIO = 0.615


def find_column_split(words: list, page_width: float) -> float:
    """Return the x-coordinate of the gutter between the page's two visual
    columns. The NASS Demographic Profile PDFs are generated from a single
    fixed template for every county, and the left/right column gutter sits
    consistently around x = 0.615 * page_width across both page types
    (overview and demographics) and every sampled state/county -- verified
    directly against several sample PDFs. A fixed ratio is far more
    reliable here than dynamic gap-detection, which cannot distinguish the
    true column gutter from narrower internal sub-column gaps (e.g.
    between a label sub-column and its value sub-column) within a single
    visual half."""
    return page_width * COLUMN_SPLIT_RATIO


def split_row_label_values(tokens: list) -> tuple:
    """Split a line's word tokens into a leading label and trailing value tokens."""
    split_idx = len(tokens)
    for i, t in enumerate(tokens):
        if VALUE_RE.match(t):
            split_idx = i
            break
    return tokens[:split_idx], tokens[split_idx:]


def parse_column(lines: list) -> list:
    """Parse one column's lines into label/value/section rows using a small
    state machine: label-only lines become the current `section` header;
    a line whose only label token is 'farms' or 'acres' is a continuation
    of the previous row's label (NASS nests farms/acres sub-rows for
    tenure & land-ownership categories without repeating the category name)."""
    rows = []
    section = None
    prev_label = None
    unlabeled_count = 0
    for line in lines:
        tokens = [w["text"] for w in line]
        label_tokens, value_tokens = split_row_label_values(tokens)

        if not value_tokens:
            # Label-only line: treat as a section header.
            section = " ".join(label_tokens)
            continue

        if len(label_tokens) == 1 and label_tokens[0].lower() in CONTINUATION_LABELS and prev_label:
            label = f"{prev_label} {label_tokens[0]}"
        elif label_tokens:
            label = " ".join(label_tokens)
            prev_label = label
        else:
            # No label tokens landed in this column for this row -- most
            # commonly the row's label fell on the *other* side of the
            # fixed column split (a handful of sections, e.g. "Farms by
            # Size", pack 4 sub-columns into the page and straddle the
            # split). Don't silently reuse the unrelated previous label --
            # that produces confidently wrong output. Mark it explicitly
            # so it's visible in the data rather than silently misleading.
            unlabeled_count += 1
            label = f"UNLABELED_ROW_{unlabeled_count}"

        rows.append({
            "section": section,
            "label": label,
            "values": value_tokens,
        })
    return rows


def parse_page_columns(page) -> list:
    """Split a page into its two visual columns and parse each independently."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return []
    split_x = find_column_split(words, page.width)
    left_words = [w for w in words if w["x0"] < split_x]
    right_words = [w for w in words if w["x0"] >= split_x]

    rows = []
    for col_words in (left_words, right_words):
        lines = cluster_words_into_lines(col_words)
        rows.extend(parse_column(lines))
    return rows


def clean_value(v: str) -> Optional[float]:
    if v in ("-", "(D)", "(Z)", "(NA)"):
        return None
    v = v.replace("$", "").replace(",", "").replace("%", "").strip("()")
    try:
        return float(v)
    except ValueError:
        return None


def extract_category(text: str) -> Optional[str]:
    m = CATEGORY_RE.search(text.replace("\n", " "))
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()


def parse_demographic_pdf(pdf_bytes: bytes, fips: str, source_url: str) -> list:
    """Parse a full 14-page county Demographic Profile PDF into a list of
    long-format row dicts: one per (category, page_type, section, label,
    value slot)."""
    records = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = pdf.pages
        for i in range(0, len(pages), 2):
            page1 = pages[i]
            page1_text = page1.extract_text() or ""
            category = extract_category(page1_text)
            if category is None:
                log.warning("%s: could not identify category on page %d", fips, i)
                continue

            if SUPPRESSED_RE.search(page1_text.replace("\n", "")):
                records.append({
                    "category": category, "page_type": "overview",
                    "section": "AVAILABILITY", "label": "data_available",
                    "value_1": "0", "value_2": None,
                })
                continue

            records.append({
                "category": category, "page_type": "overview",
                "section": "AVAILABILITY", "label": "data_available",
                "value_1": "1", "value_2": None,
            })
            for row in parse_page_columns(page1):
                records.append({
                    "category": category, "page_type": "overview",
                    "section": row["section"], "label": row["label"],
                    "value_1": row["values"][0] if len(row["values"]) > 0 else None,
                    "value_2": row["values"][1] if len(row["values"]) > 1 else None,
                })

            if i + 1 >= len(pages):
                continue
            page2 = pages[i + 1]
            page2_text = page2.extract_text() or ""
            if not page2_text.strip():
                continue
            for row in parse_page_columns(page2):
                records.append({
                    "category": category, "page_type": "demographics",
                    "section": row["section"], "label": row["label"],
                    "value_1": row["values"][0] if len(row["values"]) > 0 else None,
                    "value_2": row["values"][1] if len(row["values"]) > 1 else None,
                })

    for r in records:
        r["fips"] = fips
        r["source_pdf_url"] = source_url
    return records


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def variable_short_name(section: Optional[str], label: str) -> str:
    base = f"{section}_{label}" if section else label
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return base[:80]


def load_checkpoint() -> tuple:
    if os.path.exists(CHECKPOINT_PATH):
        df = pd.read_csv(CHECKPOINT_PATH, dtype={"fips": str})
        done = set(df["fips"].unique())
        return df, done
    return pd.DataFrame(), set()


def run_pipeline():
    log.info("=" * 60)
    log.info("NASS Demographic Profile PDF Extraction Pipeline")
    log.info("=" * 60)

    counties = get_county_list()
    log.info("Loaded %d counties across %d states", len(counties), counties["state"].nunique())

    checkpoint_df, done_fips = load_checkpoint()
    all_rows = [checkpoint_df] if not checkpoint_df.empty else []
    log.info("Resuming: %d counties already processed", len(done_fips))

    since_checkpoint = 0
    n_total = len(counties)
    n_suppressed_all = 0
    n_failed = []

    for idx, row in counties.iterrows():
        fips = row["fips"]
        if fips in done_fips:
            continue

        state, state_fips, county_fips, county = row["state"], row["state_fips"], row["county_fips"], row["county"]
        url = build_pdf_url(state, fips)
        pdf_bytes = download_pdf(url, fips)

        if pdf_bytes is None:
            n_failed.append(fips)
            time.sleep(0.5)
            continue

        try:
            records = parse_demographic_pdf(pdf_bytes, fips, url)
        except Exception as e:
            log.error("Failed to parse PDF for %s (%s): %s", fips, county, e)
            n_failed.append(fips)
            time.sleep(0.5)
            continue

        for r in records:
            r.update({
                "state": state, "state_fips": state_fips,
                "county_fips": county_fips, "county": county, "year": YEAR,
            })
            r["value_1_numeric"] = clean_value(r["value_1"]) if r["value_1"] else None
            r["value_2_numeric"] = clean_value(r["value_2"]) if r["value_2"] else None
            r["variable_short"] = variable_short_name(r["section"], r["label"])

        df_county = pd.DataFrame(records)
        all_rows.append(df_county)
        since_checkpoint += 1

        if (idx + 1) % 25 == 0 or (idx + 1) == n_total:
            log.info("Progress: %d/%d counties (%.1f%%) | failed: %d",
                      idx + 1, n_total, 100 * (idx + 1) / n_total, len(n_failed))

        if since_checkpoint >= CHECKPOINT_EVERY:
            pd.concat(all_rows, ignore_index=True).to_csv(CHECKPOINT_PATH, index=False)
            log.info("Checkpoint saved (%d counties processed so far)", len(done_fips) + since_checkpoint)
            since_checkpoint = 0

        time.sleep(1)

    if n_failed:
        log.warning("Retrying %d failed counties...", len(n_failed))
        still_failed = []
        for fips in n_failed:
            row = counties[counties["fips"] == fips].iloc[0]
            state, state_fips, county_fips, county = row["state"], row["state_fips"], row["county_fips"], row["county"]
            url = build_pdf_url(state, fips)
            pdf_bytes = download_pdf(url, fips)
            if pdf_bytes is None:
                still_failed.append(fips)
                time.sleep(1)
                continue
            try:
                records = parse_demographic_pdf(pdf_bytes, fips, url)
                for r in records:
                    r.update({
                        "state": state, "state_fips": state_fips,
                        "county_fips": county_fips, "county": county, "year": YEAR,
                    })
                    r["value_1_numeric"] = clean_value(r["value_1"]) if r["value_1"] else None
                    r["value_2_numeric"] = clean_value(r["value_2"]) if r["value_2"] else None
                    r["variable_short"] = variable_short_name(r["section"], r["label"])
                all_rows.append(pd.DataFrame(records))
            except Exception as e:
                log.error("Retry failed to parse %s: %s", fips, e)
                still_failed.append(fips)
            time.sleep(1)
        if still_failed:
            log.warning("Still failed after retry (%d): %s", len(still_failed), still_failed)

    final_df = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    if final_df.empty:
        log.error("No records extracted!")
        return final_df

    final_df["state_fips"] = final_df["state_fips"].astype(str).str.zfill(2)
    final_df["county_fips"] = final_df["county_fips"].astype(str).str.zfill(3)
    final_df["fips"] = final_df["state_fips"] + final_df["county_fips"]

    final_df.to_csv(FINAL_OUTPUT_PATH, index=False)
    if os.path.exists(CHECKPOINT_PATH):
        os.remove(CHECKPOINT_PATH)

    log.info("=" * 60)
    log.info("EXTRACTION COMPLETE: %d records -> %s", len(final_df), FINAL_OUTPUT_PATH)
    log.info("Counties covered: %d/%d", final_df["fips"].nunique(), n_total)
    log.info("Categories found: %s", sorted(final_df["category"].dropna().unique()))
    log.info("=" * 60)

    return final_df


if __name__ == "__main__":
    run_pipeline()
