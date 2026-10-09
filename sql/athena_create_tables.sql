-- weekly_metrics: 13 columns, from s3://aizoya-s04-wbr-source-704554708405/source/weekly_metrics/
CREATE EXTERNAL TABLE IF NOT EXISTS s04_wbr.weekly_metrics (
  `week_start` date,
  `state_group` string,
  `orders` bigint,
  `product_revenue` double,
  `items` bigint,
  `freight` double,
  `canceled_orders` bigint,
  `review_count` bigint,
  `review_sum` double,
  `one_star_count` bigint,
  `delivered_orders` bigint,
  `late_orders` bigint,
  `delivery_days_sum` bigint
)
STORED AS PARQUET
LOCATION 's3://aizoya-s04-wbr-source-704554708405/source/weekly_metrics/';

-- weekly_category_metrics: 14 columns, from s3://aizoya-s04-wbr-source-704554708405/source/weekly_category_metrics/
CREATE EXTERNAL TABLE IF NOT EXISTS s04_wbr.weekly_category_metrics (
  `week_start` date,
  `category_group` string,
  `state_group` string,
  `orders_with_category` bigint,
  `category_revenue` double,
  `items` bigint,
  `freight` double,
  `canceled_orders` bigint,
  `review_count` bigint,
  `review_sum` double,
  `one_star_count` bigint,
  `delivered_orders` bigint,
  `late_orders` bigint,
  `delivery_days_sum` bigint
)
STORED AS PARQUET
LOCATION 's3://aizoya-s04-wbr-source-704554708405/source/weekly_category_metrics/';

-- order_facts: 15 columns, from s3://aizoya-s04-wbr-source-704554708405/source/order_facts/
CREATE EXTERNAL TABLE IF NOT EXISTS s04_wbr.order_facts (
  `order_id` string,
  `purchase_ts` timestamp,
  `week_start` date,
  `order_status` string,
  `customer_state` string,
  `state_group` string,
  `is_revenue_order` boolean,
  `is_canceled` boolean,
  `product_revenue` double,
  `items` bigint,
  `freight` double,
  `review_score` double,
  `is_delivered` boolean,
  `is_late` boolean,
  `delivery_days` bigint
)
STORED AS PARQUET
LOCATION 's3://aizoya-s04-wbr-source-704554708405/source/order_facts/';

-- golden_weekly_metrics: 13 columns, from s3://aizoya-s04-wbr-source-704554708405/fixtures/weekly_metrics/
CREATE EXTERNAL TABLE IF NOT EXISTS s04_wbr.golden_weekly_metrics (
  `week_start` date,
  `state_group` string,
  `orders` bigint,
  `product_revenue` double,
  `items` bigint,
  `freight` double,
  `canceled_orders` bigint,
  `review_count` bigint,
  `review_sum` double,
  `one_star_count` bigint,
  `delivered_orders` bigint,
  `late_orders` bigint,
  `delivery_days_sum` bigint
)
STORED AS PARQUET
LOCATION 's3://aizoya-s04-wbr-source-704554708405/fixtures/weekly_metrics/';

-- golden_weekly_category_metrics: 14 columns, from s3://aizoya-s04-wbr-source-704554708405/fixtures/weekly_category_metrics/
CREATE EXTERNAL TABLE IF NOT EXISTS s04_wbr.golden_weekly_category_metrics (
  `week_start` date,
  `category_group` string,
  `state_group` string,
  `orders_with_category` bigint,
  `category_revenue` double,
  `items` bigint,
  `freight` double,
  `canceled_orders` bigint,
  `review_count` bigint,
  `review_sum` double,
  `one_star_count` bigint,
  `delivered_orders` bigint,
  `late_orders` bigint,
  `delivery_days_sum` bigint
)
STORED AS PARQUET
LOCATION 's3://aizoya-s04-wbr-source-704554708405/fixtures/weekly_category_metrics/';
