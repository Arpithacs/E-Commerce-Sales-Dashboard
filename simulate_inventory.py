# simulate_inventory.py
import os
import io
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

# 1. Load the connection string from environment
db_url = os.getenv("SUPABASE_DB_URL")
if not db_url:
    # Try reading from registry user profile if not in current environment
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        db_url, _ = winreg.QueryValueEx(key, "SUPABASE_DB_URL")
    except Exception:
        pass

if not db_url:
    raise EnvironmentError("SUPABASE_DB_URL environment variable not found.")

engine = create_engine(db_url)

# 2. Pull weekly net units per product/store combo
print("Pulling weekly sales data from database...")
query = """
    SELECT
        f.product_key,
        f.store_key,
        DATE_TRUNC('week', d.date_key)::date AS week_start,
        SUM(f.quantity) AS net_units
    FROM fact_sales f
    JOIN dim_date d ON f.date_key = d.date_key
    GROUP BY f.product_key, f.store_key, DATE_TRUNC('week', d.date_key)::date;
"""

with engine.connect() as conn:
    raw_sales = pd.read_sql(text(query), conn)

print(f"Retrieved {len(raw_sales)} raw weekly sales records.")

# Ensure correct data types
raw_sales['week_start'] = pd.to_datetime(raw_sales['week_start'])
raw_sales['net_units'] = raw_sales['net_units'].astype(int)

# 3. Exclude combos where total sales across the whole period is <= 0
print("Identifying valid product/store combinations...")
combo_totals = raw_sales.groupby(['product_key', 'store_key'])['net_units'].sum()
valid_combos = combo_totals[combo_totals > 0].index
num_excluded = len(combo_totals) - len(valid_combos)
print(f"Excluded {num_excluded} product/store combinations with 0 or negative total sales.")

# Filter raw sales to only valid combinations
raw_sales = raw_sales[raw_sales.set_index(['product_key', 'store_key']).index.isin(valid_combos)]

# Get all unique weeks to reconstruct complete timelines
all_weeks = pd.Series(raw_sales['week_start'].unique()).sort_values().reset_index(drop=True)
all_weeks = pd.to_datetime(all_weeks)
w = len(all_weeks)
print(f"Reconstructing timeline for {len(valid_combos)} combos across {w} weeks...")

# Create a complete cross-join skeleton
combo_df = pd.DataFrame(index=valid_combos).reset_index()
combo_df['key'] = 1
weeks_df = pd.DataFrame({'week_start': all_weeks, 'key': 1})
full_df = pd.merge(combo_df, weeks_df, on='key').drop('key', axis=1)

# Merge back the sales numbers and fill missing weeks with 0
full_df = pd.merge(full_df, raw_sales, on=['product_key', 'store_key', 'week_start'], how='left')
full_df['net_units'] = full_df['net_units'].fillna(0).astype(int)

# Sort chronologically for each combo
full_df = full_df.sort_values(by=['product_key', 'store_key', 'week_start']).reset_index(drop=True)

# 4. Run week-by-week simulation using optimized numpy arrays
print("Running inventory simulation...")
prod_keys = full_df['product_key'].values
store_keys = full_df['store_key'].values
net_units = full_df['net_units'].values

n_rows = len(full_df)
stock_on_hand = np.zeros(n_rows, dtype=np.int32)
reorder_points = np.zeros(n_rows, dtype=np.int32)

n_combos = len(valid_combos)

for c in range(n_combos):
    start_idx = c * w
    end_idx = start_idx + w
    
    combo_units = net_units[start_idx:end_idx]
    avg_weekly_units = np.mean(combo_units)
    
    # Calculate parameters
    stock_on_hand_init = int(round(avg_weekly_units * 6))
    rp = int(round(avg_weekly_units * 2))
    restock_qty = int(round(avg_weekly_units * 4))
    
    # Week 1 initialization
    stock_on_hand[start_idx] = stock_on_hand_init
    reorder_points[start_idx:end_idx] = rp
    
    current_stock = stock_on_hand_init
    
    # Subsequent weeks simulation
    for i in range(1, w):
        idx = start_idx + i
        # Deplete stock
        current_stock = max(current_stock - combo_units[i], 0)
        # Check reorder
        if current_stock < rp:
            current_stock += restock_qty
        stock_on_hand[idx] = current_stock

# Add results back to DataFrame
full_df['stock_on_hand'] = stock_on_hand
full_df['reorder_point'] = reorder_points
full_df['date_key'] = full_df['week_start'].dt.date

# 5. Write results to new table fact_inventory_v2
print("Preparing database table 'fact_inventory_v2'...")
create_table_sql = """
CREATE TABLE IF NOT EXISTS fact_inventory_v2 (
    product_key INTEGER,
    store_key INTEGER,
    date_key DATE,
    stock_on_hand INTEGER,
    reorder_point INTEGER
);
"""

with engine.begin() as conn:
    conn.execute(text(create_table_sql))
    conn.execute(text("TRUNCATE TABLE fact_inventory_v2;"))

print("Uploading simulated inventory records to Supabase (using fast bulk COPY)...")
upload_cols = ['product_key', 'store_key', 'date_key', 'stock_on_hand', 'reorder_point']

# Use psycopg2 copy_expert for much faster upload
import psycopg2
raw_conn = engine.raw_connection()
try:
    with raw_conn.cursor() as cursor:
        cursor.execute("SET statement_timeout = 0;")
        
        # Chunk the COPY operation to avoid single massive statements
        chunk_size = 500000
        for start_row in range(0, len(full_df), chunk_size):
            end_row = min(start_row + chunk_size, len(full_df))
            chunk_df = full_df[upload_cols].iloc[start_row:end_row]
            
            output = io.StringIO()
            chunk_df.to_csv(output, sep='\t', header=False, index=False)
            output.seek(0)
            
            print(f"Uploading rows {start_row} to {end_row}...")
            cursor.copy_expert("COPY fact_inventory_v2 (product_key, store_key, date_key, stock_on_hand, reorder_point) FROM STDIN WITH (FORMAT csv, DELIMITER '\t')", output)
            
    raw_conn.commit()
finally:
    raw_conn.close()

# 6. Verification & Output
print("\n" + "="*80)
print("INVENTORY SIMULATION & LOAD COMPLETE")
print("="*80)
print(f"Total rows written: {len(full_df)}")
print(f"Combinations excluded (0 sales): {num_excluded}")

# Print sample product_key=1, store_key=3
sample = full_df[(full_df['product_key'] == 1) & (full_df['store_key'] == 3)].sort_values('week_start')
print(f"\nTimeline Sample for Product 1 at Store 3 (Avg weekly units: {sample['net_units'].mean():.2f}):")
print(sample[['date_key', 'net_units', 'stock_on_hand', 'reorder_point']].to_string(index=False))
print("="*80)
