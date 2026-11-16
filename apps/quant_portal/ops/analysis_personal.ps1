param([Parameter(Mandatory=$true,Position=0)][ValidateSet('start','status','stop','pause','resume')][string]$Action)
$ErrorActionPreference='Stop'
$Repo=(Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$Var=Join-Path $Repo 'apps/quant_portal/var'
$Runtime=Join-Path $Var 'analysis_v15b_model_runtime.json'
$PidFile=Join-Path $Var 'personal_analysis_v16.pid.json'
$StopFile=Join-Path $Var 'personal_analysis_v16.stop'
$PauseFile=Join-Path $Var 'personal_analysis_v16.paused'
$OldPidFile=Join-Path $Var 'personal_analysis_v15b.pid.json'

function Get-VerifiedAnalysis {
    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
    $record=Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $proc -or $proc.CommandLine -notlike '*scripts.quant_personal_feed.analysis_v16*run*') { return $null }
    return $record
}

if ($Action -eq 'status') {
    $running=Get-VerifiedAnalysis
    Write-Output $(if($running){"ANALYSIS_RUNNING pid=$($running.pid)"}elseif(Test-Path -LiteralPath $PauseFile){'ANALYSIS_PAUSED_FOR_V17'}else{'ANALYSIS_STOPPED_OR_UNTRACKED'})
    Push-Location $Repo
    try { & python -m scripts.quant_personal_feed.analysis_v16 status }
    finally { Pop-Location }
    exit 0
}
if ($Action -eq 'pause') {
    Set-Content -LiteralPath $PauseFile -Value 'Paused for bounded v1.7 development; preserve jobs and responses.' -Encoding utf8
    if (Get-VerifiedAnalysis) {
        Set-Content -LiteralPath $StopFile -Value 'stop' -Encoding ascii
        for($i=0;$i -lt 30;$i++) {
            if (-not (Get-VerifiedAnalysis)) { Write-Output 'ANALYSIS_PAUSED_FOR_V17'; exit 0 }
            Start-Sleep -Seconds 1
        }
        Write-Output 'ANALYSIS_PAUSE_PENDING_CURRENT_BOUNDED_GENERATION'; exit 2
    }
    Write-Output 'ANALYSIS_PAUSED_FOR_V17'; exit 0
}
if ($Action -eq 'resume') {
    Remove-Item -LiteralPath $PauseFile -ErrorAction SilentlyContinue
    Write-Output 'ANALYSIS_V16_PAUSE_CLEARED_NOT_STARTED'; exit 0
}
if ($Action -eq 'stop') {
    if (-not (Get-VerifiedAnalysis)) { Write-Output 'ANALYSIS_STOPPED'; exit 0 }
    Set-Content -LiteralPath $StopFile -Value 'stop' -Encoding ascii
    for($i=0;$i -lt 30;$i++) {
        if (-not (Get-VerifiedAnalysis)) { Write-Output 'ANALYSIS_STOPPED'; exit 0 }
        Start-Sleep -Seconds 1
    }
    Write-Output 'ANALYSIS_STOP_PENDING_CURRENT_BOUNDED_GENERATION'
    exit 2
}
if (Test-Path -LiteralPath $PauseFile) { Write-Output 'ANALYSIS_V16_PAUSED_FOR_V17'; exit 2 }
if (Get-VerifiedAnalysis) { Write-Output 'ANALYSIS_ALREADY_RUNNING'; exit 0 }
if (Test-Path -LiteralPath $OldPidFile) {
    $oldRecord=Get-Content -LiteralPath $OldPidFile -Raw | ConvertFrom-Json
    $oldProc=Get-CimInstance Win32_Process -Filter "ProcessId=$($oldRecord.pid)" -ErrorAction SilentlyContinue
    if ($oldProc -and $oldProc.CommandLine -like '*scripts.quant_personal_feed.analysis_v15b*run*') {
        Write-Output 'OLD_ANALYSIS_WORKER_STILL_RUNNING_STOP_IT_FIRST'; exit 2
    }
}
if (-not (Test-Path -LiteralPath $Runtime)) { Write-Output 'ANALYSIS_MODEL_RUNTIME_NOT_CONFIGURED'; exit 2 }
$config=Get-Content -LiteralPath $Runtime -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $config.python_exe) -or -not (Test-Path -LiteralPath $config.model_dir)) {
    Write-Output 'ANALYSIS_MODEL_RUNTIME_MISSING'; exit 2
}
if ((Split-Path -Leaf $config.model_dir) -ne 'aa8e72537993ba99e69dfaafa59ed015b17504d1') {
    Write-Output 'ANALYSIS_MODEL_REVISION_MISMATCH'; exit 2
}
$env:QUANT_V15_MODEL_PYTHON=[string]$config.python_exe
$env:QUANT_V15_MODEL_DIR=[string]$config.model_dir
$out=Join-Path $Var 'analysis_v16.stdout.log'
$err=Join-Path $Var 'analysis_v16.stderr.log'
foreach($log in @($out,$err)) {
    if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 5242880) {
        Move-Item -LiteralPath $log -Destination ($log+'.'+(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ'))
    }
}
$process=Start-Process -FilePath (Get-Command python).Source -ArgumentList @('-m','scripts.quant_personal_feed.analysis_v16','run') -WorkingDirectory $Repo -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
for($i=0;$i -lt 20;$i++) {
    Start-Sleep -Milliseconds 500
    $running=Get-VerifiedAnalysis
    if ($running -and [int]$running.pid -eq $process.Id) { Write-Output "ANALYSIS_STARTED pid=$($process.Id)"; exit 0 }
    if ($process.HasExited) { break }
}
Write-Output 'ANALYSIS_START_FAILED_INSPECT_PRIVATE_LOG'
exit 2
