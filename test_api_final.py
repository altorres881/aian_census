"""Final test: find the working query pattern for AIAN county data."""

import os
import requests
import pandas as pd
from io import StringIO
from urllib.parse import urlencode
import json
import time
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["NASS_API_KEY"]
BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
PARAM_URL = "https://quickstats.nass.usda.gov/api/get_param_values/"


def query(params):
    full = {"key": API_KEY, "format": "CSV", **params}
    url = BASE_URL + "?" + urlencode(full)
    r = requests.get(url, timeout=60)
    if r.status_code == 200 and "no data" not in r.text.lower()[:100]:
        df = pd.read_csv(StringIO(r.text))
        return df
    print(f"  HTTP {r.status_code}: {r.text[:200]}")
    return None


def get_param_values(param, **filters):
    params = {"key": API_KEY, "param": param, **filters}
    r = requests.get(PARAM_URL + "?" + urlencode(params), timeout=30)
    if r.status_code == 200:
        return json.loads(r.text).get(param, [])
    return []


# Key finding: AIAN is in the commodity_desc / short_desc naming
# The domaincat_desc with "RACE = AMERICAN INDIAN" exists
# Let's try querying with that exact domaincat_desc

print("TEST A: Query with domaincat_desc for AIAN race")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "STATE",
    "group_desc": "PRODUCERS",
    "domaincat_desc": "PRODUCERS: (RACE = AMERICAN INDIAN OR ALASKA NATIVE, ALONE OR COMBINED WITH OTHER RACES)",
})
if df is not None:
    print(f"  OK! {len(df)} records")
    print(f"  short_desc: {df['short_desc'].unique()[:5].tolist()}")
    print(f"  domain_desc: {df['domain_desc'].unique().tolist()}")
else:
    print("  Failed")

time.sleep(1)

print("\nTEST B: Query with commodity_desc = PRODUCERS, AMERICAN INDIAN...")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "STATE",
    "commodity_desc": "PRODUCERS",
    "short_desc": "PRODUCERS, AMERICAN INDIAN OR ALASKA NATIVE - NUMBER OF PRODUCERS",
})
if df is not None:
    print(f"  OK! {len(df)} records")
    print(f"  domain_desc: {df['domain_desc'].unique().tolist()}")
    print(f"  domaincat_desc: {df['domaincat_desc'].unique().tolist()}")
    print(df[["state_alpha", "short_desc", "Value", "domain_desc"]].head().to_string(index=False))
else:
    print("  Failed")

time.sleep(1)

print("\nTEST C: Broader - all AIAN short_desc items for OK state")
print("=" * 60)
# Use commodity_desc to narrow down
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "STATE",
    "commodity_desc": "PRODUCERS",
    "domain_desc": "TOTAL",
})
if df is not None:
    aian_rows = df[df["short_desc"].str.contains("AMERICAN INDIAN", case=False, na=False)]
    print(f"  Total records: {len(df)}, AIAN-filtered: {len(aian_rows)}")
    if not aian_rows.empty:
        print(f"  AIAN short_desc values:")
        for s in sorted(aian_rows["short_desc"].unique()):
            print(f"    {s}")
        print(f"\n  Sample:")
        print(aian_rows[["short_desc", "Value"]].head(10).to_string(index=False))
else:
    print("  Failed")

time.sleep(1)

print("\nTEST D: County-level AIAN for OK with commodity filter")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "commodity_desc": "PRODUCERS",
    "domain_desc": "TOTAL",
})
if df is not None:
    aian_rows = df[df["short_desc"].str.contains("AMERICAN INDIAN", case=False, na=False)]
    print(f"  Total records: {len(df)}, AIAN rows: {len(aian_rows)}")
    if not aian_rows.empty:
        print(f"  Counties with AIAN data: {aian_rows['county_name'].nunique()}")
        print(f"  AIAN variables:")
        for s in sorted(aian_rows["short_desc"].unique()):
            print(f"    {s}")
        print(f"\n  Sample:")
        print(aian_rows[["county_name", "short_desc", "Value"]].head(10).to_string(index=False))
    else:
        print("  No AIAN rows in the data - checking what's there...")
        samples = df["short_desc"].unique()[:10]
        print(f"  short_desc samples: {samples.tolist()}")
else:
    print("  Failed")

time.sleep(1)

print("\nTEST E: Try 'PRODUCERS' domain_desc (not TOTAL)")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "commodity_desc": "PRODUCERS",
    "domain_desc": "PRODUCERS",
})
if df is not None:
    print(f"  {len(df)} records")
    aian_rows = df[df["short_desc"].str.contains("AMERICAN INDIAN", case=False, na=False)]
    print(f"  AIAN rows: {len(aian_rows)}")
    if not aian_rows.empty:
        print(f"  AIAN variables:")
        for s in sorted(aian_rows["short_desc"].unique()):
            print(f"    {s}")
    # Also check domaincat
    aian_cat = df[df["domaincat_desc"].str.contains("INDIAN|ALASKA", case=False, na=False)]
    print(f"  Rows with AIAN in domaincat_desc: {len(aian_cat)}")
    if not aian_cat.empty:
        print(f"  domaincat values: {aian_cat['domaincat_desc'].unique()[:5].tolist()}")
        print(f"  short_desc: {aian_cat['short_desc'].unique()[:5].tolist()}")
else:
    print("  Failed")

time.sleep(1)

print("\nTEST F: Query via domaincat_desc at county level")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "group_desc": "PRODUCERS",
    "domaincat_desc": "PRODUCERS: (RACE = AMERICAN INDIAN OR ALASKA NATIVE, ALONE OR COMBINED WITH OTHER RACES)",
})
if df is not None:
    print(f"  OK! {len(df)} records at county level!")
    print(f"  Counties: {df['county_name'].nunique()}")
    print(f"  Variables: {df['short_desc'].unique().tolist()[:5]}")
    print(f"\n  Sample:")
    print(df[["county_name", "short_desc", "Value"]].head(10).to_string(index=False))
else:
    print("  Failed")

print("\nTEST G: Just domain_desc=PRODUCERS at county for AIAN in short_desc")
print("=" * 60)
# Maybe the AIAN data uses a different filter at county level
# Let's check what's available
time.sleep(1)
agg_levels = get_param_values(
    "agg_level_desc",
    source_desc="CENSUS",
    group_desc="PRODUCERS",
    commodity_desc="PRODUCERS",
    year="2022",
)
print(f"  Available agg_level_desc: {agg_levels}")
