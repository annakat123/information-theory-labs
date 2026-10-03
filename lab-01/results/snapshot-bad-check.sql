SELECT count(*) AS rows,count_if(orderkey=-1) AS bad_rows FROM bench.v3_gold.large_volume_customers
