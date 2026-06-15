-- ============================================================================
-- Cannabis Retail Star Schema — BigQuery DDL
-- ============================================================================
-- Run once before the initial load (or let pandas-gbq auto-create the tables;
-- this DDL gives you explicit control over types, partitioning, and clustering,
-- which is preferable for BI performance).
--
-- Replace `portfolio-499022` and `cannabis_retail` to match cannabis_common.py.
--
-- Notes on BigQuery specifics:
--   * PRIMARY KEY / FOREIGN KEY constraints are supported but NOT ENFORCED
--     (advisory only). They document the model and can help the query planner.
--     They must be declared with NOT ENFORCED.
--   * fact_sales is partitioned by `date` and clustered by store_id, customer_id
--     for typical RFM / store-level filtering. Dimensions are small; no
--     partitioning needed.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS `portfolio-499022.cannabis_retail`
  OPTIONS (location = 'US');


-- ── dim_store ───────────────────────────────────────────────────────────────
CREATE OR REPLACE TABLE `portfolio-499022.cannabis_retail.dim_store`
(
  store_id       INT64   NOT NULL,
  store_name     STRING  NOT NULL,
  revenue_weight FLOAT64,
  PRIMARY KEY (store_id) NOT ENFORCED
);


-- ── dim_product ───────────────────────────────────────────────────────────--
CREATE OR REPLACE TABLE `portfolio-499022.cannabis_retail.dim_product`
(
  product_id   INT64  NOT NULL,
  product_name STRING NOT NULL,
  category     STRING NOT NULL,
  cost         FLOAT64,
  price        FLOAT64,
  margin       FLOAT64,
  PRIMARY KEY (product_id) NOT ENFORCED
);


-- ── dim_date ──────────────────────────────────────────────────────────────--
CREATE OR REPLACE TABLE `portfolio-499022.cannabis_retail.dim_date`
(
  date        DATE   NOT NULL,
  year        INT64,
  quarter     INT64,
  month       INT64,
  month_name  STRING,
  day         INT64,
  day_of_week INT64,            -- 0 = Monday ... 6 = Sunday
  day_name    STRING,
  is_weekend  BOOL,
  PRIMARY KEY (date) NOT ENFORCED
);


-- ── dim_customer ────────────────────────────────────────────────────────────
-- The living roster. total_visits / last_visit_date are maintained by the
-- nightly job and are what let it reproduce the segment-driven return process.
CREATE OR REPLACE TABLE `portfolio-499022.cannabis_retail.dim_customer`
(
  customer_id      INT64  NOT NULL,
  gender           STRING,
  generation       STRING,
  segment          STRING,           -- one_and_done | occasional | regular | loyal
  home_store_id    INT64,
  total_visits     INT64,
  first_visit_date DATE,
  last_visit_date  DATE,
  PRIMARY KEY (customer_id) NOT ENFORCED,
  FOREIGN KEY (home_store_id) REFERENCES `portfolio-499022.cannabis_retail.dim_store`(store_id) NOT ENFORCED
);


-- ── fact_sales ────────────────────────────────────────────────────────────--
-- One row per line item. Partitioned by date, clustered for store/customer
-- filtering. Grain: (transaction_id, line_id).
CREATE OR REPLACE TABLE `portfolio-499022.cannabis_retail.fact_sales`
(
  transaction_id INT64    NOT NULL,
  line_id        INT64    NOT NULL,
  date           DATE     NOT NULL,
  datetime       DATETIME NOT NULL,
  store_id       INT64    NOT NULL,
  customer_id    INT64    NOT NULL,
  product_id     INT64    NOT NULL,
  quantity       INT64,
  unit_price     FLOAT64,
  unit_cost      FLOAT64,
  discount       FLOAT64,
  line_total     FLOAT64,
  line_cost      FLOAT64,
  PRIMARY KEY (transaction_id, line_id) NOT ENFORCED,
  FOREIGN KEY (store_id)    REFERENCES `portfolio-499022.cannabis_retail.dim_store`(store_id)       NOT ENFORCED,
  FOREIGN KEY (customer_id) REFERENCES `portfolio-499022.cannabis_retail.dim_customer`(customer_id) NOT ENFORCED,
  FOREIGN KEY (product_id)  REFERENCES `portfolio-499022.cannabis_retail.dim_product`(product_id)   NOT ENFORCED,
  FOREIGN KEY (date)        REFERENCES `portfolio-499022.cannabis_retail.dim_date`(date)            NOT ENFORCED
)
PARTITION BY date
CLUSTER BY store_id, customer_id;
