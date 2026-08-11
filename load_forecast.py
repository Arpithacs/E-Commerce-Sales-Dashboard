# load_forecast.py
import os
import pandas as pd
from sqlalchemy import create_engine, text

# 1. Load the connection string from the environment
db_url = os.getenv("SUPABASE_DB_URL")
if not db_url:
    raise EnvironmentError(
        "Environment variable SUPABASE_DB_URL not found. "
        "Make sure you set it in your environment."
    )

engine = create_engine(db_url)

# 2. Load forecast results from CSV
csv_path = os.path.join(
    os.path.dirname(__file__),
    'forecast_results.csv'
)
if not os.path.exists(csv_path):
    raise FileNotFoundError(f"Could not find CSV at: {csv_path}")

df = pd.read_csv(csv_path)

# Ensure week_start is formatted as a date object
df['week_start'] = pd.to_datetime(df['week_start']).dt.date

# 3. Connect to DB and perform table setup and load
print("Connecting to Supabase and preparing 'fact_forecast' table...")
with engine.begin() as conn:  # using begin() commits automatically
    # Create the table if it does not exist with explicit types
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fact_forecast (
            product_key INTEGER,
            week_start DATE,
            type VARCHAR(50),
            quantity NUMERIC(12, 2),
            stock_code VARCHAR(50),
            description VARCHAR(255)
        );
    """))
    
    # Truncate the table to ensure idempotency (clear old records)
    conn.execute(text("TRUNCATE TABLE fact_forecast;"))

# 4. Insert data using pandas to_sql (appending to the truncated table)
print("Uploading forecast data...")
df.to_sql(
    'fact_forecast',
    con=engine,
    if_exists='append',
    index=False,
    method='multi'  # batch insert for performance
)

# 5. Query and print row count to verify load success
with engine.connect() as conn:
    row_count = conn.execute(text("SELECT COUNT(*) FROM fact_forecast;")).scalar()
    print(f"\n[SUCCESS] Loaded forecast data into 'fact_forecast' table.")
    print(f"          CSV rows: {len(df)}")
    print(f"          Table rows: {row_count}")
