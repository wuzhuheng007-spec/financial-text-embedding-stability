$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'feed_personal.ps1') stop
$FeedStatus = $LASTEXITCODE
& (Join-Path $PSScriptRoot 'local.ps1') stop
if ($FeedStatus -ne 0) { exit $FeedStatus }
exit $LASTEXITCODE
