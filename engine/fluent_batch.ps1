# fluent_batch.ps1 — 用批处理模式跑 Fluent journal（无 GUI，最稳）
# 用法:
#   & fluent_batch.ps1 -Journal run.jou -Cores 4 -WorkDir D:\ansys_work\case01
# 返回: PSCustomObject { ExitCode, LogPath, ErrPath, DurationSec, TimedOut }

param(
    [Parameter(Mandatory=$true)][string]$Journal,
    [int]$Cores = 4,
    [ValidateSet('3ddp','3d','2ddp','2d')][string]$Dim = '3ddp',
    [string]$WorkDir = (Get-Location).Path,
    [int]$TimeoutSec = 0,
    [string]$LogPath,
    [string]$FluentExe = $env:CFD_FLUENT
)

if (-not $FluentExe) { $FluentExe = 'D:\ANSYS2024R2\ANSYS Inc\v242\fluent\ntbin\win64\fluent.exe' }
if (-not (Test-Path $FluentExe)) { throw "Fluent not found: $FluentExe" }
if (-not (Test-Path $Journal)) { throw "Journal not found: $Journal" }
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null }

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
if (-not $LogPath) { $LogPath = Join-Path $WorkDir "fluent_$stamp.log" }
$ErrPath = "$LogPath.err"

if ($Cores -le 1) {
    $argList = @($Dim, '-g', '-i', "`"$Journal`"")
} else {
    $argList = @($Dim, '-g', "-t$Cores", '-i', "`"$Journal`"")
}
Write-Host "[fluent_batch] $FluentExe $($argList -join ' ')"
Write-Host "[fluent_batch] workdir=$WorkDir log=$LogPath"

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $FluentExe -ArgumentList $argList -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $LogPath -RedirectStandardError $ErrPath -PassThru -NoNewWindow
$timedOut = $false
if ($TimeoutSec -gt 0) {
    if (-not $p.WaitForExit($TimeoutSec * 1000)) { $timedOut = $true; try { $p.Kill() } catch {} }
} else {
    $p.WaitForExit()
}
$sw.Stop()
$exit = if ($p.HasExited) { $p.ExitCode } else { $null }
Write-Host "[fluent_batch] exit=$exit timedOut=$timedOut elapsed=$([math]::Round($sw.Elapsed.TotalMinutes,1)) min"

[pscustomobject]@{
    ExitCode   = $exit
    TimedOut   = $timedOut
    LogPath    = $LogPath
    ErrPath    = $ErrPath
    DurationSec= [math]::Round($sw.Elapsed.TotalSeconds,1)
    WorkDir    = $WorkDir
}
