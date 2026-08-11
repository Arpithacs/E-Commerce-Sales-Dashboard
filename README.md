# E-Commerce Sales Analytics & Demand Forecasting Pipeline

A comprehensive, production-grade retail analytics pipeline built on the **Online Retail II** dataset. This project processes raw transactional data into a PostgreSQL analytical data warehouse (hosted on Supabase) and runs modular analytics covering **RFM Customer Segmentation**, **Monthly Cohort Retention Analysis**, **Market Basket Association Analysis**, **Sales Trend & Seasonality Analysis**, **Holt-Winters Demand Forecasting**, and **Safety Stock Inventory Simulation**. The populated warehouse tables (`fact_rfm`, `fact_forecast`, `fact_inventory`, `fact_sales`) serve as the centralized data layer for Power BI dashboard visualizations (currently in progress).

---

## 🛠️ Tech Stack

* **Language**: Python 3.9+
* **Data Processing**: `pandas`, `NumPy`
* **Database & Warehouse**: PostgreSQL (hosted on Supabase cloud database)
* **ORM & DB Access**: `SQLAlchemy`, `psycopg2`
* **Statistical Modeling & Forecasting**: `statsmodels` (Holt-Winters Exponential Smoothing)
* **Visualization Layer**: Power BI (Integration work in progress)

---

## 📁 Pipeline Components & Architecture

The repository is organized into four core analytical domains:

### 1. RFM Customer Segmentation
* **`rfm_segmentation.py`**: Queries non-return sales data from `fact_sales`, computes Recency, Frequency (based on direct invoice counts), and Monetary metrics for 5,269 customers, applies 1–5 quintile scoring, and assigns customers into 7 distinct behavioral segments.
* **`load_rfm.py`**: Idempotently creates and populates the `fact_rfm` analytical table in Supabase.
* **`update_fact_sales_invoice.py`**: Database migration utility that backfilled the 1:1 `invoice` column on `fact_sales` directly from `staging_transactions`.
* **`print_comparisons.py`**: Data-integrity verification tool comparing raw staging invoice counts against `fact_sales`.

### 2. Cohort & Retention Analysis
* **`cohort_analysis.py`**: Performs multi-month cohort tracking across 25 consecutive cohorts (Dec 2009 – Dec 2011). Computes retention percentage matrices and investigates high-frequency wholesale account concentration across mature cohorts.

### 3. Market Basket Association Analysis
* **`market_basket.py`**: Analyzes 36,308 multi-item baskets to compute pairwise Support, Confidence, and Lift metrics. Automatically categorizes pairs into:
  * **Variant Pairs**: Color, size, or fragrance variations (saved to `market_basket_variants.csv`).
  * **Cross-Category Pairs**: Complementary product bundles (saved to `market_basket_cross_category.csv`).

### 4. Sales Demand Forecasting & Inventory Simulation
* **`sales_trend_seasonality.py`**: Produces a monthly/quarterly sales trend and seasonality summary, top products, and geographic revenue breakdown.
* **`forecast.py`**: Fits Holt-Winters Exponential Smoothing models to weekly demand for top-20 revenue products, projecting future sales volume.
* **`backtest.py`**: Evaluates forecasting accuracy across historical hold-out periods using MAPE and RMSE.
* **`load_forecast.py`**: Idempotently populates the `fact_forecast` analytical table in Supabase.
* **`simulate_inventory.py`**: Simulates safety stock levels, reorder thresholds, and stockout risks based on demand volatility, populating the `fact_inventory` table in Supabase.
* **`forecast_handoff.md`**: Technical handoff documentation covering model parameters and Power BI schema integration.

---

## 💡 Key Technical Insights & Methodology Notes

1. **Frequency Proxy Correction**:
   * *Problem*: Early schema iterations used `COUNT(DISTINCT date_key)` as a frequency proxy, which undercounted customer orders by 1.4x–2.0x.
   * *Fix*: The data warehouse schema was updated by adding an explicit `invoice` column to `fact_sales`, enabling true `COUNT(DISTINCT invoice)` metrics.

2. **Skewed Bin Handling (`pd.qcut` + `duplicates='drop'`)**:
   * *Problem*: Over 20% of customers have only 1 invoice, causing standard quintile binning to collapse into 4 raw bins (`0, 1, 2, 3`).
   * *Fix*: Implemented a linear scaling function `(b * (4 / b.max()) + 1).round()` to map raw bins to `[1, 2, 4, 5]`, preserving mathematical rigor without bin-edge collision errors.

3. **`assign_segment()` Precedence Optimization**:
   * *Problem*: Customers with `R=2, F>=4, M>=4` (high historical value but declining recency) overlapped between `Loyal Customers` and `At Risk`.
   * *Fix*: Reordered the `if-elif` chain to evaluate `At Risk` before `Loyal Customers`, correctly reassigning 133 churning high-value accounts into `At Risk` (adjusting `At Risk` to 182 and refining `Loyal Customers` to 491).

4. **Timeline Baseline & Seasonality**:
   * *Note*: The dataset begins in December 2009, so the 2009 Q4 and December 2009 figures represent a partial period; year-over-year comparisons are only meaningful from December 2010 onward.

---

## 🔒 Database Security Notes

All database access in this project uses a direct PostgreSQL connection string (`SUPABASE_DB_URL`), authenticated by username/password, not Supabase's REST API or anon/public key. Because of this, Supabase's Row Level Security (RLS) — which governs access through the REST API layer — does not apply to how this project connects to the database and was intentionally left unconfigured. Database credentials are never committed to this repository; they are loaded exclusively from the `SUPABASE_DB_URL` environment variable at runtime.

---

## 🚀 Setup & Execution Guide

### 1. Environment Configuration
Ensure Python 3.9+ is installed along with the required dependencies:

```bash
pip install pandas sqlalchemy psycopg2 statsmodels
```

Set the database connection string environment variable (`SUPABASE_DB_URL`):

* **PowerShell (Windows)**:
  ```powershell
  $env:SUPABASE_DB_URL="postgresql://user:password@host:5432/dbname"
  ```
* **Bash (Linux/macOS)**:
  ```bash
  export SUPABASE_DB_URL="postgresql://user:password@host:5432/dbname"
  ```

### 2. Running Pipelines

* **Run Sales Trend & Seasonality Analysis**:
  ```bash
  python sales_trend_seasonality.py
  ```

* **Run RFM Customer Segmentation & Load Warehouse**:
  ```bash
  python rfm_segmentation.py
  python load_rfm.py
  ```

* **Run Cohort Retention Analysis**:
  ```bash
  python cohort_analysis.py
  ```

* **Run Market Basket Analysis**:
  ```bash
  python market_basket.py
  ```

* **Run Sales Forecasting & Load Warehouse**:
  ```bash
  python forecast.py
  python load_forecast.py
  ```

* **Run Inventory Simulation & Load Warehouse**:
  ```bash
  python simulate_inventory.py
  ```
