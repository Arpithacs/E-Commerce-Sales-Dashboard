# load_rfm.py
import os
import pandas as pd
from sqlalchemy import create_engine, text

# 1. Load the connection string from environment
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

# 2. Extract Data & Compute RFM base
print("Pulling transaction data and computing RFM metrics...")
query = """
    SELECT
        f.customer_key,
        MAX(d.date_key) AS last_transaction_date,
        COUNT(DISTINCT f.invoice) AS frequency,
        SUM(f.quantity * f.unit_price) AS monetary
    FROM fact_sales f
    JOIN dim_customer c ON f.customer_key = c.customer_key
    JOIN dim_date d ON f.date_key = d.date_key
    WHERE f.is_return = FALSE
    GROUP BY f.customer_key;
"""

with engine.connect() as conn:
    rfm_df = pd.read_sql(text(query), conn)

# 3. Compute Recency and prepare dataset
rfm_df['last_transaction_date'] = pd.to_datetime(rfm_df['last_transaction_date'])
max_dataset_date = rfm_df['last_transaction_date'].max()
rfm_df['recency'] = (max_dataset_date - rfm_df['last_transaction_date']).dt.days
rfm_df['frequency'] = rfm_df['frequency'].astype(int)
rfm_df['monetary'] = rfm_df['monetary'].astype(float)

final_rfm = rfm_df[['customer_key', 'recency', 'frequency', 'monetary']].copy()

# 4. Calculate Quintile Scores
def get_rfm_score(series, reverse=False):
    b = pd.qcut(series, 5, duplicates='drop', labels=False)
    if reverse:
        b = b.max() - b
    return (b * (4 / b.max()) + 1).round().astype(int)

final_rfm['r_score'] = get_rfm_score(final_rfm['recency'], reverse=True)
final_rfm['f_score'] = get_rfm_score(final_rfm['frequency'])
final_rfm['m_score'] = get_rfm_score(final_rfm['monetary'])

final_rfm['rfm_score'] = (
    final_rfm['r_score'].astype(str) +
    final_rfm['f_score'].astype(str) +
    final_rfm['m_score'].astype(str)
)

# 5. Map Segments
def assign_segment(row):
    r, f, m = row['r_score'], row['f_score'], row['m_score']
    if r >= 4 and f >= 4 and m >= 4:
        return 'Champions'
    elif r <= 2 and f >= 4 and m >= 4:
        return 'At Risk'
    elif r >= 2 and r <= 4 and f >= 3 and m >= 3:
        return 'Loyal Customers'
    elif r == 1 and m >= 4 and f <= 3:
        return "Can't Lose Them"
    elif r >= 4 and f <= 2:
        return 'New Customers'
    elif r <= 2 and f <= 2 and m <= 2:
        return 'Lost'
    else:
        return 'Need Attention / Potential Loyalist'

final_rfm['segment'] = final_rfm.apply(assign_segment, axis=1)

# 6. Database Setup and Load
print("Preparing 'fact_rfm' table in database...")
with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS fact_rfm (
            customer_key INTEGER,
            recency INTEGER,
            frequency INTEGER,
            monetary NUMERIC(12, 2),
            r_score INTEGER,
            f_score INTEGER,
            m_score INTEGER,
            rfm_score VARCHAR(10),
            segment VARCHAR(100)
        );
    """))
    conn.execute(text("TRUNCATE TABLE fact_rfm;"))

print("Uploading RFM data to 'fact_rfm'...")
final_rfm.to_sql(
    'fact_rfm',
    con=engine,
    if_exists='append',
    index=False,
    method='multi'
)

# 7. Verification
with engine.connect() as conn:
    row_count = conn.execute(text("SELECT COUNT(*) FROM fact_rfm;")).scalar()
    print(f"\n[SUCCESS] Loaded RFM data into 'fact_rfm' table.")
    print(f"          Processed rows: {len(final_rfm)}")
    print(f"          Table rows:     {row_count}")
