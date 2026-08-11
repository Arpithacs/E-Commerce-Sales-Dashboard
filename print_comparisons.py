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
    print("--- Top 5 High-Frequency Customer Validation ---")
    rows = conn.execute(text('''
        SELECT c.customer_id, COUNT(DISTINCT f.invoice) AS distinct_invoices_fact
        FROM fact_sales f
        JOIN dim_customer c ON f.customer_key = c.customer_key
        WHERE c.customer_id IN ('14911', '15311', '12748', '17841', '14606')
        GROUP BY c.customer_id
        ORDER BY distinct_invoices_fact DESC
    ''')).fetchall()

    for r in rows:
        c_id, distinct_fact = r
        c_id_str = str(c_id) + ".0"
        inv_count_staging = conn.execute(
            text('SELECT COUNT(DISTINCT "Invoice") FROM staging_transactions WHERE "Customer ID" = :c'),
            {'c': c_id_str}
        ).scalar()
        print(f'customer_id={c_id}  fact_invoice_count={distinct_fact}  staging_invoice_count={inv_count_staging}')
