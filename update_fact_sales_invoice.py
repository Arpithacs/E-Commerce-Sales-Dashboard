import os
from sqlalchemy import create_engine, text

db_url = os.getenv('SUPABASE_DB_URL')
if not db_url:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        db_url, _ = winreg.QueryValueEx(key, "SUPABASE_DB_URL")
    except Exception:
        pass

engine = create_engine(db_url)
with engine.connect() as conn:
    print("1. Adding invoice column to fact_sales if it doesn't exist...")
    conn.execute(text("ALTER TABLE fact_sales ADD COLUMN IF NOT EXISTS invoice VARCHAR(50);"))
    
    print("2. Populating invoice column from staging_transactions...")
    update_query = """
        WITH staging_invoices AS (
            SELECT 
                c.customer_key,
                p.product_key,
                s."InvoiceDate"::date AS date_key,
                (s."Quantity" < 0) AS is_return,
                MAX(s."Invoice") AS invoice
            FROM staging_transactions s
            JOIN dim_customer c ON s."Customer ID" = (c.customer_id::text || '.0')
            JOIN dim_product p ON s."StockCode" = p.stock_code
            GROUP BY c.customer_key, p.product_key, s."InvoiceDate"::date, (s."Quantity" < 0)
        )
        UPDATE fact_sales f
        SET invoice = si.invoice
        FROM staging_invoices si
        WHERE f.customer_key = si.customer_key
          AND f.product_key = si.product_key
          AND f.date_key = si.date_key
          AND f.is_return = si.is_return
          AND f.customer_key IS NOT NULL;
    """
    res = conn.execute(text(update_query))
    conn.commit()
    print(f"Updated {res.rowcount} rows in fact_sales.")
