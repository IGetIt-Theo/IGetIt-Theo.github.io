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

import pandas_gbq

import cannabis_common as cc

# ── Parameters ──────────────────────────────────────────────────────────────
START_DATE         = date(2025, 6, 30)
END_DATE           = date.today() - timedelta(days=1)   # always yesterday
CUSTOMER_POOL_SIZE = 15000
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

def load_to_bigquery(fact_sales, dim_customer, dim_product, dim_store, dim_date,
                     if_exists='append'):
    """Load the seed data.

    if_exists='append'  -> use when you created tables via cannabis_schema.sql
                           (preserves partitioning/clustering/types). DEFAULT.
    if_exists='replace' -> let pandas-gbq create tables from inferred types.
                           Convenient, but you LOSE the DDL's partitioning,
                           clustering, and explicit types. Only for quick tests.
    """
    dest = lambda t: f"{cc.BQ_DATASET}.{t}"
    print(f"\nLoading to BigQuery (if_exists={if_exists})...")
    pandas_gbq.to_gbq(dim_store,    dest(cc.TBL_DIM_STORE),    project_id=cc.BQ_PROJECT, if_exists=if_exists)
    pandas_gbq.to_gbq(dim_product,  dest(cc.TBL_DIM_PRODUCT),  project_id=cc.BQ_PROJECT, if_exists=if_exists)
    pandas_gbq.to_gbq(dim_date,     dest(cc.TBL_DIM_DATE),     project_id=cc.BQ_PROJECT, if_exists=if_exists)
    pandas_gbq.to_gbq(dim_customer, dest(cc.TBL_DIM_CUSTOMER), project_id=cc.BQ_PROJECT, if_exists=if_exists)
    pandas_gbq.to_gbq(fact_sales,   dest(cc.TBL_FACT_SALES),   project_id=cc.BQ_PROJECT, if_exists=if_exists)
    print("  done.")


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