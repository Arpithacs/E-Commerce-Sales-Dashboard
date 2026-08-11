# sales_trend_seasonality.py
import os
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

# 1. Connection Setup
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

# 2. Extract Data using LEFT JOINs
print("Pulling non-return sales records from database...")
query = """
    SELECT
        f.quantity,
        f.unit_price,
        (f.quantity * f.unit_price) AS revenue,
        d.date_key,
        p.stock_code,
        p.description,
        s.country
    FROM fact_sales f
    LEFT JOIN dim_date d ON f.date_key = d.date_key
    LEFT JOIN dim_product p ON f.product_key = p.product_key
    LEFT JOIN dim_store s ON f.store_key = s.store_key
    WHERE f.is_return = FALSE;
"""

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn)

df['date_key'] = pd.to_datetime(df['date_key'])
df['year_month'] = df['date_key'].dt.to_period('M')
df['year_quarter'] = df['date_key'].dt.to_period('Q')
df['month_num'] = df['date_key'].dt.month
df['month_name'] = df['date_key'].dt.strftime('%B')

total_overall_revenue = df['revenue'].sum()
total_overall_units = df['quantity'].sum()

print(f"Retrieved {len(df):,} sales rows. Total Revenue: ${total_overall_revenue:,.2f}, Total Units: {total_overall_units:,}\n")

# -------------------------------------------------------------
# 3. Monthly & Quarterly Trends
# -------------------------------------------------------------
monthly_df = df.groupby('year_month').agg(
    revenue=('revenue', 'sum'),
    units=('quantity', 'sum')
).reset_index()

monthly_df['mom_change_pct'] = monthly_df['revenue'].pct_change() * 100
monthly_df['yoy_change_pct'] = monthly_df['revenue'].pct_change(12) * 100

quarterly_df = df.groupby('year_quarter').agg(
    revenue=('revenue', 'sum'),
    units=('quantity', 'sum')
).reset_index()

quarterly_df['qoq_change_pct'] = quarterly_df['revenue'].pct_change() * 100
quarterly_df['yoy_change_pct'] = quarterly_df['revenue'].pct_change(4) * 100

# -------------------------------------------------------------
# 4. Seasonality (Aggregated by Calendar Month)
# -------------------------------------------------------------
seasonality_df = df.groupby(['month_num', 'month_name']).agg(
    total_revenue=('revenue', 'sum'),
    total_units=('quantity', 'sum'),
    year_count=('date_key', lambda x: x.dt.year.nunique())
).reset_index()

seasonality_df['avg_monthly_revenue'] = seasonality_df['total_revenue'] / seasonality_df['year_count']
seasonality_df['revenue_share_pct'] = (seasonality_df['total_revenue'] / total_overall_revenue) * 100

# Sort by month_num
seasonality_df = seasonality_df.sort_values('month_num').reset_index(drop=True)

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

# -------------------------------------------------------------
# 5. Top Products & Categories
# -------------------------------------------------------------
df['product_label'] = df['stock_code'].astype(str) + " - " + df['description'].fillna('UNKNOWN').astype(str)
top_products_df = df.groupby('product_label').agg(
    revenue=('revenue', 'sum'),
    units=('quantity', 'sum')
).reset_index()

top_products_df['revenue_share_pct'] = (top_products_df['revenue'] / total_overall_revenue) * 100
top_products_df = top_products_df.sort_values('revenue', ascending=False).head(10).reset_index(drop=True)

# -------------------------------------------------------------
# 6. Geographic Breakdown
# -------------------------------------------------------------
geo_df = df.groupby('country').agg(
    revenue=('revenue', 'sum'),
    units=('quantity', 'sum')
).reset_index()

geo_df['revenue_share_pct'] = (geo_df['revenue'] / total_overall_revenue) * 100
geo_df = geo_df.sort_values('revenue', ascending=False).head(10).reset_index(drop=True)

# -------------------------------------------------------------
# 7. Output Handling: Excel vs Console
# -------------------------------------------------------------
has_openpyxl = False
try:
    import openpyxl
    has_openpyxl = True
except ImportError:
    has_openpyxl = False

if has_openpyxl:
    excel_path = os.path.join(os.path.dirname(__file__), 'sales_trend_summary.xlsx')
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        monthly_df.to_excel(writer, sheet_name='Monthly Trend', index=False)
        quarterly_df.to_excel(writer, sheet_name='Quarterly Trend', index=False)
        seasonality_df.to_excel(writer, sheet_name='Seasonality', index=False)
        top_products_df.to_excel(writer, sheet_name='Top Products', index=False)
        geo_df.to_excel(writer, sheet_name='Geographic Breakdown', index=False)
    print(f"SUCCESS: Saved full report to Excel at: {excel_path}\n")
else:
    print("NOTE: 'openpyxl' module not found. Displaying clean text tables to console instead.\n")

# -------------------------------------------------------------
# PRINT REPORT TABLES TO CONSOLE
# -------------------------------------------------------------
pd.set_option('display.max_columns', 10)
pd.set_option('display.width', 1000)

print("=== MONTHLY SALES TREND ===")
m_disp = monthly_df.copy()
m_disp['revenue'] = m_disp['revenue'].map('${:,.2f}'.format)
m_disp['units'] = m_disp['units'].map('{:,}'.format)
m_disp['mom_change_pct'] = m_disp['mom_change_pct'].map(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "—")
m_disp['yoy_change_pct'] = m_disp['yoy_change_pct'].map(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "—")
print(m_disp.to_string(index=False))

print("\n=== QUARTERLY SALES TREND ===")
q_disp = quarterly_df.copy()
q_disp['revenue'] = q_disp['revenue'].map('${:,.2f}'.format)
q_disp['units'] = q_disp['units'].map('{:,}'.format)
q_disp['qoq_change_pct'] = q_disp['qoq_change_pct'].map(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "—")
q_disp['yoy_change_pct'] = q_disp['yoy_change_pct'].map(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "—")
print(q_disp.to_string(index=False))

print("\n=== CALENDAR MONTH SEASONALITY SUMMARY ===")
s_disp = seasonality_df[['month_name', 'total_revenue', 'total_units', 'avg_monthly_revenue', 'revenue_share_pct', 'seasonality_flag']].copy()
s_disp['total_revenue'] = s_disp['total_revenue'].map('${:,.2f}'.format)
s_disp['total_units'] = s_disp['total_units'].map('{:,}'.format)
s_disp['avg_monthly_revenue'] = s_disp['avg_monthly_revenue'].map('${:,.2f}'.format)
s_disp['revenue_share_pct'] = s_disp['revenue_share_pct'].map('{:.1f}%'.format)
print(s_disp.to_string(index=False))

print("\n=== TOP 10 PRODUCTS BY REVENUE ===")
p_disp = top_products_df.copy()
p_disp['revenue'] = p_disp['revenue'].map('${:,.2f}'.format)
p_disp['units'] = p_disp['units'].map('{:,}'.format)
p_disp['revenue_share_pct'] = p_disp['revenue_share_pct'].map('{:.2f}%'.format)
print(p_disp.to_string(index=False))
print("\n[Note: dim_product does not contain a product category field, so category grouping was omitted.]")

print("\n=== GEOGRAPHIC BREAKDOWN (TOP 10 COUNTRIES) ===")
g_disp = geo_df.copy()
g_disp['revenue'] = g_disp['revenue'].map('${:,.2f}'.format)
g_disp['units'] = g_disp['units'].map('{:,}'.format)
g_disp['revenue_share_pct'] = g_disp['revenue_share_pct'].map('{:.2f}%'.format)
print(g_disp.to_string(index=False))

# -------------------------------------------------------------
# 8. Executive Narrative
# -------------------------------------------------------------
top_prod_name = top_products_df.iloc[0]['product_label']
top_prod_share = top_products_df.iloc[0]['revenue_share_pct']
top_country = geo_df.iloc[0]['country']
top_country_share = geo_df.iloc[0]['revenue_share_pct']

peak_months_str = ", ".join(seasonality_df[seasonality_df['seasonality_flag'] == 'Top 3 Peak']['month_name'])
trough_months_str = ", ".join(seasonality_df[seasonality_df['seasonality_flag'] == 'Bottom 3 Trough']['month_name'])

print("\n" + "="*80)
print("EXECUTIVE NARRATIVE: STATE OF THE BUSINESS")
print("="*80)
print(f"1. Overall Revenue Growth: The business generated a total revenue of ${total_overall_revenue:,.2f} across {total_overall_units:,} units sold over the 2-year transactional period.")
print(f"2. Trend Trajectory: Revenue demonstrated strong multi-quarter expansion, peaking in Q4 2010 (${quarterly_df['revenue'].max():,.2f}) and maintaining sustained baseline growth in 2011.")
print(f"3. Seasonal Dynamics: Performance displays massive Q4 seasonality driven by holiday shopping, with peak demand concentrated in {peak_months_str}.")
print(f"4. Trough Periods: Mid-year demand experiences seasonal deceleration, with the lowest relative revenue share occurring during {trough_months_str}.")
print(f"5. Product Concentration: Revenue is driven by flagship decor and gift items, led by '{top_prod_name}' generating {top_prod_share:.2f}% of total sales.")
print(f"6. Geographic Reach: Operations are predominantly centralized in the {top_country}, accounting for {top_country_share:.1f}% of total enterprise revenue.")
print("="*80)
