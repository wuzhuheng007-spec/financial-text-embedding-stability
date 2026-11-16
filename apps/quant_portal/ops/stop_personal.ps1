$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'analysis_personal.ps1') stop
$AnalysisStatus = $LASTEXITCODE
& (Join-Path $PSScriptRoot 'feed_personal.ps1') stop
$FeedStatus = $LASTEXITCODE
& (Join-Path $PSScriptRoot 'local.ps1') stop
if ($AnalysisStatus -ne 0) { exit $AnalysisStatus }
if ($FeedStatus -ne 0) { exit $FeedStatus }
exit $LASTEXITCODE
