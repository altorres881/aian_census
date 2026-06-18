"""Quick validation: run the pipeline for 2 states, all 3 years to confirm it works."""

import aian_census_extract as ace
import logging

log = logging.getLogger(__name__)

# Override to run a small test
ace.CENSUS_YEARS = [2022, 2017, 2012]
ace.STATES = ["OK", "AZ"]  # States with high AIAN populations

print("Running quick test with OK and AZ, year 2022 only...")
df_aian, df_total = ace.run_pipeline()

if not df_aian.empty:
    print(f"\nSUCCESS! Got {len(df_aian)} AIAN records")
    print(f"\nSample AIAN data:")
    cols = [c for c in ["state", "county", "variable_desc", "value", "year"] if c in df_aian.columns]
    print(df_aian[cols].head(20).to_string(index=False))
else:
    print("\nFAILED - no AIAN data returned")
