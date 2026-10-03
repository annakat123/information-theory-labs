CREATE SCHEMA bench.v3_bronze;
CREATE SCHEMA bench.v3_silver;
CREATE SCHEMA bench.v3_gold;
-- Session compression is ZSTD; select only columns needed by Q18.
CREATE TABLE bench.v3_bronze.customer WITH (format = 'PARQUET') AS
SELECT custkey, name FROM tpch.sf5.customer;
CREATE TABLE bench.v3_bronze.orders WITH (format = 'PARQUET') AS
SELECT orderkey, custkey, orderdate, totalprice FROM tpch.sf5.orders;
CREATE TABLE bench.v3_bronze.lineitem WITH (format = 'PARQUET') AS
SELECT orderkey, quantity FROM tpch.sf5.lineitem;
