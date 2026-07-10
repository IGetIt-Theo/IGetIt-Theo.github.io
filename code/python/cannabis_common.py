"""
Cannabis Retail — Shared Simulation Parameters & Helpers
========================================================
Imported by both the initial batch loader (cannabis_initial_load.py) and the
nightly incremental job (cannabis_nightly.py) so the two stay calibrated to the
same underlying process.

The key shared concept is the *daily visit probability*. The original batch
model assigned each customer a number of visits per year:

    annual_visits = VISIT_FREQUENCY[(gender, generation)] * SEGMENT_MULTIPLIER[segment]

A nightly job can't pre-assign future dates, so we reframe that same model as an
independent per-customer, per-day Bernoulli draw:

    p(visit on day d) = (annual_visits / 365) * dow_multiplier[d.weekday()]

where dow_multiplier averages ~1.0 across the week but lifts Fri/Sat. Summed over
365 days this reproduces the original annual frequency, so the batch load and the
nightly appends come from one consistent distribution.
"""

import numpy as np

# ── BigQuery target ─────────────────────────────────────────────────────────
BQ_PROJECT = "portfolio-499022"      # <-- set me
BQ_DATASET = "cannabis_retail"       # <-- set me

TBL_FACT_SALES   = "fact_sales"
TBL_DIM_CUSTOMER = "dim_customer"
TBL_DIM_PRODUCT  = "dim_product"
TBL_DIM_STORE    = "dim_store"
TBL_DIM_DATE     = "dim_date"


# ── Day-of-week weighting ───────────────────────────────────────────────────
# Weights sum to 1.0 across the 7 days. The *multiplier* (weight * 7) averages
# to 1.0, so multiplying a base daily probability by it preserves the annual mean.
DAY_OF_WEEK_WEIGHTS = {
    0: 0.120,   # Monday
    1: 0.120,   # Tuesday
    2: 0.120,   # Wednesday
    3: 0.140,   # Thursday
    4: 0.188,   # Friday
    5: 0.166,   # Saturday
    6: 0.106,   # Sunday
}

def dow_multiplier(weekday: int) -> float:
    """Day-of-week scaling factor that averages ~1.0 across the week."""
    return DAY_OF_WEEK_WEIGHTS[weekday] * 7.0


# Hour weights by day of week — columns are Mon through Sun
HOUR_DISTRIBUTIONS = {
    #    Mon   Tue   Wed   Thu   Fri   Sat   Sun
    10: [0.03, 0.03, 0.03, 0.03, 0.03, 0.05, 0.04],
    11: [0.04, 0.04, 0.04, 0.04, 0.05, 0.07, 0.06],
    12: [0.07, 0.07, 0.07, 0.07, 0.07, 0.11, 0.08],
    13: [0.07, 0.07, 0.07, 0.07, 0.07, 0.11, 0.07],
    14: [0.06, 0.06, 0.06, 0.06, 0.07, 0.10, 0.12],
    15: [0.06, 0.06, 0.06, 0.07, 0.08, 0.10, 0.08],
    16: [0.07, 0.07, 0.07, 0.08, 0.09, 0.10, 0.07],
    17: [0.11, 0.11, 0.11, 0.12, 0.13, 0.10, 0.09],
    18: [0.13, 0.13, 0.13, 0.13, 0.13, 0.09, 0.12],
    19: [0.13, 0.13, 0.13, 0.13, 0.12, 0.08, 0.10],
    20: [0.11, 0.11, 0.11, 0.11, 0.10, 0.07, 0.10],
    21: [0.12, 0.12, 0.12, 0.09, 0.06, 0.02, 0.07],
}

def sample_hour(weekday: int) -> int:
    hours   = list(HOUR_DISTRIBUTIONS.keys())
    weights = np.array([HOUR_DISTRIBUTIONS[h][weekday] for h in hours], dtype=float)
    weights /= weights.sum()
    return int(np.random.choice(hours, p=weights))


# ── Stores ──────────────────────────────────────────────────────────────────
# (store_id, store_name, revenue_weight)
STORE_DATA = [
    (1,  "Theo's Sanctuary",            0.500),
    (2,  "The Lively Leaf",             0.150),
    (3,  "Callahan Cannabis",           0.100),
    (4,  "House of Leaf - SW",          0.080),
    (5,  "Mother's Little Helper",      0.060),
    (6,  "Pacific Cannabis Collective", 0.050),
    (7,  "House of Leaf - NW",          0.030),
    (8,  "Urban Herb, Inc.",            0.020),
    (9,  "Supe Herb",                   0.010),
    (10, "Silicon Valley Green",        0.010),
]
STORE_IDS     = np.array([s[0] for s in STORE_DATA])
STORE_WEIGHTS = np.array([s[2] for s in STORE_DATA], dtype=float)
STORE_WEIGHTS /= STORE_WEIGHTS.sum()


# ── Product catalog ─────────────────────────────────────────────────────────
CATEGORIES = ['Flower', 'Vape', 'Pre-roll', 'Edibles', 'Wellness']

# WHOLESALE COST ranges. Retail price = cost * uniform(1.4, 2.2).
# Calibrated so the resulting RETAIL prices match industry benchmarks:
#   Headset (Jun 2026): avg product sold $15.91, avg eighth of flower $20.64
#   Flowhub (2025): walk-in AOV $50.56, 2.7 items/basket
# The previous values were written as retail-ish prices and then marked up
# again, producing a ~4x inflated catalog (mean sold item ~$62).
CATEGORY_COST_RANGES = {
    'Flower':    ( 7.00, 18.00),   # -> retail ~$10-40 (eighths, quarters)
    'Vape':      ( 9.00, 22.00),   # -> retail ~$13-48 (carts, disposables)
    'Pre-roll':  ( 2.20,  6.00),   # -> retail ~$3-13  (singles, infused)
    'Edibles':   ( 4.00, 11.00),   # -> retail ~$6-24  (gummy packs, chocolate)
    'Wellness':  ( 9.00, 24.00),   # -> retail ~$13-53 (tinctures, topicals)
}

PRODUCTS_PER_CATEGORY = {
    'Flower': 15, 'Vape': 10, 'Pre-roll': 10, 'Edibles': 7, 'Wellness': 3,
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

def _make_flower_names(n):
    return [f'{s} {f}' for s, f in zip(_unique_strains(n), np.random.choice(FLOWER_FORMS, size=n))]
def _make_vape_names(n):
    return [f'{s} {f}' for s, f in zip(_unique_strains(n), np.random.choice(VAPE_FORMS, size=n))]
def _make_preroll_names(n):
    return [f'{s} {f}' for s, f in zip(_unique_strains(n), np.random.choice(PREROLL_FORMS, size=n))]
def _make_edible_names(n):
    f = np.random.choice(EDIBLE_FLAVORS, size=n)
    t = np.random.choice(EDIBLE_TYPES, size=n)
    d = np.random.choice(EDIBLE_DOSES, size=n)
    return [f'{a} {b} {c}' for a, b, c in zip(f, t, d)]
def _make_wellness_names(n):
    names = []
    for _ in range(n):
        product, formats = WELLNESS_PRODUCTS[np.random.randint(len(WELLNESS_PRODUCTS))]
        names.append(f'{product} {formats[np.random.randint(len(formats))]}')
    return names

NAME_GENERATORS = {
    'Flower': _make_flower_names, 'Vape': _make_vape_names,
    'Pre-roll': _make_preroll_names, 'Edibles': _make_edible_names,
    'Wellness': _make_wellness_names,
}


def build_catalog() -> list:
    """Returns list of dicts: product_id, product_name, category, cost, price, margin."""
    catalog, product_id = [], 1
    for category, (low, high) in CATEGORY_COST_RANGES.items():
        n     = PRODUCTS_PER_CATEGORY[category]
        names = NAME_GENERATORS[category](n)
        for i in range(n):
            cost  = round(np.random.uniform(low, high), 2)
            price = round(cost * np.random.uniform(1.4, 2.2), 2)
            catalog.append({
                'product_id': product_id, 'product_name': names[i],
                'category': category, 'cost': cost, 'price': price,
                'margin': round(price / cost, 4),
            })
            product_id += 1
    return catalog


# ── Customer composition ────────────────────────────────────────────────────
GENDERS        = ['Male', 'Female']
GENDER_WEIGHTS = [0.68, 0.32]

GENERATIONS        = ['Gen Z', 'Millennial', 'Gen X', 'Boomer']
GENERATION_WEIGHTS = [0.25, 0.50, 0.18, 0.07]

CATEGORY_PREFERENCES = {
    'Gen Z':      [0.35, 0.30, 0.20, 0.10, 0.05],
    'Millennial': [0.30, 0.25, 0.20, 0.15, 0.10],
    'Gen X':      [0.35, 0.15, 0.20, 0.15, 0.15],
    'Boomer':     [0.40, 0.05, 0.15, 0.15, 0.25],
}

# Basket size as a weighted distribution over LINE COUNT, not a uniform range.
# Real retail is right-skewed: most trips are 1-2 items, a minority are stock-up
# trips of 5-6. A uniform (lo, hi) draw cannot produce that tail, which is why
# the old model had no realistic big spenders.
#   Benchmarks: ~2.7 items/basket (Flowhub 2025), mean AOV > median AOV.
# Format: generation -> (line_counts, probabilities)
BASKET_SIZE_DIST = {
    'Gen Z':      ([1, 2, 3, 4, 5, 6], [0.34, 0.32, 0.19, 0.09, 0.04, 0.02]),
    'Millennial': ([1, 2, 3, 4, 5, 6], [0.30, 0.31, 0.21, 0.11, 0.05, 0.02]),
    'Gen X':      ([1, 2, 3, 4, 5, 6], [0.26, 0.30, 0.22, 0.13, 0.06, 0.03]),
    'Boomer':     ([1, 2, 3, 4, 5, 6], [0.38, 0.32, 0.17, 0.08, 0.03, 0.02]),
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

CUSTOMER_SEGMENTS        = ['one_and_done', 'occasional', 'regular', 'loyal']
CUSTOMER_SEGMENT_WEIGHTS = [0.45, 0.25, 0.20, 0.10]

SEGMENT_MULTIPLIER = {
    'one_and_done': None,   # exactly 1 visit, ever
    'occasional':   0.25,
    'regular':      0.75,
    'loyal':        1.1,
}

# Units of the SAME sku on one line. Buying 3 of one item is uncommon.
QUANTITY_OPTIONS = [1, 2, 3]
QUANTITY_WEIGHTS = [0.80, 0.15, 0.05]


def annual_visits(gender: str, generation: str, segment: str) -> float:
    """Expected visits/year implied by the batch model. one_and_done -> 0 (special-cased)."""
    mult = SEGMENT_MULTIPLIER[segment]
    if mult is None:
        return 0.0
    return VISIT_FREQUENCY[(gender, generation)] * mult


def daily_visit_prob(gender: str, generation: str, segment: str, weekday: int) -> float:
    """Per-customer probability of visiting on a given weekday.

    Summed over a year (with the DoW multiplier averaging ~1.0) this reproduces
    annual_visits(...). Used directly by the nightly job; the batch loader uses
    the same numbers via assign_visit_dates.
    """
    base = annual_visits(gender, generation, segment) / 365.0
    return base * dow_multiplier(weekday)


def sample_new_customer_attrs(rng: np.random.Generator = None):
    """Draw (gender, generation, segment) for a brand-new customer."""
    r = rng or np.random
    gender     = r.choice(GENDERS, p=GENDER_WEIGHTS)
    generation = r.choice(GENERATIONS, p=GENERATION_WEIGHTS)
    segment    = r.choice(CUSTOMER_SEGMENTS, p=CUSTOMER_SEGMENT_WEIGHTS)
    return str(gender), str(generation), str(segment)


def sample_home_store() -> int:
    return int(np.random.choice(STORE_IDS, p=STORE_WEIGHTS))

def sample_store(home_store_id: int) -> int:
    """95% home store, 5% a weighted-random store."""
    if np.random.random() < 0.05:
        return int(np.random.choice(STORE_IDS, p=STORE_WEIGHTS))
    return home_store_id

def sample_basket_size(generation: str) -> int:
    """Draw a line count from the generation's weighted basket distribution."""
    counts, probs = BASKET_SIZE_DIST[generation]
    return int(np.random.choice(counts, p=probs))

def sample_category(generation: str) -> str:
    return str(np.random.choice(CATEGORIES, p=CATEGORY_PREFERENCES[generation]))

def sample_quantity() -> int:
    return int(np.random.choice(QUANTITY_OPTIONS, p=QUANTITY_WEIGHTS))
