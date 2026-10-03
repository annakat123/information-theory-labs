CREATE TABLE bench.v3_bronze.lineitem WITH (format = 'PARQUET') AS
SELECT orderkey, quantity FROM tpch.sf5.lineitem
