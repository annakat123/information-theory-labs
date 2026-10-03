# Credentials are read locally and never stored in this repository.
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
. (Join-Path $root 'prepare-runtime.ps1') -RequireStackSecrets
$docker=if(Get-Command docker -ErrorAction SilentlyContinue){'docker'}else{'C:\Program Files\Docker\Docker\resources\bin\docker.exe'}
$minioImage='quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e'
$pgImage='postgres@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675'
$keeperImage='quay.io/lakekeeper/catalog@sha256:9dad9f2df9cb5cb0b6d75ec94379e0de9f5a2029e817213a68f2afd8691615dc'
$trinoImage='trinodb/trino@sha256:00125e40d063bc4816d165482f6044872b18b56026fb959d3b28ce1f96ffbbee'
$env:LAKEKEEPER__PG_DATABASE_URL_WRITE='postgresql://postgres:'+([uri]::EscapeDataString($env:POSTGRES_PASSWORD))+'@host.docker.internal:5432/postgres'
$env:LAKEKEEPER__PG_DATABASE_URL_READ=$env:LAKEKEEPER__PG_DATABASE_URL_WRITE
$env:LAKEKEEPER__PG_ENCRYPTION_KEY=$env:LAKEKEEPER_ENCRYPTION_KEY
function Docker-Step {param([string[]]$Arguments); & $docker @Arguments; if($LASTEXITCODE -ne 0){throw 'Docker step failed; credentials omitted from error'}}
function Ensure-Container {
 param([string]$Name,[string[]]$Arguments)
 $found=& $docker ps -a --filter ('name=^/'+$Name+'$') --format '{{.Names}}'
 if($LASTEXITCODE -ne 0){throw 'Docker unavailable'}
 if($found -eq $Name){if((& $docker inspect $Name --format '{{.State.Running}}') -ne 'true'){Docker-Step @('start',$Name)};Write-Host "Reused: $Name"}else{Docker-Step $Arguments}
}
Docker-Step @('volume','create','minio-data')
Docker-Step @('volume','create','pg-data')
Ensure-Container 'lakehouse-minio' @('run','-d','--name','lakehouse-minio','-p','9000:9000','-p','9001:9001','-e','MINIO_ROOT_USER','-e','MINIO_ROOT_PASSWORD','-v','minio-data:/data',$minioImage,'server','/data','--console-address',':9001')
Ensure-Container 'lakehouse-pg' @('run','-d','--name','lakehouse-pg','-p','5432:5432','-e','POSTGRES_PASSWORD','-v','pg-data:/var/lib/postgresql/data',$pgImage)
$pgReady=$false
for($i=0;$i -lt 60;$i++){& $docker exec lakehouse-pg pg_isready -U postgres | Out-Null;if($LASTEXITCODE -eq 0){$pgReady=$true;break};Start-Sleep -Milliseconds 500}
if(-not $pgReady){throw 'PostgreSQL not ready'}
if((& $docker ps -a --filter 'name=^/lakekeeper$' --format '{{.Names}}') -ne 'lakekeeper'){
 Docker-Step @('run','--rm','--add-host','host.docker.internal:host-gateway','-e','LAKEKEEPER__PG_DATABASE_URL_WRITE','-e','LAKEKEEPER__PG_ENCRYPTION_KEY',$keeperImage,'migrate')
}
Ensure-Container 'lakekeeper' @('run','-d','--name','lakekeeper','--add-host','host.docker.internal:host-gateway','-p','8181:8181','-e','LAKEKEEPER__PG_DATABASE_URL_WRITE','-e','LAKEKEEPER__PG_DATABASE_URL_READ','-e','LAKEKEEPER__PG_ENCRYPTION_KEY',$keeperImage,'serve')
$info=$null
for($i=0;$i -lt 60;$i++){try{$info=Invoke-RestMethod 'http://127.0.0.1:8181/management/v1/info';break}catch{Start-Sleep -Milliseconds 500}}
if(-not $info){throw 'Lakekeeper not ready'}
if(-not $info.bootstrapped){Invoke-RestMethod 'http://127.0.0.1:8181/management/v1/bootstrap' -Method Post -ContentType 'application/json' -Body '{"accept-terms-of-use":true}' | Out-Null}
$warehouses=Invoke-RestMethod 'http://127.0.0.1:8181/management/v1/warehouse'
if(-not @($warehouses.warehouses | Where-Object name -eq 'bench').Count){
 Docker-Step @('run','--rm','--add-host','host.docker.internal:host-gateway','-e','MINIO_ROOT_USER','-e','MINIO_ROOT_PASSWORD','--entrypoint','/bin/sh','quay.io/minio/mc:latest','-c','mc alias set local http://host.docker.internal:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mb --ignore-existing local/bench')
 $request=@{'warehouse-name'='bench';'project-id'='00000000-0000-0000-0000-000000000000';'storage-profile'=@{type='s3';bucket='bench';endpoint='http://host.docker.internal:9000';region='us-east-1';'path-style-access'=$true;flavor='s3-compat';'sts-enabled'=$false};'storage-credential'=@{type='s3';'credential-type'='access-key';'access-key-id'=$env:MINIO_ROOT_USER;'secret-access-key'=$env:MINIO_ROOT_PASSWORD}}
 Invoke-RestMethod 'http://127.0.0.1:8181/management/v1/warehouse' -Method Post -ContentType 'application/json' -Body ($request | ConvertTo-Json -Depth 10) | Out-Null
}
Ensure-Container 'trino' @('run','-d','--name','trino','--add-host','host.docker.internal:host-gateway','-p','8080:8080','-v',((Join-Path $root '.local/catalog')+':/etc/trino/catalog:ro'),$trinoImage)
$ready=$false
for($i=0;$i -lt 60;$i++){try{$trinoInfo=Invoke-RestMethod 'http://127.0.0.1:8080/v1/info';if(-not $trinoInfo.starting){$ready=$true;break}}catch{};Start-Sleep -Milliseconds 500}
if(-not $ready){throw 'Trino not ready'}
Write-Host 'STACK READY; existing data preserved'
