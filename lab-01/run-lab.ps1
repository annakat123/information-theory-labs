param([switch]$ResumeBenchmark)
$ErrorActionPreference = 'Stop'
[threading.Thread]::CurrentThread.CurrentCulture = [cultureinfo]::InvariantCulture
$root = $PSScriptRoot
$out = Join-Path $root 'results'
if (-not $ResumeBenchmark -and (Test-Path -LiteralPath (Join-Path $out 'status.json'))) { throw 'Existing run found. Do not overwrite it.' }
New-Item -ItemType Directory -Force -Path $out | Out-Null
$script:compression = 'ZSTD'

function Invoke-LabSql {
 param([string]$Sql,[string]$Label)
 $Sql = $Sql.Trim().TrimEnd(';')
 $prefix = Join-Path $out $Label
 $Sql | Out-File -LiteralPath ($prefix+'.sql') -Encoding utf8
 $headers = @{'X-Trino-User'='variant3'; 'X-Trino-Catalog'='bench'; 'X-Trino-Time-Zone'='Europe/Moscow'; 'X-Trino-Session'=('bench.compression_codec='+$script:compression)}
 $timer = [diagnostics.stopwatch]::StartNew()
 $allData = [collections.generic.list[object]]::new()
 $columns = $null
 $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/statement' -Method Post -Headers $headers -ContentType 'text/plain; charset=utf-8' -Body $Sql
 while ($true) {
  if ($response.columns) { $columns = $response.columns }
  foreach ($values in $response.data) { $allData.Add($values) }
  if ($response.error) {
   $response | ConvertTo-Json -Depth 30 | Out-File -LiteralPath ($prefix+'.error.json') -Encoding utf8
   throw "$Label : $($response.error.message)"
  }
  if (-not $response.nextUri) { break }
  Start-Sleep -Milliseconds 100
  $response = Invoke-RestMethod -Uri $response.nextUri -Headers $headers
 }
 $timer.Stop()
 $rows = @($allData | ForEach-Object {
  $values = $_; $record = [ordered]@{}
  for ($i=0; $i -lt $columns.Count; $i++) { $record[$columns[$i].name] = $values[$i] }
  [pscustomobject]$record
 })
 $result = [pscustomobject]@{label=$Label; query_id=$response.id; client_wall_seconds=[math]::Round($timer.Elapsed.TotalSeconds,3); server_elapsed_ms=$response.stats.elapsedTimeMillis; stats=$response.stats; rows=$rows}
 $result | ConvertTo-Json -Depth 30 | Out-File -LiteralPath ($prefix+'.json') -Encoding utf8
 Write-Host "$Label : $($result.client_wall_seconds)s, $($rows.Count) result rows"
 return $result
}
function Run-File {
 param([string]$Name)
 $parts = (Get-Content -LiteralPath (Join-Path $root ('sql\'+$Name+'.sql')) -Raw) -split ';\s*(?:\r?\n|$)'
 $n=0
 foreach ($part in $parts) { if ($part.Trim()) { $n++; Invoke-LabSql -Sql $part -Label ($Name+'-'+$n) | Out-Null } }
}
function Canonical {
 param($Rows)
 $normalized = @($Rows | Sort-Object orderkey | ForEach-Object {
  [ordered]@{name=$_.name; custkey=[long]$_.custkey; orderkey=[long]$_.orderkey; orderdate=$_.orderdate;
  totalprice=([decimal]$_.totalprice).ToString('F2',[cultureinfo]::InvariantCulture);
  total_quantity=([decimal]$_.total_quantity).ToString('F2',[cultureinfo]::InvariantCulture)}
 })
 return (ConvertTo-Json -InputObject $normalized -Depth 8 -Compress)
}
function Table-Metrics {
 param([string]$Schema,[string]$Table,[string]$Label)
 $filesSql = 'SELECT sum(record_count) AS rows, sum(file_size_in_bytes) AS bytes, count(*) AS files FROM bench.'+$Schema+'."'+$Table+'$files"'
 $files = Invoke-LabSql -Sql $filesSql -Label ($Label+'-files')
 $properties = Invoke-LabSql -Sql ('SELECT * FROM bench.'+$Schema+'."'+$Table+'$properties"') -Label ($Label+'-properties')
 return [pscustomobject]@{schema=$Schema;table=$Table;rows=$files.rows[0].rows;bytes=$files.rows[0].bytes;files=$files.rows[0].files;
 format=($properties.rows | Where-Object key -eq 'write.format.default').value;
 parquet_codec=($properties.rows | Where-Object key -eq 'write.parquet.compression-codec').value;
 orc_codec=($properties.rows | Where-Object key -eq 'write.orc.compression-codec').value}
}
try {
 if (-not $ResumeBenchmark) {
  $reference = Invoke-LabSql -Sql (Get-Content -LiteralPath (Join-Path $root 'sql\00_source_q18.sql') -Raw) -Label 'source-q18'
  Run-File '01_bronze'
  $qualitySql = @"
SELECT 'customer' AS table_name, count(*) AS rows,
 count_if(custkey IS NULL OR name IS NULL OR trim(name) = '') AS invalid_rows FROM bench.v3_bronze.customer
UNION ALL SELECT 'orders',count(*),count_if(orderkey IS NULL OR custkey IS NULL OR orderdate IS NULL OR totalprice < 0) FROM bench.v3_bronze.orders
UNION ALL SELECT 'lineitem',count(*),count_if(orderkey IS NULL OR quantity IS NULL OR quantity <= 0) FROM bench.v3_bronze.lineitem
"@
  $quality = Invoke-LabSql -Sql $qualitySql -Label 'bronze-quality'
  foreach ($r in $quality.rows) { if ($r.invalid_rows -ne 0) { throw 'Invalid source rows found' } }
  Run-File '02_silver'
  Run-File '03_gold'
  $gold = Invoke-LabSql -Sql 'SELECT name,custkey,orderkey,orderdate,totalprice,total_quantity FROM bench.v3_gold.large_volume_customers ORDER BY totalprice DESC,orderdate' -Label 'gold-q18'
  if ($gold.rows.Count -ne 100 -or (Canonical $gold.rows) -ne (Canonical $reference.rows)) { throw 'Gold differs from original Q18' }
  Invoke-LabSql -Sql 'SELECT load_batch,loaded_at,count(*) AS rows FROM bench.v3_silver.large_orders_history GROUP BY load_batch,loaded_at ORDER BY loaded_at' -Label 'silver-history' | Out-Null
  Run-File '05_schema_evolution'
  $demo = Invoke-LabSql -Sql 'SELECT count(*) AS rows FROM bench.v3_silver.schema_evolution_demo' -Label 'schema-evolution-check'
  if ($demo.rows[0].rows -ne 3) { throw 'Schema evolution failed' }
  $snap = Invoke-LabSql -Sql 'SELECT snapshot_id FROM bench.v3_gold."large_volume_customers$snapshots" ORDER BY committed_at DESC LIMIT 1' -Label 'snapshot-before'
  $snapshotId = [long]$snap.rows[0].snapshot_id
  Invoke-LabSql -Sql "INSERT INTO bench.v3_gold.large_volume_customers VALUES ('DEMO_BAD_LOAD',-1,-1,DATE '1995-01-01',DECIMAL '999999999.00',DECIMAL '999.00',CAST(current_timestamp AS timestamp(6)))" -Label 'snapshot-bad-insert' | Out-Null
  $bad = Invoke-LabSql -Sql 'SELECT count(*) AS rows,count_if(orderkey=-1) AS bad_rows FROM bench.v3_gold.large_volume_customers' -Label 'snapshot-bad-check'
  if ($bad.rows[0].rows -ne 101 -or $bad.rows[0].bad_rows -ne 1) { throw 'Bad load demonstration failed' }
  $historical = Invoke-LabSql -Sql "SELECT count(*) AS rows FROM bench.v3_gold.large_volume_customers FOR VERSION AS OF $snapshotId" -Label 'snapshot-time-travel'
  if ($historical.rows[0].rows -ne 100) { throw 'Time travel failed' }
  Invoke-LabSql -Sql "ALTER TABLE bench.v3_gold.large_volume_customers EXECUTE rollback_to_snapshot($snapshotId)" -Label 'snapshot-rollback' | Out-Null
  $restored = Invoke-LabSql -Sql 'SELECT name,custkey,orderkey,orderdate,totalprice,total_quantity FROM bench.v3_gold.large_volume_customers ORDER BY totalprice DESC,orderdate' -Label 'snapshot-restored'
  if ((Canonical $restored.rows) -ne (Canonical $reference.rows)) { throw 'Restored Gold differs from source' }
  $metrics = @()
  foreach ($t in @('customer','orders','lineitem')) { $metrics += Table-Metrics 'v3_bronze' $t ('bronze-'+$t) }
  $metrics += Table-Metrics 'v3_silver' 'large_orders_history' 'silver-history-table'
  $metrics += Table-Metrics 'v3_gold' 'large_volume_customers' 'gold-table'
  $metrics | Export-Csv -LiteralPath (Join-Path $out 'layer-metrics.csv') -NoTypeInformation -Encoding utf8
  $metrics | ConvertTo-Json -Depth 10 | Out-File -LiteralPath (Join-Path $out 'layer-metrics.json') -Encoding utf8
  @{pipeline='passed';gold_matches_original=$true;schema_evolution='passed';time_travel='passed';rollback='passed';good_snapshot_id=$snapshotId} | ConvertTo-Json | Out-File -LiteralPath (Join-Path $out 'pipeline-status.json') -Encoding utf8
 }
 $reference = Get-Content -LiteralPath (Join-Path $out 'source-q18.json') -Raw | ConvertFrom-Json
 $template = Get-Content -LiteralPath (Join-Path $root 'sql\04_q18_bronze.sql') -Raw
 $benchmarks = @()
 foreach ($format in @('PARQUET','ORC')) {
  foreach ($codec in @('ZSTD','SNAPPY','GZIP')) {
   $script:compression=$codec
   $tag=$format.ToLowerInvariant()+'_'+$codec.ToLowerInvariant()
   $schema='v3_bench_'+$tag
   $writeSeconds = 0.0
   if ($schema -ne 'v3_bronze') {
    Invoke-LabSql -Sql "CREATE SCHEMA bench.$schema" -Label ($tag+'-schema') | Out-Null
    foreach ($table in @('customer','orders','lineitem')) {
     $write = Invoke-LabSql -Sql "CREATE TABLE bench.$schema.$table WITH (format = '$format') AS SELECT * FROM bench.v3_bronze.$table" -Label ($tag+'-'+$table+'-write')
     $writeSeconds += $write.client_wall_seconds
    }
   } else {
    foreach ($n in @(4,5,6)) { $writeSeconds += (Get-Content -LiteralPath (Join-Path $out "01_bronze-$n.json") -Raw | ConvertFrom-Json).client_wall_seconds }
   }
   $variantMetrics = @()
   foreach ($table in @('customer','orders','lineitem')) {
    $metric = Table-Metrics $schema $table ($tag+'-'+$table)
    $actualCodec = if ($format -eq 'PARQUET') {$metric.parquet_codec} else {$metric.orc_codec}
    $expectedCodec = if ($format -eq 'ORC' -and $codec -eq 'GZIP') {'zlib'} else {$codec.ToLowerInvariant()}
    if ($metric.format -ne $format -or $actualCodec -ne $expectedCodec) { throw 'Unexpected format/codec properties' }
    $variantMetrics += $metric
   }
   $times=@(); $serverTimes=@()
   for ($n=1;$n -le 3;$n++) {
    $query = Invoke-LabSql -Sql $template.Replace('__SCHEMA__',$schema) -Label ($tag+'-q18-'+$n)
    if ((Canonical $query.rows) -ne (Canonical $reference.rows)) { throw ('Q18 mismatch: '+$tag) }
    $times += $query.client_wall_seconds; $serverTimes += $query.server_elapsed_ms/1000.0
   }
   $benchmarks += [pscustomobject]@{format=$format;requested_codec=$codec;schema=$schema;bronze_rows=($variantMetrics | Measure-Object rows -Sum).Sum;
    bytes=($variantMetrics | Measure-Object bytes -Sum).Sum;files=($variantMetrics | Measure-Object files -Sum).Sum;
    write_wall_seconds=[math]::Round($writeSeconds,3);write_source=$(if($schema -eq 'v3_bronze'){'tpch.sf5 generator'}else{'materialized Bronze'});
    query_wall_1=$times[0];query_wall_2=$times[1];query_wall_3=$times[2];query_median_wall_seconds=($times|Sort-Object)[1];
    query_server_1=$serverTimes[0];query_server_2=$serverTimes[1];query_server_3=$serverTimes[2];query_median_server_seconds=($serverTimes|Sort-Object)[1];result_matches_original=$true}
   $benchmarks | Export-Csv -LiteralPath (Join-Path $out 'q18-format-benchmark.csv') -NoTypeInformation -Encoding utf8
   $benchmarks | ConvertTo-Json -Depth 10 | Out-File -LiteralPath (Join-Path $out 'q18-format-benchmark.json') -Encoding utf8
  }
 }
 @{status='passed';variant=3;query='Q18';scale='sf5';formats=6;repetitions_per_format=3} | ConvertTo-Json | Out-File -LiteralPath (Join-Path $out 'status.json') -Encoding utf8
 Write-Host 'VARIANT 3 PIPELINE AND ALL SIX Q18 FORMAT TESTS PASSED'
} catch {
 $_ | Out-String | Out-File -LiteralPath (Join-Path $out 'failure.log') -Encoding utf8
 throw
}
