import os
import pandas as pd
from sqlalchemy import create_engine, text

# 1. Database Connection
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
print("Pulling transaction data and computing RFM base...")
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

# 3. Compute Recency
rfm_df['last_transaction_date'] = pd.to_datetime(rfm_df['last_transaction_date'])
# Find the maximum date in the entire dataset
max_dataset_date = rfm_df['last_transaction_date'].max()

# Recency: days between their most recent transaction and the max date in the dataset
rfm_df['recency'] = (max_dataset_date - rfm_df['last_transaction_date']).dt.days

# Convert columns to correct types
rfm_df['frequency'] = rfm_df['frequency'].astype(int)
rfm_df['monetary'] = rfm_df['monetary'].astype(float)

# Select final columns
final_rfm = rfm_df[['customer_key', 'recency', 'frequency', 'monetary']]

# 4. Print Summary Stats
print(f"\nSuccessfully computed RFM for {len(final_rfm)} customers.")
print(f"Dataset Max Date (Anchor for Recency): {max_dataset_date.date()}")
print("\n--- RFM Summary Statistics ---")

# We want min, max, mean for recency, frequency, monetary
stats = final_rfm[['recency', 'frequency', 'monetary']].agg(['min', 'max', 'mean']).T
print(stats.round(2).to_string())

# 5. Calculate RFM Scores (1-5 quintiles)
def get_rfm_score(series, reverse=False):
    # Use qcut dropping tied edges, mapping 0-indexed bins to exactly 1-5 scale
    b = pd.qcut(series, 5, duplicates='drop', labels=False)
    if reverse:
        b = b.max() - b
    # Map back to 1-5
    return (b * (4 / b.max()) + 1).round().astype(int)

# Use .copy() or direct assignment to avoid SettingWithCopyWarning
final_rfm = final_rfm.copy()
final_rfm['R'] = get_rfm_score(final_rfm['recency'], reverse=True)
final_rfm['F'] = get_rfm_score(final_rfm['frequency'])
final_rfm['M'] = get_rfm_score(final_rfm['monetary'])

final_rfm['rfm_score'] = final_rfm['R'].astype(str) + final_rfm['F'].astype(str) + final_rfm['M'].astype(str)

# 6. Map Segments
def assign_segment(row):
    r, f, m = row['R'], row['F'], row['M']
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

# 7. Print Segment Counts
print("\n--- Customer Segments ---")
print(final_rfm['segment'].value_counts().to_string())

# 8. Print Top 10 by Monetary
print("\n--- Top 10 Customers by Monetary Value ---")
top_10_monetary = final_rfm.sort_values('monetary', ascending=False).head(10)
print(top_10_monetary[['customer_key', 'recency', 'frequency', 'monetary', 'rfm_score', 'segment']].to_string(index=False))

# 9. Print Bottom 10 by Recency
print("\n--- Bottom 10 Customers by Recency (Highest Days) ---")
bottom_10_recency = final_rfm.sort_values('recency', ascending=False).head(10)
print(bottom_10_recency[['customer_key', 'recency', 'frequency', 'monetary', 'rfm_score', 'segment']].to_string(index=False))
