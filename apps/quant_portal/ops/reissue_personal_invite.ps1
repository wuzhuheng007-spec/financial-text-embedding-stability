$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'local.ps1') reissue
exit $LASTEXITCODE
