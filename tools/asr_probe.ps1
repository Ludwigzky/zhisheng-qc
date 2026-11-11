# 语音识别引擎独立自检（不启动 Web 服务，直接在你自己终端里跑）
# 用法：双击 check-asr.bat，或
#   C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -File tools\asr_probe.ps1
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $root "data\tmp"
if (-not (Test-Path $tmp)) { New-Item -ItemType Directory -Force -Path $tmp | Out-Null }

Write-Host ""
Write-Host "==== 语音引擎自检 ====" -ForegroundColor Cyan
Write-Host ("宿主进程：{0} 位 PowerShell {1}" -f ([IntPtr]::Size * 8), $PSVersionTable.PSVersion)
Write-Host ""

# 1) 枚举识别器
Write-Host "[1/4] 枚举系统识别器 ..."
try {
  Add-Type -AssemblyName System.Speech
  $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
  if ($recs.Count -eq 0) {
    Write-Host "  ✗ 没有已安装的识别器。需要在「设置 → 时间和语言 → 语音」里添加中文语音包。" -ForegroundColor Red
    exit 1
  }
  foreach ($r in $recs) { Write-Host ("  · {0}  {1}" -f $r.Culture, $r.Name) }
} catch {
  Write-Host ("  ✗ 加载 System.Speech 失败：" + $_.Exception.Message) -ForegroundColor Red
  exit 1
}

# 2) 实例化 + 听写语法
Write-Host "[2/4] 实例化引擎并加载听写语法 ..."
try {
  $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
  $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(1.0)
  $engine.BabbleTimeout = [TimeSpan]::FromSeconds(2.0)
  Write-Host "  ✓ 引擎就绪" -ForegroundColor Green
} catch {
  Write-Host ("  ✗ 失败：" + $_.Exception.Message) -ForegroundColor Red
  Write-Host "    常见原因：进程没有语音引擎权限（服务/沙箱/受限账户），请在你自己的桌面终端里重跑本脚本。" -ForegroundColor Yellow
  exit 1
}

# 3) 合成一句话（用系统中文音库，验证链路两端）
$wav = Join-Path $tmp "probe.wav"
$synthOk = $false
Write-Host "[3/4] 合成测试语音（Microsoft Huihui）..."
try {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice("Microsoft Huihui Desktop")
  $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
  $s.SetOutputToWaveFile($wav, $fmt)
  $s.Speak("跨度两万二千五百零一")
  $s.Dispose()
  $size = (Get-Item $wav).Length
  Write-Host ("  ✓ 已生成 {0} 字节" -f $size) -ForegroundColor Green
  $synthOk = $size -gt 1000
} catch {
  Write-Host ("  ⚠ 合成失败（不影响识别）：" + $_.Exception.Message) -ForegroundColor Yellow
}

# 4) 识别
Write-Host "[4/4] 识别 ..."
if (-not $synthOk) {
  Write-Host "  没有测试音频，跳过识别。请用页面里的「识别实验室」录一句真声。" -ForegroundColor Yellow
  exit 0
}
try {
  $engine.SetInputToWaveFile($wav)
  $r = $engine.Recognize()
  if ($r -ne $null) {
    Write-Host ("  ✓ 识别结果：【{0}】 置信度 {1}" -f $r.Text, $r.Confidence) -ForegroundColor Green
    Write-Host ""
    Write-Host "结论：本机可以跑本地识别，直接启动 start.bat / run-https.bat 即可。" -ForegroundColor Green
  } else {
    Write-Host "  ⚠ 引擎没有识别出内容（合成音有时不被听写模型接受，属正常）" -ForegroundColor Yellow
    Write-Host "    请用页面里的「识别实验室」录一句真声再判断。" -ForegroundColor Yellow
  }
} catch {
  Write-Host ("  ✗ 识别出错：" + $_.Exception.Message) -ForegroundColor Red
}
$engine.Dispose()
Write-Host ""
