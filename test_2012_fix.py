"""Verify that 2012 uses OPERATORS instead of PRODUCERS."""

import os
import requests
import pandas as pd
from io import StringIO
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["NASS_API_KEY"]
BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"


def query(params):
    full = {"key": API_KEY, "format": "CSV", **params}
    r = requests.get(BASE_URL + "?" + urlencode(full), timeout=60)
    if r.status_code == 200 and "no data" not in r.text.lower()[:100]:
        return pd.read_csv(StringIO(r.text))
    print(f"  HTTP {r.status_code}: {r.text[:150]}")
    return None


print("TEST 1: 2012 with commodity_desc=PRODUCERS (expected to fail)")
df = query({
    "source_desc": "CENSUS",
    "year": "2012",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "commodity_desc": "PRODUCERS",
    "domain_desc": "TOTAL",
})
print(f"  Result: {len(df) if df is not None else 'None'}\n")

print("TEST 2: 2012 with commodity_desc=OPERATORS")
df = query({
    "source_desc": "CENSUS",
    "year": "2012",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "commodity_desc": "OPERATORS",
    "domain_desc": "TOTAL",
})
if df is not None:
    print(f"  OK! {len(df)} records")
    aian = df[df["short_desc"].str.contains("AMERICAN INDIAN", case=False, na=False)]
    print(f"  AIAN rows: {len(aian)}")
    if not aian.empty:
        print(f"  AIAN variables:")
        for v in sorted(aian["short_desc"].unique())[:10]:
            print(f"    {v}")
else:
    print("  Also failed")

print("\nTEST 3: 2012 group_desc values")
import json
PARAM_URL = "https://quickstats.nass.usda.gov/api/get_param_values/"
r = requests.get(PARAM_URL + "?" + urlencode({
    "key": API_KEY,
    "param": "commodity_desc",
    "source_desc": "CENSUS",
    "year": "2012",
    "group_desc": "OPERATORS",
}), timeout=30)
if r.status_code == 200:
    vals = json.loads(r.text).get("commodity_desc", [])
    print(f"  commodity_desc for 2012 OPERATORS group: {vals}")

print("\nTEST 4: 2012 with group_desc=OPERATORS")
df = query({
    "source_desc": "CENSUS",
    "year": "2012",
    "state_alpha": "OK",
    "agg_level_desc": "COUNTY",
    "group_desc": "OPERATORS",
    "domain_desc": "TOTAL",
})
if df is not None:
    print(f"  OK! {len(df)} records")
    aian = df[df["short_desc"].str.contains("AMERICAN INDIAN|ALASKA NATIVE", case=False, na=False)]
    print(f"  AIAN rows: {len(aian)}")
    if not aian.empty:
        print(f"  AIAN variables:")
        for v in sorted(aian["short_desc"].unique())[:15]:
            print(f"    {v}")
        print(f"\n  Sample:")
        print(aian[["county_name", "short_desc", "Value"]].head(5).to_string(index=False))
