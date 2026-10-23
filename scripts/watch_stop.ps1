#requires -Version 5.1
<#
.SYNOPSIS
    停止 watch 守护进程及其全部子孙进程（含 python launcher 派生的真实 python）。
#>

$Root    = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root '.state\watch.pid'

if (-not (Test-Path $PidFile)) {
    Write-Host "watch 守护进程未在运行。" -ForegroundColor Yellow
    exit 0
}

$daemonPid = [int](Get-Content $PidFile -Raw).Trim()
$daemonProc = Get-Process -Id $daemonPid -ErrorAction SilentlyContinue
if (-not $daemonProc) {
    Write-Host "PID $daemonPid 已不存在，清理 PID 文件。" -ForegroundColor Yellow
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

# 递归收集子孙进程 PID
function Get-DescendantIds([int]$ParentId) {
    $result = @()
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        $result += $child.ProcessId
        $result += Get-DescendantIds -ParentId $child.ProcessId
    }
    return $result
}

$descendants = Get-DescendantIds -ParentId $daemonPid
foreach ($cid in $descendants) {
    $proc = Get-Process -Id $cid -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $cid -Force -ErrorAction SilentlyContinue
        Write-Host "已终止子进程 PID $cid（$($proc.Name)）"
    }
}
Stop-Process -Id $daemonPid -Force
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "watch 守护进程（PID $daemonPid）已停止。" -ForegroundColor Green
