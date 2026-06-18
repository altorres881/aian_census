"""
Quick test script to validate API connection and discover correct
parameter values for AIAN data in Quick Stats.

Run this FIRST to confirm the API key works and find the right query structure.
"""

import os
import requests
import pandas as pd
from io import StringIO
from urllib.parse import urlencode
import time
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["NASS_API_KEY"]
BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"


def query(params: dict) -> pd.DataFrame | None:
    full = {"key": API_KEY, "format": "CSV", **params}
    url = BASE_URL + "?" + urlencode(full)
    r = requests.get(url, timeout=60)
    if r.status_code == 200 and "no data" not in r.text.lower()[:100]:
        return pd.read_csv(StringIO(r.text))
    print(f"  -> HTTP {r.status_code}: {r.text[:150]}")
    return None


print("=" * 60)
print("TEST 1: Basic API connectivity (corn acres, national)")
print("=" * 60)
df = query({
    "commodity_desc": "CORN",
    "statisticcat_desc": "AREA HARVESTED",
    "agg_level_desc": "NATIONAL",
    "year": "2022",
})
if df is not None:
    print(f"  OK - {len(df)} records returned")
else:
    print("  FAILED - check API key or network")
    exit(1)

time.sleep(1)

print("\n" + "=" * 60)
print("TEST 2: Find AIAN data structure - state level, 2022")
print("=" * 60)
print("\nAttempt A: domain_desc=RACE, domaincat_desc with AMERICAN INDIAN")
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "agg_level_desc": "STATE",
    "domain_desc": "RACE",
    "domaincat_desc": "RACE: (AMERICAN INDIAN OR ALASKA NATIVE)",
})
if df is not None:
    print(f"  OK - {len(df)} records")
    print(f"  Columns: {list(df.columns)}")
    print(f"  short_desc values (first 10): {df['short_desc'].unique()[:10].tolist()}")
    print(f"  States: {sorted(df['state_alpha'].unique())[:10]}")
else:
    print("  No data with this approach")

    time.sleep(1)
    print("\nAttempt B: group_desc=PRODUCERS, domain_desc=RACE")
    df = query({
        "source_desc": "CENSUS",
        "year": "2022",
        "agg_level_desc": "STATE",
        "group_desc": "PRODUCERS",
        "domain_desc": "RACE",
    })
    if df is not None:
        print(f"  OK - {len(df)} records")
        print(f"  domaincat_desc values: {df['domaincat_desc'].unique().tolist()}")
        print(f"  short_desc values: {df['short_desc'].unique()[:10].tolist()}")
        aian = df[df["domaincat_desc"].str.contains("INDIAN|ALASKA", case=False, na=False)]
        print(f"  AIAN-specific records: {len(aian)}")
    else:
        print("  No data with this approach either")

        time.sleep(1)
        print("\nAttempt C: Broad search for any RACE domain")
        df = query({
            "source_desc": "CENSUS",
            "year": "2022",
            "agg_level_desc": "STATE",
            "state_alpha": "OK",
            "domain_desc": "RACE",
        })
        if df is not None:
            print(f"  OK - {len(df)} records for Oklahoma")
            print(f"  domaincat_desc: {df['domaincat_desc'].unique().tolist()}")
            print(f"  short_desc: {df['short_desc'].unique()[:10].tolist()}")
        else:
            print("  Still no data")

time.sleep(1)

print("\n" + "=" * 60)
print("TEST 3: County-level AIAN for Oklahoma (high AIAN population)")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "domain_desc": "RACE",
    "domaincat_desc": "RACE: (AMERICAN INDIAN OR ALASKA NATIVE)",
})
if df is not None:
    print(f"  OK - {len(df)} county-level AIAN records for OK")
    print(f"  Counties: {df['county_name'].nunique()}")
    print(f"  Variables: {df['short_desc'].unique().tolist()[:5]}")
    print(f"\n  Sample data:")
    cols = ["county_name", "short_desc", "Value"]
    cols = [c for c in cols if c in df.columns]
    print(df[cols].head(10).to_string(index=False))
else:
    print("  No county AIAN data found")
    # Try alternative
    time.sleep(1)
    print("\n  Trying broader: all RACE domain for OK counties")
    df = query({
        "source_desc": "CENSUS",
        "year": "2022",
        "state_alpha": "OK",
        "agg_level_desc": "COUNTY",
        "domain_desc": "RACE",
    })
    if df is not None:
        print(f"  Found {len(df)} records")
        print(f"  domaincat_desc: {df['domaincat_desc'].unique().tolist()}")

time.sleep(1)

print("\n" + "=" * 60)
print("TEST 4: Check what TOTAL domain looks like")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "domain_desc": "TOTAL",
    "group_desc": "PRODUCERS",
})
if df is not None:
    print(f"  OK - {len(df)} total records for OK counties")
    print(f"  short_desc: {df['short_desc'].unique()[:5].tolist()}")
else:
    print("  No TOTAL domain. Trying without domain filter...")
    df = query({
        "source_desc": "CENSUS",
        "year": "2022",
        "state_alpha": "OK",
        "agg_level_desc": "COUNTY",
        "group_desc": "PRODUCERS",
        "statisticcat_desc": "PRODUCERS",
    })
    if df is not None:
        print(f"  Found {len(df)} records")
        print(f"  domain_desc values: {df['domain_desc'].unique().tolist()}")

print("\n" + "=" * 60)
print("TEST 5: List available get_param_values (what fields exist)")
print("=" * 60)
param_url = "https://quickstats.nass.usda.gov/api/get_param_values/?"
r = requests.get(param_url + urlencode({"key": API_KEY, "param": "domain_desc"}), timeout=30)
if r.status_code == 200:
    import json
    data = json.loads(r.text)
    domains = data.get("domain_desc", [])
    print(f"  Available domain_desc values ({len(domains)} total):")
    race_domains = [d for d in domains if "RACE" in d.upper()]
    print(f"  Race-related: {race_domains}")

print("\n" + "=" * 60)
print("TESTS COMPLETE")
print("=" * 60)
print("\nNext step: Run 'python aian_census_extract.py' for full extraction")
