#requires -Version 5.1
<#
.SYNOPSIS
    以隐藏窗口启动 watch 守护进程，等待其就绪并打印状态。

.NOTES
    守护进程是当前用户会话下的隐藏 PowerShell；注销/重启系统会终止它。
    若需要"开机自启 + 注销后仍运行"，请改用 scripts\watch_register_task.ps1。
#>
[CmdletBinding()]
param(
    [int]$Interval = 300,
    [int]$WaitSeconds = 6
)

$Root    = Split-Path -Parent $PSScriptRoot
$Daemon  = Join-Path $PSScriptRoot 'watch_daemon.ps1'
$PidFile = Join-Path $Root '.state\watch.pid'

if (Test-Path $PidFile) {
    $oldPid = [int](Get-Content $PidFile -Raw).Trim()
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        Write-Host "watch 已在运行（PID $oldPid）" -ForegroundColor Yellow
        exit 0
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$null = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$Daemon`"",'-Interval',$Interval) `
    -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$realPid  = $null
while ((Get-Date) -lt $deadline) {
    if (Test-Path $PidFile) {
        $candidate = [int](Get-Content $PidFile -Raw).Trim()
        if (Get-Process -Id $candidate -ErrorAction SilentlyContinue) {
            $realPid = $candidate
            break
        }
    }
    Start-Sleep -Milliseconds 500
}

if ($realPid) {
    Write-Host "watch 守护进程已启动，PID $realPid" -ForegroundColor Green
    Write-Host "  日志：reports\watch.log"
    Write-Host "  停止：powershell -File scripts\watch_stop.ps1"
} else {
    Write-Host "启动失败，请直接运行 scripts\watch_daemon.ps1 查看输出。" -ForegroundColor Red
    exit 1
}
