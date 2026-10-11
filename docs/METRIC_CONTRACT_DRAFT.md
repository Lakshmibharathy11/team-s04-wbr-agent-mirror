# Metric Contract — Draft for Data Review

Pending Lakshmi's final data profiling decisions.

## Revenue
Proposed basis: `SUM(order_items.price)` for qualifying orders.
Proposed exclusions: `canceled`, `unavailable`.

## Order count
`COUNT(DISTINCT order_id)` at order grain.

## Average review score
One canonical review per qualifying order; missing scores excluded from the arithmetic mean and review coverage reported.

## Category
Revenue attributed at item grain. Category order counts are distinct within category and are not summed into headline orders.

## Customer state
Order-associated customer state, with unknown/invalid states labeled `UNKNOWN`.

## Join safety
Pre-aggregate items and canonicalize reviews before headline joins to prevent fanout.
