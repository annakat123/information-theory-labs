param([switch]$RequireStackSecrets)
$ErrorActionPreference='Stop'
$localPath=Join-Path $PSScriptRoot '.local'
$settingsPath=Join-Path $localPath 'settings.json'
$settings=if(Test-Path -LiteralPath $settingsPath){Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json}else{@{}}
$names=@('MINIO_ROOT_USER','MINIO_ROOT_PASSWORD')
if($RequireStackSecrets){$names+=@('POSTGRES_PASSWORD','LAKEKEEPER_ENCRYPTION_KEY')}
foreach($name in $names){
 $value=[Environment]::GetEnvironmentVariable($name)
 if(-not $value){$value=$settings.$name}
 if(-not $value -or $value -match '^REPLACE_'){throw "Set $name in .local/settings.json or the environment; see configs/settings.example.json"}
 if($value -match '[\r\n]'){throw 'Configuration values must not contain line breaks'}
 [Environment]::SetEnvironmentVariable($name,$value,'Process')
}
New-Item -ItemType Directory -Force -Path (Join-Path $localPath 'catalog') | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'configs/catalog/tpch.properties') -Destination (Join-Path $localPath 'catalog/tpch.properties') -Force
foreach($pair in @(@('configs/catalog/bench.properties','.local/catalog/bench.properties'),@('configs/spark-defaults.conf','.local/spark-defaults.conf'))){
 $text=Get-Content -LiteralPath (Join-Path $PSScriptRoot $pair[0]) -Raw
 $text=$text.Replace('__MINIO_ACCESS_KEY__',$env:MINIO_ROOT_USER).Replace('__MINIO_SECRET_KEY__',$env:MINIO_ROOT_PASSWORD)
 # Preserve Java properties values containing backslashes.
 $text=$text.Replace('\','\\')
 [IO.File]::WriteAllText((Join-Path $PSScriptRoot $pair[1]),$text,[Text.UTF8Encoding]::new($false))
}
