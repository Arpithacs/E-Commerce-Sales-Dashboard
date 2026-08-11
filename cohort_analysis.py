# cohort_analysis.py
import os
import pandas as pd
from sqlalchemy import create_engine, text

# 1. Database Connection Setup
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

# 2. Pull invoice-level data
print("Pulling invoice-level transaction data for cohort analysis...")
query = """
    SELECT
        f.customer_key,
        f.invoice,
        d.date_key AS invoice_date
    FROM fact_sales f
    JOIN dim_customer c ON f.customer_key = c.customer_key
    JOIN dim_date d ON f.date_key = d.date_key
    WHERE f.is_return = FALSE
      AND f.customer_key IS NOT NULL;
"""

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn)

df['invoice_date'] = pd.to_datetime(df['invoice_date'])

# 3. Determine cohort_month and order_month
df['order_month'] = df['invoice_date'].dt.to_period('M')
df['cohort_month'] = df.groupby('customer_key')['order_month'].transform('min')

# 4. Compute cohort_index (months difference)
def get_month_diff(df, col1, col2):
    year_diff = df[col1].dt.year - df[col2].dt.year
    month_diff = df[col1].dt.month - df[col2].dt.month
    return year_diff * 12 + month_diff

df['cohort_index'] = get_month_diff(df, 'order_month', 'cohort_month')

# 5. Build Cohort Retention Pivot Table
cohort_counts = df.groupby(['cohort_month', 'cohort_index'])['customer_key'].nunique().reset_index()
cohort_pivot = cohort_counts.pivot(index='cohort_month', columns='cohort_index', values='customer_key')

# 6. Convert counts to retention percentages
cohort_size = cohort_pivot.iloc[:, 0]
retention = cohort_pivot.divide(cohort_size, axis=0) * 100

# Format and Print Tables
print("\n=== COHORT RETENTION MATRIX (PERCENTAGES %) ===")
pd.set_option('display.max_columns', 30)
pd.set_option('display.width', 1000)
print(retention.round(1).to_string())

print("\n=== INITIAL COHORT SIZES (COHORT INDEX 0) ===")
print(cohort_size.to_frame(name='Initial Customers').to_string())
