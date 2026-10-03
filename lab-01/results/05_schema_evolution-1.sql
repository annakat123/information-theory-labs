-- A small isolated Silver demo protects the main history.
CREATE TABLE bench.v3_silver.schema_evolution_demo AS
SELECT name, custkey, orderkey, total_quantity
FROM bench.v3_silver.large_orders_history WHERE load_batch = 'refresh'
ORDER BY orderkey LIMIT 2
