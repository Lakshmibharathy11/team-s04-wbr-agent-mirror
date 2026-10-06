WITH manual_translation AS (
    SELECT * FROM (VALUES
        ('pc_gamer', 'gaming_pc'),
        ('portateis_cozinha_e_preparadores_de_alimentos', 'portable_kitchen_food_preparers')
    ) AS m(product_category_name, product_category_name_english)
),
translation AS (
    SELECT product_category_name, product_category_name_english FROM category_translation
    UNION ALL
    SELECT product_category_name, product_category_name_english FROM manual_translation
),
product_category AS (
    -- English category for every product; 'unknown' if missing
    SELECT p.product_id,
           COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category
    FROM products p
    LEFT JOIN translation t USING (product_category_name)
),
order_category AS (
    -- one row per order x category group: that group's own items only
    SELECT oi.order_id,
           CASE WHEN pc.category IN (
                    'bed_bath_table', 'sports_leisure', 'health_beauty', 'furniture_decor',
                    'computers_accessories', 'housewares', 'watches_gifts', 'telephony',
                    'cool_stuff', 'toys', 'garden_tools', 'auto', 'perfumery', 'baby')
                THEN pc.category ELSE 'Other' END AS category_group,
           SUM(oi.price)         AS category_revenue,
           COUNT(*)              AS items,
           SUM(oi.freight_value) AS freight
    FROM order_items oi
    JOIN product_category pc USING (product_id)
    GROUP BY 1, 2
)
SELECT
    f.week_start,
    oc.category_group,
    f.state_group,
    CAST(COUNT(*) FILTER (WHERE f.is_revenue_order) AS BIGINT)                                    AS orders_with_category,
    CAST(COALESCE(SUM(oc.category_revenue) FILTER (WHERE f.is_revenue_order), 0) AS DOUBLE)       AS category_revenue,
    CAST(COALESCE(SUM(oc.items) FILTER (WHERE f.is_revenue_order), 0) AS BIGINT)                  AS items,
    CAST(COALESCE(SUM(oc.freight) FILTER (WHERE f.is_revenue_order), 0) AS DOUBLE)                AS freight,
    CAST(COUNT(*) FILTER (WHERE f.is_canceled) AS BIGINT)                                         AS canceled_orders,
    CAST(COUNT(f.review_score) FILTER (WHERE f.is_revenue_order) AS BIGINT)                       AS review_count,
    CAST(COALESCE(SUM(f.review_score) FILTER (WHERE f.is_revenue_order), 0) AS DOUBLE)            AS review_sum,
    CAST(COUNT(*) FILTER (WHERE f.is_revenue_order AND f.review_score = 1) AS BIGINT)             AS one_star_count,
    CAST(COUNT(*) FILTER (WHERE f.is_revenue_order AND f.is_delivered) AS BIGINT)                 AS delivered_orders,
    CAST(COUNT(*) FILTER (WHERE f.is_revenue_order AND f.is_late) AS BIGINT)                      AS late_orders,
    CAST(COALESCE(SUM(f.delivery_days) FILTER (WHERE f.is_revenue_order AND f.is_delivered), 0) AS BIGINT) AS delivery_days_sum
FROM order_category oc
JOIN order_facts f USING (order_id)
GROUP BY f.week_start, oc.category_group, f.state_group