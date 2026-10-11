WITH item_totals AS (
    -- one row per order: revenue, item count, shipping
    SELECT order_id,
           SUM(price)         AS product_revenue,
           COUNT(*)           AS items,
           SUM(freight_value) AS freight
    FROM order_items
    GROUP BY order_id
),
review_per_order AS (
    -- one review score per order (averaged if several)
    SELECT order_id, AVG(review_score) AS review_score
    FROM reviews
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.order_purchase_timestamp                                   AS purchase_ts,
    CAST(date_trunc('week', o.order_purchase_timestamp) AS DATE) AS week_start,
    o.order_status,
    c.customer_state,
    CASE WHEN c.customer_state IN ('SP','RJ','MG','RS','PR','SC','BA')
         THEN c.customer_state ELSE 'Other' END                  AS state_group,
    (o.order_status NOT IN ('canceled','unavailable')
        AND o.order_approved_at IS NOT NULL)                     AS is_revenue_order,
    (o.order_status IN ('canceled','unavailable'))               AS is_canceled,
    COALESCE(i.product_revenue, 0)                               AS product_revenue,
    COALESCE(i.items, 0)                                         AS items,
    COALESCE(i.freight, 0)                                       AS freight,
    r.review_score,
    (o.order_status = 'delivered'
        AND o.order_delivered_customer_date IS NOT NULL)         AS is_delivered,
    CASE WHEN o.order_status = 'delivered'
              AND o.order_delivered_customer_date IS NOT NULL
         THEN CAST(o.order_delivered_customer_date AS DATE)
              > CAST(o.order_estimated_delivery_date AS DATE)
    END                                                          AS is_late,
    CASE WHEN o.order_status = 'delivered'
              AND o.order_delivered_customer_date IS NOT NULL
         THEN date_diff('day', CAST(o.order_purchase_timestamp AS DATE),
                               CAST(o.order_delivered_customer_date AS DATE))
    END                                                          AS delivery_days
FROM orders o
JOIN customers c USING (customer_id)
LEFT JOIN item_totals i USING (order_id)
LEFT JOIN review_per_order r USING (order_id)