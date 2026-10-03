-- Variant 3: teacher query, only sf1 changed to selected sf5.
SELECT c.name, c.custkey, o.orderkey, o.orderdate, o.totalprice,
       sum(l.quantity) AS total_quantity
FROM tpch.sf5.customer AS c
JOIN tpch.sf5.orders AS o ON c.custkey = o.custkey
JOIN tpch.sf5.lineitem AS l ON o.orderkey = l.orderkey
WHERE o.orderkey IN (
  SELECT orderkey FROM tpch.sf5.lineitem
  GROUP BY orderkey HAVING sum(quantity) > 300
)
GROUP BY c.name, c.custkey, o.orderkey, o.orderdate, o.totalprice
ORDER BY o.totalprice DESC, o.orderdate
LIMIT 100;

