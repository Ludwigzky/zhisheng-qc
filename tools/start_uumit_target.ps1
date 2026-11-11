# Start only this project on a loopback port for the UUMit connector.
param([int]$Port = 8870)
$ErrorActionPreference = 'Stop'
$demoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $demoRoot '.venv-asr\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run setup-asr.bat first.' }
$chosenPort = 0
foreach ($candidatePort in $Port..($Port + 9)) {
  $portListener = $null
  try {
    $portListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $candidatePort)
    $portListener.Server.ExclusiveAddressUse = $true
    $portListener.Start()
    $chosenPort = $candidatePort
    break
  } catch { } finally { if ($portListener) { $portListener.Stop() } }
}
if (-not $chosenPort) { throw 'No unused target port in the requested range.' }
$sessionBytes = New-Object byte[] 48
$sessionRng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $sessionRng.GetBytes($sessionBytes) } finally { $sessionRng.Dispose() }
$env:QC_SECRET = [Convert]::ToBase64String($sessionBytes)
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$probeRoot = Join-Path $demoRoot '_probe'
[IO.Directory]::CreateDirectory($probeRoot) | Out-Null
$stdoutPath = Join-Path $probeRoot ("uumit-{0}.out.log" -f $chosenPort)
$stderrPath = Join-Path $probeRoot ("uumit-{0}.err.log" -f $chosenPort)
$serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('backend/app.py','--host','127.0.0.1','--port',"$chosenPort") -WorkingDirectory $demoRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$localBase = "http://127.0.0.1:$chosenPort"
for ($attempt = 0; $attempt -lt 40; $attempt++) {
  if ($serverProcess.HasExited) { throw "Demo failed to start. Check $stderrPath" }
  try {
    $probeRequest = [Net.HttpWebRequest]::Create($localBase + '/api/me')
    $probeRequest.Proxy = $null
    $probeRequest.Timeout = 1000
    $probeResponse = $probeRequest.GetResponse()
    try {
      $probeReader = [IO.StreamReader]::new($probeResponse.GetResponseStream())
      try { $identity = $probeReader.ReadToEnd() | ConvertFrom-Json }
      finally { $probeReader.Dispose() }
    } finally { $probeResponse.Close() }
    if ($identity.version -eq '0.19.1-management') {
      $result = @{port=$chosenPort;pid=$serverProcess.Id;local_url=$localBase;version=$identity.version;asr_available=$identity.asr.available;asr_engine=$identity.asr.engine}
      $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $probeRoot 'uumit-target.json') -Encoding utf8
      $result | ConvertTo-Json -Compress
      exit 0
    }
  } catch { }
  Start-Sleep -Milliseconds 500
}
throw "Demo startup timed out. Check $stdoutPath"
