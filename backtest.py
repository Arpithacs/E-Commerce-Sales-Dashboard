# backtest.py
# NOTE: This backtest validates the trend-only fallback model (Double Exponential Smoothing) specifically.
# Since the rolling holdout windows require training data below the 104-week seasonal threshold
# (N - offset - test_len = 106 - 2 - 2 = 102 weeks at best), the seasonal Holt-Winters model 
# (which is used for 17 of the 20 products in the main forecast.py execution) is not directly 
# backtested here due to insufficient history. It serves as a validation of the fallback logic.
import os
import pandas as pd
from sqlalchemy import create_engine, text
from statsmodels.tsa.holtwinters import ExponentialSmoothing

# 1. Load connection string from environment
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

# 2. Pull the same data as forecast.py
print("Pulling historical data for backtesting...")
top_n = 20
with engine.connect() as conn:
    # --- get top-20 product keys ---
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
    top_keys = top_products['product_key'].tolist()

    # --- aggregate weekly sales ---
    weekly_sales_sql = f'''
        SELECT
            f.product_key,
            DATE_TRUNC('week', d.date_key)::date AS week_start,
            SUM(f.quantity) AS weekly_qty
        FROM fact_sales f
        JOIN dim_date d ON f.date_key = d.date_key
        WHERE f.product_key = ANY(:keys)
        GROUP BY f.product_key, DATE_TRUNC('week', d.date_key)
        ORDER BY f.product_key, week_start;
    '''
    weekly_sales = pd.read_sql(
        text(weekly_sales_sql),
        conn,
        params={"keys": top_keys}
    )

# Format index
weekly_sales['iso_date'] = pd.to_datetime(weekly_sales['week_start'])
weekly_sales.set_index('iso_date', inplace=True)

# 3. Perform rolling-origin backtest
backtest_results = []
test_len = 2
offsets = [2, 10, 18]

print(f"Running rolling-origin backtest with 2-week holdouts at offsets {offsets}...")

for _, row in top_products.iterrows():
    product_key = row['product_key']
    description = row['description']
    stock_code = row['stock_code']
    
    # Slice the time series for this product
    ts = weekly_sales[weekly_sales['product_key'] == product_key]['weekly_qty']
    ts = ts.asfreq('W-MON').fillna(0)
    
    N = len(ts)
    window_maes = []
    window_naives = []
    window_models = []
    wins = 0
    
    for offset in offsets:
        train_len = N - offset - test_len
        train_ts = ts.iloc[:train_len]
        test_ts = ts.iloc[train_len:train_len + test_len]
        
        # Fit model with the exact same logic (try seasonal, fall back to trend-only)
        try:
            model = ExponentialSmoothing(
                train_ts,
                trend='add',
                seasonal='add',
                seasonal_periods=52,
                damped_trend=True
            )
            fitted = model.fit(optimized=True)
            model_used = "Holt-Winters"
        except ValueError:
            model = ExponentialSmoothing(
                train_ts,
                trend='add',
                seasonal=None,
                damped_trend=True
            )
            fitted = model.fit(optimized=True)
            model_used = "Double Exp (Trend-only)"
            
        # Forecast next test_len weeks and clip to 0
        forecast = fitted.forecast(test_len).clip(lower=0)
        
        # Compute Model MAE
        model_mae = (forecast - test_ts).abs().mean()
        
        # Generate naive baseline forecast: last observed value repeated
        naive_forecast = pd.Series([train_ts.iloc[-1]] * test_len, index=test_ts.index)
        naive_mae = (naive_forecast - test_ts).abs().mean()
        
        window_maes.append(model_mae)
        window_naives.append(naive_mae)
        window_models.append(model_used)
        if model_mae < naive_mae:
            wins += 1
            
    avg_model_mae = sum(window_maes) / len(window_maes)
    avg_naive_mae = sum(window_naives) / len(window_naives)
    
    # Model used representation
    unique_models = list(set(window_models))
    model_display = unique_models[0] if len(unique_models) == 1 else "Mixed"
    
    backtest_results.append({
        'product_key': product_key,
        'stock_code': stock_code,
        'description': description,
        'model_used': model_display,
        'avg_model_mae': round(avg_model_mae, 2),
        'avg_naive_mae': round(avg_naive_mae, 2),
        'beat_wins': wins,
        'beat_total': len(offsets)
    })

# 4. Display results in a formatted table
results_df = pd.DataFrame(backtest_results)
print("\n" + "="*110)
print(f"ROLLING-ORIGIN BACKTEST RESULTS (Averaged across offsets {offsets})")
print("="*110)
print(results_df.to_string(index=False))
print("="*110)

# Save backtest results to CSV
csv_path = os.path.join(os.path.dirname(__file__), 'backtest_results.csv')
results_df.to_csv(csv_path, index=False)
print(f"\n[SUCCESS] Backtest results written to: {csv_path}")

# Summary line
beat_on_avg = sum(1 for r in backtest_results if r['avg_model_mae'] < r['avg_naive_mae'])
print(f"\nSUMMARY: Out of {len(backtest_results)} products, {beat_on_avg} beat the naive baseline on average.")
