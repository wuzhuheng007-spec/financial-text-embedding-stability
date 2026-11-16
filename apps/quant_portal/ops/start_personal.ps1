param([string]$PythonExe = '')
$ErrorActionPreference = 'Stop'
$PortalState = & (Join-Path $PSScriptRoot 'local.ps1') status
if ($LASTEXITCODE -ne 0 -or -not ($PortalState -match '^RUNNING ')) {
    & (Join-Path $PSScriptRoot 'local.ps1') start -PythonExe $PythonExe
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Output $PortalState
}
& (Join-Path $PSScriptRoot 'feed_personal.ps1') start
exit $LASTEXITCODE
