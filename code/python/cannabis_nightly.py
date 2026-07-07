"""
Cannabis Retail — Nightly Incremental Append
============================================
Appends ONE day of transactions to BigQuery (default: yesterday). Designed to be
scheduled (cron / Cloud Scheduler + Cloud Run / Composer).

Each run reproduces the same generative process as the initial batch load, but
for a single day, by reading persisted state from dim_customer. A day's visitors
are a mix of:

  1. Returning existing customers   — Bernoulli draw per customer at their
                                       segment-implied daily probability.
                                       one_and_done customers are excluded once
                                       they already have their single visit.
  2. New one_and_done customers      — walk in once, never return.
  3. New returning customers         — appear today, persisted with a real
                                       segment so future nights can draw them.

New customers (2 & 3) arrive via a Poisson draw whose mean is calibrated so the
roster grows at roughly the same rate the initial pool implies, keeping the
new/returning mix stable over time.

Idempotency: the job checks whether the target date already exists in fact_sales
and aborts if so, so an accidental re-run won't double-load a day.

Prereqs: same as cannabis_initial_load.py.
"""

import sys
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta

from google.cloud import bigquery

import cannabis_common as cc


# ── Explicit BigQuery schemas (match cannabis_initial_load.py) ───────────────
# Pinning column types — especially DATE/DATETIME — prevents the date-mangling
# that object-dtype columns caused with inferred uploads.
def _schemas():
    bq = bigquery
    return {
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


def _append_df(client, table_name, df):
    """Append a dataframe to a BigQuery table with an explicit schema, then
    verify the table grew by exactly len(df) rows. Raises on mismatch."""
    table_id = f"{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{table_name}"
    before = list(client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result())[0]['n']
    job_config = bigquery.LoadJobConfig(
        schema=_schemas()[table_name],
        write_disposition='WRITE_APPEND',
    )
    client.load_table_from_dataframe(df, table_id, job_config=job_config).result()
    after = list(client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result())[0]['n']
    if after - before != len(df):
        raise SystemExit(
            f"Append mismatch on {table_name}: expected +{len(df)}, got +{after - before}")

# ── New-customer arrival rate ───────────────────────────────────────────────
# The initial pool was 15,000 customers over ~365 days. To keep the roster
# growing at a comparable pace, new customers arrive at ~ pool/365 per day.
# Tune NEW_CUSTOMERS_PER_DAY to grow/shrink the acquisition rate.
NEW_CUSTOMERS_PER_DAY = 41   # ~15000 / 365


def get_target_date() -> date:
    """Date to generate. Default yesterday; optional CLI arg YYYY-MM-DD."""
    if len(sys.argv) > 1:
        return datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    return date.today() - timedelta(days=1)


def date_already_loaded(client, target: date) -> bool:
    q = f"""
        SELECT COUNT(*) AS n
        FROM `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_FACT_SALES}`
        WHERE date = @d
    """
    job = client.query(q, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter('d', 'DATE', target)]
    ))
    return list(job.result())[0]['n'] > 0


def load_roster(client) -> pd.DataFrame:
    q = f"SELECT * FROM `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_DIM_CUSTOMER}`"
    return client.query(q).result().to_dataframe()


def load_catalog_from_bq(client) -> dict:
    q = f"SELECT * FROM `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_DIM_PRODUCT}`"
    prod = client.query(q).result().to_dataframe()
    catalog = prod.to_dict('records')
    return {c: [p for p in catalog if p['category'] == c] for c in cc.CATEGORIES}


def get_max_ids(client):
    q = f"""
        SELECT
          (SELECT MAX(customer_id)   FROM `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_DIM_CUSTOMER}`) AS max_cust,
          (SELECT MAX(transaction_id) FROM `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_FACT_SALES}`)   AS max_txn
    """
    r = list(client.query(q).result())[0]
    return int(r['max_cust']), int(r['max_txn'])


def date_exists(client, target: date) -> bool:
    q = f"""
        SELECT COUNT(*) AS n
        FROM `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_DIM_DATE}`
        WHERE date = @d
    """
    job = client.query(q, job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter('d', 'DATE', target)]
    ))
    return list(job.result())[0]['n'] > 0


def build_dim_date_row(target: date) -> pd.DataFrame:
    df = pd.DataFrame([{
        'date':        target,
        'year':        target.year,
        'quarter':     (target.month - 1) // 3 + 1,
        'month':       target.month,
        'month_name':  target.strftime('%B'),
        'day':         target.day,
        'day_of_week': target.weekday(),
        'day_name':    target.strftime('%A'),
        'is_weekend':  target.weekday() >= 5,
    }])
    df['date'] = pd.to_datetime(df['date'])
    return df


def generate_day(roster: pd.DataFrame, catalog_by_cat: dict, target: date,
                 max_cust: int, max_txn: int):
    weekday  = target.weekday()
    base_dt  = datetime(target.year, target.month, target.day)
    fact_rows = []
    txn_id    = max_txn + 1

    # State updates to push back to dim_customer
    visited_existing = {}   # customer_id -> (new_total, last_date)
    new_customer_rows = []

    # ── 1. Returning existing customers (Bernoulli per eligible customer) ────
    # Eligible = not (one_and_done that already has its 1 visit).
    elig = roster[~((roster['segment'] == 'one_and_done') & (roster['total_visits'] >= 1))]

    # Vectorized per-customer daily probability
    probs = np.array([
        cc.daily_visit_prob(g, gen, seg, weekday)
        for g, gen, seg in zip(elig['gender'], elig['generation'], elig['segment'])
    ])
    draws = np.random.random(len(elig)) < probs
    returners = elig[draws]

    def emit_visit(cid, gender, generation, home_store_id):
        nonlocal txn_id
        store_id    = cc.sample_store(int(home_store_id))
        hour        = cc.sample_hour(weekday)
        visit_dt    = base_dt.replace(hour=hour,
                                      minute=int(np.random.randint(0, 60)),
                                      second=int(np.random.randint(0, 60)))
        basket_size = cc.sample_basket_size(generation)
        for line_id in range(1, basket_size + 1):
            category = cc.sample_category(generation)
            product  = catalog_by_cat[category][np.random.randint(len(catalog_by_cat[category]))]
            quantity = cc.sample_quantity()
            fact_rows.append({
                'transaction_id': txn_id, 'line_id': line_id,
                'date': target, 'datetime': visit_dt,
                'store_id': store_id, 'customer_id': cid,
                'product_id': product['product_id'], 'quantity': quantity,
                'unit_price': product['price'], 'unit_cost': product['cost'],
                'discount': 0.0,
                'line_total': round(product['price'] * quantity, 2),
                'line_cost':  round(product['cost']  * quantity, 2),
            })
        txn_id += 1

    for r in returners.itertuples():
        emit_visit(r.customer_id, r.gender, r.generation, r.home_store_id)
        visited_existing[r.customer_id] = (r.total_visits + 1, target)

    # ── 2 & 3. New customers (Poisson arrivals) ──────────────────────────────
    n_new = int(np.random.poisson(NEW_CUSTOMERS_PER_DAY))
    next_cid = max_cust + 1
    for _ in range(n_new):
        gender, generation, segment = cc.sample_new_customer_attrs()
        home = cc.sample_home_store()
        emit_visit(next_cid, gender, generation, home)
        new_customer_rows.append({
            'customer_id': next_cid, 'gender': gender, 'generation': generation,
            'segment': segment, 'home_store_id': home,
            'total_visits': 1, 'first_visit_date': target, 'last_visit_date': target,
        })
        next_cid += 1

    fact_df = pd.DataFrame(fact_rows)
    new_cust_df = pd.DataFrame(new_customer_rows)
    return fact_df, new_cust_df, visited_existing


def apply_state_updates(client, visited_existing: dict, new_cust_df: pd.DataFrame, target: date):
    """Append new customers and update visit counters for returners via MERGE."""
    # New customers: append with explicit schema + verification
    if not new_cust_df.empty:
        nc = new_cust_df.copy()
        nc['first_visit_date'] = pd.to_datetime(nc['first_visit_date'])
        nc['last_visit_date']  = pd.to_datetime(nc['last_visit_date'])
        _append_df(client, cc.TBL_DIM_CUSTOMER, nc)

    # Returners: update total_visits + last_visit_date via a staging table + MERGE
    if visited_existing:
        updates = pd.DataFrame([
            {'customer_id': cid, 'total_visits': tv, 'last_visit_date': ld}
            for cid, (tv, ld) in visited_existing.items()
        ])
        updates['last_visit_date'] = pd.to_datetime(updates['last_visit_date'])
        staging_id = f"{cc.BQ_PROJECT}.{cc.BQ_DATASET}._stg_visit_updates"
        stg_schema = [
            bigquery.SchemaField('customer_id', 'INT64', mode='REQUIRED'),
            bigquery.SchemaField('total_visits', 'INT64'),
            bigquery.SchemaField('last_visit_date', 'DATE'),
        ]
        client.load_table_from_dataframe(
            updates, staging_id,
            job_config=bigquery.LoadJobConfig(
                schema=stg_schema, write_disposition='WRITE_TRUNCATE'),
        ).result()
        merge = f"""
            MERGE `{cc.BQ_PROJECT}.{cc.BQ_DATASET}.{cc.TBL_DIM_CUSTOMER}` T
            USING `{staging_id}` S
            ON T.customer_id = S.customer_id
            WHEN MATCHED THEN UPDATE SET
              T.total_visits    = S.total_visits,
              T.last_visit_date = S.last_visit_date
        """
        client.query(merge).result()
        client.query(f"DROP TABLE `{staging_id}`").result()


def main():
    target = get_target_date()
    print(f"Nightly run for {target} ({target.strftime('%A')})")
    client = bigquery.Client(project=cc.BQ_PROJECT)

    if date_already_loaded(client, target):
        print(f"  {target} already present in fact_sales — aborting to avoid double load.")
        return

    roster         = load_roster(client)
    catalog_by_cat = load_catalog_from_bq(client)
    max_cust, max_txn = get_max_ids(client)
    print(f"  roster={len(roster):,}  max_cust={max_cust:,}  max_txn={max_txn:,}")

    fact_df, new_cust_df, visited_existing = generate_day(
        roster, catalog_by_cat, target, max_cust, max_txn)
    print(f"  returning customers: {len(visited_existing):,}")
    print(f"  new customers:       {len(new_cust_df):,}")
    print(f"  line items:          {len(fact_df):,}")

    if fact_df.empty:
        print("  no transactions generated; nothing to load.")
        return

    # Normalize date columns before upload (DATE/DATETIME pinned via schema)
    fact_df = fact_df.copy()
    fact_df['date']     = pd.to_datetime(fact_df['date'])
    fact_df['datetime'] = pd.to_datetime(fact_df['datetime'])

    # Append fact rows (verified)
    _append_df(client, cc.TBL_FACT_SALES, fact_df)

    # Extend dim_date if needed
    if not date_exists(client, target):
        _append_df(client, cc.TBL_DIM_DATE, build_dim_date_row(target))

    # Update roster state
    apply_state_updates(client, visited_existing, new_cust_df, target)
    print("  done.")


if __name__ == '__main__':
    main()
