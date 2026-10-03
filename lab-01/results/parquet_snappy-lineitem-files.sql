SELECT sum(record_count) AS rows, sum(file_size_in_bytes) AS bytes, count(*) AS files FROM bench.v3_bench_parquet_snappy."lineitem$files"
