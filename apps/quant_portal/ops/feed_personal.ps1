param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('start','status','stop')]
    [string]$Action,
    # full: moomoo + Jin10 (unchanged default). jin10_only: moomoo off, for the
    # single-writer switch where a cloud feed becomes the only moomoo writer.
    [ValidateSet('full','jin10_only')]
    [string]$FeedProfile = 'full'
)
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$Var = Join-Path $RepoRoot 'apps/quant_portal/var'
$PidFile = Join-Path $Var 'personal_feed.pid.json'
# Start-Process truncates its redirect targets, so each start gets its own
# timestamped log pair; personal_feed.latest_logs.json points at the newest.
$LogDir = Join-Path $Var 'feed_logs'
$LatestLogs = Join-Path $Var 'personal_feed.latest_logs.json'
# Only these repository configs are ever started or recognised. legacy_v1 is
# recognised (status/stop) but cannot be started.
$Profiles = [ordered]@{
    'full'       = Join-Path $RepoRoot 'configs/quant_portal_personal_feeds_v11_20261116.json'
    'jin10_only' = Join-Path $RepoRoot 'configs/quant_portal_personal_feeds_jin10_only_v12_20261118.json'
    'legacy_v1'  = Join-Path $RepoRoot 'configs/quant_portal_personal_feeds_v1.json'
}
$Config = $Profiles[$FeedProfile]
$Python = (Get-Command python -ErrorAction Stop).Source

function Get-VerifiedFeed {
    # Returns the tracked collector and the profile it was started with, for
    # any recognised profile, so stop/status work whichever profile is running.
    if (-not (Test-Path -LiteralPath $PidFile)) { return $null }
    $Record = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    $Proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($Record.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $Proc -or $Proc.CommandLine -notlike '*scripts.quant_personal_feed.cli*run*') { return $null }
    foreach ($Name in $Profiles.Keys) {
        if ($Proc.CommandLine -like "*$($Profiles[$Name])*") {
            return [pscustomobject]@{ pid = $Record.pid; profile = $Name; config = $Profiles[$Name] }
        }
    }
    return $null
}

if ($Action -eq 'status') {
    $Running = Get-VerifiedFeed
    Write-Output $(if ($Running) { "FEED_RUNNING pid=$($Running.pid) profile=$($Running.profile)" } else { 'FEED_STOPPED_OR_UNTRACKED' })
    $StatusConfig = if ($Running) { $Running.config } else { $Config }
    Push-Location $RepoRoot
    try { & $Python -m scripts.quant_personal_feed.cli status --config $StatusConfig }
    finally { Pop-Location }
    exit 0
}
if ($Action -eq 'stop') {
    $Running = Get-VerifiedFeed
    if (-not $Running) { Write-Output 'FEED_STOPPED'; exit 0 }
    Push-Location $RepoRoot
    try { & $Python -m scripts.quant_personal_feed.cli stop --config $Running.config }
    finally { Pop-Location }
    for ($i=0; $i -lt 50; $i++) {
        if (-not (Get-VerifiedFeed)) { Write-Output "FEED_STOPPED profile=$($Running.profile)"; exit 0 }
        Start-Sleep -Seconds 1
    }
    Write-Output 'FEED_STOP_PENDING; inspect status before a second stop'
    exit 2
}
$Running = Get-VerifiedFeed
if ($Running) {
    if ($Running.profile -eq $FeedProfile) { Write-Output "FEED_ALREADY_RUNNING pid=$($Running.pid) profile=$($Running.profile)"; exit 0 }
    # Never start a second writer; switching profiles is stop, then start.
    Write-Output "FEED_RUNNING_OTHER_PROFILE pid=$($Running.pid) profile=$($Running.profile); stop it before starting $FeedProfile"
    exit 3
}
New-Item -ItemType Directory -Force -Path $Var | Out-Null
$EnvToken = [Environment]::GetEnvironmentVariable('JIN10_MCP_TOKEN','User')
if (-not $env:JIN10_MCP_TOKEN -and $EnvToken) { $env:JIN10_MCP_TOKEN = $EnvToken }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StartedUtc = (Get-Date).ToUniversalTime()
$Stem = 'personal_feed.' + $StartedUtc.ToString('yyyyMMddTHHmmssfffZ')
$Suffix = 0
while ((Test-Path -LiteralPath (Join-Path $LogDir "$Stem.stderr.log")) -or (Test-Path -LiteralPath (Join-Path $LogDir "$Stem.stdout.log"))) {
    $Suffix++; $Stem = 'personal_feed.' + $StartedUtc.ToString('yyyyMMddTHHmmssfffZ') + ".$Suffix"
}
$OutLog = Join-Path $LogDir "$Stem.stdout.log"
$ErrLog = Join-Path $LogDir "$Stem.stderr.log"
# Launch through WMI (Win32_Process.Create) instead of Start-Process: the collector
# then inherits no handle from this script or its caller (Start-Process with
# redirection let the collector hold the caller's stdout pipe, so callers that
# capture output never returned), and it does not depend on the launching
# terminal. cmd.exe only redirects the collector's output to the log files.
# The collector's environment is the user's environment; JIN10_MCP_TOKEN is a
# user-scope variable, as before.
$CmdLine = 'cmd.exe /d /s /c ""{0}" -m scripts.quant_personal_feed.cli run --config "{1}" 1> "{2}" 2> "{3}""' -f $Python, $Config, $OutLog, $ErrLog
$Startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$Created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $CmdLine; CurrentDirectory = $RepoRoot; ProcessStartupInformation = $Startup }
$LauncherPid = [int]$Created.ProcessId
function Write-LatestLogs($CollectorPid) {
    # Written before the start is verified, so a failed start's log is also findable.
    [pscustomobject]@{ started_at_utc = $StartedUtc.ToString('o'); pid = $CollectorPid; launcher_pid = $LauncherPid;
        profile = $FeedProfile; stdout = "feed_logs/$Stem.stdout.log"; stderr = "feed_logs/$Stem.stderr.log" } |
        ConvertTo-Json | Set-Content -LiteralPath $LatestLogs -Encoding ascii
}
Write-LatestLogs $null
if ($Created.ReturnValue -ne 0) {
    Write-Output "FEED_START_FAILED; Win32_Process.Create returned $($Created.ReturnValue)"
    exit 2
}
for ($i=0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    $Running = Get-VerifiedFeed
    if ($Running) {
        $Collector = Get-CimInstance Win32_Process -Filter "ProcessId=$($Running.pid)" -ErrorAction SilentlyContinue
        if ($Collector -and [int]$Collector.ParentProcessId -eq $LauncherPid) {
            Write-LatestLogs ([int]$Running.pid)
            Write-Output "FEED_STARTED pid=$($Running.pid) profile=$FeedProfile"; exit 0
        }
    }
    if (-not (Get-Process -Id $LauncherPid -ErrorAction SilentlyContinue)) { break }
}
Write-Output "FEED_START_FAILED; inspect private stderr log feed_logs/$Stem.stderr.log (do not share raw log)"
exit 2
