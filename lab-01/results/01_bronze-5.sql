CREATE TABLE bench.v3_bronze.orders WITH (format = 'PARQUET') AS
SELECT orderkey, custkey, orderdate, totalprice FROM tpch.sf5.orders
