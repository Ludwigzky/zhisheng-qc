# 启动质检采集 Demo（PowerShell 版）
#   .\run.ps1            本机 http://127.0.0.1:8760
#   .\run.ps1 -Https     自签 HTTPS，手机同网段可访问并录音
param(
  [switch]$Https,
  [int]$Port = 8760
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv-asr\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { $pythonExe = 'python' }

if ($Https -and -not (Test-Path "certs\cert.pem")) {
  Write-Host "首次运行，生成自签证书..." -ForegroundColor Yellow
  & $pythonExe tools\make_cert.py
}

$scheme = "http"
if ($Https) { $scheme = "https" }
Write-Host ("启动中: {0}://127.0.0.1:{1}" -f $scheme, $Port) -ForegroundColor Cyan

$serverArgs = @("backend\app.py", "--host", "0.0.0.0", "--port", "$Port")
if ($Https) { $serverArgs += "--https" }
& $pythonExe @serverArgs
