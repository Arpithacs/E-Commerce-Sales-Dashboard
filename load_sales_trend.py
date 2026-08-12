# load_sales_trend.py
import os
import pandas as pd
import numpy as np
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

# 2. Extract Pre-aggregated Daily Data (Optimized Query)
print("Pulling aggregated daily non-return sales records from database...")
query = """
    SELECT
        d.date_key,
        SUM(f.quantity * f.unit_price) AS revenue,
        SUM(f.quantity) AS quantity
    FROM fact_sales f
    LEFT JOIN dim_date d ON f.date_key = d.date_key
    WHERE f.is_return = FALSE
    GROUP BY d.date_key;
"""

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn)

df['date_key'] = pd.to_datetime(df['date_key'])
df['year_month'] = df['date_key'].dt.to_period('M')
df['month_num'] = df['date_key'].dt.month
df['month_name'] = df['date_key'].dt.strftime('%B')

total_overall_revenue = df['revenue'].sum()

# -------------------------------------------------------------
# 3. Process Table 1: fact_sales_monthly
# -------------------------------------------------------------
monthly_df = df.groupby('year_month').agg(
    revenue=('revenue', 'sum'),
    units=('quantity', 'sum')
).reset_index()

monthly_df['mom_change_pct'] = (monthly_df['revenue'].pct_change() * 100).round(2)
monthly_df['yoy_change_pct'] = (monthly_df['revenue'].pct_change(12) * 100).round(2)
monthly_df['revenue'] = monthly_df['revenue'].round(2)
monthly_df['units'] = monthly_df['units'].astype(int)

# Convert year_month Period to DATE (first day of month)
monthly_df['year_month'] = monthly_df['year_month'].dt.to_timestamp().dt.date

# Sort chronologically
monthly_df = monthly_df.sort_values(by='year_month').reset_index(drop=True)

# -------------------------------------------------------------
# 4. Process Table 2: fact_sales_seasonality
# -------------------------------------------------------------
seasonality_df = df.groupby(['month_num', 'month_name']).agg(
    total_revenue=('revenue', 'sum'),
    total_units=('quantity', 'sum'),
    year_count=('date_key', lambda x: x.dt.year.nunique())
).reset_index()

seasonality_df['avg_monthly_revenue'] = (seasonality_df['total_revenue'] / seasonality_df['year_count']).round(2)
seasonality_df['revenue_share_pct'] = ((seasonality_df['total_revenue'] / total_overall_revenue) * 100).round(2)
seasonality_df['total_revenue'] = seasonality_df['total_revenue'].round(2)
seasonality_df['total_units'] = seasonality_df['total_units'].astype(int)

# Rank top 3 and bottom 3 months
seasonality_sorted = seasonality_df.sort_values('revenue_share_pct', ascending=False)
top_3_months = set(seasonality_sorted.head(3)['month_name'])
bottom_3_months = set(seasonality_sorted.tail(3)['month_name'])

def flag_month(name):
    if name in top_3_months:
        return 'Top 3 Peak'
    elif name in bottom_3_months:
        return 'Bottom 3 Trough'
    return 'Regular'

seasonality_df['seasonality_flag'] = seasonality_df['month_name'].apply(flag_month)
seasonality_df.rename(columns={'month_num': 'month_number'}, inplace=True)

# Select and order required columns for Table 2
fact_seasonality_df = seasonality_df[[
    'month_name', 'month_number', 'total_revenue', 'total_units', 
    'avg_monthly_revenue', 'revenue_share_pct', 'seasonality_flag'
]].sort_values(by='month_number').reset_index(drop=True)

# -------------------------------------------------------------
# 5. Database Setup and Load (Idempotent)
# -------------------------------------------------------------
print("Preparing 'fact_sales_monthly' and 'fact_sales_seasonality' tables in database...")

create_monthly_sql = """
CREATE TABLE IF NOT EXISTS fact_sales_monthly (
    year_month DATE,
    revenue NUMERIC(14, 2),
    units INTEGER,
    mom_change_pct NUMERIC(6, 2),
    yoy_change_pct NUMERIC(6, 2)
);
"""

create_seasonality_sql = """
CREATE TABLE IF NOT EXISTS fact_sales_seasonality (
    month_name VARCHAR(20),
    month_number INTEGER,
    total_revenue NUMERIC(14, 2),
    total_units INTEGER,
    avg_monthly_revenue NUMERIC(14, 2),
    revenue_share_pct NUMERIC(6, 2),
    seasonality_flag VARCHAR(50)
);
"""

with engine.begin() as conn:
    conn.execute(text(create_monthly_sql))
    conn.execute(text(create_seasonality_sql))
    conn.execute(text("TRUNCATE TABLE fact_sales_monthly;"))
    conn.execute(text("TRUNCATE TABLE fact_sales_seasonality;"))

print("Uploading data to 'fact_sales_monthly'...")
monthly_df.to_sql(
    'fact_sales_monthly',
    con=engine,
    if_exists='append',
    index=False,
    method='multi'
)

print("Uploading data to 'fact_sales_seasonality'...")
fact_seasonality_df.to_sql(
    'fact_sales_seasonality',
    con=engine,
    if_exists='append',
    index=False,
    method='multi'
)

# -------------------------------------------------------------
# 6. Verification & Output
# -------------------------------------------------------------
with engine.connect() as conn:
    count_monthly = conn.execute(text("SELECT COUNT(*) FROM fact_sales_monthly;")).scalar()
    count_seasonality = conn.execute(text("SELECT COUNT(*) FROM fact_sales_seasonality;")).scalar()
    
    sample_monthly = pd.read_sql(text("SELECT * FROM fact_sales_monthly ORDER BY year_month LIMIT 10;"), conn)
    sample_seasonality = pd.read_sql(text("SELECT * FROM fact_sales_seasonality ORDER BY month_number;"), conn)

print("\n" + "="*80)
print("SALES TREND & SEASONALITY WAREHOUSE LOAD COMPLETE")
print("="*80)
print(f"fact_sales_monthly:     {count_monthly} rows loaded.")
print(f"fact_sales_seasonality: {count_seasonality} rows loaded.")

print("\nSample 10 rows from 'fact_sales_monthly':")
print(sample_monthly.to_string(index=False))

print("\nFull 12 rows from 'fact_sales_seasonality':")
print(sample_seasonality.to_string(index=False))
print("="*80)
