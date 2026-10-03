-- Second batch produces a separate Iceberg append snapshot.
INSERT INTO bench.v3_silver.large_orders_history
SELECT name, custkey, orderkey, orderdate, totalprice, total_quantity,
       order_year, 'refresh', CAST(current_timestamp AS timestamp(6))
FROM bench.v3_silver.large_orders_history WHERE load_batch = 'initial'
