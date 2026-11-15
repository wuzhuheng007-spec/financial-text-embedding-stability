param([string]$PythonExe = '')
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'local.ps1') start -PythonExe $PythonExe
exit $LASTEXITCODE
