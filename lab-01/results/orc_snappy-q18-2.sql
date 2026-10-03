SELECT c.name, c.custkey, o.orderkey, o.orderdate, o.totalprice,
       sum(l.quantity) AS total_quantity
FROM bench.v3_bench_orc_snappy.customer c
JOIN bench.v3_bench_orc_snappy.orders o ON c.custkey = o.custkey
JOIN bench.v3_bench_orc_snappy.lineitem l ON o.orderkey = l.orderkey
WHERE o.orderkey IN (
  SELECT orderkey FROM bench.v3_bench_orc_snappy.lineitem
  GROUP BY orderkey HAVING sum(quantity) > 300
)
GROUP BY c.name, c.custkey, o.orderkey, o.orderdate, o.totalprice
ORDER BY o.totalprice DESC, o.orderdate
LIMIT 100
