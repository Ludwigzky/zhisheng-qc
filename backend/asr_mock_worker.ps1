# 模拟 ASR worker：只验证 Python 侧协议（READY / GRAMMAR / OK / EMPTY / ERR）
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
function Out-Line([string]$s) { [Console]::Out.WriteLine($s); [Console]::Out.Flush() }
function B64([string]$s) { return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($s)) }
Out-Line "READY"
while ($true) {
  $line = [Console]::In.ReadLine()
  if ($null -eq $line) { break }
  $line = $line.Trim()
  if ($line -eq 'QUIT') { break }
  if ($line -eq '') { continue }
  if ($line -eq 'DICTATION') { Out-Line "GRAMMAR`tDICTATION"; continue }
  if ($line -like 'LOAD *') {
    $p = $line.Substring(5).Trim()
    if ($p -like '*_bad*') { Out-Line ("GRAMMAR`tFAIL`t" + (B64 "模拟语法错误")); continue }
    Out-Line "GRAMMAR`tOK"; continue
  }
  $name = [System.IO.Path]::GetFileNameWithoutExtension($line)
  if ($name -like '*_empty') { Out-Line "EMPTY`t0`t"; continue }
  if ($name -like '*_err') { Out-Line ("ERR`t0`t" + (B64 "模拟引擎错误")); continue }
  Out-Line ("OK`t0.93`t" + (B64 "跨度 二万二千五百零一"))
}