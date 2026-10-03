SELECT 'customer' AS table_name, count(*) AS rows,
 count_if(custkey IS NULL OR name IS NULL OR trim(name) = '') AS invalid_rows FROM bench.v3_bronze.customer
UNION ALL SELECT 'orders',count(*),count_if(orderkey IS NULL OR custkey IS NULL OR orderdate IS NULL OR totalprice < 0) FROM bench.v3_bronze.orders
UNION ALL SELECT 'lineitem',count(*),count_if(orderkey IS NULL OR quantity IS NULL OR quantity <= 0) FROM bench.v3_bronze.lineitem
