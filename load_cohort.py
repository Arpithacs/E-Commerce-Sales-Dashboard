# load_cohort.py
import os
import pandas as pd
from sqlalchemy import create_engine, text

# 1. Environment and Connection Setup
db_url = os.getenv("SUPABASE_DB_URL")
if not db_url:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        db_url, _ = winreg.QueryValueEx(key, "SUPABASE_DB_URL")
    except Exception:
        pass

if not db_url:
    raise EnvironmentError("SUPABASE_DB_URL environment variable not found.")

engine = create_engine(db_url)

# 2. Extract Transaction Data & Perform Cohort Calculations
print("Pulling invoice-level data for cohort retention loading...")
query = """
    SELECT
        f.customer_key,
        f.invoice,
        d.date_key AS invoice_date
    FROM fact_sales f
    JOIN dim_date d ON f.date_key = d.date_key
    WHERE f.is_return = FALSE
      AND f.customer_key IS NOT NULL;
"""

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn)

df['invoice_date'] = pd.to_datetime(df['invoice_date'])
df['order_month'] = df['invoice_date'].dt.to_period('M')
df['cohort_month'] = df.groupby('customer_key')['order_month'].transform('min')

def get_month_diff(df, col1, col2):
    year_diff = df[col1].dt.year - df[col2].dt.year
    month_diff = df[col1].dt.month - df[col2].dt.month
    return year_diff * 12 + month_diff

df['cohort_index'] = get_month_diff(df, 'order_month', 'cohort_month')

# 3. Build Long-Format Cohort Retention DataFrame
cohort_counts = df.groupby(['cohort_month', 'cohort_index'])['customer_key'].nunique().reset_index()
cohort_counts.rename(columns={'customer_key': 'customers_active', 'cohort_index': 'period_number'}, inplace=True)

# Determine initial cohort size (period_number == 0)
initial_sizes = cohort_counts[cohort_counts['period_number'] == 0][['cohort_month', 'customers_active']].copy()
initial_sizes.rename(columns={'customers_active': 'customers_initial'}, inplace=True)

# Merge initial cohort size back to calculate retention percentage
fact_cohort_df = pd.merge(cohort_counts, initial_sizes, on='cohort_month', how='left')
fact_cohort_df['retention_pct'] = (fact_cohort_df['customers_active'] / fact_cohort_df['customers_initial'] * 100).round(2)

# Convert cohort_month Period to Date object for Postgres DATE type compatibility
fact_cohort_df['cohort_month'] = fact_cohort_df['cohort_month'].dt.to_timestamp().dt.date

# Sort cleanly
fact_cohort_df = fact_cohort_df.sort_values(by=['cohort_month', 'period_number']).reset_index(drop=True)

# 4. Database Setup and Load (Idempotent)
print("Preparing 'fact_cohort_retention' table in database...")
create_table_sql = """
CREATE TABLE IF NOT EXISTS fact_cohort_retention (
    cohort_month DATE,
    period_number INTEGER,
    customers_active INTEGER,
    customers_initial INTEGER,
    retention_pct NUMERIC(6, 2)
);
"""

with engine.begin() as conn:
    conn.execute(text(create_table_sql))
    conn.execute(text("TRUNCATE TABLE fact_cohort_retention;"))

print("Uploading cohort retention data to 'fact_cohort_retention'...")
fact_cohort_df.to_sql(
    'fact_cohort_retention',
    con=engine,
    if_exists='append',
    index=False,
    method='multi'
)

# 5. Verification & Output
with engine.connect() as conn:
    row_count = conn.execute(text("SELECT COUNT(*) FROM fact_cohort_retention;")).scalar()
    sample_df = pd.read_sql(text("SELECT * FROM fact_cohort_retention ORDER BY cohort_month, period_number LIMIT 10;"), conn)

print("\n" + "="*80)
print("COHORT RETENTION WAREHOUSE LOAD COMPLETE")
print("="*80)
print(f"Total processed rows: {len(fact_cohort_df)}")
print(f"Database table rows:  {row_count}")
print("\nSample 10 rows from 'fact_cohort_retention':")
print(sample_df.to_string(index=False))
print("="*80)
