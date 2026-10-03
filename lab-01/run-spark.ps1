param([string]$JarsPath=(Join-Path $PSScriptRoot 'spark/jars'),[string]$ResultsPath=(Join-Path $PSScriptRoot 'results'))
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
. (Join-Path $root 'prepare-runtime.ps1')
$docker=if(Get-Command docker -ErrorAction SilentlyContinue){'docker'}else{'C:\Program Files\Docker\Docker\resources\bin\docker.exe'}
New-Item -ItemType Directory -Force -Path $JarsPath,$ResultsPath | Out-Null
foreach($artifact in @('iceberg-spark-runtime-3.5_2.12','iceberg-aws-bundle')){
 $file="$artifact-1.7.1.jar"
 $path=Join-Path $JarsPath $file
 $url="https://repo.maven.apache.org/maven2/org/apache/iceberg/$artifact/1.7.1/$file"
 if(-not(Test-Path -LiteralPath $path)){Invoke-WebRequest -Uri $url -OutFile $path}
 $expected=(Invoke-RestMethod -Uri ($url+'.sha1')).Trim().Split(' ')[0]
 if((Get-FileHash -LiteralPath $path -Algorithm SHA1).Hash.ToLowerInvariant() -ne $expected.ToLowerInvariant()){throw 'JAR checksum mismatch'}
}
$resolvedJars=(Resolve-Path -LiteralPath $JarsPath).Path
$resolvedResults=(Resolve-Path -LiteralPath $ResultsPath).Path
# SQL and reference results remain in /work; new verification output may go elsewhere.
$argsList=@('run','--rm','--name','q18-variant3-spark','--add-host','host.docker.internal:host-gateway',
 '-v',($root+':/work:ro'),'-v',($resolvedResults+':/output'),
 '-e','LAB_RESULTS_DIR=/output',
 '-v',($resolvedJars+':/opt/spark/iceberg-jars:ro'),
 '-v',((Join-Path $root '.local/spark-defaults.conf')+':/opt/spark/conf/spark-defaults.conf:ro'),
 '--entrypoint','/opt/spark/bin/spark-submit','apache/spark:3.5.3','--master','local[4]','--driver-memory','2g','/work/spark/verify_lakehouse.py')
$argsList | ConvertTo-Json | Out-File -LiteralPath (Join-Path $resolvedResults 'spark-docker-arguments.json') -Encoding utf8
& $docker @argsList 2>&1 | Tee-Object -FilePath (Join-Path $resolvedResults 'spark-console.log')
if($LASTEXITCODE -ne 0){throw 'Spark integration failed; see console log'}
