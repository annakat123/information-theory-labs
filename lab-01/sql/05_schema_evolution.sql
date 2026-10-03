-- A small isolated Silver demo protects the main history.
CREATE TABLE bench.v3_silver.schema_evolution_demo AS
SELECT name, custkey, orderkey, total_quantity
FROM bench.v3_silver.large_orders_history WHERE load_batch = 'refresh'
ORDER BY orderkey LIMIT 2;
ALTER TABLE bench.v3_silver.schema_evolution_demo ADD COLUMN note varchar;
UPDATE bench.v3_silver.schema_evolution_demo SET note = 'column added';
ALTER TABLE bench.v3_silver.schema_evolution_demo RENAME COLUMN note TO comment_text;
SELECT * FROM bench.v3_silver.schema_evolution_demo ORDER BY orderkey;
ALTER TABLE bench.v3_silver.schema_evolution_demo DROP COLUMN comment_text;
INSERT INTO bench.v3_silver.schema_evolution_demo (name,custkey,orderkey,total_quantity)
VALUES ('Schema demonstration', -1, -1, DECIMAL '301.00');
SELECT * FROM bench.v3_silver.schema_evolution_demo ORDER BY orderkey;
