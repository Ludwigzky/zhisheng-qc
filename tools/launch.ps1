# 质检采集 Demo 启动器（.bat 只做入口，中文与端口逻辑都放这里，避免 cmd 编码/换行问题）
param(
  [ValidateSet('http','https')][string]$Mode = 'http',
  [int]$Port = 8760,
  [ValidateSet('127.0.0.1','0.0.0.0')][string]$BindAddress = '0.0.0.0',
  [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Write-Line($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }

Write-Line "============================================" 'Cyan'
Write-Line "  卫华 · 质检现场采集（本地演示）" 'Cyan'
Write-Line "============================================" 'Cyan'
Write-Line ""

if (-not (Test-Path "backend\app.py")) {
  Write-Line "[错误] 找不到 backend\app.py，请在 qc-demo 目录下运行。" 'Red'
  exit 1
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
if (-not $env:QC_SECRET) {
  $sessionBytes = New-Object byte[] 48
  $sessionRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try { $sessionRng.GetBytes($sessionBytes) }
  finally { $sessionRng.Dispose() }
  $env:QC_SECRET = [Convert]::ToBase64String($sessionBytes)
}
$pythonExe = Join-Path (Get-Location) '.venv-asr\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
  $py = Get-Command python -ErrorAction SilentlyContinue
  if ($py) { $pythonExe = $py.Source } else { $pythonExe = $null }
}
if (-not $pythonExe) {
  Write-Line "[错误] 命令行里找不到 python。请先装 Python 3，或把 Anaconda 的 Scripts 目录加进 PATH。" 'Red'
  exit 1
}
Write-Line ("Python: " + $pythonExe) 'Gray'

# 找一个空闲端口（8760 起，最多试到 8769）
$chosen = 0
foreach ($p in $Port..($Port + 9)) {
  $busy = $false
  $listener = $null
  try {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $p)
    $listener.Server.ExclusiveAddressUse = $true
    $listener.Start()
  } catch { $busy = $true }
  finally { if ($listener) { $listener.Stop() } }
  if (-not $busy) { $chosen = $p; break }
}
if ($chosen -eq 0) {
  Write-Line ("[错误] {0}-{1} 都被占用了，请指定其他端口。" -f $Port, ($Port + 9)) 'Red'
  exit 1
}
if ($chosen -ne $Port) { Write-Line ("[提示] 端口 {0} 被占用，本次改用 {1}" -f $Port, $chosen) 'Yellow' }

if ($Mode -eq 'https' -and -not (Test-Path "certs\cert.pem")) {
  Write-Line "首次运行，生成自签证书..." 'Yellow'
  & $pythonExe tools\make_cert.py
}

$scheme = if ($Mode -eq 'https') { 'https' } else { 'http' }
$local = "{0}://127.0.0.1:{1}" -f $scheme, $chosen
Write-Line ""
Write-Line ("本机访问： " + $local) 'Green'
if ($Mode -eq 'https') {
  $ips = @()
  try {
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
      Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
      Select-Object -ExpandProperty IPAddress)
  } catch {
    try {
      $ips = @([System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
        Where-Object { $_.AddressFamily -eq 'InterNetwork' -and $_.IPAddressToString -notlike '127.*' } |
        ForEach-Object { $_.IPAddressToString })
    } catch { $ips = @() }
  }
  $ips = @($ips | Where-Object { $_ -and $_ -notlike '127.*' -and $_ -notlike '169.254.*' -and $_ -notlike '198.18.*' -and $_ -notlike '198.19.*' } | Sort-Object -Unique)
  $pri = @($ips | Where-Object { $_ -like '192.168.*' -or $_ -like '10.*' -or $_ -match '^172\.(1[6-9]|2[0-9]|3[01])\.' } | Sort-Object { if ($_ -like '192.168.*') { 0 } elseif ($_ -like '10.*') { 1 } else { 2 } }, { $_ })
  $rest = @($ips | Where-Object { $pri -notcontains $_ })
  if ($pri.Count -gt 0) {
    foreach ($ip in $pri) { Write-Line ("手机访问： https://{0}:{1}   ← 手机连同一个 WiFi 用这个" -f $ip, $chosen) 'Green' }
  } elseif ($rest.Count -gt 0) {
    foreach ($ip in $rest) { Write-Line ("手机访问： https://{0}:{1}" -f $ip, $chosen) 'Green' }
  } else {
    Write-Line "没找到局域网 IP：先运行 ipconfig 看本机 IP，再用 https://<IP>:端口 打开" 'Yellow'
  }
} else {
  Write-Line "手机录音需要 HTTPS，请改用 run-https.bat" 'Yellow'
}
Write-Line ""
Write-Line "语音引擎自检：双击 check-asr.bat（不需要启动服务）" 'Gray'
Write-Line "按 Ctrl+C 可停止服务" 'Gray'
Write-Line ""

if (-not $NoBrowser) { Start-Process $local -ErrorAction SilentlyContinue }
$args = @("backend\app.py", "--host", "$BindAddress", "--port", "$chosen")
if ($Mode -eq 'https') { $args += "--https" }
& $pythonExe @args
Write-Line ""
Write-Line "服务已退出。按任意键关闭窗口。" 'Yellow'
