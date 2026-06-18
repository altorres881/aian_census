"""Deeper discovery of where AIAN data lives in Quick Stats."""

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


def get_param_values(param, **filters):
    params = {"key": API_KEY, "param": param, **filters}
    r = requests.get(PARAM_URL + "?" + urlencode(params), timeout=30)
    if r.status_code == 200:
        return json.loads(r.text).get(param, [])
    return []


def query(params):
    full = {"key": API_KEY, "format": "CSV", **params}
    url = BASE_URL + "?" + urlencode(full)
    r = requests.get(url, timeout=60)
    if r.status_code == 200 and "no data" not in r.text.lower()[:100]:
        return pd.read_csv(StringIO(r.text))
    return None


# 1. What domain_desc values exist for CENSUS producers?
print("=" * 60)
print("1. domain_desc values for CENSUS producers")
print("=" * 60)
domains = get_param_values("domain_desc", source_desc="CENSUS", group_desc="PRODUCERS")
print(f"  Found {len(domains)} domains:")
for d in sorted(domains):
    print(f"    {d}")

time.sleep(1)

# 2. What domaincat_desc values exist for CENSUS producers?
print("\n" + "=" * 60)
print("2. domaincat_desc values containing INDIAN or RACE")
print("=" * 60)
domaincats = get_param_values("domaincat_desc", source_desc="CENSUS", group_desc="PRODUCERS")
race_cats = [d for d in domaincats if "INDIAN" in d.upper() or "RACE" in d.upper() or "ALASKA" in d.upper()]
print(f"  Found {len(domaincats)} total domaincat values")
print(f"  Race/AIAN related ({len(race_cats)}):")
for d in sorted(race_cats):
    print(f"    {d}")

time.sleep(1)

# 3. What short_desc (data items) mention INDIAN or RACE?
print("\n" + "=" * 60)
print("3. short_desc values mentioning INDIAN/RACE/ALASKA")
print("=" * 60)
short_descs = get_param_values("short_desc", source_desc="CENSUS", group_desc="PRODUCERS")
race_items = [d for d in short_descs if "INDIAN" in d.upper() or "ALASKA NATIVE" in d.upper() or "RACE" in d.upper()]
print(f"  Found {len(short_descs)} total data items")
print(f"  AIAN/race related ({len(race_items)}):")
for d in sorted(race_items)[:30]:
    print(f"    {d}")

time.sleep(1)

# 4. Try querying with one of those short_desc values directly
print("\n" + "=" * 60)
print("4. Direct query with AIAN short_desc")
print("=" * 60)
if race_items:
    test_item = race_items[0]
    print(f"  Testing: {test_item}")
    df = query({
        "source_desc": "CENSUS",
        "year": "2022",
        "agg_level_desc": "STATE",
        "short_desc": test_item,
    })
    if df is not None:
        print(f"  OK - {len(df)} records")
        print(f"  domain_desc: {df['domain_desc'].unique().tolist()}")
        print(f"  domaincat_desc: {df['domaincat_desc'].unique().tolist()}")
        print(f"  Sample:")
        print(df[["state_alpha", "short_desc", "Value"]].head(5).to_string(index=False))
    else:
        print("  No data")
else:
    print("  No race_items found in short_desc. Trying broader search...")
    # Try looking in commodity_desc or statisticcat_desc
    stats = get_param_values("statisticcat_desc", source_desc="CENSUS", group_desc="PRODUCERS")
    race_stats = [s for s in stats if "INDIAN" in s.upper() or "RACE" in s.upper()]
    print(f"  statisticcat_desc race items: {race_stats}")

time.sleep(1)

# 5. Check if demographics group has race data
print("\n" + "=" * 60)
print("5. DEMOGRAPHICS group exploration")
print("=" * 60)
demo_domains = get_param_values("domain_desc", source_desc="CENSUS", group_desc="DEMOGRAPHICS")
print(f"  domain_desc for DEMOGRAPHICS: {demo_domains}")

demo_domaincats = get_param_values("domaincat_desc", source_desc="CENSUS", group_desc="DEMOGRAPHICS")
race_demo = [d for d in demo_domaincats if "INDIAN" in d.upper() or "RACE" in d.upper() or "ALASKA" in d.upper()]
print(f"  AIAN domaincat for DEMOGRAPHICS: {race_demo[:10]}")

time.sleep(1)

# 6. Try all groups to find where race data lives
print("\n" + "=" * 60)
print("6. All groups in CENSUS")
print("=" * 60)
groups = get_param_values("group_desc", source_desc="CENSUS")
print(f"  Groups: {groups}")

# 7. Check if commodity_desc has race/demographic data
print("\n" + "=" * 60)
print("7. commodity_desc values for CENSUS PRODUCERS group")
print("=" * 60)
commodities = get_param_values("commodity_desc", source_desc="CENSUS", group_desc="PRODUCERS")
print(f"  Commodities ({len(commodities)}): {commodities[:20]}")

time.sleep(1)

# 8. Try fetching state-level OK producers with TOTAL domain
print("\n" + "=" * 60)
print("8. OK state producers - inspect all domain values returned")
print("=" * 60)
df = query({
    "source_desc": "CENSUS",
    "year": "2022",
    "state_alpha": "OK",
    "agg_level_desc": "STATE",
    "group_desc": "PRODUCERS",
})
if df is not None:
    print(f"  {len(df)} records")
    print(f"  domain_desc unique: {df['domain_desc'].unique().tolist()}")
    print(f"  domaincat_desc unique ({df['domaincat_desc'].nunique()}):")
    for cat in sorted(df["domaincat_desc"].unique()):
        if "INDIAN" in cat.upper() or "RACE" in cat.upper() or "ALASKA" in cat.upper():
            print(f"    *** {cat}")
        elif "NOT SPECIFIED" not in cat:
            print(f"    {cat}")
