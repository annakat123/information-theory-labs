import json
import os
from decimal import Decimal
from pathlib import Path
from pyspark.sql import SparkSession

root = Path('/work')
output = Path(os.environ.get('LAB_RESULTS_DIR', '/work/results'))
spark = SparkSession.builder.appName('variant3-bronze-gold-consumer').getOrCreate()
result = {'spark_version': spark.version, 'gpu_used': False, 'counts': {}}
def normalized(rows):
    return sorted((r['name'], int(r['custkey']), int(r['orderkey']), str(r['orderdate']),
                   format(Decimal(str(r['totalprice'])), '.2f'),
                   format(Decimal(str(r['total_quantity'])), '.2f')) for r in rows)
try:
    spark.sql('SHOW TABLES IN bench.v3_bronze').show(truncate=False)
    expected_counts = {'customer': 750000, 'orders': 7500000, 'lineitem': 29999795}
    for name, expected in expected_counts.items():
        count = spark.table(f'bench.v3_bronze.{name}').count()
        assert count == expected, (name, count, expected)
        result['counts'][name] = count
        print(f'BRONZE {name}: {count} rows', flush=True)
    reference = json.loads((root/'results/source-q18.json').read_text(encoding='utf-8-sig'))['rows']
    sql = (root/'sql/04_q18_bronze.sql').read_text(encoding='utf-8-sig').replace('__SCHEMA__', 'v3_bronze')
    q18 = [r.asDict() for r in spark.sql(sql).collect()]
    assert normalized(q18) == normalized(reference), 'Spark Bronze Q18 differs from original'
    gold = spark.table('bench.v3_gold.large_volume_customers')
    gold.show(10, truncate=False)
    gold_rows = [r.asDict() for r in gold.collect()]
    assert len(gold_rows) == 100
    assert normalized(gold_rows) == normalized(reference), 'Spark Gold differs from original'
    result.update(status='passed', bronze_q18_matches_original=True, gold_matches_original=True, gold_rows=100)
    print('SPARK READS THE SAME LAKEHOUSE: BRONZE Q18 AND GOLD PASSED', flush=True)
except Exception as error:
    result.update(status='failed', error=str(error))
    raise
finally:
    (output/'spark-integration.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    spark.stop()
