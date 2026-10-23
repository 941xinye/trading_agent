#requires -Version 5.1
<#
.SYNOPSIS
    把 watch 守护进程注册为"登录时自动启动"的 Windows 计划任务；若权限不足自动降级到 Startup 文件夹快捷方式。

.EXAMPLE
    powershell -File scripts\watch_register_task.ps1            # 注册
    powershell -File scripts\watch_register_task.ps1 -Remove    # 卸载
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [string]$TaskName = 'CryptoAnalystWatchDaemon'
)

$Root    = Split-Path -Parent $PSScriptRoot
$Daemon  = Join-Path $PSScriptRoot 'watch_daemon.ps1'
$Startup = [Environment]::GetFolderPath('Startup')
$Link    = Join-Path $Startup 'CryptoAnalystWatch.lnk'

function Remove-StartupLink {
    if (Test-Path $Link) { Remove-Item $Link -Force; Write-Host "已删除 Startup 快捷方式：$Link" -ForegroundColor Green }
}

function Install-StartupLink {
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($Link)
    $sc.TargetPath = 'powershell.exe'
    $sc.Arguments  = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Daemon`" -Interval 300"
    $sc.WorkingDirectory = $Root
    $sc.WindowStyle = 7  # Minimized
    $sc.Save()
    Write-Host "已创建 Startup 快捷方式：$Link" -ForegroundColor Green
}

if ($Remove) {
    schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
    Remove-StartupLink
    exit 0
}

# 优先尝试计划任务
$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Daemon`" -Interval 300"
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden></Settings>
  <Actions><Exec><Command>powershell.exe</Command><Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$Daemon" -Interval 300</Arguments><WorkingDirectory>$Root</WorkingDirectory></Exec></Actions>
</Task>
"@
$xmlFile = Join-Path $env:TEMP 'watch_daemon_task.xml'
$taskXml | Out-File -FilePath $xmlFile -Encoding unicode -Force
schtasks /Create /TN $TaskName /XML $xmlFile /F 2>&1 | Out-Null
$taskOk = ($LASTEXITCODE -eq 0)
Remove-Item $xmlFile -Force -ErrorAction SilentlyContinue

if ($taskOk) {
    Write-Host "已注册计划任务 $TaskName（登录时触发，LeastPrivilege）" -ForegroundColor Green
    Write-Host "  立即启动：schtasks /Run /TN $TaskName"
    Write-Host "  查看状态：schtasks /Query /TN $TaskName"
    Remove-StartupLink
} else {
    Write-Host "计划任务注册被拒（非管理员），降级使用 Startup 文件夹。" -ForegroundColor Yellow
    Install-StartupLink
}
Write-Host "  卸载：powershell -File scripts\watch_register_task.ps1 -Remove"
