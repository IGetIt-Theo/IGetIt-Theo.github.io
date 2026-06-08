"""
Cannabis Retail Synthetic Transaction Data Generator
=====================================================
Generates realistic long-format transaction data for RFM analysis.
Run this script to produce a CSV of transaction line items from
2026-01-01 through yesterday.

Output columns:
    transaction_id, line_id, date, datetime, store, customer_id,
    customer_gender, customer_generation, segment, product_id,
    product_name, category, quantity, unit_price, unit_cost,
    discount, line_total, line_cost
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from random import sample


# ── Simulation parameters ──────────────────────────────────────────────────────

START_DATE         = date(2025, 6, 30)
END_DATE           = date.today() - timedelta(days=1)   # always yesterday
CUSTOMER_POOL_SIZE = 15000


# ══════════════════════════════════════════════════════════════════════════════
# TEMPORAL STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

day_of_week_weights = {
    0: 0.120,   # Monday
    1: 0.120,   # Tuesday
    2: 0.120,   # Wednesday
    3: 0.140,   # Thursday
    4: 0.188,   # Friday
    5: 0.166,   # Saturday
    6: 0.106,   # Sunday
}

# Hour weights by day of week — columns are Mon through Sun
hour_distributions = {
    #    Mon   Tue   Wed   Thu   Fri   Sat   Sun
    10: [0.03, 0.03, 0.03, 0.03, 0.03, 0.05, 0.04],
    11: [0.04, 0.04, 0.04, 0.04, 0.05, 0.07, 0.06],
    12: [0.07, 0.07, 0.07, 0.07, 0.07, 0.11, 0.08],  # midday bump
    13: [0.07, 0.07, 0.07, 0.07, 0.07, 0.11, 0.07],
    14: [0.06, 0.06, 0.06, 0.06, 0.07, 0.10, 0.12],
    15: [0.06, 0.06, 0.06, 0.07, 0.08, 0.10, 0.08],
    16: [0.07, 0.07, 0.07, 0.08, 0.09, 0.10, 0.07],
    17: [0.11, 0.11, 0.11, 0.12, 0.13, 0.10, 0.09],  # after-work begins
    18: [0.13, 0.13, 0.13, 0.13, 0.13, 0.09, 0.12],  # close-out rush
    19: [0.13, 0.13, 0.13, 0.13, 0.12, 0.08, 0.10],
    20: [0.11, 0.11, 0.11, 0.11, 0.10, 0.07, 0.10],
    21: [0.12, 0.12, 0.12, 0.09, 0.06, 0.02, 0.07],  # late close-out weekdays
}


# ══════════════════════════════════════════════════════════════════════════════
# STORES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Store:
    name:           str
    revenue_weight: float

    def sample_datetime(self, dt: datetime) -> datetime:
        """Return a realistic transaction timestamp for this store on a given date."""
        day_index = dt.weekday()
        hours     = list(hour_distributions.keys())
        weights   = np.array([hour_distributions[h][day_index] for h in hours], dtype=float)
        weights  /= weights.sum()
        chosen_hour   = np.random.choice(hours, p=weights)
        chosen_minute = np.random.randint(0, 60)
        chosen_second = np.random.randint(0, 60)
        return dt.replace(hour=chosen_hour, minute=chosen_minute, second=chosen_second)


store_data = [
    ("Theo's Sanctuary",            0.500),
    ("The Lively Leaf",             0.150),
    ("Callahan Cannabis",           0.100),
    ("House of Leaf - SW",          0.080),
    ("Mother's Little Helper",      0.060),
    ("Pacific Cannabis Collective", 0.050),
    ("House of Leaf - NW",          0.030),
    ("Urban Herb, Inc.",            0.020),
    ("Supe Herb",                   0.010),
    ("Silicon Valley Green",        0.010),
]

stores        = [Store(name=name, revenue_weight=w) for name, w in store_data]
store_names   = [s.name for s in stores]
store_weights = np.array([s.revenue_weight for s in stores], dtype=float)
store_weights /= store_weights.sum()
store_lookup  = {s.name: s for s in stores}


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT CATALOG
# ══════════════════════════════════════════════════════════════════════════════

CATEGORIES = ['Flower', 'Vape', 'Pre-roll', 'Edibles', 'Wellness']

CATEGORY_COST_RANGES = {
    'Flower':    (15.00,  80.00),
    'Vape':      (25.00,  60.00),
    'Pre-roll':  ( 8.00,  25.00),
    'Edibles':   (15.00,  45.00),
    'Wellness':  (20.00, 100.00),
}

PRODUCTS_PER_CATEGORY = {
    'Flower':    15,
    'Vape':      10,
    'Pre-roll':  10,
    'Edibles':    7,
    'Wellness':   3,
}

STRAINS = [
    'Blue Dream', 'OG Kush', 'Pineapple Express', 'Girl Scout Cookies',
    'Sour Diesel', 'Granddaddy Purple', 'Jack Herer', 'Green Crack',
    'Durban Poison', 'Wedding Cake', 'Gelato', 'Skywalker OG',
    'Purple Punch', 'Zkittlez', 'Mac 1', 'Runtz', 'Mimosa',
    'Forbidden Fruit', 'Ice Cream Cake', 'Tropicana Cookies',
]

FLOWER_FORMS  = ['Flower', 'Indoor', 'Greenhouse', 'Small Buds', 'Shake']
VAPE_FORMS    = ['Cartridge', 'Disposable', 'Live Resin Cart', 'Rosin Cart']
PREROLL_FORMS = ['Pre-roll', 'Infused Pre-roll', 'Mini Pre-roll', 'Hash Hole']

EDIBLE_FLAVORS = [
    'Watermelon', 'Mango', 'Strawberry', 'Blueberry', 'Peach',
    'Green Apple', 'Raspberry', 'Pineapple', 'Grape', 'Lemon',
]
EDIBLE_TYPES = ['Gummies', 'Chocolate', 'Caramels', 'Hard Candy', 'Mints']
EDIBLE_DOSES = ['5mg', '10mg', '25mg', '100mg']

WELLNESS_PRODUCTS = [
    ('CBD Tincture',      ['300mg', '600mg', '1200mg']),
    ('CBN Sleep Caps',    ['30ct', '60ct']),
    ('CBD:THC Tincture',  ['1:1 300mg', '1:1 600mg']),
    ('CBD Topical Cream', ['500mg', '1000mg']),
    ('CBG Capsules',      ['30ct', '60ct']),
    ('Recovery Balm',     ['250mg', '500mg']),
]


def _unique_strains(n):
    if n <= len(STRAINS):
        return list(np.random.choice(STRAINS, size=n, replace=False))
    return (STRAINS * (n // len(STRAINS) + 1))[:n]

def make_flower_names(n):
    return [f'{s} {f}' for s, f in zip(_unique_strains(n), np.random.choice(FLOWER_FORMS, size=n))]

def make_vape_names(n):
    return [f'{s} {f}' for s, f in zip(_unique_strains(n), np.random.choice(VAPE_FORMS, size=n))]

def make_preroll_names(n):
    return [f'{s} {f}' for s, f in zip(_unique_strains(n), np.random.choice(PREROLL_FORMS, size=n))]

def make_edible_names(n):
    flavors = np.random.choice(EDIBLE_FLAVORS, size=n)
    types   = np.random.choice(EDIBLE_TYPES,   size=n)
    doses   = np.random.choice(EDIBLE_DOSES,   size=n)
    return [f'{f} {t} {d}' for f, t, d in zip(flavors, types, doses)]

def make_wellness_names(n):
    names = []
    for _ in range(n):
        product, formats = WELLNESS_PRODUCTS[np.random.randint(len(WELLNESS_PRODUCTS))]
        names.append(f'{product} {formats[np.random.randint(len(formats))]}')
    return names

NAME_GENERATORS = {
    'Flower':   make_flower_names,
    'Vape':     make_vape_names,
    'Pre-roll': make_preroll_names,
    'Edibles':  make_edible_names,
    'Wellness': make_wellness_names,
}


@dataclass
class Product:
    product_id:   int
    product_name: str
    category:     str
    cost:         float
    price:        float

    @property
    def margin(self):
        return round(self.price / self.cost, 4)


def build_catalog() -> list:
    catalog, product_id = [], 1
    for category, (low, high) in CATEGORY_COST_RANGES.items():
        n     = PRODUCTS_PER_CATEGORY[category]
        names = NAME_GENERATORS[category](n)
        for i in range(n):
            cost  = round(np.random.uniform(low, high), 2)
            price = round(cost * np.random.uniform(1.4, 2.2), 2)
            catalog.append(Product(product_id=product_id, product_name=names[i],
                                   category=category, cost=cost, price=price))
            product_id += 1
    return catalog

catalog             = build_catalog()
catalog_by_category = {cat: [p for p in catalog if p.category == cat] for cat in CATEGORIES}


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMERS
# ══════════════════════════════════════════════════════════════════════════════

CATEGORY_PREFERENCES = {
    'Gen Z':      [0.35, 0.30, 0.20, 0.10, 0.05],
    'Millennial': [0.30, 0.25, 0.20, 0.15, 0.10],
    'Gen X':      [0.35, 0.15, 0.20, 0.15, 0.15],
    'Boomer':     [0.40, 0.05, 0.15, 0.15, 0.25],
}

BASKET_SIZE = {
    'Gen Z':      (1, 3),
    'Millennial': (1, 4),
    'Gen X':      (2, 4),
    'Boomer':     (1, 3),
}

VISIT_FREQUENCY = {
    ('Male',   'Gen Z'):      20,
    ('Male',   'Millennial'): 28,
    ('Male',   'Gen X'):      22,
    ('Male',   'Boomer'):     16,
    ('Female', 'Gen Z'):      18,
    ('Female', 'Millennial'): 24,
    ('Female', 'Gen X'):      20,
    ('Female', 'Boomer'):     14,
}

# Loyalty segments
# one_and_done : exactly 1 visit, ever
# occasional   : 0.5x demographic frequency
# regular      : 1.0x demographic frequency
# loyal        : 1.5x demographic frequency
CUSTOMER_SEGMENTS        = ['one_and_done', 'occasional', 'regular', 'loyal']
CUSTOMER_SEGMENT_WEIGHTS = [0.45,            0.25,         0.20,      0.10]

SEGMENT_MULTIPLIER = {
    'one_and_done': None,   # handled separately — always exactly 1 visit
    'occasional':   0.25,
    'regular':      0.75,
    'loyal':        1.1,
}

QUANTITY_OPTIONS = [1, 2, 3]
QUANTITY_WEIGHTS = [0.75, 0.20, 0.05]


@dataclass
class Customer:
    customer_id:         int
    customer_gender:     str
    customer_generation: str
    segment:             str
    home_store:          str  = None
    visit_dates:         list = field(default_factory=list)

    @property
    def category_weights(self):
        return CATEGORY_PREFERENCES[self.customer_generation]

    @property
    def annual_visit_frequency(self):
        return VISIT_FREQUENCY[(self.customer_gender, self.customer_generation)]

    def assign_visit_dates(self, start: date, end: date) -> None:
        all_dates  = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        total_days = len(all_dates)

        # Weight each date by its day-of-week so Fridays/Saturdays get more visits
        dow_probs = np.array([day_of_week_weights[d.weekday()] for d in all_dates],
                             dtype=float)
        dow_probs /= dow_probs.sum()

        if self.segment == 'one_and_done':
            self.visit_dates = list(np.random.choice(all_dates, size=1, p=dow_probs))
            return

        multiplier = SEGMENT_MULTIPLIER[self.segment]
        num_visits = round(self.annual_visit_frequency * multiplier * (total_days / 365))
        num_visits = max(1, min(num_visits, total_days))
        self.visit_dates = list(np.random.choice(all_dates, size=num_visits,
                                                  replace=False, p=dow_probs))

    def sample_basket_size(self) -> int:
        lo, hi = BASKET_SIZE[self.customer_generation]
        return np.random.randint(lo, hi + 1)

    def sample_store(self) -> str:
        """95% home store, 5% random. Sets home_store lazily on first call."""
        if self.home_store is None:
            self.home_store = np.random.choice(store_names, p=store_weights)
        if np.random.random() < 0.05:
            return np.random.choice(store_names, p=store_weights)
        return self.home_store

    def sample_category(self) -> str:
        return np.random.choice(CATEGORIES, p=self.category_weights)


def build_customer_pool(n: int) -> list:
    genders     = np.random.choice(['Male', 'Female'], size=n, p=[0.68, 0.32])
    generations = np.random.choice(['Gen Z', 'Millennial', 'Gen X', 'Boomer'],
                                   size=n, p=[0.25, 0.50, 0.18, 0.07])
    segments    = np.random.choice(CUSTOMER_SEGMENTS, size=n, p=CUSTOMER_SEGMENT_WEIGHTS)
    return [
        Customer(
            customer_id         = i + 1,
            customer_gender     = genders[i],
            customer_generation = generations[i],
            segment             = segments[i],
        )
        for i in range(n)
    ]

customer_pool = build_customer_pool(CUSTOMER_POOL_SIZE)


# ══════════════════════════════════════════════════════════════════════════════
# PRE-ASSIGN VISIT DATES → build {date: [customers]}
# ══════════════════════════════════════════════════════════════════════════════

print("Pre-assigning visit dates...")

visits_by_date: dict = {}
for customer in customer_pool:
    customer.assign_visit_dates(START_DATE, END_DATE)
    for visit_date in customer.visit_dates:
        visits_by_date.setdefault(visit_date, []).append(customer)

total_visits = sum(len(v) for v in visits_by_date.values())
print(f"  {total_visits:,} total visits across {len(visits_by_date):,} days")


# ══════════════════════════════════════════════════════════════════════════════
# SIMULATION LOOP
# ══════════════════════════════════════════════════════════════════════════════

print(f"Simulating {START_DATE} through {END_DATE}...")

rows           = []
transaction_id = 1
current_date   = START_DATE

while current_date <= END_DATE:
    day_customers = visits_by_date.get(current_date, [])
    base_dt       = datetime(current_date.year, current_date.month, current_date.day)

    for customer in day_customers:
        store_name  = customer.sample_store()
        store_obj   = store_lookup[store_name]
        visit_dt    = store_obj.sample_datetime(base_dt)
        basket_size = customer.sample_basket_size()

        for line_id in range(1, basket_size + 1):
            category = customer.sample_category()
            product  = np.random.choice(catalog_by_category[category])
            quantity = np.random.choice(QUANTITY_OPTIONS, p=QUANTITY_WEIGHTS)

            rows.append({
                'transaction_id':      transaction_id,
                'line_id':             line_id,
                'date':                current_date,
                'datetime':            visit_dt,
                'store':               store_name,
                'customer_id':         customer.customer_id,
                'customer_gender':     customer.customer_gender,
                'customer_generation': customer.customer_generation,
                'segment':             customer.segment,
                'product_id':          product.product_id,
                'product_name':        product.product_name,
                'category':            product.category,
                'quantity':            quantity,
                'unit_price':          product.price,
                'unit_cost':           product.cost,
                'discount':            0.0,
                'line_total':          round(product.price * quantity, 2),
                'line_cost':           round(product.cost  * quantity, 2),
            })

        transaction_id += 1

    current_date += timedelta(days=1)


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

transactions = pd.DataFrame(rows)

visit_counts  = transactions.groupby('customer_id')['transaction_id'].nunique()
one_and_done  = (visit_counts == 1).sum()
avg_basket    = transactions.groupby('transaction_id')['line_id'].max().mean()
avg_txn_value = transactions.groupby('transaction_id')['line_total'].sum().mean()

print(f"\n{'='*55}")
print(f"  Line items:                {len(transactions):>10,}")
print(f"  Unique transactions:       {transactions['transaction_id'].nunique():>10,}")
print(f"  Unique customers:          {transactions['customer_id'].nunique():>10,}")
print(f"  One-and-done customers:    {one_and_done:>10,}")
print(f"  Avg basket size (items):   {avg_basket:>10.2f}")
print(f"  Avg transaction value:     ${avg_txn_value:>9.2f}")
print(f"  Date range: {transactions['date'].min()} -> {transactions['date'].max()}")
print(f"{'='*55}")

print(f"\nVisits by store:")
store_visits = transactions.groupby('store')['transaction_id'].nunique().sort_values(ascending=False)
for store, count in store_visits.items():
    print(f"  {store:<30} {count:>6,}  ({count/store_visits.sum()*100:4.1f}%)")

print(f"\nCustomers by segment:")
seg_counts = pd.Series([c.segment for c in customer_pool]).value_counts()
for seg, count in seg_counts.items():
    print(f"  {seg:<15} {count:>5,}  ({count/CUSTOMER_POOL_SIZE*100:4.1f}%)")

print(f"\nVisit frequency distribution (visits per customer):")
print(visit_counts.describe().to_string())

output_path = 'cannabis_transactions.csv'
transactions.to_csv(output_path, index=False)
print(f"\nSaved to {output_path}")