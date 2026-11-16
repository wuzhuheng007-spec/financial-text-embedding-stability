$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'local.ps1') status
$PortalStatus = $LASTEXITCODE
& (Join-Path $PSScriptRoot 'feed_personal.ps1') status
exit $PortalStatus
