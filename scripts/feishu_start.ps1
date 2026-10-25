# 飞书双向机器人一键启动脚本
# 作用：启动 feishu-hook（本地 8787）+ cpolar 内网隧道，获取并打印本次的公网回调地址。
# 用法：
#   powershell -File scripts\feishu_start.ps1              # 启动（已在运行的服务自动跳过）
#   powershell -File scripts\feishu_start.ps1 -CpolarExe "D:\tools\cpolar.exe"   # 指定 cpolar 路径
#   powershell -File scripts\feishu_start.ps1 -Stop        # 停止 feishu-hook 与 cpolar
#
# 注意：
#   - cpolar 免费版每次重启隧道都会更换随机域名，域名变更时脚本会显式提醒更新飞书后台；
#   - 请在独立的 Windows PowerShell 窗口运行（Trae/VS Code 集成终端关闭会连带杀掉子进程）；
#   - 服务日志：reports\feishu.log / reports\cpolar.log；cpolar 控制台：http://127.0.0.1:4040

param(
    [switch]$Stop,
    [string]$CpolarExe = "",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
$Root      = Split-Path $PSScriptRoot -Parent
$Python    = Join-Path $Root ".venv\Scripts\python.exe"
$ReportDir = Join-Path $Root "reports"
$DomainFile = Join-Path $Root ".state\cpolar_domain.txt"

# ---- 工具函数 ----

function Test-Port([int]$p) {
    $c = New-Object Net.Sockets.TcpClient
    try {
        $t = $c.BeginConnect("127.0.0.1", $p, $null, $null)
        if ($t.AsyncWaitHandle.WaitOne(800) -and $c.Connected) { return $true }
    } catch {} finally { $c.Close() }
    return $false
}

# 从 cpolar web 控制台（4040/4041/4042）读取本次隧道公网地址
function Get-TunnelUrl {
    foreach ($web in 4040, 4042, 4041) {
        if (-not (Test-Port $web)) { continue }
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$web/api/tunnels" -TimeoutSec 3
            $u = $r.tunnels | Where-Object { $_.public_url -like "https*" } |
                 Select-Object -First 1 -ExpandProperty public_url
            if ($u) { return $u }
        } catch {}
        try {
            $html = (Invoke-WebRequest -Uri "http://127.0.0.1:$web/" -UseBasicParsing -TimeoutSec 3).Content
            $m = [regex]::Match($html, 'https://[a-z0-9-]+(?:\.[a-z0-9-]+)*\.cpolar\.(?:top|io|cn)')
            if ($m.Success) { return $m.Value }
        } catch {}
    }
    return $null
}

# ---- 停止模式 ----

if ($Stop) {
    Get-Process cpolar -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    try {
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    } catch {}
    Write-Host "[OK] 已停止 feishu-hook 与 cpolar"
    exit 0
}

# ---- 1) 启动 feishu-hook ----

if (Test-Port $Port) {
    Write-Host "[SKIP] 端口 $Port 已有 feishu-hook 在运行"
} else {
    if (-not (Test-Path $Python)) { Write-Host "[ERR] 找不到虚拟环境：$Python"; exit 1 }
    Start-Process -FilePath $Python `
        -ArgumentList "main.py", "feishu-hook", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ReportDir "feishu.out.log") `
        -RedirectStandardError  (Join-Path $ReportDir "feishu.log")
    $ok = $false
    foreach ($i in 1..20) {
        Start-Sleep -Milliseconds 500
        if (Test-Port $Port) { $ok = $true; break }
    }
    if ($ok) { Write-Host "[OK] feishu-hook 已启动，日志：reports\feishu.log" }
    else     { Write-Host "[ERR] feishu-hook 启动超时，请查看 reports\feishu.log"; exit 1 }
}

# ---- 2) 启动 cpolar 隧道 ----

if (Get-Process cpolar -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] cpolar 已在运行"
} else {
    if (-not $CpolarExe) {
        $candidates = @(
            "E:\Work\cpolar\extract\cpolar\cpolar.exe",
            "C:\Users\LENOVO\cpolar\extracted\cpolar\cpolar.exe",
            "$env:USERPROFILE\cpolar\extracted\cpolar\cpolar.exe"
        )
        $CpolarExe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not ($CpolarExe -and (Test-Path $CpolarExe))) {
        Write-Host "[ERR] 未找到 cpolar.exe，请用 -CpolarExe 参数指定路径"; exit 1
    }
    Start-Process -FilePath $CpolarExe -ArgumentList "http", "$Port" -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $ReportDir "cpolar.log") `
        -RedirectStandardError  (Join-Path $ReportDir "cpolar.err.log")
    Write-Host "[OK] cpolar 隧道已启动，日志：reports\cpolar.log"
}

# ---- 3) 获取并打印本次公网回调地址 ----

$url = $null
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    $url = Get-TunnelUrl
    if ($url) { break }
}
if (-not $url) {
    Write-Host "[ERR] 30 秒内未获取到公网地址；请检查 reports\cpolar.err.log、cpolar 账户隧道额度，或打开 http://127.0.0.1:4040 查看状态"
    exit 1
}

$old = ""
if (Test-Path $DomainFile) { $old = (Get-Content $DomainFile -Raw -ErrorAction SilentlyContinue).Trim() }

Write-Host ""
Write-Host "==================== 飞书回调地址 ===================="
Write-Host "  $url/feishu/event"
Write-Host "====================================================="
if ($old -and ($old -ne $url)) {
    Write-Host "[提醒] 公网域名已变更（旧：$old）"
    Write-Host "       请到飞书开放平台 > 事件与回调 > 事件订阅，更新请求地址后保存！"
}
Set-Content -Path $DomainFile -Value $url -Encoding ASCII
Write-Host ""
Write-Host "调试入口：本地服务日志 reports\feishu.log ｜ 隧道控制台 http://127.0.0.1:4040"
