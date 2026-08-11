# forecast.py
import os
import pandas as pd
from sqlalchemy import create_engine, text
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# --------------------------------------------------------------
# 1. Load the connection string from the environment
# --------------------------------------------------------------
db_url = os.getenv("SUPABASE_DB_URL")
if not db_url:
    raise EnvironmentError(
        "Environment variable SUPABASE_DB_URL not found. "
        "Make sure you set it as described in the previous step."
    )

# Create a SQLAlchemy engine (no password hard‑coded)
engine = create_engine(db_url)

# --------------------------------------------------------------
# 2. Pull weekly aggregated sales for the top‑20 products
# --------------------------------------------------------------
#   * First we compute total quantity per product to rank them.
#   * Then we aggregate weekly sales (sum of quantity) for those top products.
#   * We use ISO week numbers (Monday‑based) for consistency.
# --------------------------------------------------------------

top_n = 20

with engine.connect() as conn:
    # --- get top‑20 product keys -------------------------------------------------
    top_products_sql = f'''
        SELECT
            p.product_key,
            p.stock_code,
            p.description,
            SUM(f.quantity) AS total_quantity
        FROM fact_sales f
        JOIN dim_product p ON f.product_key = p.product_key
        GROUP BY p.product_key, p.stock_code, p.description
        ORDER BY total_quantity DESC
        LIMIT {top_n};
    '''
    top_products = pd.read_sql(text(top_products_sql), conn)
    top_keys = top_products['product_key'].tolist()  # list converts to PostgreSQL array for ANY()

    # --- aggregate weekly sales for those products --------------------------------
    weekly_sales_sql = f'''
        SELECT
            f.product_key,
            DATE_TRUNC('week', d.date_key)::date AS week_start,
            SUM(f.quantity) AS weekly_qty
        FROM fact_sales f
        JOIN dim_date d ON f.date_key = d.date_key
        WHERE f.product_key = ANY(:keys)      -- filter to top products
        GROUP BY f.product_key, DATE_TRUNC('week', d.date_key)
        ORDER BY f.product_key, week_start;
    '''
    weekly_sales = pd.read_sql(
        text(weekly_sales_sql),
        conn,
        params={"keys": top_keys}
    )

# Create a proper datetime index (use the week start date directly)
weekly_sales['iso_date'] = pd.to_datetime(weekly_sales['week_start'])
weekly_sales.set_index('iso_date', inplace=True)



# --------------------------------------------------------------
# 3. Fit Holt‑Winters per product and forecast next 8 weeks
# --------------------------------------------------------------
forecast_horizon = 8  # weeks
results = []          # will hold dictionaries for CSV output

for product_key in top_keys:
    # Slice the time series for this product
    ts = weekly_sales[weekly_sales['product_key'] == product_key]['weekly_qty']
    ts = ts.asfreq('W-MON')                # ensure regular weekly frequency
    ts = ts.fillna(0)                      # missing weeks -> 0 sales

    # Fit the model:
    # Try full Holt-Winters with weekly seasonality first.
    # If the history is too short (<104 weeks), fall back to trend-only model (Double Exponential Smoothing).
    try:
        model = ExponentialSmoothing(
            ts,
            trend='add',
            seasonal='add',
            seasonal_periods=52,
            damped_trend=True
        )
        fitted = model.fit(optimized=True)
    except ValueError:
        model = ExponentialSmoothing(
            ts,
            trend='add',
            seasonal=None,
            damped_trend=True
        )
        fitted = model.fit(optimized=True)

    # Forecast next 8 weeks
    forecast = fitted.forecast(forecast_horizon).clip(lower=0)

    # ------------------------------------------------------------------
    # Append historical actuals (label = 'actual')
    # ------------------------------------------------------------------
    for date, qty in ts.items():
        results.append({
            'product_key': product_key,
            'week_start': date.date(),
            'type': 'actual',
            'quantity': int(qty)
        })

    # ------------------------------------------------------------------
    # Append forecasts (label = 'forecast')
    # ------------------------------------------------------------------
    for date, qty in forecast.items():
        results.append({
            'product_key': product_key,
            'week_start': date.date(),
            'type': 'forecast',
            'quantity': float(round(qty, 2))
        })

# --------------------------------------------------------------
# 4. Save everything to CSV
# --------------------------------------------------------------
output_df = pd.DataFrame(results)

# Add helpful columns from dim_product (stock_code, description) for Power BI
output_df = output_df.merge(
    top_products[['product_key', 'stock_code', 'description']],
    on='product_key',
    how='left'
)

csv_path = os.path.join(
    os.path.dirname(__file__),   # same folder as this script
    'forecast_results.csv'
)

output_df.to_csv(csv_path, index=False)

print(f"\n[SUCCESS] Forecast CSV written to: {csv_path}")
print("   You can now import this file into Power BI or load it back to Postgres.")
