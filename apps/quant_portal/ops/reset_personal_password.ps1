$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'local.ps1') reset-password
exit $LASTEXITCODE
