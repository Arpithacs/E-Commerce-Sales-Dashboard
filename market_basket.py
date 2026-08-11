# market_basket.py
import os
import re
import itertools
import pandas as pd
from collections import Counter
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

# 2. Extract Line Item Data
print("Pulling transaction line items from fact_sales...")
query = """
    SELECT
        f.invoice,
        p.stock_code,
        p.description
    FROM fact_sales f
    JOIN dim_product p ON f.product_key = p.product_key
    WHERE f.is_return = FALSE;
"""

with engine.connect() as conn:
    df = pd.read_sql(text(query), conn)

print("Processing baskets...")
# Store stock_code and description as tuples
df['stock_code'] = df['stock_code'].astype(str).str.strip()
df['description'] = df['description'].fillna('UNKNOWN').astype(str).str.strip()

# Combine into a tuple for clean hashing and retrieval
df['prod_tuple'] = list(zip(df['stock_code'], df['description']))

# Group by invoice to create distinct product sets
baskets = df.groupby('invoice')['prod_tuple'].apply(lambda x: sorted(list(set(x)), key=lambda item: item[0]))

# Exclude baskets with only 1 item
baskets = baskets[baskets.apply(len) > 1]
total_baskets = len(baskets)

print(f"Analyzed {total_baskets} multi-item baskets.")

# 3. Compute Item and Pair Frequencies
item_counts = Counter()
pair_counts = Counter()

for basket in baskets:
    for item in basket:
        item_counts[item] += 1
    for pair in itertools.combinations(basket, 2):
        pair_counts[pair] += 1

# 4. Helper Function for Variant Pair Detection
COLOR_SIZE_WORDS = {
    'RED', 'BLUE', 'GREEN', 'YELLOW', 'PINK', 'WHITE', 'BLACK', 'PURPLE', 'ORANGE',
    'BROWN', 'GREY', 'GRAY', 'SILVER', 'GOLD', 'BRONZE', 'COPPER', 'ROSE', 'LAVENDER',
    'PEACH', 'SUMMER', 'SPRING', 'AUTUMN', 'WINTER', 'PASTEL', 'IVORY', 'LIGHT', 'DARK',
    'LT', 'DK', 'SMALL', 'MEDIUM', 'LARGE', 'MINI', 'BIG', 'SET', 'S', 'M', 'L', 'S/6', 'S/4', 'S/12',
    'BOYS', 'GIRLS', 'BOY', 'GIRL'
}

def extract_prefix(stock_code):
    # Strip trailing single/double letters (e.g. 37489A -> 37489, 84745B -> 84745, DCGSSBOY -> DCGSS)
    m = re.match(r'^([0-9]{4,5}|[A-Z0-9]+?)[A-Z]{1,2}$', stock_code.upper())
    if m:
        return m.group(1)
    return stock_code.upper()

def is_variant_pair(stock_a, desc_a, stock_b, desc_b):
    prefix_a = extract_prefix(stock_a)
    prefix_b = extract_prefix(stock_b)
    
    # Case 1: Base stock code prefix match (e.g. 37489A and 37489B share 37489)
    if prefix_a == prefix_b and len(prefix_a) >= 4:
        return True
    
    # Case 2: Highly similar descriptions (differing primarily by color/size words)
    words_a = set(re.findall(r'\w+', desc_a.upper()))
    words_b = set(re.findall(r'\w+', desc_b.upper()))
    
    base_words_a = words_a - COLOR_SIZE_WORDS
    base_words_b = words_b - COLOR_SIZE_WORDS
    
    if len(base_words_a) > 0 and base_words_a == base_words_b:
        return True
        
    return False

# 5. Compute Metrics and Categorize Pairs
pairs_data = []
for (item_a, item_b), count in pair_counts.items():
    if count >= 10:  # Minimum support threshold: 10 co-occurrences
        stock_a, desc_a = item_a
        stock_b, desc_b = item_b
        
        supp_ab = count / total_baskets
        supp_a = item_counts[item_a] / total_baskets
        supp_b = item_counts[item_b] / total_baskets
        lift = supp_ab / (supp_a * supp_b)
        
        is_var = is_variant_pair(stock_a, desc_a, stock_b, desc_b)
        category = 'Variant' if is_var else 'Cross-Category'
        
        pairs_data.append({
            'product_a': f"{stock_a} - {desc_a}",
            'product_b': f"{stock_b} - {desc_b}",
            'co_occurrence_count': count,
            'support_a': supp_a,
            'support_b': supp_b,
            'support_ab': supp_ab,
            'lift': lift,
            'pair_type': category
        })

results_df = pd.DataFrame(pairs_data)

# Separate into Variants and Cross-Category DataFrames
variant_df = results_df[results_df['pair_type'] == 'Variant'].sort_values(by='lift', ascending=False).reset_index(drop=True)
cross_df = results_df[results_df['pair_type'] == 'Cross-Category'].sort_values(by='lift', ascending=False).reset_index(drop=True)

# 6. Save to CSVs
variant_csv = os.path.join(os.path.dirname(__file__), 'market_basket_variants.csv')
cross_csv = os.path.join(os.path.dirname(__file__), 'market_basket_cross_category.csv')

variant_df.to_csv(variant_csv, index=False)
cross_df.to_csv(cross_csv, index=False)

print(f"Saved {len(variant_df)} variant pairs to: {variant_csv}")
print(f"Saved {len(cross_df)} cross-category pairs to: {cross_csv}")

# 7. Print Top 15 Pairs for Each Group
pd.set_option('display.max_columns', 10)
pd.set_option('display.width', 1000)

print("\n=== TOP 15 VARIANT PAIRS BY LIFT ===")
top_15_var = variant_df.head(15)[['product_a', 'product_b', 'co_occurrence_count', 'support_ab', 'lift']].copy()
top_15_var['support_ab'] = top_15_var['support_ab'].map('{:.4f}'.format)
top_15_var['lift'] = top_15_var['lift'].map('{:.2f}'.format)
print(top_15_var.to_string(index=False))

print("\n=== TOP 15 CROSS-CATEGORY PAIRS BY LIFT ===")
top_15_cross = cross_df.head(15)[['product_a', 'product_b', 'co_occurrence_count', 'support_ab', 'lift']].copy()
top_15_cross['support_ab'] = top_15_cross['support_ab'].map('{:.4f}'.format)
top_15_cross['lift'] = top_15_cross['lift'].map('{:.2f}'.format)
print(top_15_cross.to_string(index=False))
