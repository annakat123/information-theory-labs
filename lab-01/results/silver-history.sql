SELECT load_batch,loaded_at,count(*) AS rows FROM bench.v3_silver.large_orders_history GROUP BY load_batch,loaded_at ORDER BY loaded_at
