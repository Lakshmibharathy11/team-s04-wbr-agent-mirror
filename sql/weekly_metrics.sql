SELECT
    week_start,
    state_group,
    CAST(COUNT(*) FILTER (WHERE is_revenue_order) AS BIGINT)                             AS orders,
    CAST(COALESCE(SUM(product_revenue) FILTER (WHERE is_revenue_order), 0) AS DOUBLE)    AS product_revenue,
    CAST(COALESCE(SUM(items) FILTER (WHERE is_revenue_order), 0) AS BIGINT)              AS items,
    CAST(COALESCE(SUM(freight) FILTER (WHERE is_revenue_order), 0) AS DOUBLE)            AS freight,
    CAST(COUNT(*) FILTER (WHERE is_canceled) AS BIGINT)                                  AS canceled_orders,
    CAST(COUNT(review_score) FILTER (WHERE is_revenue_order) AS BIGINT)                  AS review_count,
    CAST(COALESCE(SUM(review_score) FILTER (WHERE is_revenue_order), 0) AS DOUBLE)       AS review_sum,
    CAST(COUNT(*) FILTER (WHERE is_revenue_order AND review_score = 1) AS BIGINT)        AS one_star_count,
    CAST(COUNT(*) FILTER (WHERE is_revenue_order AND is_delivered) AS BIGINT)            AS delivered_orders,
    CAST(COUNT(*) FILTER (WHERE is_revenue_order AND is_late) AS BIGINT)                 AS late_orders,
    CAST(COALESCE(SUM(delivery_days) FILTER (WHERE is_revenue_order AND is_delivered), 0) AS BIGINT) AS delivery_days_sum
FROM order_facts
GROUP BY week_start, state_group