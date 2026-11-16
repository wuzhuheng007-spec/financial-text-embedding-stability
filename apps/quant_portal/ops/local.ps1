param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet('start','status','stop','admin','reissue','reset-password')]
    [string]$Action,
    [string]$PythonExe = '',
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$PortalRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$StateDir = Join-Path $PortalRoot 'var'
$PidPath = Join-Path $StateDir 'local.pid.json'
$SecretPath = Join-Path $StateDir 'local_secret.txt'
$OutPath = Join-Path $StateDir 'local.stdout.log'
$ErrPath = Join-Path $StateDir 'local.stderr.log'
$ManagePath = Join-Path $PortalRoot 'manage.py'

function Resolve-Python {
    if ($PythonExe) {
        if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'PythonExe does not exist.' }
        return (Resolve-Path -LiteralPath $PythonExe).Path
    }
    $LocalPython = Join-Path $StateDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $LocalPython)) {
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        py -3.11 -m venv (Join-Path $StateDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 virtual environment creation failed.' }
        & $LocalPython -m pip install --disable-pip-version-check -r (Join-Path $PortalRoot 'requirements.lock') | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }
    }
    return $LocalPython
}

function Get-PortalListener([int]$CheckPort) {
    $Socket = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $CheckPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $Socket) { return $null }
    $ServerPid = [int]$Socket.OwningProcess
    $Process = Get-Process -Id $ServerPid -ErrorAction SilentlyContinue
    $Cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ServerPid" -ErrorAction SilentlyContinue
    if ($null -eq $Process -or $null -eq $Cim) { return $null }
    return [pscustomobject]@{
        pid = $ServerPid
        started_utc = $Process.StartTime.ToUniversalTime().ToString('o')
        command_line = [string]$Cim.CommandLine
    }
}

function Get-TrackedProcess {
    if (-not (Test-Path -LiteralPath $PidPath)) { return $null }
    $State = Get-Content -LiteralPath $PidPath -Raw | ConvertFrom-Json
    $Listener = Get-PortalListener ([int]$State.port)
    if ($null -eq $Listener) { return $null }
    if ($Listener.pid -ne [int]$State.server_pid -or $Listener.started_utc -ne [string]$State.server_started_utc) { return $null }
    if ($Listener.command_line -notlike "*$ManagePath*runserver*127.0.0.1:$($State.port)*") { return $null }
    return $State
}

function Stop-TrackedTree($State) {
    $Launcher = Get-Process -Id ([int]$State.launcher_pid) -ErrorAction SilentlyContinue
    $UseLauncher = $false
    if ($null -ne $Launcher -and $Launcher.StartTime.ToUniversalTime().ToString('o') -eq [string]$State.launcher_started_utc) {
        $LauncherCim = Get-CimInstance Win32_Process -Filter "ProcessId = $($State.launcher_pid)" -ErrorAction SilentlyContinue
        $UseLauncher = $null -ne $LauncherCim -and $LauncherCim.CommandLine -like "*$ManagePath*runserver*127.0.0.1:$($State.port)*"
    }
    $KillPid = if ($UseLauncher) { [int]$State.launcher_pid } else { [int]$State.server_pid }
    & taskkill.exe /PID $KillPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the verified local server process tree.' }
    for ($Try = 0; $Try -lt 40; $Try++) {
        if ($null -eq (Get-PortalListener ([int]$State.port))) { return }
        Start-Sleep -Milliseconds 250
    }
    throw 'Port is still listening after process-tree stop; inspect it before starting again.'
}

if ($Action -eq 'status') {
    $State = Get-TrackedProcess
    if ($null -eq $State) { Write-Output 'STOPPED_OR_UNTRACKED'; exit 1 }
    try {
        $Response = Invoke-WebRequest -Uri "http://127.0.0.1:$($State.port)/readyz/" -TimeoutSec 3 -UseBasicParsing
        $CssResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$($State.port)/static/core/portal.css" -TimeoutSec 3 -UseBasicParsing
        Write-Output "RUNNING pid=$($State.server_pid) ready_http=$([int]$Response.StatusCode) css_http=$([int]$CssResponse.StatusCode) url=http://127.0.0.1:$($State.port)/"
        exit 0
    } catch {
        Write-Output "PROCESS_RUNNING_NOT_READY pid=$($State.server_pid)"
        exit 2
    }
}

if ($Action -eq 'stop') {
    $State = Get-TrackedProcess
    if ($null -eq $State) { Write-Output 'STOPPED; no matching process to stop'; exit 0 }
    Stop-TrackedTree $State
    Remove-Item -LiteralPath $PidPath
    Write-Output 'STOPPED'
    exit 0
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
if (-not (Test-Path -LiteralPath $SecretPath)) {
    $Bytes = New-Object byte[] 64
    $Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $Rng.GetBytes($Bytes) }
    finally { $Rng.Dispose() }
    $GeneratedSecret = [Convert]::ToBase64String($Bytes)
    [System.IO.File]::WriteAllText($SecretPath, $GeneratedSecret, [System.Text.UTF8Encoding]::new($false))
}
$env:SECRET_KEY = [System.IO.File]::ReadAllText($SecretPath, [System.Text.Encoding]::UTF8).Trim()
$env:DEBUG = '1'
$env:DJANGO_TESTING = '1'
$env:ALLOWED_HOSTS = '127.0.0.1,localhost'
$env:CSRF_TRUSTED_ORIGINS = "http://127.0.0.1:$Port"
$env:PUBLIC_MARKET_DATA_ENABLED = '0'
$env:PERSONAL_MODE = '1'
Remove-Item Env:POSTGRES_HOST -ErrorAction SilentlyContinue
$ResolvedPython = Resolve-Python

if ($Action -eq 'admin') {
    Push-Location $PortalRoot
    try { & $ResolvedPython manage.py createsuperuser; exit $LASTEXITCODE }
    finally { Pop-Location }
}

if ($Action -eq 'reissue') {
    $NewInvite = Join-Path $StateDir ("personal_invite_" + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.txt')
    Push-Location $PortalRoot
    try { & $ResolvedPython manage.py create_invite owner@local.invalid --output $NewInvite; exit $LASTEXITCODE }
    finally { Pop-Location }
}

if ($Action -eq 'reset-password') {
    Push-Location $PortalRoot
    try { & $ResolvedPython manage.py changepassword owner@local.invalid; exit $LASTEXITCODE }
    finally { Pop-Location }
}

if ($null -ne (Get-TrackedProcess)) { throw 'Portal is already running; use status or stop.' }
if ($null -ne (Get-PortalListener $Port)) { throw 'Port is occupied by an untracked process; refusing to start.' }
Push-Location $PortalRoot
try {
    & $ResolvedPython manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Local migration failed.' }
    & $ResolvedPython manage.py bootstrap_personal
    if ($LASTEXITCODE -ne 0) { throw 'Personal bootstrap failed; inspect the source receipt before retrying.' }
    $Proc = Start-Process -FilePath $ResolvedPython -ArgumentList @($ManagePath,'runserver',"127.0.0.1:$Port",'--noreload') -WorkingDirectory $PortalRoot -PassThru -RedirectStandardOutput $OutPath -RedirectStandardError $ErrPath -WindowStyle Hidden
    $Listener = $null
    for ($Try = 0; $Try -lt 40; $Try++) {
        $Listener = Get-PortalListener $Port
        if ($null -ne $Listener) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($null -eq $Listener) { throw "Server did not listen within 10 seconds; inspect $ErrPath" }
    if ($Listener.command_line -notlike "*$ManagePath*runserver*127.0.0.1:$Port*") { throw 'Port was taken by another process; refusing to claim success.' }
    $State = @{
        launcher_pid = $Proc.Id
        launcher_started_utc = $Proc.StartTime.ToUniversalTime().ToString('o')
        server_pid = $Listener.pid
        server_started_utc = $Listener.started_utc
        port = $Port
    }
    $State | ConvertTo-Json -Compress | Set-Content -LiteralPath $PidPath -Encoding utf8
    if ($null -eq (Get-TrackedProcess)) { throw 'Listener identity check failed.' }
    try {
        $Ready = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/readyz/" -TimeoutSec 3 -UseBasicParsing
        $Css = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/static/core/portal.css" -TimeoutSec 3 -UseBasicParsing
        if ($Ready.StatusCode -ne 200 -or $Css.StatusCode -ne 200) { throw 'Ready or CSS endpoint did not return HTTP 200.' }
    } catch {
        Stop-TrackedTree $State
        Remove-Item -LiteralPath $PidPath -ErrorAction SilentlyContinue
        throw
    }
    Write-Output "STARTED http://127.0.0.1:$Port/ (local SQLite; personal feeds are a separate worker)"
    Write-Output "Read the one-time invite privately from: $(Join-Path $StateDir 'personal_invite.txt')"
} finally { Pop-Location }
