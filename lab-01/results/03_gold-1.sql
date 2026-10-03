-- Freshness refers to ingestion, not TPC-H orderdate (1990s).
CREATE TABLE bench.v3_gold.large_volume_customers WITH (format = 'PARQUET') AS
SELECT name, custkey, orderkey, orderdate, totalprice, total_quantity, loaded_at
FROM bench.v3_silver.large_orders_history
WHERE loaded_at = (SELECT max(loaded_at) FROM bench.v3_silver.large_orders_history)
ORDER BY totalprice DESC, orderdate
LIMIT 100
