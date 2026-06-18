# AIAN Census of Agriculture Data Extraction

Extracts American Indian or Alaska Native (AIAN) producer data from the USDA NASS Quick Stats API for all U.S. counties, states, and American Indian Reservations across Census of Agriculture years (2022, 2017, 2012).

## Quick Start

```bash
# Install dependencies
pip install requests pandas

# Run full extraction (all 50 states, 3 years — takes ~30-40 minutes)
python aian_census_extract.py

# Or quick test with 2 states
python test_quick_run.py
```

## Output Files

All outputs are saved to the `output/` directory:

| File | Description |
|------|-------------|
| `aian_county_data.csv` | AIAN-specific producer data at county level |
| `total_county_data.csv` | Total (all producers) county data for ratio calculation |
| `aian_reservation_data.csv` | Data at the American Indian Reservation aggregation level |
| `aian_combined_ratios.csv` | Merged dataset with AIAN/Total ratios pre-computed |

## Output Schema

### aian_county_data.csv / total_county_data.csv

| Column | Description |
|--------|-------------|
| `state` | Two-letter state abbreviation |
| `state_fips` | Two-digit state FIPS code |
| `county_fips` | Three-digit county FIPS code |
| `county` | County name |
| `fips` | Full 5-digit FIPS (state + county) |
| `year` | Census year (2022, 2017, or 2012) |
| `variable_desc` | Full NASS variable description (short_desc) |
| `variable_short` | Abbreviated variable name for analysis |
| `domain` | Domain classification |
| `domain_category` | Domain category detail |
| `value` | Raw value as string (includes suppressed codes like "D", "Z") |
| `value_numeric` | Numeric value (NaN for suppressed/unavailable) |
| `cv_pct` | Coefficient of variation (%) |
| `agg_level` | Aggregation level (COUNTY, STATE, etc.) |

### aian_combined_ratios.csv

Includes all columns above plus:

| Column | Description |
|--------|-------------|
| `total_variable` | The corresponding "all producers" variable |
| `total_value` | Total val the same geography/year |
| `aian_ratio` | AIAN value / Total value (0 to 1 scale) |

## AIAN Variables Available at County Level (2022)

The pipeline captures ~23 variables per county including:

- Number of AIAN producers (alone and combined with other races)
- Acres operated by AIAN producers
- Number of farm operations
- Average age
- Acres per operation
- Gender breakdown (female/male)
- Age groups (< 35, 35-64, 65+)
- Days worked off operation (0, 1-199, 200+)
- Primary occupation (farming vs. non-farming)
- Military service
- Years on operation (< 11 years)
- Hispanic AIAN producers

## How AIAN Data Works in Quick Stats

The USDA NASS Quick Stats API does **not** expose race as a filterable domain parameter. Instead, AIAN data is identified by the `short_desc` (data item name) field:

- `PRODUCERS, AMERICAN INDIAN OR ALASKA NATIVE - NUMBER OF PRODUCERS`
- `PRODUCERS, AMERICAN INDIAN OR ALASKA NATIVE, ALONE OR COMBINED WITH OTHER RACES - ACRES OPERATED`
- etc.

The working query pattern is:
```
source_desc = CENSUS
commodity_desc = PRODUCERS   (or OPERATORS for 2012 and earlier)
domain_desc = TOTAL
agg_level_desc = COUNTY
state_alpha = {state}
year = {year}
```

This returns ALL producer data items for that state/county/year. The results are then filtered client-side for rows where `short_desc` contains "AMERICAN INDIAN".

### Terminology Change: OPERATORS vs PRODUCERS

USDA changed terminology between the 2012 and 2017 Census of Agriculture:
- **2012 and earlier**: Uses `commodity_desc=OPERATORS` and variable names like `OPERATORS, AMERICAN INDIAN OR ALASKA NATIVE - NUMBER OF OPERATORS`
- **2017 and later**: Uses `commodity_desc=PRODUCERS` and variable names like `PRODUCERS, AMERICAN INDIAN OR ALASKA NATIVE - NUMBER OF PRODUCERS`

The pipeline handles this automatically — it queries with the correct commodity for each year and harmonizes all variable names to the PRODUCERS terminology in the output files.

### Reservation-Level Data

Reservation-level aggregations (`agg_level_desc=AMERICAN INDIAN RESERVATION`) are only available for 2017 and 2022. The 2012 Census did not publish this aggregation level.

## API Details

- **API endpoint**: `https://quickstats.nass.usda.gov/api/api_GET/`
- **API key**: Included in code (free registration at https://quickstats.nass.usda.gov/api/)
- **Rate limit**: ~1 request/second sustained at first; then script uses a variable delay between each call when the API refuses requests
- **Row limit**: 50,000 rows per request (querying by individual state avoids this)
- **Available aggregation levels**: COUNTY, STATE, NATIONAL, AMERICAN INDIAN RESERVATION, ZIP CODE


## Customization

To modify the extraction scope, edit these variables in `aian_census_extract.py`:

```python
CENSUS_YEARS = [2022, 2017, 2012]  # Add/remove years
STATES = ["OK", "AZ", ...]         # Subset states for faster runs
```

## Dependencies

- Python 3.10+
- `requests`
- `pandas`

## Suppressed Data

USDA suppresses values to protect confidentiality. You'll see:
- `(D)` — Withheld to avoid disclosing data for individual operations
- `(Z)` — Less than half the unit shown

These appear as NaN in the `value_numeric` column. The raw string is preserved in `value`.

## Variable Discovery

To explore what other variables are available in the API, run:

```bash
python test_api_discovery.py
```

This queries the `get_param_values` endpoint to list all available `short_desc`, `domain_desc`, `domaincat_desc`, and `group_desc` values for Census producer data.
