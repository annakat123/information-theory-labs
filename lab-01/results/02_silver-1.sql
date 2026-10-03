-- First historical batch is simulated as yesterday, not a real previous-day run.
CREATE TABLE bench.v3_silver.large_orders_history
WITH (format = 'PARQUET', partitioning = ARRAY['day(loaded_at)']) AS
WITH quantities AS (
  SELECT orderkey, sum(CAST(quantity AS decimal(18,2))) AS total_quantity
  FROM bench.v3_bronze.lineitem
  WHERE orderkey IS NOT NULL AND quantity > 0
  GROUP BY orderkey
  HAVING sum(CAST(quantity AS decimal(18,2))) > 300
)
SELECT trim(c.name) AS name, c.custkey, o.orderkey, o.orderdate,
       CAST(o.totalprice AS decimal(18,2)) AS totalprice,
       q.total_quantity, year(o.orderdate) AS order_year,
       'initial' AS load_batch,
       CAST(current_timestamp AS timestamp(6)) - INTERVAL '1' DAY AS loaded_at
FROM quantities q
JOIN bench.v3_bronze.orders o ON q.orderkey = o.orderkey
JOIN bench.v3_bronze.customer c ON o.custkey = c.custkey
WHERE c.name IS NOT NULL AND trim(c.name) <> ''
