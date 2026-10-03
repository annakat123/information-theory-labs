SELECT sum(record_count) AS rows, sum(file_size_in_bytes) AS bytes, count(*) AS files FROM bench.v3_bronze."orders$files"
