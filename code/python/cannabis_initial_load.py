"""
Cannabis Retail — Initial Star-Schema Load to BigQuery
======================================================
Generates ~1 year of synthetic transactions (2025-06-30 -> yesterday) and loads
them to BigQuery as a star schema:
 
    fact_sales   (one row per line item; keys + measures only)
    dim_customer (living roster: demographics, segment, home_store_id,
                  total_visits, first_visit_date, last_visit_date)
    dim_product  (product_id, product_name, category, cost, price, margin)
    dim_store    (store_id, store_name, revenue_weight)
    dim_date     (date + calendar attributes)
 
Run this ONCE to seed the warehouse. After that, cannabis_nightly.py appends one
day at a time. dim_customer carries total_visits / last_visit_date precisely so
the nightly job knows who exists, what segment they are, and (for one_and_done)
who must never return.
 
Prereqs:
    pip install pandas numpy pandas-gbq google-cloud-bigquery
    Authenticate via Application Default Credentials (gcloud auth application-default login)
    Set BQ_PROJECT / BQ_DATASET in cannabis_common.py
"""
 
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta
 
import cannabis_common as cc
 
# ── Parameters ──────────────────────────────────────────────────────────────
START_DATE         = date(2025, 7, 1)
END_DATE           = date.today() - timedelta(days=1)   # always yesterday
CUSTOMER_POOL_SIZE = 17000
RANDOM_SEED        = None   # set an int for reproducibility
 
if RANDOM_SEED is not None:
    np.random.seed(RANDOM_SEED)
 
 
# ══════════════════════════════════════════════════════════════════════════════
# DIMENSIONS: store, product, date
# ══════════════════════════════════════════════════════════════════════════════
 
def build_dim_store() -> pd.DataFrame:
    return pd.DataFrame(
        [{'store_id': sid, 'store_name': name, 'revenue_weight': w}
         for sid, name, w in cc.STORE_DATA]
    )
 
def build_dim_product(catalog: list) -> pd.DataFrame:
    return pd.DataFrame(catalog)[
        ['product_id', 'product_name', 'category', 'cost', 'price', 'margin']
    ]
 
def build_dim_date(start: date, end: date) -> pd.DataFrame:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows = []
    for d in days:
        rows.append({
            'date':        d,
            'year':        d.year,
            'quarter':     (d.month - 1) // 3 + 1,
            'month':       d.month,
            'month_name':  d.strftime('%B'),
            'day':         d.day,
            'day_of_week': d.weekday(),               # 0 = Monday
            'day_name':    d.strftime('%A'),
            'is_weekend':  d.weekday() >= 5,
        })
    return pd.DataFrame(rows)
 
 
# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER POOL + VISIT-DATE ASSIGNMENT (batch model)
# ══════════════════════════════════════════════════════════════════════════════
 
def build_customer_pool(n: int) -> pd.DataFrame:
    genders     = np.random.choice(cc.GENDERS,      size=n, p=cc.GENDER_WEIGHTS)
    generations = np.random.choice(cc.GENERATIONS,  size=n, p=cc.GENERATION_WEIGHTS)
    segments    = np.random.choice(cc.CUSTOMER_SEGMENTS, size=n, p=cc.CUSTOMER_SEGMENT_WEIGHTS)
    home_stores = np.random.choice(cc.STORE_IDS,    size=n, p=cc.STORE_WEIGHTS)
    return pd.DataFrame({
        'customer_id':   np.arange(1, n + 1),
        'gender':        genders,
        'generation':    generations,
        'segment':       segments,
        'home_store_id': home_stores,
    })
 
 
def assign_visit_dates(gender, generation, segment, all_dates, dow_probs) -> list:
    """Batch visit-date assignment, consistent with daily_visit_prob over the window.
 
    Takes plain values (not an object) so there's no attribute-access indirection
    that could silently misbehave. one_and_done always gets exactly one visit;
    everyone else gets at least one.
    """
    if segment == 'one_and_done':
        return list(np.random.choice(all_dates, size=1, p=dow_probs))
    annual = cc.annual_visits(gender, generation, segment)
    total_days = len(all_dates)
    num_visits = round(annual * (total_days / 365))
    num_visits = max(1, min(num_visits, total_days))
    return list(np.random.choice(all_dates, size=num_visits, replace=False, p=dow_probs))
 
 
# ══════════════════════════════════════════════════════════════════════════════
# SIMULATION
# ══════════════════════════════════════════════════════════════════════════════
 
def simulate():
    print("Building dimensions...")
    catalog       = cc.build_catalog()
    dim_store     = build_dim_store()
    dim_product   = build_dim_product(catalog)
    dim_date      = build_dim_date(START_DATE, END_DATE)
    catalog_by_cat = {c: [p for p in catalog if p['category'] == c] for c in cc.CATEGORIES}
 
    print("Building customer pool...")
    customers = build_customer_pool(CUSTOMER_POOL_SIZE)
 
    print("Assigning visit dates...")
    all_dates  = [START_DATE + timedelta(days=i) for i in range((END_DATE - START_DATE).days + 1)]
    dow_probs  = np.array([cc.DAY_OF_WEEK_WEIGHTS[d.weekday()] for d in all_dates], dtype=float)
    dow_probs /= dow_probs.sum()
 
    visits_by_date = {}
    cust_records   = customers.to_dict('records')
    for rec in cust_records:
        rec['_visit_dates'] = assign_visit_dates(
            rec['gender'], rec['generation'], rec['segment'], all_dates, dow_probs)
        for vd in rec['_visit_dates']:
            visits_by_date.setdefault(vd, []).append(rec)
 
    total_visits = sum(len(v) for v in visits_by_date.values())
    print(f"  {total_visits:,} visits across {len(visits_by_date):,} days")
 
    print(f"Simulating {START_DATE} -> {END_DATE}...")
    rows           = []
    transaction_id = 1
    # Track per-customer aggregates for dim_customer state
    cust_visit_count = {rec['customer_id']: 0 for rec in cust_records}
    cust_first       = {}
    cust_last        = {}
 
    current = START_DATE
    while current <= END_DATE:
        base_dt = datetime(current.year, current.month, current.day)
        for rec in visits_by_date.get(current, []):
            cid           = rec['customer_id']
            home          = int(rec['home_store_id'])
            store_id      = cc.sample_store(home)
            hour          = cc.sample_hour(current.weekday())
            visit_dt      = base_dt.replace(hour=hour,
                                            minute=int(np.random.randint(0, 60)),
                                            second=int(np.random.randint(0, 60)))
            basket_size   = cc.sample_basket_size(rec['generation'])
 
            cust_visit_count[cid] += 1
            cust_first.setdefault(cid, current)
            cust_last[cid] = current
 
            for line_id in range(1, basket_size + 1):
                category = cc.sample_category(rec['generation'])
                product  = catalog_by_cat[category][np.random.randint(len(catalog_by_cat[category]))]
                quantity = cc.sample_quantity()
                rows.append({
                    'transaction_id': transaction_id,
                    'line_id':        line_id,
                    'date':           current,
                    'datetime':       visit_dt,
                    'store_id':       store_id,
                    'customer_id':    cid,
                    'product_id':     product['product_id'],
                    'quantity':       quantity,
                    'unit_price':     product['price'],
                    'unit_cost':      product['cost'],
                    'discount':       0.0,
                    'line_total':     round(product['price'] * quantity, 2),
                    'line_cost':      round(product['cost']  * quantity, 2),
                })
            transaction_id += 1
        current += timedelta(days=1)
 
    fact_sales = pd.DataFrame(rows)
 
    # dim_customer carries the state the nightly job reads back
    customers['total_visits']     = customers['customer_id'].map(cust_visit_count).fillna(0).astype(int)
    customers['first_visit_date'] = customers['customer_id'].map(cust_first)
    customers['last_visit_date']  = customers['customer_id'].map(cust_last)
    # Drop customers who never visited (rare, but keeps roster clean)
    dim_customer = customers[customers['total_visits'] > 0].drop(columns=[]).reset_index(drop=True)
 
    return fact_sales, dim_customer, dim_product, dim_store, dim_date
 
 
# ══════════════════════════════════════════════════════════════════════════════
# LOAD TO BIGQUERY
# ══════════════════════════════════════════════════════════════════════════════
 
def _normalize_dates(fact_sales, dim_customer, dim_date):
    """Force date/datetime columns to proper pandas dtypes BEFORE upload.
 
    The original object-dtype `date` columns let the uploader infer types
    inconsistently — which is what produced integer date serials and, worse,
    fact rows that silently failed to land. Converting to datetime64 up front
    makes the upload deterministic.
    """
    fact_sales = fact_sales.copy()
    dim_customer = dim_customer.copy()
    dim_date = dim_date.copy()
 
    fact_sales['date']     = pd.to_datetime(fact_sales['date'])
    fact_sales['datetime'] = pd.to_datetime(fact_sales['datetime'])
    dim_customer['first_visit_date'] = pd.to_datetime(dim_customer['first_visit_date'])
    dim_customer['last_visit_date']  = pd.to_datetime(dim_customer['last_visit_date'])
    dim_date['date']       = pd.to_datetime(dim_date['date'])
    return fact_sales, dim_customer, dim_date
 
 
# Explicit BigQuery schemas — no type inference, no surprises.
def _schemas():
    from google.cloud import bigquery as bq
    return {
        cc.TBL_DIM_STORE: [
            bq.SchemaField('store_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('store_name', 'STRING', mode='REQUIRED'),
            bq.SchemaField('revenue_weight', 'FLOAT64'),
        ],
        cc.TBL_DIM_PRODUCT: [
            bq.SchemaField('product_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('product_name', 'STRING', mode='REQUIRED'),
            bq.SchemaField('category', 'STRING', mode='REQUIRED'),
            bq.SchemaField('cost', 'FLOAT64'),
            bq.SchemaField('price', 'FLOAT64'),
            bq.SchemaField('margin', 'FLOAT64'),
        ],
        cc.TBL_DIM_DATE: [
            bq.SchemaField('date', 'DATE', mode='REQUIRED'),
            bq.SchemaField('year', 'INT64'),
            bq.SchemaField('quarter', 'INT64'),
            bq.SchemaField('month', 'INT64'),
            bq.SchemaField('month_name', 'STRING'),
            bq.SchemaField('day', 'INT64'),
            bq.SchemaField('day_of_week', 'INT64'),
            bq.SchemaField('day_name', 'STRING'),
            bq.SchemaField('is_weekend', 'BOOL'),
        ],
        cc.TBL_DIM_CUSTOMER: [
            bq.SchemaField('customer_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('gender', 'STRING'),
            bq.SchemaField('generation', 'STRING'),
            bq.SchemaField('segment', 'STRING'),
            bq.SchemaField('home_store_id', 'INT64'),
            bq.SchemaField('total_visits', 'INT64'),
            bq.SchemaField('first_visit_date', 'DATE'),
            bq.SchemaField('last_visit_date', 'DATE'),
        ],
        cc.TBL_FACT_SALES: [
            bq.SchemaField('transaction_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('line_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('date', 'DATE', mode='REQUIRED'),
            bq.SchemaField('datetime', 'DATETIME', mode='REQUIRED'),
            bq.SchemaField('store_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('customer_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('product_id', 'INT64', mode='REQUIRED'),
            bq.SchemaField('quantity', 'INT64'),
            bq.SchemaField('unit_price', 'FLOAT64'),
            bq.SchemaField('unit_cost', 'FLOAT64'),
            bq.SchemaField('discount', 'FLOAT64'),
            bq.SchemaField('line_total', 'FLOAT64'),
            bq.SchemaField('line_cost', 'FLOAT64'),
        ],
    }
 
 
def load_to_bigquery(fact_sales, dim_customer, dim_product, dim_store, dim_date,
                     write_disposition='WRITE_TRUNCATE'):
    """Load all five tables using the native BigQuery client with EXPLICIT schemas.
 
    This replaces pandas_gbq, whose type inference on object-dtype date columns
    was mangling dates and dropping fact rows. Here every column type is pinned,
    and we verify the loaded row count equals what we sent — failing loudly if
    BigQuery accepted fewer rows than we handed it.
 
    write_disposition:
      'WRITE_TRUNCATE' -> replace table contents (default; correct for re-seed)
      'WRITE_APPEND'   -> add rows
 
    Tables are plain (non-partitioned). At ~280k rows partitioning/clustering buy
    nothing, and a partitioned destination was causing WRITE_TRUNCATE to keep
    only the most recent partitions. Plain tables make the load boring and correct.
    The load job auto-creates each table from the explicit schema if it doesn't
    exist, so no separate DDL step is required.
    """
    from google.cloud import bigquery as bq
 
    fact_sales, dim_customer, dim_date = _normalize_dates(fact_sales, dim_customer, dim_date)
    schemas = _schemas()
    client  = bq.Client(project=cc.BQ_PROJECT)
 
    tables = [
        (cc.TBL_DIM_STORE,    dim_store),
        (cc.TBL_DIM_PRODUCT,  dim_product),
        (cc.TBL_DIM_DATE,     dim_date),
        (cc.TBL_DIM_CUSTOMER, dim_customer),
        (cc.TBL_FACT_SALES,   fact_sales),
    ]
 
    print(f"\nLoading to BigQuery (native client, {write_disposition})...")
    for table_name, df in tables:
        table_id = f"{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{table_name}"
        job_config = bq.LoadJobConfig(
            schema=schemas[table_name],
            write_disposition=write_disposition,
        )
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()  # wait; raises on any row-level failure
 
        sent = len(df)
        count_job = client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`")
        loaded = list(count_job.result())[0]['n']
        status = "OK" if loaded == sent else "*** MISMATCH ***"
        print(f"  {table_name:<14} sent={sent:>8,}  loaded={loaded:>8,}  {status}")
        if loaded != sent:
            raise SystemExit(
                f"Row count mismatch loading {table_name}: sent {sent}, loaded {loaded}")
 
    print("  all tables loaded and row counts verified.")
 
 
def summarize(fact_sales, dim_customer):
    n_txn   = fact_sales['transaction_id'].nunique()
    print(f"\n{'='*55}")
    print(f"  Line items:           {len(fact_sales):>10,}")
    print(f"  Transactions:         {n_txn:>10,}")
    print(f"  Customers (w/ visit): {len(dim_customer):>10,}")
    print(f"  Date range: {fact_sales['date'].min()} -> {fact_sales['date'].max()}")
    print(f"{'='*55}")
 
 
def check_integrity(fact_sales, dim_customer, dim_product, dim_store):
    """Fail loudly BEFORE loading if the star schema has referential gaps.
 
    The orphaned-customer bug (dim_customer rows with no matching fact_sales
    rows) is exactly what this guards against. Re-seeding should never silently
    push a broken roster to BigQuery again.
    """
    problems = []
 
    fact_cust = set(fact_sales['customer_id'].unique())
    dim_cust  = set(dim_customer['customer_id'].unique())
 
    orphans = dim_cust - fact_cust
    if orphans:
        problems.append(f"{len(orphans):,} customers in dim_customer have NO fact rows")
 
    # Every one_and_done customer must have exactly one transaction.
    oad_ids = set(dim_customer.loc[dim_customer['segment'] == 'one_and_done', 'customer_id'])
    txn_per_cust = fact_sales.groupby('customer_id')['transaction_id'].nunique()
    oad_bad = [cid for cid in oad_ids if txn_per_cust.get(cid, 0) != 1]
    if oad_bad:
        problems.append(f"{len(oad_bad):,} one_and_done customers don't have exactly 1 transaction")
 
    # total_visits in dim_customer must match distinct transactions in fact.
    mismatched = 0
    dim_visits = dim_customer.set_index('customer_id')['total_visits']
    for cid, tv in dim_visits.items():
        if txn_per_cust.get(cid, 0) != tv:
            mismatched += 1
    if mismatched:
        problems.append(f"{mismatched:,} customers: total_visits != actual transaction count")
 
    # Fact keys must resolve against dimensions.
    if not set(fact_sales['product_id']).issubset(set(dim_product['product_id'])):
        problems.append("fact_sales has product_id values not in dim_product")
    if not set(fact_sales['store_id']).issubset(set(dim_store['store_id'])):
        problems.append("fact_sales has store_id values not in dim_store")
 
    if problems:
        print("\n*** INTEGRITY CHECK FAILED — NOT loading to BigQuery ***")
        for p in problems:
            print(f"   - {p}")
        raise SystemExit(1)
 
    print("\nIntegrity check passed: no orphans, keys resolve, counts reconcile.")
 
 
if __name__ == '__main__':
    fact_sales, dim_customer, dim_product, dim_store, dim_date = simulate()
    summarize(fact_sales, dim_customer)
    # Hard gate: refuse to load a broken star schema.
    check_integrity(fact_sales, dim_customer, dim_product, dim_store)
    # Save local copies as a backup / for inspection
    fact_sales.to_csv('fact_sales.csv', index=False)
    dim_customer.to_csv('dim_customer.csv', index=False)
    load_to_bigquery(fact_sales, dim_customer, dim_product, dim_store, dim_date)