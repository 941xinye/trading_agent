#requires -Version 5.1
<#
.SYNOPSIS
    链析 watch 常驻守护进程：自动重启 + 日志轮转 + PID 管理。

.DESCRIPTION
    - 每轮调用 main.py watch --interval $Interval，进程退出后按指数退避重启；
    - 日志写入 reports\watch.log，超过 $MaxLogMB 自动归档为 watch.YYYYMMDD-HHmmss.log；
    - PID 写入 .state\watch.pid，供 watch_stop.ps1 精确终止；
    - 单实例运行：发现已有存活实例时直接退出，避免重复采集。

.NOTES
    一般不直接手工运行本脚本，而是用 scripts\watch_start.ps1 以隐藏窗口启动。
#>
[CmdletBinding()]
param(
    [int]$Interval = 300,        # watch 轮询间隔（秒）
    [int]$MaxLogMB = 5,          # 单文件日志上限，超过即归档
    [int]$MaxCrashPerHour = 6    # 一小时内崩溃次数超过此值则退出，防止热循环
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root      = Split-Path -Parent $PSScriptRoot
$Python    = Join-Path $Root '.venv\Scripts\python.exe'
$Main      = Join-Path $Root 'main.py'
$StateDir  = Join-Path $Root '.state'
$ReportDir = Join-Path $Root 'reports'
$PidFile   = Join-Path $StateDir 'watch.pid'
$LogFile   = Join-Path $ReportDir 'watch.log'

foreach ($dir in @($StateDir, $ReportDir)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
}

function Write-Log([string]$Message, [string]$Level = 'INFO') {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Invoke-LogRotate {
    if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length / 1MB) -gt $MaxLogMB) {
        $archive = Join-Path $ReportDir ('watch.{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Move-Item $LogFile $archive -Force
        Write-Log "日志已归档到 $archive"
    }
}

# ---- 单实例检查 ----
if (Test-Path $PidFile) {
    $oldPid = [int](Get-Content $PidFile -Raw).Trim()
    $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($oldProc) {
        Write-Log "已有 watch 守护进程在运行（PID $oldPid），本次启动退出。" 'WARN'
        exit 0
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
$PID | Out-File -FilePath $PidFile -Encoding ascii -Force
Write-Log "watch 守护进程启动，PID $PID，轮询间隔 ${Interval}s"

# ---- 崩溃退避 ----
$crashTimes = New-Object System.Collections.Generic.List[datetime]

try {
    while ($true) {
        Invoke-LogRotate

        # 清理 1 小时以前的崩溃记录
        $cutoff = (Get-Date).AddHours(-1)
        $crashTimes.RemoveAll({ param($t) $t -lt $cutoff }) | Out-Null
        if ($crashTimes.Count -ge $MaxCrashPerHour) {
            Write-Log "过去 1 小时已崩溃 $($crashTimes.Count) 次，守护进程主动退出，请检查网络/配置。" 'ERROR'
            exit 2
        }

        Write-Log "启动 python main.py watch --interval $Interval"
        $proc = Start-Process -FilePath $Python `
            -ArgumentList @('-u', $Main, 'watch', '--interval', $Interval) `
            -WorkingDirectory $Root `
            -RedirectStandardOutput (Join-Path $ReportDir 'watch.stdout.log') `
            -RedirectStandardError  (Join-Path $ReportDir 'watch.stderr.log') `
            -NoNewWindow -PassThru

        $proc.WaitForExit()
        $proc.Refresh()
        $code = if ($null -ne $proc.ExitCode) { $proc.ExitCode } else { -1 }
        $crashTimes.Add((Get-Date))

        # 退避：第 1 次 10s，第 2 次 30s，第 3 次起 60s
        $backoff = switch ($crashTimes.Count) { 1 { 10 } 2 { 30 } default { 60 } }
        Write-Log "watch 进程退出（exit=$code），${backoff}s 后重启（近 1h 崩溃 $($crashTimes.Count) 次）" 'WARN'
        Start-Sleep -Seconds $backoff
    }
}
finally {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Log "watch 守护进程退出"
}
