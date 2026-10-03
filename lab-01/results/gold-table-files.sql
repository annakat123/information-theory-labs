SELECT sum(record_count) AS rows, sum(file_size_in_bytes) AS bytes, count(*) AS files FROM bench.v3_gold."large_volume_customers$files"
