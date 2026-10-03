-- Session compression is ZSTD; select only columns needed by Q18.
CREATE TABLE bench.v3_bronze.customer WITH (format = 'PARQUET') AS
SELECT custkey, name FROM tpch.sf5.customer
