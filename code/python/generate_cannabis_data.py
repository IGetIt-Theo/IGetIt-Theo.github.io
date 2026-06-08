import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random

np.random.seed()
random.seed()

# DIMENSION: Stores with power law distribution
stores = pd.DataFrame({
    'store_id': range(1, 11),
    'store_name': [
        'Theo\'s Sanctuary',  # Flagship - will generate ~50% of revenue
        'The Lively Leaf',
        'Callahan Cannabis',
        'House of Leaf - SW',
        'Mother\'s Little Helper',
        'Pacific Cannabis Collective',
        'House of Leaf - NW',
        'Urban Herb, Inc.',
        'Sunset Cannabis',
        'Silicon Valley Green'
    ]
})

# Store weights for power curve (flagship gets 50%, rest follow power law)
store_weights = np.array([0.50, 0.15, 0.10, 0.08, 0.06, 0.05, 0.03, 0.02, 0.01, 0.005])
store_weights = store_weights / store_weights.sum()  # Normalize

store_weights_norm = (store_weights - np.min(store_weights)) / (np.max(store_weights)-np.min(store_weights))

# DIMENSION: Products
categories = ['Flower', 'Vape', 'Pre-roll', 'Edibles', 'Wellness']
# Number of products per category (Flower and Pre-roll get more variety)
products_per_category = {'Flower': 15, 'Vape': 10, 'Pre-roll': 10, 'Edibles': 7, 'Wellness': 3}

products_list = []
product_id = 1

for category in categories:
    num_products = products_per_category[category]
    for i in range(num_products):
        # Price ranges vary by category
        if category == 'Flower':
            cost = np.random.uniform(15, 80)
        elif category == 'Vape':
            cost = np.random.uniform(25, 60)
        elif category == 'Pre-roll':
            cost = np.random.uniform(8, 25)
        elif category == 'Edibles':
            cost = np.random.uniform(15, 45)
        else:  # Wellness
            cost = np.random.uniform(20, 100)
        
        margin = np.random.uniform(1.4, 2.2)
        price = round(cost * margin, 2)
        
        products_list.append({
            'product_id': product_id,
            'product_name': f'{category} Product {i+1}',
            'category': category,
            'price': price,
            'cost': cost,
            'margin': margin
        })
        product_id += 1

products = pd.DataFrame(products_list)

# DIMENSION: Customers with realistic age/gender distribution
num_customers = 25000

# Gender: 68% Male, 32% Female (industry average)
genders = np.random.choice(['Male', 'Female'], num_customers, p=[0.68, 0.32])

# Age distribution weighted toward millennials and Gen Z
age_ranges = {
    'Gen Z': (21, 26),
    'Millennial': (27, 42),
    'Gen X': (43, 58),
    'Boomer': (59, 74)
}

# Generation weights (Millennials ~50%, Gen Z ~25%, Gen X ~18%, Boomer ~7%)
gen_weights = [0.25, 0.50, 0.18, 0.07]
generations = np.random.choice(list(age_ranges.keys()), num_customers, p=gen_weights)

ages = []
for gen in generations:
    min_age, max_age = age_ranges[gen]
    ages.append(np.random.randint(min_age, max_age + 1))

customers = pd.DataFrame({
    'customer_id': range(1, num_customers + 1),
    'age': ages,
    'generation': generations,
    'gender': genders
})

# Assign basket size based on generation (older = larger baskets)
basket_multipliers = {
    'Gen Z': 0.7,
    'Millennial': 1.0,
    'Gen X': 1.3,
    'Boomer': 1.5
}

# FACT: Sales Transactions with realistic patterns
start_date = datetime(2024, 1, 1)
end_date = datetime.now() - timedelta(days=1)

# Customer purchase patterns
one_time_customers = int(num_customers * 0.70)
regular_customers = num_customers - one_time_customers

# Assign purchase frequencies
customer_frequencies = {}
for cust_id in range(1, one_time_customers + 1):
    customer_frequencies[cust_id] = 1

for cust_id in range(one_time_customers + 1, num_customers + 1):
    # Regular customers: 2-50 visits, weighted toward lower end
    customer_frequencies[cust_id] = int(np.random.exponential(8)) + 2
    customer_frequencies[cust_id] = min(customer_frequencies[cust_id], 50)

# Assign favorite products based on gender preferences
customer_favorites = {}

for cust_id in range(1, num_customers + 1):
    cust_gender = customers.loc[customers['customer_id'] == cust_id, 'gender'].values[0]
    cust_gen = customers.loc[customers['customer_id'] == cust_id, 'generation'].values[0]
    
    # Gender-based category preferences
    if cust_gender == 'Male':
        # Males prefer Flower (67%), Vape, Pre-roll
        category_prefs = np.random.choice(
            ['Flower', 'Flower', 'Flower', 'Vape', 'Pre-roll', 'Edibles'],
            size=3,
            replace=False
        )
    else:
        # Females prefer Edibles, Wellness, Vape
        category_prefs = np.random.choice(
            ['Edibles', 'Wellness', 'Vape', 'Vape', 'Flower', 'Pre-roll'],
            size=3,
            replace=False
        )
    
    # Gen Z has different preferences (more vapes, concentrates would be here)
    if cust_gen == 'Gen Z':
        if 'Vape' not in category_prefs:
            category_prefs = np.append(category_prefs[:2], 'Vape')
    
    # Select favorite products from preferred categories
    favorites = []
    for cat in category_prefs[:np.random.choice([1, 2, 3], p=[0.6, 0.3, 0.1])]:
        cat_products = products[products['category'] == cat]['product_id'].values
        if len(cat_products) > 0:
            favorites.append(np.random.choice(cat_products))
    
    customer_favorites[cust_id] = favorites if favorites else [np.random.choice(products['product_id'].values)]

# Generate transactions
transactions = []
transaction_id = 1

for cust_id, frequency in customer_frequencies.items():
    cust_gender = customers.loc[customers['customer_id'] == cust_id, 'gender'].values[0]
    cust_gen = customers.loc[customers['customer_id'] == cust_id, 'generation'].values[0]
    customer_prods = customer_favorites[cust_id]
    
    # Basket size multiplier based on generation
    basket_mult = basket_multipliers[cust_gen]
    
    for visit_num in range(frequency):
        # Random date with some seasonality (slightly more in summer, holiday spikes)
        day_of_year = np.random.randint(0, (end_date - start_date).days)
        month = (start_date + timedelta(days=day_of_year)).month
        
        # Slight seasonal adjustment
        if month in [4, 7, 11, 12]:  # 4/20, summer, green wednesday, holidays
            if np.random.random() < 0.3:  # 30% chance to skip this for non-seasonal
                day_of_year = np.random.randint(0, (end_date - start_date).days)
        
        random_date = start_date + timedelta(
            days=day_of_year,
            hours=np.random.randint(9, 20),
            minutes=np.random.randint(0, 60)
        )
        
        # Store selection with power law distribution (flagship gets 50%)
        store_id = np.random.choice(stores['store_id'].values, p=store_weights)
        
        # Determine basket size (influenced by generation and gender)
        base_items = np.random.choice([1, 2, 3], p=[0.70, 0.25, 0.05])
        num_items = max(1, int(base_items * basket_mult))
        
        # Usually buy from favorites, occasionally try something new
        if np.random.random() < 0.80:
            product_ids = np.random.choice(
                customer_prods,
                size=min(num_items, len(customer_prods)),
                replace=False
            ).tolist()
        else:
            # Exploring new products - still biased toward their preferred categories
            if cust_gender == 'Male':
                explore_cats = ['Flower', 'Vape', 'Pre-roll']
            else:
                explore_cats = ['Edibles', 'Wellness', 'Vape']
            
            explore_prods = products[products['category'].isin(explore_cats)]['product_id'].values
            product_ids = [np.random.choice(explore_prods)]
        
        # Create line items for this transaction
        for prod_id in product_ids:
            quantity = np.random.choice([1, 2, 3], p=[0.85, 0.12, 0.03])
            discount = np.random.uniform(0.0, 0.25)
            price = products.loc[products['product_id'] == prod_id, 'price'].values[0]
            cost = products.loc[products['product_id'] == prod_id, 'cost'].values[0]
            transactions.append({
                'transaction_id': transaction_id,
                'datetime': random_date,
                'store_id': store_id,
                'customer_id': cust_id,
                'product_id': prod_id,
                'quantity': quantity,
                'unit_price': price * (1-discount),
                'total_price': round(price * (1-discount) * quantity, 2),
                'cost': cost,
                'total_cost': round(cost * quantity, 2)
            })
        
        transaction_id += 1

sales_fact = pd.DataFrame(transactions)
sales_fact = sales_fact.sort_values('datetime').reset_index(drop=True)

# Calculate and display statistics
print("="*60)
print("RETAIL CANNABIS DATASET - GENERATION SUMMARY")
print("="*60)

# Store performance
store_revenue = sales_fact.groupby('store_id').agg({
    'total_price': 'sum',
    'transaction_id': 'nunique'
}).reset_index()
store_revenue = store_revenue.merge(stores, on='store_id')
store_revenue = store_revenue.sort_values('total_price', ascending=False)
store_revenue['revenue_pct'] = (store_revenue['total_price'] / store_revenue['total_price'].sum() * 100)

print("\nSTORE PERFORMANCE (Top 5):")
print(store_revenue[['store_name', 'total_price', 'revenue_pct', 'transaction_id']].head().to_string(index=False))
print(f"\nFlagship store (The Flower Shop) revenue share: {store_revenue.iloc[0]['revenue_pct']:.1f}%")

# Gender analysis
gender_revenue = sales_fact.merge(customers[['customer_id', 'gender']], on='customer_id')
gender_stats = gender_revenue.groupby('gender')['total_price'].sum()
gender_pct = (gender_stats / gender_stats.sum() * 100)

print(f"\nGENDER REVENUE CONTRIBUTION:")
print(f"  Male: {gender_pct['Male']:.1f}%")
print(f"  Female: {gender_pct['Female']:.1f}%")

# Category preferences by gender
cat_gender = sales_fact.merge(customers[['customer_id', 'gender']], on='customer_id')
cat_gender = cat_gender.merge(products[['product_id', 'category']], on='product_id')
cat_gender_summary = cat_gender.groupby(['gender', 'category'])['total_price'].sum().reset_index()
cat_gender_summary['pct'] = cat_gender_summary.groupby('gender')['total_price'].transform(lambda x: x / x.sum() * 100)

print(f"\nCATEGORY PREFERENCES BY GENDER:")
print("\nMale customers:")
male_cats = cat_gender_summary[cat_gender_summary['gender'] == 'Male'].sort_values('pct', ascending=False)
print(male_cats[['category', 'pct']].to_string(index=False))
print("\nFemale customers:")
female_cats = cat_gender_summary[cat_gender_summary['gender'] == 'Female'].sort_values('pct', ascending=False)
print(female_cats[['category', 'pct']].to_string(index=False))

# Generation analysis
gen_revenue = sales_fact.merge(customers[['customer_id', 'generation']], on='customer_id')
gen_stats = gen_revenue.groupby('generation').agg({
    'total_price': ['sum', 'mean', 'count']
}).reset_index()
gen_stats.columns = ['generation', 'total_revenue', 'avg_transaction', 'num_transactions']
gen_stats['revenue_pct'] = (gen_stats['total_revenue'] / gen_stats['total_revenue'].sum() * 100)
gen_stats = gen_stats.sort_values('total_revenue', ascending=False)

print(f"\nGENERATION REVENUE CONTRIBUTION:")
print(gen_stats[['generation', 'revenue_pct', 'avg_transaction']].to_string(index=False))

print(f"\nDATASET STATISTICS:")
print(f"  Stores: {len(stores)}")
print(f"  Products: {len(products)} across {len(categories)} categories")
print(f"  Customers: {len(customers)}")
print(f"  Transaction line items: {len(sales_fact)}")
print(f"  Unique transactions: {sales_fact['transaction_id'].nunique()}")
print(f"  Date range: {sales_fact['datetime'].min()} to {sales_fact['datetime'].max()}")
print(f"\nCustomer behavior:")
print(f"  One-time buyers: {one_time_customers} ({one_time_customers/num_customers*100:.1f}%)")
print(f"  Regular customers: {regular_customers} ({regular_customers/num_customers*100:.1f}%)")

# Save to CSV files
stores.to_csv('dim_stores.csv', index=False)
products.to_csv('dim_products.csv', index=False)
customers.to_csv('dim_customers.csv', index=False)
sales_fact.to_csv('fact_sales.csv', index=False)

print("\n" + "="*60)
print("Files saved to outputs directory")
print("="*60)
