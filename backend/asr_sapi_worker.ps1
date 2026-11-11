# 质检口报识别 · 本地 SAPI worker（32 位宿主）
# 协议（stdin 每行一条指令；stdout 每行一条结果，文本 base64 避免编码问题）
#   LOAD <grxml 路径>   → GRAMMAR<TAB>OK | GRAMMAR<TAB>FAIL<TAB>base64(msg)
#   DICTATION           → GRAMMAR<TAB>DICTATION
#   <wav 路径>          → OK<TAB>conf<TAB>base64(text) | EMPTY | ERR<TAB>0<TAB>base64(msg)
#   QUIT
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Out-Line([string]$s) { [Console]::Out.WriteLine($s); [Console]::Out.Flush() }
function B64([string]$s) { return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($s)) }

function Use-Dictation($engine) {
  $engine.UnloadAllGrammars()
  $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
}

try {
  Add-Type -AssemblyName System.Speech
  # 无参数构造会选系统默认语言，可能是 en-US，不能用它冒充 zh-CN。
  $culture = New-Object System.Globalization.CultureInfo -ArgumentList 'zh-CN'
  $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine -ArgumentList $culture
  Use-Dictation $engine
  # 输入是有限 WAV；允许用户按下录音后稍作停顿，由文件 EOF 终止。
  $engine.InitialSilenceTimeout = [TimeSpan]::Zero
  $engine.EndSilenceTimeout = [TimeSpan]::FromSeconds(1.0)
  $engine.BabbleTimeout = [TimeSpan]::Zero
  Out-Line "READY"
} catch {
  Out-Line ("FAIL`t" + (B64 $_.Exception.Message))
  exit 1
}

$mode = "dictation"
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  $line = $line.Trim()
  if ($line -eq 'QUIT') { break }
  if ($line -eq '') { continue }

  if ($line -eq 'DICTATION') {
    try { Use-Dictation $engine; $mode = "dictation"; Out-Line "GRAMMAR`tDICTATION" }
    catch { Out-Line ("GRAMMAR`tFAIL`t" + (B64 $_.Exception.Message)) }
    continue
  }
  if ($line -like 'LOAD *') {
    $path = $line.Substring(5).Trim()
    try {
      if (-not (Test-Path $path)) { throw "语法文件不存在: $path" }
      $doc = New-Object System.Speech.Recognition.SrgsGrammar.SrgsDocument -ArgumentList $path
      $g = New-Object System.Speech.Recognition.Grammar -ArgumentList $doc
      $engine.UnloadAllGrammars()
      $engine.LoadGrammar($g)
      $mode = "grammar"
      Out-Line "GRAMMAR`tOK"
    } catch {
      $msg = $_.Exception.Message
      try { Use-Dictation $engine; $mode = "dictation" } catch {}
      Out-Line ("GRAMMAR`tFAIL`t" + (B64 $msg))
    }
    continue
  }

  try {
    $engine.SetInputToWaveFile($line)
    $texts = New-Object 'System.Collections.Generic.List[string]'
    $confidence = 1.0
    $complete = $false
    # Recognize() 只返回一个短语。继续读到 EOF，避免一秒停顿后后半句丢失。
    # 拒识也可能返回 null，因此只有 null 且读取位置不再推进才终止。
    for ($part = 0; $part -lt 128; $part++) {
      $before = $engine.RecognizerAudioPosition
      $r = $engine.Recognize()
      if ($null -ne $r -and -not [string]::IsNullOrWhiteSpace($r.Text)) {
        $texts.Add($r.Text)
        $confidence = [Math]::Min($confidence, [double]$r.Confidence)
      } elseif ($engine.RecognizerAudioPosition -le $before) {
        $complete = $true
        break
      }
    }
    $engine.SetInputToNull()
    if (-not $complete) { throw '音频短语过多，请按一项一句分段录音' }
    if ($texts.Count -gt 0) {
      $conf = $confidence.ToString([System.Globalization.CultureInfo]::InvariantCulture)
      Out-Line ("OK`t" + $conf + "`t" + (B64 ($texts -join '，')))
    } else {
      Out-Line "EMPTY`t0`t"
    }
  } catch {
    Out-Line ("ERR`t0`t" + (B64 $_.Exception.Message))
  }
}
